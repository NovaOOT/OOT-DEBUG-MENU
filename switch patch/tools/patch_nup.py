"""Write a copy of a .nup dump of the game with the debug module in it: for each package whose main is a build in
tools/builds.py, main gets that build's patch (the hook stub and the branch to it) and exefs gets the module as
subsdk9. The .nup given is only read.

    python patch_nup.py GAME.nup [--out OUT.nup] [--plain-text]

Run tools/build.py first: the module and the patches come from build/switch-patch/. Needs the zbic package (the
game's main is ZBIC-compressed) and Python 3.10 or newer; "switch patch/patch-nup.bat" runs it through uv.

How the copy is made. A .nup's data offsets are absolute. The emulator's reader requires the table of contents
(TOC) to lie between 0x80 and the blob offset, where the file data starts, and each file's data at or after the blob
offset (check_layout has its rules). The original fills that space with the TOC and each package's meta, so the copy
is the whole .nup with the patched main, the module and the package metas appended after the data, and the new,
slightly larger TOC written at 0x80 in the space the old TOC and metas took; the superblock gives the new TOC and size.
The old main stays in the file, unused. In the new TOC the module's file entry goes right after the package's last
file and its child entry right after the exefs folder's last one, so each package's dirs, files and children stay one
unbroken run (as tools/nup_*.bat expect). --repair moves the TOC of a copy an earlier version wrote (TOC at the end,
which the emulator refuses) to 0x80 in place.

How main is patched. Only the text segment changes: it is unpacked, the patch's writes are made (the stub lies in the
padding after the code, so the segment grows by the stub's size, still short of the read-only segment), its sha256
is made again and it is packed again with ZBIC at a level whose output unpacks to the same bytes (zbic's default
level does not on this code), so main keeps Nintendo's flags. With --plain-text, or when no level gives the same
bytes back, the text segment is stored plainly instead (its compression bit cleared); ro and data are copied as
they are.
"""

import argparse
import hashlib
import os
import shutil
import struct
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent.parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(REPO / 'tools'))

import builds   # noqa: E402
import mkpatch  # noqa: E402
import nup      # noqa: E402  (the repository's reader, to check the copy)

OUT = REPO / 'build' / 'switch-patch'
MODULE = 'subsdk9'
SUPER = struct.Struct('<4sHHQQ32sQQII16s32s')
DIRECTORY = struct.Struct('<IIIIQ')
HDR, PKG, DIR, FIL = 0x30, 0x40, 0x18, 0x50
CHUNK = 16 << 20
ZBIC_LEVELS = (9, 1, 19)


class Fail(Exception):
    pass


def align(v, a):
    return (v + a - 1) // a * a


# ---- main

def patch_main(raw, ips, b, plain):
    """The patched main NSO and a line saying how its text segment was stored."""
    if raw[:4] != b'NSO0':
        raise Fail('main is not an NSO')
    flags = struct.unpack_from('<I', raw, 0xC)[0]
    segs = [list(struct.unpack_from('<III', raw, 0x10 + i * 0x10)) for i in range(3)]
    stored = list(struct.unpack_from('<III', raw, 0x60))
    t_off, t_mem, t_size = segs[0]
    packed = raw[t_off:t_off + stored[0]]
    if flags & 1:
        if not flags & 0x80:
            raise Fail('main\'s text segment is LZ4-packed; this tool knows the ZBIC-packed main of the game only')
        text = zbic().decompress(packed)
    else:
        text = packed
    if len(text) != t_size:
        raise Fail(f'main\'s text unpacks to {len(text):#x} bytes, its header says {t_size:#x}')
    if flags & 8 and hashlib.sha256(text).digest() != raw[0xA0:0xC0]:
        raise Fail('main\'s text segment is damaged (sha256 mismatch)')
    if struct.unpack_from('<I', text, b['frame_site'])[0] != b['frame_word']:
        raise Fail(f'main+{b["frame_site"]:#x} does not hold the instruction the patch replaces')

    writes = mkpatch.records(ips)
    end = max(a + len(d) for a, d in writes)
    if end > segs[1][1]:
        raise Fail('the patch writes past the text segment')
    text = bytearray(text) + bytes(max(0, align(end, 4) - t_size))
    for addr, data in writes:
        if addr >= t_size and any(text[addr:addr + len(data)]):
            raise Fail(f'main+{addr:#x} is not empty padding')
        text[addr:addr + len(data)] = data
    text = bytes(text)

    how = 'stored plainly'
    new_flags = flags
    if flags & 1 and not plain:
        for level in ZBIC_LEVELS:
            try:
                out = zbic().compress(text, level)
                if zbic().decompress(out) == text:
                    packed, how = out, f'packed with ZBIC level {level}'
                    break
            except zbic().error:
                pass
        else:
            new_flags &= ~1
    else:
        new_flags &= ~1
    if not new_flags & 1:
        packed = text

    head = bytearray(raw[:0x100])
    struct.pack_into('<I', head, 0xC, new_flags)
    body = bytearray(raw[0x100:t_off])   # the module name, between the header and the text
    pos = t_off
    parts = [packed, raw[segs[1][0]:segs[1][0] + stored[1]], raw[segs[2][0]:segs[2][0] + stored[2]]]
    sizes = [len(text), segs[1][2], segs[2][2]]
    for i, part in enumerate(parts):
        struct.pack_into('<III', head, 0x10 + i * 0x10, pos, segs[i][1], sizes[i])
        struct.pack_into('<I', head, 0x60 + i * 4, len(part))
        body += part
        pos += len(part)
    if new_flags & 8:
        head[0xA0:0xC0] = hashlib.sha256(text).digest()
    return bytes(head + body), how


_zbic = None


def zbic():
    global _zbic
    if _zbic is None:
        try:
            import zbic as z
        except ImportError:
            raise Fail('the zbic package is missing: run this through patch-nup.bat, or pip install zbic') from None
        _zbic = z
    return _zbic


# ---- the table of contents

class Toc:
    def __init__(self, data):
        magic, npkg, ndir, nfile, pool_off, pool_len, self.reserved = struct.unpack_from('<IIQQQQQ', data, 0)
        if magic != 0x01434F54:
            raise Fail('unknown table of contents')
        at = HDR
        self.pkgs = [bytearray(data[at + i * PKG:at + (i + 1) * PKG]) for i in range(npkg)]
        at += npkg * PKG
        self.dirs = [list(DIRECTORY.unpack_from(data, at + i * DIR)) for i in range(ndir)]
        at += ndir * DIR
        self.files = [bytearray(data[at + i * FIL:at + (i + 1) * FIL]) for i in range(nfile)]
        at += nfile * FIL
        self.children = list(struct.unpack_from(f'<{(pool_off - at) // 4}I', data, at))
        self.pool = bytearray(data[pool_off:pool_off + pool_len])

    @property
    def ndir(self):
        return len(self.dirs)

    def name(self, off, n):
        return self.pool[off:off + n].decode('utf-8')

    def pkg_dir(self, p, field):   # 8 root, 12 exefs, 16 romfs
        return struct.unpack_from('<I', self.pkgs[p], field)[0]

    def entries(self, d):
        """{name: ('dir', index) or ('file', index)} of a dir's direct children."""
        _no, _nl, first, count, _ = self.dirs[d]
        out = {}
        for v in self.children[first:first + count]:
            if v < self.ndir:
                out[self.name(self.dirs[v][0], self.dirs[v][1])] = ('dir', v)
            else:
                fi = v - self.ndir
                out[self.name(struct.unpack_from('<I', self.files[fi], 0)[0],
                              struct.unpack_from('<I', self.files[fi], 4)[0])] = ('file', fi)
        return out

    def files_below(self, d):
        found, stack, seen = [], [d], set()
        while stack:
            d = stack.pop()
            if d in seen:
                raise Fail('the table of contents is malformed (a dir below itself)')
            seen.add(d)
            _no, _nl, first, count, _ = self.dirs[d]
            for v in self.children[first:first + count]:
                (stack.append(v) if v < self.ndir else found.append(v - self.ndir))
        return found

    def add_file(self, p, exefs, name, size, offset, sha):
        """A new file in package p's exefs dir, kept inside the package's runs of files and children."""
        ins_f = max(self.files_below(self.pkg_dir(p, 8))) + 1
        self.children = [v + 1 if v >= self.ndir + ins_f else v for v in self.children]
        entry = bytearray(FIL)
        name_b = name.encode()
        struct.pack_into('<IIQQQ32sB', entry, 0, len(self.pool), len(name_b), size, offset, size, sha, 0)
        self.pool += name_b
        self.files.insert(ins_f, entry)
        _no, _nl, first, count, _ = self.dirs[exefs]
        ins_c = first + count
        self.children.insert(ins_c, self.ndir + ins_f)
        for i, d in enumerate(self.dirs):
            if i != exefs and d[3] and d[2] >= ins_c:
                d[2] += 1
        self.dirs[exefs][3] += 1

    def set_data(self, fi, size, offset, sha):
        struct.pack_into('<QQQ32sB', self.files[fi], 8, size, offset, size, sha, 0)

    def pack(self):
        body = b''.join(self.pkgs) + b''.join(DIRECTORY.pack(*d) for d in self.dirs) + b''.join(self.files)
        body += struct.pack(f'<{len(self.children)}I', *self.children)
        pool_off = align(HDR + len(body), 16)
        head = struct.pack('<IIQQQQQ', 0x01434F54, len(self.pkgs), len(self.dirs), len(self.files), pool_off,
                           len(self.pool), self.reserved)
        out = bytearray(head + body)
        out += bytes(pool_off - len(out))
        out += self.pool
        return bytes(out + bytes(align(len(out), 16) - len(out)))


# ---- the copy

def copy(src, dst, total):
    done, last, start = 0, -1, time.time()
    with open(src, 'rb') as i, open(dst, 'wb') as o:
        while True:
            b = i.read(CHUNK)
            if not b:
                break
            o.write(b)
            done += len(b)
            pct = done * 100 // total
            if pct != last:
                last = pct
                rate = done / max(time.time() - start, 1e-3) / 1e6
                print(f'\r  copying: {pct}%  ({done / 1e9:.2f} of {total / 1e9:.2f} GB, {rate:.0f} MB/s)   ', end='',
                      flush=True)
    print()
    if done != total:
        raise Fail('the .nup changed size while it was copied')


def read_at(path, off, n):
    with open(path, 'rb') as f:
        f.seek(off)
        b = f.read(n)
    if len(b) != n:
        raise Fail('the .nup ends early')
    return b


def run(src, dst, plain, dry_run=False):
    src = Path(src).resolve()
    if not src.is_file():
        raise Fail(f'file not found: {src}')
    size = src.stat().st_size
    sb = read_at(src, 0, SUPER.size)
    magic, a, b_, toc_off, toc_size, toc_sha, blob, fsize, npkg, z, uid, zeros = SUPER.unpack(sb)
    if magic != b'NUP\x01':
        raise Fail('not a .nup file')
    if fsize != size:
        raise Fail(f'the .nup is {size} bytes, its header says {fsize}: it is incomplete')
    toc_data = read_at(src, toc_off, toc_size)
    if hashlib.sha256(toc_data).digest() != toc_sha:
        raise Fail('the table of contents is damaged (sha256)')
    toc = Toc(toc_data)
    module = (OUT / MODULE).read_bytes() if (OUT / MODULE).is_file() else None
    if module is None:
        raise Fail(f'{OUT / MODULE} is missing: run tools/build.py first')
    module_sha = hashlib.sha256(module).digest()
    print(f'Reading {src}')

    plan = []   # (package, exefs dir, main file index, build name, patched main)
    for p in range(len(toc.pkgs)):
        tid, version, kind = struct.unpack_from('<Q', toc.pkgs[p], 0)[0], struct.unpack_from('<I', toc.pkgs[p], 20)[0], toc.pkgs[p][24]
        label = {0: 'base game', 1: 'update', 2: 'DLC'}.get(kind, f'kind {kind}')
        exefs = toc.pkg_dir(p, 12)
        if exefs >= toc.ndir:
            print(f'  package {p}: {tid:016X} v{version} ({label}), no exefs: left as it is')
            continue
        ent = toc.entries(exefs)
        if ent.get('main', ('', 0))[0] != 'file':
            print(f'  package {p}: {tid:016X} v{version} ({label}), no main: left as it is')
            continue
        mi = ent['main'][1]
        _no, _nl, m_size, m_off, m_stored, m_sha, m_codec = struct.unpack_from('<IIQQQ32sB', toc.files[mi], 0)
        if m_codec != 0:
            raise Fail('main is stored zstd-packed in this .nup; this tool reads a stored main only')
        raw = read_at(src, m_off, m_stored)
        if hashlib.sha256(raw).digest() != m_sha:
            raise Fail(f'package {p}: main is damaged (sha256)')
        build_id = raw[0x40:0x54].hex().upper()
        name = next((n for n, bb in builds.BUILDS.items() if bb['build_id'] == build_id), None)
        if name is None:
            print(f'  package {p}: {tid:016X} v{version} ({label}), main {build_id[:8]}: no patch for this build, '
                  f'left as it is')
            continue
        if MODULE in ent:
            if ent[MODULE][0] == 'file' and toc.files[ent[MODULE][1]][32:64] == module_sha:
                raise Fail(f'package {p} already holds this module: the .nup is patched already')
            raise Fail(f'package {p} already has an exefs/{MODULE} (another mod?); this tool will not replace it')
        ips = (OUT / f'{build_id}.ips').read_bytes()
        patched, how = patch_main(raw, ips, builds.BUILDS[name], plain)
        print(f'  package {p}: {tid:016X} v{version} ({label}), game {name}: main patched, its code {how}')
        plan.append((p, exefs, mi, name, patched))
    if not plan:
        raise Fail('no package of this .nup holds a game build the patch supports (1.1.1, 1.0.0)')

    dst = Path(dst).resolve() if dst else src.with_name(src.stem + ' (OOT-PC debug).nup')
    if dst == src:
        raise Fail('the copy would replace the .nup it is made from')
    metas = read_metas(src, toc)

    # Everything appended goes after the data: the patched mains, the module (once: files with the same bytes share
    # one copy, as in the original), then each package's meta, which leaves room for the grown TOC at 0x80
    writes = []
    pos = align(size, 0x100)
    module_at = None
    for p, exefs, mi, name, patched in plan:
        writes.append((pos, patched))
        toc.set_data(mi, len(patched), pos, hashlib.sha256(patched).digest())
        pos = align(pos + len(patched), 0x100)
        if module_at is None:
            writes.append((pos, module))
            module_at = pos
            pos = align(pos + len(module), 0x100)
    for p, exefs, mi, name, patched in plan:
        toc.add_file(p, exefs, MODULE, len(module), module_at, module_sha)
    end = place_metas(toc, metas, pos, writes)
    new_toc = toc.pack()
    check_toc(toc_data, Toc(new_toc), plan)
    head = header_region(sb, new_toc, blob, end)
    check_layout(head[:SUPER.size], new_toc, end)
    print(f'  layout: table of contents at 0x80 ({len(new_toc):#x} of {blob - 0x80:#x} bytes before the data), '
          f'{len(writes)} blocks appended after the data')

    if dry_run:
        print(f'Dry run: would write {dst} ({end / 1e9:.2f} GB); nothing written')
        return
    free = shutil.disk_usage(dst.parent).free
    if free < end + (1 << 20):
        raise Fail(f'{dst.parent} has {free / 1e9:.1f} GB free; the copy needs {end / 1e9:.1f} GB')
    print(f'Writes {dst} ({end / 1e9:.2f} GB)')

    part = dst.with_name(dst.name + '.partial')
    try:
        copy(src, part, size)
        with open(part, 'r+b') as f:
            write_all(f, head, writes, size, end)
        verify(part, plan, module_sha)
        os.replace(part, dst)
    except BaseException:
        if part.exists():
            part.unlink()
        raise
    print(f'Done: {dst}')


def read_metas(path, toc):
    """{package: its meta bytes} (u64 offset at +32, u32 length at +40 of a package entry); none for a length 0."""
    out = {}
    for p, pkg in enumerate(toc.pkgs):
        off, n = struct.unpack_from('<QI', pkg, 32)
        out[p] = read_at(path, off, n) if n else b''
    return out


def place_metas(toc, metas, pos, writes):
    """Puts each package's meta at pos onward (16-byte aligned) and points its entry there. Returns the end."""
    for p, meta in metas.items():
        if meta:
            pos = align(pos, 16)
            writes.append((pos, meta))
            struct.pack_into('<Q', toc.pkgs[p], 32, pos)
            pos += len(meta)
    return pos


def header_region(sb, new_toc, blob, end):
    """The bytes from 0 up to the data: the superblock, the TOC at 0x80, zeros after it (the old TOC and metas)."""
    if 0x80 + len(new_toc) > blob:
        raise Fail(f'the new table of contents ({len(new_toc):#x} bytes) does not fit before the data')
    fields = list(SUPER.unpack(sb))
    fields[3], fields[4], fields[5], fields[7] = 0x80, len(new_toc), hashlib.sha256(new_toc).digest(), end
    out = SUPER.pack(*fields) + bytes(0x80 - SUPER.size) + new_toc
    return out + bytes(blob - len(out))


def write_all(f, head, writes, data_end, end):
    f.seek(data_end)
    f.write(bytes(align(data_end, 0x100) - data_end))
    for at, data in writes:
        f.seek(at)
        f.write(data)
    f.truncate(end)
    f.seek(0)
    f.write(head)
    f.flush()
    os.fsync(f.fileno())


def check_layout(sb, toc_data, file_size):
    """The rules the emulator's .nup reader applies before it loads anything (its container parser, read from its
    executable): the superblock, the TOC between 0x80 and the data, each package's dirs and meta, each file's data
    after the blob offset, 16-byte aligned and inside the file, children inside the tables."""
    magic, hsize, _v, toc_off, toc_size, toc_sha, blob, fsize, npkg, zero, _uid, _z = SUPER.unpack(sb)
    rules = [
        (magic == b'NUP\x01', 'magic'), (hsize >= 0x80, 'header size'), (zero == 0, 'reserved word'),
        (fsize == file_size, 'file size'), (toc_off >= 0x80, 'TOC before 0x80'),
        (blob >= toc_off and toc_size <= blob - toc_off, 'TOC not before the data'), (blob <= fsize, 'blob offset'),
        (blob % 16 == 0, 'blob offset alignment'), (hashlib.sha256(toc_data).digest() == toc_sha, 'TOC sha256'),
    ]
    for ok, what in rules:
        if not ok:
            raise Fail(f'layout: {what} breaks the emulator\'s rules')
    t = Toc(toc_data)
    if not t.pkgs or len(t.pkgs) != npkg:
        raise Fail('layout: package count')
    for p, pkg in enumerate(t.pkgs):
        kind, flags = pkg[24], pkg[25]
        root, exefs, romfs = (struct.unpack_from('<I', pkg, k)[0] for k in (8, 12, 16))
        if kind > 2 or flags > 1 or root >= t.ndir or not all(d < t.ndir or d == 0xFFFFFFFF for d in (exefs, romfs)):
            raise Fail(f'layout: package {p} entry')
        off, n = struct.unpack_from('<QI', pkg, 32)
        if n and (off > fsize or n > fsize - off):
            raise Fail(f'layout: package {p} meta outside the file')
    nfile = len(t.files)
    for i, e in enumerate(t.files):
        no, nl, size, off, stored = struct.unpack_from('<IIQQQ', e, 0)
        if e[64] > 1 or e[65] or no + nl > len(t.pool):
            raise Fail(f'layout: file {i} entry')
        if size and not (off >= blob and off % 16 == 0 and stored and off <= fsize and stored <= fsize - off):
            raise Fail(f'layout: file {i} data at {off:#x} breaks the emulator\'s rules')
        if not size and (off or stored):
            raise Fail(f'layout: empty file {i} with data')
    if any(v >= t.ndir + nfile for v in t.children):
        raise Fail('layout: a child outside the tables')
    for p in range(len(t.pkgs)):
        t.files_below(t.pkg_dir(p, 8))   # raises on a cycle


def repair(path):
    """Moves the TOC of a copy made by an earlier version of this tool (TOC at the end) to 0x80, with the package
    metas after the data instead. The file data stays where it is; the old header region and the end are saved to
    build/switch-patch/repair-backup/ first."""
    path = Path(path).resolve()
    size = path.stat().st_size
    sb = read_at(path, 0, SUPER.size)
    magic, _a, _b, toc_off, toc_size, toc_sha, blob, fsize, *_ = SUPER.unpack(sb)
    if magic != b'NUP\x01' or fsize != size:
        raise Fail('not a complete .nup')
    if toc_off < blob:
        raise Fail('its table of contents already lies before the data: nothing to repair')
    toc_data = read_at(path, toc_off, toc_size)
    if hashlib.sha256(toc_data).digest() != toc_sha or toc_off + toc_size != size:
        raise Fail('its table of contents is not the one this tool appended at the end')
    toc = Toc(toc_data)
    metas = read_metas(path, toc)
    backup = OUT / 'repair-backup'
    backup.mkdir(parents=True, exist_ok=True)
    (backup / f'{path.stem}.head.bin').write_bytes(read_at(path, 0, blob))
    (backup / f'{path.stem}.tail.bin').write_bytes(read_at(path, toc_off, size - toc_off))
    print(f'Repairing {path}\n  saved the header region and the old TOC in {backup}')
    writes = []
    end = place_metas(toc, metas, toc_off, writes)
    new_toc = toc.pack()
    if listing(Toc(new_toc)) != listing(Toc(toc_data)):
        raise Fail('the moved table of contents would list other files')
    head = header_region(sb, new_toc, blob, end)
    check_layout(head[:SUPER.size], new_toc, end)
    with open(path, 'r+b') as f:
        write_all(f, head, writes, toc_off, end)
    verify_file(path)
    print(f'Done: {path}')


def verify_file(path):
    """The written file read back: the emulator's rules, then every package's exefs through tools/nup.py."""
    sb = read_at(path, 0, SUPER.size)
    _m, _a, _b, toc_off, toc_size, *_ = SUPER.unpack(sb)
    check_layout(sb, read_at(path, toc_off, toc_size), Path(path).stat().st_size)
    n = nup.Nup(path)
    for p in range(len(n.packages)):
        if n.packages[p]['exefs'] < n.ndir:
            for _rel, fi in n.tree(n.packages[p]['exefs']):
                n.read(fi)
    print('  checked: the layout keeps the emulator\'s rules, every exefs file reads back with its sha256')


def listing(toc):
    """{(package, path): file entry less its name fields} of every file, by walking each package's root."""
    out = {}
    for p in range(len(toc.pkgs)):
        stack = [(toc.pkg_dir(p, 8), '')]
        while stack:
            d, prefix = stack.pop()
            for name, (kind, i) in toc.entries(d).items():
                if kind == 'dir':
                    stack.append((i, prefix + name + '/'))
                else:
                    out[(p, prefix + name)] = bytes(toc.files[i][8:])
    return out


def check_toc(old_data, new, plan):
    """The new TOC lists every file of the old one with the same entry, except each patched main, plus the module."""
    old = listing(Toc(old_data))
    have = listing(new)
    want = dict(old)
    for p, exefs, mi, name, patched in plan:
        want.pop((p, 'exefs/main'))
        want.pop((p, f'exefs/{MODULE}'), None)
        if (p, 'exefs/main') not in have or (p, f'exefs/{MODULE}') not in have:
            raise Fail('the new table of contents lacks main or the module')
    for p, *_ in plan:
        for f in ('exefs/main', f'exefs/{MODULE}'):
            have.pop((p, f))
    if have != want:
        raise Fail(f'the new table of contents differs from the old in {len(set(have.items()) ^ set(want.items()))} '
                   f'entries besides main and the module')
    print(f'  table of contents: {len(old)} files as before, main replaced and the module added in '
          f'{len(plan)} package(s)')


def verify(path, plan, module_sha):
    """Read the copy back with the repository's .nup reader: each patched package's main and module, with their
    sha256 checked, the patch's branch and marker in main, and one file of every other folder still readable."""
    verify_file(path)
    n = nup.Nup(path)
    for p, exefs, mi, name, patched in plan:
        b = builds.BUILDS[name]
        files = dict(n.tree(n.packages[p]['exefs']))
        main = n.read(files['main'])
        module = n.read(files[MODULE])
        if hashlib.sha256(module).digest() != module_sha or main != patched:
            raise Fail('the copy does not read back as written')
        t_off = struct.unpack_from('<I', main, 0x10)[0]
        stored = struct.unpack_from('<I', main, 0x60)[0]
        flags = struct.unpack_from('<I', main, 0xC)[0]
        text = main[t_off:t_off + stored]
        if flags & 1:
            text = zbic().decompress(text)
        site = struct.unpack_from('<I', text, b['frame_site'])[0]
        if site != mkpatch.site_word(b) or text[builds.stub_addr(b):builds.stub_addr(b) + 8] != builds.MARKER:
            raise Fail('the patched main does not hold the branch and the stub')
        seen = 0
        for rel, fi in n.tree(n.packages[p]['root']):
            if n.size(fi) < (1 << 20):
                n.read(fi)
                seen += 1
                if seen >= 64:
                    break
    print('  checked: the copy reads back, main holds the hook, the module is in exefs')


def main():
    ap = argparse.ArgumentParser(description='Write a copy of a .nup with the OOT-PC debug module in it.')
    ap.add_argument('nup', help='the game .nup (only read; with --repair, the copy to repair)')
    ap.add_argument('--repair', action='store_true',
                    help='move the TOC of a copy an earlier version made (TOC at the end) to 0x80, in place')
    ap.add_argument('--out', help='the copy (default: next to it, "... (OOT-PC debug).nup")')
    ap.add_argument('--plain-text', action='store_true', help='store main\'s patched code unpacked')
    ap.add_argument('--dry-run', action='store_true', help='patch main and build the new TOC, write nothing')
    args = ap.parse_args()
    try:
        if args.repair:
            repair(args.nup)
        else:
            run(args.nup, args.out, args.plain_text, args.dry_run)
    except Fail as e:
        print(f'\nERROR: {e}')
        return 1
    except KeyboardInterrupt:
        print('\nCancelled; nothing was written.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
