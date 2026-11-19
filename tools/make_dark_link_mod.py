"""Make the Dark Link mod: Link plays as Dark Link, the adult with Dark Link's own models, the child made dark.

The adult. The game picks an actor's model from the ModelInfo component in its pack (Pack/Actor/<actor>.pack.zs, a
zstd SARC): FmdbName and ModelProjectName name Model/<project>.<fmdb>.bfres. Dark Link's own models are the player's
skeleton dressed dark (Model/Player_Adult_Dark..., and _Dark copies of the Master Sword, the Hylian Shield and its
sheath). The mod carries the four packs with their ModelInfo pointing at those models.

The child has no dark model, so the mod carries his own (Model/Player_Child.Player_Child.bfres) with its colour
textures swapped for dark copies. A model names its textures in its string table, and the game loads each as
Tex/<name>.bntx.zs, or, for a name System/Gfx/UnifyTex.Product.100.byml lists, as the texture it maps the name to (the
child's hair albedo is the adult's Mt_Hairs_Player_Adult_Alb that way). Each colour texture the model names (albedo,
the hair's colour ramp, the eyes' glow) is copied under a new name of the same length that keeps the name's end
(PREFIX in place of its first three characters), and the model's string is changed in place: the file keeps its size
and every offset, and a name dictionary, which branches on bits counted from a key's end, keeps its shape. A copy's
texture takes the new name too (written over the old in place, so a source name may be longer; the one-entry name
dictionary of the texture file gets the new name's branch bit). The copies are filled with one colour, near black for
the body and red for the eyes, whatever their block format, and keep their alpha where it varies (cut-out cloth and
leather):
- ASTC: a block that is opaque becomes a constant-colour block (void extent); a block whose alpha varies becomes a
  luminance-alpha block (colour endpoint mode 4) with that colour, its alpha range and a weight grid read from the
  decoded alpha. The runtime's decoder (runtime/tex_astc.c, built into build/dark-link) decodes the blocks.
- BC1 keeps its transparent texels and BC3 its alpha half; BC7 becomes opaque; RGBA8 keeps its alpha.
The copies are new files, so other characters that share a texture with the child keep theirs.

Every other piece of the player's gear at both ages (gear_actors: each Armor_ and Weapon_ pack, so the tunics, heads,
boots, gauntlets, masks and Lens of Truth, the swords, shields and sheaths, and the tools from the bow to the bottles)
is made dark the same way. Those models are shared with the shop, chests and pickups, so the dark copies go under a
new project name of the same length (Model/<PREFIX project>.<fmdb>.bfres, the model file's own "project.fmdb" string
renamed with it, with copies of the project's other models and animations) and only the player's equipment packs point
their ModelProjectName (their sub-models' too) at it.

The mod's ootpc-feature.txt turns on the feature dark-link (runtime/mods.h MOD_FEATURE_DARK_LINK): with N64 Graphics
on, the runtime draws N64 Link as the N64 game's Dark Link at both ages (docs/n64.md "Dark Link").

    python tools/make_dark_link_mod.py --game <game folder> [--out <mod folder>]

Run with Python 3.14 (its compression.zstd) and a C compiler for the ASTC decoder (MSYS2 UCRT64 clang or gcc, or a
clang or gcc on PATH). Standard library only besides the repository's tools.
"""
import argparse
import ctypes
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import n64voice   # noqa: E402  RomFS: the unpacked RomFS with the zstd dictionaries
import randomizer   # noqa: E402  byml_read, byml_write

ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = 'Dark Link'
FEATURE = 'dark-link'   # runtime/mods.h MOD_FEATURE_DARK_LINK
# actor pack -> the model it takes
MODELS = {
    'Player_Adult': 'Player_Adult_Dark',
    'Weapon_Sword_Master': 'Weapon_Sword_Master_Dark',
    'Weapon_Shield_Hylian': 'Weapon_Shield_Hylian_Dark',
    'Weapon_Sheath_070': 'Weapon_Sheath_Master_Dark',
}
# Gear of the adult whose effect user becomes the Dark Link enemy's, so the game has that user's resource while he wears
# one of them: the runtime lends it to the player to start Dark Link's own effects on him (runtime/dark_link_fx.c
# LENDERS), and the player keeps his own user. Their own users have little to lose: the boots' have no effects, the
# Hylian Shield's two (guarding against a beam). An actor's components are the game's to make, so only gear that has
# an effect component of its own takes part.
LENDERS = ('Weapon_Shield_Hylian', 'Armor_Adult_HeavyBoot', 'Armor_Adult_HoverBoot')
DARK_USER = 'Enemy_MiddleBoss_DarkLink'
CHILD = 'Player_Child'   # the model made dark in place
# Weapon_ packs that are not the player's: the Poes' lanterns and torches
GEAR_SKIP = ('Weapon_Poe', 'Weapon_GhostPicturePerformer_Poe')
UNIFY = 'System/Gfx/UnifyTex.Product.100.byml.zs'   # texture name -> the texture the game loads for it
BODY = (22, 22, 28)      # the body's colour (sRGB), near black
EYES = (235, 24, 18)     # the eyes' colour and glow
PREFIX = 'DL_'           # takes the place of a dark copy's first three characters
README = """Dark Link: Link plays as Dark Link. The adult has Dark Link's own models (the game's), with Dark Link's sword,
shield and sheath; the child, who has no dark model in the game, has his own made near black with red eyes. Every
other piece of gear at both ages is made dark too: tunics, hats, boots, gauntlets, masks, the Lens of Truth, swords,
shields and sheaths, and the tools (bow, quiver, slingshot, hookshot and longshot, hammer, boomerang, Deku Stick,
fishing rod, bottles). Only the ones Link holds and wears: the shop's, the chests' and the pickups' stay as they are.

With N64 Graphics on, the Mods tab's "Dark Link style" picks how N64 Link looks while this mod is on:
- N64 style (the default): N64 Link is drawn as the N64 game's Dark Link, as child and adult.
- Remake style: N64 Link is off, and the remake's dark models above play.

Made by tools/make_dark_link_mod.py from your own game files. Turn it off on the Mods tab to play as Link again.
"""


def sarc_members(data):
    """[(name, bytes, start)] of a little-endian SARC's members in SFAT order (start: where the data began)."""
    if data[:4] != b'SARC' or data[6:8] != b'\xff\xfe':
        raise ValueError('not a little-endian SARC')
    header, _size, base = struct.unpack_from('<HxxII', data, 4)
    count = struct.unpack_from('<H', data, header + 6)[0]
    names = header + 12 + count * 16 + 8
    out = []
    for i in range(count):
        _hash, attr, lo, hi = struct.unpack_from('<4I', data, header + 12 + i * 16)
        at = names + (attr & 0xFFFFFF) * 4
        out.append((data[at:data.index(b'\0', at)].decode(), data[base + lo:base + hi], lo))
    return out


def sarc_replace(data, name, new):
    """data with member name's bytes replaced by new. The header, SFAT and SFNT stay; the members after it move, each
    to the alignment its old place had (up to 0x2000)."""
    header, _size, base = struct.unpack_from('<HxxII', data, 4)
    count = struct.unpack_from('<H', data, header + 6)[0]
    members = sarc_members(data)
    out = bytearray(data[:base])
    at = 0
    for i, (n, body, lo) in enumerate(members):
        align = 1
        while align < 0x2000 and lo % (align * 2) == 0 and lo:
            align *= 2
        at = (at + align - 1) // align * align
        body = new if n == name else body
        node = header + 12 + i * 16
        struct.pack_into('<II', out, node + 8, at, at + len(body))
        out += b'\0' * (base + at - len(out)) + body
        at += len(body)
    struct.pack_into('<I', out, 8, len(out))
    if not any(n == name for n, _, _ in members):
        raise ValueError(f'{name} is not in the pack')
    if count != len(members):
        raise ValueError('the SARC was not read whole')
    return bytes(out)


def dark_pack(fs, actor, model, keys=('FmdbName', 'ModelProjectName')):
    """The actor's pack with its ModelInfo's keys naming model, zstd-compressed as the game's was."""
    path = f'Pack/Actor/{actor}.pack.zs'
    data, d = fs.zs(path)
    info = [n for n, _, _ in sarc_members(data) if n.startswith('Component/ModelInfo/') and n.endswith('.bgyml')]
    if len(info) != 1:
        raise ValueError(f'{path}: {len(info)} ModelInfo files')
    body = dict((n, b) for n, b, _ in sarc_members(data))[info[0]]
    ver, root = randomizer.byml_read(body)
    for key in keys:
        if root.get(key, (None,))[0] != randomizer.T_STR:
            raise ValueError(f'{path}: ModelInfo has no {key}')
        root[key] = (randomizer.T_STR, model)
    return path, fs.compress(sarc_replace(data, info[0], randomizer.byml_write(ver, root)), d)


def elink_pack(fs, packed, path, user):
    """The pack at path (packed: the mod's version of it already made, else the game's) with its ELink component's
    UserName naming user, zstd-compressed as the game's was."""
    data, d = fs.zs(path)
    if packed is not None:
        data = fs.zstd.decompress(packed, zstd_dict=d) if d is not None else fs.zstd.decompress(packed)
    names = [n for n, _, _ in sarc_members(data) if n.startswith('Component/ELink/') and n.endswith('.bgyml')]
    if len(names) != 1:
        raise ValueError(f'{path}: {len(names)} ELink files')
    ver, root = randomizer.byml_read(dict((n, b) for n, b, _ in sarc_members(data))[names[0]])
    if root.get('UserName', (None,))[0] != randomizer.T_STR:
        raise ValueError(f'{path}: ELink has no UserName')
    root['UserName'] = (randomizer.T_STR, user)
    return fs.compress(sarc_replace(data, names[0], randomizer.byml_write(ver, root)), d)


# ---- the child's dark textures

# BNTX formats (the second byte of the format word; the first is 6 for sRGB) and the ASTC block footprints
ASTC = {0x2d: (4, 4), 0x2e: (5, 4), 0x2f: (5, 5), 0x30: (6, 5), 0x31: (6, 6), 0x32: (8, 5), 0x33: (8, 6), 0x34: (8, 8),
        0x35: (10, 5), 0x36: (10, 6), 0x37: (10, 8), 0x38: (10, 10), 0x39: (12, 10), 0x3a: (12, 12)}
RGBA8, BC1, BC3, BC7 = 0x0b, 0x1a, 0x1c, 0x20


def u32(b, at): return struct.unpack_from('<I', b, at)[0]
def u64(b, at): return struct.unpack_from('<Q', b, at)[0]


def colour_of(name):
    """The colour a texture's dark copy takes; None for one that keeps its own (normals, roughness, masks)."""
    if not (name.endswith('_Alb') or name.endswith('_Gn0') or (name.endswith('_Emc') and 'Eye' in name)):
        return None
    return EYES if 'Eye_Inner' in name or name.endswith('_Emc') else BODY


def dark_name(name):
    return PREFIX + name[len(PREFIX):]


def rename_strings(data, names, what):
    """data with each string table entry (a u16 length, the text, a zero) of names (a dict old: new, or a list of
    old names renamed to dark_name) renamed in place. A new name shorter than the old one is written over it with its
    own length, zeros filling the rest: every offset stays."""
    out = bytearray(data)
    pairs = names.items() if isinstance(names, dict) else [(n, dark_name(n)) for n in names]
    for name, to in pairs:
        old, new = name.encode(), to.encode()
        if len(new) > len(old):
            raise ValueError(f'{what}: {to} is longer than {name}')
        pat = struct.pack('<H', len(old)) + old + b'\0'
        at, n = out.find(pat), 0
        while at >= 0:
            out[at:at + 3 + len(old)] = struct.pack('<H', len(new)) + new + bytes(1 + len(old) - len(new))
            n += 1
            at = out.find(pat, at + 1)
        if not n:
            raise ValueError(f'{what}: no string {name}')
    return bytes(out)


def dict_bit(key):
    """The branch bit of a one-entry name dictionary's entry: the key's lowest set bit, counted from its end (bit i is
    bit i % 8 of the character i // 8 from the end)."""
    k = key.encode()
    for i in range(8 * len(k)):
        if k[len(k) - 1 - (i >> 3)] >> (i & 7) & 1:
            return i
    raise ValueError(f'the name {key!r} has no set bit')


def one_entry_dict(data, key, what):
    """data with its one-entry name dictionary (_DIC, u32 count 1, the root node, then the entry node: u32 bit, u16
    left, u16 right, u64 name) branching on key's bit."""
    at = data.find(b'_DIC')
    if at < 0 or u32(data, at + 4) != 1:
        raise ValueError(f'{what} has no one-entry name dictionary')
    out = bytearray(data)
    struct.pack_into('<I', out, at + 8 + 16, dict_bit(key))
    return bytes(out)


def bfres_strings(data):
    """The strings of a model file's string table (_STR: a u32 count at +0x10, then from +0x14 entries of a u16
    length, the text and a zero, each at an even offset; the first is the empty string)."""
    at = data.find(b'_STR')
    if at < 0:
        raise ValueError('the model has no string table')
    count, p, out = u32(data, at + 0x10), at + 0x14, []
    for _ in range(count + 1):
        n = struct.unpack_from('<H', data, p)[0]
        out.append(data[p + 2:p + 2 + n].decode('utf-8', 'replace'))
        p = (p + 2 + n + 2) & ~1
    return out


class AstcDecoder:
    """runtime/tex_astc.c astc_decode_block as a library built into build/dark-link: the source is copied beside a
    tex.h with only the standard headers it needs, and a wrapper exports the function."""

    # Where the decoder is built and which MSYS2 UCRT64 folder's compiler builds it: the checkout's build folder and
    # C:\msys64\ucrt64 by default; make() takes the launcher's own (its cache folder and toolchain/msys64/ucrt64).
    work = None
    msys = None

    def __init__(self):
        out = Path(self.work) if self.work else ROOT / 'build' / 'dark-link'
        out.mkdir(parents=True, exist_ok=True)
        src = ROOT / 'runtime' / 'tex_astc.c'
        dll = out / 'dark_astc.dll'
        if not dll.is_file() or dll.stat().st_mtime < src.stat().st_mtime:
            shutil.copyfile(src, out / 'tex_astc.c')
            (out / 'tex.h').write_text('#pragma once\n#include <stdbool.h>\n#include <stddef.h>\n#include <stdint.h>\n')
            (out / 'dark_astc.c').write_text(
                '#include "tex_astc.c"\n'
                '__declspec(dllexport) int dark_astc_block(const uint8_t *b, uint32_t bw, uint32_t bh, int srgb, '
                'uint8_t *out) { return astc_decode_block(b, bw, bh, srgb != 0, out); }\n')
            dirs = [Path(self.msys) / 'bin'] if self.msys else []
            dirs.append(Path(r'C:\msys64\ucrt64\bin'))
            cc = next((str(c) for d in dirs for c in (d / 'clang.exe', d / 'gcc.exe') if c.is_file()), None)
            cc = cc or shutil.which('clang') or shutil.which('gcc')
            if not cc:
                raise SystemExit('no C compiler for the ASTC decoder: install MSYS2 UCRT64 clang (docs/building.md)')
            # the compiler's own folder first on PATH: MSYS2's gcc finds its parts through it
            env = dict(os.environ, PATH=str(Path(cc).parent) + os.pathsep + os.environ.get('PATH', ''))
            r = subprocess.run([cc, '-shared', '-O2', '-w', '-static-libgcc', str(out / 'dark_astc.c'), '-o',
                                str(dll)], capture_output=True, text=True, env=env)
            if r.returncode:
                raise SystemExit(f'the ASTC decoder did not compile ({cc}):\n{r.stdout}{r.stderr}')
        self.lib = ctypes.CDLL(str(dll))
        self.lib.dark_astc_block.restype = ctypes.c_int
        self.lib.dark_astc_block.argtypes = [ctypes.c_char_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int,
                                             ctypes.c_char_p]

    def rgba(self, block, bw, bh, srgb):
        """The block's texels as RGBA8, row by row; None for a block that does not decode."""
        out = ctypes.create_string_buffer(bw * bh * 4)
        if not self.lib.dark_astc_block(bytes(block), bw, bh, int(srgb), out):
            return None
        return out.raw


def astc_void(rgb, a=255):
    """A constant-colour ASTC block (an LDR void extent without extent: every texel rgb, a)."""
    return struct.pack('<Q4H', 0xFFFFFFFFFFFFFDFC, *(c * 257 for c in rgb), a * 257)


def _block_mode(m):
    """(weight grid width, height, two planes, quantization level) of an 11-bit 2D block mode, None for a reserved
    one (the specification's block mode table)."""
    r, h, d, a = m >> 4 & 1, m >> 9 & 1, m >> 10 & 1, m >> 5 & 3
    if m & 3:
        r |= (m & 3) << 1
        b, k = m >> 7 & 3, m >> 2 & 3
        if k == 0:
            wx, wy = b + 4, a + 2
        elif k == 1:
            wx, wy = b + 8, a + 2
        elif k == 2:
            wx, wy = a + 2, b + 8
        elif m & 0x100:
            wx, wy = (b & 1) + 2, a + 2
        else:
            wx, wy = a + 2, (b & 1) + 6
    else:
        r |= (m >> 2 & 3) << 1
        if not m >> 2 & 3:
            return None
        b, k = m >> 9 & 3, m >> 7 & 3
        if k == 0:
            wx, wy = 12, a + 2
        elif k == 1:
            wx, wy = a + 2, 12
        elif k == 2:
            wx, wy, d, h = a + 6, b + 6, 0, 0
        elif a < 2:
            wx, wy = (6, 10) if a == 0 else (10, 6)
        else:
            return None
    return wx, wy, d, r - 2 + 6 * h


# quantization levels whose weights are plain bits (no trits or quints): level -> bits per weight
PLAIN = {0: 1, 2: 2, 5: 3, 8: 4, 11: 5}


def la_modes(bw, bh):
    """[(mode, grid width, grid height, bits per weight)] for luminance-alpha blocks of the footprint: one plane, the
    weights leaving 32 bits for four 8-bit endpoint values after the 17 header bits; per grid size the most bits, and
    only grids at least half the footprint each way (fill tries each on a block and keeps the closest)."""
    best = {}
    for m in range(2048):
        bm = _block_mode(m)
        if not bm:
            continue
        wx, wy, d, q = bm
        if d or q not in PLAIN or wx > bw or wy > bh or 2 * wx < bw or 2 * wy < bh:
            continue
        bits = wx * wy * PLAIN[q]
        if wx * wy > 64 or bits < 24 or bits > 128 - 17 - 32:
            continue
        if (wx, wy) not in best or PLAIN[q] > best[wx, wy][3]:
            best[wx, wy] = (m, wx, wy, PLAIN[q])
    if not best:
        raise ValueError(f'no luminance-alpha block mode for {bw}x{bh}')
    return list(best.values())


def astc_la(lum, alpha, bw, bh, mode):
    """A luminance-alpha ASTC block (one partition, colour endpoint mode 4, 8-bit endpoints): every texel lum, its
    alpha from alpha (row by row) through the mode's weight grid, each grid point taking the texel nearest it."""
    m, wx, wy, bits = mode
    a0, a1 = min(alpha), max(alpha)
    top = (1 << bits) - 1
    stream = 0
    for gy in range(wy):
        ty = round(gy * (bh - 1) / (wy - 1))
        for gx in range(wx):
            tx = round(gx * (bw - 1) / (wx - 1))
            w = round((alpha[ty * bw + tx] - a0) * top / (a1 - a0)) if a1 > a0 else 0
            stream |= w << ((gy * wx + gx) * bits)
    v = m | 4 << 13 | lum << 17 | lum << 25 | a0 << 33 | a1 << 41
    for k in range(wx * wy * bits):   # the weights run down from the block's top bit
        if stream >> k & 1:
            v |= 1 << (127 - k)
    return v.to_bytes(16, 'little')


def astc_maybe_alpha(block):
    """1 when an ASTC block may hold alpha below opaque: a void extent below opaque, more than one partition, or one
    partition with an endpoint mode that has alpha."""
    v = int.from_bytes(block, 'little')
    if v & 0x1FF == 0x1FC:
        return v >> 112 != 0xFFFF
    if v >> 11 & 3:
        return True
    return (v >> 13 & 15) in (4, 5, 10, 12, 13)


def bc1_colour(rgb):
    r, g, b = (round(c * m / 255) for c, m in zip(rgb, (31, 63, 31)))
    return r << 11 | g << 5 | b


def bc7_constant(rgb):
    """An opaque constant BC7 block: mode 6, both endpoints rgb (7 bits and a p-bit of 1), every index 0."""
    v = 1 << 6
    for i, c in enumerate(rgb):
        v |= (c >> 1) << (7 + 14 * i) | (c >> 1) << (14 + 14 * i)
    v |= 127 << 49 | 127 << 56 | 1 << 63 | 1 << 64
    return v.to_bytes(16, 'little')


def fill(data, start, size, kind, srgb, rgb, decoder, stats):
    """data with a texture's blocks (every level: start..start + size) turned to rgb, keeping alpha where the format
    lets it."""
    out = bytearray(data)
    end = start + size
    if kind in ASTC:
        bw, bh = ASTC[kind]
        modes, void, lum = None, astc_void(rgb), round(sum(rgb) / 3)
        for at in range(start, end - 15, 16):
            block = out[at:at + 16]
            px = decoder.rgba(block, bw, bh, srgb) if astc_maybe_alpha(block) else None
            a = px[3::4] if px else None
            if not a or min(a) == 255:
                out[at:at + 16] = void
            elif min(a) == max(a):
                out[at:at + 16] = astc_void(rgb, a[0])
            else:
                modes = modes or la_modes(bw, bh)
                best = None
                for mode in modes:   # the grid whose decoded alpha is closest to the block's
                    new = astc_la(lum, a, bw, bh, mode)
                    got = decoder.rgba(new, bw, bh, srgb)
                    if not got:
                        raise ValueError('a luminance-alpha block did not decode')
                    err = sum(abs(x - y) for x, y in zip(got[3::4], a))
                    if not best or err < best[0]:
                        best = (err, new)
                stats['alpha blocks'] = stats.get('alpha blocks', 0) + 1
                stats['alpha error'] = stats.get('alpha error', 0) + best[0] / len(a)
                out[at:at + 16] = best[1]
    elif kind == BC1:
        c = bc1_colour(rgb)
        for at in range(start, end - 7, 8):
            c0, c1, idx = struct.unpack_from('<HHI', out, at)
            keep = 0
            if c0 <= c1:   # three colours and transparent black: index 3 stays transparent
                for t in range(16):
                    if idx >> (2 * t) & 3 == 3:
                        keep |= 3 << (2 * t)
            struct.pack_into('<HHI', out, at, c, c, keep)
    elif kind == BC3:
        c = bc1_colour(rgb)
        for at in range(start, end - 15, 16):   # the alpha half (the first 8 bytes) stays
            struct.pack_into('<HHI', out, at + 8, c, c, 0)
    elif kind == BC7:
        block = bc7_constant(rgb)
        for at in range(start, end - 15, 16):
            out[at:at + 16] = block
    elif kind == RGBA8:
        for at in range(start, end - 3, 4):
            out[at:at + 3] = bytes(rgb)
    else:
        raise ValueError(f'texture format 0x{kind:02x} is not one this tool fills')
    return bytes(out)


def dark_texture(fs, src, name, rgb, decoder, stats):
    """(path, file) of Tex/<name>.bntx.zs: a copy of src's texture file with every level filled with rgb, its texture
    renamed name when that fits in place (else it keeps src's name, as a texture the game loads through UNIFY does)."""
    what = f'Tex/{src}.bntx.zs'
    data, d = fs.zs(what)
    if data[:4] != b'BNTX':
        raise ValueError(f'{what} is not a BNTX file')
    count, arr = u32(data, 0x24), u64(data, 0x28)
    for i in range(count):
        p = u64(data, arr + 8 * i)
        if data[p:p + 4] != b'BRTI':
            raise ValueError(f'{what}: texture {i} has no BRTI header')
        fmt = u32(data, p + 0x1c)
        data = fill(data, u64(data, u64(data, p + 0x70)), u32(data, p + 0x50), fmt >> 8, (fmt & 0xff) == 6, rgb,
                    decoder, stats)
    if len(name) <= len(src):
        data = one_entry_dict(rename_strings(data, {src: name}, what), name, what)
    return f'Tex/{name}.bntx.zs', fs.compress(data, d)


def unify_table(fs):
    """{texture name: the texture the game loads for it} from UNIFY; {} when the game has none."""
    if UNIFY not in fs.files:
        return {}
    _, root = randomizer.byml_read(fs.zs(UNIFY)[0])
    return {k: v[1] for k, v in root.items() if isinstance(v, tuple) and isinstance(v[1], str)}


class Darkener:
    """Makes models' dark copies: each colour texture a model names renamed to dark_name and copied dark once."""

    def __init__(self, fs, log):
        self.fs, self.log = fs, log
        self.unify = unify_table(fs)
        self.tex = {p[4:-len('.bntx.zs')] for p in fs.files if p.startswith('Tex/') and p.endswith('.bntx.zs')}
        self.decoder = AstcDecoder()
        self.files = {}   # RomFS path -> file
        self.made = set()

    def source(self, name):
        """The texture file the game loads for name (UNIFY's choice first), None for none."""
        for n in (self.unify.get(name), name):
            if n and n in self.tex:
                return n
        return None

    def model(self, src, dest, renames, label):
        """Model/<dest> as a copy of Model/<src> naming dark textures, its strings renames (old: new) renamed too."""
        model, d = self.fs.zs(f'Model/{src}')
        strings = set(bfres_strings(model))
        names = sorted(n for n in strings if colour_of(n) and self.source(n))
        new = [dark_name(n) for n in names]
        clash = sorted(set(n for n in new if n in strings or n in self.tex or n in self.unify or new.count(n) > 1))
        if clash:
            raise ValueError(f'{src}: dark texture names already taken: ' + ', '.join(clash))
        pairs = {n: dark_name(n) for n in names}
        pairs.update((k, v) for k, v in renames.items() if k in strings)
        self.files[f'Model/{dest}'] = self.fs.compress(rename_strings(model, pairs, src), d)
        for n in names:
            if dark_name(n) in self.made:
                continue
            self.made.add(dark_name(n))
            stats, s = {}, self.source(n)
            path, data = dark_texture(self.fs, s, dark_name(n), colour_of(n), self.decoder, stats)
            self.files[path] = data
            kept = (f', {stats["alpha blocks"]} blocks keep their alpha, off by '
                    f'{stats["alpha error"] / stats["alpha blocks"]:.1f} of 255 on average' if stats else '')
            via = f' (the game loads {s} for it)' if s != n else ''
            self.log(f'{label}: {n} -> {dark_name(n)}{via}{kept}')


def model_info(fs, actor):
    """(ModelProjectName, FmdbName) of the actor's pack."""
    data, _ = fs.zs(f'Pack/Actor/{actor}.pack.zs')
    for n, body, _ in sarc_members(data):
        if n.startswith('Component/ModelInfo/') and n.endswith('.bgyml'):
            _, root = randomizer.byml_read(body)
            return root['ModelProjectName'][1], root['FmdbName'][1]
    raise ValueError(f'Pack/Actor/{actor}.pack.zs has no ModelInfo')


def gear_actors(fs):
    """The player's equipment packs other than MODELS': every Armor_ and Weapon_ pack but the bases, Dark Link's own and
    the Poes' (enemies' weapons)."""
    out = []
    for p in sorted(fs.files):
        if not (p.startswith('Pack/Actor/') and p.endswith('.pack.zs')):
            continue
        actor = p[len('Pack/Actor/'):-len('.pack.zs')]
        if (actor.startswith(('Armor_', 'Weapon_')) and actor not in MODELS and not actor.endswith('_Base')
                and 'DarkLink' not in actor and not actor.startswith(GEAR_SKIP)):
            out.append(actor)
    return out


def info_projects(fs, actor):
    """The model projects the actor's ModelInfo files name (their own and their sub-models')."""
    data, _ = fs.zs(f'Pack/Actor/{actor}.pack.zs')
    found = []

    def walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                if k == 'ModelProjectName' and isinstance(x, tuple) and x[0] == randomizer.T_STR:
                    found.append(x[1])
                else:
                    walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    for n, body, _ in sarc_members(data):
        if n.startswith('Component/ModelInfo/') and n.endswith('.bgyml'):
            walk(randomizer.byml_read(body)[1])
    return [p for p in dict.fromkeys(found) if p != 'dammy_data']


def project_pack(fs, actor, renames):
    """The actor's pack with each ModelProjectName its ModelInfo files name (sub-models' too) renamed by renames,
    zstd-compressed as the game's was."""
    path = f'Pack/Actor/{actor}.pack.zs'
    data, d = fs.zs(path)
    count = 0

    def walk(v):
        nonlocal count
        if isinstance(v, dict):
            for k, x in v.items():
                if k == 'ModelProjectName' and isinstance(x, tuple) and x[0] == randomizer.T_STR and x[1] in renames:
                    v[k] = (randomizer.T_STR, renames[x[1]])
                    count += 1
                else:
                    walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    for n, body, _ in sarc_members(data):
        if n.startswith('Component/ModelInfo/') and n.endswith('.bgyml'):
            ver, root = randomizer.byml_read(body)
            before = count
            walk(root)
            if count > before:
                data = sarc_replace(data, n, randomizer.byml_write(ver, root))
    if not count:
        raise ValueError(f'{path}: no ModelProjectName to rename')
    return path, fs.compress(data, d)


def dark_project(fs, dk, project, files, label):
    """The dark copies of a model project's models (into dk) and animations (into files) under dark_name(project)."""
    dark = dark_name(project)
    if any(p.startswith(f'Model/{dark}.') or p.startswith(f'Model/{dark}_') for p in fs.files):
        raise ValueError(f'the game already has models of the project {dark}')
    for p in sorted(fs.files):
        if p.startswith(f'Model/{project}.') and p.endswith('.bfres.zs') and '.anim.' not in p:
            fmdb = p[len(f'Model/{project}.'):-len('.bfres.zs')]
            dk.model(p[6:], f'{dark}.{fmdb}.bfres.zs', {f'{project}.{fmdb}': f'{dark}.{fmdb}'}, label)
        elif p == f'Model/{project}.anim.bfres.zs':
            files[f'Model/{dark}.anim.bfres.zs'] = fs.raw(p)
        elif p.startswith(f'Model/{project}_') and p.endswith('.anim.bfres.zs'):
            files[f'Model/{dark}_{p[len(f"Model/{project}_"):]}'] = fs.raw(p)


def dark_gear(fs, log):
    """{RomFS path: file}: the child's model naming dark textures, the dark copies of every piece of the player's gear
    under new projects with the packs that take them, and the dark textures."""
    dk = Darkener(fs, log)
    dk.model(f'{CHILD}.{CHILD}.bfres.zs', f'{CHILD}.{CHILD}.bfres.zs', {}, CHILD)
    files, done = {}, set()
    for actor in gear_actors(fs):
        projects = [p for p in info_projects(fs, actor)
                    if any(f.startswith(f'Model/{p}.') and f.endswith('.bfres.zs') for f in fs.files)]
        if not projects:
            log(f'{actor}: no model of its own, kept')
            continue
        for project in projects:
            if project not in done:
                done.add(project)
                dark_project(fs, dk, project, files, actor)
        path, data = project_pack(fs, actor, {p: dark_name(p) for p in projects})
        files[path] = data
        log(f'{actor}: project ' + ', '.join(dark_name(p) for p in projects))
    files.update(dk.files)
    return files


def make(game, out=None, log=print, msys=None, work=None):
    """Write the mod into out (default <game>/mods/Dark Link). msys: an MSYS2 UCRT64 folder whose clang or gcc builds
    the ASTC decoder; work: the folder it is built in (AstcDecoder)."""
    AstcDecoder.msys, AstcDecoder.work = msys, work
    fs = n64voice.RomFS(Path(game) / 'romfs')
    for actor, model in MODELS.items():
        if f'Model/{model}.{model}.bfres' not in fs.files and f'Model/{model}.{model}.bfres.zs' not in fs.files:
            raise SystemExit(f'the game has no Model/{model}.{model}.bfres')
    mod = Path(out) if out else Path(game) / 'mods' / MOD_NAME
    files = {}
    for actor, model in MODELS.items():
        path, data = dark_pack(fs, actor, model)
        files[path] = data
        log(f'{actor}: {model}')
    files.update(dark_gear(fs, log))
    for actor in LENDERS:   # after the gear: a lender's pack may already name a dark project
        path = f'Pack/Actor/{actor}.pack.zs'
        files[path] = elink_pack(fs, files.get(path), path, DARK_USER)
        log(f'{actor}: effect user {DARK_USER}')
    if (mod / 'romfs').is_dir():   # the files of an earlier run go: the mod is this run's files alone
        shutil.rmtree(mod / 'romfs')
    for path, data in files.items():
        dest = mod / 'romfs' / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    (mod / 'ootpc-feature.txt').write_text(FEATURE + '\n', encoding='utf-8')
    (mod / 'README.txt').write_text(README, encoding='utf-8')
    log(f'{mod}')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--game', required=True, help='the game folder (its romfs folder is read, its mods folder written)')
    ap.add_argument('--out', help='the mod folder to write (default: <game>/mods/Dark Link)')
    a = ap.parse_args()
    make(a.game, a.out)


if __name__ == '__main__':
    main()
