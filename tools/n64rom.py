"""Read an N64 Ocarina of Time ROM: its file table, its compressed files, and the code tables that name the scenes, the
objects and the actors' code. The player's own ROM is the input; nothing read from it is ever written into the
repository (tools/make_n64_mod.py writes what it makes into the game's mods folder).

Supported: the big-endian dump (.z64) of NTSC 1.0, 1.1 and 1.2 and of the PAL and GameCube releases, which share the
layout below; a byte-swapped (.v64) or little-endian (.n64) dump is turned around first.

- The file table (dmadata) is a list of 16-byte rows: virtual start and end (the file's place in the address space the
  game loads from), then its physical start and end in the ROM (end 0: stored uncompressed, at the physical start).
  Its first row is the ROM's own first file, 0 .. 0x1060, which is how it is found.
- A compressed file is Yaz0: a 16-byte header (the magic, the size uncompressed) and groups of a flag byte and eight
  items, each a literal byte (flag bit 1) or a copy of 3 to 0x111 bytes from up to 0x1000 bytes back.
- The code file (the largest one, about 1 MB) holds the scene table (rows of 0x14 bytes: the scene file's virtual start
  and end, the title card's, then four bytes) and the object table (rows of 8 bytes: the object file's virtual start
  and end, 0 0 for an unused number). Both are found as the longest run of rows that name files of the file table.

Standard library only.
"""
import struct
from pathlib import Path

# The scenes by number (the scene table's order, the same in every release). The names say where the scene is.
SCENES = [
    'Inside the Deku Tree', "Dodongo's Cavern", "Inside Jabu-Jabu's Belly", 'Forest Temple', 'Fire Temple',
    'Water Temple', 'Spirit Temple', 'Shadow Temple', 'Bottom of the Well', 'Ice Cavern', "Ganon's Tower",
    'Gerudo Training Ground', "Thieves' Hideout", "Inside Ganon's Castle", "Ganon's Tower (collapsing)",
    "Inside Ganon's Castle (collapsing)", 'Treasure Box Shop', "Gohma's Lair", "King Dodongo's Lair", "Barinade's Lair",
    "Phantom Ganon's Lair", "Volvagia's Lair", "Morpha's Lair", "Twinrova's Lair", "Bongo Bongo's Lair",
    "Ganondorf's Lair", 'Tower Collapse Exterior', 'Market Entrance (child, day)', 'Market Entrance (child, night)',
    'Market Entrance (ruins)', 'Back Alley (day)', 'Back Alley (night)', 'Market (child, day)', 'Market (child, night)',
    'Market (ruins)', 'Temple of Time Exterior (child, day)', 'Temple of Time Exterior (child, night)',
    'Temple of Time Exterior (ruins)', "Know-It-All Brothers' House", 'House of Twins', "Mido's House",
    "Saria's House", 'Carpenter Boss House', "Man in Green's House", 'Bazaar', 'Kokiri Shop', 'Goron Shop',
    'Zora Shop', 'Kakariko Potion Shop', 'Market Potion Shop', 'Bombchu Shop', 'Happy Mask Shop', "Link's House",
    'Back Alley House (Dog Lady)', 'Stable', "Impa's House", 'Lakeside Laboratory', 'Carpenters\' Tent',
    'Gravekeeper\'s Hut', 'Great Fairy\'s Fountain (upgrades)', 'Fairy\'s Fountain', 'Great Fairy\'s Fountain (spells)',
    'Grottos', 'Grave (Redead)', 'Grave (Fairy\'s Fountain)', 'Royal Family\'s Tomb', 'Shooting Gallery',
    'Temple of Time', 'Chamber of the Sages', 'Castle Hedge Maze (day)', 'Castle Hedge Maze (night)',
    'Cutscene Map', 'Dampe\'s Grave and Windmill', 'Fishing Pond', 'Castle Courtyard', 'Bombchu Bowling Alley',
    'Ranch House and Silo', 'Guard House', 'Granny\'s Potion Shop', 'Ganon Battle Arena', 'House of Skulltula',
    'Hyrule Field', 'Kakariko Village', 'Graveyard', 'Zora\'s River', 'Kokiri Forest', 'Sacred Forest Meadow',
    'Lake Hylia', 'Zora\'s Domain', 'Zora\'s Fountain', 'Gerudo Valley', 'Lost Woods', 'Desert Colossus',
    'Gerudo\'s Fortress', 'Haunted Wasteland', 'Hyrule Castle', 'Death Mountain Trail', 'Death Mountain Crater',
    'Goron City', 'Lon Lon Ranch', 'Outside Ganon\'s Castle',
    # unused test scenes after these
]


class RomError(Exception):
    pass


def yaz0(src):
    """The Yaz0 data src, decompressed."""
    if src[:4] != b'Yaz0':
        raise RomError('not Yaz0 data')
    size = struct.unpack_from('>I', src, 4)[0]
    out = bytearray(size)
    i, o = 16, 0
    n = len(src)
    while o < size and i < n:
        code = src[i]
        i += 1
        for b in range(8):
            if o >= size:
                break
            if code & (0x80 >> b):
                out[o] = src[i]
                i += 1
                o += 1
            else:
                b1, b2 = src[i], src[i + 1]
                i += 2
                start = o - (((b1 & 0xF) << 8 | b2) + 1)
                count = b1 >> 4
                if count == 0:
                    count = src[i] + 0x12
                    i += 1
                else:
                    count += 2
                if start + count <= o:   # no overlap: one slice
                    out[o:o + count] = out[start:start + count]
                else:
                    for k in range(count):
                        out[o + k] = out[start + k]
                o += count
    return bytes(out)


def big_endian(data):
    """The ROM as the big-endian (.z64) byte order, whichever order the dump has."""
    head = data[:4]
    if head == b'\x80\x37\x12\x40':
        return data
    if head == b'\x37\x80\x40\x12':   # .v64: bytes swapped in pairs
        b = bytearray(data)
        b[0::2], b[1::2] = data[1::2], data[0::2]
        return bytes(b)
    if head == b'\x40\x12\x37\x80':   # .n64: little-endian words
        b = bytearray(len(data))
        for k in range(4):
            b[k::4] = data[3 - k::4]
        return bytes(b)
    raise RomError('not an N64 ROM (the first word is %s)' % head.hex())


class Rom:
    def __init__(self, path):
        self.data = big_endian(Path(path).read_bytes())
        self.game_id = self.data[0x3B:0x3F].decode('ascii', 'replace')
        self.revision = self.data[0x3F]
        self.title = self.data[0x20:0x34].decode('ascii', 'replace').strip()
        if 'ZELDA' not in self.title.upper():
            raise RomError(f'the ROM is "{self.title}", not Ocarina of Time')
        self.files = self._dmadata()
        self._by_start = {f[0]: f for f in self.files}
        self._cache = {}
        self.code, self.scene_table, self.object_table = self._tables()

    def _dmadata(self):
        at = self.data.find(bytes.fromhex('00000000000010600000000000000000'))
        if at < 0:
            raise RomError('no file table')
        files = []
        while at + 16 <= len(self.data):
            vs, ve, ps, pe = struct.unpack_from('>IIII', self.data, at)
            if ve == 0:
                break
            files.append((vs, ve, ps, pe))
            at += 16
        return files

    def file(self, vstart):
        """The file that starts at the virtual address vstart, uncompressed; None when there is none."""
        if vstart in self._cache:
            return self._cache[vstart]
        f = self._by_start.get(vstart)
        if not f:
            return None
        vs, ve, ps, pe = f
        if ps == 0xFFFFFFFF:   # a file left out of this release
            return None
        data = self.data[ps:ps + (ve - vs)] if pe == 0 else yaz0(self.data[ps:pe])
        if len(self._cache) > 64:
            self._cache.clear()
        self._cache[vstart] = data
        return data

    def is_file(self, vs, ve):
        f = self._by_start.get(vs)
        return f is not None and f[1] == ve

    def _run(self, data, step, row_ok, need):
        """The offset of the longest run of rows of step bytes that row_ok accepts in data, and its length."""
        best, bestn = None, 0
        for off in range(0, len(data) - step * need, 4):
            if not row_ok(data, off):
                continue
            n = 0
            while off + (n + 1) * step <= len(data) and row_ok(data, off + n * step):
                n += 1
            if n > bestn:
                best, bestn = off, n
        return best, bestn

    def _tables(self):
        code = None
        for vs, ve, ps, pe in sorted(self.files, key=lambda f: f[1] - f[0], reverse=True)[:6]:
            d = self.file(vs)
            if d and len(d) > 0xC0000:
                found = self._scene_table(d)
                if found:
                    code = d
                    break
        if code is None:
            raise RomError('no scene table in the code file')
        scenes = found
        objects = self._object_table(code)
        return code, scenes, objects

    def _scene_table(self, code):
        def ok(d, off):
            s, e = struct.unpack_from('>II', d, off)
            return self.is_file(s, e)
        off, n = self._run(code, 0x14, ok, 90)
        if off is None or n < 90:
            return None
        return [struct.unpack_from('>IIII', code, off + k * 0x14) for k in range(n)]

    def _object_table(self, code):
        # a row is a file of the file table, or an unused number: 0 0, or a file's start with no length
        def row(off):
            s, e = struct.unpack_from('>II', code, off)
            if (s, e) == (0, 0):
                return 0
            f = self._by_start.get(s)
            return 2 if f and f[1] == e else 1 if f and e == s else -1
        best, bestn = None, 0
        off = 0
        while off < len(code) - 8 * 350:
            if row(off) != 2:
                off += 4
                continue
            n = files = 0
            while off + (n + 1) * 8 <= len(code) and row(off + n * 8) >= 0:
                files += row(off + n * 8) == 2
                n += 1
            if files > bestn:
                best, bestn = (off, n), files
            off += n * 8
        if not best or bestn < 350:
            raise RomError('no object table in the code file')
        start, n = best
        # object 0 is unused (0 0) and comes just before object 1, gameplay_keep, where the run starts
        if start >= 8 and struct.unpack_from('>II', code, start - 8) == (0, 0):
            start -= 8
            n += 1
        out = []
        for k in range(n):
            s, e = struct.unpack_from('>II', code, start + k * 8)
            out.append((s, e) if s != e else (0, 0))
        return out

    def scene(self, number):
        """The scene file of scene number, uncompressed."""
        return self.file(self.scene_table[number][0])

    def object(self, number):
        """The object file of object number, uncompressed; None for an unused number."""
        if number >= len(self.object_table):
            return None
        s, e = self.object_table[number]
        return self.file(s) if s else None


def describe(rom):
    return (f'{rom.title} ({rom.game_id}, revision {rom.revision}): {len(rom.files)} files, '
            f'{len(rom.scene_table)} scenes, {len(rom.object_table)} objects')


if __name__ == '__main__':
    import sys
    print(describe(Rom(sys.argv[1])))
