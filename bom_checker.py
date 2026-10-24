#!/usr/bin/env python3
"""@package
BoM Checker: check InvenTree availability and related parts.
Input: KiCad XML netlist, CSV or InteractiveHtmlBom HTML.
KiCad BOM generator command:
python "bom_checker.py" "%I" "%O-stock" --config "config.json" --open
"""
import argparse
import json
import os
import sys
import webbrowser
import xml.etree.ElementTree as ET
from pathlib import Path

from bomcheck.checker import check
from bomcheck.config import load_config
from bomcheck.inputs import read_bom
from bomcheck.inventree import InvenTree
from bomcheck.report import write_reports


def main(argv=None):
    parser = argparse.ArgumentParser(description='Compare a KiCad/iBOM BOM with InvenTree inventory (read-only).')
    parser.add_argument('input', help='CSV, iBOM HTML or KiCad XML')
    parser.add_argument('output', nargs='?', help='Output report path without extension')
    parser.add_argument('--config', default=str(Path(__file__).with_name('config.json')))
    parser.add_argument('--boards', type=int, default=1, help='Number of boards to assemble')
    parser.add_argument('--open', action='store_true', help='Open the report in a browser')
    parser.add_argument('--prepare-mapping', action='store_true', help='Generate value/footprint mappings without connecting to the server')
    args = parser.parse_args(argv)
    if args.boards <= 0:
        parser.error('--boards must be positive')
    try:
        lines = read_bom(args.input)
        if not lines:
            raise ValueError('BOM contains no populated components')
        if args.prepare_mapping:
            output = Path(args.output or str(Path(args.input).with_suffix('')) + '-mapping.json')
            # Exclusive creation prevents accidentally replacing a completed map.
            with output.open('x', encoding='utf-8') as stream:
                json.dump({'mappings': [{'value': v, 'footprint': f, 'ipn': ''}
                                       for v, f in sorted({(l.value, l.footprint) for l in lines})]}, stream, indent=2, ensure_ascii=False)
            print(f'Mapping file to complete: {output.resolve()}')
            return 0
        config = load_config(args.config)
        token = os.environ.get('INVENTREE_TOKEN', '').strip()
        if not token:
            raise ValueError('Set INVENTREE_TOKEN to your API token (see README.md)')
        client = InvenTree(config['url'], token)
        print(f'Checking {len(lines)} rows for {args.boards} boards...')
        results, parts, stock = check(lines, client, config, args.boards)
        output = args.output or str(Path(args.input).with_suffix('')) + '-stock'
        if Path(args.input).resolve() in {Path(str(output) + ext).resolve() for ext in ('.html', '.csv')}:
            raise ValueError('The output would overwrite the source BOM: choose a different name')
        html_path, csv_path = write_reports(output, args.input, results, parts, stock, args.boards)
        print(f'HTML: {html_path.resolve()}\nCSV: {csv_path.resolve()}')
        unresolved = sum(r.status not in ('OK', 'OK_EQUIVALENT') for r in results)
        print(f'{len(results) - unresolved}/{len(results)} rows covered; {unresolved} unresolved.')
        if args.open:
            webbrowser.open(html_path.resolve().as_uri())
        # Generation success is distinct from stock completeness, like other BOM generators.
        return 0
    except (OSError, ValueError, KeyError, TypeError, IndexError, ET.ParseError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
