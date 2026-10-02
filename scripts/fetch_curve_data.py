"""Archive the public Federal Reserve GSW nominal yield-curve CSV."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://www.federalreserve.gov/data/yield-curve-tables/feds200628.csv"
SOURCE_PAGE = "https://www.federalreserve.gov/data/nominal-yield-curve.htm"
MAX_BYTES = 64 * 1024 * 1024


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Replace the existing local source snapshot")
    args = parser.parse_args()
    destination = ROOT / "data" / "raw"
    destination.mkdir(parents=True, exist_ok=True)
    csv_path = destination / "feds200628.csv"
    manifest_path = destination / "source_manifest.json"
    if csv_path.exists() and not args.refresh:
        print(f"Existing snapshot retained: {csv_path}")
        return
    request = Request(SOURCE_URL, headers={"User-Agent": "TreasuryCurvePortfolioResearch/0.1", "Accept": "text/csv,text/plain,*/*"})
    with urlopen(request, timeout=45) as response:
        payload = response.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES:
            raise ValueError("Official CSV exceeds the 64 MiB acquisition limit")
        response_headers = {name: response.headers[name] for name in
                            ("Date", "Content-Type", "Content-Length", "ETag", "Last-Modified")
                            if response.headers.get(name) is not None}
    if b"Date" not in payload or b"BETA0" not in payload or b"SVENY10" not in payload:
        raise ValueError("Response is missing expected GSW CSV headers")
    captured_at = datetime.now(timezone.utc).isoformat()
    sha256 = hashlib.sha256(payload).hexdigest()
    csv_path.write_bytes(payload)
    manifest_path.write_text(json.dumps({"source_url": SOURCE_URL, "source_page": SOURCE_PAGE,
                                        "captured_at": captured_at, "sha256": sha256, "bytes": len(payload),
                                        "response_headers": response_headers,
                                        "data_vintage": "Downloaded current vintage; historical observations may be revised.",
                                        "model_type": "Federal Reserve staff fitted off-the-run nominal Treasury curve"}, indent=2) + "\n", encoding="utf-8")
    print(f"Archived {len(payload):,} bytes; SHA-256 {sha256}")
    print(csv_path)


if __name__ == "__main__":
    main()
