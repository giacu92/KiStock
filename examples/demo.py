"""Generate a visibly labelled report with simulated stock; no server access."""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bomcheck.inputs import read_bom
from bomcheck.checker import check
from bomcheck.report import write_reports


class DemoInventory:
    parts = {
        1: {'pk': 1, 'IPN': 'P1', 'name': 'Resistor 10k 1% 0603'},
        2: {'pk': 2, 'IPN': 'P2', 'name': 'Equivalent resistor 10k 1% 0603'},
        3: {'pk': 3, 'IPN': 'P3', 'name': 'Capacitor 100n 50V X7R'},
        4: {'pk': 4, 'IPN': 'P4', 'name': 'Green LED 0603'},
        5: {'pk': 5, 'IPN': 'P5', 'name': 'Microcontroller LQFP48'},
    }
    def find(self, kind, code):
        return [p for p in self.parts.values() if p['IPN'] == code]

    def related(self, pk):
        return [self.parts[2]] if pk == 1 else []

    def available(self, pk):
        return Decimal({1: 1, 2: 10, 3: 25, 4: 1, 5: 0}[pk])

    def suggestions(self, value):
        return []


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    results, parts, stock = check(read_bom(root / 'examples/demo.csv'), DemoInventory(), {})
    paths = write_reports(root / 'reports/demo-stock', 'DEMO - SIMULATED STOCK.csv', results, parts, stock, 1)
    print('\n'.join(str(p) for p in paths))
