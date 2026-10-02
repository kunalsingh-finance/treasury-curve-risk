"""Archive the already verified local GSW snapshot for exact offline restoration."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from treasury_risk.data import validate_source_manifest


def pin(root: Path = ROOT, *, replace: bool = False) -> dict:
    raw = root / "data/raw"
    source = validate_source_manifest(raw / "feds200628.csv", raw / "source_manifest.json")
    payload = (raw / "feds200628.csv").read_bytes()
    metadata = (raw / "source_manifest.json").read_bytes()
    destination = root / "data/pinned"
    destination.mkdir(parents=True, exist_ok=True)
    paths = [destination / name for name in ("feds200628.csv.gz", "source_manifest.json", "archive_manifest.json")]
    if any(path.exists() for path in paths) and not replace:
        raise ValueError("Pinned archive already exists; --replace is required to publish a different snapshot")
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", filename="", mtime=0, compresslevel=9) as compressed:
        compressed.write(payload)
    archive = buffer.getvalue()
    manifest = {"schema_version": 1, "archive_file": "feds200628.csv.gz",
                "archive_bytes": len(archive), "archive_sha256": hashlib.sha256(archive).hexdigest(),
                "uncompressed_bytes": len(payload), "uncompressed_sha256": source["sha256"],
                "source_manifest_sha256": hashlib.sha256(metadata).hexdigest()}
    paths[0].write_bytes(archive)
    paths[1].write_bytes(metadata)
    paths[2].write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replace", action="store_true", help="Explicitly replace the immutable checked-in snapshot; rebuild release evidence afterward")
    print(json.dumps(pin(replace=parser.parse_args().replace), indent=2))
