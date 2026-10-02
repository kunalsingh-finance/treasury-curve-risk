from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from treasury_risk.benchmarks import BenchmarkError, load_instrument_config, official_auction_benchmarks, treasury_price_from_yield


class IndependentTreasuryBenchmarkTests(unittest.TestCase):
    def test_original_treasury_ten_year_auction_price(self):
        # Official 2021-11-09 result: CUSIP 91282CDJ7, issue 2021-11-15.
        result = treasury_price_from_yield("2021-11-15", "2031-11-15", 0.01375, 0.01444)
        self.assertAlmostEqual(result["clean_price"], 99.359650, delta=0.000001)
        self.assertEqual(result["accrued_interest"], 0)
        self.assertEqual(result["coupon_periods_remaining"], 20)

    def test_original_treasury_twenty_year_auction_and_accrual(self):
        # Official 2021-11-17 result: 912810TC2, $0.82873 AI per $1000.
        result = treasury_price_from_yield("2021-11-30", "2041-11-15", 0.02, 0.02065)
        self.assertAlmostEqual(result["clean_price"], 98.940421, delta=0.000001)
        self.assertAlmostEqual(result["accrued_interest"] * 10, 0.82873, delta=0.000005)
        self.assertEqual(result["days_accrued"], 15)
        self.assertEqual(result["days_in_coupon_period"], 181)
        self.assertAlmostEqual(result["dirty_price"], result["clean_price"] + result["accrued_interest"], places=12)

    def test_appendix_b_published_fractional_period_example(self):
        # Appendix B II.D: 9.5% note, 9.54% yield; rounded example P=99.730918.
        result = treasury_price_from_yield("1985-11-29", "1995-11-15", "0.095", "0.0954")
        self.assertAlmostEqual(result["clean_price"], 99.730918, delta=0.000001)
        self.assertAlmostEqual(result["accrued_interest"], 0.367403, delta=0.000001)
        self.assertEqual(result["days_to_next_coupon"], 167)
        self.assertEqual(result["days_in_coupon_period"], 181)

    def test_end_of_month_and_leap_year_coupon_period(self):
        result = treasury_price_from_yield("2024-02-29", "2025-08-31", 0.04, 0.04)
        self.assertEqual(result["previous_coupon_date"], "2024-02-29")
        self.assertEqual(result["next_coupon_date"], "2024-08-31")
        self.assertEqual(result["accrued_interest"], 0)
        self.assertAlmostEqual(result["clean_price"], 100, places=12)

    def test_face_scaling_zero_yield_and_clean_dirty_identity(self):
        result = treasury_price_from_yield("2022-01-03", "2026-11-30", 0.0125, 0, 1000)
        self.assertAlmostEqual(result["dirty_price"], 1062.5, places=10)
        self.assertGreater(result["accrued_interest"], 0)
        base = treasury_price_from_yield("2022-01-03", "2026-11-30", 0.0125, 0, 100)
        self.assertAlmostEqual(result["clean_price"], 10 * base["clean_price"], places=10)

    def test_invalid_and_unsupported_inputs_reject(self):
        for args in (
            ("20220103", "2026-11-30", 0.01, 0.02),
            ("2022-02-30", "2026-11-30", 0.01, 0.02),
            ("2026-11-30", "2026-11-30", 0.01, 0.02),
            ("2022-01-03", "2026-11-30", -0.01, 0.02),
            ("2022-01-03", "2026-11-30", 0.01, -2),
            ("2022-01-03", "2026-11-30", 0.01, float("nan")),
            ("2022-01-03", "2026-11-30", True, 0.02),
        ):
            with self.subTest(args=args), self.assertRaises(BenchmarkError):
                treasury_price_from_yield(*args)

    def test_actual_config_transcriptions_and_all_official_auction_prices(self):
        config_path = Path(__file__).resolve().parents[1] / "configs" / "treasury_instruments.json"
        config = load_instrument_config(config_path)
        instruments = [item for group in ("latest", "historical") for role in ("targets", "hedges") for item in config[group][role]]
        results = official_auction_benchmarks(instruments)
        self.assertEqual(len(results), 16)
        self.assertTrue(all(result["passed"] for result in results), results)
        self.assertNotEqual(config["latest"]["targets"][0]["cusip"], config["historical"]["targets"][0]["cusip"])

    def test_config_hash_and_inception_controls(self):
        config_path = Path(__file__).resolve().parents[1] / "configs" / "treasury_instruments.json"
        original = json.loads(config_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instruments.json"
            for key, value in (("source_snapshot_text", "changed"), ("issue_date", "2023-01-01"), ("source_url", "https://example.com/result.pdf")):
                changed = deepcopy(original)
                changed["historical"]["targets"][0][key] = value
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.subTest(key=key), self.assertRaises(BenchmarkError):
                    load_instrument_config(path)


if __name__ == "__main__":
    unittest.main()
