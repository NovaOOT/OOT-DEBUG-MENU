"""N64 Link for the N64 Graphics mod (tools/make_n64_mod.py): his objects' fixed segments and a standing pose from his
own animations, which runtime/n64pose.c turns the remake player's skeleton into.

Link's two objects (20 adult, 21 child) are flexible skeletons of 21 limbs whose display lists also read three
segments the player's draw sets every frame: 0x08 the eyes' texture and 0x09 the mouth's (both kept at the start of the
object: eight eye textures of 0x800 bytes from 0, then four mouths of 0x400 bytes from 0x4000), and 0x0C a display
list that turns back-face culling on. The tunic's colour (and the hat's) is the environment colour the draw sets before the skeleton.
The mod is made with the Kokiri tunic, and draws his face once per eye texture and once per mouth texture
(face_variants), for runtime/n64pose.c to show one of each.

His animations are not in the objects. gameplay_keep (object 1) holds their headers, 8 bytes each: u16 frame count,
u16 0, u32 segment 7 address of the frames. Segment 7 is a file of its own (link_animetion), found here as the ROM file
in which the headers' frames read as animation. A frame is 67 s16: the root limb's position (model units), then each
of the 21 limbs' rotation (x, y, z in 1/65536 turns, applied z first, then y, then x, in the limb's parent's frame),
then the face (eyes and mouth).

The player's code (the ROM's code file) picks, per frame, what his hands and his sheath limb draw from a table of
display lists. Each row is four segment 6 addresses, (adult near, child near, adult far, child far), 0 where that age
has none; a limb's own display list may stand in a row. player_tables() finds the table as the longest run of such
rows and names the rows the mod uses (ROWS). Right after the table come the tunics' environment colours: three
r, g, b triples, Kokiri, Goron and Zora.

Standard library only.
"""
import math
import struct

import n64gfx
import n64obj

LINK_OBJECTS = {20: 'adult', 21: 'child'}
LIMBS = 21
FRAME = 2 * (3 + 3 * LIMBS + 1)   # 134 bytes
EYES = 0x0000
MOUTH = 0x4000
EYE_STEP, EYE_COUNT = 0x800, 8       # open, half shut, shut, then looking aside, wide and squinting
MOUTH_STEP, MOUTH_COUNT = 0x400, 4   # shut, half open, open, smiling
KOKIRI_TUNIC = (30, 105, 27, 255)
G_CULL_BACK = 0x00000400


def cull_back_list():
    """The display list segment 0x0C points at: set back-face culling, end."""
    return struct.pack('>IIII', 0xD9FFFFFF, G_CULL_BACK, 0xDF000000, 0)


def segments(segs, data):
    """Sets the player's fixed segments on segs (n64gfx.Segments) for one of Link's objects."""
    segs.set(0x08, data[EYES:])
    segs.set(0x09, data[MOUTH:])
    segs.set(0x0C, cull_back_list())


def _headers(keep, size=None):
    out = []
    for o in range(0, len(keep) - 8, 4):
        n, pad, seg = struct.unpack_from('>HHI', keep, o)
        at = seg & 0xFFFFFF
        if 1 <= n <= 1000 and pad == 0 and seg >> 24 == 7 and not at & 1 and (size is None or at + n * FRAME <= size):
            out.append((n, at))
    return out


def _smooth(data, n, at):
    """The frames of a header read as animation: consecutive frames differ little."""
    if n < 3 or at + n * FRAME > len(data):
        return False
    a = struct.unpack_from('>%dh' % (n * 67), data, at)
    total = 0
    for f in range(1, n):
        for k in range(66):
            d = (a[f * 67 + k] - a[(f - 1) * 67 + k]) & 0xFFFF
            total += min(d, 0x10000 - d) if k >= 3 else abs(a[f * 67 + k] - a[(f - 1) * 67 + k])
    return total / ((n - 1) * 66) < 600


def animation_file(rom):
    """(the file's bytes, [(frames, offset)] of the headers whose frames are in it), or (None, []). Kept on rom."""
    found = getattr(rom, '_link_animations', None)
    if found is None:
        found = rom._link_animations = _animation_file(rom)
    return found


def _animation_file(rom):
    keep = rom.object(1)
    if not keep:
        return None, []
    heads = _headers(keep)
    best = (0, None)
    for vs, ve, ps, pe in rom.files:
        if ve - vs < 0x100000 or ps == 0xFFFFFFFF:
            continue
        d = rom.file(vs)
        if not d:
            continue
        sample = [h for h in heads if h[1] + h[0] * FRAME <= len(d)][:40]
        good = sum(_smooth(d, n, at) for n, at in sample)
        if good > best[0]:
            best = (good, d)
    if best[0] < 10:
        return None, []
    d = best[1]
    return d, [h for h in heads if _smooth(d, *h)]


def _rot(x, y, z):
    """Row-major 3x3 of the rotation z, then y, then x (Rz Ry Rx), angles in 1/65536 turns."""
    ax, ay, az = (v * math.pi / 32768 for v in (x, y, z))
    cx, sx, cy, sy, cz, sz = math.cos(ax), math.sin(ax), math.cos(ay), math.sin(ay), math.cos(az), math.sin(az)
    return [[cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
            [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
            [-sy, cy * sx, cy * cx]]


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def limb_axes(skel, frame):
    """Each limb's x axis (the way the limb runs) in model space for one frame (67 s16)."""
    world = []
    for i, lb in enumerate(skel.limbs):
        r = _rot(*frame[3 + 3 * i:6 + 3 * i])
        world.append(r if lb.parent < 0 else _mul(world[lb.parent], r))
    return [(w[0][0], w[1][0], w[2][0]) for w in world], world


# limbs whose x axis points down when he stands: thighs, shins, upper arms, forearms
_DOWN = (3, 4, 6, 7, 13, 14, 16, 17)
_UP = (9, 20)   # the upper body and the torso


def rest_pose(rom, skel):
    """67 s16: the frame of his animations nearest to standing straight, facing +z with his arms down, or None."""
    if len(skel.limbs) != LIMBS:
        return None
    data, heads = animation_file(rom)
    if not data:
        return None
    best = (-1e9, None)
    for n, at in heads:
        frame = struct.unpack_from('>67h', data, at)
        axes, world = limb_axes(skel, frame)
        score = sum(-axes[i][1] for i in _DOWN) + sum(axes[i][1] for i in _UP)
        score += 2 * world[0][2][2]   # the root keeps +z ahead
        if score > best[0]:
            best = (score, frame)
    return list(best[1]) if best[1] else None


# The rows of the hand and sheath table the mod uses, by their place in it (runtime/n64pose.c reads them in this order).
ROWS = {
    'rh_closed': 0, 'rh_deku': 1, 'rh_hylian': 2, 'rh_mirror': 3,
    'sheath_sword': 4, 'sheath_sword_deku': 5, 'sheath_sword_hylian': 6, 'sheath_sword_mirror': 7,
    'sheath_empty': 10, 'sheath_empty_deku': 11, 'sheath_empty_hylian': 12, 'sheath_empty_mirror': 13,
    'lh_biggoron': 16, 'lh_broken_knife': 17, 'lh_open': 18, 'lh_closed': 19, 'lh_sword': 20, 'rh_open': 22,
}
ROW_ORDER = sorted(ROWS, key=ROWS.get)


def player_tables(rom):
    """([(adult near, child near, adult far, child far)] for the rows of ROW_ORDER, [Kokiri, Goron, Zora tunic r, g, b]),
    or (None, None) when the table is not found. Kept on rom."""
    found = getattr(rom, '_link_tables', None)
    if found is None:
        found = rom._link_tables = _player_tables(rom)
    return found


def _player_tables(rom):
    adult, child = rom.object(20), rom.object(21)
    if not adult or not child:
        return None, None

    def lists(d):
        skels, roots = n64obj.find(d)
        out = set(r & 0xFFFFFF for r in roots)
        for s in skels:
            out.update(lb.dl & 0xFFFFFF for lb in s.limbs if lb.dl)
        return out
    a_dl, c_dl = lists(adult), lists(child)

    def ok(v, d):   # none, or a display list's place in the object
        return v == 0 or (v >> 24 == 6 and not v & 7 and (v & 0xFFFFFF) < len(d))

    def row_ok(o):
        w = struct.unpack_from('>4I', rom.code, o)
        return ok(w[0], adult) and ok(w[1], child) and ok(w[2], adult) and ok(w[3], child)

    def real(o):   # the table starts with a row of four known display lists
        w = struct.unpack_from('>4I', rom.code, o)
        return all(v >> 24 == 6 and (v & 0xFFFFFF) in (a_dl, c_dl)[k & 1] for k, v in enumerate(w))
    code = rom.code
    best, bestn = None, 0
    o = 0
    while o + 16 <= len(code):
        if not real(o) or not row_ok(o):
            o += 4
            continue
        n = 0
        while o + 16 * (n + 1) <= len(code) and row_ok(o + 16 * n):
            n += 1
        if n > bestn:
            best, bestn = o, n
        o += 16 * n
    if best is None or bestn <= max(ROWS.values()):
        return None, None
    rom._link_table = (best, bestn)
    rows = [struct.unpack_from('>4I', code, best + 16 * ROWS[k]) for k in ROW_ORDER]
    at = code.find(bytes(KOKIRI_TUNIC[:3]), best + 16 * bestn, best + 16 * bestn + 0x400)
    tunics = [tuple(code[at + 3 * k:at + 3 * k + 3]) for k in range(3)] if at >= 0 else [KOKIRI_TUNIC[:3]]
    return rows, tunics


# ---- gear the age's own lists lack
#
# The remake lets either age hold any sword or shield; the N64 game draws only the age's own. These pieces, written
# after ROW_ORDER's rows in the player section (make_n64_mod.py), let runtime/n64pose.c draw the rest: the other age's
# swords without their hand (drawn with the age's own closed hand), its sheath, and each shield alone, on the back
# and in hand. Each is (name, whose lists, row, row subtracted): 'other' the other age's object, 'any' the age's own
# when its two rows differ (it has that shield) and the other age's when not.
EXTRAS = [
    ('x_sword', 'other', 'lh_sword', 'lh_closed'),
    ('x_biggoron', 'other', 'lh_biggoron', 'lh_closed'),
    ('x_broken_knife', 'other', 'lh_broken_knife', 'lh_closed'),
    ('x_sheath_sword', 'other', 'sheath_sword', None),
    ('x_sheath_empty', 'other', 'sheath_empty', None),
    ('back_deku', 'any', 'sheath_empty_deku', 'sheath_empty'),
    ('back_hylian', 'any', 'sheath_empty_hylian', 'sheath_empty'),
    ('back_mirror', 'any', 'sheath_empty_mirror', 'sheath_empty'),
    ('hand_deku', 'any', 'rh_deku', 'rh_closed'),
    ('hand_hylian', 'any', 'rh_hylian', 'rh_closed'),
    ('hand_mirror', 'any', 'rh_mirror', 'rh_closed'),
]


def _tri_key(mesh, t):
    return tuple(sorted(tuple(round(c) for c in mesh.verts[i][:3]) for i in t[1:]))


def _shape(mesh, tris):
    """A part's material structure: its triangle counts per material, sorted."""
    count = {}
    for t in tris:
        count[t[0]] = count.get(t[0], 0) + 1
    return sorted(count.values())


def subtract(a, b):
    """The triangles of mesh a that are not mesh b's: those at b's places, and then any connected part of what is left
    with b's material structure (a hand posed otherwise in a's list)."""
    places = {_tri_key(b, t) for t in b.tris}
    left = [t for t in a.tris if _tri_key(a, t) not in places]
    if len(left) > len(a.tris) - len(b.tris):
        corners = [{tuple(round(c) for c in a.verts[i][:3]) for i in t[1:]} for t in left]
        group = list(range(len(left)))

        def root(x):
            while group[x] != x:
                group[x] = group[group[x]]
                x = group[x]
            return x
        owner = {}
        for i, cs in enumerate(corners):
            for c in cs:
                if c in owner:
                    group[root(i)] = root(owner[c])
                else:
                    owner[c] = i
        parts = {}
        for i in range(len(left)):
            parts.setdefault(root(i), []).append(left[i])
        want = _shape(b, b.tris)
        drop = next((p for p in parts.values() if _shape(a, p) == want), None)
        if drop:
            gone = {id(t) for t in drop}
            left = [t for t in left if id(t) not in gone]
    return left


def extra_pieces(rom, number, walk):
    """For Link's object number: [(name, mesh, triangles, display list offset)] of EXTRAS, mesh None where a piece
    cannot be made. walk(object number, segment 6 address) gives the mesh of that object's list."""
    rows, _tunics = player_tables(rom)
    if not rows:
        return []
    by = dict(zip(ROW_ORDER, rows))
    own = 1 if LINK_OBJECTS[number] == 'child' else 0
    other_number = {v: k for k, v in LINK_OBJECTS.items()}['adult' if own else 'child']
    out = []
    for name, whose, row, minus in EXTRAS:
        age, obj = (1 - own, other_number) if whose == 'other' else (own, number)
        if whose == 'any' and by[row][own] == by[minus][own]:
            age, obj = 1 - own, other_number
        addr = by[row][age]
        if not addr or (minus and by[minus][age] == addr):
            out.append((name, None, [], 0))
            continue
        mesh = walk(obj, addr)
        tris = subtract(mesh, walk(obj, by[minus][age])) if minus else list(mesh.tris)
        out.append((name, mesh if tris else None, tris, addr & 0xFFFFFF))
    return out


# ---- what he holds and wears besides sword and shield
#
# Pieces written after EXTRAS in the player section (make_n64_mod.py) for runtime/n64pose.c, in ITEM_ORDER. Hand items
# come from the table's rows (rows 24 and 29 to 34 of player_tables' table: the right hand with the bow or the
# slingshot, with either ocarina, with the hookshot; the left hand with the hammer, the boomerang, a bottle's grip).
# Each has two pieces: NAME, the age's own row drawn in place of the hand, and NAME_alone, the item without its hand
# (from the age that has it), drawn beside the age's own closed hand when NAME is none.
HAND_ITEMS = [   # name, table row, hand, the age whose row has it ('any': each age's own)
    ('bow', 24, 'r', 'adult'), ('slingshot', 24, 'r', 'child'), ('ocarina_fairy', 29, 'r', 'any'),
    ('ocarina_time', 30, 'r', 'any'), ('hookshot', 31, 'r', 'adult'), ('hammer', 32, 'l', 'adult'),
    ('boomerang', 33, 'l', 'child'), ('bottle_hand', 34, 'l', 'any'),
]
# Then pieces drawn beside a limb's own model, in that limb's space: (name, the age whose object has it, where).
# where: 'bottle' the player code's pair of bottle lists (adult, child), drawn see-through; ('boots', k) entry k of
# the code's boots table (iron left, iron right, hover left, hover right); else the list's offset in the object and
# its triangle count, checked (another layout of the object leaves the piece out). The gauntlet plates are the
# adult's, drawn in the code's silver or gold colour; the Deku stick lies along the hand's x axis as a sword does
# (STICK_IN_HAND).
LIMB_ITEMS = [
    ('bottle', 'own', 'bottle'),
    ('deku_stick', 'child', (0x6CC0, 36)),
    ('mask_keaton', 'child', (0x2B060, 44)), ('mask_skull', 'child', (0x2AD40, 45)),
    ('mask_spooky', 'child', (0x2AF70, 18)), ('mask_bunny', 'child', (0x2CA38, 68)),
    ('mask_goron', 'child', (0x2B350, 32)), ('mask_zora', 'child', (0x2B580, 40)),
    ('mask_gerudo', 'child', (0x2B788, 70)), ('mask_truth', 'child', (0x2B1F0, 30)),
    ('iron_boot_l', 'adult', ('boots', 0)), ('iron_boot_r', 'adult', ('boots', 1)),
    ('hover_boot_l', 'adult', ('boots', 2)), ('hover_boot_r', 'adult', ('boots', 3)),
    ('goron_bracelet', 'child', (0x16118, 18)),
] + [(f'{metal}_{part}', 'adult', (at, n)) for metal in ('silver', 'gold')
     for part, at, n in (('forearm_l', 0x25218, 12), ('hand_l', 0x252D8, 18), ('hand_l_open', 0x25438, 18),
                         ('forearm_r', 0x25598, 12), ('hand_r', 0x25658, 18), ('hand_r_open', 0x257B8, 18))]
ITEM_ORDER = [n for name, *_ in HAND_ITEMS for n in (name, name + '_alone')] + [n for n, *_ in LIMB_ITEMS]
STICK_IN_HAND = (-428.26, 267.2, -33.82)   # the stick's base in the left hand's space (_stick_in_hand)
GAUNTLETS = [(255, 255, 255), (254, 207, 15)]   # silver, gold, when the code's table is not found


def _code_words(rom):
    """{'gauntlets': [silver, gold], 'boots': [four lists], 'bottle': (adult, child)}, a key missing when not found.
    The gauntlet colours follow the tunic colours after the table, the boots' lists follow them; the bottle pair is
    the only pair of words in the code naming a see-through list of each object."""
    out = {}
    found = getattr(rom, '_link_table', None)
    code = rom.code
    if found:
        best, n = found
        at = code.find(bytes(KOKIRI_TUNIC[:3]), best + 16 * n, best + 16 * n + 0x400)
        if at >= 0:
            g = (at + 9 + 3) & ~3   # each array starts on a word
            out['gauntlets'] = [tuple(code[g + 3 * k:g + 3 * k + 3]) for k in range(2)]
            b = (g + 6 + 3) & ~3
            words = struct.unpack_from('>4I', code, b)
            if all(w >> 24 == 6 and not w & 7 for w in words):
                out['boots'] = list(words)
    adult, child = rom.object(20), rom.object(21)
    for o in range(0, len(code) - 8, 4):
        a, c = struct.unpack_from('>II', code, o)
        if a >> 24 != 6 or c >> 24 != 6 or a & 7 or c & 7 or (a & 0xFFFFFF) >= len(adult) or (c & 0xFFFFFF) >= len(child):
            continue
        if _see_through(adult, a) and _see_through(child, c):
            out['bottle'] = (a, c)
            break
    return out


def _see_through(data, addr):
    """The list's first primitive colour (within its first commands) has an alpha below 255: the bottle's glass."""
    at = addr & 0xFFFFFF
    for k in range(at, min(at + 0x80, len(data) - 8), 8):
        if data[k] == 0xFA:
            return 0 < data[k + 7] < 255
        if data[k] == 0xDF:
            return False
    return False


def _outside(mesh, tris, hand):
    """tris without the connected parts lying wholly inside the hand's bounds grown by a third (a hand modelled again,
    a little larger, in the item's row, which subtract cannot match)."""
    if not hand.verts:
        return tris
    lo = [min(v[k] for v in hand.verts) for k in range(3)]
    hi = [max(v[k] for v in hand.verts) for k in range(3)]
    grow = [(hi[k] - lo[k]) / 3 for k in range(3)]
    lo = [lo[k] - grow[k] for k in range(3)]
    hi = [hi[k] + grow[k] for k in range(3)]
    corners = [{tuple(round(c) for c in mesh.verts[i][:3]) for i in t[1:]} for t in tris]
    group = list(range(len(tris)))

    def root(x):
        while group[x] != x:
            group[x] = group[group[x]]
            x = group[x]
        return x
    owner = {}
    for i, cs in enumerate(corners):
        for c in cs:
            if c in owner:
                group[root(i)] = root(owner[c])
            else:
                owner[c] = i
    parts = {}
    for i in range(len(tris)):
        parts.setdefault(root(i), []).append(i)
    keep = []
    for idx in parts.values():
        inside = all(lo[k] <= c[k] <= hi[k] for i in idx for c in corners[i] for k in range(3))
        if not inside:
            keep += idx
    return [tris[i] for i in sorted(keep)]


def _hand_rows(by, side):
    return [by['lh_closed'], by['lh_open']] if side == 'l' else [by['rh_closed'], by['rh_open']]


def item_pieces(rom, number, walk):
    """For Link's object number: [(name, mesh or None, triangles)] in ITEM_ORDER. walk(object number, segment 6
    address, env=None) gives that list's mesh (env: a fixed environment colour instead of the tunic's)."""
    rows, _tunics = player_tables(rom)
    found = getattr(rom, '_link_table', None)
    if not rows or not found:
        return []
    best, n = found
    table = [struct.unpack_from('>4I', rom.code, best + 16 * k) for k in range(n)]
    by = dict(zip(ROW_ORDER, rows))
    own = 1 if LINK_OBJECTS[number] == 'child' else 0
    obj_of = {0: 20, 1: 21}
    out = []
    for name, row, side, owner in HAND_ITEMS:
        if row >= n:
            out += [(name, None, []), (name + '_alone', None, [])]
            continue
        has = [owner in ('any', 'adult'), owner in ('any', 'child')]
        plain = [{h[a] for h in _hand_rows(by, side)} for a in (0, 1)]
        holds = [bool(has[a] and table[row][a] and table[row][a] not in plain[a]) for a in (0, 1)]
        if holds[own]:
            m = walk(obj_of[own], table[row][own])
            out += [(name, m, list(m.tris)), (name + '_alone', None, [])]
            continue
        out.append((name, None, []))
        if not holds[1 - own]:
            out.append((name + '_alone', None, []))
            continue
        a = 1 - own
        m = walk(obj_of[a], table[row][a])
        left = None
        for h in _hand_rows(by, side):
            hand = walk(obj_of[a], h[a])
            rest = _outside(m, subtract(m, hand), hand)
            if left is None or len(rest) < len(left):
                left = rest
        out.append((name + '_alone', m if left else None, left or []))
    words = _code_words(rom)
    gauntlets = words.get('gauntlets', GAUNTLETS)
    for name, whose, where in LIMB_ITEMS:
        age = own if whose == 'own' else (0 if whose == 'adult' else 1)
        env = (255, 255, 255, 255) if where == 'bottle' else None   # the colour of an empty bottle
        if name.startswith(('silver_', 'gold_')):
            env = tuple(gauntlets[0 if name.startswith('silver_') else 1]) + (255,)
        checked = None
        if where == 'bottle':
            addr = words.get('bottle', (0, 0))[age]
        elif where[0] == 'boots':
            addr = words['boots'][where[1]] if 'boots' in words else 0
        else:
            addr, checked = 0x06000000 | where[0], where[1]
        if not addr:
            out.append((name, None, []))
            continue
        m = walk(obj_of[age], addr, env)
        if checked is not None and len(m.tris) != checked:
            out.append((name, None, []))
            continue
        if name == 'deku_stick':
            m = _stick_in_hand(m)
        out.append((name, m, list(m.tris)))
    return out


def _stick_in_hand(mesh):
    """The Deku stick's list stands along +y from its base; the hand holds it with its base at STICK_IN_HAND and its
    length along the hand's +x: (x, y, z) goes to the base plus (y, x, -z), a half turn about x then a quarter turn
    about z."""
    import copy
    m = copy.copy(mesh)
    bx, by, bz = STICK_IN_HAND
    lit = {i for i, mat in enumerate(mesh.materials) if mat.geometry & 0x00020000}
    lit_verts = {i for t in mesh.tris if t[0] in lit for i in t[1:]}
    verts = []
    for i, v in enumerate(mesh.verts):
        x, y, z, s, t, r, g, b, a, *rest = v
        if i in lit_verts:   # the normal in the colour's bytes, signed, turned the same way
            r, g, b = g, r, (-(b - 256 if b > 127 else b)) & 255
        verts.append((bx + y, by + x, bz - z, s, t, r, g, b, a, *rest))
    m.verts = verts
    return m


# ---- where the ocarina meets his mouth
#
# runtime/n64pose.c poses his arms and head while he plays as his own animations hold the ocarina, then moves his hands
# so that the ocarina's mouthpiece is at his mouth. The mod keeps, after the standing frame (make_n64_mod.py, block
# 10): his mouth in the head limb's space, the mouthpiece of the Fairy Ocarina and of the Ocarina of Time in the right
# hand's space (each x, y, z), then the 67 values of the frame of his animations in which he plays.
HEAD_LIMB = 10
R_HAND_LIMB = 18
CHILD = 21


def mouth_point(mesh):
    """The middle of his mouth's front in the head limb's space: the vertices drawn with the mouth's texture (segment
    0x09), at their front (the head's +y). None when the mesh has none."""
    pts = [mesh.verts[i][:3] for t in face_tris(mesh, 0x09) for i in t[1:] if mesh.verts[i][9] == HEAD_LIMB]
    if not pts:
        return None
    return (round(sum(p[0] for p in pts) / len(pts)), max(p[1] for p in pts), round(sum(p[2] for p in pts) / len(pts)))


def mouthpiece(mesh, tris):
    """The end of the ocarina's mouthpiece in the hand's space: the hand row's vertex furthest along the hand's +x,
    where the ocarina leaves the fingers."""
    pts = [mesh.verts[i][:3] for t in tris for i in t[1:]]
    return tuple(max(pts, key=lambda p: (p[0], p[1]))) if pts else None


def _limb_world(skel, frame):
    """Each limb's rotation (row-major 3x3) and joint position in model space for one frame."""
    _, world = limb_axes(skel, frame)
    pos = []
    for i, lb in enumerate(skel.limbs):
        if lb.parent < 0:
            pos.append((0.0, 0.0, 0.0))
            continue
        r, p = world[lb.parent], pos[lb.parent]
        pos.append(tuple(p[k] + r[k][0] * lb.x + r[k][1] * lb.y + r[k][2] * lb.z for k in range(3)))
    return world, pos


def playing_frame(rom, skel, mouth, tip):
    """67 s16: the frame of his animations that brings the ocarina's mouthpiece (tip, in the right hand's space) nearest
    his mouth (in the head's) with his hands together, or None. The child's skeleton, mouth and Ocarina of Time decide
    it; the adult plays the same animation."""
    data, heads = animation_file(rom)
    if not data or len(skel.limbs) != LIMBS or not mouth or not tip:
        return None
    best = (1e30, None)
    for n, at in heads:
        for f in range(n):
            frame = struct.unpack_from('>67h', data, at + f * FRAME)
            world, pos = _limb_world(skel, frame)
            m = [pos[HEAD_LIMB][k] + sum(world[HEAD_LIMB][k][j] * mouth[j] for j in range(3)) for k in range(3)]
            t = [pos[R_HAND_LIMB][k] + sum(world[R_HAND_LIMB][k][j] * tip[j] for j in range(3)) for k in range(3)]
            score = math.dist(m, t) + 0.3 * math.dist(pos[15], pos[R_HAND_LIMB])
            if score < best[0]:
                best = (score, frame)
    return list(best[1]) if best[1] else None


def ocarina_points(mouth, pieces):
    """The nine values of the points kept after the standing frame, or [] when one is missing. pieces: item_pieces'
    list."""
    tips = {name: mouthpiece(mesh, tris) for name, mesh, tris in pieces if mesh is not None and tris}
    fairy, time = tips.get('ocarina_fairy'), tips.get('ocarina_time')
    if not mouth or not fairy or not time:
        return []
    return [int(v) for p in (mouth, fairy, time) for v in p]


def ocarina_frame(rom, walk):
    """The child's playing frame (playing_frame), found once and kept on rom; None when it cannot be found. walk as for
    item_pieces."""
    if hasattr(rom, '_ocarina_frame'):
        return rom._ocarina_frame
    rom._ocarina_frame = None
    d = rom.object(CHILD)
    skels = n64obj.find(d)[0] if d else []
    if skels:
        segs = n64gfx.Segments()
        segs.set(0x06, d)
        if rom.object(1):
            segs.set(0x04, rom.object(1))
        segments(segs, d)
        tips = {name: mouthpiece(m, tris) for name, m, tris in item_pieces(rom, CHILD, walk) if m is not None and tris}
        rom._ocarina_frame = playing_frame(rom, skels[0], mouth_point(n64obj.skeleton_mesh(segs, skels[0])),
                                           tips.get('ocarina_time'))
    return rom._ocarina_frame


# ---- his face
def face_tris(mesh, seg):
    """The triangles of mesh drawn with a texture of segment seg (0x08 the eyes, 0x09 the mouth)."""
    addr = {t.key: k[0] for k, t in mesh.textures.items()}
    mats = {i for i, m in enumerate(mesh.materials)
            if any(u and u.tex and addr.get(u.tex.key, 0) >> 24 == seg for u in m.tex)}
    return [t for t in mesh.tris if t[0] in mats]


def face_variants(segs, data, skel, walk):
    """[(mesh, triangles)]: the eyes drawn with each of the EYE_COUNT eye textures, then the mouth with each of the
    MOUTH_COUNT mouths. walk(segs) walks the skeleton under the segments. segs is left as segments() set it."""
    out = []
    for seg, start, step, count in ((0x08, EYES, EYE_STEP, EYE_COUNT), (0x09, MOUTH, MOUTH_STEP, MOUTH_COUNT)):
        for k in range(count):
            segs.set(seg, data[start + k * step:])
            mesh = walk(segs)
            out.append((mesh, face_tris(mesh, seg)))
        segs.set(seg, data[start:])
    return out
