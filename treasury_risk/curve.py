"""Svensson continuous zero/forward rates and explicit zero-rate bump scenarios.

Federal Reserve Svensson beta parameters are percentages; public rate outputs
are annual fractions under continuous compounding. Tau parameters and times are
in years. Floating-point calculations are approximations, not cent accounting.
Negative yields are supported. Numerical nonfinite/overflow/underflow results
raise CurveMathError instead of imposing economic yield floors or caps.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from numbers import Real
from types import MappingProxyType
from typing import Protocol

KEY_RATE_NODES = (0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 20.0, 30.0)
BASIS_POINT = 0.0001


class CurveMathError(ValueError):
    """Invalid inputs or a result outside representable floating-point precision."""


class DiscountCurve(Protocol):
    """Minimal pricing interface shared by base and bumped continuous-zero curves."""

    def zero_yield(self, t: float) -> float: ...

    def discount(self, t: float) -> float: ...


def _finite(value: Real, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise CurveMathError(f"{name} must be a finite real number")
    try:
        result = float(value)
    except (ValueError, OverflowError) as error:
        raise CurveMathError(f"{name} must be a finite real number") from error
    if not math.isfinite(result):
        raise CurveMathError(f"{name} must be finite")
    return result


def _time(t: Real) -> float:
    time = _finite(t, "t")
    if time < 0:
        raise CurveMathError("t must be nonnegative")
    return time


def _sum(values: Sequence[float], name: str) -> float:
    try:
        result = math.fsum(values)
    except (ValueError, OverflowError) as error:
        raise CurveMathError(f"{name} exceeds floating-point precision") from error
    if not math.isfinite(result):
        raise CurveMathError(f"{name} must remain finite")
    return result


def _loadings(x: float) -> tuple[float, float, float, float]:
    """Return zero slope/curvature and forward slope/curvature loadings stably."""
    if x == 0:
        return 1.0, 0.0, 1.0, 0.0
    if math.isinf(x):
        # Finite t divided by an extremely small positive tau can overflow.
        # This is the well-defined asymptotic loading, not a rate floor.
        return 0.0, 0.0, 0.0, 0.0
    exponential = math.exp(-x)
    slope = -math.expm1(-x) / x
    if x < 1e-4:
        # slope-exp(-x) otherwise cancels two numbers arbitrarily close to 1.
        curvature = x * (0.5 + x * (-1 / 3 + x * (1 / 8 + x * (-1 / 30 + x * (1 / 144 - x / 840)))))
    else:
        curvature = slope - exponential
    return slope, curvature, exponential, x * exponential


def _discount(zero_yield: float, time: float) -> float:
    if time == 0:
        return 1.0
    exponent = -zero_yield * time
    if not math.isfinite(exponent):
        raise CurveMathError("Discount exponent exceeds floating-point precision")
    try:
        factor = math.exp(exponent)
    except OverflowError as error:
        raise CurveMathError("Discount factor overflows floating-point precision") from error
    if not math.isfinite(factor) or factor == 0:
        raise CurveMathError("Discount factor is outside positive finite floating-point precision")
    return factor


def _short_rate(beta0: float, beta1: float) -> float:
    """Analytic zero-time limit, with a safe fallback for percent-sum overflow."""
    try:
        return math.fsum((beta0, beta1)) / 100
    except OverflowError:
        return _sum((beta0 / 100, beta1 / 100), "Zero-time yield")


@dataclass(frozen=True)
class SvenssonCurve:
    """Frozen Fed-parameter Svensson curve; beta values percent and taus years."""

    beta0: float
    beta1: float
    beta2: float
    beta3: float
    tau1: float
    tau2: float

    def __post_init__(self) -> None:
        for name in ("beta0", "beta1", "beta2", "beta3", "tau1", "tau2"):
            value = _finite(getattr(self, name), name)
            object.__setattr__(self, name, value)
        if self.tau1 <= 0 or self.tau2 <= 0:
            raise CurveMathError("Svensson tau parameters must be positive")

    def zero_yield(self, t: float) -> float:
        """Annual fractional continuous zero yield; analytic y(0)=(beta0+beta1)/100."""
        time = _time(t)
        if time == 0:
            return _short_rate(self.beta0, self.beta1)
        slope, curve1, _, _ = _loadings(time / self.tau1)
        _, curve2, _, _ = _loadings(time / self.tau2)
        # Convert before summing to avoid overflowing percent intermediates when
        # a finite fractional-rate calculation remains representable.
        return _sum((self.beta0 / 100, self.beta1 / 100 * slope,
                     self.beta2 / 100 * curve1, self.beta3 / 100 * curve2), "Zero yield")

    def forward_yield(self, t: float) -> float:
        """Instantaneous forward rate f(t)=d[t*y(t)]/dt, in annual fractions."""
        time = _time(t)
        if time == 0:
            return _short_rate(self.beta0, self.beta1)
        _, _, exp1, curvature1 = _loadings(time / self.tau1)
        _, _, _, curvature2 = _loadings(time / self.tau2)
        return _sum((self.beta0 / 100, self.beta1 / 100 * exp1,
                     self.beta2 / 100 * curvature1, self.beta3 / 100 * curvature2), "Forward yield")

    def discount(self, t: float) -> float:
        """Discount exp(-t*y(t)); t=0 is exactly 1 and negative rates are valid."""
        time = _time(t)
        return _discount(self.zero_yield(time), time)


def _nodes(nodes: Sequence[float]) -> tuple[float, ...]:
    try:
        values = tuple(_finite(node, "key-rate node") for node in nodes)
    except TypeError as error:
        raise CurveMathError("Key-rate nodes must be a sequence") from error
    if not values or any(node <= 0 for node in values):
        raise CurveMathError("Key-rate nodes must be nonempty and positive")
    if any(left >= right for left, right in zip(values, values[1:])):
        raise CurveMathError("Key-rate nodes must be strictly increasing")
    return values


@dataclass(frozen=True)
class BumpedCurve:
    """Add a parallel and/or nodal piecewise-linear bump to continuous zero rates.

    All bumps are specified in basis points (1 bp = .0001 fractional annual rate).
    Nodal values interpolate linearly between nodes and stay flat beyond the first
    and last node. Consequently nodal hat functions form a partition of unity,
    including endpoint tails. This is a declared risk scenario, not a recalibration
    of the Fed curve, instrument quote bump, or a market key-rate convention.
    """

    base: DiscountCurve
    parallel_bps: float = 0.0
    key_rate_bumps_bps: Mapping[float, float] | None = None
    nodes: tuple[float, ...] = KEY_RATE_NODES
    _fractional_bumps: tuple[float, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not callable(getattr(self.base, "zero_yield", None)) or not callable(getattr(self.base, "discount", None)):
            raise CurveMathError("Base curve must provide zero_yield and discount")
        object.__setattr__(self, "parallel_bps", _finite(self.parallel_bps, "parallel_bps"))
        nodes = _nodes(self.nodes)
        object.__setattr__(self, "nodes", nodes)
        supplied = {} if self.key_rate_bumps_bps is None else self.key_rate_bumps_bps
        if not isinstance(supplied, Mapping):
            raise CurveMathError("key_rate_bumps_bps must map supported nodes to basis points")
        normalized = {}
        for node, value in supplied.items():
            key = _finite(node, "key-rate bump node")
            if key not in nodes:
                raise CurveMathError(f"Unsupported key-rate node {key}; use one of {nodes}")
            normalized[key] = _finite(value, "key-rate shock_bps")
        object.__setattr__(self, "key_rate_bumps_bps", MappingProxyType(normalized))
        object.__setattr__(self, "_fractional_bumps", tuple(normalized.get(node, 0.0) / 10000 for node in nodes))

    def _nodal_bump(self, time: float) -> float:
        if time <= self.nodes[0]:
            return self._fractional_bumps[0]
        if time >= self.nodes[-1]:
            return self._fractional_bumps[-1]
        right = bisect.bisect_right(self.nodes, time)
        left = right - 1
        weight = (time - self.nodes[left]) / (self.nodes[right] - self.nodes[left])
        return _sum(((1 - weight) * self._fractional_bumps[left], weight * self._fractional_bumps[right]), "Nodal bump")

    def zero_yield(self, t: float) -> float:
        time = _time(t)
        base_yield = _finite(self.base.zero_yield(time), "Base zero yield")
        return _sum((base_yield, self.parallel_bps / 10000, self._nodal_bump(time)), "Bumped zero yield")

    def discount(self, t: float) -> float:
        time = _time(t)
        return _discount(self.zero_yield(time), time)


def node_bumped_curve(base: DiscountCurve, node: float, shock_bps: float, nodes: Sequence[float] = KEY_RATE_NODES) -> BumpedCurve:
    """Return a single-node zero-rate hat bump with flat endpoint tails."""
    return BumpedCurve(base, key_rate_bumps_bps={node: shock_bps}, nodes=_nodes(nodes))


def parallel_bumped_curve(base: DiscountCurve, shock_bps: float) -> BumpedCurve:
    """Return a uniform fractional continuous-zero-rate shift in basis points."""
    return BumpedCurve(base, parallel_bps=shock_bps)
