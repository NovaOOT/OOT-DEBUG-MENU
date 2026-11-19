"""The scenes of an N64 Ocarina of Time ROM (tools/n64rom.py): rooms, their meshes, light settings and collision.

A scene file and each of its room files start with header commands of 8 bytes (the first byte says which) that end
with 0x14. The ones read here:
  0x03 collision (scene): the bounds, vertices (s16 x 3) and polygons (16 bytes: type, three vertex numbers with flags
       in the top 3 bits, the normal and the distance)
  0x04 room list (scene): rows of 8 bytes, each room file's virtual start and end
  0x07 special files (scene): the keep object segment 5 points at in its rooms (2 field keep, 3 dungeon keep)
  0x0A mesh (room): type 0, a list of opaque and see-through display lists; type 1, a prerendered background with its
       lists (the background itself is a picture for one camera, not a mesh); type 2, the same as 0 with a bounding
       sphere per entry
  0x0F light settings (scene): rows of 0x16 bytes: ambient, two lights (direction, colour), fog colour, fog near and
       draw distance
  0x18 alternate headers: header lists for child at night, adult by day, adult at night and the cutscenes; 0 where a
       setup uses the first
Addresses in the files are segmented: segment 2 is the scene file, 3 the room file.

Standard library only.
"""
import struct

import n64gfx

SETUPS = ('child day', 'child night', 'adult day', 'adult night')


def _seg_off(addr):
    return addr & 0xFFFFFF


def headers(data, base=0):
    """The commands of the header at data[base:], as (cmd, w0, w1), up to 0x14."""
    out = []
    off = base
    while off + 8 <= len(data):
        w0, w1 = struct.unpack_from('>II', data, off)
        cmd = w0 >> 24
        out.append((cmd, w0, w1))
        if cmd == 0x14 or len(out) > 64:
            break
        off += 8
    return out


def setups(data):
    """The header offsets of a scene or room file per setup (0 child day, 1 child night, 2 adult day, 3 adult night,
    4 on: cutscenes); a setup without its own header uses the first."""
    first = headers(data)
    alt = next((w1 for c, w0, w1 in first if c == 0x18), None)
    out = [0]
    if alt is not None:
        o = _seg_off(alt)
        for k in range(15):
            if o + 4 * k + 4 > len(data):
                break
            p = struct.unpack_from('>I', data, o + 4 * k)[0]
            if p and (p >> 24) not in (2, 3):
                break
            out.append(_seg_off(p) if p else None)
    return out


class Room:
    def __init__(self, number, data):
        self.number, self.data = number, data
        self.mesh_type = None
        self.opa, self.xlu = [], []   # segmented display list addresses
        self.spheres = []             # type 2: (x, y, z, radius) per entry
        self.objects = []
        cmds = headers(data)
        for c, w0, w1 in cmds:
            if c == 0x0A:
                self._mesh(_seg_off(w1))
            elif c == 0x0B:
                n = (w0 >> 16) & 0xFF
                o = _seg_off(w1)
                self.objects = [struct.unpack_from('>H', data, o + 2 * k)[0] for k in range(n) if o + 2 * k + 2 <= len(data)]

    def _mesh(self, o):
        d = self.data
        kind = d[o]
        self.mesh_type = kind
        if kind == 0 or kind == 2:
            n = d[o + 1]
            start = _seg_off(struct.unpack_from('>I', d, o + 4)[0])
            size = 8 if kind == 0 else 16
            for k in range(n):
                e = start + size * k
                if kind == 2:
                    x, y, z, r = struct.unpack_from('>hhhh', d, e)
                    self.spheres.append((x, y, z, r))
                    e += 8
                opa, xlu = struct.unpack_from('>II', d, e)
                if opa:
                    self.opa.append(opa)
                if xlu:
                    self.xlu.append(xlu)
        elif kind == 1:
            entry = _seg_off(struct.unpack_from('>I', d, o + 4)[0])
            if entry + 8 <= len(d):
                opa, xlu = struct.unpack_from('>II', d, entry)
                if opa:
                    self.opa.append(opa)
                if xlu:
                    self.xlu.append(xlu)


class Scene:
    def __init__(self, rom, number):
        self.rom, self.number = rom, number
        self.data = rom.scene(number)
        if self.data is None:
            raise ValueError(f'scene {number} is not in this ROM')
        self.keep = 0          # 2 field keep, 3 dungeon keep (object numbers)
        self.rooms = []
        self.lights = []
        self.collision = None
        for c, w0, w1 in headers(self.data):
            if c == 0x04:
                n = (w0 >> 16) & 0xFF
                o = _seg_off(w1)
                for k in range(n):
                    vs, ve = struct.unpack_from('>II', self.data, o + 8 * k)
                    rd = rom.file(vs)
                    if rd is not None:
                        self.rooms.append(Room(k, rd))
            elif c == 0x07:
                self.keep = w1 & 0xFFFF
            elif c == 0x0F:
                self.lights = self._lights((w0 >> 16) & 0xFF, _seg_off(w1))
            elif c == 0x03:
                self.collision = self._collision(_seg_off(w1))

    def _lights(self, n, o):
        out = []
        d = self.data
        for k in range(n):
            e = o + 0x16 * k
            if e + 0x16 > len(d):
                break
            amb = tuple(d[e:e + 3])
            dir1 = struct.unpack_from('>bbb', d, e + 3)
            col1 = tuple(d[e + 6:e + 9])
            dir2 = struct.unpack_from('>bbb', d, e + 9)
            col2 = tuple(d[e + 12:e + 15])
            fog = tuple(d[e + 15:e + 18])
            near, far = struct.unpack_from('>HH', d, e + 18)
            out.append({'ambient': amb, 'dir1': dir1, 'col1': col1, 'dir2': dir2, 'col2': col2, 'fog': fog,
                        'fog_near': near & 0x3FF, 'blend': near >> 10, 'far': far})
        return out

    def _collision(self, o):
        d = self.data
        mn = struct.unpack_from('>hhh', d, o)
        mx = struct.unpack_from('>hhh', d, o + 6)
        nv = struct.unpack_from('>H', d, o + 12)[0]
        pv = _seg_off(struct.unpack_from('>I', d, o + 16)[0])
        npoly = struct.unpack_from('>H', d, o + 20)[0]
        pp = _seg_off(struct.unpack_from('>I', d, o + 24)[0])
        verts = [struct.unpack_from('>hhh', d, pv + 6 * k) for k in range(nv) if pv + 6 * k + 6 <= len(d)]
        polys = []
        for k in range(npoly):
            e = pp + 16 * k
            if e + 16 > len(d):
                break
            t, a, b, c = struct.unpack_from('>HHHH', d, e)
            polys.append((t, a & 0x1FFF, b & 0x1FFF, c & 0x1FFF))
        return {'min': mn, 'max': mx, 'verts': verts, 'polys': polys}

    def segments(self, room):
        """The segments a room's display lists read: the scene, the room, gameplay_keep and the scene's keep."""
        segs = n64gfx.Segments()
        segs.set(0x02, self.data)
        segs.set(0x03, room.data)
        keep1 = self.rom.object(1)
        if keep1:
            segs.set(0x04, keep1)
        if self.keep:
            k = self.rom.object(self.keep)
            if k:
                segs.set(0x05, k)
        return segs

    def room_mesh(self, room):
        """(opaque mesh, see-through mesh) of a room, each with its own RDP state from the start."""
        segs = self.segments(room)
        return n64gfx.walk(segs, room.opa), n64gfx.walk(segs, room.xlu)
