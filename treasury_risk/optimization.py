"""Constrained variance hedges and strictly prior-window curve covariance.

Exposure columns, parallel sensitivities and prices describe 100 face units.
The objective is residual dollar variance plus a ridge penalty on those weights.
An LP proves feasibility. An independent first-order LP can certify that point
directly; otherwise SLSQP searches for a candidate that must pass the same check.
Failed or infeasible solves contain no proposed hedge positions.
"""

from __future__ import annotations

from datetime import date
import math
from numbers import Real
import re

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, linprog, minimize

from .hedge import KEY_RATE_NODES


_DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_SCOPE = "Theoretical 100-face cash-bond units; no financing, borrow, transaction costs or execution model."


def _array(value: object, name: str, shape: tuple[int, ...] | None = None) -> np.ndarray:
    try:
        raw = np.asarray(value)
        if raw.dtype.kind not in "iuf":
            raise ValueError("real numeric array required")
        with np.errstate(over="raise", invalid="raise"):
            array = np.asarray(raw, dtype=float)
    except (TypeError, ValueError, OverflowError, FloatingPointError) as error:
        raise ValueError(f"{name} must contain finite real numbers") from error
    if not np.isfinite(array).all() or (shape is not None and array.shape != shape):
        raise ValueError(f"{name} must be finite with shape {shape}")
    return array


def _scalar(value: object, name: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite real number")
    try:
        result = float(value)
    except (ValueError, OverflowError) as error:
        raise ValueError(f"{name} must be a finite real number") from error
    if not math.isfinite(result) or (nonnegative and result < 0):
        raise ValueError(f"{name} must be finite" + (" and nonnegative" if nonnegative else ""))
    return result


def _finite(value: float, name: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{name} exceeds finite calculation capacity")
    return float(value)


def _covariance(value: object) -> tuple[np.ndarray, np.ndarray, dict]:
    matrix = _array(value, "covariance", (9, 9))
    scale = float(np.max(np.abs(matrix)))
    tolerance = max(np.finfo(float).tiny, scale * np.finfo(float).eps * 100)
    if float(np.max(np.abs(matrix - matrix.T))) > tolerance:
        raise ValueError("covariance must be symmetric")
    symmetric = (matrix / 2) + (matrix.T / 2)
    try:
        eigenvalues, eigenvectors = np.linalg.eigh(symmetric)
    except np.linalg.LinAlgError as error:
        raise ValueError("covariance eigendecomposition failed") from error
    spectral_scale = float(np.max(np.abs(eigenvalues)))
    psd_tolerance = max(np.finfo(float).tiny, spectral_scale * np.finfo(float).eps * 100)
    if float(eigenvalues[0]) < -psd_tolerance:
        raise ValueError("covariance must be positive semidefinite")
    adjusted = np.maximum(eigenvalues, 0)
    factor = np.sqrt(adjusted)[:, None] * eigenvectors.T
    normalized = factor.T @ factor
    return normalized, factor, {
        "covariance_rank": int(np.count_nonzero(adjusted > psd_tolerance)),
        "minimum_input_eigenvalue": float(eigenvalues[0]),
        "psd_roundoff_tolerance": psd_tolerance,
        "numerical_psd_adjustment": bool(np.any(eigenvalues < 0)),
    }


def _variance(factor: np.ndarray, exposure: np.ndarray) -> float:
    with np.errstate(over="raise", invalid="raise"):
        transformed = factor @ exposure
        return _finite(float(transformed @ transformed), "variance")


def _lp_diagnostics(result) -> dict:
    return {"success": bool(result.success), "status": int(result.status), "message": str(result.message)}


def optimize_covariance_hedge(target_exposure: object, hedge_matrix: object, covariance: object, *,
                              target_parallel_dv01: float, hedge_parallel_dv01: object,
                              hedge_prices: object, gross_face_limit: float,
                              position_face_limits: object, cash_neutral: bool = False,
                              ridge_penalty: float = 0.0) -> dict:
    """Minimize (target+H*w)'Cov(target+H*w) + ridge_penalty*w'w.

    Enforce target_parallel_dv01 + hedge_parallel_dv01 @ w == 0, absolute
    per-instrument face limits and total absolute face. Optional cash neutrality
    requires hedge_prices @ w == 0. Covariance units are squared basis points;
    exposures are dollars per basis point. Equality checks have explicit finite
    numerical tolerances, reported with the actual residuals. No position is
    returned unless feasibility, residuals and independent optimality pass.
    A feasible LP point that already satisfies the convex certificate avoids
    unnecessary nonlinear iteration, including on a singleton feasible set.
    """
    target = _array(target_exposure, "target_exposure", (9,))
    matrix = _array(hedge_matrix, "hedge_matrix")
    if matrix.ndim != 2 or matrix.shape[0] != 9 or matrix.shape[1] == 0:
        raise ValueError("hedge_matrix must have nine rows and at least one instrument")
    if np.any(np.all(matrix == 0, axis=0)):
        raise ValueError("each hedge instrument must have nonzero key-rate exposure")
    count = matrix.shape[1]
    parallel = _array(hedge_parallel_dv01, "hedge_parallel_dv01", (count,))
    prices = _array(hedge_prices, "hedge_prices", (count,))
    if np.any(prices <= 0):
        raise ValueError("hedge_prices must be positive prices per100face")
    limits = _array(position_face_limits, "position_face_limits", (count,))
    if np.any(limits < 0):
        raise ValueError("position_face_limits must be nonnegative")
    gross_limit = _scalar(gross_face_limit, "gross_face_limit", nonnegative=True)
    target_parallel = _scalar(target_parallel_dv01, "target_parallel_dv01")
    ridge = _scalar(ridge_penalty, "ridge_penalty", nonnegative=True)
    if not isinstance(cash_neutral, bool):
        raise ValueError("cash_neutral must be a boolean")
    covariance_matrix, factor, covariance_diagnostics = _covariance(covariance)
    try:
        before = _variance(factor, target)
        weight_scale = max(1.0, gross_limit / 100, float(np.max(limits)) / 100)
        with np.errstate(over="raise", invalid="raise"):
            scaled_matrix = matrix * weight_scale
            transformed_matrix, transformed_target = factor @ scaled_matrix, factor @ target
            quadratic = transformed_matrix.T @ transformed_matrix
            ridge_scale = ridge * weight_scale * weight_scale
            quadratic += ridge_scale * np.eye(count)
            linear = transformed_matrix.T @ transformed_target
        if not np.isfinite(quadratic).all() or not np.isfinite(linear).all() or not math.isfinite(ridge_scale):
            raise ValueError("optimization objective exceeds finite capacity")
    except (FloatingPointError, OverflowError) as error:
        raise ValueError("optimization objective exceeds finite capacity") from error
    objective_scale = max(before, float(np.max(np.abs(quadratic))), float(np.max(np.abs(linear))))
    objective_scale = objective_scale if objective_scale > 0 else 1.0
    constraints = {"gross_face_limit": gross_limit, "position_face_limits": limits.tolist(),
                   "cash_neutral": cash_neutral, "parallel_equality_target": -target_parallel}
    result = {"status": None, "weights": None, "face_amounts": None, "residual_exposure": None,
              "variance_before": before, "variance_after": None, "parallel_residual_dv01": None,
              "hedge_cash_dollars": None, "gross_absolute_face_units": None,
              "constraints": constraints, "ridge_penalty": ridge, "solver_diagnostics": {},
              **covariance_diagnostics, "key_rate_nodes": list(KEY_RATE_NODES), "scope": _SCOPE}

    # Variables are normalized weights followed by absolute-weight auxiliaries.
    upper = limits / (100 * weight_scale)
    bounds = [(-value, value) for value in upper] + [(0.0, value) for value in upper]
    identity = np.eye(count)
    inequalities = np.vstack((np.column_stack((identity, -identity)), np.column_stack((-identity, -identity)),
                              np.r_[np.zeros(count), np.ones(count)][None, :]))
    inequality_targets = np.r_[np.zeros(2 * count), gross_limit / (100 * weight_scale)]
    raw_equalities = [parallel * weight_scale]
    equality_targets = [-target_parallel]
    if cash_neutral:
        raw_equalities.append(prices * weight_scale)
        equality_targets.append(0.0)
    raw_equalities = np.asarray(raw_equalities)
    equality_targets = np.asarray(equality_targets)
    row_scales = np.maximum(np.max(np.abs(raw_equalities), axis=1), np.abs(equality_targets))
    row_scales[row_scales == 0] = 1
    equalities = np.column_stack((raw_equalities / row_scales[:, None], np.zeros((len(row_scales), count))))
    scaled_targets = equality_targets / row_scales
    if not np.isfinite(equalities).all() or not np.isfinite(scaled_targets).all():
        raise ValueError("optimization equalities exceed finite capacity")

    def fail(status: str, message: str) -> dict:
        result["status"], result["message"] = status, message
        return result

    feasibility = linprog(np.r_[np.zeros(count), np.ones(count)], A_ub=inequalities, b_ub=inequality_targets,
                          A_eq=equalities, b_eq=scaled_targets, bounds=bounds, method="highs")
    result["solver_diagnostics"]["feasibility"] = _lp_diagnostics(feasibility)
    if not feasibility.success:
        return fail("infeasible" if feasibility.status == 2 else "solver_failed", "No certified feasible hedge; positions withheld")

    def primal_valid(point: np.ndarray) -> bool:
        return (point.shape == (2 * count,) and np.isfinite(point).all()
                and np.max(np.abs(equalities @ point - scaled_targets)) <= 1e-8
                and np.max(inequalities @ point - inequality_targets) <= 1e-8
                and all(low - 1e-8 <= value <= high + 1e-8 for value, (low, high) in zip(point, bounds)))

    starting = np.asarray(feasibility.x, dtype=float)
    if not primal_valid(starting):
        return fail("solver_failed", "Feasibility solver returned an invalid point; positions withheld")
    independent_rows = []
    for index in range(len(equalities)):
        candidate = equalities[independent_rows + [index]]
        if np.linalg.matrix_rank(candidate) > len(independent_rows):
            independent_rows.append(index)
    linear_constraints = [LinearConstraint(inequalities, -np.inf, inequality_targets)]
    if independent_rows:
        linear_constraints.append(LinearConstraint(equalities[independent_rows], scaled_targets[independent_rows], scaled_targets[independent_rows]))

    def objective(point: np.ndarray) -> float:
        exposure = target + scaled_matrix @ point[:count]
        return (_variance(factor, exposure) + ridge_scale * float(point[:count] @ point[:count])) / objective_scale

    def gradient(point: np.ndarray) -> np.ndarray:
        return np.r_[2 * (quadratic @ point[:count] + linear) / objective_scale, np.zeros(count)]

    # Tight face limits and equalities can fix every weight. SLSQP may report
    # roundoff or incompatible inequalities at that feasible boundary despite
    # there being no improving direction. Check the independently solved convex
    # gap before starting nonlinear iterations; keep the same final tolerances.
    starting_derivative = gradient(starting)
    initial_certificate = linprog(starting_derivative, A_ub=inequalities, b_ub=inequality_targets,
                                  A_eq=equalities, b_eq=scaled_targets, bounds=bounds, method="highs")
    result["solver_diagnostics"]["initial_optimality_lp"] = _lp_diagnostics(initial_certificate)
    initial_gap = None
    if initial_certificate.success and primal_valid(np.asarray(initial_certificate.x, dtype=float)):
        initial_gap = max(float(starting_derivative @ (starting - initial_certificate.x)), 0.0)
        result["solver_diagnostics"]["initial_normalized_first_order_gap"] = initial_gap
    certificate = None
    if initial_gap is not None and math.isfinite(initial_gap) and initial_gap <= 1e-7:
        point = starting
        certificate = initial_certificate
        result["solver_diagnostics"]["optimization"] = {"success": True, "status": 0,
            "message": "Feasible LP point independently certified optimal; nonlinear iteration unnecessary", "iterations": 0}
    elif gross_limit == 0 or np.all(limits == 0):
        point = starting
        result["solver_diagnostics"]["optimization"] = {"success": True, "status": 0,
            "message": "Constraints fix every hedge position to zero", "iterations": 0}
    else:
        try:
            solved = minimize(objective, starting, jac=gradient, method="SLSQP",
                              bounds=Bounds(*np.asarray(bounds).T), constraints=linear_constraints,
                              options={"ftol": 1e-15, "maxiter": 1000})
        except (ValueError, FloatingPointError, np.linalg.LinAlgError) as error:
            return fail("solver_failed", f"Optimization failed: {error}; positions withheld")
        result["solver_diagnostics"]["optimization"] = {"success": bool(solved.success), "status": int(solved.status),
            "message": str(solved.message), "iterations": int(getattr(solved, "nit", 0))}
        if not solved.success:
            return fail("solver_failed", "Optimization did not converge; positions withheld")
        point = np.asarray(solved.x, dtype=float)
    if not primal_valid(point):
        return fail("solver_failed", "Optimization constraint residual check failed; positions withheld")

    # Convex first-order gap independently bounds objective suboptimality.
    derivative = gradient(point)
    if certificate is None:
        certificate = linprog(derivative, A_ub=inequalities, b_ub=inequality_targets, A_eq=equalities,
                              b_eq=scaled_targets, bounds=bounds, method="highs")
    result["solver_diagnostics"]["optimality_lp"] = _lp_diagnostics(certificate)
    if not certificate.success or not primal_valid(np.asarray(certificate.x, dtype=float)):
        return fail("solver_failed", "Independent optimality check failed; positions withheld")
    gap = max(float(derivative @ (point - certificate.x)), 0.0)
    result["solver_diagnostics"].update(normalized_first_order_gap=gap, first_order_gap_tolerance=1e-7,
                                       objective_scale=objective_scale, equality_rank=len(independent_rows))
    if not math.isfinite(gap) or gap > 1e-7:
        return fail("solver_failed", "Independent optimality gap exceeds tolerance; positions withheld")

    weights = point[:count] * weight_scale
    face = weights * 100
    residual = target + matrix @ weights
    parallel_residual = float(target_parallel + parallel @ weights)
    cash = float(prices @ weights)
    gross = math.fsum(abs(float(value)) for value in face)
    parallel_tolerance = max(1e-8, abs(target_parallel) * 1e-10)
    cash_tolerance = max(1e-6, math.fsum(abs(float(value)) for value in prices * weights) * 1e-10)
    face_tolerance = max(1e-7, max(gross_limit, float(np.max(limits))) * 1e-10)
    result["solver_diagnostics"].update(parallel_residual_dv01=parallel_residual, parallel_tolerance=parallel_tolerance,
        hedge_cash_dollars=cash, cash_tolerance=cash_tolerance, face_tolerance=face_tolerance,
        gross_face_violation=max(gross - gross_limit, 0.0),
        maximum_position_face_violation=max(float(np.max(np.abs(face) - limits)), 0.0))
    if (not np.isfinite(weights).all() or not np.isfinite(residual).all() or not np.isfinite(face).all()
            or not all(math.isfinite(value) for value in (parallel_residual, cash, gross))
            or abs(parallel_residual) > parallel_tolerance or (cash_neutral and abs(cash) > cash_tolerance)
            or gross > gross_limit + face_tolerance or np.any(np.abs(face) > limits + face_tolerance)):
        return fail("solver_failed", "Final economic constraint checks failed; positions withheld")
    after = _variance(factor, residual)
    result.update(status="optimal", message="Feasible hedge with independently checked convex optimality gap",
        weights=weights.tolist(), face_amounts=face.tolist(), residual_exposure=residual.tolist(),
        residual_norm=_finite(math.hypot(*residual), "residual norm"), variance_after=after,
        variance_reduction_pct=(1 - after / before) * 100 if before else None,
        parallel_residual_dv01=parallel_residual, hedge_cash_dollars=cash, gross_absolute_face_units=gross,
        gross_face_capacity_remaining=max(gross_limit - gross, 0.0),
        position_face_capacity_remaining=np.maximum(limits - np.abs(face), 0).tolist(),
        objective_value=_finite(after + ridge * float(weights @ weights), "objective value"),
        objective_units="Dollar variance plus ridge penalty on 100-face-unit weights",
        covariance_units="Squared basis-point changes; exposures in dollars per1bp")
    return result


def _iso_date(value: object, name: str) -> date:
    if not isinstance(value, str) or not _DATE_PATTERN.fullmatch(value):
        raise ValueError(f"{name} must be an ISO calendar date")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{name} is not a valid calendar date") from error


def estimate_covariance(history: list[dict], execution_date: str, *, lookback_changes: int = 252,
                        shrinkage: float = 0.10, max_gap_days: int = 7) -> dict:
    """Estimate sample covariance/PCA from supplied prior observed zero yields.

    Rows contain date and zero_yields_bps (nine actual historical yield levels).
    Exactly lookback_changes successive differences of strictly earlier levels
    are used; no future row supplies an endpoint. Covariance is shrunk toward a
    spherical matrix with the sample's mean variance. Gaps exceeding the stated
    calendar limit reject the window rather than create a purported daily move.
    Source authenticity and unrevised vintage remain the caller's responsibility.
    """
    execution = _iso_date(execution_date, "execution_date")
    if isinstance(lookback_changes, bool) or not isinstance(lookback_changes, int) or lookback_changes < 2:
        raise ValueError("lookback_changes must be an integer at least two")
    if isinstance(max_gap_days, bool) or not isinstance(max_gap_days, int) or max_gap_days < 1:
        raise ValueError("max_gap_days must be a positive integer")
    shrink = _scalar(shrinkage, "shrinkage", nonnegative=True)
    if shrink > 1:
        raise ValueError("shrinkage cannot exceed one")
    if not isinstance(history, list):
        raise ValueError("history must be a list of observed dated yield rows")
    prior, excluded = [], 0
    for row in history:
        if not isinstance(row, dict):
            raise ValueError("each history row must be an object")
        observed = _iso_date(row.get("date"), "history date")
        if observed >= execution:
            excluded += 1
            continue
        prior.append((observed, row))
    prior.sort(key=lambda item: item[0])
    if len({observed for observed, _ in prior}) != len(prior):
        raise ValueError("prior history contains duplicate dates")
    if len(prior) < lookback_changes + 1:
        raise ValueError("insufficient strictly prior observations for the full covariance window")
    selected = prior[-(lookback_changes + 1):]
    gaps = [(selected[index][0] - selected[index - 1][0]).days for index in range(1, len(selected))]
    if max(gaps) > max_gap_days:
        raise ValueError("covariance window exceeds the maximum calendar gap")
    levels = np.vstack([_array(row.get("zero_yields_bps"), "zero_yields_bps", (9,)) for _, row in selected])
    try:
        with np.errstate(over="raise", invalid="raise"):
            changes = np.diff(levels, axis=0)
            centered = changes - np.mean(changes, axis=0)
            sample = centered.T @ centered / (lookback_changes - 1)
            mean_variance = float(np.trace(sample) / 9)
            shrunk = (1 - shrink) * sample + shrink * mean_variance * np.eye(9)
        sample, _, _ = _covariance(sample)
        shrunk, _, _ = _covariance(shrunk)
        eigenvalues, eigenvectors = np.linalg.eigh(sample)
    except (FloatingPointError, OverflowError, np.linalg.LinAlgError) as error:
        raise ValueError("historical covariance exceeds finite calculation capacity") from error
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues = np.maximum(eigenvalues[order], 0)
    eigenvectors = eigenvectors[:, order]
    # Fix each arbitrary PCA sign for reproducible reporting; no economic naming.
    for column in range(9):
        pivot = int(np.argmax(np.abs(eigenvectors[:, column])))
        if eigenvectors[pivot, column] < 0:
            eigenvectors[:, column] *= -1
    total_variance = float(np.sum(eigenvalues))
    return {"covariance": shrunk.tolist(), "sample_covariance": sample.tolist(),
        "execution_date": execution.isoformat(), "training_start_date": selected[0][0].isoformat(),
        "training_end_date": selected[-1][0].isoformat(), "training_dates": [observed.isoformat() for observed, _ in selected],
        "change_count": lookback_changes, "level_count": len(selected), "shrinkage": shrink,
        "max_gap_days": max_gap_days, "maximum_observed_calendar_gap_days": max(gaps),
        "excluded_on_or_after_execution": excluded, "key_rate_nodes": list(KEY_RATE_NODES),
        "covariance_units": "Squared basis-point changes between successive observed curves",
        "pca": {"eigenvalues": eigenvalues.tolist(), "eigenvectors": eigenvectors.T.tolist(),
            "explained_variance_ratio": (eigenvalues / total_variance).tolist() if total_variance else [0.0] * 9,
            "basis": "Unshrunk sample covariance; eigenvectors are rows ordered by descending variance"},
        "scope": "Frozen prior window of supplied historical zero-yield levels; no simulated shocks or future endpoints. Calendar-gap controlled observation changes, not guaranteed daily returns. Source vintage is not independently authenticated."}
