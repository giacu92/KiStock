"""Prepare and explicitly create a build with exact stock-lot allocations."""
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_FLOOR

from .checker import usable


def decimal(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise ValueError()
        return result
    except (ValueError, InvalidOperation):
        raise ValueError('A required stock/BOM quantity is unknown or invalid.') from None


def free_stock(item):
    if not item.get('in_stock', False) or item.get('expired') or item.get('is_building') or item.get('belongs_to') or item.get('customer'):
        return Decimal(0)
    return max(Decimal(0), decimal(item.get('quantity')) - decimal(item.get('allocated'))).to_integral_value(rounding=ROUND_FLOOR)


def stock_items(client, part_id):
    return client.list('stock/', part=part_id, in_stock='true', available='true', location_detail='true')


@dataclass
class BuildPlan:
    assembly: dict
    boards: int
    reference: str
    requirements: dict
    bom: dict
    allocations: list
    substitutes: dict
    pcb_stock_id: int


def requirements_from_results(results, boards, pcb_part):
    required, picks, substitutes = {}, defaultdict(lambda: defaultdict(int)), defaultdict(set)
    for row in results:
        if row.status == 'EXCLUDED_AI':
            continue
        if row.status not in ('OK', 'OK_EQUIVALENT') or not row.primary or row.missing:
            raise ValueError('All physical BOM rows must be associated and fully covered before creating a build.')
        if row.required != row.line.quantity * boards:
            raise ValueError('The board quantity changed. Refresh availability before preparing the build.')
        pk = row.primary['pk']
        entry = required.setdefault(pk, {'quantity': 0, 'reference': [], 'part': row.primary})
        entry['quantity'] += row.line.quantity
        entry['reference'].append(row.line.refs)
        for actual, amount in row.used.items():
            picks[pk][actual] += amount
            if actual != pk:
                substitutes[pk].add(actual)
    if not required:
        raise ValueError('No physical components are available for this build.')
    if pcb_part['pk'] in required:
        raise ValueError('The selected bare PCB is already a mapped BOM row. Exclude that duplicate before adding the dedicated PCB requirement.')
    required[pcb_part['pk']] = {'quantity': 1, 'reference': ['BARE_PCB'], 'part': pcb_part}
    picks[pcb_part['pk']][pcb_part['pk']] = boards
    return required, picks, substitutes


def existing_bom(client, assembly_id, requirements, substitutes):
    rows = client.list('bom/', part=assembly_id)
    if not rows:
        return {}
    by_part = {}
    for row in rows:
        pk = row['sub_part']
        if pk in by_part:
            raise ValueError('The existing InvenTree BOM contains duplicate component lines. Resolve them in InvenTree before creating this build.')
        by_part[pk] = row
    if set(by_part) != set(requirements):
        raise ValueError('An InvenTree BOM already exists but its components (including the bare PCB) differ from this KiCad BOM. It has not been changed.')
    for pk, row in by_part.items():
        if decimal(row['quantity']) != decimal(requirements[pk]['quantity']) or row.get('consumable') or row.get('optional') or any(decimal(row.get(k) or 0) for k in ('attrition', 'setup_quantity')) or decimal(row.get('rounding_multiple') or 0) not in (0, 1):
            raise ValueError('The existing InvenTree BOM has different quantities or special consumption rules. It has not been changed.')
        allowed = {s['part'] for s in client.list('bom/substitute/', bom_item=row['pk'])} if substitutes.get(pk) else set()
        if not substitutes.get(pk, set()) <= allowed:
            raise ValueError('The existing InvenTree BOM does not approve all substitutes in this pick. Add those substitutes in InvenTree or adjust the associations.')
    return by_part


def prepare_build(client, results, assembly_id, pcb_part_id, pcb_stock_id, boards, reference):
    if not reference.strip() or type(boards) is not int or boards <= 0:
        raise ValueError('Enter a build reference and a positive board quantity.')
    if hasattr(client, 'cache'):
        client.cache.clear()
    assembly = client.get(f'part/{assembly_id}/')
    pcb_part = client.get(f'part/{pcb_part_id}/')
    if not assembly.get('assembly') or not usable(assembly) or not usable(pcb_part):
        raise ValueError('Select an active assembly to produce and an active bare-PCB part.')
    required, picks, substitutes = requirements_from_results(results, boards, pcb_part)
    if assembly_id in required:
        raise ValueError('An assembly cannot consume itself. Select a separate bare-PCB component and assembled output part.')
    for entry in required.values():
        if entry['part'].get('trackable'):
            raise ValueError('Tracked components require per-output allocations. Create this build directly in InvenTree.')
    if any(b.get('reference') == reference.strip() for b in client.list('build/', search=reference.strip())):
        raise ValueError('That build reference already exists. Open the existing order rather than creating a duplicate.')
    bom = existing_bom(client, assembly_id, required, substitutes)
    pcb_stock = client.get(f'stock/{pcb_stock_id}/')
    if pcb_stock.get('part') != pcb_part_id or free_stock(pcb_stock) < boards:
        raise ValueError('The selected bare-PCB stock lot cannot cover the requested boards.')
    # A shared stock item is decremented in one ledger across all BOM lines.
    ledger, lots_by_part, allocations = {}, {}, []
    for primary, actuals in picks.items():
        for actual, amount in actuals.items():
            if actual == pcb_part_id:
                lots = [pcb_stock]
            else:
                if actual not in lots_by_part:
                    lots_by_part[actual] = sorted(stock_items(client, actual), key=lambda x: x['pk'])
                lots = lots_by_part[actual]
            remaining = Decimal(amount)
            for lot in lots:
                if lot.get('part') != actual:
                    continue
                pk = lot['pk']
                if pk not in ledger:
                    ledger[pk] = free_stock(lot)
                take = min(remaining, ledger[pk])
                if take > 0:
                    allocations.append({'component': primary, 'actual_part': actual, 'stock_item': pk, 'quantity': str(take), 'batch': lot.get('batch') or '', 'location': (lot.get('location_detail') or {}).get('pathstring') or str(lot.get('location') or '')})
                    remaining -= take
                    ledger[pk] -= take
                if not remaining:
                    break
            if remaining:
                raise ValueError(f'Part #{actual} no longer has enough allocatable stock. Refresh availability.')
    return BuildPlan(assembly, boards, reference.strip(), required, bom, allocations, dict(substitutes), pcb_stock_id)


class BuildCreationError(ValueError):
    def __init__(self, message, build_id=None):
        super().__init__(message)
        self.build_id = build_id


def create_build(client, plan):
    """No retries: report partial creation instead of risking duplicate orders."""
    build = None
    writes_started = False
    try:
        if hasattr(client, 'cache'):
            client.cache.clear()
        current = existing_bom(client, plan.assembly['pk'], plan.requirements, plan.substitutes)
        if bool(current) != bool(plan.bom):
            raise ValueError('The assembly BOM changed since the preview. Prepare the build again.')
        # Recheck each lot immediately before creating any records.
        totals = defaultdict(Decimal)
        for allocation in plan.allocations:
            totals[allocation['stock_item']] += decimal(allocation['quantity'])
        for pk, quantity in totals.items():
            item = client.get(f'stock/{pk}/')
            expected_parts = {a['actual_part'] for a in plan.allocations if a['stock_item'] == pk}
            if item.get('part') not in expected_parts or free_stock(item) < quantity:
                raise ValueError('Stock changed since the preview. Prepare the build again.')
        if any(b.get('reference') == plan.reference for b in client.list('build/', search=plan.reference)):
            raise ValueError('That build reference already exists. No duplicate order was created.')
        bom = current
        if not bom:
            for pk, entry in plan.requirements.items():
                writes_started = True
                bom[pk] = client.post('bom/', {'part': plan.assembly['pk'], 'sub_part': pk, 'quantity': entry['quantity'], 'reference': ', '.join(entry['reference'])[:1000]})
                for substitute in sorted(plan.substitutes.get(pk, [])):
                    client.post('bom/substitute/', {'bom_item': bom[pk]['pk'], 'part': substitute})
        writes_started = True
        build = client.post('build/', {'part': plan.assembly['pk'], 'quantity': plan.boards, 'reference': plan.reference,
                                      'title': 'KiCad PCB assembly', 'notes': f'Created by KiCad BoM Checker. Bare PCB stock lot: #{plan.pcb_stock_id}. Stock is allocated, not consumed.'})
        lines = client.list('build/line/', build=build['pk'])
        line_ids = {line['bom_item']: line['pk'] for line in lines}
        payload = []
        for item in plan.allocations:
            bom_id = bom[item['component']]['pk']
            if bom_id not in line_ids:
                raise ValueError('The server has not generated all build lines. Review the created order in InvenTree.')
            payload.append({'build_line': line_ids[bom_id], 'stock_item': item['stock_item'], 'quantity': item['quantity']})
        client.post(f'build/{build["pk"]}/allocate/', {'items': payload})
        return build
    except Exception as error:
        if build:
            message = f'Build #{build["pk"]} was created, but allocation was not confirmed: {error} Open that order and inspect it before retrying.'
        elif writes_started:
            message = f'Creation did not finish: {error} Some BOM/order records may have been accepted. Inspect the assembly BOM and build reference {plan.reference} before retrying.'
        else:
            message = str(error)
        raise BuildCreationError(message, build.get('pk') if build else None) from None
