"""Offline acquisition controls with isolated temporary roots and fake HTTP."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import fetch_curve_data as fetch


CSV = b"Date,BETA0,SVENY10\n2026-09-25,4.1,4.2\n"


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


class FakeResponse(io.BytesIO):
    headers = {"Date": "Fri, 02 Oct 2026 05:46:37 GMT", "Content-Type": "text/csv",
               "ETag": '"test"', "Set-Cookie": "private-test-cookie"}

    def __init__(self, payload):
        super().__init__(payload)
        self.read_bounds = []

    def read(self, count=-1):
        self.read_bounds.append(count)
        return super().read(count)


class FetchCurveDataTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.pinned = self.root / "data/pinned"
        self.pinned.mkdir(parents=True)
        self.raw = self.root / "data/raw"
        self.manifest = {"source_url": fetch.SOURCE_URL, "source_page": fetch.SOURCE_PAGE,
                         "captured_at": "2026-10-02T05:46:36+00:00", "bytes": len(CSV),
                         "sha256": digest(CSV), "data_vintage": "OFFLINE SYNTHETIC fixture"}
        self.manifest_bytes = (json.dumps(self.manifest, indent=2) + "\n").encode()
        self.archive_bytes = gzip.compress(CSV, mtime=0)
        self.archive_manifest = {"schema_version": 1, "archive_file": fetch.ARCHIVE_NAME,
            "archive_bytes": len(self.archive_bytes), "archive_sha256": digest(self.archive_bytes),
            "uncompressed_bytes": len(CSV), "uncompressed_sha256": digest(CSV),
            "source_manifest_sha256": digest(self.manifest_bytes)}
        self.write_pin()

    def write_pin(self):
        (self.pinned / fetch.ARCHIVE_NAME).write_bytes(self.archive_bytes)
        (self.pinned / fetch.MANIFEST_NAME).write_bytes(self.manifest_bytes)
        (self.pinned / "archive_manifest.json").write_text(json.dumps(self.archive_manifest), encoding="utf-8")

    def offline_acquire(self):
        with patch.object(fetch, "urlopen", side_effect=AssertionError("Unexpected network access")):
            return fetch.acquire(self.root)

    def test_fresh_root_restores_exact_pinned_bytes_without_network(self):
        result = self.offline_acquire()
        self.assertEqual(result["status"], "restored")
        self.assertEqual(result["source"], self.manifest)
        self.assertEqual((self.raw / fetch.CSV_NAME).read_bytes(), CSV)
        self.assertEqual((self.raw / fetch.MANIFEST_NAME).read_bytes(), self.manifest_bytes)
        self.assertEqual({path.name for path in self.raw.iterdir()}, {fetch.CSV_NAME, fetch.MANIFEST_NAME})

    def test_existing_valid_snapshot_is_validated_and_retained(self):
        self.offline_acquire()
        csv = self.raw / fetch.CSV_NAME
        before = csv.stat().st_mtime_ns
        (self.pinned / fetch.ARCHIVE_NAME).write_bytes(b"corrupt unused archive")
        result = self.offline_acquire()
        self.assertEqual(result["status"], "retained")
        self.assertEqual(csv.stat().st_mtime_ns, before)
        self.assertEqual(csv.read_bytes(), CSV)

    def test_corrupt_existing_raw_does_not_fall_back_to_pin_or_web(self):
        self.offline_acquire()
        csv = self.raw / fetch.CSV_NAME
        csv.write_bytes(CSV + b"corrupt")
        with self.assertRaises(ValueError):
            self.offline_acquire()
        self.assertEqual(csv.read_bytes(), CSV + b"corrupt")

    def test_incomplete_existing_snapshot_does_not_fall_back(self):
        for missing in (fetch.CSV_NAME, fetch.MANIFEST_NAME):
            with self.subTest(missing=missing):
                self.raw.mkdir(exist_ok=True)
                (self.raw / fetch.CSV_NAME).write_bytes(CSV)
                (self.raw / fetch.MANIFEST_NAME).write_bytes(self.manifest_bytes)
                (self.raw / missing).unlink()
                with self.assertRaisesRegex(ValueError, "incomplete"):
                    self.offline_acquire()
                self.assertFalse((self.raw / missing).exists())

    def test_missing_pin_has_no_network_fallback_or_partial_raw(self):
        (self.pinned / fetch.ARCHIVE_NAME).unlink()
        with self.assertRaises(FileNotFoundError):
            self.offline_acquire()
        self.assertFalse(self.raw.exists())

    def test_corrupt_archive_is_rejected_before_publication(self):
        (self.pinned / fetch.ARCHIVE_NAME).write_bytes(self.archive_bytes + b"corrupt")
        with self.assertRaisesRegex(ValueError, "archive size or SHA-256"):
            self.offline_acquire()
        self.assertFalse(self.raw.exists())

    def test_corrupt_source_manifest_is_rejected_before_publication(self):
        (self.pinned / fetch.MANIFEST_NAME).write_bytes(self.manifest_bytes + b" ")
        with self.assertRaisesRegex(ValueError, "source-manifest SHA-256"):
            self.offline_acquire()
        self.assertFalse(self.raw.exists())

    def test_pin_metadata_requires_literal_file_and_bounded_byte_counts(self):
        original = dict(self.archive_manifest)
        for key, invalid in (("archive_file", "../outside.gz"), ("schema_version", 2), ("schema_version", True),
                             ("archive_bytes", True), ("uncompressed_bytes", fetch.MAX_BYTES + 1),
                             ("archive_sha256", "INVALID")):
            with self.subTest(field=key):
                self.archive_manifest = dict(original, **{key: invalid})
                self.write_pin()
                with self.assertRaises(ValueError):
                    self.offline_acquire()
                self.assertFalse(self.raw.exists())

    def test_archive_and_source_manifest_must_agree(self):
        self.archive_manifest["uncompressed_sha256"] = "0" * 64
        self.write_pin()
        with self.assertRaisesRegex(ValueError, "manifests disagree"):
            self.offline_acquire()
        self.assertFalse(self.raw.exists())

    def test_retained_snapshot_is_bounded_before_validation(self):
        self.offline_acquire()
        with patch.object(fetch, "MAX_BYTES", len(CSV) - 1):
            with self.assertRaisesRegex(ValueError, "bounded byte count"):
                self.offline_acquire()
        with patch.object(fetch, "MAX_MANIFEST_BYTES", len(self.manifest_bytes) - 1):
            with self.assertRaisesRegex(ValueError, "manifest exceeds"):
                self.offline_acquire()
        self.assertEqual((self.raw / fetch.CSV_NAME).read_bytes(), CSV)

    def test_refresh_network_failure_preserves_existing_snapshot(self):
        self.offline_acquire()
        with patch.object(fetch, "urlopen", side_effect=OSError("Synthetic connection unavailable")):
            with self.assertRaisesRegex(OSError, "Synthetic connection"):
                fetch.acquire(self.root, refresh=True)
        self.assertEqual((self.raw / fetch.CSV_NAME).read_bytes(), CSV)
        self.assertEqual((self.raw / fetch.MANIFEST_NAME).read_bytes(), self.manifest_bytes)

    def test_decompressed_payload_must_match_source_hash(self):
        self.archive_bytes = gzip.compress(CSV.replace(b"4.1", b"9.9"), mtime=0)
        self.archive_manifest.update(archive_bytes=len(self.archive_bytes), archive_sha256=digest(self.archive_bytes))
        self.write_pin()
        with self.assertRaisesRegex(ValueError, "Source payload size or SHA-256"):
            self.offline_acquire()
        self.assertFalse(self.raw.exists())

    def test_invalid_or_truncated_gzip_is_rejected(self):
        for archive in (b"not-gzip", self.archive_bytes[:-4]):
            with self.subTest(archive=archive):
                self.archive_bytes = archive
                self.archive_manifest.update(archive_bytes=len(archive), archive_sha256=digest(archive))
                self.write_pin()
                with self.assertRaisesRegex(ValueError, "valid gzip"):
                    self.offline_acquire()
                self.assertFalse(self.raw.exists())

    def test_nonofficial_pinned_source_urls_are_rejected(self):
        original = dict(self.manifest)
        for key in ("source_url", "source_page"):
            with self.subTest(field=key):
                self.manifest = dict(original, **{key: "https://example.invalid/source"})
                self.manifest_bytes = json.dumps(self.manifest).encode()
                self.archive_manifest["source_manifest_sha256"] = digest(self.manifest_bytes)
                self.write_pin()
                with self.assertRaisesRegex(ValueError, "official Fed URLs"):
                    self.offline_acquire()
                self.assertFalse(self.raw.exists())

    def test_decompression_limit_is_applied_to_expanded_bytes(self):
        self.archive_bytes = gzip.compress(CSV + b"x" * 1000, mtime=0)
        self.archive_manifest.update(archive_bytes=len(self.archive_bytes), archive_sha256=digest(self.archive_bytes))
        self.write_pin()
        # Metadata declares a small source; compressed bytes pass, expanded bytes do not.
        with patch.object(fetch, "MAX_BYTES", 128):
            with self.assertRaisesRegex(ValueError, "Expanded pinned archive"):
                self.offline_acquire()
        self.assertFalse(self.raw.exists())

    def test_explicit_refresh_uses_official_url_bounded_read_and_safe_headers(self):
        response = FakeResponse(CSV)
        with patch.object(fetch, "urlopen", return_value=response) as network:
            result = fetch.acquire(self.root, refresh=True)
        self.assertEqual(result["status"], "refreshed")
        request = network.call_args.args[0]
        self.assertEqual(request.full_url, fetch.SOURCE_URL)
        self.assertEqual(network.call_args.kwargs["timeout"], 45)
        self.assertEqual(response.read_bounds, [fetch.MAX_BYTES + 1])
        self.assertEqual(result["source"]["sha256"], digest(CSV))
        self.assertNotIn("Set-Cookie", result["source"]["response_headers"])
        self.assertEqual((self.raw / fetch.CSV_NAME).read_bytes(), CSV)

    def test_explicit_refresh_can_replace_a_corrupt_working_snapshot(self):
        self.offline_acquire()
        (self.raw / fetch.CSV_NAME).write_bytes(b"bad working copy")
        (self.pinned / fetch.ARCHIVE_NAME).write_bytes(b"bad pin")
        with patch.object(fetch, "urlopen", return_value=FakeResponse(CSV)):
            result = fetch.acquire(self.root, refresh=True)
        self.assertEqual(result["status"], "refreshed")
        self.assertEqual(self.offline_acquire()["status"], "retained")

    def test_invalid_refresh_preserves_existing_snapshot_bytes(self):
        self.offline_acquire()
        before = (self.raw / fetch.MANIFEST_NAME).read_bytes()
        with patch.object(fetch, "urlopen", return_value=FakeResponse(b"<html>Not curve data</html>")):
            with self.assertRaisesRegex(ValueError, "expected GSW CSV headers"):
                fetch.acquire(self.root, refresh=True)
        self.assertEqual((self.raw / fetch.CSV_NAME).read_bytes(), CSV)
        self.assertEqual((self.raw / fetch.MANIFEST_NAME).read_bytes(), before)

    def test_oversized_refresh_is_rejected_before_writing(self):
        with patch.object(fetch, "MAX_BYTES", 32), patch.object(fetch, "urlopen", return_value=FakeResponse(CSV)):
            with self.assertRaisesRegex(ValueError, "acquisition limit"):
                fetch.acquire(self.root, refresh=True)
        self.assertFalse(self.raw.exists())

    def test_source_manifest_is_published_after_csv(self):
        original = Path.replace
        replacements = []

        def record_replace(path, destination):
            replacements.append(Path(destination).name)
            if Path(destination).name == fetch.CSV_NAME:
                self.assertFalse((self.raw / fetch.MANIFEST_NAME).exists())
            return original(path, destination)

        with patch.object(Path, "replace", record_replace):
            self.offline_acquire()
        self.assertEqual(replacements, [fetch.CSV_NAME, fetch.MANIFEST_NAME])

    def test_failure_during_staging_preserves_working_data_and_removes_stage(self):
        self.offline_acquire()
        before = (self.raw / fetch.MANIFEST_NAME).read_bytes()
        with patch.object(fetch, "validate_source_manifest", side_effect=ValueError("Synthetic staged validation failure")), \
             patch.object(fetch, "urlopen", return_value=FakeResponse(CSV)):
            with self.assertRaisesRegex(ValueError, "Synthetic staged"):
                fetch.acquire(self.root, refresh=True)
        self.assertEqual((self.raw / fetch.CSV_NAME).read_bytes(), CSV)
        self.assertEqual((self.raw / fetch.MANIFEST_NAME).read_bytes(), before)
        self.assertEqual({path.name for path in self.raw.iterdir()}, {fetch.CSV_NAME, fetch.MANIFEST_NAME})


if __name__ == "__main__":
    unittest.main()
