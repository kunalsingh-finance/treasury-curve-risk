"""Dated Treasury research: constrained risk, prior-date rules and funded ledgers."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict
from datetime import date
from numbers import Real
from pathlib import Path

from .analysis import benchmark_curves, curve_from_record, shock_scenarios
from .curve import KEY_RATE_NODES
from .dated_bonds import DatedBond
from .accounting import CashLedger, FundingPolicy, Position
from .hedge import select_hedge
from .optimization import estimate_covariance, optimize_covariance_hedge
from .benchmarks import load_instrument_config as validated_config

METHODS = ("unhedged", "duration", "unweighted", "constrained")
DEFAULT_FACES = (2_000_000.0, 4_000_000.0, 6_000_000.0)
_DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


def _iso_date(value: object, name: str) -> date:
    if not isinstance(value, str) or not _DATE_PATTERN.fullmatch(value):
        raise ValueError(f"{name} must be a strict ISO YYYY-MM-DD date")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a valid ISO calendar date") from error


def _real(value: object, name: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite real number")
    try:
        result = float(value)
    except (ValueError, OverflowError) as error:
        raise ValueError(f"{name} must be a finite real number") from error
    if not math.isfinite(result) or (nonnegative and result < 0):
        raise ValueError(f"{name} must be finite" + (" and nonnegative" if nonnegative else ""))
    return result


def _face_amounts(values, count: int) -> tuple[float, ...]:
    """Freeze a supplied iterable before pricing, sizing or constructing positions."""
    try:
        supplied = tuple(values)
    except TypeError as error:
        raise ValueError("Positive finite real face amounts are required for each target") from error
    if len(supplied) != count:
        raise ValueError("Positive finite real face amounts are required for each target")
    faces = tuple(_real(value, "face amount") for value in supplied)
    if any(face <= 0 for face in faces):
        raise ValueError("Positive finite real face amounts are required for each target")
    return faces


def _validate_record_dates(records: list[dict]) -> None:
    if not isinstance(records, list) or not records:
        raise ValueError("Curve records must be a nonempty list")
    previous = None
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("Each curve record must be an object with an ISO date")
        observed = _iso_date(row.get("date"), "curve record date")
        if previous is not None and observed <= previous:
            raise ValueError("Curve record dates must be unique and strictly increasing")
        previous = observed


def load_instrument_config(path: Path) -> dict:
    config = validated_config(path)
    if config.get("schema_version") != 1:
        raise ValueError("Unsupported instrument configuration schema")
    for era in ("latest", "historical"):
        for group in ("targets", "hedges"):
            entries = config[era][group]
            if len({entry["cusip"] for entry in entries}) != len(entries):
                raise ValueError("Duplicate instrument identifiers")
            for entry in entries:
                if entry["face"] != 100:
                    raise ValueError("Instrument risk columns must use 100 face")
                digest = hashlib.sha256(entry["source_snapshot_text"].encode("utf-8")).hexdigest()
                if digest != entry["source_snapshot_sha256"]:
                    raise ValueError(f"Instrument transcription hash mismatch: {entry['cusip']}")
    return config


def code_fingerprint(root: Path) -> str:
    """Fingerprint model source so a saved release cannot masquerade as current."""
    digest = hashlib.sha256()
    for path in sorted((root / "treasury_risk").glob("*.py")):
        digest.update(path.name.encode("utf-8") + b"\0" + path.read_bytes())
    return digest.hexdigest()


def instruments(config: dict, era: str, group: str) -> list[DatedBond]:
    fields = ("cusip", "label", "issue_date", "dated_date", "maturity_date", "coupon_rate", "face")
    return [DatedBond(**{field: item[field] for field in fields}) for item in config[era][group]]


def level_history(records: list[dict], audit: dict) -> list[dict]:
    rejected = {row["date"] for row in audit["exceptions"]}
    return [{"date": item["date"], "zero_yields_bps": [curve_from_record(item).zero_yield(node) * 10000
                                                     for node in KEY_RATE_NODES]}
            for item in records if item["date"] not in rejected]


def risk_inputs(targets: list[DatedBond], hedges: list[DatedBond], faces: list[float], curve, settlement: str) -> dict:
    faces = _face_amounts(faces, len(targets))
    if any(bond.face != 100 for bond in targets + hedges):
        raise ValueError("Dated risk instruments must have face 100; scale positions with units")
    target_vectors = [bond.key_rate_dv01(curve, settlement) for bond in targets]
    hedge_vectors = [bond.key_rate_dv01(curve, settlement) for bond in hedges]
    target = [math.fsum(face / 100 * vector[node] for face, vector in zip(faces, target_vectors))
              for node in KEY_RATE_NODES]
    matrix = [[vector[node] for vector in hedge_vectors] for node in KEY_RATE_NODES]
    return {"target_exposure": target, "hedge_matrix": matrix,
            "target_matrix": [[vector[node] for vector in target_vectors] for node in KEY_RATE_NODES],
            "target_parallel_dv01_per_100": [bond.parallel_dv01(curve, settlement) for bond in targets],
            "target_parallel_dv01": math.fsum(face / 100 * bond.parallel_dv01(curve, settlement)
                                               for face, bond in zip(faces, targets)),
            "hedge_parallel_dv01": [bond.parallel_dv01(curve, settlement) for bond in hedges],
            "hedge_prices": [bond.price(curve, settlement) for bond in hedges],
            "target_price": math.fsum(face / 100 * bond.price(curve, settlement) for face, bond in zip(faces, targets))}


def hedge_methods(inputs: dict, covariance: list, gross_face_limit: float,
                  position_face_limit: float, cash_neutral: bool = False) -> dict:
    matrix, target = inputs["hedge_matrix"], inputs["target_exposure"]
    n = len(inputs["hedge_prices"])
    if n == 0:
        raise ValueError("At least one outstanding hedge instrument is required")
    # Choose the longest available note nearest the 10-year risk node externally.
    duration_index = min(range(n), key=lambda i: abs(inputs["hedge_tenors"][i] - 10))
    parallel = inputs["hedge_parallel_dv01"][duration_index]
    if parallel <= 0:
        raise ValueError("Designated duration hedge has no outstanding rate exposure")
    weights = [0.0] * n
    weights[duration_index] = -inputs["target_parallel_dv01"] / parallel
    unweighted = select_hedge(target, matrix)
    constrained = optimize_covariance_hedge(
        target, matrix, covariance, target_parallel_dv01=inputs["target_parallel_dv01"],
        hedge_parallel_dv01=inputs["hedge_parallel_dv01"], hedge_prices=inputs["hedge_prices"],
        gross_face_limit=gross_face_limit, position_face_limits=[position_face_limit] * n,
        cash_neutral=cash_neutral, ridge_penalty=0.0)
    return {"unhedged": {"weights": [0.0] * n}, "duration": {"weights": weights},
            "unweighted": unweighted, "constrained": constrained}


def add_tenors(inputs: dict, hedges: list[DatedBond], settlement: str) -> None:
    origin = date.fromisoformat(settlement)
    inputs["hedge_tenors"] = [(bond.maturity_date - origin).days / 365 for bond in hedges]


def latest_analysis(records: list[dict], config: dict, audit: dict, *, faces=DEFAULT_FACES,
                    valuation_date: str | None = None, lookback: int = 252,
                    gross_multiple: float = 2.0, position_multiple: float = 1.5,
                    cash_neutral: bool = False) -> dict:
    _validate_record_dates(records)
    gross_multiple = _real(gross_multiple, "gross_multiple", nonnegative=True)
    position_multiple = _real(position_multiple, "position_multiple", nonnegative=True)
    if not isinstance(cash_neutral, bool):
        raise ValueError("cash_neutral must be a boolean")
    valuation_date = records[-1]["date"] if valuation_date is None else valuation_date
    _iso_date(valuation_date, "valuation_date")
    selected = next((item for item in records if item["date"] == valuation_date), None)
    if selected is None or valuation_date in {row["date"] for row in audit["exceptions"]}:
        raise ValueError("Requested curve date is unavailable or quarantined")
    curve = curve_from_record(selected)
    targets, hedges = instruments(config, "latest", "targets"), instruments(config, "latest", "hedges")
    faces = _face_amounts(faces, len(targets))
    inputs = risk_inputs(targets, hedges, faces, curve, valuation_date)
    add_tenors(inputs, hedges, valuation_date)
    covariance = estimate_covariance(level_history(records, audit), valuation_date,
                                     lookback_changes=lookback, shrinkage=0.10)
    methods = hedge_methods(inputs, covariance["covariance"], sum(faces) * gross_multiple,
                            sum(faces) * position_multiple, cash_neutral)
    details = []
    for item, bond, face in zip(config["latest"]["targets"], targets, faces):
        details.append({"cusip": bond.cusip, "label": bond.label, "face": face,
                        "maturity_date": bond.maturity_date.isoformat(), "coupon_rate": bond.coupon_rate,
                        "dirty_price": bond.price(curve, valuation_date) * face / 100,
                        "clean_price": bond.clean_price(curve, valuation_date) * face / 100,
                        "accrued_interest": bond.accrued_interest(valuation_date) * face / 100,
                        "source_url": item["source_url"]})
    scenarios = []
    base_hedge_prices = inputs["hedge_prices"]
    for name, shocked in shock_scenarios(curve):
        target_change = math.fsum(face / 100 * bond.price(shocked, valuation_date) for face, bond in zip(faces, targets)) - inputs["target_price"]
        hedge_changes = [bond.price(shocked, valuation_date) - price for bond, price in zip(hedges, base_hedge_prices)]
        values = {method: (target_change + math.fsum(w * p for w, p in zip(result["weights"], hedge_changes))
                            if result.get("weights") is not None else None)
                  for method, result in methods.items()}
        scenarios.append({"name": name, **values})
    return {"date": valuation_date, "faces": list(faces), "portfolio": details,
            "hedges": [{"cusip": bond.cusip, "label": bond.label, "maturity_date": bond.maturity_date.isoformat(),
                        "coupon_rate": bond.coupon_rate, "dirty_price_per_100": price}
                       for bond, price in zip(hedges, base_hedge_prices)],
            "risk_inputs": inputs, "covariance": covariance, "methods": methods, "scenarios": scenarios,
            "constraints": {"gross_face_limit": sum(faces) * gross_multiple,
                            "position_face_limit": sum(faces) * position_multiple, "cash_neutral": cash_neutral},
            "scope": "Actual sourced security terms, hypothetical holdings and fitted-curve PVs; no current quote validation"}


def simulate_period(records: list[dict], config: dict, audit: dict, start_date: str, end_date: str,
                    *, faces=DEFAULT_FACES, lookback=252, gross_multiple=2.0,
                    position_multiple=1.5, cash_rate=0.02, funding_rate=0.05,
                    transaction_cost_bps=1.0, capital_buffer_multiple=0.10) -> dict:
    _validate_record_dates(records)
    first_bound, last_bound = _iso_date(start_date, "start_date"), _iso_date(end_date, "end_date")
    if last_bound < first_bound:
        raise ValueError("end_date cannot precede start_date")
    gross_multiple = _real(gross_multiple, "gross_multiple", nonnegative=True)
    position_multiple = _real(position_multiple, "position_multiple", nonnegative=True)
    capital_buffer_multiple = _real(capital_buffer_multiple, "capital_buffer_multiple", nonnegative=True)
    rejected = {row["date"] for row in audit["exceptions"]}
    window = [item for item in records if start_date <= item["date"] <= end_date]
    if len(window) < 2 or any(item["date"] in rejected for item in window):
        raise ValueError("Historical period is insufficient or contains quarantined dates")
    targets, all_hedges = instruments(config, "historical", "targets"), instruments(config, "historical", "hedges")
    faces = _face_amounts(faces, len(targets))
    history = level_history(records, audit)
    policy = FundingPolicy(cash_rate_annual=cash_rate, funding_rate_annual=funding_rate,
                           transaction_cost_bps=transaction_cost_bps, short_margin_rate=0.02,
                           long_haircut_rate=0.0, unsecured_limit=0.0)
    first_date = window[0]["date"]
    initial_curve = curve_from_record(window[0])
    equity = math.fsum(face / 100 * bond.price(initial_curve, first_date) for face, bond in zip(faces, targets)) + sum(faces) * capital_buffer_multiple
    if not math.isfinite(equity) or equity <= 0:
        raise ValueError("Initial equity must be positive and finite for wealth-return metrics")
    ledgers, rows, decisions = {}, [], []
    previous_month = None
    for current in window:
        day = current["date"]
        curve = curve_from_record(current)
        month = day[:7]
        if month != previous_month:
            previous = next((item for item in reversed(records) if item["date"] < day and item["date"] not in rejected), None)
            if previous is None:
                raise ValueError("No prior curve is available for a rebalance decision")
            if (date.fromisoformat(day) - date.fromisoformat(previous["date"])).days > 7:
                raise ValueError("Prior decision curve is stale by more than seven calendar days")
            decision_curve = curve_from_record(previous)
            active = [bond for bond in all_hedges if bond.issue_date <= date.fromisoformat(day) and bond.cashflows(day)]
            inputs = risk_inputs(targets, active, faces, decision_curve, day)
            add_tenors(inputs, active, day)
            covariance = estimate_covariance(history, day, lookback_changes=lookback, shrinkage=0.10)
            methods = hedge_methods(inputs, covariance["covariance"], sum(faces) * gross_multiple,
                                    sum(faces) * position_multiple)
            if methods["constrained"].get("weights") is None:
                raise ValueError(f"Required constrained hedge failed on {day}: {methods['constrained']}")
            decisions.append({"trade_date": day, "decision_curve_date": previous["date"],
                              "covariance": covariance, "active_hedges": [bond.cusip for bond in active],
                              "risk_inputs": inputs, "methods": methods})
            for method in METHODS:
                positions = [Position(bond, face / 100) for bond, face in zip(targets, faces)]
                positions += [Position(bond, weight) for bond, weight in zip(active, methods[method]["weights"])]
                if method not in ledgers:
                    ledgers[method] = CashLedger(positions, curve, day, initial_equity=equity, policy=policy)
                    row = ledgers[method].initial_row
                else:
                    row = ledgers[method].rebalance(positions, curve, day)
                rows.append({"method": method, **row})
            previous_month = month
        else:
            for method in METHODS:
                rows.append({"method": method, **ledgers[method].mark(curve, day)})
    summaries = {}
    for method in METHODS:
        selected_rows = [row for row in rows if row["method"] == method]
        peak = equity
        worst = 0.0
        for row in selected_rows:
            peak = max(peak, row["wealth"])
            worst = min(worst, row["wealth"] / peak - 1)
        summaries[method] = {"initial_equity": equity, "final_wealth": selected_rows[-1]["wealth"],
                             "net_pnl": selected_rows[-1]["wealth"] - equity,
                             "wealth_change_fraction": selected_rows[-1]["wealth"] / equity - 1,
                             "maximum_drawdown": worst,
                             "max_cash_roll_residual": max(abs(row["cash_roll_residual"]) for row in selected_rows),
                             "accounting_totals": {key: selected_rows[-1].get(key) for key in selected_rows[-1] if key.startswith("cumulative_")},
                             "collateral_breach_observations": sum(bool(row.get("collateral_breach")) for row in selected_rows),
                             "financing_gap_observations": sum(row.get("financing_gap", 0) > 1e-6 for row in selected_rows)}
    return {"start_date": first_date, "end_date": window[-1]["date"], "observations": len(window),
            "faces": list(faces), "initial_equity": equity, "funding_policy": asdict(policy), "rows": rows,
            "strategy_constraints": {"gross_face_limit": sum(faces) * gross_multiple,
                                     "position_face_limit": sum(faces) * position_multiple,
                                     "cash_neutral": False},
            "decisions": decisions, "summaries": summaries,
            "rule": "Monthly weights use prior observed curve and covariance levels strictly before trade date; current-date curve only prices the trade/mark",
            "scope": "Dated, financed model wealth with coupon/principal cash and costs. Revised curve inputs and hypothetical funding/execution; not realized trading performance."}
