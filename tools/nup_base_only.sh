#!/usr/bin/env bash
# Writes a copy of a .nup dump of Ocarina of Time that holds only the base game (1.0.0), without the update.
# The original .nup is only read, never changed. The copy goes next to it, named "... (base game only).nup".
#
# Use:   bash nup_base_only.sh "/path/to/game.nup"     (or start it with no argument and paste the path)
# Needs: bash and Python 3 (standard library only). It needs about as much free space as the original .nup,
#        because the base game is almost all of it.
# Set NUP_STRIP_CHECK=1 to only check the .nup and print what it would write, NUP_NOPAUSE=1 to start without asking.
#
# The work is done by the Python code below, which this script hands to Python 3. It does what nup_base_only.bat does.
set -u

src="${1:-}"
if [ -z "$src" ]; then
    echo "Give the path of your .nup file and press Enter."
    read -r -p "Path: " src
    src="${src%\"}"; src="${src#\"}"; src="${src%\'}"; src="${src#\'}"
fi
[ -n "$src" ] || exit 1

py=""
for cand in python3 python "py -3"; do
    if $cand -c 'import sys; sys.exit(sys.version_info < (3, 6))' >/dev/null 2>&1; then py="$cand"; break; fi
done
if [ -z "$py" ]; then
    echo "ERROR: this script needs Python 3, and none was found (tried python3, python and py -3)."
    exit 1
fi

code=$(cat <<'PYTHON'
# The .nup layout (little endian, nothing encrypted), as tools/nup.py reads it:
#   0x00 superblock, 0x80 bytes: "NUP\x01", u16, u16, u64 toc offset, u64 toc size, sha256(toc), u64 blob offset,
#        u64 file size, u32 package count, u32, 16-byte id, 32 bytes
#   TOC: 0x30 header (u32 magic, u32 packages, u64 dirs, u64 files, u64 name pool offset, u64 pool length, u64),
#        packages 0x40 each, dirs 0x18 each, files 0x50 each, children u32 each, then the name pool.
#   A child below the dir count is a dir, else dir count + file index. File data offsets are absolute, and files with
#   the same bytes share one copy of them.
# The base game's dirs, files and children come first in the TOC, so the copy keeps that front part, renumbers the
# children that name files (the dir count shrinks), drops the update's package, and copies only the data the base
# game's files use, checking each file's sha256 on the way.
import hashlib
import os
import re
import shutil
import struct
import sys
import time

PKG, DIR, FIL, HDR = 0x40, 0x18, 0x50, 0x30
CHUNK = 8 << 20


class Fail(Exception):
    pass


def u32(b, o):
    return struct.unpack_from('<I', b, o)[0]


def u64(b, o):
    return struct.unpack_from('<Q', b, o)[0]


def align(v, a):
    return (v + a - 1) // a * a


def gb(v):
    return f'{v / 1e9:.2f} GB'


def read_full(f, n):
    b = f.read(n)
    if len(b) != n:
        raise Fail('the .nup ends early: it is incomplete or damaged')
    return b


def main(src, check_only, ask):
    src = os.path.abspath(src.strip())
    if not os.path.isfile(src):
        raise Fail('file not found: ' + src)
    print('Reading ' + src)

    with open(src, 'rb') as f:
        size = os.fstat(f.fileno()).st_size
        if size < 0x80:
            raise Fail('not a .nup file (too short)')
        sb = read_full(f, 0x80)
        if sb[:4] != b'NUP\x01':
            raise Fail('not a .nup file')
        toc_off, toc_size = u64(sb, 8), u64(sb, 16)
        if toc_off > size or toc_size > size - toc_off:
            raise Fail('the table of contents is damaged')
        f.seek(toc_off)
        toc = read_full(f, toc_size)
    if hashlib.sha256(toc).digest() != sb[24:56]:
        raise Fail('the table of contents is damaged (its sha256 does not match)')

    if u32(toc, 0) != 0x01434F54:
        raise Fail('unknown table of contents')
    npkg, ndir, nfile = u32(toc, 4), u64(toc, 8), u64(toc, 16)
    pool_off, pool_len = u64(toc, 24), u64(toc, 32)
    pkg_at = HDR
    dir_at = pkg_at + npkg * PKG
    file_at = dir_at + ndir * DIR
    child_at = file_at + nfile * FIL
    if child_at > pool_off or pool_off + pool_len > len(toc):
        raise Fail('the table of contents is malformed')
    nchild = (pool_off - child_at) // 4

    # The base game's package, and what else the .nup holds
    base_pkg = -1
    for i in range(npkg):
        p = pkg_at + i * PKG
        kind = toc[p + 24]
        what = {0: 'base game', 1: 'update', 2: 'DLC'}.get(kind, f'kind {kind}')
        print(f'  package {i}: {u64(toc, p):016X} v{u32(toc, p + 20)} ({what})')
        if kind == 0:
            if base_pkg >= 0:
                raise Fail('this .nup holds more than one base game')
            base_pkg = i
    if base_pkg < 0:
        raise Fail('this .nup holds no base game')
    if npkg == 1:
        raise Fail('this .nup already holds only the base game')
    bp = pkg_at + base_pkg * PKG
    base_ver = u32(toc, bp + 20)

    # Every dir and file below the base game's root
    dirs, files, child_end = set(), [], 0
    stack = [u32(toc, bp + 8)]
    while stack:
        d = stack.pop()
        if d >= ndir or d in dirs:
            raise Fail(f'the table of contents is malformed (dir {d})')
        dirs.add(d)
        first, count = u32(toc, dir_at + d * DIR + 8), u32(toc, dir_at + d * DIR + 12)
        if first + count > nchild:
            raise Fail(f'the table of contents is malformed (children of dir {d})')
        child_end = max(child_end, first + count)
        for c in range(first, first + count):
            v = u32(toc, child_at + c * 4)
            if v < ndir:
                stack.append(v)
            elif v - ndir < nfile:
                files.append(v - ndir)
            else:
                raise Fail(f'the table of contents is malformed (child {c})')
    D, F, C = len(dirs), len(files), child_end
    if any(d >= D for d in dirs):
        raise Fail("the base game's dirs are not the first in the .nup, which this tool cannot handle")
    if len(set(files)) != F:
        raise Fail('the table of contents is malformed (a file is listed twice)')
    if any(fi >= F for fi in files):
        raise Fail("the base game's files are not the first in the .nup, which this tool cannot handle")

    # The data the base game uses: each stored range once, in the order it sits in the .nup
    ranges = {}   # old offset -> (stored size, a file index to check its sha256 against)
    for fi in range(F):
        e = file_at + fi * FIL
        off, stored = u64(toc, e + 16), u64(toc, e + 24)
        if toc[e + 64] > 1:
            raise Fail(f'file {fi} uses an unknown codec')
        if off in ranges:
            if ranges[off][0] != stored:
                raise Fail(f'two files share data of different sizes at {off}')
        else:
            ranges[off] = (stored, fi)
    meta_off, meta_len = u64(toc, bp + 32), u32(toc, bp + 40)

    # The new layout: superblock, table of contents, the package's meta, then the data
    n_at = HDR + PKG + D * DIR + F * FIL + C * 4
    n_pool_off = align(n_at, 16)
    n_toc_size = align(n_pool_off + pool_len, 16)
    n_meta_off = align(0x80 + n_toc_size, 16)
    n_blob = align(n_meta_off + meta_len, 64)
    new_off = {}
    cur = end = n_blob
    data_bytes = 0
    for off in sorted(ranges):
        stored = ranges[off][0]
        new_off[off] = cur
        end = cur + stored
        cur = align(end, 16)
        data_bytes += stored
    total = end

    nt = bytearray(n_toc_size)
    nt[0:HDR] = toc[0:HDR]
    struct.pack_into('<I', nt, 4, 1)
    struct.pack_into('<QQQ', nt, 8, D, F, n_pool_off)
    nt[HDR:HDR + PKG] = toc[bp:bp + PKG]
    struct.pack_into('<Q', nt, HDR + 32, n_meta_off)
    n_dir_at = HDR + PKG
    n_file_at = n_dir_at + D * DIR
    n_child_at = n_file_at + F * FIL
    nt[n_dir_at:n_dir_at + D * DIR] = toc[dir_at:dir_at + D * DIR]
    nt[n_file_at:n_file_at + F * FIL] = toc[file_at:file_at + F * FIL]
    for fi in range(F):
        e = n_file_at + fi * FIL
        struct.pack_into('<Q', nt, e + 16, new_off[u64(nt, e + 16)])
    for c in range(C):
        v = u32(toc, child_at + c * 4)
        struct.pack_into('<I', nt, n_child_at + c * 4, v if v < ndir else v - ndir + D)
    nt[n_pool_off:n_pool_off + pool_len] = toc[pool_off:pool_off + pool_len]

    folder = os.path.dirname(src)
    stem = re.sub(r'\s*\[v\d+\].*$', '', os.path.splitext(os.path.basename(src))[0])
    dst = os.path.join(folder, f'{stem}[v{base_ver}] (base game only).nup')
    part = dst + '.partial'

    print()
    print(f'Keeps the base game (v{base_ver}): {F} files, {gb(data_bytes)} of data.')
    print('Writes ' + dst)
    print('  size ' + gb(total))
    free = shutil.disk_usage(folder).free
    print(f'  free space there {gb(free)}')
    if check_only:
        print()
        print('Check only: nothing written.')
        return 0
    if os.path.exists(dst):
        raise Fail('the copy already exists. Delete or rename it first: ' + dst)
    if free < total + (256 << 20):
        raise Fail(f'not enough free space (needs {gb(total)})')
    if ask:
        print()
        input('Press Enter to start, or Ctrl+C to cancel. ')

    nsb = bytearray(sb)
    struct.pack_into('<QQ', nsb, 8, 0x80, n_toc_size)
    nsb[24:56] = hashlib.sha256(nt).digest()
    struct.pack_into('<QQI', nsb, 56, n_blob, total, 1)

    started = time.time()
    try:
        copy(src, part, nt, nsb, toc, ranges, new_off, file_at, meta_off, meta_len, n_meta_off, data_bytes, total)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    os.replace(part, dst)
    print()
    print()
    print(f'Done in {int((time.time() - started) // 60)} min. The base-game-only copy is:')
    print('  ' + dst)
    return 0


def copy(src, part, nt, nsb, toc, ranges, new_off, file_at, meta_off, meta_len, n_meta_off, data_bytes, total):
    with open(src, 'rb') as inp, open(part, 'wb') as out:
        out.write(bytes(0x80))   # the superblock goes in last, once everything else is written
        out.write(nt)
        inp.seek(meta_off)
        meta = read_full(inp, meta_len)
        out.seek(n_meta_off)
        out.write(meta)

        done, shown = 0, -1
        for off in sorted(ranges):
            stored, fi = ranges[off]
            e = file_at + fi * FIL
            check = toc[e + 64] == 0   # stored as is: its sha256 is of these bytes
            if inp.tell() != off:
                inp.seek(off)
            out.seek(new_off[off])
            h = hashlib.sha256()
            left = stored
            while left:
                b = read_full(inp, min(CHUNK, left))
                if check:
                    h.update(b)
                out.write(b)
                left -= len(b)
                done += len(b)
            if check and (h.digest() != toc[e + 32:e + 64] or u64(toc, e + 8) != stored):
                raise Fail(f'file {fi} in the .nup is damaged (its sha256 does not match), so no copy was made.')
            pct = 100 if data_bytes == 0 else done * 100 // data_bytes
            if pct != shown:
                shown = pct
                print(f'\r  copying: {pct}%  ({gb(done)} of {gb(data_bytes)})   ', end='', flush=True)
        out.truncate(total)
        out.seek(0)
        out.write(nsb)
        out.flush()
        os.fsync(out.fileno())


try:
    sys.exit(main(sys.argv[1], os.environ.get('NUP_STRIP_CHECK') == '1', not os.environ.get('NUP_NOPAUSE')))
except Fail as e:
    print()
    print('ERROR: ' + str(e))
except KeyboardInterrupt:
    print()
    print('Cancelled: nothing was kept.')
except Exception as e:
    print()
    print(f'ERROR: {type(e).__name__}: {e}')
sys.exit(1)
PYTHON
)

$py -I -c "$code" "$src"
