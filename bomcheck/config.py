"""Shared CLI/plugin configuration loading."""
import json
from pathlib import Path


def load_config(path):
    path = Path(path).resolve()
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    if config.get('mapping_file'):
        extra = json.loads((path.parent / config['mapping_file']).read_text(encoding='utf-8-sig'))
        config['mappings'] = config.get('mappings', []) + extra.get('mappings', [])
    config['mappings'] = [m for m in config.get('mappings', []) if m.get('part_id') or m.get('ipn', '').strip()]
    return config
