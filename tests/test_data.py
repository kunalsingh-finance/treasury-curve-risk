import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from treasury_risk.data import GSWDataError, OFFICIAL_SOURCE_URL, load_gsw, validate_source_manifest


HEADER = ["Date", "BETA0", "BETA1", "BETA2", "BETA3", "TAU1", "TAU2"] + [f"SVENY{x:02d}" for x in range(1, 31)]


def row(day, parameters=None, yields=None):
    return [day] + (parameters if parameters is not None else ["4", "-1", "2", "1", "2", "5"]) + (yields if yields is not None else ["4.5"] * 30)


class GSWDataTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "curve.csv"

    def write(self, rows, header=HEADER):
        with self.path.open("w", encoding="utf-8", newline="") as source:
            source.write('"Staff research product, not official release"\n\n')
            writer = csv.writer(source)
            writer.writerow(header)
            writer.writerows(rows)
        return self.path

    def test_schema_units_and_missing_published_yield(self):
        yields = ["4.5"] * 30
        yields[4] = "NA"
        records, quality = load_gsw(self.write([row("2022-01-03", yields=yields)]))
        self.assertEqual(records[0]["date"], "2022-01-03")
        self.assertEqual(records[0]["beta0"], 4.0)
        self.assertEqual(records[0]["tau2"], 5.0)
        self.assertEqual(records[0]["published_zero_yields"][1], 0.045)
        self.assertIsNone(records[0]["published_zero_yields"][5])
        self.assertEqual(set(records[0]["published_zero_yields"]), set(range(1, 31)))
        self.assertEqual(records[0]["data_vintage"], "current_vintage")
        self.assertEqual(quality["valid_rows"], 1)
        self.assertEqual(quality["start_date"], "2022-01-03")

    def test_missing_invalid_and_out_of_range_rows_are_counted_without_fill(self):
        rows = [
            row("1961-06-14", ["4", "-1", "2", "0", "2", "-999.99"]),
            row("2022-01-03"),
            row("2022-01-04", ["NA"] * 6),
            row("2022-01-05", ["4", "NA", "2", "1", "2", "5"]),
            row("2022-01-06", ["Infinity", "-1", "2", "1", "2", "5"]),
            row("2022-01-07", ["4", "-1", "2", "1", "2", "0"]),
            row("2022-01-10"),
        ]
        records, quality = load_gsw(self.write(rows), end_date="2022-01-07")
        self.assertEqual([item["date"] for item in records], ["2022-01-03"])
        self.assertEqual(quality["rows_read"], 7)
        self.assertEqual(quality["out_of_range"], 2)
        self.assertEqual(quality["missing_param_rows"], 1)
        self.assertEqual(quality["invalid_param_rows"], 3)

    def test_pre1980_is_excluded_even_if_start_is_earlier(self):
        records, quality = load_gsw(self.write([row("1961-06-14"), row("1980-01-02")]), start_date="1961-01-01")
        self.assertEqual([item["date"] for item in records], ["1980-01-02"])
        self.assertEqual(quality["out_of_range"], 1)

    def test_invalid_yield_is_excluded(self):
        yields = ["4.5"] * 30
        yields[9] = "NaN"
        records, quality = load_gsw(self.write([row("2022-01-03", yields=yields)]))
        self.assertEqual(records, [])
        self.assertEqual(quality["invalid_yield_rows"], 1)
        self.assertIsNone(quality["start_date"])

    def test_dates_are_strict_and_ordered_including_out_of_range(self):
        cases = [
            [row("20220103")], [row("2022-02-30")],
            [row("2022-01-03"), row("2022-01-03")],
            [row("2022-01-04"), row("2022-01-03")],
            [row("1961-06-14"), row("1961-06-14")],
        ]
        for rows in cases:
            with self.subTest(rows=rows):
                with self.assertRaises(GSWDataError):
                    load_gsw(self.write(rows))

    def test_missing_duplicate_or_wrong_header_and_row_width_reject(self):
        for header in (HEADER[:-1], HEADER + ["BETA0"], ["Date", "bad"]):
            with self.subTest(header=header):
                with self.assertRaises(GSWDataError):
                    load_gsw(self.write([], header=header))
        with self.assertRaises(GSWDataError):
            load_gsw(self.write([row("2022-01-03")[:-1]]))
        self.path.write_text("preamble only\n", encoding="utf-8")
        with self.assertRaises(GSWDataError):
            load_gsw(self.path)

    def test_bad_date_bounds_reject(self):
        self.write([row("2022-01-03")])
        for arguments in ({"start_date": "20220103"}, {"start_date": "2022-01-04", "end_date": "2022-01-03"}):
            with self.assertRaises(GSWDataError):
                load_gsw(self.path, **arguments)

    def manifest(self):
        content = self.path.read_bytes()
        return {"source_url": OFFICIAL_SOURCE_URL, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}

    def test_manifest_checks_exact_bytes_hash_and_official_url(self):
        self.write([row("2022-01-03")])
        manifest_path = self.path.with_suffix(".json")
        expected = self.manifest()
        manifest_path.write_text(json.dumps(expected), encoding="utf-8")
        self.assertEqual(validate_source_manifest(self.path, manifest_path), expected)
        for key, value in (("bytes", expected["bytes"] + 1), ("sha256", "0" * 64), ("source_url", "https://example.com/curve.csv"), ("bytes", True)):
            with self.subTest(key=key):
                invalid = dict(expected)
                invalid[key] = value
                manifest_path.write_text(json.dumps(invalid), encoding="utf-8")
                with self.assertRaises(GSWDataError):
                    validate_source_manifest(self.path, manifest_path)
        manifest_path.write_text(json.dumps(expected), encoding="utf-8")
        content = bytearray(self.path.read_bytes())
        content[-2] = ord("x")
        self.path.write_bytes(content)
        with self.assertRaisesRegex(GSWDataError, "SHA-256"):
            validate_source_manifest(self.path, manifest_path)


if __name__ == "__main__":
    unittest.main()
