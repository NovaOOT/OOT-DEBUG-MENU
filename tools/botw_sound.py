"""Sound for tools/make_botw_enemies_mod.py: Breath of the Wild's sound archives read, and an archive of this game
made whose clips are those sounds.

- Breath of the Wild's BARS: "BARS", u32 file size, byte order mark, u16 version, u32 entry count, a u32 CRC32 of
  each entry's name, then per entry the u32 offsets of its AMTA block (the clip's name in its STRG section: u32 size,
  then the name) and of its FWAV.
- FWAV: "FWAV", byte order mark, u16 header size, u32 version, u32 file size, u16 block count, u16 0, then per block
  u16 type (0x7000 INFO, 0x7001 DATA), u16 0, u32 offset, u32 size. INFO: magic, u32 size, u8 codec (2 DSP-ADPCM),
  u8 loop, u16 0, u32 sample rate, u32 loop start, u32 sample count, u32 0, then a reference table of the channels
  (u32 count, then u16 type, u16 0, s32 offset from the table). A channel: a reference to its samples (offset from
  DATA + 8) and one to its DSP-ADPCM information (offset from the channel; 16 s16 coefficients first).
- This game's BARS (tools/n64voice.py): its clips are found by name, so an archive made from one of the game's keeps
  every AMTA block and name and gets new BWAVs. Each is held whole in the archive (not a prefetch of a stream file),
  so the new archive needs no stream files of its own: the stream files are found by the clip's name, which the
  game's own archive of that name shares.
- Sound/ResourceListPack/BarslistPack.Product.111.byml.zs: BarslistTable, by actor name, the archives the game loads
  for that actor ({'Bars': [names]}). It is a big-endian BYML; byml_swap turns a BYML into the other byte order, so
  it is read and written with tools/randomizer.py's little-endian code.

Needs Python's compression.zstd (3.14) for the RomFS, as tools/n64voice.py does.
"""
import re
import struct
from array import array

import n64voice


# ---- Breath of the Wild's archives

def botw_clips(b):
    """{name: (AMTA offset, FWAV offset)} of a Breath of the Wild BARS."""
    if b[:4] != b'BARS' or b[8:10] != b'\xff\xfe':
        raise ValueError('not a little-endian BARS')
    n = struct.unpack_from('<I', b, 0xC)[0]
    pairs = 0x10 + 4 * n
    out = {}
    for i in range(n):
        a, w = struct.unpack_from('<II', b, pairs + 8 * i)
        s = b.find(b'STRG', a, a + 0x400)
        if s < 0:
            raise ValueError(f'BARS entry {i}: no STRG in its AMTA')
        size = struct.unpack_from('<I', b, s + 4)[0]
        out[b[s + 8:s + 8 + size].split(b'\0')[0].decode('ascii', 'replace')] = (a, w)
    return out


def decode_dsp(b, start, coefs, count):
    """count 16-bit samples of the DSP-ADPCM frames at b[start:]."""
    out = array('h', bytes(2 * count))
    h1 = h2 = 0
    pos, i = start, 0
    while i < count:
        ps = b[pos]
        pos += 1
        c1, c2 = coefs[2 * (ps >> 4)], coefs[2 * (ps >> 4) + 1]
        scale = 1 << (ps & 15)
        for k in range(14):
            if i >= count:
                break
            nib = b[pos + k // 2] >> 4 if k % 2 == 0 else b[pos + k // 2] & 15
            if nib >= 8:
                nib -= 16
            s = max(-32768, min(32767, ((nib * scale) << 11) + 1024 + c1 * h1 + c2 * h2 >> 11))
            out[i] = s
            h2, h1 = h1, s
            i += 1
        pos += 7
    return out


def fwav_pcm(b, w):
    """(16-bit samples, sample rate) of the FWAV at b[w:]: its first channel."""
    if b[w:w + 4] != b'FWAV':
        raise ValueError(f'no FWAV at {w:#x}')
    nblocks = struct.unpack_from('<H', b, w + 0x10)[0]
    blocks = [struct.unpack_from('<HHII', b, w + 0x14 + 12 * k) for k in range(nblocks)]
    info = w + next(r[2] for r in blocks if r[0] == 0x7000)
    data = w + next(r[2] for r in blocks if r[0] == 0x7001)
    codec = b[info + 8]
    rate, _loop_start, count = struct.unpack_from('<III', b, info + 0xC)
    if codec != 2:
        raise ValueError(f'FWAV at {w:#x}: codec {codec}, not DSP-ADPCM')
    table = info + 0x1C
    ch = table + struct.unpack_from('<i', b, table + 8)[0]
    samples = data + 8 + struct.unpack_from('<i', b, ch + 4)[0]
    coefs = struct.unpack_from('<16h', b, ch + struct.unpack_from('<i', b, ch + 12)[0])
    return decode_dsp(b, samples, coefs, count), rate


# ---- this game's archives

LOOP_END, NO_LOOP = 0x4C, 0xFFFFFFFF   # a BWAV's first channel: its loop end (the loop start follows), and none


def clip_stem(name):
    """The last part of a clip's name, without its number and the spaces some names carry: 'SE_Enemy_Lizalfos_ Run01'
    -> 'Run'."""
    return re.sub(r'_?\d+$', '', name.replace(' ', '')).rsplit('_', 1)[-1]


def rebuild_bars(b, picks, botw, log=print):
    """The game's archive b with the clips picks names replaced. picks: {clip_stem of a clip: [Breath of the Wild
    clip names]} (the clips of that stem take them in turn); botw: {name: (16-bit samples, rate)}. Returns (archive,
    replaced count)."""
    entries = n64voice._entries(b)
    pairs = 0x10 + 4 * len(entries)
    starts = sorted({w for _n, _a, w in entries}) + [len(b)]
    size = {w: starts[k + 1] - w for k, w in enumerate(starts[:-1])}
    out = bytearray(b[:starts[0]])
    placed, turn, encoded = {}, {}, {}
    replaced = 0
    for i in sorted(range(len(entries)), key=lambda i: entries[i][2]):
        name, _a, w = entries[i]
        stem = clip_stem(name)
        new = None
        if w not in placed and stem in picks:
            k = turn.get(stem, 0)
            turn[stem] = k + 1
            src = picks[stem][k % len(picks[stem])]
            if src not in encoded:
                pcm = n64voice.resample(*botw[src], n64voice.RATE)
                encoded[src] = (len(pcm),) + n64voice.encode_dsp(pcm)
            count, coefs, frames, head = encoded[src]
            new = bytearray(n64voice.bwav(n64voice.RATE, coefs, frames, head, count, count, False))
            if struct.unpack_from('<I', b, w + LOOP_END)[0] != NO_LOOP:   # the base's clip loops: the new one whole
                struct.pack_into('<II', new, LOOP_END, count, 0)
            replaced += 1
        if w not in placed:
            while len(out) % 0x40:
                out.append(0)
            placed[w] = len(out)
            out += new if new is not None else b[w:w + size[w]]
        struct.pack_into('<I', out, pairs + 8 * i + 4, placed[w])
    struct.pack_into('<I', out, 4, len(out))
    left = sorted({re.sub(r'_?\d+$', '', n) for n, _a, _w in entries if clip_stem(n) not in picks})
    if left:
        log(f'  sounds kept from the base: {", ".join(left)}')
    return bytes(out), replaced


# ---- BYML byte order

def byml_swap(b):
    """The BYML b in the other byte order (same layout, every number swapped)."""
    src = '>' if b[:2] == b'BY' else '<'
    if b[:2] not in (b'BY', b'YB'):
        raise ValueError('not a BYML file')
    dst = '<' if src == '>' else '>'
    out = bytearray(b)
    out[0:2] = b'YB' if src == '>' else b'BY'

    def swap32(o):
        struct.pack_into(dst + 'I', out, o, struct.unpack_from(src + 'I', b, o)[0])

    def head(o):   # a node's header: its type byte, then its 24-bit count
        out[o + 1:o + 4] = b[o + 1:o + 4][::-1]
        return b[o], struct.unpack_from(src + 'I', b, o)[0] & 0xFFFFFF if src == '>' else \
            struct.unpack_from(src + 'I', b, o)[0] >> 8
    struct.pack_into(dst + 'H', out, 2, struct.unpack_from(src + 'H', b, 2)[0])
    for o in (4, 8, 12):
        swap32(o)
    kt, st, root = struct.unpack_from(src + 'III', b, 4)
    for t in (kt, st):
        if t:
            _, n = head(t)
            for k in range(n + 1):
                swap32(t + 4 + 4 * k)
    seen = set()

    def value(t, at):
        v = struct.unpack_from(src + 'I', b, at)[0]
        swap32(at)
        if t in (0xC0, 0xC1):
            node(v)
        elif t in (0xD4, 0xD5, 0xD6) and v not in seen:
            seen.add(v)
            struct.pack_into(dst + 'Q', out, v, struct.unpack_from(src + 'Q', b, v)[0])
        elif t == 0xA1 and v not in seen:
            seen.add(v)
            swap32(v)

    def node(o):
        if o in seen:
            return
        seen.add(o)
        t, n = head(o)
        if t == 0xC0:
            vo = o + 4 + (n + 3 & ~3)
            for i in range(n):
                value(b[o + 4 + i], vo + 4 * i)
        elif t == 0xC1:
            for i in range(n):
                e = o + 4 + 8 * i
                out[e:e + 3] = b[e:e + 3][::-1]
                value(b[e + 3], e + 4)
        else:
            raise ValueError(f'BYML node type {t:#x} at {o:#x} is not handled')
    if root:
        node(root)
    return bytes(out)
