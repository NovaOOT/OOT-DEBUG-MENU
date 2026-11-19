"""The models in an N64 Ocarina of Time object file (tools/n64rom.py Rom.object): skeletons and display lists, found
by their shapes, since the ROM has no table of them.

Addresses in an object are segmented: segment 6 is the object itself, 4 gameplay_keep.

- A skeleton is 8 bytes: a pointer (segment 6) to its limb table and the number of limbs, then three zero bytes; a
  flexible skeleton (one whose limbs share vertices) adds the number of limbs that have a display list and three zero
  bytes. The limb table points at each limb: its joint position (s16 x 3, relative to its parent, in model units), its
  first child and next sibling (0xFF none) and its display list (0 none); a limb with a far display list has a second
  one, which is not read.
- A display list is found as a run of valid commands that ends with G_ENDDL. The models are the display lists no other
  display list or limb calls ("roots"), and the skeletons.

In a flexible skeleton's display lists a G_MTX of segment 0x0D picks the matrix the following vertices move with: the
n-th matrix belongs to the n-th limb that has a display list. find() returns limb numbers for them, so a vertex's bone
is always a limb number.

Standard library only.
"""
import struct

import n64gfx

SEG = 0x06
VALID_OPS = {0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0xD3, 0xD6, 0xD7, 0xD8, 0xD9, 0xDA, 0xDB, 0xDC, 0xDE,
             0xDF, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9, 0xEA, 0xEB, 0xEC, 0xED, 0xEE, 0xEF, 0xF0, 0xF1,
             0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF7, 0xF8, 0xF9, 0xFA, 0xFB, 0xFC, 0xFD, 0xFE, 0xFF}


class Limb:
    __slots__ = ('x', 'y', 'z', 'child', 'sibling', 'dl', 'parent')


class Skeleton:
    def __init__(self, offset, limbs, flex):
        self.offset, self.limbs, self.flex = offset, limbs, flex


def _ptr_ok(addr, size, seg=SEG):
    return (addr >> 24) == seg and (addr & 0xFFFFFF) < size


def _skeleton_at(d, o):
    size = len(d)
    if o + 8 > size:
        return None
    p, n = struct.unpack_from('>IB', d, o)
    if not _ptr_ok(p, size) or not 1 <= n <= 96 or d[o + 5:o + 8] != b'\0\0\0':
        return None
    table = p & 0xFFFFFF
    if table % 4 or table + 4 * n > size:
        return None
    flex = o + 12 <= size and 0 < d[o + 8] <= n and d[o + 9:o + 12] == b'\0\0\0'
    limbs = []
    for k in range(n):
        lp = struct.unpack_from('>I', d, table + 4 * k)[0]
        if not _ptr_ok(lp, size) or (lp & 0xFFFFFF) % 4 or (lp & 0xFFFFFF) + 12 > size:
            return None
        lo = lp & 0xFFFFFF
        x, y, z, child, sib, dl = struct.unpack_from('>hhhBBI', d, lo)
        if (child != 0xFF and child >= n) or (sib != 0xFF and sib >= n) or (dl and not _ptr_ok(dl, size)):
            return None
        lb = Limb()
        lb.x, lb.y, lb.z, lb.child, lb.sibling, lb.dl, lb.parent = x, y, z, child, sib, dl, -1
        limbs.append(lb)
    # the hierarchy: limb 0 is the root, every other limb is reached from it exactly once
    seen = set()

    def visit(i, parent, depth):
        while i != 0xFF:
            if i in seen or depth > 96:
                return False
            seen.add(i)
            limbs[i].parent = parent
            if limbs[i].child != 0xFF and not visit(limbs[i].child, i, depth + 1):
                return False
            i = limbs[i].sibling
        return True
    if not visit(0, -1, 0) or len(seen) != n:
        return None
    if not any(lb.dl for lb in limbs):
        return None
    return Skeleton(o, limbs, flex)


def _dl_length(d, o, limit=4096):
    """The number of commands of a valid display list at d[o:], ending with G_ENDDL (or a jump); 0 when not one."""
    size = len(d)
    n = 0
    tris = vtx = 0
    while o + 8 <= size and n < limit:
        op = d[o]
        if op not in VALID_OPS:
            return 0
        w0, w1 = struct.unpack_from('>II', d, o)
        if op == 0x01:
            vtx += 1
            if (w1 >> 24) not in (SEG, 0x04, 0x05) or ((w1 >> 24) == SEG and (w1 & 0xFFFFFF) >= size):
                return 0
        elif op in (0x05, 0x06, 0x07):
            tris += 1
        elif op == 0xDE:
            seg = w1 >> 24
            if seg == SEG and (w1 & 0xFFFFFF) >= size:
                return 0
            if (w0 >> 16) & 0xFF:   # a jump ends this list
                return n + 1
        elif op == 0x00 and (w0 or w1):
            return 0
        n += 1
        o += 8
        if op == 0xDF:
            return n if n > 1 else 0
    return 0


def find(data):
    """The skeletons and the root display lists of an object file: (skeletons, roots as segmented addresses)."""
    size = len(data)
    skeletons = []
    for o in range(0, size - 8, 4):
        s = _skeleton_at(data, o)
        if s:
            skeletons.append(s)
    # a skeleton whose limb table another skeleton also uses is the same model (a flexible skeleton's header is a
    # plain one with four bytes more): keep the one found first at each table... prefer the flexible header
    by_table = {}
    for s in skeletons:
        t = struct.unpack_from('>I', data, s.offset)[0]
        if t not in by_table or (s.flex and not by_table[t].flex):
            by_table[t] = s
    skeletons = sorted(by_table.values(), key=lambda s: s.offset)

    # display lists: every place where one starts and that something points at with segment 6
    called = set()
    for s in skeletons:
        for lb in s.limbs:
            if lb.dl:
                called.add(lb.dl & 0xFFFFFF)
    starts = []
    o = 0
    while o + 8 <= size:
        n = _dl_length(data, o)
        if n >= 2:
            starts.append((o, n))
            o += 8 * n
        else:
            o += 8
    for o, n in starts:
        for k in range(n):
            w0, w1 = struct.unpack_from('>II', data, o + 8 * k)
            if (w0 >> 24) == 0xDE and (w1 >> 24) == SEG:
                called.add(w1 & 0xFFFFFF)
    # a list that draws something (it loads vertices or calls one that does) and nothing calls is a model
    roots = []
    for o, n in starts:
        if o in called:
            continue
        has = False
        for k in range(n):
            op = data[o + 8 * k]
            if op in (0x01, 0x05, 0x06, 0x07, 0xDE):
                has = True
                break
        if has:
            roots.append(SEG << 24 | o)
    return skeletons, roots


def skeleton_mesh(segments, skel, mesh=None, env=None):
    """The mesh of a skeleton: each limb's display list in limb order, its vertices bound to that limb (or, in a
    flexible skeleton, to the limb whose matrix a G_MTX picked). env: the environment colour the game sets before it
    draws the skeleton (r, g, b, a), when it sets one: the materials that keep it are marked as the tunic's. The mesh's
    parts say which triangles each limb's display list drew."""
    w = n64gfx.Walker(segments, mesh)
    if env is not None:
        w.env = tuple(env)
        w.env_tunic = True
    dl_limbs = [i for i, lb in enumerate(skel.limbs) if lb.dl]
    for i, lb in enumerate(skel.limbs):
        if not lb.dl:
            continue
        w.bone = i
        first = len(w.mesh.verts)
        first_tri = len(w.mesh.tris)
        w.walk(lb.dl)
        w.mesh.parts.append((i, first_tri, len(w.mesh.tris)))
        if skel.flex:
            # the vertices a G_MTX bound took the matrix number: turn it into the limb number
            for k in range(first, len(w.mesh.verts)):
                v = w.mesh.verts[k]
                b = v[9]
                if b != i:
                    v = v[:9] + ((dl_limbs[b] if b < len(dl_limbs) else i),)
                    w.mesh.verts[k] = v
    return w.mesh
