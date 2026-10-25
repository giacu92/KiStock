import copy
import unittest

from bomcheck.builds import prepare_build, create_build, BuildCreationError, free_stock
from bomcheck.checker import Result
from bomcheck.inputs import Line


class Server:
    def __init__(self, existing=False):
        self.base = 'http://inventory.test/'
        self.cache = {}
        self.parts = {1: {'pk': 1, 'name': 'Assembled board', 'assembly': True},
                      2: {'pk': 2, 'name': 'Resistor', 'component': True},
                      3: {'pk': 3, 'name': 'Bare PCB', 'component': True},
                      4: {'pk': 4, 'name': 'Approved resistor substitute', 'component': True}}
        self.lots = {20: {'pk': 20, 'part': 2, 'quantity': 10, 'allocated': 0, 'in_stock': True},
                     30: {'pk': 30, 'part': 3, 'quantity': 10, 'allocated': 0, 'in_stock': True},
                     40: {'pk': 40, 'part': 4, 'quantity': 10, 'allocated': 0, 'in_stock': True}}
        self.bom = ([{'pk': 52, 'sub_part': 2, 'quantity': 2}, {'pk': 53, 'sub_part': 3, 'quantity': 1}] if existing else [])
        self.writes = []
        self.orders = []
        self.fail_allocation = False

    def get(self, path, **params):
        kind, pk, _ = path.split('/')
        return copy.deepcopy((self.parts if kind == 'part' else self.lots)[int(pk)])

    def list(self, path, **params):
        if path == 'bom/':
            return copy.deepcopy(self.bom)
        if path == 'bom/substitute/':
            return []
        if path == 'stock/':
            return [copy.deepcopy(item) for item in self.lots.values() if item['part'] == params['part']]
        if path == 'build/':
            return self.orders
        if path == 'build/line/':
            return [{'pk': row['pk'] + 100, 'bom_item': row['pk']} for row in self.bom]
        raise AssertionError(path)

    def post(self, path, payload):
        self.writes.append((path, payload))
        if path == 'bom/':
            result = {'pk': 50 + len(self.bom), **payload}
            self.bom.append(result)
            return result
        if path == 'build/':
            self.orders.append({'pk': 80, **payload})
            return self.orders[-1]
        if path == 'build/80/allocate/' and self.fail_allocation:
            raise ValueError('Allocation rejected')
        return {}


def results(server, substitute=False):
    return [Result(Line('R1, R2', '10k', 'R0603', 2, {}), 4,
                   primary=server.parts[2], used={4 if substitute else 2: 4}, status='OK_EQUIVALENT' if substitute else 'OK')]


class BuildTests(unittest.TestCase):
    def test_skipped_components_do_not_enter_build_requirements(self):
        server = Server()
        rows = results(server)
        rows.append(Result(Line('LOGO1', 'Logo', 'Artwork', 1, {}), 0, status='SKIPPED'))
        from bomcheck.builds import requirements_from_results
        required, _, _ = requirements_from_results(rows, 2, server.parts[3])
        self.assertEqual(set(required), {2, 3})
    def plan(self, server, rows=None):
        return prepare_build(server, rows or results(server), 1, 3, 30, 2, 'BO-001')

    def test_prepare_is_read_only_and_preview_has_selected_pcb_lot(self):
        server = Server()
        plan = self.plan(server)
        self.assertFalse(server.writes)
        self.assertFalse(plan.bom)
        self.assertEqual(plan.requirements[3]['quantity'], 1)
        self.assertTrue(any(a['stock_item'] == 30 and a['quantity'] == '2' for a in plan.allocations))

    def test_creates_missing_bom_then_order_and_allocates_exact_lots(self):
        server = Server()
        order = create_build(server, self.plan(server))
        self.assertEqual(order['pk'], 80)
        self.assertEqual([path for path, _ in server.writes], ['bom/', 'bom/', 'build/', 'build/80/allocate/'])
        allocations = server.writes[-1][1]['items']
        self.assertEqual({a['stock_item'] for a in allocations}, {20, 30})
        self.assertTrue(all('build_line' in a for a in allocations))
        self.assertFalse(any('consume' in path or 'complete' in path for path, _ in server.writes))

    def test_reuses_existing_bom_without_writing_bom(self):
        server = Server(existing=True)
        server.bom[0]['rounding_multiple'] = 1
        create_build(server, self.plan(server))
        self.assertEqual([path for path, _ in server.writes], ['build/', 'build/80/allocate/'])

    def test_existing_mismatched_bom_is_not_overwritten(self):
        server = Server(existing=True)
        server.bom[0]['quantity'] = 3
        with self.assertRaises(ValueError):
            self.plan(server)
        self.assertFalse(server.writes)

    def test_insufficient_pcb_or_changed_stock_prevents_all_writes(self):
        server = Server()
        server.lots[30]['allocated'] = 9
        with self.assertRaises(ValueError):
            self.plan(server)
        self.assertFalse(server.writes)
        server.lots[30]['allocated'] = 0
        plan = self.plan(server)
        server.lots[20]['allocated'] = 9
        with self.assertRaises(BuildCreationError):
            create_build(server, plan)
        self.assertFalse(server.writes)

    def test_duplicate_order_reference_prevents_writes(self):
        server = Server()
        plan = self.plan(server)
        server.orders.append({'pk': 80, 'reference': 'BO-001'})
        with self.assertRaises(BuildCreationError):
            create_build(server, plan)
        self.assertFalse(server.writes)

    def test_partial_failure_reports_existing_build_without_retry(self):
        server = Server()
        plan = self.plan(server)
        server.fail_allocation = True
        with self.assertRaises(BuildCreationError) as caught:
            create_build(server, plan)
        self.assertEqual(caught.exception.build_id, 80)
        self.assertIn('was created', str(caught.exception))
        self.assertEqual(sum(path == 'build/' for path, _ in server.writes), 1)

    def test_registers_approved_substitutes_only_for_a_new_bom(self):
        server = Server()
        create_build(server, self.plan(server, results(server, substitute=True)))
        self.assertTrue(any(path == 'bom/substitute/' and payload['part'] == 4 for path, payload in server.writes))
        server = Server(existing=True)
        with self.assertRaises(ValueError):
            self.plan(server, results(server, substitute=True))
        self.assertFalse(server.writes)

    def test_unknown_stock_quantity_is_not_treated_as_available(self):
        with self.assertRaises(ValueError):
            free_stock({'in_stock': True, 'quantity': 10, 'allocated': None})


if __name__ == '__main__':
    unittest.main()
