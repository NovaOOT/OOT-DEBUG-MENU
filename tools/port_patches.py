"""Ports a patch file (recomp/patches.py) from one game build's main to another's with tools/version_map.py: each
site's address, its original word, and the replacement words whose PC-relative fields (b, bl, b.cond, cbz, tbz, adr,
adrp with the add or ldr after it) must follow the move. A bl or b to the host-function window keeps its target. The
`main+0x...` addresses in the comments are mapped too when the map is sure of them; the others keep the old address
and are listed.

    python tools/port_patches.py OLD_MAIN NEW_MAIN recomp/patches-v111.txt recomp/patches-v100.txt --version 1.0.0

A site whose code changed between the builds cannot be ported this way: it is written commented out with the reason,
and the script exits 1, so that it is placed by hand.
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import version_map as vm   # noqa: E402

MAIN_BASE = 0x7000000000
HOST_FROM = 0x7010000000   # the kernel and host-function windows (runtime/host.h): fixed, not part of main


def sext(v, bits):
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def pc_target(w, pc):
    """(kind, target) of a PC-relative instruction at pc, else None."""
    if (w & 0x7C000000) == 0x14000000:
        return 'b', pc + sext(w & 0x3FFFFFF, 26) * 4
    if (w & 0xFF000010) == 0x54000000 or (w & 0x7E000000) == 0x34000000:
        return 'imm19', pc + sext((w >> 5) & 0x7FFFF, 19) * 4
    if (w & 0x7E000000) == 0x36000000:
        return 'imm14', pc + sext((w >> 5) & 0x3FFF, 14) * 4
    if (w & 0x9F000000) == 0x90000000:
        imm = ((w >> 29) & 3) | (((w >> 5) & 0x7FFFF) << 2)
        return 'adrp', (pc & ~0xFFF) + sext(imm, 21) * 4096
    if (w & 0x9F000000) == 0x10000000:
        imm = ((w >> 29) & 3) | (((w >> 5) & 0x7FFFF) << 2)
        return 'adr', pc + sext(imm, 21)
    if (w & 0x3B000000) == 0x18000000:
        return 'imm19', pc + sext((w >> 5) & 0x7FFFF, 19) * 4
    return None


def encode(w, kind, pc, target):
    if kind == 'b':
        d = (target - pc) >> 2
        assert -(1 << 25) <= d < (1 << 25)
        return (w & 0xFC000000) | (d & 0x3FFFFFF)
    if kind == 'imm19':
        d = (target - pc) >> 2
        assert -(1 << 18) <= d < (1 << 18)
        return (w & 0xFF00001F) | ((d & 0x7FFFF) << 5)
    if kind == 'imm14':
        d = (target - pc) >> 2
        assert -(1 << 13) <= d < (1 << 13)
        return (w & 0xFFF8001F) | ((d & 0x3FFF) << 5)
    if kind in ('adrp', 'adr'):
        d = ((target >> 12) - (pc >> 12)) if kind == 'adrp' else target - pc
        assert -(1 << 20) <= d < (1 << 20)
        d &= 0x1FFFFF
        return (w & 0x9F00001F) | ((d & 3) << 29) | ((d >> 2) << 5)
    raise ValueError(kind)


def lo12_fix(nxt, target):
    """The add or ldr/str after an adrp, its immediate set for target's low 12 bits."""
    lo = target & 0xFFF
    if (nxt & 0x7F800000) == 0x11000000:
        return (nxt & ~(0xFFF << 10)) | (lo << 10)
    size = nxt >> 30
    if (nxt & 0x04000000) and (nxt >> 23) & 1:
        size = 4
    assert lo % (1 << size) == 0
    return (nxt & ~(0xFFF << 10)) | ((lo >> size) << 10)


class Porter:
    def __init__(self, old, new, placed=None):
        self.old, self.new = vm.Main(old), vm.Main(new)
        self.map = vm.Map(self.old, self.new)
        self.placed = placed or {}   # {old site: new site} found by hand
        self.problems = []
        # the patch page: the first page after main's image (recomp/patches.py load), so it moves with main's size
        self.old_page = MAIN_BASE + ((self.old.end + 0xFFF) & ~0xFFF)
        self.new_page = MAIN_BASE + ((self.new.end + 0xFFF) & ~0xFFF)

    def addr(self, off):
        """The new offset of an old main offset, or None; sure only when the note has no '?'."""
        new_off, note, _ = self.map.map(off)
        return (new_off if new_off is not None and '?' not in note else None), note

    def target(self, t):
        """An absolute target: main's mapped, the patch page's moved with it, anything else (host functions) kept."""
        if self.old_page <= t < self.old_page + 0x1000:
            return t - self.old_page + self.new_page, 'on the patch page'
        if MAIN_BASE <= t < MAIN_BASE + self.old.end:
            n, note = self.addr(t - MAIN_BASE)
            return None if n is None else MAIN_BASE + n, note
        return t, 'outside main'

    def site(self, off, orig, repl):
        """(new offset, new original, new replacement words) or raises ValueError with the reason."""
        n, note = (self.placed[off], 'placed by hand') if off in self.placed else self.addr(off)
        if n is None:
            raise ValueError(f'site {off:#x} not found ({note})')
        have = self.new.word(n)
        if have != orig:
            j = (n - self.new.text_off) >> 2
            if self.old.masked[self.map.code_index(off)] != self.new.masked[j]:
                raise ValueError(f'site {off:#x} -> {n:#x}: the new build has {have:08x} there, the old {orig:08x}')
        old_pc, new_pc = MAIN_BASE + off, MAIN_BASE + n
        out = []
        i = 0
        while i < len(repl):
            w = repl[i]
            if isinstance(w, str):
                out.append(w)
                i += 1
                continue
            pt = pc_target(w, old_pc)
            if pt is None:
                out.append(w)
                i += 1
                continue
            kind, t = pt
            if kind == 'adrp' and i + 1 < len(repl) and isinstance(repl[i + 1], int):
                nxt = repl[i + 1]
                full = vm.target([w, nxt], 0, old_pc)   # page + low bits
                if full is not None:
                    nt, tnote = self.target(full)
                    if nt is None:
                        raise ValueError(f'site {off:#x}: data {full - MAIN_BASE:#x} not mapped ({tnote})')
                    out += [encode(w, 'adrp', new_pc, nt), lo12_fix(nxt, nt)]
                    i += 2
                    continue
            nt, tnote = self.target(t)
            if nt is None:
                raise ValueError(f'site {off:#x}: target {t - MAIN_BASE:#x} not mapped ({tnote})')
            out.append(encode(w, kind, new_pc, nt))
            i += 1
        return n, have, out

    def comment(self, text, old_name, new_name):
        def sub(m):
            off = int(m.group(1), 16)
            n, _ = self.addr(off)
            if n is None:
                self.problems.append(f'comment main+{m.group(1)} kept')
                return m.group(0)
            return f'main+0x{n:x}'
        text = re.sub(r'main\+0x([0-9a-f]+)', sub, text)
        return text.replace(old_name, new_name)


def port(porter, src, version, old_version):
    out = []
    failed = 0
    for raw in Path(src).read_text().splitlines():
        body, sep, cmt = raw.partition('#')
        f = body.split()
        if f and f[0] == 'site':
            try:
                off, orig = int(f[3], 16), int(f[4], 16)
                repl = [w if w.startswith('@') else int(w, 16) for w in f[5:]]
                n, have, new = porter.site(off, orig, repl)
                words = ' '.join(w if isinstance(w, str) else f'{w:08x}' for w in new)
                line = f'site {f[1]:<11} {f[2]} {n:x} {have:08x} {words}'
                if sep:
                    line = line.ljust(len(body.rstrip())) + '   #' + porter.comment(cmt, old_version, version)
                out.append(line)
            except ValueError as e:
                failed += 1
                out.append(f'# NOT PORTED ({e}): {raw}')
                porter.problems.append(str(e))
        elif sep:
            out.append(body + sep + porter.comment(cmt, old_version, version))
        else:
            out.append(raw)
    return '\n'.join(out) + '\n', failed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('old')
    ap.add_argument('new')
    ap.add_argument('src')
    ap.add_argument('dst')
    ap.add_argument('--version', required=True, help='the new build, as the comments name it (1.0.0)')
    ap.add_argument('--old-version', default='1.1.1')
    ap.add_argument('--place', action='append', default=[], metavar='OLD=NEW',
                    help='a site whose new address was found by hand (its words are still checked)')
    a = ap.parse_args()
    placed = {int(o, 16): int(n, 16) for o, n in (p.split('=') for p in a.place)}
    porter = Porter(a.old, a.new, placed)
    text, failed = port(porter, a.src, a.version, a.old_version)
    Path(a.dst).write_text(text, newline='\n')
    for p in porter.problems:
        print(p)
    print(f'{a.dst}: {failed} site(s) not ported')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
