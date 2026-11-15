"""The Save Tree window: a window of its own, apart from the launcher's (it stays while the launcher steps aside for
the game), that draws the game's save tree (launcher/savetree.py) as a tree growing up from its roots, follows the game
as it adds nodes, and loads or exports the node selected.

The tree is drawn in sections: the nodes made one after another in the same place form one section, and a section is
drawn folded into one bud (its place and how many saves it holds) or open, every node a leaf. The sections on the way to
the node the game is on are open at first; a double click on a bud or a leaf folds or opens its section. The search
looks through every node's label, place and changed flags; picking a result opens the sections on its way and shows it.
"""
import math
import os
import shutil
import tempfile
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox

from .. import flagtext, saves, savetree
from . import art

DX, DY = 340, 112              # the room of one leaf across and one step up, at zoom 1
LEAF_R, BUD_R = 7, 15
REFRESH_MS = 800
LABEL_CHARS = 44
KIND_COLOR = {'load': '#e6b65a', 'flag': '#8fd16a', 'autosave': '#6cc7c0', 'manual': '#e88f6c'}
SEARCH_LIMIT = 300


def short(text):
    return text if len(text) <= LABEL_CHARS else text[:LABEL_CHARS - 1] + '…'


class DNode:
    """A drawn node: a leaf (one save) or a bud (a folded section)."""
    __slots__ = ('node', 'section', 'children', 'x', 'y', 'depth', 'weight', 'parent', 'run')

    def __init__(self, node, section=None):
        self.node, self.section = node, section   # section: the section's head node when this is a bud
        self.children, self.parent = [], None
        self.run = False           # a leaf standing for a run of quiet saves one after another (node: the last)
        self.x = self.y = 0.0
        self.depth = 0
        self.weight = 1


class SaveTreeWindow:
    def __init__(self, app):
        self.app = app
        self.game = app.settings.get('game_dir')
        self.tree = savetree.Tree(self.game)
        self.s = getattr(app, 's', 1.0)
        self.zoom = 1.0
        self.open = set()          # heads of the sections drawn open
        self.closed = set()        # heads the player folded (they stay folded when the game moves on)
        self.sel = -1
        self.cur = -1
        self.hits = set()
        self.follow = tk.BooleanVar(value=False)
        self.drawn = {}            # canvas item id -> node id
        self.where = {}            # node id -> (x, y) of its leaf or of the bud that holds it
        self.after = None
        self.search_after = None
        self.resize_after = None
        self.head = []             # node id -> the head of its section
        self.words = []            # node id -> (title, lines) in plain words (flagtext.describe)
        self.quiet = []            # node id -> nothing a player would notice happened (only people's routines)

        P = dict(art.PALETTE)
        P.update(window='#071b17', panel='#102a23', value='#0b211b', desc='#0b211b',
                 line='#42513b', label_top='#304636', label_border='#6c7952',
                 text='#e2dcc6', text_bright='#fff0cf', muted='#a6b5a0', dim='#82937f',
                 gold='#d8bc78', orange='#bda05c', orange_light='#d7bd7c',
                 orange_dark='#5c633e', on_text='#fff3d5', button_hover='#425b41', status='#a6b5a0')
        self.P = P
        w = self.win = tk.Toplevel(app.root)
        w.title('Save Tree')
        w.configure(bg=P['window'])
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        ww, wh = int(min(sw - 100, 1280 * self.s)), int(min(sh - 120, 920 * self.s))
        w.geometry(f'{ww}x{wh}+{max(0, (sw - ww) // 2)}+{max(0, (sh - wh) // 2)}')
        w.minsize(int(720 * self.s), int(540 * self.s))
        w.protocol('WM_DELETE_WINDOW', self.close)
        try:
            if getattr(app, 'ico', None):
                w.iconbitmap(app.ico)
        except tk.TclError:
            pass
        fam = 'Segoe UI'
        px = lambda n: -max(8, int(round(n * self.s)))
        self.f_label = tkfont.Font(root=w, family=fam, size=px(13))
        self.f_bud = tkfont.Font(root=w, family=fam, size=px(14), weight='bold')
        self.f_ui = tkfont.Font(root=w, family=fam, size=px(14))
        self.f_title = tkfont.Font(root=w, family='Georgia', size=px(22))
        self.f_heading = tkfont.Font(root=w, family='Georgia', size=px(27))
        self.f_small = tkfont.Font(root=w, family=fam, size=px(12))
        self.build()
        self.tick()
        self.win.after_idle(self.center_tree)

    # ---------------------------------------------------------------- widgets

    def build(self):
        P, w = self.P, self.win
        heading = tk.Frame(w, bg=P['window'])
        heading.pack(fill='x', padx=24, pady=(18, 12))
        emblem = tk.Canvas(heading, width=52, height=48, bg=P['window'], highlightthickness=0)
        emblem.pack(side='left', padx=(0, 14))
        for x, y in ((26, 6), (15, 25), (37, 25)):
            emblem.create_polygon(x, y, x - 10, y + 17, x + 10, y + 17,
                                  fill=P['gold'], outline='')
        titles = tk.Frame(heading, bg=P['window'])
        titles.pack(side='left')
        tk.Label(titles, text='The Save Tree', font=self.f_heading, bg=P['window'],
                 fg=P['text_bright'], anchor='w').pack(anchor='w')
        tk.Label(titles, text='Every journey leaves a branch.', font=self.f_small,
                 bg=P['window'], fg=P['muted']).pack(anchor='w')
        self.summary = tk.Label(heading, text='', font=self.f_ui, bg=P['window'], fg=P['gold'])
        self.summary.pack(side='right')
        tk.Frame(w, height=1, bg=P['label_border']).pack(fill='x', padx=24)
        bar = tk.Frame(w, bg=P['panel'])
        bar.pack(side='top', fill='x')
        tk.Label(bar, text='Search', bg=P['panel'], fg=P['text'], font=self.f_ui).pack(side='left', padx=(10, 4), pady=8)
        self.q = tk.StringVar()
        e = self.entry = tk.Entry(bar, textvariable=self.q, bg=P['value'], fg=P['text_bright'], insertbackground=P['text'],
                                  relief='flat', font=self.f_ui, highlightthickness=1, highlightbackground=P['line'],
                                  highlightcolor=P['orange'])
        e.pack(side='left', fill='x', expand=True, pady=8)
        self.q.trace_add('write', lambda *_: self.search_soon())
        e.bind('<Return>', lambda _e: self.pick_result(0))
        e.bind('<Escape>', lambda _e: self.q.set(''))
        e.bind('<Down>', lambda _e: (self.results.focus_set(), self.results.selection_set(0)) if self.results_shown else None)
        tk.Checkbutton(bar, text='Follow the game', variable=self.follow, bg=P['panel'], fg=P['text'],
                       selectcolor=P['value'], activebackground=P['panel'], activeforeground=P['text_bright'],
                       font=self.f_ui, command=lambda: self.follow.get() and self.show_node(self.cur)).pack(
            side='left', padx=8)

        tools = tk.Frame(w, bg=P['window'])
        tools.pack(fill='x', padx=20, pady=8)
        tk.Label(tools, text='Drag to explore  ·  Double-click to fold a branch', font=self.f_small,
                 bg=P['window'], fg=P['muted']).pack(side='left')
        for label, command in (('−', lambda: self.set_zoom(self.zoom / 1.15)),
                               ('+', lambda: self.set_zoom(self.zoom * 1.15)),
                               ('Center tree', self.center_tree),
                               ('Current save', lambda: self.show_node(self.cur))):
            tk.Button(tools, text=label, command=command, bg=P['panel'], fg=P['text'],
                      activebackground=P['button_hover'], activeforeground=P['text_bright'],
                      font=self.f_small, relief='flat', bd=0, padx=12, pady=5,
                      cursor='hand2').pack(side='right', padx=3)
        self.zoom_label = tk.Label(tools, text='100%', bg=P['window'], fg=P['gold'], font=self.f_small)
        self.zoom_label.pack(side='right', padx=10)
        self.side_buttons = {}
        for side in ('right', 'bottom', 'left'):   # packed from the right, so they read Left, Bottom, Right
            b = tk.Button(tools, text=side.capitalize(), command=lambda s=side: self.place_details(s),
                          fg=P['text'], activebackground=P['button_hover'], activeforeground=P['text_bright'],
                          font=self.f_small, relief='flat', bd=0, padx=10, pady=5, cursor='hand2')
            b.pack(side='right', padx=1)
            self.side_buttons[side] = b
        tk.Label(tools, text='Details', bg=P['window'], fg=P['muted'], font=self.f_small).pack(side='right', padx=(16, 4))

        self.pane = tk.PanedWindow(w, orient='vertical', bg=P['line'], sashwidth=5, bd=0)
        self.pane.pack(fill='both', expand=True)
        top = self.tree_frame = tk.Frame(self.pane, bg=P['window'])
        c = self.canvas = tk.Canvas(top, bg=P['window'], highlightthickness=0, bd=0)
        ys = tk.Scrollbar(top, orient='vertical', command=c.yview)
        xs = tk.Scrollbar(top, orient='horizontal', command=c.xview)
        c.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side='right', fill='y')
        xs.pack(side='bottom', fill='x')
        c.pack(fill='both', expand=True)
        c.bind('<Configure>', self.resize)
        c.bind('<ButtonPress-1>', self.press)
        c.bind('<B1-Motion>', self.drag)
        c.bind('<ButtonRelease-1>', self.release)
        c.bind('<Double-Button-1>', self.double)
        c.bind('<Button-3>', self.double)
        c.bind('<MouseWheel>', self.wheel)
        c.bind('<Shift-MouseWheel>', self.horizontal_wheel)
        c.bind('<Control-MouseWheel>', self.zoom_wheel)
        # search results, over the tree while a search is typed
        self.results = tk.Listbox(top, bg=P['desc'], fg=P['text'], selectbackground=P['orange_dark'],
                                  selectforeground=P['on_text'], font=self.f_ui, relief='flat', activestyle='none',
                                  highlightthickness=1, highlightbackground=P['orange'])
        self.results.bind('<<ListboxSelect>>', lambda _e: self.pick_result(None))
        self.results.bind('<Return>', lambda _e: self.pick_result(None))
        self.results.bind('<Escape>', lambda _e: (self.q.set(''), self.entry.focus_set()))
        self.results_shown = False
        self.result_ids = []

        info = self.info = tk.Frame(self.pane, bg=P['panel'])
        self.title = tk.Label(info, text='', bg=P['panel'], fg=P['text_bright'], font=self.f_title, anchor='w',
                              justify='left', wraplength=int(520 * self.s))
        self.title.pack(fill='x', padx=12, pady=(10, 0))
        self.meta = tk.Label(info, text='', bg=P['panel'], fg=P['muted'], font=self.f_small, anchor='w', justify='left')
        self.meta.pack(fill='x', padx=12)
        info.bind('<Configure>', self.wrap_details)
        btns = tk.Frame(info, bg=P['panel'])
        btns.pack(side='bottom', fill='x', padx=10, pady=8)

        def button(text, cmd, accent=False):
            b = tk.Button(btns, text=text, command=cmd, font=self.f_ui, relief='flat', bd=0, padx=14, pady=5,
                          bg=P['orange'] if accent else P['label_top'], fg=P['on_text'],
                          activebackground=P['orange_light'] if accent else P['button_hover'],
                          activeforeground=P['on_text'], cursor='hand2')
            b.pack(side='left', padx=4)
            return b
        self.b_load = button('Load Now', self.load, accent=True)
        self.b_export = button('Export…', self.export)
        self.b_fold = button('Fold Section', self.toggle_selected)
        self.status = tk.Label(info, text='', bg=P['panel'], fg=P['status'], font=self.f_small, anchor='w')
        self.status.pack(side='bottom', fill='x', padx=12)
        self.changes = tk.Listbox(info, bg=P['desc'], fg=P['desc_text'], font=self.f_small, relief='flat',
                                  highlightthickness=0, activestyle='none', selectbackground=P['desc'],
                                  selectforeground=P['desc_text'])
        self.changes.pack(fill='both', expand=True, padx=12, pady=(6, 0))
        side = self.app.settings.get('tree_details', 'right')
        self.place_details(side if side in self.side_buttons else 'right', remember=False)
        self.show_details()

    def place_details(self, side, remember=True):
        """Put the details of the selected save below the tree or beside it, on the left or the right. The tree grows
        mostly upwards, so a pane at the side covers less of it. The choice is kept in the launcher's settings."""
        P, pane = self.P, self.pane
        for child in pane.panes():
            pane.forget(child)
        tree_opts = dict(stretch='always', minsize=int(200 * self.s))
        if side == 'bottom':
            pane.configure(orient='vertical')
            pane.add(self.tree_frame, **tree_opts)
            pane.add(self.info, stretch='never', minsize=int(150 * self.s), height=int(220 * self.s))
        else:
            pane.configure(orient='horizontal')
            info_opts = dict(stretch='never', minsize=int(280 * self.s), width=int(420 * self.s))
            if side == 'left':
                pane.add(self.info, **info_opts)
                pane.add(self.tree_frame, **tree_opts)
            else:
                pane.add(self.tree_frame, **tree_opts)
                pane.add(self.info, **info_opts)
        for name, b in self.side_buttons.items():
            b.configure(bg=P['orange_dark'] if name == side else P['panel'])
        if remember and self.app.settings.get('tree_details') != side:
            self.app.settings['tree_details'] = side
            try:
                self.app.settings.save()
            except OSError:
                pass

    def wrap_details(self, event):
        """Wrap the title and the line under it to the width the details pane has now."""
        width = max(100, event.width - int(30 * self.s))
        self.title.configure(wraplength=width)
        self.meta.configure(wraplength=width)

    def close(self):
        for pending in (self.after, self.search_after, self.resize_after):
            if pending:
                self.win.after_cancel(pending)
        self.win.destroy()
        if getattr(self.app, 'tree_window', None) is self:
            self.app.tree_window = None

    def lift(self):
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()

    # ---------------------------------------------------------------- the model

    def describe_new(self):
        """Plain words for the nodes read since the last call; the search looks through them too."""
        nodes = self.tree.nodes
        while len(self.words) < len(nodes):
            n = nodes[len(self.words)]
            title, lines = flagtext.describe(n, nodes[n.parent] if n.parent >= 0 else None)
            self.words.append((title, lines))
            self.quiet.append(n.kind == 'flag' and n.parent >= 0 and not any(t[0] >= 20 for t in lines))
            n.text += ' ' + ' '.join([title] + [t[1] for t in lines]).lower()

    def title_of(self, node_id):
        return self.words[node_id][0] if node_id < len(self.words) else self.tree.nodes[node_id].label

    def sections(self):
        """self.head for every node: a node starts a section when it is a root or its parent is in another place."""
        nodes = self.tree.nodes
        while len(self.head) < len(nodes):
            n = nodes[len(self.head)]
            p = n.parent
            self.head.append(n.id if p < 0 or nodes[p].area != n.area else self.head[p])

    def open_path(self, node_id):
        for i in self.tree.path_to(node_id):
            self.open.add(self.head[i])
            self.closed.discard(self.head[i])

    def is_open(self, head):
        return head in self.open and head not in self.closed

    def display(self):
        """The drawn tree: a DNode per leaf of an open section and per bud of a folded one, under a ground node."""
        nodes, head = self.tree.nodes, self.head
        ground = DNode(None)
        made = {}   # node id -> its DNode (a bud for every member of a folded section)
        runs = {}   # id(DNode) -> the node ids of a run of quiet saves

        def dnode_of(n):
            h = head[n.id]
            if self.is_open(h):
                d = made.get(n.id)
                if d is None:
                    p = n.parent
                    if (self.quiet[n.id] and p >= 0 and head[p] == h and self.quiet[p]
                            and len(nodes[p].children) == 1):
                        # quiet saves one after another are one leaf: "Time passed ... (12 saves)"
                        d = made[n.id] = dnode_of(nodes[p])
                        d.run = True
                        d.node = n
                        d.weight += 1
                        runs.setdefault(id(d), [p]).append(n.id)
                        return d
                    d = made[n.id] = DNode(n)
                    attach(d, n)
                return d
            d = made.get(h)
            if d is None:
                d = made[h] = DNode(nodes[h], section=h)
                attach(d, nodes[h])
            made[n.id] = d
            return d

        def attach(d, n):
            parent = dnode_of(nodes[n.parent]) if n.parent >= 0 else ground
            d.parent = parent
            parent.children.append(d)

        for n in nodes:
            dnode_of(n)
        # a bud's weight is how many saves it holds
        self.members = dict(runs)
        for n in nodes:
            d = made[n.id]
            if d.section is not None:
                self.members.setdefault(id(d), []).append(n.id)
                if n.id != d.section:
                    d.weight += 1
        return ground

    def layout(self, ground):
        """Leaves side by side, each parent above the middle of its children, the ground at the bottom."""
        order = [0]
        # iterative, to stay clear of Python's recursion limit on long chains
        stack = [(ground, 0, False)]
        while stack:
            d, depth, done = stack.pop()
            if not done:
                d.depth = depth
                stack.append((d, depth, True))
                for ch in reversed(d.children):
                    stack.append((ch, depth + 1, False))
            elif not d.children:
                d.x = order[0]
                order[0] += 1
            else:
                d.x = (d.children[0].x + d.children[-1].x) / 2
        # how much grows above each node: branches thin out as they rise
        post = []
        stack = [ground]
        while stack:
            d = stack.pop()
            post.append(d)
            stack.extend(d.children)
        size = {}
        for d in reversed(post):
            size[id(d)] = d.weight + sum(size[id(ch)] for ch in d.children)
        self.size = size
        return order[0], max((d.depth for d in post), default=0)

    # ---------------------------------------------------------------- drawing

    def redraw(self, keep_view=True):
        c, P = self.canvas, self.P
        self.sections()
        self.describe_new()
        if 0 <= self.cur < len(self.tree.nodes):
            if not self.open:   # the first drawing: the sections on the way to the game's node are open
                self.open_path(self.cur)
            elif self.head[self.cur] not in self.closed:
                self.open.add(self.head[self.cur])
        ground = self.display()
        width, depth = self.layout(ground)
        dx, dy = DX * self.zoom * self.s, DY * self.zoom * self.s
        base = 82 * self.s   # where the trunk starts, its roots above it; the saves grow down from there

        def pos(d):
            return 210 * self.s + d.x * dx, base + d.depth * dy

        view_center = (c.canvasx(c.winfo_width()/2), c.canvasy(c.winfo_height()/2))
        c.delete('all')
        self.drawn.clear()
        self.where.clear()
        gx = 210 * self.s + max(0, width - 1) / 2 * dx
        right = 420 * self.s + max(0, width - 1) * dx
        bottom = base + (depth + 1) * dy + 80 * self.s
        # Muted leaf sprays frame the history without competing with save labels.
        for side in (-1, 1):
            edge = 26*self.s if side < 0 else right-26*self.s
            for step in range(7):
                yy = 90*self.s + step*32*self.s
                xx = edge + side*math.sin(step*0.6)*10*self.s
                c.create_line(xx, yy, xx-side*5*self.s, yy+34*self.s,
                              fill='#203c2d', width=1)
                for direction in (-1, 1):
                    c.create_polygon(xx, yy, xx+direction*20*self.s, yy-18*self.s,
                                     xx+direction*24*self.s, yy-8*self.s,
                                     xx+direction*12*self.s, yy+2*self.s,
                                     fill='#142f25', outline='#24432f', smooth=True)
        # Fine gold roots and a quiet canopy keep the branch connections legible.
        wood, wood_dark = '#738254', '#3b5540'
        for k in range(-3, 4):
            c.create_line(gx, base, gx + k * 14 * self.s, base - 20 * self.s,
                          gx + k * 26 * self.s, base - 45 * self.s,
                          smooth=True, width=max(1, 2 * self.s), fill=wood_dark)
        c.create_polygon(gx, base - 49 * self.s, gx - 4 * self.s, base - 43 * self.s,
                         gx, base - 37 * self.s, gx + 4 * self.s, base - 43 * self.s,
                         fill=P['gold'], outline='')

        # branches first, then leaves on top
        maxsize = max(1, self.size[id(ground)])
        items = []
        stack = [ground]
        while stack:
            d = stack.pop()
            items.append(d)
            stack.extend(d.children)
        for d in items:
            if d.parent is None:
                continue
            x0, y0 = pos(d.parent) if d.parent.node is not None else (gx, base)
            x1, y1 = pos(d)
            wgt = 1 + 3 * math.log1p(self.size[id(d)]) / math.log1p(maxsize)
            mid = (y0 + y1) / 2
            c.create_line(x0, y0, x0, mid, x1, mid, x1, y1, smooth=True, width=wgt * self.s ** 0.5 * self.zoom ** 0.5,
                          fill=wood if d.parent.node is not None else wood_dark, capstyle='round')
        for d in items:
            if d.node is None:
                continue
            x, y = pos(d)
            n = d.node
            ids = self.members.get(id(d), [n.id]) if d.section is not None or d.run else [n.id]
            for m in ids:
                self.where[m] = (x, y)
            current, selected = self.cur in ids, self.sel in ids
            ring = '#8ed7e9' if current else P['gold'] if selected or self.hits & set(ids) else '#4b654b'
            fill = '#173d35' if selected else '#102b24'
            half_w = min(148, DX * self.zoom / 2 - 14) * self.s
            half_h = min(39, DY * self.zoom / 2 - 9) * self.s
            node_id = d.section if d.section is not None else n.id
            def remember(item):
                self.drawn[item] = node_id
            # Chamfered corners echo the framed panels of the game's menus.
            cut = 7 * self.s
            points = (x-half_w+cut, y-half_h, x+half_w-cut, y-half_h,
                      x+half_w, y-half_h+cut, x+half_w, y+half_h-cut,
                      x+half_w-cut, y+half_h, x-half_w+cut, y+half_h,
                      x-half_w, y+half_h-cut, x-half_w, y-half_h+cut)
            if current or selected:
                c.create_rectangle(x-half_w-3, y-half_h-3, x+half_w+3, y+half_h+3,
                                   outline='#284c48' if current else '#494a31', width=2)
            remember(c.create_polygon(*points, fill=fill, outline=ring, width=2 if current or selected else 1))
            if self.zoom < 0.55:
                remember(c.create_text(x, y, text=str(d.weight) if d.weight > 1 else str(n.id),
                                       fill=ring, font=self.f_small))
                continue
            left = x - half_w + 15 * self.s
            usable = 2 * half_w - 30 * self.s
            def fitted(text, font, limit):
                if font.measure(text) <= limit:
                    return text
                while text and font.measure(text + '…') > limit:
                    text = text[:-1]
                return text + '…'
            title = flagtext.place(n.area) if d.section is not None else self.title_of(n.id)
            if self.zoom < 0.85:
                remember(c.create_text(left, y, text=fitted(title, self.f_small, usable),
                                       anchor='w', fill=P['text'], font=self.f_small))
                continue
            badge = 'NOW' if current else ('FOLDED' if d.section is not None else f'SAVE {n.id}')
            color = '#9ad8e5' if current else P['gold']
            remember(c.create_text(left, y - 23*self.s, text=badge, anchor='w',
                                   fill=color, font=self.f_small))
            remember(c.create_text(left, y - 2*self.s, text=fitted(title, self.f_bud, usable),
                                   anchor='w', fill=P['text_bright'], font=self.f_bud))
            detail = (f'{d.weight} saves · double-click to open' if d.section is not None else
                      f'{d.weight} quiet saves' if d.run else
                      f'{flagtext.place(n.area)} · {n.time[-8:]}')
            remember(c.create_text(left, y + 21*self.s, text=fitted(detail, self.f_small, usable),
                                   anchor='w', fill=P['muted'], font=self.f_small))
            if current:
                c.create_oval(x+half_w-19*self.s, y-half_h+11*self.s,
                              x+half_w-11*self.s, y-half_h+19*self.s,
                              fill='#b8edff', outline='#76b9d1')
        if not self.tree.nodes:
            c.create_text(gx + 140 * self.s, base + 120 * self.s, fill=P['muted'], font=self.f_ui, justify='center',
                          text='No saves in the tree yet.\nPlay a save: every flag the game changes\nadds a save here.')
        self.tree_bounds = (0, 0, right, bottom)
        if keep_view:
            self.center_on(*view_center)
        else:
            self.update_region()
        self.summary.configure(text=f'{len(self.tree.nodes)} saves  /  {len(self.tree.roots)} journeys')

    def update_region(self, target=None):
        c = self.canvas
        left, top, right, bottom = getattr(self, 'tree_bounds', (0, 0, 1, 1))
        vw, vh = max(1, c.winfo_width()), max(1, c.winfo_height())
        # A smaller tree occupies the middle of the viewport. Extra travel at
        # every edge lets even the first and last save sit exactly at its center.
        cx, cy = (left + right) / 2, (top + bottom) / 2
        left, right = min(left, cx-vw/2), max(right, cx+vw/2)
        top, bottom = min(top, cy-vh/2), max(bottom, cy+vh/2)
        if target is not None:
            x, y = target
            left, right = min(left, x-vw/2), max(right, x+vw/2)
            top, bottom = min(top, y-vh/2), max(bottom, y+vh/2)
        c.configure(scrollregion=(left, top, right, bottom))
        return left, top, right, bottom

    def center_on(self, x, y):
        c = self.canvas
        c.update_idletasks()
        left, top, right, bottom = self.update_region((x, y))
        c.xview_moveto((x-c.winfo_width()/2-left)/max(1, right-left))
        c.yview_moveto((y-c.winfo_height()/2-top)/max(1, bottom-top))

    def center_tree(self):
        self.follow.set(False)
        left, top, right, bottom = getattr(self, 'tree_bounds', (0, 0, 1, 1))
        self.center_on((left+right)/2, (top+bottom)/2)

    def show_node(self, node_id):
        if node_id in self.where:
            self.center_on(*self.where[node_id])
        else:
            self.center_tree()

    def resize(self, event=None):
        if self.resize_after:
            self.win.after_cancel(self.resize_after)
        self.resize_after = self.win.after(100, self.recenter)

    def recenter(self):
        self.resize_after = None
        if self.follow.get() and self.cur in self.where:
            self.show_node(self.cur)
        else:
            c = self.canvas
            self.center_on(c.canvasx(c.winfo_width()/2), c.canvasy(c.winfo_height()/2))

    # ---------------------------------------------------------------- the live tree

    def tick(self):
        self.after = None
        try:
            added = self.tree.refresh()
            cur = self.tree.current()
            moved = cur != self.cur
            self.cur = cur
            if added or moved or not self.drawn:
                follow = self.follow.get() and (moved or added)
                if follow and cur >= 0:
                    self.sections()
                    if cur < len(self.head):
                        self.closed.discard(self.head[cur])
                        self.open.add(self.head[cur])
                self.redraw(keep_view=True)
                if self.sel < 0 and cur >= 0:
                    self.select(cur)
                elif moved:
                    self.show_details()
                if follow and cur >= 0:
                    self.show_node(cur)
            running = self.game and saves.game_running(self.game)
            st = self.tree.status()
            self.status.configure(text=f'{len(self.tree.nodes)} saves · ' + (st if running else 'the game is not running'))
        except (OSError, tk.TclError) as e:
            self.status.configure(text=str(e)[:80])
        if self.win.winfo_exists():
            self.after = self.win.after(REFRESH_MS, self.tick)

    # ---------------------------------------------------------------- selection and details

    def select(self, node_id):
        self.follow.set(False)
        self.sel = node_id
        self.show_details()
        self.redraw(keep_view=True)

    def show_details(self):
        P = self.P
        nodes = self.tree.nodes
        self.changes.delete(0, 'end')
        if not (0 <= self.sel < len(nodes)):
            self.title.configure(text='Save Tree')
            self.meta.configure(text='Select a save to load it or export it.')
            for b in (self.b_load, self.b_export, self.b_fold):
                b.configure(state='disabled')
            return
        n = nodes[self.sel]
        self.describe_new()
        title, lines = self.words[n.id]
        self.title.configure(text=title)
        kind = {'load': 'the save as it was loaded', 'flag': 'saved when these flags changed',
                'autosave': 'an autosave of the game', 'manual': 'a save from the game\'s menu'}.get(n.kind, n.kind)
        here = '  ·  the game is here' if n.id == self.cur else ''
        self.meta.configure(text=f'Save {n.id} · {n.area} · slot {n.slot + 1} · {n.time} · {kind}{here}\n'
                                 f'{n.n} flag{"s" if n.n != 1 else ""} changed · {len(n.children)} branch'
                                 f'{"es" if len(n.children) != 1 else ""} from here')
        for rank, text, raw in lines:
            self.changes.insert('end', ('• ' if rank >= 20 else '   ') + text)
            if rank < 20:
                self.changes.itemconfig('end', fg=P['muted'])
        if not lines:
            self.changes.insert('end', 'Nothing changed: the save as the game made it.')
        if n.changes:   # the game's own flag names under it, for anyone who wants them
            self.changes.insert('end', '')
            self.changes.insert('end', 'The game\'s flags:')
            self.changes.itemconfig('end', fg=P['dim'])
            for ch in n.changes:
                typ, name, old, new = (list(ch) + ['', '', '', ''])[:4]
                self.changes.insert('end', f'   {name}: {old} → {new}')
                self.changes.itemconfig('end', fg=P['dim'])
        for b in (self.b_load, self.b_export, self.b_fold):
            b.configure(state='normal')
        self.b_fold.configure(text='Fold Section' if self.is_open(self.head[n.id]) else 'Open Section')

    def toggle_section(self, head):
        if self.is_open(head):
            self.closed.add(head)
            self.open.discard(head)
        else:
            self.closed.discard(head)
            self.open.add(head)
        self.redraw(keep_view=True)
        self.show_details()

    def toggle_selected(self):
        if 0 <= self.sel < len(self.head):
            self.toggle_section(self.head[self.sel])
            self.show_node(self.sel)

    def node_at(self, e):
        c = self.canvas
        x, y = c.canvasx(e.x), c.canvasy(e.y)
        for i in reversed(c.find_overlapping(x - 3, y - 3, x + 3, y + 3)):
            if i in self.drawn:
                return self.drawn[i]
        return None

    def press(self, e):
        self.follow.set(False)
        self.pressed = (e.x, e.y)
        self.dragging = False
        self.canvas.scan_mark(e.x, e.y)

    def drag(self, e):
        if abs(e.x - self.pressed[0]) + abs(e.y - self.pressed[1]) > 4:
            self.dragging = True
            self.canvas.scan_dragto(e.x, e.y, gain=1)

    def release(self, e):
        if self.dragging:
            return
        nid = self.node_at(e)
        if nid is not None:
            self.select(nid)

    def double(self, e):
        nid = self.node_at(e)
        if nid is not None and nid < len(self.head):
            self.sel = nid
            self.toggle_section(self.head[nid])
            self.show_node(nid)

    def horizontal_wheel(self, e):
        self.follow.set(False)
        self.canvas.xview_scroll(-1 if e.delta > 0 else 1, 'units')

    def wheel(self, e):
        self.follow.set(False)
        self.canvas.yview_scroll(-1 if e.delta > 0 else 1, 'units')

    def set_zoom(self, zoom):
        self.follow.set(False)
        c = self.canvas
        self.zoom_center = (c.canvasx(c.winfo_width()/2), c.canvasy(c.winfo_height()/2))
        self.zoom = min(2.0, max(0.25, zoom))
        self.zoom_label.configure(text=f'{self.zoom:.0%}')
        self.redraw(keep_view=True)
        self.center_on(*self.zoom_center)

    def zoom_wheel(self, e):
        self.set_zoom(self.zoom * (1.15 if e.delta > 0 else 1 / 1.15))
        return 'break'

    # ---------------------------------------------------------------- search

    def search_soon(self):
        if self.search_after:
            self.win.after_cancel(self.search_after)
        self.search_after = self.win.after(250, self.search)

    def search(self):
        self.search_after = None
        q = self.q.get().strip()
        self.describe_new()
        found = self.tree.search(q, SEARCH_LIMIT) if q else []
        self.hits = {n.id for n in found}
        self.result_ids = [n.id for n in found]
        r = self.results
        r.delete(0, 'end')
        if not q:
            if self.results_shown:
                r.place_forget()
                self.results_shown = False
            self.redraw(keep_view=True)
            return
        for n in found:
            r.insert('end', f'{n.id}  ·  {self.title_of(n.id)}  ·  {flagtext.place(n.area)}')
        if not found:
            r.insert('end', 'Nothing matches')
        r.place(relx=0, rely=0, relwidth=1, height=int(min(10, max(1, len(found))) * 24 * self.s) + 8)
        self.results_shown = True
        self.redraw(keep_view=True)

    def pick_result(self, index):
        if index is None:
            sel = self.results.curselection()
            index = sel[0] if sel else None
        if index is None or index >= len(self.result_ids):
            return
        nid = self.result_ids[index]
        self.open_path(nid)
        self.results.place_forget()
        self.results_shown = False
        self.select(nid)
        self.show_node(nid)

    # ---------------------------------------------------------------- load and export

    def load(self):
        n = self.tree.nodes[self.sel] if 0 <= self.sel < len(self.tree.nodes) else None
        if not n or not self.game:
            return
        if saves.game_running(self.game):
            savetree.request_load(self.game, n.id)
            self.status.configure(text=f'Loading save {n.id} in the game…')
            return
        if not messagebox.askyesno('Load this save', f'Make save {n.id} ("{self.title_of(n.id)}") the save in slot {n.slot + 1}?\n\n'
                                   'The slot\'s current save is copied to the saves folder first. Start the game and '
                                   'load that slot to play it.', parent=self.win):
            return
        stage = tempfile.mkdtemp(prefix='nx2navi-tree-')
        try:
            folder = savetree.stage_slot(self.game, n.id, stage)
            r = saves.import_save(self.game, folder=folder, log=self.app.ctx.log)
            self.status.configure(text=f'Save {n.id} is in slot {n.slot + 1}' +
                                  (f'; the old one is in {r["backup"].name}' if r.get('backup') else ''))
        except (savetree.TreeError, saves.SaveError, OSError) as e:
            messagebox.showerror('Load this save', str(e), parent=self.win)
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def export(self):
        n = self.tree.nodes[self.sel] if 0 <= self.sel < len(self.tree.nodes) else None
        if not n:
            return
        start = self.app.export_dir() if hasattr(self.app, 'export_dir') else saves.default_export_dir()
        d = filedialog.askdirectory(parent=self.win, title=f'Export save {n.id} to', initialdir=str(start), mustexist=True)
        if not d:
            return
        try:
            z = savetree.export_node(self.game, n, d, self.title_of(n.id))
        except (savetree.TreeError, OSError) as e:
            messagebox.showerror('Export this save', str(e), parent=self.win)
            return
        self.app.ctx.log(f'save tree: exported node {n.id} to {z}')
        self.status.configure(text=f'Exported to {z.name}')
        try:
            if os.name == 'nt':
                import subprocess
                subprocess.Popen(['explorer', '/select,', str(z)])
        except OSError:
            pass
