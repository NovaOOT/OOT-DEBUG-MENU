"""Build the project's icon files from art/icon.jpeg (or another square picture given with --src).

Writes runtime/ootpc.ico (the icon of ocarina.exe, OOT-PC-Setup.exe and the launcher's OOT-PC.exe: runtime/ootpc.rc,
installer/launcher/launcher.rc and launcher/native/res.rc), launcher/ui/icon.ico (the launcher window's own icon,
launcher/ui/art.py app_ico), launcher/ui/icon-64.png (Tk's photo icon, art.py app_icon_png) and launcher/ui/icon-256.rgba
(the picture as raw 256 x 256 RGBA bytes, which the launcher scales without an imaging package: art.py app_icon, the
title band's emblem and the Steam shortcut's icon). The .ico holds every size Windows asks for. Needs Pillow:

    uv run --no-project --with pillow python tools/make_icon.py
"""
import argparse
import io
import struct
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def square(img):
    """The picture cropped to its centre square, as RGBA."""
    img = img.convert('RGBA')
    w, h = img.size
    n = min(w, h)
    return img.crop(((w - n) // 2, (h - n) // 2, (w - n) // 2 + n, (h - n) // 2 + n))


def png(img, n):
    out = io.BytesIO()
    img.resize((n, n), Image.Resampling.LANCZOS).save(out, format='PNG', optimize=True)
    return out.getvalue()


def dib(img, n):
    """One .ico entry as a 32-bit bitmap with its AND mask, bottom-up (what every Windows API and Tk read)."""
    small = img.resize((n, n), Image.Resampling.LANCZOS)
    px = small.tobytes()   # RGBA, top-down
    rows = [px[y * n * 4:(y + 1) * n * 4] for y in range(n)][::-1]
    bgra = b''.join(bytes(b for i in range(0, len(r), 4) for b in (r[i + 2], r[i + 1], r[i], r[i + 3])) for r in rows)
    stride = ((n + 31) // 32) * 4
    mask = bytearray()
    for r in rows:
        bits = bytearray(stride)
        for x in range(n):
            if r[4 * x + 3] < 128:
                bits[x // 8] |= 0x80 >> (x % 8)
        mask += bits
    head = struct.pack('<IiiHHIIiiII', 40, n, 2 * n, 1, 32, 0, len(bgra) + len(mask), 0, 0, 0, 0)
    return head + bgra + bytes(mask)


def ico(img):
    """A Windows .ico at SIZES: bitmaps up to 64 pixels, PNG above, as Windows' own icons (size byte 0 means 256)."""
    datas = [png(img, n) if n > 64 else dib(img, n) for n in SIZES]
    head = struct.pack('<HHH', 0, 1, len(datas))
    offset = 6 + 16 * len(datas)
    dirs, body = b'', b''
    for n, data in zip(SIZES, datas):
        dirs += struct.pack('<BBBBHHII', n % 256, n % 256, 0, 0, 1, 32, len(data), offset + len(body))
        body += data
    return head + dirs + body


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--src', type=Path, default=ROOT / 'art' / 'icon.jpeg')
    a = ap.parse_args()
    img = square(Image.open(a.src))
    data = ico(img)
    for dest in (ROOT / 'runtime' / 'ootpc.ico', ROOT / 'launcher' / 'ui' / 'icon.ico'):
        dest.write_bytes(data)
        print(f'{dest.relative_to(ROOT)}: {len(data)} bytes')
    (ROOT / 'launcher' / 'ui' / 'icon-64.png').write_bytes(png(img, 64))
    print('launcher/ui/icon-64.png')
    (ROOT / 'launcher' / 'ui' / 'icon-256.rgba').write_bytes(img.resize((256, 256), Image.Resampling.LANCZOS).tobytes())
    print('launcher/ui/icon-256.rgba')


if __name__ == '__main__':
    main()
