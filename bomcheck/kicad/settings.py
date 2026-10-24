import json
import os
from pathlib import Path


def settings_path():
    if os.name == 'nt':
        root = Path(os.environ['APPDATA'])
    elif __import__('sys').platform == 'darwin':
        root = Path.home() / 'Library' / 'Application Support'
    else:
        root = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
    return root / 'inventree-bom-checker' / 'settings.json'


def load_settings(path):
    if Path(path).is_file():
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    return {'url': 'http://inventree.example.test:1337', 'related_as_equivalents': True, 'mappings': []}


def save_settings(path, config):
    # Raw credentials are stored separately with Windows account encryption.
    safe = {k: config[k] for k in ('url', 'related_as_equivalents', 'mappings', 'remember_token') if k in config}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(safe, indent=2, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def assign(config, line, part):
    mappings = config.setdefault('mappings', [])
    mappings[:] = [m for m in mappings if (m['value'], m['footprint']) != (line.value, line.footprint)]
    mappings.append({'value': line.value, 'footprint': line.footprint, 'part_id': part['pk']})
