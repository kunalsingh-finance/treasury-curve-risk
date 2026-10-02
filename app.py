"""Interactive local Treasury portfolio, scenario and source-review interface."""
from pathlib import Path
import json

import numpy as np
import pandas as pd
import streamlit as st

from treasury_risk.analysis import benchmark_curves
from treasury_risk.curve import KEY_RATE_NODES
from treasury_risk.data import load_gsw, validate_source_manifest
from treasury_risk.research import latest_analysis, load_instrument_config, METHODS
from scripts.verify_release import load_verified_release

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="Treasury Curve Risk", page_icon="📈", layout="wide")
st.markdown("""<style>.block-container{padding-top:2rem;max-width:1450px}h1{font-family:Georgia,serif;font-weight:500}
[data-testid=stMetric]{border:1px solid #d9e4dc;padding:16px;border-radius:8px;background:#f1f7f3}
[data-testid=stSidebar]{background:#edf3ed}</style>""", unsafe_allow_html=True)
st.caption("TREASURY RESEARCH / DATED CASH FLOWS / CONSTRAINED RISK")
st.title("Treasury Curve & Hedge Engine")
st.write("Price sourced Treasury cash flows, size bounded hedges and inspect what remains exposed.")


@st.cache_data(show_spinner=False)
def source_data(source_hash: str, instrument_hash: str):
    records, quality = load_gsw(ROOT / "data/raw/feds200628.csv")
    audit = benchmark_curves(records, strict=False)
    config = load_instrument_config(ROOT / "configs/treasury_instruments.json")
    return records, quality, audit, config


try:
    # Verify current bytes on every rerun; cache only the expensive derived work.
    import hashlib
    source = validate_source_manifest(ROOT / "data/raw/feds200628.csv", ROOT / "data/raw/source_manifest.json")
    config_hash = hashlib.sha256((ROOT / "configs/treasury_instruments.json").read_bytes()).hexdigest()
    records, quality, audit, config = source_data(source["sha256"], config_hash)
except Exception as error:
    st.error(f"Source validation failed: {error}")
    st.stop()

with st.sidebar:
    st.header("Portfolio controls")
    earliest = max(item["issue_date"] for group in ("targets", "hedges") for item in config["latest"][group])
    rejected = {item["date"] for item in audit["exceptions"]}
    dates = [item["date"] for item in records if item["date"] >= earliest and item["date"] not in rejected]
    valuation = st.selectbox("Valuation date", dates, index=len(dates)-1, key="valuation")
    faces = []
    for index, (item, default) in enumerate(zip(config["latest"]["targets"], (2.0, 4.0, 6.0))):
        faces.append(st.number_input(f"{item['cusip']} face ($m)", min_value=0.1, max_value=100.0,
                                     value=default, step=0.5, key=f"face_{index}") * 1_000_000)
    lookback = st.selectbox("Prior observations for covariance", (63, 126, 252), index=2, key="lookback")
    gross = st.slider("Gross hedge face / target face", 0.1, 5.0, 2.0, 0.1, key="gross")
    position = st.slider("Each hedge face / target face", 0.1, 3.0, 1.5, 0.1, key="position")
    cash_neutral = st.checkbox("Require zero hedge trade cash", value=False, key="cash_neutral")
    st.caption("Zero trade cash can make the constraints infeasible. The engine withholds failed solutions.")

try:
    with st.spinner("Repricing dated cash flows and solving the constrained hedge…"):
        result = latest_analysis(records, config, audit, faces=faces, valuation_date=valuation,
                                 lookback=lookback, gross_multiple=gross, position_multiple=position,
                                 cash_neutral=cash_neutral)
except Exception as error:
    st.error(f"Analysis unavailable: {error}")
    st.stop()

solution = result["methods"]["constrained"]
columns = st.columns(4)
columns[0].metric("Dated portfolio dirty PV", f"${result['risk_inputs']['target_price']:,.0f}")
columns[1].metric("Portfolio parallel DV01", f"${result['risk_inputs']['target_parallel_dv01']:,.2f} / bp")
columns[2].metric("Solver status", solution["status"])
columns[3].metric("Source curves quarantined", audit["quarantined_observations"])
st.caption("Actual CUSIP terms, hypothetical face allocations and fitted-curve prices. Source vintage is revised; prices are not live quotes.")

release, release_verification, release_error = None, None, None
try:
    release, release_verification = load_verified_release(ROOT / "outputs/release")
except Exception as error:
    release_error = str(error)

tabs = st.tabs(["Portfolio & hedges", "Curve shocks", "Dated history", "Evidence"])
with tabs[0]:
    st.subheader("Clean price, accrued interest and cash-flow risk")
    st.dataframe(pd.DataFrame(result["portfolio"]), hide_index=True, width="stretch")
    if solution.get("weights") is None:
        st.error(f"No valid constrained hedge: {solution['message']}")
    else:
        reduction = solution['variance_reduction_pct']
        reduction_text = "unavailable for zero estimated variance" if reduction is None else f"{reduction:.2f}%"
        st.success(f"Constrained hedge verified; prior-window covariance variance reduction {reduction_text}.")
        hedges = pd.DataFrame(result["hedges"])
        for method in METHODS[1:]:
            hedges[f"{method}_face"] = [weight * 100 for weight in result["methods"][method]["weights"]]
        st.dataframe(hedges, hide_index=True, width="stretch")
        target = np.array(result["risk_inputs"]["target_exposure"])
        matrix = np.array(result["risk_inputs"]["hedge_matrix"])
        exposures = pd.DataFrame({method: target + matrix @ np.array(result["methods"][method]["weights"])
                                  for method in METHODS}, index=KEY_RATE_NODES)
        exposures.index.name = "Zero-rate maturity (years)"
        st.line_chart(exposures, y_label="Residual DV01 ($ / bp)")
        st.write(f"Gross hedge face ${solution['gross_absolute_face_units']:,.0f}; parallel residual "
                 f"${solution['parallel_residual_dv01']:.8f} per bp.")
        st.caption(f"Covariance window: {result['covariance']['training_start_date']} to "
                   f"{result['covariance']['training_end_date']}, strictly before {valuation}.")
        with st.expander("Solver verification and remaining capacities"):
            st.json(solution)
with tabs[1]:
    st.subheader("Full repricing under rate shocks")
    st.dataframe(pd.DataFrame(result["scenarios"]), hide_index=True, width="stretch")
    st.caption("Parallel ±100 bp, prescribed long-end steepening and front-end selloff; dollar price changes before costs.")
with tabs[2]:
    if release is None:
        st.warning("Build the verified dated-history release with python scripts/build_release.py.")
        st.caption(f"Saved release unavailable: {release_error}")
    else:
        period_index = st.selectbox("Historical evaluation year", [p["start_date"][:4] for p in release["periods"]], key="period")
        period = next(p for p in release["periods"] if p["start_date"].startswith(period_index))
        frame = pd.DataFrame(period["rows"])
        wealth = frame.pivot(index="date", columns="method", values="wealth") - period["initial_equity"]
        st.line_chart(wealth, y_label="Dated model net P&L ($)")
        st.dataframe(pd.DataFrame(period["summaries"]).T, width="stretch")
        st.caption("Historical controls are fixed in the release and do not follow the current sidebar inputs. Monthly weights "
                   "use prior curves. Coupons, principal, capital buffer, collateral and assumed cash/funding rates are accounted for. "
                   "Current-vintage model wealth is not realized trading performance.")
        st.download_button("Download dated cash ledger", data=frame.to_csv(index=False),
                           file_name=f"treasury_ledger_{period_index}.csv", mime="text/csv")
with tabs[3]:
    st.subheader("Source quality and independent convention checks")
    st.write(f"{audit['comparisons']:,} source-yield comparisons, unchanged {audit['tolerance_bps']} bp tolerance.")
    st.dataframe(pd.DataFrame(audit["exceptions"]), hide_index=True, width="stretch")
    st.json(quality)
    st.markdown("[Federal Reserve curve methodology](https://www.federalreserve.gov/data/nominal-yield-curve.htm)")
    st.caption("Original auction-price benchmarks verify Treasury price/yield conventions. They do not establish today's CUSIP market price.")
    if release is not None:
        st.dataframe(pd.DataFrame(release["auction_benchmarks"]), hide_index=True, width="stretch")
        st.json(release_verification)
    st.download_button("Download current portfolio analysis", json.dumps(result, indent=2, allow_nan=False),
                       file_name="treasury_portfolio.json", mime="application/json")
