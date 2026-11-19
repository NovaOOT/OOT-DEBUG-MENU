"""Put the N64 version's voice of Link into the N64 Graphics mod (tools/make_n64_mod.py): the remake's voice clips of
the player (Sound/Resource/Player_Child.bars.zs and Player_Adult.bars.zs, and the stream files of the longer clips)
are rebuilt from the player's own RomFS with each clip that has an N64 counterpart replaced by that sound of the
player's ROM (tools/n64audio.py), and written into the mod's romfs folder, which the game reads over its own while the
mod is on (runtime/mods.c).

- BARS: "BARS", u32 file size, byte order mark, u16 version, u32 entry count, then a u32 CRC32 of each entry's name
  (ascending), then per entry the u32 offsets of its AMTA block (metadata; the name at 0x24 plus the u32 there) and
  of its BWAV. The AMTA blocks are kept as they are; the BWAVs follow them, each at a multiple of 0x40.
- BWAV: "BWAV", byte order mark, u16 version, u32 CRC32 of the sample data, u16 prefetch flag, u16 channel count, then
  0x4C bytes per channel (u16 codec, 1 DSP-ADPCM; u16 pan, 2 middle; u32 sample rate; u32 sample count; u32 the
  samples held here; 16 s16 coefficients; u32 offset of the samples, twice; u32 1; u32 loop end, 0xFFFFFFFF none;
  u32 loop start; u16 first frame header and two s16 of history, twice), the samples at 0x80.
- A clip longer than the archive should hold is a prefetch: the archive's BWAV has the flag and the first 14336
  samples, and Sound/Resource/Stream/<name>.bwav the whole clip without the flag. Every replaced clip keeps its
  original's choice. The game's sound engine plays every clip at 48 kHz whatever its BWAV says, so the N64 clips
  (20 kHz) are resampled to that. An archive that grows needs no more: the runtime raises the resource size table's
  entry of each file a mod serves (runtime/romfs.c restbl_overlay).
- DSP-ADPCM: 8-byte frames of a header (coefficient pair << 4 | scale exponent) and 14 four-bit samples, each
  predicted from the two decoded before it. encode_dsp fits the clip's eight pairs (fit_pairs: silence, and seven
  centres of the frames' own best pairs, weighted by their loudness) and picks, per frame, the pair and scale with
  the least error.
- The RomFS's .zs files are zstd frames made with one of the dictionaries in Pack/ZsDic.pack.zs (a SARC); a rebuilt
  archive is compressed with the dictionary its original used.

Needs Python's compression.zstd (3.14), as the launcher's own Python has it.
"""
import json
import re
import struct
import zlib
from array import array
from pathlib import Path

import n64audio

RATE = 48000               # the rate the game plays every clip at
PREFETCH = 14336
ARCHIVES = {'Player_Child': ('PlChild', 0x20), 'Player_Adult': ('PlAdult', 0)}

# The N64 voice sounds (tools/n64audio.py: an adult's index, the child's 0x20 later) by what Link does.
SWORD_N, SWORD_L, LASH, HANG, CLIMB_END, DAMAGE_S, FREEZE, FALL_S, FALL_L, BREATH_REST, BREATH_DRINK, DOWN = range(12)
DRINK, AUTO_JUMP, MAGIC_NAYRU, SURPRISE, MAGIC_FARORE, PUSH, LAND_DAMAGE, MAGIC_DIN = 0x10, 0x14, 0x15, 0x16, 0x17, \
    0x18, 0x1A, 0x1C
SILENT = -1   # a clip the N64 Link has no voice for where the remake has one: it plays silence

# The remake's clip names (Vo_PlChild_<name>_<nn>, Vo_PlAdult_<name>_<nn>) by the name without its number, and the N64
# sound each gets. A clip not listed keeps the remake's voice: the N64 Link has no such sound.
VOICES = {
    'SwordAttack_S': SWORD_N, 'SwordAttack_M': SWORD_N, 'Swing': SWORD_N, 'Hammer': SWORD_N,
    'BigSwordAttack_S': SWORD_N, 'BigSwordAttack_M': SWORD_N,
    'Boomerang': SWORD_N, 'BoomerangC': SWORD_N, 'ThrowingBom': SWORD_N, 'ThrowingNuts': SWORD_N,
    'SwordAttack_B': SWORD_L, 'BigSwordAttack_B': SWORD_L, 'JumpAttack': SWORD_L, 'BS_JumpAttack': SWORD_L,
    'H_JumpAttack': SWORD_L, 'RotaryCut_S': SWORD_L, 'RotaryCut_B': SWORD_L,
    'RollAttack': SILENT,   # every roll: the N64 Link rolls without a sound of his own, only the roll's own on the ground
    'Jump_S': AUTO_JUMP, 'Jump_B': AUTO_JUMP, 'BackFlip': AUTO_JUMP, 'SideStep': AUTO_JUMP, 'ClimbJump': AUTO_JUMP,
    'GrabJump': AUTO_JUMP, 'JumpCliffs': AUTO_JUMP,
    'lifting': LASH, 'liftingImpossible': LASH, 'liftingImpossible_03_alt': LASH, 'SilverGlove_LiftUp': LASH,
    'SilverGlove_LiftUpStart': LASH, 'SilverGlove_LiftUpEnd': LASH, 'SilverGlove_Throw': SWORD_N,
    'Push': PUSH, 'Push_03_alt': PUSH, 'NormalBlock': PUSH,
    'ClimbSt': HANG, 'GrabFall': HANG, 'Climb': CLIMB_END, 'ReachingTop': CLIMB_END,
    'Damage_S': DAMAGE_S, 'Damage_M': DAMAGE_S, 'Damage_B': DAMAGE_S, 'FireDamage_S': DAMAGE_S,
    'FireDamage_L': DAMAGE_S, 'ElectricDamage_S': DAMAGE_S, 'ElectricDamage_L': DAMAGE_S, 'PoisonDamage': DAMAGE_S,
    'AntlionDamage': DAMAGE_S, 'RollAttackDamage_S': DAMAGE_S, 'RollAttackDamage_B': DAMAGE_S, 'LavaDamage': DAMAGE_S,
    'LavaSwallowedDamage': DAMAGE_S, 'IceDamage': FREEZE,
    'FallSuddenly': FALL_S, 'FallingScream': FALL_L, 'FallingScream_For_Demo': FALL_L, 'WallMasterReturn_Scream': FALL_L,
    'LandingDamage': LAND_DAMAGE, 'LandingDamage_01_alt': LAND_DAMAGE, 'LandingDamage_02_alt': LAND_DAMAGE,
    'Death': DOWN, 'WaterDeath': DOWN,
    'Surprised': SURPRISE, 'HeavyBbreathing': BREATH_REST, 'EscapedBreath': BREATH_REST,
    'BottleDrink': DRINK, 'BottleDrinkEd': BREATH_DRINK, 'DrinkBreath': BREATH_DRINK,
    'DinsFire_Shoot': MAGIC_DIN, 'DinsFire_01_Shoot': MAGIC_DIN, 'DinsFire_02_Shoot': MAGIC_DIN,
    'FaroresWindShoot': MAGIC_FARORE, 'FaroresWind_01_Shoot': MAGIC_FARORE, 'FaroresWind_02_Shoot': MAGIC_FARORE,
    'NayrsLove_Shoot': MAGIC_NAYRU, 'NayrsLove_01_Shoot': MAGIC_NAYRU, 'NayrsLove_02_Shoot': MAGIC_NAYRU,
}

# DSP coefficient pairs (2.11 fixed point) that fit_pairs starts from: silence, first and second order ones.
PAIRS = [(0, 0), (2048, 0), (4096, -2048), (3584, -1536), (3072, -1024), (1536, 0), (2560, -1024), (1024, 512)]


class VoiceError(Exception):
    pass


# ---- RomFS ----

def _sarc(data):
    """A SARC's members: {name: (start, end)} in data."""
    if data[:4] != b'SARC':
        raise VoiceError('not a SARC')
    e = '<' if data[6:8] == b'\xff\xfe' else '>'
    base = struct.unpack_from(e + 'I', data, 0xC)[0]
    n = struct.unpack_from(e + 'H', data, 0x1A)[0]
    names = 0x20 + n * 16 + 8
    out = {}
    for i in range(n):
        _h, attr, lo, hi = struct.unpack_from(e + 'IIII', data, 0x20 + i * 16)
        at = names + (attr & 0xFFFFFF) * 4
        out[data[at:data.index(b'\0', at)].decode()] = (base + lo, base + hi)
    return out


class RomFS:
    """The game's RomFS as the launcher unpacks it: a folder of romfs.bin and index.json."""

    def __init__(self, folder):
        import compression.zstd as zstd
        self.zstd = zstd
        folder = Path(folder)
        self.bin = folder / 'romfs.bin'
        index = json.loads((folder / 'index.json').read_text(encoding='utf-8'))
        self.files = {f['path']: f for f in index['files']}
        self.dicts = {}
        pack = zstd.decompress(self.raw('Pack/ZsDic.pack.zs'))
        for lo, hi in _sarc(pack).values():
            d = zstd.ZstdDict(pack[lo:hi])
            self.dicts[d.dict_id] = d

    def raw(self, path):
        f = self.files.get(path)
        if not f:
            raise VoiceError(f'{path} is not in the RomFS')
        with self.bin.open('rb') as h:
            h.seek(f['offset'])
            return h.read(f['size'])

    def zs(self, path):
        """A .zs file's contents and the dictionary it was made with."""
        raw = self.raw(path)
        d = self.dicts.get(self.zstd.get_frame_info(raw).dictionary_id)
        return self.zstd.decompress(raw, zstd_dict=d), d

    def compress(self, data, d):
        out = self.zstd.compress(data, level=19, zstd_dict=d)
        if self.zstd.decompress(out, zstd_dict=d) != data:
            raise VoiceError('zstd round trip failed')
        return out


# ---- sound ----

def resample(pcm, rate, to):
    """pcm (16-bit samples at rate) at the rate to, by cubic interpolation."""
    if abs(rate - to) < 0.5:
        return array('h', pcm)
    n = int(len(pcm) * to / rate)
    step = rate / to
    out = array('h', bytes(2 * n))
    last = len(pcm) - 1
    for i in range(n):
        x = i * step
        k = int(x)
        t = x - k
        p0 = pcm[max(k - 1, 0)]
        p1 = pcm[min(k, last)]
        p2 = pcm[min(k + 1, last)]
        p3 = pcm[min(k + 2, last)]
        v = p1 + 0.5 * t * (p2 - p0 + t * (2 * p0 - 5 * p1 + 4 * p2 - p3 + t * (3 * (p1 - p2) + p3 - p0)))
        out[i] = max(-32768, min(32767, int(round(v))))
    return out


def fit_pairs(pcm):
    """Eight coefficient pairs for pcm: (0, 0), then seven centres (k-means, by loudness) of each frame's best pair."""
    pts = []
    for start in range(2, len(pcm) - 14, 14):
        r11 = r22 = r12 = r01 = r02 = 0.0
        for n in range(start, start + 14):
            x0, x1, x2 = pcm[n], pcm[n - 1], pcm[n - 2]
            r11 += x1 * x1
            r22 += x2 * x2
            r12 += x1 * x2
            r01 += x0 * x1
            r02 += x0 * x2
        det = r11 * r22 - r12 * r12
        if r11 < 1e3 or r22 < 1e3 or abs(det) <= 1e-6 * r11 * r22:
            continue
        c1 = (r01 * r22 - r02 * r12) / det
        c2 = (r02 * r11 - r01 * r12) / det
        lim = 32767 / 2048
        pts.append((max(-lim, min(lim, c1)), max(-lim, min(lim, c2)), r11))
    centres = [(a / 2048, b / 2048) for a, b in PAIRS[1:]]
    for _ in range(12):
        acc = [[0.0, 0.0, 0.0] for _ in centres]
        for c1, c2, w in pts:
            k = min(range(len(centres)), key=lambda k: (centres[k][0] - c1) ** 2 + (centres[k][1] - c2) ** 2)
            acc[k][0] += c1 * w
            acc[k][1] += c2 * w
            acc[k][2] += w
        centres = [(a[0] / a[2], a[1] / a[2]) if a[2] else c for a, c in zip(acc, centres)]
    return [(0, 0)] + [(int(round(a * 2048)), int(round(b * 2048))) for a, b in centres]


def encode_dsp(pcm):
    """DSP-ADPCM of pcm: (16 coefficients, frame bytes, the first frame's header)."""
    pairs = fit_pairs(pcm)
    out = bytearray()
    h1 = h2 = 0
    n = len(pcm)
    for start in range(0, n, 14):
        block = list(pcm[start:start + 14])
        block += [0] * (14 - len(block))
        best = None
        for ci, (c1, c2) in enumerate(pairs):
            # the scale the residual needs, predicted from the input itself
            p1, p2, peak = h1, h2, 0
            for s in block:
                r = s - ((c1 * p1 + c2 * p2 + 1024) >> 11)
                peak = max(peak, abs(r))
                p2, p1 = p1, s
            scale = 0
            while scale < 11 and peak > (7 << scale):
                scale += 1
            for sc in (scale, min(scale + 1, 11)):
                p1, p2, err, nib = h1, h2, 0, []
                for s in block:
                    pred = (c1 * p1 + c2 * p2 + 1024) >> 11
                    q = int(round((s - pred) / (1 << sc)))
                    q = max(-8, min(7, q))
                    d = max(-32768, min(32767, ((q << sc << 11) + 1024 + c1 * p1 + c2 * p2) >> 11))
                    err += (d - s) * (d - s)
                    nib.append(q & 0xF)
                    p2, p1 = p1, d
                if best is None or err < best[0]:
                    best = (err, ci, sc, nib, p1, p2)
            if best[0] == 0:
                break
        _err, ci, sc, nib, h1, h2 = best
        out.append(ci << 4 | sc)
        for k in range(0, 14, 2):
            out.append(nib[k] << 4 | nib[k + 1])
    coefs = [max(-32768, min(32767, v)) for pair in pairs for v in pair]
    return coefs, bytes(out), out[0] if out else 0


def bwav(rate, coefs, frames, head, count, held, prefetch):
    """A mono DSP-ADPCM BWAV of count samples holding the first held of them (frames: all the clip's frames)."""
    data = frames[:(held + 13) // 14 * 8]
    crc = zlib.crc32(frames)
    hdr = bytearray(0x80)
    struct.pack_into('<4s2sHIHH', hdr, 0, b'BWAV', b'\xff\xfe', 1, crc, 1 if prefetch else 0, 1)
    struct.pack_into('<HHIII16hIIIIIHhhHhh', hdr, 0x10, 1, 2, int(round(rate)), count, held, *coefs, 0x80, 0x80, 1, 0xFFFFFFFF,
                     0, head, 0, 0, head, 0, 0)
    return bytes(hdr) + data


def n64_clips(rom):
    """{voice index: [(16-bit samples, sample rate), one per variant]} of the ROM's voice sounds."""
    a = n64audio.Audio(rom)
    font, voices = a.voice_samples()
    cache = {}
    out = {}
    for i, variants in enumerate(voices):
        clips = []
        for var in variants:
            key = tuple(var)
            if key not in cache:
                parts, rate = [], None
                for k, t in var:
                    s = a.sfx(font, k)
                    if s:
                        rate = RATE
                        parts.append((resample(s[0], s[1], rate), int(t * rate / n64audio.TICKS_PER_SECOND)))
                if not parts:
                    cache[key] = None
                else:
                    mix = [0] * max(len(p) + at for p, at in parts)
                    for p, at in parts:
                        for j, v in enumerate(p):
                            mix[at + j] += v
                    cache[key] = (array('h', (max(-32768, min(32767, v)) for v in mix)), rate)
            if cache[key] is not None:
                clips.append(cache[key])
        if clips:
            out[i] = clips
    return out


# ---- the archives ----

def _entries(b):
    n = struct.unpack_from('<I', b, 0xC)[0]
    pairs = 0x10 + 4 * n
    out = []
    for i in range(n):
        a, w = struct.unpack_from('<II', b, pairs + 8 * i)
        at = a + 0x24 + struct.unpack_from('<I', b, a + 0x24)[0]
        out.append((b[at:b.index(b'\0', at)].decode('ascii', 'replace'), a, w))
    return out


def rebuild(b, who, offset, clips, encoded):
    """The archive b with its voice clips replaced: (new archive, {stream path: bytes}, replaced count)."""
    entries = _entries(b)
    pairs = 0x10 + 4 * len(entries)
    starts = sorted({w for _n, _a, w in entries}) + [len(b)]
    size = {w: starts[k + 1] - w for k, w in enumerate(starts[:-1])}   # with the padding up to the next
    out = bytearray(b[:starts[0]])
    streams = {}
    placed = {}
    replaced = 0
    pick = {}
    for i in sorted(range(len(entries)), key=lambda i: entries[i][2]):
        name, _a, w = entries[i]
        m = re.fullmatch(rf'Vo_{who}_(.+?)_?(\d+)', name)
        new = None
        if w not in placed and m and m.group(1) in VOICES and (VOICES[m.group(1)] == SILENT or
                                                              VOICES[m.group(1)] + offset in clips):
            voice = SILENT if VOICES[m.group(1)] == SILENT else VOICES[m.group(1)] + offset
            variants = [(array('h', bytes(2 * RATE // 50)), RATE)] if voice == SILENT else clips[voice]
            n = pick.get(m.group(1), 0)
            pick[m.group(1)] = n + 1
            key = (voice, n % len(variants))
            if key not in encoded:
                pcm, rate = variants[key[1]]
                encoded[key] = (rate, len(pcm)) + encode_dsp(pcm)
            rate, count, coefs, frames, head = encoded[key]
            if struct.unpack_from('<H', b, w + 0xC)[0] == 1:   # a prefetch: the rest is streamed
                new = bwav(rate, coefs, frames, head, count, min(count, PREFETCH), True)
                streams[f'Sound/Resource/Stream/{name}.bwav'] = bwav(rate, coefs, frames, head, count, count, False)
            else:
                new = bwav(rate, coefs, frames, head, count, count, False)
            replaced += 1
        if w not in placed:
            while len(out) % 0x40:
                out.append(0)
            placed[w] = len(out)
            out += new if new is not None else b[w:w + size[w]]
        struct.pack_into('<I', out, pairs + 8 * i + 4, placed[w])
    struct.pack_into('<I', out, 4, len(out))
    return bytes(out), streams, replaced


def make(rom, romfs_folder, mod_folder, log=print):
    """Write the rebuilt archives and stream files under mod_folder/romfs. Returns the number of clips replaced."""
    fs = RomFS(romfs_folder)
    clips = n64_clips(rom)
    total = 0
    encoded = {}
    for archive, (who, offset) in ARCHIVES.items():
        path = f'Sound/Resource/{archive}.bars.zs'
        b, d = fs.zs(path)
        new, streams, n = rebuild(b, who, offset, clips, encoded)
        dest = Path(mod_folder) / 'romfs'
        (dest / path).parent.mkdir(parents=True, exist_ok=True)
        (dest / path).write_bytes(fs.compress(new, d))
        for p, data in streams.items():
            (dest / p).parent.mkdir(parents=True, exist_ok=True)
            (dest / p).write_bytes(data)
        log(f'{archive}: {n} voice clips replaced ({len(streams)} streamed), {len(b)} -> {len(new)} bytes')
        total += n
    return total
