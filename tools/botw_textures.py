"""Textures for tools/make_botw_enemies_mod.py: Breath of the Wild's textures (a BNTX inside a .Tex.sbfres) made into
this game's (one BNTX 4.1.1 per Tex/<name>.bntx.zs, "Ounc" platform block).

The block-linear layout is the same in both but for the order of the 16-byte pieces inside a 512-byte GOB
(runtime/tex_deswizzle.c gob_off), so colour textures keep their blocks and only have each GOB reordered. This game's
normal maps hold X in red, green and blue and Y in alpha; a BotW BC1 normal map (X red, Y green) is decoded and written
as BC3 that way. A new texture file is one of the game's files of the same slot with the name, format, size and
levels changed.
"""
import struct

import make_dark_link_mod as darklink   # rename_strings, one_entry_dict
from botw_bfres import Rec, align, sname, u16, u32, u64, yaz0

# BNTX formats (the second byte of the format word): bytes per block and block size
FORMATS = {0x0b: (4, 1, 1), 0x1a: (8, 4, 4), 0x1b: (16, 4, 4), 0x1c: (16, 4, 4), 0x1d: (8, 4, 4), 0x1e: (16, 4, 4),
           0x20: (16, 4, 4)}
BC1, BC3, BC4, BC5 = 0x1a, 0x1c, 0x1d, 0x1e


def pick_tile(gob_rows):
    return 4 if gob_rows >= 16 else 3 if gob_rows >= 8 else 2 if gob_rows >= 4 else 1 if gob_rows >= 2 else 0


def adj_tile(shift, rows):
    """A level's block height (log2 GOBs) from the texture's: halved while half of it still covers the level."""
    if not shift:
        return 0
    x = 8 << (shift - 1)
    if x >= rows:
        while shift:
            shift -= 1
            if not shift:
                break
            x >>= 1
            if x < rows:
                break
    return shift


def levels(w, h, kind, mips, th):
    """[(offset, blocks wide, blocks high, block height)] of a block-linear texture's levels, and the total size."""
    bpb, bw, bh = FORMATS[kind]
    out, off = [], 0
    for i in range(mips):
        lw, lh = -(-max(w >> i, 1) // bw), -(-max(h >> i, 1) // bh)
        t = adj_tile(th, lh)
        gx, gy = -(-(lw * bpb) // 64), -(-lh // 8)
        out.append((off, lw, lh, t))
        off += gx * ((gy + (1 << t) - 1) >> t) << (9 + t)
    return out, off


def gob_x1(x, y):
    return (x % 64 // 32) * 256 + (y % 8 // 2) * 64 + (x % 32 // 16) * 32 + (y % 2) * 16 + x % 16


def gob_nvn2(x, y):
    """runtime/tex_deswizzle.c gob_off for Switch 2 titles."""
    return (x % 64 // 32) * 256 + (y % 8 // 4) * 128 + (x % 32 // 16) * 64 + (y % 4) * 16 + x % 16


def bl_offset(x, y, row_bytes, th, gob):
    bh = 1 << th
    gobs_x = (row_bytes + 63) // 64
    block = 512 * bh
    return (y // (8 * bh)) * gobs_x * block + (x // 64) * block + (y % (8 * bh)) // 8 * 512 + gob(x, y)


# a GOB's 16-byte pieces: where the piece at a Switch layout's place goes in this game's layout
GOB_ORDER = [0] * 32
for _y in range(8):
    for _x in range(0, 64, 16):
        GOB_ORDER[gob_x1(_x, _y) // 16] = gob_nvn2(_x, _y) // 16


def regob(data):
    """Block-linear data in the Switch GOB order turned into this game's: each GOB's pieces moved, nothing else."""
    out = bytearray(len(data))
    for g in range(0, len(data) - 511, 512):
        for k in range(32):
            d = g + GOB_ORDER[k] * 16
            out[d:d + 16] = data[g + 16 * k:g + 16 * k + 16]
    return bytes(out)


def read_level(data, base, lw, lh, bpb, th, gob):
    """[block bytes] of a level in row order."""
    row = lw * bpb
    out = []
    for y in range(lh):
        for x in range(lw):
            a = base + bl_offset(x * bpb, y, row, th, gob)
            out.append(data[a:a + bpb])
    return out


def write_level(out, base, blocks, lw, lh, bpb, th, gob):
    row = lw * bpb
    for y in range(lh):
        for x in range(lw):
            a = base + bl_offset(x * bpb, y, row, th, gob)
            out[a:a + bpb] = blocks[y * lw + x]


def bc1_colours(block):
    c0, c1 = struct.unpack_from('<HH', block)
    cols = [((c >> 11 & 31) * 255 // 31, (c >> 5 & 63) * 255 // 63, (c & 31) * 255 // 31) for c in (c0, c1)]
    if c0 > c1:
        cols += [tuple((2 * a + b) // 3 for a, b in zip(*cols)), tuple((a + 2 * b) // 3 for a, b in zip(*cols))]
    else:
        cols += [tuple((a + b) // 2 for a, b in zip(*cols)), (0, 0, 0)]
    bits = u32(block, 4)
    return [cols[bits >> (2 * i) & 3] for i in range(16)]


def bc4_block(vals):
    """A BC4 block of 16 values (eight-value mode between their least and greatest)."""
    lo, hi = min(vals), max(vals)
    if lo == hi:
        return bytes([hi, lo]) + bytes(6)
    pal = [hi, lo] + [((7 - i) * hi + i * lo) // 7 for i in range(1, 7)]
    bits = 0
    for k, v in enumerate(vals):
        bits |= min(range(8), key=lambda i: abs(pal[i] - v)) << (3 * k)
    return bytes([hi, lo]) + bits.to_bytes(6, 'little')


def bc1_grey_block(vals):
    """A BC1 block of 16 grey values (four-colour mode between their least and greatest)."""
    lo, hi = min(vals), max(vals)

    def c565(g):
        return (g * 31 + 127) // 255 << 11 | (g * 63 + 127) // 255 << 5 | (g * 31 + 127) // 255
    c0, c1 = c565(hi), c565(lo)
    if c0 == c1:
        return struct.pack('<HHI', c0, c1, 0)
    if c0 < c1:
        c0, c1, hi, lo = c1, c0, lo, hi
    pal = [hi, lo, (2 * hi + lo) // 3, (hi + 2 * lo) // 3]
    bits = 0
    for k, v in enumerate(vals):
        bits |= min(range(4), key=lambda i: abs(pal[i] - v)) << (2 * k)
    return struct.pack('<HHI', c0, c1, bits)


def bc4_values(block):
    """The 16 values of a BC4 block (unsigned)."""
    a, b = block[0], block[1]
    if a > b:
        pal = [a, b] + [((7 - i) * a + i * b) // 7 for i in range(1, 7)]
    else:
        pal = [a, b] + [((5 - i) * a + i * b) // 5 for i in range(1, 5)] + [0, 255]
    bits = int.from_bytes(block[2:8], 'little')
    return [pal[bits >> (3 * i) & 7] for i in range(16)]


def normal_block_bc5(bc5):
    """A BC3 block holding a BC5 normal map block (X in the first half, Y in the second) in this game's layout."""
    return bc4_block(bc4_values(bc5[8:16])) + bc1_grey_block(bc4_values(bc5[0:8]))


def normal_block(bc1):
    """A BC3 block holding a BC1 normal map block in this game's layout: X (red) in the colour as grey, Y (green) in
    the alpha."""
    px = bc1_colours(bc1)
    return bc4_block([p[1] for p in px]) + bc1_grey_block([p[0] for p in px])


class BotwTextures:
    """The BNTX of a BotW .Tex.sbfres (its one external file): textures by name."""

    def __init__(self, data):
        b = yaz0(data)
        at = b.find(b'BNTX')
        if at < 0:
            raise ValueError('no BNTX in the texture file')
        self.b = b[at:]
        x = self.b
        self.tex = {}
        for i in range(u32(x, 0x24)):
            t = u64(x, u64(x, 0x28) + 8 * i)
            r = Rec()
            r.fmt, (r.w, r.h), r.mips = u32(x, t + 0x1c), struct.unpack_from('<2I', x, t + 0x24), u16(x, t + 0x16)
            r.th, r.size, r.chan = x[t + 0x34], u32(x, t + 0x50), x[t + 0x58:t + 0x5c]
            r.data_at = u64(x, u64(x, t + 0x70))
            self.tex[sname(x, u64(x, t + 0x60))] = r

    def data(self, r):
        return self.b[r.data_at:r.data_at + r.size]


def game_bntx(template, name, fmt, w, h, mips, th, chan, data):
    """A BNTX file of this game holding one texture: the template file (one of the game's, one texture) renamed to
    name, with the format, size, levels and data given. Layout kept from the template: header, platform block, the
    string table and name dictionary, the BRTI after them with its level offsets, the BRTD at 0xff0 with the data at
    0x1000, then the relocation table."""
    t = template
    brti = u64(t, 0x198)   # after the string table, so 0x200 or 0x208 with the name's length
    if t[:4] != b'BNTX' or u32(t, 0x24) != 1 or brti not in (0x200, 0x208) or t[0xff0:0xff4] != b'BRTD':
        raise ValueError('the template texture file is not laid out as expected')
    old = sname(t, u64(t, brti + 0x60))
    head = bytearray(darklink.one_entry_dict(darklink.rename_strings(t[:0x1000], {old: name}, name), name, name))
    struct.pack_into('<H', head, brti + 0x16, mips)
    struct.pack_into('<I', head, brti + 0x1c, fmt)
    struct.pack_into('<3I', head, brti + 0x24, w, h, 1)
    struct.pack_into('<I', head, brti + 0x34, th)
    struct.pack_into('<I', head, brti + 0x50, len(data))
    head[brti + 0x58:brti + 0x5c] = chan
    lv, total = levels(w, h, fmt >> 8, mips, th)
    if total != len(data):
        raise ValueError(f'{name}: {len(data)} bytes of data for {total} bytes of levels')
    mip_arr = u64(head, brti + 0x70)
    for k in range(16):
        struct.pack_into('<Q', head, mip_arr + 8 * k, 0x1000 + lv[k][0] if k < mips else 0)
    body = align(len(data), 0x1000)
    struct.pack_into('<Q', head, 0xff8, 0x10 + body)
    rlt = 0x1000 + body
    # the relocation table: the template's, with the data section's size and the level pointers' count
    trlt = u32(t, 0x18)
    nsec = u32(t, trlt + 8)
    tab = bytearray(t[trlt:u32(t, 0x1c)])
    struct.pack_into('<I', tab, 4, rlt)
    ents = 0x10 + 0x18 * nsec
    for s in range(nsec):
        pos, _size, first, cnt = struct.unpack_from('<4I', tab, 0x10 + 0x18 * s + 8)
        if pos == 0xff0:
            struct.pack_into('<I', tab, 0x10 + 0x18 * s + 0xc, 0x10 + body)
        for e in range(first, first + cnt):
            if u32(tab, ents + 8 * e) == mip_arr:
                tab[ents + 8 * e + 6] = mips
    out = bytes(head) + data + bytes(body - len(data)) + bytes(tab)
    out = bytearray(out)
    struct.pack_into('<II', out, 0x18, rlt, len(out))
    return bytes(out)


def convert_albedo(src, r):
    """(fmt, mips, th, chan, data) of a BotW colour texture for this game: the same blocks in this game's GOB order."""
    if (r.fmt >> 8) not in FORMATS:
        raise ValueError(f'texture format {r.fmt:#x}')
    _, total = levels(r.w, r.h, r.fmt >> 8, r.mips, r.th)
    data = src.data(r)
    if total != len(data):
        raise ValueError(f'texture size {len(data)} is not the {total} its levels take')
    return r.fmt, r.mips, r.th, r.chan, regob(data)


def convert_normal(src, r):
    """A BotW BC1 or BC5 normal map as a BC3 one in this game's layout (X in red, green, blue; Y in alpha)."""
    kind = r.fmt >> 8
    if kind not in (BC1, BC5):
        raise ValueError(f'normal map format {r.fmt:#x}, BC1 or BC5 expected')
    bpb, conv = (8, normal_block) if kind == BC1 else (16, normal_block_bc5)
    data = src.data(r)
    lin, _ = levels(r.w, r.h, kind, r.mips, r.th)
    th = pick_tile((-(-r.h // 4) * 3 // 2) // 8)
    lout, total = levels(r.w, r.h, BC3, r.mips, th)
    out = bytearray(total)
    for (off, lw, lh, t), (off2, _, _, t2) in zip(lin, lout):
        blocks = [conv(b) for b in read_level(data, off, lw, lh, bpb, t, gob_x1)]
        write_level(out, off2, blocks, lw, lh, 16, t2, gob_nvn2)
    return BC3 << 8 | 1, r.mips, th, bytes([2, 3, 4, 5]), bytes(out)


def atlas_texture(w, h, normal, cells):
    """(fmt, mips, th, chan, data) of one texture w x h holding several BotW textures, each in a cell of its own:
    cells [(x, y, cell width, cell height, across, down, textures, texture)] in pixels, the texture repeated across x
    down times to fill its cell (a shape whose UVs run over several repeats of its texture keeps them in the cell).
    Each cell takes the source level whose size is one repeat's. Colour textures must all be of one format and are
    kept as blocks; normal maps become BC3 as convert_normal makes them. Levels go down while every repeat is still
    four pixels or more; what no cell covers is zero."""
    kinds = {r.fmt >> 8 for *_, r in cells}
    if not normal and len(kinds) != 1:
        raise ValueError(f'colour textures of several formats in one atlas: {sorted(kinds)}')
    first = cells[0][7]
    kind_out = BC3 if normal else first.fmt >> 8
    fmt = BC3 << 8 | 1 if normal else first.fmt
    chan = bytes([2, 3, 4, 5]) if normal else first.chan
    bpb_out = FORMATS[kind_out][0]
    placed = []
    mips = 16
    for x, y, cw, ch, nu, nv, src, r in cells:
        tw, tt = cw // nu, ch // nv
        if tw * nu != cw or tt * nv != ch or x % 4 or y % 4:
            raise ValueError(f'a cell {cw}x{ch} at {x},{y} does not hold {nu}x{nv} whole repeats on the block grid')
        k = next((k for k in range(r.mips) if r.w >> k == tw and max(r.h >> k, 1) == tt), None)
        if k is None:
            raise ValueError(f'no level of a {r.w}x{r.h} texture is {tw}x{tt}')
        mips = min(mips, r.mips - k, min(tw, tt).bit_length() - 2)
        kind = r.fmt >> 8
        conv = None if not normal else normal_block if kind == BC1 else normal_block_bc5 if kind == BC5 else 'bad'
        if conv == 'bad':
            raise ValueError(f'normal map format {r.fmt:#x}, BC1 or BC5 expected')
        placed.append((x, y, nu, nv, k, src, r, conv))
    th = pick_tile((-(-h // 4) * 3 // 2) // 8)
    lout, total = levels(w, h, kind_out, mips, th)
    out = bytearray(total)
    for lv, (off, lw, lh, t) in enumerate(lout):
        grid = [bytes(bpb_out)] * (lw * lh)
        for x, y, nu, nv, k, src, r, conv in placed:
            kind = r.fmt >> 8
            bpb = FORMATS[kind][0]
            lin, _ = levels(r.w, r.h, kind, r.mips, r.th)
            soff, slw, slh, st = lin[k + lv]
            blocks = read_level(src.data(r), soff, slw, slh, bpb, st, gob_x1)
            if conv:
                blocks = [conv(b) for b in blocks]
            bx, by = (x >> lv) // 4, (y >> lv) // 4
            for j in range(nv):
                for i in range(nu):
                    for sy in range(slh):
                        row = (by + j * slh + sy) * lw + bx + i * slw
                        grid[row:row + slw] = blocks[sy * slw:(sy + 1) * slw]
        write_level(out, off, grid, lw, lh, bpb_out, t, gob_nvn2)
    return fmt, mips, th, chan, bytes(out)


def constant_texture(value):
    """A 64x64 texture of one level holding one value (BC4, red) or colour (BC1)."""
    if isinstance(value, tuple):
        c = (value[0] * 31 + 127) // 255 << 11 | (value[1] * 63 + 127) // 255 << 5 | (value[2] * 31 + 127) // 255
        block, kind, chan, fmt = struct.pack('<HHI', c, c, 0), BC1, bytes([2, 3, 4, 5]), BC1 << 8 | 6
    else:
        block, kind, chan, fmt = bytes([value, value]) + bytes(6), BC4, bytes([2, 2, 2, 2]), BC4 << 8 | 1
    th = pick_tile(16 * 3 // 2 // 8)
    _, total = levels(64, 64, kind, 1, th)
    return fmt, 1, th, chan, block * (total // len(block))
