# Dated Treasury research methodology — v1.0.2

The release connects sourced fixed-rate Treasury terms, a fitted zero curve, constrained hedge decisions and a signed cash ledger. It evaluates risk and model wealth under declared assumptions. The main evidence is [release.json](../outputs/release/release.json), the [HTML report](../outputs/release/report.html) and exported dated ledger rows.

## Source controls and admissible observations

The Federal Reserve CSV is retained byte-for-byte with source URL, capture timestamp, response metadata, size and SHA-256. Runs verify the acquisition manifest before analysis. The working sample begins in 2000; missing parameter observations are skipped and counted, without forward filling. The source is a current revised vintage, so strictly ordered observation dates do not establish historical publication availability. The [Federal Reserve documentation](https://www.federalreserve.gov/data/nominal-yield-curve.htm) explains the source's revision policy.

Each parameter-valid curve is reconstructed and compared with available published continuously compounded zero yields. The fixed 0.006 bp tolerance accommodates four-decimal-percent rounding up to 0.005 bp plus numerical error. Published par yields are not substituted for zeros. In the pinned snapshot, 71 maturity comparisons fail across nine dates; a tenth date has no published benchmark. All ten dates are retained as exceptions and excluded from valuations, decision curves and covariance histories. The full-source audit remains under review even when the required release dates pass.

The saved-audit verifier separately compares its fresh calculations with the recorded numerical errors. Large cancelling fitted coefficients can amplify small platform differences in exponential functions, so this replay comparison uses a floating-point allowance derived from coefficient magnitude. Dates, counts, classifications and the source's fixed acceptance tolerance must match exactly; replay roundoff never turns a quarantined date into an accepted observation.

Sixteen Treasury term records contain CUSIP, label, actual issue date, dated date, maturity and coupon, together with primary auction links and selected-field transcriptions. Their text hashes test local consistency; they do not authenticate original PDF bytes. Latest and historical sets each contain three targets and five hedges. Face allocations of $2m/$4m/$6m are hypothetical.

## Dated valuation and sensitivities

Coupon dates are generated backward from maturity in six-month steps with a consistent end-of-month anchor. The dated date must lie on this regular schedule; unsupported first-period stubs are rejected. Actual issue date can be later than dated date. Accrued interest uses actual elapsed days divided by actual days in the unadjusted coupon period. Cash pays on the following Federal Reserve Bank business day without an additional coupon. Discount time uses ACT/365F to that adjusted payment date. Full conventions and boundary examples are in [CONVENTIONS.md](CONVENTIONS.md).

Svensson beta parameters retain percentage-point units and taus are years. The engine returns fractional continuously compounded zero rates, instantaneous forwards and `D(t) = exp(-y(t) × t)`. Negative yields are supported; unstable or unrepresentable floating-point calculations reject explicitly. Dirty PV is the sum of discounted coupon/principal amounts; clean PV is dirty PV less accrued interest.

Parallel DV01 is `[PV(-1 bp) − PV(+1 bp)] / 2`, in dollars per bp. Effective duration divides DV01 by `PV × 0.0001`. Convexity uses the centered second price difference divided by `PV × 0.0001²`. After final payment, PV, accrued interest and DV01 are zero; duration and convexity are undefined and returned as null.

Key-rate DV01 uses continuous zero-rate bumps at 0.5, 1, 2, 3, 5, 7, 10, 20 and 30 years. Each bump is piecewise linear between nodes with flat endpoint tails. The basis functions sum to one, so summed node DV01 approximates parallel DV01; centered finite differences need not agree exactly. Every hedge column is risk per $100 face. Weights scale that face; negative weights denote short holdings.

## Prior-window risk estimation and hedge objectives

For each valuation/execution date, covariance uses exactly 252 successive changes from 253 admissible zero-yield levels **strictly before that date**. Changes have basis-point units. Gaps above seven calendar days reject the window. These are successive observed-curve changes, not a guarantee of one-day observations. The sample covariance is shrunk 10% toward mean variance times the identity. PCA summarizes this prior sample; factor signs are fixed for reproducibility and are not forecasts.

Let `b` be target node DV01, `H` hedge DV01 columns, `w` 100-face weights and `Σ` the estimated bp-change covariance. The constrained objective is:

`minimize (b + H w)' Σ (b + H w)`

The v1 release sets ridge penalty to zero, matches the separately calculated parallel DV01, limits gross absolute hedge face to twice target face ($24m), and limits each hedge to 1.5 times target face ($18m). These limits apply to the hedge sleeve; they do not include the $12m target holdings. Zero hedge trade cash is an optional interface constraint and is off in the saved release.

The solver first checks feasibility, then minimizes the convex objective. It checks exposure/equality/face residuals and solves a separate linear first-order optimality-gap problem over the feasible set. Failed or uncertified solutions are withheld; required release decisions stop the build rather than use a fallback. Numerical tolerances and solver diagnostics remain in the saved result. This certificate concerns the specified optimization problem, not funding access or security borrow availability.

Comparators use the same target terms: unhedged, a single available hedge nearest ten years that matches actual parallel DV01, and unweighted least squares minimizing the node-exposure norm. Least squares has no covariance or parallel-equality constraint. Objectives therefore differ, and a smaller unweighted norm need not give smaller estimated variance or stress loss.

## Historical decision timing

The saved evaluations cover January 3–December 30, 2022 (249 observations) and January 3–December 29, 2023 (250 observations). Each year starts a new ledger and equal initial equity across all four methods: initial target dirty PV plus a $1.2m buffer, or 10% of target face. These are separate experiments rather than one continuous two-year return series.

Weights reset on the first available observation of each month. They use the preceding admissible curve, no more than seven calendar days old, to value risk at the intended trade date. Covariance endpoints are strictly earlier than the trade date. The current-date curve only prices the assumed trade and marks held assets. Between monthly decisions, positions stay fixed while cash flows, accrued interest and time to payment evolve.

Targets are held to maturity without replacement. Existing holders receive coupons/principal once before end-date marks and trades. Expired targets may remain as zero-value identifiers in the ledger. Expired hedges are removed from later decisions, and securities not yet issued are excluded. Prior-date logic prevents future observations entering the calculation; revised source history still prevents a point-in-time trading claim.

## Cash, costs and collateral

Establishment exchanges signed dirty PV for cash and charges transaction costs. Subsequent trades exchange only changed units at the current fitted dirty PV. Cost is 1 bp of absolute changed dirty notional, including initial establishment. Unchanged positions incur no trade cost. Short positions pay signed negative coupons/principal; maturity redemptions are cash flows, not automatically profits.

Positive total cash, including reserved short proceeds, earns an assumed nominal 2% annual rate. Negative cash incurs an assumed nominal 5% rate. Both compound daily as `(1 + rate / 365)^calendar_days`. Only actual coupon/principal/trade cash events reset the accumulation anchor, so mark frequency does not change carry. These are hypothetical cash/rebate and funding policies, not measured repo terms.

The ledger reserves full short dirty market value plus 2%, with zero haircut on fully paid long holdings. `free_cash = cash − required_collateral`. An unsecured limit of zero exposes negative-cash financing gaps. Collateral shortfall and financing flags are reported; the engine does not inject capital or automatically enforce liquidation. The optimizer's face limits do not themselves establish financing feasibility.

Every row reconciles `wealth = cash + dirty PV`, `dirty PV = clean PV + accrued`, and the signed cash roll. Floating-point residual tolerances are explicit and scale with representable precision. Failed ledger operations restore prior state. The saved verifier independently recomputes these identities and rejects extra external cash after inception.

## Separate validation evidence and publication controls

An independent module follows the Treasury auction price/yield convention and compares all 16 issue-date clean prices with the corresponding official six-decimal auction prices. The $0.000001-per-$100 tolerance is separate from the curve reconstruction tolerance. These checks test auction arithmetic and terms, without establishing current secondary-market prices.

Full repricing scenarios use parallel ±100 bp, long-end steepening and a front-end selloff. They measure immediate fitted price changes before funding and costs; the dated annual ledgers measure wealth with those items included. Neither result is realized execution performance. The [decision memo](DECISION_MEMO.md) reports cash-interest attribution so assumed carry is visible alongside net PnL.

The builder publishes `building` status first and replaces the report with a failure page on an exception. Successful outputs include JSON, HTML, charts, CSV rows and an artifact manifest. `verify_release.py` checks artifact hashes and current source/config/model fingerprints, reconstructed exposures/variance, constrained decisions, prior-date boundaries and cash identities. It reconstructs constrained feasibility and solves a fresh linear first-order optimality certificate for the latest hedge and every monthly decision, rather than trusting saved solver-status text.

The exported Excel pack provides live formulas for position scaling, exposures, covariance risk and limits under frozen per-$100 marks and risk inputs. Its historical ledger and original covariance/optimization evidence remain Python exports. Editing workbook faces does not regenerate dated history or certify a new optimized solution; repricing, covariance estimation, hedge optimization and ledger simulation run in Python. The Streamlit interface recomputes current analysis and shows saved dated history only when its source/config/model fingerprints match.

The legacy [RESEARCH_NOTE.md](RESEARCH_NOTE.md) documents a different undated frozen-cash-flow diagnostic and is not evidence for this dated release.
