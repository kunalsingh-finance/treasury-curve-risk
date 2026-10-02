# Treasury Curve & Hedge Engine v1.0.1

A dated cash-Treasury research platform with sourced security terms, constrained hedge decisions and auditable cash accounting.

## Changes in v1.0.1

- Reject invalid face amounts, controls and dates before research calculations. Iterable holdings are materialized once; duplicate or unordered observations and nonpositive starting equity reject.
- Verify portfolio values, comparison hedges, scenarios and covariance evidence against numerical identities and the checked source. Report HTML and ledger CSVs must agree with the saved pack.
- Bound platform rounding in source-audit replay using fitted coefficient magnitudes; source acceptance and quarantine decisions remain exact.
- Lock publication so overlapping builds cannot erase another writer's successful release. Review packages use their own staging files and a protected source snapshot.
- Derive release version and source-exception count from their recorded inputs. Renew the report, workbook and review archive together.

The pinned source, economic conventions and valid default financial results remain unchanged. The original v1.0.0 GitHub release remains available.

## Research scope

- Price 16 officially sourced Treasury terms with dated coupon schedules, accrued interest and clean/dirty fitted-curve values.
- Compare parallel-DV01, unweighted and covariance-risk hedges with gross-face and position limits, and independently checked convex optimality gaps.
- Review full repricing shocks and separate 2022/2023 ledgers containing signed coupons, principal, trading costs, assumed funding and collateral reserves.
- Change portfolio controls in the local Streamlit dashboard; failed or infeasible calculations withhold invalid output.
- Restore the exact October 2, 2026 source snapshot offline from its checked-in, checksum-verified archive.
- Review the static HTML report, decision memo and five-sheet Excel risk pack. The packaged workbook retains its original source-pack provenance.

The validated research release includes 16 original auction-price checks, 1,996 reconciled cash rows and 24 strictly prior monthly decisions. The test suite, saved-release verifier, dashboard smoke and isolated installation check are reproducible from a fresh checkout.

Holdings, execution and financing are hypothetical. Fitted prices are not dealer quotes, and the historical source vintage is revised. Assumed cash interest drives most of the constrained hedge's positive model PnL. Ten older curve dates remain quarantined at the fixed tolerance. The decision memo records these limitations and the financial interpretation.

Start with `README.md`. Run `python scripts/fetch_curve_data.py` to restore the source, followed by `python scripts/verify_release.py` and `python -m streamlit run app.py`. A graphical Python build is unnecessary to review the included HTML report and Excel file.
