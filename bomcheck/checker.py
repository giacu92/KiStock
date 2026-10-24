"""Exact identity matching and stock distribution across the complete BOM."""
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR


@dataclass
class Result:
    line: object
    required: int
    primary: object = None
    candidates: list = field(default_factory=list)
    suggestions: list = field(default_factory=list)
    used: dict = field(default_factory=dict)
    status: str = ''
    note: str = ''

    @property
    def missing(self):
        return self.required - sum(self.used.values())


def usable(part):
    return part.get('active', True) and not part.get('virtual', False) and not part.get('is_template', False)


def identity(line, config):
    for kind in ('ipn', 'mpn'):
        for name in config.get(kind + '_fields', ['InvenTree IPN', 'IPN'] if kind == 'ipn' else ['MPN', 'Part Num']):
            code = line.fields.get(name, '').strip()
            if code and code not in ('*', '~', '-'):
                return kind, code
    mappings = [m for m in config.get('mappings', [])
                if m['value'] == line.value and m['footprint'] == line.footprint]
    if len(mappings) > 1:
        raise ValueError(f'Duplicate mapping: {line.value} / {line.footprint}')
    if mappings:
        if mappings[0].get('part_id'):
            return 'part_id', str(mappings[0]['part_id'])
        return 'ipn', mappings[0]['ipn']
    return None


def distribute(results, stock):
    """Maximum flow: a shared substitute cannot be counted twice.

    Reverse edges allow earlier choices to be rerouted when a later line has
    fewer substitutes. Units are whole components; fractional stock is floored.
    """
    capacities, edges = {}, {}
    def edge(a, b, capacity):
        edges.setdefault(a, []).append(b)
        edges.setdefault(b, []).append(a)
        capacities[a, b] = capacity
        capacities[b, a] = 0
    for i, row in enumerate(results):
        node = ('row', i)
        edge('source', node, row.required)
        for part in row.candidates:
            edge(node, ('part', part['pk']), row.required)
    for pk, quantity in stock.items():
        edge(('part', pk), 'sink', int(quantity.to_integral_value(rounding=ROUND_FLOOR)))
    while True:
        parents = {'source': None}
        queue = deque(['source'])
        while queue and 'sink' not in parents:
            a = queue.popleft()
            for b in edges.get(a, []):
                if b not in parents and capacities[a, b] > 0:
                    parents[b] = a
                    queue.append(b)
        if 'sink' not in parents:
            break
        amount, b = float('inf'), 'sink'
        while parents[b] is not None:
            a = parents[b]
            amount = min(amount, capacities[a, b])
            b = a
        b = 'sink'
        while parents[b] is not None:
            a = parents[b]
            capacities[a, b] -= amount
            capacities[b, a] += amount
            b = a
    for i, row in enumerate(results):
        row.used = {p['pk']: capacities[('part', p['pk']), ('row', i)]
                    for p in row.candidates if capacities[('part', p['pk']), ('row', i)] > 0}
        if row.status:
            continue
        if row.missing:
            row.status = 'PARTIAL' if row.used else 'SHORTAGE'
        elif any(pk != row.primary['pk'] for pk in row.used):
            row.status = 'OK_EQUIVALENT'
        else:
            row.status = 'OK'


def check(lines, client, config, boards=1):
    results, parts, stock = [], {}, {}
    for line in lines:
        row = Result(line, line.quantity * boards)
        results.append(row)
        key = identity(line, config)
        if key is None:
            row.status = 'UNMAPPED'
            row.note = 'No IPN/MPN: associate this value and footprint with an inventory part.'
            row.suggestions = client.suggestions(line.value)
            continue
        matches = client.find(*key)
        if len(matches) != 1:
            row.status = 'NOT_FOUND' if not matches else 'AMBIGUOUS'
            row.note = f'{key[0].upper()} {key[1]}: {len(matches)} exact matches.'
            row.suggestions = matches
            continue
        row.primary = matches[0]
        candidates = [row.primary]
        if config.get('related_as_equivalents', True):
            candidates += client.related(row.primary['pk'])
        unique = {p['pk']: p for p in candidates if usable(p)}
        row.candidates = list(unique.values())
        for pk, part in unique.items():
            parts[pk] = part
            if pk not in stock:
                stock[pk] = client.available(pk)
        if not usable(row.primary):
            row.note = 'Original part is inactive, virtual or a template; excluded from usable stock.'
    distribute(results, stock)
    return results, parts, stock
