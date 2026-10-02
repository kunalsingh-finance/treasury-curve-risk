"""Regular dated fixed-principal Treasury-like bonds with explicit conventions.

Coupon accrual uses unadjusted six-calendar-month periods and ACT/ACT within the
coupon period. Cash pays on the following Federal Reserve Bank business day,
without extra coupon interest. Curve time is ACT/365F to that actual payment date.
Marks are ex-payment: cash paid ON an as-of date belongs to an existing holder,
and is excluded from the future bond PV or a new purchase on that date.

This module does not ingest or validate auction terms, CUSIP identity, dealer
quotes, TreasuryDirect book-entry entitlement rules, repo, or trade execution.
The explicit ex-payment convention is a model ownership rule. The regular bank
calendar omits emergency closures and supports 1986-2100; it is not a SIFMA
trading-hours calendar. A dated_date can separate accrual origin from issue date.

Primary convention sources:
https://www.treasurydirect.gov/files/laws-and-regulations/auction-regulations-uoc/31-cfr-part-356.pdf
https://www.federalreserve.gov/aboutthefed/chapter-6-reporting-requirements.htm
https://www.federalreserve.gov/aboutthefed/k8.htm
"""

from __future__ import annotations

import calendar
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Sequence

from .curve import (
    BASIS_POINT, KEY_RATE_NODES, CurveMathError, DiscountCurve, _finite, _nodes,
    node_bumped_curve, parallel_bumped_curve,
)

CONVENTION_SOURCES = (
    {"title": "Treasury auction regulations: regular interest and accrued interest", "url": "https://www.treasurydirect.gov/files/laws-and-regulations/auction-regulations-uoc/31-cfr-part-356.pdf"},
    {"title": "Federal Reserve Bank holiday settlement rules", "url": "https://www.federalreserve.gov/aboutthefed/chapter-6-reporting-requirements.htm"},
    {"title": "Federal Reserve holiday calendar", "url": "https://www.federalreserve.gov/aboutthefed/k8.htm"},
)


def as_date(value: date | str, name: str = "date") -> date:
    if isinstance(value, datetime):
        raise CurveMathError(f"{name} must be a date, not a datetime")
    if isinstance(value, date):
        result = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        try:
            result = date.fromisoformat(value)
        except ValueError as error:
            raise CurveMathError(f"{name} must be a valid ISO calendar date") from error
    else:
        raise CurveMathError(f"{name} must be a date or YYYY-MM-DD string")
    if not 1986 <= result.year <= 2100:
        raise CurveMathError(f"{name} is outside the supported 1986-2100 bank calendar")
    return result


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (occurrence - 1))


def _sunday_observed(day: date) -> date:
    # Federal Reserve BANKS remain open on the Friday before a Saturday holiday.
    return day + timedelta(days=1) if day.weekday() == 6 else day


@lru_cache(maxsize=128)
def bank_holidays(year: int) -> frozenset[date]:
    if isinstance(year, bool) or not isinstance(year, int) or not 1986 <= year <= 2100:
        raise CurveMathError("Bank calendar year must be an integer in 1986-2100")
    last_may = date(year, 5, 31)
    days = {
        _sunday_observed(date(year, 1, 1)), _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3), last_may - timedelta(days=last_may.weekday()),
        _sunday_observed(date(year, 7, 4)), _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 10, 0, 2), _sunday_observed(date(year, 11, 11)),
        _nth_weekday(year, 11, 3, 4), _sunday_observed(date(year, 12, 25)),
    }
    if year >= 2022:
        days.add(_sunday_observed(date(year, 6, 19)))
    return frozenset(days)


def is_business_day(day: date | str) -> bool:
    value = as_date(day)
    return value.weekday() < 5 and value not in bank_holidays(value.year)


def following_business_day(day: date | str) -> date:
    value = as_date(day)
    while not is_business_day(value):
        value += timedelta(days=1)
    return value


def year_fraction(start: date | str, end: date | str) -> float:
    """Explicit ACT/365F curve-time mapping, separate from coupon ACT/ACT accrual."""
    first, last = as_date(start, "start"), as_date(end, "end")
    if last < first:
        raise CurveMathError("ACT/365F end cannot precede start")
    return (last - first).days / 365.0


def _anchor_month(maturity: date, months_back: int) -> date:
    index = maturity.year * 12 + maturity.month - 1 - months_back
    year, month_index = divmod(index, 12)
    month = month_index + 1
    month_end = calendar.monthrange(year, month)[1]
    anchor_end = maturity.day == calendar.monthrange(maturity.year, maturity.month)[1]
    return date(year, month, month_end if anchor_end else min(maturity.day, month_end))


@dataclass(frozen=True)
class CouponEvent:
    scheduled_date: date
    pay_date: date
    coupon: float
    principal: float = 0.0

    @property
    def amount(self) -> float:
        return self.coupon + self.principal


@dataclass(frozen=True)
class DatedBond:
    """Dated regular fixed-rate note/bond terms; coupon_rate is an annual fraction.

    dated_date defaults to issue_date. It must be a regular boundary of the
    backward six-month schedule. An actual issue/reopening may occur later than
    dated_date, and settlement cannot precede issue_date. Reopened investor
    purchases still pay/receive the explicitly computed accrued interest.
    Irregular first periods, callable terms, inflation-linked principal, floating
    coupons, and frequencies other than semiannual are unsupported.
    """

    cusip: str
    label: str
    issue_date: date | str
    maturity_date: date | str
    coupon_rate: float
    face: float = 100.0
    frequency: int = 2
    dated_date: date | str | None = None
    _schedule: tuple[date, ...] = field(init=False, repr=False)
    _events: tuple[CouponEvent, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.cusip, str) or not re.fullmatch(r"[A-Z0-9]{9}", self.cusip):
            raise CurveMathError("CUSIP must be a nine-character uppercase alphanumeric identifier")
        if not isinstance(self.label, str) or not self.label.strip():
            raise CurveMathError("Bond label must be a nonempty string")
        issue = as_date(self.issue_date, "issue_date")
        maturity = as_date(self.maturity_date, "maturity_date")
        dated = issue if self.dated_date is None else as_date(self.dated_date, "dated_date")
        if not dated <= issue < maturity:
            raise CurveMathError("Bond dates must satisfy dated_date <= issue_date < maturity_date")
        if (maturity - dated).days > 200 * 366:
            raise CurveMathError("Dated bond tenor exceeds the supported 200 years")
        if isinstance(self.frequency, bool) or self.frequency != 2:
            raise CurveMathError("Only regular semiannual dated coupons are supported")
        coupon_rate, face = _finite(self.coupon_rate, "coupon_rate"), _finite(self.face, "face")
        if coupon_rate < 0 or face <= 0:
            raise CurveMathError("Coupon must be nonnegative and face must be positive")
        coupon = face * (coupon_rate / 2)
        if not math.isfinite(coupon) or not math.isfinite(coupon + face):
            raise CurveMathError("Dated cash-flow amount exceeds floating-point precision")
        backward = []
        for step in range(401):
            candidate = _anchor_month(maturity, 6 * step)
            if candidate == dated:
                break
            if candidate < dated:
                raise CurveMathError("dated_date is not a regular coupon boundary; irregular first/stub periods are unsupported")
            backward.append(candidate)
        else:
            raise CurveMathError("Dated schedule exceeds the supported number of coupons")
        schedule = tuple(reversed(backward))
        if not schedule:
            raise CurveMathError("Dated bond requires at least one future coupon period")
        events = tuple(CouponEvent(day, following_business_day(day), coupon,
                                   face if day == maturity else 0.0) for day in schedule)
        object.__setattr__(self, "issue_date", issue)
        object.__setattr__(self, "maturity_date", maturity)
        object.__setattr__(self, "dated_date", dated)
        object.__setattr__(self, "coupon_rate", coupon_rate)
        object.__setattr__(self, "face", face)
        object.__setattr__(self, "_schedule", schedule)
        object.__setattr__(self, "_events", events)

    def coupon_schedule(self) -> tuple[date, ...]:
        """Unadjusted contractual accrual dates; maturity anchoring prevents EOM drift."""
        return self._schedule

    def coupon_events(self, start: date | str, end: date | str) -> tuple[CouponEvent, ...]:
        """Existing-holder receipts over the interval start < adjusted payment <= end."""
        first, last = as_date(start, "start"), as_date(end, "end")
        if last < first:
            raise CurveMathError("Coupon event interval must be chronological")
        return tuple(event for event in self._events if first < event.pay_date <= last)

    def _settlement(self, settlement: date | str) -> date:
        value = as_date(settlement, "settlement")
        if value < self.issue_date:
            raise CurveMathError("Settlement/as-of cannot precede the actual issue date")
        return value

    def cashflows(self, settlement: date | str) -> tuple[tuple[date, float], ...]:
        """Future cash after settlement; payments exactly on settlement are excluded."""
        value = self._settlement(settlement)
        return tuple((event.pay_date, event.amount) for event in self._events if event.pay_date > value)

    def accrued_interest(self, settlement: date | str) -> float:
        """ACT/ACT coupon-period accrual on UNADJUSTED dates; resets on coupon date.

        No interest accrues past scheduled maturity. A delayed final payment can
        remain in dirty PV while accrual is zero. Any coupon awaiting its adjusted
        payment date remains a separate future cash event, not extended accrual.
        """
        value = self._settlement(settlement)
        if value >= self.maturity_date:
            return 0.0
        previous = self.dated_date
        for next_coupon in self._schedule:
            if value < next_coupon:
                elapsed = (value - previous).days
                length = (next_coupon - previous).days
                return self.face * (self.coupon_rate / 2) * elapsed / length
            previous = next_coupon
        return 0.0

    def price(self, curve: DiscountCurve, settlement: date | str) -> float:
        """Dirty fitted-curve PV using ACT/365F to each actual adjusted payment."""
        value = self._settlement(settlement)
        pvs = []
        for pay_date, amount in self.cashflows(value):
            discount = _finite(curve.discount(year_fraction(value, pay_date)), "Discount factor")
            if discount <= 0:
                raise CurveMathError("Dated discount factors must be positive")
            pv = amount * discount
            if not math.isfinite(pv) or (amount > 0 and pv == 0):
                raise CurveMathError("Dated cash-flow PV exceeds floating-point precision")
            pvs.append(pv)
        try:
            return _finite(math.fsum(pvs), "Dated dirty PV")
        except OverflowError as error:
            raise CurveMathError("Dated dirty PV exceeds floating-point precision") from error

    def clean_price(self, curve: DiscountCurve, settlement: date | str) -> float:
        """Dirty fitted PV minus accrued; not a quoted or execution-validated price."""
        return _finite(self.price(curve, settlement) - self.accrued_interest(settlement), "Dated clean PV")

    def remaining_years(self, settlement: date | str) -> float:
        value = self._settlement(settlement)
        return max(0.0, (self._events[-1].pay_date - value).days / 365)

    def zero_yield(self, curve: DiscountCurve, settlement: date | str) -> float:
        """Curve zero rate at final-payment tenor; NOT the bond's yield to maturity."""
        return _finite(curve.zero_yield(self.remaining_years(settlement)), "Dated maturity zero rate")

    def forward_yield(self, curve, settlement: date | str) -> float:
        """Instantaneous curve forward at final-payment tenor; not a coupon rate."""
        if not callable(getattr(curve, "forward_yield", None)):
            raise CurveMathError("Curve must provide an instantaneous forward_yield")
        return _finite(curve.forward_yield(self.remaining_years(settlement)), "Dated maturity forward rate")

    def parallel_dv01(self, curve: DiscountCurve, settlement: date | str) -> float:
        down = self.price(parallel_bumped_curve(curve, -1), settlement)
        up = self.price(parallel_bumped_curve(curve, 1), settlement)
        return _finite((down - up) / 2, "Dated parallel DV01")

    def key_rate_dv01(self, curve: DiscountCurve, settlement: date | str,
                      nodes: Sequence[float] = KEY_RATE_NODES) -> dict[float, float]:
        checked = _nodes(nodes)
        return {node: _finite((self.price(node_bumped_curve(curve, node, -1, checked), settlement)
                              - self.price(node_bumped_curve(curve, node, 1, checked), settlement)) / 2,
                             "Dated key-rate DV01") for node in checked}

    def effective_duration(self, curve: DiscountCurve, settlement: date | str) -> float | None:
        base = self.price(curve, settlement)
        if base == 0:
            return None
        denominator = base * BASIS_POINT
        if not math.isfinite(denominator) or denominator <= 0:
            raise CurveMathError("Dated duration denominator exceeds floating-point precision")
        return _finite(self.parallel_dv01(curve, settlement) / denominator, "Dated duration")

    def convexity(self, curve: DiscountCurve, settlement: date | str) -> float | None:
        base = self.price(curve, settlement)
        if base == 0:
            return None
        denominator = base * BASIS_POINT**2
        if not math.isfinite(denominator) or denominator <= 0:
            raise CurveMathError("Dated convexity denominator exceeds floating-point precision")
        down = self.price(parallel_bumped_curve(curve, -1), settlement)
        up = self.price(parallel_bumped_curve(curve, 1), settlement)
        return _finite(math.fsum((down - base, up - base)) / denominator, "Dated convexity")
