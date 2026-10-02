"""Theoretical cash-bond hedges of nine key-rate dollar-per-basis-point risks.

Matrix columns describe a hedge instrument per 100 face units. A weight of
-2 therefore means short 200 face units; financing and execution are unmodeled.
Least squares minimizes the unweighted Euclidean norm of the residual buckets,
not a covariance-weighted risk measure. A small residual for one portfolio does
not imply that the instrument matrix spans every possible curve exposure.
"""

from __future__ import annotations

import math
from numbers import Real

import numpy as np


KEY_RATE_NODES = (0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 20.0, 30.0)
ILL_CONDITION_THRESHOLD = 1e8


def _numeric_array(value: object, name: str) -> np.ndarray:
    try:
        array = np.asarray(value)
        if array.dtype.kind not in "iuf":
            raise ValueError(f"{name} must contain real numeric values")
        with np.errstate(over="raise", invalid="raise"):
            array = np.asarray(array, dtype=float)
    except (TypeError, ValueError, OverflowError, FloatingPointError) as error:
        raise ValueError(f"{name} must contain finite real numeric values") from error
    if not np.isfinite(array).all():
        raise ValueError(f"{name} must contain finite real numeric values")
    return array


def _target(value: object) -> np.ndarray:
    array = _numeric_array(value, "target_exposure")
    if array.shape != (len(KEY_RATE_NODES),):
        raise ValueError("target_exposure must have exactly nine key-rate entries")
    return array


def _matrix(value: object) -> np.ndarray:
    array = _numeric_array(value, "hedge_matrix")
    if array.ndim != 2 or array.shape[0] != len(KEY_RATE_NODES) or array.shape[1] == 0:
        raise ValueError("hedge_matrix must have shape (9, number_of_hedges), with at least one hedge")
    if np.any(np.all(array == 0, axis=0)):
        raise ValueError("each hedge instrument must have nonzero key-rate exposure")
    return array


def _ridge(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError("ridge_penalty must be a finite nonnegative number")
    try:
        number = float(value)
    except (ValueError, OverflowError) as error:
        raise ValueError("ridge_penalty must be a finite nonnegative number") from error
    if not math.isfinite(number) or number < 0:
        raise ValueError("ridge_penalty must be a finite nonnegative number")
    return number


def _finite(number: float, name: str) -> float:
    if not math.isfinite(number):
        raise ValueError(f"{name} exceeds finite floating-point reporting capacity")
    return number


def _total(array: np.ndarray, name: str) -> float:
    try:
        return _finite(math.fsum(float(value) for value in array), name)
    except OverflowError as error:
        raise ValueError(f"{name} exceeds finite floating-point reporting capacity") from error


def _diagnostics(matrix: np.ndarray, rank: int, singular_values: np.ndarray) -> dict:
    if not np.isfinite(singular_values).all():
        raise ValueError("matrix singular values exceed finite reporting capacity")
    deficient = rank < min(matrix.shape)
    condition = None
    if singular_values[-1] > 0:
        with np.errstate(over="ignore"):
            ratio = float(singular_values[0] / singular_values[-1])
        if math.isfinite(ratio):
            condition = ratio
    return {
        "matrix_rank": int(rank),
        "singular_values": singular_values.tolist(),
        "condition_number": condition,
        "condition_status": "singular_or_numerically_rank_deficient" if deficient else "full_rank",
        "rank_deficient": bool(deficient),
        "unique_weights": bool(rank == matrix.shape[1]),
        "fully_spans_key_rates": bool(rank == len(KEY_RATE_NODES)),
        "ill_conditioned": bool(condition is None or condition > ILL_CONDITION_THRESHOLD),
        "ill_condition_threshold": ILL_CONDITION_THRESHOLD,
    }


def _result(target: np.ndarray, matrix: np.ndarray, weights: np.ndarray,
            diagnostics: dict, method: str, penalty: float) -> dict:
    if not np.isfinite(weights).all():
        raise ValueError("hedge weights exceed finite floating-point capacity")
    try:
        with np.errstate(over="raise", invalid="raise"):
            residual = target + matrix @ weights
            face = weights * 100.0
        if not np.isfinite(residual).all() or not np.isfinite(face).all():
            raise ValueError("hedge amounts exceed finite floating-point capacity")
        target_norm = _finite(math.hypot(*(float(value) for value in target)), "target norm")
        residual_norm = _finite(math.hypot(*(float(value) for value in residual)), "residual norm")
        ratio = _finite(residual_norm / target_norm, "residual ratio") if target_norm else None
        gross = _finite(math.fsum(abs(float(value)) for value in face), "gross face units")
    except (OverflowError, FloatingPointError) as error:
        raise ValueError("hedge calculation exceeds finite floating-point capacity") from error
    return {
        "method": method,
        "key_rate_nodes": list(KEY_RATE_NODES),
        "weights": weights.tolist(),
        "face_amounts": face.tolist(),
        "gross_absolute_face_units": gross,
        "target_exposure": target.tolist(),
        "residual_exposure": residual.tolist(),
        "target_norm": target_norm,
        "residual_norm": residual_norm,
        "residual_ratio": ratio,
        "target_total_dv01": _total(target, "target total DV01"),
        "residual_total_dv01": _total(residual, "residual total DV01"),
        "ridge_penalty": penalty,
        **diagnostics,
        "weight_units": "100 face units per weight; negative weights are short cash bonds",
        "exposure_units": "dollars per 1bp at each key-rate node",
        "scope": "theoretical units; no financing, transaction costs, execution or covariance optimization",
    }


def select_hedge(target_exposure: object, hedge_matrix: object, *, ridge_penalty: float = 0.0) -> dict:
    """Minimize ||target + H @ w||² + ridge_penalty * ||w||².

    H is a nine-row matrix with one column per instrument, per 100 face units.
    With zero penalty, NumPy's numerical-rank cutoff and minimum-norm solution
    apply, including singular systems. Diagnostics describe the original H,
    even when ridge regularization produces a unique penalized solution. The
    penalty acts on 100-face-unit weights and is not a transaction-cost model.
    """
    target, matrix, penalty = _target(target_exposure), _matrix(hedge_matrix), _ridge(ridge_penalty)
    try:
        weights, _, rank, singular_values = np.linalg.lstsq(matrix, -target, rcond=None)
        diagnostics = _diagnostics(matrix, rank, singular_values)
        if penalty:
            augmented = np.vstack((matrix, math.sqrt(penalty) * np.eye(matrix.shape[1])))
            augmented_target = np.concatenate((-target, np.zeros(matrix.shape[1])))
            weights = np.linalg.lstsq(augmented, augmented_target, rcond=None)[0]
    except np.linalg.LinAlgError as error:
        raise ValueError("hedge least-squares calculation failed") from error
    return _result(target, matrix, weights, diagnostics, "unweighted_key_rate_least_squares", penalty)


def parallel_hedge(target_exposure: object, hedge_exposure: object) -> dict:
    """Match total DV01 with one designated hedge; retain its bucket residual.

    This uses sums of key-rate exposures as the parallel-risk proxy. It does not
    claim that the separate finite-difference parallel DV01 is identical to that
    sum, or that matching the sum removes curve-shape risk.
    """
    target = _target(target_exposure)
    vector = _numeric_array(hedge_exposure, "hedge_exposure")
    if vector.shape != (len(KEY_RATE_NODES),):
        raise ValueError("hedge_exposure must have exactly nine key-rate entries")
    matrix = _matrix(vector[:, None])
    hedge_total = _total(vector, "hedge total DV01")
    if hedge_total == 0:
        raise ValueError("designated hedge has zero total DV01 and cannot hedge parallel exposure")
    try:
        weights = np.array([-_total(target, "target total DV01") / hedge_total])
        _, _, rank, singular_values = np.linalg.lstsq(matrix, -target, rcond=None)
    except np.linalg.LinAlgError as error:
        raise ValueError("hedge diagnostics calculation failed") from error
    return _result(target, matrix, weights, _diagnostics(matrix, rank, singular_values), "parallel_dv01_only", 0.0)
