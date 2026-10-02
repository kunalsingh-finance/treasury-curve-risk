# Treasury Curve & Hedge Engine

A completed dated fixed-income research engine: source Treasury terms, value their cash flows, size constrained hedges, and reconcile the resulting cash and portfolio wealth.

Start with the [v1.0.1 release report](outputs/release/report.html) and [hedge decision memo](docs/DECISION_MEMO.md). The local interface in [app.py](app.py) lets a reviewer change face allocations, valuation date, covariance lookback and hedge limits, then inspect prices, residual risk, scenarios and source exceptions.

## What the release demonstrates

- Sixteen actual Treasury note/bond term records, linked to official auction results; separate latest and historical instrument sets.
- Dated semiannual schedules, following bank-business-day payments, ACT/ACT coupon-period accrual, and clean/dirty fitted-curve PV.
- Svensson zero and forward rates, parallel DV01, duration, convexity and nine-node key-rate DV01.
- Four comparisons: unhedged holdings, a single-instrument parallel-DV01 hedge, unweighted least squares, and bounded covariance-risk optimization with an independent optimality check.
- Monthly historical decisions based on prior curves and strictly earlier covariance observations, with dated coupons, principal, signed trades, collateral reserves, cash interest, funding cost and transaction costs.
- Source, instrument and artifact hashes; quarantined source exceptions; a separate verifier for saved accounting and optimizer results.

The terms are real; the holdings are illustrative. The default three target faces are **$2m, $4m and $6m**. All current marks and assumed trades use a fitted curve rather than dealer quotes. Historical observations come from a revised source vintage. The project demonstrates pricing, risk controls and auditable research, without an alpha or executable-performance claim.

## Run locally

Use Python 3.11 with the versions pinned in [requirements.txt](requirements.txt).

```powershell
python -m pip install -r requirements.txt
python scripts/fetch_curve_data.py
python scripts/verify_release.py
python -m streamlit run app.py
```

On Windows, [Launch Treasury Dashboard.cmd](<Launch Treasury Dashboard.cmd>) starts the local interface. The HTML release report can also be opened directly without starting Streamlit.

The first setup restores the checked-in, compressed October 2, 2026 source snapshot into `data/raw/` **without a network request**. Archive bytes, decompressed bytes and acquisition metadata are verified first. Existing raw files must also pass integrity checks. This preserves the exact curve vintage used by the saved report and workbook.

`python scripts/build_release.py` recomputes the Python research outputs from the restored source. To intentionally acquire a newer, mutable Federal Reserve vintage, use `python scripts/fetch_curve_data.py --refresh`, then rebuild and verify. A refresh does not replace the checked-in archive or regenerate the Excel workbook. `scripts/pin_source_snapshot.py --replace` explicitly changes the archived vintage; its related report and workbook evidence must then be renewed.

Run the numerical and integration suite with:

```powershell
python -m unittest discover -s tests -v
python scripts/check_dashboard.py
python scripts/check_installation.py
```

The dashboard integration smoke check requires the restored raw source. The installation check exports the committed repository, restores the source with networking disabled, verifies report artifacts and installs a wheel in an isolated target. It requires the runtime requirements plus setuptools and wheel. The wheel exposes the numerical Python library; the app, scripts and data are run from a source checkout.

The release builder holds an exclusive publication lock before replacing report status with `building` and publishes a failure page if a required check fails. An overlapping build stops without changing the active build's files. The verifier checks saved artifact bytes, current source/config/model fingerprints, cash rolls, dated decision boundaries and hedge constraints. It also checks report and CSV content against the saved pack and reconstructs financial inputs from the verified curve source. A successfully generated report does not convert source exceptions into passed observations.

## Evidence from the pinned release

The October 2, 2026 source capture contains 6,688 parameter-valid curves since 2000 and 200,610 published-zero-yield comparisons. Ten dates remain quarantined: nine exceed the unchanged 0.006 bp reconstruction tolerance, and one has no published benchmarks. The full-source audit remains under review. Required latest and historical dates pass their available checks.

All **16 independent official auction-price checks** pass within $0.000001 per $100 face. These validate the specified auction yield convention at original issue dates, separately from current curve valuations.

At September 25, 2026, target dirty PV is $11.637m and parallel DV01 is $5,631.16 per bp. The constrained hedge reduces variance estimated from its prior covariance window by 98.99%, with $12.338m gross hedge face against a $24m limit. That is an estimated risk result, not a forecast return.

The 2022 and 2023 evaluations restart separately with equal initial equity across methods. Constrained model net PnL is $260,770 and $224,789, respectively. Recorded hypothetical cash interest is $259,577 and $235,389, so the financing assumption dominates the positive result. See the [decision memo](docs/DECISION_MEMO.md) for comparison tables and attribution.

## Review the implementation

| Evidence | Location |
| --- | --- |
| Dated release and charts | [outputs/release/report.html](outputs/release/report.html) |
| Saved source, instruments, risks and ledgers | [outputs/release/release.json](outputs/release/release.json) |
| Exported dated cash rows | [2022 ledger](outputs/release/ledger_2022.csv), [2023 ledger](outputs/release/ledger_2023.csv) |
| Editable Excel risk pack | [treasury_risk_pack.xlsx](outputs/01a0fad9-107c-7cd2-b374-8b14defca53a/treasury_risk_pack.xlsx) |
| Instrument terms and official links | [configs/treasury_instruments.json](configs/treasury_instruments.json) |
| Model and evaluation rules | [methodology](docs/METHODOLOGY.md), [conventions](docs/CONVENTIONS.md) |
| Independent auction calculation | [treasury_risk/benchmarks.py](treasury_risk/benchmarks.py) |
| Release integrity and result verification | [scripts/verify_release.py](scripts/verify_release.py) |
| Completed numerical, dashboard and workbook checks | [validation record](docs/VALIDATION.md) |

The workbook has live formulas for face-scaled PV, accrued interest, parallel/key-rate exposure, covariance risk and face-limit checks. Editable target/hedge faces and limits update those calculations; its covariance multiplier scales risk under the saved matrix. Per-$100 curve marks and sensitivities, covariance/PCA inputs, historical ledgers and Python-selected weights are frozen exports. Excel edits do not reprice cash flows, reestimate covariance, reoptimize hedges or rerun history; those operations run in Python before a new export.

The delivered Excel file is a verified v1 snapshot. Rebuilding Python outputs leaves that workbook attached to its original `release.json` SHA-256, recorded in `workbook_verification.json`. Its export script uses Codex's bundled `@oai/artifact-tool` through a local runtime junction; ordinary Python or Node installation does not supply that runtime. The [review-package builder](scripts/package_release.py) checks that the workbook and saved Python release share the recorded source pack before bundling them.

GitHub Actions validates the numerical suite, source restoration, saved release, dashboard failure states and isolated wheel installation on Linux and Windows. The [release notes](docs/RELEASE_NOTES.md) describe the packaged v1 scope.

[docs/RESEARCH_NOTE.md](docs/RESEARCH_NOTE.md), `scripts/run_demo.py` and `outputs/report.html` are legacy **undated, frozen-cash-flow diagnostics**. The dated v1 release in `outputs/release/` is the main deliverable.

The curve source is the [Federal Reserve's fitted nominal Treasury research curve](https://www.federalreserve.gov/data/nominal-yield-curve.htm). It is revisable and represents off-the-run coupon securities. Calendar and auction references are recorded in [conventions](docs/CONVENTIONS.md).
