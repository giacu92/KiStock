import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from bomcheck.checker import identity
from bomcheck.config import load_config
from bomcheck.inputs import Line
from bomcheck.inventree import InvenTree
from bomcheck.kicad.settings import assign, load_settings, save_settings
from bomcheck.kicad.credentials import credential_key, load_secret, save_secret
from tools.build_plugin import build


class SettingsTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI')
    def test_saved_token_survives_process_restart_and_can_be_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            key = credential_key('inventree', 'http://inventory.test/')
            save_secret(path, key, 'dummy-persistent-token')
            self.assertNotIn('dummy-persistent-token', (path.parent / 'credentials.json').read_text())
            script = 'from bomcheck.kicad.credentials import load_secret; import sys; assert load_secret(sys.argv[1], sys.argv[2]) == "dummy-persistent-token"'
            subprocess.run([sys.executable, '-c', script, str(path), key], check=True)
            self.assertEqual(load_secret(path, credential_key('inventree', 'http://other.test')), '')
            save_secret(path, key, '')
            self.assertEqual(load_secret(path, key), '')

    def test_association_without_ipn_and_cli_loading(self):
        line = Line('R1', '10k', 'R0603', 1, {})
        config = {'url': 'http://inventory.test', 'mappings': []}
        assign(config, line, {'pk': 17, 'IPN': ''})
        self.assertEqual(identity(line, config), ('part_id', '17'))
        assign(config, line, {'pk': 18})
        self.assertEqual(len(config['mappings']), 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            save_settings(path, config)
            self.assertEqual(load_config(path)['mappings'][0]['part_id'], 18)

    def test_api_token_is_never_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            save_settings(path, {'url': 'http://inventory.test', 'mappings': [], 'token': 'secret-token', 'api_token': 'secret-token'})
            self.assertNotIn('secret-token', path.read_text())
            self.assertNotIn('token', load_settings(path))

    def test_part_id_lookup(self):
        client = InvenTree('http://inventory.test', 'secret')
        with patch.object(client, 'get', return_value={'pk': 17}) as request:
            self.assertEqual(client.find('part_id', '17'), [{'pk': 17}])
            request.assert_called_once_with('part/17/')


class PackageTests(unittest.TestCase):
    def test_package_is_self_contained_and_has_no_local_data(self):
        path = build()
        with ZipFile(path) as archive:
            names = archive.namelist()
            self.assertIn('plugins/__init__.py', names)
            self.assertIn('plugins/bomcheck/kicad/gui.py', names)
            self.assertIn('plugins/bomcheck/kicad/icon.png', names)
            self.assertFalse(any('config.json' in name or '.local.json' in name or 'reports/' in name or '__pycache__' in name for name in names))
            for name in names:
                if name.endswith('.py'):
                    compile(archive.read(name), name, 'exec')
            metadata = json.loads(archive.read('metadata.json'))
            self.assertEqual(metadata['versions'][0]['runtime'], 'swig')


if __name__ == '__main__':
    unittest.main()
