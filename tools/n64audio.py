"""Read the sounds of an N64 Ocarina of Time ROM: the audio tables of the code file, the sound fonts, their samples
(decoded to 16-bit PCM) and, from the sound effect sequence, which samples each of Link's voice sounds plays. The
player's own ROM is the input (tools/n64rom.py); tools/n64voice.py puts the voices into the mod.

- The audio lives in three files after the file table: the sound fonts (file 3), the sequences (file 4) and the sample
  banks (file 5). The code file lists each in a table: a 16-byte header (s16 count, s16 medium, u32 ROM address 0,
  eight zero bytes), then count rows of u32 offset into the file, u32 size, s8 medium, s8 cache policy and three s16
  (for a font: the sample bank number << 8 | a second bank, the instrument count << 8 | the drum count, the sound
  effect count). They are found as the runs of rows that fit their file.
- A font starts with the offsets (from the font's start) of its drum list and of its sound effect list, then one per
  instrument. A sound effect is 8 bytes: the offset of its sample and its tuning (f32: the sample plays at tuning *
  32000 Hz). A sample is 16 bytes: u32 codec << 28 | medium << 26 | flags << 24 | size in bytes, u32 offset in its
  sample bank, then the offsets of its loop and of its codebook (u32 order, u32 predictor count, then order * 8 s16
  per predictor).
- VADPCM: 9-byte frames of a header (scale exponent << 4 | predictor) and sixteen 4-bit samples, each half of eight
  predicted from the two samples before it and the earlier samples of the half (decode_vadpcm).
- The sound effect sequence (number 0) gives each sound bank a channel; the voice bank's is channel 14. Its script
  reads the sound's index from an I/O port and calls the index's script through a table of u16 offsets (command
  0xC2). Each of those scripts loads the offset of a list of sound effect numbers (0xCE), stores it into the shared
  routine that plays it (0xCF), and passes the list's length (0xCC, or 0xB8 a random pick among that many): the
  sound picks one of its list at random. voice_samples follows that shape.

Standard library only.
"""
import struct
from array import array

import n64rom

SAMPLE_RATE = 32000          # a tuning of 1.0 plays the sample at this rate
VOICE_CHANNEL = 14           # the sound effect sequence's channel of the voice bank


class AudioError(Exception):
    pass


def _tables(code, sizes):
    """Each audio table of the code file whose rows fit one of the files: {file index: [(offset in code, rows)]}."""
    found = {}
    for off in range(0, len(code) - 32, 16):
        n, _medium, rom = struct.unpack_from('>hhI', code, off)
        if not 3 <= n <= 256 or rom or code[off + 8:off + 16] != bytes(8) or off + 16 + 16 * n > len(code):
            continue
        rows = [struct.unpack_from('>IIbbhhh', code, off + 16 + 16 * k) for k in range(n)]
        if rows[0][0] != 0 or sum(1 for r in rows if r[1]) <= n // 2:
            continue
        for fi, size in sizes.items():
            if all(r[0] + r[1] <= size for r in rows):
                found.setdefault(fi, []).append((off, rows))
    return found


def decode_vadpcm(data, book, order, nsamples):
    """16-bit samples of VADPCM data (9-byte frames) with the codebook book (a flat list of s16)."""
    out = array('h')
    hist = [0] * 8
    pos = 0
    while len(out) < nsamples and pos + 9 <= len(data):
        head = data[pos]
        scale = 1 << (head >> 4)
        pred = (head & 0xF) * order * 8
        c = [book[pred + k * 8:pred + k * 8 + 8] for k in range(order)]
        for half in range(2):
            ins = []
            for k in range(4):
                b = data[pos + 1 + half * 4 + k]
                for nib in (b >> 4, b & 0xF):
                    ins.append(((nib - 16) if nib >= 8 else nib) * scale)
            last = c[order - 1]
            new = []
            for j in range(8):
                acc = 0
                for k in range(order):
                    acc += c[k][j] * hist[8 - order + k]
                for k in range(j):
                    acc += last[j - k - 1] * ins[k]
                acc = (acc >> 11) + ins[j]
                new.append(max(-32768, min(32767, acc)))
            hist = new
            out.extend(new)
        pos += 9
    del out[nsamples:]
    return out


class Audio:
    def __init__(self, rom):
        self.rom = rom
        files = rom.files
        if len(files) < 6:
            raise AudioError('no audio files')
        self.fontfile, self.seqfile, self.bankfile = (rom.file(files[i][0]) for i in (3, 4, 5))
        if not (self.fontfile and self.seqfile and self.bankfile):
            raise AudioError('an audio file is missing')
        found = _tables(rom.code, {3: len(self.fontfile), 4: len(self.seqfile), 5: len(self.bankfile)})
        # the sequences: the longest table that fits the sequence file and no smaller file; fonts and banks likewise
        def pick(fi, ok):
            c = [rows for off, rows in found.get(fi, []) if ok(rows)]
            if not c:
                raise AudioError(f'no table for audio file {fi}')
            return max(c, key=len)
        self.fonts = pick(3, lambda rows: any(r[6] for r in rows))
        self.seqs = pick(4, lambda rows: len(rows) >= 64)
        self.banks = pick(5, lambda rows: len(rows) <= 16 and rows[0][1] > 0x100000)

    def sequence(self, n):
        off, size = self.seqs[n][:2]
        return self.seqfile[off:off + size]

    def font(self, n):
        off, size = self.fonts[n][:2]
        return self.fontfile[off:off + size]

    def font_sfx_count(self, n):
        return self.fonts[n][6]

    def sfx(self, font, index):
        """Sound effect index of the font: (16-bit samples, sample rate), or None."""
        f = self.font(font)
        if index >= self.font_sfx_count(font):
            return None
        sfx_list = struct.unpack_from('>I', f, 4)[0]
        sample, tuning = struct.unpack_from('>If', f, sfx_list + 8 * index)
        if not sample or sample + 16 > len(f):
            return None
        bits, addr, _loop, book_at = struct.unpack_from('>IIII', f, sample)
        codec, size = bits >> 28, bits & 0xFFFFFF
        bank = (self.fonts[font][4] >> 8) & 0xFF
        boff = self.banks[bank][0]
        data = self.bankfile[boff + addr:boff + addr + size]
        if codec != 0 or len(data) != size or not book_at:
            return None   # the voices are all VADPCM
        order, npred = struct.unpack_from('>ii', f, book_at)
        if not (1 <= order <= 8 and 1 <= npred <= 16):
            return None
        book = list(struct.unpack_from(f'>{order * npred * 8}h', f, book_at + 8))
        n = size // 9 * 16
        return decode_vadpcm(data, book, order, n), tuning * SAMPLE_RATE

    def voice_samples(self, font=None):
        """For each sound of the voice bank, by its index (Link's are 0..0x3F: an adult's sound, and 0x20 later the
        child's): the list of its sound effect numbers (it plays one at random), in font. font None: the sequence's
        own default, found as the font whose sound effects every list fits."""
        seq = self.sequence(0)
        start = None
        at = 0
        while at < 0x80 and seq[at] != 0xFF:   # the sequence's header: 0x9n ldchan n, u16 offset
            op = seq[at]
            if op & 0xF0 == 0x90:
                if op & 0xF == VOICE_CHANNEL:
                    start = struct.unpack_from('>H', seq, at + 1)[0]
                at += 3
            elif op in (0xD7, 0xD6, 0xC7):
                at += 3 + (op == 0xC7)
            elif op in (0xFD,):
                at += 2 + (seq[at + 1] >> 7)
            elif op in (0xF4, 0xF3, 0xF2):
                at += 2
            else:
                at += 2
        if start is None:
            raise AudioError('no voice channel in the sound effect sequence')

        def table_of(at):   # the dyntbl operands of the channel's loop whose scripts look like a voice sound's
            for p in range(at, min(at + 0x80, len(seq) - 3)):
                if seq[p] != 0xC2:
                    continue
                t = struct.unpack_from('>H', seq, p + 1)[0]
                if t + 2 <= len(seq):
                    s = struct.unpack_from('>H', seq, t)[0]
                    if s < len(seq) and seq[s] == 0xCE:
                        return t
            return None
        table = table_of(start)
        if table is None:
            raise AudioError('no voice table in the sound effect sequence')
        out = []
        for i in range(0x40):
            out.append(_voice_script(seq, struct.unpack_from('>H', seq, table + 2 * i)[0]))
        if font is None:
            need = max((k for v in out for var in v for k, _ in var), default=0)
            fonts = [f for f in range(len(self.fonts)) if self.font_sfx_count(f) > need]
            font = fonts[0] if fonts else 0
        return font, out


TICKS_PER_SECOND = 96      # the sound effect sequence's tempo (120 beats a minute) times 48 ticks a beat


def _layer(seq, at):
    """A layer script's notes, as one variant: [(sound effect number, start in ticks)]. 0x40..0x7F: note (the low six
    bits), a delay (one byte, or two when the first has its top bit) and a velocity; 0xC2: a transposition, which for
    sound effects adds 64 per step to the number; 0xFF: the end."""
    notes, t, transpose = [], 0, 0
    while at < len(seq):
        op = seq[at]
        if op == 0xFF:
            break
        if op == 0xC2:
            transpose = seq[at + 1] - 256 if seq[at + 1] >= 0x80 else seq[at + 1]
            at += 2
        elif 0x40 <= op < 0x80:
            d = seq[at + 1]
            n = 2
            if d & 0x80:
                d = (d & 0x7F) << 8 | seq[at + 2]
                n = 3
            notes.append(((op & 0x3F) + 64 * transpose, t))
            t += d
            at += n + 1
        else:
            break   # anything else ends what this reader follows
    return notes


def _voice_script(seq, s, depth=0):
    """The variants of one voice sound's script at s: a list of variants, each [(sound effect number, start)]."""
    if s + 6 > len(seq) or depth > 4:
        return []
    op = seq[s]
    if op == 0xFB:   # jump: another sound's script
        return _voice_script(seq, struct.unpack_from('>H', seq, s + 1)[0], depth + 1)
    if op == 0xCE:   # the list's offset, then (stored for the shared routine) its length, or one fixed entry
        lst = struct.unpack_from('>H', seq, s + 1)[0]
        if seq[s + 3] == 0xFB:
            return [[(seq[lst], 0)]]
        for p in range(s + 6, min(s + 16, len(seq) - 1)):
            if seq[p] in (0xCC, 0xB8):
                return [[(k, 0)] for k in seq[lst:lst + max(1, seq[p + 1])]]
        return [[(seq[lst], 0)]]
    if op & 0xF8 == 0x88:   # a layer of its own
        notes = _layer(seq, struct.unpack_from('>H', seq, s + 1)[0])
        return [notes] if notes else []
    if op & 0xF0 == 0x60:   # turns: a pair from a table (0xCB ldseq), one or the other each time
        for p in range(s, min(s + 12, len(seq) - 3)):
            if seq[p] == 0xCB:
                t = struct.unpack_from('>H', seq, p + 1)[0]
                return [[(seq[t], 0)], [(seq[t + 1], 0)]]
    return []


def describe(rom):
    a = Audio(rom)
    font, v = a.voice_samples()
    return f'{len(a.fonts)} sound fonts, {len(a.seqs)} sequences, {len(a.banks)} sample banks; {len(v)} voice sounds (font {font})'


if __name__ == '__main__':
    import sys
    r = n64rom.Rom(sys.argv[1])
    print(describe(r))
    a = Audio(r)
    font, v = a.voice_samples()
    for i, variants in enumerate(v):
        out = []
        for var in variants:
            parts = []
            for k, t in var:
                s = a.sfx(font, k)
                parts.append(f'{k}@{t}:{len(s[0]) / s[1]:.2f}s' if s else f'{k}:-')
            out.append('+'.join(parts))
        print(hex(i), ' | '.join(out))
