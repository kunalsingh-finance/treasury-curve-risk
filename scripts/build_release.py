"""Build the complete, fail-closed Treasury research release from the pinned source."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import sys
import tomllib
from uuid import uuid4
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from treasury_risk.analysis import benchmark_curves
from treasury_risk.benchmarks import official_auction_benchmarks
from treasury_risk.data import load_gsw, validate_source_manifest
from treasury_risk.research import code_fingerprint, latest_analysis, load_instrument_config, simulate_period
from treasury_risk.release_report import charts, render_release
from scripts.verify_release import ARTIFACT_NAMES, verify_pack, verify
from scripts.publication_lock import publication_lock


def clear_generated(output: Path) -> None:
    """Remove only the named generated artifacts; preserve unrelated reader files."""
    for name in ARTIFACT_NAMES:
        path = output / name
        if path.resolve().parent != output.resolve():
            raise ValueError("Generated artifact escapes the release directory")
        if path.exists():
            path.unlink()


def build(output: Path = ROOT / "outputs/release") -> dict:
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    # Acquire before deleting results or writing a status marker. A refused
    # concurrent writer must leave the current generation untouched.
    with publication_lock(output):
        return _build_locked(output)


def _build_locked(output: Path) -> dict:
    clear_generated(output)
    (output / "artifact_manifest.json").write_text(json.dumps({"status": "building"}), encoding="utf-8")
    (output / "release.json").write_text(json.dumps({"status": "building"}), encoding="utf-8")
    (output / "report.html").write_text("<!doctype html><h1>Release is being rebuilt</h1><p>Prior results are unavailable until all checks pass.</p>", encoding="utf-8")
    stage = output / (".staging-" + uuid4().hex)
    if stage.resolve().parent != output.resolve():
        raise ValueError("Staging directory escapes release directory")
    stage.mkdir()
    try:
        source = validate_source_manifest(ROOT / "data/raw/feds200628.csv", ROOT / "data/raw/source_manifest.json")
        records, quality = load_gsw(ROOT / "data/raw/feds200628.csv")
        audit = benchmark_curves(records, strict=False)
        config = load_instrument_config(ROOT / "configs/treasury_instruments.json")
        all_instruments = [item for era in ("latest", "historical") for group in ("targets", "hedges") for item in config[era][group]]
        auction_checks = official_auction_benchmarks(all_instruments)
        if not all(item["passed"] for item in auction_checks):
            raise ValueError("Independent official Treasury auction-price verification failed")
        latest = latest_analysis(records, config, audit)
        if latest["methods"]["constrained"].get("weights") is None:
            raise ValueError("Required latest constrained hedge failed")
        periods = [simulate_period(records, config, audit, f"{year}-01-01", f"{year}-12-31") for year in (2022, 2023)]
        with (ROOT / "pyproject.toml").open("rb") as project_stream:
            version = tomllib.load(project_stream)["project"]["version"]
        pack = {"version": version, "schema_version": 1, "status": "complete_research_release",
                "code_sha256": code_fingerprint(ROOT),
                "instrument_config_sha256": hashlib.sha256((ROOT / "configs/treasury_instruments.json").read_bytes()).hexdigest(),
                "generated_at": datetime.now(timezone.utc).isoformat(), "source": source,
                "data_quality": quality, "source_audit": audit, "instrument_config": config,
                "auction_benchmarks": auction_checks, "latest": latest, "periods": periods,
                "limitations": [
                    "Actual Treasury security terms and dates are sourced from official auction releases; face allocations are hypothetical.",
                    "Fitted off-the-run Treasury curve marks are not executable prices, bid/ask quotes or independent current CUSIP market-price checks.",
                    "GSW historical observations are the current revised download, not historical release vintages.",
                    "Monthly decisions use previous available curves and strictly earlier covariance levels; current-date curves only mark trades and holdings.",
                    "Execution at fitted dirty PV is assumed. Transaction cost is 1 bp of changed dirty notional; no actual market fills are represented.",
                    "Positive cash and restricted collateral earn an assumed nominal 2% annual cash/rebate rate; debit cash is charged 5%, compounded daily on ACT/365F. These fixed assumptions are not observed repo rates or secured-lending offers.",
                    "Full short market value plus 2% is reserved as collateral; starting capital buffer is 10% of target face. Collateral/funding breaches are reported, not covered by silent external cash.",
                    "Targets are held to maturity without replenishment. Each annual evaluation restarts with equal initial equity across hedge methods.",
                    "No overnight-indexed swaps, futures CTD/convexity adjustment, optionality or derivatives margin model is included in this cash-Treasury release.",
                    f"{len(audit['exceptions'])} older source curves remain quarantined. Required evaluation dates and independent auction conventions must pass before publication.",
                ]}
        encoded = json.dumps(pack, indent=2, allow_nan=False)
        verify_pack(pack)
        chart_files = charts(pack, stage)
        report = render_release(pack, chart_files)
        for period in periods:
            with (stage / f"ledger_{period['start_date'][:4]}.csv").open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(period["rows"][0]))
                writer.writeheader()
                writer.writerows(period["rows"])
        (stage / "release.json").write_text(encoded, encoding="utf-8")
        (stage / "report.html").write_text(report, encoding="utf-8")
        artifact_names = ["release.json", "report.html", *chart_files, "ledger_2022.csv", "ledger_2023.csv"]
        manifest = {"version": pack["version"], "status": pack["status"], "files": {
            name: {"sha256": hashlib.sha256((stage / name).read_bytes()).hexdigest(), "bytes": (stage / name).stat().st_size}
            for name in artifact_names}}
        (stage / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        verify(stage)
        for name in sorted(ARTIFACT_NAMES):
            (stage / name).replace(output / name)
        # A complete manifest is the final publication marker.
        (stage / "artifact_manifest.json").replace(output / "artifact_manifest.json")
        return pack
    except Exception as error:
        clear_generated(output)
        (output / "release.json").write_text(json.dumps({"status": "failed", "error": str(error)}), encoding="utf-8")
        import html
        (output / "report.html").write_text("<!doctype html><h1>Release failed validation</h1><pre>" + html.escape(str(error)) + "</pre>", encoding="utf-8")
        (output / "artifact_manifest.json").write_text(json.dumps({"status": "failed", "error": str(error)}), encoding="utf-8")
        raise
    finally:
        for name in (*ARTIFACT_NAMES, "artifact_manifest.json"):
            path = stage / name
            if path.exists():
                path.unlink()
        stage.rmdir()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/release")
    args = parser.parse_args()
    pack = build(args.output)
    print(json.dumps({"status": pack["status"], "version": pack["version"], "auction_price_checks": len(pack["auction_benchmarks"]),
                      "latest_hedge_status": pack["latest"]["methods"]["constrained"]["status"],
                      "historical_observations": sum(period["observations"] for period in pack["periods"]),
                      "report": str(args.output / "report.html")}, indent=2))
