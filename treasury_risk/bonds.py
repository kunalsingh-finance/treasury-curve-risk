"""Abstract on-coupon-date bond cash flows and curve-implied finite-difference risk.

These bonds have regular annual/semiannual tenors, no stubs, and no actual date or
day-count processing. At t=0 they are on a coupon date after the current coupon:
future cash flows are discounted to a dirty curve-implied PV. No accrued interest,
clean quoted market price, Treasury settlement convention, or market execution
claim is made. All results are floating-point approximations in face-value dollars.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from .curve import (
    BASIS_POINT, KEY_RATE_NODES, CurveMathError, DiscountCurve,
    _finite, _nodes, node_bumped_curve, parallel_bumped_curve,
)


@dataclass(frozen=True)
class CouponBond:
    """Synthetic regular coupon bond; coupon_rate is an annual fraction, not percent.

    Maturity must be a positive half-year multiple up to 200 years, and an integer
    number of payment periods. Supported payment frequencies are 1 or 2 per year.
    Face is positive and coupons nonnegative. The first flow is at 1/frequency;
    each coupon is face*coupon_rate/frequency, with face added only to the last.
    """

    maturity_years: float
    coupon_rate: float
    face: float = 100.0
    frequency: int = 2

    def __post_init__(self) -> None:
        for name in ("maturity_years", "coupon_rate", "face"):
            object.__setattr__(self, name, _finite(getattr(self, name), name))
        if self.maturity_years <= 0 or self.maturity_years > 200:
            raise CurveMathError("Abstract bond maturity must be in (0,200] years")
        if self.coupon_rate < 0 or self.face <= 0:
            raise CurveMathError("Coupon must be nonnegative and face must be positive")
        if isinstance(self.frequency, bool) or not isinstance(self.frequency, int) or self.frequency not in (1, 2):
            raise CurveMathError("Only annual or semiannual regular coupon frequencies are supported")
        half_years = self.maturity_years * 2
        periods = self.maturity_years * self.frequency
        if not math.isclose(half_years, round(half_years), rel_tol=0, abs_tol=1e-12) or not math.isclose(periods, round(periods), rel_tol=0, abs_tol=1e-12):
            raise CurveMathError("Maturity must be a regular half-year tenor and a whole number of coupon periods; stubs are unsupported")
        coupon = self.face * (self.coupon_rate / self.frequency)
        if not math.isfinite(coupon) or not math.isfinite(coupon + self.face):
            raise CurveMathError("Coupon cash flow exceeds floating-point precision")

    def cashflows(self) -> tuple[tuple[float, float], ...]:
        periods = round(self.maturity_years * self.frequency)
        coupon = self.face * (self.coupon_rate / self.frequency)
        return tuple((period / self.frequency, coupon + (self.face if period == periods else 0.0))
                     for period in range(1, periods + 1))

    def price(self, curve: DiscountCurve) -> float:
        return price(self, curve)

    def parallel_dv01(self, curve: DiscountCurve) -> float:
        return parallel_dv01(self, curve)

    def effective_duration(self, curve: DiscountCurve) -> float:
        return effective_duration(self, curve)

    def convexity(self, curve: DiscountCurve) -> float:
        return convexity(self, curve)

    def key_rate_dv01(self, curve: DiscountCurve, nodes: Sequence[float] = KEY_RATE_NODES) -> dict[float, float]:
        return key_rate_dv01(self, curve, nodes)


def price(bond: CouponBond, curve: DiscountCurve) -> float:
    """Return dirty continuous-curve PV in face-value dollars, using future flows only."""
    if not isinstance(bond, CouponBond) or not callable(getattr(curve, "discount", None)):
        raise CurveMathError("Price requires a CouponBond and a discount curve")
    present_values = []
    for time, amount in bond.cashflows():
        discount = _finite(curve.discount(time), "Discount factor")
        if discount <= 0:
            raise CurveMathError("Pricing discount factors must be positive")
        value = amount * discount
        if not math.isfinite(value) or (amount > 0 and value == 0):
            raise CurveMathError("Cash-flow PV exceeds floating-point precision")
        present_values.append(value)
    try:
        result = math.fsum(present_values)
    except OverflowError as error:
        raise CurveMathError("Bond price exceeds floating-point precision") from error
    if not math.isfinite(result) or result <= 0:
        raise CurveMathError("Bond price must remain positive and finite")
    return result


def _parallel_prices(bond: CouponBond, curve: DiscountCurve) -> tuple[float, float, float]:
    return (price(bond, curve), price(bond, parallel_bumped_curve(curve, -1)),
            price(bond, parallel_bumped_curve(curve, 1)))


def parallel_dv01(bond: CouponBond, curve: DiscountCurve) -> float:
    """Dollar PV sensitivity to 1 bp: (PV[-1bp]-PV[+1bp])/2, positive for long bonds."""
    down = price(bond, parallel_bumped_curve(curve, -1))
    up = price(bond, parallel_bumped_curve(curve, 1))
    return _finite((down - up) / 2, "Parallel DV01")


def effective_duration(bond: CouponBond, curve: DiscountCurve) -> float:
    """Centered ±1bp effective duration of a parallel continuous-zero-rate shift."""
    base, down, up = _parallel_prices(bond, curve)
    denominator = base * (2 * BASIS_POINT)
    if not math.isfinite(denominator) or denominator <= 0:
        raise CurveMathError("Effective duration denominator exceeds floating-point precision")
    return _finite((down - up) / denominator, "Effective duration")


def convexity(bond: CouponBond, curve: DiscountCurve) -> float:
    """Centered ±1bp convexity = (PV[-1bp]+PV[+1bp]-2PV0)/(PV0*.0001²)."""
    base, down, up = _parallel_prices(bond, curve)
    denominator = base * BASIS_POINT**2
    if not math.isfinite(denominator) or denominator <= 0:
        raise CurveMathError("Convexity denominator exceeds floating-point precision")
    try:
        numerator = math.fsum((down - base, up - base))
    except OverflowError as error:
        raise CurveMathError("Convexity numerator exceeds floating-point precision") from error
    return _finite(numerator / denominator, "Convexity")


def key_rate_dv01(bond: CouponBond, curve: DiscountCurve, nodes: Sequence[float] = KEY_RATE_NODES) -> dict[float, float]:
    """Nine dollar DV01 buckets for piecewise-linear continuous-zero-rate hat bumps.

    Each bucket independently uses ±1bp. Their sum approximates parallel DV01;
    finite bump nonlinearities mean the sum need not equal it exactly. Nodes use
    the explicitly declared endpoint-flat scenario geometry in BumpedCurve.
    """
    result = {}
    checked_nodes = _nodes(nodes)
    for node in checked_nodes:
        down = price(bond, node_bumped_curve(curve, node, -1, checked_nodes))
        up = price(bond, node_bumped_curve(curve, node, 1, checked_nodes))
        result[node] = _finite((down - up) / 2, "Key-rate DV01")
    return result
