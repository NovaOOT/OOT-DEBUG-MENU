"""Make the N64 Graphics mod (docs/n64.md) from the player's own N64 Ocarina of Time ROM: every scene's rooms and every
object's models, decoded (tools/n64rom.py, n64scene.py, n64obj.py, n64gfx.py) into the files the game draws them from
(runtime/n64gfx.c), written into the game's mods folder. Nothing of the ROM goes anywhere else.

    python tools/make_n64_mod.py --rom <the .z64> --game <game folder>        # into <game>/mods/N64 Graphics
    python tools/make_n64_mod.py --rom <the .z64> --out <folder> [--romfs <the game's romfs folder>]

With the game's RomFS (<game>/romfs, or --romfs) and Python's compression.zstd, the mod also gets Link's N64 voice:
romfs/Sound/Resource/Player_Child.bars.zs, Player_Adult.bars.zs and Stream/Vo_*.bwav (tools/n64voice.py), and the
characters' N64 models fitted to the remake's skeletons: n64/actors.bin (tools/n64actors.py gives its layout), and the
levels' N64 textures in place of the remake's: n64/world.bin and world.json (tools/n64world.py gives its layout).

The mod folder holds ootpc-feature.txt (runtime/mods.h MOD_FEATURE_N64), a readme, n64/manifest.json and
n64/scene_<nnn>.n64g and n64/object_<nnn>.n64g, little-endian:

    header   'N64G', u32 version (3), u32 kind (1 scene, 2 object), u32 number, then 12 sections, each u32 offset and
             u32 count, from the start of the file:
    0 vertices    24 bytes: f32 x, y, z (N64 units); s16 s, t (texels, 10.5 fixed); u8 r, g, b, a (colour, or the
                  normal and alpha when the material lights); u16 node (the limb or room the vertex moves with); u16 0
    1 (unused)
    2 materials   80 bytes, 20 u32: combine (high, low word), othermode high, low, geometry mode, primitive colour,
                  environment colour (r | g << 8 | b << 16 | a << 24), primitive LOD | texture on << 8, texture scale
                  s and t (f32), two tiles of 4 u32 (texel offset into section 3; w | h << 16; uls | ult << 16 in
                  10.2; cms | cmt << 2 | masks << 4 | maskt << 8 | shifts << 12 | shiftt << 16 | 1 << 20 when the
                  tile has a texture), fog colour, flags (1: the environment colour is the player's tunic, which the
                  game sets before it draws him)
    3 texels      u32 RGBA8 (r in the low byte)
    4 batches     16 bytes: u32 first vertex, u32 vertex count (3 per triangle), u32 material, u16 node, u16 flags
                  (1: see-through, drawn after the opaque batches). A skeleton's batches belong to the limb whose
                  display list drew them.
    5 nodes       16 bytes: s16 x, y, z (a limb's joint position relative to its parent); s16 parent (-1 none);
                  u16 first batch, u16 batch count; u16 kind (1 room, 2 limb, 3 model); u16 index (room number,
                  skeleton number, or a model's display list offset in the object / 8)
    6 lights      24 bytes per light setting: the scene's 0x16-byte row (n64scene.py Scene._lights) and two zero bytes
    7 collision vertices   8 bytes: s16 x, y, z, 0
    8 collision triangles  8 bytes: u16 a, b, c, polygon type
    9 bounds      the room's or model's bounds: 24 bytes per node, f32 min x, y, z, max x, y, z
    10 rest pose  s16 each: for Link's objects (tools/n64link.py), the 67 values of one frame of his animations in which
                  he stands (root position, 21 limb rotations, face), then when found nine more (n64link.ocarina_points:
                  his mouth, the two ocarinas' mouthpieces) and the 67 of the frame in which he plays the ocarina
                  (n64link.ocarina_frame); empty for the others
    11 player     u32 each, for Link's objects (tools/n64link.py player_tables): the Kokiri, Goron and Zora tunic
                  colours (r | g << 8 | b << 16 | 255 << 24), then for each row of n64link.ROW_ORDER the node this
                  object's near model of the row is (a limb's own list: that limb), then for each of n64link.EXTRAS
                  the node of that piece (gear the age's own lists lack), then for each of n64link.ITEM_ORDER the
                  node of that item piece (what he holds and wears, drawn in or beside a limb's place; files made
                  before them end after EXTRAS), then the nodes of his face (n64link.face_variants: the eyes with
                  each eye texture, then the mouth with each mouth, each drawn with the head limb's matrix; files
                  made before them end after the items); 0xFFFFFFFF none; empty for others

Standard library only (the voice: Python 3.14's compression.zstd).
"""
import argparse
import json
import struct
import sys
import time
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT))

import n64gfx  # noqa: E402
import n64link  # noqa: E402
import n64obj  # noqa: E402
import n64rom  # noqa: E402
import n64scene  # noqa: E402

VERSION = 3
FEATURE = 'n64-graphics'
MOD_NAME = 'N64 Graphics'
SECTIONS = 12


def rgba32(c):
    return c[0] | c[1] << 8 | c[2] << 16 | c[3] << 24


class Writer:
    """One .n64g file: meshes go in as batches of one node each, textures are shared within the file."""

    def __init__(self, kind, number):
        self.kind, self.number = kind, number
        self.verts = bytearray()
        self.nverts = 0
        self.materials = bytearray()
        self.nmaterials = 0
        self.texels = array('I')
        self.tex_at = {}     # Texture.key -> texel offset
        self.batches = bytearray()
        self.nbatches = 0
        self.nodes = []      # (x, y, z, parent, first, count, kind, index)
        self.bounds = []
        self.lights = bytearray()
        self.col_verts = bytearray()
        self.col_tris = bytearray()
        self.rest = []
        self.player = []
        self.missing = {}

    def _texture(self, tex):
        k = (tex.w, tex.h, tex.rgba)
        at = self.tex_at.get(k)
        if at is None:
            at = self.tex_at[k] = len(self.texels)
            self.texels.frombytes(tex.rgba)
        return at

    def _tile(self, tu):
        if tu is None or tu.tex is None:
            return (0, 0, 0, 0)
        at = self._texture(tu.tex)
        return (at, tu.tex.w | tu.tex.h << 16, tu.uls | tu.ult << 16,
                tu.cms | tu.cmt << 2 | tu.masks << 4 | tu.maskt << 8 | tu.shifts << 12 | tu.shiftt << 16 | 1 << 20)

    def _material(self, m):
        t0 = self._tile(m.tex[0])
        t1 = self._tile(m.tex[1])
        self.materials += struct.pack('<10I', m.combine >> 32, m.combine & 0xFFFFFFFF, m.othermode_h & 0xFFFFFFFF,
                                      m.othermode_l & 0xFFFFFFFF, m.geometry & 0xFFFFFFFF, rgba32(m.prim),
                                      rgba32(m.env), m.prim_lod | (m.tex_on & 0xFF) << 8, 0, 0)
        struct.pack_into('<ff', self.materials, len(self.materials) - 8, *m.tex_scale)
        self.materials += struct.pack('<8I', *t0, *t1)
        self.materials += struct.pack('<2I', rgba32(m.fog), 1 if m.tunic else 0)
        self.nmaterials += 1
        return self.nmaterials - 1

    def add_mesh(self, mesh, node, flags=0, node_of_vertex=None, tris=None):
        """Adds mesh's triangles (or those of tris) as batches (one per run of one material) for node; returns
        (first batch, count)."""
        if tris is None or tris is mesh.tris:
            self.note_missing(mesh)
        mat_map = {}
        first_batch = self.nbatches
        run_mat, run_first, run_n = None, 0, 0

        def flush():
            if run_n:
                self.batches.extend(struct.pack('<IIIHH', run_first, run_n, run_mat, node, flags))
                self.nbatches += 1
        for (mi, a, b, c) in (mesh.tris if tris is None else tris):
            mat = mat_map.get(mi)
            if mat is None:
                mat = mat_map[mi] = self._material(mesh.materials[mi])
            if mat != run_mat:
                flush()
                run_mat, run_first, run_n = mat, self.nverts, 0
            for i in (a, b, c):
                x, y, z, s, t, r, g, bl, al, bone = mesh.verts[i]
                nd = node_of_vertex(bone) if node_of_vertex else node
                si = max(-32768, min(32767, int(round(s * 32))))
                ti = max(-32768, min(32767, int(round(t * 32))))
                self.verts += struct.pack('<fffhhBBBBHH', x, y, z, si, ti, r, g, bl, al, nd, 0)
                self.nverts += 1
                run_n += 1
        flush()
        return first_batch, self.nbatches - first_batch

    def note_missing(self, mesh):
        for k, v in mesh.missing.items():
            self.missing[k] = self.missing.get(k, 0) + v

    def node(self, x, y, z, parent, first, count, kind, index, bounds):
        self.nodes.append((x, y, z, parent, first, count, kind, index))
        self.bounds.append(bounds)
        return len(self.nodes) - 1

    def bytes(self):
        secs = [(self.verts, self.nverts), (b'', 0), (self.materials, self.nmaterials),
                (self.texels.tobytes(), len(self.texels)), (self.batches, self.nbatches),
                (b''.join(struct.pack('<hhhhHHHH', *n) for n in self.nodes), len(self.nodes)),
                (self.lights, len(self.lights) // 24), (self.col_verts, len(self.col_verts) // 8),
                (self.col_tris, len(self.col_tris) // 8),
                (b''.join(struct.pack('<6f', *b) for b in self.bounds), len(self.bounds)),
                (struct.pack('<%dh' % len(self.rest), *self.rest), len(self.rest)),
                (struct.pack('<%dI' % len(self.player), *self.player), len(self.player))]
        head = 16 + 8 * SECTIONS
        out = bytearray(head)
        struct.pack_into('<4sIII', out, 0, b'N64G', VERSION, self.kind, self.number)
        for k, (data, count) in enumerate(secs):
            while len(out) % 16:
                out.append(0)
            struct.pack_into('<II', out, 16 + 8 * k, len(out), count)
            out += data
        return bytes(out)


def mesh_bounds(meshes, offset=None):
    lo = [1e30] * 3
    hi = [-1e30] * 3
    for m in meshes:
        for v in m.verts:
            p = v[:3] if offset is None else tuple(v[k] + offset[v[9]][k] for k in range(3))
            for k in range(3):
                lo[k] = min(lo[k], p[k])
                hi[k] = max(hi[k], p[k])
    return tuple(lo + hi) if lo[0] <= hi[0] else (0.0,) * 6


def export_scene(rom, number):
    sc = n64scene.Scene(rom, number)
    w = Writer(1, number)
    rooms = []
    for room in sc.rooms:
        opa, xlu = sc.room_mesh(room)
        node = len(w.nodes)
        f0, n0 = w.add_mesh(opa, node, 0)
        f1, n1 = w.add_mesh(xlu, node, 1)
        w.node(0, 0, 0, -1, f0, n0 + n1, 1, room.number, mesh_bounds([opa, xlu]))
        rooms.append({'room': room.number, 'mesh_type': room.mesh_type, 'triangles': len(opa.tris) + len(xlu.tris),
                      'objects': room.objects})
    for L in sc.lights:
        w.lights += bytes(L['ambient']) + struct.pack('bbb', *L['dir1']) + bytes(L['col1']) + \
            struct.pack('bbb', *L['dir2']) + bytes(L['col2']) + bytes(L['fog']) + \
            struct.pack('>HH', L['fog_near'] | L['blend'] << 10, L['far']) + b'\0\0'
    if sc.collision:
        for v in sc.collision['verts']:
            w.col_verts += struct.pack('<hhhh', *v, 0)
        for t, a, b, c in sc.collision['polys']:
            w.col_tris += struct.pack('<HHHH', a, b, c, t & 0xFFFF)
    info = {'name': n64rom.SCENES[number] if number < len(n64rom.SCENES) else f'scene {number}',
            'keep': sc.keep, 'rooms': rooms, 'lights': len(sc.lights),
            'collision_triangles': len(sc.collision['polys']) if sc.collision else 0,
            'bounds': sc.collision and [sc.collision['min'], sc.collision['max']]}
    return w, info


# Texture sets an actor points segments at as it draws (segment -> offset in the object): the Tektite's red and blue
# bodies (segments 8, 9 and 0x0A: its body, legs and eye), the open eyes and the mouth of the characters that blink.
# 'env' is the environment colour the actor sets before it draws (r, g, b, a). The first set is the actor's usual one.
def colour_dl(r, g, b, a=255):
    """A display list that sets the environment colour and returns: what an actor points a segment at to tint a
    model (the Poe sisters' cloaks)."""
    return bytes([0xFB, 0, 0, 0, r, g, b, a, 0xDF, 0, 0, 0, 0, 0, 0, 0])


OBJECT_VARIANTS = {
    22: [{0x08: 0x1B00, 0x09: 0x1F00, 0x0A: 0x2100}, {0x08: 0x1300, 0x09: 0x1700, 0x0A: 0x1900}],
    188: [{0x08: 0x2F48, 0x09: 0x2F48, 0x0A: 0x3348}],   # Saria: her eyes (one texture, both sides), her mouth
    195: [{0x08: 0x15F8, 'env': (70, 190, 60, 255)}],    # the Kokiri girl: her eyes, her dress's colour
    196: [{'env': (0, 130, 70, 255)}],                   # the Kokiri boy: his tunic's colour
    251: [{0x08: 0x5930}],                               # Mido: his eyes
    # the people of Hyrule: open eyes (segment 8, and 9 where each eye has its own), mouths, Talon's and Ingo's noses
    29: [{0x08: 0x8848, 0x09: 0x8848, 0x0A: 0x7608}],    # Zelda as a child
    401: [{0x08: 0x2AF0, 0x09: 0x2AF0, 0x0A: 0x46F0}],   # Zelda grown up
    387: [{0x08: 0x0300}],                               # Wolfos (its eyes)
    350: [{0x08: 0x07E0}],                               # a man cursed into a Skulltula (his eyes)
    224: [{0x08: 0x1B18, 0x09: 0x2B18}],                 # Malon as a child
    208: [{0x08: 0x2570, 0x09: 0x3570}],                 # Malon grown up
    136: [{0x08: 0x6EC0, 0x09: 0x7AC0}],                 # Talon
    192: [{0x08: 0x3590, 0x09: 0x3190}],                 # Ingo
    137: [{0x08: 0x3B40}],                               # Dampe
    135: [{0x08: 0x7D50}],                               # Impa
    138: [{0x08: 0x56E0}],                               # Sheik
    254: [{0x08: 0x3E40}],                               # a Zora
    163: [{0x08: 0xF238, 0x09: 0xFA38}],                 # Ruto as a child
    202: [{0x08: 0x22E0}],                               # Ruto grown up
    255: [{0x08: 0x1470}],                               # King Zora
    179: [{0x08: 0xB428, 0x09: 0xB428}],                 # Nabooru
    278: [{0x08: 0x0708}],                               # a Gerudo
    361: [{0x08: 0x5FE8}],                               # a Gerudo with two swords
    157: [{0x08: 0x8680, 0x09: 0x9280, 0x0A: 0x08C0}],   # Darunia: eyes, mouth, nose
    305: [{0x08: 0x89A8}],                               # Kaepora Gaebora
    167: [{0x08: 0x3960}],                               # Rauru
    201: [{0x08: 0xCE80, 0x09: 0xDE80}],                 # a Goron: eyes, mouth
    341: [{0x08: 0x0970}],                               # Biggoron (his tired eyes)
    64: [{0x08: 0x7698}],                                # Lord Jabu-Jabu
    10: [{0x08: 0x17930, 0x0A: 0x19930}],                # the Great Fairy: eyes, mouth
    230: [{0x08: 0x59A0, 0x09: 0x59A0}],                 # a frog
    370: [{0x08: 0x27D0}],                               # a Lost Woods stage scrub (its eyes)
    369: [{0x08: 0x30A0}],                               # the Business Scrub (its eyes)
    # the Poe sisters, one model: segment 9 tints the cloak, Meg purple, Joelle red, Beth blue, Amy green
    # the Iron Knuckle's plates (segments 8, 9 and 10 set their colours): silver, and Nabooru's dark plates with gold
    262: [{0x08: colour_dl(200, 200, 215), 0x09: colour_dl(200, 200, 215), 0x0A: colour_dl(200, 200, 215)},
          {0x08: colour_dl(60, 55, 70), 0x09: colour_dl(220, 170, 60), 0x0A: colour_dl(60, 55, 70)}],
    158: [{0x08: colour_dl(255, 60, 0), 0x09: colour_dl(255, 200, 0)}],   # the Flare Dancer's flames
    153: [{0x09: colour_dl(150, 70, 255)}, {0x09: colour_dl(255, 50, 40)}, {0x09: colour_dl(40, 90, 255)},
          {0x09: colour_dl(60, 220, 70)}],
}


def export_object(rom, number):
    d = rom.object(number)
    if not d:
        return None, None
    skels, roots = n64obj.find(d)
    if not skels and not roots:
        return None, None
    segs = n64gfx.Segments()
    segs.set(0x06, d)
    keep = rom.object(1)
    if keep:
        segs.set(0x04, keep)
    w = Writer(2, number)
    env = None
    link = number in n64link.LINK_OBJECTS and skels
    mouth = None   # his mouth in the head limb's space (n64link.mouth_point)
    face_nodes = []   # the nodes of his eyes, one per eye texture, then of his mouth, one per mouth
    if link:
        n64link.segments(segs, d)
        w.rest = n64link.rest_pose(rom, skels[0]) or []
        env = n64link.KOKIRI_TUNIC
    info = {'skeletons': [], 'models': []}
    # an object whose actor points segments at one of several texture sets (OBJECT_VARIANTS) gets its first skeleton
    # once per set: the first set with the object's skeletons, the others after them (with a 'variant' number)
    variants = OBJECT_VARIANTS.get(number, [None])
    jobs = [(si, s, variants[0] if si == 0 else None, 0) for si, s in enumerate(skels)]
    jobs += [(len(skels) + k - 1, skels[0], variants[k], k) for k in range(1, len(variants))] if skels else []
    for si, s, chosen, variant in jobs:
        colour = None   # the environment colour the actor sets before it draws (a Kokiri's tunic)
        for seg, off in (chosen or {}).items():
            if seg == 'env':
                colour = off
            else:
                segs.set(seg, off if isinstance(off, bytes) else d[off:])
        base = len(w.nodes)
        world = []
        for i, lb in enumerate(s.limbs):
            p = world[lb.parent] if lb.parent >= 0 else (0, 0, 0)
            world.append((p[0] + lb.x, p[1] + lb.y, p[2] + lb.z) if lb.parent >= 0 else (0, 0, 0))
        mesh = n64obj.skeleton_mesh(segs, s, env=env or colour)
        if colour and not env:   # a fixed colour, not the player's tunic
            for m in mesh.materials:
                m.tunic = False
        mesh = texgen_uv(mesh)   # a reflecting blade such as a Lizalfos's sword
        if link and si == 0:
            mouth = n64link.mouth_point(mesh)
        parts = {}
        w.note_missing(mesh)
        # his eyes and mouth are drawn apart, once per texture (n64link.face_variants), the limbs without them
        face = set(map(id, n64link.face_tris(mesh, 0x08) + n64link.face_tris(mesh, 0x09))) if link and si == 0 else set()
        for limb, a, b in mesh.parts:   # each limb's batches carry its node, so a limb can be left out
            parts[limb] = w.add_mesh(mesh, base + limb, 0, node_of_vertex=lambda b, base=base: base + b,
                                     tris=[t for t in mesh.tris[a:b] if id(t) not in face])
        for i, lb in enumerate(s.limbs):
            first, count = parts.get(i, (0, 0))
            w.node(lb.x, lb.y, lb.z, base + lb.parent if lb.parent >= 0 else -1,
                   first, count, 2, si, mesh_bounds([mesh], world) if i == 0 else (0.0,) * 6)
        if face:
            for vmesh, vtris in n64link.face_variants(segs, d, s, lambda sg, s=s: n64obj.skeleton_mesh(sg, s, env=env)):
                first, count = w.add_mesh(vmesh, len(w.nodes), 0, node_of_vertex=lambda b, base=base: base + b,
                                          tris=vtris) if vtris else (w.nbatches, 0)
                face_nodes.append(w.node(0, 0, 0, -1, first, count, 3, 0, (0.0,) * 6))
        info['skeletons'].append({'offset': s.offset, 'limbs': len(s.limbs), 'flex': s.flex,
                                  'triangles': len(mesh.tris), 'first_node': base, **({'variant': variant} if variant else {})})
    model_node = {}
    rows, tunics = n64link.player_tables(rom) if link else (None, None)
    extra = []   # a table's list that something else also calls is no root, but is drawn alone too
    if rows:
        age = 1 if n64link.LINK_OBJECTS[number] == 'child' else 0
        limb_dls = set(lb.dl for lb in skels[0].limbs if lb.dl)
        extra = sorted(set(r[age] for r in rows if r[age]) - set(roots) - limb_dls)
    for addr in list(roots) + extra:
        if link:   # what his hands hold and his sheath: drawn in a limb's place, under the tunic's colour
            walker = n64gfx.Walker(segs)
            walker.env, walker.env_tunic = tuple(env), True
            walker.walk(addr)
            mesh = walker.mesh
        else:
            mesh = texgen_uv(n64gfx.walk(segs, [addr]))
        if not mesh.tris:
            continue
        node = len(w.nodes)
        first, count = w.add_mesh(mesh, node, 0)
        w.node(0, 0, 0, -1, first, count, 3, (addr & 0xFFFFFF) // 8, mesh_bounds([mesh]))
        model_node[addr & 0xFFFFFF] = node
        info['models'].append({'offset': addr & 0xFFFFFF, 'triangles': len(mesh.tris), 'node': node})
    if rows:
        base = info['skeletons'][0]['first_node']
        limb_node = {lb.dl & 0xFFFFFF: base + i for i, lb in enumerate(skels[0].limbs) if lb.dl}
        w.player = [rgba32(tuple(t) + (255,)) for t in (tunics + [tunics[0]] * 3)[:3]]
        for row in rows:
            at = row[age] & 0xFFFFFF
            w.player.append(limb_node.get(at, model_node.get(at, 0xFFFFFFFF)) if row[age] else 0xFFFFFFFF)
        info['player'] = dict(zip(n64link.ROW_ORDER, w.player[3:]))

        def walk(obj, addr, fixed=None):   # a list of either Link object, under the tunic's colour or a fixed one
            od = rom.object(obj)
            osegs = n64gfx.Segments()
            osegs.set(0x06, od)
            if keep:
                osegs.set(0x04, keep)
            n64link.segments(osegs, od)
            walker = n64gfx.Walker(osegs)
            walker.env, walker.env_tunic = (tuple(fixed), False) if fixed else (tuple(env), True)
            walker.walk(addr)
            return walker.mesh
        for name, mesh, tris, offset in n64link.extra_pieces(rom, number, walk):
            if mesh is None:
                w.player.append(0xFFFFFFFF)
                continue
            node = len(w.nodes)
            first, count = w.add_mesh(mesh, node, 0, tris=tris)
            w.node(0, 0, 0, -1, first, count, 3, offset // 8, mesh_bounds([mesh]))
            w.player.append(node)
            info['player'][name] = node
        pieces = n64link.item_pieces(rom, number, walk)
        points = n64link.ocarina_points(mouth, pieces) if len(w.rest) == 67 else []
        playing = n64link.ocarina_frame(rom, walk) if points else None
        if playing:
            w.rest += points + playing
        if len(pieces) == len(n64link.ITEM_ORDER):   # all of them or none: runtime/n64pose.c reads them by place
            for name, mesh, tris in pieces:
                if mesh is None or not tris:
                    w.player.append(0xFFFFFFFF)
                    continue
                node = len(w.nodes)
                mesh = texgen_uv(mesh)
                opa = [t for t in tris if not see_through(mesh.materials[t[0]])]
                xlu = [t for t in tris if see_through(mesh.materials[t[0]])]
                first, count = w.add_mesh(mesh, node, 0, tris=opa) if opa else (w.nbatches, 0)
                if xlu:
                    f1, n1 = w.add_mesh(mesh, node, 1, tris=xlu)
                    first, count = (f1, n1) if not opa else (first, count + n1)
                w.node(0, 0, 0, -1, first, count, 3, 0, mesh_bounds([mesh]))
                w.player.append(node)
                info['player'][name] = node
            if len(face_nodes) == n64link.EYE_COUNT + n64link.MOUTH_COUNT:
                w.player += face_nodes
                info['player']['face'] = face_nodes
    if not w.nbatches:
        return None, None
    return w, info


def see_through(mat):
    """The material blends over what is behind it without writing depth (no Z_UPD in its render mode): drawn in the
    second pass, as the rooms' see-through meshes are."""
    return not mat.othermode_l & 0x20


def texgen_uv(mesh):
    """A copy of mesh in which the vertices of materials that make their texture coordinates from the normal
    (G_TEXTURE_GEN, a reflection such as a bottle's glass) carry coordinates made from their object-space normal: the
    N64 makes them from the normal as seen from the camera each frame, a fixed reflection is close enough."""
    gen = {i for i, m in enumerate(mesh.materials) if m.geometry & n64gfx.G_TEXTURE_GEN and m.geometry & n64gfx.G_LIGHTING}
    if not gen:
        return mesh
    import copy
    out = copy.copy(mesh)
    out.verts = list(mesh.verts)
    signed = lambda v: v - 256 if v > 127 else v
    for mi, a, b, c in mesh.tris:
        if mi not in gen:
            continue
        for i in (a, b, c):
            x, y, z, s, t, r, g, bl, al, bone = mesh.verts[i]
            out.verts[i] = (x, y, z, (signed(r) / 128.0 * 0.5 + 0.5) * 1000.0, (signed(g) / 128.0 * 0.5 + 0.5) * 1000.0,
                            r, g, bl, al, bone)
    return out


def make(rom_path, out, log=print, romfs=None):
    t0 = time.time()
    rom = n64rom.Rom(rom_path)
    log(n64rom.describe(rom))
    out = Path(out)
    data = out / 'n64'
    data.mkdir(parents=True, exist_ok=True)
    (out / 'ootpc-feature.txt').write_text(FEATURE + '\n', encoding='utf-8')
    (out / 'README.txt').write_text(
        'N64 Graphics for OOT-PC\n\n'
        'Made by tools/make_n64_mod.py from your own N64 Ocarina of Time ROM: the game draws the player as the N64\n'
        'version\'s Link, moving with the remake\'s animations (an upscaler must be on). Switch the mod on or off on\n'
        'the Mods screen or the menu\'s Mods tab; it applies at the next start. Do not share this folder: it holds\n'
        'the ROM\'s graphics.\n',
        encoding='utf-8')
    manifest = {'rom': {'title': rom.title, 'id': rom.game_id, 'revision': rom.revision}, 'version': VERSION,
                'scenes': {}, 'objects': {}, 'missing': {}}
    for number in range(len(rom.scene_table)):
        try:
            w, info = export_scene(rom, number)
        except Exception as e:   # one damaged scene must not stop the others
            log(f'scene {number}: {e}')
            continue
        (data / f'scene_{number:03d}.n64g').write_bytes(w.bytes())
        info['file'] = f'scene_{number:03d}.n64g'
        manifest['scenes'][number] = info
        for k, v in w.missing.items():
            manifest['missing'][k] = manifest['missing'].get(k, 0) + v
    log(f'{len(manifest["scenes"])} scenes ({time.time() - t0:.0f} s)')
    for number in range(1, len(rom.object_table)):
        try:
            w, info = export_object(rom, number)
        except Exception as e:
            log(f'object {number}: {e}')
            continue
        if not w:
            continue
        (data / f'object_{number:03d}.n64g').write_bytes(w.bytes())
        info['file'] = f'object_{number:03d}.n64g'
        manifest['objects'][number] = info
        for k, v in w.missing.items():
            manifest['missing'][k] = manifest['missing'].get(k, 0) + v
    log(f'{len(manifest["objects"])} objects ({time.time() - t0:.0f} s)')
    manifest['voice'] = 0
    manifest['actors'] = 0
    manifest['world'] = 0
    if romfs and (Path(romfs) / 'index.json').is_file():
        try:   # the characters' N64 models: the remake's skeletons come from the game's own files
            import n64actors
            import n64voice
            first = {int(k): [s['first_node'] for s in v['skeletons']] for k, v in manifest['objects'].items()}
            models = {int(k): {m['offset']: m['node'] for m in v['models']}
                      for k, v in manifest['objects'].items()}
            blob = n64actors.make(rom, n64voice.RomFS(romfs), first, log, models)
            (data / 'actors.bin').write_bytes(blob)
            manifest['actors'] = struct.unpack_from('<I', blob, 8)[0]
            log(f'{manifest["actors"]} characters ({time.time() - t0:.0f} s)')
        except Exception as e:   # Link stands without them
            log(f'characters: {type(e).__name__}: {e}')
        try:
            import n64voice
            manifest['voice'] = n64voice.make(rom, romfs, out, log)
            log(f'{manifest["voice"]} voice clips ({time.time() - t0:.0f} s)')
        except Exception as e:   # the graphics stand without the voice
            log(f'voice: {type(e).__name__}: {e}')
        try:   # the levels' textures: reads the scene files written above
            import n64voice
            import n64world
            blob, report = n64world.make(n64voice.RomFS(romfs), out, log)
            (data / 'world.bin').write_bytes(blob)
            (data / 'world.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
            manifest['world'] = struct.unpack_from('<I', blob, 16)[0]
            log(f'{manifest["world"]} level textures ({time.time() - t0:.0f} s)')
        except Exception as e:   # the levels keep the remake's textures
            log(f'levels: {type(e).__name__}: {e}')
    else:
        log("voice: no RomFS given, so Link keeps the remake's voice")
    (data / 'manifest.json').write_text(json.dumps(manifest, indent=1), encoding='utf-8')
    return manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--rom', type=Path, required=True)
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument('--game', type=Path, help="put the mod into this game folder's mods folder")
    where.add_argument('--out', type=Path, help='write the mod folder here')
    ap.add_argument('--romfs', type=Path, help="the game's unpacked RomFS (romfs.bin and index.json); default <game>/romfs")
    a = ap.parse_args()
    if a.game:
        from launcher import mods
        out = mods.ensure_mods_dir(a.game) / MOD_NAME
    else:
        out = a.out
    m = make(a.rom, out, romfs=a.romfs or (a.game / 'romfs' if a.game else None))
    print(f'{out}: {len(m["scenes"])} scenes, {len(m["objects"])} objects')


if __name__ == '__main__':
    main()
