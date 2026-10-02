"""Verify complete artifacts, reconstructed constraints, convex gaps and cash rolls."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import date
import hashlib
import json
import math
from numbers import Real
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from scipy.optimize import linprog
from treasury_risk.research import code_fingerprint, METHODS
from treasury_risk.data import validate_source_manifest

ARTIFACT_NAMES = frozenset(("release.json", "report.html", "risk_and_factors.png",
                            "wealth_2022.png", "wealth_2023.png", "ledger_2022.csv", "ledger_2023.csv"))


def scalar(value, name):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite and numeric")
    return float(value)


def array(value, shape, name):
    try:
        raw = np.asarray(value)
        if raw.dtype.kind not in "iuf":
            raise ValueError("Numeric array required")
        result = raw.astype(float)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"Invalid {name}") from error
    if result.shape != shape or not np.isfinite(result).all():
        raise ValueError(f"Invalid shape or nonfinite values in {name}")
    return result


def equal(value, expected, name, *, absolute=1e-7, relative=1e-10):
    if not math.isclose(scalar(value, name), scalar(expected, name), abs_tol=absolute, rel_tol=relative):
        raise ValueError(f"{name} cannot be reconstructed")


def covariance_checked(saved, execution):
    dates = saved["training_dates"]
    if (not dates or dates != sorted(set(dates)) or dates[-1] >= execution
            or saved["training_start_date"] != dates[0] or saved["training_end_date"] != dates[-1]
            or saved["execution_date"] != execution or saved["level_count"] != len(dates)
            or saved["change_count"] != len(dates) - 1 or len(dates) < 3):
        raise ValueError("Covariance training coverage is incomplete or not strictly prior")
    days = [date.fromisoformat(item) for item in dates]
    if max((b - a).days for a, b in zip(days, days[1:])) > 7:
        raise ValueError("Covariance contains a stale calendar gap")
    matrix = array(saved["covariance"], (9, 9), "covariance")
    scale = max(float(np.max(np.abs(matrix))), np.finfo(float).tiny)
    if not np.allclose(matrix, matrix.T, rtol=0, atol=scale * 1e-12):
        raise ValueError("Covariance is not symmetric")
    eigenvalues, eigenvectors = np.linalg.eigh((matrix + matrix.T) / 2)
    if eigenvalues[0] < -scale * 1e-10:
        raise ValueError("Covariance is not positive semidefinite")
    factor = np.sqrt(np.maximum(eigenvalues, 0))[:, None] * eigenvectors.T
    return factor.T @ factor, factor


def solution_checked(inputs, saved_covariance, result, declared, execution):
    """Reconstruct feasibility and solve a fresh LP convex first-order certificate."""
    if result.get("status") != "optimal" or result.get("weights") is None:
        raise ValueError("A required hedge lacks a successful solution")
    count = len(inputs["hedge_prices"])
    if not count:
        raise ValueError("No hedge instruments supplied")
    target = array(inputs["target_exposure"], (9,), "target exposure")
    matrix = array(inputs["hedge_matrix"], (9, count), "hedge matrix")
    parallel = array(inputs["hedge_parallel_dv01"], (count,), "parallel sensitivities")
    prices = array(inputs["hedge_prices"], (count,), "hedge prices")
    if np.any(prices <= 0):
        raise ValueError("Hedge prices must be positive")
    target_parallel = scalar(inputs["target_parallel_dv01"], "target parallel DV01")
    weights = array(result["weights"], (count,), "hedge weights")
    faces = array(result["face_amounts"], (count,), "hedge faces")
    gross_limit = scalar(declared["gross_face_limit"], "gross capacity")
    position_limit = scalar(declared["position_face_limit"], "position capacity")
    cash_neutral = declared["cash_neutral"]
    if min(gross_limit, position_limit) < 0 or not isinstance(cash_neutral, bool):
        raise ValueError("Invalid declared constraints")
    constraints = result["constraints"]
    equal(constraints["gross_face_limit"], gross_limit, "saved gross capacity")
    limits = array(constraints["position_face_limits"], (count,), "position capacities")
    if not np.allclose(limits, position_limit, rtol=0, atol=1e-7) or constraints["cash_neutral"] != cash_neutral:
        raise ValueError("Saved capacities differ from declared strategy")
    equal(constraints["parallel_equality_target"], -target_parallel, "parallel target")
    face_tolerance = max(1e-7, max(gross_limit, position_limit) * 1e-10)
    if not np.allclose(faces, weights * 100, rtol=1e-12, atol=1e-7):
        raise ValueError("Saved hedge face does not equal 100 times weight")
    gross = float(np.abs(faces).sum())
    if gross > gross_limit + face_tolerance or np.any(np.abs(faces) > limits + face_tolerance):
        raise ValueError("Hedge face exceeds an independently checked capacity")
    actual_parallel = float(target_parallel + parallel @ weights)
    if abs(actual_parallel) > max(1e-8, abs(target_parallel) * 1e-10):
        raise ValueError("Reconstructed parallel hedge equality failed")
    cash = float(prices @ weights)
    cash_tolerance = max(1e-6, float(np.abs(prices * weights).sum()) * 1e-10)
    if cash_neutral and abs(cash) > cash_tolerance:
        raise ValueError("Reconstructed zero-cash hedge equality failed")
    equal(result["parallel_residual_dv01"], actual_parallel, "saved parallel residual")
    equal(result["hedge_cash_dollars"], cash, "saved hedge cash")
    equal(result["gross_absolute_face_units"], gross, "saved gross hedge face")
    residual = target + matrix @ weights
    if not np.allclose(residual, array(result["residual_exposure"], (9,), "saved residual"), rtol=1e-10, atol=1e-7):
        raise ValueError("Saved hedge exposure cannot be reconstructed")
    covariance, factor = covariance_checked(saved_covariance, execution)
    before, after = float(target @ covariance @ target), float(residual @ covariance @ residual)
    equal(result["variance_before"], before, "unhedged variance", absolute=1e-5, relative=1e-9)
    equal(result["variance_after"], after, "hedged variance", absolute=1e-5, relative=1e-9)
    if before:
        equal(result["variance_reduction_pct"], (1 - after / before) * 100, "variance reduction")
    elif result["variance_reduction_pct"] is not None:
        raise ValueError("Zero initial variance must have no percentage reduction")
    ridge = scalar(result["ridge_penalty"], "ridge penalty")
    if ridge < 0:
        raise ValueError("Negative ridge penalty")
    weight_scale = max(1.0, gross_limit / 100, position_limit / 100)
    transformed = factor @ (matrix * weight_scale)
    quadratic = transformed.T @ transformed + ridge * weight_scale**2 * np.eye(count)
    linear = transformed.T @ (factor @ target)
    objective_scale = max(before, float(np.max(np.abs(quadratic))), float(np.max(np.abs(linear)))) or 1.0
    point = weights / weight_scale
    derivative = np.r_[2 * (quadratic @ point + linear) / objective_scale, np.zeros(count)]
    upper = limits / (100 * weight_scale)
    identity = np.eye(count)
    inequalities = np.vstack((np.c_[identity, -identity], np.c_[-identity, -identity],
                              np.r_[np.zeros(count), np.ones(count)][None, :]))
    rhs = np.r_[np.zeros(2 * count), gross_limit / (100 * weight_scale)]
    raw = np.array([parallel * weight_scale] + ([prices * weight_scale] if cash_neutral else []))
    targets = np.array([-target_parallel] + ([0.0] if cash_neutral else []))
    scales = np.maximum(np.max(np.abs(raw), axis=1), np.abs(targets))
    scales[scales == 0] = 1
    equalities = np.c_[raw / scales[:, None], np.zeros((len(scales), count))]
    targets = targets / scales
    bounds = [(-limit, limit) for limit in upper] + [(0, limit) for limit in upper]
    certificate = linprog(derivative, A_ub=inequalities, b_ub=rhs,
                          A_eq=equalities, b_eq=targets, bounds=bounds, method="highs")
    if not certificate.success or not np.isfinite(certificate.x).all():
        raise ValueError("Independent convex certificate LP failed")
    if (np.max(np.abs(equalities @ certificate.x - targets)) > 1e-8
            or np.max(inequalities @ certificate.x - rhs) > 1e-8
            or any(not low - 1e-8 <= v <= high + 1e-8 for v, (low, high) in zip(certificate.x, bounds))):
        raise ValueError("Certificate LP solution violates constraints")
    gap = max(float(derivative @ (np.r_[point, np.abs(point)] - certificate.x)), 0.0)
    if not math.isfinite(gap) or gap > 1e-7:
        raise ValueError("Recomputed convex first-order gap exceeds 1e-7")
    return gap


def verify_pack(pack: dict) -> dict:
    try:
        return _verify_pack(pack)
    except (KeyError, TypeError, IndexError, OverflowError, np.linalg.LinAlgError) as error:
        raise ValueError(f"Incomplete or invalid release schema: {error}") from error


def _verify_pack(pack):
    if pack.get("schema_version") != 1 or pack.get("status") != "complete_research_release":
        raise ValueError("Release does not have the supported completed schema")
    terms = [item for era in ("latest", "historical") for group in ("targets", "hedges")
             for item in pack["instrument_config"][era][group]]
    checks = pack["auction_benchmarks"]
    if not terms or Counter(row["cusip"] for row in checks) != Counter(row["cusip"] for row in terms):
        raise ValueError("Auction evidence does not cover every configured instrument")
    for row in checks:
        difference = scalar(row["calculated_clean_price_per_100"], "calculated auction price") - scalar(row["official_clean_price_per_100"], "official auction price")
        if abs(difference) > 1e-6 or row["passed"] is not True:
            raise ValueError("An independently reconstructed auction-price check failed")
        equal(row["error_per_100"], difference, "auction price difference", absolute=1e-12)
        matching = [term for term in terms if term["cusip"] == row["cusip"] and term["issue_date"] == row["settlement_date"]]
        if not matching or not any(math.isclose(term["original_official_price_per_100"], row["official_clean_price_per_100"], abs_tol=1e-12, rel_tol=0) for term in matching):
            raise ValueError("Auction check does not match original instrument metadata")
    periods = pack["periods"]
    if len(periods) != 2 or {p["start_date"][:4] for p in periods} != {"2022", "2023"}:
        raise ValueError("Both annual evaluations are required")
    checked_rows = decisions = 0
    gaps = []
    latest = pack["latest"]
    gaps.append(solution_checked(latest["risk_inputs"], latest["covariance"], latest["methods"]["constrained"], latest["constraints"], latest["date"]))
    for period in periods:
        rows = period["rows"]
        dates = sorted({row["date"] for row in rows})
        if (len(dates) < 2 or len(dates) != period["observations"] or len(rows) != len(dates) * len(METHODS)
                or dates[0] != period["start_date"] or dates[-1] != period["end_date"]
                or any(Counter(row["method"] for row in rows if row["date"] == day) != Counter(METHODS) for day in dates)):
            raise ValueError("Ledger observation coverage is incomplete")
        equity = scalar(period["initial_equity"], "initial equity")
        if equity <= 0:
            raise ValueError("Evaluation requires positive initial equity")
        previous, totals = {}, {}
        for row in rows:
            method = row["method"]
            for key, value in row.items():
                if isinstance(value, Real) and not isinstance(value, bool):
                    scalar(value, key)
            prior = previous.get(method)
            if prior and row["date"] <= prior["date"]:
                raise ValueError("Ledger observations are not ordered")
            scale = max(1.0, *(abs(scalar(row[key], key)) for key in ("cash", "dirty_pv", "trade_cash", "coupon_cash", "principal_cash", "wealth")))
            tolerance = max(1e-8, 128 * math.ulp(scale))
            equal(row["wealth"], row["cash"] + row["dirty_pv"], "cash plus dirty PV", absolute=tolerance, relative=0)
            equal(row["dirty_pv"], row["clean_pv"] + row["accrued_pv"], "clean plus accrued PV", absolute=tolerance, relative=0)
            opening = prior["cash"] if prior else 0.0
            expected = math.fsum((opening, row["coupon_cash"], row["principal_cash"], row["cash_interest"], -row["funding_cost"], row["trade_cash"], -row["transaction_cost"], row["external_cash"]))
            equal(row["cash"], expected, "independent cash roll", absolute=tolerance, relative=0)
            equal(row["external_cash"], 0 if prior else equity, "external capital", absolute=tolerance, relative=0)
            if row["audit_status"] != "passed" or abs(row["cash_roll_residual"]) > tolerance:
                raise ValueError("Saved accounting audit failed")
            policy = period["funding_policy"]
            equal(row["transaction_cost"], row["gross_dirty_turnover"] * policy["transaction_cost_bps"] / 10000, "transaction cost", absolute=tolerance, relative=0)
            equal(row["short_collateral_reserve"], row["short_dirty_value"] * (1 + policy["short_margin_rate"]), "short collateral", absolute=tolerance, relative=0)
            equal(row["long_haircut_reserve"], row["long_dirty_value"] * policy["long_haircut_rate"], "long reserve", absolute=tolerance, relative=0)
            equal(row["required_collateral"], row["short_collateral_reserve"] + row["long_haircut_reserve"], "collateral total", absolute=tolerance, relative=0)
            equal(row["free_cash"], row["cash"] - row["required_collateral"], "free cash", absolute=tolerance, relative=0)
            equal(row["financing_gap"], max(0, -row["cash"] - policy["unsecured_limit"]), "financing gap", absolute=tolerance, relative=0)
            equal(row["collateral_shortfall"], max(0, row["required_collateral"] - max(row["cash"], 0)), "collateral shortfall", absolute=tolerance, relative=0)
            audit_scale = max(1.0, *(abs(row[key]) for key in ("cash", "dirty_pv", "trade_cash", "coupon_cash", "principal_cash")))
            audit_tolerance = max(1e-8, 64 * math.ulp(audit_scale))
            equal(row["audit_tolerance"], audit_tolerance, "ledger floating-point tolerance", absolute=1e-15, relative=0)
            if (row["collateral_breach"] != (row["free_cash"] < -audit_tolerance)
                    or row["financing_breach"] != (row["financing_gap"] > audit_tolerance)):
                raise ValueError("Financing breach flags disagree with reconstructed balances")
            cumulative = totals.setdefault(method, {})
            for flow in ("coupon_cash", "principal_cash", "cash_interest", "funding_cost", "transaction_cost"):
                cumulative[flow] = cumulative.get(flow, 0) + row[flow]
                equal(row["cumulative_" + flow], cumulative[flow], "cumulative " + flow, absolute=tolerance, relative=1e-12)
            previous[method] = row
            checked_rows += 1
        if set(period["summaries"]) != set(METHODS):
            raise ValueError("Method summaries are incomplete")
        for method in METHODS:
            saved, final = period["summaries"][method], previous[method]
            equal(saved["initial_equity"], equity, "summary initial equity")
            equal(saved["final_wealth"], final["wealth"], "summary final wealth")
            equal(saved["net_pnl"], final["wealth"] - equity, "summary net P&L")
            equal(saved["wealth_change_fraction"], final["wealth"] / equity - 1, "summary wealth return", absolute=1e-12)
            peak, worst = equity, 0.0
            selected = [row for row in rows if row["method"] == method]
            for row in selected:
                peak = max(peak, row["wealth"])
                worst = min(worst, row["wealth"] / peak - 1)
            equal(saved["maximum_drawdown"], worst, "summary drawdown", absolute=1e-12)
            equal(saved["max_cash_roll_residual"], max(abs(row["cash_roll_residual"]) for row in selected), "summary accounting residual", absolute=1e-12)
            for flow in totals[method]:
                equal(saved["accounting_totals"]["cumulative_" + flow], final["cumulative_" + flow], "summary " + flow)
            if (saved["collateral_breach_observations"] != sum(bool(row["collateral_breach"]) for row in selected)
                    or saved["financing_gap_observations"] != sum(row["financing_gap"] > 1e-6 for row in selected)):
                raise ValueError("Summary financing observations disagree with ledger")
        first_month = {}
        for day in dates:
            first_month.setdefault(day[:7], day)
        saved_decisions = period["decisions"]
        if [decision["trade_date"] for decision in saved_decisions] != list(first_month.values()):
            raise ValueError("Monthly decision coverage is incomplete")
        for decision in saved_decisions:
            day, prior_date = decision["trade_date"], decision["decision_curve_date"]
            if not 0 < (date.fromisoformat(day) - date.fromisoformat(prior_date)).days <= 7:
                raise ValueError("Decision curve is same-day, future or stale")
            if len(decision["active_hedges"]) != len(decision["risk_inputs"]["hedge_prices"]):
                raise ValueError("Decision instrument count differs from risk inputs")
            gaps.append(solution_checked(decision["risk_inputs"], decision["covariance"], decision["methods"]["constrained"], period["strategy_constraints"], day))
            decisions += 1
    return {"status": "passed", "ledger_rows_reconciled": checked_rows,
            "prior_date_decisions_verified": decisions, "auction_price_checks": len(checks),
            "maximum_recomputed_convex_gap": max(gaps)}


def load_verified_release(output: Path) -> tuple[dict, dict]:
    """Read each artifact once and return the same pack whose bytes were verified."""
    manifest = json.loads((output / "artifact_manifest.json").read_bytes())
    if manifest.get("status") != "complete_research_release" or set(manifest.get("files", {})) != ARTIFACT_NAMES:
        raise ValueError("Artifact manifest lacks the complete seven-file release")
    payloads = {}
    for name in sorted(ARTIFACT_NAMES):
        path = (output / name).resolve()
        if path.parent != output.resolve():
            raise ValueError("Artifact name escapes release directory")
        payload, reference = path.read_bytes(), manifest["files"][name]
        if not payload or len(payload) != reference["bytes"] or hashlib.sha256(payload).hexdigest() != reference["sha256"]:
            raise ValueError(f"Artifact integrity failed: {name}")
        payloads[name] = payload
    pack = json.loads(payloads["release.json"])
    current_source = validate_source_manifest(ROOT / "data/raw/feds200628.csv", ROOT / "data/raw/source_manifest.json")
    if pack["source"]["sha256"] != current_source["sha256"]:
        raise ValueError("Saved release curve source is stale")
    if pack["code_sha256"] != code_fingerprint(ROOT):
        raise ValueError("Saved release model code is stale")
    config_bytes = (ROOT / "configs/treasury_instruments.json").read_bytes()
    if pack["instrument_config_sha256"] != hashlib.sha256(config_bytes).hexdigest():
        raise ValueError("Saved release instrument configuration is stale")
    if pack["instrument_config"] != json.loads(config_bytes):
        raise ValueError("Embedded instrument metadata differs from verified configuration")
    result = verify_pack(pack)
    result["artifacts_verified"] = len(payloads)
    return pack, result


def verify(output: Path) -> dict:
    return load_verified_release(output)[1]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/release")
    print(json.dumps(verify(parser.parse_args().output), indent=2))
