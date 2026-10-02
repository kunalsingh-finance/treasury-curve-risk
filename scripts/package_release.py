"""Bundle the verified report and its matching frozen workbook for review."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.verify_release import ARTIFACT_NAMES, load_verified_release

WORKBOOK_FOLDER = Path("outputs/01a0fad9-107c-7cd2-b374-8b14defca53a")


def package(root: Path = ROOT) -> Path:
    pack, checks = load_verified_release(root / "outputs/release")
    release_bytes = (root / "outputs/release/release.json").read_bytes()
    verification = (root / WORKBOOK_FOLDER / "workbook_verification.json").read_bytes()
    workbook_checks = json.loads(verification)
    if workbook_checks["releaseSha256"] != hashlib.sha256(release_bytes).hexdigest():
        raise ValueError("Frozen workbook belongs to a different saved release; export a matching workbook before packaging")
    artifacts = {name: (root / "outputs/release" / name).read_bytes() for name in ARTIFACT_NAMES}
    artifact_manifest_bytes = (root / "outputs/release/artifact_manifest.json").read_bytes()
    artifact_manifest = json.loads(artifact_manifest_bytes)
    if artifact_manifest.get("status") != "complete_research_release" or set(artifact_manifest.get("files", {})) != ARTIFACT_NAMES:
        raise ValueError("Review package requires the complete verified artifact manifest")
    for name, payload in artifacts.items():
        reference = artifact_manifest["files"][name]
        if len(payload) != reference["bytes"] or hashlib.sha256(payload).hexdigest() != reference["sha256"]:
            raise ValueError(f"Artifact changed after release validation: {name}")
    if artifacts["release.json"] != release_bytes:
        raise ValueError("Release changed while constructing the review package")
    files = {"outputs/release/" + name: payload for name, payload in artifacts.items()}
    files["outputs/release/artifact_manifest.json"] = artifact_manifest_bytes
    files["treasury_risk_pack.xlsx"] = (root / WORKBOOK_FOLDER / "treasury_risk_pack.xlsx").read_bytes()
    if workbook_checks["workbookSha256"] != hashlib.sha256(files["treasury_risk_pack.xlsx"]).hexdigest():
        raise ValueError("Workbook bytes differ from the verified final export")
    files["workbook_verification.json"] = verification
    for name in ("DECISION_MEMO.md", "METHODOLOGY.md", "CONVENTIONS.md", "VALIDATION.md", "RELEASE_NOTES.md", "RESEARCH_NOTE.md"):
        files["docs/" + name] = (root / "docs" / name).read_bytes()
    for name in ("configs/treasury_instruments.json", "treasury_risk/benchmarks.py"):
        files[name] = (root / name).read_bytes()
    files["START_HERE.txt"] = (
        "Treasury Curve & Hedge Engine v1.0.0\n\n"
        "Open outputs/release/report.html in a browser and treasury_risk_pack.xlsx in a spreadsheet editor.\n"
        "Keep the report's three PNG files beside it. Read docs/DECISION_MEMO.md for the research interpretation.\n"
        "JSON and CSV files retain the pricing, source checks and full accounting evidence.\n"
        "Excel inputs change frozen valuation/risk formulas; optimization and historical replay run in Python.\n"
        "Holdings, financing and fitted trade prices are hypothetical research assumptions.\n"
    ).encode()
    bundle_manifest = {"version": pack["version"], "verification": checks, "files": {
        name: {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
        for name, payload in sorted(files.items())}}
    files["review_manifest.json"] = (json.dumps(bundle_manifest, indent=2) + "\n").encode()
    dist = root / "dist"
    dist.mkdir(exist_ok=True)
    if not dist.resolve().is_relative_to(root.resolve()):
        raise ValueError("Package destination escapes the project directory")
    output = dist / f"treasury_curve_risk_v{pack['version']}_review.zip"
    temporary = output.with_suffix(".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            for name, payload in sorted(files.items()):
                entry = zipfile.ZipInfo(name, date_time=(2026, 10, 2, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = 0o644 << 16
                bundle.writestr(entry, payload)
        with zipfile.ZipFile(temporary) as bundle:
            if set(bundle.namelist()) != set(files) or any(bundle.read(name) != payload for name, payload in files.items()):
                raise ValueError("Packaged artifact bytes failed independent readback")
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output


if __name__ == "__main__":
    result = package()
    print(json.dumps({"status": "passed", "review_package": str(result),
                      "bytes": result.stat().st_size,
                      "sha256": hashlib.sha256(result.read_bytes()).hexdigest()}, indent=2))
