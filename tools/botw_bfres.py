"""BFRES reading and writing for tools/make_botw_enemies_mod.py: Breath of the Wild's models and animations (BFRES 5,
Yaz0) read, and this game's (BFRES 10) written.

The two versions share their records' contents. A version 5 block header is 8 bytes longer, so most of its fields sit
8 bytes later; its bones are 0x50 bytes against 0x58, its bone animations 0x28 against 0x38 (two more null pointers),
its skeletal animations 0x60 against 0x50 (the flags move to the header), materials 0xb8 against 0xb0. Curves are the
same 0x30 bytes; in version 10 their flags carry 0x4000, and 0x0800 when the keys are quantized. Name dictionaries are
the same radix trees (with "_DIC" in version 5 and zero in version 10).

A version 10 file is relocated by its relocation table (_RLT): sections of the file, each with runs of 8-byte pointer
fields whose targets lie in that section. The tools here keep a file's pointer fields as a set, so data can be added
and the table written anew.

Standard library only.
"""
import math
import struct

# ---- bytes

def u16(b, o): return struct.unpack_from('<H', b, o)[0]
def u32(b, o): return struct.unpack_from('<I', b, o)[0]
def u64(b, o): return struct.unpack_from('<Q', b, o)[0]
def f32(b, o): return struct.unpack_from('<f', b, o)[0]


def align(n, a):
    return (n + a - 1) // a * a


def yaz0(src):
    """The data of a Yaz0 stream (data that is not one is returned as it is): groups of eight items after a code byte,
    a set bit a literal byte, a clear one a back reference of two bytes (length - 2 in the top nibble, 0 for a third
    byte of length - 0x12; distance - 1 in the low 12 bits)."""
    if src[:4] != b'Yaz0':
        return bytes(src)
    size = struct.unpack_from('>I', src, 4)[0]
    out = bytearray(size)
    s, d = 16, 0
    while d < size:
        code = src[s]
        s += 1
        for bit in range(8):
            if d >= size:
                break
            if code & (0x80 >> bit):
                out[d] = src[s]
                d += 1
                s += 1
                continue
            b1, b2 = src[s], src[s + 1]
            s += 2
            dist = ((b1 & 15) << 8 | b2) + 1
            n = b1 >> 4
            if n:
                n += 2
            else:
                n = src[s] + 0x12
                s += 1
            p = d - dist
            if dist >= n:
                out[d:d + n] = out[p:p + n]
            else:
                for i in range(n):
                    out[d + i] = out[p + i]
            d += n
    return bytes(out)


def sname(b, p):
    """A string of a file's string table (a u16 length, the text) at p; '' for none."""
    return b[p + 2:p + 2 + u16(b, p)].decode('utf-8', 'replace') if p else ''


def dic_names(b, p):
    """The names of a name dictionary (u32 count at +4, the root node, then 16-byte nodes with the name at +8)."""
    return [sname(b, u64(b, p + 8 + 16 * (i + 1) + 8)) for i in range(u32(b, p + 4))] if p else []


def _bit(key, b):
    i = b >> 3
    return key[len(key) - 1 - i] >> (b & 7) & 1 if i < len(key) else 0


def dic_nodes(names):
    """The nodes (bit, left, right) of the name dictionary of names in that order: a radix tree on the bits of the
    names read from their end (bit i is bit i % 8 of the character i // 8 from the end), entered one name after
    another. Node 0 is the root (bit -1), its left child the top of the tree."""
    keys = [n.encode() for n in names]
    nodes = [[-1, 0, 0, b'']]

    def child(n, d):
        return nodes[n][2] if d else nodes[n][1]
    for k in keys:
        prev, cur = 0, nodes[0][1]
        while nodes[prev][0] < nodes[cur][0]:
            prev, cur = cur, child(cur, _bit(k, nodes[cur][0]))
        other = nodes[cur][3]
        b = 0
        while _bit(k, b) == _bit(other, b):
            b += 1
            if b > 8 * max(len(k), len(other)) + 8:
                raise ValueError(f'the name {k!r} is in the dictionary twice')
        prev, cur = 0, nodes[0][1]
        while nodes[prev][0] < nodes[cur][0] < b:
            prev, cur = cur, child(cur, _bit(k, nodes[cur][0]))
        new = len(nodes)
        nodes.append([b, cur, new, k] if _bit(k, b) else [b, new, cur, k])
        if prev == 0:
            nodes[0][1] = new
        elif _bit(k, nodes[prev][0]):
            nodes[prev][2] = new
        else:
            nodes[prev][1] = new
    return [(n[0] & 0xffffffff, n[1], n[2]) for n in nodes]


# ---- vertex attributes

# The high byte of a format the layout (components, bits; 0 for 10_10_10_2), the low byte the type (1 unorm, 2 snorm,
# 3 uint, 4 sint, 5 float).
LAYOUT = {0x02: (1, 8), 0x09: (2, 8), 0x0b: (4, 8), 0x0e: (4, 0), 0x0a: (1, 16), 0x12: (2, 16), 0x15: (4, 16),
          0x14: (1, 32), 0x17: (2, 32), 0x18: (3, 32), 0x19: (4, 32)}


def decode_attr(b, at, f):
    kind, typ = f >> 8, f & 0xff
    n, bits = LAYOUT[kind]
    if not bits:
        v = u32(b, at)
        xs = [v >> s & 0x3ff for s in (0, 10, 20)]
        if typ == 2:
            w = v >> 30
            return [(x - 1024 if x & 512 else x) / 511 for x in xs] + [float(w - 4 if w & 2 else w)]
        return [x / 1023 for x in xs] + [(v >> 30) / 3]
    if typ == 5:
        return list(struct.unpack_from('<%d%s' % (n, {16: 'e', 32: 'f'}[bits]), b, at))
    raw = struct.unpack_from('<%d%s' % (n, {8: 'B', 16: 'H', 32: 'I'}[bits]), b, at)
    top = (1 << bits) - 1
    if typ == 1:
        return [x / top for x in raw]
    if typ == 2:
        return [max((x - top - 1 if x > top >> 1 else x) / (top >> 1), -1.0) for x in raw]
    if typ == 3:
        return list(raw)
    if typ == 4:
        return [x - top - 1 if x > top >> 1 else x for x in raw]
    raise ValueError(f'vertex format {f:#x}')


def encode_attr(vals, f):
    kind, typ = f >> 8, f & 0xff
    n, bits = LAYOUT[kind]
    vals = (list(vals) + [0.0, 0.0, 0.0, 1.0])[:4] if n == 4 else (list(vals) + [0.0] * n)[:n]
    if not bits:
        if typ != 2:
            raise ValueError(f'vertex format {f:#x}')
        v = 0
        for i, x in enumerate(vals[:3]):
            v |= (round(max(-1.0, min(1.0, x)) * 511) & 0x3ff) << (10 * i)
        return struct.pack('<I', v | (round(max(-1.0, min(1.0, vals[3]))) & 3) << 30)
    if typ == 5:
        return struct.pack('<%d%s' % (n, {16: 'e', 32: 'f'}[bits]), *vals)
    fmt = '<%d%s' % (n, {8: 'B', 16: 'H', 32: 'I'}[bits])
    top = (1 << bits) - 1
    if typ == 1:
        return struct.pack(fmt, *(round(max(0.0, min(1.0, x)) * top) for x in vals))
    if typ == 2:
        h = top >> 1
        return struct.pack(fmt, *(round(max(-1.0, min(1.0, x)) * h) & top for x in vals))
    if typ in (3, 4):
        return struct.pack(fmt, *(int(x) & top for x in vals))
    raise ValueError(f'vertex format {f:#x}')


# ---- matrices (4x4 row-major lists)

def local_matrix(scale, rot, trans, euler):
    """A bone's local matrix: translation * rotation * scale, the rotation Euler XYZ (applied X, then Y, then Z) or a
    quaternion (x, y, z, w)."""
    if euler:
        cx, sx, cy, sy, cz, sz = (f(a) for a in rot[:3] for f in (math.cos, math.sin))
        r = [[cy * cz, sx * sy * cz - cx * sz, cx * sy * cz + sx * sz],
             [cy * sz, sx * sy * sz + cx * cz, cx * sy * sz - sx * cz],
             [-sy, sx * cy, cx * cy]]
    else:
        x, y, z, w = rot
        r = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
             [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
             [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    return [[r[i][j] * scale[j] for j in range(3)] + [trans[i]] for i in range(3)] + [[0.0, 0.0, 0.0, 1.0]]


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def mat_inv(m):
    """The inverse of an affine matrix."""
    a = [row[:3] for row in m[:3]]
    det = (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1]) - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
           + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    inv = [[(a[(j + 1) % 3][(i + 1) % 3] * a[(j + 2) % 3][(i + 2) % 3]
             - a[(j + 1) % 3][(i + 2) % 3] * a[(j + 2) % 3][(i + 1) % 3]) / det for j in range(3)] for i in range(3)]
    t = [-sum(inv[i][k] * m[k][3] for k in range(3)) for i in range(3)]
    return [inv[i] + [t[i]] for i in range(3)] + [[0.0, 0.0, 0.0, 1.0]]


def xform(m, v, w=1.0):
    return [m[i][0] * v[0] + m[i][1] * v[1] + m[i][2] * v[2] + m[i][3] * w for i in range(3)]


def normalize(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def decompose(m):
    """(scale, rotation matrix 3x3, translation) of an affine matrix without shear."""
    cols = [[m[r][c] for r in range(3)] for c in range(3)]
    scale = [math.sqrt(sum(x * x for x in col)) or 1.0 for col in cols]
    rot = [[m[r][c] / scale[c] for c in range(3)] for r in range(3)]
    return scale, rot, [m[r][3] for r in range(3)]


def euler_of(r):
    """Euler XYZ angles of a rotation matrix (local_matrix's order)."""
    sy = max(-1.0, min(1.0, -r[2][0]))
    y = math.asin(sy)
    if abs(sy) < 0.99999:
        x = math.atan2(r[2][1], r[2][2])
        z = math.atan2(r[1][0], r[0][0])
    else:
        x = math.atan2(-r[1][2], r[1][1])
        z = 0.0
    return [x, y, z]


def quat_of(r):
    t = r[0][0] + r[1][1] + r[2][2]
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        return [(r[2][1] - r[1][2]) / s, (r[0][2] - r[2][0]) / s, (r[1][0] - r[0][1]) / s, 0.25 * s]
    if r[0][0] > r[1][1] and r[0][0] > r[2][2]:
        s = math.sqrt(1.0 + r[0][0] - r[1][1] - r[2][2]) * 2
        return [0.25 * s, (r[0][1] + r[1][0]) / s, (r[0][2] + r[2][0]) / s, (r[2][1] - r[1][2]) / s]
    if r[1][1] > r[2][2]:
        s = math.sqrt(1.0 + r[1][1] - r[0][0] - r[2][2]) * 2
        return [(r[0][1] + r[1][0]) / s, 0.25 * s, (r[1][2] + r[2][1]) / s, (r[0][2] - r[2][0]) / s]
    s = math.sqrt(1.0 + r[2][2] - r[0][0] - r[1][1]) * 2
    return [(r[0][2] + r[2][0]) / s, (r[1][2] + r[2][1]) / s, 0.25 * s, (r[1][0] - r[0][1]) / s]


# ---- models

class Rec:
    """A record read from a file (its fields as attributes, at: where it is)."""


class Bfres:
    """The models of a BFRES file, version 5 or 10: skeleton, vertex buffers, shapes with their meshes, materials with
    their texture names."""

    def __init__(self, b):
        self.b = b
        self.v10 = u16(b, 0xa) >= 9
        self.d = 0 if self.v10 else 8
        info = 0x168 if self.v10 else u64(b, 0x90)   # the buffer area: u32 flags, u32 size, u64 offset
        self.buf_size, self.buf_base = (u32(b, info + 4), u64(b, info + 8)) if info else (0, 0)
        arr = u64(b, 0x28)
        self.models = [self.model(arr + 0x78 * i) for i in range(len(dic_names(b, u64(b, 0x30))))]

    def model(self, p):
        b, d = self.b, self.d
        m = Rec()
        m.at, m.name = p, sname(b, u64(b, p + 8 + d))
        nv, ns, nm = struct.unpack_from('<3H', b, p + 0x68)
        self.skeleton(m, u64(b, p + 0x18 + d))
        m.vtx = [self.fvtx(u64(b, p + 0x20 + d) + (0x58 + d) * i) for i in range(nv)]
        m.shapes = [self.fshp(u64(b, p + 0x28 + d) + (0x60 + 2 * d) * i) for i in range(ns)]
        m.mats = [self.fmat(u64(b, p + 0x38 + d) + (0xb0 if self.v10 else 0xb8) * i) for i in range(nm)]
        return m

    def skeleton(self, m, p):
        b, d = self.b, self.d
        m.skel_at = p
        m.skel_flags = flags = u32(b, p + 4) if self.v10 else u32(b, p + 0x38)
        arr, mtb = u64(b, p + 0x10 + d), u64(b, p + 0x18 + d)
        nb, ns, nr = struct.unpack_from('<3H', b, p + (0x38 if self.v10 else 0x3c))
        size, o = (0x58, 0x20) if self.v10 else (0x50, 0x18)
        m.bones = []
        for i in range(nb):
            q = arr + size * i
            bo = Rec()
            bo.at, bo.name = q, sname(b, u64(b, q))
            _, bo.parent, bo.smooth, bo.rigid = struct.unpack_from('<4H', b, q + o)
            bo.parent = -1 if bo.parent == 0xffff else bo.parent
            bo.flags = u32(b, q + o + 0xc)
            bo.scale = struct.unpack_from('<3f', b, q + o + 0x10)
            bo.rot = struct.unpack_from('<4f', b, q + o + 0x1c)
            bo.trans = struct.unpack_from('<3f', b, q + o + 0x2c)
            bo.local = local_matrix(bo.scale, bo.rot, bo.trans, flags & 0x1000)
            bo.world = mat_mul(m.bones[bo.parent].world, bo.local) if bo.parent >= 0 else bo.local
            m.bones.append(bo)
        lst = [u16(b, mtb + 2 * i) for i in range(ns + nr)]
        m.smooth, m.rigid = lst[:ns], lst[ns:]
        m.by_name = {bo.name: i for i, bo in enumerate(m.bones)}

    def fvtx(self, p):
        b, d = self.b, self.d
        v = Rec()
        v.at = p
        v.size_arr, v.stride_arr = u64(b, p + 0x30 + d), u64(b, p + 0x38 + d)
        v.pool_off, v.nattr, v.nbuf = u32(b, p + 0x48 + d), b[p + 0x4c + d], b[p + 0x4d + d]
        v.count, v.skin = u32(b, p + 0x50 + d), b[p + 0x54 + d]
        arr = u64(b, p + 8 + d)
        v.attrs = [(sname(b, u64(b, arr + 16 * i)), u16(b, arr + 16 * i + 8), u16(b, arr + 16 * i + 0xc),
                    u16(b, arr + 16 * i + 0xe)) for i in range(v.nattr)]
        v.sizes = [u32(b, v.size_arr + 16 * i) for i in range(v.nbuf)]
        v.strides = [u32(b, v.stride_arr + 16 * i) for i in range(v.nbuf)]
        return v

    def vertices(self, v):
        """{attribute name: [values of each vertex]}. A vertex buffer set's buffers follow each other in the buffer
        area from its offset, each at a multiple of 8."""
        offs, at = [], self.buf_base + v.pool_off
        for s in v.sizes:
            offs.append(at)
            at = align(at + s, 8)
        return {name: [decode_attr(self.b, offs[buf] + off + v.strides[buf] * i, f) for i in range(v.count)]
                for name, f, off, buf in v.attrs}

    def fshp(self, p):
        b, d = self.b, self.d
        s = Rec()
        s.at, s.name = p, sname(b, u64(b, p + 8 + d))
        s.mesh_arr, s.skin_arr = u64(b, p + 0x18 + d), u64(b, p + 0x20 + d)
        s.bbox, s.spheres = u64(b, p + 0x38 + d), u64(b, p + 0x40 + d)
        if self.v10:
            _, s.mat, s.bone, s.vtx = struct.unpack_from('<4H', b, p + 0x50)
            s.nskin, s.skin, s.nmesh = u16(b, p + 0x58), b[p + 0x5a], b[p + 0x5b]
        else:
            _, s.mat, s.bone, s.vtx, s.nskin = struct.unpack_from('<5H', b, p + 0x5c)
            s.skin, s.nmesh = b[p + 0x66], b[p + 0x67]
        s.skin_bones = [u16(b, s.skin_arr + 2 * i) for i in range(s.nskin)]
        s.meshes = []
        for i in range(s.nmesh):
            q = s.mesh_arr + 0x38 * i
            me = Rec()
            me.at, me.sub_arr, me.size_at = q, u64(b, q), u64(b, q + 0x18)
            me.face_off, me.prim, me.ifmt, me.icount, me.first = struct.unpack_from('<5I', b, q + 0x20)
            me.nsub = u16(b, q + 0x34)
            s.meshes.append(me)
        return s

    def indices(self, me):
        """A mesh's triangle list, its first vertex added."""
        at = self.buf_base + me.face_off
        f = '<%d%s' % (me.icount, 'H' if me.ifmt == 1 else 'I')
        return [i + me.first for i in struct.unpack_from(f, self.b, at)]

    def fmat(self, p):
        b, d = self.b, self.d
        mt = Rec()
        mt.at, mt.name = p, sname(b, u64(b, p + 8 + d))
        mt.tex_arr, n = (u64(b, p + 0x20), b[p + 0xa3]) if self.v10 else (u64(b, p + 0x38), b[p + 0xa9])
        mt.textures = [sname(b, u64(b, mt.tex_arr + 8 * k)) for k in range(n)]
        return mt


# ---- relocation

def relocations(b):
    """([(position, size)] of the sections, sorted positions of the pointer fields) of a version 10 file."""
    r = u32(b, 0x18)
    n = u32(b, r + 8)
    ents = r + 0x10 + 0x18 * n
    sections, ptrs = [], set()
    for s in range(n):
        _, pos, size, first, cnt = struct.unpack_from('<QIIII', b, r + 0x10 + 0x18 * s)
        sections.append((pos, size))
        for e in range(first, first + cnt):
            p, structs, offs, pads = struct.unpack_from('<IHBB', b, ents + 8 * e)
            for _ in range(structs):
                for k in range(offs):
                    ptrs.add(p + 8 * k)
                p += 8 * (offs + pads)
    return sections, sorted(ptrs)


def section_of(sections, t):
    """The section a pointer to t is relocated with: the one holding t, else the last one starting at or before t."""
    best = 0
    for i, (pos, size) in enumerate(sections):
        if pos <= t < pos + size:
            return i
        if pos <= t:
            best = i
    return best


def rlt_block(at, sections, ptrs, data):
    """A relocation table at at for the pointer fields ptrs of data (each relocated with the section its target lies
    in), as runs of consecutive fields."""
    by = [[] for _ in sections]
    for p in sorted(set(ptrs)):
        by[section_of(sections, u64(data, p))].append(p)
    ents, heads = [], []
    for sec in by:
        first = len(ents)
        i = 0
        while i < len(sec):
            j = i
            while j + 1 < len(sec) and sec[j + 1] == sec[j] + 8 and j - i < 254:
                j += 1
            ents.append(struct.pack('<IHBB', sec[i], 1, j - i + 1, 0))
            i = j + 1
        heads.append((first, len(ents) - first))
    out = bytearray(b'_RLT' + struct.pack('<III', at, len(sections), 0))
    for (pos, size), (first, cnt) in zip(sections, heads):
        out += struct.pack('<QIIII', 0, pos, size, first, cnt)
    return bytes(out) + b''.join(ents)


class Str(str):
    """A string to be put in the string block: where it lands is known when the block is written."""


class Blob:
    """Data added to a file at a fixed place: records, arrays and the string block, and the pointer fields among
    them."""

    def __init__(self, base):
        self.base, self.data, self.ptrs, self.str_refs = base, bytearray(), [], []

    def alloc(self, n, a=8):
        at = align(len(self.data), a)
        self.data += bytes(at + n - len(self.data))
        return self.base + at

    def put(self, at, data):
        o = at - self.base
        self.data[o:o + len(data)] = data

    def bytes_(self, data, a=8):
        at = self.alloc(len(data), a)
        self.put(at, data)
        return at

    def ptr(self, at, target):
        """A pointer field at at to target (a position, or a Str)."""
        if isinstance(target, Str):
            self.str_refs.append((at, target))
            return
        self.put(at, struct.pack('<Q', target))
        if target:
            self.ptrs.append(at)

    def dic(self, names):
        """A version 10 name dictionary of names."""
        nodes = dic_nodes(names)
        at = self.alloc(8 + 16 * len(nodes))
        self.put(at, struct.pack('<II', 0, len(names)))
        for i, (bit, left, right) in enumerate(nodes):
            q = at + 8 + 16 * i
            self.put(q, struct.pack('<IHH', bit, left, right))
            self.ptr(q + 8, Str(names[i - 1] if i else ''))
        return at

    def strings(self, extra=()):
        """The string block (_STR: u32 next block 0, u64 size (set by the caller), u32 count, then each string as a u16
        length, the text and a zero at even offsets, the empty one first, the others in byte order) with every string
        pointed at and extra. Returns (block position, {string: position})."""
        names = sorted(set(self.str_refs and [s for _, s in self.str_refs]) | set(extra) | {''},
                       key=lambda x: x.encode())
        names.remove('')
        at = self.alloc(0x14, 8)
        self.put(at, b'_STR' + struct.pack('<IQI', 0, 0, len(names)))
        where = {}
        for s in [''] + names:
            e = s.encode()
            where[s] = self.bytes_(struct.pack('<H', len(e)) + e + b'\0', 2)
        self.alloc(0, 8)
        for p, s in self.str_refs:
            self.ptr(p, where[s])
        self.str_refs = []
        return at, where

    def strings_plain(self, extra=()):
        """Every string pointed at, as the strings of a string block are (a u16 length, the text and a zero, at even
        offsets) but without a block header: for a file whose string block stays where it is. Returns
        {string: position}."""
        where = {}
        for s in sorted({s for _, s in self.str_refs} | set(extra), key=lambda x: x.encode()):
            e = s.encode()
            where[s] = self.bytes_(struct.pack('<H', len(e)) + e + b'\0', 2)
        self.alloc(0, 8)
        for p, s in self.str_refs:
            self.ptr(p, where[s])
        self.str_refs = []
        return where


# ---- skeletal animations

def _frames(b, at, kind, n):
    if kind == 0:
        return list(struct.unpack_from('<%df' % n, b, at))
    if kind == 1:
        return [x / 32 for x in struct.unpack_from('<%dh' % n, b, at)]
    return list(b[at:at + n])


KEY_SIZE = {0: (4, 'f'), 1: (2, 'h'), 2: (1, 'b')}
ELEMENTS = {0: 4, 1: 2, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1, 7: 1}   # values a key holds by curve type


class Curve(Rec):
    """A curve: its frames and keys as numbers (keys raw, value = offset + scale * key for the first element).
    A version 5 cubic curve holds four coefficients a key, of the segment from its frame to the next. This game's
    (hermite) holds two, the value at its frame and the slope there in units a frame; a frame given twice carries the
    slope into it on the first key and the slope out of it on the second."""

    hermite = False

    def values(self):
        """The elements of each key, scaled: [c0 + offset, c1, ...] times scale."""
        n = 2 if self.hermite else ELEMENTS[self.kind]
        return [[self.offset + self.scale * k[0]] + [self.scale * x for x in k[1:]]
                for k in (self.keys[i * n:(i + 1) * n] for i in range(self.nkey))]

    def at_frame(self, f):
        fr = self.frames
        vals = self.values()
        if self.hermite:
            if self.flags >> 2 & 3 == 0:   # float keys: scale and offset unused
                vals = [list(self.keys[2 * i:2 * i + 2]) for i in range(self.nkey)]
            if f <= fr[0]:
                return vals[0][0]
            if f >= fr[-1]:
                return vals[-1][0]
            i = max(k for k in range(len(fr) - 1) if fr[k] <= f and fr[k + 1] > fr[k])
            span = fr[i + 1] - fr[i]
            t = (f - fr[i]) / span
            (p0, m0), (p1, m1) = vals[i], vals[i + 1]
            m0, m1 = m0 * span, m1 * span
            return ((2 * t ** 3 - 3 * t * t + 1) * p0 + (t ** 3 - 2 * t * t + t) * m0
                    + (-2 * t ** 3 + 3 * t * t) * p1 + (t ** 3 - t * t) * m1)
        if f <= fr[0]:
            i, t = 0, 0.0
        elif f >= fr[-1]:
            i, t = len(fr) - 1, 0.0
        else:
            i = max(k for k in range(len(fr)) if fr[k] <= f)
            span = fr[i + 1] - fr[i] if i + 1 < len(fr) else 1.0
            t = (f - fr[i]) / span if span else 0.0
        c = vals[i]
        if self.kind == 0:
            return c[0] + c[1] * t + c[2] * t * t + c[3] * t * t * t
        if self.kind == 1:
            return c[0] + c[1] * t
        return c[0]


def hermite_keys(cv):
    """A version 5 curve (cubic or linear segments) as this game's: [(frame, value, slope a frame)], a frame given
    twice where the curve's value or slope jumps (Curve)."""
    fr, vals, out = cv.frames, cv.values(), []

    def add(f, v, s):
        if out and out[-1][0] == f and abs(out[-1][1] - v) < 1e-6 and abs(out[-1][2] - s) < 1e-6:
            return
        out.append((f, v, s))
    for i, c in enumerate(vals):
        span = (fr[i + 1] if i + 1 < len(fr) else cv.end) - fr[i]
        if cv.kind == 0:
            c0, c1, c2, c3 = c
            end, d0, d1 = c0 + c1 + c2 + c3, c1, c1 + 2 * c2 + 3 * c3
        else:
            c0, c1 = c[0], c[1] if len(c) > 1 else 0.0
            end, d0, d1 = c0 + c1, c1, c1
        if span <= 0:
            add(fr[i], c0, 0.0)
            continue
        add(fr[i], c0, d0 / span)
        add(fr[i] + span, end, d1 / span)
    return out


def read_anims(b):
    """The skeletal animations of a BFRES file, version 5 or 10: [Rec(name, flags, frames, bones)], each bone a
    Rec(name, flags, begin: its four begin bytes, base: the base values, curves)."""
    v10 = u16(b, 0xa) >= 9
    if v10:
        arr, n, size = u64(b, 0x58), u16(b, 0xe2), 0x50
        o_name, o_bones, o_flags, o_frames, o_nbone = 0x8, 0x28, 0x4, 0x40, 0x4c
    else:
        arr, n, size = u64(b, 0x38), 0, 0x60
        while arr and b[arr + size * n:arr + size * n + 4] == b'FSKA':
            n += 1
        o_name, o_bones, o_flags, o_frames, o_nbone = 0x10, 0x30, 0x48, 0x4c, 0x58
    bsize, bflags = (0x38, 0x28) if v10 else (0x28, 0x18)
    out = []
    for i in range(n):
        p = arr + size * i
        a = Rec()
        a.name, a.flags, a.frames = sname(b, u64(b, p + o_name)), u32(b, p + o_flags), struct.unpack_from(
            '<i', b, p + o_frames)[0]
        ba = u64(b, p + o_bones)
        a.bones = []
        for k in range(u16(b, p + o_nbone)):
            q = ba + bsize * k
            bo = Rec()
            bo.name, cu, base = sname(b, u64(b, q)), u64(b, q + 8), u64(b, q + 0x10)
            bo.flags = u32(b, q + bflags)
            bo.begin = bytes(b[q + bflags + 4:q + bflags + 8])
            ncurve = bo.begin[2]
            nb = (3 if bo.flags & 8 else 0) + (4 if bo.flags & 0x10 else 0) + (3 if bo.flags & 0x20 else 0)
            bo.base = list(struct.unpack_from('<%df' % nb, b, base)) if nb else []
            bo.curves = []
            for c in range(ncurve):
                r = cu + 0x30 * c
                cv = Curve()
                cv.flags, cv.nkey, cv.target = struct.unpack_from('<HHI', b, r + 0x10)
                cv.start, cv.end = struct.unpack_from('<2f', b, r + 0x18)
                # version 10 has a word of zero before the scale
                cv.scale, cv.offset, cv.delta = struct.unpack_from('<3f', b, r + (0x24 if v10 else 0x20))
                cv.kind = cv.flags >> 4 & 7
                cv.hermite = v10 and cv.kind == 0
                ksz, kf = KEY_SIZE[cv.flags >> 2 & 3]
                cv.frames = _frames(b, u64(b, r), cv.flags & 3, cv.nkey)
                ne = (2 if cv.hermite else ELEMENTS[cv.kind]) * cv.nkey
                cv.keys = list(struct.unpack_from('<%d%s' % (ne, kf), b, u64(b, r + 8)))
                bo.curves.append(cv)
            a.bones.append(bo)
        out.append(a)
    return out


# BoneAnimData: u32 flags, scale (+4), translation (+0x10), padding, rotation (+0x20): a curve's target offset
TARGET_SCALE, TARGET_TRANS, TARGET_ROT = 4, 0x10, 0x20


def pose(anim, frame, bones=None):
    """{bone name: (scale, rotation 4, translation)} of an animation at frame (bones: only these)."""
    out = {}
    for bo in anim.bones:
        if bones is not None and bo.name not in bones:
            continue
        vals = {}
        base = list(bo.base)
        k = 0
        if bo.flags & 8:
            for j in range(3):
                vals[TARGET_SCALE + 4 * j] = base[k]
                k += 1
        if bo.flags & 0x10:
            for j in range(4):
                vals[TARGET_ROT + 4 * j] = base[k]
                k += 1
        if bo.flags & 0x20:
            for j in range(3):
                vals[TARGET_TRANS + 4 * j] = base[k]
                k += 1
        for cv in bo.curves:
            vals[cv.target] = cv.at_frame(frame)
        out[bo.name] = ([vals.get(TARGET_SCALE + 4 * j) for j in range(3)],
                        [vals.get(TARGET_ROT + 4 * j) for j in range(4)],
                        [vals.get(TARGET_TRANS + 4 * j) for j in range(3)])
    return out


FMAA_SIZE = 0x70   # a version 10 material animation record


def copy_material_anims(t, blob):
    """The template animation file's material animations (header 0x68, names 0x70) copied into blob, for a model that
    keeps the template model's materials in their order: the records, and the data they point to (one run between
    the last skeletal animation data and the string block, which points nowhere else), moved as they are with their
    pointer fields set again; strings by their text. A material animation that switches textures (its texture names,
    record +0x30) would show the template's own textures and is left out. Returns how many were copied."""
    n, arr = u16(t, 0xe4), u64(t, 0x68)
    if not n or not arr:
        return 0
    _, ptrs = relocations(t)
    ptrs = set(ptrs)
    strs = t.find(b'_STR')
    keep = [arr + FMAA_SIZE * i for i in range(n) if not u64(t, arr + FMAA_SIZE * i + 0x30)]
    if any(t[r:r + 4] != b'FMAA' for r in keep):
        raise ValueError('the template\'s material animations are not laid out as expected')
    targets = [u64(t, r + o) for r in keep for o in range(8, FMAA_SIZE, 8) if r + o in ptrs]
    lo = min((x for x in targets if x < strs), default=strs)
    if not keep:
        return 0
    run = blob.bytes_(t[lo:strs])
    new = blob.alloc(FMAA_SIZE * len(keep))
    for i, r in enumerate(keep):
        blob.put(new + FMAA_SIZE * i, t[r:r + FMAA_SIZE])

    def moved(p, at):
        x = u64(t, p)
        if x >= strs:
            blob.ptr(at, Str(sname(t, x)))
        elif lo <= x:
            blob.ptr(at, run + x - lo)
        else:
            raise ValueError(f'a material animation points outside its data ({x:#x})')
    for i, r in enumerate(keep):
        for o in range(0, FMAA_SIZE, 8):
            if r + o in ptrs:
                moved(r + o, new + FMAA_SIZE * i + o)
    for p in ptrs:
        if lo <= p < strs:
            moved(p, run + p - lo)
    blob.ptr(0x68, new)
    blob.ptr(0x70, blob.dic([sname(t, u64(t, r + 8)) for r in keep]))
    return len(keep)


def write_anim_file(template, file_name, anims, materials=True):
    """A version 10 animation file holding anims (as read_anims gives them, each with the name, flags and frame count
    it is to have): the template file's header (an animation file of this game) with its skeletal animations replaced
    and, with materials, its material animations kept (copy_material_anims). A curve's frames are written as bytes
    when all are whole and up to 255, else as floats; a version 5 cubic or linear curve's keys as this game's
    (hermite_keys) in floats, any other curve's as they are."""
    if u16(template, 0xa) < 9 or u64(template, 0x28) or u64(template, 0x58) != 0xf0:
        raise ValueError('the template is not an animation file of this game')
    names = [a.name for a in anims]
    if len(set(names)) != len(names):
        raise ValueError('two animations of the same name')
    blob = Blob(0)
    head = bytearray(template[:0xf0])
    for o in range(0x20, 0xd8, 8):   # every pointer of the header is set again below
        struct.pack_into('<Q', head, o, 0)
    blob.data += head
    blob.ptr(0x20, Str(file_name))
    arr = blob.alloc(0x50 * len(anims))
    blob.ptr(0x58, arr)
    blob.ptr(0x60, blob.dic(names))
    # the counts of the header's arrays (0xdc to 0xeb) and its flags (0xee): the file holds skeletal animations, the
    # template's material animations (copy_material_anims) and the external file below, nothing else. A count left
    # standing for an array not carried over would have the engine read it through a null pointer.
    for o in range(0xdc, 0xec, 2):
        struct.pack_into('<H', blob.data, o, 0)
    blob.data[0xee] = 0
    struct.pack_into('<H', blob.data, 0xe2, len(anims))
    if materials:
        n = copy_material_anims(template, blob)
        if n:
            struct.pack_into('<H', blob.data, 0xe4, n)
            blob.data[0xee] = template[0xee]
    # the external file the template carries (a marker the engine looks for), kept
    ext_n = u16(template, 0xec)
    if ext_n:
        ext_names = dic_names(template, u64(template, 0xc0))
        ext = blob.alloc(16 * ext_n)
        for k in range(ext_n):
            blob.put(ext + 16 * k, template[u64(template, 0xb8) + 16 * k:u64(template, 0xb8) + 16 * k + 16])
        blob.ptr(0xb8, ext)
        blob.ptr(0xc0, blob.dic(ext_names))
    for i, a in enumerate(anims):
        p = arr + 0x50 * i
        nb = len(a.bones)
        blob.put(p, b'FSKA' + struct.pack('<I', a.flags))
        blob.ptr(p + 8, Str(a.name))
        blob.ptr(p + 0x10, Str(''))
        bind = blob.bytes_(b'\xff\xff' * nb, 8)
        blob.ptr(p + 0x20, bind)
        barr = blob.alloc(0x38 * nb)
        blob.ptr(p + 0x28, barr)
        ncurve = sum(len(bo.curves) for bo in a.bones)
        first = 0
        for k, bo in enumerate(a.bones):
            q = barr + 0x38 * k
            blob.ptr(q, Str(bo.name))
            if bo.base:
                blob.ptr(q + 0x10, blob.bytes_(struct.pack('<%df' % len(bo.base), *bo.base), 4))
            begin = bytearray(bo.begin)
            begin[2] = len(bo.curves)
            blob.put(q + 0x28, struct.pack('<I', bo.flags) + bytes(begin) + struct.pack('<I', first))
            first += len(bo.curves)
            if bo.curves:
                carr = blob.alloc(0x30 * len(bo.curves))
                blob.ptr(q + 8, carr)
                for c, cv in enumerate(bo.curves):
                    r = carr + 0x30 * c
                    if cv.hermite or cv.kind > 1:   # this game's already, or not a cubic or linear one
                        frames, keys, nkey = cv.frames, cv.keys, cv.nkey
                        kflags, kf = cv.flags, KEY_SIZE[cv.flags >> 2 & 3][1]
                        scale, offset, delta = cv.scale, cv.offset, cv.delta
                    else:   # a version 5 curve: float keys of value and slope, as this game's own float curves
                        hk = hermite_keys(cv)
                        frames, keys, nkey = [k[0] for k in hk], [x for k in hk for x in k[1:]], len(hk)
                        kflags, kf, scale, offset, delta = 0, 'f', 0.0, 0.0, 0.0
                    whole = all(x == int(x) and 0 <= x <= 255 for x in frames)
                    flags = (kflags & 0x7c) | (2 if whole else 0) | 0x4000 | (0x800 if kflags >> 2 & 3 else 0)
                    if whole:   # whole frames up to 255 as bytes, as this game's own files hold them
                        fr = blob.bytes_(bytes(int(x) for x in frames), 4)
                    else:
                        fr = blob.bytes_(struct.pack('<%df' % nkey, *frames), 4)
                    ky = blob.bytes_(struct.pack('<%d%s' % (len(keys), kf), *keys), 4)
                    blob.ptr(r, fr)
                    blob.ptr(r + 8, ky)
                    blob.put(r + 0x10, struct.pack('<HHI2f4x3f', flags, nkey, cv.target, cv.start, cv.end, scale,
                                                   offset, delta))
        # the size of the animation sampled at every frame (a float per curve and frame, a looping one without its
        # last frame, and 32 bytes per curve), as this game's files give it
        baked = ncurve * (4 * (a.frames + (0 if a.flags & 4 else 1)) + 32)
        blob.put(p + 0x40, struct.pack('<iiIHH', a.frames, ncurve, baked, nb, 0))
    blob.ptr(0xd0, Str(''))
    strs, where = blob.strings()
    rlt = blob.alloc(0, 8)
    data = blob.data
    struct.pack_into('<Q', data, strs + 8, rlt - strs)
    struct.pack_into('<I', data, 0xd8, rlt - strs)
    struct.pack_into('<IHH', data, 0x10, where[file_name] + 2, 0, strs & 0xffff)
    sections = [(0, rlt)] + [(rlt, 0)] * 5
    table = rlt_block(rlt, sections, blob.ptrs, data)
    out = bytearray(data) + table
    struct.pack_into('<II', out, 0x18, rlt, len(out))
    return bytes(out)


# ---- writing a model into a model file of this game

def bone_hints(scale, rot, trans, euler):
    """The transform hints of a bone's flags: uniform scale, scale of volume one, no rotation, no translation."""
    flags = 0
    if abs(scale[0] - scale[1]) < 1e-6 and abs(scale[1] - scale[2]) < 1e-6:
        flags |= 0x01000000
    if abs(scale[0] * scale[1] * scale[2] - 1) < 1e-6:
        flags |= 0x02000000
    if all(abs(x) < 1e-7 for x in rot[:3]) and (euler or abs(abs(rot[3]) - 1) < 1e-7):
        flags |= 0x04000000
    if all(abs(x) < 1e-7 for x in trans):
        flags |= 0x08000000
    return flags


def rebuild_model(base, new):
    """A model file of this game: base (one of the game's model files; its first model) with what new gives in place
    of its skeleton, geometry and texture names. new holds:
      skel_flags                          the skeleton's flags (rotation mode, scale mode)
      bones [Rec(name, parent, flags, scale, rot (4), trans, world)]   parents first
      smooth, rigid                       the matrix palette: bone indices
      shapes {base shape name: Rec(vertices: bytes in the shape's own layout, count, indices: [int],
                                   skin_bones: [bone index], radius, center, extent,
                                   formats: {attribute name: a format of the same size in its place})}
      textures {material name: [texture name of each slot]}
      file_name                           the name in the file's header (<project>.<model>)
    The file keeps its records where they are; the new records, names and arrays go after its main data, the vertex
    and index data make a new buffer area, and the relocation table is written anew."""
    b = base
    sections, ptrs = relocations(b)
    if len(sections) != 6:
        raise ValueError('the base model file is not laid out as expected')
    A, isz = sections[1]
    vpos, vsz = sections[2]
    B = sections[4][0]
    R = u32(b, 0x18)
    if vpos != A + isz or B != vpos + vsz or sections[3][0] != B or u64(b, 0x170) != A:
        raise ValueError("the base model file's buffer area is not laid out as expected")
    f = Bfres(b)
    m = f.models[0]
    G = align(sections[0][1], 16)
    main = bytearray(b[:G])
    fields = {p: u64(b, p) for p in ptrs}   # pointer field -> its target in the base file
    moved = {}                              # pointer field -> its new target (a place in the new file, or a Str)

    def setp(at, target):
        moved[at] = target
        fields.pop(at, None)
    blob = Blob(G)

    # skeleton
    sk = m.skel_at
    nb = len(new.bones)
    barr = blob.alloc(0x58 * nb)
    for i, bo in enumerate(new.bones):
        q = barr + 0x58 * i
        blob.ptr(q, Str(bo.name))
        sm = new.smooth.index(i) if i in new.smooth else 0xffff
        rg = new.rigid.index(i) if i in new.rigid else 0xffff
        blob.put(q + 0x20, struct.pack('<6HI3f4f3f', i, bo.parent & 0xffff, sm, rg, 0xffff, 0, bo.flags,
                                       *bo.scale, *bo.rot, *bo.trans))
    setp(sk + 0x10, barr)
    setp(sk + 0x08, blob.dic([bo.name for bo in new.bones]))
    pal = new.smooth + new.rigid
    setp(sk + 0x18, blob.bytes_(struct.pack('<%dH' % len(pal), *pal)))
    inv = bytearray()
    for i in new.smooth:
        w = mat_inv(new.bones[i].world)
        inv += struct.pack('<12f', *(x for row in w[:3] for x in row))
    setp(sk + 0x20, blob.bytes_(bytes(inv), 16) if inv else 0)
    struct.pack_into('<I', main, sk + 4, new.skel_flags)
    struct.pack_into('<3H', main, sk + 0x38, nb, len(new.smooth), len(new.rigid))

    # geometry: index data first, then vertex data, as the game's files have it
    idx_data, vtx_data = bytearray(), bytearray()
    placed = []
    for s in m.shapes:
        g = new.shapes[s.name]
        v = m.vtx[s.vtx]
        if len(v.sizes) != 1:
            raise ValueError(f'{s.name}: {len(v.sizes)} vertex buffers, one expected')
        wide = g.count > 0xffff
        ib = struct.pack('<%d%s' % (len(g.indices), 'I' if wide else 'H'), *g.indices)
        io = align(len(idx_data), 0x100)
        idx_data += bytes(io - len(idx_data)) + ib
        vo = align(len(vtx_data), 0x100)
        vtx_data += bytes(vo - len(vtx_data)) + g.vertices
        placed.append((s, v, g, io, len(ib), vo, wide))
    isz2 = align(len(idx_data), 0x1000)
    for s, v, g, io, ilen, vo, wide in placed:
        struct.pack_into('<I', main, v.at + 0x48, isz2 + vo)
        arr = u64(b, v.at + 8)   # the attribute records: name, u16 format, u16 offset, u16 buffer
        for i, (name, f, off, buf) in enumerate(v.attrs):
            fmt = getattr(g, 'formats', {}).get(name)
            if fmt is not None:
                if LAYOUT[fmt >> 8] != LAYOUT[f >> 8]:
                    raise ValueError(f'{s.name}: {name} cannot change from {f:#x} to {fmt:#x}')
                struct.pack_into('<H', main, arr + 16 * i + 8, fmt)
        struct.pack_into('<I', main, v.at + 0x50, g.count)
        struct.pack_into('<I', main, v.size_arr, len(g.vertices))
        for me in s.meshes:   # every level of detail draws the whole mesh
            struct.pack_into('<5I', main, me.at + 0x20, io, me.prim, 2 if wide else 1, len(g.indices), 0)
            struct.pack_into('<I', main, me.size_at, ilen)
            for k in range(me.nsub):
                struct.pack_into('<2I', main, me.sub_arr + 8 * k, 0, len(g.indices) if k == 0 else 0)
        nbox = sum(me.nsub + 1 for me in s.meshes)
        for k in range(nbox):
            struct.pack_into('<6f', main, s.bbox + 24 * k, *g.center, *g.extent)
        sk_bones = g.skin_bones or [0]
        setp(s.at + 0x20, blob.bytes_(struct.pack('<%dH' % len(sk_bones), *sk_bones), 8))
        sph = blob.alloc(16 * len(sk_bones), 8)
        for k in range(len(sk_bones)):
            blob.put(sph + 16 * k, struct.pack('<4f', 0.0, 0.0, 0.0, g.radius))
        setp(s.at + 0x40, sph)
        struct.pack_into('<H', main, s.at + 0x58, len(sk_bones))
        struct.pack_into('<H', main, s.at + 0x54, 0)
    # texture names
    for mt in m.mats:
        names = new.textures.get(mt.name)
        if names is None:
            continue
        if len(names) != len(mt.textures):
            raise ValueError(f'{mt.name}: {len(names)} textures for {len(mt.textures)} slots')
        for k, t in enumerate(names):
            if t != mt.textures[k]:
                setp(mt.tex_arr + 8 * k, Str(t))
    setp(0x20, Str(new.file_name))
    # the names: the new ones after the records, as a string block's strings are
    where = blob.strings_plain([s for s in moved.values() if isinstance(s, Str)])
    for at, s in list(moved.items()):
        if isinstance(s, Str):
            moved[at] = where[s]
    # places
    E = G + len(blob.data)
    A2 = align(E, 0x1000)
    B2 = A2 + isz2 + align(len(vtx_data), 0x1000)
    R2 = B2 + (R - B)

    def remap(t):
        if t < A:
            return t
        if t < B:
            return t - A + A2
        return t - B + B2
    data = bytearray(main) + blob.data + bytes(A2 - E)
    data += idx_data + bytes(isz2 - len(idx_data)) + vtx_data + bytes(B2 - A2 - isz2 - len(vtx_data))
    data += b[B:R]
    allp = list(blob.ptrs)
    for p, t in fields.items():
        struct.pack_into('<Q', data, p, remap(t))
        allp.append(p)
    for p, t in moved.items():
        struct.pack_into('<Q', data, p, t)
        if t:
            allp.append(p)
    struct.pack_into('<I', data, 0x16c, B2 - A2)
    strpos = u64(b, 0xd0) - 0x14   # the string block: its size runs to the relocation table
    if data[strpos:strpos + 4] != b'_STR':
        raise ValueError("the base model's string block is not where its header says")
    struct.pack_into('<Q', data, strpos + 8, R2 - strpos)
    struct.pack_into('<I', data, 0x10, where[new.file_name] + 2)
    secs = [(0, E), (A2, isz2), (A2 + isz2, B2 - A2 - isz2), (B2, 0), (B2, R - B), (R2, 0)]
    data += rlt_block(R2, secs, allp, data)
    struct.pack_into('<II', data, 0x18, R2, len(data))
    return bytes(data)
