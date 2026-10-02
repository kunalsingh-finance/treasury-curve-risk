# Initial hedge comparison

This note describes the initial current-vintage research run. It is not a trading-performance result or a recommendation to transact.

## Source controls

Original CSV: [Federal Reserve GSW nominal curve](https://www.federalreserve.gov/data/yield-curve-tables/feds200628.csv), acquired October 2, 2026. The recorded SHA-256 is `379cae673092e64aea0baa58796b276ebc5c9c7f2c342d66e7b1fe1e52fd0421` and the byte count is 16,650,134. Acquisition metadata and exact numerical results are in `outputs/analysis.json`.

The working sample begins January 3, 2000 and ends September 25, 2026. The loader counts 17,033 original dated rows, 10,058 outside the requested range and 287 rows with missing parameters. It retains 6,688 finite, valid parameter rows without filling missing rates.

All 200,610 available published zero yields are compared with the reconstructed Svensson curve. The 0.006 bp threshold is fixed before evaluating the source and is not expanded to accommodate mismatches. Published four-decimal-percent yield rounding explains up to 0.005 bp, but 71 comparisons on nine dates exceed that allowance. The largest difference is 0.03796499 bp. Their cause remains unresolved; no claim that they are Fed errors is made.

| Date | Maximum difference, bp | Disposition |
| --- | ---: | --- |
| 2002-01-16 | 0.01444200 | Quarantined |
| 2003-05-20 | 0.00859790 | Quarantined |
| 2003-05-21 | 0.01277907 | Quarantined |
| 2007-10-09 | 0.00708623 | Quarantined |
| 2008-03-21 | Unavailable | All 30 published zero yields missing; quarantined |
| 2008-05-28 | 0.00732624 | Quarantined |
| 2008-07-16 | 0.02925735 | Quarantined |
| 2009-10-26 | 0.01176297 | Quarantined |
| 2013-10-16 | 0.03796499 | Quarantined |
| 2013-11-22 | 0.01050315 | Quarantined |

Raw observations and exception rows remain visible. Valuation and replay dates must pass their available published benchmarks; a failure on any required date stops the analysis and invalidates prior generated outputs. All required dates in this run pass. This validates curve reconstruction, rather than real security prices.

## Hypothetical portfolio and hedges

Target holdings are $4 million face of an 8-year 3.5% coupon bond, $6 million of a 12-year 4.25% bond and $2 million of a 25-year 4.75% bond. They have regular semiannual schedules on a coupon date. None is represented as an observed CUSIP or an actual security position.

Five 4% coupon hedge instruments have 2-, 5-, 10-, 20- and 30-year tenors. The duration comparator matches separately calculated parallel DV01 using the 10-year instrument. The multi-instrument comparator minimizes the unweighted nine-bucket Euclidean norm. Both use theoretical signed cash-bond holdings.

On the September 25, 2026 curve, the target PV is approximately $10.815 million and parallel DV01 is $10,098.60. The duration hedge leaves a bucket norm of $4,621.61 per bp; the multi-instrument hedge leaves $1,504.60. The latter is 27.16% of the original bucket norm. Its five-column exposure matrix has rank five and condition number 5.09; it does not span the nine risk buckets.

| Curve shock | Unhedged PV change | Duration-hedged change | Multi-instrument-hedged change |
| --- | ---: | ---: | ---: |
| Parallel +100 bp | -$950,840 | $13,529 | -$125,932 |
| Parallel -100 bp | $1,075,148 | $16,776 | $132,194 |
| Long-end steepening | -$941,284 | -$26,667 | -$94,830 |
| Front-end selloff | -$205,482 | $32,904 | -$44,759 |

The exact nodal shocks are saved in the JSON pack and displayed in the report. Cash flows are fully repriced, including signed hedge positions. These changes omit financing, initial proceeds, coupon realization and execution.

## Interpretation and next research question

The duration hedge produces smaller absolute price changes for all four chosen shocks. The multi-instrument hedge achieves its stated bucket-norm objective but leaves more aggregate parallel exposure. The four scenarios are descriptive checks, not an exhaustive set of possible moves or a statistical estimate of future risk.

The 2022 check freezes hedge amounts at the first valid curve and reprices identical starting cash-flow tenors on 249 subsequent curves. Its maximum absolute versus-start changes are approximately $3.524 million unhedged, $259,970 with the duration hedge and $441,638 with the multi-instrument hedge. Maturities do not age, coupons are not realized and the source can revise old observations. This is a curve-move diagnostic, not a historical investment return.

The next research question is whether a constrained, covariance-weighted hedge can control parallel exposure and relevant curve shapes within stated financing and gross-face limits. That requires a frozen evaluation design and additional historical periods before making a performance claim.
