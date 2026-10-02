# Dated v1 release validation

Validated locally on October 2, 2026 with Python 3.11 and the pinned requirements. The release uses the September 25, 2026 latest curve and separate 2022/2023 annual accounting evaluations.

| Check | Result |
| --- | --- |
| Offline numerical and regression suite | 163 tests passed |
| Independent original Treasury auction calculations | 16/16 within $0.000001 per $100 face |
| Independent saved cash/wealth reconciliation | 1,996 rows across four methods and two years |
| Strictly prior monthly hedge decisions | 24 verified |
| Fresh convex first-order LP checks | Latest and every monthly solution passed; maximum normalized gap 1.10e-8 against a fixed 1e-7 limit |
| Release artifacts and source/config/model provenance | Seven artifacts verified, including report, JSON, charts and both ledgers |
| Dashboard integration checks | Six phases passed: initial view, face edit, infeasibility, restored controls, malformed release and stale release |
| Excel output | Five sheets rendered and reviewed; zero formula errors detected; saved XML dates, panes and error cells checked |
| Exact source reproduction | Checked-in gzip and metadata restore the original 16,650,134 source bytes, with networking disabled |
| Fresh checkout and installed library | Exact Git export, restored-source verification and isolated wheel import/pricing checks |
| Review package integrity | Paired workbook/release provenance, artifact hashes and ZIP readback checked |

The dashboard integration uses Streamlit AppTest. Editing a target face reprices the portfolio; infeasible constraints withhold positions and success messages. Malformed or stale saved releases hide history and auction evidence while current analysis remains usable. A graphical browser session was not part of this validation.

Workbook verification uses Artifact Tool recalculation and saved-file XML inspection. Increasing the first holding by 10% changes PV from $11,637,353.60 to $11,838,823.96 and parallel DV01 from $5,631.16 to $5,647.12. Multiplying covariance by four doubles the residual step standard deviation from $2,459.12 to $4,918.23. All inputs were restored before the final export. Native Excel interaction was not tested.

The regression suite includes analytic bond and covariance examples, unsupported convention rejection, coupon ownership, signed principal payments, mark-frequency-independent daily carry, rollback on failed ledger operations, infeasible optimizations, tampered feasible but suboptimal hedges, incomplete artifacts, nonfinite cash, misleading saved tolerances, false breach flags and inconsistent summary PnL.

Reproduce the checks after restoring the checked-in local source snapshot:

```powershell
python scripts/fetch_curve_data.py
python -m unittest discover -s tests -q
python scripts/verify_release.py
python scripts/check_dashboard.py
python scripts/check_installation.py
python scripts/package_release.py
```

The saved release is published only after in-memory and staged-artifact verification. Its complete manifest is written last. A failed rebuild removes only known generated report/ledger/chart files and preserves unrelated reader notes. `.gitattributes` retains exact file bytes so recorded model/artifact fingerprints survive Git checkout.

The checked-in source archive preserves the exact capture; `--refresh` alone acquires a newer mutable vintage. The acquisition regressions reject corrupt archives, incomplete raw pairs, oversized compressed/expanded data, nonofficial URLs and staged failures. Review-package regressions reject a stale workbook/release pair and artifacts changed after verification, preserving unrelated files and the last valid review ZIP when construction fails.

Ten older source dates remain quarantined at the original 0.006 bp tolerance; their cause is unresolved. These numerical controls do not establish executable market prices, historical point-in-time data, borrow availability or realized performance. The [decision memo](DECISION_MEMO.md) explains the model assumptions and financial interpretation. GitHub Actions runs numerical, source, release, dashboard, installation and review-package checks on Linux and Windows; consult the repository's Actions results for remote execution status.
