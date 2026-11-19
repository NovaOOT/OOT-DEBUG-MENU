"""The N64 Graphics mod's levels (N64_WORLD): the remake's world surfaces drawn with the N64 version's textures in place
of the remake's. tools/make_n64_mod.py runs it after the scenes are written; it writes n64/world.bin, which
runtime/n64world.c reads and runtime/vk_n64world.c applies.

    python tools/n64world.py --mod "<game>/mods/N64 Graphics" --romfs "<game>/romfs" [--area KokiriForest]
                             [--keep-normals]

The N64 rooms do not fit the remake's reshaped levels (docs/n64.md), so no N64 mesh is drawn. Each remake world
texture is drawn with an N64 texture of the area it stands in instead:

1. The remake's textures of an area. The area's placement files (Banc/<area>/<banc>_Static.bcett.byml.zs and
   _Dynamic) name the actors it places; every model file named after one (Model/<project>.<actor>.bfres.zs) names
   its materials' textures (Tex/<texture>.bntx.zs). Each texture's name says what it is: Rock_*, Sand_*, Wood_*,
   *Moss*, *Thatch* ... (category_of). Characters, items, cloth and props whose name says nothing of the kind stay
   the remake's. The terrain's material array (Tex/MaterialAlb.bntx.zs) is one texture with a layer per ground
   material; its layers are told apart at run time by their colour (runtime/vk_n64world.c layer_colour).
2. The N64 textures of the area's scenes (the mod's n64/scene_<nnn>.n64g, written by make_n64_mod.py): for each
   texture the colour it is drawn with (the combiner with the material's colours, the vertices' tint), where it lies
   (floor, wall, ceiling, see-through) and how large its texels are. One texture is chosen per category (pick):
   grass is the green texture of the most floor, rock the grey or brown one of the most wall, and so on, a missing
   category falling back to a near one (FALLBACK).
3. A remake texture is known at run time by its first 4096 bytes as the game lays them out (block linear, as the
   .bntx file holds them) and its size: key() is FNV-1a 64 over u32 width, u32 height and those bytes. The runtime
   takes the same hash of a texture's storage at its upload.

world.bin, little-endian:

    header   'N64W', u32 version (1), u32 area count, u32 N64 texture count, u32 entry count, u32 texel count,
             u32 0, u32 0
    area     48 bytes: the banc's name (32 bytes, zero padded: Sequence_CurrentBanc, e.g. KokiriForest_Child), then
             per category (CATEGORIES) u16 the N64 texture drawn for it, 0xFFFF none
    texture  16 bytes: u32 first texel, u16 width, u16 height, u16 flags (1: its alpha is a cut-out the remake
             texture keeps), u8 metres one repeat covers in the N64 room x 8 (0: not known), u8 0, u32 0
    entry    16 bytes, ascending by key: u64 key, u8 category, u8 kind (1 colour, 2 normal map: drawn flat while the
             colour of its material is the N64 one, 3 colour of a texture array, the category per layer by its
             colour), u8 metres one repeat of the remake texture covers x 8 (0: not known), u8 0, u32 0
    texels   u32 RGBA8 (r in the low byte), the colour the N64 game draws each texel with

Needs Python's compression.zstd (3.14) for the RomFS, as tools/n64voice.py.
"""
import argparse
import colorsys
import json
import math
import re
import struct
import sys
from pathlib import Path

VERSION = 1
CATEGORIES = ('grass', 'dirt', 'rock', 'wood', 'leaf', 'water', 'roof', 'path')
CAT = {c: i for i, c in enumerate(CATEGORIES)}
FALLBACK = {'grass': ('dirt',), 'dirt': ('path', 'grass'), 'rock': ('wood', 'dirt'), 'wood': ('rock', 'dirt'),
            'leaf': ('grass',), 'water': (), 'roof': ('wood', 'rock'), 'path': ('dirt', 'rock')}
KIND_COLOUR, KIND_NORMAL, KIND_ARRAY = 1, 2, 3
KEY_BYTES = 4096
NONE = 0xFFFF
UNITS_PER_METRE = 35.0   # N64 units per metre of the remake's world, as measured against its rooms
REMAKE_TEXELS_PER_METRE = 256.0   # assumed of a remake texture: one repeat of a 1024 texture covers 4 m

# The remake's areas (Banc/<folder>/<banc>) and the N64 scenes whose textures stand in for theirs (tools/n64rom.py
# SCENES numbers). The first scene is the area itself; the others are its houses, shops and bosses, which the remake
# keeps in the same area.
AREAS = {
    'KokiriForest': (['KokiriForest_Child', 'KokiriForest_Adult'], [85, 40, 41, 45, 52, 38, 39]),
    'LostWood': (['LostWood_Child', 'LostWood_Adult'], [91]),
    'SacredForestMeadow': (['SacredForestMeadow_Child', 'SacredForestMeadow_Adult'], [86]),
    'HyruleField': (['HyruleField_Child', 'HyruleField_Adult'], [81, 99, 87, 56, 76, 73]),
    'Market': (['Market_Child', 'Market_Adult'], [32, 34, 27, 29, 30, 35, 37, 44, 49, 50, 51, 53, 66, 75, 16, 67]),
    'HyruleCastle': (['HyruleCastle'], [95, 69, 74, 77]),
    'KakarikoVillage': (['KakarikoVillage_Child', 'KakarikoVillage_Adult', 'KakarikoVillage_Adult_Alternative'],
                        [82, 55, 48, 78, 80, 42, 54, 72]),
    'Graveyard': (['Graveyard_Child', 'Graveyard_Adult'], [83, 58, 63, 64, 65]),
    'DeathMountainTrail': (['DeathMountainTrail_Child', 'DeathMountainTrail_Adult'], [96]),
    'DeathMountainCrater': (['DeathMountainCrater_Child', 'DeathMountainCrater_Adult'], [97]),
    'GoronCity': (['GoronCity_Child', 'GoronCity_Adult'], [98, 46]),
    'ZoraRiver': (['ZoraRiver_Child', 'ZoraRiver_Adult'], [84]),
    'ZoraDomain': (['ZoraDomain_Child', 'ZoraDomain_Adult', 'ZoraDomain_Adult_Alternative'], [88, 47]),
    'ZoraFountain': (['ZoraFountain_Child', 'ZoraFountain_Adult'], [89]),
    'GerudoValley': (['GerudoValley_Child', 'GerudoValley_Adult'], [90, 57]),
    'GerudoFortress': (['GerudoFortress_Adult'], [93, 12]),
    'HountedWasteland': (['HountedWasteland_Child', 'HountedWasteland_Adult'], [94]),
    'DesertColossus': (['DesertColossus_Child', 'DesertColossus_Adult'], [92]),
    'InsideTheDekuTree': (['InsideTheDekuTree'], [0, 17]),
    'DodongoCavern': (['DodongoCavern'], [1, 18]),
    'InsideJabuJabuBelly': (['InsideJabuJabuBelly'], [2, 19]),
    'ForestTemple': (['ForestTemple'], [3, 20]),
    'FireTemple': (['FireTemple'], [4, 21]),
    'WaterTemple': (['WaterTemple'], [5, 22]),
    'SpiritTemple': (['SpiritTemple'], [6, 23]),
    'ShadowTemple': (['ShadowTemple'], [7, 24]),
    'BottomOfTheWell': (['BottomOfTheWell'], [8]),
    'IceCavern': (['IceCavern'], [9]),
    'GerudoTrainingGround': (['GerudoTrainingGround'], [11]),
    'GanonCastle': (['GanonCastle', 'CrumblingGanonCastle', 'FinalBattleField'], [100, 26, 79]),
    'InsideGanonCastle': (['InsideGanonCastle'], [13, 10, 25]),
    'SageRoom': (['SageRoom'], [68]),
}

# What a remake texture's name says it is (category_of). Names that start with one of SKIP are characters, items,
# cloth, food and the like: they stay the remake's. Then a word anywhere in the name (OVERRIDE), then how the name
# starts (PREFIX), then a word anywhere (WORD).
SKIP = ('mt_', 'cmntex_chara', 'cmntex_map_detailmap', 'chara_', 'cloth_', 'food_', 'etc_', 'armor_', 'animal_',
        'npc_', 'enemy_', 'weapon_', 'item_', 'obj_', 'body_', 'eye', 'face', 'hair', 'rbake_', 'shape_', 'cloud_',
        'gradation', 'outside_rakugaki', 'kokiribasket', 'clay_')
OVERRIDE = (('waterside', None), ('waterfall', None), ('thatch', 'roof'), ('roof', 'roof'), ('pineground', 'dirt'),
            ('leaf', None), ('ivy', None), ('fern', None), ('lycopodium', None), ('tillandsia', None),
            ('moss', 'grass'), ('grass', 'grass'), ('turf', 'grass'), ('lawn', 'grass'), ('paving', 'path'),
            ('water', 'water'))
PREFIX = (('rock_', 'rock'), ('stone_', 'rock'), ('brick_', 'rock'), ('metal_', None), ('sand_', 'dirt'),
          ('soil_', 'dirt'), ('wood_', 'wood'), ('tree_', 'wood'), ('bark_', 'wood'), ('plant_', None))
WORD = (('cliff', 'rock'), ('rock', 'rock'), ('stone', 'rock'), ('cave', 'rock'), ('brick', 'rock'),
        ('wall', 'rock'), ('floor', 'path'), ('tile', 'path'), ('stair', 'path'), ('soil', 'dirt'),
        ('ground', 'dirt'), ('sand', 'dirt'), ('mud', 'dirt'), ('dirt', 'dirt'), ('wood', 'wood'),
        ('bark', 'wood'), ('log', 'wood'), ('plank', 'wood'), ('board', 'wood'), ('branch', 'wood'),
        ('root', 'wood'), ('trunk', 'wood'), ('stump', 'wood'), ('bridge', 'wood'), ('fence', 'wood'))


def category_of(name):
    """The category of a remake colour texture (its name without the _Alb), or None: it stays the remake's."""
    n = name.lower()
    if n.startswith(SKIP):
        return None
    for word, cat in OVERRIDE:
        if word in n:
            return cat
    for pre, cat in PREFIX:
        if n.startswith(pre):
            if cat:
                return cat
            break
    for word, cat in WORD:
        if word in n:
            return cat
    return None


def fnv64(data, h=0xcbf29ce484222325):
    for b in data:
        h = ((h ^ b) * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h


def key(w, h, first):
    """The run time's name of a texture (runtime/n64world.c n64world_key): FNV-1a 64 over u32 width, u32 height and
    the first KEY_BYTES of its storage."""
    return fnv64(first, fnv64(struct.pack('<II', w, h)))


def bntx_textures(d):
    """The textures of a BNTX file: (name, width, height, layers, size, the first KEY_BYTES of the storage). The
    platform block at 0x20 counts the textures (+4) and points at their BRTI records (+8); a BRTI holds the width,
    height, depth and layers (+0x24), the storage size (+0x50), the name (+0x60) and the level pointers (+0x70), the
    first of which is where the storage starts (runtime/menu_assets.c reads the same records)."""
    if d[:4] != b'BNTX':
        return []
    count, arr = struct.unpack_from('<IQ', d, 0x24)
    out = []
    for i in range(min(count, 65536)):
        p = struct.unpack_from('<Q', d, arr + 8 * i)[0]
        if p + 0x78 > len(d) or d[p:p + 4] != b'BRTI':
            continue
        w, h, _depth, layers = struct.unpack_from('<IIII', d, p + 0x24)
        size = struct.unpack_from('<I', d, p + 0x50)[0]
        no, = struct.unpack_from('<Q', d, p + 0x60)
        name = d[no + 2:no + 2 + struct.unpack_from('<H', d, no)[0]].decode('utf-8', 'replace')
        levels, = struct.unpack_from('<Q', d, p + 0x70)
        start, = struct.unpack_from('<Q', d, levels)
        out.append((name, w, h, layers, size, d[start:start + KEY_BYTES]))
    return out


def byml_strings(d):
    """Every string of a BYML file's string table (the values of its placements: actor names among them)."""
    if d[:2] != b'YB':
        return []
    st = struct.unpack_from('<I', d, 8)[0]
    if not st:
        return []
    n = struct.unpack_from('<I', d, st)[0] >> 8
    offs = struct.unpack_from('<%dI' % n, d, st + 4)
    return [d[st + o:d.index(b'\0', st + o)].decode('utf-8', 'replace') for o in offs]


# ---- the remake's side ----

class Remake:
    def __init__(self, fs, log=print):
        self.fs, self.log = fs, log
        self.tex = {p[4:-8] for p in fs.files if p.startswith('Tex/') and p.endswith('.bntx.zs')}
        self.models = {}   # actor (the model file's second name) -> model files
        for p in fs.files:
            if p.startswith('Model/') and p.endswith('.bfres.zs'):
                parts = p[6:-9].split('.')
                if len(parts) == 2:
                    self.models.setdefault(parts[1], []).append(p)
        self._model_tex = {}

    def model_textures(self, path):
        """The texture names a model file names (its strings that are a Tex file's name)."""
        got = self._model_tex.get(path)
        if got is None:
            d, _ = self.fs.zs(path)
            got = self._model_tex[path] = {s.decode() for s in set(re.findall(rb'[A-Za-z0-9_.]{6,}', d))
                                           if s.decode() in self.tex}
        return got

    def area_textures(self, folder, bancs):
        """The colour textures (names ending _Alb) the area's placed actors' models use."""
        actors = set()
        for banc in bancs:
            for part in ('Static', 'Dynamic'):
                path = f'Banc/{folder}/{banc}_{part}.bcett.byml.zs'
                if path in self.fs.files:
                    d, _ = self.fs.zs(path)
                    actors.update(s for s in byml_strings(d) if s in self.models)
        names = set()
        for a in sorted(actors):
            for m in self.models[a]:
                try:
                    names |= self.model_textures(m)
                except Exception as e:   # one unreadable model must not stop the area
                    self.log(f'world: {m}: {type(e).__name__}: {e}')
        return {n for n in names if n.endswith('_Alb')}, len(actors)

    def texture(self, name):
        """(width, height, layers, size, first bytes) of Tex/<name>.bntx.zs's texture of that name, or None."""
        try:
            d, _ = self.fs.zs(f'Tex/{name}.bntx.zs')
        except Exception:
            return None
        for t in bntx_textures(d):
            if t[0] == name:
                return t[1:]
        return None


# ---- the N64's side ----

def _hue_class(r, g, b):
    """'green', 'blue', 'brown', 'grey' or 'dark' (runtime/vk_n64world.c colour_class sorts the same way)."""
    mx, mn = max(r, g, b), min(r, g, b)
    if mx < 16:
        return 'dark'
    s = (mx - mn) / mx
    if s < 0.22:
        return 'grey'
    h = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)[0] * 360.0
    if 65 <= h < 170:
        return 'green'
    if 170 <= h < 260:
        return 'blue'
    if h < 65 or h >= 330:
        return 'brown'
    return 'other'


def _combine(m, tex, shade):
    """The colour and alpha of each texel as the material's combiner draws it, with the vertex colour shade (r, g, b
    in 0..1) and the texture's own colour for both texture units: (a - b) * c + d, the second cycle on the first's
    result in two-cycle mode (runtime/shaders/n64_fs.frag cycle)."""
    hi, lo = m['combine']
    prim = [(m['prim'] >> (8 * k) & 255) / 255.0 for k in range(4)]
    env = [(m['env'] >> (8 * k) & 255) / 255.0 for k in range(4)]
    sh = list(shade) + [1.0]
    cycles = [((hi >> 20) & 15, (lo >> 28) & 15, (hi >> 15) & 31, (lo >> 15) & 7,
               (hi >> 12) & 7, (lo >> 12) & 7, (hi >> 9) & 7, (lo >> 9) & 7),
              ((hi >> 5) & 15, (lo >> 24) & 15, hi & 31, (lo >> 6) & 7,
               (lo >> 21) & 7, (lo >> 3) & 7, (lo >> 18) & 7, lo & 7)]
    two = (m['othermode_h'] >> 20) & 3 == 1
    out = []
    for t in tex:
        tx = [(t >> (8 * k) & 255) / 255.0 for k in range(4)]
        comb = [0.0, 0.0, 0.0, 0.0]
        for ci, (a, b, c, d, aa, ab, ac, ad) in enumerate(cycles[:2 if two else 1]):
            def src(sel, kind):
                common = {0: comb, 1: tx, 2: tx, 3: prim, 4: sh, 5: env}
                if sel in common:
                    v = common[sel]
                    return v[:3]
                if kind == 'a' and sel == 6 or kind == 'd' and sel == 6:
                    return [1.0] * 3
                if kind == 'c':
                    alpha = {7: comb[3], 8: tx[3], 9: tx[3], 10: prim[3], 11: 1.0, 12: env[3]}.get(sel)
                    if alpha is not None:
                        return [alpha] * 3
                return [0.0] * 3

            def asrc(sel, kind):
                if kind == 'c':
                    return {1: tx[3], 2: tx[3], 3: prim[3], 4: 1.0, 5: env[3]}.get(sel, 0.0)
                return {0: comb[3], 1: tx[3], 2: tx[3], 3: prim[3], 4: 1.0, 5: env[3], 6: 1.0}.get(sel, 0.0)
            A, B, C, D = src(a, 'a'), src(b, 'b'), src(c, 'c'), src(d, 'd')
            rgb = [(A[k] - B[k]) * C[k] + D[k] for k in range(3)]
            al = (asrc(aa, 'a') - asrc(ab, 'b')) * asrc(ac, 'c') + asrc(ad, 'd')
            comb = [min(1.0, max(0.0, v)) for v in rgb] + [min(1.0, max(0.0, al))]
        r, g, b, _ = (int(round(v * 255)) for v in comb)
        out.append((r, g, b, t >> 24 & 255))
    return out


class N64Scene:
    """A scene file the mod holds (tools/make_n64_mod.py's layout): each texture its rooms use, with where and how it
    is drawn."""

    def __init__(self, data):
        d = data
        if d[:4] != b'N64G':
            raise ValueError('not an N64G file')
        secs = [struct.unpack_from('<II', d, 16 + 8 * k) for k in range(12)]
        vo, vn = secs[0]
        mo, mn = secs[2]
        to, _tn = secs[3]
        bo, bn = secs[4]
        mats = []
        for i in range(mn):
            f = struct.unpack_from('<8I2f8I2I', d, mo + 80 * i)
            mats.append({'combine': (f[0], f[1]), 'othermode_h': f[2], 'othermode_l': f[3], 'geometry': f[4],
                         'prim': f[5], 'env': f[6], 'tile': f[10:14]})
        self.textures = {}   # (texel offset, w, h) -> stats
        for i in range(bn):
            first, cnt, mi, _node, flags = struct.unpack_from('<IIIHH', d, bo + 16 * i)
            m = mats[mi]
            off, wh, _ul, bits = m['tile']
            if not bits >> 20 & 1:
                continue
            k = (off, wh & 0xFFFF, wh >> 16)
            T = self.textures.get(k)
            if T is None:
                T = self.textures[k] = {'floor': 0.0, 'wall': 0.0, 'ceil': 0.0, 'xlu': 0.0, 'area': 0.0,
                                        'texels': 0.0, 'shade': [0.0, 0.0, 0.0], 'shade_w': 0.0, 'mat': m,
                                        'cutout': False}
            T['cutout'] |= bool(m['othermode_l'] & 3)
            lit = bool(m['geometry'] & 0x20000)
            for v in range(first, first + cnt - 2, 3):
                p = [struct.unpack_from('<fffhhBBBB', d, vo + 24 * (v + j)) for j in range(3)]
                u = [p[1][j] - p[0][j] for j in range(3)]
                w_ = [p[2][j] - p[0][j] for j in range(3)]
                n = (u[1] * w_[2] - u[2] * w_[1], u[2] * w_[0] - u[0] * w_[2], u[0] * w_[1] - u[1] * w_[0])
                area = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2) / 2
                if area < 1e-3:
                    continue
                ny = n[1] / (2 * area)
                T['floor' if ny > 0.7 else 'ceil' if ny < -0.7 else 'wall'] += area
                if flags & 1:
                    T['xlu'] += area
                T['area'] += area
                s1, t1 = (p[1][3] - p[0][3]) / 32.0, (p[1][4] - p[0][4]) / 32.0
                s2, t2 = (p[2][3] - p[0][3]) / 32.0, (p[2][4] - p[0][4]) / 32.0
                T['texels'] += abs(s1 * t2 - s2 * t1) / 2
                if not lit:
                    for j in range(3):
                        for c in range(3):
                            T['shade'][c] += p[j][5 + c] * area
                    T['shade_w'] += 3 * area
        for (off, w, h), T in self.textures.items():
            raw = struct.unpack_from('<%dI' % (w * h), d, to + 4 * off)
            if T['shade_w']:
                avg = [c / T['shade_w'] for c in T['shade']]
                top = max(avg) or 1.0
                shade = [c / top for c in avg]   # the vertices' tint, without their baked darkness
            else:
                shade = [1.0, 1.0, 1.0]
            px = _combine(T['mat'], raw, shade)
            T['w'], T['h'] = w, h
            T['rgba'] = px
            n = len(px)
            T['mean'] = tuple(sum(p[c] for p in px) / n for c in range(3))
            T['cls'] = _hue_class(*T['mean'])
            # texels per N64 unit along a side, and the metres one repeat covers
            dens = math.sqrt(T['texels'] / T['area']) if T['area'] and T['texels'] else 0.0
            T['repeat_m'] = (max(w, h) / dens / UNITS_PER_METRE) if dens else 0.0
            T['wrap'] = _tiles(T, px)


def _tiles(T, px):
    """Whether the texture repeats both ways without a seam: its tile wraps (no clamp, tools/make_n64_mod.py's cms
    and cmt bits) and the step across each edge is no larger than twice the texture's own step between neighbours
    (a strip such as a cliff's grassy top, or a patch with a rim, has a seam)."""
    bits = T['mat']['tile'][3]
    if bits & 2 or bits >> 2 & 2:
        return False
    w, h = T['w'], T['h']
    L = [0.3 * p[0] + 0.59 * p[1] + 0.11 * p[2] for p in px]
    if w < 2 or h < 2:
        return True
    in_x = sum(abs(L[y * w + x] - L[y * w + x + 1]) for y in range(h) for x in range(w - 1)) / (h * (w - 1))
    in_y = sum(abs(L[y * w + x] - L[(y + 1) * w + x]) for y in range(h - 1) for x in range(w)) / ((h - 1) * w)
    seam_x = sum(abs(L[y * w] - L[y * w + w - 1]) for y in range(h)) / h
    seam_y = sum(abs(L[x] - L[(h - 1) * w + x]) for x in range(w)) / w
    return seam_x <= 2 * in_x + 1 and seam_y <= 2 * in_y + 1


def pick(scenes):
    """Per category the N64 texture (scene, key) that stands for it in these scenes, fallbacks applied."""
    stats = []
    for si, sc in enumerate(scenes):
        for k, T in sc.textures.items():
            stats.append((si, k, T))

    def best(score, avoid=None):
        """The texture of the highest score, those that repeat without a seam first, then any; avoid is passed by
        when another scores at all."""
        for need_wrap in (True, False):
            top, at = 0.0, None
            for si, k, T in stats:
                if need_wrap and not T['wrap'] or (si, k) == avoid:
                    continue
                s = score(T)
                if s > top:
                    top, at = s, (si, k)
            if at:
                return at
        return avoid if avoid and score(stats_of[avoid]) > 0 else None
    stats_of = {(si, k): T for si, k, T in stats}

    def share(T, *parts):
        return sum(T[p] for p in parts) / T['area'] if T['area'] else 0.0
    solid = lambda T: not T['cutout'] and T['xlu'] < 0.5 * T['area']
    got = {
        'grass': best(lambda T: T['floor'] if solid(T) and T['cls'] == 'green' and share(T, 'floor') >= 0.5 else 0),
        'dirt': best(lambda T: T['floor'] if solid(T) and T['cls'] in ('brown', 'grey') and share(T, 'floor') >= 0.5
                     else 0),
        'path': best(lambda T: T['floor'] * (1.5 if T['cls'] == 'grey' else 1.0)
                     if solid(T) and T['cls'] in ('brown', 'grey') and share(T, 'floor') >= 0.5 else 0),
        'rock': best(lambda T: T['wall'] * (1.5 if T['cls'] == 'grey' else 1.0)
                     if solid(T) and T['cls'] in ('brown', 'grey') and share(T, 'wall', 'ceil') >= 0.5 else 0),
        'leaf': best(lambda T: T['wall'] + T['ceil'] if T['cls'] == 'green' and share(T, 'wall', 'ceil') >= 0.5
                     else 0),
        'water': best(lambda T: T['xlu'] if T['xlu'] >= 0.5 * T['area'] and share(T, 'floor') >= 0.5
                      and T['cls'] in ('blue', 'green', 'grey') else 0),
        'roof': None,
    }
    # wood: the brown wall texture of the most wall other than rock's, when there is one
    got['wood'] = best(lambda T: T['wall'] if solid(T) and T['cls'] == 'brown' and share(T, 'wall', 'ceil') >= 0.4
                       else 0, avoid=got['rock'])
    out = {}
    for c in CATEGORIES:
        at = got.get(c)
        for f in FALLBACK[c]:
            if at is None:
                at = got.get(f)
        out[c] = at
    return out


# ---- the file ----

def make(fs, mod_dir, log=print, only=None, keep_normals=False):
    """world.bin's bytes from the RomFS (tools/n64voice.py RomFS) and the mod's scene files in mod_dir/n64."""
    data = Path(mod_dir) / 'n64'
    remake = Remake(fs, log)
    tex_rows, texels, tex_at = [], [], {}   # N64 textures in the file
    areas, entries = [], {}
    report = {}
    for folder, (bancs, numbers) in AREAS.items():
        if only and folder not in only:
            continue
        scenes = []
        for n in numbers:
            p = data / f'scene_{n:03d}.n64g'
            if p.is_file():
                try:
                    scenes.append((n, N64Scene(p.read_bytes())))
                except Exception as e:
                    log(f'world: scene {n}: {type(e).__name__}: {e}')
        if not scenes:
            log(f'world: {folder}: none of the scenes {numbers} is in the mod')
            continue
        chosen = pick([s for _, s in scenes])
        row = []
        for c in CATEGORIES:
            at = chosen[c]
            if at is None:
                row.append(NONE)
                continue
            si, k = at
            ident = (scenes[si][0], k)
            if ident not in tex_at:
                T = scenes[si][1].textures[k]
                keep = c == 'leaf' and T['cutout']
                tex_at[ident] = len(tex_rows)
                tex_rows.append((len(texels), T['w'], T['h'], 1 if keep else 0,
                                 min(255, int(round(T['repeat_m'] * 8)))))
                for r, g, b, a in T['rgba']:
                    texels.append(r | g << 8 | b << 16 | (a if keep else 255) << 24)
            row.append(tex_at[ident])
        names, nactors = remake.area_textures(folder, bancs)
        cats = {}
        for name in sorted(names):
            c = category_of(name[:-4])
            if c and row[CAT[c]] != NONE:
                cats[name] = c
        for b in bancs:
            areas.append((b, row))
        kept = 0
        for name, c in cats.items():
            for tname, kind in ((name, KIND_COLOUR), (name[:-4] + '_Nrm', KIND_NORMAL)):
                if kind == KIND_NORMAL and (keep_normals or tname not in remake.tex):
                    continue
                t = remake.texture(tname)
                if not t or t[3] < KEY_BYTES or len(t[4]) < KEY_BYTES:
                    continue
                w, h, _layers, _size, first = t
                k = key(w, h, first)
                metres = min(255, int(round(w / REMAKE_TEXELS_PER_METRE * 8)))
                old = entries.get(k)
                if old and old[:2] != (CAT[c], kind):
                    log(f'world: {tname} has the key of another texture; neither is replaced')
                    entries[k] = None
                    continue
                if old is None and k in entries:
                    continue
                entries[k] = (CAT[c], kind, metres, tname)
                kept += kind == KIND_COLOUR
        report[folder] = {'actors': nactors, 'textures': len(names), 'replaced': kept,
                          'n64': {c: (None if chosen[c] is None else
                                      f'scene {scenes[chosen[c][0]][0]} texels {chosen[c][1][0]} '
                                      f'{chosen[c][1][1]}x{chosen[c][1][2]}') for c in CATEGORIES}}
        log(f'world: {folder}: {kept} of {len(names)} colour textures of {nactors} actors get N64 textures')
    # the terrain's material array: one entry, its layers sorted by colour at run time
    t = remake.texture('MaterialAlb')
    if t and t[3] >= KEY_BYTES:
        entries[key(t[0], t[1], t[4])] = (CAT['grass'], KIND_ARRAY, 0, 'MaterialAlb')
    rows = sorted((k, v) for k, v in entries.items() if v)
    out = bytearray(struct.pack('<4s7I', b'N64W', VERSION, len(areas), len(tex_rows), len(rows), len(texels), 0, 0))
    for name, row in areas:
        out += name.encode()[:31].ljust(32, b'\0') + struct.pack('<8H', *row)
    for first, w, h, flags, metres in tex_rows:
        out += struct.pack('<IHHHBBI', first, w, h, flags, metres, 0, 0)
    for k, (c, kind, metres, _name) in rows:
        out += struct.pack('<QBBBBI', k, c, kind, metres, 0, 0)
    out += struct.pack('<%dI' % len(texels), *texels)
    report['entries'] = len(rows)
    report['names'] = {f'{k:016x}': v[3] for k, v in rows}
    return bytes(out), report


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--mod', type=Path, required=True, help='the N64 Graphics mod folder (its n64 folder has the scenes)')
    ap.add_argument('--romfs', type=Path, required=True, help="the game's unpacked RomFS (romfs.bin and index.json)")
    ap.add_argument('--area', action='append', help='only this area (a key of AREAS; may be given more than once)')
    ap.add_argument('--keep-normals', action='store_true', help="keep the remake's normal maps on replaced surfaces")
    a = ap.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import n64voice
    blob, report = make(n64voice.RomFS(a.romfs), a.mod, only=a.area, keep_normals=a.keep_normals)
    (a.mod / 'n64' / 'world.bin').write_bytes(blob)
    (a.mod / 'n64' / 'world.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print(f'{a.mod / "n64" / "world.bin"}: {report["entries"]} textures')


if __name__ == '__main__':
    main()
