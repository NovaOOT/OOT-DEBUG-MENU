"""OOT-PC's forest-and-brass interface art, rendered into antialiased RGBA pictures for Tk.

The authored chassis and nine-slice control plates in generated/menu-ui match the in-game menu. Text and hit targets
remain live canvas items. Procedural masks supply icons, focus animation and the customizable color controls.
"""
import base64
import colorsys
import re
import struct
import zlib
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------- colours

# The first colour scheme, Forest: bronze wood on a misty dark green.
BASE = {
    'window': '#06180d',
    'panel': '#091b11',
    'label_top': '#142d1c', 'label_mid': '#102719', 'label_bottom': '#0c2015', 'label_border': '#32432b',
    'ring': '#09190f',
    'value': '#0c2015', 'value_border': '#263b28',
    'orange': '#756335', 'orange_light': '#bca263', 'orange_dark': '#4e472b',
    'desc': '#0b1e14', 'desc_border': '#71603a',
    'text': '#e9dcc0', 'text_bright': '#f1e6cc', 'value_text': '#e3d5b6', 'desc_text': '#e9dfc8',
    'muted': '#d4c6a6', 'dim': '#7d786a', 'off': '#5e5b51', 'on_text': '#fdf4de', 'salmon': '#e88f6c',
    'ok': '#a7d68c', 'gold': '#e6b65a', 'band_text': '#f3ecdc', 'status': '#b9a988', 'log_text': '#cfc4ab',
    'line': '#655735', 'dot': '#a88c4f', 'filigree': '#c2a15a', 'glow': '#dbb969',
    'button_hover': '#2c4929', 'close_hover': '#b0402c',
    # the backdrop: the field's four heights, its mist, the see-through tint over it (edge, side, middle), the corners
    'scene_0': '#394244', 'scene_1': '#414b4c', 'scene_2': '#474234', 'scene_3': '#3e3523', 'mist': '#262a22',
    'tint_edge': '#05180d', 'tint_side': '#092615', 'tint_mid': '#0c2e1b', 'vignette': '#020e07',
    # the title band's wood from top to bottom and the two lines under it
    'wood_0': '#0d2116', 'wood_1': '#10281a', 'wood_2': '#102719', 'wood_3': '#0a1a11',
    'band_line_dark': '#13281a', 'band_line_light': '#8f7845',
    # the cursor's glow near and far from its frame, the sparks drifting over the backdrop, Navi's halo
    'glow_near': '#c3a45d', 'glow_far': '#89743d', 'spark': '#e5c778', 'halo': '#c1aa65',
    'icon_frame': '#e9dcc0', 'icon_fill': '#efe4ca', 'chevron_dim': '#d9ccae', 'bullet': '#c99a45',
    'badge_top': '#fbbd45', 'badge_bottom': '#e2942a', 'badge_ring': '#3a2a14', 'badge_text': '#3a2208',
    'text_shadow': '#06110b', 'band_shadow': '#06120b', 'frame': '#b19450',
}

# The colours a scheme turns together, each group to one hue.
GROUPS = {
    'wood': ('panel', 'label_top', 'label_mid', 'label_bottom', 'label_border', 'ring', 'desc', 'desc_border', 'line',
             'dot', 'filigree', 'button_hover', 'wood_0', 'wood_1', 'wood_2', 'wood_3', 'band_line_dark',
             'band_line_light', 'value', 'value_border', 'text_shadow', 'band_shadow', 'frame'),
    'accent': ('orange', 'orange_light', 'orange_dark', 'gold', 'badge_top', 'badge_bottom', 'badge_ring',
               'badge_text', 'bullet'),
    'scene': ('window', 'scene_0', 'scene_1', 'scene_2', 'scene_3', 'mist', 'tint_edge', 'tint_side', 'tint_mid',
              'vignette'),
    'glow': ('glow', 'glow_near', 'glow_far', 'spark', 'halo'),
    'text': ('text', 'text_bright', 'value_text', 'desc_text', 'muted', 'band_text', 'status', 'log_text', 'on_text',
             'icon_frame', 'icon_fill', 'chevron_dim'),
}

# The colour schemes of Customize Launcher, in the order the row steps through them. Each group: (hue in degrees or
# None to keep each colour's own, saturation factor, lightness factor, saturation added), applied to the first
# scheme's colours (recolor), so a scheme keeps the first one's light and dark steps. 'set': colours given as they are.
THEMES = {
    'forest': {'label': 'Forest'},
    'volcano': {'label': 'Volcano', 'wood': (14, 0.95, 0.80, 0), 'accent': (16, 1.05, 1.0, 0),
                       'scene': (6, 1.0, 1.0, 0.25), 'glow': (30, 1.0, 1.0, 0), 'text': (30, 1.0, 1.0, 0)},
    'lake': {'label': 'Lake', 'wood': (207, 0.75, 0.92, 0), 'accent': (188, 0.95, 1.0, 0),
             'scene': (200, 1.0, 1.05, 0.2), 'glow': (185, 1.0, 1.0, 0), 'text': (200, 0.55, 1.02, 0)},
    'desert': {'label': 'Desert', 'wood': (26, 1.15, 1.0, 0), 'accent': (352, 0.9, 1.0, 0),
               'scene': (318, 0.8, 0.95, 0.15), 'glow': (45, 1.0, 1.0, 0), 'text': (36, 1.0, 1.0, 0)},
    'temple': {'label': 'Temple', 'wood': (220, 0.22, 0.95, 0), 'accent': (46, 0.95, 1.02, 0),
               'scene': (222, 0.55, 1.0, 0.05), 'glow': (48, 1.0, 1.0, 0.5), 'text': (48, 0.45, 1.02, 0)},
    'shadow': {'label': 'Shadow', 'wood': (268, 0.55, 0.78, 0), 'accent': (286, 0.75, 1.0, 0),
               'scene': (272, 0.9, 0.8, 0.2), 'glow': (280, 1.0, 1.0, 0.2), 'text': (270, 0.5, 1.0, 0)},
    'custom': {'label': 'Custom'},
}
DEFAULT_THEME = 'forest'

# The Custom scheme: each colour the player picks takes the place of one colour of the first scheme (ref), and the
# rest of that colour's part of the window follows it, lighter and darker as in the first scheme (follow). Each part:
# (ref, the colours that follow it, the text on it, which turns dark when the picked colour is light). The buttons are
# the rows' label cells, their value cells and the orange choices in them; the panel is the list panel, the
# description box, the dividers and the title band. The colours start as the first scheme's.
CUSTOM_SLOTS = {
    'background': ('tint_mid', GROUPS['scene'], ()),
    'panel': ('panel', ('panel', 'desc', 'desc_border', 'line', 'dot', 'filigree', 'button_hover', 'wood_0', 'wood_1',
                        'wood_2', 'wood_3', 'band_line_dark', 'band_line_light', 'band_shadow', 'frame'),
              ('desc_text', 'muted', 'band_text', 'status', 'log_text')),
    'buttons': ('label_mid', ('label_top', 'label_mid', 'label_bottom', 'label_border', 'ring', 'value', 'value_border',
                              'text_shadow') + GROUPS['accent'],
                ('text', 'text_bright', 'value_text', 'on_text', 'icon_frame', 'icon_fill', 'chevron_dim')),
}
CUSTOM_DEFAULT = {slot: BASE[ref] for slot, (ref, _, _) in CUSTOM_SLOTS.items()}
CUSTOM = dict(CUSTOM_DEFAULT)
HEX = re.compile(r'^#[0-9a-f]{6}$')


def recolor(hexcolor, hue, sat=1.0, light=1.0, add=0.0):
    """hexcolor turned to hue (degrees; None keeps its own), its saturation times sat plus add, its lightness times
    light."""
    h = hexcolor.lstrip('#')
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    hh, ll, ss = colorsys.rgb_to_hls(r, g, b)
    if hue is not None:
        hh = (hue % 360) / 360.0
    r, g, b = colorsys.hls_to_rgb(hh, max(0.0, min(1.0, ll * light)), max(0.0, min(1.0, ss * sat + add)))
    return '#%02x%02x%02x' % tuple(int(round(v * 255)) for v in (r, g, b))


def hls(hexcolor):
    h = hexcolor.lstrip('#')
    return colorsys.rgb_to_hls(*(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)))


def hexcode(r, g, b):
    """#rrggbb of r, g, b in 0..1."""
    return '#%02x%02x%02x' % tuple(int(round(max(0.0, min(1.0, v)) * 255)) for v in (r, g, b))


def follow(hexcolor, ref, chosen):
    """hexcolor moved as ref moves to chosen: chosen's hue, and its lightness and saturation shifted the way ref's
    shift, so ref becomes chosen exactly and the colours lighter or darker than ref stay so."""
    _, l, s = hls(hexcolor)
    _, rl, rs = hls(ref)
    ch, cl, cs = hls(chosen)

    def move(v, r, c):
        if c <= r:
            return v * c / r if r > 0 else c
        return v + (1 - v) * (c - r) / (1 - r) if r < 1 else c
    return hexcode(*colorsys.hls_to_rgb(ch, max(0.0, min(1.0, move(l, rl, cl))), max(0.0, min(1.0, move(s, rs, cs)))))


def luminance(hexcolor):
    h = hexcolor.lstrip('#')
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def custom_palette(colors):
    """The Custom scheme's colours from {slot: '#rrggbb'} (CUSTOM_SLOTS); a slot left out or not a colour keeps the
    first scheme's. Text on a light button or panel turns dark, so it stays readable, and its shadow light."""
    pal = dict(BASE)
    for slot, (ref, keys, text) in CUSTOM_SLOTS.items():
        c = str((colors or {}).get(slot) or '').lower()
        if not HEX.match(c) or c == BASE[ref]:   # the first scheme's own colour: its colours as written
            continue
        for k in keys:
            pal[k] = follow(BASE[k], BASE[ref], c)
        if text and luminance(c) > 0.5:
            h = hls(c)[0] * 360
            for k in text:
                pal[k] = recolor(BASE[k], h, 0.5, 0.3 if k == 'chevron_dim' else 0.18)
            shadow = 'text_shadow' if slot == 'buttons' else 'band_shadow'
            pal[shadow] = follow(BASE['label_top'], BASE[ref], c)
        if slot == 'buttons' and luminance(pal['orange']) > 0.65:   # the choices come out lighter than the cells
            pal['on_text'] = recolor(BASE['on_text'], hls(c)[0] * 360, 0.5, 0.18)
    return pal


def theme_palette(name):
    """The colours of scheme name (an unknown name: the first scheme's)."""
    if name == 'custom':
        return custom_palette(CUSTOM)
    t = THEMES.get(name) or THEMES[DEFAULT_THEME]
    pal = dict(BASE)
    for group, keys in GROUPS.items():
        if group in t:
            for k in keys:
                pal[k] = recolor(BASE[k], *t[group])
    pal.update(t.get('set', {}))
    return pal


PALETTE = dict(BASE)
theme = DEFAULT_THEME


def set_theme(name, custom=None):
    """Fills PALETTE (in place: the window holds a reference) with scheme name's colours; the name in effect.
    custom: the Custom scheme's colours, {slot: '#rrggbb'} (CUSTOM_SLOTS)."""
    global theme
    if custom is not None:
        CUSTOM.clear()
        CUSTOM.update(CUSTOM_DEFAULT)
        CUSTOM.update({k: v.lower() for k, v in custom.items() if k in CUSTOM_SLOTS and HEX.match(str(v).lower())})
    theme = name if name in THEMES else DEFAULT_THEME
    PALETTE.clear()
    PALETTE.update(theme_palette(theme))
    return theme


def rgb(hexcolor):
    h = hexcolor.lstrip('#')
    return np.array([int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)], dtype=np.float32)


# ---------------------------------------------------------------- PNG and Tk

def png_bytes(img):
    """PNG of an HxWx4 float array in 0..1 (straight alpha) or uint8 array."""
    a = img if img.dtype == np.uint8 else (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)
    h, w = a.shape[:2]
    raw = b''.join(b'\0' + a[y].tobytes() for y in range(h))

    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 3)) + chunk(b'IEND', b''))


def photo(master, img):
    import tkinter as tk
    return tk.PhotoImage(master=master, data=base64.b64encode(png_bytes(img)).decode('ascii'), format='png')


def dib_icon(img):
    """One .ico entry as a 32-bit bitmap with its AND mask (what every Windows API and Tk read)."""
    a = img if img.dtype == np.uint8 else (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)
    n = a.shape[0]
    bgra = a[::-1, :, [2, 1, 0, 3]].tobytes()   # bottom-up rows
    stride = ((n + 31) // 32) * 4
    mask = bytearray()
    for row in a[::-1]:
        bits = bytearray(stride)
        for x in range(n):
            if row[x, 3] < 128:
                bits[x // 8] |= 0x80 >> (x % 8)
        mask += bits
    head = struct.pack('<IiiHHIIiiII', 40, n, 2 * n, 1, 32, 0, len(bgra) + len(mask), 0, 0, 0, 0)
    return head + bgra + bytes(mask)


def ico_bytes(images):
    """A Windows .ico of square HxWx4 images: bitmaps up to 64 pixels, PNG above (as Windows' own icons do)."""
    datas = [png_bytes(im) if im.shape[0] > 64 else dib_icon(im) for im in images]
    head = struct.pack('<HHH', 0, 1, len(datas))
    offset = 6 + 16 * len(datas)
    dirs, body = b'', b''
    for im, data in zip(images, datas):
        n = im.shape[0]
        dirs += struct.pack('<BBBBHHII', n % 256, n % 256, 0, 0, 1, 32, len(data), offset + len(body))
        body += data
    return head + dirs + body


# ---------------------------------------------------------------- drawing primitives

# The blurred masks and the noise depend on sizes only, not on colours: kept here, a change of the colour scheme
# paints them again in its colours without computing them again (most of the time a redraw took). The arrays kept
# are read, never written.
_KEPT = {}


def kept(key, make):
    if key not in _KEPT:
        if len(_KEPT) >= 160:   # row and box sizes of every screen stay far below this
            _KEPT.clear()
        _KEPT[key] = make()
    return _KEPT[key]


def new(h, w, color=None, alpha=0.0):
    img = np.zeros((h, w, 4), dtype=np.float32)
    if color is not None:
        img[..., :3] = rgb(color)
        img[..., 3] = alpha
    return img


def over(dst, src, x=0, y=0):
    """Composite src over dst at (x, y), straight alpha, clipped to dst."""
    h, w = src.shape[:2]
    H, W = dst.shape[:2]
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, W), min(y + h, H)
    if x0 >= x1 or y0 >= y1:
        return dst
    s = src[y0 - y:y1 - y, x0 - x:x1 - x]
    d = dst[y0:y1, x0:x1]
    sa, da = s[..., 3:4], d[..., 3:4]
    if da.min() >= 1:   # onto a solid picture (the backdrop's layers): a plain mix, the result stays solid
        d[..., :3] += (s[..., :3] - d[..., :3]) * sa
        return dst
    under = da * (1 - sa)
    oa = sa + under
    oc = s[..., :3] * sa
    oc += d[..., :3] * under
    oc /= np.maximum(oa, 1e-6)   # where both are clear the sum above is 0, and stays 0
    d[..., :3], d[..., 3:4] = oc, oa
    return dst


def paint(img, mask, color, alpha=1.0):
    """Paint a colour through a coverage mask (HxW, 0..1) onto img."""
    if mask.shape != img.shape[:2]:
        layer = new(*mask.shape, color)
        layer[..., 3] = np.clip(mask, 0, 1) * alpha
        return over(img, layer)
    # over() with one colour for the whole layer, without making the layer
    sa = (np.clip(mask, 0, 1) * alpha)[..., None].astype(np.float32)
    c, da = rgb(color), img[..., 3:4]
    if da.min() >= 1:
        img[..., :3] += (c - img[..., :3]) * sa
        return img
    under = da * (1 - sa)
    oa = sa + under
    oc = img[..., :3] * under
    oc += c * sa
    oc /= np.maximum(oa, 1e-6)
    img[..., :3], img[..., 3:4] = oc, oa
    return img


def vgradient(h, w, stops):
    """HxWx3 colours down the height through (position 0..1, colour) stops."""
    pos = np.array([p for p, _ in stops], dtype=np.float32)
    cols = np.stack([rgb(c) for _, c in stops])
    t = (np.arange(h, dtype=np.float32) + 0.5) / max(h, 1)
    col = np.stack([np.interp(t, pos, cols[:, k]) for k in range(3)], axis=1)
    return np.broadcast_to(col[:, None, :], (h, w, 3)).copy()


def hgradient_alpha(h, w, stops):
    """HxW alphas across the width through (position, alpha) stops."""
    pos = np.array([p for p, _ in stops], dtype=np.float32)
    al = np.array([a for _, a in stops], dtype=np.float32)
    t = (np.arange(w, dtype=np.float32) + 0.5) / max(w, 1)
    return np.broadcast_to(np.interp(t, pos, al)[None, :], (h, w)).copy()


def hgradient_rgba(h, w, stops):
    """An RGBA layer whose colour and alpha run across the width through (position, colour, alpha) stops."""
    pos = np.array([p for p, _, _ in stops], dtype=np.float32)
    cols = np.stack([rgb(c) for _, c, _ in stops])
    al = np.array([a for _, _, a in stops], dtype=np.float32)
    t = (np.arange(w, dtype=np.float32) + 0.5) / max(w, 1)
    layer = new(h, w)
    for k in range(3):
        layer[..., k] = np.interp(t, pos, cols[:, k])[None, :]
    layer[..., 3] = np.interp(t, pos, al)[None, :]
    return layer


def value_noise(h, w, period_x, period_y=None, seed=0):
    """Smooth noise in 0..1 with features about period pixels apart."""
    period_y = period_y or period_x
    rng = np.random.default_rng(seed)
    gh, gw = int(h / period_y) + 3, int(w / period_x) + 3
    g = rng.random((gh, gw), dtype=np.float32)
    ys = np.arange(h, dtype=np.float32) / period_y
    xs = np.arange(w, dtype=np.float32) / period_x
    y0, x0 = ys.astype(int), xs.astype(int)
    fy, fx = ys - y0, xs - x0
    fy, fx = fy * fy * (3 - 2 * fy), fx * fx * (3 - 2 * fx)
    a, b = g[y0][:, x0], g[y0][:, x0 + 1]
    c, d = g[y0 + 1][:, x0], g[y0 + 1][:, x0 + 1]
    top = a + (b - a) * fx[None, :]
    bot = c + (d - c) * fx[None, :]
    return top + (bot - top) * fy[:, None]


def fractal(h, w, period_x, period_y=None, octaves=3, seed=0, contrast=0.4):
    """Fractal noise around mid grey, about contrast wide (the range of an SVG feTurbulence turned grey)."""
    return 0.5 + (_fractal01(h, w, period_x, period_y, octaves, seed) - 0.5) * contrast


def _fractal01(h, w, period_x, period_y=None, octaves=3, seed=0):
    return kept(('fractal', h, w, period_x, period_y, octaves, seed),
                lambda: _fractal01_make(h, w, period_x, period_y, octaves, seed))


def _fractal01_make(h, w, period_x, period_y, octaves, seed):
    period_y = period_y or period_x
    out = np.zeros((h, w), dtype=np.float32)
    amp, total = 1.0, 0.0
    for o in range(octaves):
        out += amp * value_noise(h, w, max(period_x / 2 ** o, 1.5), max(period_y / 2 ** o, 1.5), seed + o * 101)
        total += amp
        amp *= 0.5
    out /= total
    lo, hi = np.percentile(out, 1), np.percentile(out, 99)
    return np.clip((out - lo) / max(hi - lo, 1e-6), 0, 1)


def overlay(rgb_arr, n, opacity):
    """CSS mix-blend-mode overlay of a grey noise n (HxW) over colours, at opacity."""
    n = n[..., None]
    blended = np.where(rgb_arr < 0.5, 2 * rgb_arr * n, 1 - 2 * (1 - rgb_arr) * (1 - n))
    return rgb_arr + (blended - rgb_arr) * opacity


def box_blur(a, r):
    """Three box blurs of radius r over a 2-D array (close to a gaussian)."""
    r = int(round(r))
    if r < 1:
        return a
    out = a.astype(np.float32)
    for _ in range(3):
        for axis in (0, 1):
            pad = [(0, 0), (0, 0)]
            pad[axis] = (r + 1, r)
            p = np.pad(out, pad, mode='edge')
            c = np.cumsum(p, axis=axis, dtype=np.float64)
            if axis == 0:
                out = ((c[2 * r + 1:] - c[:-2 * r - 1]) / (2 * r + 1)).astype(np.float32)
            else:
                out = ((c[:, 2 * r + 1:] - c[:, :-2 * r - 1]) / (2 * r + 1)).astype(np.float32)
    return out


def rect_mask(h, w, x0, y0, x1, y1):
    """Coverage of the rectangle [x0, x1) x [y0, y1) with fractional edges."""
    xs = np.arange(w, dtype=np.float32)
    ys = np.arange(h, dtype=np.float32)
    cx = np.clip(np.minimum(xs + 1, x1) - np.maximum(xs, x0), 0, 1)
    cy = np.clip(np.minimum(ys + 1, y1) - np.maximum(ys, y0), 0, 1)
    return cy[:, None] * cx[None, :]


def ring_mask(h, w, x0, y0, x1, y1, width):
    return np.clip(rect_mask(h, w, x0, y0, x1, y1) - rect_mask(h, w, x0 + width, y0 + width, x1 - width, y1 - width),
                   0, 1)


def polygon_mask(h, w, pts, ss=4):
    """Antialiased coverage of a convex polygon (pixel coordinates), supersampled ss x ss."""
    ys = (np.arange(h * ss, dtype=np.float32) + 0.5) / ss
    xs = (np.arange(w * ss, dtype=np.float32) + 0.5) / ss
    X, Y = np.meshgrid(xs, ys)
    inside = np.ones_like(X, dtype=bool)
    n = len(pts)
    area = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))
    sign = 1 if area > 0 else -1
    for i in range(n):
        (ax, ay), (bx, by) = pts[i], pts[(i + 1) % n]
        inside &= sign * ((bx - ax) * (Y - ay) - (by - ay) * (X - ax)) >= 0
    return inside.reshape(h, ss, w, ss).mean(axis=(1, 3)).astype(np.float32)


def stroke_mask(h, w, polylines, width):
    """Antialiased coverage of polylines (lists of (x, y)) stroked with round joins and caps."""
    ys = np.arange(h, dtype=np.float32) + 0.5
    xs = np.arange(w, dtype=np.float32) + 0.5
    X, Y = np.meshgrid(xs, ys)
    best = np.full((h, w), 1e9, dtype=np.float32)
    for line in polylines:
        P = np.asarray(line, dtype=np.float32)
        if len(P) == 1:
            P = np.vstack([P, P])
        for (ax, ay), (bx, by) in zip(P[:-1], P[1:]):
            dx, dy = bx - ax, by - ay
            ll = dx * dx + dy * dy
            if ll < 1e-9:
                d = np.hypot(X - ax, Y - ay)
            else:
                t = np.clip(((X - ax) * dx + (Y - ay) * dy) / ll, 0, 1)
                d = np.hypot(X - (ax + t * dx), Y - (ay + t * dy))
            np.minimum(best, d, out=best)
    return np.clip(width / 2 + 0.5 - best, 0, 1)


def shadow(h, w, mask, color, alpha, blur, dx=0, dy=0):
    """A soft shadow of a mask: blurred, offset, as an RGBA layer of the same size."""
    m = box_blur(np.roll(np.roll(mask, int(dy), 0), int(dx), 1), blur / 2)
    layer = new(h, w, color)
    layer[..., 3] = np.clip(m, 0, 1) * alpha
    return layer


# ---------------------------------------------------------------- SVG path data (the filigree)

def svg_polylines(d, scale=1.0, ox=0.0, oy=0.0, steps=10):
    """Points along an SVG path made of M, L, C and Z commands."""
    toks = re.findall(r'[MLCZmlcz]|-?\d*\.?\d+', d)
    lines, cur, pos, i, cmd = [], [], (0.0, 0.0), 0, None
    start = (0.0, 0.0)

    def num():
        nonlocal i
        v = float(toks[i])
        i += 1
        return v

    while i < len(toks):
        if toks[i] in 'MLCZmlcz':
            cmd = toks[i]
            i += 1
            if cmd in 'Zz':
                cur.append(start)
                continue
        if cmd in 'Mm':
            if cur:
                lines.append(cur)
            pos = (num(), num())
            start = pos
            cur = [pos]
            cmd = 'L'
        elif cmd in 'Ll':
            pos = (num(), num())
            cur.append(pos)
        elif cmd in 'Cc':
            p0 = pos
            c1, c2, p1 = (num(), num()), (num(), num()), (num(), num())
            for k in range(1, steps + 1):
                t = k / steps
                u = 1 - t
                cur.append((u ** 3 * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t ** 3 * p1[0],
                            u ** 3 * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t ** 3 * p1[1]))
            pos = p1
    if cur:
        lines.append(cur)
    return [[((x - ox) * scale, (y - oy) * scale) for x, y in line] for line in lines]


# The game's divider ornament, traced from the options menu: two pointed lens ends, S-waves into spiral hooks and a
# diamond knot under the middle, in a 120 x 38 box (centre x 60).
FILIGREE_PATHS = (
    'M20 17.4 C20.4 17.3 21.6 16.8 22.2 16.9 C22.8 17 23.5 17.7 23.5 18.2 C23.5 18.7 23.1 19.4 22.4 19.8 C21.6 20.2 '
    '20.3 20.4 19 20.4 C17.7 20.4 15.9 20.2 14.5 19.9 C13.1 19.6 10.6 19.4 10.5 18.6 C10.4 17.9 12.6 16.3 14 15.4 '
    'C15.4 14.5 17.2 13.6 19 13.2 C20.8 12.8 23.2 12.8 25 13.1 C26.8 13.4 28.1 13.9 29.5 14.8 C30.9 15.7 32.2 17.4 '
    '33.5 18.6 C34.8 19.8 36 21.2 37.5 22 C39 22.8 40.8 23.4 42.5 23.6 C44.2 23.8 46 23.5 47.5 23.1 C49 22.7 50.4 '
    '21.9 51.2 21.2 C52 20.5 52.5 19.5 52.4 18.8 C52.3 18.1 51.5 17.3 50.6 16.9 C49.7 16.5 48.2 16.2 47 16.3 C45.8 '
    '16.4 44.2 17.1 43.6 17.2',
    'M100 17.4 C99.6 17.3 98.4 16.8 97.8 16.9 C97.2 17 96.5 17.7 96.5 18.2 C96.5 18.7 96.8 19.4 97.6 19.8 C98.3 20.2 '
    '99.7 20.4 101 20.4 C102.3 20.4 104.1 20.2 105.5 19.9 C106.9 19.6 109.4 19.4 109.5 18.6 C109.6 17.9 107.4 16.3 '
    '106 15.4 C104.6 14.5 102.8 13.6 101 13.2 C99.2 12.8 96.8 12.8 95 13.1 C93.2 13.4 91.9 13.9 90.5 14.8 C89.1 15.7 '
    '87.8 17.4 86.5 18.6 C85.2 19.8 84 21.2 82.5 22 C81 22.8 79.2 23.4 77.5 23.6 C75.8 23.8 74 23.5 72.5 23.1 C71 '
    '22.7 69.6 21.9 68.8 21.2 C68 20.5 67.5 19.5 67.6 18.8 C67.7 18.1 68.5 17.3 69.4 16.9 C70.3 16.5 71.8 16.2 73 '
    '16.3 C74.2 16.4 75.8 17.1 76.4 17.2',
    'M40.6 17.6 C40.7 17 40.7 14.9 41.2 14 C41.7 13.1 42.6 12.4 43.8 12 C45 11.6 46.7 11.4 48.2 11.5 C49.7 11.6 51.3 '
    '11.8 52.6 12.3 C53.9 12.8 55 13.6 56 14.6 C57 15.6 58.2 17.6 58.6 18.2 L64.6 26 L60 31.6',
    'M79.4 17.6 C79.3 17 79.3 14.9 78.8 14 C78.3 13.1 77.4 12.4 76.2 12 C75 11.6 73.3 11.4 71.8 11.5 C70.3 11.6 68.7 '
    '11.8 67.4 12.3 C66.1 12.8 65 13.6 64 14.6 C63 15.6 61.8 17.6 61.4 18.2 L55.4 26 L60 31.6',
)


# ---------------------------------------------------------------- the window's pieces

def backdrop(W, H, s, picture=None, dim=0.4, top=0):
    """The whole window behind the panels: a dim misty field under a see-through tint of the colour scheme, darker at
    the sides. picture: the player's own background instead of the field, an array of H - top rows and W columns in
    0..1 put below the title band (top: where the band ends), darkened by dim (0 as it is .. 1 the scheme's darkest
    colour) so the text over it stays readable."""
    P = PALETTE
    img = new(H, W, P['window'], 1.0)
    pad = int(45 * s)
    vig = kept(('vignette', W, H, s), lambda: 1 - box_blur(np.pad(rect_mask(H, W, 0, 0, W, H), pad,
                                                                   constant_values=0), 45 * s)[pad:-pad, pad:-pad])
    if picture is not None:
        part = picture[:H - top, :W]
        img[top:top + part.shape[0], :part.shape[1], :3] = part
        paint(img, np.ones((H, W), dtype=np.float32), P['tint_edge'], max(0.0, min(1.0, dim)))
        paint(img, np.clip(vig * 1.6, 0, 1), P['vignette'], 0.45)
        return img
    m = int(30 * s)
    scene = new(H, W - 2 * m)
    scene[..., :3] = vgradient(H, W - 2 * m, [(0, P['scene_0']), (0.36, P['scene_1']), (0.60, P['scene_2']),
                                              (1, P['scene_3'])])
    scene[..., 3] = 1
    over(img, scene, m, 0)
    def mist():
        band = np.zeros((H, W), dtype=np.float32)
        y0, y1 = int(250 * s), int(330 * s)
        t = (np.arange(y1 - y0) + 0.5) / max(y1 - y0, 1)
        band[y0:y1, m:W - m] = (np.interp(t, [0, 0.55, 1], [0, 0.85, 0]))[:, None]
        return box_blur(band, 10 * s)
    paint(img, kept(('mist', W, H, s), mist), P['mist'])
    over(img, hgradient_rgba(H, W, [(0, P['tint_edge'], 0.96), (0.14, P['tint_side'], 0.82), (0.5, P['tint_mid'], 0.72),
                                    (0.86, P['tint_side'], 0.82), (1, P['tint_edge'], 0.96)]))
    paint(img, np.clip(vig * 1.6, 0, 1), P['vignette'], 0.6)
    return img


def hsv_rgb(h, s, v):
    """HSV to RGB on arrays (or numbers) in 0..1; an H... x 3 array."""
    h, s, v = (np.asarray(a, dtype=np.float32) for a in (h, s, v))
    h6 = (h % 1.0) * 6
    c = v * s
    x = c * (1 - np.abs(h6 % 2 - 1))
    z = np.zeros_like(c)
    k = np.floor(h6).astype(int) % 6
    r = np.choose(k, [c, x, z, z, x, c])
    g = np.choose(k, [x, c, c, x, z, z])
    b = np.choose(k, [z, z, x, c, c, x])
    m = v - c
    return np.stack([r + m, g + m, b + m], axis=-1)


def color_square(w, h, hue):
    """The colour square of the Custom scheme's picker for one hue (0..1): saturation from grey on the left to the
    full colour on the right, brightness from black at the bottom to full at the top."""
    sat = (np.arange(w, dtype=np.float32) + 0.5) / w
    val = 1 - (np.arange(h, dtype=np.float32) + 0.5) / h
    img = new(h, w)
    img[..., :3] = hsv_rgb(np.full((h, w), hue), np.broadcast_to(sat, (h, w)), np.broadcast_to(val[:, None], (h, w)))
    img[..., 3] = 1
    return img


def hue_bar(w, h):
    """The picker's hue bar: every hue from red on the left through yellow, green, cyan, blue and magenta to red."""
    hue = (np.arange(w, dtype=np.float32) + 0.5) / w
    img = new(h, w)
    img[..., :3] = hsv_rgb(np.broadcast_to(hue, (h, w)), np.ones((h, w)), np.ones((h, w)))
    img[..., 3] = 1
    return img


def picker_mark(size, s):
    """The ring that shows the picked colour on the square and the bar: white with a dark edge inside and out."""
    t = (np.arange(size, dtype=np.float32) + 0.5) - size / 2
    d = np.hypot(t[None, :], t[:, None])
    r = size / 2 - 1.5 * s
    img = new(size, size, '#000000')
    dark = np.clip(1.6 * s - np.abs(d - r) + 0.5, 0, 1) * 0.7
    white = np.clip(0.9 * s - np.abs(d - r) + 0.5, 0, 1)
    img[..., 3] = np.maximum(dark, white)
    img[..., :3] = np.where(white[..., None] > dark[..., None] * 0.5, 1.0, 0.0)
    return img


def spark(size, alpha=1.0):
    """One spark of the dust drifting over the backdrop: a soft round dot of the scheme's spark colour, size pixels
    across, brightest in its middle."""
    ys = (np.arange(size, dtype=np.float32) + 0.5) / size * 2 - 1
    d = np.hypot(ys[:, None], ys[None, :])
    img = new(size, size, PALETTE['spark'])
    img[..., 3] = np.clip(1 - d, 0, 1) ** 1.6 * alpha
    core = np.clip(1 - d * 2.6, 0, 1)
    img[..., :3] += (1 - img[..., :3]) * core[..., None]   # a white middle
    return img


def halo_frames(size, n=12):
    """The light around Navi in the title band: a soft disc of the scheme's halo colour, n frames of one slow pulse."""
    ys = (np.arange(size, dtype=np.float32) + 0.5) / size * 2 - 1
    d = np.hypot(ys[:, None], ys[None, :])
    fall = np.clip(1 - d, 0, 1) ** 2
    frames = []
    for k in range(n):
        t = 0.5 - 0.5 * np.cos(2 * np.pi * k / n)
        img = new(size, size, PALETTE['halo'])
        img[..., 3] = fall * (0.30 + 0.30 * t)
        frames.append(img)
    return frames


def band(w, h, s):
    """The wooden title band, its two-line bottom edge and the shadow under it; (image, extra height below)."""
    pad = int(14 * s)
    img = new(h + pad, w)
    wood = vgradient(h, w, [(0, PALETTE['wood_0']), (0.30, PALETTE['wood_1']), (0.65, PALETTE['wood_2']),
                            (1, PALETTE['wood_3'])])
    wood = overlay(wood, fractal(h, w, 33 * s, None, 3, seed=4), 0.055)
    over(img, kept(('band shadow', w, h, s), lambda: shadow(h + pad, w, rect_mask(h + pad, w, 0, 0, w, h + 4 * s),
                                                            '#000000', 0.6, 16 * s, 0, 5 * s)))
    top = new(h, w)
    top[..., :3], top[..., 3] = wood, 1
    over(img, top)
    paint(img, rect_mask(h + pad, w, 0, h, w, h + 2 * s), PALETTE['band_line_dark'])
    paint(img, rect_mask(h + pad, w, 0, h + 2 * s, w, h + 4 * s), PALETTE['band_line_light'])
    return img


def botanical(size, s, alpha=1.0):
    """An engraved corner sprig. Curved stems, pointed leaves and veins are rendered at display resolution."""
    img = new(size, size)
    lines, veins = [], []
    for angle in (0.12, 1.45):
        stem = []
        for t in np.linspace(0, 1, 28):
            x, y = 12*s + np.cos(angle)*t*size*.8, 12*s + np.sin(angle)*t*size*.8
            stem.append((x, y))
        lines.append(stem)
        for k in range(1, 8):
            t = k/9
            x, y = 12*s + np.cos(angle)*t*size*.8, 12*s + np.sin(angle)*t*size*.8
            for sign in (-1, 1):
                a = angle + sign*.8
                length = (11 + 7*np.sin(t*np.pi))*s
                tip = (x+np.cos(a)*length, y+np.sin(a)*length)
                mx, my = (x+tip[0])/2, (y+tip[1])/2
                width = length*.23
                leaf = [(x, y)]
                for q in np.linspace(0, 1, 9):
                    leaf.append(((1-q)**2*x + 2*(1-q)*q*(mx-np.sin(a)*width) + q*q*tip[0],
                                 (1-q)**2*y + 2*(1-q)*q*(my+np.cos(a)*width) + q*q*tip[1]))
                for q in np.linspace(0, 1, 9):
                    leaf.append(((1-q)**2*tip[0] + 2*(1-q)*q*(mx+np.sin(a)*width) + q*q*x,
                                 (1-q)**2*tip[1] + 2*(1-q)*q*(my-np.cos(a)*width) + q*q*y))
                lines.append(leaf)
                veins.append([(x,y),tip])
    paint(img, stroke_mask(size,size,lines,1.15*s), PALETTE['gold'], alpha)
    paint(img, stroke_mask(size,size,veins,.65*s), PALETTE['filigree'], alpha*.65)
    return img


def ornate_frame(w, h, s):
    """The launcher's own brass frame, shared visually with the in-game menu. No game artwork is needed."""
    img = new(h,w)
    for inset, width, color in ((12,1.4,'gold'),(16,.7,'filigree'),(21,.6,'line')):
        paint(img,ring_mask(h,w,inset*s,inset*s,w-inset*s,h-inset*s,max(.8,width*s)),PALETTE[color])
    size = min(round(120*s),w//3,h//3)
    tile = botanical(size,s)
    for right, bottom in ((False,False),(True,False),(False,True),(True,True)):
        part=tile[::-1] if bottom else tile
        part=part[:,::-1] if right else part
        over(img,part,w-size-round(12*s) if right else round(12*s),h-size-round(12*s) if bottom else round(12*s))
    return img


def ocarina_emblem(size, s):
    """Gold line relief of a transverse ocarina, with its chamber and side mouthpiece drawn separately."""
    img=new(size,size)
    scale=size/100
    paths=['M 25 43 L 16 18 L 29 13 L 39 40',
           'M 86 39 C 56 16 16 28 12 54 C 8 87 48 87 86 39 Z',
           'M 19 62 C 25 82 53 68 77 45', 'M 20 26 L 32 21']
    lines=[]
    for path in paths:
        lines.extend(svg_polylines(path,scale,0,0,steps=20))
    for x,y,r in ((26,51,4),(39,40,3.3),(53,38,3),(67,39,2.5),(40,62,4),(55,53,3.4)):
        lines.append([(scale*(x+r*np.cos(a)),scale*(y+r*np.sin(a))) for a in np.linspace(0,2*np.pi,24)])
    paint(img,stroke_mask(size,size,lines,1.3*scale),PALETTE['gold'])
    sprig=botanical(size,s*.55,.6)
    over(img,sprig[:,::-1])
    return img


def _forest_source(name, alpha=False):
    from tools.build_menu_art import png_rgb
    source=Path(__file__).resolve().parents[2]/'generated'/'menu-ui'/name
    def load():
        w,h,pixels=png_rgb(source,alpha)
        return np.frombuffer(pixels,dtype=np.uint8).reshape(h,w,4 if alpha else 3).copy()
    return kept(('forest-source',name),load)


def forest_skin(w,h,picture=None,dim=.4,cutout=False):
    """The same authored frame and forest materials embedded by the runtime. Text stays live canvas content."""
    from .picture import resize
    source=_forest_source('forest-chassis.png',True)
    img=new(h,w)
    alpha=resize(np.repeat(source[...,3:4],3,axis=2),w,h)/255
    alpha[alpha<=8/255]=0
    color=resize(source[...,:3],w,h)/255
    background=np.broadcast_to(rgb('#010203' if cutout else PALETTE['window']),(h,w,3)).copy()
    if picture is not None:
        from .picture import cover
        background=cover((np.clip(picture,0,1)*255).astype(np.uint8),w,h,True)/255*(1-dim)
    img[...,:3]=color*alpha+background*(1-alpha)
    img[...,3]=1
    if theme != 'forest':
        surface=(img[...,1]>img[...,0]*.95)&(img[...,:3].mean(axis=2)<.35)&(alpha[...,0]>.5)
        factor=rgb(PALETTE['panel'])/np.maximum(rgb(BASE['panel']),.03)
        img[...,:3][surface]=np.clip(img[...,:3][surface]*factor,0,1)
    return img


def forest_bounds(w,h):
    """Tight client bounds around the actual gold artwork, including its protruding crest and jewels."""
    source=_forest_source('forest-chassis.png',True)
    ys,xs=np.where(source[...,3]>8)
    sh,sw=source.shape[:2]
    return (max(0,int(xs.min()*w/sw)-1),max(0,int(ys.min()*h/sh)-1),
            min(w,int((xs.max()+1)*w/sw)+2),min(h,int((ys.max()+1)*h/sh)+2))


def forest_widget(w,h,kind=0):
    """Nine-sliced emerald selection, gold action plate or jewel divider; source alpha is preserved."""
    from .picture import resize
    src=_forest_source('forest-widgets.png',True)
    rects=((48,80,2127,232),(640,319,1532,496),(52,550,2120,677))
    x0,y0,x1,y1=rects[kind]
    src=src[y0:y1,x0:x1]
    def scale(part,pw):
        if pw<=0: return new(h,0)
        # Premultiplication avoids dark fringes around the gold's antialiased alpha edges.
        a=part[...,3:4].astype(np.float32)/255
        color=resize(part[...,:3].astype(np.float32)*a,pw,h)/255
        alpha=resize(np.repeat(a,3,axis=2),pw,h)[...,0:1]
        return np.concatenate((color/np.maximum(alpha,1e-6),alpha),axis=2)
    cut=330 if kind==0 else 210
    if kind==2:
        left,right=786-x0,1386-x0
        middle=min(round(600*h/(y1-y0)),int(w*.6))
        a=(w-middle)//2
        return np.concatenate((scale(src[:,:left],a),scale(src[:,left:right],middle),scale(src[:,right:],w-a-middle)),axis=1)
    cap=min(round(cut*h/(y1-y0)),int(w*.35))
    return np.concatenate((scale(src[:,:cut],cap),scale(src[:,cut:-cut],w-2*cap),scale(src[:,-cut:],cap)),axis=1)


def navigation_icon(kind,size):
    paths={
        'home':['M 9 5 L 25 16 L 9 27 Z'],
        'updates':['M 26 12 C 22 2 9 4 5 13 M 5 13 L 5 6 M 5 13 L 12 13',
                   'M 6 20 C 10 30 23 28 27 19 M 27 19 L 20 19 M 27 19 L 27 26'],
        'versions':['M 5 4 L 24 4 L 24 23 L 5 23 Z M 10 27 L 28 27 L 28 9'],
        'saves':['M 5 4 L 25 4 L 28 8 L 28 28 L 5 28 Z M 10 4 L 10 12 L 22 12 L 22 4',
                 'M 11 28 L 11 19 L 23 19 L 23 28'],
        'mods':['M 4 6 L 13 6 C 10 -2 24 -2 21 6 L 28 6 L 28 14 C 20 11 20 24 28 21 L 28 28 L 20 28 C 23 20 10 20 13 28 L 4 28 Z'],
        'customize':['M 6 25 L 22 5 L 28 10 L 12 29 Z M 6 25 L 3 30 L 12 29 M 19 9 L 25 14'],
        'settings':['M 16 3 L 27 10 L 27 23 L 16 30 L 5 23 L 5 10 Z',
                    'M 22 16 C 22 24 10 24 10 16 C 10 8 22 8 22 16 Z']}
    lines=[]
    for path in paths[kind]: lines.extend(svg_polylines(path,size/32,steps=16))
    img=new(size,size)
    paint(img,stroke_mask(size,size,lines,max(1.0,size/20)),PALETTE['gold'])
    return img


def panel(w, h, s, alpha=1.0):
    """The left list panel with the shadow on its right. alpha below 1 lets the player's own background show through
    it."""
    sh = int(26 * s)
    img = new(h, w + sh)
    over(img, kept(('panel shadow', w, h, s), lambda: shadow(h, w + sh, rect_mask(h, w + sh, 0, 0, w, h), '#000000',
                                                             0.35, 18 * s, 6 * s, 0)))
    col = np.broadcast_to(rgb(PALETTE['panel']), (h, w, 3)).copy()
    col = overlay(col, fractal(h, w, 33 * s, None, 3, seed=4), 0.055)
    layer = new(h, w)
    layer[..., :3], layer[..., 3] = col, 1
    over(img, layer)
    if alpha < 1:
        img[:, :w, 3] = alpha
    return img


def label_cell(w, h, s, bright=False):
    """A bronze label cell: gradient, mottled texture, 1 px border and the 1 px dark ring around the row."""
    r = max(1, int(round(s)))
    img = new(h + 2 * r, w + 2 * r, PALETTE['ring'], 1.0)
    col = vgradient(h, w, [(0, PALETTE['label_top']), (0.5, PALETTE['label_mid']), (1, PALETTE['label_bottom'])])
    col = overlay(col, fractal(h, w, 33 * s, None, 3, seed=(w * 7 + h) % 997), 0.05)
    if bright:
        col = np.clip(col * 1.32, 0, 1)
    cell = new(h, w)
    cell[..., :3], cell[..., 3] = col, 1
    over(img, cell, r, r)
    paint(img, ring_mask(h + 2 * r, w + 2 * r, r, r, w + r, h + r, r), PALETTE['label_border'])
    return img


def desc_box(w, h, s, alpha=1.0):
    pad = int(20 * s)
    img = new(h + 2 * pad, w + 2 * pad)
    body = rect_mask(h + 2 * pad, w + 2 * pad, pad, pad, pad + w, pad + h)
    over(img, shadow(h + 2 * pad, w + 2 * pad, body, '#000000', 0.35, 18 * s, 0, 6 * s))
    col = np.broadcast_to(rgb(PALETTE['desc']), (h, w, 3)).copy()
    col = overlay(col, fractal(h, w, 33 * s, None, 3, seed=11), 0.045)
    layer = new(h, w)
    layer[..., :3], layer[..., 3] = col, 1
    over(img, layer, pad, pad)
    if alpha < 1:   # the player's own background shows through the box, not through its border
        img[pad:pad + h, pad:pad + w, 3] = alpha
    paint(img, ring_mask(h + 2 * pad, w + 2 * pad, pad, pad, pad + w, pad + h, max(1, round(s))),
          PALETTE['desc_border'])
    return img, pad


def glow_frames(w, h, s, n=12):
    """The selection cursor around a w x h box: a 3 px white frame 6 px outside it with a soft glow of the scheme's
    colour inside and out, n frames of one breath. Returns (frames, margin) where the box sits at (margin, margin)."""
    gap, thick, spread = 2 * s, 1.2 * s, 7 * s
    mg = int(gap + spread)
    H, W = h + 2 * mg, w + 2 * mg
    ring = ring_mask(H, W, mg - gap, mg - gap, mg + w + gap, mg + h + gap, thick)
    soft_out = kept(('glow near', w, h, s), lambda: np.clip(box_blur(ring, 6 * s) * 2.8, 0, 1))
    soft_far = kept(('glow far', w, h, s), lambda: np.clip(box_blur(ring, 12 * s) * 2.2, 0, 1))
    frames = []
    for k in range(n):
        t = 0.5 - 0.5 * np.cos(2 * np.pi * k / n)
        img = new(H, W)
        paint(img, soft_far, PALETTE['glow_far'], 0.12 + 0.07 * t)
        paint(img, soft_out, PALETTE['glow_near'], 0.26 + 0.14 * t)
        paint(img, ring, PALETTE['glow'])
        frames.append(img)
    return frames, mg


def filigree(w, s):
    """The divider: a thin line with a dot at each end and the ornament in the middle; (image, height)."""
    h = int(round(26 * s))
    img = new(h, w)
    mid = h / 2
    orn_w = 78 * s
    scale = orn_w / 108.0
    ox = (w - orn_w) / 2
    dot_r = 1.5 * s
    gap = 5 * s
    left_end, right_start = ox - gap, ox + orn_w + gap
    xs0, xs1 = 4 * s + 2 * dot_r + gap, w - 4 * s - 2 * dot_r - gap
    paint(img, rect_mask(h, w, xs0, mid - 0.5 * s, left_end, mid + 0.5 * s), PALETTE['line'])
    paint(img, rect_mask(h, w, right_start, mid - 0.5 * s, xs1, mid + 0.5 * s), PALETTE['line'])
    dots = stroke_mask(h, w, [[(4 * s + dot_r, mid)], [(w - 4 * s - dot_r, mid)]], 2 * dot_r)
    paint(img, dots, PALETTE['dot'])
    lines = []
    for d in FILIGREE_PATHS:
        for line in svg_polylines(d, scale, 6, 8):
            lines.append([(x + ox, y + mid - 13 * scale) for x, y in line])
    paint(img, stroke_mask(h, w, lines, 2.2 * scale), PALETTE['filigree'])
    return img, h


# Navi, the launcher's emblem and icon: 22 x 21 pixels as the artwork has them. W white (the outline and the
# wings' highlights), g the wings' grey, K the black outline of the body, B and L its two blues, . transparent.
NAVI = (
    'WWWW..............WWWW',
    'WggWW............WWggW',
    'WgggWW..........WWgggW',
    'WgWggWW........WWggWgW',
    'WggWggW........WggWggW',
    'WgWgWgWW......WWgWgWgW',
    'WggggWgWKKKKKKWgWggggW',
    'WWggWggKBBBBBBKggWggWW',
    '.WggggKKBBLLBBKKggggW.',
    '.WWggKKBBLLLLBBKKggWW.',
    '..WWgKBBLLWWLLBBKgWW..',
    '...WWKBBLWWWWLBBKWW...',
    '.....KBBLLWWLLBBK.....',
    '.....KBBBLLLLBBBK.....',
    '.....WKKBBBBBBKKW.....',
    '....WWgKKBBBBKKgWW....',
    '...WWgggKKKKKKgggWW...',
    '...WggWgW....WgWggW...',
    '...WgWggW....WggWgW...',
    '...WWggWW....WWggWW...',
    '....WWWW......WWWW....',
)
NAVI_COLORS = {'W': (255, 255, 255), 'g': (207, 216, 220), 'K': (0, 0, 0), 'B': (2, 136, 209), 'L': (79, 195, 247)}


def navi_pixels():
    """The artwork as a 21 x 22 RGBA uint8 array."""
    a = np.zeros((len(NAVI), len(NAVI[0]), 4), dtype=np.uint8)
    for y, row in enumerate(NAVI):
        for x, ch in enumerate(row):
            if ch in NAVI_COLORS:
                a[y, x, :3] = NAVI_COLORS[ch]
                a[y, x, 3] = 255
    return a


def navi(k):
    """Navi with every artwork pixel k x k screen pixels (crisp edges), as a float RGBA array."""
    a = np.repeat(np.repeat(navi_pixels(), k, axis=0), k, axis=1)
    return a.astype(np.float32) / 255.0


def navi_icon(n):
    """Navi on a transparent n x n square: whole-pixel scaling where the size allows it, an average of a large
    rendering below 22 pixels."""
    sq = np.zeros((n, n, 4), dtype=np.float32)
    rows, cols = len(NAVI), len(NAVI[0])
    if n >= cols:
        img = navi(n // cols)
    else:   # premultiplied box average of an 8x rendering, so the edges blend instead of dropping pixels
        big = navi(8)
        H, W = big.shape[:2]
        h, w = max(1, round(rows * n / cols)), n
        ys = (np.arange(h + 1) * H / h).astype(int)
        xs = (np.arange(w + 1) * W / w).astype(int)
        pm = big.copy()
        pm[..., :3] *= pm[..., 3:4]
        img = np.zeros((h, w, 4), dtype=np.float32)
        for y in range(h):
            for x in range(w):
                img[y, x] = pm[ys[y]:ys[y + 1], xs[x]:xs[x + 1]].mean(axis=(0, 1))
        img[..., :3] = np.where(img[..., 3:4] > 0, img[..., :3] / np.maximum(img[..., 3:4], 1e-6), 0)
    h, w = img.shape[:2]
    over(sq, img, (n - w) // 2, (n - h) // 2)
    return sq


def navi_ico():
    """Navi as a .ico at the sizes Windows asks for (the Steam shortcut's art draws its own, launcher/gameart.py)."""
    return ico_bytes([navi_icon(n) for n in (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)])


ICON_DIR = Path(__file__).resolve().parent


def app_ico():
    """The project's icon as a .ico (the launcher window and the taskbar; tools/make_icon.py writes it)."""
    return (ICON_DIR / 'icon.ico').read_bytes()


def app_icon_png():
    """The project's icon at 64 x 64 as PNG bytes, for Tk's photo icon (tools/make_icon.py writes it)."""
    return (ICON_DIR / 'icon-64.png').read_bytes()


ICON_RAW = 256   # icon-256.rgba: ICON_RAW x ICON_RAW RGBA bytes (tools/make_icon.py)


def app_icon(n, corner=0.18):
    """The project's icon as an n x n float RGBA array (straight alpha, 0..1), every pixel the average of the source
    pixels under it, with corners rounded by corner x n (0 keeps them square; the edge is anti-aliased)."""
    src = np.frombuffer((ICON_DIR / 'icon-256.rgba').read_bytes(), dtype=np.uint8)
    src = src.reshape(ICON_RAW, ICON_RAW, 4).astype(np.float32) / 255.0
    pm = src.copy()
    pm[..., :3] *= pm[..., 3:4]
    cut = (np.arange(n + 1) * ICON_RAW / n).astype(int)
    span = np.diff(cut).astype(np.float32)
    pm = np.add.reduceat(pm, cut[:-1], axis=0) / span[:, None, None]
    pm = np.add.reduceat(pm, cut[:-1], axis=1) / span[None, :, None]
    if corner > 0:
        r = corner * n
        c = np.arange(n, dtype=np.float32) + 0.5
        dx = np.maximum(np.maximum(r - c, c - (n - r)), 0)
        d = np.hypot(dx[None, :], dx[:, None])
        pm *= np.clip(r - d + 0.5, 0, 1)[..., None]
    out = pm.copy()
    out[..., :3] = np.where(pm[..., 3:4] > 0, pm[..., :3] / np.maximum(pm[..., 3:4], 1e-6), 0)
    return out


def title_glyph(kind, size, s):
    """The minimise bar or the close cross of the band's buttons, in the band's light text colour."""
    img = new(size, size)
    t = max(1.2, 1.4 * s)
    m = size * 0.12
    if kind == 'min':
        lines = [[(m, size / 2), (size - m, size / 2)]]
    else:
        lines = [[(m, m), (size - m, size - m)], [(size - m, m), (m, size - m)]]
    paint(img, stroke_mask(size, size, lines, t), PALETTE['band_text'])
    return img


def check_mark(size, s, color=None):
    img = new(size, size)
    k = size / 24.0
    paint(img, stroke_mask(size, size, [[(5 * k, 12.5 * k), (9.2 * k, 16.7 * k), (19 * k, 7 * k)]], 3 * k),
          color or PALETTE['ok'])
    return img


def chevron(w, h, color, left=False):
    img = new(h, w)
    pts = [(w - 0.5, 0), (0.5, h / 2), (w - 0.5, h)] if left else [(0.5, 0), (w - 0.5, h / 2), (0.5, h)]
    paint(img, polygon_mask(h, w, pts), color)
    return img


def framed_icon(size, s, kind):
    """The square-framed icon of a big row (play or refresh), like the icon frames of the game's menu rows."""
    img = new(size, size)
    t = 1.5 * s
    paint(img, ring_mask(size, size, 0.5, 0.5, size - 0.5, size - 0.5, t), PALETTE['icon_frame'])
    c = size / 2
    if kind == 'play':
        k = size / 32
        paint(img, polygon_mask(size, size, [(c - 5 * k, c - 7 * k), (c + 7 * k, c), (c - 5 * k, c + 7 * k)]),
              PALETTE['icon_fill'])
    elif kind == 'refresh':
        import math
        k = size / 32
        r = 7.5 * k
        arcs = []
        for a0, a1 in ((200, 340), (20, 160)):
            arcs.append([(c + r * math.cos(math.radians(a)), c + r * math.sin(math.radians(a)))
                         for a in range(a0, a1 + 1, 6)])
        heads = [[(c + r * math.cos(math.radians(340)) - 3.5 * k, c + r * math.sin(math.radians(340)) - 1 * k),
                  (c + r * math.cos(math.radians(340)), c + r * math.sin(math.radians(340))),
                  (c + r * math.cos(math.radians(340)) + 0.5 * k, c + r * math.sin(math.radians(340)) - 4 * k)],
                 [(c + r * math.cos(math.radians(160)) + 3.5 * k, c + r * math.sin(math.radians(160)) + 1 * k),
                  (c + r * math.cos(math.radians(160)), c + r * math.sin(math.radians(160))),
                  (c + r * math.cos(math.radians(160)) - 0.5 * k, c + r * math.sin(math.radians(160)) + 4 * k)]]
        paint(img, stroke_mask(size, size, arcs + heads, 2.2 * k), PALETTE['icon_fill'])
    return img


def badge(size, s):
    """The orange "!" disc beside Updates when a new version is ready (the "!" itself is text)."""
    img = new(size + 4, size + 4)
    ring = stroke_mask(size + 4, size + 4, [[((size + 4) / 2, (size + 4) / 2)]], size + 1.5 * s)
    over(img, shadow(size + 4, size + 4, ring, PALETTE['badge_top'], 0.6, 6 * s))
    paint(img, ring, PALETTE['badge_ring'])
    disc = stroke_mask(size + 4, size + 4, [[((size + 4) / 2, (size + 4) / 2)]], size)
    layer = new(size + 4, size + 4)
    layer[..., :3] = vgradient(size + 4, size + 4, [(0, PALETTE['badge_top']), (1, PALETTE['badge_bottom'])])
    layer[..., 3] = disc
    over(img, layer)
    return img
