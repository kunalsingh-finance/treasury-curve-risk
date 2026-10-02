"""Offline synthetic dated-bond integration and temporal-boundary checks."""

from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from treasury_risk.dated_bonds import is_business_day
from treasury_risk.research import latest_analysis, load_instrument_config, simulate_period


def term(cusip, maturity, coupon=.04, issue="2018-01-15"):
    text = f"OFFLINE SYNTHETIC regular coupon fixture {cusip}; no actual auction transcription"
    return {"cusip": cusip, "label": f"Synthetic {cusip}", "issue_date": issue,
            "dated_date": issue, "maturity_date": maturity, "coupon_rate": coupon, "face": 100,
            "source_url": "https://example.invalid/offline-synthetic-fixture",
            "source_snapshot_text": text, "source_snapshot_sha256": hashlib.sha256(text.encode()).hexdigest()}


def fixture_config():
    targets = [term("TARGET001", "2025-01-15", .035), term("TARGET002", "2027-01-15", .0425),
               term("TARGET003", "2030-01-15", .0475)]
    hedges = [term(f"HEDGE000{index}", maturity) for index, maturity in enumerate(
        ("2023-01-15", "2026-01-15", "2030-01-15", "2035-01-15", "2045-01-15"), 1)]
    return {"schema_version": 1, "latest": {"targets": deepcopy(targets), "hedges": deepcopy(hedges)},
            "historical": {"targets": deepcopy(targets), "hedges": deepcopy(hedges)}}


def fixture_curves():
    rows = []
    day = date(2021, 10, 1)
    while day <= date(2022, 2, 28):
        if is_business_day(day):
            index = len(rows)
            rows.append({"date": day.isoformat(), "beta0": 2 + index * .005 + .03 * math.sin(index * .31),
                         "beta1": -.6 + .02 * math.cos(index * .23), "beta2": .4 + .06 * math.sin(index * .17),
                         "beta3": .2 + .03 * math.cos(index * .11), "tau1": 1.2, "tau2": 4.0})
        day += timedelta(days=1)
    return rows


FACES = (1000, 1200, 1400)


class DatedResearchTests(unittest.TestCase):
    def setUp(self):
        self.records = fixture_curves()
        self.config = fixture_config()
        self.audit = {"exceptions": []}

    def simulate(self, **parameters):
        return simulate_period(self.records, self.config, self.audit, "2022-01-03", "2022-02-04",
                               faces=FACES, lookback=30, **parameters)

    def test_latest_cash_neutral_infeasibility_is_explicit_and_has_no_fallback(self):
        # One positive-price hedge cannot offset positive DV01 with zero purchase cash.
        self.config["latest"]["hedges"] = [self.config["latest"]["hedges"][2]]
        result = latest_analysis(self.records, self.config, self.audit, faces=FACES,
                                 valuation_date="2022-01-31", lookback=30, cash_neutral=True)
        self.assertEqual(result["methods"]["constrained"]["status"], "infeasible")
        self.assertIsNone(result["methods"]["constrained"]["weights"])
        self.assertTrue(all(row["constrained"] is None for row in result["scenarios"]))
        self.assertTrue(result["constraints"]["cash_neutral"])

    def test_every_monthly_decision_and_covariance_endpoint_precedes_execution(self):
        result = self.simulate()
        self.assertEqual([row["trade_date"] for row in result["decisions"]], ["2022-01-03", "2022-02-01"])
        for row in result["decisions"]:
            self.assertLess(row["decision_curve_date"], row["trade_date"])
            self.assertLess(row["covariance"]["training_end_date"], row["trade_date"])
            self.assertTrue(all(day < row["trade_date"] for day in row["covariance"]["training_dates"]))
            self.assertEqual(row["covariance"]["execution_date"], row["trade_date"])
            self.assertEqual(row["covariance"]["change_count"], 30)
            self.assertEqual(row["methods"]["constrained"]["status"], "optimal")

    def test_later_curve_perturbation_cannot_change_earlier_weights(self):
        baseline = self.simulate()
        for row in self.records:
            if row["date"] >= "2022-02-01":
                row["beta0"] += .4
                row["beta1"] -= .1
        changed = self.simulate()
        for before, after in zip(baseline["decisions"], changed["decisions"]):
            self.assertEqual(before["decision_curve_date"], after["decision_curve_date"])
            self.assertEqual(before["covariance"]["covariance"], after["covariance"]["covariance"])
            for method in ("duration", "unweighted", "constrained"):
                np.testing.assert_array_equal(before["methods"][method]["weights"], after["methods"][method]["weights"])
        self.assertNotAlmostEqual(baseline["summaries"]["unhedged"]["final_wealth"],
                                  changed["summaries"]["unhedged"]["final_wealth"])

    def test_quarantined_prior_curve_is_not_a_decision_or_training_endpoint(self):
        self.audit["exceptions"] = [{"date": "2021-12-31", "status": "outside_rounding_tolerance"}]
        result = self.simulate()
        first = result["decisions"][0]
        self.assertEqual(first["decision_curve_date"], "2021-12-30")
        self.assertEqual(first["covariance"]["training_end_date"], "2021-12-30")
        self.assertNotIn("2021-12-31", first["covariance"]["training_dates"])

    def test_issue_and_adjusted_final_payment_dates_filter_active_hedges(self):
        self.config["historical"]["hedges"] += [term("EXPIRY001", "2022-01-15"),
                                                term("FUTURE001", "2032-02-15", issue="2022-02-15")]
        result = self.simulate()
        first, second = result["decisions"]
        self.assertIn("EXPIRY001", first["active_hedges"])
        self.assertNotIn("EXPIRY001", second["active_hedges"])
        self.assertNotIn("FUTURE001", first["active_hedges"])
        self.assertNotIn("FUTURE001", second["active_hedges"])

    def test_stale_prior_curve_and_failed_required_optimizer_stop_the_period(self):
        original = self.records
        self.records = [row for row in self.records if not "2021-12-23" <= row["date"] < "2022-01-03"]
        with self.assertRaisesRegex(ValueError, "stale"):
            self.simulate()
        self.records = original
        with self.assertRaisesRegex(ValueError, "Required constrained hedge failed"):
            self.simulate(gross_multiple=0)

    def test_per100_face_units_and_source_terms_are_preserved(self):
        result = latest_analysis(self.records, self.config, self.audit, faces=FACES,
                                 valuation_date="2022-01-31", lookback=30)
        for supplied, holding, face in zip(self.config["latest"]["targets"], result["portfolio"], FACES):
            self.assertEqual(holding["cusip"], supplied["cusip"])
            self.assertEqual(holding["source_url"], supplied["source_url"])
            self.assertEqual(holding["maturity_date"], supplied["maturity_date"])
            self.assertEqual(holding["coupon_rate"], supplied["coupon_rate"])
            self.assertEqual(holding["face"], face)
            self.assertAlmostEqual(holding["clean_price"] + holding["accrued_interest"], holding["dirty_price"])
        risk = result["risk_inputs"]
        reconstructed = np.array(risk["target_matrix"]) @ (np.array(FACES) / 100)
        np.testing.assert_allclose(reconstructed, risk["target_exposure"], atol=1e-10)
        self.assertAlmostEqual(sum(per100 * face / 100 for per100, face in
            zip(risk["target_parallel_dv01_per_100"], FACES)), risk["target_parallel_dv01"])
        self.config["latest"]["hedges"][0]["face"] = 200
        with self.assertRaisesRegex(ValueError, "face 100"):
            latest_analysis(self.records, self.config, self.audit, faces=FACES, valuation_date="2022-01-31", lookback=30)

    def test_funded_ledger_exposes_cash_costs_and_signed_payments(self):
        result = self.simulate(cash_rate=.02, funding_rate=.06, transaction_cost_bps=2)
        self.assertEqual(result["funding_policy"]["cash_rate_annual"], .02)
        self.assertEqual(result["funding_policy"]["funding_rate_annual"], .06)
        self.assertEqual(result["funding_policy"]["transaction_cost_bps"], 2)
        first_day = result["start_date"]
        for row in result["rows"]:
            self.assertAlmostEqual(row["wealth"], row["cash"] + row["dirty_pv"])
            self.assertLessEqual(abs(row["cash_roll_residual"]), row["audit_tolerance"])
            self.assertEqual(row["external_cash"], result["initial_equity"] if row["date"] == first_day else 0)
            self.assertIn("collateral_shortfall", row)
            self.assertIn("financing_gap", row)
        # All three target coupons pay January18 after weekend and MLK closure.
        unhedged = result["summaries"]["unhedged"]["accounting_totals"]
        self.assertAlmostEqual(unhedged["cumulative_coupon_cash"], 76.25)
        self.assertEqual(unhedged["cumulative_principal_cash"], 0)
        self.assertGreater(unhedged["cumulative_cash_interest"], 0)
        self.assertGreater(unhedged["cumulative_transaction_cost"], 0)

    def test_synthetic_source_hash_loader_requires_unchanged_term_text(self):
        with tempfile.TemporaryDirectory(prefix="treasury_research_test_") as directory:
            path = Path(directory) / "synthetic_instruments.json"
            path.write_text(json.dumps(self.config), encoding="utf-8")
            # Isolate local transcription integrity from the separately tested
            # official-source/configuration loader; these terms are synthetic.
            with patch("treasury_risk.research.validated_config", side_effect=lambda p: json.loads(Path(p).read_text())):
                self.assertEqual(load_instrument_config(path), self.config)
                self.config["historical"]["targets"][0]["source_snapshot_text"] += " tampered"
                path.write_text(json.dumps(self.config), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    load_instrument_config(path)


if __name__ == "__main__":
    unittest.main()
