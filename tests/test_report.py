"""Escaping, source-link boundaries and empty-evidence presentation checks."""

import unittest

from treasury_risk.report import render_report


def empty_pack():
    return {"title": "Treasury curve research", "scope": "Hypothetical curves and bonds", "source": {},
            "data_quality": {}, "benchmark": {}, "latest": {}, "historical": {}, "limitations": []}


class ReportTests(unittest.TestCase):
    def test_empty_evidence_does_not_claim_benchmark_success(self):
        result = render_report(empty_pack())
        self.assertTrue(result.startswith("<!doctype html>"))
        self.assertIn("Benchmark review required", result)
        self.assertNotIn("Yield benchmarks pass", result)
        self.assertIn("No complete finite observations", result)
        self.assertIn("not an actual return or trading P/L", result)
        self.assertIn("Maturities do not age and coupons are not realized", result)
        self.assertIn("historical observations may be revised", result)
        self.assertNotIn("<script", result)

    def test_all_reader_labels_are_html_escaped(self):
        attack = '<img src=x onerror="alert(1)">'
        pack = empty_pack()
        pack.update(title=attack, scope=attack, limitations=[attack], data_quality={attack: attack})
        pack["source"].update(model_type=attack, captured_at=attack, sha256=attack, data_vintage=attack)
        pack["latest"].update(date=attack, portfolio=[{"label": attack}], hedge_instruments=[{"label": attack}],
                              scenarios=[{"name": attack}])
        pack["historical"].update(label=attack, start_date=attack, end_date=attack)
        result = render_report(pack)
        self.assertNotIn("<img", result)
        self.assertIn("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;", result)

    def test_source_links_allow_only_https_federal_reserve_hosts(self):
        for url in ("javascript:alert(1)", "http://www.federalreserve.gov/data/", "https://www.federalreserve.gov.evil.example/",
                    "https://user@www.federalreserve.gov/", "https://www.federalreserve.gov:8443/", "https://www.federalreserve.gov/\nattack"):
            with self.subTest(url=url):
                pack = empty_pack()
                pack["source"]["source_url"] = url
                self.assertNotIn('href="' + url, render_report(pack))
                self.assertIn("No approved Federal Reserve source link", render_report(pack))
        pack = empty_pack()
        pack["source"] = {"source_url": "https://www.federalreserve.gov/data/yield-curve-tables/feds200628.csv",
                          "source_page": "https://www.federalreserve.gov/data/nominal-yield-curve.htm",
                          "response_headers": {"set-cookie": "DO_NOT_DISPLAY_HEADER_VALUES"}}
        result = render_report(pack)
        self.assertIn('href="https://www.federalreserve.gov/data/yield-curve-tables/feds200628.csv"', result)
        self.assertNotIn("DO_NOT_DISPLAY_HEADER_VALUES", result)

    def test_finite_charts_and_risk_rows_keep_hypothetical_context(self):
        pack = empty_pack()
        pack["benchmark"] = {"comparisons": 18, "failed_comparisons": 0, "max_absolute_error_bps": 0.001, "tolerance_bps": 0.01}
        pack["latest"] = {"date": "2026-09-25", "zero_curve": [{"maturity_years": .5, "zero_yield_percent": 3.0},
                                                                   {"maturity_years": 30, "zero_yield_percent": 4.5}],
                          "key_rate_nodes": [.5, 1], "target_key_rate_dv01": [5, 7],
                          "duration_hedge": {"residual_exposure": [5, -5]},
                          "multi_hedge": {"residual_exposure": [1, -1], "condition_number": 4.2, "matrix_rank": 2},
                          "hedge_instruments": [{"label": "Two-year"}, {"label": "Ten-year"}]}
        pack["historical"] = {"start_date": "2025-01-02", "end_date": "2025-01-03", "observations": 2,
                              "rows": [{"date": "2025-01-02", "unhedged_change": 0, "duration_hedged_change": 0, "multi_hedged_change": 0},
                                       {"date": "2025-01-03", "unhedged_change": 12, "duration_hedged_change": 4, "multi_hedged_change": -1}]}
        result = render_report(pack)
        self.assertEqual(result.count('<svg '), 2)
        self.assertEqual(result.count('<polyline '), 4)
        self.assertIn("Yield benchmarks pass", result)
        self.assertIn("−$5.00", result)
        self.assertIn("Hypothetical bonds, current-vintage curve data", result)
        self.assertIn("gross absolute face", result)
        self.assertIn("not an actual return or trading P/L", result)


if __name__ == "__main__":
    unittest.main()
