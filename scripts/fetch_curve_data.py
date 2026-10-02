"""Restore the verified pinned Fed snapshot offline; refresh only on request."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
from uuid import uuid4
from urllib.request import Request, urlopen
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from treasury_risk.data import validate_source_manifest

SOURCE_URL = "https://www.federalreserve.gov/data/yield-curve-tables/feds200628.csv"
SOURCE_PAGE = "https://www.federalreserve.gov/data/nominal-yield-curve.htm"
MAX_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
CSV_NAME = "feds200628.csv"
MANIFEST_NAME = "source_manifest.json"
ARCHIVE_NAME = CSV_NAME + ".gz"


def _bounded_read(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise ValueError(f"{path.name} exceeds its acquisition size limit")
    return payload


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _metadata(payload: bytes, label: str) -> dict:
    try:
        value = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid {label}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _size(value, label: str, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 < value <= maximum:
        raise ValueError(f"{label} must be a positive bounded byte count")
    return value


def _hash(value, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _validate_payload(payload: bytes, manifest_bytes: bytes) -> dict:
    manifest = _metadata(manifest_bytes, "source manifest")
    if manifest.get("source_url") != SOURCE_URL or manifest.get("source_page") != SOURCE_PAGE:
        raise ValueError("Source manifest does not identify the official Fed URLs")
    size = _size(manifest.get("bytes"), "Source bytes", MAX_BYTES)
    digest = _hash(manifest.get("sha256"), "Source hash")
    if len(payload) != size or _digest(payload) != digest:
        raise ValueError("Source payload size or SHA-256 differs from its manifest")
    if b"Date" not in payload or b"BETA0" not in payload or b"SVENY10" not in payload:
        raise ValueError("Source response is missing expected GSW CSV headers")
    return manifest


def _pinned_payload(root: Path) -> tuple[bytes, bytes]:
    pinned = root / "data" / "pinned"
    archive_manifest = _metadata(_bounded_read(pinned / "archive_manifest.json", MAX_MANIFEST_BYTES),
                                 "archive manifest")
    if (type(archive_manifest.get("schema_version")) is not int
            or archive_manifest["schema_version"] != 1
            or archive_manifest.get("archive_file") != ARCHIVE_NAME):
        raise ValueError("Unsupported pinned archive schema or file name")
    archive_size = _size(archive_manifest.get("archive_bytes"), "Archive bytes", MAX_BYTES)
    archive_hash = _hash(archive_manifest.get("archive_sha256"), "Archive hash")
    source_size = _size(archive_manifest.get("uncompressed_bytes"), "Uncompressed bytes", MAX_BYTES)
    source_hash = _hash(archive_manifest.get("uncompressed_sha256"), "Uncompressed hash")
    manifest_hash = _hash(archive_manifest.get("source_manifest_sha256"), "Manifest hash")
    archive = _bounded_read(pinned / ARCHIVE_NAME, MAX_BYTES)
    manifest_bytes = _bounded_read(pinned / MANIFEST_NAME, MAX_MANIFEST_BYTES)
    if len(archive) != archive_size or _digest(archive) != archive_hash:
        raise ValueError("Pinned archive size or SHA-256 mismatch")
    if _digest(manifest_bytes) != manifest_hash:
        raise ValueError("Pinned source-manifest SHA-256 mismatch")
    source_manifest = _metadata(manifest_bytes, "source manifest")
    if source_manifest.get("bytes") != source_size or source_manifest.get("sha256") != source_hash:
        raise ValueError("Archive and source manifests disagree")
    # Limit expanded bytes too: a small compressed file must not bypass MAX_BYTES.
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(archive), mode="rb") as stream:
            payload = stream.read(MAX_BYTES + 1)
    except (OSError, EOFError, zlib.error) as error:
        raise ValueError("Pinned archive is not a complete valid gzip stream") from error
    if len(payload) > MAX_BYTES:
        raise ValueError("Expanded pinned archive exceeds the 64 MiB acquisition limit")
    _validate_payload(payload, manifest_bytes)
    return payload, manifest_bytes


def _refreshed_payload() -> tuple[bytes, bytes]:
    request = Request(SOURCE_URL, headers={"User-Agent": "TreasuryCurvePortfolioResearch/1.0",
                                          "Accept": "text/csv,text/plain,*/*"})
    with urlopen(request, timeout=45) as response:
        payload = response.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES:
            raise ValueError("Official CSV exceeds the 64 MiB acquisition limit")
        headers = {name: response.headers[name] for name in
                   ("Date", "Content-Type", "Content-Length", "ETag", "Last-Modified")
                   if response.headers.get(name) is not None}
    manifest_bytes = (json.dumps({"source_url": SOURCE_URL, "source_page": SOURCE_PAGE,
        "captured_at": datetime.now(timezone.utc).isoformat(), "sha256": _digest(payload),
        "bytes": len(payload), "response_headers": headers,
        "data_vintage": "Downloaded current vintage; historical observations may be revised.",
        "model_type": "Federal Reserve staff fitted off-the-run nominal Treasury curve"}, indent=2)
        + "\n").encode("utf-8")
    _validate_payload(payload, manifest_bytes)
    return payload, manifest_bytes


def _safe_child(directory: Path, name: str) -> Path:
    path = directory / name
    if path.resolve().parent != directory.resolve():
        raise ValueError("Source path escapes the intended directory")
    return path


def _publish(root: Path, payload: bytes, manifest_bytes: bytes) -> dict:
    # Validate in memory before creating the working data directory.
    _validate_payload(payload, manifest_bytes)
    destination = root / "data" / "raw"
    if not destination.resolve().is_relative_to(root.resolve()):
        raise ValueError("Working source directory escapes the project")
    destination.mkdir(parents=True, exist_ok=True)
    csv_path = _safe_child(destination, CSV_NAME)
    manifest_path = _safe_child(destination, MANIFEST_NAME)
    stage = _safe_child(destination, ".source-" + uuid4().hex)
    stage.mkdir()
    try:
        staged_csv = _safe_child(stage, CSV_NAME)
        staged_manifest = _safe_child(stage, MANIFEST_NAME)
        staged_csv.write_bytes(payload)
        staged_manifest.write_bytes(manifest_bytes)
        manifest = validate_source_manifest(staged_csv, staged_manifest)
        staged_csv.replace(csv_path)
        # Publish the manifest last. An interrupted replacement leaves a
        # mismatched pair that subsequent validation rejects.
        staged_manifest.replace(manifest_path)
        return manifest
    finally:
        for name in (CSV_NAME, MANIFEST_NAME):
            path = _safe_child(stage, name)
            if path.exists():
                path.unlink()
        stage.rmdir()


def acquire(root: Path = ROOT, *, refresh: bool = False) -> dict:
    """Acquire under an explicit root; the default path never uses the web."""
    root = Path(root)
    destination = root / "data" / "raw"
    if not destination.resolve().is_relative_to(root.resolve()):
        raise ValueError("Working source directory escapes the project")
    csv_path = _safe_child(destination, CSV_NAME)
    manifest_path = _safe_child(destination, MANIFEST_NAME)
    if not refresh and (csv_path.exists() or manifest_path.exists()):
        if not csv_path.is_file() or not manifest_path.is_file():
            raise ValueError("Existing snapshot is incomplete; repair it or explicitly use --refresh")
        _size(csv_path.stat().st_size, "Retained CSV bytes", MAX_BYTES)
        if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError("Retained manifest exceeds its acquisition size limit")
        manifest = validate_source_manifest(csv_path, manifest_path)
        if manifest.get("source_page") != SOURCE_PAGE:
            raise ValueError("Retained source manifest lacks the official source page")
        _size(manifest["bytes"], "Retained source bytes", MAX_BYTES)
        return {"status": "retained", "path": str(csv_path), "source": manifest}
    payload, manifest_bytes = _refreshed_payload() if refresh else _pinned_payload(root)
    manifest = _publish(root, payload, manifest_bytes)
    return {"status": "refreshed" if refresh else "restored", "path": str(csv_path), "source": manifest}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true",
                        help="Explicitly download the mutable current Fed vintage instead of restoring the pinned release")
    args = parser.parse_args()
    result = acquire(refresh=args.refresh)
    print(f"Source {result['status']}: {result['source']['bytes']:,} bytes; SHA-256 {result['source']['sha256']}")
    print(result["path"])


if __name__ == "__main__":
    main()
