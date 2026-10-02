"""Standalone HTML evidence report for hypothetical Treasury curve risk."""

from __future__ import annotations

from datetime import date
import html
import json
import math
from urllib.parse import urlsplit


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _format(value: object, places: int = 2, *, dollars: bool = False) -> str:
    number = _number(value)
    if number is None:
        return '<span class="muted">Unavailable</span>'
    sign = "−" if number < 0 else ""
    return f"{sign}{'$' if dollars else ''}{abs(number):,.{places}f}"


def _count(value: object) -> int | None:
    if isinstance(value, (list, tuple)):
        return len(value)
    number = _number(value)
    return int(number) if number is not None and number >= 0 and number.is_integer() else None


def _at(values: object, index: int) -> object:
    return values[index] if isinstance(values, (list, tuple)) and index < len(values) else None


def _source_link(value: object, label: str) -> str:
    if not isinstance(value, str) or any(ord(character) < 32 for character in value):
        return ""
    try:
        parsed = urlsplit(value)
        allowed = (parsed.scheme == "https" and parsed.hostname in {"www.federalreserve.gov", "federalreserve.gov"}
                   and parsed.port in {None, 443} and parsed.username is None and parsed.password is None)
    except ValueError:
        return ""
    return f'<a href="{_escape(value)}" rel="noopener noreferrer">{_escape(label)}</a>' if allowed else ""


def _table(headers: list[str], rows: list[list[str]], empty: str) -> str:
    if not rows:
        return f'<p class="empty">{_escape(empty)}</p>'
    head = "".join(f"<th>{_escape(value)}</th>" for value in headers)
    body = "".join("<tr>" + "".join(f"<td>{value}</td>" for value in row) + "</tr>" for row in rows)
    return f'<div class="table-scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _chart(series: list[tuple[str, str, list[tuple[float, float]]]], chart_id: str,
           title: str, *, historical: bool = False) -> str:
    points = [point for _, _, values in series for point in values]
    if not points:
        return '<p class="empty">No complete finite observations are available for this chart.</p>'
    x_min, x_max = min(x for x, _ in points), max(x for x, _ in points)
    y_min, y_max = min(y for _, y in points), max(y for _, y in points)
    if historical:
        y_min, y_max = min(y_min, 0), max(y_max, 0)
    if x_min == x_max:
        x_min, x_max = x_min - 0.5, x_max + 0.5
    span = y_max - y_min
    padding = span * 0.12 if span else max(abs(y_min) * 0.12, 0.1)
    y_min, y_max = y_min - padding, y_max + padding
    if not all(math.isfinite(value) for value in (x_min, x_max, y_min, y_max, x_max - x_min, y_max - y_min)):
        return '<p class="empty">Chart range exceeds finite plotting capacity.</p>'
    left, right, top, bottom = 90.0, 936.0, 20.0, 286.0

    def x_position(value: float) -> float:
        return left + (value - x_min) / (x_max - x_min) * (right - left)

    def y_position(value: float) -> float:
        return bottom - (value - y_min) / (y_max - y_min) * (bottom - top)

    elements = []
    for index in range(5):
        value = y_min + (y_max - y_min) * index / 4
        position = y_position(value)
        label = f"{value:,.0f}" if historical else f"{value:.2f}%"
        elements.append(f'<line class="grid-line" x1="{left}" x2="{right}" y1="{position:.2f}" y2="{position:.2f}"/>'
                        f'<text class="tick" x="{left - 12}" y="{position + 4:.2f}" text-anchor="end">{_escape(label)}</text>')
    if historical and y_min <= 0 <= y_max:
        position = y_position(0)
        elements.append(f'<line class="zero-line" x1="{left}" x2="{right}" y1="{position:.2f}" y2="{position:.2f}"/>')
    for index in range(5):
        value = x_min + (x_max - x_min) * index / 4
        if historical:
            label = date.fromordinal(round(value)).isoformat()
        else:
            label = f"{value:g}y"
        elements.append(f'<text class="tick" x="{x_position(value):.2f}" y="310" text-anchor="middle">{_escape(label)}</text>')
    for _, color, values in series:
        if len(values) == 1:
            x_value, y_value = values[0]
            elements.append(f'<circle cx="{x_position(x_value):.2f}" cy="{y_position(y_value):.2f}" r="4" fill="{color}"/>')
        elif values:
            coordinates = " ".join(f"{x_position(x_value):.2f},{y_position(y_value):.2f}" for x_value, y_value in values)
            elements.append(f'<polyline points="{coordinates}" fill="none" stroke="{color}" stroke-width="2.6" stroke-linejoin="round"/>')
    legend = "".join(f'<span><i style="background:{color}"></i>{_escape(label)}</span>' for label, color, values in series if values)
    return (f'<div class="chart"><svg viewBox="0 0 960 326" role="img" aria-labelledby="{chart_id}" xmlns="http://www.w3.org/2000/svg">'
            f'<title id="{chart_id}">{_escape(title)}</title>{"".join(elements)}</svg></div><div class="legend">{legend}</div>')


def _curve_chart(rows: list[dict]) -> str:
    points = []
    for row in rows:
        x_value, y_value = _number(row.get("maturity_years")), _number(row.get("zero_yield_percent"))
        if x_value is not None and x_value >= 0 and y_value is not None:
            points.append((x_value, y_value))
    return _chart([("Continuously compounded zero yield", "#1a7868", sorted(points))], "zero-curve-title", "Latest fitted Treasury zero curve")


def _history_chart(rows: list[dict]) -> str:
    definitions = (("Unhedged", "#b36b38", "unhedged_change"),
                   ("Parallel hedge", "#617c9c", "duration_hedged_change"),
                   ("Key-rate hedge", "#1a7868", "multi_hedged_change"))
    series = []
    for label, color, field in definitions:
        points = []
        for row in rows:
            try:
                x_value = float(date.fromisoformat(row["date"]).toordinal())
            except (KeyError, TypeError, ValueError):
                continue
            y_value = _number(row.get(field))
            if y_value is not None:
                points.append((x_value, y_value))
        series.append((label, color, sorted(points)))
    return _chart(series, "history-title", "Fixed-cashflow historical price changes", historical=True)


def render_report(pack: dict) -> str:
    """Render supplied results without network calls, scripts or external assets."""
    latest, history = pack.get("latest", {}), pack.get("historical", {})
    source, benchmark = pack.get("source", {}), pack.get("benchmark", {})
    multi, parallel = latest.get("multi_hedge", {}), latest.get("duration_hedge", {})
    comparisons, failed = _count(benchmark.get("comparisons")), _count(benchmark.get("failed_comparisons"))
    exceptions = benchmark.get("exceptions", [])
    quarantined, admissible = _count(benchmark.get("quarantined_observations")), _count(benchmark.get("admissible_observations"))
    audit_requires_review = bool(exceptions) or bool(quarantined) or benchmark.get("audit_status") not in {None, "pass"}
    status = "Yield benchmarks pass" if comparisons and failed == 0 and not audit_requires_review else "Benchmark review required"
    status_class = "badge" if status == "Yield benchmarks pass" else "badge review"
    exception_rows = [[_escape(row.get("date", "Unavailable")), _format(_count(row.get("comparisons")), 0),
                       _format(row.get("max_absolute_error_bps"), 8),
                       _escape(str(row.get("status", "Unavailable")).replace("_", " "))] for row in exceptions]
    nodes, instruments = latest.get("key_rate_nodes", []), latest.get("hedge_instruments", [])
    portfolio_rows = [[_escape(row.get("label", "")), _format(row.get("maturity_years")), _format(row.get("coupon_percent")),
                       _format(row.get("face"), dollars=True), _format(row.get("price"), dollars=True),
                       _format(row.get("dv01"), dollars=True), _format(row.get("duration"), 3), _format(row.get("convexity"), 3)]
                      for row in latest.get("portfolio", [])]
    risk_rows = [[_format(node), _format(_at(latest.get("target_key_rate_dv01"), index), dollars=True),
                  _format(_at(parallel.get("residual_exposure"), index), dollars=True),
                  _format(_at(multi.get("residual_exposure"), index), dollars=True)] for index, node in enumerate(nodes)]
    face_rows = [[_escape(row.get("label", "")), _format(row.get("maturity_years")), _format(row.get("coupon_percent")),
                  _format(row.get("face"), dollars=True), _format(row.get("price"), dollars=True),
                  _format(_at(parallel.get("face_amounts"), index), dollars=True),
                  _format(_at(multi.get("face_amounts"), index), dollars=True)] for index, row in enumerate(instruments)]
    scenario_rows = [[_escape(row.get("name", "")), _format(row.get("unhedged_change"), dollars=True),
                      _format(row.get("duration_hedged_change"), dollars=True), _format(row.get("multi_hedged_change"), dollars=True)]
                     for row in latest.get("scenarios", [])]
    shock_rows = []
    for row in latest.get("scenarios", []):
        definition = row.get("shock_definition", {})
        if not isinstance(definition, dict):
            definition = {}
        bumps = definition.get("key_rate_bumps_bps")
        if isinstance(bumps, dict):
            ordered = sorted(bumps.items(), key=lambda item: (_number(item[0]) is None, _number(item[0]) or 0, str(item[0])))
            description = "; ".join(f"{_escape(node)}y: {_format(value)} bp" for node, value in ordered)
            description = description or "No explicit key-rate bumps"
        else:
            description = '<span class="muted">Unavailable</span>'
        shock_rows.append([_escape(row.get("name", "")), _format(definition.get("parallel_bps")), description,
                           _escape(definition.get("interpolation", "Unavailable"))])
    frozen_rows = [[_escape(row.get("label", "")), _format(_at(history.get("duration_face_amounts"), index), dollars=True),
                    _format(_at(history.get("multi_face_amounts"), index), dollars=True)] for index, row in enumerate(instruments)]
    quality_rows = []
    for key, value in pack.get("data_quality", {}).items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value, sort_keys=True, ensure_ascii=False)
        quality_rows.append([_escape(str(key).replace("_", " ").capitalize()), _escape(value)])
    source_links = " · ".join(link for link in (_source_link(source.get("source_page"), "Federal Reserve methodology"),
                                               _source_link(source.get("source_url"), "Captured source series")) if link)
    condition = _number(multi.get("condition_number"))
    condition_text = _escape(f"{condition:.3g}") if condition is not None else "Unavailable / singular"
    rank = _count(multi.get("matrix_rank"))
    rank_text = f"{rank} / {len(instruments)}" if rank is not None else "Unavailable"
    diagnosis = ("Numerically rank deficient" if multi.get("rank_deficient") else
                 "Unique least-squares weights" if multi.get("unique_weights") else "Weights may be nonunique")
    if multi.get("ill_conditioned"):
        diagnosis += "; conditioning requires attention"
    limitations = "".join(f"<li>{_escape(value)}</li>" for value in pack.get("limitations", []))
    title = _escape(pack.get("title", "Treasury Curve, Bond Pricing and Hedge Engine"))
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f3f4ef;color:#1a3438;font:15px/1.6 system-ui,-apple-system,Segoe UI,sans-serif}}main{{max-width:1280px;margin:auto;padding:30px 28px 64px}}a{{color:#166655;text-underline-offset:3px}}.masthead{{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #c8d5ce;padding-bottom:20px;gap:20px}}.brand,.eyebrow{{font-size:11px;letter-spacing:.16em;text-transform:uppercase;font-weight:750}}nav{{display:flex;gap:20px;font-size:13px}}h1{{font:500 clamp(32px,5vw,58px)/1.1 Georgia,serif;max-width:960px;margin:22px 0}}h2{{font:500 29px/1.2 Georgia,serif;margin:8px 0 15px}}h3{{font-size:16px;margin:0 0 8px}}p{{margin:10px 0}}.intro{{max-width:860px;color:#526667;font-size:17px}}.hero{{padding:30px 0 8px}}.badge{{display:inline-block;background:#dcebe1;color:#24604f;border:1px solid #bfdbca;padding:6px 11px;border-radius:3px;font-size:12px;font-weight:700}}.metrics{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:15px;margin:25px 0}}.metric,.panel{{background:#fff;border:1px solid #d5ded7;border-radius:7px;padding:22px}}.metric strong{{display:block;font-size:27px;font-weight:650;line-height:1.3}}.metric span{{display:block;color:#526667;font-size:12px;margin-top:5px}}.metric small{{display:block;color:#71817e;font-size:11px;margin-top:5px}}.section{{margin-top:35px}}.section-heading{{display:flex;justify-content:space-between;align-items:flex-end;gap:20px}}.section-heading p{{color:#637675;font-size:13px}}.two-column{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}.notice{{border-left:3px solid #b98347;background:#fff7e9;padding:16px 20px;margin:18px 0;color:#634e34}}.small,.muted{{font-size:12px;color:#647573}}.empty{{padding:20px;color:#6c7b77;background:#f7f8f4;border-radius:4px}}.table-scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:12px 10px;text-align:right;border-bottom:1px solid #e5eae3;font-variant-numeric:tabular-nums;white-space:nowrap}}th{{font-size:11px;line-height:1.3;color:#567069;background:#f1f5ef;font-weight:750}}th:first-child,td:first-child{{text-align:left}}tbody tr:last-child td{{border-bottom:0}}tbody tr:hover{{background:#fafbf8}}.chart svg{{display:block;width:100%;height:auto;min-width:480px}}.chart{{overflow-x:auto}}.tick{{font:11px system-ui,sans-serif;fill:#667a75}}.grid-line{{stroke:#e0e7df;stroke-width:1}}.zero-line{{stroke:#8ca29a;stroke-dasharray:4 4;stroke-width:1}}.legend{{display:flex;flex-wrap:wrap;gap:22px;font-size:12px;color:#4d6660;margin:8px 0}}.legend span{{display:flex;align-items:center;gap:7px}}.legend i{{width:22px;height:3px;display:inline-block}}.method-note{{max-width:980px;color:#576c67;font-size:13px}}.provenance{{word-break:break-word}}code{{font-size:11px}}ul{{padding-left:21px}}li{{margin:8px 0}}footer{{margin-top:38px;border-top:1px solid #cbd8ce;padding-top:20px;color:#63746e;font-size:12px}}@media(max-width:850px){{.metrics{{grid-template-columns:repeat(2,minmax(0,1fr))}}.two-column{{grid-template-columns:1fr}}.masthead{{align-items:flex-start}}nav{{flex-wrap:wrap;gap:10px}}}}@media(max-width:520px){{main{{padding:18px 15px 40px}}.masthead{{display:block}}nav{{margin-top:12px}}.metric,.panel{{padding:16px}}.metric strong{{font-size:22px}}.section-heading{{display:block}}}}
.badge.review{{background:#fff0d5;color:#73501e;border-color:#e3c99c}}
</style></head><body><main>
<header class="masthead"><div class="brand">Treasury / Curve Research</div><nav><a href="#benchmarks">Benchmarks</a><a href="#curve">Curve</a><a href="#hedges">Hedges</a><a href="#history">Historical check</a><a href="#sources">Sources</a></nav></header>
<section class="hero"><div class="eyebrow">Observed curves · Hypothetical bond risk</div><h1>{title}</h1>
<p class="intro">{_escape(pack.get('scope', 'Fit Treasury curve risk to explicit bond cash flows, then measure the risk remaining after a hedge.'))}</p>
<span class="{status_class}">{status}</span><p class="small">Latest curve observation: {_escape(latest.get('date', 'Unavailable'))}</p></section>
<div class="metrics"><div class="metric"><strong>{_format(latest.get('target_price'), dollars=True)}</strong><span>Hypothetical portfolio price</span></div>
<div class="metric"><strong>{_format(latest.get('target_dv01'), dollars=True)}</strong><span>Portfolio parallel DV01 / 1bp</span></div>
<div class="metric"><strong>{_format(multi.get('residual_norm'), dollars=True)}</strong><span>Key-rate hedge residual norm / 1bp</span><small>Unweighted Euclidean bucket norm</small></div>
<div class="metric"><strong>{_format(benchmark.get('max_absolute_error_bps'), 4)}</strong><span>Largest source-yield difference, bp</span><small>{comparisons if comparisons is not None else 'Unavailable'} comparisons · tolerance {_format(benchmark.get('tolerance_bps'), 4)} bp</small></div></div>
<div class="notice"><strong>Hypothetical bonds, current-vintage curve data.</strong> These are model cash flows, not quoted Treasury securities. Historical source observations may be revised. Prices exclude accrued interest, financing, transaction costs and execution.</div>
<section id="benchmarks" class="section panel"><div class="section-heading"><div><div class="eyebrow">Source audit / Exceptions retained</div><h2>Source yield benchmark review</h2></div><p>{_format(quarantined, 0)} quarantined observations · {_format(admissible, 0)} admissible observations</p></div>
<p>The latest valuation date ({_escape(latest.get('date', 'Unavailable'))}) and every historical diagnostic date ({_escape(history.get('start_date', 'Unavailable'))} to {_escape(history.get('end_date', 'Unavailable'))}) must pass the unchanged {_format(benchmark.get('tolerance_bps'), 4)} bp tolerance. Older exceptions listed below are quarantined and excluded from valuations and the plotted diagnostic.</p>
<p class="small">Full-source audit: {_escape(benchmark.get('audit_status', 'Unavailable'))}. Comparisons outside tolerance: {_format(failed, 0)}. Missing published yields remain unavailable; they are not inferred or counted as passes.</p>
{_table(['Quarantined date','Available comparisons','Largest error, bp','Source audit status'], exception_rows, 'No source exception rows were supplied.')}</section>
<section id="curve" class="section panel"><div class="section-heading"><div><div class="eyebrow">01 / The curve</div><h2>Latest fitted zero curve</h2></div><p>Yield in percent · maturity in years</p></div>{_curve_chart(latest.get('zero_curve', []))}
<p class="method-note">Continuously compounded zero yields discount each specified cash flow. The fitted Federal Reserve staff curve describes off-the-run nominal Treasury data; it does not supply executable prices for these hypothetical bonds.</p></section>
<section class="section panel"><div class="eyebrow">02 / Cash flows</div><h2>Portfolio priced on the latest curve</h2>
{_table(['Bond','Maturity, years','Coupon, %','Face','Model price','DV01 / 1bp','Effective duration','Convexity'], portfolio_rows, 'No portfolio bonds were supplied.')}
<p class="small">Price and DV01 refer to the face amount shown. Effective duration and convexity use symmetric parallel curve shocks.</p></section>
<section id="hedges" class="section"><div class="section-heading"><div><div class="eyebrow">03 / Residual risk</div><h2>What the hedges leave exposed</h2></div><p>Dollar change per 1bp · positive values represent long rate exposure</p></div>
<div class="metrics"><div class="metric"><strong>{rank_text}</strong><span>Matrix rank / hedge instruments</span><small>{len(nodes)} curve-risk nodes</small></div>
<div class="metric"><strong>{condition_text}</strong><span>Unregularized matrix condition number</span><small>{_escape(diagnosis)}</small></div>
<div class="metric"><strong>{_format(multi.get('gross_absolute_face_units'), dollars=True)}</strong><span>Key-rate hedge gross absolute face</span><small>Theoretical long and short cash-bond units</small></div>
<div class="metric"><strong>{_format(multi.get('parallel_residual_dv01'), dollars=True)}</strong><span>Key-rate hedge parallel DV01 remaining</span><small>Repriced parallel measure, separate from bucket sum</small></div></div>
<div class="two-column"><div class="panel"><h3>Bucket exposures</h3>{_table(['Node, years','Unhedged, $/bp','Parallel hedge, $/bp','Key-rate hedge, $/bp'], risk_rows, 'No risk bucket exposures were supplied.')}</div>
<div class="panel"><h3>Two different hedge objectives</h3><p>A parallel hedge matches total parallel DV01 with one designated instrument. A key-rate hedge minimizes the unweighted norm of residual node exposures, with any supplied ridge penalty applied to instrument weights.</p>
<p>A small residual for this portfolio does not establish that the instruments span all possible curve moves. Rank, conditioning and gross face show constraints that a residual norm alone can hide.</p>
<p class="small">Parallel hedge residual norm: {_format(parallel.get('residual_norm'), dollars=True)} / 1bp. Parallel DV01 remaining: {_format(parallel.get('parallel_residual_dv01'), dollars=True)} / 1bp.</p>
<p class="small">Ridge penalty: {_format(multi.get('ridge_penalty'), 4)}. Matrix diagnostics describe the original exposure matrix.</p></div></div></section>
<section class="section panel"><h2>Theoretical hedge face amounts</h2>{_table(['Instrument','Maturity, years','Coupon, %','Pricing face','Model price','Parallel hedge face','Key-rate hedge face'], face_rows, 'No hedge instruments were supplied.')}
<p class="small">Negative face amounts represent short cash bonds; weights are expressed per 100 face units. Financing, borrow availability and execution are outside this calculation.</p></section>
<section class="section panel"><div class="eyebrow">04 / Repricing</div><h2>Scenario price changes</h2>{_table(['Curve shock','Unhedged change','Parallel hedge change','Key-rate hedge change'], scenario_rows, 'No stress scenarios were supplied.')}
<p class="small">These are scenario repricing changes, not confidence intervals or promised protection. Nonlinear moves can leave risk after a local DV01 hedge.</p>
<details><summary>Review exact scenario shocks</summary>{_table(['Scenario','Parallel shift, bp','Explicit key-rate bumps by node','Shape convention'], shock_rows, 'No scenario shock definitions were supplied.')}
<p class="small">Node years identify the supplied key-rate bump locations. The parallel shift applies across the curve in addition to those bumps.</p></details></section>
<section id="history" class="section panel"><div class="section-heading"><div><div class="eyebrow">05 / Historical curve check</div><h2>Fixed-cashflow price changes</h2></div><p>{_escape(history.get('start_date', 'Unavailable'))} → {_escape(history.get('end_date', 'Unavailable'))}<br>{_count(history.get('observations')) if _count(history.get('observations')) is not None else 'Unavailable'} observations · changes in dollars</p></div>
<p class="method-note">{_escape(history.get('label', 'Historical curves reprice a fixed cash-flow schedule.'))} Each plotted price change is relative to the starting curve on {_escape(history.get('start_date', 'the first observation'))}; it is not a daily change.</p>{_history_chart(history.get('rows', []))}
<div class="notice"><strong>This is not an actual return or trading P/L series.</strong> Maturities do not age and coupons are not realized. Each historical curve reprices the same fixed cash-flow schedules with frozen hedge face amounts.</div>
<details><summary>Frozen historical hedge face amounts</summary>{_table(['Instrument','Parallel hedge face','Key-rate hedge face'], frozen_rows, 'No frozen historical hedge amounts were supplied.')}</details></section>
<section id="sources" class="section two-column"><div class="panel provenance"><div class="eyebrow">06 / Evidence</div><h2>Source and vintage</h2>
<p>{_escape(source.get('model_type', 'Federal Reserve fitted nominal Treasury curve'))}</p><p>{_escape(source.get('data_vintage', 'Downloaded current vintage; historical observations may be revised.'))}</p>
<p>{source_links or 'No approved Federal Reserve source link was supplied.'}</p><p class="small">Captured: {_escape(source.get('captured_at', 'Unavailable'))}<br>Raw source SHA-256: <code>{_escape(source.get('sha256', 'Unavailable'))}</code></p>
<h3>Data checks</h3>{_table(['Check','Observed result'], quality_rows, 'No data-quality diagnostics were supplied.')}</div>
<div class="panel"><h2>Scope and limitations</h2><ul>{limitations or '<li>No additional limitations were supplied.</li>'}</ul>
<p class="small">Yield comparisons validate curve reconstruction against supplied source benchmarks. They do not establish trading performance, a predictive holdout, or a complete Treasury valuation convention.</p></div></section>
<footer>Independent research implementation. Current-vintage source data, hypothetical cash flows and theoretical hedge units; no investment or execution conclusion.</footer>
</main></body></html>'''
