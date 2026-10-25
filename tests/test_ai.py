import io
import json
import unittest
import threading
from decimal import Decimal
from unittest.mock import patch, MagicMock

from bomcheck.ai import CompatibleAI, Proposal, analyze, apply_proposals, validate_decisions, normalized_value, parse_decision, AICancelled
from bomcheck.checker import check, identity
from bomcheck.inputs import Line


def row(ref='R1', value='10k', footprint='R0603'):
    return Line(ref, value, footprint, 1, {})


CATALOG = {17: {'pk': 17, 'IPN': '', 'name': '10k 0603 resistor', 'active': True}}


class AIValidationTests(unittest.TestCase):
    def test_json_formatting_wrappers_are_accepted_without_repair(self):
        for text in (' {"rows": []} ', '```json\n{"rows": []}\n```',
                     'Here is the result:\n```json\n{"rows": []}\n```',
                     '<think>Reasoning text</think>\n{"rows": []}',
                     'Result: {"rows": []}\nDone.'):
            self.assertEqual(parse_decision(text), {'rows': []})
        for text in ('', 'No result', '{"rows": [', '{"rows": [],}', '{"rows": []} {"rows": []}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_decision(text)

    def test_cancelled_analysis_makes_no_requests_and_discards_late_response(self):
        event = threading.Event()
        event.set()
        inventory, ai = MagicMock(), MagicMock()
        with self.assertRaises(AICancelled):
            analyze([row()], inventory, {}, ai, cancel_event=event)
        inventory.list.assert_not_called()
        event.clear()
        inventory.list.return_value = [dict(CATALOG[17])]
        inventory.available.return_value = Decimal(10)
        def late_response(payload):
            event.set()
            return {'rows': [{'row_id': 0, 'action': 'match', 'part_id': 17, 'confidence': 1, 'reason': 'Late'}]}
        ai.complete.side_effect = late_response
        with self.assertRaises(AICancelled):
            analyze([row()], inventory, {}, ai, cancel_event=event)

    def test_engineering_value_equivalence(self):
        for values in [('4k7', '4.7k', '4700'), ('1M', '1000k')]:
            normalized = [normalized_value(row(value=v)) for v in values]
            self.assertTrue(all(v == normalized[0] for v in normalized))
        self.assertEqual(normalized_value(row('C1', '0.1uF')), normalized_value(row('C1', '100nF')))
        self.assertEqual(normalized_value(row('C1', '4n7')), normalized_value(row('C1', '4.7nF')))
        self.assertIsNone(normalized_value(row('U1', 'BSS138')))

    def test_rejects_unknown_ids_duplicates_missing_rows_and_bad_confidence(self):
        valid = {'row_id': 0, 'action': 'match', 'part_id': 17, 'confidence': 0.95, 'reason': 'Exact package and rating'}
        bad_rows = [[], [valid, valid], [{**valid, 'part_id': 999}], [{**valid, 'row_id': True}],
                    [{**valid, 'confidence': float('nan')}], [{**valid, 'action': 'delete'}]]
        for rows in bad_rows:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                validate_decisions({'rows': rows}, {0}, CATALOG)

    def test_non_match_actions_discard_irrelevant_part_ids(self):
        for action in ('keep', 'exclude'):
            for pk in (17, 999, '17', 'null', 0):
                with self.subTest(action=action, part_id=pk):
                    raw = {'rows': [
                        {'row_id': 0, 'action': action, 'part_id': pk, 'confidence': 0.99, 'reason': 'No association requested'},
                        {'row_id': 1, 'action': 'match', 'part_id': 17, 'confidence': 0.99, 'reason': 'Verified resistor'}]}
                    proposals = validate_decisions(raw, {0, 1}, CATALOG)
                    self.assertIsNone(proposals[0].part_id)
                    self.assertEqual(proposals[1].part_id, 17)
                    config, exclusions = apply_proposals([row('LOGO1', 'Logo', 'Artwork'), row()], {}, {}, proposals, CATALOG)
                    self.assertEqual(identity(row(), config), ('part_id', '17'))
                    self.assertIsNone(identity(row('LOGO1', 'Logo', 'Artwork'), config))
                    self.assertEqual(bool(exclusions), action == 'exclude')
                    self.assertEqual(raw['rows'][0]['part_id'], pk)

    def test_applies_valid_matches_and_exclusions_without_mutating_originals(self):
        lines = [row(), row('LOGO1', 'Logo', 'Artwork')]
        config = {'mappings': []}
        proposals = [Proposal(0, 'match', 17, 0.99, 'Suitable resistor'), Proposal(1, 'exclude', None, 0.99, 'Copper artwork')]
        updated, exclusions = apply_proposals(lines, config, {}, proposals, CATALOG)
        self.assertEqual(config['mappings'], [])
        self.assertEqual(identity(lines[0], updated), ('part_id', '17'))
        self.assertEqual(exclusions, {1: 'Copper artwork'})

    def test_refuses_override_of_design_or_confirmed_mapping(self):
        for config, line in [({}, Line('R1', '10k', 'R0603', 1, {'IPN': 'P1'})),
                             ({'mappings': [{'value': '10k', 'footprint': 'R0603', 'part_id': 17}]}, row())]:
            with self.assertRaises(ValueError):
                apply_proposals([line], config, {}, [Proposal(0, 'exclude', None, 0.99, 'Wrong')], CATALOG)

    def test_conflicting_assignments_fail_atomically(self):
        catalog = {**CATALOG, 18: {'pk': 18, 'name': 'Other resistor'}}
        config = {'mappings': []}
        decisions = [Proposal(0, 'match', 17, 1, 'First'), Proposal(1, 'match', 18, 1, 'Second')]
        with self.assertRaises(ValueError):
            apply_proposals([row('R1'), row('R2')], config, {}, decisions, catalog)
        self.assertEqual(config, {'mappings': []})

    def test_excluded_rows_remain_auditable_and_use_no_stock(self):
        class Inventory:
            def suggestions(self, value):
                raise AssertionError('Excluded row must not query inventory')
        rows, _, stock = check([row('H1', 'Hole', 'MountingHole')], Inventory(), {}, 5, {0: 'Bare mounting hole'})
        self.assertEqual(rows[0].status, 'EXCLUDED_AI')
        self.assertEqual(rows[0].required, 0)
        self.assertEqual(rows[0].missing, 0)
        self.assertIn('original requirement: 5', rows[0].note)
        self.assertFalse(stock)

    def test_manual_skip_ignores_even_explicit_parts_and_uses_no_stock(self):
        client = MagicMock()
        line = Line('H1', 'Hole', 'MountingHole', 1, {'IPN': 'P1'})
        rows, parts, stock = check([line], client, {}, 5, skipped={0})
        self.assertEqual(rows[0].status, 'SKIPPED')
        self.assertEqual(rows[0].required, 0)
        self.assertEqual(rows[0].missing, 0)
        self.assertEqual(rows[0].used, {})
        self.assertIn('original requirement: 5', rows[0].note)
        self.assertFalse(parts or stock)
        client.find.assert_not_called()
        client.available.assert_not_called()


class TransportTests(unittest.TestCase):
    def test_exhausted_or_unknown_stock_proposal_stays_unresolved(self):
        class Inventory:
            def list(self, *args, **kwargs):
                return [dict(CATALOG[17])]
            def available(self, pk):
                return Decimal('0.00000')
        client = CompatibleAI('http://localhost:1234/v1', 'model')
        decision = {'rows': [{'row_id': 0, 'action': 'match', 'part_id': 17, 'confidence': 0.99, 'reason': 'Same value and package'}]}
        with patch.object(client, 'complete', return_value=decision):
            proposals, _ = analyze([row()], Inventory(), {}, client)
        self.assertEqual(proposals[0].action, 'keep')
        self.assertIsNone(proposals[0].part_id)

    def test_compatible_request_and_json_mode_optional(self):
        for json_mode in (False, True):
            client = CompatibleAI('http://localhost:1234/v1/', 'configured-model', 'ai-secret', json_mode)
            def respond(request, timeout):
                self.assertEqual(request.full_url, 'http://localhost:1234/v1/chat/completions')
                self.assertEqual(request.get_method(), 'POST')
                self.assertEqual(request.get_header('Authorization'), 'Bearer ai-secret')
                body = json.loads(request.data)
                self.assertEqual(body['model'], 'configured-model')
                self.assertEqual('response_format' in body, json_mode)
                self.assertNotIn('ai-secret', request.data.decode())
                return io.BytesIO(json.dumps({'choices': [{'message': {'content': '{"rows": []}'}}]}).encode())
            with patch.object(client.opener, 'open', side_effect=respond):
                self.assertEqual(client.complete({'bom': [], 'inventory': []}), {'rows': []})

    def test_malformed_completion_fails_without_changes(self):
        client = CompatibleAI('http://localhost:1234/v1', 'model')
        with patch.object(client.opener, 'open', return_value=io.BytesIO(b'{"choices": []}')):
            with self.assertRaises(ValueError):
                client.complete({})

    def test_catalog_and_bom_are_sent_but_no_inventory_credentials(self):
        class Inventory:
            def list(self, *args, **kwargs):
                return list(CATALOG.values())
            def available(self, pk):
                return Decimal(10)
        client = CompatibleAI('http://localhost:1234/v1', 'model')
        decision = {'rows': [{'row_id': 0, 'action': 'match', 'part_id': 17, 'confidence': 0.95, 'reason': 'Exact match'}]}
        with patch.object(client, 'complete', return_value=decision) as completion:
            proposals, catalog = analyze([row()], Inventory(), {}, client)
        self.assertEqual(proposals[0].part_id, 17)
        payload = completion.call_args.args[0]
        self.assertEqual(payload['inventory'][0]['part_id'], 17)
        self.assertEqual(payload['bom'][0]['footprint'], 'R0603')
        self.assertNotIn('token', json.dumps(payload))

    def test_associated_rows_are_not_sent_to_ai_or_inventory(self):
        inventory = MagicMock()
        client = CompatibleAI('http://localhost:1234/v1', 'model')
        with patch.object(client, 'complete') as completion:
            self.assertEqual(analyze([Line('R1', '10k', 'R0603', 1, {'IPN': 'P1'})], inventory, {}, client), ([], {}))
            completion.assert_not_called()
            inventory.list.assert_not_called()

    def test_only_unmapped_rows_sent_with_original_ids_across_batches(self):
        inventory = MagicMock()
        inventory.list.return_value = list(CATALOG.values())
        inventory.available.return_value = Decimal(10)
        client = MagicMock()
        lines = [row(f'R{i}', str(i)) for i in range(9)]
        lines[0].fields['IPN'] = 'P1'
        config = {'mappings': [{'value': '2', 'footprint': 'R0603', 'part_id': 17}]}
        batches = []
        def respond(payload):
            ids = [r['row_id'] for r in payload['bom']]
            batches.append(ids)
            return {'rows': [{'row_id': i, 'action': 'keep', 'part_id': None,
                              'confidence': 1, 'reason': 'Unresolved'} for i in ids]}
        client.complete.side_effect = respond
        proposals, _ = analyze(lines, inventory, config, client, exclusions={4: 'Logo'})
        self.assertEqual(batches, [[1, 3, 5], [6, 7, 8]])
        self.assertEqual([p.row_id for p in proposals], [1, 3, 5, 6, 7, 8])


if __name__ == '__main__':
    unittest.main()
