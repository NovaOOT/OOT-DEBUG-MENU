"""Search the unpacked game files for any word and read every file it is in, decoded.

    python tools/romfs_search.py --root F:\\OOT-romfs-extracted

opens the search page in the browser (http://127.0.0.1:8765). The first start indexes the folder once: the strings in
every file (names, parameters, message text, whole numbers of four digits or more, and the file paths) go into an
SQLite file, build/romfs-search/index.sqlite unless --index says otherwise. That read takes a while on a big folder;
the page shows its progress. Later starts reuse the index and need no arguments, since it remembers the folder. The
page's Update button, or --update, re-reads only the files that changed; --reindex reads every file again.

The folder is the romfs as the extraction leaves it: archives unpacked into folders of the archive's name, files
decompressed, and each BYML file next to a .json of its contents (a missing .json is made on the fly). The page shows
each file decoded:
- JSON and other text as it is, a BYML file through its JSON, with each hashed number that _hash_names.pkl (in the
  folder) knows followed by its name
- MSBT message files as one block per label, with control tags as [group.type: parameters]
- BFEVFL event flows as their entry points, each with the events it runs in order (forks, joins, switches and
  subflows laid out), every actor and every parameter
- every other file as the strings it holds, in file order with their offsets
and a Strings and a Hex view of any file, the hex view around each match. Matches are highlighted; double-click a name
in the viewer to search for it.

Standard library only, Python 3.10 or later.
"""
import argparse
import html
import json
import multiprocessing
import os
import pickle
import re
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO = Path(__file__).resolve().parent.parent
DEFAULT_INDEX = REPO / 'build' / 'romfs-search' / 'index.sqlite'
SKIP_FILES = {'_extract.log', '_errors.txt', '_hash_names.pkl'}
HASH_FILE = '_hash_names.pkl'
TEXT_EXT = {'.json', '.txt', '.xml', '.csv', '.yaml', '.yml', '.ini', '.log', '.html', '.htm', '.lua', '.md', '.cfg'}
BYML_EXT = ('.byml', '.bgyml')
# Formats made of names and parameters: every NUL-terminated string in them is indexed. Any other binary file is
# mostly pixel, vertex, collision, audio or video data, and only NUL-bounded strings shaped like names are taken from it
# (see RE_IDENT), so the index does not fill with runs of bytes that merely happen to be printable.
STRUCTURED_EXT = {'.bfevfl', '.ainb', '.asb', '.baev', '.belnk', '.bslnk', '.byml', '.bgyml', '.baatarc', '.bagst',
                  '.baglmf', '.bgmsconf', '.bgapkglist', '.bgapkginfo', '.bptclconf', '.rsizetable', '.buil', '.bbox',
                  '.bnvib', '.sarc', '.bfarc'}
# Audio, video and terrain grids hold no names: only their paths are indexed.
NO_CONTENT_EXT = {'.bwav', '.bfwav', '.bfstm', '.mp4', '.webm', '.usm', '.hght', '.extm', '.mate'}
CHUNK = 32 << 20             # files are read in pieces of this size
MAX_STR = 400                # longer strings are cut to this many characters in the index
MAX_MSG = 4000               # message text is kept up to this length
VIEW_FULL = 4_000_000        # a decoded view longer than this many characters shows only the parts around matches
MAX_HITS_STRINGS = 50_000    # a search stops collecting matching strings here
MAX_HITS_FILES = 5_000       # and lists at most this many files

RE_ASCII = re.compile(rb'(?:[\x20-\x7e]|[\xc2-\xdf][\x80-\xbf]|[\xe0-\xef][\x80-\xbf]{2}){4,}')
# Strings in the binary formats end with a NUL. Requiring it drops most of the printable runs that raw data (audio,
# collision meshes, pixels) happens to hold, and requiring a NUL in front as well, plus a name's shape, drops nearly
# all of them in the formats that are mostly such data.
RE_ASCII_Z = re.compile(rb'(?:[\x20-\x7e]|[\xc2-\xdf][\x80-\xbf]|[\xe0-\xef][\x80-\xbf]{2}){4,}(?=\0)')
RE_IDENT = re.compile(rb'(?<=\0)[A-Za-z0-9_][A-Za-z0-9_./\-:@]{3,}(?=\0)')
RE_HAS_WORD = re.compile(rb'[a-z]{2}|_[A-Za-z]')   # what a name has and a run of random capitals and digits lacks
RE_JSTR = re.compile(rb'"((?:[^"\\\r\n]|\\.)*)"')
RE_JINT = re.compile(rb'(?<![\w.])-?\d{4,20}(?![\w.])')
RE_JINT_S = re.compile(r'(?<![\w.])-?\d{4,20}(?![\w.])')


def ext_of(name):
    i = name.rfind('.')
    return name[i:].lower() if i > 0 else ''


# ---- hash names

class _PlainData(pickle.Unpickler):
    """Loads dicts, lists, tuples, sets, numbers and strings only: no class of any module runs."""
    def find_class(self, module, name):
        if module == 'builtins' and name in ('set', 'frozenset'):
            return {'set': set, 'frozenset': frozenset}[name]
        raise pickle.UnpicklingError('%s.%s is not plain data' % (module, name))


def load_hashes(root):
    """The folder's _hash_names.pkl as {number: name}, or {} when it is missing or holds something else."""
    p = os.path.join(root, HASH_FILE)
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, 'rb') as f:
            d = _PlainData(f).load()
    except Exception:
        return {}
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(k, int) and isinstance(v, str):
                if v and k > 999:
                    out[k] = v
            elif isinstance(k, str) and isinstance(v, int):
                out[v] = k
            elif isinstance(k, int) and isinstance(v, (list, tuple, set)) and v:
                out[k] = ' | '.join(sorted(str(x) for x in v))
    return out


# ---- reading files in pieces

def read_pieces(path, cut_at=(b'\n',)):
    """The file's bytes in pieces of about CHUNK bytes, each ending at one of cut_at where one is near the end, so a
    string seldom falls across two pieces."""
    with open(path, 'rb') as f:
        carry = b''
        while True:
            blk = f.read(CHUNK)
            if not blk:
                if carry:
                    yield carry
                return
            blk = carry + blk
            cut = max(blk.rfind(c) for c in cut_at)
            if cut < len(blk) - 65536:
                cut = len(blk) - 1
            yield blk[:cut + 1]
            carry = blk[cut + 1:]


def _clip(s, n=MAX_STR):
    s = s.strip()
    return s[:n] if len(s) > n else s


# ---- index: what one file contributes

_W_ROOT = None
_W_HASHES = {}


def _worker_init(root):
    global _W_ROOT, _W_HASHES
    _W_ROOT = root
    _W_HASHES = load_hashes(root)


def strings_json(path, out, hashes):
    for piece in read_pieces(path):
        for m in RE_JSTR.finditer(piece):
            raw = m.group(1)
            if not raw:
                continue
            if b'\\' in raw:
                try:
                    s = json.loads(b'"' + raw + b'"')
                except ValueError:
                    s = raw.decode('utf-8', 'replace')
            else:
                s = raw.decode('utf-8', 'replace')
            if len(s) >= 2:
                out.add(_clip(s))
        for m in RE_JINT.finditer(piece):
            t = m.group(0).decode()
            out.add(t)
            name = hashes.get(int(t))
            if name:
                out.add(_clip(name))


def strings_text(path, out):
    for piece in read_pieces(path):
        for m in RE_ASCII.finditer(piece):
            out.add(_clip(m.group(0).decode('utf-8', 'replace')))


def strings_binary(path, out, blob, stop_at=None):
    """Strings of a binary file into out; reading ends at the first piece holding stop_at (where the names end and
    the bulk data begins)."""
    rx = RE_IDENT if blob else RE_ASCII_Z
    for piece in read_pieces(path, (b'\0', b'\n')):
        if stop_at is not None:
            cut = piece.find(stop_at)
            if cut >= 0:
                piece = piece[:cut]
        for m in rx.finditer(piece):
            s = m.group(0)
            if blob and not RE_HAS_WORD.search(s):
                continue
            s = _clip(s.decode('utf-8', 'replace'))
            if len(s) >= 4:
                out.add(s)
        if stop_at is not None and cut >= 0:
            return


def index_one(rel):
    """(rel, size, mtime, strings) for one file under the worker's root."""
    path = os.path.join(_W_ROOT, rel)
    try:
        st = os.stat(path)
    except OSError:
        return rel, -1, 0, []
    ext = ext_of(rel)
    out = set()
    try:
        if ext == '.json':
            strings_json(path, out, _W_HASHES)
        elif ext in TEXT_EXT:
            strings_text(path, out)
        elif ext == '.msbt':
            with open(path, 'rb') as f:
                b = f.read()
            for label, text in msbt_messages(b):
                out.add(label)
                plain = re.sub(r'\[[^\]]*\]', '', text)
                for s in (text, plain):
                    if s.strip():
                        out.add(_clip(s, MAX_MSG))
            strings_binary(path, out, False)
        elif ext in BYML_EXT and os.path.isfile(path + '.json'):
            pass   # its .json says the same, decoded
        elif ext in NO_CONTENT_EXT:
            pass
        elif ext == '.bntx':
            strings_binary(path, out, True, b'BRTD')   # the pixel data block comes after the names
        else:
            strings_binary(path, out, ext not in STRUCTURED_EXT)
    except Exception:
        try:
            strings_binary(path, out, True)
        except Exception:
            pass
    return rel, st.st_size, int(st.st_mtime), list(out)


# ---- index: building

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY, path TEXT NOT NULL, size INTEGER, mtime INTEGER);
CREATE TABLE IF NOT EXISTS strs(id INTEGER PRIMARY KEY, s TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS refs(fid INTEGER NOT NULL, sid INTEGER NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS files_path ON files(path);
"""


class Status:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = 'idle'     # idle, scanning, indexing, finishing, ready, error
        self.done = 0
        self.total = 0
        self.message = ''
        self.started = 0.0

    def set(self, **kw):
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def snapshot(self):
        with self.lock:
            return {'state': self.state, 'done': self.done, 'total': self.total, 'message': self.message,
                    'elapsed': round(time.time() - self.started, 1) if self.started else 0}


def connect(db_path):
    con = sqlite3.connect(str(db_path), check_same_thread=False)
    con.executescript(SCHEMA)
    return con


def meta_get(con, k, default=None):
    r = con.execute('SELECT v FROM meta WHERE k=?', (k,)).fetchone()
    return r[0] if r else default


def meta_set(con, k, v):
    con.execute('INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)', (k, str(v)))


def list_files(root, status):
    out = []
    stack = ['']
    while stack:
        rel = stack.pop()
        try:
            it = os.scandir(os.path.join(root, rel))
        except OSError:
            continue
        with it:
            for e in it:
                r = e.name if not rel else rel + '/' + e.name
                try:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(r)
                    elif not (not rel and e.name in SKIP_FILES):
                        st = e.stat()
                        out.append((r, st.st_size, int(st.st_mtime)))
                except OSError:
                    pass
        if len(out) % 5000 < 50:
            status.set(message='listing files: %d so far' % len(out))
    return out


def build_index(root, db_path, status, full=False, workers=None):
    status.set(state='scanning', done=0, total=0, message='listing files', started=time.time())
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = connect(db_path)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA synchronous=OFF')
    if full or meta_get(con, 'root') not in (None, root):
        con.executescript('DROP INDEX IF EXISTS refs_sid; DELETE FROM refs; DELETE FROM strs; DELETE FROM files;')
    meta_set(con, 'root', root)
    meta_set(con, 'complete', 0)
    con.commit()

    on_disk = list_files(root, status)
    have = {p: (i, s, m) for i, p, s, m in con.execute('SELECT id, path, size, mtime FROM files')}
    disk_paths = {p for p, _, _ in on_disk}
    gone = [have[p][0] for p in have if p not in disk_paths]
    todo = [p for p, s, m in on_disk if p not in have or have[p][1:] != (s, m)]
    gone += [have[p][0] for p in todo if p in have]
    if gone:
        con.execute('CREATE TEMP TABLE gone(id INTEGER PRIMARY KEY)')
        con.executemany('INSERT INTO gone VALUES(?)', ((i,) for i in gone))
        con.execute('DELETE FROM refs WHERE fid IN (SELECT id FROM gone)')
        con.execute('DELETE FROM files WHERE id IN (SELECT id FROM gone)')
        con.execute('DROP TABLE gone')
        con.commit()
    bulk = len(todo) > 5000
    if bulk:
        con.execute('DROP INDEX IF EXISTS refs_sid')

    status.set(state='indexing', total=len(todo), message='reading files')
    sid = {}
    for i, s in con.execute('SELECT id, s FROM strs'):
        sid[s] = i
    next_sid = (con.execute('SELECT max(id) FROM strs').fetchone()[0] or 0) + 1
    next_fid = (con.execute('SELECT max(id) FROM files').fetchone()[0] or 0) + 1
    new_strs, new_refs, new_files = [], [], []

    def flush():
        con.executemany('INSERT INTO strs(id, s) VALUES(?, ?)', new_strs)
        con.executemany('INSERT INTO refs(fid, sid) VALUES(?, ?)', new_refs)
        con.executemany('INSERT INTO files(id, path, size, mtime) VALUES(?, ?, ?, ?)', new_files)
        con.commit()
        new_strs.clear(); new_refs.clear(); new_files.clear()

    workers = workers or max(1, min(12, (os.cpu_count() or 4)))
    # Small files first in each batch keep the progress figure moving; the order does not matter otherwise.
    with multiprocessing.Pool(workers, _worker_init, (root,)) as pool:
        for n, (rel, size, mtime, strs) in enumerate(pool.imap_unordered(index_one, todo, chunksize=16), 1):
            if size < 0:
                continue
            fid = next_fid
            next_fid += 1
            new_files.append((fid, rel, size, mtime))
            name = rel.rsplit('/', 1)[-1]
            for s in set(strs) | {rel, name}:
                i = sid.get(s)
                if i is None:
                    i = sid[s] = next_sid
                    next_sid += 1
                    new_strs.append((i, s))
                new_refs.append((fid, i))
            if len(new_refs) > 400_000 or n % 2000 == 0:
                flush()
                status.set(done=n, message='%d strings so far' % len(sid))
    flush()
    status.set(state='finishing', done=len(todo), message='writing the search index (a few minutes on a full build)')
    con.execute('CREATE INDEX IF NOT EXISTS refs_sid ON refs(sid, fid)')
    meta_set(con, 'complete', 1)
    meta_set(con, 'built', time.strftime('%Y-%m-%d %H:%M'))
    con.commit()
    con.execute('PRAGMA optimize')
    con.close()
    status.set(state='ready', message='%d files, %d strings' % (len(disk_paths), len(sid)))


# ---- search

def murmur3(s, seed=0):
    """32-bit MurmurHash3 of the UTF-8 bytes: the hash the game stores in place of many names (flags, actors)."""
    key = s.encode('utf-8')
    n, h, c1, c2, m = len(key), seed, 0xcc9e2d51, 0x1b873593, 0xffffffff
    for i in range(0, n // 4 * 4, 4):
        k = int.from_bytes(key[i:i + 4], 'little') * c1 & m
        k = ((k << 15) | (k >> 17)) & m
        h ^= k * c2 & m
        h = ((h << 13) | (h >> 19)) & m
        h = (h * 5 + 0xe6546b64) & m
    tail = key[n // 4 * 4:]
    if tail:
        k = int.from_bytes(tail, 'little') * c1 & m
        k = ((k << 15) | (k >> 17)) & m
        h ^= k * c2 & m
    h ^= n
    h ^= h >> 16
    h = h * 0x85ebca6b & m
    h ^= h >> 13
    h = h * 0xc2b2ae35 & m
    return h ^ (h >> 16)


RE_NAME = re.compile(r'[A-Za-z_][\w.\-/]{2,}')


def search(con, q, case):
    # A name is also looked for as its hash, a decimal number the way the JSON files hold it.
    hq = str(murmur3(q)) if RE_NAME.fullmatch(q) else ''
    if case:
        rows = con.execute('SELECT id, s FROM strs WHERE instr(s, ?) > 0 OR s = ? LIMIT ?',
                           (q, hq, MAX_HITS_STRINGS + 1)).fetchall()
    else:
        like = '%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        rows = con.execute("SELECT id, s FROM strs WHERE s LIKE ? ESCAPE '\\' OR s = ? LIMIT ?",
                           (like, hq, MAX_HITS_STRINGS + 1)).fetchall()
        if not q.isascii():   # LIKE folds ASCII letters only
            ql = q.lower()
            rows = [r for r in rows if ql in r[1].lower() or r[1] == hq]
    more_strings = len(rows) > MAX_HITS_STRINGS
    rows = rows[:MAX_HITS_STRINGS]
    text = dict(rows)
    per_file = {}
    ids = list(text)
    for k in range(0, len(ids), 500):
        part = ids[k:k + 500]
        for fid, s in con.execute('SELECT fid, sid FROM refs WHERE sid IN (%s)' % ','.join('?' * len(part)), part):
            per_file.setdefault(fid, []).append(s)
    more_files = len(per_file) > MAX_HITS_FILES
    fids = sorted(per_file)
    files = {}
    for k in range(0, len(fids), 500):
        part = fids[k:k + 500]
        for fid, path, size in con.execute('SELECT id, path, size FROM files WHERE id IN (%s)'
                                           % ','.join('?' * len(part)), part):
            files[fid] = (path, size)
    # A BYML file and its .json are one entry, opened through the .json.
    merged = {}
    for fid, sids in per_file.items():
        if fid not in files:
            continue
        path, size = files[fid]
        is_json = path.endswith('.json') and ext_of(path[:-5]) in BYML_EXT
        shown = path[:-5] if is_json else path
        e = merged.setdefault(shown, {'path': path, 'shown': shown, 'size': size, 'names': set()})
        if is_json:
            e['path'], e['size'] = path, size
        e['names'].update(text[s] for s in sids)
    out = []
    for e in merged.values():
        own = {e['path'], e['shown'], e['path'].rsplit('/', 1)[-1], e['shown'].rsplit('/', 1)[-1]}
        names = sorted(e.pop('names') - own, key=lambda s: (len(s), s))   # the file's own name is in the list already
        e.update(n=len(names), strings=names[:6])
        out.append(e)
    out.sort(key=lambda r: r['shown'].lower())
    return {'files': out[:MAX_HITS_FILES], 'more_files': more_files, 'more_strings': more_strings,
            'nstrings': len(rows), 'hash': hq if any(r[1] == hq for r in rows) else ''}


# ---- decoders

def msbt_messages(b):
    """[(label, text)] of an MSBT file, in message order; control tags become [group.type: params]."""
    if b[:8] != b'MsgStdBn':
        raise ValueError('not an MSBT file')
    e = '<' if b[8:10] == b'\xff\xfe' else '>'
    enc = b[0x0c]
    nsec = struct.unpack_from(e + 'H', b, 0x0e)[0]
    off, secs = 0x20, {}
    for _ in range(nsec):
        if off + 0x10 > len(b):
            break
        magic = b[off:off + 4]
        size = struct.unpack_from(e + 'I', b, off + 4)[0]
        secs[magic] = (off + 0x10, size)
        off = (off + 0x10 + size + 15) & ~15
    labels = {}
    if b'LBL1' in secs:
        d, _ = secs[b'LBL1']
        for g in range(struct.unpack_from(e + 'I', b, d)[0]):
            cnt, o = struct.unpack_from(e + 'II', b, d + 4 + 8 * g)
            p = d + o
            for _ in range(cnt):
                ln = b[p]
                name = b[p + 1:p + 1 + ln].decode('utf-8', 'replace')
                labels[struct.unpack_from(e + 'I', b, p + 1 + ln)[0]] = name
                p += 1 + ln + 4
    if b'TXT2' not in secs:
        return [(labels[k], '') for k in sorted(labels)]
    d, size = secs[b'TXT2']
    n = struct.unpack_from(e + 'I', b, d)[0]
    offs = struct.unpack_from(e + '%dI' % n, b, d + 4)
    out = []
    for i, o in enumerate(offs):
        end = offs[i + 1] if i + 1 < n else size
        out.append((labels.get(i, '#%d' % i), _msbt_text(b[d + o:d + end], e, enc)))
    return out


def _msbt_tag_params(p, e, codec):
    """A tag's parameters as text: the strings in them (each a u16 byte length, the text, maybe a NUL) quoted, other
    bytes in hex."""
    parts, i = [], 0
    while i < len(p):
        if i + 2 <= len(p):
            ln = struct.unpack_from(e + 'H', p, i)[0]
            if 0 < ln <= len(p) - i - 2:
                try:
                    s = p[i + 2:i + 2 + ln].decode(codec)
                except UnicodeDecodeError:
                    s = None
                if s and s.isprintable():
                    parts.append('"%s"' % s)
                    i += 2 + ln
                    while i < len(p) and p[i] == 0 and len(parts) and (len(p) - i) % 2:
                        i += 1   # the string's NUL
                    continue
        rest = p[i:]
        parts.append(rest.hex(' '))
        break
    return ' '.join(parts)


def _msbt_text(raw, e, enc):
    """One message. A tag is the unit 0x0E then u16 group, type and parameter size and the parameters; an end tag
    the unit 0x0F then u16 group and type. A unit is a byte in UTF-8 files, two bytes in UTF-16 ones."""
    w = 2 if enc == 1 else 4 if enc == 2 else 1
    codec = {1: 'utf-16-le' if e == '<' else 'utf-16-be', 2: 'utf-32-le' if e == '<' else 'utf-32-be'}.get(enc, 'utf-8')
    unit = {1: 'B', 2: 'H', 4: 'I'}[w]
    out, run, i = [], bytearray(), 0
    while i + w <= len(raw):
        c = struct.unpack_from(e + unit, raw, i)[0]
        if c in (0, 0x0e, 0x0f):
            out.append(run.decode(codec, 'replace'))
            run = bytearray()
        if c == 0:
            break
        if c == 0x0e and i + w + 6 <= len(raw):
            g, t, sz = struct.unpack_from(e + 'HHH', raw, i + w)
            prm = raw[i + w + 6:i + w + 6 + sz]
            out.append('[%d.%d%s]' % (g, t, (': ' + _msbt_tag_params(prm, e, codec)) if prm else ''))
            i += w + 6 + sz
        elif c == 0x0f and i + w + 4 <= len(raw):
            g, t = struct.unpack_from(e + 'HH', raw, i + w)
            out.append('[/%d.%d]' % (g, t))
            i += w + 4
        else:
            run += raw[i:i + w]
            i += w
    if run:
        out.append(run.decode(codec, 'replace'))
    return ''.join(out)


def decode_msbt(b):
    msgs = msbt_messages(b)
    lines = ['MSBT message file, %d messages. Control tags read [group.type: parameters], an end tag [/group.type].'
             % len(msgs), '']
    for label, text in msgs:
        lines.append('[%s]' % label)
        lines.extend('    ' + ln for ln in (text.split('\n') if text else ['']))
        lines.append('')
    return '\n'.join(lines)


EVFL_EVENT_TYPES = ('Action', 'Switch', 'Fork', 'Join', 'Subflow')


def decode_evfl(b):
    """An event flow (BFEVFL, version 4 layout: actors 0x38 bytes, events 0x30, entry points 0x20) as readable text."""
    if b[:8] != b'BFEVFL\0\0':
        raise ValueError('not a BFEVFL file')
    u16 = lambda o: struct.unpack_from('<H', b, o)[0]
    u32 = lambda o: struct.unpack_from('<I', b, o)[0]
    i32 = lambda o: struct.unpack_from('<i', b, o)[0]
    u64 = lambda o: struct.unpack_from('<Q', b, o)[0]
    f32 = lambda o: struct.unpack_from('<f', b, o)[0]

    def s(p):
        if not p:
            return ''
        return b[p + 2:p + 2 + u16(p)].decode('utf-8', 'replace')

    def ws(p):
        if not p:
            return ''
        return b[p + 2:p + 2 + 2 * u16(p)].decode('utf-16-le', 'replace')

    def dic_names(d):
        if not d or b[d:d + 4] != b'DIC ':
            return []
        return [s(u64(d + 8 + 16 * (i + 1) + 8)) for i in range(u32(d + 4))]

    def value(c, depth=0):
        if not c or depth > 16:
            return None
        t, n, d = b[c], u16(c + 2), c + 0x10
        if t == 1:
            names = dic_names(u64(c + 8))
            return {(names[i] if i < len(names) else '#%d' % i): value(u64(d + 8 * i), depth + 1) for i in range(n)}
        if t == 0:
            return ('argument', s(u64(d)))
        if t in (2, 7):
            v = [i32(d + 4 * i) for i in range(max(n, 1))]
        elif t in (3, 8):
            v = [bool(u32(d + 4 * i)) for i in range(max(n, 1))]
        elif t in (4, 9):
            v = [float('%.7g' % f32(d + 4 * i)) for i in range(max(n, 1))]
        elif t in (5, 10):
            v = [s(u64(d + 8 * i)) for i in range(max(n, 1))]
        elif t in (6, 11):
            v = [ws(u64(d + 8 * i)) for i in range(max(n, 1))]
        elif t == 12:
            return ('actor', s(u64(d)), s(u64(d + 8)))
        else:
            return ('type %d' % t, b[c:c + 0x18].hex(' '))
        return v if t >= 7 else v[0]

    def fmt_value(v):
        if isinstance(v, tuple):
            if v[0] == 'argument':
                return 'argument ' + v[1]
            if v[0] == 'actor':
                return 'actor ' + v[1] + (' [%s]' % v[2] if v[2] else '')
            return '<%s: %s>' % v
        if isinstance(v, str):
            return json.dumps(v, ensure_ascii=False)
        if isinstance(v, list):
            return '[' + ', '.join(fmt_value(x) for x in v) + ']'
        return json.dumps(v)

    def params_lines(c, pad):
        v = value(c)
        if not isinstance(v, dict):
            return []
        out = []
        for k, x in v.items():
            if isinstance(x, dict):
                out.append('%s%s:' % (pad, k))
                out.extend(_dict_lines(x, pad + '    '))
            else:
                out.append('%s%s = %s' % (pad, k, fmt_value(x)))
        return out

    def _dict_lines(v, pad):
        out = []
        for k, x in v.items():
            if isinstance(x, dict):
                out.append('%s%s:' % (pad, k))
                out.extend(_dict_lines(x, pad + '    '))
            else:
                out.append('%s%s = %s' % (pad, k, fmt_value(x)))
        return out

    nfc, ntl = u16(0x20), u16(0x22)
    lines = []
    for f in range(nfc):
        fc = u64(u64(0x28) + 8 * f)
        if b[fc:fc + 4] != b'EVFL':
            raise ValueError('flowchart %d has no EVFL block' % f)
        na, _nact, _nq, nev, nep = (u16(fc + 0x10 + 2 * i) for i in range(5))
        name = s(u64(fc + 0x20))
        ap, evp, epd, epp = u64(fc + 0x28), u64(fc + 0x30), u64(fc + 0x38), u64(fc + 0x40)
        actors = []
        for i in range(na):
            a = ap + 0x38 * i
            nacts, nqs = u16(a + 0x30), u16(a + 0x32)
            actors.append({
                'name': s(u64(a)), 'sub': s(u64(a + 8)), 'arg': s(u64(a + 0x10)),
                'actions': [s(u64(u64(a + 0x18) + 8 * j)) for j in range(nacts)],
                'queries': [s(u64(u64(a + 0x20) + 8 * j)) for j in range(nqs)],
                'params': u64(a + 0x28),
            })

        def actor_name(i):
            if i >= len(actors):
                return 'actor#%d' % i
            a = actors[i]
            return a['name'] + ('[%s]' % a['sub'] if a['sub'] else '')

        events = []
        for i in range(nev):
            e = evp + 0x30 * i
            t = b[e + 8]
            ev = {'name': s(u64(e)), 'type': t}
            if t == 0:
                ev.update(next=u16(e + 0xa), actor=u16(e + 0xc), what=u16(e + 0xe), params=u64(e + 0x10))
            elif t == 1:
                ncase, cp = u16(e + 0xa), u64(e + 0x18)
                ev.update(actor=u16(e + 0xc), what=u16(e + 0xe), params=u64(e + 0x10),
                          cases=[(u32(cp + 0x10 * k), u16(cp + 0x10 * k + 4)) for k in range(ncase)])
            elif t == 2:
                nf, fp = u16(e + 0xa), u64(e + 0x10)
                ev.update(join=u16(e + 0xc), forks=[u16(fp + 2 * k) for k in range(nf)])
            elif t == 3:
                ev.update(next=u16(e + 0xa))
            elif t == 4:
                ev.update(next=u16(e + 0xa), params=u64(e + 0x10), flow=s(u64(e + 0x18)), entry=s(u64(e + 0x20)))
            events.append(ev)

        def ev_name(i):
            return events[i]['name'] if i < len(events) else 'event#%d' % i

        ep_names = dic_names(epd)
        entries = []
        for i in range(nep):
            p = epp + 0x20 * i
            entries.append((ep_names[i] if i < len(ep_names) else '#%d' % i, u16(p + 0x1c)))

        reached = set()

        def walk(idx, stop, pad, out, depth=0, end='(end)'):
            while idx != 0xffff and idx != stop:
                if idx >= len(events):
                    out.append(pad + '(bad event index %d)' % idx)
                    return
                if idx in reached:
                    out.append(pad + '-> continues at %s (shown above)' % ev_name(idx))
                    return
                reached.add(idx)
                ev, t = events[idx], events[idx]['type']
                if t == 0:
                    a = actors[ev['actor']] if ev['actor'] < len(actors) else None
                    act = a['actions'][ev['what']] if a and ev['what'] < len(a['actions']) else '#%d' % ev['what']
                    out.append('%s%s: %s.%s' % (pad, ev['name'], actor_name(ev['actor']), act))
                    out.extend(params_lines(ev['params'], pad + '      '))
                    idx = ev['next']
                elif t == 1:
                    a = actors[ev['actor']] if ev['actor'] < len(actors) else None
                    q = a['queries'][ev['what']] if a and ev['what'] < len(a['queries']) else '#%d' % ev['what']
                    out.append('%s%s: switch on %s.%s' % (pad, ev['name'], actor_name(ev['actor']), q))
                    out.extend(params_lines(ev['params'], pad + '      '))
                    for val, target in ev['cases']:
                        out.append('%s  case %d:' % (pad, val))
                        walk(target, stop, pad + '      ', out, depth + 1)
                    return
                elif t == 2:
                    out.append('%s%s: fork into %d branches, joining at %s'
                               % (pad, ev['name'], len(ev['forks']), ev_name(ev['join'])))
                    for k, f in enumerate(ev['forks']):
                        out.append('%s  branch %d:' % (pad, k + 1))
                        walk(f, ev['join'], pad + '      ', out, depth + 1, '(branch done)')
                    idx = ev['join']
                elif t == 3:
                    out.append('%s%s: join' % (pad, ev['name']))
                    idx = ev['next']
                elif t == 4:
                    out.append('%s%s: run subflow %s :: %s'
                               % (pad, ev['name'], ev['flow'] or '(this flow)', ev['entry']))
                    out.extend(params_lines(ev['params'], pad + '      '))
                    idx = ev['next']
                else:
                    out.append('%s%s: (event type %d)' % (pad, ev['name'], t))
                    return
            if idx == 0xffff:
                out.append(pad + end)

        lines.append('Event flow %s: %d actors, %d events, %d entry points%s'
                     % (name, na, nev, nep, (', %d timelines (not decoded)' % ntl) if ntl else ''))
        lines.append('')
        lines.append('== Entry points ==')
        for ep_name, main in entries:
            lines.append('')
            lines.append('Entry point "%s" (starts at %s)' % (ep_name, ev_name(main)))
            walk(main, None, '    ', lines)
        rest = [i for i in range(len(events)) if i not in reached]
        if rest:
            lines.append('')
            lines.append('== Events no entry point reaches ==')
            for i in rest:
                if i not in reached:
                    lines.append('')
                    walk(i, None, '    ', lines)
        lines.append('')
        lines.append('== Actors ==')
        for i, a in enumerate(actors):
            lines.append('')
            lines.append('[%d] %s%s' % (i, actor_name(i), ('  argument %s' % a['arg']) if a['arg'] else ''))
            if a['actions']:
                lines.append('    actions: ' + ', '.join(a['actions']))
            if a['queries']:
                lines.append('    queries: ' + ', '.join(a['queries']))
            pl = params_lines(a['params'], '        ')
            if pl:
                lines.append('    parameters:')
                lines.extend(pl)
        lines.append('')
    return '\n'.join(lines)


def byml_to_obj(b):
    """A BYML file (version 2 and later, either byte order) as plain Python data."""
    e = '<' if b[:2] == b'YB' else '>' if b[:2] == b'BY' else None
    if not e:
        raise ValueError('not a BYML file')
    u32 = lambda o: struct.unpack_from(e + 'I', b, o)[0]
    kt, st, root = u32(4), u32(8), u32(12)

    def table(off):
        if not off:
            return []
        n = u32(off) & 0xffffff if e == '>' else u32(off) >> 8
        out = []
        for i in range(n):
            o = off + u32(off + 4 + 4 * i)
            out.append(b[o:b.index(b'\0', o)].decode('utf-8', 'replace'))
        return out
    keys, strs = table(kt), table(st)

    def hdr(off):
        x = u32(off)
        return (b[off], x & 0xffffff) if e == '>' else (b[off], x >> 8)

    def val(t, v):
        if t == 0xA0:
            return strs[v]
        if t in (0xC0, 0xC1):
            return node(v)
        if t == 0xD0:
            return bool(v)
        if t == 0xD1:
            return struct.unpack(e + 'i', struct.pack(e + 'I', v))[0]
        if t == 0xD2:
            return struct.unpack(e + 'f', struct.pack(e + 'I', v))[0]
        if t == 0xD3:
            return v
        if t in (0xD4, 0xD5, 0xD6):
            return struct.unpack_from(e + {0xD4: 'q', 0xD5: 'Q', 0xD6: 'd'}[t], b, v)[0]
        if t == 0xFF:
            return None
        return '<type 0x%02x: %d>' % (t, v)

    def node(off):
        t, n = hdr(off)
        if t == 0xC0:
            types = b[off + 4:off + 4 + n]
            vo = off + 4 + ((n + 3) & ~3)
            return [val(types[i], u32(vo + 4 * i)) for i in range(n)]
        if t == 0xC1:
            d = {}
            for i in range(n):
                p = off + 4 + 8 * i
                x = u32(p)
                k = (x >> 8) if e == '>' else (x & 0xffffff)
                d[keys[k]] = val(b[p + 3], u32(p + 4))
            return d
        return '<node 0x%02x>' % t
    return node(root) if root else None


def strings_view(b, limit_lines=300_000):
    lines = []
    for m in RE_ASCII.finditer(b):
        s = m.group(0).decode('utf-8', 'replace').strip()
        if len(s) >= 4:
            lines.append('%08X  %s' % (m.start(), s))
            if len(lines) >= limit_lines:
                lines.append('... (stopped after %d strings)' % limit_lines)
                break
    return '\n'.join(lines)


def hex_view(b, q, case):
    pats = []
    if q:
        for enc in ('utf-8', 'utf-16-le'):
            try:
                pats.append(re.escape(q.encode(enc)))
            except UnicodeEncodeError:
                pass
    hits = []
    if pats:
        rx = re.compile(b'|'.join(pats), 0 if case else re.I)
        hits = [m.start() for m in rx.finditer(b)][:200]
    out = []
    spans = [(max(0, h - 128) & ~15, min(len(b), h + 160)) for h in hits] or [(0, min(len(b), 4096))]
    merged = []
    for a, z in spans:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(z, merged[-1][1]))
        else:
            merged.append((a, z))
    for a, z in merged:
        out.append('---- 0x%X' % a)
        for o in range(a, z, 16):
            row = b[o:o + 16]
            asc = ''.join(chr(c) if 32 <= c < 127 else '.' for c in row)
            out.append('%08X  %-48s  %s' % (o, row.hex(' '), asc))
    head = ('%d matches in the raw bytes (UTF-8 or UTF-16)' % len(hits)) if q else 'start of the file'
    return head + '\n\n' + '\n'.join(out)


RE_MSG_ID = re.compile(r'"([A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)+):([A-Za-z0-9_]+)"')


class Messages:
    """Message text by id ("Folder/File:Label") from one language's message archive, Mals/<lang>.*/."""
    def __init__(self, root, lang):
        self.base = None
        mals = os.path.join(root, 'Mals')
        if os.path.isdir(mals):
            for n in sorted(os.listdir(mals)):
                if n.startswith(lang + '.') and os.path.isdir(os.path.join(mals, n)):
                    self.base = os.path.join(mals, n)
        self.files = {}
        self.lock = threading.Lock()

    def get(self, file, label):
        if not self.base:
            return None
        with self.lock:
            if file not in self.files:
                p = os.path.join(self.base, *file.split('/')) + '.msbt'
                try:
                    with open(p, 'rb') as f:
                        self.files[file] = dict(msbt_messages(f.read()))
                except (OSError, ValueError, struct.error):
                    self.files[file] = {}
            return self.files[file].get(label)

    def annotate(self, text):
        """The text with each line that names a message followed by that message's text."""
        if not self.base or ':' not in text:
            return text
        out = []
        for ln in text.split('\n'):
            out.append(ln)
            for m in RE_MSG_ID.finditer(ln):
                msg = self.get(m.group(1), m.group(2))
                if msg:
                    pad = ' ' * (len(ln) - len(ln.lstrip()) + 4)
                    flat = msg.replace('\n', ' ')
                    out.append('%s>> "%s"' % (pad, flat if len(flat) < 400 else flat[:400] + '...'))
        return '\n'.join(out)


def annotate_hashes(text, hashes):
    if not hashes:
        return text

    def sub(m):
        name = hashes.get(int(m.group(0)))
        return '%s /* %s */' % (m.group(0), name) if name else m.group(0)
    return RE_JINT_S.sub(sub, text)


def excerpt(text, q, case, ctx=30, max_windows=400):
    """The lines around each match of a text too long to show whole."""
    lines = text.split('\n')
    if not q:
        return '\n'.join(lines[:20000]) + '\n... (the file goes on; search for something to see the parts that match)'
    ql = q if case else q.lower()
    hits = [i for i, ln in enumerate(lines) if ql in (ln if case else ln.lower())]
    out, last = [], -1
    for i in hits[:max_windows]:
        a, z = max(0, i - ctx), min(len(lines), i + ctx + 1)
        if a > last + 1:
            out.append('\n.... lines %d to %d ....' % (a + 1, z))
        out.extend(lines[max(a, last + 1):z])
        last = z - 1
    if len(hits) > max_windows:
        out.append('\n.... %d more matches not shown ....' % (len(hits) - max_windows))
    return '\n'.join(out) if out else '(no line of the decoded text matches)'


def view(root, rel, mode, q, case, hashes, messages=None):
    """(format, note, text) for a file under root."""
    path = os.path.normpath(os.path.join(root, rel))
    if os.path.commonpath([os.path.abspath(root), os.path.abspath(path)]) != os.path.abspath(root):
        raise ValueError('outside the folder')
    ext = ext_of(path)
    if mode == 'decoded' and ext in BYML_EXT and os.path.isfile(path + '.json'):
        path, ext = path + '.json', '.json'
    size = os.path.getsize(path)
    if mode == 'decoded' and (ext in TEXT_EXT) and size > 64 << 20:
        # Very large text: read the matching parts as it streams by.
        out, n = [], 0
        ql = q if case else q.lower()
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for i, ln in enumerate(f):
                if q and ql in (ln if case else ln.lower()):
                    out.append('%9d: %s' % (i + 1, ln.rstrip('\n')))
                    n += 1
                    if n >= 5000:
                        break
        return 'text', 'File of %d MB: only the matching lines are shown, with their line numbers.' % (size >> 20), \
            annotate_hashes('\n'.join(out), hashes)
    with open(path, 'rb') as f:
        b = f.read()
    note, fmt = '', ext.lstrip('.') or 'binary'
    if mode == 'hex':
        return fmt, 'Hex view', hex_view(b, q, case)
    if mode == 'strings':
        return fmt, 'Every run of four or more printable characters, with its offset in the file.', strings_view(b)
    text = None
    try:
        if ext == '.json':
            text, fmt = annotate_hashes(b.decode('utf-8', 'replace'), hashes), 'json'
            if rel.endswith('.json') or path.endswith('.json'):
                src = path[:-5]
                if ext_of(src) in BYML_EXT:
                    fmt, note = 'byml', 'BYML file shown through its decoded JSON (%s).' % os.path.basename(path)
            if hashes:
                note = (note + ' ' if note else '') + 'Numbers the hash table knows are followed by /* name */.'
        elif ext in TEXT_EXT:
            text, fmt = b.decode('utf-8', 'replace'), 'text'
        elif b[:8] == b'MsgStdBn':
            text, fmt = decode_msbt(b), 'msbt'
        elif b[:8] == b'BFEVFL\0\0':
            text, fmt = decode_evfl(b), 'bfevfl'
        elif b[:2] in (b'YB', b'BY') and ext in BYML_EXT:
            text = annotate_hashes(json.dumps(byml_to_obj(b), indent=1, ensure_ascii=False), hashes)
            fmt, note = 'byml', 'BYML file decoded here (it has no .json next to it).'
    except Exception as ex:
        note = 'The %s decoder failed (%s: %s); showing the strings instead.' % (fmt, type(ex).__name__, ex)
        text = None
    if text is None:
        text = strings_view(b)
        note = note or ('No structural decoder for %s files: these are the strings it holds, in file order with '
                        'their offsets. The Hex view shows the bytes around each match.' % (fmt.upper() or 'these'))
        fmt = fmt + ' (strings)'
    elif messages and fmt in ('bfevfl', 'byml', 'json') and len(text) < VIEW_FULL:
        text2 = messages.annotate(text)
        if text2 is not text and len(text2) != len(text):
            text = text2
            note = (note + ' ' if note else '') + 'A >> line under a message id shows that message\'s text.'
    if len(text) > VIEW_FULL:
        note = (note + ' ' if note else '') + 'Decoded text of %d MB: only the parts around matches are shown.' \
            % (len(text) >> 20)
        text = excerpt(text, q, case)
    return fmt, note, text


# ---- web page

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Game File Search</title>
<style>
:root{--bg:#15171c;--panel:#1c1f26;--line:#2b303a;--fg:#d9dce3;--dim:#8b93a3;--acc:#5aa9ff;--mark:#f5c84c;
--markfg:#141414;--cur:#ff8a3d;--sel:#26364d}
@media (prefers-color-scheme: light){:root{--bg:#f6f7f9;--panel:#fff;--line:#dfe3ea;--fg:#1d2330;--dim:#667085;
--acc:#1a6fd8;--mark:#ffe08a;--markfg:#111;--cur:#ff9d5c;--sel:#e3eefc}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,Segoe UI,sans-serif;height:100vh;
display:flex;flex-direction:column}
header{display:flex;gap:8px;align-items:center;padding:10px 12px;border-bottom:1px solid var(--line);
background:var(--panel);flex-wrap:wrap}
#q{flex:1;min-width:220px;font-size:16px;padding:7px 10px;border:1px solid var(--line);border-radius:6px;
background:var(--bg);color:var(--fg)}
button{background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:5px;padding:5px 10px;
cursor:pointer;font:inherit}
button:hover{border-color:var(--acc)}
button.primary{background:var(--acc);color:#fff;border-color:var(--acc)}
#status{color:var(--dim);font-size:13px}
main{flex:1;display:flex;min-height:0}
#left{width:34%;min-width:260px;max-width:620px;border-right:1px solid var(--line);display:flex;
flex-direction:column;min-height:0}
#filter{margin:8px;padding:5px 8px;border:1px solid var(--line);border-radius:5px;background:var(--bg);
color:var(--fg)}
#summary{padding:0 10px 6px;color:var(--dim);font-size:13px}
#results{overflow:auto;flex:1;padding-bottom:20px}
.grp{padding:6px 10px;font-weight:600;color:var(--dim);font-size:12px;text-transform:uppercase;
letter-spacing:.04em;cursor:pointer;position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line)}
.item{padding:5px 10px 6px 18px;cursor:pointer;border-bottom:1px solid transparent}
.item:hover{background:var(--panel)}
.item.on{background:var(--sel)}
.item .nm{font-family:Consolas,monospace;font-size:13px;word-break:break-all}
.item .dir{color:var(--dim);font-size:12px;word-break:break-all}
.item .ss{color:var(--dim);font-size:12px;font-family:Consolas,monospace;word-break:break-all}
#right{flex:1;display:flex;flex-direction:column;min-width:0}
#vhead{padding:8px 12px;border-bottom:1px solid var(--line);background:var(--panel)}
#vpath{font-family:Consolas,monospace;word-break:break-all}
#vtools{display:flex;gap:6px;align-items:center;margin-top:6px;flex-wrap:wrap}
.tabs button.on{background:var(--acc);color:#fff;border-color:var(--acc)}
#hitpos{min-width:70px;text-align:center;color:var(--dim)}
#vnote{padding:6px 12px;color:var(--dim);font-size:13px}
#view{flex:1;overflow:auto;margin:0;padding:8px 12px 40px;font:13px/1.45 Consolas,Menlo,monospace;
white-space:pre;tab-size:4}
#view.wrap{white-space:pre-wrap;word-break:break-all}
mark{background:var(--mark);color:var(--markfg);border-radius:2px}
mark.cur{background:var(--cur)}
.ss mark{padding:0 1px}
.bar{height:4px;background:var(--line);border-radius:2px;overflow:hidden;width:160px;display:inline-block;
vertical-align:middle}
.bar i{display:block;height:100%;background:var(--acc)}
.empty{padding:30px;color:var(--dim)}
</style></head><body>
<header>
  <input id="q" placeholder="Search every game file, for example Dm_TimeTemple_0130" autofocus>
  <label><input type="checkbox" id="cs"> Match case</label>
  <button id="go" class="primary">Search</button>
  <button id="upd" title="Re-read files that changed since the index was built">Update index</button>
  <button id="stop" title="Stop the search program (start it again with the launcher)">Stop</button>
  <span id="status"></span>
</header>
<main>
  <section id="left">
    <input id="filter" placeholder="Filter the results by path">
    <div id="summary"></div>
    <div id="results"><div class="empty">Type a name or any text and press Enter. Double-click a word in the viewer
      to search for it.</div></div>
  </section>
  <section id="right">
    <div id="vhead">
      <div id="vpath">No file open</div>
      <div id="vtools">
        <span class="tabs"><button data-m="decoded" class="on">Decoded</button><button data-m="strings">Strings</button><button data-m="hex">Hex</button></span>
        <button id="prev" title="Previous match (Shift+F3)">&#9650;</button><span id="hitpos"></span>
        <button id="next" title="Next match (F3)">&#9660;</button>
        <label><input type="checkbox" id="wrap"> Wrap</label>
        <button id="copy">Copy path</button><button id="reveal">Show in Explorer</button>
      </div>
    </div>
    <div id="vnote"></div>
    <pre id="view"></pre>
  </section>
</main>
<script>
const $ = s => document.querySelector(s);
let lastQ = '', lastHash = '', lastCase = false, results = [], cur = null, mode = 'decoded', marks = [], mi = -1;
const esc = s => s.replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const escRe = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
function hl(text, q, cs) {
  if (!q) return [esc(text), 0];
  const re = new RegExp([q, lastHash].filter(Boolean).map(escRe).join('|'), cs ? 'g' : 'gi');
  let out = [], last = 0, n = 0, m;
  while ((m = re.exec(text))) {
    out.push(esc(text.slice(last, m.index)), '<mark>', esc(m[0]), '</mark>');
    last = m.index + m[0].length; n++;
  }
  out.push(esc(text.slice(last)));
  return [out.join(''), n];
}
function size(n) { return n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(1) + ' KB' : (n / 1048576).toFixed(1) + ' MB'; }

async function status() {
  try {
    const s = await (await fetch('/api/status')).json();
    let t = '';
    if (s.state === 'indexing') {
      const p = s.total ? s.done / s.total : 0;
      t = `Indexing ${s.done.toLocaleString()} / ${s.total.toLocaleString()} files <span class="bar"><i style="width:${(p*100).toFixed(1)}%"></i></span> ${s.message} (${Math.round(s.elapsed/60)} min). Searches find only what is indexed so far.`;
    } else if (s.state === 'scanning' || s.state === 'finishing') t = esc(s.message) + '…';
    else if (s.state === 'error') t = 'Index error: ' + esc(s.message);
    else t = 'Index: ' + esc(s.message || 'ready');
    $('#status').innerHTML = t;
    setTimeout(status, s.state === 'ready' || s.state === 'error' ? 15000 : 1000);
  } catch (e) { setTimeout(status, 3000); }
}

async function doSearch(q) {
  q = (q ?? $('#q').value).trim();
  if (q.length < 2) { $('#summary').textContent = 'Type at least 2 characters.'; return; }
  $('#q').value = q; lastQ = q; lastHash = ''; lastCase = $('#cs').checked;
  history.replaceState(null, '', '#q=' + encodeURIComponent(q));
  $('#summary').textContent = 'Searching…'; $('#results').innerHTML = '';
  const t0 = performance.now();
  const r = await (await fetch('/api/search?q=' + encodeURIComponent(q) + '&case=' + (lastCase ? 1 : 0))).json();
  if (r.error) { $('#summary').textContent = r.error; return; }
  results = r.files; lastHash = r.hash || '';
  let s = `${results.length.toLocaleString()} files, ${r.nstrings.toLocaleString()} distinct matching strings, ${((performance.now()-t0)/1000).toFixed(1)} s`;
  if (r.hash) s += `; includes files holding its hash ${r.hash}`;
  if (r.more_files || r.more_strings) s += ' (too many to list all: make the search more specific)';
  $('#summary').textContent = s;
  renderResults();
  if (results.length) openFile(results[0].path);
}

function renderResults() {
  const f = $('#filter').value.trim().toLowerCase();
  const groups = new Map();
  for (const r of results) {
    if (f && !r.shown.toLowerCase().includes(f)) continue;
    const g = r.shown.includes('/') ? r.shown.slice(0, r.shown.indexOf('/')) : '(top)';
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(r);
  }
  const out = [];
  for (const [g, list] of groups) {
    out.push(`<div class="grp" data-g="${esc(g)}">${esc(g)} · ${list.length}</div><div class="gl">`);
    for (const r of list) {
      const i = r.shown.lastIndexOf('/');
      const ss = r.strings.slice(0, 3).map(s => hl(s.length > 160 ? s.slice(0, 160) + '…' : s, lastQ, lastCase)[0]).join('<br>');
      out.push(`<div class="item${cur === r.path ? ' on' : ''}" data-p="${esc(r.path)}"><div class="nm">${hl(r.shown.slice(i + 1), lastQ, lastCase)[0]}</div>
        <div class="dir">${esc(r.shown.slice(0, i + 1))} · ${size(r.size)}${r.n > 3 ? ' · ' + r.n + ' matching strings' : ''}</div><div class="ss">${ss}</div></div>`);
    }
    out.push('</div>');
  }
  $('#results').innerHTML = out.join('') || '<div class="empty">No file matches.</div>';
}

async function openFile(p, keepMode) {
  cur = p;
  if (!keepMode) mode = 'decoded';
  document.querySelectorAll('.tabs button').forEach(b => b.classList.toggle('on', b.dataset.m === mode));
  document.querySelectorAll('.item').forEach(e => e.classList.toggle('on', e.dataset.p === p));
  const r0 = results.find(r => r.path === p);
  $('#vpath').textContent = r0 ? r0.shown : p;
  $('#vnote').textContent = 'Loading…'; $('#view').innerHTML = '';
  const r = await (await fetch(`/api/view?path=${encodeURIComponent(p)}&mode=${mode}&q=${encodeURIComponent(lastQ)}&case=${lastCase ? 1 : 0}`)).json();
  if (cur !== p) return;
  if (r.error) { $('#vnote').textContent = r.error; return; }
  const [h, n] = hl(r.text, lastQ, lastCase);
  $('#view').innerHTML = h;
  $('#vnote').textContent = `${r.format.toUpperCase()} · ${n} highlighted match${n === 1 ? '' : 'es'}${r.note ? ' · ' + r.note : ''}`;
  marks = [...document.querySelectorAll('#view mark')]; mi = -1;
  if (marks.length) step(1); else { $('#hitpos').textContent = '0 / 0'; $('#view').scrollTop = 0; }
}
function step(d) {
  if (!marks.length) return;
  if (mi >= 0) marks[mi].classList.remove('cur');
  mi = (mi + d + marks.length) % marks.length;
  marks[mi].classList.add('cur');
  marks[mi].scrollIntoView({block: 'center', inline: 'nearest'});
  $('#hitpos').textContent = `${mi + 1} / ${marks.length}`;
}

$('#go').onclick = () => doSearch();
$('#q').addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });
$('#filter').addEventListener('input', renderResults);
$('#results').addEventListener('click', e => {
  const it = e.target.closest('.item');
  if (it) return openFile(it.dataset.p);
  const g = e.target.closest('.grp');
  if (g) { const l = g.nextElementSibling; l.style.display = l.style.display === 'none' ? '' : 'none'; }
});
document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => { mode = b.dataset.m; if (cur) openFile(cur, true); });
$('#next').onclick = () => step(1);
$('#prev').onclick = () => step(-1);
$('#wrap').onchange = () => $('#view').classList.toggle('wrap', $('#wrap').checked);
document.addEventListener('keydown', e => { if (e.key === 'F3') { e.preventDefault(); step(e.shiftKey ? -1 : 1); } });
$('#view').addEventListener('dblclick', () => {
  const s = String(window.getSelection()).trim();
  if (s.length >= 3 && s.length < 200) doSearch(s);
});
$('#copy').onclick = async () => { if (cur) { const r = await (await fetch('/api/abs?path=' + encodeURIComponent(cur))).json(); navigator.clipboard.writeText(r.path); } };
$('#reveal').onclick = () => { if (cur) fetch('/api/reveal?path=' + encodeURIComponent(cur)); };
$('#upd').onclick = async () => { await fetch('/api/update', {method: 'POST'}); status(); };
$('#stop').onclick = async () => {
  if (!confirm('Stop the search program?')) return;
  try { await fetch('/api/stop', {method: 'POST'}); } catch (e) {}
  document.body.innerHTML = '<div class="empty">The search has stopped. Start it again with the Game File Search shortcut or tools\\romfs_search.cmd.</div>';
};
status();
const fromHash = () => decodeURIComponent((location.hash.match(/q=([^&]*)/) || [])[1] || '');
window.addEventListener('hashchange', () => { const h = fromHash(); if (h && h !== lastQ) doSearch(h); });
if (fromHash()) doSearch(fromHash());
</script>
</body></html>
"""


class App:
    def __init__(self, root, db_path, lang='USen'):
        self.root = root
        self.db_path = db_path
        self.status = Status()
        self.messages = Messages(root, lang)
        self.hashes = None
        self.build_thread = None
        self.server = None
        self.local = threading.local()

    def con(self):
        c = getattr(self.local, 'con', None)
        if c is None:
            c = self.local.con = sqlite3.connect('file:%s?mode=ro' % self.db_path.as_posix(), uri=True,
                                                 check_same_thread=False)
        return c

    def get_hashes(self):
        if self.hashes is None:
            self.hashes = load_hashes(self.root)
        return self.hashes

    def start_build(self, full=False):
        if self.build_thread and self.build_thread.is_alive():
            return

        def run():
            try:
                build_index(self.root, self.db_path, self.status, full)
            except Exception as ex:
                self.status.set(state='error', message='%s: %s' % (type(ex).__name__, ex))
        self.build_thread = threading.Thread(target=run, daemon=True)
        self.build_thread.start()


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, body, ctype='application/json; charset=utf-8', code=200):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if urlparse(self.path).path == '/api/update':
                app.start_build(False)
                return self.send({'ok': True})
            if urlparse(self.path).path == '/api/stop':
                # Answer first; serve_forever then returns in main(), which ends the program. An index build
                # cut short this way resumes at the next start, which re-reads only the files it lacks.
                self.send({'ok': True})
                threading.Thread(target=app.server.shutdown, daemon=True).start()
                return
            self.send({'error': 'not found'}, code=404)

        def do_GET(self):
            u = urlparse(self.path)
            a = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if u.path == '/':
                    return self.send(PAGE.encode('utf-8'), 'text/html; charset=utf-8')
                if u.path == '/api/status':
                    return self.send(app.status.snapshot())
                if u.path == '/api/search':
                    q = a.get('q', '').strip()
                    if len(q) < 2:
                        return self.send({'error': 'Type at least 2 characters.'})
                    if not app.db_path.exists() or not app.con().execute(
                            "SELECT 1 FROM sqlite_master WHERE name='refs_sid'").fetchone():
                        return self.send({'error': 'The index is still being built; searching works once it is done.'})
                    return self.send(search(app.con(), q, a.get('case') == '1'))
                if u.path == '/api/view':
                    fmt, note, text = view(app.root, a['path'], a.get('mode', 'decoded'), a.get('q', ''),
                                           a.get('case') == '1', app.get_hashes(), app.messages)
                    return self.send({'format': fmt, 'note': note, 'text': text})
                if u.path in ('/api/reveal', '/api/abs'):
                    p = os.path.normpath(os.path.join(app.root, a['path']))
                    if os.path.commonpath([os.path.abspath(app.root), p]) != os.path.abspath(app.root):
                        raise ValueError('outside the folder')
                    if u.path == '/api/reveal':
                        subprocess.Popen(['explorer', '/select,', p])
                    return self.send({'path': p})
            except Exception as ex:
                return self.send({'error': '%s: %s' % (type(ex).__name__, ex)})
            self.send({'error': 'not found'}, code=404)
    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--root', help='the unpacked romfs folder (remembered in the index)')
    ap.add_argument('--index', default=str(DEFAULT_INDEX), help='the index file (default %(default)s)')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--lang', default='USen', help='the language whose text is shown beside message ids '
                    '(a Mals folder prefix: USen, EUde, JPja, ...)')
    ap.add_argument('--update', action='store_true', help='re-read the files that changed since the last build')
    ap.add_argument('--reindex', action='store_true', help='read every file again')
    ap.add_argument('--build-only', action='store_true', help='build or update the index, then exit')
    ap.add_argument('--no-browser', action='store_true')
    ap.add_argument('--stop', action='store_true', help='stop the search running on --port, then exit')
    args = ap.parse_args()

    if args.stop:
        url = 'http://127.0.0.1:%d/api/stop' % args.port
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=b'', method='POST'), timeout=5) as r:
                ok = json.load(r).get('ok')
        except Exception:
            ok = False
        print('The search has stopped.' if ok else 'The search was not running (nothing answered on port %d).'
              % args.port)
        return

    db_path = Path(args.index).resolve()
    root = args.root
    complete = False
    if db_path.exists():
        c = sqlite3.connect(str(db_path))
        try:
            root = root or meta_get(c, 'root')
            complete = meta_get(c, 'complete') == '1' and meta_get(c, 'root') == os.path.abspath(root or '')
        except sqlite3.Error:
            pass
        c.close()
    if not root:
        sys.exit('Pass --root <the unpacked romfs folder> the first time.')
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        sys.exit('%s is not a folder' % root)

    app = App(root, db_path, args.lang)
    if args.build_only:
        app.start_build(args.reindex)
        while app.build_thread.is_alive():
            app.build_thread.join(30)
            s = app.status.snapshot()
            print('%s %d/%d %s (%d s)' % (s['state'], s['done'], s['total'], s['message'], s['elapsed']), flush=True)
        return
    if args.reindex or args.update or not complete:
        app.start_build(args.reindex)
    else:
        c = sqlite3.connect(str(db_path))
        n = c.execute('SELECT count(*) FROM files').fetchone()[0]
        app.status.set(state='ready', message='%d files, built %s' % (n, meta_get(c, 'built', '?')))
        c.close()

    class Server(ThreadingHTTPServer):
        allow_reuse_address = sys.platform != 'win32'   # on Windows it lets a second copy share the port unnoticed
        daemon_threads = True
    try:
        srv = app.server = Server(('127.0.0.1', args.port), make_handler(app))
    except OSError as ex:
        # Already running (a second start, say from the launcher): open its page instead.
        url = 'http://127.0.0.1:%d/' % args.port
        try:
            with urllib.request.urlopen(url + 'api/status', timeout=3) as r:
                running = 'state' in json.load(r)
        except Exception:
            running = False
        if running:
            print('The search is already running: opening %s' % url)
            if not args.no_browser:
                webbrowser.open(url)
            return
        sys.exit('Port %d is in use by another program (%s). Pass --port to use another one.'
                 % (args.port, ex.strerror))
    url = 'http://127.0.0.1:%d/' % args.port
    print('Game file search: %s  (folder %s, index %s). Ctrl+C stops it.' % (url, root, db_path))
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    srv.server_close()
    print('Stopped.')


if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
