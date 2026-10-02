"""Self-financing signed-position cash ledger for fitted-curve hypothetical marks.

Initial and subsequent transactions exchange dirty curve-implied PV against cash.
The same curve marks assets and trades; these are NOT tradable quotes. Existing
holders receive signed coupons/principal before ex-payment end-date marks and
trades. Positive total cash earns a stated assumed rate; negative cash incurs a
stated funding rate. Nominal annual rates compound each calendar day using
(1 + annual_rate / day_count_basis) ** days. Valuation dates do not reset the
cash accumulation anchor; only actual bond/trade cash events do so.

Short-sale proceeds remain in cash but are reserved against full short dirty
market value plus an explicit margin. Interest on reserved proceeds is an assumed
cash/rebate policy, not measured repo income. Margin and credit-limit shortfalls
remain visible diagnostics; the model does not assert funding or trading access.
Dollar calculations are floating-point approximations with reported ULP-scaled
cash-roll audit tolerances. No external cash is injected after establishment.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

from .curve import CurveMathError, DiscountCurve, _finite
from .dated_bonds import DatedBond, as_date, is_business_day


@dataclass(frozen=True)
class Position:
    """Signed units of the supplied bond face; default face100 means 100-face units."""

    bond: DatedBond
    units: float

    def __post_init__(self) -> None:
        if not isinstance(self.bond, DatedBond):
            raise CurveMathError("A ledger position requires a DatedBond")
        object.__setattr__(self, "units", _finite(self.units, "position units"))


@dataclass(frozen=True)
class FundingPolicy:
    """Hypothetical nominal annual cash/funding rates compounded daily.

    Negative cash rates are supported provided their daily factor stays positive.
    Funding rates are nonnegative. Reserved short proceeds earn the same assumed
    cash rate as other positive cash; no measured rebate or repo rate is implied.
    """

    cash_rate_annual: float
    funding_rate_annual: float
    transaction_cost_bps: float = 0.0
    short_margin_rate: float = 0.02
    long_haircut_rate: float = 0.02
    unsecured_limit: float = 0.0
    day_count_basis: float = 365.0

    def __post_init__(self) -> None:
        for name in ("cash_rate_annual", "funding_rate_annual", "transaction_cost_bps",
                     "short_margin_rate", "long_haircut_rate", "unsecured_limit", "day_count_basis"):
            object.__setattr__(self, name, _finite(getattr(self, name), name))
        for name in ("funding_rate_annual", "transaction_cost_bps", "short_margin_rate",
                     "long_haircut_rate", "unsecured_limit"):
            if getattr(self, name) < 0:
                raise CurveMathError(f"{name} must be nonnegative")
        if self.day_count_basis <= 0:
            raise CurveMathError("Cash day_count_basis must be positive")
        for name in ("cash_rate_annual", "funding_rate_annual"):
            factor = _finite(1 + getattr(self, name) / self.day_count_basis, f"{name} daily factor")
            if factor <= 0:
                raise CurveMathError(f"{name} must imply a positive daily accumulation factor")
        if self.long_haircut_rate > 1:
            raise CurveMathError("Long haircut reserve rate cannot exceed 100%")


def _positions(values: Iterable[Position]) -> dict[str, Position]:
    try:
        rows = list(values)
    except TypeError as error:
        raise CurveMathError("Positions must be an iterable of Position values") from error
    result = {}
    for position in rows:
        if not isinstance(position, Position):
            raise CurveMathError("Every ledger position must be a Position")
        if position.bond.cusip in result:
            raise CurveMathError("Duplicate CUSIP positions must be netted explicitly")
        result[position.bond.cusip] = position
    return result


def _sum(values, label: str) -> float:
    try:
        return _finite(math.fsum(values), label)
    except OverflowError as error:
        raise CurveMathError(f"{label} exceeds floating-point precision") from error


class CashLedger:
    """Mutable deterministic ledger; caller owns trading rules and curve provenance.

    initial_equity supplies the only external capital. initial_row records purchase
    costs and cash. mark(curve,date) accrues interest and held-position payments;
    rebalance(positions,curve,date) first marks old positions, then trades deltas.
    New trades require Fed Bank business dates. Marks can occur on calendar dates.
    Coupon ownership uses start < adjusted pay_date <= end for the old position.
    Positions and all output rows use actual bond-face units, including short units.
    """

    def __init__(self, positions: Iterable[Position], curve: DiscountCurve, settlement: date | str,
                 *, initial_equity: float = 0.0, policy: FundingPolicy):
        if not isinstance(policy, FundingPolicy):
            raise CurveMathError("An explicit FundingPolicy is required")
        self.policy = policy
        self.date = as_date(settlement, "initial settlement")
        if not is_business_day(self.date):
            raise CurveMathError("Initial settlement must be a Fed Bank business day")
        self.positions = _positions(positions)
        self.initial_equity = _finite(initial_equity, "initial_equity")
        self._validate_new_positions(self.positions, self.date)
        dirty = self._values(curve, self.date)["dirty_pv"]
        turnover = _sum((abs(position.units * position.bond.price(curve, self.date))
                         for position in self.positions.values()), "Initial gross turnover")
        cost = _finite(turnover * (policy.transaction_cost_bps / 10000), "Initial transaction cost")
        self.cash = _finite(self.initial_equity - dirty - cost, "Initial cash")
        self._cash_anchor_date, self._cash_anchor_balance = self.date, self.cash
        self.cumulative_coupon_cash = self.cumulative_principal_cash = 0.0
        self.cumulative_cash_interest = self.cumulative_funding_cost = 0.0
        self.cumulative_transaction_cost = cost
        self.history: list[dict[str, Any]] = []
        residual = self.cash - self.initial_equity + dirty + cost
        self.initial_row = self._snapshot(curve, self.date, coupon=0, principal=0, cash_interest=0,
                                          funding_cost=0, transaction_cost=cost, trade_cash=-dirty,
                                          turnover=turnover, residual=residual, external_cash=self.initial_equity)
        self.history.append(self.initial_row.copy())

    @staticmethod
    def _validate_new_positions(positions: dict[str, Position], day: date,
                                existing: dict[str, Position] | None = None) -> None:
        for position in positions.values():
            position.bond.cashflows(day)  # Also validates issue/settlement ordering.
            retained = existing and existing.get(position.bond.cusip) == position
            if position.units and not position.bond.cashflows(day) and not retained:
                raise CurveMathError("Cannot establish a nonzero new position after its final payment")

    def _values(self, curve: DiscountCurve, day: date) -> dict[str, float]:
        dirty_values, accrued_values, long_values, short_values = [], [], [], []
        for position in self.positions.values():
            signed = _finite(position.units * position.bond.price(curve, day), "Signed dirty position PV")
            accrued = _finite(position.units * position.bond.accrued_interest(day), "Signed accrued position PV")
            dirty_values.append(signed)
            accrued_values.append(accrued)
            long_values.append(max(signed, 0))
            short_values.append(max(-signed, 0))
        dirty = _sum(dirty_values, "Portfolio dirty PV")
        accrued = _sum(accrued_values, "Portfolio accrued PV")
        return {"dirty_pv": dirty, "accrued_pv": accrued,
                "clean_pv": _finite(dirty - accrued, "Portfolio clean PV"),
                "long_dirty_value": _sum(long_values, "Long dirty value"),
                "short_dirty_value": _sum(short_values, "Short dirty value")}

    def _snapshot(self, curve, day, *, coupon, principal, cash_interest, funding_cost,
                  transaction_cost, trade_cash, turnover, residual, external_cash=0) -> dict[str, Any]:
        values = self._values(curve, day)
        short_collateral = _finite(values["short_dirty_value"] * (1 + self.policy.short_margin_rate), "Short collateral reserve")
        long_reserve = _finite(values["long_dirty_value"] * self.policy.long_haircut_rate, "Long haircut reserve")
        collateral = _finite(short_collateral + long_reserve, "Required collateral")
        free_cash = _finite(self.cash - collateral, "Free cash after collateral reserve")
        gap = max(0.0, -self.cash - self.policy.unsecured_limit)
        collateral_shortfall = max(0.0, collateral - max(self.cash, 0.0))
        scale = max(1.0, abs(self.cash), abs(values["dirty_pv"]), abs(trade_cash), abs(coupon), abs(principal))
        tolerance = max(1e-8, 64 * math.ulp(scale))
        residual = _finite(residual, "Cash roll residual")
        if abs(residual) > tolerance:
            raise CurveMathError("Self-financing cash roll exceeds the floating-point audit tolerance")
        row = {"date": day.isoformat(), "cash": self.cash, **values,
               "wealth": _finite(self.cash + values["dirty_pv"], "Total wealth"),
               "coupon_cash": coupon, "principal_cash": principal, "cash_interest": cash_interest,
               "funding_cost": funding_cost, "transaction_cost": transaction_cost,
               "trade_cash": trade_cash, "gross_dirty_turnover": turnover, "external_cash": external_cash,
               "cumulative_coupon_cash": self.cumulative_coupon_cash,
               "cumulative_principal_cash": self.cumulative_principal_cash,
               "cumulative_cash_interest": self.cumulative_cash_interest,
               "cumulative_funding_cost": self.cumulative_funding_cost,
               "cumulative_transaction_cost": self.cumulative_transaction_cost,
               "short_collateral_reserve": short_collateral, "long_haircut_reserve": long_reserve,
               "required_collateral": collateral, "free_cash": free_cash,
               "financing_gap": gap, "collateral_shortfall": collateral_shortfall,
               "collateral_breach": free_cash < -tolerance, "financing_breach": gap > tolerance,
               "cash_roll_residual": residual, "audit_tolerance": tolerance, "audit_status": "passed"}
        return row

    def _advance(self, curve: DiscountCurve, day: date) -> dict[str, Any]:
        if day < self.date:
            raise CurveMathError("Ledger dates must be nondecreasing")
        opening_cash, start = self.cash, self.date
        event_flows: dict[date, list[tuple[float, float]]] = {}
        for position in self.positions.values():
            for event in position.bond.coupon_events(start, day):
                event_flows.setdefault(event.pay_date, []).append((position.units * event.coupon,
                                                                   position.units * event.principal))
        coupon_total = principal_total = credit_total = funding_total = 0.0
        for event_day in sorted(set(event_flows) | {day}):
            credit, funding = self._accrue_to(event_day)
            credit_total = _finite(credit_total + credit, "Interval cash interest")
            funding_total = _finite(funding_total + funding, "Interval funding cost")
            coupon = _sum((values[0] for values in event_flows.get(event_day, [])), "Signed coupon receipts")
            principal = _sum((values[1] for values in event_flows.get(event_day, [])), "Signed principal receipts")
            self.cash = _finite(self.cash + coupon + principal, "Cash after bond receipts")
            coupon_total = _finite(coupon_total + coupon, "Interval coupon receipts")
            principal_total = _finite(principal_total + principal, "Interval principal receipts")
            if coupon != 0 or principal != 0:
                self._cash_anchor_date, self._cash_anchor_balance = event_day, self.cash
        self.date = day
        self.cumulative_coupon_cash = _finite(self.cumulative_coupon_cash + coupon_total, "Cumulative coupons")
        self.cumulative_principal_cash = _finite(self.cumulative_principal_cash + principal_total, "Cumulative principal")
        self.cumulative_cash_interest = _finite(self.cumulative_cash_interest + credit_total, "Cumulative cash interest")
        self.cumulative_funding_cost = _finite(self.cumulative_funding_cost + funding_total, "Cumulative funding cost")
        residual = self.cash - opening_cash - coupon_total - principal_total - credit_total + funding_total
        return self._snapshot(curve, day, coupon=coupon_total, principal=principal_total,
                              cash_interest=credit_total, funding_cost=funding_total,
                              transaction_cost=0, trade_cash=0, turnover=0, residual=residual)

    def _accrue_to(self, day: date) -> tuple[float, float]:
        """Accumulate from the last cash event, avoiding mark-frequency drift."""
        positive = self._cash_anchor_balance >= 0
        rate = self.policy.cash_rate_annual if positive else self.policy.funding_rate_annual
        daily_factor = 1 + rate / self.policy.day_count_basis
        try:
            accumulation = math.pow(daily_factor, (day - self._cash_anchor_date).days)
        except OverflowError as error:
            raise CurveMathError("Cash accumulation exceeds floating-point precision") from error
        if not math.isfinite(accumulation) or accumulation <= 0:
            raise CurveMathError("Cash accumulation must remain finite and positive")
        balance = _finite(self._cash_anchor_balance * accumulation, "Cash after daily carry")
        if self._cash_anchor_balance != 0 and balance == 0:
            raise CurveMathError("Cash accumulation underflows supported floating-point precision")
        change = _finite(balance - self.cash, "Daily compounded cash carry")
        self.cash = balance
        return (change, 0.0) if positive else (0.0, -change)

    def mark(self, curve: DiscountCurve, day: date | str) -> dict[str, Any]:
        """Advance held cash/events, then value unchanged signed positions ex-payment."""
        checkpoint = self._checkpoint()
        try:
            row = self._advance(curve, as_date(day, "mark date"))
        except Exception:
            self._restore(checkpoint)
            raise
        self.history.append(row.copy())
        return row

    def rebalance(self, positions: Iterable[Position], curve: DiscountCurve, day: date | str) -> dict[str, Any]:
        """Trade dirty-PV position deltas after existing-holder payments on this date."""
        target_day = as_date(day, "rebalance date")
        if not is_business_day(target_day):
            raise CurveMathError("Rebalance settlement must be a Fed Bank business day")
        new = _positions(positions)
        self._validate_new_positions(new, target_day, self.positions)
        for cusip in self.positions.keys() & new.keys():
            if self.positions[cusip].bond != new[cusip].bond:
                raise CurveMathError("Terms cannot change under the same CUSIP")
        checkpoint = self._checkpoint()
        try:
            row = self._trade(new, curve, target_day)
        except Exception:
            self._restore(checkpoint)
            raise
        self.history.append(row.copy())
        return row

    def _checkpoint(self) -> tuple:
        return (self.positions.copy(), self.date, self.cash, self.cumulative_coupon_cash,
                self.cumulative_principal_cash, self.cumulative_cash_interest,
                self.cumulative_funding_cost, self.cumulative_transaction_cost,
                self._cash_anchor_date, self._cash_anchor_balance)

    def _restore(self, checkpoint: tuple) -> None:
        (self.positions, self.date, self.cash, self.cumulative_coupon_cash,
         self.cumulative_principal_cash, self.cumulative_cash_interest,
         self.cumulative_funding_cost, self.cumulative_transaction_cost,
         self._cash_anchor_date, self._cash_anchor_balance) = checkpoint

    def _trade(self, new: dict[str, Position], curve: DiscountCurve, target_day: date) -> dict[str, Any]:
        before = self._advance(curve, target_day)
        changes = []
        for cusip in self.positions.keys() | new.keys():
            old, target = self.positions.get(cusip), new.get(cusip)
            bond = target.bond if target else old.bond
            delta = (target.units if target else 0) - (old.units if old else 0)
            changes.append(_finite(delta * bond.price(curve, target_day), "Dirty trade notional"))
        trade_value = _sum(changes, "Net dirty trade value")
        turnover = _sum((abs(value) for value in changes), "Gross changed-position turnover")
        cost = _finite(turnover * (self.policy.transaction_cost_bps / 10000), "Rebalance transaction cost")
        cash_before_trade = self.cash
        self.cash = _finite(self.cash - trade_value - cost, "Cash after rebalance")
        if trade_value != 0 or cost != 0:
            self._cash_anchor_date, self._cash_anchor_balance = target_day, self.cash
        self.positions = new
        self.cumulative_transaction_cost = _finite(self.cumulative_transaction_cost + cost, "Cumulative transaction cost")
        residual = before["cash_roll_residual"] + self.cash - cash_before_trade + trade_value + cost
        row = self._snapshot(curve, target_day, coupon=before["coupon_cash"], principal=before["principal_cash"],
                             cash_interest=before["cash_interest"], funding_cost=before["funding_cost"],
                             transaction_cost=cost, trade_cash=-trade_value, turnover=turnover, residual=residual)
        return row
