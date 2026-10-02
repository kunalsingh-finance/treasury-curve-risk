"""Offline review-bundle provenance and readback checks using synthetic bytes."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import package_release


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


class ReviewPackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="treasury-package-test-")
        self.root = Path(self.temporary.name).resolve()
        if not self.root.is_relative_to(Path(tempfile.gettempdir()).resolve()):
            raise ValueError("Test directory escapes the explicit temporary directory")
        self.release = self.root / "outputs/release"
        self.release.mkdir(parents=True)
        self.pack = {"version": "1.0.0", "status": "complete_research_release"}
        self.checks = {"status": "passed", "artifacts_verified": 7}
        self.payloads = {}
        for name in package_release.ARTIFACT_NAMES:
            payload = (json.dumps(self.pack) + "\n").encode() if name == "release.json" else f"SYNTHETIC {name}\n".encode()
            self.payloads[name] = payload
            (self.release / name).write_bytes(payload)
        self.write_artifact_manifest()
        self.workbook = self.root / package_release.WORKBOOK_FOLDER
        self.workbook.mkdir(parents=True)
        # Bundle integrity depends on byte identity, not spreadsheet rendering.
        # Formula correctness and real XLSX rendering are validated separately.
        self.workbook_bytes = b"SYNTHETIC frozen workbook fixture\n"
        (self.workbook / "treasury_risk_pack.xlsx").write_bytes(self.workbook_bytes)
        self.workbook_checks = {"releaseSha256": digest(self.payloads["release.json"]),
                                "workbookSha256": digest(self.workbook_bytes)}
        self.write_workbook_checks()
        self.docs = ("DECISION_MEMO.md", "METHODOLOGY.md", "CONVENTIONS.md", "VALIDATION.md", "RELEASE_NOTES.md", "RESEARCH_NOTE.md")
        (self.root / "docs").mkdir()
        for name in self.docs:
            (self.root / "docs" / name).write_bytes(f"# Synthetic {name}\n".encode())
        (self.root / "configs").mkdir()
        (self.root / "configs/treasury_instruments.json").write_bytes(b'{"fixture": true}\n')
        (self.root / "treasury_risk").mkdir()
        (self.root / "treasury_risk/benchmarks.py").write_bytes(b'"""Synthetic reference source."""\n')
        self.dist = self.root / "dist"
        self.dist.mkdir()
        self.note = self.dist / "reader-notes.txt"
        self.note.write_bytes(b"Unrelated reader notes must survive packaging failures.\n")

    def tearDown(self):
        self.temporary.cleanup()

    def write_artifact_manifest(self):
        manifest = {"status": "complete_research_release", "files": {
            name: {"bytes": len(payload), "sha256": digest(payload)}
            for name, payload in self.payloads.items()}}
        (self.release / "artifact_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def write_workbook_checks(self):
        (self.workbook / "workbook_verification.json").write_text(json.dumps(self.workbook_checks), encoding="utf-8")

    def build(self):
        with patch.object(package_release, "load_verified_release", return_value=(self.pack, self.checks)):
            return package_release.package(self.root)

    def assert_no_partial_package(self):
        self.assertEqual(list(self.dist.glob("*.tmp")), [])
        self.assertEqual(list(self.dist.glob("*.zip")), [])
        self.assertEqual(self.note.read_bytes(), b"Unrelated reader notes must survive packaging failures.\n")

    def test_valid_bundle_has_complete_members_and_readback_hashes(self):
        output = self.build()
        expected = {"outputs/release/" + name for name in package_release.ARTIFACT_NAMES} | {
            "outputs/release/artifact_manifest.json", "treasury_risk_pack.xlsx", "workbook_verification.json",
            "configs/treasury_instruments.json", "treasury_risk/benchmarks.py",
            "START_HERE.txt", "review_manifest.json", *("docs/" + name for name in self.docs)}
        with zipfile.ZipFile(output) as bundle:
            self.assertIsNone(bundle.testzip())
            self.assertEqual(set(bundle.namelist()), expected)
            self.assertEqual(len(bundle.namelist()), len(expected))
            for name in expected:
                self.assertFalse(Path(name).is_absolute())
                self.assertNotIn("..", Path(name).parts)
            review = json.loads(bundle.read("review_manifest.json"))
            self.assertEqual(review["version"], self.pack["version"])
            self.assertEqual(review["verification"], self.checks)
            self.assertEqual(set(review["files"]), expected - {"review_manifest.json"})
            for name, reference in review["files"].items():
                payload = bundle.read(name)
                self.assertEqual(reference["bytes"], len(payload), name)
                self.assertEqual(reference["sha256"], digest(payload), name)
            for name, original in self.payloads.items():
                self.assertEqual(bundle.read("outputs/release/" + name), original, name)
            self.assertEqual(bundle.read("treasury_risk_pack.xlsx"), self.workbook_bytes)
            self.assertIn(b"outputs/release/report.html", bundle.read("START_HERE.txt"))
        self.assertEqual(self.note.read_bytes(), b"Unrelated reader notes must survive packaging failures.\n")

    def test_same_inputs_produce_identical_review_archive(self):
        first = self.build().read_bytes()
        second = self.build().read_bytes()
        self.assertEqual(first, second)

    def test_changed_release_rejects_old_workbook_pair(self):
        self.payloads["release.json"] += b" "
        (self.release / "release.json").write_bytes(self.payloads["release.json"])
        self.write_artifact_manifest()
        with self.assertRaises(ValueError):
            self.build()
        self.assert_no_partial_package()

    def test_changed_workbook_rejects_old_workbook_hash(self):
        (self.workbook / "treasury_risk_pack.xlsx").write_bytes(self.workbook_bytes + b"changed")
        with self.assertRaises(ValueError):
            self.build()
        self.assert_no_partial_package()

    def test_workbook_hash_is_required(self):
        del self.workbook_checks["workbookSha256"]
        self.write_workbook_checks()
        with self.assertRaises((ValueError, KeyError)):
            self.build()
        self.assert_no_partial_package()

    def test_failed_repack_preserves_last_valid_archive_and_reader_notes(self):
        output = self.build()
        original = output.read_bytes()
        self.workbook_checks["releaseSha256"] = "0" * 64
        self.write_workbook_checks()
        with self.assertRaises(ValueError):
            self.build()
        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(list(self.dist.glob("*.tmp")), [])
        self.assertEqual(self.note.read_bytes(), b"Unrelated reader notes must survive packaging failures.\n")

    def test_missing_required_document_publishes_no_partial_archive(self):
        (self.root / "docs" / "METHODOLOGY.md").unlink()
        with self.assertRaises(FileNotFoundError):
            self.build()
        self.assert_no_partial_package()

    def test_artifact_corruption_after_verification_is_rejected(self):
        def verified_then_changed(_):
            (self.release / "ledger_2022.csv").write_bytes(b"Changed after release verification\n")
            return self.pack, self.checks
        with patch.object(package_release, "load_verified_release", side_effect=verified_then_changed):
            with self.assertRaises(ValueError):
                package_release.package(self.root)
        self.assert_no_partial_package()

    def test_verifier_refusal_publishes_no_partial_archive(self):
        with patch.object(package_release, "load_verified_release", side_effect=ValueError("Corrupt source artifact")):
            with self.assertRaises(ValueError):
                package_release.package(self.root)
        self.assert_no_partial_package()


if __name__ == "__main__":
    unittest.main()
