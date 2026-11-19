"""Make the Desert free roam mod: no void-outs and no sending back in the Haunted Wasteland and the Desert Colossus, so
the player can roam both freely.

The desert sends the player back four ways, and the mod stops each one:

1. Areas whose AI module gives the abyss hit. A stage file (Banc/<area>/<area>_{Static,Dynamic}.bcett.byml.zs) places
   the actors and lists AiGroups: {Hash, Logic: "Logic/Module/<dir>/<name>.ain", References: [{Id, Path, Reference: a
   placement's Hash}]}; the module is the RomFS file Logic/<name>.ainb, an AI node graph (layout below; the debug
   menu reads the same, runtime/debug_place.c abyss_ids). An ActorLogicAreaActor node stands for the Area whose Id is
   its InstanceName, and its sensor hits go to the nodes that take its output. HoldSensorHitInfoGiveAbyssActor (and its
   ...Condition form) sends the player back from the Area; HoldSensorHitInfoSendSandAbyss does it for the Haunted
   Wasteland's sand rivers.
2. The Haunted Wasteland's own send-back when the player leaves the flag path: Areas off the path, watched by
   HoldActorObserverBySensorHitInfo nodes (for the player, and for the guide Poe and the player in turn), start an
   EventRequestActor whose RequestEventName is HountedWasteland_Field_RestartToEntrance or ..._RestartToCheckpoint
   (Event/EventFlow/HountedWasteland_Field_Restart.bfevfl: fade out, warp to a WarpPoint, fade in), and the "omen"
   Areas round them feed HoldHountedWastelandOutAreaCheck, which thickens the sandstorm while the player is off the
   path.
3. The player's fall check reads the area's volume statistics (Phive/VolumeStats/<folder>/<area>_X<i>_Z<j>.bpvsts.zs):
   a 1 m cell whose record has the 1-bit field at record bit 244 set and the 4-bit field at bit 245 at 5 or more is a
   void cell (runtime/debug_voidcells.c documents the file and the check).
4. The same check also voids every point 8 m or more from any record and every point outside the area's
   VolumeStatsAABB grown by 5 m across and below. Only code can stop that.

What the mod changes:
- A module used by the desert's stage files alone: each Area node whose hits reach a send-back (the abyss nodes above,
  HoldHountedWastelandOutAreaCheck, or a restart EventRequestActor) gets Logic_Create false, the game's own way of
  keeping a referenced actor from being created (its unconnected bool input: default 0, flags 0x00800000, as the
  game's files write an input set to false; 0x01800000 marks the class default). Same size, every other byte kept.
- A module other areas use too (the generic fall gimmick): its AiGroup, and the Areas only it references, come out
  of that area's stage file. The file's Actors and AiGroups arrays are shrunk where they are (the other bytes stay as
  they were; the dropped entries' own nodes are left unreferenced), then compressed again with the same zstd
  dictionary. Only when nothing else in the area names the group or its Areas.
- Volume statistics: bit 244 of every void record is cleared (the value at bit 245 then does not count), in the base
  tiles and in any <area>_presence_* variant. Every record stays, so the 8 m rule sees what it saw before.
- ootpc-feature.txt turns on the built-in feature desert-free-roam (runtime/mods.h MOD_FEATURE_DESERT_FREE_ROAM,
  runtime/desert_roam.c): in these four areas the game's void-out is held back as the debug menu's No void-out does,
  unless the player has fallen 40 m below the bottom of the area's VolumeStatsAABB.

AINB (version 0x408), as read here: u32 command count +0x0c, node count +0x10, string pool +0x24, immediate
parameters +0x2c, input and output parameters +0x34 (twelve u32 offsets: for int, bool, float, string, vec3 and
user-defined, the inputs then the outputs), the multi-source table +0x38. Nodes after the 0x74-byte header and 0x18
bytes per command, 0x3c each: u16 type, u16 index, name +0x08, body +0x14. A body starts with six {u32 first, u32
count} runs of immediates (a string immediate: 12 bytes {u32 name, u32 flags, u32 value}), then the twelve runs of
inputs and outputs. An input: 16 bytes {u32 name, i16 source node, i16 source output, u32 flags, u32 default}, 24 for
vec3, 20 for user-defined {u32 name, u32 class, i16 node, i16 output, u32 flags, u32 default}; a source node of -1 is
none, -100 or less names a run of the multi-source table (8 bytes each: {u16 node, u16 output, u32}) that starts at
entry -100 - node and is as long as the output field says.

    python tools/make_desert_roam_mod.py --game <game folder> [--out <mod folder>]

Run with Python 3.14 (its compression.zstd). Standard library only besides the repository's tools.
"""
import argparse
import shutil
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import n64voice   # noqa: E402  RomFS: the unpacked RomFS with the zstd dictionaries
import randomizer   # noqa: E402  byml_read

MOD_NAME = 'Desert free roam'
FEATURE = 'desert-free-roam'   # runtime/mods.h MOD_FEATURE_DESERT_FREE_ROAM
AREAS = ('HountedWasteland_Adult', 'HountedWasteland_Child', 'DesertColossus_Adult', 'DesertColossus_Child')
RESTART_EVENTS = ('HountedWasteland_Field_RestartToEntrance', 'HountedWasteland_Field_RestartToCheckpoint')
ABYSS_NODES = ('HoldSensorHitInfoGiveAbyssActor', 'HoldSensorHitInfoSendSandAbyss')   # ...Condition included
STRAY_NODES = ('HoldHountedWastelandOutAreaCheck',)
CREATE_OFF = 0x00800000      # the flags of an unconnected input whose default is not the class default
CREATE_DEFAULT = 0x01800000  # ... and of one that keeps the class default
VOID_FLAG_BIT, VOID_VALUE_BIT, VOID_MIN = 244, 245, 5   # runtime/debug_voidcells.c FLAG_BIT, VALUE_BIT, VALUE_MIN
VOID_TYPE = 6
README = """Desert free roam: the Haunted Wasteland and the Desert Colossus no longer send you back, so you can roam them
freely, as child and adult.

- No void-outs there: the Wasteland's sand rivers and the Colossus' fall area no longer send you back, the empty air
  the game counts as a fall no longer does, and with this mod on the game holds back its fall check in these two
  areas (as the debug menu's No void-out does, only there). Falling far below the ground still sends you back, so a
  fall out of the world is not the end.
- No guide needed in the Wasteland: leaving the flag path no longer sends you back to the entrance or the last
  checkpoint, and the sandstorm no longer thickens off the path.

Everything else stays as it was: the loading zones, the guide Poe, the Leevers, the chests, the walls at the edge.

The Randomizer mod changes the Desert Colossus stage files too; with both on, the one higher in the Mods tab's order
wins those files (this mod's fall area change, or the Randomizer's items there).

Made by tools/make_desert_roam_mod.py from your own game files. Turn it off on the Mods tab to have the desert as it was.
"""


def u16(b, o): return struct.unpack_from('<H', b, o)[0]
def i16(b, o): return struct.unpack_from('<h', b, o)[0]
def u32(b, o): return struct.unpack_from('<I', b, o)[0]


# ---- AI modules

class Ainb:
    """The nodes of an AINB module: name, index, InstanceName, and its inputs as {type, name, at (the entry's offset),
    sources [(node index, output)]}."""
    IN_SIZE = (16, 16, 16, 16, 24, 20)

    def __init__(self, data, what):
        p = self.p = bytes(data)
        if p[:4] != b'AIB ' or u32(p, 4) != 0x408:
            raise ValueError(f'{what}: not an AINB module of version 0x408')
        ncmd, nnode = u32(p, 0x0c), u32(p, 0x10)
        self.pool = u32(p, 0x24)
        imm, io, multi = u32(p, 0x2c), u32(p, 0x34), u32(p, 0x38)
        strs = u32(p, imm + 12)
        tables = [u32(p, io + 4 * i) for i in range(12)]
        self.nodes = []
        for i in range(nnode):
            n = 0x74 + ncmd * 0x18 + i * 0x3c
            body = u32(p, n + 0x14)
            node = {'index': u16(p, n + 2), 'name': self.str(u32(p, n + 8)), 'instance': None, 'inputs': []}
            first, count = u32(p, body + 24), u32(p, body + 28)
            for k in range(count):
                e = strs + 12 * (first + k)
                if self.str(u32(p, e)) == 'InstanceName':
                    node['instance'] = self.str(u32(p, e + 8))
            for t in range(6):
                first, count = u32(p, body + 0x30 + 16 * t), u32(p, body + 0x30 + 16 * t + 4)
                for k in range(count):
                    e = tables[2 * t] + self.IN_SIZE[t] * (first + k)
                    at = e + (8 if t == 5 else 4)
                    src, out = i16(p, at), i16(p, at + 2)
                    if src <= -100:
                        sources = [(u16(p, multi + 8 * (-100 - src + j)), u16(p, multi + 8 * (-100 - src + j) + 2))
                                   for j in range(out)]
                    else:
                        sources = [(src, out)] if src >= 0 else []
                    node['inputs'].append({'type': t, 'name': self.str(u32(p, e)), 'at': e, 'link': at,
                                           'sources': sources})
            self.nodes.append(node)
        self.by_index = {n['index']: n for n in self.nodes}

    def str(self, off):
        s = self.pool + off
        return self.p[s:self.p.index(b'\0', s)].decode('utf-8')

    def consumers(self):
        """{node index: [the nodes that take one of its outputs]}."""
        out = {}
        for n in self.nodes:
            for inp in n['inputs']:
                for src, _ in inp['sources']:
                    out.setdefault(src, []).append(n)
        return out

    def reaches(self, start, sink):
        """The nodes reached from node start through the nodes that take its outputs, in order found, when one of them
        is a sink (sink(node) true); else []."""
        cons, seen, todo, order, hit = self.consumers(), {start['index']}, [start], [], False
        while todo:
            for c in cons.get(todo.pop()['index'], []):
                if c['index'] not in seen:
                    seen.add(c['index'])
                    order.append(c)
                    todo.append(c)
                    hit |= sink(c)
        return order if hit else []


def create_off(data, nodes, what):
    """data with each of nodes' Logic_Create input set to false (an unconnected bool input, default 1 now)."""
    out = bytearray(data)
    for n in nodes:
        inp = [i for i in n['inputs'] if i['type'] == 1 and i['name'] == 'Logic_Create']
        if len(inp) != 1:
            raise ValueError(f'{what}: {n["instance"]} has no Logic_Create input')
        e = inp[0]['at']
        if i16(out, e + 4) != -1 or u32(out, e + 8) != CREATE_DEFAULT or u32(out, e + 12) != 1:
            raise ValueError(f'{what}: {n["instance"]}\'s Logic_Create is not an unconnected true')
        struct.pack_into('<II', out, e + 8, CREATE_OFF, 0)
    return bytes(out)


# ---- stage files

def plain(node):
    """A BYML tree from randomizer.byml_read with the values' types dropped."""
    if isinstance(node, dict):
        return {k: plain(v) for k, v in node.items()}
    if isinstance(node, list):
        return [plain(v) for v in node]
    if node is not None and node[0] == randomizer.T_FLOAT:
        return struct.unpack('<f', struct.pack('<I', node[1]))[0]
    return node[1] if node is not None else None


def u64_values(node, out):
    """Every u64 value of a BYML tree into out (a list), wherever it is."""
    if isinstance(node, dict):
        for v in node.values():
            u64_values(v, out)
    elif isinstance(node, list):
        for v in node:
            u64_values(v, out)
    elif node is not None and node[0] == randomizer.T_UINT64:
        out.append(node[1])
    return out


def root_array(data, key):
    """The offset of the array the root dictionary of a BYML file holds under key."""
    keys, root = u32(data, 4), u32(data, 12)
    count = u32(data, keys) >> 8
    names = [data[keys + o:data.index(b'\0', keys + o)].decode() for o in struct.unpack_from(f'<{count}I', data, keys + 4)]
    if data[root] != randomizer.T_DICT:
        raise ValueError('the root is not a dictionary')
    for i in range(u32(data, root) >> 8):
        e = root + 4 + 8 * i
        if names[u32(data, e) & 0xffffff] == key:
            if data[e + 3] != randomizer.T_ARR:
                raise ValueError(f'{key} is not an array')
            return u32(data, e + 4)
    raise ValueError(f'the root has no {key}')


def shrink_array(buf, at, drop):
    """The BYML array at offset at in buf (a bytearray) without the elements whose indices are in drop, written where
    it was; the bytes it no longer uses become zeros. Returns (start, end) of the bytes it held."""
    if buf[at] != randomizer.T_ARR:
        raise ValueError('not an array')
    n = u32(buf, at) >> 8
    types = bytes(buf[at + 4:at + 4 + n])
    vals_at = at + 4 + (n + 3 & ~3)
    vals = struct.unpack_from(f'<{n}I', buf, vals_at)
    keep = [i for i in range(n) if i not in drop]
    m = len(keep)
    new = bytearray(struct.pack('<I', randomizer.T_ARR | m << 8))
    new += bytes(types[i] for i in keep)
    new += bytes(-len(new) % 4)
    new += struct.pack(f'<{m}I', *(vals[i] for i in keep))
    end = vals_at + 4 * n
    buf[at:end] = new + bytes(end - at - len(new))
    return at, end


class Stage:
    """An area's two stage files: path -> (bytes, zstd dictionary, tree)."""

    def __init__(self, fs, area):
        folder = area.rsplit('_', 1)[0]
        self.files = {}
        for part in ('Static', 'Dynamic'):
            path = f'Banc/{folder}/{area}_{part}.bcett.byml.zs'
            data, d = fs.zs(path)
            ver, root = randomizer.byml_read(data)
            self.files[path] = (data, d, ver, root)

    def actors(self):
        return {a['Hash'][1]: (path, a) for path, (_, _, _, root) in self.files.items() for a in root.get('Actors', [])}

    def groups(self):
        return [(path, g) for path, (_, _, _, root) in self.files.items() for g in root.get('AiGroups', [])]

    def mentions(self, value):
        return sum(u64_values(root, []).count(value) for _, _, _, root in self.files.values())


def module_path(group):
    return 'Logic/' + group['Logic'][1].rsplit('/', 1)[1] + 'b'   # .../<name>.logic.module.ain -> Logic/<name>...ainb


def restart_requesters(stage, group):
    """The Ids of the group's references that are EventRequestActors for a restart event."""
    actors = stage.actors()
    out = set()
    for r in group.get('References', []):
        _, a = actors.get(r['Reference'][1], (None, None))
        if a and a['Gyaml'][1] == 'EventRequestActor' and \
                a.get('Dynamic', {}).get('RequestEventName', (0, ''))[1] in RESTART_EVENTS:
            out.add(r['Id'][1])
    return out


def sink_test(restarts):
    def sink(n):
        return (any(s in n['name'] for s in ABYSS_NODES + STRAY_NODES)
                or (n['name'] == 'ActorLogicEventRequestActor' and n['instance'] in restarts))
    return sink


def module_users(fs):
    """{module path: the areas whose stage files use it}."""
    out = {}
    for p in sorted(fs.files):
        if p.startswith('Banc/') and p.endswith('.bcett.byml.zs') and p.count('/') == 2:
            area = p.rsplit('/', 1)[1].rsplit('_', 1)[0]
            _, root = randomizer.byml_read(fs.zs(p)[0])
            for g in root.get('AiGroups', []):
                out.setdefault(module_path(g), set()).add(area)
    return out


# ---- volume statistics

def void_cells(data, clear=False):
    """The void records of a volume statistics tile (records whose bit 244 is set and whose 4 bits at 245 are 5 or
    more): (count, records in all, [their cells' world positions], the tile with their bit 244 cleared when clear)."""
    if data[:8] != b'Phive\0\1\0':
        raise ValueError('not a volume statistics tile')
    out = bytearray(data) if clear else None
    org = struct.unpack_from('<3i', data, 0x14)
    dims = struct.unpack_from('<3I', data, 0x20)
    width, kind = data[0x36], data[0x3e]
    at, found, total, cells = 0x40, 0, 0, []
    sq0 = [o // 250 for o in org]
    for x in range(dims[0]):
        for y in range(dims[1]):
            for z in range(dims[2]):
                at += 4
                levels = []
                for _ in range(8):
                    n = u32(data, at)
                    levels.append((at + 4, n))
                    at += 4 + 4 * n
                streams = []
                for _ in range(8):
                    n = u32(data, at)
                    streams.append((at + 4, n))
                    at += 4 + n
                if not levels[0][1]:
                    continue
                if kind != VOID_TYPE or width < VOID_VALUE_BIT + 4:
                    raise ValueError(f'records of type {kind}, {width} bits: not the fall check\'s')
                rec, size = streams[7]
                corner = [(sq0[0] + x) * 250 - 3, (sq0[1] + y) * 250 - 3, (sq0[2] + z) * 250 - 3]
                # walk the node levels: each node {first child << 8 | mask of children}, child bit x | y << 1 | z << 2
                nodes = [(0, 0, 0, 0)]   # (index at this depth, x, y, z of its low corner in metres from corner)
                for d in range(8):
                    base, n = levels[d]
                    half = 1 << (7 - d)
                    nxt = []
                    for idx, cx, cy, cz in nodes:
                        node = u32(data, base + 4 * idx)
                        mask, first = node & 0xff, node >> 8
                        k = 0
                        for o in range(8):
                            if mask >> o & 1:
                                nxt.append((first + k, cx + (o & 1) * half, cy + (o >> 1 & 1) * half,
                                            cz + (o >> 2 & 1) * half))
                                k += 1
                    nodes = nxt
                if len(nodes) * width > size * 8:
                    raise ValueError('the record stream is shorter than its nodes')
                for idx, cx, cy, cz in nodes:
                    bit = idx * width + VOID_FLAG_BIT
                    chunk = int.from_bytes(data[rec + (bit >> 3):rec + (bit >> 3) + 3].ljust(3, b'\0'), 'little')
                    total += 1
                    if chunk >> (bit & 7) & 1 and chunk >> ((bit & 7) + 1) & 15 >= VOID_MIN:
                        found += 1
                        cells.append((corner[0] + cx, corner[1] + cy, corner[2] + cz))
                        if clear:
                            out[rec + (bit >> 3)] &= ~(1 << (bit & 7)) & 0xff
    return found, total, cells, (bytes(out) if clear else None)


# ---- the mod

def make(game, out=None, log=print):
    """Write the mod into out (default <game>/mods/Desert free roam). Returns {RomFS path: bytes} it wrote."""
    fs = n64voice.RomFS(Path(game) / 'romfs')
    for area in AREAS:
        if f'Game/VolumeStatsAABB/{area}.game__VolumeStatsAABB.bgyml' not in fs.files:
            raise SystemExit(f'the game has no area {area}')
    mod = Path(out) if out else Path(game) / 'mods' / MOD_NAME
    users = module_users(fs)
    files, plan = {}, {}

    # 1 and 2: the Areas that send the player back
    module_off = {}   # module path -> {Area node index: node}
    for area in AREAS:
        stage = Stage(fs, area)
        drop = {}     # stage path -> (AiGroups indices, Actors hashes)
        for path, g in stage.groups():
            mpath = module_path(g)
            if mpath not in fs.files:
                continue
            m = Ainb(fs.raw(mpath), mpath)
            sink = sink_test(restart_requesters(stage, g))
            hit = [n for n in m.nodes if n['name'] == 'ActorLogicAreaActor' and m.reaches(n, sink)]
            if not hit:
                continue
            if users[mpath] <= set(AREAS):
                module_off.setdefault(mpath, {}).update((n['index'], n) for n in hit)
                continue
            # a module other areas use: the group and its Areas leave this area's stage file
            refs = {r['Id'][1]: r['Reference'][1] for r in g.get('References', [])}
            actors = stage.actors()
            ids = [n['instance'] for n in hit]
            if sorted(ids) != sorted(refs) or any(actors.get(refs[i], (0, {'Gyaml': (0, '')}))[1]['Gyaml'][1] != 'Area'
                                                  for i in ids):
                raise SystemExit(f'{area}: {mpath} references more than the Areas that send back; left as it is')
            own = {i: u64_values(actors[refs[i]][1], []).count(refs[i]) for i in ids}   # its Hash, its Phive ID
            if stage.mentions(g['Hash'][1]) != 1 or any(stage.mentions(refs[i]) != own[i] + 1 for i in ids):
                raise SystemExit(f'{area}: something else names {mpath}\'s group or its Areas; left as it is')
            groups = drop.setdefault(path, (set(), set()))[0]
            groups.add(stage.files[path][3]['AiGroups'].index(g))
            for i in ids:
                drop.setdefault(actors[refs[i]][0], (set(), set()))[1].add(refs[i])
                at = plain(actors[refs[i]][1]).get('Translate')
                log(f'{area}: {mpath.rsplit("/", 1)[1]}: {i} at {", ".join(f"{v:.1f}" for v in at)} leaves the stage')
        for path, (groups, hashes) in drop.items():
            data, d, ver, root = stage.files[path]
            buf = bytearray(data)
            spans = []
            if groups:
                spans.append(shrink_array(buf, root_array(data, 'AiGroups'), groups))
            if hashes:
                idx = {i for i, a in enumerate(root['Actors']) if a['Hash'][1] in hashes}
                if len(idx) != len(hashes):
                    raise SystemExit(f'{path}: an Area to drop is not in its Actors')
                spans.append(shrink_array(buf, root_array(data, 'Actors'), idx))
            files[path] = fs.compress(bytes(buf), d)
            plan[path] = ('stage', groups, hashes, spans)
    for mpath, nodes in sorted(module_off.items()):
        nodes = [nodes[k] for k in sorted(nodes)]
        files[mpath] = create_off(fs.raw(mpath), nodes, mpath)
        plan[mpath] = ('module', {i['at'] for n in nodes for i in n['inputs']
                                  if i['type'] == 1 and i['name'] == 'Logic_Create'})
        log(f'{mpath}: {len(nodes)} Areas not created: ' + ', '.join(n['instance'] for n in nodes))

    # 3: the void cells of the volume statistics
    for area in AREAS:
        folder = area.rsplit('_', 1)[0]
        tiles = sorted(p for p in fs.files if p.startswith(f'Phive/VolumeStats/{folder}/{area}_')
                       and p.endswith('.bpvsts.zs'))
        if not tiles:
            raise SystemExit(f'{area} has no volume statistics')
        cleared = 0
        for p in tiles:
            data, d = fs.zs(p)
            found, total, cells, new = void_cells(data, clear=True)
            if not found:
                continue
            files[p] = fs.compress(new, d)
            plan[p] = ('tile', found, total)
            cleared += found
            lo = [min(c[a] for c in cells) for a in range(3)]
            hi = [max(c[a] for c in cells) + 1 for a in range(3)]
            log(f'{p}: {found} of {total} records were void cells, x {lo[0]}..{hi[0]}, y {lo[1]}..{hi[1]}, '
                f'z {lo[2]}..{hi[2]}')
        if not cleared:
            log(f'{area}: no void cells in its {len(tiles)} volume statistics tiles')

    if mod.exists():
        shutil.rmtree(mod / 'romfs', ignore_errors=True)
    for path, data in files.items():
        dest = mod / 'romfs' / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    (mod / 'ootpc-feature.txt').write_text(FEATURE + '\n', encoding='utf-8')
    (mod / 'README.txt').write_text(README, encoding='utf-8')
    verify(fs, mod, plan, log)
    log(f'{mod}: {len(files)} files')
    return files


def verify(fs, mod, plan, log):
    """Read the written files back: each holds the game's file with only the planned changes."""
    import compression.zstd as zstd
    for path, what in plan.items():
        written = (mod / 'romfs' / path).read_bytes()
        if path.endswith('.zs'):
            orig, d = fs.zs(path)
            info = zstd.get_frame_info(written)
            if info.dictionary_id != (d.dict_id if d else 0) or info.decompressed_size != len(orig):
                raise SystemExit(f'{path}: the zstd frame does not name the dictionary or the size as the game\'s')
            new = zstd.decompress(written, zstd_dict=d)
        else:
            orig, new = fs.raw(path), written
        if len(new) != len(orig):
            raise SystemExit(f'{path}: the size changed')
        diff = [i for i in range(len(orig)) if orig[i] != new[i]]
        if what[0] == 'stage':
            _, groups, hashes, spans = what
            if any(not any(a <= i < b for a, b in spans) for i in diff):
                raise SystemExit(f'{path}: bytes outside the shrunk arrays changed')
            ver, root = randomizer.byml_read(orig)
            want = dict(root)
            want['AiGroups'] = [g for i, g in enumerate(root.get('AiGroups', [])) if i not in groups]
            want['Actors'] = [a for a in root['Actors'] if a['Hash'][1] not in hashes]
            if randomizer.byml_read(new) != (ver, want):
                raise SystemExit(f'{path}: does not read back as the game\'s file without the dropped entries')
            left = u64_values(randomizer.byml_read(new)[1], [])
            if any(h in left for h in hashes):
                raise SystemExit(f'{path}: a dropped Area is still named')
            log(f'checked {path}: {len(groups)} AiGroups and {len(hashes)} Areas out, the rest as it was')
        elif what[0] == 'module':
            at = what[1]
            if any(not any(e + 8 <= i < e + 16 for e in at) for i in diff):
                raise SystemExit(f'{path}: bytes other than the Logic_Create inputs changed')
            m = Ainb(new, path)
            for n in m.nodes:
                for i in n['inputs']:
                    if i['at'] in at and (u32(new, i['at'] + 8), u32(new, i['at'] + 12)) != (CREATE_OFF, 0):
                        raise SystemExit(f'{path}: {n["instance"]} is still created')
            log(f'checked {path}: {len(at)} Logic_Create inputs false, the rest as it was')
        else:
            found, total, cells, _ = void_cells(new)
            _, total0, _, _ = void_cells(orig)
            if found or total != total0 or len(diff) > what[1] or any((orig[i] ^ new[i]) & (orig[i] ^ new[i]) - 1
                                                                       for i in diff):
                raise SystemExit(f'{path}: void cells left, records lost or other bits changed')
            log(f'checked {path}: no void cells, {total} records kept')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--game', required=True, help='the game folder (its romfs folder is read, its mods folder written)')
    ap.add_argument('--out', help=f'the mod folder to write (default: <game>/mods/{MOD_NAME})')
    a = ap.parse_args()
    make(a.game, a.out)


if __name__ == '__main__':
    main()
