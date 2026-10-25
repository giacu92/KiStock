"""Create a self-contained PCM ZIP without configs, tokens or local reports."""
import json
import struct
import zlib
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED


def icon_png(size=24):
    """InvenTree-inspired box/cube with a stock-check badge; antialiased at small sizes."""
    scale = 4
    edge = size * scale
    pixels = [[(0, 0, 0, 0) for _ in range(edge)] for _ in range(edge)]
    factor = edge / 24
    ink = (43, 53, 68, 255)

    def polygon(points, color):
        points = [(x * factor, y * factor) for x, y in points]
        for y in range(edge):
            for x in range(edge):
                px, py, inside = x + .5, y + .5, False
                previous = points[-1]
                for current in points:
                    ax, ay = previous
                    bx, by = current
                    if (ay > py) != (by > py) and px < (bx - ax) * (py - ay) / (by - ay) + ax:
                        inside = not inside
                    previous = current
                if inside:
                    pixels[y][x] = color

    def stroke(points, color, width):
        for a, b in zip(points, points[1:]):
            ax, ay = (n * factor for n in a)
            bx, by = (n * factor for n in b)
            dx, dy = bx - ax, by - ay
            for y in range(edge):
                for x in range(edge):
                    t = max(0, min(1, ((x + .5 - ax) * dx + (y + .5 - ay) * dy) / (dx * dx + dy * dy)))
                    if (x + .5 - ax - t * dx) ** 2 + (y + .5 - ay - t * dy) ** 2 <= (width * factor / 2) ** 2:
                        pixels[y][x] = color

    outer = [(11, 1), (21, 6.5), (21, 16.5), (11, 22), (1, 16.5), (1, 6.5), (11, 1)]
    polygon(outer, (224, 189, 143, 255))
    stroke(outer, ink, .9)
    polygon([(11, 4), (18, 8), (18, 15), (11, 19), (4, 15), (4, 8)], (234, 236, 210, 255))
    polygon([(11, 5.5), (16.5, 8.7), (11, 12), (5.5, 8.7)], (145, 170, 214, 255))
    polygon([(5.5, 8.7), (11, 12), (11, 18), (5.5, 14.8)], (113, 141, 192, 255))
    polygon([(11, 12), (16.5, 8.7), (16.5, 14.8), (11, 18)], (89, 119, 174, 255))
    stroke([(5.5, 8.7), (11, 12), (16.5, 8.7)], ink, .7)
    stroke([(11, 12), (11, 18)], ink, .7)
    # The green check distinguishes stock checking from the upstream application.
    polygon([(17, 14), (23, 14), (23, 21), (20, 23), (15, 20), (15, 16)], (32, 143, 94, 255))
    stroke([(17, 18), (19, 20), (22, 16.5)], (255, 255, 255, 255), 1.5)
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            samples = [pixels[y * scale + dy][x * scale + dx] for dy in range(scale) for dx in range(scale)]
            alpha = sum(p[3] for p in samples)
            row.extend([round(sum(p[c] * p[3] for p in samples) / alpha) if alpha else 0 for c in range(3)] + [round(alpha / (scale * scale))])
        rows.append(b'\0' + bytes(row))
    raw = b''.join(rows)
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')


def build(root=None):
    root = Path(root) if root else Path(__file__).resolve().parents[1]
    metadata = json.loads((root / 'plugin/metadata.json').read_text(encoding='utf-8'))
    output = root / 'dist' / ('kistock-' + metadata['versions'][0]['version'] + '.zip')
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
