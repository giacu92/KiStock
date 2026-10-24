import csv
import html
import json
from collections import Counter
from datetime import datetime
from pathlib import Path


def label(part):
    return f"{part.get('IPN') or '#' + str(part['pk'])} — {part.get('name', '')}"


def write_reports(output, source, results, parts, stock, boards):
    base = Path(output)
    base.parent.mkdir(parents=True, exist_ok=True)
    csv_path, html_path = Path(str(base) + '.csv'), Path(str(base) + '.html')
    headers = ['Status', 'References', 'Value', 'Footprint', 'Required', 'Covered',
               'Missing', 'Original part', 'Proposed pick', 'Usable stock',
               'Unconfirmed suggestions', 'Notes']
    rows = []
    for r in results:
        pick = '; '.join(f'{label(parts[pk])}: {qty}' for pk, qty in r.used.items())
        available = '; '.join(f'{label(p)}: {stock[p["pk"]]}' for p in r.candidates)
        suggestions = '; '.join(label(p) for p in r.suggestions)
        rows.append([r.status, r.line.refs, r.line.value, r.line.footprint,
                     r.required, sum(r.used.values()), r.missing,
                     label(r.primary) if r.primary else '', pick, available, suggestions, r.note])
    with csv_path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream, delimiter=';')
        writer.writerow(headers)
        for row in rows:
            # Protect spreadsheet users from formulas embedded in part names.
            writer.writerow(["'" + v if isinstance(v, str) and v.startswith(('=', '+', '-', '@', '\t', '\r')) else v for v in row])
    esc = lambda x: html.escape(str(x))
    table = ''.join('<tr class="' + esc(row[0]) + '">' + ''.join('<td>' + esc(v) + '</td>' for v in row) + '</tr>' for row in rows)
    summary = ' · '.join(f'{state}: {count}' for state, count in sorted(Counter(r.status for r in results).items()))
    page = '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BoM Checker · InvenTree</title><style>
body{font:15px system-ui;margin:28px;color:#1c2939;background:#f5f7fb}h1{margin-bottom:6px}
.card{background:white;border:1px solid #dce3ee;padding:18px;border-radius:10px;margin:16px 0}
input{font:inherit;padding:10px;width: min(440px,85%);border:1px solid #a6b4c5;border-radius:6px}
.scroll{overflow:auto}table{border-collapse:collapse;min-width:1150px;width:100%;background:white}
th,td{text-align:left;vertical-align:top;padding:10px;border-bottom:1px solid #e0e5ee}th{background:#22334b;color:white}
td:first-child{font-weight:bold;white-space:nowrap}.OK td:first-child{color:#19713c}.OK_EQUIVALENT td:first-child{color:#166b85}
.SHORTAGE td:first-child,.PARTIAL td:first-child{color:#b33d19}.UNMAPPED td:first-child,.AMBIGUOUS td:first-child{color:#8b6811}
small{color:#53657a}td{max-width:330px;overflow-wrap:anywhere}</style>
<h1>BoM Checker <small>KiCad → InvenTree</small></h1>
'''
    page += f'<p>{esc(Path(source).name)} · {boards} boards · {esc(datetime.now().astimezone().isoformat(timespec="seconds"))}</p>'
    page += '<div class="card">' + esc(summary or 'Empty BOM') + '</div>'
    page += '''<p>Usable stock is unallocated stock, rounded down to whole components.
The proposed pick distributes stock across the entire BOM without counting a component twice.
Stock quantities are initial totals per part; do not add them across rows.
Suggestions require manual confirmation. Related InvenTree parts are considered approved substitutes when enabled.</p>
<p><strong>Read-only:</strong> stock is never reserved or changed. Availability is a snapshot.</p>
<p><input id="filter" placeholder="Filter references, value, IPN or status" aria-label="Filter BOM"></p>
<div class="scroll"><table><thead><tr>'''
    page += ''.join('<th>' + esc(h) + '</th>' for h in headers) + '</tr></thead><tbody>' + table + '</tbody></table></div>'
    page += '''<script>document.getElementById('filter').addEventListener('input',function(){
const q=this.value.toLowerCase();document.querySelectorAll('tbody tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q));});</script></html>'''
    html_path.write_text(page, encoding='utf-8')
    unknown = {(r.line.value, r.line.footprint) for r in results if r.status == 'UNMAPPED'}
    mapping_path = Path(str(base) + '.mapping.json')
    mapping_path.write_text(json.dumps({'mappings': [{'value': v, 'footprint': f, 'ipn': ''} for v, f in sorted(unknown)]}, indent=2, ensure_ascii=False), encoding='utf-8')
    return html_path, csv_path
