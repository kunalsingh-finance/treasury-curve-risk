"""Manual wealth, signed cash-flow, funding, and changed-position cost oracles."""

import unittest
from datetime import date

from treasury_risk.accounting import CashLedger, FundingPolicy, Position
from treasury_risk.curve import CurveMathError, SvenssonCurve
from treasury_risk.dated_bonds import DatedBond


def flat(percent=0):
    return SvenssonCurve(percent, 0, 0, 0, 1, 2)


def zero_bond():
    return DatedBond("000000010", "Zero cash fixture", "2023-11-30", "2024-11-30", 0)


def policy(**overrides):
    return FundingPolicy(**{"cash_rate_annual": 0, "funding_rate_annual": 0,
                            "long_haircut_rate": 0, **overrides})


class LedgerTests(unittest.TestCase):
    def test_positive_cash_interest_and_wealth_are_manually_reconciled(self):
        ledger = CashLedger([Position(zero_bond(), 1)], flat(), "2024-01-02", initial_equity=200,
                            policy=policy(cash_rate_annual=.10))
        self.assertEqual(ledger.initial_row["cash"], 100)
        row = ledger.mark(flat(), "2024-01-12")
        expected = 100 * ((1 + .10 / 365) ** 10 - 1)
        self.assertAlmostEqual(row["cash_interest"], expected, places=12)
        self.assertAlmostEqual(row["cash"], 100 + expected, places=12)
        self.assertAlmostEqual(row["wealth"], 200 + expected, places=12)
        self.assertEqual(row["funding_cost"], 0)
        self.assertLessEqual(abs(row["cash_roll_residual"]), row["audit_tolerance"])

    def test_negative_cash_charges_funding_and_exposes_unavailable_credit(self):
        ledger = CashLedger([Position(zero_bond(), 1)], flat(), "2024-01-02",
                            policy=policy(funding_rate_annual=.20))
        row = ledger.mark(flat(), "2024-01-12")
        expected = 100 * ((1 + .20 / 365) ** 10 - 1)
        self.assertAlmostEqual(row["funding_cost"], expected, places=12)
        self.assertEqual(row["cash_interest"], 0)
        self.assertAlmostEqual(row["cash"], -100 - expected, places=12)
        self.assertAlmostEqual(row["wealth"], -expected, places=12)
        self.assertTrue(row["financing_breach"])
        self.assertAlmostEqual(row["financing_gap"], 100 + expected, places=12)

    def test_short_proceeds_are_reserved_plus_margin_and_not_free_cash(self):
        ledger = CashLedger([Position(zero_bond(), -1)], flat(), "2024-01-02", initial_equity=20,
                            policy=policy(short_margin_rate=.10))
        row = ledger.initial_row
        self.assertEqual(row["cash"], 120)
        self.assertAlmostEqual(row["required_collateral"], 110, places=12)
        self.assertAlmostEqual(row["free_cash"], 10, places=12)
        self.assertEqual(row["wealth"], 20)
        self.assertFalse(row["collateral_breach"])
        insufficient = CashLedger([Position(zero_bond(), -1)], flat(), "2024-01-02", initial_equity=5,
                                  policy=policy(short_margin_rate=.10)).initial_row
        self.assertTrue(insufficient["collateral_breach"])
        self.assertAlmostEqual(insufficient["collateral_shortfall"], 5, places=12)
        self.assertEqual(insufficient["financing_gap"], 0)

    def test_transaction_costs_apply_only_to_initial_and_actual_unit_changes(self):
        bond = zero_bond()
        ledger = CashLedger([Position(bond, 1)], flat(), "2024-01-02", initial_equity=200,
                            policy=policy(transaction_cost_bps=100))
        self.assertEqual(ledger.initial_row["transaction_cost"], 1)
        self.assertEqual(ledger.initial_row["wealth"], 199)
        same = ledger.rebalance([Position(bond, 1)], flat(), "2024-01-02")
        self.assertEqual(same["transaction_cost"], 0)
        increased = ledger.rebalance([Position(bond, 2)], flat(), "2024-01-03")
        self.assertEqual(increased["gross_dirty_turnover"], 100)
        self.assertEqual(increased["transaction_cost"], 1)
        self.assertEqual(increased["cash"], -2)
        self.assertEqual(increased["wealth"], 198)
        reduced = ledger.rebalance([Position(bond, .5)], flat(), "2024-01-04")
        self.assertEqual(reduced["trade_cash"], 150)
        self.assertEqual(reduced["transaction_cost"], 1.5)
        self.assertEqual(reduced["wealth"], 196.5)
        self.assertEqual(reduced["cumulative_transaction_cost"], 3.5)

    def test_existing_holder_gets_coupon_before_same_day_new_position_trade(self):
        bond = DatedBond("000000011", "Coupon trade fixture", "2023-01-31", "2024-01-31", .10)
        ledger = CashLedger([Position(bond, 1)], flat(), "2023-07-28", initial_equity=200, policy=policy())
        self.assertEqual(ledger.initial_row["cash"], 90)
        trade = ledger.rebalance([Position(bond, 2)], flat(), "2023-07-31")
        self.assertEqual(trade["coupon_cash"], 5)
        self.assertEqual(trade["trade_cash"], -105)
        self.assertEqual(trade["cash"], -10)
        self.assertEqual(trade["dirty_pv"], 210)
        self.assertEqual(trade["wealth"], 200)
        matured = ledger.mark(flat(), "2024-01-31")
        self.assertEqual(matured["coupon_cash"], 10)
        self.assertEqual(matured["principal_cash"], 200)
        self.assertEqual(matured["cash"], 200)
        self.assertEqual(matured["dirty_pv"], 0)
        self.assertEqual(matured["wealth"], 200)

    def test_signed_short_coupon_and_principal_outflows_are_not_paid_twice(self):
        bond = DatedBond("000000011", "Short cash fixture", "2023-01-31", "2024-01-31", .10)
        ledger = CashLedger([Position(bond, -1)], flat(), "2023-07-28", policy=policy())
        self.assertEqual(ledger.initial_row["cash"], 110)
        coupon = ledger.mark(flat(), "2023-07-31")
        self.assertEqual(coupon["coupon_cash"], -5)
        self.assertEqual(coupon["cash"], 105)
        matured = ledger.mark(flat(), "2024-01-31")
        self.assertEqual(matured["coupon_cash"], -5)
        self.assertEqual(matured["principal_cash"], -100)
        self.assertEqual(matured["cash"], 0)
        self.assertEqual(matured["wealth"], 0)
        repeated = ledger.mark(flat(), "2024-01-31")
        self.assertEqual(repeated["principal_cash"], 0)
        retained = ledger.rebalance([Position(bond, -1)], flat(), "2024-02-01")
        self.assertEqual(retained["principal_cash"], 0)
        self.assertEqual(retained["transaction_cost"], 0)
        self.assertEqual(retained["wealth"], 0)

    def test_coupon_cash_carry_is_split_at_actual_payment_dates(self):
        bond = DatedBond("000000011", "Carry fixture", "2023-01-31", "2024-01-31", .10)
        ledger = CashLedger([Position(bond, 1)], flat(), "2023-07-28", initial_equity=200,
                            policy=policy(cash_rate_annual=.10))
        row = ledger.mark(flat(), "2023-08-02")
        before_coupon = 90 * ((1 + .10 / 365) ** 3 - 1)
        after_coupon = (90 + before_coupon + 5) * ((1 + .10 / 365) ** 2 - 1)
        self.assertAlmostEqual(row["cash_interest"], before_coupon + after_coupon, places=12)
        self.assertAlmostEqual(row["cash"], 95 + before_coupon + after_coupon, places=12)

    def test_curve_price_change_moves_wealth_without_inventing_external_cash(self):
        bond = zero_bond()
        start_price = bond.price(flat(5), "2024-01-02")
        ledger = CashLedger([Position(bond, 1)], flat(5), "2024-01-02", initial_equity=start_price + 10,
                            policy=policy())
        row = ledger.mark(flat(4), "2024-01-03")
        change = bond.price(flat(4), "2024-01-03") - start_price
        self.assertAlmostEqual(row["wealth"] - ledger.initial_equity, change, places=12)
        self.assertEqual(row["external_cash"], 0)
        self.assertEqual(row["coupon_cash"], 0)

    def test_failed_mark_rolls_back_cash_events_dates_and_cumulative_values(self):
        class BadCurve:
            def discount(self, time):
                return float("nan")

        ledger = CashLedger([Position(zero_bond(), 1)], flat(), "2024-01-02", initial_equity=200,
                            policy=policy(cash_rate_annual=.10))
        with self.assertRaises(CurveMathError):
            ledger.mark(BadCurve(), "2024-01-12")
        self.assertEqual(ledger.date, date(2024, 1, 2))
        self.assertEqual(ledger.cash, 100)
        self.assertEqual(ledger.cumulative_cash_interest, 0)
        self.assertEqual(len(ledger.history), 1)
        row = ledger.mark(flat(), "2024-01-12")
        self.assertAlmostEqual(row["cash_interest"], 100 * ((1 + .10 / 365) ** 10 - 1), places=12)

    def test_daily_carry_is_independent_of_intermediate_valuation_frequency(self):
        bond = DatedBond("000000011", "Carry fixture", "2023-01-31", "2024-01-31", .10)
        for equity, cash_rate, funding_rate in ((200, .10, .20), (0, .10, .20), (200, -.10, .20)):
            with self.subTest(equity=equity, cash_rate=cash_rate):
                kwargs = {"initial_equity": equity,
                          "policy": policy(cash_rate_annual=cash_rate, funding_rate_annual=funding_rate)}
                sparse = CashLedger([Position(bond, 1)], flat(), "2023-07-28", **kwargs)
                dense = CashLedger([Position(bond, 1)], flat(), "2023-07-28", **kwargs)
                for day in ("2023-07-29", "2023-07-30", "2023-07-31", "2023-08-01"):
                    dense.mark(flat(), day)
                sparse_row, dense_row = sparse.mark(flat(), "2023-08-02"), dense.mark(flat(), "2023-08-02")
                for key in ("cash", "wealth", "cumulative_coupon_cash", "cumulative_cash_interest", "cumulative_funding_cost"):
                    self.assertAlmostEqual(sparse_row[key], dense_row[key], places=12, msg=key)
                # A valuation is not a cash event: both retain the July 31 coupon anchor.
                self.assertEqual(sparse._cash_anchor_date, date(2023, 7, 31))
                self.assertEqual(dense._cash_anchor_date, date(2023, 7, 31))

    def test_negative_cash_rate_and_unsupported_daily_factors(self):
        ledger = CashLedger([], flat(), "2024-01-02", initial_equity=100,
                            policy=policy(cash_rate_annual=-.10))
        row = ledger.mark(flat(), "2024-01-12")
        expected_cash = 100 * (1 - .10 / 365) ** 10
        self.assertAlmostEqual(row["cash"], expected_cash, places=12)
        self.assertAlmostEqual(row["cash_interest"], expected_cash - 100, places=12)
        self.assertEqual(row["funding_cost"], 0)
        for rate in (-365, -366):
            with self.subTest(rate=rate), self.assertRaises(CurveMathError):
                policy(cash_rate_annual=rate)
        with self.assertRaises(CurveMathError):
            policy(cash_rate_annual=1e308, day_count_basis=.001)

    def test_overflowed_compounding_rejects_and_restores_ledger(self):
        ledger = CashLedger([], flat(), "2024-01-02", initial_equity=100,
                            policy=policy(cash_rate_annual=1e300))
        with self.assertRaises(CurveMathError):
            ledger.mark(flat(), "2024-01-12")
        self.assertEqual(ledger.date, date(2024, 1, 2))
        self.assertEqual(ledger.cash, 100)
        self.assertEqual(ledger.cumulative_cash_interest, 0)
        self.assertEqual(len(ledger.history), 1)

    def test_invalid_calendar_trades_dates_terms_and_duplicate_units_reject(self):
        with self.assertRaises(CurveMathError):
            CashLedger([Position(zero_bond(), 1)], flat(), "2024-01-01", policy=policy())
        with self.assertRaises(CurveMathError):
            CashLedger([Position(zero_bond(), 1), Position(zero_bond(), 1)], flat(), "2024-01-02", policy=policy())
        ledger = CashLedger([Position(zero_bond(), 1)], flat(), "2024-01-02", policy=policy())
        with self.assertRaises(CurveMathError):
            ledger.mark(flat(), "2024-01-01")
        with self.assertRaises(CurveMathError):
            ledger.rebalance([], flat(), "2024-01-06")
        with self.assertRaises(CurveMathError):
            FundingPolicy(0, -.01)
        with self.assertRaises(CurveMathError):
            Position(zero_bond(), float("inf"))


if __name__ == "__main__":
    unittest.main()
