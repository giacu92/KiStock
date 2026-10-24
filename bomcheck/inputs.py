"""Read exports as data; never execute JavaScript from an iBOM file."""
import csv
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Line:
    refs: str
    value: str
    footprint: str
    quantity: int
    fields: dict


def decompress_lz64(encoded):
    """Decode the LZ-String base64 stream used by InteractiveHtmlBom."""
    alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/='
    values = [alphabet.index(c) for c in encoded]
    position = 0

    def bits(count):
        nonlocal position
        result = 0
        for shift in range(count):
            if position // 6 >= len(values):
                raise ValueError('Truncated compressed iBOM')
            result |= ((values[position // 6] >> (5 - position % 6)) & 1) << shift
            position += 1
        return result

    dictionary = {0: '', 1: '', 2: ''}
    width, enlarge, size = 3, 4, 4
    first = bits(2)
    if first == 2:
        return ''
    if first not in (0, 1):
        raise ValueError('Invalid compressed iBOM')
    previous = chr(bits(8 if first == 0 else 16))
    dictionary[3] = previous
    result = [previous]
    while True:
        code = bits(width)
        if code == 2:
            # LZ-String stores UTF-16 code units, including surrogate pairs.
            return ''.join(result).encode('utf-16-le', 'surrogatepass').decode('utf-16-le')
        if code in (0, 1):
            dictionary[size] = chr(bits(8 if code == 0 else 16))
            code = size
            size += 1
            enlarge -= 1
        if enlarge == 0:
            enlarge = 1 << width
            width += 1
        if code in dictionary:
            entry = dictionary[code]
        elif code == size:
            entry = previous + previous[0]
        else:
            raise ValueError('Invalid iBOM dictionary')
        result.append(entry)
        dictionary[size] = previous + entry[0]
        size += 1
        enlarge -= 1
        previous = entry
        if enlarge == 0:
            enlarge = 1 << width
            width += 1


def read_ibom(path):
    text = Path(path).read_text(encoding='utf-8-sig')
    match = re.search(r'(?:var|const|let)\s+pcbdata\s*=\s*JSON.parse\(LZString.decompressFromBase64\("([A-Za-z0-9+/=]+)"\)\)', text)
    if match:
        data = json.loads(decompress_lz64(match[1]))
    else:
        match = re.search(r'(?:var|const|let)\s+pcbdata\s*=\s*', text)
        if not match:
            raise ValueError('No pcbdata found in the iBOM file')
        data, _ = json.JSONDecoder().raw_decode(text[match.end():])
    config_match = re.search(r'(?:var|const|let)\s+config\s*=\s*', text)
    config = json.JSONDecoder().raw_decode(text[config_match.end():])[0] if config_match else {}
    names = config.get('fields', ['Value', 'Footprint'])
    bom = data['bom']
    # Prefer both sides together: F and B can contain the same BOM group.
    groups = bom.get('both')
    if groups is None:
        groups = bom.get('F', []) + bom.get('B', [])
    seen = set()
    lines = []
    for group in groups:
        references = []
        for ref, index in group:
            if index not in seen:
                references.append(ref)
                seen.add(index)
        if not references:
            continue
        index = group[0][1]
        raw = bom.get('fields', {}).get(str(index), [])
        fields = dict(zip(names, raw))
        footprint = data.get('footprints', [{}])[index]
        lines.append(Line(', '.join(references), fields.get('Value', footprint.get('value', '')),
                          fields.get('Footprint', footprint.get('footprint', '')),
                          len(references), fields))
    return lines


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        sample = stream.read(8192)
        stream.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
        reader = csv.DictReader(stream, dialect=dialect)
        if not reader.fieldnames:
            raise ValueError('Empty CSV')
        headers = {h.strip().lower(): h for h in reader.fieldnames}
        def column(*choices):
            return next((headers[c] for c in choices if c in headers), None)
        qty, refs = column('qty', 'quantity', 'quantità'), column('parts', 'references', 'reference', 'refs', 'designator')
        value, fp = column('value', 'valore'), column('footprint', 'package')
        if not qty or not refs:
            raise ValueError('CSV requires Qty/Quantity and Parts/References columns')
        lines = []
        for row in reader:
            count = int(row[qty])
            if count < 0:
                raise ValueError('Negative CSV quantity')
            if count == 0 or row.get(column('dnp'), '').strip().lower() not in ('', '0', 'false', 'no'):
                continue
            lines.append(Line(row[refs], row.get(value, ''), row.get(fp, ''), count,
                              {k.strip(): (v or '').strip() for k, v in row.items() if k}))
        return lines


def read_xml(path):
    root = ET.parse(path).getroot()
    if root.tag != 'export':
        raise ValueError('Expected a KiCad XML netlist with an export root')
    lines = []
    for comp in root.findall('./components/comp'):
        flags = {p.get('name') for p in comp.findall('property')}
        if flags & {'dnp', 'exclude_from_bom'} or comp.get('ref', '').startswith('#'):
            continue
        fields = {f.get('name'): f.text or '' for f in comp.findall('./fields/field')}
        lines.append(Line(comp.get('ref', ''), comp.findtext('value', ''),
                          comp.findtext('footprint', ''), 1, fields))
    return lines


def read_bom(path):
    suffix = Path(path).suffix.lower()
    readers = {'.html': read_ibom, '.htm': read_ibom, '.csv': read_csv, '.xml': read_xml}
    if suffix not in readers:
        raise ValueError('Supported formats: .csv, iBOM .html or KiCad .xml')
    return readers[suffix](path)
