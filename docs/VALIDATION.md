# Dated v1 release validation

Validated locally on October 2, 2026 with Python 3.11 and the pinned requirements. The release uses the September 25, 2026 latest curve and separate 2022/2023 annual accounting evaluations.

| Check | Result |
| --- | --- |
| Offline numerical and regression suite | 132 tests passed |
| Independent original Treasury auction calculations | 16/16 within $0.000001 per $100 face |
| Independent saved cash/wealth reconciliation | 1,996 rows across four methods and two years |
| Strictly prior monthly hedge decisions | 24 verified |
| Fresh convex first-order LP checks | Latest and every monthly solution passed; maximum normalized gap 1.10e-8 against a fixed 1e-7 limit |
| Release artifacts and source/config/model provenance | Seven artifacts verified, including report, JSON, charts and both ledgers |
| Dashboard integration checks | Six phases passed: initial view, face edit, infeasibility, restored controls, malformed release and stale release |
| Excel output | Five sheets rendered and reviewed; zero formula errors detected; saved XML dates, panes and error cells checked |

The dashboard integration uses Streamlit AppTest. Editing a target face reprices the portfolio; infeasible constraints withhold positions and success messages. Malformed or stale saved releases hide history and auction evidence while current analysis remains usable. A graphical browser session was not part of this validation.

Workbook verification uses Artifact Tool recalculation and saved-file XML inspection. Increasing the first holding by 10% changes PV from $11,637,353.60 to $11,838,823.96 and parallel DV01 from $5,631.16 to $5,647.12. Multiplying covariance by four doubles the residual step standard deviation from $2,459.12 to $4,918.23. All inputs were restored before the final export. Native Excel interaction was not tested.

The regression suite includes analytic bond and covariance examples, unsupported convention rejection, coupon ownership, signed principal payments, mark-frequency-independent daily carry, rollback on failed ledger operations, infeasible optimizations, tampered feasible but suboptimal hedges, incomplete artifacts, nonfinite cash, misleading saved tolerances, false breach flags and inconsistent summary PnL.

Reproduce the checks after acquiring or retaining the local source snapshot:

```powershell
python -m unittest discover -s tests -q
python scripts/build_release.py
python scripts/verify_release.py
python scripts/check_dashboard.py
```

The saved release is published only after in-memory and staged-artifact verification. Its complete manifest is written last. A failed rebuild removes only known generated report/ledger/chart files and preserves unrelated reader notes. `.gitattributes` retains exact file bytes so recorded model/artifact fingerprints survive Git checkout.

Ten older source dates remain quarantined at the original 0.006 bp tolerance; their cause is unresolved. These numerical controls do not establish executable market prices, historical point-in-time data, borrow availability or realized performance. The [decision memo](DECISION_MEMO.md) explains the model assumptions and financial interpretation. GitHub Actions is configured to run the offline suite when the repository is pushed; a remote CI run has not been performed.
