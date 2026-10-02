"""Analytic and chronological checks of the research diagnostic, without downloads."""

import math
import unittest

from treasury_risk.analysis import (
    benchmark_curves, build_analysis, curve_from_record, default_hedges,
    default_portfolio, hedge_setup, historical_diagnostic, repricing_changes,
)
from treasury_risk.bonds import CouponBond
from treasury_risk.curve import BumpedCurve, SvenssonCurve


def record(date, rate=4.0):
    return {"date": date, "beta0": rate, "beta1": 0.0, "beta2": 0.0,
            "beta3": 0.0, "tau1": 1.0, "tau2": 2.0,
            "published_zero_yields": {i: rate / 100 for i in range(1, 31)}}


class AnalysisTests(unittest.TestCase):
    def test_external_benchmark_percent_fraction_and_rounding_units(self):
        item = record("2022-01-03")
        item["published_zero_yields"][1] += 0.0000005  # 0.005bp rounding
        result = benchmark_curves([item])
        self.assertEqual(result["comparisons"], 30)
        self.assertAlmostEqual(result["max_absolute_error_bps"], 0.005, places=10)
        item["published_zero_yields"][1] += 0.00000011  # now 0.0061bp
        with self.assertRaisesRegex(ValueError, "Curve benchmark failed"):
            benchmark_curves([item])

    def test_missing_benchmarks_are_counted_but_no_benchmark_fails(self):
        item = record("2022-01-03")
        item["published_zero_yields"][30] = None
        self.assertEqual(benchmark_curves([item])["missing_published_values"], 1)
        item["published_zero_yields"] = {}
        with self.assertRaisesRegex(ValueError, "No published"):
            benchmark_curves([item])
        with self.assertRaisesRegex(ValueError, "No valid"):
            benchmark_curves([])

    def test_duration_hedge_and_full_repricing_against_zero_bond_formula(self):
        curve = SvenssonCurve(4, 0, 0, 0, 1, 2)
        target, instruments = [CouponBond(2, 0, 1000)], [CouponBond(10, 0, 100)]
        setup = hedge_setup(target, instruments, curve)
        target_dv01 = 1000 * math.exp(-0.04 * 2) * math.sinh(0.0001 * 2)
        hedge_dv01 = 100 * math.exp(-0.04 * 10) * math.sinh(0.0001 * 10)
        weight = -target_dv01 / hedge_dv01
        self.assertAlmostEqual(setup["duration_hedge"]["weights"][0], weight, places=10)
        self.assertAlmostEqual(setup["duration_hedge"]["parallel_residual_dv01"], 0, places=12)
        result = repricing_changes(target, instruments, curve, BumpedCurve(curve, parallel_bps=100), setup)
        target_change = 1000 * (math.exp(-0.05 * 2) - math.exp(-0.04 * 2))
        hedge_change = 100 * (math.exp(-0.05 * 10) - math.exp(-0.04 * 10))
        self.assertAlmostEqual(result["unhedged_change"], target_change, places=10)
        self.assertAlmostEqual(result["duration_hedged_change"], target_change + weight * hedge_change, places=10)

    def test_historical_hedge_weights_use_first_curve_and_fixed_tenors(self):
        target, instruments = [CouponBond(2, 0, 1000)], [CouponBond(10, 0, 100)]
        records = [record("2022-01-03", 4), record("2022-12-30", 5)]
        result = historical_diagnostic(records, target, instruments)
        expected_weight = hedge_setup(target, instruments, curve_from_record(records[0]))["duration_hedge"]["weights"][0]
        self.assertAlmostEqual(result["duration_face_amounts"][0], expected_weight * 100)
        expected_change = 1000 * (math.exp(-0.05 * 2) - math.exp(-0.04 * 2))
        self.assertAlmostEqual(result["rows"][-1]["unhedged_change"], expected_change, places=10)
        self.assertEqual(result["rows"][0]["unhedged_change"], 0)
        altered = historical_diagnostic([records[0], record("2022-12-30", 9)], target, instruments)
        self.assertEqual(result["duration_face_amounts"], altered["duration_face_amounts"])
        self.assertIn("no maturity aging", result["label"])

    def test_insufficient_historical_data_rejected(self):
        with self.assertRaisesRegex(ValueError, "At least two"):
            historical_diagnostic([record("2022-01-03")], default_portfolio(), default_hedges())

    def test_hedge_face_units_cannot_silently_misstate_positions(self):
        with self.assertRaisesRegex(ValueError, "exactly 100 face"):
            hedge_setup([CouponBond(2, 0, 1000)], [CouponBond(10, 0, 200)],
                        SvenssonCurve(4, 0, 0, 0, 1, 2))

    def test_multi_hedge_reduces_bucket_norm_and_reports_unspanned_risk(self):
        curve = SvenssonCurve(4, 0, 0, 0, 1, 2)
        setup = hedge_setup(default_portfolio(), default_hedges(), curve)
        multi, duration = setup["multi_hedge"], setup["duration_hedge"]
        self.assertLess(multi["residual_norm"], duration["residual_norm"])
        self.assertFalse(multi["fully_spans_key_rates"])
        self.assertEqual(multi["matrix_rank"], 5)
        self.assertAlmostEqual(duration["parallel_residual_dv01"], 0, places=8)

    def test_complete_pack_uses_latest_curve_and_documents_historical_scope(self):
        items = [record("2022-01-03", 4), record("2022-12-30", 5), record("2026-09-25", 4.5)]
        pack = build_analysis(items, {"source_url": "fixture"}, {"valid": 3})
        self.assertEqual(pack["latest"]["date"], "2026-09-25")
        self.assertEqual(pack["historical"]["observations"], 2)
        self.assertEqual(pack["benchmark"]["comparisons"], 90)
        self.assertEqual(len(pack["latest"]["scenarios"]), 4)
        self.assertIn("no maturity aging", pack["historical"]["label"])
        self.assertTrue(all(math.isfinite(value["multi_hedged_change"]) for value in pack["latest"]["scenarios"]))

    def test_old_source_exceptions_are_preserved_and_required_dates_fail_closed(self):
        old = record("2013-10-16")
        old["published_zero_yields"][6] += 0.000003
        missing = record("2014-01-02")
        missing["published_zero_yields"] = {}
        items = [old, missing, record("2022-01-03"), record("2022-12-30"), record("2026-09-25")]
        pack = build_analysis(items, {}, {})
        self.assertEqual(pack["status"], "research_demo_with_source_exceptions")
        self.assertEqual(pack["benchmark"]["quarantined_observations"], 2)
        self.assertEqual(pack["benchmark"]["failed_comparisons"], 1)
        self.assertEqual(pack["benchmark"]["exceptions"][1]["status"], "benchmark_unavailable")
        items[-1]["published_zero_yields"][1] = 0.05
        with self.assertRaisesRegex(ValueError, "Required valuation dates fail"):
            build_analysis(items, {}, {})

    def test_bad_historical_replay_date_cannot_be_silently_dropped(self):
        items = [record("2022-01-03"), record("2022-12-30"), record("2026-09-25")]
        items[1]["published_zero_yields"][30] = None
        # A partial but available independent benchmark is retained and counted.
        self.assertEqual(build_analysis(items, {}, {})["benchmark"]["missing_published_values"], 1)
        items[1]["published_zero_yields"] = {}
        with self.assertRaisesRegex(ValueError, "Required valuation dates fail"):
            build_analysis(items, {}, {})


if __name__ == "__main__":
    unittest.main()
