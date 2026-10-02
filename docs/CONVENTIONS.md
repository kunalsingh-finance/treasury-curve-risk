# Dated Treasury conventions — v1.0.0

These conventions describe the implemented regular fixed-rate cash-Treasury model. They distinguish contractual terms, research curve inputs, model ownership rules and hypothetical financing assumptions.

## Instrument and source units

[treasury_instruments.json](../configs/treasury_instruments.json) records 16 actual Treasury CUSIPs across separate latest/historical sets. Coupon rates and official auction yields are annual decimal fractions. Each engine instrument has $100 face; portfolio units scale the supplied face and may be signed. Default target holdings are illustrative $2m, $4m and $6m allocations.

Actual issue date is the earliest allowed settlement/as-of. Dated date is the accrual origin and may precede issue date, including an original issue delayed by a holiday or a reopening. Each record links an official TreasuryDirect auction result. SHA-256 covers the retained selected-field transcription, not the source PDF bytes; it verifies consistency of the local text rather than independently authenticating the source.

## Curve conventions

The [Federal Reserve source](https://www.federalreserve.gov/data/nominal-yield-curve.htm) is a fitted off-the-run coupon-Treasury research curve. Bills, FRNs, on-the-run and first-off-the-run securities are excluded from the fit. It is a revisable staff product, so the pinned download is current-vintage history, not historical release-vintage evidence.

`BETA0`–`BETA3` are percentage-point rates; `TAU1`/`TAU2` are years. `SVENYxx` benchmarks are continuously compounded zero yields, converted to fractional rates for discounting. Coupon-equivalent par yields are a different series. The [official series table](https://www.federalreserve.gov/data/yield-curve-tables/feds200628_1.html) identifies compounding conventions.

Pricing uses `D(t) = exp(-zero_yield(t) × t)`. Time is actual calendar days from valuation to **adjusted payment**, divided by 365, including leap years: ACT/365F. This curve-time mapping is an explicit model choice and is separate from Treasury coupon-period accrual. A curve zero or forward quoted at final-payment tenor is not the bond's yield to maturity. Negative curve yields are allowed; overflow, underflow and nonfinite results reject.

## Coupon schedules, accrued interest and payments

The model supports fixed principal, fixed coupons and regular semiannual periods. It generates coupon dates backward from maturity, preserving a maturity-anchored end-of-month pattern. A regular period pays `face × annual coupon / 2`, and accrued interest is that coupon times elapsed actual days divided by actual days in the scheduled period. The dated date must be a regular boundary. Irregular first coupons/stubs are rejected.

Accrual uses **unadjusted** scheduled dates. It resets at each scheduled coupon boundary and stops at scheduled maturity. Thus a delayed final payment can remain in dirty PV with zero accrued interest. Payments move to the following Federal Reserve Bank business day without additional coupon interest. The underlying Treasury interest/payment rules are in [31 CFR 356, including Appendix B and §356.30](https://www.treasurydirect.gov/files/laws-and-regulations/auction-regulations-uoc/31-cfr-part-356.pdf).

The regular calendar supports 1986–2100. It includes weekends and Federal Reserve Bank holidays, with Juneteenth included from 2022. A Saturday holiday leaves the preceding Friday open; a Sunday holiday closes the following Monday. Good Friday remains bank-open. This follows the [Federal Reserve Bank clearing convention, §60.52](https://www.federalreserve.gov/aboutthefed/chapter-6-reporting-requirements.htm). Emergency closures and SIFMA trading hours are outside this calendar.

For example, an August 31 maturity generates February 29 in a leap year and August 31 again, without iterative date drift. A scheduled Sunday payment on May 15, 2022 moves to Monday May 16; it earns no extra coupon for that day. ACT/365F discount time extends to May 16 while accrued interest is zero from May 15.

## Settlement and coupon ownership

Marks are ex-payment under the declared model rule: `cashflows(as_of)` includes only adjusted payment dates **after** as-of. Existing ledger positions receive payments where `previous_as_of < payment_date <= current_as_of`, before current marks and trades. A new purchase on the payment date does not receive that payment. Initial purchases and rebalances require bank-business dates; marks may use calendar dates.

Accrued interest resets on the scheduled coupon date, independently of delayed cash payment. At an adjusted payment date after that boundary, the next period may already have accrued a small amount. This is a model entitlement convention, not a complete implementation of custody/ex-coupon rules or an actual settlement lag. Dirty fitted PV less accrued equals clean fitted PV; neither is presented as an executable quote.

After final adjusted payment, future flows, PV, accrued interest and rate sensitivities are zero. Duration/convexity are null when PV is zero. An existing expired identifier can remain in the ledger with unchanged units, but no second maturity receipt is generated; establishing a new nonzero expired position rejects.

## Risk and hedge units

Parallel and key-rate DV01 are dollars per 1 bp, obtained from centered ±1 bp price differences. The fixed key-rate nodes are 0.5, 1, 2, 3, 5, 7, 10, 20 and 30 years. Bumps apply to continuously compounded zero rates and interpolate linearly, with flat tails outside the endpoints. Node sensitivities sum approximately to parallel DV01 because the bump basis sums to one; finite differences retain numerical/nonlinear error.

Hedge weights are $100-face units, with a negative sign meaning short. The covariance matrix uses squared bp changes, so `residual DV01' × covariance × residual DV01` has dollar-squared units. The constrained solver's limits are gross absolute **hedge** face and per-hedge face, with target holdings excluded. Matching parallel DV01 does not eliminate convexity or every shape exposure.

## Independent auction price/yield benchmark

[benchmarks.py](../treasury_risk/benchmarks.py) computes auction prices independently of the curve and dated-bond engine. For non-indexed securities, it follows [31 CFR 356 Appendix B II.D](https://www.treasurydirect.gov/files/laws-and-regulations/auction-regulations-uoc/31-cfr-part-356.pdf): simple interest for the first fractional coupon period and semiannual compounding for later full periods.

With `w` equal to actual days to next coupon divided by days in its period, `j = annual auction yield / 2`, coupon `K`, and `n` full periods after next coupon:

`auction dirty price = [K + K × sum((1+j)^(-k), k=1..n) + face × (1+j)^(-n)] / (1+w×j)`

Subtract auction accrued interest for clean price. All 16 published issue-date prices match within $0.000001 per $100. This is an original-auction coupon-equivalent YTM check, separate from continuous-zero-curve discounting and current secondary-market marks.

## Ledger and financing assumptions

Signed dirty PV is exchanged against cash at inception and when holdings change. Existing long/short positions receive/pay signed coupon and principal cash. Cost is 1 bp of absolute dirty turnover at establishment and actual rebalances; unchanged units incur no trade cost. Each annual experiment supplies equal initial equity across methods: target dirty PV plus 10% of target face. No further external cash is added.

Positive total cash, including reserved proceeds, earns an assumed nominal 2% annual rate; negative cash is charged 5%. Rates compound daily with `(1 + rate / 365)^days`. Accumulation anchors change at actual cash events rather than report marks. Negative cash rates are supported only when their daily accumulation factor is positive. Reserved-proceeds interest is an assumed cash/rebate policy, not measured repo income.

Required collateral is full short dirty market value plus 2%, with zero haircut on fully paid longs. Free cash is total cash minus required collateral. The unsecured limit is zero. Collateral/funding breaches are explicit diagnostics, without presumed borrowing capacity, extra capital or automatic liquidation. Zero reported breaches under these assumptions establish model consistency, not secured-financing availability.

Dollar results are floating-point approximations. Cash-roll tolerance is `max($0.00000001, 64 × ulp(row scale))` and is saved per row. The independent verifier recomputes identities within twice that tolerance. Source zero-yield tolerance is 0.006 bp; auction-price tolerance is $0.000001 per $100. These are distinct checks with distinct units.

TIPS, FRNs, bills, derivatives, optionality, actual borrowing/repo terms, observed bid/ask spreads and emergency settlement closures are outside this release. The dated engine accounts for modeled cash holdings and risk without claiming actual transaction availability.
