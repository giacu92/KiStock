"""Create a self-contained PCM ZIP without configs, tokens or local reports."""
import json
import struct
import zlib
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED


def icon_png():
    pixels = [[(0, 0, 0, 0) for _ in range(24)] for _ in range(24)]
    def rect(x0, y0, x1, y1, color):
        for y in range(y0, y1):
            for x in range(x0, x1):
                pixels[y][x] = color
    ink = (40, 79, 115, 255)
    rect(4, 4, 20, 20, ink)
    rect(6, 6, 18, 18, (235, 245, 250, 255))
    for n in (6, 11, 16):
        rect(n, 1, n + 2, 4, ink)
        rect(n, 20, n + 2, 23, ink)
        rect(1, n, 4, n + 2, ink)
        rect(20, n, 23, n + 2, ink)
    for x, y in [(7, 12), (8, 13), (9, 14), (10, 15), (11, 14), (12, 13), (13, 12), (14, 11), (15, 10), (16, 9)]:
        rect(x, y, x + 2, y + 2, (21, 148, 78, 255))
    raw = b''.join(b'\0' + bytes(channel for pixel in row for channel in pixel) for row in pixels)
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 24, 24, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')


def build(root=None):
    root = Path(root) if root else Path(__file__).resolve().parents[1]
    metadata = json.loads((root / 'plugin/metadata.json').read_text(encoding='utf-8'))
    output = root / 'dist' / ('inventree-bom-checker-' + metadata['versions'][0]['version'] + '.zip')
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        archive.write(root / 'plugin/metadata.json', 'metadata.json')
        archive.write(root / 'plugin/entrypoint.py', 'plugins/__init__.py')
        archive.write(root / 'LICENSE', 'plugins/LICENSE')
        for path in sorted((root / 'bomcheck').rglob('*.py')):
            archive.write(path, 'plugins/' + path.relative_to(root).as_posix())
        archive.writestr('plugins/bomcheck/kicad/icon.png', icon_png())
    print(output)
    return output


if __name__ == '__main__':
    build()
