import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const outputDir = path.join(root, 'outputs', '01a0fad9-107c-7cd2-b374-8b14defca53a');
const runtimeRequire = createRequire(path.join(outputDir, 'package.json'));
const { Workbook, SpreadsheetFile } = await import(pathToFileURL(runtimeRequire.resolve('@oai/artifact-tool')).href);
const releaseBytes = await fs.readFile(path.join(root, 'outputs', 'release', 'release.json'));
const releaseSha256 = createHash('sha256').update(releaseBytes).digest('hex');
const data = JSON.parse(releaseBytes.toString('utf8'));
const config = JSON.parse(await fs.readFile(path.join(root, 'configs', 'treasury_instruments.json'), 'utf8'));
const latest = data.latest;
const ri = latest.risk_inputs;
const nodes = latest.covariance.key_rate_nodes;
const portfolio = latest.portfolio;
const hedges = latest.hedges;
const method = latest.methods.constrained;
const periods = data.periods;
const matrix = ri.target_matrix;
const parallelTargets = ri.target_parallel_dv01_per_100;
const assert = (condition, message) => { if (!condition) throw new Error(message); };
assert(nodes.length === 9 && portfolio.length === 3 && hedges.length === 5, 'Expected nine risk nodes, three targets and five hedges');
assert(matrix?.length === 9 && matrix.every(row => row.length === 3 && row.every(Number.isFinite)), 'Per-100 target_matrix is required for editable face risk');
assert(parallelTargets?.length === 3 && parallelTargets.every(Number.isFinite), 'Per-100 target parallel DV01 values are required');
assert(method.weights?.length === 5 && method.weights.every(Number.isFinite), 'Certified constrained hedge positions are required');

const wb = Workbook.create();
const sheets = Object.fromEntries(['Summary', 'Risk', 'Inputs', 'Ledger', 'Audit'].map(name => [name, wb.worksheets.add(name)]));
const colors = { navy: '#172D46', teal: '#376875', pale: '#EDF2F7', blue: '#0000FF', green: '#008000', amber: '#FFF2CC', red: '#C00000', gray: '#697586' };
const num = '#,##0.00;(#,##0.00);"-"';
const money = '"$"#,##0;("$"#,##0);"-"';
const pct = '0.0%;(0.0%);"-"';
const precise = '0.000000;(0.000000);"-"';
const date = iso => new Date(`${iso}T00:00:00Z`);
const col = index => { let text = ''; for (let n = index + 1; n > 0; n = Math.floor((n - 1) / 26)) text = String.fromCharCode(65 + (n - 1) % 26) + text; return text; };
function value(sheet, address, value_) { sheet.getRange(address).values = [[value_]]; }
function formula(sheet, address, formula_, cross = false) { sheet.getRange(address).formulas = [[formula_]]; sheet.getRange(address).format.font.color = cross ? colors.green : '#000000'; }
function block(sheet, row, column, values) { if (values.length) sheet.getRangeByIndexes(row - 1, column - 1, values.length, values[0].length).values = values; }
function header(sheet, range) { sheet.getRange(range).format = { fill: colors.navy, font: { name: 'Arial', size: 10, bold: true, color: '#FFFFFF' }, horizontalAlignment: 'center', verticalAlignment: 'center', rowHeight: 28 }; sheet.getRange(range).format.borders = { insideVertical: { style: 'thin', color: '#FFFFFF' } }; }
function section(sheet, address, text, endColumn = 'L') { value(sheet, address, text); const row = Number(address.match(/\d+/)[0]); sheet.getRange(`C${row}:${endColumn}${row}`).format = { fill: colors.pale, font: { name: 'Arial', size: 10, bold: true, color: colors.navy }, rowHeight: 23 }; }
function title(sheet, text, lastRow, lastColumn = 'O') {
  sheet.showGridLines = false;
  sheet.getRange(`A1:${lastColumn}${lastRow}`).format = { font: { name: 'Arial', size: 10, color: '#172D46' }, verticalAlignment: 'center', rowHeight: 21 };
  sheet.getRange(`A1:B${lastRow}`).format.columnWidth = 2;
  sheet.getRange(`C1:${lastColumn}${lastRow}`).format.columnWidth = 14;
  value(sheet, 'C2', text);
  sheet.getRange('C2').format.font = { name: 'Arial', size: 16, bold: true, color: colors.navy };
  sheet.getRange(`C3:${lastColumn}3`).format.borders = { bottom: { style: 'thin', color: colors.teal } };
  sheet.getRange(`C2:${lastColumn}2`).format.rowHeight = 30;
}
function note(sheet, address, text, width = 80) { value(sheet, address, text); sheet.getRange(address).format.font = { name: 'Arial', size: 10, italic: true, color: colors.gray }; sheet.getRange(address).format.wrapText = false; }
function total(sheet, range) { sheet.getRange(range).format.font.bold = true; sheet.getRange(range).format.borders = { top: { style: 'thin', color: colors.navy } }; }
function warn(sheet, range, expression) { sheet.getRange(range).conditionalFormats.addCustom(expression, { fill: '#FCE4D6', font: { color: colors.red, bold: true } }); }

// Inputs own sourced per-100 marks, risk vectors and the editable allocations.
const inputs = sheets.Inputs;
title(inputs, 'Portfolio and risk inputs', 123, 'N');
inputs.getRange('C4:D4').values = [['Curve valuation date', date(latest.date)]];
inputs.getRange('D4').setNumberFormat('mm/dd/yy');
value(inputs, 'C5', 'Fitted continuous zero curve'); value(inputs, 'M5', data.source.source_page ?? data.source.source_url);
note(inputs, 'C6', 'Blue numbers are inputs. Amber faces are editable. Prices and risk vectors remain fixed at the curve date.');
section(inputs, 'C8', 'Target holdings and curve-implied prices', 'K');
block(inputs, 9, 3, [['CUSIP', 'Security', 'Face (USD)', 'Coupon', 'Maturity', 'Dirty / 100', 'Clean / 100', 'Accrued / 100', 'Price basis']]);
header(inputs, 'C9:K9');
block(inputs, 10, 3, portfolio.map(item => [item.cusip, item.label, item.face, item.coupon_rate, date(item.maturity_date), item.dirty_price * 100 / item.face, item.clean_price * 100 / item.face, item.accrued_interest * 100 / item.face, 'GSW fitted curve']));
block(inputs, 10, 13, portfolio.map(item => [item.source_url ?? config.latest.targets.find(x => x.cusip === item.cusip)?.source_url]));
note(inputs, 'C14', 'Actual Treasury terms. Holdings are hypothetical. Source links document terms, not current market prices.');
section(inputs, 'C17', 'Position controls', 'K');
block(inputs, 18, 3, [['Covariance multiplier', 1], ['Gross hedge face limit', latest.constraints.gross_face_limit], ['Single hedge face limit', latest.constraints.position_face_limit]]);
inputs.getRange('D18:D20').format.font.color = colors.blue;
inputs.getRange('D18:D20').format.fill = colors.amber;
inputs.getRange('D19:D20').setNumberFormat(money);
inputs.getRange('D18').setNumberFormat('0.00"x"');
section(inputs, 'C22', 'Constrained hedge positions', 'K');
block(inputs, 23, 3, [['CUSIP', 'Security', 'Signed face (USD)', 'Coupon', 'Maturity', 'Dirty / 100', 'Position units', 'Absolute face', 'Price basis']]);
header(inputs, 'C23:K23');
block(inputs, 24, 3, hedges.map((item, i) => [item.cusip, item.label.replace(/; about (\d+) years remaining/, ' ($1Y remaining)'), method.face_amounts?.[i] ?? method.weights[i] * 100, item.coupon_rate, date(item.maturity_date), item.dirty_price_per_100, null, null, 'GSW fitted curve']));
block(inputs, 24, 13, hedges.map(item => [config.latest.hedges.find(x => x.cusip === item.cusip)?.source_url]));
for (let r = 24; r <= 28; r++) { formula(inputs, `I${r}`, `=E${r}/100`); formula(inputs, `J${r}`, `=ABS(E${r})`); }
note(inputs, 'C30', 'Negative face is a short position. Editing faces changes exposure formulas and limits; it does not rerun the optimizer.');
note(inputs, 'C31', 'Regenerate the Python release to reestimate covariance, optimize positions, reprice cash flows or rerun history.');
section(inputs, 'C34', 'Frozen covariance window', 'L');
block(inputs, 35, 3, [['First level date', date(latest.covariance.training_start_date)], ['Last level date', date(latest.covariance.training_end_date)], ['Observed changes', latest.covariance.change_count], ['Shrinkage', latest.covariance.shrinkage]]);
inputs.getRange('D35:D36').setNumberFormat('mm/dd/yy'); inputs.getRange('D38').setNumberFormat(pct);
note(inputs, 'F35', 'Successive observed curve changes, measured in squared basis points.');
note(inputs, 'F36', 'Current revised source vintage. Frozen covariance is strictly before the decision date.');
section(inputs, 'C41', 'Per-100 key-rate DV01 (USD per 1 bp)', 'M');
block(inputs, 42, 3, [['Node (years)', ...portfolio.map(x => x.cusip)]]);
block(inputs, 42, 8, [hedges.map(x => x.cusip)]);
header(inputs, 'C42:F42'); header(inputs, 'H42:L42');
block(inputs, 43, 3, nodes.map((node, i) => [node, ...matrix[i]]));
block(inputs, 43, 8, ri.hedge_matrix);
value(inputs, 'C54', 'Parallel / 100'); block(inputs, 54, 4, [parallelTargets]); block(inputs, 54, 8, [ri.hedge_parallel_dv01]);
section(inputs, 'C57', 'Covariance matrix (bp squared per observed change)', 'L');
block(inputs, 58, 3, [['Node (years)', ...nodes]]); header(inputs, 'C58:L58');
block(inputs, 59, 3, nodes.map((node, i) => [node, ...latest.covariance.covariance[i]]));
section(inputs, 'C70', 'Principal components of the unshrunk sample covariance', 'F');
block(inputs, 71, 3, [['Component', 'Eigenvalue', 'Variance share', 'Cumulative share']]); header(inputs, 'C71:F71');
block(inputs, 72, 3, latest.covariance.pca.eigenvalues.map((v, i) => [i + 1, v, latest.covariance.pca.explained_variance_ratio[i], null]));
for (let r = 72; r <= 80; r++) formula(inputs, `F${r}`, `=SUM($E$72:E${r})`);
inputs.getRange('E72:F80').setNumberFormat(pct);
section(inputs, 'C83', 'Independent Treasury auction price inputs', 'M');
block(inputs, 84, 3, [['CUSIP', 'Settlement', 'Official clean / 100', 'Calculated clean / 100', 'Accrued / 100', 'Tolerance / 100']]); header(inputs, 'C84:H84');
block(inputs, 85, 3, data.auction_benchmarks.map(item => [item.cusip, date(item.settlement_date), item.official_clean_price_per_100, item.calculated_clean_price_per_100, item.calculated_accrued_interest_per_100, item.tolerance_per_100]));
block(inputs, 85, 13, data.auction_benchmarks.map(item => [item.source_url]));
note(inputs, 'C103', 'Auction YTM validation uses Treasury Appendix B simple first-period interest and semiannual full periods.');
note(inputs, 'C104', 'Auction prices validate the independent convention implementation. They do not validate latest curve-implied market marks.');
section(inputs, 'C107', 'Curve source snapshot', 'L');
block(inputs, 108, 3, [['Vintage', data.source.data_vintage], ['Captured (UTC)', new Date(data.source.captured_at)], ['Bytes', data.source.bytes], ['SHA-256', data.source.sha256], ['Source URL', data.source.source_url]]);
inputs.getRange('D109').setNumberFormat('mm/dd/yy hh:mm');
inputs.getRange('E10:J12').format.font.color = colors.blue; inputs.getRange('E24:H28').format.font.color = colors.blue;
inputs.getRange('D43:F51').format.font.color = colors.blue; inputs.getRange('H43:L51').format.font.color = colors.blue;
inputs.getRange('D54:F54').format.font.color = colors.blue; inputs.getRange('H54:L54').format.font.color = colors.blue;
inputs.getRange('D59:L67').format.font.color = colors.blue;
inputs.getRange('E10:E12').format.fill = colors.amber; inputs.getRange('E24:E28').format.fill = colors.amber;
inputs.getRange('E10:E12').setNumberFormat(money); inputs.getRange('E24:E28').setNumberFormat(money);
inputs.getRange('F10:F12').setNumberFormat('0.000%'); inputs.getRange('F24:F28').setNumberFormat('0.000%');
inputs.getRange('G10:G12').setNumberFormat('mm/dd/yy'); inputs.getRange('G24:G28').setNumberFormat('mm/dd/yy');
inputs.getRange('H10:J12').setNumberFormat('0.0000'); inputs.getRange('H24:H28').setNumberFormat('0.0000');
inputs.getRange('D43:F54').setNumberFormat(precise); inputs.getRange('H43:L54').setNumberFormat(precise);
inputs.getRange('D59:L67').setNumberFormat('0.0000'); inputs.getRange('D85:D100').setNumberFormat('mm/dd/yy'); inputs.getRange('E85:H100').setNumberFormat(precise);
inputs.getRange('C1:C123').format.columnWidth = 19; inputs.getRange('D1:D123').format.columnWidth = 30; inputs.getRange('E1:E123').format.columnWidth = 20;
inputs.getRange('M1:M123').format.columnWidth = 100;
inputs.getRange('F42:L42').format.columnWidth = 18;
inputs.getRange('F71').format.wrapText = true; inputs.getRange('C71:F71').format.rowHeight = 30;
inputs.getRange('E84:H84').format.wrapText = true; inputs.getRange('C84:H84').format.rowHeight = 33;
inputs.getRange('D72:D80').setNumberFormat('0.000000'); inputs.getRange('J24:J28').setNumberFormat(money); inputs.getRange('I24:I28').setNumberFormat(num);
inputs.getRange('D108:D112').format.columnWidth = 30;
inputs.freezePanes.freezeRows(9); inputs.freezePanes.freezeColumns(4); inputs.tabColor = '#8EA6B8';

// Risk rebuilds target and hedge exposures from editable signed faces.
const risk = sheets.Risk;
title(risk, 'Key-rate exposure and variance', 49, 'O');
note(risk, 'C4', 'DV01 is dollar loss for a +1 bp yield change. Residual exposure includes the signed hedge positions.');
block(risk, 7, 3, [['Node (years)', 'Target DV01', ...hedges.map(x => x.cusip), 'Residual DV01', 'Covariance × residual', 'Variance contribution', null, 'Covariance × target', 'Target variance contribution']]);
header(risk, 'C7:O7');
for (let i = 0; i < 9; i++) {
  const r = i + 8, inputRow = i + 43, covCol = col(i + 3);
  value(risk, `C${r}`, nodes[i]);
  formula(risk, `D${r}`, `='Inputs'!D${inputRow}*'Inputs'!$E$10/100+'Inputs'!E${inputRow}*'Inputs'!$E$11/100+'Inputs'!F${inputRow}*'Inputs'!$E$12/100`, true);
  for (let h = 0; h < 5; h++) formula(risk, `${col(h + 4)}${r}`, `='Inputs'!${col(h + 7)}${inputRow}*'Inputs'!$E$${h + 24}/100`, true);
  formula(risk, `J${r}`, `=SUM(D${r}:I${r})`);
  formula(risk, `K${r}`, `=SUMPRODUCT('Inputs'!${covCol}$59:${covCol}$67,$J$8:$J$16)`, true);
  formula(risk, `L${r}`, `=J${r}*K${r}`);
  formula(risk, `N${r}`, `=SUMPRODUCT('Inputs'!${covCol}$59:${covCol}$67,$D$8:$D$16)`, true);
  formula(risk, `O${r}`, `=D${r}*N${r}`);
}
section(risk, 'C19', 'Risk and position totals', 'J');
block(risk, 20, 3, [['Target parallel DV01'], ['Hedge parallel DV01'], ['Residual parallel DV01'], ['Gross hedge face'], ['Gross face limit'], ['Largest absolute hedge face'], ['Single-position limit'], ['Gross limit excess'], ['Single-position limit excess']]);
formula(risk, 'D20', "='Inputs'!D54*'Inputs'!E10/100+'Inputs'!E54*'Inputs'!E11/100+'Inputs'!F54*'Inputs'!E12/100", true);
formula(risk, 'D21', "='Inputs'!H54*'Inputs'!E24/100+'Inputs'!I54*'Inputs'!E25/100+'Inputs'!J54*'Inputs'!E26/100+'Inputs'!K54*'Inputs'!E27/100+'Inputs'!L54*'Inputs'!E28/100", true);
formula(risk, 'D22', '=SUM(D20:D21)'); formula(risk, 'D23', "=SUM('Inputs'!J24:J28)", true);
formula(risk, 'D24', "='Inputs'!D19", true); formula(risk, 'D25', "=MAX('Inputs'!J24:J28)", true); formula(risk, 'D26', "='Inputs'!D20", true);
formula(risk, 'D27', '=MAX(0,D23-D24)'); formula(risk, 'D28', '=MAX(0,D25-D26)');
section(risk, 'C31', 'Frozen-window variance sensitivity', 'J');
block(risk, 32, 3, [['Target variance (USD squared)'], ['Residual variance (USD squared)'], ['Variance reduction'], ['Covariance multiplier'], ['Target step standard deviation'], ['Residual step standard deviation']]);
formula(risk, 'D32', '=SUM(O8:O16)'); formula(risk, 'D33', '=SUM(L8:L16)'); formula(risk, 'D34', '=IF(D32=0,"n.a.",1-D33/D32)'); formula(risk, 'D35', "='Inputs'!D18", true);
formula(risk, 'D36', '=IF(D35<0,"n.a.",SQRT(MAX(0,D32*D35)))'); formula(risk, 'D37', '=IF(D35<0,"n.a.",SQRT(MAX(0,D33*D35)))');
note(risk, 'C39', 'Variance uses the frozen shrunk covariance and linear key-rate exposure. The multiplier scales covariance, not positions.');
note(risk, 'C40', 'The observation step can include weekends and holidays. This is a model forecast, not a daily return estimate.');
risk.getRange('C1:C49').format.columnWidth = 35; risk.getRange('D1:J49').format.columnWidth = 17;
risk.getRange('K1:L49').format.columnWidth = 23; risk.getRange('M1:M49').format.columnWidth = 3; risk.getRange('N1:O49').format.columnWidth = 25;
risk.getRange('K7:O7').format.wrapText = true; risk.getRange('C7:O7').format.rowHeight = 33; risk.getRange('M7').format.fill = '#FFFFFF';
risk.getRange('D8:J16').setNumberFormat(num); risk.getRange('K8:O16').setNumberFormat(num); risk.getRange('D20:D28').setNumberFormat(num); risk.getRange('D32:D37').setNumberFormat(num); risk.getRange('D34').setNumberFormat(pct); risk.getRange('D35').setNumberFormat('0.00"x"');
warn(risk, 'D27:D28', 'D27>0.01'); warn(risk, 'D35', 'D35<0'); total(risk, 'C22:D22'); total(risk, 'C33:D34'); risk.tabColor = colors.teal;

// Ledger retains every Python research row. Its summaries remain tied to the frozen export.
const ledger = sheets.Ledger;
const ledgerRows = periods.flatMap(period => period.rows.map(row => ({ period: period.start_date.slice(0, 4), ...row })));
const ledgerKeys = ['period', 'date', 'method', ...Object.keys(ledgerRows[0]).filter(k => !['period', 'date', 'method'].includes(k))];
const ledgerEndCol = col(ledgerKeys.length + 1);
const ledgerStart = 7, ledgerEnd = ledgerStart + ledgerRows.length - 1;
const ledgerSummaryHeader = ledgerEnd + 4, ledgerSummaryStart = ledgerSummaryHeader + 1;
const summaryRows = periods.flatMap(period => Object.entries(period.summaries).map(([name, summary]) => ({ period, name, summary })));
title(ledger, 'Dated cash and financing ledger', ledgerSummaryStart + summaryRows.length, ledgerEndCol);
note(ledger, 'C4', 'Frozen Python export. Excel face edits do not rerun this history. Regenerate the release for new holdings, funding or hedges.');
note(ledger, 'C5', 'Year summaries are presented on Summary. Funding assumptions are on Inputs. Monthly decisions use strictly prior curves.');
section(ledger, `C${ledgerSummaryHeader - 1}`, 'Historical model-wealth summaries', 'O');
block(ledger, ledgerSummaryHeader, 3, [['Year', 'Method', 'Start', 'End', 'Initial equity', 'Final wealth', 'Net P&L', 'Wealth change', 'Max drawdown', 'Transaction cost', 'Funding cost', 'Collateral exceptions', 'Financing gaps']]); header(ledger, `C${ledgerSummaryHeader}:O${ledgerSummaryHeader}`);
block(ledger, ledgerSummaryStart, 3, summaryRows.map(({ period, name, summary }) => [Number(period.start_date.slice(0, 4)), name, date(period.start_date), date(period.end_date), summary.initial_equity, summary.final_wealth, null, null, summary.maximum_drawdown, summary.accounting_totals.cumulative_transaction_cost, summary.accounting_totals.cumulative_funding_cost, summary.collateral_breach_observations, summary.financing_gap_observations]));
for (let r = ledgerSummaryStart; r < ledgerSummaryStart + summaryRows.length; r++) { formula(ledger, `I${r}`, `=H${r}-G${r}`); formula(ledger, `J${r}`, `=I${r}/G${r}`); }
ledger.getRange(`E${ledgerSummaryStart}:F${ledgerSummaryStart + summaryRows.length - 1}`).setNumberFormat('mm/dd/yy'); ledger.getRange(`G${ledgerSummaryStart}:I${ledgerSummaryStart + summaryRows.length - 1}`).setNumberFormat(money); ledger.getRange(`J${ledgerSummaryStart}:K${ledgerSummaryStart + summaryRows.length - 1}`).setNumberFormat(pct); ledger.getRange(`L${ledgerSummaryStart}:M${ledgerSummaryStart + summaryRows.length - 1}`).setNumberFormat(money);
section(inputs, 'C115', 'Frozen ledger funding and execution assumptions', 'L');
block(inputs, 116, 3, [['Year', 'Cash rate', 'Funding rate', 'Trading cost (bp)', 'Short margin', 'Long haircut', 'Unsecured limit', 'Day-count basis']]); header(inputs, 'C116:J116');
block(inputs, 117, 3, periods.map(period => { const p = period.funding_policy; return [Number(period.start_date.slice(0, 4)), p.cash_rate_annual, p.funding_rate_annual, p.transaction_cost_bps, p.short_margin_rate, p.long_haircut_rate, p.unsecured_limit, p.day_count_basis]; }));
inputs.getRange('D117:E118').setNumberFormat(pct); inputs.getRange('G117:H118').setNumberFormat(pct);
inputs.getRange('C116:J116').format.wrapText = true; inputs.getRange('C116:J116').format.rowHeight = 34;
note(inputs, 'C120', 'Assumed funding and execution. Revised fitted curves. Historical model wealth is not realized trading performance.');
block(ledger, 6, 3, [ledgerKeys.map(k => k.replaceAll('_', ' '))]); header(ledger, `C6:${ledgerEndCol}6`);
block(ledger, ledgerStart, 3, ledgerRows.map(row => ledgerKeys.map(key => key === 'date' ? date(row[key]) : row[key] ?? null)));
ledger.getRange(`D${ledgerStart}:D${ledgerEnd}`).setNumberFormat('mm/dd/yy');
ledger.getRange(`F${ledgerStart}:${ledgerEndCol}${ledgerEnd}`).setNumberFormat(num);
ledger.getRange(`C1:C${ledgerEnd}`).format.columnWidth = 12; ledger.getRange(`D1:D${ledgerEnd}`).format.columnWidth = 16; ledger.getRange(`E1:E${ledgerEnd}`).format.columnWidth = 16;
ledger.getRange(`F1:${ledgerEndCol}${ledgerEnd}`).format.columnWidth = 20;
ledger.getRange(`C6:${ledgerEndCol}6`).format.wrapText = true; ledger.getRange(`C6:${ledgerEndCol}6`).format.rowHeight = 52;
const residualColumn = col(ledgerKeys.indexOf('cash_roll_residual') + 2), toleranceColumn = col(ledgerKeys.indexOf('audit_tolerance') + 2);
ledger.getRange(`${residualColumn}${ledgerStart}:${toleranceColumn}${ledgerEnd}`).setNumberFormat('0.00E+00');
ledger.freezePanes.freezeRows(6); ledger.freezePanes.freezeColumns(5);

// Summary presents the live exposure build and frozen year results without reading Audit.
const summary = sheets.Summary;
title(summary, 'Treasury portfolio risk pack', 48, 'L'); summary.tabColor = colors.navy;
value(summary, 'C4', 'Curve date'); formula(summary, 'D4', "='Inputs'!D4"); summary.getRange('D4').setNumberFormat('mm/dd/yy');
note(summary, 'F4', 'Actual Treasury terms. Hypothetical holdings. Curve-implied marks.');
section(summary, 'C6', 'Live portfolio valuation', 'G');
block(summary, 7, 3, [['CUSIP', 'Face (USD)', 'Clean PV (USD)', 'Accrued (USD)', 'Dirty PV (USD)']]); header(summary, 'C7:G7');
for (let i = 0; i < 3; i++) { const r = i + 8, ir = i + 10; formula(summary, `C${r}`, `='Inputs'!C${ir}`); formula(summary, `D${r}`, `='Inputs'!E${ir}`); formula(summary, `E${r}`, `=D${r}*'Inputs'!I${ir}/100`); formula(summary, `F${r}`, `=D${r}*'Inputs'!J${ir}/100`); formula(summary, `G${r}`, `=D${r}*'Inputs'!H${ir}/100`); }
value(summary, 'C11', 'Total'); for (const c of ['D', 'E', 'F', 'G']) formula(summary, `${c}11`, `=SUM(${c}8:${c}10)`); total(summary, 'C11:G11'); summary.getRange('D8:G11').setNumberFormat(money);
section(summary, 'C14', 'Live hedge risk', 'G');
block(summary, 15, 3, [['Target parallel DV01'], ['Residual parallel DV01'], ['Gross hedge face'], ['Residual step standard deviation'], ['Forecast variance reduction'], ['Gross limit excess'], ['Single-position limit excess']]);
for (const [r, sourceCell] of [[15, 'D20'], [16, 'D22'], [17, 'D23'], [18, 'D37'], [19, 'D34'], [20, 'D27'], [21, 'D28']]) formula(summary, `D${r}`, `='Risk'!${sourceCell}`);
summary.getRange('D15:D21').setNumberFormat(num); summary.getRange('D19').setNumberFormat(pct); warn(summary, 'D20:D21', 'D20>0.01');
section(summary, 'C24', 'Frozen 2022 and 2023 model wealth', 'K');
block(summary, 25, 3, [['Year', 'Method', 'Net P&L (USD)', 'Wealth change', 'Max drawdown', 'Transaction cost', 'Funding cost', 'Collateral exceptions', 'Financing gaps']]); header(summary, 'C25:K25');
for (let i = 0; i < summaryRows.length; i++) { const r = 26 + i, lr = ledgerSummaryStart + i; for (const [c, lc] of [['C', 'C'], ['D', 'D'], ['E', 'I'], ['F', 'J'], ['G', 'K'], ['H', 'L'], ['I', 'M'], ['J', 'N'], ['K', 'O']]) formula(summary, `${c}${r}`, `='Ledger'!${lc}${lr}`); }
summary.getRange(`E26:E${25 + summaryRows.length}`).setNumberFormat(money); summary.getRange(`F26:G${25 + summaryRows.length}`).setNumberFormat(pct); summary.getRange(`H26:I${25 + summaryRows.length}`).setNumberFormat(money);
note(summary, 'C36', 'Faces and hedge positions update current value, exposure and limit formulas. Python regeneration is required to reoptimize.');
note(summary, 'C37', 'Historical results include dated coupons, principal, cash, funding and costs under the displayed assumptions.');
note(summary, 'C38', 'Fitted GSW curve prices are not market quotes. Current source vintage includes revisions to historical observations.');
summary.getRange('C1:C48').format.columnWidth = 35; summary.getRange('D1:D48').format.columnWidth = 22; summary.getRange('E1:K48').format.columnWidth = 20;
summary.getRange('C15:C21').format.columnWidth = 35; summary.getRange('C25:K25').format.wrapText = true; summary.getRange('C25:K25').format.rowHeight = 35;

// Audit is a terminal reader. No valuation or risk formula depends on it.
const audit = sheets.Audit;
title(audit, 'Reconciliation and source review', 95, 'M');
section(audit, 'C6', 'Current portfolio reconciliations', 'G');
block(audit, 7, 3, [['Check', 'Calculated', 'Source control', 'Difference', 'Tolerance']]); header(audit, 'C7:G7');
block(audit, 8, 3, [['Dirty PV', null, ri.target_price, null, 0.000001], ['Parallel DV01', null, ri.target_parallel_dv01, null, 0.000001], ['Dirty less clean and accrued', null, 0, null, 0.000001], ['Source constrained variance', null, method.variance_after, null, 0.01]]);
formula(audit, 'D8', "='Summary'!G11", true); formula(audit, 'D9', "='Risk'!D20", true); formula(audit, 'D10', "='Summary'!G11-'Summary'!E11-'Summary'!F11", true); formula(audit, 'D11', "='Risk'!D33", true);
for (let r = 8; r <= 11; r++) formula(audit, `F${r}`, `=D${r}-E${r}`);
audit.getRange('D8:G11').setNumberFormat('0.000000'); warn(audit, 'F8:F11', 'ABS(F8)>G8');
section(audit, 'C14', 'Independent official auction-price validation', 'G');
block(audit, 15, 3, [['CUSIP', 'Calculated clean', 'Official clean', 'Difference / 100', 'Tolerance / 100']]); header(audit, 'C15:G15');
for (let i = 0; i < data.auction_benchmarks.length; i++) { const r = i + 16, ir = i + 85; for (const [c, ic] of [['C', 'C'], ['D', 'F'], ['E', 'E'], ['G', 'H']]) formula(audit, `${c}${r}`, `='Inputs'!${ic}${ir}`, true); formula(audit, `F${r}`, `=D${r}-E${r}`); }
audit.getRange('D16:G31').setNumberFormat('0.000000'); warn(audit, 'F16:F31', 'ABS(F16)>G16');
section(audit, 'C34', 'Full-source curve reconstruction audit', 'H');
const sa = data.source_audit;
const auditMetrics = [['Published comparison tolerance (bp)', sa.tolerance_bps], ['Published comparisons', sa.comparisons], ['Maximum absolute error (bp)', sa.max_absolute_error_bps], ['Failed comparisons', sa.failed_comparisons], ['Quarantined observations', sa.quarantined_observations], ['Missing published node values', sa.missing_published_values]];
block(audit, 35, 3, auditMetrics);
note(audit, 'C42', 'The original 0.006 bp tolerance remains fixed. Exceptional dates are excluded from research and retained below.');
const exceptionKeys = ['date', ...Object.keys(sa.exceptions[0] ?? {}).filter(k => k !== 'date')];
block(audit, 44, 3, [exceptionKeys.map(k => k.replaceAll('_', ' '))]); header(audit, `C44:${col(exceptionKeys.length + 1)}44`);
block(audit, 45, 3, sa.exceptions.map(item => exceptionKeys.map(k => k === 'date' ? date(item[k]) : item[k] === null ? 'n.a.' : k === 'status' ? item[k].replaceAll('_', ' ') : typeof item[k] === 'object' ? JSON.stringify(item[k]) : item[k] ?? null)));
audit.getRange('C45:C60').setNumberFormat('mm/dd/yy');
note(audit, 'C63', 'Cash-roll differences and floating-point tolerances are retained on every Ledger row.');
audit.getRange('C1:C95').format.columnWidth = 42; audit.getRange('D1:G95').format.columnWidth = 22;
audit.getRange('D35:D40').setNumberFormat('0.000000'); audit.getRange('D36').setNumberFormat('#,##0'); audit.getRange('D38:D40').setNumberFormat('#,##0'); audit.getRange('E45:E54').setNumberFormat('0.000000'); audit.getRange('E45:E54').format.horizontalAlignment = 'right'; audit.getRange('F1:F95').format.columnWidth = 36; audit.freezePanes.freezeRows(7);
section(audit, 'C66', 'Frozen monthly decision windows and solver diagnostics', 'M');
block(audit, 67, 3, [['Trade date', 'Decision curve', 'Training start', 'Training end', 'Changes', 'Active hedges', 'Curve lag (days)', 'Training lag (days)', 'Solver status', 'Optimality gap', 'Gap tolerance']]); header(audit, 'C67:M67');
const decisions = periods.flatMap(period => period.decisions);
block(audit, 68, 3, decisions.map(item => [date(item.trade_date), date(item.decision_curve_date), date(item.covariance.training_start_date), date(item.covariance.training_end_date), item.covariance.change_count, item.active_hedges.length, null, null, item.methods.constrained.status, item.methods.constrained.solver_diagnostics.normalized_first_order_gap, item.methods.constrained.solver_diagnostics.first_order_gap_tolerance]));
for (let r = 68; r < 68 + decisions.length; r++) { formula(audit, `I${r}`, `=C${r}-D${r}`); formula(audit, `J${r}`, `=C${r}-F${r}`); }
audit.getRange(`C68:F${67 + decisions.length}`).setNumberFormat('mm/dd/yy'); audit.getRange(`L68:M${67 + decisions.length}`).setNumberFormat('0.00E+00');
audit.getRange('C67:M67').format.wrapText = true; audit.getRange('C67:M67').format.rowHeight = 34;
warn(audit, `I68:J${67 + decisions.length}`, 'I68<=0'); warn(audit, `L68:L${67 + decisions.length}`, 'L68>M68'); warn(audit, `K68:K${67 + decisions.length}`, 'K68<>"optimal"');

// Verification is kept in support JSON rather than delivered model cells.
wb.recalculate();
const basePV = summary.getRange('G11').values[0][0];
const baseDV01 = risk.getRange('D20').values[0][0];
const baseKRD = risk.getRange('J8:J16').values.map(row => row[0]);
const faceBefore = inputs.getRange('E10').values[0][0];
inputs.getRange('E10').values = [[faceBefore * 1.1]];
wb.recalculate();
const changedPV = summary.getRange('G11').values[0][0];
const changedDV01 = risk.getRange('D20').values[0][0];
const changedKRD = risk.getRange('J8:J16').values.map(row => row[0]);
assert(Number.isFinite(changedPV) && Number.isFinite(changedDV01) && changedPV !== basePV && changedDV01 !== baseDV01, 'Face edit must change portfolio PV and risk');
assert(changedKRD.some((v, i) => Math.abs(v - baseKRD[i]) > 1e-6), 'Face edit must change residual key-rate DV01');
inputs.getRange('E10').values = [[faceBefore]];
wb.recalculate();
assert(Math.abs(summary.getRange('G11').values[0][0] - basePV) < 1e-6, 'Face restoration must restore PV');
assert(Math.abs(risk.getRange('D20').values[0][0] - baseDV01) < 1e-6, 'Face restoration must restore risk');
assert(risk.getRange('J8:J16').values.every((row, i) => Math.abs(row[0] - baseKRD[i]) < 1e-6), 'Face restoration must restore residual key-rate DV01');
assert(Math.abs(basePV - ri.target_price) < 1e-6, 'Current dirty PV must reconcile to Python');
assert(Math.abs(baseDV01 - ri.target_parallel_dv01) < 1e-6, 'Current parallel DV01 must reconcile to Python');
assert(Math.abs(risk.getRange('D33').values[0][0] - method.variance_after) < 0.01, 'Residual variance must reconcile to Python');
const baseForecast = risk.getRange('D37').values[0][0];
inputs.getRange('D18').values = [[4]];
wb.recalculate();
const scaledForecast = risk.getRange('D37').values[0][0];
assert(Math.abs(scaledForecast - baseForecast * 2) < 1e-6, 'Four times covariance must double model standard deviation');
inputs.getRange('D18').values = [[1]];
wb.recalculate();
const inspected = {};
for (const [name, range] of [['Summary', 'C7:K33'], ['Risk', 'C7:O37'], ['Inputs', 'C8:M28'], ['Ledger', `C${ledgerSummaryHeader}:O${ledgerSummaryStart + summaryRows.length - 1}`], ['Audit', 'C7:G31']]) inspected[name] = (await wb.inspect({ kind: 'table', range: `${name}!${range}`, include: 'values,formulas', tableMaxRows: 40, tableMaxCols: 13, maxChars: 18000 })).ndjson;
const errors = await wb.inspect({ kind: 'match', searchTerm: '#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!', options: { useRegex: true, maxResults: 300 }, summary: 'Final formula error scan', maxChars: 6000 });
assert(errors.ndjson.includes('Cell search matched 0 entries.'), 'Formula error scan must have no matches');
await fs.mkdir(outputDir, { recursive: true });
await fs.writeFile(path.join(outputDir, 'workbook_verification.json'), JSON.stringify({ releaseSha256, generatedAt: data.generated_at, basePV, changedPV, baseDV01, changedDV01, baseKRD, changedKRD, baseForecast, scaledForecast, faceRestored: true, covarianceMultiplierRestored: true, ledgerRows: ledgerRows.length, monthlyDecisions: decisions.length, errors: errors.ndjson, inspected }, null, 2));
console.log(JSON.stringify({ basePV, changedPV, baseDV01, changedDV01, errors: errors.ndjson, ledgerRows: ledgerRows.length }));
const previews = [['Summary', 'C2:K38'], ['Risk', 'C2:O40'], ['Inputs', 'C2:M31'], ['Inputs', 'C34:L80'], ['Inputs', 'C83:M112'], ['Ledger', `C${ledgerSummaryHeader - 1}:O${ledgerSummaryStart + summaryRows.length - 1}`], ['Ledger', 'C2:O17'], ['Audit', 'C2:H63'], ['Ledger', 'P6:AA17'], ['Ledger', `AB6:${ledgerEndCol}17`], ['Inputs', 'C115:L120'], ['Audit', 'C66:M91']];
for (let i = 0; i < previews.length; i++) { const [sheetName, range] = previews[i]; const image = await wb.render({ sheetName, range, scale: 1.4, format: 'png' }); await fs.writeFile(path.join(outputDir, `preview_${String(i + 1).padStart(2, '0')}_${sheetName.toLowerCase()}.png`), new Uint8Array(await image.arrayBuffer())); }
if (process.argv.includes('--export')) {
  const xlsx = await SpreadsheetFile.exportXlsx(wb);
  const xlsxPath = path.join(outputDir, 'treasury_risk_pack.xlsx');
  await xlsx.save(xlsxPath);
  console.log(`Saved ${xlsxPath}`);
} else console.log('Preview verification complete. Final XLSX export requires --export after the final release is frozen.');
