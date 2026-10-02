"""Offline release-integrity regressions; fixtures contain no real price claims."""

from copy import deepcopy
import csv
from datetime import date, timedelta
import hashlib
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from scripts import build_release, verify_release
from treasury_risk.analysis import curve_from_record
from treasury_risk.dated_bonds import is_business_day
from treasury_risk.research import instruments, latest_analysis, risk_inputs, simulate_period


def synthetic_term(cusip, maturity, coupon=.04):
    text = f"OFFLINE SYNTHETIC regular-coupon fixture {cusip}; not an actual Treasury auction"
    return {"cusip": cusip, "label": f"Synthetic {cusip}", "issue_date": "2018-01-15",
            "dated_date": "2018-01-15", "maturity_date": maturity, "coupon_rate": coupon,
            "face": 100, "source_url": "https://example.invalid/offline-fixture",
            "original_official_yield": coupon, "original_official_price_per_100": 100.0,
            "source_snapshot_text": text,
            "source_snapshot_sha256": hashlib.sha256(text.encode()).hexdigest()}


def synthetic_complete_pack(*, capital_buffer_multiple=.10):
    targets = [synthetic_term("TARGET001", "2025-01-15", .035),
               synthetic_term("TARGET002", "2027-01-15", .0425),
               synthetic_term("TARGET003", "2030-01-15", .0475)]
    hedges = [synthetic_term(f"HEDGE000{i}", maturity) for i, maturity in enumerate(
        ("2023-01-15", "2026-01-15", "2030-01-15", "2035-01-15", "2045-01-15"), 1)]
    config = {"schema_version": 1, "latest": {"valuation_date": "2023-01-04", "targets": deepcopy(targets), "hedges": deepcopy(hedges)},
              "historical": {"valuation_date": "2022-01-03", "targets": deepcopy(targets), "hedges": deepcopy(hedges)}}
    records = []
    day = date(2021, 10, 1)
    while day <= date(2023, 1, 4):
        if is_business_day(day):
            index = len(records)
            records.append({"date": day.isoformat(), "beta0": 2 + index * .0005 + .03 * math.sin(index * .31),
                            "beta1": -.6 + .02 * math.cos(index * .23), "beta2": .4 + .06 * math.sin(index * .17),
                            "beta3": .2 + .03 * math.cos(index * .11), "tau1": 1.2, "tau2": 4.0})
        day += timedelta(days=1)
    audit = {"exceptions": []}
    faces = (1000, 1200, 1400)
    latest = latest_analysis(records, config, audit, faces=faces, valuation_date="2023-01-04", lookback=30)
    periods = [simulate_period(records, config, audit, f"{year}-01-03", f"{year}-01-04",
                               faces=faces, lookback=30, capital_buffer_multiple=capital_buffer_multiple)
               for year in (2022, 2023)]
    # An independently verifiable saved decision needs its actual numerical inputs.
    # Retain compatibility with the pre-review writer that omitted this field.
    target_bonds, hedge_bonds = instruments(config, "historical", "targets"), instruments(config, "historical", "hedges")
    by_date = {row["date"]: row for row in records}
    for period in periods:
        period.setdefault("faces", list(faces))
        period.setdefault("strategy_constraints", {"gross_face_limit": sum(faces) * 2,
            "position_face_limit": sum(faces) * 1.5, "cash_neutral": False})
        for decision in period["decisions"]:
            if "risk_inputs" not in decision:
                active = [bond for bond in hedge_bonds if bond.cusip in decision["active_hedges"]]
                decision["risk_inputs"] = risk_inputs(target_bonds, active, list(faces),
                    curve_from_record(by_date[decision["decision_curve_date"]]), decision["trade_date"])
    benchmarks = [{"cusip": item["cusip"], "settlement_date": item["issue_date"],
                   "official_clean_price_per_100": 100.0, "calculated_clean_price_per_100": 100.0,
                   "calculated_accrued_interest_per_100": 0.0, "error_per_100": 0.0,
                   "tolerance_per_100": .000001, "passed": True,
                   "source_url": item["source_url"], "convention": "OFFLINE SYNTHETIC exact equality fixture"}
                  for era in ("latest", "historical") for group in ("targets", "hedges") for item in config[era][group]]
    config_bytes = json.dumps(config).encode()
    return {"version": "1.0.0", "schema_version": 1, "status": "complete_research_release",
            "code_sha256": "synthetic-model-hash", "source": {"sha256": "synthetic-curve-hash"},
            "instrument_config_sha256": hashlib.sha256(config_bytes).hexdigest(),
            "instrument_config": config, "latest": latest, "periods": periods,
            "auction_benchmarks": benchmarks}, config_bytes


class ReleaseValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.complete, cls.config_bytes = synthetic_complete_pack()

    def setUp(self):
        self.pack = deepcopy(self.complete)

    def test_complete_small_offline_pack_is_verified(self):
        result = verify_release.verify_pack(self.pack)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["ledger_rows_reconciled"], 16)
        self.assertEqual(result["prior_date_decisions_verified"], 2)
        self.assertEqual(result["auction_price_checks"], 16)

    def test_missing_history_or_auction_evidence_is_rejected(self):
        for field in ("periods", "auction_benchmarks"):
            with self.subTest(field=field):
                altered = deepcopy(self.pack)
                altered[field] = []
                with self.assertRaises(ValueError):
                    verify_release.verify_pack(altered)

    def test_auction_pass_flag_cannot_override_reported_price_error(self):
        row = self.pack["auction_benchmarks"][0]
        row["calculated_clean_price_per_100"] += 1.0
        self.assertTrue(row["passed"])
        with self.assertRaises(ValueError):
            verify_release.verify_pack(self.pack)

    def test_declared_observation_count_requires_all_four_methods(self):
        self.pack["periods"][0]["rows"] = [row for row in self.pack["periods"][0]["rows"]
                                            if row["method"] != "duration"]
        with self.assertRaises(ValueError):
            verify_release.verify_pack(self.pack)

    def test_saved_face_amounts_must_match_weights(self):
        result = self.pack["periods"][0]["decisions"][0]["methods"]["constrained"]
        self.assertTrue(any(result["weights"]))
        result["face_amounts"] = [0.0] * len(result["weights"])
        with self.assertRaises(ValueError):
            verify_release.verify_pack(self.pack)

    def test_latest_and_history_capacity_is_reconstructed(self):
        for section in ("latest", "historical"):
            with self.subTest(section=section):
                altered = deepcopy(self.pack)
                result = (altered["latest"]["methods"]["constrained"] if section == "latest" else
                          altered["periods"][0]["decisions"][0]["methods"]["constrained"])
                result["constraints"]["gross_face_limit"] = 0.0
                result["constraints"]["position_face_limits"] = [0.0] * len(result["weights"])
                with self.assertRaises(ValueError):
                    verify_release.verify_pack(altered)

    def test_parallel_equality_cannot_be_certified_by_saved_zero_residual(self):
        decision = self.pack["periods"][0]["decisions"][0]
        decision["risk_inputs"]["target_parallel_dv01"] += 1.0
        self.assertLess(abs(decision["methods"]["constrained"]["parallel_residual_dv01"]), 1e-6)
        with self.assertRaises(ValueError):
            verify_release.verify_pack(self.pack)

    def test_feasible_suboptimal_weights_cannot_use_a_forged_zero_gap(self):
        latest = self.pack["latest"]
        inputs, result = latest["risk_inputs"], latest["methods"]["constrained"]
        target, matrix = np.array(inputs["target_exposure"]), np.array(inputs["hedge_matrix"])
        parallel = np.array(inputs["hedge_parallel_dv01"])
        covariance = np.array(latest["covariance"]["covariance"])
        weights = np.array(result["weights"])
        # This direction is independently orthogonal to the one equality.
        weights[0] += 20.0
        weights[1] -= 20.0 * parallel[0] / parallel[1]
        self.assertAlmostEqual(inputs["target_parallel_dv01"] + parallel @ weights, 0, places=8)
        residual = target + matrix @ weights
        variance = float(residual @ covariance @ residual)
        self.assertGreater(variance, result["variance_after"] * 1.01)
        faces = 100 * weights
        gross = float(np.abs(faces).sum())
        self.assertLess(gross, result["constraints"]["gross_face_limit"])
        self.assertTrue(np.all(np.abs(faces) < result["constraints"]["position_face_limits"]))
        result.update(weights=weights.tolist(), face_amounts=faces.tolist(), residual_exposure=residual.tolist(),
                      variance_after=variance, residual_norm=float(np.linalg.norm(residual)),
                      parallel_residual_dv01=float(inputs["target_parallel_dv01"] + parallel @ weights),
                      hedge_cash_dollars=float(np.array(inputs["hedge_prices"]) @ weights),
                      gross_absolute_face_units=gross, objective_value=variance,
                      variance_reduction_pct=100 * (1 - variance / result["variance_before"]),
                      gross_face_capacity_remaining=result["constraints"]["gross_face_limit"] - gross,
                      position_face_capacity_remaining=(np.array(result["constraints"]["position_face_limits"]) - np.abs(faces)).tolist())
        result["solver_diagnostics"]["normalized_first_order_gap"] = 0.0
        with self.assertRaises(ValueError):
            verify_release.verify_pack(self.pack)

    def test_cash_neutrality_is_checked_when_enabled(self):
        result = self.pack["latest"]["methods"]["constrained"]
        self.assertGreater(abs(result["hedge_cash_dollars"]), 1.0)
        result["constraints"]["cash_neutral"] = True
        self.pack["latest"]["constraints"]["cash_neutral"] = True
        with self.assertRaises(ValueError):
            verify_release.verify_pack(self.pack)

    def test_nonfinite_ledger_amounts_cannot_pass_absolute_difference_checks(self):
        for bad in (float("nan"), float("inf")):
            with self.subTest(value=bad):
                altered = deepcopy(self.pack)
                altered["periods"][0]["rows"][0]["cash"] = bad
                with self.assertRaises(ValueError):
                    verify_release.verify_pack(altered)

    def test_saved_audit_tolerance_cannot_hide_material_cash_error(self):
        row = self.pack["periods"][0]["rows"][-1]
        row["cash"] += 1000
        row["wealth"] += 1000
        row["audit_tolerance"] = 1e20
        with self.assertRaises(ValueError):
            verify_release.verify_pack(self.pack)

    def test_summary_cannot_disagree_with_final_ledger_wealth(self):
        self.pack["periods"][0]["summaries"]["unhedged"]["net_pnl"] += 1000
        with self.assertRaises(ValueError):
            verify_release.verify_pack(self.pack)

    def test_actual_funding_and_collateral_breaches_cannot_be_relabelled_safe(self):
        # With no capital buffer the engine retains unfunded costs and short
        # collateral deficiencies rather than injecting additional equity.
        underfunded, _ = synthetic_complete_pack(capital_buffer_multiple=0)
        self.assertEqual(verify_release.verify_pack(underfunded)["status"], "passed")
        for field in ("collateral_breach", "financing_breach", "collateral_shortfall"):
            with self.subTest(field=field):
                altered = deepcopy(underfunded)
                affected = [row for period in altered["periods"] for row in period["rows"] if row[field]]
                self.assertTrue(affected, field)
                for row in affected:
                    row[field] = 0.0 if field == "collateral_shortfall" else False
                if field == "collateral_breach":
                    for period in altered["periods"]:
                        for summary in period["summaries"].values():
                            summary["collateral_breach_observations"] = 0
                with self.assertRaises(ValueError):
                    verify_release.verify_pack(altered)

    def make_release_directory(self, root):
        (root / "configs").mkdir()
        (root / "configs/treasury_instruments.json").write_bytes(self.config_bytes)
        output = root / "release"
        output.mkdir()
        (output / "release.json").write_text(json.dumps(self.pack), encoding="utf-8")
        (output / "report.html").write_text("<!doctype html><p>Offline synthetic artifact fixture</p>")
        for name in ("risk_and_factors.png", "wealth_2022.png", "wealth_2023.png"):
            (output / name).write_bytes(b"offline synthetic chart artifact; no real chart claim")
        for period in self.pack["periods"]:
            stream = io.StringIO(newline="")
            writer = csv.DictWriter(stream, fieldnames=list(period["rows"][0]))
            writer.writeheader()
            writer.writerows(period["rows"])
            (output / f"ledger_{period['start_date'][:4]}.csv").write_text(stream.getvalue(), encoding="utf-8")
        manifest = {"version": "1.0.0", "status": "complete_research_release", "files": {
            path.name: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
            for path in output.iterdir()}}
        (output / "artifact_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return output

    def test_manifest_requires_all_published_artifacts(self):
        for omission in ("all", "ledger_2023.csv", "release.json"):
            with self.subTest(omission=omission), tempfile.TemporaryDirectory(prefix="treasury_release_audit_") as directory:
                root = Path(directory)
                output = self.make_release_directory(root)
                manifest_path = output / "artifact_manifest.json"
                manifest = json.loads(manifest_path.read_text())
                if omission == "all":
                    manifest["files"] = {}
                else:
                    manifest["files"].pop(omission)
                manifest_path.write_text(json.dumps(manifest))
                with patch.object(verify_release, "ROOT", root), \
                     patch.object(verify_release, "code_fingerprint", return_value="synthetic-model-hash"), \
                     patch.object(verify_release, "validate_source_manifest", return_value={"sha256": "synthetic-curve-hash"}):
                    with self.assertRaises(ValueError):
                        verify_release.verify(output)

    def test_complete_seven_artifact_manifest_is_verified(self):
        with tempfile.TemporaryDirectory(prefix="treasury_release_audit_") as directory:
            root = Path(directory)
            output = self.make_release_directory(root)
            with patch.object(verify_release, "ROOT", root), \
                 patch.object(verify_release, "code_fingerprint", return_value="synthetic-model-hash"), \
                 patch.object(verify_release, "validate_source_manifest", return_value={"sha256": "synthetic-curve-hash"}):
                result = verify_release.verify(output)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["artifacts_verified"], 7)

    def test_embedded_instrument_terms_match_pinned_config_bytes(self):
        self.pack["instrument_config"]["latest"]["targets"][0]["coupon_rate"] += .01
        with tempfile.TemporaryDirectory(prefix="treasury_release_provenance_") as directory:
            root = Path(directory)
            output = self.make_release_directory(root)
            with patch.object(verify_release, "ROOT", root), \
                 patch.object(verify_release, "code_fingerprint", return_value="synthetic-model-hash"), \
                 patch.object(verify_release, "validate_source_manifest", return_value={"sha256": "synthetic-curve-hash"}):
                with self.assertRaises(ValueError):
                    verify_release.verify(output)

    def test_failed_rebuild_removes_generated_ledgers_and_charts(self):
        with tempfile.TemporaryDirectory(prefix="treasury_release_failure_") as directory:
            root = Path(directory)
            output = self.make_release_directory(root)
            notes = output / "reader_notes.txt"
            notes.write_text("User-owned notes must survive a failed rebuild")
            with patch.object(build_release, "validate_source_manifest", side_effect=ValueError("synthetic source rejected")):
                with self.assertRaisesRegex(ValueError, "synthetic source rejected"):
                    build_release.build(output)
            self.assertEqual(json.loads((output / "release.json").read_text())["status"], "failed")
            self.assertEqual(json.loads((output / "artifact_manifest.json").read_text())["status"], "failed")
            for name in ("ledger_2022.csv", "ledger_2023.csv", "risk_and_factors.png", "wealth_2022.png", "wealth_2023.png"):
                self.assertFalse((output / name).exists(), name)
            self.assertTrue(notes.exists())


if __name__ == "__main__":
    unittest.main()
