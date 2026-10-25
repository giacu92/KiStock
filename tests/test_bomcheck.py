import io
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from bomcheck.checker import check
from bomcheck.inputs import Line, decompress_lz64, read_bom
from bomcheck.inventree import InvenTree
from bomcheck.report import write_reports
from bom_checker import main


def part(pk, ipn=None):
    return {'pk': pk, 'IPN': ipn or f'P{pk}', 'name': f'Part {pk}', 'active': True}


class Inventory:
    def __init__(self, stock, related=None):
        self.stock = stock
        self.relations = related or {}

    def find(self, kind, code):
        return [part(pk) for pk in self.stock if code == f'P{pk}']

    def related(self, pk):
        return [part(p) for p in self.relations.get(pk, [])]

    def available(self, pk):
        return Decimal(str(self.stock[pk]))

    def suggestions(self, value):
        return [part(pk) for pk in self.stock]


def line(ipn, quantity=1, refs='R1'):
    return Line(refs, '10k', 'R0603', quantity, {'IPN': ipn})


class CheckerTests(unittest.TestCase):
    def test_boards_and_equivalents(self):
        rows, _, _ = check([line('P1', 2)], Inventory({1: 1, 2: 5}, {1: [2]}), {}, 3)
        self.assertEqual(rows[0].required, 6)
        self.assertEqual(rows[0].used, {1: 1, 2: 5})
        self.assertEqual(rows[0].status, 'OK_EQUIVALENT')

    def test_shared_stock_not_counted_twice(self):
        rows, _, _ = check([line('P1', 2), line('P1', 2, 'R2')], Inventory({1: 3}), {})
        self.assertEqual(sum(sum(r.used.values()) for r in rows), 3)
        self.assertEqual(sum(r.missing for r in rows), 1)

    def test_reroutes_shared_equivalent(self):
        # Greedy selection takes P1 for first row, leaving second row short.
        # Global distribution must move first row to P2 instead.
        rows, _, _ = check([line('P1'), line('P1', refs='R2')], Inventory({1: 1, 2: 1}, {1: [2]}), {})
        rows[1].candidates = [part(1)]
        from bomcheck.checker import distribute
        for row in rows:
            row.status = ''
        distribute(rows, {1: Decimal(1), 2: Decimal(1)})
        self.assertEqual(rows[0].used, {2: 1})
        self.assertEqual(rows[1].used, {1: 1})

    def test_no_identity_only_suggestions(self):
        rows, _, stock = check([line('*')], Inventory({1: 200}), {})
        self.assertEqual(rows[0].status, 'UNMAPPED')
        self.assertEqual(rows[0].missing, 1)
        self.assertEqual(stock, {})

    def test_mapping_and_disabled_relations(self):
        cfg = {'related_as_equivalents': False, 'mappings': [{'value': '10k', 'footprint': 'R0603', 'ipn': 'P1'}]}
        rows, _, _ = check([line('*', 2)], Inventory({1: 1, 2: 500}, {1: [2]}), cfg)
        self.assertEqual(rows[0].status, 'PARTIAL')
        self.assertEqual(rows[0].missing, 1)

    def test_ambiguous_matches_not_used(self):
        inv = Inventory({1: 10, 2: 20})
        inv.find = lambda kind, code: [part(1), part(2)]
        rows, _, _ = check([line('P1')], inv, {})
        self.assertEqual(rows[0].status, 'AMBIGUOUS')
        self.assertFalse(rows[0].used)

    def test_fractional_stock_floored(self):
        rows, _, _ = check([line('P1', 2)], Inventory({1: '1.9'}), {})
        self.assertEqual(rows[0].missing, 1)

    def test_inactive_substitute_not_used(self):
        inv = Inventory({1: 0, 2: 50})
        inv.related = lambda pk: [dict(part(2), active=False)]
        rows, _, _ = check([line('P1')], inv, {})
        self.assertEqual(rows[0].status, 'SHORTAGE')


class InputTests(unittest.TestCase):
    def read(self, suffix, text):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ('bom' + suffix)
            path.write_text(text, encoding='utf-8-sig')
            return read_bom(path)

    def test_user_csv_dialect_and_zero_quantity(self):
        rows = self.read('.csv', '"Qty","Value","Part Num","Package","Parts"\n"0","","*","3MM","H1(DNP)"\n"2","10k","*","R0603","R1, R2"\n')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].quantity, 2)
        self.assertEqual(rows[0].footprint, 'R0603')

    def test_native_csv_semicolon_dnp(self):
        rows = self.read('.csv', 'References;Quantity;Value;Footprint;DNP;IPN\nR1;1;10k;R0603;;P1\nC1;1;10n;C0603;DNP;P2\n')
        self.assertEqual([r.refs for r in rows], ['R1'])

    def test_xml_flags(self):
        rows = self.read('.xml', '<export><components><comp ref="R1"><value>10k</value><fields><field name="IPN">P1</field></fields></comp><comp ref="R2"><property name="dnp"/></comp><comp ref="R3"><property name="exclude_from_bom"/></comp></components></export>')
        self.assertEqual([r.refs for r in rows], ['R1'])
        self.assertEqual(rows[0].fields['IPN'], 'P1')

    def test_ibom_front_back_deduplicated(self):
        data = {'bom': {'F': [[['R1', 0]]], 'B': [[['R1', 0]], [['R2', 1]]],
                        'fields': {'0': ['10k', 'R0603'], '1': ['22k', 'R0603']}},
                'footprints': [{'ref': 'R1'}, {'ref': 'R2'}]}
        rows = self.read('.html', 'var config = {"fields":["Value","Footprint"]}; var pcbdata = ' + json.dumps(data))
        self.assertEqual(sum(r.quantity for r in rows), 2)
        self.assertEqual(rows[1].value, '22k')

    def test_lz_string_known_vector(self):
        self.assertEqual(decompress_lz64('BIUwNmD2AEDukCcwBMg='), 'Hello world')
        with self.assertRaises(ValueError):
            decompress_lz64('B')


class ApiTests(unittest.TestCase):
    def test_stock_locations_group_lots_and_keep_full_paths(self):
        client = InvenTree('http://inventory.test', 'secret')
        lots = [{'quantity': 2, 'location_detail': {'pathstring': 'Room / Box 2'}},
                {'quantity': '3.5', 'location_detail': {'pathstring': 'Room / Box 2'}},
                {'quantity': 7, 'location': 4}, {'quantity': 1, 'location': None},
                {'quantity': 0, 'location_detail': {'name': 'Empty'}}]
        with patch.object(client, 'list', return_value=lots) as request, \
             patch.object(client, 'get', return_value={'pathstring': 'Room / Drawer 4'}) as fallback:
            locations = client.stock_locations(17)
        request.assert_called_once_with('stock/', part=17, in_stock='true', location_detail='true')
        fallback.assert_called_once_with('stock/location/4/')
        self.assertEqual(locations, [{'path': 'Room / Box 2', 'quantity': Decimal('5.5')},
                                    {'path': 'Room / Drawer 4', 'quantity': Decimal(7)},
                                    {'path': 'Unassigned location', 'quantity': Decimal(1)}])
        for quantity in ('NaN', 'unknown', -1):
            with patch.object(client, 'list', return_value=[{'quantity': quantity}]), self.assertRaises(ValueError):
                client.stock_locations(17)

    def test_mpn_exact_and_same_part_deduplicated(self):
        client = InvenTree('http://inventory.test', 'secret')
        matches = [{'MPN': 'ABC', 'part': 1}, {'MPN': 'ABC', 'part': 1}, {'MPN': 'ABCD', 'part': 2}]
        with patch.object(client, 'list', return_value=matches), patch.object(client, 'get', return_value=part(1)):
            self.assertEqual(client.find('mpn', 'ABC'), [part(1)])

    def test_pagination_auth_and_exact_ipn(self):
        client = InvenTree('http://inventory.test', 'secret')
        responses = [dict(results=[part(1, 'P10')], next='http://inventory.test/api/part/?offset=1'),
                     dict(results=[part(2, 'P1')], next=None)]
        def open_request(request, timeout):
            self.assertEqual(request.get_header('Authorization'), 'Token secret')
            self.assertEqual(request.get_method(), 'GET')
            return io.BytesIO(json.dumps(responses.pop(0)).encode())
        with patch.object(client.opener, 'open', side_effect=open_request):
            self.assertEqual(client.find('ipn', 'P1'), [part(2, 'P1')])

    def test_does_not_send_token_to_foreign_pagination(self):
        client = InvenTree('http://inventory.test', 'secret')
        with self.assertRaises(ValueError):
            client.get('http://other.test/api/part/')

    def test_stock_unknown_is_error(self):
        client = InvenTree('http://inventory.test', 'secret')
        for value in (None, 'NaN', 'Infinity', 'unknown'):
            with patch.object(client, 'get', return_value={'unallocated_stock': value}):
                with self.assertRaises(ValueError):
                    client.available(1)


class EndToEndTests(unittest.TestCase):
    def test_cli_does_not_overwrite_input(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = 'Qty,Value,Package,Parts,IPN\n1,10k,R0603,R1,P1\n'
            (root / 'bom.csv').write_text(source, encoding='utf-8')
            (root / 'config.json').write_text('{"url":"http://inventory.test"}', encoding='utf-8')
            with patch('bom_checker.InvenTree', return_value=Inventory({1: 10})), patch.dict('os.environ', {'INVENTREE_TOKEN': 'test'}):
                code = main([str(root / 'bom.csv'), str(root / 'bom'), '--config', str(root / 'config.json')])
            self.assertEqual(code, 1)
            self.assertEqual((root / 'bom.csv').read_text(encoding='utf-8'), source)

    def test_cli_csv_to_reports(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'bom.csv').write_text('Qty,Value,Package,Parts,IPN\n2,10k,R0603,"R1 R2",P1\n', encoding='utf-8')
            (root / 'config.json').write_text('{"url":"http://inventory.test"}', encoding='utf-8')
            with patch('bom_checker.InvenTree', return_value=Inventory({1: 1, 2: 1}, {1: [2]})), patch.dict('os.environ', {'INVENTREE_TOKEN': 'test'}):
                code = main([str(root / 'bom.csv'), str(root / 'report'), '--config', str(root / 'config.json')])
            self.assertEqual(code, 0)
            self.assertIn('OK_EQUIVALENT', (root / 'report.html').read_text(encoding='utf-8'))
            self.assertIn('P2', (root / 'report.csv').read_text(encoding='utf-8-sig'))

    def test_html_escapes_part_data(self):
        rows, parts, stock = check([line('P1')], Inventory({1: 5}), {})
        rows[0].line.value = '<script>alert(1)</script>'
        with tempfile.TemporaryDirectory() as folder:
            path, _ = write_reports(Path(folder) / 'report', 'bom.csv', rows, parts, stock, 1)
            text = path.read_text(encoding='utf-8')
            self.assertNotIn('<script>alert(1)</script>', text)
            self.assertIn('&lt;script&gt;', text)


if __name__ == '__main__':
    unittest.main()
