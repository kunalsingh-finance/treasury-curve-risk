"""Export charts and a compact HTML decision report from a complete release pack."""
from __future__ import annotations

import html
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .curve import KEY_RATE_NODES
from .research import METHODS

COLORS = ("#9a6037", "#527796", "#9e86ab", "#187568")
LABELS = ("Unhedged", "Duration", "Unweighted buckets", "Constrained covariance")


def charts(pack: dict, output: Path) -> list[str]:
    output.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.spines.top": False,
                         "axes.spines.right": False, "figure.facecolor": "#f5f7f4", "axes.facecolor": "white"})
    latest = pack["latest"]
    inputs = latest["risk_inputs"]
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.4), layout="constrained")
    exposures = np.array(inputs["target_exposure"])
    matrix = np.array(inputs["hedge_matrix"])
    for method, color, label in zip(METHODS, COLORS, LABELS):
        residual = exposures + matrix @ np.array(latest["methods"][method]["weights"])
        axes[0].plot(range(9), residual, marker="o", color=color, label=label, linewidth=1.8)
    axes[0].axhline(0, color="#9aaa9f", linewidth=0.8)
    axes[0].set_xticks(range(9), [f"{node:g}" for node in KEY_RATE_NODES])
    axes[0].set(xlabel="Continuous-zero node, years", ylabel="Residual DV01, dollars / bp", title=f"Dated portfolio risk · {latest['date']}")
    axes[0].legend(fontsize=8)
    pca = latest["covariance"]["pca"]
    for i, color in enumerate(COLORS[:3]):
        axes[1].plot(range(9), pca["eigenvectors"][i], marker="o", color=color,
                     label=f"PC{i+1} · {pca['explained_variance_ratio'][i]:.1%} variance")
    axes[1].set_xticks(range(9), [f"{node:g}" for node in KEY_RATE_NODES])
    axes[1].set(xlabel="Zero-curve node, years", ylabel="Unit eigenvector loading", title="Prior-window curve factors")
    axes[1].legend(fontsize=8)
    filename = "risk_and_factors.png"
    fig.savefig(output / filename, dpi=160)
    plt.close(fig)
    files = [filename]
    for period in pack["periods"]:
        fig, axes = plt.subplots(1, 2, figsize=(13, 4.4), layout="constrained")
        for method, color, label in zip(METHODS, COLORS, LABELS):
            rows = [row for row in period["rows"] if row["method"] == method]
            from datetime import date
            dates = [date.fromisoformat(row["date"]) for row in rows]
            axes[0].plot(dates, [(row["wealth"] - period["initial_equity"]) / 1000 for row in rows], color=color, label=label)
            axes[1].plot(dates, [row["free_cash"] / 1000 for row in rows], color=color, label=label)
        axes[0].set(title=f"Dated model wealth · {period['start_date'][:4]}", ylabel="P&L versus starting equity, $ thousands")
        axes[1].set(title="Cash after short collateral reserve", ylabel="Free cash, $ thousands")
        for ax in axes:
            ax.axhline(0, color="#9aaa9f", linewidth=0.8)
            ax.tick_params(axis="x", labelrotation=25)
            ax.legend(fontsize=8)
        filename = f"wealth_{period['start_date'][:4]}.png"
        fig.savefig(output / filename, dpi=160)
        plt.close(fig)
        files.append(filename)
    return files


def render_release(pack: dict, chart_files: list[str]) -> str:
    def escape(value):
        return html.escape(str(value), quote=True)
    def money(value):
        return f"${value:,.2f}"
    def table(headers, rows):
        return "<div class='scroll'><table><thead><tr>" + "".join(f"<th>{escape(h)}</th>" for h in headers) + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{escape(x)}</td>" for x in row) + "</tr>" for row in rows) + "</tbody></table></div>"
    latest, constrained = pack["latest"], pack["latest"]["methods"]["constrained"]
    reduction = constrained["variance_reduction_pct"]
    reduction_text = "Unavailable" if reduction is None else f"{reduction:.2f}%"
    portfolio = table(["CUSIP", "Maturity", "Face", "Clean PV", "Accrued", "Dirty PV"],
                      [[r["cusip"], r["maturity_date"], money(r["face"]), money(r["clean_price"]), money(r["accrued_interest"]), money(r["dirty_price"])] for r in latest["portfolio"]])
    hedge_table = table(["CUSIP", "Maturity", "Constrained face"],
                        [[r["cusip"], r["maturity_date"], money(face)] for r, face in zip(latest["hedges"], constrained["face_amounts"])])
    stresses = table(["Curve shock", *LABELS], [[r["name"], *[money(r[m]) for m in METHODS]] for r in latest["scenarios"]])
    sections = []
    for period, filename in zip(pack["periods"], chart_files[1:]):
        summary = table(["Method", "Net model P&L", "Wealth change", "Max drawdown", "Collateral breaches", "Cash-roll error"],
                        [[label, money(s["net_pnl"]), f"{s['wealth_change_fraction']:.2%}", f"{s['maximum_drawdown']:.2%}",
                          s["collateral_breach_observations"], f"{s['max_cash_roll_residual']:.2e}"]
                         for method, label in zip(METHODS, LABELS) for s in [period["summaries"][method]]])
        sections.append(f"<section><h2>{period['start_date'][:4]} dated accounting evaluation</h2><p>{escape(period['rule'])}</p>"
                        f"<img src='{filename}' alt='Dated wealth and free cash charts'>{summary}</section>")
    audits = table(["CUSIP", "Official auction clean", "Independent calculation", "Difference / 100", "Pass"],
                   [[r["cusip"], f"{r['official_clean_price_per_100']:.6f}", f"{r['calculated_clean_price_per_100']:.6f}",
                     f"{r['error_per_100']:.8f}", r["passed"]] for r in pack["auction_benchmarks"]])
    exceptions = table(["Curve date", "Status", "Max difference, bp"],
                       [[r["date"], r["status"].replace("_", " "), "Unavailable" if r["max_absolute_error_bps"] is None else f"{r['max_absolute_error_bps']:.8f}"] for r in pack["source_audit"]["exceptions"]])
    limitations = "".join(f"<li>{escape(item)}</li>" for item in pack["limitations"])
    return f"""<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Treasury Curve Risk · Complete research release</title><style>
body{{font:15px/1.6 system-ui,sans-serif;background:#f4f6f1;color:#233f3b;margin:0}}main{{max-width:1200px;margin:auto;padding:40px 26px}}h1{{font:52px/1.12 Georgia,serif;max-width:900px}}h2{{font:29px Georgia,serif}}section{{background:white;border:1px solid #d5e0d8;padding:24px;margin-top:24px;border-radius:8px}}.eyebrow{{letter-spacing:.12em;text-transform:uppercase;font-size:12px}}.notice{{padding:16px;background:#fff3dc;border-left:4px solid #b88035}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:15px}}.metric{{background:#e5eee7;padding:20px;border-radius:6px}}.metric strong{{display:block;font-size:26px}}table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{padding:10px;border-bottom:1px solid #e0e8e1;text-align:right;white-space:nowrap}}td:first-child,th:first-child{{text-align:left}}th{{background:#eaf0e8}}.scroll{{overflow:auto}}img{{max-width:100%;height:auto}}a{{color:#1c7567}}code{{overflow-wrap:anywhere}}@media(max-width:700px){{.grid{{grid-template-columns:1fr}}h1{{font-size:36px}}}}
</style><main><div class='eyebrow'>Treasury research · Version {escape(pack['version'])}</div><h1>Treasury Curve, Bond Pricing and Hedge Engine</h1>
<p>Dated Treasury cash flows, audited coupon accounting and risk-constrained hedge decisions.</p>
<div class='notice'>Research release complete. Historical source review remains open for ten quarantined curve dates. Model prices and hypothetical execution are not current market quotes or realized investment performance.</div>
<div class='grid'><div class='metric'>Latest dated portfolio PV<strong>{money(latest['risk_inputs']['target_price'])}</strong>{latest['date']}</div>
<div class='metric'>Estimated covariance risk reduction<strong>{reduction_text}</strong>Prior-window variance, subject to bounds</div>
<div class='metric'>Parallel DV01 remaining<strong>{money(constrained['parallel_residual_dv01'])}</strong>Independent post-solver validation</div></div>
<section><h2>Actual Treasury terms, hypothetical holdings</h2>{portfolio}<p>Dirty PV = clean PV + accrued interest. Dates use regular semiannual schedules; payment calendars and accrual conventions are documented.</p>{hedge_table}</section>
<section><h2>Risk objective and curve factors</h2><img src='{chart_files[0]}' alt='Dated node exposures and prior-window PCA factors'><p>The constrained optimizer minimizes covariance-weighted risk, matches parallel DV01 and applies gross-face and instrument limits. Training ends on {latest['covariance']['training_end_date']}, before the valuation date. PCA factors describe the prior sample; they are not forecasts.</p></section>
<section><h2>Full repricing under rate shocks</h2>{stresses}<p>Scenario definitions match the original research report and JSON pack. All dated cash flows are repriced; these results precede hypothetical execution costs.</p></section>
{''.join(sections)}<section><h2>Independent Treasury auction price checks</h2><p>Coupon-equivalent price/yield conventions reproduce the published original auction prices. These reference prices do not validate today's fitted-curve mark.</p>{audits}</section>
<section><h2>Source audit and unresolved historical exceptions</h2><p>{pack['source_audit']['comparisons']:,} published zero-yield comparisons; threshold {pack['source_audit']['tolerance_bps']} bp remains fixed.</p>{exceptions}<p>Primary curve source: <a href='https://www.federalreserve.gov/data/nominal-yield-curve.htm'>Federal Reserve GSW</a>. Actual instrument sources and all acquisition metadata are preserved in the JSON pack and config. Raw curve SHA-256: <code>{escape(pack['source']['sha256'])}</code>.</p></section>
<section><h2>Scope and model assumptions</h2><ul>{limitations}</ul><p>Run the interactive portfolio interface with <code>python -m streamlit run app.py</code>. Use the risk workbook for linked exposures; regenerate Python outputs to recompute optimizations and dated history.</p></section></main></html>"""
