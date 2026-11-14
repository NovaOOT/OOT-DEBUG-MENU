"""The save tree the game keeps in <game>/saves/tree (runtime/save_tree.h has the files): reading its nodes as the game
adds them, the save of a node, exporting that save, and asking the running game to load it.

A node is a save folder as the game writes one, <its folder>/slot_NN/caption.sav and progress.sav. Its folder ("dir" in
tree.jsonl) is named after the place and what happened: "Kakariko Village/000137 Child - Talked to Kokiri Mido".
"""
import json
import os
import re
import time
import zipfile
from pathlib import Path

SLOTS = 6


class TreeError(Exception):
    pass


def tree_dir(game):
    return Path(game) / 'saves' / 'tree'


class Node:
    __slots__ = ('id', 'parent', 'time', 'slot', 'kind', 'area', 'label', 'n', 'changes', 'children', 'text', 'dir')

    def __init__(self, d):
        self.id = int(d['id'])
        self.parent = int(d.get('parent', -1))
        self.time = d.get('time', '')
        self.slot = int(d.get('slot', 0))
        self.kind = d.get('kind', '')
        self.area = d.get('area', '') or '?'
        self.label = d.get('label', '') or f'Node {self.id}'
        self.n = int(d.get('n', 0))
        self.changes = d.get('changes') or []
        # a tree from before folders had names keeps its nodes in nodes/<id / 1000>/<id>
        self.dir = d.get('dir') or f'nodes/{self.id // 1000}/{self.id}'
        self.children = []
        # what the search looks through: the label, the place, the names and values of the flags that changed
        self.text = ' '.join([self.label, self.area, self.kind] +
                             [' '.join(str(x) for x in c) for c in self.changes]).lower()


class Tree:
    """The nodes of tree.jsonl, read again from where the last read stopped (refresh): the game only appends."""

    def __init__(self, game):
        self.game = Path(game)
        self.dir = tree_dir(game)
        self.nodes = []
        self.roots = []
        self._pos = 0
        self._rest = b''

    def refresh(self):
        """Reads the lines the game added since the last call. The number of new nodes."""
        p = self.dir / 'tree.jsonl'
        try:
            size = p.stat().st_size
        except OSError:
            return 0
        if size < self._pos:   # the file was replaced (the tree was deleted): start over
            self.__init__(self.game)
        if size == self._pos:
            return 0
        with open(p, 'rb') as f:
            f.seek(self._pos)
            data = self._rest + f.read(size - self._pos)
        self._pos = size
        lines = data.split(b'\n')
        self._rest = lines.pop()   # a line the game is still writing
        added = 0
        for line in lines:
            try:
                n = Node(json.loads(line))
            except (ValueError, KeyError, TypeError):
                continue
            if n.id != len(self.nodes):
                continue   # out of order: the game's own loader leaves such a line out too
            self.nodes.append(n)
            if 0 <= n.parent < n.id:
                self.nodes[n.parent].children.append(n)
            else:
                n.parent = -1
                self.roots.append(n)
            added += 1
        return added

    def current(self):
        """The node the running game is on, -1 when none."""
        try:
            return int((self.dir / 'current.txt').read_text().split()[0])
        except (OSError, ValueError, IndexError):
            return -1

    def status(self):
        try:
            return (self.dir / 'status.txt').read_text(encoding='utf-8', errors='replace').strip()
        except OSError:
            return ''

    def path_to(self, node_id):
        """The ids from a root to node_id."""
        out = []
        while 0 <= node_id < len(self.nodes) and len(out) <= len(self.nodes):
            out.append(node_id)
            node_id = self.nodes[node_id].parent
        return out[::-1]

    def search(self, query, limit=500):
        words = query.lower().split()
        if not words:
            return []
        out = []
        for n in self.nodes:
            if all(w in n.text for w in words):
                out.append(n)
                if len(out) >= limit:
                    break
        return out


def _node(game, node):
    """The Node itself, or the Node of a node id (read from tree.jsonl)."""
    if isinstance(node, Node):
        return node
    tree = Tree(game)
    tree.refresh()
    if not 0 <= node < len(tree.nodes):
        raise TreeError(f'there is no save {node} in the tree')
    return tree.nodes[node]


def node_dir(game, node):
    return tree_dir(game) / _node(game, node).dir


def node_slot_dir(game, node):
    """The node's slot_NN folder (the game's own two files), or TreeError."""
    node = _node(game, node)
    d = node_dir(game, node)
    for slot in range(SLOTS):
        sd = d / f'slot_{slot:02d}'
        if (sd / 'progress.sav').is_file():
            return sd
    raise TreeError(f'save {node.id} has no save files in {d}')


def node_save(game, node):
    """(slot, caption.sav bytes, progress.sav bytes) of a node."""
    node = _node(game, node)
    sd = node_slot_dir(game, node)
    try:
        return int(sd.name[5:]), (sd / 'caption.sav').read_bytes(), (sd / 'progress.sav').read_bytes()
    except OSError as e:
        raise TreeError(f'save {node.id} cannot be read ({e})') from None


def safe_name(text, cap=60):
    s = re.sub(r'[^A-Za-z0-9 ._()+-]+', ' ', text).strip()
    s = re.sub(r'\s+', ' ', s)
    return (s[:cap].rstrip() or 'save')


def export_node(game, node, dest, title=None):
    """Writes the node's save into dest as a folder slot_NN (the launcher's Import a Save Folder takes it: only that
    slot is replaced) inside a folder named after the node, and the same as one zip beside it. The zip's path."""
    slot, caption, prog = node_save(game, node)
    dest = Path(dest)
    stem = f'OOT save tree {node.id} - {safe_name(title or node.label)}'
    folder = dest / stem
    k = 2
    while folder.exists() or folder.with_suffix('.zip').exists():
        folder = dest / f'{stem} ({k})'
        k += 1
    sd = folder / f'slot_{slot:02d}'
    sd.mkdir(parents=True)
    (sd / 'caption.sav').write_bytes(caption)
    (sd / 'progress.sav').write_bytes(prog)
    info = {'node': node.id, 'title': title or node.label, 'label': node.label, 'area': node.area, 'time': node.time, 'slot': slot,
            'changes': node.changes, 'exported': time.strftime('%Y-%m-%dT%H:%M:%S')}
    (folder / 'about.json').write_text(json.dumps(info, indent=1) + '\n', encoding='utf-8')
    z = folder.with_name(folder.name + '.zip')
    with zipfile.ZipFile(z, 'w', zipfile.ZIP_DEFLATED) as f:
        f.writestr(f'slot_{slot:02d}/caption.sav', caption)
        f.writestr(f'slot_{slot:02d}/progress.sav', prog)
        f.writestr('about.json', json.dumps(info, indent=1) + '\n')
    return z


def request_load(game, node_id):
    """Asks the running game to load the node (runtime/save_tree.c load_poll reads load.txt)."""
    d = tree_dir(game)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / 'load.txt.part'
    tmp.write_text(f'{node_id}\n')
    os.replace(tmp, d / 'load.txt')


def stage_slot(game, node_id, dest):
    """The node's slot written as dest/slot_NN for saves.import_save while the game is not running. The folder."""
    slot, caption, prog = node_save(game, node_id)
    sd = Path(dest) / f'slot_{slot:02d}'
    sd.mkdir(parents=True, exist_ok=True)
    (sd / 'caption.sav').write_bytes(caption)
    (sd / 'progress.sav').write_bytes(prog)
    return sd
