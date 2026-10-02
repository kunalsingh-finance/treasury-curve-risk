"""Reproducible curve checks and explicitly hypothetical hedge diagnostics."""

from __future__ import annotations

import math
from datetime import datetime, timezone

from .bonds import CouponBond
from .curve import KEY_RATE_NODES, BumpedCurve, SvenssonCurve
from .hedge import select_hedge

BENCHMARK_TOLERANCE_BPS = 0.006
PARAMETERS = ("beta0", "beta1", "beta2", "beta3", "tau1", "tau2")
HISTORICAL_LABEL = (
    "Frozen-cash-flow curve-move diagnostic: fixed starting tenors and hedge "
    "weights; no maturity aging, realized coupons, financing or trading returns"
)


def curve_from_record(record: dict) -> SvenssonCurve:
    return SvenssonCurve(**{name: record[name] for name in PARAMETERS})


def benchmark_curves(records: list[dict], *, strict: bool = True) -> dict:
    """Check reconstructed zero yields against the Fed's rounded external series.

    Published SVENY rates have four decimal places in percent: their rounding
    alone allows 0.005bp of error. 0.006bp permits tiny numerical error too.
    Strict calls reject exceptions. Audit calls retain them for review; the
    analysis builder separately rejects any exception in its required dates.
    """
    if not records:
        raise ValueError("No valid source observations are available")
    comparisons = failed = missing = 0
    maximum = 0.0
    rows, exceptions = [], []
    for record in records:
        curve = curve_from_record(record)
        errors = []
        published = record["published_zero_yields"]
        for maturity in range(1, 31):
            observed = published.get(maturity)
            if observed is None:
                missing += 1
                continue
            if not math.isfinite(observed):
                raise ValueError("Published benchmark must be finite or explicitly missing")
            error = abs(curve.zero_yield(maturity) - observed) * 10000
            errors.append(error)
            comparisons += 1
            failed += error > BENCHMARK_TOLERANCE_BPS
        day_maximum = max(errors) if errors else None
        if day_maximum is not None:
            maximum = max(maximum, day_maximum)
        status = ("benchmark_unavailable" if not errors else
                  "outside_rounding_tolerance" if day_maximum > BENCHMARK_TOLERANCE_BPS else "pass")
        row = {"date": record["date"], "comparisons": len(errors),
               "max_absolute_error_bps": day_maximum, "status": status}
        rows.append(row)
        if status != "pass":
            exceptions.append(row)
        if strict and not errors:
            raise ValueError(f"No published zero-yield benchmark on {record['date']}")
    if strict and failed:
        raise ValueError(f"Curve benchmark failed: {failed} comparisons exceed "
                         f"{BENCHMARK_TOLERANCE_BPS}bp; maximum {maximum:.8f}bp")
    return {"comparisons": comparisons, "missing_published_values": missing,
            "max_absolute_error_bps": maximum,
            "tolerance_bps": BENCHMARK_TOLERANCE_BPS,
            "failed_comparisons": int(failed), "date_checks": rows,
            "exceptions": exceptions, "quarantined_observations": len(exceptions),
            "admissible_observations": len(rows) - len(exceptions),
            "audit_status": "source_review_required" if exceptions else "pass"}


def default_portfolio() -> list[CouponBond]:
    return [CouponBond(8, 0.035, 4_000_000),
            CouponBond(12, 0.0425, 6_000_000),
            CouponBond(25, 0.0475, 2_000_000)]


def default_hedges() -> list[CouponBond]:
    return [CouponBond(maturity, 0.04, 100) for maturity in (2, 5, 10, 20, 30)]


def portfolio_price(bonds: list[CouponBond], curve) -> float:
    return math.fsum(bond.price(curve) for bond in bonds)


def hedge_setup(bonds: list[CouponBond], instruments: list[CouponBond], curve) -> dict:
    """Freeze hedge units using only the supplied curve and 100-face instruments."""
    if not bonds or not instruments:
        raise ValueError("A target portfolio and hedge instruments are required")
    if any(bond.face != 100 for bond in instruments):
        raise ValueError("Hedge instruments must each represent exactly 100 face units")
    ten_year_indices = [i for i, bond in enumerate(instruments) if bond.maturity_years == 10]
    if len(ten_year_indices) != 1:
        raise ValueError("The duration comparator requires exactly one ten-year hedge")
    target_vectors = [bond.key_rate_dv01(curve) for bond in bonds]
    target = [math.fsum(vector[node] for vector in target_vectors) for node in KEY_RATE_NODES]
    instrument_vectors = [bond.key_rate_dv01(curve) for bond in instruments]
    matrix = [[vector[node] for vector in instrument_vectors] for node in KEY_RATE_NODES]
    target_dv01 = math.fsum(bond.parallel_dv01(curve) for bond in bonds)
    instrument_dv01 = [bond.parallel_dv01(curve) for bond in instruments]
    index = ten_year_indices[0]
    if instrument_dv01[index] == 0:
        raise ValueError("Ten-year hedge has zero parallel DV01")
    duration_weights = [0.0] * len(instruments)
    duration_weights[index] = -target_dv01 / instrument_dv01[index]
    residual = [target[i] + matrix[i][index] * duration_weights[index]
                for i in range(len(KEY_RATE_NODES))]
    duration = {
        "method": "single_ten_year_instrument_actual_parallel_dv01_match",
        "weights": duration_weights,
        "face_amounts": [100 * weight for weight in duration_weights],
        "residual_exposure": residual,
        "residual_norm": math.hypot(*residual),
        "parallel_residual_dv01": target_dv01 + duration_weights[index] * instrument_dv01[index],
        "gross_absolute_face_units": math.fsum(abs(100 * weight) for weight in duration_weights),
    }
    multi = select_hedge(target, matrix)
    multi["parallel_residual_dv01"] = target_dv01 + math.fsum(
        weight * exposure for weight, exposure in zip(multi["weights"], instrument_dv01))
    return {"target_dv01": target_dv01, "target_key_rate_dv01": target,
            "duration_hedge": duration, "multi_hedge": multi}


def repricing_changes(bonds: list[CouponBond], instruments: list[CouponBond],
                      base_curve, changed_curve, setup: dict) -> dict:
    """Full PV changes including signed hedge positions; no financing inference."""
    target_change = portfolio_price(bonds, changed_curve) - portfolio_price(bonds, base_curve)
    instrument_changes = [bond.price(changed_curve) - bond.price(base_curve) for bond in instruments]
    def hedged(method):
        weights = setup[method]["weights"]
        if len(weights) != len(instrument_changes):
            raise ValueError("Hedge weights must match instruments")
        return target_change + math.fsum(weight * change
                                        for weight, change in zip(weights, instrument_changes))
    return {"unhedged_change": target_change,
            "duration_hedged_change": hedged("duration_hedge"),
            "multi_hedged_change": hedged("multi_hedge")}


def shock_scenarios(curve) -> list[tuple[str, BumpedCurve]]:
    steepener = dict(zip(KEY_RATE_NODES, (20, 25, 35, 45, 65, 80, 100, 125, 140)))
    front_end = dict(zip(KEY_RATE_NODES, (100, 90, 80, 70, 45, 30, 20, 5, 0)))
    return [("Parallel +100bp", BumpedCurve(curve, parallel_bps=100)),
            ("Parallel -100bp", BumpedCurve(curve, parallel_bps=-100)),
            ("Long-end steepening", BumpedCurve(curve, key_rate_bumps_bps=steepener)),
            ("Front-end selloff", BumpedCurve(curve, key_rate_bumps_bps=front_end))]


def historical_diagnostic(records: list[dict], bonds: list[CouponBond],
                          instruments: list[CouponBond], year: int = 2022) -> dict:
    selected = [record for record in records if record["date"].startswith(f"{year:04d}-")]
    if len(selected) < 2:
        raise ValueError(f"At least two valid {year} curves are required for the diagnostic")
    base_curve = curve_from_record(selected[0])
    setup = hedge_setup(bonds, instruments, base_curve)
    rows = [{"date": record["date"], **repricing_changes(
        bonds, instruments, base_curve, curve_from_record(record), setup)} for record in selected]
    return {"label": HISTORICAL_LABEL,
            "start_date": selected[0]["date"], "end_date": selected[-1]["date"],
            "observations": len(rows), "rows": rows,
            "duration_face_amounts": setup["duration_hedge"]["face_amounts"],
            "multi_face_amounts": setup["multi_hedge"]["face_amounts"],
            "start_hedge_diagnostics": setup,
            "maximum_absolute_curve_pv_changes": {
                field: max(abs(row[field]) for row in rows)
                for field in ("unhedged_change", "duration_hedged_change", "multi_hedged_change")},
            "data_vintage": "Current downloaded vintage; not an unrevised historical information set"}


def bond_summary(bond: CouponBond, curve, label: str) -> dict:
    return {"label": label, "maturity_years": bond.maturity_years,
            "coupon_percent": bond.coupon_rate * 100, "face": bond.face,
            "price": bond.price(curve), "dv01": bond.parallel_dv01(curve),
            "duration": bond.effective_duration(curve), "convexity": bond.convexity(curve)}


def build_analysis(records: list[dict], source: dict, data_quality: dict,
                   historical_year: int = 2022) -> dict:
    benchmark = benchmark_curves(records, strict=False)
    latest_record = records[-1]
    required_dates = {latest_record["date"]} | {
        record["date"] for record in records if record["date"].startswith(f"{historical_year:04d}-")}
    inadmissible = [row["date"] for row in benchmark["exceptions"] if row["date"] in required_dates]
    if inadmissible:
        raise ValueError(f"Required valuation dates fail the source benchmark: {inadmissible}")
    curve = curve_from_record(latest_record)
    bonds, instruments = default_portfolio(), default_hedges()
    setup = hedge_setup(bonds, instruments, curve)
    scenarios = []
    for name, bumped in shock_scenarios(curve):
        shock = {"parallel_bps": bumped.parallel_bps,
                 "key_rate_bumps_bps": dict(bumped.key_rate_bumps_bps),
                 "interpolation": "linear continuous-zero-rate bumps; flat endpoint tails"}
        scenarios.append({"name": name, **repricing_changes(bonds, instruments, curve, bumped, setup),
                          "shock_definition": shock})
    return {
        "title": "Treasury Curve, Bond Pricing and Hedge Engine",
        "status": ("research_demo_with_source_exceptions" if benchmark["exceptions"] else "validated_research_demo"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Curve-implied valuations and theoretical hedges of hypothetical regular coupon bonds",
        "source": source, "data_quality": data_quality, "benchmark": benchmark,
        "latest": {"date": latest_record["date"], "key_rate_nodes": list(KEY_RATE_NODES),
                   "zero_curve": [{"maturity_years": i / 2, "zero_yield_percent": curve.zero_yield(i / 2) * 100}
                                  for i in range(1, 61)],
                   "portfolio": [bond_summary(bond, curve, f"Target {i + 1}") for i, bond in enumerate(bonds)],
                   "hedge_instruments": [bond_summary(bond, curve, f"{bond.maturity_years:g}Y / 4%")
                                         for bond in instruments],
                   "target_price": portfolio_price(bonds, curve), **setup, "scenarios": scenarios},
        "historical": historical_diagnostic(records, bonds, instruments, historical_year),
        "limitations": [
            "Hypothetical regular semiannual cash flows on a coupon date; no actual CUSIP, settlement, accrued interest or market price validation.",
            "Federal Reserve staff fitted off-the-run nominal Treasury research curve; current downloaded vintage can revise prior dates.",
            "Dates without published benchmarks or outside the declared rounding tolerance are quarantined; all valuation and replay dates must pass. Full-source exceptions remain visible for review.",
            "Zero-rate bucket interpolation is a declared scenario convention; these are not instrument-quote or OIS curve bumps.",
            "The five hedge instruments do not span all nine risk buckets. Unweighted least squares is not covariance-risk optimization.",
            "Short cash-bond units omit financing, repo, transaction costs, collateral and execution constraints.",
            HISTORICAL_LABEL + ".",
        ],
    }
