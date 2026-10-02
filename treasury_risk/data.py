"""Load a pinned current-vintage Federal Reserve GSW nominal-curve snapshot.

Beta parameters remain in percentage points; tau parameters are years.
Published SVENY zero yields are converted from percent to decimal fractions and
are continuously compounded. Missing values are never forward filled. These
are revised fitted observations, not historical publication-time vintages.
"""

from __future__ import annotations

import csv
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any


OFFICIAL_SOURCE_URL = "https://www.federalreserve.gov/data/yield-curve-tables/feds200628.csv"
OFFICIAL_SOURCE_PAGE = "https://www.federalreserve.gov/data/nominal-yield-curve.htm"
MODEL_SUPPORT_START = date(1980, 1, 1)
PARAMETERS = ("BETA0", "BETA1", "BETA2", "BETA3", "TAU1", "TAU2")
YIELD_COLUMNS = tuple(f"SVENY{maturity:02d}" for maturity in range(1, 31))
_DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


class GSWDataError(ValueError):
    """A source, schema, date, or snapshot-integrity control failed."""


def _date(value: str, label: str) -> date:
    if not isinstance(value, str) or not _DATE_PATTERN.fullmatch(value):
        raise GSWDataError(f"{label} must be a strict ISO YYYY-MM-DD date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise GSWDataError(f"invalid {label}: {value}") from exc


def _missing(value: str) -> bool:
    return value.strip() in ("", "NA")


def _finite(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite number")
    return number


def load_gsw(
    path: str | Path,
    start_date: str = "2000-01-01",
    end_date: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return curve records and row-level quality counts from the local CSV.

    Record fields: date (ISO string), beta0..beta3 (percentage-point floats),
    tau1/tau2 (positive year floats), published_zero_yields (int maturities 1..30
    mapped to continuously compounded decimal fractions or None), data_vintage.

    The date range is inclusive. Pre-1980 observations are excluded because
    this loader supports the six-parameter Svensson model, not the earlier
    Nelson-Siegel rows with negative tau2 sentinels. Every data-row date must be
    valid and strictly increasing, including rows outside the requested range.
    Structurally malformed rows fail; missing/invalid numeric observations are
    counted and excluded. A missing published yield remains None.
    """
    requested_start = _date(start_date, "start_date")
    requested_end = _date(end_date, "end_date") if end_date is not None else None
    if requested_end is not None and requested_end < requested_start:
        raise GSWDataError("end_date precedes start_date")
    effective_start = max(requested_start, MODEL_SUPPORT_START)
    quality: dict[str, Any] = {
        "rows_read": 0,
        "out_of_range": 0,
        "missing_param_rows": 0,
        "invalid_param_rows": 0,
        "invalid_yield_rows": 0,
        "valid_rows": 0,
        "start_date": None,
        "end_date": None,
        "requested_start_date": start_date,
        "requested_end_date": end_date,
        "effective_start_date": effective_start.isoformat(),
        "data_vintage": "current_vintage",
    }
    records: list[dict[str, Any]] = []
    previous_date: date | None = None
    try:
        with Path(path).open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.reader(source, strict=True)
            header: list[str] | None = None
            for row in reader:
                if row and row[0] == "Date":
                    header = row
                    break
            if header is None:
                raise GSWDataError("GSW Date,BETA0... header not found after preamble")
            if header[:5] != ["Date", "BETA0", "BETA1", "BETA2", "BETA3"]:
                raise GSWDataError("GSW header must begin Date,BETA0,BETA1,BETA2,BETA3")
            if len(set(header)) != len(header):
                raise GSWDataError("duplicate CSV column names")
            missing_columns = set(PARAMETERS + YIELD_COLUMNS).difference(header)
            if missing_columns:
                raise GSWDataError("missing required columns: " + ", ".join(sorted(missing_columns)))
            indices = {column: header.index(column) for column in ("Date",) + PARAMETERS + YIELD_COLUMNS}
            for row in reader:
                if not row or all(not value.strip() for value in row):
                    continue
                quality["rows_read"] += 1
                if len(row) != len(header):
                    raise GSWDataError(f"row {reader.line_num} has an incorrect column count")
                observed_date = _date(row[indices["Date"]], f"row {reader.line_num} date")
                if previous_date is not None and observed_date <= previous_date:
                    raise GSWDataError(f"duplicate or nonincreasing date: {observed_date.isoformat()}")
                previous_date = observed_date
                if observed_date < effective_start or (requested_end is not None and observed_date > requested_end):
                    quality["out_of_range"] += 1
                    continue
                raw_parameters = [row[indices[column]] for column in PARAMETERS]
                if all(_missing(value) for value in raw_parameters):
                    quality["missing_param_rows"] += 1
                    continue
                try:
                    if any(_missing(value) for value in raw_parameters):
                        raise ValueError("partially missing parameters")
                    parameters = dict(zip(PARAMETERS, map(_finite, raw_parameters)))
                    if parameters["TAU1"] <= 0 or parameters["TAU2"] <= 0:
                        raise ValueError("nonpositive tau")
                except ValueError:
                    quality["invalid_param_rows"] += 1
                    continue
                try:
                    zero_yields = {
                        maturity: None if _missing(row[indices[column]]) else _finite(row[indices[column]]) / 100.0
                        for maturity, column in enumerate(YIELD_COLUMNS, start=1)
                    }
                except ValueError:
                    quality["invalid_yield_rows"] += 1
                    continue
                records.append({
                    "date": observed_date.isoformat(),
                    **{key.lower(): value for key, value in parameters.items()},
                    "published_zero_yields": zero_yields,
                    "data_vintage": "current_vintage",
                })
    except (OSError, UnicodeError, csv.Error) as exc:
        raise GSWDataError(f"cannot read GSW source: {exc}") from exc
    quality["valid_rows"] = len(records)
    if records:
        quality["start_date"] = records[0]["date"]
        quality["end_date"] = records[-1]["date"]
    return records, quality


def validate_source_manifest(csv_path: str | Path, manifest_path: str | Path) -> dict[str, Any]:
    """Verify official URL, exact byte length, and SHA-256; return manifest data.

    Matching a local manifest establishes snapshot consistency, not a digital
    signature or historical as-of availability. No network access is performed.
    """
    try:
        with Path(manifest_path).open("r", encoding="utf-8-sig") as source:
            manifest = json.load(source)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GSWDataError(f"cannot read source manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise GSWDataError("source manifest must be a JSON object")
    if manifest.get("source_url") != OFFICIAL_SOURCE_URL:
        raise GSWDataError("manifest source_url is not the official GSW CSV URL")
    if "source_page" in manifest and manifest["source_page"] != OFFICIAL_SOURCE_PAGE:
        raise GSWDataError("manifest source_page is not the official nominal-curve page")
    expected_bytes = manifest.get("bytes")
    expected_hash = manifest.get("sha256")
    if not isinstance(expected_bytes, int) or isinstance(expected_bytes, bool) or expected_bytes < 0:
        raise GSWDataError("manifest bytes must be a nonnegative integer")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise GSWDataError("manifest sha256 must be 64 lowercase hexadecimal characters")
    digest = hashlib.sha256()
    size = 0
    try:
        with Path(csv_path).open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                size += len(block)
                digest.update(block)
    except OSError as exc:
        raise GSWDataError(f"cannot read pinned CSV: {exc}") from exc
    if size != expected_bytes:
        raise GSWDataError(f"CSV byte length mismatch: expected {expected_bytes}, got {size}")
    if digest.hexdigest() != expected_hash:
        raise GSWDataError("CSV SHA-256 mismatch")
    return dict(manifest)
