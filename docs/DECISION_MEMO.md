# Treasury hedge decision memo — dated v1.0.0

**Research date:** September 25, 2026. **Evidence:** October 2, 2026 pinned curve capture and the saved [dated release](../outputs/release/release.json). Dollar amounts below are rounded; the JSON and ledger CSVs retain calculation precision.

Use the constrained covariance hedge as the release's reference risk-control method. It has the smallest absolute repricing changes in the four prescribed latest-date shocks and passes its parallel-DV01 and face-limit checks. Treat the positive historical model PnL as assumption-dependent wealth accounting: hypothetical cash interest accounts for nearly all of the 2022 constrained gain and exceeds its 2023 gain. The results support an auditable hedge analysis, with no alpha or executable-trading claim.

## Portfolio and risk decision

The latest illustrative portfolio holds three actual Treasury notes:

| CUSIP | Maturity | Annual coupon | Hypothetical face |
| --- | --- | ---: | ---: |
| 91282CKZ3 | 2027-07-15 | 4.375% | $2m |
| 91282CLJ8 | 2031-08-31 | 3.750% | $4m |
| 91282CKQ3 | 2034-05-15 | 4.375% | $6m |

Target fitted dirty PV is **$11,637,354**, with parallel DV01 of **$5,631.16 per bp**. The constrained objective minimizes residual variance estimated from 252 observed changes spanning September 22, 2025–September 24, 2026. All training levels precede valuation. The estimate uses 10% covariance shrinkage and an independently checked convex optimality gap.

| Hedge CUSIP | Maturity | Signed face selected |
| --- | --- | ---: |
| 91282CNM9 | 2028-07-15 | −$1,738,731 |
| 91282CLM1 | 2031-09-30 | −$4,743,482 |
| 91282CNT4 | 2035-08-15 | −$5,255,725 |
| 912810UN6 | 2045-08-15 | +$484,840 |
| 912810UM8 | 2055-08-15 | −$115,257 |

Negative face denotes short holdings. Gross hedge face is **$12,338,036**, below the $24m limit; each holding is below its $18m absolute limit. Calculated parallel residual is zero at saved precision, and prior-window estimated variance falls **98.99%**. Zero hedge trade cash is not imposed; the static solution has approximately $10.866m net fitted sale proceeds. Those proceeds require collateral in the dated accounting model.

The unweighted hedge has a smaller node-exposure norm: $1,789 per bp versus $1,842 for the constrained hedge. It minimizes a different objective and does not impose parallel equality. This comparison makes the choice of risk metric visible rather than assuming that one scalar hedge score establishes universal protection.

## Prescribed shocks: immediate price changes

These are full fitted-curve repricings of dated cash flows before financing and transaction costs, in dollars. They are four selected shocks, not a probability distribution or exhaustive stress set.

| Curve shock | Unhedged | Duration hedge | Unweighted hedge | Constrained hedge |
| --- | ---: | ---: | ---: | ---: |
| Parallel +100 bp | −$545,806 | −$5,452 | −$88,735 | −$155 |
| Parallel −100 bp | +$581,229 | −$5,992 | +$94,734 | −$177 |
| Long-end steepening | −$404,164 | +$74,032 | −$52,649 | +$6,438 |
| Front-end selloff | −$212,067 | −$59,700 | −$42,284 | +$810 |

The constrained hedge has the smallest absolute change in this set, while residual convexity and shape exposure remain. The apparent precision of a ±1 bp parallel match does not imply zero price change for a 100 bp move.

## Dated historical wealth

Historical securities differ from the latest set. Each year restarts the same hypothetical $2m/$4m/$6m target allocation and supplies equal starting equity across methods: target fitted dirty PV plus a $1.2m buffer. Starting equity is $12,947,193 in 2022 and $11,628,693 in 2023. These are separate annual experiments, without a continuous two-year capital path.

Monthly hedge weights use the preceding admissible curve and a strictly prior covariance window. Current-date fitted PV prices trades and marks positions. Targets age, pay dated coupons/principal and remain held until maturity without replacement. The October 2023 target maturity therefore changes the remaining portfolio; the following hedge maturity also generates its signed cash flow once.

| Method | 2022 net model PnL | 2022 maximum drawdown | 2023 net model PnL | 2023 maximum drawdown |
| --- | ---: | ---: | ---: | ---: |
| Unhedged | −$1,214,411 | −11.708% | +$416,899 | −4.701% |
| Duration | +$131,666 | −0.600% | +$335,616 | −0.197% |
| Unweighted | +$167,666 | −0.201% | +$244,384 | −0.019% |
| Constrained | +$260,770 | −0.018% | +$224,789 | −0.018% |

PnL is final cash plus dirty PV less starting equity, after modeled transaction costs. Drawdown uses sampled marked wealth, so it does not measure unobserved intraday or between-observation losses. The constrained hedge produces much smaller sampled drawdowns, while its net PnL is not the highest in 2023. Risk protection and return ranking are separate outcomes.

## Financing drives the positive constrained result

The release assumes nominal 2% annual interest on positive total cash, including reserved short proceeds, and 5% on debit cash, compounded daily. Initial and changed-position trades incur 1 bp of absolute dirty turnover. Required short collateral is full dirty market value plus 2%; fully paid longs have no haircut reserve. There is no additional external funding after inception.

| Constrained accounting item | 2022 | 2023 |
| --- | ---: | ---: |
| Net model PnL | +$260,770 | +$224,789 |
| Recorded hypothetical cash interest | +$259,577 | +$235,389 |
| Net PnL less recorded cash interest | +$1,193 | −$10,600 |
| Signed coupon cash | −$13,347 | −$13,531 |
| Signed principal cash | $0 | −$265,386 |
| Transaction costs | $2,426 | $2,236 |
| Funding cost | $0 | $0 |

Cash interest is approximately 99.5% of the 2022 constrained gain and 104.7% of the 2023 gain. Subtracting the recorded interest is an accounting attribution, **not a counterfactual rerun with zero rates**. Principal cash can be negative because short redemptions exceed long principal receipts; principal itself is not return.

All four methods have zero sampled collateral-breach and financing-gap observations under the declared buffer/reserve policy. This does not establish actual secured financing or security-lending availability. Interest on restricted proceeds is a hypothetical rebate assumption; no observed repo rates, dealer fills, borrow charges or bid/ask validation support an execution claim.

## Evidence quality and conclusion

The source audit contains 200,610 zero-yield comparisons across 6,688 parameter-valid curves. Ten older dates remain unresolved and quarantined: nine reconstruction exceptions and one unavailable benchmark. The tolerance is unchanged at 0.006 bp. Required latest/history observations are admissible; the full-source audit still requires review.

Separately, all 16 official issue-date auction-price calculations pass within $0.000001 per $100 face, with maximum absolute error about $0.000000478. These checks validate Treasury auction price/yield arithmetic. They do not validate fitted current CUSIP market prices.

The [Federal Reserve data](https://www.federalreserve.gov/data/nominal-yield-curve.htm) are revisable. Prior-date model logic is implemented and verified, but this source is not a historical point-in-time archive. Historical choices of instruments, notionals and policy settings are illustrative rather than documented as a live precommitted trading mandate. Two selected years and four prescribed shocks do not establish out-of-sample alpha.

The release's credible result is a reproducible chain from source checks and dated security terms to constrained exposures, then to signed coupons, principal, costs, cash and collateral diagnostics. The [methodology](METHODOLOGY.md) explains the rules and the [conventions](CONVENTIONS.md) records their boundaries. The older [RESEARCH_NOTE.md](RESEARCH_NOTE.md) remains an undated frozen-cash-flow diagnostic and should not be used to interpret these dated wealth results.
