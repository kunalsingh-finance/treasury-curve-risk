"""Real publication-lock regressions using temporary synthetic release bytes."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import subprocess
import sys
from threading import Event
import unittest
from unittest.mock import patch

from scripts import build_release
from scripts.publication_lock import publication_lock


class PublicationConcurrencyTests(unittest.TestCase):
    def test_process_exit_releases_persistent_lock_file(self):
        with tempfile.TemporaryDirectory(prefix="treasury-process-lock-") as directory:
            code = ("import os,sys; from pathlib import Path; "
                    "from scripts.publication_lock import publication_lock; "
                    "guard=publication_lock(Path(sys.argv[1])); guard.__enter__(); os._exit(0)")
            result = subprocess.run([sys.executable, "-c", code, directory],
                                    cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            self.assertTrue((Path(directory) / ".publication.lock").exists())
            with publication_lock(Path(directory)):
                pass

    def test_competing_rebuild_preserves_every_existing_byte(self):
        with tempfile.TemporaryDirectory(prefix="treasury-publication-lock-") as directory:
            output = Path(directory)
            original = {name: ("Synthetic existing " + name).encode()
                        for name in (*build_release.ARTIFACT_NAMES, "artifact_manifest.json", "reader_notes.txt")}
            for name, payload in original.items():
                (output / name).write_bytes(payload)
            with publication_lock(output):
                with self.assertRaisesRegex(RuntimeError, "Another publisher"):
                    build_release.build(output)
            self.assertEqual({name: (output / name).read_bytes() for name in original}, original)
            self.assertEqual(list(output.glob(".staging-*")), [])

    def test_failed_earlier_rebuild_cannot_overlap_a_later_publisher(self):
        paused, resume = Event(), Event()

        def failing_source(*unused):
            paused.set()
            if not resume.wait(10):
                raise AssertionError("Test did not release the paused writer")
            raise ValueError("Synthetic earlier rebuild failure")

        with tempfile.TemporaryDirectory(prefix="treasury-overlapping-rebuild-") as directory:
            output = Path(directory)
            notes = output / "reader_notes.txt"
            notes.write_text("Reader notes survive publication failures")
            with patch.object(build_release, "validate_source_manifest", side_effect=failing_source), \
                 ThreadPoolExecutor(max_workers=1) as executor:
                first = executor.submit(build_release.build, output)
                try:
                    self.assertTrue(paused.wait(10))
                    in_progress = {name: (output / name).read_bytes()
                                   for name in ("artifact_manifest.json", "release.json", "report.html")}
                    with self.assertRaisesRegex(RuntimeError, "Another publisher"):
                        build_release.build(output)
                    self.assertEqual({name: (output / name).read_bytes() for name in in_progress}, in_progress)
                finally:
                    resume.set()
                with self.assertRaisesRegex(ValueError, "Synthetic earlier rebuild failure"):
                    first.result(timeout=10)
            self.assertEqual(json.loads((output / "artifact_manifest.json").read_text())["status"], "failed")
            self.assertEqual(notes.read_text(), "Reader notes survive publication failures")
            self.assertEqual(list(output.glob(".staging-*")), [])
            # The failed writer releases its kernel lock in all exception paths.
            with publication_lock(output):
                pass

    def test_success_releases_lock_and_reads_version_from_project(self):
        # Financial checks are separate. This fixture exercises version wiring,
        # staging/publication and lock release without actual Treasury claims.
        latest = {"methods": {"constrained": {"weights": [0.0]}}}
        config = {era: {group: [{"cusip": "SYNTHETIC"}]
                        for group in ("targets", "hedges")} for era in ("latest", "historical")}
        periods = [{"start_date": f"{year}-01-03", "rows": [{"date": f"{year}-01-03", "cash": 0.0}]}
                   for year in (2022, 2023)]

        def synthetic_charts(unused, stage):
            names = ["risk_and_factors.png", "wealth_2022.png", "wealth_2023.png"]
            for name in names:
                (stage / name).write_bytes(b"Synthetic chart fixture")
            return names

        with tempfile.TemporaryDirectory(prefix="treasury-build-version-") as directory:
            root = Path(directory)
            (root / "configs").mkdir()
            (root / "configs/treasury_instruments.json").write_text(json.dumps(config))
            (root / "pyproject.toml").write_text('[project]\nversion = "9.8.7"\n')
            output = root / "release"
            with ExitStack() as stack:
                mocks = {"ROOT": root, "validate_source_manifest": {"sha256": "synthetic"},
                         "load_gsw": ([], {}), "benchmark_curves": {"exceptions": []}, "load_instrument_config": config,
                         "official_auction_benchmarks": [{"passed": True}], "latest_analysis": latest,
                         "code_fingerprint": "synthetic", "verify_pack": {}, "verify": {},
                         "render_release": "<h1>Synthetic report fixture</h1>"}
                for name, value in mocks.items():
                    stack.enter_context(patch.object(build_release, name, value) if name == "ROOT"
                                        else patch.object(build_release, name, return_value=value))
                stack.enter_context(patch.object(build_release, "simulate_period", side_effect=periods))
                stack.enter_context(patch.object(build_release, "charts", side_effect=synthetic_charts))
                pack = build_release.build(output)
            self.assertEqual(pack["version"], "9.8.7")
            self.assertIn("0 older source curves remain quarantined", pack["limitations"][-1])
            self.assertEqual(json.loads((output / "artifact_manifest.json").read_text())["version"], "9.8.7")
            self.assertEqual(list(output.glob(".staging-*")), [])
            with publication_lock(output):
                pass


if __name__ == "__main__":
    unittest.main()
