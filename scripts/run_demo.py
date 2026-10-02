"""Verify the archived Fed snapshot, then publish a reproducible research pack."""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from treasury_risk.analysis import build_analysis
from treasury_risk.data import load_gsw, validate_source_manifest
from treasury_risk.report import render_report

OUTPUT_NAMES = ("analysis.json", "curve_benchmark.csv", "key_rate_exposure.csv",
                "hedge_weights.csv", "scenario_comparison.csv", "historical_curve_replay.csv", "report.html")


def csv_text(rows: list[dict], fields: list[str]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def export_pack(pack: dict, output: Path) -> None:
    latest = pack["latest"]
    nodes = latest["key_rate_nodes"]
    bucket_rows = [{"maturity_years": node, "target_dv01": latest["target_key_rate_dv01"][i],
                    "duration_hedged_dv01": latest["duration_hedge"]["residual_exposure"][i],
                    "multi_hedged_dv01": latest["multi_hedge"]["residual_exposure"][i]}
                   for i, node in enumerate(nodes)]
    hedge_rows = [{"instrument": bond["label"], "maturity_years": bond["maturity_years"],
                   "duration_face": latest["duration_hedge"]["face_amounts"][i],
                   "multi_face": latest["multi_hedge"]["face_amounts"][i]}
                  for i, bond in enumerate(latest["hedge_instruments"])]
    contents = {
        "analysis.json": json.dumps(pack, indent=2, allow_nan=False),
        "curve_benchmark.csv": csv_text(pack["benchmark"]["date_checks"], ["date", "comparisons", "max_absolute_error_bps", "status"]),
        "key_rate_exposure.csv": csv_text(bucket_rows, ["maturity_years", "target_dv01", "duration_hedged_dv01", "multi_hedged_dv01"]),
        "hedge_weights.csv": csv_text(hedge_rows, ["instrument", "maturity_years", "duration_face", "multi_face"]),
        "scenario_comparison.csv": csv_text(latest["scenarios"], ["name", "unhedged_change", "duration_hedged_change", "multi_hedged_change"]),
        "historical_curve_replay.csv": csv_text(pack["historical"]["rows"], ["date", "unhedged_change", "duration_hedged_change", "multi_hedged_change"]),
        "report.html": render_report(pack),
    }
    output.mkdir(parents=True, exist_ok=True)
    # Prepare every serialization before publishing any artifact.
    for name, content in contents.items():
        temporary = output / f".{name}.tmp"
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(output / name)


def run(data: Path, manifest: Path, output: Path, historical_year: int = 2022) -> dict:
    try:
        source = validate_source_manifest(data, manifest)
        records, quality = load_gsw(data, start_date="2000-01-01")
        pack = build_analysis(records, source, quality, historical_year)
        export_pack(pack, output)
        return pack
    except Exception as error:
        # Only remove known generated outputs, never the user's source data.
        output.mkdir(parents=True, exist_ok=True)
        for name in OUTPUT_NAMES:
            path = output / name
            if path.is_file():
                path.unlink()
        (output / "analysis.json").write_text(json.dumps({"status": "failed", "error": str(error)}), encoding="utf-8")
        import html
        (output / "report.html").write_text(
            "<!doctype html><meta charset='utf-8'><title>Research run failed</title>"
            "<h1>Research run failed</h1><p>No validated research outputs are available.</p><pre>"
            + html.escape(str(error)) + "</pre>", encoding="utf-8")
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data/raw/feds200628.csv")
    parser.add_argument("--manifest", type=Path, default=ROOT / "data/raw/source_manifest.json")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs")
    parser.add_argument("--historical-year", type=int, default=2022)
    args = parser.parse_args()
    pack = run(args.data, args.manifest, args.output, args.historical_year)
    latest = pack["latest"]
    print(json.dumps({"status": pack["status"], "latest_curve": latest["date"],
                      "source_observations": len(pack["benchmark"]["date_checks"]),
                      "benchmark_comparisons": pack["benchmark"]["comparisons"],
                      "maximum_benchmark_error_bps": pack["benchmark"]["max_absolute_error_bps"],
                      "quarantined_source_observations": pack["benchmark"]["quarantined_observations"],
                      "target_pv": latest["target_price"],
                      "multi_hedge_residual_ratio": latest["multi_hedge"]["residual_ratio"],
                      "report": str(args.output / "report.html")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
