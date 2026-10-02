"""Check an exact Git export and installed wheel without network access.

Requires Git, the runtime dependencies and existing setuptools/wheel build
tools in the active Python environment. The temporary checkout and installed
core-library wheel stay in the ignored build directory. The exported fetcher
restores the tracked source archive with network access disabled, then the
saved release is independently verified. No dependency downloads occur.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_NAMES = frozenset(("release.json", "report.html", "risk_and_factors.png",
                            "wealth_2022.png", "wealth_2023.png", "ledger_2022.csv", "ledger_2023.csv"))


def run(command: list[str], directory: Path) -> str:
    result = subprocess.run(command, cwd=directory, check=True,
                            capture_output=True, text=True)
    return result.stdout.strip()


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--copy-local-source", action="store_true",
                        help="Compatibility check using existing local raw bytes instead of the tracked archive")
    args = parser.parse_args()
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    if not build.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("Build directory resolves outside the project workspace")
    commit = run(["git", "rev-parse", "HEAD"], ROOT)
    with tempfile.TemporaryDirectory(prefix="installation-check-", dir=build) as temporary:
        scratch = Path(temporary)
        if not scratch.resolve().is_relative_to(build.resolve()):
            raise ValueError("Temporary checkout resolves outside the build directory")
        archive = scratch / "checkout.zip"
        checkout = scratch / "checkout"
        run(["git", "archive", "--format=zip", f"--output={archive}", commit], ROOT)
        with zipfile.ZipFile(archive) as source:
            # Git supplies these names; enforce the extraction boundary anyway.
            for entry in source.infolist():
                if not (checkout / entry.filename).resolve().is_relative_to(checkout.resolve()):
                    raise ValueError("Git archive member escapes checkout")
            source.extractall(checkout)
        names = run(["git", "ls-tree", "-r", "--name-only", commit], ROOT).splitlines()
        for name in names:
            original = subprocess.run(["git", "show", f"{commit}:{name}"], cwd=ROOT,
                                      check=True, capture_output=True).stdout
            if (checkout / name).read_bytes() != original:
                raise ValueError(f"Git export changed file bytes: {name}")
        manifest = json.loads((checkout / "outputs/release/artifact_manifest.json").read_bytes())
        if (manifest.get("status") != "complete_research_release"
                or set(manifest.get("files", {})) != ARTIFACT_NAMES):
            raise ValueError("Git export lacks a complete seven-artifact release")
        for name, reference in manifest["files"].items():
            payload = (checkout / "outputs/release" / name).read_bytes()
            if len(payload) != reference["bytes"] or sha256(payload) != reference["sha256"]:
                raise ValueError(f"Exported artifact does not match manifest: {name}")
        wheels = scratch / "wheels"
        run([sys.executable, "-m", "pip", "wheel", ".", "--no-deps",
             "--no-build-isolation", "--no-cache-dir", "--wheel-dir", str(wheels)], checkout)
        wheel_files = list(wheels.glob("*.whl"))
        if len(wheel_files) != 1:
            raise ValueError("Expected exactly one project wheel")
        wheel = wheel_files[0]
        installed = scratch / "installed"
        run([sys.executable, "-m", "pip", "install", "--no-deps", "--no-cache-dir",
             "--target", str(installed), str(wheel)], scratch)
        smoke = """
import importlib.metadata
import json
import math
from pathlib import Path
import sys
target = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(target))
import treasury_risk
from treasury_risk.curve import SvenssonCurve
from treasury_risk.dated_bonds import DatedBond
from treasury_risk.accounting import CashLedger
from treasury_risk.optimization import optimize_covariance_hedge
from treasury_risk.benchmarks import treasury_price_from_yield
if not Path(treasury_risk.__file__).resolve().is_relative_to(target):
    raise ValueError('Smoke test imported checkout rather than installed wheel')
curve = SvenssonCurve(4.0, 0.0, 0.0, 0.0, 1.0, 2.0)
if not math.isclose(curve.discount(2.0), math.exp(-0.08), abs_tol=1e-14):
    raise ValueError('Installed curve calculation failed')
print(json.dumps({'version': importlib.metadata.version('treasury-curve-risk'),
                  'installed_model_import': 'passed', 'flat_curve_smoke': 'passed'}))
"""
        imported = json.loads(run([sys.executable, "-I", "-c", smoke, str(installed)], scratch))
        if args.copy_local_source:
            source_dir = checkout / "data/raw"
            source_dir.mkdir(parents=True, exist_ok=True)
            for name in ("feds200628.csv", "source_manifest.json"):
                source = ROOT / "data/raw" / name
                destination = source_dir / name
                shutil.copyfile(source, destination)
                if destination.read_bytes() != source.read_bytes():
                    raise ValueError(f"Local source copy changed bytes: {name}")
            source_status = "local snapshot copied byte-for-byte; exported release verified"
        else:
            pinned = checkout / "data/pinned"
            if not pinned.is_dir() or not any(pinned.iterdir()):
                raise ValueError("Git commit lacks the pinned archive; commit the source bundle before this offline check")
            restore = """
import runpy
import socket
import urllib.request
def block_network(*args, **kwargs):
    raise RuntimeError('Network access is disabled during the installation check')
urllib.request.urlopen = block_network
socket.create_connection = block_network
socket.socket.connect = block_network
runpy.run_path('scripts/fetch_curve_data.py', run_name='__main__')
"""
            run([sys.executable, "-I", "-c", restore], checkout)
            source_status = "tracked pinned archive restored with network disabled; exported release verified"
        validation = json.loads(run([sys.executable, "scripts/verify_release.py"], checkout))
        print(json.dumps({"status": "passed", "git_commit": commit,
                          "tracked_files_byte_verified": len(names),
                          "release_artifacts_hash_verified": len(manifest["files"]),
                          "wheel": wheel.name, "wheel_sha256": sha256(wheel.read_bytes()),
                          "installation": imported, "source": source_status,
                          "release_validation": validation}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        print(error.stdout, file=sys.stderr)
        print(error.stderr, file=sys.stderr)
        raise
