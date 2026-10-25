import io
import json
import unittest
from unittest.mock import patch, MagicMock

from bomcheck.ai import CompatibleAI
from bomcheck.inputs import Line
from bomcheck.mouser import Mouser, CartError, prepare_cart, search_query


PART = {'MouserPartNumber': '123-RES10K', 'ManufacturerPartNumber': 'RES10K', 'AvailabilityInStock': '100', 'Min': '1', 'Mult': '1', 'Description': '10k 0603 1% resistor'}


class MouserTests(unittest.TestCase):
    def test_skipped_components_are_not_searched_or_added(self):
        client, ai = MagicMock(), MagicMock()
        rows = prepare_cart([Line('LOGO1', 'Logo', 'Artwork', 1, {})], {0: 'Skipped manually'}, 1, client, ai)
        self.assertEqual(rows, [])
        client.search.assert_not_called()
        ai.complete.assert_not_called()
        client.create_cart.assert_not_called()
    def test_search_normalizes_eagle_ids_but_preserves_explicit_mpn(self):
        line = Line('R1', '4k7', 'R0603_38612003', 1, {})
        self.assertEqual(search_query(line), '4.7k 0603')
        self.assertEqual(line.footprint, 'R0603_38612003')
        line.fields['MPN'] = 'RC0603FR-074K7L'
        self.assertEqual(search_query(line), 'RC0603FR-074K7L')

    def test_cart_only_endpoint_and_new_cart_payload(self):
        client = Mouser('cart-secret')
        def respond(request, timeout):
            self.assertIn('/api/v1.0/cart/items/insert?', request.full_url)
            self.assertNotIn('/order', request.full_url)
            body = json.loads(request.data)
            self.assertEqual(body['CartKey'], '00000000-0000-0000-0000-000000000000')
            self.assertEqual(body['CartItems'][0]['Quantity'], 5)
            self.assertNotIn('cart-secret', request.data.decode())
            self.assertNotIn('SubmitOrder', body)
            return io.BytesIO(json.dumps({'CartKey': 'c0f9eb83-7881-4faa-b663-320710b25a11', 'CartItems': [], 'Errors': []}).encode())
        with patch.object(client.opener, 'open', side_effect=respond):
            client.create_cart([{'MouserPartNumber': '123-RES10K', 'Quantity': 2}, {'MouserPartNumber': '123-RES10K', 'Quantity': 3}])
        with self.assertRaises(ValueError):
            client._post('/api/v1.0/order', {}, 'key')

    def test_ai_selection_is_bound_to_real_candidates_and_moq(self):
        client = Mouser('key')
        part = dict(PART, Min='10', Mult='5')
        ai = CompatibleAI('http://localhost:11434', 'model', protocol='ollama')
        decision = {'rows': [{'row_id': 0, 'candidate_id': '0:0', 'confidence': 0.95, 'reason': 'Same value, package and tolerance'}]}
        with patch.object(client, 'search', return_value=[part]), patch.object(ai, 'complete', return_value=decision):
            rows = prepare_cart([Line('R1', '10k', 'R0603', 1, {})], {}, 2, client, ai)
        self.assertEqual(rows[0].requested, 2)
        self.assertEqual(rows[0].quantity, 10)
        self.assertEqual(rows[0].sku, '123-RES10K')
        decision['rows'][0]['candidate_id'] = 'invented'
        with patch.object(client, 'search', return_value=[part]), patch.object(ai, 'complete', return_value=decision):
            with self.assertRaises(ValueError):
                prepare_cart([Line('R1', '10k', 'R0603', 1, {})], {}, 2, client, ai)

    def test_cart_preparation_does_not_create_cart(self):
        client = Mouser('key')
        ai = CompatibleAI('http://localhost:11434', 'model', protocol='ollama')
        decision = {'rows': [{'row_id': 0, 'candidate_id': None, 'confidence': 0.5, 'reason': 'Package cannot be verified'}]}
        with patch.object(client, 'search', return_value=[]), patch.object(client, 'create_cart') as create, patch.object(ai, 'complete', return_value=decision):
            rows = prepare_cart([Line('U1', 'Unknown', 'Package', 1, {})], {}, 1, client, ai)
        create.assert_not_called()
        self.assertFalse(rows[0].sku)

    def test_partial_cart_error_keeps_cart_key(self):
        client = Mouser('key')
        response = {'CartKey': 'c0f9eb83-7881-4faa-b663-320710b25a11', 'Errors': [{'Message': 'Part not available'}]}
        with patch.object(client, '_post', return_value=response):
            with self.assertRaises(CartError) as caught:
                client.create_cart([{'MouserPartNumber': '123-RES10K', 'Quantity': 1}])
        self.assertEqual(caught.exception.cart_key, response['CartKey'])

    def test_ollama_native_request_and_response(self):
        for root in ('http://localhost:11434', 'http://localhost:11434/api'):
            ai = CompatibleAI(root, 'local-model', json_mode=False, protocol='ollama')
            def respond(request, timeout):
                self.assertEqual(request.full_url, 'http://localhost:11434/api/chat')
                body = json.loads(request.data)
                self.assertFalse(body['stream'])
                self.assertEqual(body['format'], 'json')
                self.assertNotIn('response_format', body)
                return io.BytesIO(b'{"message":{"content":"{\\"rows\\":[]}"}}')
            with patch.object(ai.opener, 'open', side_effect=respond):
                self.assertEqual(ai.complete({}), {'rows': []})


if __name__ == '__main__':
    unittest.main()
