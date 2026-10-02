"""Calendar, actual coupon-period accrual, and payment-ownership oracles."""

import math
import unittest
from datetime import date

from treasury_risk.curve import KEY_RATE_NODES, CurveMathError, SvenssonCurve
from treasury_risk.dated_bonds import (
    DatedBond, following_business_day, is_business_day, year_fraction,
)


def flat(percent=0):
    return SvenssonCurve(percent, 0, 0, 0, 1, 2)


class BankCalendarTests(unittest.TestCase):
    def test_bank_saturday_holidays_do_not_close_the_previous_friday(self):
        self.assertTrue(is_business_day("2021-12-31"))  # Jan1,2022 Saturday.
        self.assertTrue(is_business_day("2026-07-03"))  # July4 Saturday.
        self.assertFalse(is_business_day("2023-01-02"))  # Jan1 Sunday -> Monday.
        self.assertEqual(following_business_day("2023-01-01"), date(2023, 1, 3))

    def test_juneteenth_calendar_starts_with_2022_bank_holidays(self):
        self.assertTrue(is_business_day("2021-06-18"))
        self.assertTrue(is_business_day("2021-06-21"))
        self.assertFalse(is_business_day("2022-06-20"))
        self.assertEqual(following_business_day("2022-06-19"), date(2022, 6, 21))
        self.assertTrue(is_business_day("2027-06-18"))

    def test_regular_bank_calendar_is_not_a_bond_trading_holiday_calendar(self):
        self.assertTrue(is_business_day("2024-03-29"))  # Good Friday.
        self.assertFalse(is_business_day("2024-11-28"))
        self.assertEqual(following_business_day("2024-11-28"), date(2024, 11, 29))


class DatedBondTests(unittest.TestCase):
    def test_maturity_anchoring_preserves_eom_and_leap_coupon_periods(self):
        bond = DatedBond("000000001", "EOM fixture", "2023-08-31", "2024-08-31", .06, face=1000)
        self.assertEqual(bond.coupon_schedule(), (date(2024, 2, 29), date(2024, 8, 31)))
        self.assertEqual(bond.cashflows("2023-08-31"), ((date(2024, 2, 29), 30), (date(2024, 9, 3), 1030)))
        self.assertEqual((date(2024, 2, 29) - date(2023, 8, 31)).days, 182)
        self.assertEqual((date(2024, 8, 31) - date(2024, 2, 29)).days, 184)

    def test_act_act_accrued_interest_uses_unadjusted_period_and_leap_days(self):
        bond = DatedBond("000000001", "Leap fixture", "2023-08-31", "2024-08-31", .06, face=1000)
        self.assertAlmostEqual(bond.accrued_interest("2024-02-15"), 30 * 168 / 182, places=12)
        self.assertEqual(bond.accrued_interest("2024-02-29"), 0)
        self.assertAlmostEqual(bond.accrued_interest("2024-03-01"), 30 / 184, places=12)

    def test_actual_issue_can_differ_from_regular_dated_accrual_origin(self):
        bond = DatedBond("000000002", "Issue delayed fixture", "2021-02-16", "2022-02-15", .06,
                         face=1000, dated_date="2021-02-15")
        self.assertAlmostEqual(bond.accrued_interest("2021-02-16"), 30 / 181, places=12)
        self.assertEqual(bond.cashflows("2021-02-16")[0], (date(2021, 8, 16), 30))
        with self.assertRaises(CurveMathError):
            bond.price(flat(), "2021-02-15")

    def test_settlement_on_payment_date_excludes_that_coupon_for_a_new_purchase(self):
        bond = DatedBond("000000003", "Ownership fixture", "2022-05-16", "2023-05-15", .05,
                         dated_date="2022-05-15")
        self.assertEqual(bond.cashflows("2022-11-14")[0], (date(2022, 11, 15), 2.5))
        self.assertEqual(bond.cashflows("2022-11-15"), ((date(2023, 5, 15), 102.5),))
        self.assertEqual(bond.accrued_interest("2022-11-15"), 0)
        events = bond.coupon_events("2022-11-14", "2022-11-15")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].coupon, 2.5)
        self.assertEqual(bond.coupon_events("2022-11-15", "2022-11-15"), ())

    def test_delayed_final_payment_has_no_extra_coupon_and_no_post_maturity_accrual(self):
        bond = DatedBond("000000004", "Delayed maturity", "2021-05-15", "2022-05-15", .05)
        self.assertEqual(bond.cashflows("2022-05-15"), ((date(2022, 5, 16), 102.5),))
        self.assertEqual(bond.accrued_interest("2022-05-15"), 0)
        self.assertEqual(bond.price(flat(), "2022-05-15"), 102.5)
        for day in ("2022-05-16", "2022-06-01"):
            with self.subTest(day=day):
                self.assertEqual(bond.price(flat(), day), 0)
                self.assertEqual(bond.clean_price(flat(), day), 0)
                self.assertEqual(bond.accrued_interest(day), 0)
                self.assertEqual(bond.parallel_dv01(flat(), day), 0)
                self.assertEqual(set(bond.key_rate_dv01(flat(), day).values()), {0})
                self.assertIsNone(bond.effective_duration(flat(), day))
                self.assertIsNone(bond.convexity(flat(), day))

    def test_curve_pv_uses_actual_days_to_adjusted_payment_and_clean_dirty_identity(self):
        bond = DatedBond("000000004", "Delayed maturity", "2021-05-15", "2022-05-15", .05)
        years = 3 / 365  # FriMay13 -> adjustedMonMay16.
        self.assertEqual(year_fraction("2022-05-13", "2022-05-16"), years)
        self.assertAlmostEqual(bond.price(flat(5), "2022-05-13"), 102.5 * math.exp(-.05 * years), places=12)
        self.assertAlmostEqual(bond.clean_price(flat(5), "2022-05-13") + bond.accrued_interest("2022-05-13"),
                               bond.price(flat(5), "2022-05-13"), places=12)

    def test_dated_zero_coupon_and_risk_match_actual_time_analytic_oracle(self):
        bond = DatedBond("000000005", "Zero fixture", "2023-10-31", "2024-10-31", 0)
        time = (date(2024, 10, 31) - date(2024, 3, 1)).days / 365
        expected = 100 * math.exp(-.05 * time)
        self.assertAlmostEqual(bond.price(flat(5), "2024-03-01"), expected, places=12)
        self.assertAlmostEqual(bond.parallel_dv01(flat(5), "2024-03-01"), expected * math.sinh(time * .0001), places=12)
        buckets = bond.key_rate_dv01(flat(5), "2024-03-01")
        self.assertEqual(tuple(buckets), KEY_RATE_NODES)
        self.assertTrue(math.isclose(sum(buckets.values()), bond.parallel_dv01(flat(5), "2024-03-01"), rel_tol=2e-6))
        self.assertEqual(bond.zero_yield(flat(5), "2024-03-01"), .05)
        self.assertEqual(bond.forward_yield(flat(5), "2024-03-01"), .05)

    def test_underflowed_risk_denominators_raise_the_model_precision_error(self):
        # The remaining dirty PV is positive and representable, but normalizing
        # by a bp (or bp squared) cannot be represented for this tiny face.
        bond = DatedBond("000000005", "Subnormal face fixture", "2023-01-31", "2024-01-31", 0, face=1e-320)
        self.assertGreater(bond.price(flat(4), "2024-01-30"), 0)
        for method in (bond.effective_duration, bond.convexity):
            with self.subTest(method=method.__name__), self.assertRaisesRegex(CurveMathError, "denominator"):
                method(flat(4), "2024-01-30")
        # A paid-off position retains the existing undefined-risk result.
        self.assertIsNone(bond.effective_duration(flat(4), "2024-01-31"))
        self.assertIsNone(bond.convexity(flat(4), "2024-01-31"))

    def test_dated_normalized_zero_coupon_risk_matches_the_analytic_oracle(self):
        bond = DatedBond("000000005", "Normal face fixture", "2023-10-31", "2024-10-31", 0)
        time = (date(2024, 10, 31) - date(2024, 3, 1)).days / 365
        self.assertAlmostEqual(bond.effective_duration(flat(5), "2024-03-01"),
                               math.sinh(time * .0001) / .0001, places=10)
        expected_convexity = 2 * (math.cosh(time * .0001) - 1) / .0001**2
        self.assertAlmostEqual(bond.convexity(flat(5), "2024-03-01"), expected_convexity, delta=1e-7)

    def test_stubs_invalid_dates_and_unsupported_terms_reject(self):
        invalid = [{"issue_date": "2023-09-01"}, {"frequency": 1}, {"coupon_rate": -.01},
                   {"face": 0}, {"cusip": "BAD"}, {"issue_date": "2024-08-31"},
                   {"issue_date": "2023-02-30"}, {"maturity_date": "2101-08-31"}]
        for change in invalid:
            kwargs = {"cusip": "000000001", "label": "Fixture", "issue_date": "2023-08-31",
                      "maturity_date": "2024-08-31", "coupon_rate": .05, **change}
            with self.subTest(change=change), self.assertRaises(CurveMathError):
                DatedBond(**kwargs)


if __name__ == "__main__":
    unittest.main()
