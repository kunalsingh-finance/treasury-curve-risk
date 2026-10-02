"""Independent Treasury auction coupon-equivalent price/yield benchmarks.

This implementation does not import the project's bond or curve engine. It
follows 31 CFR 356 Appendix B II.D for ordinary non-indexed coupon securities:
simple interest over the first fractional coupon period, then semiannual
compounding. It is an auction YTM convention, not a GSW curve-discount PV or a
claim about current tradable prices. Long/short first coupon stubs, TIPS, FRNs,
bills, ex-coupon markets, settlement lags, and business-day adjustments are out
of scope. Dates are contractual unadjusted coupon dates.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


TREASURY_FORMULA_SOURCE = "https://www.treasurydirect.gov/files/laws-and-regulations/auction-regulations-uoc/31-cfr-part-356.pdf"
CONVENTION = "Treasury auction Appendix B II.D: first fractional period simple; subsequent periods semiannual"


class BenchmarkError(ValueError):
    """Unsupported benchmark input or inconsistent source metadata."""


def _date(value: str | date, name: str) -> date:
    if type(value) is date:
        return value
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise BenchmarkError(f"{name} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise BenchmarkError(f"invalid {name}") from exc


def _number(value: float | str | Decimal, name: str) -> Decimal:
    if isinstance(value, bool):
        raise BenchmarkError(f"{name} must be numeric")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise BenchmarkError(f"{name} must be numeric") from exc
    if not result.is_finite():
        raise BenchmarkError(f"{name} must be finite")
    return result


def _coupon_date(maturity: date, periods_before: int) -> date:
    absolute_month = maturity.year * 12 + maturity.month - 1 - periods_before * 6
    year, month0 = divmod(absolute_month, 12)
    month = month0 + 1
    end_of_month = maturity.day == monthrange(maturity.year, maturity.month)[1]
    day = monthrange(year, month)[1] if end_of_month else min(maturity.day, monthrange(year, month)[1])
    return date(year, month, day)


def treasury_price_from_yield(
    settlement_date: str | date,
    maturity_date: str | date,
    coupon_rate: float | str | Decimal,
    yield_rate: float | str | Decimal,
    face: float | str | Decimal = 100.0,
) -> dict[str, Any]:
    """Return independent auction-convention clean/dirty prices and accrual.

    coupon_rate and yield_rate are annual decimal fractions. Monetary outputs
    are in the same units as face. Settlement on a coupon date excludes that
    date's coupon and has zero accrued interest. EOM maturity preserves EOM
    coupon dates. The caller supplies a regular coupon bond with no stubs.
    """
    settlement = _date(settlement_date, "settlement_date")
    maturity = _date(maturity_date, "maturity_date")
    coupon = _number(coupon_rate, "coupon_rate")
    yield_value = _number(yield_rate, "yield_rate")
    face_value = _number(face, "face")
    if settlement >= maturity:
        raise BenchmarkError("settlement must precede maturity")
    if coupon < 0 or face_value <= 0:
        raise BenchmarkError("coupon must be nonnegative and face positive")
    if yield_value <= -2:
        raise BenchmarkError("semiannual yield base must be positive")
    future_dates = []
    for offset in range(401):
        payment = _coupon_date(maturity, offset)
        if payment <= settlement:
            previous = payment
            break
        future_dates.append(payment)
    else:
        raise BenchmarkError("benchmark horizon exceeds 200 years")
    future_dates.reverse()
    next_payment = future_dates[0]
    period_days = (next_payment - previous).days
    elapsed_days = (settlement - previous).days
    remaining_days = (next_payment - settlement).days
    periods_after_first = len(future_dates) - 1
    with localcontext() as context:
        context.prec = 60
        half_coupon = face_value * coupon / 2
        half_yield = yield_value / 2
        v = 1 / (1 + half_yield)
        # Appendix B's a_n is independently evaluated as a geometric sum. This
        # also supports zero yields without a division by zero.
        annuity = sum((v**period for period in range(1, periods_after_first + 1)), Decimal(0))
        coupon_date_value = half_coupon + half_coupon * annuity + face_value * v**periods_after_first
        first_period_factor = 1 + Decimal(remaining_days) / period_days * half_yield
        dirty = coupon_date_value / first_period_factor
        accrued = half_coupon * Decimal(elapsed_days) / period_days
        clean = dirty - accrued
    monetary = {"dirty_price": float(dirty), "clean_price": float(clean), "accrued_interest": float(accrued)}
    if not all(math.isfinite(value) for value in monetary.values()):
        raise BenchmarkError("benchmark result exceeds floating-point output capacity")
    return {
        **monetary,
        "previous_coupon_date": previous.isoformat(),
        "next_coupon_date": next_payment.isoformat(),
        "days_accrued": elapsed_days,
        "days_to_next_coupon": remaining_days,
        "days_in_coupon_period": period_days,
        "coupon_periods_remaining": len(future_dates),
        "convention": CONVENTION,
        "formula_source": TREASURY_FORMULA_SOURCE,
    }


def official_auction_benchmarks(
    instruments: Iterable[Mapping[str, Any]], price_tolerance: float = 0.000001,
) -> list[dict[str, Any]]:
    """Compare original issue-date YTM prices with independently published prices.

    The default tolerance is one millionth of a dollar per face 100, matching
    published six-decimal auction prices plus small calculation differences.
    These comparisons validate price/yield conventions, not GSW market fit.
    """
    if not math.isfinite(price_tolerance) or price_tolerance <= 0:
        raise BenchmarkError("price_tolerance must be positive and finite")
    results = []
    for instrument in instruments:
        required = {"cusip", "issue_date", "dated_date", "maturity_date", "coupon_rate", "source_url", "original_official_yield", "original_official_price_per_100"}
        if not required.issubset(instrument):
            raise BenchmarkError("instrument lacks required official auction metadata")
        issue = _date(instrument["issue_date"], "issue_date")
        dated = _date(instrument["dated_date"], "dated_date")
        maturity = _date(instrument["maturity_date"], "maturity_date")
        if not dated <= issue < maturity:
            raise BenchmarkError("dated/issue/maturity dates are inconsistent")
        calculated = treasury_price_from_yield(issue, maturity, instrument["coupon_rate"], instrument["original_official_yield"], 100)
        if calculated["previous_coupon_date"] != dated.isoformat():
            raise BenchmarkError("initial stubs or reopenings require separate documented treatment")
        official = float(_number(instrument["original_official_price_per_100"], "official price"))
        if official <= 0:
            raise BenchmarkError("official price must be positive")
        error = calculated["clean_price"] - official
        results.append({
            "cusip": instrument["cusip"],
            "settlement_date": issue.isoformat(),
            "official_clean_price_per_100": official,
            "calculated_clean_price_per_100": calculated["clean_price"],
            "calculated_accrued_interest_per_100": calculated["accrued_interest"],
            "error_per_100": error,
            "tolerance_per_100": price_tolerance,
            "passed": abs(error) <= price_tolerance,
            "source_url": instrument["source_url"],
            "convention": CONVENTION,
        })
    return results


def load_instrument_config(path: str | Path) -> dict[str, Any]:
    """Load and check selected-field source snapshots and portfolio date bounds.

    Snapshot hashes cover archived UTF-8 transcriptions, not original PDF bytes.
    They verify local consistency and do not authenticate publisher content.
    """
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"cannot read instrument config: {exc}") from exc
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise BenchmarkError("unsupported instrument config schema")
    for group_name in ("latest", "historical"):
        group = config.get(group_name)
        if not isinstance(group, dict):
            raise BenchmarkError(f"missing {group_name} portfolio")
        valuation = _date(group.get("valuation_date"), "valuation_date")
        for role in ("targets", "hedges"):
            instruments = group.get(role)
            if not isinstance(instruments, list) or not instruments:
                raise BenchmarkError(f"missing {group_name} {role}")
            identifiers = set()
            for instrument in instruments:
                if not isinstance(instrument, dict):
                    raise BenchmarkError("instrument must be a mapping")
                cusip = instrument.get("cusip")
                if not isinstance(cusip, str) or not re.fullmatch(r"[A-Z0-9]{8}[0-9]", cusip) or cusip in identifiers:
                    raise BenchmarkError("invalid or duplicate instrument CUSIP")
                identifiers.add(cusip)
                issue = _date(instrument.get("issue_date"), "issue_date")
                dated = _date(instrument.get("dated_date"), "dated_date")
                maturity = _date(instrument.get("maturity_date"), "maturity_date")
                if not dated <= issue <= valuation < maturity:
                    raise BenchmarkError("instrument unavailable or matured at portfolio inception")
                if _number(instrument.get("coupon_rate"), "coupon_rate") < 0 or _number(instrument.get("face"), "face") <= 0:
                    raise BenchmarkError("invalid coupon or face")
                source_url = instrument.get("source_url")
                if not isinstance(source_url, str) or not re.fullmatch(r"https://www\.treasurydirect\.gov/instit/annceresult/press/preanre/[0-9]{4}/R_[0-9]{8}_[0-9]+\.pdf", source_url):
                    raise BenchmarkError("unsupported instrument source URL")
                snapshot = instrument.get("source_snapshot_text")
                if not isinstance(snapshot, str) or hashlib.sha256(snapshot.encode("utf-8")).hexdigest() != instrument.get("source_snapshot_sha256"):
                    raise BenchmarkError("instrument transcription SHA-256 mismatch")
    return config
