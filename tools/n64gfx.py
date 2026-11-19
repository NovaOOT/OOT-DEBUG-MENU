"""N64 graphics of Ocarina of Time read into meshes the runtime draws (runtime/n64gfx.c): F3DEX2 display lists, the
RDP's texture state and the texture formats.

A display list is a list of 8-byte commands for the RSP (vertices, triangles, matrices) and the RDP (textures, colour
combiner, render mode). Walk() runs one the way the hardware would and records, for every triangle, its three
vertices and the RDP state it was drawn with (a Material). What the game sets while it runs (the segments 0x08 to 0x0F
it points at per frame, the lights, the matrices of a moving actor) is not in the data: a call or a texture in such a
segment is noted and left out, and a G_MTX of segment 0x0D names the limb of a skinned model whose matrix it is.

Textures: a G_SETTIMG names an image in memory, a load (G_LOADBLOCK, G_LOADTILE, G_LOADTLUT) copies part of it into
TMEM, and a tile (G_SETTILE, G_SETTILESIZE) says how a draw reads TMEM. The reader keeps which image each TMEM address
was loaded from, so a tile's texture is decoded from the image in memory: RGBA 16 and 32 bit, CI 4 and 8 bit (with the
palette loaded at TMEM 0x100 + 16 * the tile's palette, RGBA 5551 or IA 16), IA 4, 8 and 16 bit, I 4 and 8 bit.
Decoded textures are RGBA8; their addressing (clamp, mirror, mask, shift, the tile's origin) stays with the material
and the runtime's shader applies it, as the RDP does, with its 3-point filter.

Standard library only.
"""
import struct

# texture formats and sizes (G_SETTIMG, G_SETTILE)
FMT_RGBA, FMT_YUV, FMT_CI, FMT_IA, FMT_I = range(5)
SIZ_4, SIZ_8, SIZ_16, SIZ_32 = range(4)
BITS = (4, 8, 16, 32)

# geometry mode bits (F3DEX2)
G_ZBUFFER = 0x00000001
G_SHADE = 0x00000004
G_CULL_FRONT = 0x00000200
G_CULL_BACK = 0x00000400
G_FOG = 0x00010000
G_LIGHTING = 0x00020000
G_TEXTURE_GEN = 0x00040000
G_TEXTURE_GEN_LINEAR = 0x00080000
G_SHADING_SMOOTH = 0x00200000

DYNAMIC_SEGMENTS = range(0x08, 0x10)   # pointed at by the game's code per frame; 0x0D: the limb matrices


class Texture:
    """A decoded texture: w x h RGBA8 texels, top row first."""
    __slots__ = ('w', 'h', 'rgba', 'key')

    def __init__(self, w, h, rgba, key):
        self.w, self.h, self.rgba, self.key = w, h, rgba, key


class TileUse:
    """How a material reads one texture: the RDP tile's addressing."""
    __slots__ = ('tex', 'cms', 'cmt', 'masks', 'maskt', 'shifts', 'shiftt', 'uls', 'ult', 'lrs', 'lrt')

    def key(self):
        return (self.tex.key if self.tex else None, self.cms, self.cmt, self.masks, self.maskt, self.shifts,
                self.shiftt, self.uls, self.ult, self.lrs, self.lrt)


class Material:
    __slots__ = ('combine', 'prim', 'prim_lod', 'env', 'fog', 'othermode_h', 'othermode_l', 'geometry', 'tex',
                 'tex_scale', 'tex_on', 'missing', 'tunic')

    def key(self):
        return (self.combine, self.prim, self.prim_lod, self.env, self.othermode_h, self.othermode_l, self.geometry,
                tuple(t.key() if t else None for t in self.tex), self.tex_scale, self.tex_on, self.tunic)


def _rgba5551(v):
    r, g, b = (v >> 11) & 31, (v >> 6) & 31, (v >> 1) & 31
    return (r << 3 | r >> 2, g << 3 | g >> 2, b << 3 | b >> 2, 255 if v & 1 else 0)


_RGBA5551 = [_rgba5551(v) for v in range(65536)]


def decode(data, off, fmt, siz, w, h, stride, palette=None, tlut_ia=False):
    """w x h texels of the image at data[off:] (rows stride bytes apart) as RGBA8 bytes; None when out of the data."""
    bits = BITS[siz]
    need = off + stride * (h - 1) + (w * bits + 7) // 8
    if off < 0 or need > len(data):
        return None
    out = bytearray(w * h * 4)
    o = 0
    for y in range(h):
        row = off + y * stride
        for x in range(w):
            if bits == 4:
                b = data[row + (x >> 1)]
                v = (b >> 4) if not x & 1 else (b & 15)
            elif bits == 8:
                v = data[row + x]
            elif bits == 16:
                v = data[row + 2 * x] << 8 | data[row + 2 * x + 1]
            else:
                p = row + 4 * x
                out[o:o + 4] = data[p:p + 4]
                o += 4
                continue
            if fmt == FMT_CI:
                c = palette[v] if palette and v < len(palette) else (255, 0, 255, 255)
            elif fmt == FMT_RGBA and bits == 16:
                c = _RGBA5551[v]
            elif fmt == FMT_IA:
                if bits == 4:
                    i = (v >> 1) * 255 // 7
                    c = (i, i, i, 255 if v & 1 else 0)
                elif bits == 8:
                    i = (v >> 4) * 17
                    c = (i, i, i, (v & 15) * 17)
                else:
                    c = (v >> 8, v >> 8, v >> 8, v & 255)
            elif fmt == FMT_I:
                i = v * 17 if bits == 4 else v
                c = (i, i, i, i)
            else:   # YUV and odd pairs: shown as intensity
                i = v & 255
                c = (i, i, i, 255)
            out[o] = c[0]
            out[o + 1] = c[1]
            out[o + 2] = c[2]
            out[o + 3] = c[3]
            o += 4
    return bytes(out)


class Segments:
    """Segment number -> the file it points at. Addresses are 0xSSoooooo with S the segment."""

    def __init__(self, **files):
        self.seg = {}
        for k, v in files.items():
            self.seg[int(k[1:], 16)] = v

    def set(self, n, data):
        self.seg[n] = data

    def at(self, addr):
        """(data, offset) of a segmented address; None when its segment is not known or is dynamic."""
        s = (addr >> 24) & 0xFF
        if s & 0x80:   # a RAM address (0x80......): not in the data
            return None
        d = self.seg.get(s)
        if d is None:
            return None
        o = addr & 0xFFFFFF
        return (d, o) if o < len(d) else None


class Mesh:
    """What walk() records: vertices (x, y, z, s, t, r, g, b, a, bone) with r, g, b the normal when the material lights,
    and triangles as (material index, i0, i1, i2). parts: (limb, first triangle, end triangle) per limb when a skeleton
    was walked (n64obj.skeleton_mesh)."""

    def __init__(self):
        self.parts = []
        self.verts = []
        self.tris = []
        self.materials = []
        self._mat_index = {}
        self.textures = {}     # key -> Texture
        self.missing = {}      # what could not be read: reason -> count

    def material(self, m):
        k = m.key()
        i = self._mat_index.get(k)
        if i is None:
            i = self._mat_index[k] = len(self.materials)
            self.materials.append(m)
        return i

    def miss(self, why):
        self.missing[why] = self.missing.get(why, 0) + 1


class _Tile:
    __slots__ = ('fmt', 'siz', 'line', 'tmem', 'pal', 'cmt', 'maskt', 'shiftt', 'cms', 'masks', 'shifts',
                 'uls', 'ult', 'lrs', 'lrt')

    def __init__(self):
        for k in self.__slots__:
            setattr(self, k, 0)


class Walker:
    """Runs display lists into a Mesh. One per model: its state (render mode, combiner, textures) carries from one
    display list to the next, as the game's own state does between the draws of one model."""

    MAX_DEPTH = 18

    def __init__(self, segments, mesh=None):
        self.segs = segments
        self.mesh = mesh or Mesh()
        self.vbuf = [None] * 64
        self.geometry = G_ZBUFFER | G_SHADE | G_CULL_BACK | G_SHADING_SMOOTH
        # The game's setup before a room or an actor draws: two cycles, filtered and perspective-correct textures
        # (G_CYC_2CYCLE, G_TP_PERSP, G_TF_BILERP, G_TC_FILT); the lists themselves set the rest.
        self.othermode_h = 1 << 20 | 1 << 19 | 2 << 12 | 6 << 9
        self.othermode_l = 0
        self.combine = 0xFC127E24FFFFF9FC   # G_CC_SHADE, G_CC_SHADE
        self.prim = (255, 255, 255, 255)
        self.prim_lod = 0
        self.env = (255, 255, 255, 255)
        self.env_tunic = False   # env is still the colour the caller set for the player's tunic (n64obj.skeleton_mesh)
        self.fog = (0, 0, 0, 0)
        self.tex_on = 0
        self.tex_tile = 0
        self.tex_scale = (1.0, 1.0)
        self.timg = (0, 0, 0, 0)   # fmt, siz, width, address
        self.tiles = [_Tile() for _ in range(8)]
        self.tmem = {}   # TMEM word address -> (image address, fmt, siz, width, uls, ult) the load came from
        self.tlut = {}   # TMEM address -> palette (list of 256 RGBA) loaded there
        self.bone = 0    # the limb whose matrix the vertices are loaded with
        self.mtx_limb = None

    # ---- textures

    def _texture(self, t):
        """The texture tile t reads, decoded; None when its image is not known."""
        src = self.tmem.get(t.tmem)
        if src is None:
            # the image may have been loaded to a lower address that covers this one (two textures in one load)
            return None
        addr, lfmt, lsiz, lwidth, luls, lult, kind = src
        w = ((t.lrs - t.uls) >> 2) + 1
        h = ((t.lrt - t.ult) >> 2) + 1
        if w <= 0 or h <= 0 or w > 1024 or h > 1024:
            return None
        # a masked tile repeats 2^mask texels: that is the image; a clamped one reads w x h
        if t.masks and w > (1 << t.masks):
            w = 1 << t.masks
        if t.maskt and h > (1 << t.maskt):
            h = 1 << t.maskt
        at = self.segs.at(addr)
        if at is None:
            self.mesh.miss('texture in segment %02X' % ((addr >> 24) & 0xFF))
            return None
        data, off = at
        bits = BITS[t.siz]
        if kind == 'block':
            # a block load copies memory in order, so a TMEM row of the tile (line 64-bit words) is that many bytes
            # of the image too
            stride = t.line * 8 if t.line else max(1, (w * bits + 7) // 8)
        else:   # a tile load: the image's rows are the G_SETTIMG width apart, from the load's origin
            stride = (lwidth * BITS[lsiz] + 7) // 8
            off += (luls >> 2) * BITS[lsiz] // 8 + (lult >> 2) * stride
        palette = None
        if t.fmt == FMT_CI:
            tl = (self.othermode_h >> 14) & 3   # G_MDSFT_TEXTLUT: 2 RGBA16, 3 IA16
            base = 0x100 + (t.pal << 4 if t.siz == SIZ_4 else 0)
            pal = self.tlut.get(base)
            if pal is None:
                pal = self.tlut.get(0x100)
            if pal is not None:
                palette = pal[1] if tl == 3 else pal[0]
                if t.siz == SIZ_4 and base != 0x100 and self.tlut.get(base) is None:
                    palette = palette[t.pal * 16:]
        key = (addr, t.fmt, t.siz, w, h, stride, id(palette) if palette else 0,
               tuple(palette[:4]) if palette else None)
        tex = self.mesh.textures.get(key)
        if tex is None:
            rgba = decode(data, off, t.fmt, t.siz, w, h, stride, palette)
            if rgba is None:
                self.mesh.miss('texture out of its file')
                return None
            tex = self.mesh.textures[key] = Texture(w, h, rgba, len(self.mesh.textures))
        return tex

    def _tile_use(self, n):
        t = self.tiles[n]
        u = TileUse()
        u.tex = self._texture(t)
        u.cms, u.cmt, u.masks, u.maskt = t.cms, t.cmt, t.masks, t.maskt
        u.shifts, u.shiftt, u.uls, u.ult, u.lrs, u.lrt = t.shifts, t.shiftt, t.uls, t.ult, t.lrs, t.lrt
        return u

    def _material(self):
        m = Material()
        m.combine, m.prim, m.prim_lod, m.env, m.fog = self.combine, self.prim, self.prim_lod, self.env, self.fog
        m.othermode_h, m.othermode_l, m.geometry = self.othermode_h, self.othermode_l, self.geometry
        m.tex_on, m.tex_scale = self.tex_on, self.tex_scale
        m.tunic = self.env_tunic
        if self.tex_on:
            m.tex = (self._tile_use(self.tex_tile), self._tile_use((self.tex_tile + 1) & 7))
        else:
            m.tex = (None, None)
        m.missing = False
        return m

    # ---- the walk

    def walk(self, addr, depth=0):
        """Runs the display list at the segmented address addr."""
        if depth > self.MAX_DEPTH:
            self.mesh.miss('display lists nested too deep')
            return
        seg = (addr >> 24) & 0xFF
        if seg in DYNAMIC_SEGMENTS and seg not in self.segs.seg:   # unless the caller set it (n64link.segments)
            self.mesh.miss('display list in segment %02X' % seg)
            return
        at = self.segs.at(addr)
        if at is None:
            self.mesh.miss('display list not in the data')
            return
        data, off = at
        cur_mat = None
        while off + 8 <= len(data):
            w0, w1 = struct.unpack_from('>II', data, off)
            off += 8
            op = w0 >> 24
            if op == 0x01:   # G_VTX: n vertices into the buffer ending at slot v0 + n
                n = (w0 >> 12) & 0xFF
                end = (w0 & 0xFF) >> 1
                self._vertices(w1, n, end - n)
            elif op == 0x05:   # G_TRI1
                cur_mat = self._tri(((w0 >> 16) & 0xFF) >> 1, ((w0 >> 8) & 0xFF) >> 1, (w0 & 0xFF) >> 1, cur_mat)
            elif op == 0x06:   # G_TRI2
                cur_mat = self._tri(((w0 >> 16) & 0xFF) >> 1, ((w0 >> 8) & 0xFF) >> 1, (w0 & 0xFF) >> 1, cur_mat)
                cur_mat = self._tri(((w1 >> 16) & 0xFF) >> 1, ((w1 >> 8) & 0xFF) >> 1, (w1 & 0xFF) >> 1, cur_mat)
            elif op == 0x07:   # G_QUAD
                a, b, c, d = ((w0 >> 16) & 0xFF) >> 1, ((w0 >> 8) & 0xFF) >> 1, (w0 & 0xFF) >> 1, ((w1 >> 16) & 0xFF) >> 1
                cur_mat = self._tri(a, b, c, cur_mat)
                cur_mat = self._tri(a, c, d, cur_mat)
            elif op == 0xDE:   # G_DL: a call, or a jump when the push flag is clear
                self.walk(w1, depth + 1)
                cur_mat = None
                if (w0 >> 16) & 0xFF:   # G_DL_NOPUSH: this list ends here
                    return
            elif op == 0xDF:   # G_ENDDL
                return
            elif op == 0xDA:   # G_MTX: a limb's matrix (segment 0x0D, 0x40 bytes each)
                if (w1 >> 24) == 0x0D:
                    self.bone = (w1 & 0xFFFFFF) // 0x40
                    self.mtx_limb = self.bone
            elif op == 0xD8:   # G_POPMTX
                pass
            elif op == 0xD9:   # G_GEOMETRYMODE: clear, then set
                self.geometry = (self.geometry & (w0 & 0xFFFFFF)) | w1
                cur_mat = None
            elif op == 0xD7:   # G_TEXTURE
                self.tex_tile = (w0 >> 8) & 7
                self.tex_on = (w0 >> 1) & 0x7F
                sc_s, sc_t = (w1 >> 16) & 0xFFFF, w1 & 0xFFFF
                self.tex_scale = ((sc_s or 0x10000) / 65536.0 if sc_s != 0xFFFF else 1.0,
                                  (sc_t or 0x10000) / 65536.0 if sc_t != 0xFFFF else 1.0)
                cur_mat = None
            elif op == 0xE2 or op == 0xE3:   # G_SETOTHERMODE_L / _H: bits sft .. sft + len
                length = (w0 & 0xFF) + 1
                sft = 32 - ((w0 >> 8) & 0xFF) - length
                if sft < 0:   # not a command the hardware would take: no display list after all
                    self.mesh.miss('bad othermode command')
                    return
                mask = ((1 << length) - 1) << sft
                if op == 0xE2:
                    self.othermode_l = (self.othermode_l & ~mask) | (w1 & mask)
                else:
                    self.othermode_h = (self.othermode_h & ~mask) | (w1 & mask)
                cur_mat = None
            elif op == 0xEF:   # G_RDPSETOTHERMODE: both words at once
                self.othermode_h = w0 & 0xFFFFFF
                self.othermode_l = w1
                cur_mat = None
            elif op == 0xFC:   # G_SETCOMBINE
                self.combine = (w0 << 32 | w1) & 0xFFFFFFFFFFFFFFFF
                cur_mat = None
            elif op == 0xFA:   # G_SETPRIMCOLOR
                self.prim = ((w1 >> 24) & 255, (w1 >> 16) & 255, (w1 >> 8) & 255, w1 & 255)
                self.prim_lod = w0 & 0xFF
                cur_mat = None
            elif op == 0xFB:   # G_SETENVCOLOR
                self.env = ((w1 >> 24) & 255, (w1 >> 16) & 255, (w1 >> 8) & 255, w1 & 255)
                self.env_tunic = False
                cur_mat = None
            elif op == 0xF8:   # G_SETFOGCOLOR
                self.fog = ((w1 >> 24) & 255, (w1 >> 16) & 255, (w1 >> 8) & 255, w1 & 255)
            elif op == 0xFD:   # G_SETTIMG
                self.timg = ((w0 >> 21) & 7, (w0 >> 19) & 3, (w0 & 0xFFF) + 1, w1)
            elif op == 0xF5:   # G_SETTILE
                t = self.tiles[(w1 >> 24) & 7]
                t.fmt, t.siz = (w0 >> 21) & 7, (w0 >> 19) & 3
                t.line, t.tmem = (w0 >> 9) & 0x1FF, w0 & 0x1FF
                t.pal, t.cmt, t.maskt, t.shiftt = (w1 >> 20) & 15, (w1 >> 18) & 3, (w1 >> 14) & 15, (w1 >> 10) & 15
                t.cms, t.masks, t.shifts = (w1 >> 8) & 3, (w1 >> 4) & 15, w1 & 15
                cur_mat = None
            elif op == 0xF2:   # G_SETTILESIZE
                t = self.tiles[(w1 >> 24) & 7]
                t.uls, t.ult = (w0 >> 12) & 0xFFF, w0 & 0xFFF
                t.lrs, t.lrt = (w1 >> 12) & 0xFFF, w1 & 0xFFF
                cur_mat = None
            elif op == 0xF3:   # G_LOADBLOCK
                t = self.tiles[(w1 >> 24) & 7]
                fmt, siz, width, a = self.timg
                self.tmem[t.tmem] = (a, fmt, siz, width, 0, 0, 'block')
                cur_mat = None
            elif op == 0xF4:   # G_LOADTILE
                t = self.tiles[(w1 >> 24) & 7]
                fmt, siz, width, a = self.timg
                self.tmem[t.tmem] = (a, fmt, siz, width, (w0 >> 12) & 0xFFF, w0 & 0xFFF, 'tile')
                cur_mat = None
            elif op == 0xF0:   # G_LOADTLUT: count colours of 16 bits into TMEM from the tile's address
                t = self.tiles[(w1 >> 24) & 7]
                count = ((w1 >> 14) & 0x3FF) + 1
                a = self.timg[3]
                at = self.segs.at(a)
                if at is None:
                    self.mesh.miss('palette in segment %02X' % ((a >> 24) & 0xFF))
                else:
                    d, o = at
                    raw = [d[o + 2 * k] << 8 | d[o + 2 * k + 1] for k in range(count) if o + 2 * k + 1 < len(d)]
                    rgba = [_RGBA5551[v] for v in raw]
                    ia = [(v >> 8, v >> 8, v >> 8, v & 255) for v in raw]
                    self.tlut[t.tmem] = (rgba, ia)
                cur_mat = None
            elif op == 0xDB or op == 0xDC:   # G_MOVEWORD, G_MOVEMEM: lights, segments, fog; the game's to set
                if op == 0xDB and ((w0 >> 16) & 0xFF) == 0x06:   # G_MW_SEGMENT
                    pass
            elif op == 0xE7 or op == 0xE6 or op == 0xE8 or op == 0xE9 or op == 0x00:   # syncs, no-op
                pass
            elif op == 0xD0 or op == 0x02 or op == 0x03 or op == 0x04 or op == 0xD6 or op == 0xD3:
                pass   # G_LOAD_UCODE, G_MODIFYVTX, G_CULLDL, G_BRANCH_Z (both ways drawn), G_DMA_IO, G_SPECIAL
            elif op in (0xE4, 0xE5, 0xF6, 0xF7, 0xED, 0xEE, 0xF9, 0xFE, 0xFF, 0xF1, 0xEC, 0xEA, 0xEB):
                pass   # rectangles, fill and blend colours, scissor, depth image: nothing of a mesh
            else:
                self.mesh.miss('command %02X' % op)

    def _vertices(self, addr, n, v0):
        at = self.segs.at(addr)
        if at is None:
            self.mesh.miss('vertices in segment %02X' % ((addr >> 24) & 0xFF))
            for k in range(n):
                if 0 <= v0 + k < 64:
                    self.vbuf[v0 + k] = None
            return
        d, o = at
        for k in range(n):
            p = o + 16 * k
            if p + 16 > len(d) or not 0 <= v0 + k < 64:
                break
            x, y, z, _flag, s, t, r, g, b, a = struct.unpack_from('>hhhHhhBBBB', d, p)
            self.vbuf[v0 + k] = (x, y, z, s / 32.0, t / 32.0, r, g, b, a, self.bone)

    def _tri(self, a, b, c, cur_mat):
        vs = [self.vbuf[i] if i < 64 else None for i in (a, b, c)]
        if None in vs:
            self.mesh.miss('triangle with a vertex not loaded')
            return cur_mat
        if cur_mat is None:
            cur_mat = self.mesh.material(self._material())
        base = len(self.mesh.verts)
        self.mesh.verts.extend(vs)
        self.mesh.tris.append((cur_mat, base, base + 1, base + 2))
        return cur_mat


def walk(segments, addresses, mesh=None):
    """The mesh the display lists at addresses (segmented) draw, one after the other with one RDP state."""
    w = Walker(segments, mesh)
    for a in addresses:
        w.walk(a)
    return w.mesh
