"""Maps addresses of one game build's main to another's (1.1.1 to 1.0.0, say): the code by matching instruction
windows with their PC-relative fields masked, the data (.rodata, .data, .bss) through the matched code's references
to it (an adrp with the add, ldr or str after it).

    python tools/version_map.py OLD_MAIN NEW_MAIN 0x6e6cc0 0x4c7e890 ...
    python tools/version_map.py OLD_MAIN NEW_MAIN --stats

OLD_MAIN and NEW_MAIN are flat NSOs (the exefs/main make_game.py writes into a game folder, or tools/make_game.py
flat_nso). Addresses are main offsets. Each answer says how it was found and whether it is sure: a code address whose
two nearest anchors agree on the shift and whose instruction matches, a data address between two references with the
same shift. An unsure answer prints `?` and the reason.
"""
import argparse
import bisect
import struct
import sys

import numpy as np

WINDOW = 8   # instructions per anchor


class Main:
    def __init__(self, path):
        raw = open(path, 'rb').read()
        if raw[:4] != b'NSO0':
            sys.exit(f'{path} is not an NSO')
        self.segs = []
        for i in range(3):
            file_off, mem_off, size = struct.unpack_from('<III', raw, 0x10 + i * 0x10)
            self.segs.append((mem_off, size, raw[file_off:file_off + size]))
        self.bss = struct.unpack_from('<I', raw, 0x3C)[0]
        self.build = raw[0x40:0x50].hex()
        t_off, t_size, t_data = self.segs[0]
        self.text_off = t_off
        self.words = np.frombuffer(t_data[:t_size & ~3], dtype='<u4').astype(np.uint32)
        self.masked = mask(self.words)
        self.end = self.segs[2][0] + self.segs[2][1] + self.bss

    def seg_of(self, off):
        names = ('text', 'rodata', 'data')
        for (m, s, _), n in zip(self.segs, names):
            if m <= off < m + s:
                return n
        d = self.segs[2]
        if d[0] + d[1] <= off < self.end + 0x1000:
            return 'bss'
        return None

    def word(self, off):
        i = (off - self.text_off) >> 2
        return int(self.words[i]) if 0 <= i < len(self.words) else None


def mask(w):
    """The instruction words with what moves between builds cleared: branch and literal offsets, adrp/adr pages, and
    the low 12 bits (add, ldr, str immediates) of an instruction whose base register an adrp one or two words before
    set."""
    m = w.copy()
    adrp = (w & 0x1F000000) == 0x10000000
    m[adrp] &= 0x9F00001F
    b = (w & 0x7C000000) == 0x14000000
    m[b] &= 0xFC000000
    bcond = (w & 0xFF000010) == 0x54000000
    m[bcond] &= 0xFF00001F
    cbz = (w & 0x7E000000) == 0x34000000
    m[cbz] &= 0xFF00001F
    tbz = (w & 0x7E000000) == 0x36000000
    m[tbz] &= 0xFFF8001F
    lit = (w & 0x3B000000) == 0x18000000
    m[lit] &= 0xFF00001F
    rn = (w >> 5) & 31
    rd = w & 31
    lo12 = ((w & 0x7F800000) == 0x11000000) | ((w & 0x3B000000) == 0x39000000)   # add/sub imm, ldr/str uimm
    for back in (1, 2):
        prev_adrp = np.zeros_like(adrp)
        prev_adrp[back:] = adrp[:-back] & ((w[:-back] & 0x80000000) != 0)
        prev_rd = np.zeros_like(rd)
        prev_rd[back:] = rd[:-back]
        hit = lo12 & prev_adrp & (rn == prev_rd)
        m[hit] &= 0xFFC003FF
    return m


def window_hash(m):
    h = np.zeros(len(m) - WINDOW + 1, dtype=np.uint64)
    for k in range(WINDOW):
        h = (h * np.uint64(0x100000001B3)) ^ m[k:len(m) - WINDOW + 1 + k].astype(np.uint64)
    return h


def unique_positions(h):
    order = np.argsort(h, kind='stable')
    s = h[order]
    first = np.ones(len(s), dtype=bool)
    first[1:] = s[1:] != s[:-1]
    last = np.ones(len(s), dtype=bool)
    last[:-1] = s[1:] != s[:-1]
    keep = first & last
    return s[keep], order[keep]


class Map:
    def __init__(self, old, new):
        self.old, self.new = old, new
        ho, hn = window_hash(old.masked), window_hash(new.masked)
        ko, po = unique_positions(ho)
        kn, pn = unique_positions(hn)
        common, io, inn = np.intersect1d(ko, kn, assume_unique=True, return_indices=True)
        pairs = sorted(zip(po[io].tolist(), pn[inn].tolist()))
        # keep the pairs whose shift agrees with a neighbour's: a lone pair is a chance match
        keep = []
        for k, (a, b) in enumerate(pairs):
            d = b - a
            if (k and pairs[k - 1][1] - pairs[k - 1][0] == d) or (k + 1 < len(pairs) and
                                                                  pairs[k + 1][1] - pairs[k + 1][0] == d):
                keep.append((a, b))
        self.anchors = keep
        self.anchor_old = [a for a, _ in keep]
        self.calls = {}
        self.data = self.data_pairs()
        self.calls = {k: v for k, v in self.calls.items() if v != -1}
        self.data_old = sorted(self.data)

    def code_index(self, off):
        return (off - self.old.text_off) >> 2

    def code(self, off):
        """(new offset or None, note). An anchor's shift counts only when the code at the address matches there: the
        8 words from it on, or 3 on each side. A function's first word inside an anchor that ends the function before
        it matches backward alone, though the function itself may have moved."""
        if off in self.calls:
            return self.calls[off], 'the target of a call or branch in matched code'
        i = self.code_index(off)
        k = bisect.bisect_right(self.anchor_old, i) - 1
        tried = set()
        for kk in (k, k + 1):
            if 0 <= kk < len(self.anchors):
                a, b = self.anchors[kk]
                d = b - a
                if d in tried:
                    continue
                tried.add(d)
                if self.agrees(i, i + d):
                    return self.new.text_off + (i + d) * 4, f'anchor at {a - i:+d} words'
        return self.search(i)

    def agrees(self, i, j):
        m, n = self.old.masked, self.new.masked
        if not (0 <= j < len(n)):
            return False

        def run(step, most):
            c = 0
            while c < most and 0 <= i + step * c < len(m) and 0 <= j + step * c < len(n) and                     m[i + step * c] == n[j + step * c]:
                c += 1
            return c
        fwd, bwd = run(1, 8), run(-1, 4) - 1 if m[i] == n[j] else 0
        return fwd >= 8 or (fwd >= 3 and bwd >= 3)

    def search(self, i, radius=24):
        """A window around old word i searched in the new text: the widest window found exactly once. A window with
        nothing after the address leaves the answer unsure."""
        m, n = self.old.masked, self.new.masked
        for lo, hi in ((radius, radius), (0, radius), (12, 12), (0, 16), (6, 12), (radius, 0), (16, 0)):
            s, e = max(0, i - lo), min(len(m), i + hi + 1)
            pat = m[s:e]
            if len(pat) < 12:
                continue
            hits = np.flatnonzero(n[:len(n) - len(pat) + 1] == pat[0])
            for k in range(1, len(pat)):
                hits = hits[n[hits + k] == pat[k]]
                if len(hits) <= 1:
                    break
            if len(hits) == 1 and all(n[hits[0] + k] == pat[k] for k in range(len(pat))):
                sure = '' if hi else '?'
                return self.new.text_off + (hits[0] + i - s) * 4, f'{sure}window -{i - s}/+{e - 1 - i} found once'
        return None, 'no unique window: the code changed here'

    def data_pairs(self):
        """{old target: new target} from adrp + add/ldr/str pairs at aligned anchor positions."""
        out = {}
        ow, nw = self.old.words, self.new.words
        for a, b in self.anchors:
            for k in range(WINDOW - 1):
                i, j = a + k, b + k
                t1 = target(ow, i, self.old.text_off)
                t2 = target(nw, j, self.new.text_off)
                if t1 is not None and t2 is not None:
                    out.setdefault(t1, t2)
                self.note_call(ow, nw, i, j)
        # extend between anchors with the same shift
        for (a, b), (a2, b2) in zip(self.anchors, self.anchors[1:]):
            if b - a == b2 - a2 and a2 - a < 4096:
                for i in range(a + WINDOW, a2):
                    j = i + b - a
                    if self.old.masked[i] != self.new.masked[j]:
                        continue
                    t1 = target(ow, i, self.old.text_off)
                    t2 = target(nw, j, self.new.text_off)
                    if t1 is not None and t2 is not None:
                        out.setdefault(t1, t2)
                    self.note_call(ow, nw, i, j)
        return out

    def note_call(self, ow, nw, i, j):
        """A b or bl at aligned words: its old target maps to its new one (the first seen; a target two pairs
        disagree on is dropped)."""
        a, b = int(ow[i]), int(nw[j])
        if (a & 0x7C000000) != 0x14000000 or (a & 0xFC000000) != (b & 0xFC000000):
            return
        t1 = self.old.text_off + i * 4 + sext(a & 0x3FFFFFF, 26) * 4
        t2 = self.new.text_off + j * 4 + sext(b & 0x3FFFFFF, 26) * 4
        have = self.calls.get(t1)
        if have is None:
            self.calls[t1] = t2
        elif have != t2 and have != -1:
            self.calls[t1] = -1

    def datum(self, off):
        if off in self.data:
            return self.data[off], 'referenced directly'
        k = bisect.bisect_right(self.data_old, off) - 1
        lo = self.data_old[k] if k >= 0 else None
        hi = self.data_old[k + 1] if k + 1 < len(self.data_old) else None
        seg = self.old.seg_of(off)
        dl = self.data[lo] - lo if lo is not None and self.old.seg_of(lo) == seg else None
        dh = self.data[hi] - hi if hi is not None and self.old.seg_of(hi) == seg else None
        if dl is not None and dl == dh:
            return off + dl, f'between references {off - lo:#x} below and {hi - off:#x} above'
        if dl is not None and off - lo < 0x40:
            return off + dl, f'?{off - lo:#x} past a reference (the one above disagrees)'
        return None, f'no agreeing references (below {lo and hex(lo)} shift {dl and hex(dl)}, above {hi and hex(hi)} ' \
                     f'shift {dh and hex(dh)})'

    def map(self, off):
        seg = self.old.seg_of(off)
        if seg == 'text':
            return self.code(off) + (seg,)
        if seg:
            return self.datum(off) + (seg,)
        return None, 'outside main', None


def sext(v, bits):
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def target(w, i, text_off):
    """The address an adrp at word i and the add/ldr/str right after it form, or None."""
    if i + 1 >= len(w):
        return None
    a = int(w[i])
    if (a & 0x9F000000) != 0x90000000:
        return None
    pc = text_off + i * 4
    imm = ((a >> 29) & 3) | (((a >> 5) & 0x7FFFF) << 2)
    if imm & (1 << 20):
        imm -= 1 << 21
    page = (pc & ~0xFFF) + (imm << 12)
    rd = a & 31
    nxt = int(w[i + 1])
    if (nxt & 0x7F800000) == 0x11000000 and (nxt >> 5) & 31 == rd and not (nxt >> 22) & 1:   # add imm, no shift
        return page + ((nxt >> 10) & 0xFFF)
    if (nxt & 0x3B000000) == 0x39000000 and (nxt >> 5) & 31 == rd:   # ldr/str unsigned offset
        size = nxt >> 30
        if (nxt & 0x04000000) and (nxt >> 23) & 1:   # 128-bit simd
            size = 4
        return page + ((nxt >> 10) & 0xFFF) * (1 << size)
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('old')
    ap.add_argument('new')
    ap.add_argument('addr', nargs='*')
    ap.add_argument('--stats', action='store_true')
    a = ap.parse_args()
    old, new = Main(a.old), Main(a.new)
    mp = Map(old, new)
    if a.stats:
        print(f'old {old.build[:8]} text {len(old.words) * 4:#x}, new {new.build[:8]} text {len(new.words) * 4:#x}')
        print(f'{len(mp.anchors)} anchors, {len(mp.data)} data references')
        shifts = {}
        for x, y in mp.anchors:
            shifts[y - x] = shifts.get(y - x, 0) + 1
        for d, n in sorted(shifts.items(), key=lambda t: -t[1])[:15]:
            print(f'  shift {d * 4:+#x}: {n}')
    for s in a.addr:
        off = int(s, 16)
        new_off, note, seg = mp.map(off)
        print(f'{off:#x} {seg or "-"} -> {new_off:#x} ({note})' if new_off is not None else
              f'{off:#x} {seg or "-"} -> ? ({note})')


if __name__ == '__main__':
    main()
