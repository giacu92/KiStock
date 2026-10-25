import json
import os
import hashlib
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
    safe = {k: config[k] for k in ('url', 'related_as_equivalents', 'mappings', 'remember_token', 'skipped_components') if k in config}
    if 'ai' in config:
        safe['ai'] = {k: config['ai'][k] for k in ('base_url', 'model', 'remember_key', 'json_mode', 'protocol') if k in config['ai']}
    if 'mouser' in config:
        safe['mouser'] = {k: config['mouser'][k] for k in ('country', 'currency', 'remember_key') if k in config['mouser']}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(safe, indent=2, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def assign(config, line, part):
    mappings = config.setdefault('mappings', [])
    mappings[:] = [m for m in mappings if (m['value'], m['footprint']) != (line.value, line.footprint)]
    mappings.append({'value': line.value, 'footprint': line.footprint, 'part_id': part['pk'],
                     'part_label': ' | '.join(str(part.get(key) or '') for key in ('IPN', 'name')).strip(' | ')})


def skip_keys(source, line):
    if not source or source == 'Unsaved PCB':
        return None, None
    project = os.path.normcase(os.path.abspath(source))
    component = json.dumps([sorted(line.refs.replace(',', ' ').split()), line.value, line.footprint], ensure_ascii=False)
    return tuple(hashlib.sha256(value.encode('utf-8')).hexdigest() for value in (project, component))


def skipped_rows(config, source, lines):
    rows = set()
    for i, line in enumerate(lines):
        project, component = skip_keys(source, line)
        if project is not None and component in config.get('skipped_components', {}).get(project, []):
            rows.add(i)
    return rows


def set_component_skipped(config, source, line, skipped):
    project, component = skip_keys(source, line)
    if project is None:
        return
    projects = config.setdefault('skipped_components', {})
    entries = set(projects.get(project, []))
    if skipped:
        entries.add(component)
    else:
        entries.discard(component)
    if entries:
        projects[project] = sorted(entries)
    else:
        projects.pop(project, None)
