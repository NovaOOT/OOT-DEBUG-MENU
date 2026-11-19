"""The static collision of the remake's areas: the triangles of Phive/StaticCompoundBody/<folder>/<area>[_X<i>_Z<j>]
.Nin_Ounce_NVN2.bphsc tiles, read the way runtime/debug_hook.c reads them for the debug menu's Show collision (its
header comment describes the format): every compound shape and the terrain of each tile, each polygon whose collision
filter stops the player, in world coordinates (metres, y up).

tools/n64align.py fits the N64 rooms to them. The files are the extracted romfs's (already without their zstd layer).

Standard library only (numpy optional: triangles() returns a list of 9-float tuples).
"""
import math
import re
import struct
from pathlib import Path

SUB_PLAYER = (1 << 0) | (1 << 5)
SUB_SENSOR = 0x77bbffff


class _Buf:
    def __init__(self, data):
        self.p = data
        self.n = len(data)

    def has(self, o, ln):
        return 0 <= o <= self.n and ln <= self.n - o

    def u16(self, o):
        return struct.unpack_from('<H', self.p, o)[0] if self.has(o, 2) else 0

    def u32(self, o):
        return struct.unpack_from('<I', self.p, o)[0] if self.has(o, 4) else 0

    def i32(self, o):
        return struct.unpack_from('<i', self.p, o)[0] if self.has(o, 4) else 0

    def i64(self, o):
        return struct.unpack_from('<q', self.p, o)[0] if self.has(o, 8) else 0

    def u64(self, o):
        return struct.unpack_from('<Q', self.p, o)[0] if self.has(o, 8) else 0

    def f32(self, o):
        return struct.unpack_from('<f', self.p, o)[0] if self.has(o, 4) else 0.0

    def view(self, o, elem):
        off, n = self.i32(o), self.i32(o + 4)
        at = o + off
        if not self.has(o, 8) or n <= 0 or at < 0 or not self.has(at, n * elem):
            return 0, 0
        return at, n


def _chunk_size(b, o):
    if not b.has(o, 8):
        return 0
    return struct.unpack_from('>I', b.p, o)[0] & 0x3fffffff


def _chunk_find(b, o, end, tag):
    while o + 8 <= end:
        size = _chunk_size(b, o)
        if size < 8 or o + size > end:
            return None
        t = b.p[o + 4:o + 8]
        if t == tag:
            return o + 8, o + size
        if t in (b'TYPE', b'INDX'):
            r = _chunk_find(b, o + 8, o + size, tag)
            if r:
                return r
        o += size
    return None


def _varint(b, o, end):
    if o >= end:
        return None, o
    x = b.p[o]
    ln = 1 if x < 0x80 else 2 if x < 0xc0 else 3 if x < 0xe0 else 4 if x < 0xe8 else 0
    if not ln or o + ln > end:
        return None, o
    r = x if ln == 1 else x & 0x3f if ln == 2 else x & 0x1f if ln == 3 else x & 7
    for i in range(1, ln):
        r = r << 8 | b.p[o + i]
    return r, o + ln


class _Tag:
    """A Havok tag file: its objects' absolute offsets and type names."""

    def __init__(self, b, o):
        self.ok = False
        size = _chunk_size(b, o)
        if size < 8 or not b.has(o, size) or b.p[o + 4:o + 8] != b'TAG0':
            return
        end = o + size
        sdkv = _chunk_find(b, o + 8, end, b'SDKV')
        if not sdkv or b.p[sdkv[0]:sdkv[0] + 8] != b'20250200':
            return
        data, tst, tna, item = (_chunk_find(b, o + 8, end, t) for t in (b'DATA', b'TST1', b'TNA1', b'ITEM'))
        if not (data and tst and tna and item):
            return
        strs = bytes(b.p[tst[0]:tst[1]]).split(b'\0')
        count, q = _varint(b, tna[0], tna[1])
        if count is None or count >= 100000:
            return
        names = [''] * (count + 1)
        for i in range(1, count):
            name, q = _varint(b, q, tna[1])
            tpl, q = _varint(b, q, tna[1])
            if name is None or tpl is None or name >= len(strs) or tpl >= 64:
                return
            names[i] = strs[name].decode('latin-1')
            for _ in range(tpl):
                v, q = _varint(b, q, tna[1])
                v, q = _varint(b, q, tna[1])
                if v is None:
                    return
        self.types = {}
        for i in range((item[1] - item[0]) // 12):
            f = b.u32(item[0] + 12 * i)
            off = b.u32(item[0] + 12 * i + 4)
            if (f >> 24) & 0x10 and (f & 0xffffff) < count:
                self.types[data[0] + off] = names[f & 0xffffff]
        self.first = min(self.types) if self.types else None
        self.ok = True


class _Keep:
    def __init__(self, b, rec=0, mat=0, nrec=0, nmat=0, direct=False, fil=0):
        self.b, self.rec, self.mat, self.nrec, self.nmat, self.direct, self.fil = b, rec, mat, nrec, nmat, direct, fil

    def stops_player(self, m):
        if not self.fil:
            return True
        sub = self.b.u64(self.fil + 16 * m + 8)
        return sub != SUB_SENSOR and (sub & SUB_PLAYER) != 0

    def material(self, base, sub):
        if sub == 0xffff:
            sub = 0
        if self.direct:
            m = base + sub
        else:
            if base >= self.nrec:
                return -1
            r = self.rec + 20 * base
            count = self.b.u32(r + 0x10)
            if sub >= (count or 1):
                return -1
            m = self.b.u16(r + 0x0c) + sub
        return m if m < self.nmat else -1

    def kept(self, base, sub):
        m = self.material(base, sub)
        return m < 0 or self.stops_player(m)


def _xf_instance(q, t, s):
    x, y, z, w = q
    r = [1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w),
         2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w),
         2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y)]
    return [r[c * 3 + i] * s[c] for c in range(3) for i in range(3)], list(t)


def _xf_apply(x, v):
    m, t = x
    return (t[0] + m[0] * v[0] + m[3] * v[1] + m[6] * v[2],
            t[1] + m[1] * v[0] + m[4] * v[1] + m[7] * v[2],
            t[2] + m[2] * v[0] + m[5] * v[1] + m[8] * v[2])


def _xf_mul(a, b):
    am, _ = a
    bm, bt = b
    m = [am[i] * bm[c * 3] + am[3 + i] * bm[c * 3 + 1] + am[6 + i] * bm[c * 3 + 2] for c in range(3) for i in range(3)]
    return m, list(_xf_apply(a, bt))


IDENTITY = ([1, 0, 0, 0, 1, 0, 0, 0, 1], [0, 0, 0])


class _Out:
    def __init__(self):
        self.tris = []

    def emit(self, x, pts):
        q = [_xf_apply(x, p) for p in pts]
        if not all(math.isfinite(c) for p in q for c in p):
            return
        for i in range(1, len(q) - 1):
            self.tris.append(q[0] + q[i] + q[i + 1])


def _mesh_tag(b, table, n, key):
    lo, hi, tag = 0, n - 1, 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if b.u32(table + 8 * mid) <= key:
            tag = b.u16(table + 8 * mid + 4)
            lo = mid + 1
        else:
            hi = mid - 1
    return tag


def _mesh(b, m, x, base, k, o):
    table, nt = b.view(m + 0x60, 8)
    secs, ns = b.view(m + 0x88, 0x40)
    if not secs:
        return
    inv = (b.f32(m + 0x50), b.f32(m + 0x54), b.f32(m + 0x58))
    for s in range(ns):
        g = secs + 0x40 * s
        prims, np_ = b.view(g + 0x08, 4)
        verts, nv = b.view(g + 0x10, 1)
        if not prims or not verts:
            continue
        so = (b.i32(g + 0x20), b.i32(g + 0x24), b.i32(g + 0x28))
        floats = so == (0x7fffffff,) * 3
        nv //= 12 if floats else 6
        if floats:
            vv = [struct.unpack_from('<3f', b.p, verts + 12 * i) for i in range(nv)]
        else:
            vv = [tuple((so[a] + c) * inv[a] for a, c in enumerate(struct.unpack_from('<3H', b.p, verts + 6 * i)))
                  for i in range(nv)]
        for i in range(min(np_, 256)):
            tag = _mesh_tag(b, table, nt, s << 9 | i << 1) if nt else 0
            if not k.kept(base, tag):
                continue
            idx = b.p[prims + 4 * i:prims + 4 * i + 4]
            if max(idx) >= nv:
                continue
            v = [vv[c] for c in idx]
            if idx[2] == idx[3]:
                o.emit(x, v[:3])
            else:
                o.emit(x, v)


def _hull(b, h, x, o):
    verts, nv = b.view(h, 12)
    faces, nf = b.view(h + 0x10, 4)
    idx, ni = b.view(h + 0x18, 1)
    if not verts or not faces or not idx:
        return
    for f in range(nf):
        first, cnt = b.u16(faces + 4 * f), b.p[faces + 4 * f + 2]
        if cnt < 3 or first + cnt > ni:
            continue
        pts = []
        for j in range(cnt):
            vi = b.p[idx + first + j]
            if vi >= nv:
                break
            pts.append(struct.unpack_from('<3f', b.p, verts + 12 * vi))
        if len(pts) >= 3:
            o.emit(x, pts)


def _shape(b, t, sp, x, base, depth, k, o):
    ty = t.types.get(sp, '')
    if ty == 'hknpCompoundShape':
        if depth > 4:
            return
        off, n = b.i64(sp + 0x38), b.i32(sp + 0x40)
        at = sp + 0x38 + off
        if n <= 0 or at < 0 or not b.has(at, n * 0x40):
            return
        for i in range(n):
            e = at + 0x40 * i
            q = struct.unpack_from('<4f', b.p, e)
            tr = struct.unpack_from('<3f', b.p, e + 0x10)
            sc = struct.unpack_from('<3f', b.p, e + 0x20)
            tag = b.u16(e + 0x2c)
            rp = b.i64(e + 0x30)
            if not rp or e + 0x30 + rp < 0:
                continue
            cx = _xf_mul(x, _xf_instance(q, tr, sc))
            _shape(b, t, e + 0x30 + rp, cx, tag if depth == 0 else base, depth + 1, k, o)
    elif ty == 'hknpMeshShape':
        _mesh(b, sp, x, base, k, o)
    elif ty in ('hknpBoxShape', 'hknpConvexShape', 'hknpConvexPolytopeShape'):
        if k.kept(base, 0):
            _hull(b, sp + 0x38, x, o)


def _tag_root(b, o, x, k, out):
    t = _Tag(b, o)
    if t.ok and t.first is not None:
        _shape(b, t, t.first, x, 0, 0, k, out)


def _shape_file(b, ph, x, out):
    if not b.has(ph, 0x2c) or b.p[ph:ph + 8] != b'Phive\0\x01\0':
        return
    tag, mat, fil = b.u32(ph + 0x0c), b.u32(ph + 0x10), b.u32(ph + 0x14)
    mat_n, fil_n = b.u32(ph + 0x20), b.u32(ph + 0x24)
    k = _Keep(b, mat=ph + mat, nmat=mat_n // 16 if b.has(ph + mat, mat_n) else 0, direct=True,
              fil=ph + fil if fil_n == mat_n and b.has(ph + fil, fil_n) else 0)
    _tag_root(b, ph + tag, x, k, out)


def tile(data, shift=(0.0, 0.0)):
    """The triangles of one tile's file contents, as 9-float tuples."""
    b = _Buf(data)
    out = _Out()
    if not b.has(0, 0x10) or b.p[:8] != b'Phive\0\x02\x02':
        return out.tris
    off = []
    while len(off) < 64:
        v = b.u32(0x0c + 4 * len(off))
        off.append(v)
        if not b.has(0x0c + 4 * (len(off) - 1), 4) or v >= b.n:
            break
    n = len(off)
    if n < 12 or off[-1] != b.n or not off[4] <= off[5] <= off[6] <= off[7]:
        return out.tris
    k = _Keep(b, rec=off[4], mat=off[5], nrec=(off[5] - off[4]) // 20, nmat=(off[6] - off[5]) // 16,
              fil=off[6] if off[7] - off[6] == off[6] - off[5] else 0)
    for i in range(8, n - 3):
        if off[i] < off[i + 1]:
            _tag_root(b, off[i], IDENTITY, k, out)
    t = [b.f32(off[n - 3] + 0x18 + 4 * a) for a in range(3)]
    placed = any(abs(v) >= 1e-6 for v in t)
    tiny = any(v != 0.0 and abs(v) < 1e-6 for v in t)
    if not placed and tiny:
        t = [shift[0], 0.0, shift[1]]
    _shape_file(b, off[n - 2], ([1, 0, 0, 0, 1, 0, 0, 0, 1], t), out)
    return out.tris


def _tile_shift(rest):
    m = re.match(r'_X(-?\d+)_Z(-?\d+)', rest)
    return ((int(m.group(1)) - 8) * 250.0, (int(m.group(2)) - 8) * 250.0) if m else (0.0, 0.0)


def area_files(romfs, area):
    """The collision tiles of an area (a Banc stage name such as KokiriForest_Child)."""
    root = Path(romfs) / 'Phive' / 'StaticCompoundBody'
    out = []
    for f in root.glob('*/' + area + '*'):
        rest = f.name[len(area):]
        if rest.startswith('_X') or rest.startswith('.Nin_Ounce_NVN2.bphsc'):
            out.append(f)
    return sorted(out)


def area(romfs, area_name):
    """Every triangle of an area's static collision."""
    tris = []
    for f in area_files(romfs, area_name):
        data = f.read_bytes()
        if f.suffix == '.zs':
            continue   # the extracted romfs holds them without their zstd layer
        tris += tile(data, _tile_shift(f.name[len(area_name):]))
    return tris


if __name__ == '__main__':
    import sys
    t = area(sys.argv[1], sys.argv[2])
    print(len(t), 'triangles')
    if t:
        lo = [min(tr[a::3]) for a in range(3) for tr in [[c for tri in t for c in tri[a::3]]]]
        xs = [c for tri in t for c in tri[0::3]]
        ys = [c for tri in t for c in tri[1::3]]
        zs = [c for tri in t for c in tri[2::3]]
        print('x', min(xs), max(xs), 'y', min(ys), max(ys), 'z', min(zs), max(zs))
