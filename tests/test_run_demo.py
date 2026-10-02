"""Publication checks: failed source verification must invalidate stale evidence."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.run_demo import OUTPUT_NAMES, export_pack, run
from treasury_risk.analysis import build_analysis
from test_analysis import record


class PublicationTests(unittest.TestCase):
    def test_failed_verification_invalidates_known_outputs_and_preserves_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            for name in OUTPUT_NAMES:
                (output / name).write_text("old validated-looking results", encoding="utf-8")
            notes = output / "my_notes.txt"
            notes.write_text("retain user notes", encoding="utf-8")
            with patch("scripts.run_demo.validate_source_manifest", side_effect=ValueError("hash mismatch <unsafe>")):
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    run(output / "input.csv", output / "manifest.json", output)
            self.assertEqual(json.loads((output / "analysis.json").read_text())["status"], "failed")
            html = (output / "report.html").read_text(encoding="utf-8")
            self.assertIn("Research run failed", html)
            self.assertIn("&lt;unsafe&gt;", html)
            self.assertNotIn("old validated", html)
            self.assertFalse((output / "scenario_comparison.csv").exists())
            self.assertEqual(notes.read_text(), "retain user notes")

    def test_successful_export_is_parseable_and_records_exact_shocks_and_units(self):
        items = [record("2022-01-03", 4), record("2022-12-30", 5)]
        pack = build_analysis(items, {"source_url": "fixture"}, {"valid": 2})
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            export_pack(pack, output)
            saved = json.loads((output / "analysis.json").read_text())
            self.assertEqual(saved["status"], "validated_research_demo")
            shock = saved["latest"]["scenarios"][2]["shock_definition"]
            self.assertEqual(shock["key_rate_bumps_bps"]["30.0"], 140)
            self.assertEqual(len(list(output.glob("*.csv"))), 5)
            self.assertIn("multi_hedged_dv01", (output / "key_rate_exposure.csv").read_text())
            self.assertIn("Maturities do not age", (output / "report.html").read_text(encoding="utf-8"))

    def test_missing_old_benchmark_cannot_display_a_source_pass_badge(self):
        old = record("2008-03-21")
        old["published_zero_yields"] = {}
        pack = build_analysis([old, record("2022-01-03"), record("2022-12-30")], {}, {})
        with tempfile.TemporaryDirectory() as directory:
            export_pack(pack, Path(directory))
            html = (Path(directory) / "report.html").read_text(encoding="utf-8")
            self.assertIn("Benchmark review required", html)
            self.assertNotIn("Yield benchmarks pass", html)
            self.assertIn("2008-03-21", html)
            self.assertIn("benchmark unavailable", html)


if __name__ == "__main__":
    unittest.main()
