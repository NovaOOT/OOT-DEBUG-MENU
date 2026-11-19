"""The launcher's window, laid out like the game's options menu (docs/launcher.md, "Look"): a wooden title band with
Navi and the name in the game's Cetra Display across the whole top (the window's own title bar: drag it to move the
window, minimise and close at its right end), a list of rows on the left (a bronze label cell and a dark value cell each), a white
glowing cursor on the selected row, filigree dividers between groups and a description box on the right that
explains the selected row.

The window is one Tk canvas at 1000 x 640 logical pixels, scaled to the display. The pictures come from art.py; text,
flat cells and the orange choices are canvas items. Screens:

    first_run   the dump, the install folder, three choices and Begin Installation
    home        Play, Updates, the game folder, Saves, Rebuild the Game, Customize Launcher, Settings
    customize   the colour scheme, the player's own background picture, animations and sounds; Add to Steam
    color       the Custom scheme's colour picker: a colour square, a hue bar and the hex code
    saves       Export Save (a zip for a bug report or another player), Import a Save (such a zip), a save folder
    job         the steps of an install, update or rebuild, with their progress, and Cancel; after a failure Try Again
                and Copy Report
    settings    the launcher's choices, the folders, the logs and Uninstall
    uninstall   what to remove, then Uninstall or Cancel

Mouse, keyboard (arrows, Enter or Space, Esc, F5) and an XInput gamepad (d-pad or stick, A, B, Y) all move the cursor.

The look is the player's (Customize Launcher): art.set_theme fills the palette every picture is drawn from, a picture
of the player's own can replace the backdrop (picture.py), and unless Animations is off the cursor glides between
rows, a new screen slides in, sparks drift over the backdrop and Navi bobs in the title band (animate). sound.py
plays the game's own decide and cancel sounds, read from the player's game, for a choice and for going back.
"""
import argparse
import colorsys
import math
import os
import random
import shutil
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox

from .. import NAME, build, feed, gameini, gamefiles, installs, jobs, locks, mods, rebrand, saves, steam, steamlib, versions, winsys
from ..context import Context
from ..paths import long_path_problem
from . import art, picture, sound

W, H = 1440, 850
BAND = (0, W, 178)             # the header inside the frame also moves the frameless window
PIC_TOP = 88
TITLE_BUTTONS = (('min', 1250, 1296), ('close', 1296, 1342))
TITLE_BUTTON_Y = (88,124)
PANEL = (365, 88, 1375)         # one continuous dark surface beneath the brass frame
ROWS = (390, 895, 188)
LABEL_W = 255
GAP = 9
DESC = (925, 390)
P = art.PALETTE                 # the colour scheme in use: art.set_theme changes it in place
FRAME_MS = 15                   # between two steps of the animations; they move by the time that really passed
SPARKS = 22                     # the dust drifting over the backdrop
DEPTH = {'first_run': 0, 'home': 0, 'job': 1, 'saves': 1, 'settings': 1, 'customize': 1, 'uninstall': 2, 'versions': 1,
         'color': 2, 'mods': 1, 'mod_order': 2}
MODS_PAGE = 7     # mods on a page of the Mods screen, beside its other rows
ORDER_PAGE = 10   # ... of the Load Order screen
# the Custom scheme's picker (draw_picker), logical pixels: its box (x, top, width, height), the colour square and
# the hue bar (x0, y0, x1, y1)
PICKER_BOX = (925, 188, 390, 452)
SQUARE = (990, 248, 1220, 478)
BAR = (990, 496, 1220, 518)
CUSTOM_LABELS = {'background': 'Background Color', 'panel': 'Panel Color', 'buttons': 'Button Color'}
CUSTOM_DESC = {'background': 'The color behind the menus.', 'panel': 'The color of the panels and the top bar.',
               'buttons': 'The color of the buttons.'}
DIMS = (0, 20, 40, 60, 80)      # Background Dimming, in percent
SOUND_LEVELS = (('off', 'Off'), ('low', 'Low'), ('medium', 'Medium'), ('high', 'High'))
# Import a Save and Import a Save Folder are switched off for now: the rows show as off, the home's Saves row no longer
# says a save waits, and selecting a row anyway asks past a warning that the import may break the saves.
IMPORT_DISABLED = True
IMPORT_WARNING = ('Importing saves is turned off for now.\n\n'
                  'IT MAY BREAK YOUR SAVES. The import replaces the game\'s save, and an imported save can leave '
                  'your saves broken or glitched.\n\n'
                  'Your current save is copied to the saves folder first.\n\nImport anyway?')


class Row:
    def __init__(self, kind, label='', value='', style='normal', big=False, icon=None, badge=False, desc=None,
                 on=None, left=None, right=None, get=None, options=None, fraction=0.0, actions=None, check=False,
                 focus_value=False, height=None, enabled=True):
        self.kind, self.label, self.value, self.style = kind, label, value, style
        self.big, self.icon, self.badge, self.desc, self.check = big, icon, badge, desc, check
        self.on, self.left, self.right, self.get, self.options = on, left, right, get, options
        self.fraction, self.actions, self.focus_value, self.enabled = fraction, actions or [], focus_value, enabled
        default_h = {'divider': 26, 'action': 54, 'pair': 44}.get(kind, 64 if big else 40)
        self.h = height or default_h
        self.rect = self.value_rect = None
        self.parts = []              # pair: [(rect, label, callback)]

    @property
    def focusable(self):
        return self.kind != 'divider'


def fit(font, text, width):
    """text shortened in the middle with an ellipsis so it fits width pixels."""
    if font.measure(text) <= width:
        return text
    lo, hi = 1, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        keep = mid // 2
        cand = text[:keep] + '…' + text[len(text) - (mid - keep):]
        if font.measure(cand) <= width:
            lo = mid
        else:
            hi = mid - 1
    keep = lo // 2
    return text[:keep] + '…' + text[len(text) - (lo - keep):]


def free_gb(path):
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        return shutil.disk_usage(p).free / 1e9
    except OSError:
        return 0.0


# Exit codes that end the game, and what they mean. The game exits with 70 after a FATAL line of its own: an error it
# cannot go on from, most often an allocation that failed because the memory ran out (runtime/dispatch.c rt_fatal), or
# a failed re-run of itself (runtime/platform/win32.c plat_reexec). Its crash report exits with 71 after writing its
# FATAL lines (runtime/main.c crash_handler). The others come from Windows when something ends the process before or
# around that report.
EXIT_CODES = {
    70: 'a fatal error: the FATAL line near the end of its log says which, often memory that ran out',
    71: 'the crash report ended the game',
    0xC0000005: 'access violation',
    0xC00000FD: 'stack overflow',
    0xC0000142: 'a DLL failed to start',
    0xC0000374: 'heap corruption',
    0xC0000409: 'fail-fast: a security check or abort ended the process without an exception',
    0xC0000417: 'invalid parameter to a C runtime function',
    0xC000041D: 'a fault inside a Windows callback',
    0xE06D7363: 'a C++ exception no one caught',
}


def exit_code_text(code):
    """The exit code as the game's log and Windows show it: hex for a Windows status, with its meaning when known."""
    code &= 0xFFFFFFFF
    meaning = EXIT_CODES.get(code)
    shown = f'0x{code:08X}' if code >= 0x80000000 else str(code)
    return f'{shown} ({meaning})' if meaning else shown


FATAL_TAIL = 256 << 10   # bytes of the game's log last_fatal reads from its end
FATAL_SHOWN = 240        # characters of a FATAL line the home screen shows; the launcher's log has all of it


def last_fatal(game_dir):
    """The last FATAL line of the game's latest run, without its "FATAL: ", or ''. The game writes one before it
    exits with 70 (runtime/dispatch.c rt_fatal) or 71 (runtime/main.c crash_handler) into logs/ootpc.log, which
    each run starts anew (runtime/main.c log_open). A line this launcher added for an earlier run's end
    (note_exit_in_log) ends the search: what stands before it is not this run's."""
    if not game_dir:
        return ''
    try:
        with open(Path(game_dir) / 'logs' / 'ootpc.log', 'rb') as f:
            f.seek(max(0, f.seek(0, 2) - FATAL_TAIL))
            lines = f.read().decode('utf-8', 'replace').splitlines()
    except OSError:
        return ''
    for line in reversed(lines):
        if line.startswith('[launcher] the game ended'):
            return ''
        if line.startswith('FATAL: '):
            return line[len('FATAL: '):].strip()
    return ''


def note_exit_in_log(game_dir, text):
    """Adds the exit code to the end of the game's log, so a log a player sends shows how the run ended even when the
    process died before it could write a line itself."""
    if not game_dir:
        return
    log = Path(game_dir) / 'logs' / 'ootpc.log'
    try:
        with open(log, 'a', encoding='utf-8') as f:
            f.write(f'[launcher] the game ended with code {text} at {time.strftime("%Y-%m-%d %H:%M:%S")}\n')
    except OSError:
        pass


def partial_image_gb(game_dir):
    """The size of what an install that was cut short left of the RomFS image (romfs/romfs.bin.part, up to 26 GB).
    The next install writes the image anew (tools/make_game.py step_romfs), so that space is the install's to use:
    the first-run screen counts it as free."""
    try:
        return (Path(game_dir) / 'romfs' / 'romfs.bin.part').stat().st_size / 1e9
    except OSError:
        return 0.0


def drop_partial_image(game_dir):
    """Removes that file (partial_image_gb), so that it does not count against the free space an install asks
    for."""
    try:
        (Path(game_dir) / 'romfs' / 'romfs.bin.part').unlink(missing_ok=True)
    except OSError:
        pass


# Seconds after an update check before the Updates row or F5 starts another. Each check is a request to GitHub,
# which allows 60 an hour for an address: a player who selected the row 25 times in 90 seconds used the allowance
# up, and every check of the next hour was refused.
FORCED_CHECK_PAUSE = 60

# How long an update's rebuild takes, for the Updates row. No single figure holds: a version that changed little
# compiles a few files and links, minutes; one that changed the lifter translates and compiles the game's code again,
# and one that changed what the shaders are built with builds all of them into the graphics driver's cache again
# (tools/make_game.py warm_driver_cache). Players' updates took 24, 34 and 45 minutes where the text said "a few
# minutes".
UPDATE_TAKES = ('That takes a few minutes when little changed, and half an hour or more when the game\'s code or '
                'its shaders are built again.')


def drive_of(path):
    return Path(path).anchor.rstrip('\\/') or str(Path(path).parent)


class App:
    def __init__(self, root, ctx, args):
        self.root, self.ctx, self.args = root, ctx, args
        self.layout, self.settings = ctx.layout, ctx.settings
        self.s = self.compute_scale()
        self.sprites = {}
        self.photos = {}
        self.rows, self.focus = [], 0
        self.screen = None
        self.job = None
        self.job_rev = -1
        self.job_return = 'home'
        self.check = None
        self.release = None
        self.check_error = ''
        self.checked_at = 0.0
        self.check_started = 0.0
        # the update fetched in the background and installed when the game is not running; a checkout and the
        # selftest have nothing to switch to
        self.bg = jobs.BackgroundUpdate(ctx, lambda a: winsys.restart(self.layout, a),
                                        enabled=not self.layout.dev and not args.selftest)
        self.row_gap = GAP
        self.message = ''
        self.look_message = ''   # why the last background picture was not taken, in Customize Launcher's descriptions
        self.save_message = ''   # the last import's result, shown in the import rows' descriptions
        self.export_message = ''   # the last Export Save result, shown in its description
        self.steam_message = ''   # the last Add to Steam result, shown in its description
        self.mods_message = ''   # what the last change on the Mods screen did, shown in its descriptions
        self.n64_busy = False    # make_n64 is writing the N64 Graphics mod
        self.dark_busy = False   # make_dark_link is writing the Dark Link mod
        self.mods_page = 0       # the page of the Mods and Load Order screens
        self.mods_failed = []    # the mods whose code a build before Play could not take in: Play no longer waits
        self.steam_busy = False   # Add to Steam is making the pictures or writing Steam's files
        self.pick = None   # the colour picker: {'slot', 'h', 's', 'v'} (HSV in 0..1) of the colour being picked
        self.pick_drag = None   # 'square' or 'bar' while the mouse drags on the picker
        self.hex_entry = None   # the box the hex code is typed into
        self.look_after = None   # the redraw a picked colour waits for (look_soon)
        self.dumps = None
        self.dump_search = None
        self.dump_versions = None   # the game versions the dump holds, [(version, buildable)] (find_dump_versions)
        self.dump_versions_of = ''  # the dump they were read from
        if self.settings.get('installs') != installs.entries(self.settings):   # the folder from before versions
            self.settings.save()
        self.uninstall_game = False
        self.game_proc = None
        self.tree_window = None    # the Save Tree window (ui/savetree.py) while it is open
        self.tree_only = False     # the launcher's window was closed while the Save Tree was open (on_close)
        self.instance_lock = None  # the single-instance lock (main), let go of when only the Save Tree is left
        self.cursor_frames, self.cursor_k = [], 0
        self.last_second = 0
        self.gamepad = winsys.Gamepad()
        self.tb_hover = None
        self.drag = None
        self.ico = None
        # the look: the colour scheme, the player's background picture at the window's size (None: the scheme's own
        # backdrop), the sounds (none while the selftest draws the screens)
        art.set_theme(self.settings.get('theme'), self.custom_colors())
        self.bg_store = self.layout.data / 'background.npy'
        self.picture = None
        self.sounds = sound.Sounds(self.layout.cache, 'off' if args.selftest else self.settings.get('sounds'), ctx.log)
        # the animations' state (animate): the clock and the last step's time, the cursor's place on its way to the
        # focused row, how far the screen still stands from its place after a change, the sparks, Navi and its halo
        self.anim_t = 0.0
        self.anim_last = time.perf_counter()
        self.cur_xy = self.cur_target = None
        self.cur_screen = None
        self.slide = 0.0
        self.slide_applied = 0
        self.sparks = []
        self.navi_xy = None
        self.halo = []

        root.title(NAME)
        root.resizable(False, False)
        window_bg='#010203' if os.name == 'nt' else P['window']
        root.configure(background=window_bg)
        self.canvas = tk.Canvas(root, width=self.X(W), height=self.X(H), highlightthickness=0, bd=0,
                                background=window_bg, cursor='arrow')
        self.canvas.pack()
        self.set_icon()
        self.load_fonts()
        self.load_picture()
        self.draw_static()
        self.bind_input()
        self.start()
        self.root.after(80, self.animate)
        self.root.after(150, self.tick)
        self.root.after(50, self.poll_gamepad)
        self.root.after(60000, self.minute)
        self.center()
        self.framed = not winsys.frameless(root)   # Windows: the band is the title bar
        if os.name == 'nt':
            root.attributes('-transparentcolor','#010203')
        self.root.after(60, self.native_window)

    # ---------------------------------------------------------------- scale, icon, fonts

    def compute_scale(self):
        try:
            s = self.root.winfo_fpixels('1i') / 96.0
        except tk.TclError:
            s = 1.0
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        s = min(max(s, 1.0), sw * 0.94 / W, sh * 0.88 / H)
        return max(0.75, round(s * 4) / 4)

    def X(self, v):
        return int(round(v * self.s))

    def center(self):
        self.root.update_idletasks()
        left,top,right,bottom=art.forest_bounds(self.X(W),self.X(H))
        w,h=right-left,bottom-top
        self.canvas.configure(width=w,height=h,scrollregion=(0,0,self.X(W),self.X(H)),
                              xscrollincrement=1,yscrollincrement=1,confine=False)
        self.canvas.xview_moveto(left/self.X(W))
        self.canvas.yview_moveto(top/self.X(H))
        x = max(0, (self.root.winfo_screenwidth() - w) // 2)
        y = max(0, (self.root.winfo_screenheight() - h) // 3)
        self.root.geometry(f'{w}x{h}+{x}+{y}')

    def set_icon(self):
        """The project's icon as the window's icon: Tk's photo icon everywhere, and on Windows the .ico set on the window
        itself, so the taskbar button shows it rather than Python's icon."""
        try:
            self.photos['icon'] = tk.PhotoImage(master=self.root, data=art.app_icon_png(), format='png')
            self.root.iconphoto(True, self.photos['icon'])
            if os.name == 'nt':
                data = art.app_ico()
                self.ico = self.layout.cache / 'ootpc.ico'
                if not self.ico.is_file() or self.ico.read_bytes() != data:
                    self.ico.write_bytes(data)
        except (tk.TclError, OSError) as e:
            self.ctx.log(f'icon: {e}')

    def native_window(self):
        if self.ico:
            winsys.set_window_icon(self.root, self.ico)
        try:
            self.root.focus_force()
        except tk.TclError:
            pass

    def load_fonts(self):
        """The game's Cetra Display when this PC has decoded it from the player's game (data/cache/fonts), else the
        system's interface font."""
        fams = {}
        for role, (path, family) in gamefiles.cached_fonts(self.layout).items():
            if winsys.register_font(path):
                fams[role] = family
        available = set(tkfont.families(self.root))
        fallback = next((f for f in ('Segoe UI', 'Source Sans 3', 'Noto Sans', 'DejaVu Sans', 'Helvetica')
                         if f in available), 'TkDefaultFont')
        # Distinct display and body faces keep dense settings readable while the headings retain their character.
        text = fallback
        title = fams.get('title') if fams.get('title') in available else next(
            (f for f in ('Georgia', 'Noto Serif', 'DejaVu Serif') if f in available), fallback)
        mono = next((f for f in ('Consolas', 'Cascadia Mono', 'DejaVu Sans Mono', 'Courier New') if f in available),
                    'TkFixedFont')
        self.game_fonts = bool(fams.get('title') and fams['title'] in available)
        self.ctx.log(f'fonts: {text}' + (' (the game\'s, from data/cache/fonts)' if self.game_fonts else
                                        ' (the game\'s Cetra Display is not decoded yet)'))

        self.fspec = {}

        def F(family, px, weight='normal'):
            font = tkfont.Font(root=self.root, family=family, size=-max(8, int(round(px * self.s))), weight=weight)
            self.fspec[id(font)] = (family, max(8, int(round(px * self.s))), weight)
            return font
        self.fcache = {}
        self.f = {'band': F(title, 25), 'big': F(text, 23), 'big_small': F(text, 21), 'label': F(text, 17),
                  'value': F(text, 16), 'desc': F(text, 17), 'small': F(text, 15), 'heading': F(title, 21),
                  'sub': F(title, 17), 'action': F(text, 18), 'status': F(text, 15), 'mono': F(mono, 11.5),
                  'badge': F(text, 15, 'bold'), 'action_big': F(text, 21), 'page': F(title, 36)}

    def fit_font(self, font, text, width, smallest=0.72):
        """font, or the same face a few pixels smaller so text fits width (down to smallest of its size)."""
        if font.measure(text) <= width:
            return font
        family, px, weight = self.fspec[id(font)]
        for size in range(px - 1, max(7, int(px * smallest)) - 1, -1):
            key = (family, size, weight)
            if key not in self.fcache:
                self.fcache[key] = tkfont.Font(root=self.root, family=family, size=-size, weight=weight)
            if self.fcache[key].measure(text) <= width:
                return self.fcache[key]
        return self.fcache[(family, max(7, int(px * smallest)), weight)]

    def fonts_from_game(self, game_dir='', dump=''):
        """Decode the game's fonts and its interface sounds in the background, redraw with the fonts and take up
        the sounds."""
        if self.args.selftest:
            return
        fonts, sounds = not self.game_fonts, not self.sounds.game
        if not fonts and not sounds:
            return

        def work():
            got = heard = {}
            try:
                if fonts:
                    # The cached files are registered (load_fonts) and were not usable, or this would not run:
                    # registered, Windows keeps them open, and decoding them again failed with "Permission denied"
                    # at every start, so a cache file that had gone bad was never replaced.
                    for path, _family in gamefiles.cached_fonts(self.layout).values():
                        winsys.unregister_font(path)
                    got = gamefiles.extract_fonts(self.layout, game_dir, dump)
            except Exception as e:
                self.ctx.log(f'fonts: {e}')
            try:
                if sounds:
                    heard = gamefiles.extract_sounds(self.layout, game_dir, dump)
                    self.ctx.log(f'sounds: {len(heard)} of {len(gamefiles.SOUNDS)} interface sounds from the game')
            except Exception as e:
                self.ctx.log(f'sounds: {e}')
            if got:
                self.root.after(0, self.reload_fonts)
            if heard:
                self.root.after(0, self.sounds.load_game)
        import threading
        threading.Thread(target=work, daemon=True).start()

    def reload_fonts(self):
        self.load_fonts()
        self.draw_static()
        self.render()

    # ---------------------------------------------------------------- pictures

    def sprite(self, key, make):
        if key not in self.sprites:
            self.sprites[key] = art.photo(self.root, make())
        return self.sprites[key]

    def animated(self):
        """Animations on (Customize Launcher); never while the selftest draws the screens, whose pictures must not
        depend on the moment they are taken."""
        return bool(self.settings.get('animations')) and not self.args.selftest

    def load_picture(self):
        """The player's background at the size of the window below the title band, when one is set and its kept copy
        is readable. A copy an older launcher kept for the whole window is made again from the picture when the file
        is still there, so no more of it is cut off than the new shape needs."""
        self.picture = None
        src = self.settings.get('background')
        if src:
            if picture.kept_shape(self.bg_store) not in (None, (picture.KEEP_H, picture.KEEP_W)) \
                    and Path(src).is_file():
                try:
                    picture.keep(src, self.bg_store, self.root)
                except (picture.PictureError, OSError, MemoryError, ValueError) as e:
                    self.ctx.log(f'background: {src}: {e!r}; the copy kept before stays')
            self.picture = picture.kept(self.bg_store, self.X(W), self.X(H) - self.X(PIC_TOP))
            if self.picture is None:
                self.ctx.log(f'background: {self.bg_store} is missing or unreadable; the colour scheme\'s backdrop')

    def see_through(self):
        """How solid the panel and the description box are: a little see-through over the player's own picture."""
        return 0.8 if self.picture is not None else 1.0

    def draw_static(self):
        c,s=self.canvas,self.s
        c.delete('static')
        dim=max(0,min(100,self.settings.get('background_dim',40)))/100
        c.create_image(0,0,anchor='nw',tags='static',image=self.sprite('forest-chassis',
                       lambda:art.forest_skin(self.X(W),self.X(H),self.picture,dim,os.name == 'nt')))
        self.make_sparks()
        # The emblem is part of the authored skin; the brand remains live, crisp text.
        tx=self.X(214)
        c.create_text(tx,self.X(116),text=NAME,anchor='w',font=self.f['band'],fill=P['text_bright'],tags='static')
        c.create_text(tx,self.X(145),text='Ocarina of Time',anchor='w',font=self.f['small'],fill=P['muted'],tags='static')
        self.halo=[]
        self.navi_xy=(self.X(140),self.X(132))
        c.tag_lower('static')
        self.draw_title_buttons()

    def draw_navigation(self):
        c,X=self.canvas,self.X
        self.nav_hits=[]
        selected={'home':'home','first_run':'home','job':'home','versions':'versions','saves':'saves',
                  'mods':'mods','mod_order':'mods','customize':'customize','color':'customize',
                  'settings':'settings','uninstall':'settings'}.get(self.screen,'home')
        links=(('home','Play'),('versions','Game versions'),('saves','Saves'),
               ('mods','Mods'),('customize','Appearance'),('settings','Settings'))
        for index,(target,label) in enumerate(links):
            x0,y0,x1,y1=X(96),X(202+index*44),X(332),X(242+index*44)
            if target==selected:
                pic=self.sprite(('nav-plate',x1-x0,y1-y0),lambda:art.forest_widget(x1-x0,y1-y0,0))
                c.create_image(x0,y0,anchor='nw',image=pic,tags='dyn')
            icon=self.sprite(('nav-icon',target,X(24)),lambda key=target:art.navigation_icon(key,X(24)))
            c.create_image(x0+X(13),(y0+y1)//2,anchor='w',image=icon,tags='dyn')
            self.text(x0+X(49),(y0+y1)//2,label,self.f['label'],P['text_bright'] if target==selected else P['muted'])
            if self.screen not in ('first_run','job'):
                self.nav_hits.append(((x0,y0,x1,y1),target))


    def make_sparks(self):
        """The dust over the backdrop: SPARKS soft dots of three sizes, each with its own speed, sway and twinkle, at
        places of their own from the first frame on. None while Animations is off."""
        self.sparks = []
        if not self.animated():
            return
        rnd = random.Random(7)
        for i in range(SPARKS):
            size = self.X((5, 8, 12)[i % 3])
            pics = [self.sprite(('spark', size, a), lambda sz=size, al=a: art.spark(sz, al)) for a in (0.9, 0.55, 0.25)]
            # logical pixels: its speed upwards a second, how far it sways to each side and how fast
            sp = {'x': rnd.uniform(0, W), 'y': rnd.uniform(BAND[2], H), 'vy': rnd.uniform(4, 14),
                  'sway': rnd.uniform(4, 16), 'rate': rnd.uniform(0.4, 1.1), 'phase': rnd.uniform(0, 6.28),
                  'pics': pics, 'shown': 0}
            sp['id'] = self.canvas.create_image(self.X(sp['x']), self.X(sp['y']), image=pics[0], tags=('static', 'spark'))
            self.sparks.append(sp)

    def apply_look(self):
        """Draw everything again after the colour scheme, the background or Animations changed."""
        art.set_theme(self.settings.get('theme'), self.custom_colors())
        self.sprites.clear()
        window_bg='#010203' if os.name == 'nt' else P['window']
        self.root.configure(background=window_bg)
        self.canvas.configure(background=window_bg)
        self.load_picture()
        self.draw_static()
        self.render()

    def draw_title_buttons(self):
        """Minimise and close at the band's right end, lit when the mouse is over them."""
        c, X = self.canvas, self.X
        c.delete('tb')
        top,bottom=map(X,TITLE_BUTTON_Y)
        for kind, x0, x1 in TITLE_BUTTONS:
            if self.tb_hover == kind:
                c.create_rectangle(X(x0), top, X(x1), bottom - 1, outline='', tags='tb',
                                   fill=P['close_hover'] if kind == 'close' else P['button_hover'])
            size = X(12)
            glyph = self.sprite(('glyph', kind, size), lambda kd=kind: art.title_glyph(kd, size, self.s))
            c.create_image((X(x0) + X(x1)) // 2, (top+bottom)//2, image=glyph, tags='tb')

    def title_button_at(self, x, y):
        if y < self.X(TITLE_BUTTON_Y[0]) or y >= self.X(TITLE_BUTTON_Y[1]):
            return None
        for kind, x0, x1 in TITLE_BUTTONS:
            if self.X(x0) <= x < self.X(x1):
                return kind
        return None

    # ---------------------------------------------------------------- input

    def bind_input(self):
        r = self.root
        for key, fn in (('<Up>', lambda e: self.move(-1)), ('<Down>', lambda e: self.move(1)),
                        ('<Left>', lambda e: self.side(-1)), ('<Right>', lambda e: self.side(1)),
                        ('<Return>', lambda e: self.activate()), ('<KP_Enter>', lambda e: self.activate()),
                        ('<space>', lambda e: self.activate()), ('<Escape>', lambda e: self.back()),
                        ('<F5>', lambda e: self.check_now(force=True))):
            r.bind(key, fn)
        self.canvas.bind('<Motion>', lambda e: self.canvas_event(self.on_motion,e))
        self.canvas.bind('<Button-1>', lambda e: self.canvas_event(self.on_click,e))
        self.canvas.bind('<B1-Motion>', lambda e: self.canvas_event(self.on_drag,e))
        self.canvas.bind('<ButtonRelease-1>', lambda e: self.canvas_event(self.on_release,e))
        self.canvas.bind('<Leave>', lambda e: self.set_tb_hover(None))
        r.bind('<Alt-F4>', lambda e: self.on_close())
        r.protocol('WM_DELETE_WINDOW', self.on_close)

    def canvas_event(self,handler,event):
        # Hit tests use design coordinates even though the visible window trims the skin's transparent padding.
        event.x=round(self.canvas.canvasx(event.x))
        event.y=round(self.canvas.canvasy(event.y))
        handler(event)

    def poll_gamepad(self):
        try:
            for b in self.gamepad.poll(winsys.in_front(self.root)):
                {'up': lambda: self.move(-1), 'down': lambda: self.move(1), 'left': lambda: self.side(-1),
                 'right': lambda: self.side(1), 'a': self.activate, 'b': self.back,
                 'y': lambda: self.check_now(force=True)}.get(b, lambda: None)()
        except Exception as e:
            self.ctx.log(f'gamepad: {e}')
            self.gamepad.dll = None
        self.root.after(50, self.poll_gamepad)

    def focusables(self):
        return [i for i, r in enumerate(self.rows) if r.focusable]

    def move(self, d):
        idx = self.focusables()
        if not idx:
            return
        pos = idx.index(self.focus) if self.focus in idx else 0
        self.focus = idx[max(0, min(len(idx) - 1, pos + d))]
        self.render()

    def focused(self):
        return self.rows[self.focus] if 0 <= self.focus < len(self.rows) else None

    def side(self, d):
        r = self.focused()
        if not r:
            return
        if r.kind == 'toggle':
            r.on(d < 0)                     # left = On, right = Off
        elif r.kind == 'enum':
            (r.left if d < 0 else r.right)()
        elif r.kind == 'pair':
            r.pick = max(0, min(len(r.actions) - 1, getattr(r, 'pick', 0) + d))
        else:
            return
        if r.kind != 'pair':   # a switch or a choice changed
            self.sounds.play('select')
        self.render()

    def activate(self):
        r = self.focused()
        if not r or not r.enabled:
            return
        if r.kind == 'toggle':
            self.sounds.play('select')
            r.on(not r.get())
        elif r.kind == 'enum':
            self.sounds.play('select')
            r.right()
        elif r.kind == 'pair':
            self.sounds.play('select')
            r.actions[getattr(r, 'pick', 0)][1]()
            return
        elif r.on:
            self.sounds.play('back' if r.kind == 'action' and r.label == 'Back' else 'select')
            r.on()
            return
        self.render()

    def home_row(self, label):
        """The index of the home screen's row with this label, for coming back to it."""
        return next((i for i, r in enumerate(self.rows_home()) if r.label == label), None)

    def back(self):
        if self.screen in ('settings', 'uninstall', 'saves', 'customize', 'job', 'color', 'mods', 'mod_order',
                           'versions'):
            self.sounds.play('back')
        if self.screen == 'color':
            self.pick_done()
        elif self.screen == 'settings':
            self.show('home', self.home_row('Settings'))
        elif self.screen == 'uninstall':
            self.show('settings')
        elif self.screen == 'saves':
            self.show('home', self.home_row('Saves'))
        elif self.screen == 'mods':
            self.show('home', self.home_row('Mods'))
        elif self.screen == 'versions':
            self.show('home', self.home_row('Game Version'))
        elif self.screen == 'mod_order':
            self.show('mods', self.mods_back_focus())
        elif self.screen == 'customize':
            self.show('home', self.home_row('Customize Launcher'))
        elif self.screen == 'job' and self.job and self.job.result is None:
            self.cancel_job()
        elif self.screen == 'job':
            self.leave_job()

    def hit(self, x, y):
        for i, r in enumerate(self.rows):
            if r.rect and r.focusable:
                x0, y0, x1, y1 = r.rect
                if x0 <= x <= x1 and y0 - self.X(self.row_gap / 2) <= y <= y1 + self.X(self.row_gap / 2):
                    return i
        return None

    def set_tb_hover(self, kind):
        if kind != self.tb_hover:
            self.tb_hover = kind
            self.draw_title_buttons()

    def on_drag(self, e):
        if self.pick_drag:
            self.picker_at(e.x, e.y, self.pick_drag)
            return
        if self.drag:   # moving the window without the system's help (Linux)
            dx, dy = self.drag
            self.root.geometry(f'+{e.x_root - dx}+{e.y_root - dy}')

    def on_motion(self, e):
        self.set_tb_hover(self.title_button_at(e.x, e.y))
        i = self.hit(e.x, e.y)
        if i is not None and i != self.focus:
            self.focus = i
            r = self.rows[i]
            if r.kind == 'pair':
                r.pick = 0 if e.x < (r.rect[0] + r.rect[2]) / 2 else 1
            self.render()
        elif i is not None and self.rows[i].kind == 'pair':
            r = self.rows[i]
            pick = 0 if e.x < (r.rect[0] + r.rect[2]) / 2 else 1
            if pick != getattr(r, 'pick', 0):
                r.pick = pick
                self.render()

    def on_click(self, e):
        try:
            self.root.focus_force()
        except tk.TclError:
            pass
        kind = self.title_button_at(e.x, e.y)
        if kind == 'close':
            self.on_close()
            return
        if kind == 'min':
            self.set_tb_hover(None)
            winsys.minimize(self.root)
            return
        for (x0,y0,x1,y1),target in getattr(self,'nav_hits',[]):
            if x0 <= e.x <= x1 and y0 <= e.y <= y1:
                self.sounds.play('select')
                self.show(target)
                return
        if e.y < self.X(BAND[2]):   # the band moves the window, like a title bar
            if not winsys.start_drag(self.root):
                self.drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())
            return
        if self.screen == 'color':
            part = self.picker_at(e.x, e.y)
            if part:
                self.pick_drag = part
                return
        i = self.hit(e.x, e.y)
        if i is None:
            return
        self.focus = i
        r = self.rows[i]
        if not r.enabled:
            self.render()
            return
        if r.kind == 'toggle' and r.value_rect:
            x0, _, x1, _ = r.value_rect
            self.sounds.play('select')
            r.on(e.x < (x0 + x1) / 2)
            self.render()
        elif r.kind == 'enum' and r.value_rect:
            x0, _, x1, _ = r.value_rect
            self.sounds.play('select')
            (r.left if e.x < x0 + (x1 - x0) / 3 else r.right)()
            self.render()
        elif r.kind == 'pair':
            r.pick = 0 if e.x < (r.rect[0] + r.rect[2]) / 2 else 1
            self.sounds.play('select')
            r.actions[r.pick][1]()
        else:
            self.activate()

    def on_release(self, e):
        self.drag = None
        if self.pick_drag:
            self.pick_drag = None
            self.pick_changed(final=True)

    def on_close(self):
        if self.job and self.job.result is None:
            self.job.cancel()
        if self.bg.staging():   # what it downloaded is resumed, and its unfinished folder replaced, next time
            self.bg.job.cancel()
        self.sounds.stop()
        tree = self.tree_window
        if tree is not None and tree.win.winfo_exists():
            # The Save Tree stays open: the launcher's window goes, and the process lives on for the tree alone until
            # it is closed (tree_only_wait). It lets go of the single-instance lock, so the launcher can be started
            # again meanwhile; this one runs no jobs and no longer watches the game.
            self.tree_only = True
            self.root.withdraw()
            if self.instance_lock:
                self.instance_lock.release()
            self.ctx.log('launcher closed; the Save Tree window stays open')
            self.root.after(500, self.tree_only_wait)
            return
        self.root.destroy()

    def tree_only_wait(self):
        tree = self.tree_window
        if tree is None or not tree.win.winfo_exists():
            self.root.destroy()
            return
        self.root.after(500, self.tree_only_wait)

    # ---------------------------------------------------------------- screens

    def start(self):
        a = self.args
        gd = self.settings.get('game_dir')
        if a.rolled_back:
            self.ctx.log(f'the stub went back from {a.rolled_back} to {self.layout.version}')
            self.settings['rolled_back'] = versions.display(a.rolled_back)   # never installed again by itself
            self.settings.save()
        if self.ctx.zstd_missing:   # told at once, not when Begin Installation stops at the build tools
            self.message = self.ctx.zstd_missing
        left = None if a.selftest else jobs.interrupted(self.ctx)   # before any job of this start leaves its own
        if left:
            self.ctx.log(f'the launcher was stopped during {left["task"]} (started '
                         f'{time.strftime("%Y-%m-%d %H:%M", time.localtime(left.get("started") or 0))}, game folder '
                         f'{left.get("game_dir") or "none"})')
        for old in self.layout.root.glob('python.old*'):   # the Python an update replaced (launcher/native/ootpc.c)
            shutil.rmtree(old, ignore_errors=True)
        if gd or self.settings.get('dump'):
            self.fonts_from_game(gd, self.settings.get('dump'))
        if a.uninstall:
            self.show('uninstall')
        elif a.continue_update is not None and not a.rolled_back:
            # after a rollback the stub passes the failed version's --continue-update on to this one: the game was
            # never rebuilt for the new version, so there is nothing to finish, and home tells why the version is back
            self.run_job(jobs.ContinueUpdateJob(self.ctx, gd if build.installed(gd) else '',
                                                a.continue_update, self.settings.get('dump')), 'home')
        elif build.installed(gd):
            # also a game whose last update stopped in the compile (build.installed): home, where Play starts the
            # build from before the update and Rebuild the Game finishes it, not the first-run screen and its dump
            self.show('home')
            self.after_update_notice()
            if left and not self.message:
                # make_game.py keeps the old exe until the new one is linked and records each finished step, so the
                # game folder is playable and a rebuild goes on from there; the player is told that it is due
                self.message = (f'OOT-PC was closed or stopped during {left["task"]}, which did not finish. Play '
                                'starts the last finished build; Rebuild the Game finishes what was left.')
                self.render()
            if self.install_staged('a staged update waited for the launcher to start'):
                return
            if a.play:   # Play from Steam with a mod's code to build in first (play_only)
                self.play()
                return
            if self.settings.get('auto_check'):
                self.check_now()
        else:
            if left and not self.message:
                self.message = (f'OOT-PC was closed or stopped during {left["task"]}, which did not finish. Begin '
                                'Installation goes on from the steps that were done.')
            self.show('first_run')
            self.find_dumps()

    def after_update_notice(self):
        if self.args.rolled_back:
            self.message = (f'Version {self.args.rolled_back} did not start, so OOT-PC went back to '
                            f'{versions.display(self.layout.version)}.')

    def show(self, screen, focus=None):
        if screen != self.screen and self.screen is not None and self.animated():
            # another screen slides into place: from the right when it lies deeper, from the left on the way back
            deeper = DEPTH.get(screen, 1) >= DEPTH.get(self.screen, 0)
            self.slide = float(self.X(44) if deeper else -self.X(44))
        self.close_hex()
        self.screen = screen
        self.rows = getattr(self, 'rows_' + screen)()
        idx = self.focusables()
        self.focus = focus if focus is not None and focus in idx else (idx[0] if idx else 0)
        if screen == 'job' and focus is None:
            self.focus = self.job_focus()
        self.render()

    # ---- first run

    def find_dumps(self):
        import threading
        extra = [self.args.source] if self.args.source else []
        extra.append(str(self.layout.root.parent))

        def work():
            try:
                found = gamefiles.find_dumps(self.layout, extra)
            except Exception as e:
                self.ctx.log(f'dump search: {e}')
                found = []
            self.root.after(0, lambda: self.dumps_found(found))
        self.dump_search = threading.Thread(target=work, daemon=True)
        self.dump_search.start()

    def dumps_found(self, found):
        self.dumps = found
        if found and not self.settings.get('dump'):
            self.settings['dump'] = str(found[0])
            self.settings.save()
            self.fonts_from_game('', str(found[0]))
        if self.screen == 'first_run':
            self.show('first_run', self.focus)

    def game_dir(self):
        return self.settings.get('game_dir') or str(self.layout.default_game_dir())

    def rows_first_run(self):
        dump = self.settings.get('dump')
        if dump:
            dump_value, dump_style, check = Path(dump).name, 'normal', True
            where = str(Path(dump).parent)
            dump_desc = (f'Your dump: {Path(dump).name}, in {where}. Select this row to choose another .nup file.',)
        elif self.dumps is None:
            dump_value, dump_style, check = 'Looking for your dump…', 'dim', False
            dump_desc = ('Looking for your dump of Ocarina of Time in Downloads, Desktop, Documents and the folder '
                         'the setup ran from.',)
        else:
            dump_value, dump_style, check = 'Choose your .nup file', 'salmon', False
            dump_desc = ('Choose your own dump of Ocarina of Time with the 1.1.1 update, as one .nup file. None was '
                         'found in Downloads, Desktop or Documents.',)
        gd = self.game_dir()
        need = 34
        free = free_gb(gd) + partial_image_gb(gd)   # Begin Installation removes the partial image (begin_install)
        folder_desc = (f'The game is built into this folder. {free:.0f} GB free on {drive_of(gd)}; the game needs '
                       f'about {need} GB. Select this row to choose another folder.',)
        ready = bool(dump)
        begin_desc = (f'Build Ocarina of Time on this PC from your own dump. It takes 20 to 45 minutes, once, and '
                      f'downloads 170 MB of build tools. The game needs about {need} GB on {drive_of(gd)} '
                      f'({free:.0f} GB free).',) if ready else \
            ('Choose your dump first: select Game Dump above.',)
        return [
            Row('value', 'Game Dump', dump_value, dump_style, big=True, check=check, on=self.choose_dump,
                desc=dump_desc),
            Row('value', 'Install Folder', gd, big=True, on=self.choose_folder, desc=folder_desc),
            Row('divider'),
            self.toggle('Desktop Shortcut', 'desktop_shortcut',
                        'Put an OOT-PC shortcut on the desktop. The Start menu always has one.'),
            self.toggle('Check for Updates at Start', 'auto_check',
                        'Look for a new version of OOT-PC when the launcher opens, and every hour while it stays '
                        'open. A new version downloads in the background and installs when you are not playing; '
                        'Settings can make it wait until you choose Updates.'),
            self.channel_row(),
            Row('divider'),
            Row('action', 'Begin Installation', on=self.begin_install, desc=begin_desc, enabled=True),
        ]

    def toggle(self, label, key, desc):
        def on(v):
            self.settings[key] = bool(v)
            self.settings.save()
            if key == 'desktop_shortcut':
                winsys.desktop_shortcut(self.layout, bool(v))
        return Row('toggle', label, on=on, get=lambda: bool(self.settings.get(key)), desc=(desc,))

    def channel_row(self):
        def flip():
            self.settings['channel'] = 'preview' if self.settings.get('channel') == 'stable' else 'stable'
            self.settings.save()
            self.release, self.checked_at = None, 0
        return Row('enum', 'Update Channel', options=('Stable', 'Preview'),
                   get=lambda: 'Preview' if self.settings.get('channel') == 'preview' else 'Stable',
                   left=flip, right=flip,
                   desc=('Stable gets each finished release. Preview also gets test builds before they are '
                         'released, which may have new problems.',))

    def discord_row(self, game_dir):
        """The game's discord_rpc setting (runtime/discord_rpc.c), kept in the game's OOT-PC.ini as the game's own
        menu keeps it; the game reads it when it starts. On unless the file says 0, as the game reads it (cfg_bool,
        default on)."""
        def on(v):
            # Not while the game runs, as the mods' switches (switch_mod): the game writes the same file when a menu
            # setting changes. Each side reads the file, changes its own lines and puts a new file in place, so of
            # two changes in the same moment the second drops the first. The game reads this setting only when it
            # starts, and its own menu switches it meanwhile.
            if self.game_busy(game_dir):
                self.message = ('Quit the game first, or switch Discord Status in the game\'s menu (Game tab): the '
                                'game writes its settings file while it runs.')
                return
            try:
                gameini.put(game_dir, 'discord', 'discord_rpc', '1' if v else '0')
                self.message = ''
            except OSError as e:
                self.message = f'The game\'s settings could not be written: {e}'

        def get():
            try:
                return gameini.get(game_dir, 'discord_rpc') != '0'
            except OSError:   # OOT-PC.ini there but unreadable: the game's default
                return True
        return Row('toggle', 'Discord Status', on=on, get=get,
                   desc=('Show "Playing The Legend of Zelda: Ocarina of Time PC" with your play time in your Discord status while the game runs. '
                         'The game\'s menu switches it too (Game tab).',))

    def choose_dump(self):
        start = str(Path(self.settings.get('dump')).parent) if self.settings.get('dump') else str(Path.home())
        path = filedialog.askopenfilename(parent=self.root, title='Choose your Ocarina of Time dump',
                                          initialdir=start, filetypes=[('Game dump', '*.nup'), ('All files', '*.*')])
        if not path:
            return
        if not gamefiles.is_game_nup(self.layout, path):
            self.message = f'{Path(path).name} is not a dump of Ocarina of Time.'
            self.render()
            return
        self.settings['dump'] = path
        self.settings.save()
        self.message = ''
        self.fonts_from_game('', path)
        self.show(self.screen, self.focus)

    def choose_folder(self):
        path = filedialog.askdirectory(parent=self.root, title='Choose the folder for the game',
                                       initialdir=str(Path(self.game_dir()).parent))
        if not path:
            return
        p = Path(path)
        if p.exists() and any(p.iterdir()) and not (p / 'build-cache').is_dir() and p.name.lower() not in ('oot-pc', 'oot-debug-menu'):
            p = p / 'OOT-PC'
        self.settings['game_dir'] = str(p)
        self.settings.save()
        self.show(self.screen, self.focus)

    def begin_install(self):
        dump = self.settings.get('dump')
        if not dump:
            self.focus = 0
            self.choose_dump()
            return
        gd = self.game_dir()
        too_long = long_path_problem(gd)   # told here, not as a missing file halfway through the build
        if too_long:
            self.message = App.friendly(too_long)
            self.render()
            return
        drop_partial_image(gd)   # before the free space is measured
        if free_gb(gd) < 34 and not (Path(gd) / 'romfs' / 'romfs.bin').is_file():
            self.message = f'{drive_of(gd)} has {free_gb(gd):.0f} GB free; the game needs about 34 GB.'
            self.render()
            return
        self.settings['game_dir'] = gd
        self.settings.save()
        if self.settings.get('desktop_shortcut'):
            winsys.desktop_shortcut(self.layout, True)
        self.run_job(jobs.InstallJob(self.ctx, dump, gd), 'home', back='first_run')

    # ---- home

    def rows_home(self):
        gd = self.settings.get('game_dir')
        built = build.built_version(gd)
        mine = versions.display(self.layout.version)
        play_value = 'Ocarina of Time'
        staged = self.bg.ready(self.release)
        if staged:
            upd = Row('value', 'Updates', f'Ready · {staged["version"]}', 'orange_arrow', big=True, icon='refresh',
                      badge=True, on=self.start_update, focus_value=True, desc=lambda: self.staged_desc(staged))
        elif self.bg.staging():
            upd = Row('value', 'Updates', f'Downloading {self.bg.job.ver}', 'orange_arrow', big=True,
                      icon='refresh', on=self.start_update, focus_value=True, desc=self.staging_desc)
        elif self.release and jobs.update_available(self.ctx, self.release):
            nv = versions.display(self.release.version)
            upd = Row('value', 'Updates', f'Version {nv}', 'orange_arrow', big=True, icon='refresh', badge=True,
                      on=self.start_update, focus_value=True, desc=self.update_desc)
        elif self.check and self.check.result is None:
            upd = Row('value', 'Updates', 'Checking…', 'dim', big=True, icon='refresh', on=lambda: None,
                      desc=('Looking for a new version of OOT-PC.',))
        elif self.check_error:
            upd = Row('value', 'Updates', 'Could not check', 'dim', big=True, icon='refresh',
                      on=lambda: self.check_now(force=True),
                      desc=(f'The update check failed: {self.check_error}', 'Select this row to try again.'))
        elif self.checked_at:
            upd = Row('value', 'Updates', f'Up to Date · {mine}', 'normal', big=True, icon='refresh', check=True,
                      on=lambda: self.check_now(force=True),
                      desc=(f'{mine} is the newest version. Checked {self.ago(self.checked_at)}.',
                            'Select this row to check again.'))
        else:
            upd = Row('value', 'Updates', f'Version {mine}', 'normal', big=True, icon='refresh',
                      on=lambda: self.check_now(force=True),
                      desc=(f'You have OOT-PC {mine}. Select this row to look for a new version.',))
        # an update that stopped in the compile left no finished build on record, but the exe of the build before it
        unfinished = not built and build.installed(gd)
        rebuild_value = (f'Built with {versions.display(built)}' if built else
                         'Not Finished' if unfinished else 'Not built')
        rebuild_style = 'normal' if versions.display(built) == mine else 'salmon'
        play_desc = ('Start Ocarina of Time. The launcher steps aside while you play and comes back when you quit.',)
        if built and versions.display(built) != mine:
            play_desc += (f'The game was built with {versions.display(built)}. Rebuild the Game brings it to {mine}.',)
        elif unfinished:
            play_desc += (f'The last rebuild of the game did not finish, so Play starts the build from before it. '
                          f'Rebuild the Game finishes it and brings the game to {mine}; your dump is not needed.',)
        if staged and self.settings.get('auto_install'):
            play_desc += (f'OOT-PC {staged["version"]} is downloaded and installs after you quit the game.',)
        if self.message:
            play_desc = (self.message,) + play_desc
        return [
            Row('value', 'Play', play_value, big=True, icon='play', on=self.play, desc=play_desc),
            upd,
            Row('divider'),
            self.version_row(),
            Row('value', 'Game Folder', gd, on=lambda: self.open(gd),
                desc=('Open the game folder. The game keeps your save in its profile folder, its settings in '
                      'OOT-PC.ini and its logs in logs. Its saves folder holds backups and the save tree, and saves '
                      'to import go into saves\\import.',)),
            self.saves_row(gd),
            Row('value', 'Save Tree', 'Open', 'arrow', on=self.open_save_tree,
                desc=('A window of its own beside the game: every time a flag of the game changes while you play, '
                      'the game saves, and each save grows the tree, labelled with the flags that changed. Every '
                      'path you ever played stays in it.',
                      'Select a save in the tree to load it at once or export it as a save file for anyone. '
                      'Search finds a save by its flags or its place; a section folds into one bud.')),
            self.mods_row(gd),
            Row('value', 'Rebuild the Game', rebuild_value, rebuild_style, on=self.start_rebuild,
                desc=('Build the game again from the files of the last build. Only what changed is compiled. Use it '
                      'after changing build tools, or if the game stopped starting.',)),
            Row('value', 'Customize Launcher', art.THEMES[art.theme]['label'], 'arrow',
                on=lambda: self.show('customize'),
                desc=('Colour scheme, background picture, animations and sounds.',)),
            Row('value', 'Settings', '', 'arrow', on=lambda: self.show('settings'),
                desc=('Updates, the game folder, the logs and uninstalling.',)),
        ]

    # ---- game versions (launcher/installs.py): each in its own folder, with its own saves

    def version_row(self):
        listed = installs.entries(self.settings)
        sel = installs.selected_version(self.settings)
        others = [e['version'] for e in listed if e['version'] != sel]
        desc = (f'You are playing Ocarina of Time {sel}. Select this row to switch to another version of the game, '
                'or to install one more from your dump.',
                'Each version is a game of its own in its own folder: its own saves, settings, mods and save tree.')
        if others:
            desc += (f'Also installed: {", ".join(others)}.',)
        return Row('value', 'Game Version', sel, 'arrow', on=self.open_versions, desc=desc)

    def open_versions(self):
        dump = self.settings.get('dump')
        if dump != self.dump_versions_of:
            self.dump_versions, self.dump_versions_of = None, dump
            self.find_dump_versions(dump)
        self.show('versions')

    def find_dump_versions(self, dump):
        """Reads the game versions the dump holds on a thread (make_game.py --versions reads each main's head)."""
        import threading

        def work():
            try:
                found = build.dump_versions(self.layout, dump) if dump else []
            except Exception as e:   # noqa: BLE001 - the list stays empty, the screen says so
                self.ctx.log(f'dump versions: {e}')
                found = []
            self.root.after(0, lambda: self.dump_versions_found(dump, found))
        threading.Thread(target=work, daemon=True).start()

    def dump_versions_found(self, dump, found):
        if dump != self.dump_versions_of:
            return
        self.dump_versions = found
        if self.screen == 'versions':
            self.show('versions', self.focus)

    def rows_versions(self):
        listed = installs.entries(self.settings)
        sel = installs.selected(self.settings)
        rows = []
        for e in listed:
            v, d = e['version'], e['dir']
            here = bool(sel) and sel['version'] == v
            if not Path(d).is_dir():
                value, style = 'Folder Missing', 'salmon'
            elif here:
                value, style = 'Playing', 'normal'
            else:
                value, style = 'Switch', 'arrow'
            built = build.built_version(d)
            desc = (f'Ocarina of Time {v}, in {d}.',
                    'Its saves, settings and mods are its own: switching versions never touches the other one\'s.')
            if here:
                desc += ('This is the version Play, Saves, Mods and the Save Tree act on now.',)
            else:
                desc += ('Select this row to play this version instead.',)
            if built and versions.display(built) != versions.display(self.layout.version):
                desc += (f'It was built with {versions.display(built)}: after switching, Rebuild the Game brings it '
                         f'to {versions.display(self.layout.version)}.',)
            rows.append(Row('value', f'Ocarina of Time {v}', value, style, check=here,
                            on=(lambda v=v: self.switch_version(v)), desc=desc))
        rows.append(Row('divider'))
        have = {e['version'] for e in listed}
        dump = self.settings.get('dump')
        if not dump or not Path(dump).is_file():
            # a dump moved or renamed since the first install is chosen again here, not read as holding nothing
            rows.append(Row('value', 'Install Another Version', 'Choose a Dump', 'arrow', on=self.choose_dump,
                            desc=('Choose your .nup dump first: the versions it holds can then be installed.',)
                            if not dump else
                            (f'{Path(dump).name} is no longer where it was. Choose your .nup dump again: the '
                             'versions it holds can then be installed.',)))
        elif dump != self.dump_versions_of:   # a dump chosen on this screen: read its versions
            self.dump_versions, self.dump_versions_of = None, dump
            self.find_dump_versions(dump)
            rows.append(Row('value', 'Install Another Version', 'Looking…', 'dim', on=lambda: None,
                            desc=(f'Reading which versions of the game {Path(dump).name} holds.',)))
        elif self.dump_versions is None:
            rows.append(Row('value', 'Install Another Version', 'Looking…', 'dim', on=lambda: None,
                            desc=(f'Reading which versions of the game {Path(dump).name} holds.',)))
        else:
            more = [(v, ok) for v, ok in self.dump_versions if v not in have]
            for v, ok in more:
                where = installs.default_dir(self.version_base_dir(), v)
                if ok:
                    rows.append(Row('value', f'Install {v}', 'Install', 'orange_arrow',
                                    on=(lambda v=v: self.install_version(v)),
                                    desc=(f'Build Ocarina of Time {v} from your dump into {where}, beside the game '
                                          'you have. It gets its own saves; your other version stays as it is.',
                                          'It takes as long and as much space (about 34 GB) as the first install.')))
                else:
                    rows.append(Row('value', f'Install {v}', 'Not Supported Yet', 'dim', on=lambda: None,
                                    desc=(f'Your dump holds Ocarina of Time {v}, but OOT-PC cannot build it '
                                          'yet: the port to this version is still being made.',)))
            if not more:
                found = ', '.join(v for v, _ in self.dump_versions) or 'no version this launcher knows'
                rows.append(Row('value', 'Install Another Version', 'All Installed' if self.dump_versions else
                                'None Found', 'dim', on=lambda: None,
                                desc=(f'{Path(dump).name} holds {found}; each one it holds is installed.'
                                      if self.dump_versions else
                                      f'{Path(dump).name} holds no other version of the game that can be built.',)))
        rows.append(Row('action', 'Back', on=lambda: self.show('home', self.home_row('Game Version')), height=40,
                        desc=('Back to the main menu.',)))
        return rows

    def record_install(self, j):
        """A finished install becomes its version's entry and the selected game: the version make_game.py's check
        found, else the one asked for, else 1.1.1 (the only version a build before versions made)."""
        gd = getattr(j, 'game_dir', None)
        if not isinstance(gd, str) or not gd:
            return
        version = next((v for v in (getattr(j, 'game_version', None), getattr(j, 'version', None))
                        if isinstance(v, str) and v), installs.DEFAULT_VERSION)
        installs.add(self.settings, version, gd)

    def version_base_dir(self):
        """The folder a new version's folder is named after: the 1.1.1 install's, else the selected one's."""
        e = installs.find(self.settings, installs.DEFAULT_VERSION)
        return e['dir'] if e else self.game_dir()

    def switch_version(self, version):
        sel = installs.selected(self.settings)
        if sel and sel['version'] == version:
            self.show('home', self.home_row('Play'))
            return
        gd = self.settings.get('game_dir')
        if self.job and self.job.result is None:
            self.message = 'Wait until the current job has finished, then switch.'
        elif self.game_proc is not None and self.game_proc.poll() is None or (gd and build.game_running(gd)):
            self.message = 'Quit the game first: then switch to another version.'
        elif self.tree_window is not None:
            self.message = 'Close the Save Tree window first: it shows the saves of the version you play now.'
        elif not installs.select(self.settings, version):
            self.message = f'Ocarina of Time {version} is not installed.'
        else:
            self.message = f'Switched to Ocarina of Time {version}: its own saves, settings and mods.'
            self.show('home', self.home_row('Play'))
            return
        self.show('versions', self.focus)

    def install_version(self, version):
        dump = self.settings.get('dump')
        if self.job and self.job.result is None:
            self.message = 'Wait until the current job has finished.'
            self.show('versions', self.focus)
            return
        gd = installs.default_dir(self.version_base_dir(), version)
        too_long = long_path_problem(gd)
        if too_long:
            self.message = App.friendly(too_long)
            self.show('versions', self.focus)
            return
        drop_partial_image(gd)
        if free_gb(gd) < 34 and not (Path(gd) / 'romfs' / 'romfs.bin').is_file():
            self.message = f'{drive_of(gd)} has {free_gb(gd):.0f} GB free; another version needs about 34 GB.'
            self.show('versions', self.focus)
            return
        self.run_job(jobs.InstallJob(self.ctx, dump, gd, version), 'home', back='versions')

    # ---- customize

    def rows_customize(self):
        names = list(art.THEMES)
        custom = bool(self.settings.get('background'))

        def scheme(d):
            self.settings['theme'] = names[(names.index(art.theme) + d) % len(names)]
            self.settings.save()
            # the rows again: Background Color and Button Color are there only while the scheme is Custom; the
            # cursor stays on Color Scheme, the first row
            art.set_theme(self.settings['theme'], self.custom_colors())
            self.rows = self.rows_customize()
            self.apply_look()

        def dim(d):
            now = self.settings.get('background_dim', 40)
            at = min(range(len(DIMS)), key=lambda i: abs(DIMS[i] - now))
            self.settings['background_dim'] = DIMS[max(0, min(len(DIMS) - 1, at + d))]
            self.settings.save()
            self.apply_look()

        def animations(v):
            self.settings['animations'] = bool(v)
            self.settings.save()
            self.cur_xy = None
            self.slide = 0.0
            self.apply_look()

        def sounds(d):
            keys = [k for k, _ in SOUND_LEVELS]
            now = self.settings.get('sounds')
            at = keys.index(now if now in keys else sound.DEFAULT_LEVEL)
            self.settings['sounds'] = keys[max(0, min(len(keys) - 1, at + d))]
            self.settings.save()
            self.sounds.set_level(self.settings['sounds'])
            self.sounds.play('select')   # the new loudness, heard at once

        bg_desc = ('Use a picture of your own as the background.',)
        if self.look_message:
            bg_desc = (self.look_message,) + bg_desc
        rows = [
            Row('enum', 'Color Scheme', get=lambda: art.THEMES[art.theme]['label'],
                left=lambda: scheme(-1), right=lambda: scheme(1),
                desc=('The launcher\'s colours. Custom lets you pick your own.',)),
        ]
        if art.theme == 'custom':
            rows += [Row('value', CUSTOM_LABELS[slot], art.CUSTOM[slot], 'swatch',
                         on=lambda sl=slot: self.open_picker(sl), desc=(CUSTOM_DESC[slot],))
                     for slot in art.CUSTOM_SLOTS]
        rows += [
            Row('value', 'Background', Path(self.settings['background']).name if custom else 'Choose a Picture',
                'arrow', on=self.choose_background, desc=bg_desc),
        ]
        if custom:
            rows += [
                Row('enum', 'Background Dimming', get=lambda: f'{self.settings.get("background_dim", 40)} %',
                    left=lambda: dim(-1), right=lambda: dim(1),
                    desc=('Darkens your picture so the text stays easy to read.',)),
                Row('value', 'Remove Background', '', 'arrow', on=self.clear_background,
                    desc=('Go back to the colour scheme\'s own background.',)),
            ]
        rows += [
            Row('divider'),
            Row('toggle', 'Animations', on=animations, get=lambda: bool(self.settings.get('animations')),
                desc=('A gliding cursor, sliding screens and drifting sparks.',)),
            Row('enum', 'Sounds', get=lambda: dict(SOUND_LEVELS).get(self.settings.get('sounds'),
                                                                     dict(SOUND_LEVELS)[sound.DEFAULT_LEVEL]),
                left=lambda: sounds(-1), right=lambda: sounds(1),
                desc=('The game\'s select and back sounds. Low by default.',)),
            Row('divider'),
        ]
        if os.name == 'nt':
            rows += [self.steam_row(), Row('divider')]
        rows += [
            Row('action', 'Back', on=lambda: self.show('home', self.home_row('Customize Launcher')), height=40,
                desc=('Back to the main menu.',)),
        ]
        return rows

    # ---- the Custom scheme's colour picker

    def custom_colors(self):
        return {slot: self.settings.get('custom_' + slot, '') for slot in art.CUSTOM_SLOTS}

    def open_picker(self, slot):
        h = art.CUSTOM.get(slot) or art.CUSTOM_DEFAULT[slot]
        hh, ss, vv = colorsys.rgb_to_hsv(*(int(h[i:i + 2], 16) / 255.0 for i in (1, 3, 5)))
        self.pick = {'slot': slot, 'h': hh, 's': ss, 'v': vv}
        self.show('color', 0)

    def pick_hex(self):
        p = self.pick
        return art.hexcode(*colorsys.hsv_to_rgb(p['h'], p['s'], p['v']))

    def pick_step(self, key, d):
        """Hue by 5 degrees along the hue bar, saturation and brightness by 5 % (the arrows and the gamepad)."""
        p = self.pick
        if key == 'h':
            p['h'] = (round(p['h'] * 72) + d) % 72 / 72.0
        else:
            p[key] = max(0.0, min(1.0, (round(p[key] * 20) + d) / 20.0))
        self.pick_changed(final=True)

    def pick_changed(self, final):
        """The picked colour into the settings and onto the screen: the picker at once, the launcher's colours a
        moment later (look_soon), so dragging over the square stays smooth. final: saved to launcher.json."""
        if not self.pick:
            return
        self.settings['custom_' + self.pick['slot']] = self.pick_hex()
        if final:
            self.settings.save()
        if self.screen == 'color':
            self.rows = self.rows_color()
            self.render()
        self.look_soon(0 if final else 120)

    def look_soon(self, ms):
        if self.look_after:
            self.root.after_cancel(self.look_after)
        self.look_after = self.root.after(ms, self.look_now)

    def look_now(self):
        self.look_after = None
        self.apply_look()

    def pick_reset(self):
        slot = self.pick['slot']
        self.settings['custom_' + slot] = ''
        self.settings.save()
        art.CUSTOM[slot] = art.CUSTOM_DEFAULT[slot]
        self.open_picker(slot)
        self.focus = next(i for i, r in enumerate(self.rows) if r.label == 'Reset')
        self.apply_look()

    def pick_done(self):
        slot = self.pick['slot'] if self.pick else 'background'
        if self.look_after:
            self.root.after_cancel(self.look_after)
            self.look_after = None
            self.apply_look()
        rows = self.rows_customize()
        self.show('customize', next((i for i, r in enumerate(rows) if r.label == CUSTOM_LABELS[slot]), 0))

    def rows_color(self):
        p = self.pick
        return [
            Row('enum', 'Hue', get=lambda: f'{round(p["h"] * 360) % 360}°',
                left=lambda: self.pick_step('h', -1), right=lambda: self.pick_step('h', 1)),
            Row('enum', 'Saturation', get=lambda: f'{round(p["s"] * 100)} %',
                left=lambda: self.pick_step('s', -1), right=lambda: self.pick_step('s', 1)),
            Row('enum', 'Brightness', get=lambda: f'{round(p["v"] * 100)} %',
                left=lambda: self.pick_step('v', -1), right=lambda: self.pick_step('v', 1)),
            Row('value', 'Hex Code', self.pick_hex().upper(), 'arrow', on=self.type_hex),
            Row('divider'),
            Row('value', 'Reset', '', 'arrow', on=self.pick_reset),
            Row('divider'),
            Row('action', 'Done', on=self.pick_done, height=40),
        ]

    def picker_at(self, x, y, part=None):
        """The colour under the mouse at (x, y) on the square or the bar into the picker; which of the two it was
        (part: the one being dragged, which takes the mouse also outside it), or None."""
        X, p = self.X, self.pick

        def inside(box, m=0):
            x0, y0, x1, y1 = box
            return X(x0) - X(m) <= x <= X(x1) + X(m) and X(y0) - X(m) <= y <= X(y1) + X(m)

        def frac(v, a, b):
            return max(0.0, min(1.0, (v - X(a)) / max(1, X(b) - X(a))))
        sx0, sy0, sx1, sy1 = SQUARE
        bx0, _, bx1, _ = BAR
        if part == 'square' or (part is None and inside(SQUARE, 4)):
            p['s'] = frac(x, sx0, sx1)
            p['v'] = 1 - frac(y, sy0, sy1)
            part = 'square'
        elif part == 'bar' or (part is None and inside(BAR, 8)):
            p['h'] = min(frac(x, bx0, bx1), 0.9999)
            part = 'bar'
        else:
            return None
        self.pick_changed(final=False)
        return part

    def type_hex(self):
        """A box over the Hex Code row to type a colour into, like #3A7BD5; Enter takes it, Esc leaves it."""
        r = self.focused()
        if self.hex_entry or not r or not r.value_rect:
            return
        x0, y0, x1, y1 = r.value_rect
        X = self.X
        e = tk.Entry(self.canvas, font=self.f['label'], bg=P['value'], fg=P['value_text'],
                     insertbackground=P['value_text'], selectbackground=P['orange'], selectforeground=P['on_text'],
                     relief='flat', justify='center', highlightthickness=1, highlightcolor=P['orange'],
                     highlightbackground=P['value_border'])
        e.insert(0, self.pick_hex().upper())
        e.select_range(0, 'end')
        self.hex_entry = e
        self.canvas.create_window(x0 + X(3), y0 + X(3), anchor='nw', window=e, width=x1 - x0 - X(6),
                                  height=y1 - y0 - X(6), tags='hexentry')
        e.focus_set()

        def take(ev):
            self.hex_typed(e.get())
            return 'break'

        def leave(ev):
            self.close_hex()
            return 'break'
        for key in ('<Return>', '<KP_Enter>'):
            e.bind(key, take)
        e.bind('<Escape>', leave)
        for key in ('<Up>', '<Down>', '<space>'):
            e.bind(key, lambda ev: 'break')
        e.bind('<FocusOut>', lambda ev: self.close_hex())

    @staticmethod
    def parse_hex(text):
        """(r, g, b) in 0..255 of a typed hex code (#3A7BD5, 3A7BD5 or #37D), or None."""
        t = text.strip().lstrip('#').lower()
        if len(t) == 3:
            t = ''.join(ch * 2 for ch in t)
        return tuple(int(t[i:i + 2], 16) for i in (0, 2, 4)) if art.HEX.match('#' + t) else None

    def hex_typed(self, text):
        got = self.parse_hex(text)
        if got is None:
            self.sounds.play('back')
            self.hex_entry.select_range(0, 'end')
            return
        self.sounds.play('select')
        hh, ss, vv = colorsys.rgb_to_hsv(*(v / 255.0 for v in got))
        self.pick.update(h=hh, s=ss, v=vv)
        self.close_hex()
        self.pick_changed(final=True)

    def close_hex(self):
        if self.hex_entry:
            e, self.hex_entry = self.hex_entry, None
            self.canvas.delete('hexentry')
            e.destroy()
            try:
                self.root.focus_force()
            except tk.TclError:
                pass

    def draw_picker(self):
        """The colour picker in the description box's place: the slot's name, the colour square of the picked hue
        with a ring on the picked colour, the hue bar, and the colour itself with its hex code."""
        c, X, s, p = self.canvas, self.X, self.s, self.pick
        bx, by, bw, bh = PICKER_BOX
        key = ('desc', X(bw), X(bh) // 4)
        if key not in self.sprites:
            img, pad = art.desc_box(X(bw), X(bh), s, self.see_through())
            self.sprites[key] = (art.photo(self.root, img), pad)
        pic, pad = self.sprites[key]
        c.create_image(X(bx) - pad, X(by) - pad, anchor='nw', image=pic, tags='dyn')
        self.text(X(bx + 26), X(by + 34), CUSTOM_LABELS[p['slot']], self.f['heading'], P['text_bright'])
        sx0, sy0, sx1, sy1 = SQUARE
        sw, sh = X(sx1) - X(sx0), X(sy1) - X(sy0)
        hue = round(p['h'], 3)
        square = self.sprite(('square', sw, sh, hue), lambda: art.color_square(sw, sh, hue))
        c.create_rectangle(X(sx0) - 1, X(sy0) - 1, X(sx0) + sw, X(sy0) + sh, outline=P['value_border'], tags='dyn')
        c.create_image(X(sx0), X(sy0), anchor='nw', image=square, tags='dyn')
        size = X(18)
        mark = self.sprite(('mark', size), lambda: art.picker_mark(size, s))
        c.create_image(X(sx0) + round(p['s'] * sw), X(sy0) + round((1 - p['v']) * sh), image=mark, tags='dyn')
        bx0, by0, bx1, by1 = BAR
        bw_, bh_ = X(bx1) - X(bx0), X(by1) - X(by0)
        bar = self.sprite(('hues', bw_, bh_), lambda: art.hue_bar(bw_, bh_))
        c.create_rectangle(X(bx0) - 1, X(by0) - 1, X(bx0) + bw_, X(by0) + bh_, outline=P['value_border'], tags='dyn')
        c.create_image(X(bx0), X(by0), anchor='nw', image=bar, tags='dyn')
        c.create_image(X(bx0) + round(p['h'] * bw_), X(by0) + bh_ // 2, image=mark, tags='dyn')
        hexc = self.pick_hex()
        c.create_rectangle(X(bx0), X(452), X(bx0 + 64), X(500), fill=hexc, outline=P['value_border'], tags='dyn')
        self.text(X(bx0 + 80), X(476), hexc.upper(), self.f['label'], P['text_bright'])
        self.text(X(bx + 26), X(526), 'Click or drag the square and the bar.', self.f['small'], P['muted'])

    def steam_row(self):
        """Add to Steam: the game in the Steam library, started from there without the launcher (launcher/steam.py)."""
        found = steam.find_steam()
        desc = ('Adds the game to your Steam library with its own icon and artwork, so you can start it from Steam.',
                'Steam restarts while it is added.')
        if self.steam_message:
            desc = (self.steam_message,) + desc
        if self.steam_busy:
            value, style = 'Adding…', 'dim'
        elif not found:
            value, style = 'Steam not found', 'dim'
        else:
            value, style = ('Added' if steam.added(found[0]) else ''), 'arrow'
        return Row('value', 'Add to Steam', value, style, on=self.add_to_steam, desc=desc)

    def add_to_steam(self):
        import threading
        if self.steam_busy:
            return
        gd = self.settings.get('game_dir')
        if not steam.find_steam():
            self.steam_message = 'Steam is not installed on this PC.'
            self.sounds.play('back')
            self.show('customize', self.focus)
            return
        if not gd or not (Path(gd) / 'ocarina.exe').is_file():
            self.steam_message = 'The game is not built yet.'
            self.sounds.play('back')
            self.show('customize', self.focus)
            return
        self.steam_busy = True
        self.steam_message = 'Adding to Steam…'
        self.sounds.play('select')
        self.show('customize', self.focus)
        dump = self.settings.get('dump')

        def work():
            try:
                text, ok = steam.add(self.layout, gd, dump, self.ctx.log), True
            except steam.SteamError as e:
                text, ok = str(e), False
            except Exception as e:   # a Steam folder in a state nobody expected: say so instead of dying
                import traceback
                self.ctx.log('steam: ' + traceback.format_exc())
                text, ok = f'Adding to Steam failed: {e}', False
            self.ctx.log(f'steam: {text}')
            self.root.after(0, lambda: self.steam_done(text, ok))
        threading.Thread(target=work, daemon=True).start()

    def steam_done(self, text, ok):
        self.steam_busy = False
        self.steam_message = text
        if not ok:
            self.sounds.play('back')
        if self.screen == 'customize':
            self.show('customize', self.focus)

    def choose_background(self):
        start = self.settings.get('background')
        start = str(Path(start).parent) if start and Path(start).parent.is_dir() else \
            str(winsys.known_folder('pictures') or Path.home())
        pattern = ' '.join('*' + t for t in picture.TYPES)
        path = filedialog.askopenfilename(parent=self.root, title='Choose a background picture', initialdir=start,
                                          filetypes=[('Pictures', pattern), ('All files', '*.*')])
        if not path:
            return
        try:
            picture.keep(path, self.bg_store, self.root)
        except (picture.PictureError, OSError, MemoryError, ValueError) as e:
            self.ctx.log(f'background: {path}: {e!r}')
            self.look_message = str(e) if isinstance(e, picture.PictureError) else \
                f'{Path(path).name} could not be used as the background: {e}'
            self.sounds.play('back')
            self.show('customize', self.focus)
            return
        self.look_message = ''
        self.settings['background'] = str(Path(path))
        self.settings.save()
        self.apply_look()
        self.show('customize', self.focus)

    def clear_background(self):
        self.settings['background'] = ''
        self.settings.save()
        try:
            self.bg_store.unlink(missing_ok=True)
        except OSError as e:
            self.ctx.log(f'background: {e}')
        self.look_message = ''
        self.apply_look()
        self.show('customize', 1)

    def save_waiting(self, gd):
        """A save put into the game folder's saves\\import folder (a shared zip, a save folder), waiting to be
        imported."""
        if gd and Path(gd).is_dir():
            try:
                saves.ensure_saves_dir(gd)
                return saves.waiting(gd)[0] is not None
            except OSError:
                pass
        return False

    def open_save_tree(self):
        """The Save Tree window: it stays open while the game runs and the launcher's own window is hidden."""
        if self.tree_window is not None:
            self.tree_window.lift()
            return
        if not self.settings.get('game_dir'):
            self.message = 'Choose the game folder first.'
            self.render()
            return
        from .savetree import SaveTreeWindow
        self.tree_window = SaveTreeWindow(self)
        self.sounds.play('select')

    def saves_row(self, gd):
        """Saves: the screen that exports and imports saves; orange while a save waits in the import folder."""
        ready = not IMPORT_DISABLED and self.save_waiting(gd)
        desc = ('Export your save into one zip, to attach to a bug report or give to another player, or import a '
                'save someone shared, a Switch save backup or an emulator\'s save.',)
        if ready:
            desc += ('A save waits in the import folder (saves\\import in the game folder): select this row, then '
                     'Import a Save.',)
        return Row('value', 'Saves', 'Import Waiting' if ready else '', 'orange_arrow' if ready else 'arrow',
                   on=lambda: self.show('saves'), desc=desc)

    # ---- saves

    def export_dir(self):
        d = self.settings.get('save_export_dir')
        return Path(d) if d and Path(d).is_dir() else saves.default_export_dir()

    def rows_saves(self):
        gd = self.settings.get('game_dir')
        ready = not IMPORT_DISABLED and self.save_waiting(gd)
        out = self.export_dir()
        export_desc = ('Write your save into one zip file, to attach to a bug report or give to another player: every '
                       'save slot and a note of the OOT-PC and game versions, never your keys or game files. The '
                       'folder opens with the zip selected.',)
        if self.export_message:
            export_desc = (self.export_message,) + export_desc
        import_desc = ('Make a save someone shared (an OOT-PC-save zip) your save: every slot is replaced. Put the zip '
                       'into the import folder (saves\\import in the game folder) and select this row, or select this '
                       'row and choose the zip.', 'Your current save is copied to the saves folder first.')
        if gd and saves.pending_import(gd):
            import_desc += ('A save imported in the game\'s menu waits for the game\'s next start; an import here '
                            'replaces it.',)
        folder_desc = ('Bring in a save folder: a Switch save backup, an emulator\'s save, another OOT-PC install, or '
                       'one save slot (a folder named slot_00 to slot_05: only that slot is replaced).',
                       'Your current save is copied to the saves folder first.')
        if self.save_message:
            import_desc, folder_desc = (self.save_message,) + import_desc, (self.save_message,) + folder_desc
        if IMPORT_DISABLED:
            off = 'Turned off for now: importing may break your saves.'
            import_desc, folder_desc = (off,) + import_desc, (off,) + folder_desc

        def report(v):
            self.settings['export_with_log'] = bool(v)
            self.settings.save()
        return [
            Row('value', 'Export Save', out.name or str(out), on=self.export_save, desc=export_desc),
            Row('toggle', 'Add Log and Settings', on=report, get=lambda: bool(self.settings.get('export_with_log')),
                desc=('On: the zip also holds the game\'s latest log (logs\\ootpc.log) and its settings '
                      '(OOT-PC.ini), which show what happened and with which settings. Keep it on for a bug report: '
                      'while the game has no save yet, Export Save then writes these two alone.',
                      'Off: the save alone, to give to another player.')),
            Row('value', 'Export To', str(out), on=self.choose_export_dir,
                desc=(f'Export Save writes into {out}.', 'Select this row to choose another folder.')),
            Row('divider'),
            Row('value', 'Import a Save', 'Turned Off' if IMPORT_DISABLED else
                'From the Import Folder' if ready else 'Choose a Zip',
                'dim' if IMPORT_DISABLED else 'orange_arrow' if ready else 'arrow',
                on=lambda: self.import_saves(folder=False), desc=import_desc),
            Row('value', 'Import a Save Folder', 'Turned Off' if IMPORT_DISABLED else 'Choose a Folder',
                'dim' if IMPORT_DISABLED else 'arrow', on=lambda: self.import_saves(folder=True), desc=folder_desc),
            Row('value', 'Open Import Folder', '', 'arrow',
                on=lambda: gd and Path(gd).is_dir() and (saves.ensure_saves_dir(gd), self.open(saves.import_dir(gd))),
                desc=('The import folder (saves\\import in the game folder): put a save here, then select Import a '
                      'Save.',)),
            Row('value', 'Open Saves Folder', '', 'arrow',
                on=lambda: gd and Path(gd).is_dir() and self.open(saves.ensure_saves_dir(gd)),
                desc=('The saves folder of the game folder: a backup of each save an import replaced, the saves it '
                      'took in, the save tree, and the import folder.',)),
            Row('action', 'Back', on=lambda: self.show('home'), height=40, desc=('Back to the main menu.',)),
        ]

    def choose_export_dir(self):
        d = filedialog.askdirectory(parent=self.root, title='Choose where Export Save writes its zip',
                                    initialdir=str(self.export_dir()), mustexist=True)
        if d:
            self.settings['save_export_dir'] = str(Path(d))
            self.settings.save()
        self.show('saves', self.focus)

    def export_save(self):
        gd = self.settings.get('game_dir')
        if not gd or not Path(gd).is_dir():
            self.export_message = 'Install the game first.'
        else:
            try:
                r = saves.export_save(gd, self.export_dir(), bug_report=bool(self.settings.get('export_with_log')),
                                      version=versions.display(build.built_version(gd) or self.layout.version),
                                      log=self.ctx.log)
            except (saves.SaveError, OSError) as e:
                self.export_message = f'The save was not exported: {e}'
            else:
                extra = ' with the game\'s log and settings' if r['extras'] else ''
                self.export_message = f'Exported {r["files"]} files{extra} to {r["path"]}.'
                if not r['files']:   # a game that stopped before its first save: the report files alone
                    self.export_message = (f'The game has no save yet: exported its log and settings alone to '
                                           f'{r["path"]}.')
                try:
                    winsys.reveal(r['path'])
                except OSError:
                    pass
        self.show('saves', self.focus)

    def import_saves(self, folder):
        """Import a Save (folder False): what waits in the import folder, else a zip the player picks. Import a Save
        Folder (folder True): a folder the player picks; the saves or the import folder itself takes what waits
        there."""
        gd = self.settings.get('game_dir')
        if IMPORT_DISABLED and not messagebox.askyesno('WARNING: this may break your saves', IMPORT_WARNING,
                                                       icon='warning', default='no', parent=self.root):
            return
        if not gd or not Path(gd).is_dir():
            self.save_message = 'Install the game first.'
        elif (self.game_proc and self.game_proc.poll() is None) or saves.game_running(gd):
            self.save_message = 'Quit the game first: it keeps its save open while it runs.'
        else:
            try:
                src = None
                if folder:
                    src = filedialog.askdirectory(parent=self.root, title='Choose the folder with the save',
                                                  initialdir=str(saves.ensure_saves_dir(gd) / saves.IMPORT),
                                                  mustexist=True)
                    if not src:
                        return
                elif saves.waiting(gd)[0] is None:
                    src = filedialog.askopenfilename(parent=self.root, title='Choose the save to import',
                                                     initialdir=str(self.export_dir()),
                                                     filetypes=[('OOT-PC save', '*.zip'), ('All files', '*.*')])
                    if not src:
                        return
                r = saves.import_save(gd, src, log=self.ctx.log)
            except (saves.SaveError, OSError) as e:
                self.save_message = f'The save was not imported: {e}'
            else:
                self.save_message = self.imported_text(r)
        self.show('saves', self.focus)

    @staticmethod
    def imported_text(r):
        """What an import did, for the import rows' descriptions."""
        m = r.get('manifest')
        if m:
            text = (f'Save imported from {r["source"].name} (exported {str(m.get("exported", ""))[:10]} with OOT-PC '
                    f'{m.get("ootpc") or "?"}).')
        elif r['slot']:
            text = f'Save imported into {r["slot"]}.'
        else:
            text = f'Save imported: {r["files"]} files from {r["source"].name}.'
        return text + (f' Your old save is in saves\\{r["backup"].name}.' if r['backup'] else '')

    # ---- mods

    def mod_list(self, gd, details=True):
        """The game folder's mods in the game's load order ([] before the game is installed or when the folder cannot
        be read)."""
        if not gd or not Path(gd).is_dir():
            return []
        try:
            return mods.scan(gd, details=details)
        except OSError as e:
            self.ctx.log(f'mods: {e}')
            return []

    def mods_row(self, gd):
        """Mods: the screen of the game's mods; orange while a mod's code waits to be built into the game."""
        found = self.mod_list(gd, details=False)
        try:
            waiting = mods.code_waiting(gd) if found else []
        except OSError:
            waiting = []
        on = sum(1 for m in found if m.enabled)
        desc = ('Mods change the game: its pictures, models, sounds, text or code. Put a mod you downloaded into the '
                'mods folder, its .zip, .7z or .rar file as it is or its folder, or select Mods and then Add a Mod: '
                'Play takes it in. Switch each mod on or off, and choose which wins where two change the same file.',)
        if waiting:
            desc += (f'{", ".join(waiting)} changes the game\'s code: Play first builds the game again with it, which '
                     'takes a few minutes.',)
        return Row('value', 'Mods', 'Build Waiting' if waiting else f'{on} On' if found else 'None',
                   'orange_arrow' if waiting else 'arrow', on=lambda: self.show('mods'), desc=desc)

    def mods_page_count(self, n, per):
        return max(1, -(-n // per))

    def rows_mods(self):
        gd = self.settings.get('game_dir')
        folder = mods.mods_dir(gd) if gd else Path('mods')
        found = self.mod_list(gd)
        over = mods.overlaps(found)
        pages = self.mods_page_count(len(found), MODS_PAGE)
        self.mods_page = max(0, min(pages - 1, self.mods_page))
        msg = (self.mods_message,) if self.mods_message else ()
        rows = [
            Row('value', 'Open Mods Folder', str(folder), on=self.open_mods,
                desc=msg + ('Open the mods folder: one folder for each mod. A mod you downloaded goes in as it came, '
                            'an archive (.zip, .7z or .rar) or a folder; Play unpacks archives into folders of their '
                            'own and moves a mod\'s files to where the game looks for them.',
                            'To remove a mod, delete its folder and its archive, or switch it off here.')),
            Row('value', 'Add a Mod', 'Choose a File', 'arrow', on=lambda: self.add_mod(folder=False),
                desc=msg + ('Choose a mod you downloaded, a .zip, .7z or .rar file: it is unpacked into the mods '
                            'folder, ready for the game\'s next start.',)),
            Row('value', 'Add a Mod Folder', 'Choose a Folder', 'arrow', on=lambda: self.add_mod(folder=True),
                desc=msg + ('Choose the folder of a mod (one with a romfs, exefs or cheats folder in it, or an '
                            'atmosphere folder): it is copied into the mods folder.',)),
            Row('value', 'Make N64 Graphics', 'Working' if self.n64_busy else 'Choose Your ROM', 'arrow',
                on=self.make_n64,
                desc=msg + ('Choose your own N64 Ocarina of Time ROM (.z64, .n64 or .v64): the launcher makes the N64 '
                            "Graphics mod from it, which draws Link as the N64 version's, with his N64 sword, "
                            "shield, tunics and voice, moving with this game's animations. Takes about a minute; "
                            "the game loads it at its next start. Do not share the mod: it holds the ROM's graphics "
                            'and sounds.',)),
            Row('value', 'Make Dark Link', 'Working' if self.dark_busy else 'Make', 'arrow', on=self.make_dark_link,
                desc=msg + ("Makes the Dark Link mod from this game's own files: Link plays as Dark Link, the adult "
                            "with Dark Link's models and the child made dark. With N64 Graphics on too, N64 Link is "
                            "drawn as the N64 game's Dark Link at both ages. Takes about a minute; the game loads it "
                            'at its next start. Switch it off below to play as Link again.',)),
        ]
        if found:
            rows.append(Row('divider'))
        for m in found[self.mods_page * MODS_PAGE:(self.mods_page + 1) * MODS_PAGE]:
            label = fit(self.f['label'], m.name, self.X(LABEL_W - 30))
            if m.usable:
                rows.append(Row('toggle', label, on=lambda v, m=m: self.switch_mod(m, v), get=lambda m=m: m.enabled,
                                desc=lambda m=m: self.mod_desc(m, over)))
            else:
                rows.append(Row('value', label, 'Not Usable', 'salmon', on=lambda: None,
                                desc=lambda m=m: self.mod_desc(m, over)))
        if pages > 1:
            rows.append(Row('enum', 'More Mods', get=lambda: f'Page {self.mods_page + 1} of {pages}',
                            left=lambda: self.mods_turn(-1, 'mods'), right=lambda: self.mods_turn(1, 'mods'),
                            desc=(f'{len(found)} mods, {MODS_PAGE} to a page: left and right turn the pages.',)))
        if len(found) > 1:
            rows.append(Row('value', 'Load Order', '', 'arrow', on=lambda: self.show('mod_order'),
                            desc=('Where two mods change the same file, the one higher in the load order wins. Set '
                                  'the order here.',)))
        rows.append(Row('action', 'Back', on=lambda: self.show('home', self.home_row('Mods')), height=40,
                        desc=('Back to the main menu.',)))
        return rows

    def mods_turn(self, d, screen):
        self.mods_page += d
        self.show(screen, next((i for i, r in enumerate(getattr(self, 'rows_' + screen)()) if r.label == 'More Mods'),
                               None))

    def mod_desc(self, m, over):
        lines = [self.mods_message] if self.mods_message else []
        if not m.usable:
            lines.append(m.problem)
        what = []
        if m.files:
            what.append(f'replaces {len(m.files):,} of the game\'s files' if len(m.files) > 1 else
                        'replaces one of the game\'s files')
        if m.patches:
            what.append(f'patches the game\'s code ({m.patches} patch file{"s" if m.patches > 1 else ""})')
        if m.cheat_file:
            what.append('brings cheats, which the Cheats tab of the game\'s menu switches on and off')
        if what:
            lines.append('It ' + (', '.join(what[:-1]) + ' and ' + what[-1] if len(what) > 1 else what[0]) + '.')
        if m.name == mods.STAND_INS:
            lines.append('The launcher made it: stand-ins for game files its check found damaged, so that the game '
                         'can load what needs them.')
        if m.other_version:
            lines.append(f'{m.other_version} of its code patch files are for another version of the game and are '
                         'left out.')
        for other, n, wins in over.get(m.name, [])[:3]:
            lines.append(f'{n:,} of its files are in {other} too: {"this mod" if wins else other} wins, being higher '
                         'in the load order.')
        if m.waiting and m.enabled:
            lines.append('Its code is not in the game yet: Play first builds the game again with it, which takes a '
                         'few minutes.')
        if m.usable:
            lines.append('On: the game loads it at its next start. Off: it stays in the mods folder, unused.')
        return {'title': m.name, 'text': lines}

    def game_busy(self, gd):
        return bool((self.game_proc and self.game_proc.poll() is None) or (gd and saves.game_running(gd)))

    def switch_mod(self, m, on):
        gd = self.settings.get('game_dir')
        if self.game_busy(gd):
            self.mods_message = 'Quit the game first: it reads the mods when it starts.'
            return
        try:
            mods.set_enabled(gd, m.name, on)
        except OSError as e:
            self.mods_message = f'The game\'s settings file could not be written: {e}'
            return
        m.enabled, self.mods_message = bool(on), ''
        self.ctx.log(f'mods: {m.name} {"on" if on else "off"}')

    def open_mods(self):
        gd = self.settings.get('game_dir')
        if not gd or not Path(gd).is_dir():
            self.mods_message = 'Install the game first.'
            self.show('mods', self.focus)
            return
        try:
            folder = mods.ensure_mods_dir(gd)
        except OSError as e:
            self.mods_message = f'The mods folder could not be made: {e}'
            self.show('mods', self.focus)
            return
        self.open(folder)

    def add_mod(self, folder):
        gd = self.settings.get('game_dir')
        if not gd or not Path(gd).is_dir():
            self.mods_message = 'Install the game first.'
        else:
            if folder:
                src = filedialog.askdirectory(parent=self.root, title='Choose the folder of the mod',
                                              initialdir=str(Path.home() / 'Downloads'), mustexist=True)
            else:
                src = filedialog.askopenfilename(parent=self.root, title='Choose the mod',
                                                 initialdir=str(Path.home() / 'Downloads'),
                                                 filetypes=[('Mod', '*.zip *.7z *.rar'), ('All files', '*.*')])
            if not src:
                return
            try:
                target = mods.add(gd, src, log=self.ctx.log)
            except (mods.ModError, OSError) as e:
                self.mods_message = f'{Path(src).name} was not added: {e}.'
            else:
                added = next((m for m in self.mod_list(gd) if m.name == target.name), None)
                self.mods_message = f'Added {target.name}.' + (f' {added.problem}' if added and not added.usable else
                                                              ' It is on: the game loads it at its next start.')
        self.show('mods', self.focus)

    def make_n64(self):
        """Make the N64 Graphics mod (tools/make_n64_mod.py) from a ROM the player chooses, on a thread of its own."""
        gd = self.settings.get('game_dir')
        if self.n64_busy:
            return
        if not gd or not Path(gd).is_dir():
            self.mods_message = 'Install the game first.'
        elif self.game_busy(gd):
            self.mods_message = 'Quit the game first: it reads the mods when it starts.'
        else:
            rom = filedialog.askopenfilename(parent=self.root, title='Choose your N64 Ocarina of Time ROM',
                                             initialdir=str(Path.home()),
                                             filetypes=[('N64 ROM', '*.z64 *.n64 *.v64'), ('All files', '*.*')])
            if not rom:
                return
            self.n64_busy = True
            self.mods_message = 'Making N64 Graphics from your ROM...'

            def work():
                try:
                    import make_n64_mod
                    out = mods.ensure_mods_dir(gd) / make_n64_mod.MOD_NAME
                    m = make_n64_mod.make(rom, out, log=self.ctx.log, romfs=Path(gd) / 'romfs')
                    voice = ' and his voice' if m.get('voice') else ''
                    msg = (f"Made N64 Graphics: Link's models{voice} from {Path(rom).name}. It is on: the game "
                           'loads it at its next start.')
                except Exception as e:   # a ROM that is not Ocarina of Time, a file that cannot be read or written
                    self.ctx.log(f'n64: {type(e).__name__}: {e}')
                    msg = f'N64 Graphics was not made: {e}'

                def done():
                    self.n64_busy, self.mods_message = False, msg
                    if self.screen == 'mods':
                        self.show('mods', self.focus)
                self.root.after(0, done)
            import threading
            threading.Thread(target=work, daemon=True).start()
        self.show('mods', self.focus)

    def make_dark_link(self):
        """Make the Dark Link mod (tools/make_dark_link_mod.py) from the installed game's files, on a thread of its own."""
        gd = self.settings.get('game_dir')
        if self.dark_busy:
            return
        if not gd or not Path(gd).is_dir():
            self.mods_message = 'Install the game first.'
        elif self.game_busy(gd):
            self.mods_message = 'Quit the game first: it reads the mods when it starts.'
        else:
            self.dark_busy = True
            self.mods_message = 'Making Dark Link from the game\'s files...'

            def work():
                try:
                    import make_dark_link_mod
                    out = mods.ensure_mods_dir(gd) / make_dark_link_mod.MOD_NAME
                    make_dark_link_mod.make(gd, out, log=self.ctx.log, msys=self.ctx.layout.msys,
                                            work=self.ctx.layout.cache / 'dark-link')
                    msg = 'Made Dark Link. It is on: the game loads it at its next start.'
                except (Exception, SystemExit) as e:   # no compiler, a game without the models, a file not written
                    self.ctx.log(f'dark link: {type(e).__name__}: {e}')
                    msg = f'Dark Link was not made: {e}'

                def done():
                    self.dark_busy, self.mods_message = False, msg
                    if self.screen == 'mods':
                        self.show('mods', self.focus)
                self.root.after(0, done)
            import threading
            threading.Thread(target=work, daemon=True).start()
        self.show('mods', self.focus)

    def rows_mod_order(self):
        gd = self.settings.get('game_dir')
        found = self.mod_list(gd, details=False)
        pages = self.mods_page_count(len(found), ORDER_PAGE)
        self.mods_page = max(0, min(pages - 1, self.mods_page))
        rows = []
        first = self.mods_page * ORDER_PAGE
        for k, m in enumerate(found[first:first + ORDER_PAGE], first + 1):
            rows.append(Row('enum', fit(self.f['label'], m.name, self.X(LABEL_W - 30)), get=lambda k=k: f'{k}',
                            left=lambda m=m: self.order_move(m.name, -1), right=lambda m=m: self.order_move(m.name, 1),
                            desc=lambda m=m, k=k: {'title': m.name, 'text': [
                                f'Number {k} of {len(found)} in the load order{" (off)" if not m.enabled else ""}. '
                                'Left moves it up: it wins over the mods below it where two change the same file. '
                                'Right moves it down.'] + ([self.mods_message] if self.mods_message else [])}))
        if pages > 1:
            rows.append(Row('enum', 'More Mods', get=lambda: f'Page {self.mods_page + 1} of {pages}',
                            left=lambda: self.mods_turn(-1, 'mod_order'), right=lambda: self.mods_turn(1, 'mod_order'),
                            desc=(f'{len(found)} mods, {ORDER_PAGE} to a page: left and right turn the pages.',)))
        rows.append(Row('action', 'Back', on=lambda: self.show('mods', self.mods_back_focus()), height=40,
                        desc=('Back to the mods.',)))
        return rows

    def mods_back_focus(self):
        return next((i for i, r in enumerate(self.rows_mods()) if r.label == 'Load Order'), None)

    def order_move(self, name, d):
        gd = self.settings.get('game_dir')
        if self.game_busy(gd):
            self.mods_message = 'Quit the game first: it reads the mods when it starts.'
            return
        try:
            mods.move(gd, name, d)
        except OSError as e:
            self.mods_message = f'The game\'s settings file could not be written: {e}'
            return
        self.mods_message = ''
        names = [m.name for m in self.mod_list(gd, details=False)]
        at = names.index(name) if name in names else 0
        self.mods_page = at // ORDER_PAGE
        self.show('mod_order', at % ORDER_PAGE)

    def mods_before_play(self, gd):
        """Put the mods folder in order (mods.prepare) and say whether the game must be built again for a mod's code
        first: the mods that wait for it, [] when none do."""
        try:
            for line in mods.prepare(gd, log=self.ctx.log):
                self.mods_message = line
                if 'was not unpacked' in line:   # on the home screen too, when the launcher comes back
                    self.message = line
        except OSError as e:
            self.ctx.log(f'mods: the mods folder could not be put in order: {e}')
        try:
            return mods.code_waiting(gd) if build.can_rebuild(gd) else []
        except OSError as e:
            self.ctx.log(f'mods: {e}')
            return []

    def update_desc(self):
        r = self.release
        nv, mine = versions.display(r.version), versions.display(self.layout.version)
        notes = feed.plain_notes(r.notes, 6)
        return {'title': f'Version {nv} Is Ready',
                'text': [f'You have {mine}. Updating downloads the new version and rebuilds the game. {UPDATE_TAKES} '
                         f'Your saves and settings stay as they are.'],
                'divider': bool(notes), 'subtitle': "What's New" if notes else '', 'bullets': notes}

    def staged_desc(self, m):
        """The Updates row of a release downloaded in the background (BackgroundUpdate.ready)."""
        nv, mine = m['version'], versions.display(self.layout.version)
        notes = feed.plain_notes(m.get('notes', ''), 4)
        if self.settings.get('auto_install'):
            text = (f'You have {mine}. Version {nv} is downloaded and installs when you are not playing: after you '
                    'quit the game, or when OOT-PC next opens. Select this row to install it now.')
        else:
            text = f'You have {mine}. Version {nv} is downloaded. Select this row to install it.'
        return {'title': f'Version {nv} Is Ready', 'text': [text],
                'divider': bool(notes), 'subtitle': "What's New" if notes else '', 'bullets': notes,
                'note': f'Installing restarts the launcher and rebuilds the game. {UPDATE_TAKES} Your saves and '
                        'settings stay as they are.'}

    def staging_desc(self):
        nv, mine = self.bg.job.ver, versions.display(self.layout.version)
        if self.bg.asked:
            then = 'It installs as soon as the download is done.'
        elif self.settings.get('auto_install'):
            then = 'It installs when you are not playing. Select this row to install it as soon as it is downloaded.'
        else:
            then = 'Select this row to install it as soon as it is downloaded.'
        return {'title': f'Downloading Version {nv}',
                'text': [f'You have {mine}. Version {nv} is downloading in the background; you can play meanwhile. '
                         f'{then}']}

    def ago(self, t):
        d = time.time() - t
        if d < 90:
            return 'just now'
        if d < 3600:
            return f'{int(d // 60)} minutes ago'
        return time.strftime('at %H:%M', time.localtime(t))

    def check_now(self, force=False):
        if self.check and self.check.result is None:
            return
        if not force and time.time() - self.settings.get('last_check', 0) < 600 and self.release:
            return
        if force and time.time() - self.check_started < FORCED_CHECK_PAUSE:
            self.message = 'OOT-PC looked for updates less than a minute ago.'
            if self.screen == 'home':
                self.show('home', self.focus)
            return
        self.check_error = ''
        self.check_started = time.time()
        self.check = jobs.CheckJob(self.ctx).start()
        if self.screen == 'home':
            self.show('home', self.focus)

    def check_done(self):
        c = self.check
        self.bg.checked()   # the release found is staged at the next tick, also one whose staging failed before
        if c.result == 'ok':
            self.release = c.release
            self.checked_at = time.time()
            self.settings['last_check'] = self.checked_at
            if self.release:
                self.settings['last_seen'] = self.release.version
            self.settings.save()
            self.ctx.log(f'update check: {self.release.version if self.release else "no release"}')
        else:
            self.check_error = c.error or 'cancelled'
            self.ctx.log(f'update check: {self.check_error}')
        if self.screen == 'home':
            self.show('home', self.focus)

    def play(self, built=False):
        """built: the build for the mods' code (ModsRebuildJob) just ended, so the game starts without asking again."""
        gd = self.settings.get('game_dir')
        exe = Path(gd) / 'ocarina.exe'
        if not exe.is_file():
            self.message = 'The game is not built yet.'
            self.render()
            return
        if self.game_proc and self.game_proc.poll() is None:
            return
        if saves.game_running(gd):   # a copy this launcher did not start (another launcher, a shortcut, a test run)
            self.message = 'The game is already running.'
            self.render()
            return
        waiting = [] if built else self.mods_before_play(gd)
        if waiting and waiting != self.mods_failed:
            # a mod's code cheats or ExeFS patches the game was not built with: built in first (tools/make_game.py
            # lift), then the game starts (job_finished). After a build that failed for the same mods the game
            # starts without them, so that a build that cannot finish never keeps the player from the game.
            self.ctx.log(f'play: the game is built again first for the code of {", ".join(waiting)}')
            job = jobs.ModsRebuildJob(self.ctx, gd)
            job.waiting = waiting
            self.run_job(job, 'home')
            self.message = f'Building the game with the code of {", ".join(waiting)}; it starts after.'
            return
        if waiting:
            self.ctx.log(f'play: without the code of {", ".join(waiting)}, whose build did not finish')
        if mods.feature_on(gd, mods.STEAM_COOP):   # the Steam Co-op mod: Steam's library file beside the game
            steamlib.ensure(gd, self.ctx.layout.app / 'bin', log=self.ctx.log)
        try:
            self.game_proc = winsys.start_game(exe)
        except OSError as e:
            self.message = f'The game could not start: {e}'
            self.render()
            return
        self.ctx.log(f'play: {exe}')
        self.sounds.play('select')
        if self.settings.get('hide_while_playing'):
            self.root.withdraw()
        self.root.after(1000, self.watch_game)

    def watch_game(self):
        if self.tree_only:   # the launcher was closed (on_close): it does not come back when the game ends
            self.game_proc = None
            return
        # The game's own Restart ends the process started here and starts another (gfx_present.c gfx_window_closed)
        gd = self.settings.get('game_dir')
        if (self.game_proc and self.game_proc.poll() is None) or (gd and saves.game_running(gd)):
            self.root.after(1000, self.watch_game)
            return
        code = self.game_proc.returncode if self.game_proc else 0
        self.game_proc = None
        self.root.deiconify()
        self.root.lift()
        if code not in (0, None):
            text = exit_code_text(code)
            # 70 and 71 follow a FATAL line of the game's own, which names the error: the player gets that line
            # and the launcher's log too, where the exit code alone said only that there was one
            fatal = last_fatal(self.game_dir()) if code & 0xFFFFFFFF in (70, 71) else ''
            if fatal:
                shown = fatal if len(fatal) <= FATAL_SHOWN else fatal[:FATAL_SHOWN].rstrip() + '…'
                self.ctx.log(f'the game ended with code {code & 0xFFFFFFFF}; its log says: FATAL: {fatal}')
                self.message = (f'The game ended with a fatal error (code {code & 0xFFFFFFFF}): {shown}. Its log is '
                                'logs\\ootpc.log in the game folder.')
            else:
                self.ctx.log(f'the game ended with code {text}')
                self.message = f'The game ended with code {text}. Its log is logs\\ootpc.log in the game folder.'
            note_exit_in_log(self.game_dir(), text)
        if self.install_staged('the game exited'):
            return
        self.show('home', self.focus)

    def open(self, path):
        try:
            winsys.open_path(path)
        except OSError as e:
            self.message = str(e)
            self.render()

    def start_update(self):
        """The Updates row: install the release staged in the background, have the one downloading install once it
        is staged, or else download it here with its steps on screen (as when the background download failed)."""
        gd = self.settings.get('game_dir')
        if gd and build.game_running(gd):
            self.message = 'Close the game first: the update rebuilds it.'
            self.show('home', self.focus)
            return
        if self.bg.staging():
            self.bg.asked = True
            self.message = f'Version {self.bg.job.ver} installs as soon as it is downloaded.'
            self.show('home', self.focus)
            return
        if self.bg.ready(self.release):
            if not self.install_staged('selected', asked=True):
                self.message = f'The update could not be installed: {self.bg.error or "see the launcher log"}.'
                self.show('home', self.focus)
            return
        if not self.release:
            return
        self.run_job(jobs.UpdateDownloadJob(self.ctx, self.release), 'home')

    def install_staged(self, why, asked=False):
        """Install the release staged in the background when nothing stops it (BackgroundUpdate.install) and close
        for the restart into it; True when it was installed."""
        if self.job and self.job.result is None:
            return False
        running = bool(self.game_proc and self.game_proc.poll() is None)
        ver = self.bg.install(self.settings.get('game_dir'), self.release, why, asked=asked, running=running)
        if not ver:
            return False
        if self.bg.restarting:
            self.message = f'Installing OOT-PC {ver}…'
            self.root.after(200, self.root.destroy)
        else:
            self.message = f'OOT-PC {ver} is installed: start OOT-PC again to finish it.'
        if self.screen == 'home':
            self.show('home', self.focus)
        return True

    def background_event(self, ev):
        """A background staging started or ended (BackgroundUpdate.poll)."""
        if ev in ('staged', 'failed') and (self.bg.asked or self.bg.waiting):
            if self.install_staged('the download finished'):
                return
            if ev == 'failed' and self.bg.asked:
                # the player chose to update: download it here instead, where its error and Try Again show
                self.bg.asked = False
                gd = self.settings.get('game_dir')
                if (self.screen == 'home' and self.release and jobs.update_available(self.ctx, self.release)
                        and not (self.game_proc and self.game_proc.poll() is None)
                        and not (gd and build.game_running(gd))):
                    self.run_job(jobs.UpdateDownloadJob(self.ctx, self.release), 'home')
                    return
        if self.screen == 'home':
            self.show('home', self.focus)

    def start_rebuild(self):
        gd = self.settings.get('game_dir')
        self.run_job(jobs.RebuildJob(self.ctx, gd, self.settings.get('dump')), 'home')

    # ---- settings

    def rows_settings(self):
        gd = self.settings.get('game_dir')
        dump = self.settings.get('dump')
        return [
            self.toggle('Check for Updates at Start', 'auto_check',
                        'Look for a new version of OOT-PC when the launcher opens, and every hour while it stays '
                        'open (also while you play). A new version downloads in the background.'),
            self.toggle('Install Updates Automatically', 'auto_install',
                        'Install a downloaded update by itself when you are not playing: after you quit the game, '
                        'or when OOT-PC opens. Off: it waits until you select Updates. Updates download in the '
                        'background either way.'),
            self.toggle('Hide Launcher While Playing', 'hide_while_playing',
                        'Hide this window while the game runs; it comes back when you quit the game.'),
            self.toggle('Keep Build Cache', 'keep_cache',
                        'Keep the translated code and compiled files (about 1.5 GB) in the game folder, so an update '
                        'compiles only what changed. Off: they are deleted after each build.'),
            self.channel_row(),
            *([self.discord_row(gd)] if gd and Path(gd).is_dir() else []),
            Row('divider'),
            Row('value', 'Game Folder', gd or 'Not chosen', on=lambda: gd and self.open(gd),
                desc=('Open the game folder: saves in profile, the game\'s settings in OOT-PC.ini, its logs in '
                      'logs.',)),
            Row('value', 'Game Dump', Path(dump).name if dump else 'Not chosen', on=self.choose_dump,
                desc=(f'The dump the game was built from: {dump}.' if dump else 'No dump chosen yet.',
                      'Select this row to choose another .nup file.')),
            Row('divider'),
            Row('value', 'Open Logs Folder', '', 'arrow', on=lambda: self.open(self.layout.logs),
                desc=('The launcher\'s logs. Attach launcher.log when you report a problem with installing or '
                      'updating; never send game files.',)),
            Row('value', 'Uninstall OOT-PC', 'Keeps your saves', 'salmon', on=lambda: self.show('uninstall'),
                desc=('Remove the launcher and its build tools. You choose on the next screen whether the built '
                      'game goes too.',)),
            Row('action', 'Back', on=lambda: self.show('home'), height=40, desc=('Back to the main menu.',)),
        ]

    # ---- uninstall

    def rows_uninstall(self):
        def on(v):
            self.uninstall_game = bool(v)
        gd = self.settings.get('game_dir')
        return [
            Row('toggle', 'Remove the Built Game', on=on, get=lambda: self.uninstall_game,
                desc=(f'On: the game folder ({gd or "none"}) is deleted too, saves included.',
                      'Off: the game folder and your saves stay where they are.')),
            Row('divider'),
            Row('pair', actions=[('Uninstall', self.do_uninstall), ('Cancel', lambda: self.show('settings'))],
                desc=('Uninstall removes OOT-PC, its build tools and its shortcuts. Your dump is never touched.',)),
        ]

    def do_uninstall(self):
        gd = self.settings.get('game_dir') if self.uninstall_game else ''
        if gd and build.game_running(gd):
            self.message = 'Close the game first.'
            self.render()
            return
        self.ctx.log(f'uninstall (game folder too: {bool(gd)})')
        winsys.uninstall(self.layout, gd, self.ctx.log)
        self.root.destroy()

    # ---- jobs

    def run_job(self, job, return_to, back=None):
        self.job, self.job_return, self.job_back = job, return_to, back or return_to
        self.job_rev = -1
        self.message = ''
        winsys.keep_awake(True)
        job.start()
        self.show('job')

    def rows_job(self):
        j = self.job
        rows = []
        with j.locked():   # each step as one change of the worker left it, not a state with the value before it
            steps = [(st.id, st.label, st.state, st.value, st.fraction) for st in j.steps]
        for sid, label, state, value, fraction in steps:
            if state == jobs.RUNNING:
                rows.append(Row('progress', label, fraction=fraction, value=f'{int(fraction * 100)}%',
                                focus_value=True, desc=None))
            else:
                style = {jobs.DONE: 'normal', jobs.SKIPPED: 'dim', jobs.PENDING: 'dim', jobs.FAILED: 'salmon'}[state]
                rows.append(Row('value', label, value, style, check=state == jobs.DONE, desc=None,
                                focus_value=state == jobs.FAILED))
            if sid in ('romfs', 'verify') and sid != steps[-1][0]:
                rows.append(Row('divider'))
        if j.result is None:
            label = {'install': 'Cancel Installation', 'update': 'Cancel Update'}.get(j.kind, 'Cancel')
            rows.append(Row('action', label, on=self.cancel_job, height=44))
        elif j.result == 'failed':
            rows.append(Row('pair', actions=[('Try Again', self.retry_job), ('Copy Report', self.copy_report)]))
        for r in rows:
            r.desc = self.job_desc
        return rows

    def job_focus(self):
        """The cursor sits on the running step, or on Try Again after a failure."""
        for i, r in enumerate(self.rows):
            if self.job.result == 'failed' and r.kind == 'pair':
                return i
            if r.kind == 'progress':
                return i
        return self.focusables()[-1] if self.focusables() else 0

    def job_desc(self):
        j = self.job
        cur = j.current()
        lines = [ln for ln in j.last_lines(200) if ln.strip() and not ln.startswith('@@')][-6:]
        if j.result == 'failed':
            return {'title': 'The Build Stopped' if j.kind != 'update' or cur.id not in ('download', 'verify')
                    else 'The Update Stopped',
                    'text': [self.friendly(j.error), 'Nothing in your game folder was changed.' if cur.id not in
                             ('download', 'verify', 'tools', 'tools_update') else ''],
                    'log': lines, 'note': 'Try again first. If it stops again, use Copy Report and send it with your '
                                          'issue. Never send game files.'}
        if j.result == 'cancelled':
            return {'title': 'Cancelled', 'text': ['Nothing was changed that a new start cannot finish.']}
        if j.result == 'ok':
            return {'title': 'Done', 'text': [f'Finished in {jobs.mmss(j.elapsed())}.']}
        text = [cur.desc + (f' {cur.detail}.' if cur.detail else '')]
        eta = j.eta()
        return {'title': self.step_title(cur), 'text': text, 'progress': j.overall(),
                'progress_label': ('Overall', self.eta_text(eta)),
                'log': lines,
                'note': 'Keep using your PC if you like. It stays awake until the build is done.'
                if j.kind != 'update' or cur.id not in ('download', 'verify') else
                'Your saves and settings stay as they are.'}

    @staticmethod
    def eta_text(eta):
        if not eta:
            return ''
        minutes = max(1, int(eta // 60 + 1))
        return 'about 1 minute left' if minutes == 1 else f'about {minutes} minutes left'

    def step_title(self, st):
        return {'tools': 'Gathering Build Tools', 'tools_update': 'Updating Build Tools', 'check': 'Inspecting Your Game',
                'exefs': 'Unpacking the Program', 'romfs': 'Packing the Game Data', 'lift': 'Translating the Code',
                'compile': 'Compiling', 'compile_changed': 'Compiling Changed Files', 'shaders': 'Preparing the Shaders',
                'prompts': 'Drawing the Button Prompts', 'scan': 'Checking the Game Files', 'ready': 'Ready to Play',
                'download': 'Downloading', 'verify': 'Verifying', 'swap': 'Swapping In the New Build'}.get(st.id,
                                                                                                         st.label)

    @staticmethod
    def friendly(err):
        e = (err or 'unknown error').strip()
        return e[0].upper() + e[1:] + ('' if e.endswith('.') else '.')

    def cancel_job(self):
        if self.job and self.job.result is None:
            self.job.cancel()

    def retry_job(self):
        j = self.job
        if isinstance(j, jobs.InstallJob):
            self.run_job(jobs.InstallJob(self.ctx, j.dump, j.game_dir, j.version), self.job_return, self.job_back)
        elif isinstance(j, jobs.ContinueUpdateJob):
            self.run_job(jobs.ContinueUpdateJob(self.ctx, j.game_dir, j.old, j.dump), self.job_return)
        elif isinstance(j, jobs.RebuildJob):
            self.run_job(jobs.RebuildJob(self.ctx, j.game_dir, j.dump), self.job_return)
        elif isinstance(j, jobs.UpdateDownloadJob):
            self.run_job(jobs.UpdateDownloadJob(self.ctx, j.release), self.job_return)

    def copy_report(self):
        j = self.job
        report = [f'OOT-PC {self.layout.version} ({"checkout" if self.layout.dev else "release"}), {sys.platform}',
                  f'Job: {j.kind}, step: {j.current().label}', f'Error: {j.error}', '', 'Last lines:']
        report += j.last_lines(60)
        self.root.clipboard_clear()
        self.root.clipboard_append('\n'.join(report))
        self.message = 'Report copied.'
        self.render()

    def leave_job(self):
        j = self.job
        if j.result == 'ok':
            self.show(self.job_return)
        elif j.result == 'cancelled':
            self.show(self.job_back)
        else:
            self.show(self.job_back)

    def job_finished(self):
        j = self.job
        winsys.keep_awake(False)
        self.ctx.log(f'job {j.kind}: {j.result} {j.error}')
        if j.result in ('ok', 'failed'):
            self.sounds.play('select' if j.result == 'ok' else 'back')
        if j.result == 'ok':
            if j.kind == 'install':   # first, as what follows acts on the selected game folder, now this one
                self.record_install(j)
            if j.after == 'restart':
                if winsys.restart(self.layout, ['--continue-update', versions.display(self.layout.version)]):
                    self.root.after(200, self.root.destroy)
                    return
                self.message = 'The update is ready: start OOT-PC again to finish it.'
            elif j.after == 'staged':
                self.message = 'The update was downloaded; a checkout keeps running from the repository.'
            elif j.after == 'play':   # the mods' code is built in (play): the game starts
                if not self.settings.get('keep_cache'):
                    self.drop_build_cache()
                self.message, self.mods_failed = '', []
                self.show('home', self.home_row('Play'))
                self.play(built=True)
                return
            self.mods_failed = []
            if not self.settings.get('keep_cache') and j.kind in ('install', 'update', 'rebuild'):
                self.drop_build_cache()
            if j.kind == 'install':
                self.message = 'The game is built. Select Play.'
            elif j.kind in ('update', 'rebuild'):
                self.message = 'The game is rebuilt and up to date.' if j.kind == 'rebuild' else \
                    f'OOT-PC is now {versions.display(self.layout.version)}.'
                self.release = None
            note = getattr(j, 'note', '')   # what the file check found (jobs.Job.note_scan)
            if note and j.after is None:
                self.message = f'{self.message} {note}'.strip()
            self.reload_fonts_if_new()
            self.show(self.job_return)
            if j.kind == 'install' and self.settings.get('auto_check'):
                self.check_now()
        elif j.result == 'cancelled':
            self.mods_failed = getattr(j, 'waiting', self.mods_failed)
            self.show(self.job_back)
        else:
            self.mods_failed = getattr(j, 'waiting', self.mods_failed)
            self.show('job')

    def drop_build_cache(self):
        """Keep Build Cache off: delete the lifted C and the objects (about 1.5 GB) but keep state.json, which an
        update needs to rebuild without the dump; the next build then lifts and compiles everything again."""
        gd = self.settings.get('game_dir')
        if not gd:
            return
        cache = Path(gd) / 'build-cache'
        for name in ('lift', 'obj', 'gen'):
            shutil.rmtree(cache / name, ignore_errors=True)
        for name in ('link.rsp', 'link.key'):
            (cache / name).unlink(missing_ok=True)

    def reload_fonts_if_new(self):
        if not self.game_fonts and gamefiles.cached_fonts(self.layout):
            self.reload_fonts()

    # ---------------------------------------------------------------- timers

    def tick(self):
        try:
            if self.check and self.check.result is not None and not getattr(self.check, 'seen', False):
                self.check.seen = True
                self.check_done()
            ev = self.bg.poll(self.release, busy=bool(self.job and self.job.result is None))
            if ev:
                self.background_event(ev)
            if self.screen == 'job' and self.job:
                if self.job.rev != self.job_rev:
                    self.job_rev = self.job.rev
                    if self.job.result is not None:
                        self.job_finished()
                    else:
                        self.rows = self.rows_job()
                        self.focus = self.job_focus()
                        self.render()
                elif int(time.time()) != self.last_second and self.job.result is None:
                    self.render()
            self.last_second = int(time.time())
        except Exception as e:   # a drawing error must not stop the timer
            self.ctx.log(f'tick: {e!r}')
        self.root.after(150, self.tick)

    def minute(self):
        """Once a minute, also while the window is hidden during play: look for a new version an hour after the last
        look (a new one then downloads in the background), and install a staged release that waited for a copy of
        the game the launcher did not start to close."""
        try:
            if not self.args.selftest and self.screen in ('home', 'settings'):
                last = max(self.settings.get('last_check', 0.0), self.check_started)
                if self.settings.get('auto_check') and time.time() - last >= jobs.RECHECK:
                    self.check_now()
                if self.bg.waiting and not (self.game_proc and self.game_proc.poll() is None):
                    self.install_staged('the game closed')
        except Exception as e:
            self.ctx.log(f'minute: {e!r}')
        self.root.after(60000, self.minute)

    def animate(self):
        """One step of everything that moves: the cursor on its way to the focused row and its breath, the screen
        sliding into place, the sparks and Navi. Each moves by the time since the last step (the timer comes when
        Tk gets to it, 16 to 50 ms apart), at most a tenth of a second's worth. With Animations off, while the
        window is hidden or minimised, and while another window is in front, nothing moves and the timer runs slowly.
        The last covers a launcher left open behind the game (Hide Launcher While Playing off): drawing every 15 ms
        took about 0.6 of a CPU core from the game for a window nobody saw."""
        moving = False
        try:
            now = time.perf_counter()
            dt = min(0.1, max(0.0, now - self.anim_last))
            self.anim_last = now
            moving = self.animated() and self.root.state() == 'normal' and winsys.in_front(self.root)
            if moving:
                self.anim_t += dt
                self.step_slide(dt)
                self.step_cursor(dt)
                self.step_backdrop(dt)
        except Exception as e:   # a drawing error must not stop the timer
            self.ctx.log(f'animate: {e!r}')
        self.root.after(FRAME_MS if moving else 300, self.animate)

    def step_slide(self, dt):
        if not self.slide:
            return
        left = self.slide * math.exp(-dt * 16)   # most of the way in a fifth of a second
        if abs(left) < 1:
            left = 0.0
        self.canvas.move('dyn', int(left) - self.slide_applied, 0)
        self.slide, self.slide_applied = left, int(left)

    def step_cursor(self, dt):
        c = self.canvas
        if self.cursor_frames:   # one breath in 1.6 s
            k = int(self.anim_t / 1.6 * len(self.cursor_frames)) % len(self.cursor_frames)
            if k != self.cursor_k:
                self.cursor_k = k
                c.itemconfigure('cursor', image=self.cursor_frames[k])
        if self.cur_xy and self.cur_target and self.cur_xy != self.cur_target:
            (x, y), (tx, ty) = self.cur_xy, self.cur_target
            f = 1 - math.exp(-dt * 22)   # there in about a tenth of a second
            x, y = x + (tx - x) * f, y + (ty - y) * f
            if abs(tx - x) < 0.6 and abs(ty - y) < 0.6:
                x, y = tx, ty
            self.cur_xy = (x, y)
            c.coords('cursor', round(x) + self.slide_applied, round(y))

    def step_backdrop(self, dt):
        c, t = self.canvas, self.anim_t
        for sp in self.sparks:   # up, swaying, and back in at the bottom once under the title band
            sp['y'] -= sp['vy'] * dt
            if sp['y'] < BAND[2] - 8:
                sp['y'] = H + 8
            c.coords(sp['id'], self.X(sp['x'] + math.sin(t * sp['rate'] + sp['phase']) * sp['sway']), self.X(sp['y']))
            k = int((math.sin(t * sp['rate'] * 2.3 + sp['phase'] * 3) + 1) * 1.49)   # 0, 1 or 2: its twinkle
            if k != sp['shown']:
                sp['shown'] = k
                c.itemconfigure(sp['id'], image=sp['pics'][k])
        if self.navi_xy:   # the emblem's halo pulses every 2.4 s
            if self.halo:
                c.itemconfigure('halo', image=self.halo[int(t / 2.4 * len(self.halo)) % len(self.halo)])

    # ---------------------------------------------------------------- drawing

    def render(self):
        c = self.canvas
        c.delete('dyn')
        titles = {'home': 'Your adventure', 'first_run': 'Welcome to OOT-PC', 'settings': 'Settings',
                  'customize': 'Appearance', 'saves': 'Saves', 'mods': 'Mods', 'versions': 'Game versions',
                  'mod_order': 'Load order', 'uninstall': 'Manage installation', 'color': 'Custom colors',
                  'job': 'Preparing your adventure'}
        self.draw_navigation()
        self.text(self.X(390),self.X(120),titles.get(self.screen,'OOT-PC'),self.f['page'],P['text_bright'])
        divider=self.sprite(('heading-rule',self.X(925)),lambda:art.forest_widget(self.X(925),self.X(25),2))
        c.create_image(self.X(390),self.X(161),anchor='w',image=divider,tags='dyn')
        if self.screen == 'job' and self.job:
            if self.job.result is None or self.rows == []:
                self.rows = self.rows_job()
            if not (0 <= self.focus < len(self.rows)) or not self.rows[self.focus].focusable:
                self.focus = self.job_focus()
        x0, x1, y = ROWS
        heights, self.row_gap = self.fit_rows(y, H - 115 if self.screen == 'job' else H - 100)
        for r, h in zip(self.rows, heights):
            self.draw_row(r, self.X(x0), self.X(y), self.X(x1), self.X(y + h))
            y += h + self.row_gap
        self.draw_status()
        self.draw_cursor()
        if self.screen == 'color' and self.pick:
            self.draw_picker()
        else:
            self.draw_desc()
        self.slide_applied = int(self.slide)
        if self.slide_applied:   # drawn in place, then put where the slide has got to (step_slide)
            c.move('dyn', self.slide_applied, 0)

    def fit_rows(self, top, bottom):
        """The height of each row and the gap between rows (logical pixels) that end the rows above bottom. A screen
        with more rows than the window holds (Settings with every row, a job with every step) narrows its gaps
        first, then its dividers, and last makes its rows a little lower."""
        rows = self.rows
        hs = [r.h for r in rows]
        gaps = max(1, len(rows) - 1)
        room = bottom - top
        for stage in ('gaps', 'dividers', 'rows'):
            if stage == 'dividers':
                hs = [min(h, 14) if r.kind == 'divider' else h for r, h in zip(rows, hs)]
            elif stage == 'rows':
                over = sum(hs) + 6 * gaps - room
                cut = -(-over // max(1, sum(1 for r in rows if r.kind != 'divider')))
                hs = [h if r.kind == 'divider' else max(30, h - cut) for r, h in zip(rows, hs)]
            gap = min(GAP, (room - sum(hs)) // gaps)
            if gap >= 6:
                return hs, gap
        return hs, 6

    def draw_status(self):
        if self.screen != 'job' or not self.job:
            return
        j = self.job
        steps = [s for s in j.steps]
        cur = j.current()
        n = steps.index(cur) + 1 if cur in steps else len(steps)
        left = f'Stopped at step {n} of {len(steps)}' if j.result == 'failed' else f'Step {n} of {len(steps)}'
        if self.message:
            left = self.message
        y = self.X(H - 104)
        self.canvas.create_text(self.X(PANEL[0] + 30), y, text=left, anchor='sw', font=self.f['status'],
                                fill=P['status'], tags='dyn')
        self.canvas.create_text(self.X(PANEL[2] - 30), y, text=f'Elapsed {jobs.mmss(j.elapsed())}', anchor='se',
                                font=self.f['status'], fill=P['status'], tags='dyn')

    def text(self, x, y, text, font, fill, anchor='w', shadow=True, width=0):
        c = self.canvas
        if shadow:
            c.create_text(x + 1, y + 1, text=text, font=font, fill=P['text_shadow'], anchor=anchor, tags='dyn',
                          width=width)
        return c.create_text(x, y, text=text, font=font, fill=fill, anchor=anchor, tags='dyn', width=width)

    def draw_row(self, r, x0, y0, x1, y1):
        c, s, X = self.canvas, self.s, self.X
        r.rect = (x0, y0, x1, y1)
        if r.kind == 'divider':
            img = self.sprite(('forest-divider', x1 - x0), lambda: art.forest_widget(x1-x0,X(22),2))
            c.create_image(x0, (y0 + y1) // 2, anchor='w', image=img, tags='dyn')
            return
        if self.screen == 'home' and r.label == 'Play':
            img=self.sprite(('play-plate',x1-x0,y1-y0),lambda:art.forest_widget(x1-x0,y1-y0,1))
            c.create_image(x0,y0,anchor='nw',image=img,tags='dyn')
            self.text((x0+x1)//2,(y0+y1)//2,f'Play  ·  {r.value}',self.f['action_big'],'#20240f',anchor='center',shadow=False)
            r.value_rect=None
            return
        if r.kind in ('action', 'pair'):
            parts = [(r.label, r.on)] if r.kind == 'action' else r.actions
            gap = X(GAP)
            w = (x1 - x0 - gap * (len(parts) - 1)) // len(parts)
            r.parts = []
            for k, (label, cb) in enumerate(parts):
                px0 = x0 + k * (w + gap)
                px1 = px0 + w if k < len(parts) - 1 else x1
                lit = r is self.focused() and (r.kind == 'action' or getattr(r, 'pick', 0) == k)
                img = self.sprite(('label', px1 - px0, y1 - y0, lit),
                                  lambda a=px1 - px0, b=y1 - y0: art.label_cell(a, b, s, lit))
                ring = max(1, int(round(s)))
                c.create_image(px0 - ring, y0 - ring, anchor='nw', image=img, tags='dyn')
                font = self.fit_font(self.f['action_big'] if r.h >= 50 else self.f['action'], label,
                                     px1 - px0 - X(16))
                self.text((px0 + px1) // 2, (y0 + y1) // 2, label, font, P['text_bright'], anchor='center')
                r.parts.append(((px0, y0, px1, y1), label, cb))
            r.value_rect = None
            return
        lw = X(LABEL_W)
        lx1 = x0 + lw
        lit = r is self.focused()
        if lit:
            img=self.sprite(('selected-row',x1-x0,y1-y0),lambda:art.forest_widget(x1-x0,y1-y0,0))
            c.create_image(x0,y0,anchor='nw',image=img,tags='dyn')
        tx = x0 + X(16)
        if r.icon:
            size = X(32)
            ic = self.sprite(('icon', r.icon, size), lambda: art.framed_icon(size, s, r.icon))
            c.create_image(tx, (y0 + y1) // 2, anchor='w', image=ic, tags='dyn')
            tx += size + X(14)
        font = (self.f['big'] if r.icon else self.f['big_small']) if r.big else self.f['label']
        font = self.fit_font(font, r.label, lx1 - tx - X(10) - (X(32) if r.badge else 0))
        tid = self.text(tx, (y0 + y1) // 2, r.label, font, P['text_bright'] if r.big else P['text'])
        if r.badge:
            bx = self.canvas.bbox(tid)[2] + X(10)
            size = X(22)
            b = self.sprite(('badge', size), lambda: art.badge(size, s))
            c.create_image(bx, (y0 + y1) // 2, anchor='w', image=b, tags='dyn')
            c.create_text(bx + (size + 4) // 2, (y0 + y1) // 2, text='!', font=self.f['badge'],
                          fill=P['badge_text'], tags='dyn')
        vx0 = lx1
        r.value_rect = (vx0, y0, x1, y1)
        c.create_line(x0,y1-1,x1,y1-1,fill=P['value_border'],tags='dyn')
        pad = X(3) if not r.big else X(4)
        ix0, iy0, ix1, iy1 = vx0 + pad, y0 + pad, x1 - pad, y1 - pad
        cy = (y0 + y1) // 2
        cx = (vx0 + x1) // 2
        vfont = self.f['label'] if r.kind in ('toggle', 'enum', 'progress') or r.style == 'orange_arrow' \
            else self.f['value']
        if r.kind == 'toggle':
            on = bool(r.get())
            mid = (ix0 + ix1) // 2
            for k, (label, sel) in enumerate((('On', on), ('Off', not on))):
                sx0, sx1 = (ix0, mid) if k == 0 else (mid, ix1)
                if sel:
                    self.orange(sx0, iy0, sx1, iy1)
                self.canvas.create_text((sx0 + sx1) // 2, cy, text=label, font=vfont,
                                        fill=P['on_text'] if sel else P['off'], tags='dyn')
        elif r.kind == 'enum':
            self.orange(ix0, iy0, ix1, iy1)
            cw, ch = X(9), X(12)
            for left in (True, False):
                img = self.sprite(('chev', cw, ch, left, 'on'), lambda lf=left: art.chevron(cw, ch, P['on_text'], lf))
                c.create_image(ix0 + X(9) if left else ix1 - X(9), cy, anchor='w' if left else 'e', image=img,
                               tags='dyn')
            self.canvas.create_text(cx, cy, text=r.get(), font=vfont, fill=P['on_text'], tags='dyn')
        elif r.kind == 'progress':
            fw = int((ix1 - ix0) * max(0.0, min(1.0, r.fraction)))
            if fw > 2:
                self.orange(ix0, iy0, ix0 + fw, iy1)
            self.canvas.create_text(cx + 1, cy + 1, text=r.value, font=vfont, fill=P['band_shadow'], tags='dyn')
            self.canvas.create_text(cx, cy, text=r.value, font=vfont, fill=P['on_text'], tags='dyn')
        elif r.style == 'orange_arrow':
            self.orange(ix0, iy0, ix1, iy1)
            cw, ch = X(9), X(12)
            vfont = self.fit_font(vfont, r.value, (ix1 - ix0) - X(30) - cw, 0.8)
            label_w = vfont.measure(r.value)
            total = label_w + X(10) + cw
            sx = cx - total // 2
            self.canvas.create_text(sx, cy, text=r.value, font=vfont, fill=P['on_text'], anchor='w', tags='dyn')
            img = self.sprite(('chev', cw, ch, False, 'on'), lambda: art.chevron(cw, ch, P['on_text']))
            c.create_image(sx + label_w + X(10), cy, anchor='w', image=img, tags='dyn')
        elif r.style == 'swatch':   # a colour of the Custom scheme: a patch of it and its hex code
            cw, ch = X(9), X(12)
            img = self.sprite(('chev', cw, ch, False, 'dim'), lambda: art.chevron(cw, ch, P['chevron_dim']))
            c.create_image(x1 - X(16), cy, anchor='e', image=img, tags='dyn')
            tw = vfont.measure(r.value.upper())
            sw = X(26)
            sx = (vx0 + x1 - X(16) - cw - X(10)) // 2 - (sw + X(10) + tw) // 2
            c.create_rectangle(sx, cy - X(9), sx + sw, cy + X(9), fill=r.value, outline=P['value_border'], tags='dyn')
            c.create_text(sx + sw + X(10), cy, text=r.value.upper(), font=vfont, fill=P['value_text'], anchor='w',
                          tags='dyn')
        elif r.style == 'arrow':
            cw, ch = X(9), X(12)
            img = self.sprite(('chev', cw, ch, False, 'dim'), lambda: art.chevron(cw, ch, P['chevron_dim']))
            c.create_image(x1 - X(16), cy, anchor='e', image=img, tags='dyn')
            if r.value:   # a value, centred in the cell's room left of the arrow
                right = x1 - X(16) - cw - X(10)
                avail = right - vx0 - X(16)
                vfont = self.fit_font(vfont, r.value, avail, 0.8)
                c.create_text((vx0 + right) // 2, cy, text=fit(vfont, r.value, avail), font=vfont,
                              fill=P['value_text'], tags='dyn')
        else:
            color = {'normal': P['value_text'], 'dim': P['dim'], 'salmon': P['salmon']}.get(r.style, P['value_text'])
            avail = (x1 - vx0) - X(20) - (X(21) if r.check else 0)
            vfont = self.fit_font(vfont, r.value, avail, 0.8)
            label = fit(vfont, r.value, avail)
            tw = vfont.measure(label)
            sx = cx - (tw + (X(21) if r.check else 0)) // 2
            if r.check:
                size = X(14)
                ck = self.sprite(('check', size), lambda: art.check_mark(size, s))
                c.create_image(sx, cy, anchor='w', image=ck, tags='dyn')
                sx += X(21)
            self.canvas.create_text(sx, cy, text=label, font=vfont, fill=color, anchor='w', tags='dyn')

    def orange(self, x0, y0, x1, y1, tags=('dyn',)):
        c = self.canvas
        c.create_rectangle(x0, y0, x1 - 1, y1 - 1, fill=P['orange'], outline=P['orange_dark'], width=1, tags=tags)
        if x1 - x0 > 4:
            c.create_rectangle(x0 + 1, y0 + 1, x1 - 2, y1 - 2, outline=P['orange_light'], width=1, tags=tags)

    def draw_cursor(self):
        r = self.focused()
        self.cursor_frames = []
        if not r or not r.rect:
            self.cur_xy = self.cur_target = None
            return
        if r.kind == 'pair' and r.parts:
            box = r.parts[getattr(r, 'pick', 0)][0]
        else:
            box = r.rect
        x0, y0, x1, y1 = box
        frames, mg = self.glow(x1 - x0, y1 - y0)
        self.cursor_frames = frames
        self.cursor_k %= len(frames)
        # The cursor is drawn where it has got to and animate takes it on to the row (step_cursor); on a new screen,
        # while one slides in and with Animations off it stands on the row at once.
        target = (float(x0 - mg), float(y0 - mg))
        glide = self.animated() and self.cur_xy and self.cur_screen == self.screen and not self.slide
        self.cur_xy = self.cur_xy if glide else target
        self.cur_target, self.cur_screen = target, self.screen
        self.canvas.create_image(round(self.cur_xy[0]), round(self.cur_xy[1]), anchor='nw',
                                 image=frames[self.cursor_k], tags=('dyn', 'cursor'))

    def glow(self, w, h):
        key = ('glow', w, h)
        if key not in self.sprites:
            frames, mg = art.glow_frames(w, h, self.s)
            self.sprites[key] = ([art.photo(self.root, f) for f in frames], mg)
        return self.sprites[key]

    def draw_desc(self):
        r = self.focused()
        d = r.desc if r else None
        if callable(d):
            d = d()
        if not d:
            if self.message and self.screen != 'job':
                d = (self.message,)
            else:
                return
        if isinstance(d, tuple):
            d = {'text': [t for t in d if t]}
        elif self.message and self.screen in ('first_run', 'settings', 'uninstall'):
            d = dict(d)
            d['text'] = [self.message] + list(d.get('text', []))
        c, X = self.canvas, self.X
        dx, dw = DESC
        x0 = X(dx)
        w = X(dw)
        padx, pady = X(26), X(22)
        inner = w - 2 * padx
        items = []
        y = 0

        def add(kind, *args):
            items.append((kind, args))

        if d.get('title'):
            add('text', d['title'], self.f['heading'], P['text_bright'], X(14))
        for t in d.get('text', []):
            if t:
                add('text', t, self.f['desc'], P['desc_text'], X(12))
        if d.get('progress') is not None:
            add('progress', d['progress'], d.get('progress_label', ('', '')))
        if d.get('divider'):
            add('filigree')
        if d.get('subtitle'):
            add('text', d['subtitle'], self.f['sub'], P['gold'], X(8))
        for b in d.get('bullets', []):
            add('bullet', b)
        if d.get('log'):
            add('log', d['log'])
        if d.get('note'):
            add('text', d['note'], self.f['small'], P['muted'], X(4))
        # measure: draw at a provisional top, then move everything to the final top
        top0 = X(188)
        y = top0 + pady
        made = []
        for kind, args in items:
            if kind == 'text':
                text, font, fill, after = args
                tid = c.create_text(x0 + padx, y, text=text, font=font, fill=fill, anchor='nw', width=inner,
                                    tags=('dyn', 'desc'))
                bb = c.bbox(tid)
                y = bb[3] + after
                made.append(tid)
            elif kind == 'progress':
                frac, (left, right) = args
                c.create_text(x0 + padx, y, text=left, font=self.f['small'], fill=P['muted'], anchor='nw',
                              tags=('dyn', 'desc'))
                c.create_text(x0 + w - padx, y, text=right, font=self.f['small'], fill=P['muted'], anchor='ne',
                              tags=('dyn', 'desc'))
                y += self.f['small'].metrics('linespace') + X(6)
                h = X(22)
                c.create_rectangle(x0 + padx, y, x0 + w - padx - 1, y + h - 1, fill=P['value'],
                                   outline=P['value_border'], tags=('dyn', 'desc'))
                fw = int((inner - X(6)) * frac)
                if fw > 2:
                    self.orange(x0 + padx + X(3), y + X(3), x0 + padx + X(3) + fw, y + h - X(3), ('dyn', 'desc'))
                y += h + X(14)
            elif kind == 'filigree':
                img = self.sprite(('filigree', inner), lambda: art.filigree(inner, self.s)[0])
                c.create_image(x0 + padx, y + X(9), anchor='w', image=img, tags=('dyn', 'desc'))
                y += X(22)
            elif kind == 'bullet':
                (text,) = args
                c.create_rectangle(x0 + padx + X(1), y + X(8), x0 + padx + X(6), y + X(13), fill=P['bullet'],
                                   outline='', tags=('dyn', 'desc'))
                tid = c.create_text(x0 + padx + X(16), y, text=text, font=self.f['value'], fill=P['desc_text'],
                                    anchor='nw', width=inner - X(16), tags=('dyn', 'desc'))
                y = c.bbox(tid)[3] + X(8)
            elif kind == 'log':
                (lines,) = args
                lh = self.f['mono'].metrics('linespace')
                shown = [fit(self.f['mono'], ln, inner - X(24)) for ln in lines[-6:]]
                h = lh * max(len(shown), 1) + X(20)
                c.create_rectangle(x0 + padx, y, x0 + w - padx - 1, y + h - 1, fill=P['value'],
                                   outline=P['value_border'], tags=('dyn', 'desc'))
                ty = y + X(10)
                for ln in shown:
                    color = P['salmon'] if ln.lower().startswith(('error', 'stopped')) else P['log_text']
                    c.create_text(x0 + padx + X(12), ty, text=ln, font=self.f['mono'], fill=color, anchor='nw',
                                  tags=('dyn', 'desc'))
                    ty += lh
                y += h + X(14)
        height = y - top0 + pady - X(4)
        top = X(210) if height < X(200) else X(188)
        top = max(X(188), min(top, X(H - 105) - height))
        dy = top - top0
        if dy:
            c.move('desc', 0, dy)
        hb = max(X(60), height)
        key = ('desc', w, hb // 4)
        if key not in self.sprites:
            img, pad = art.desc_box(w, hb, self.s, self.see_through())
            self.sprites[key] = (art.photo(self.root, img), pad)
        pic, pad = self.sprites[key]
        bid = c.create_image(x0 - pad, top - pad, anchor='nw', image=pic, tags='dyn')
        c.tag_lower(bid, 'desc')


def parse_args(argv):
    ap = argparse.ArgumentParser(prog='OOT-PC')
    ap.add_argument('--from', dest='source', help='a folder to look in for the dump first (the setup\'s folder)')
    ap.add_argument('--continue-update', metavar='OLD', help='finish an update after the restart (from OLD)')
    ap.add_argument('--rolled-back', metavar='VERSION', help='the stub went back from VERSION')
    ap.add_argument('--uninstall', action='store_true')
    ap.add_argument('--root', help='the launcher folder (tests)')
    ap.add_argument('--selftest', action='store_true', help='draw every screen and close')
    ap.add_argument('--shots', help='with --selftest: save a PNG of each screen into this folder')
    ap.add_argument('--play', action='store_true',
                    help='start the game without the window and wait for it (the Steam shortcut, launcher/steam.py)')
    a, _unknown = ap.parse_known_args(argv)
    return a


def single_instance(layout, wait):
    """The launcher folder's lock (launcher/locks.py) for this window's life. locks.Busy, naming the holder, when
    another launcher or a command of launcher/cli.py still holds it after wait seconds (the launcher an update
    restart replaces may still be closing)."""
    return locks.FileLock(layout.instance_lock, 'the launcher window', wait=wait, where=layout.root)


def tell(text):
    """A sentence for the player before the window is up: a message box that waits for OK."""
    try:
        if os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, NAME, 0x40)
        else:
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo(NAME, text, parent=root)
            root.destroy()
    except Exception:   # no display, no toolkit: the terminal that started it
        print(text, file=sys.stderr)


def play_only(ctx):
    """--play, from the Steam shortcut (launcher/steam.py): the game starts from its folder with its console hidden, as
    Play starts it, and this process waits until it and the copies its own Restart starts have ended, so Steam shows
    the game as running all that time. None when there is no built game: the launcher's window opens instead."""
    gd = ctx.settings.get('game_dir')
    exe = Path(gd) / 'ocarina.exe' if gd else None
    if not exe or not exe.is_file():
        return None
    if saves.game_running(gd):
        ctx.log('play from Steam: the game is already running')
        return 0
    try:
        for line in mods.prepare(gd, log=ctx.log):
            pass
        waiting = mods.code_waiting(gd) if build.can_rebuild(gd) else []
    except OSError as e:
        ctx.log(f'play from Steam: mods: {e}')
        waiting = []
    if waiting:   # the window builds the mods' code in, then starts the game (App.start with --play)
        ctx.log(f'play from Steam: the game is built again first for the code of {", ".join(waiting)}')
        return None
    if mods.feature_on(gd, mods.STEAM_COOP):   # the Steam Co-op mod: Steam's library file beside the game
        steamlib.ensure(gd, ctx.layout.app / 'bin', log=ctx.log)
    try:
        proc = winsys.start_game(exe)
    except OSError as e:
        ctx.log(f'play from Steam: {exe}: {e}')
        return None
    ctx.log(f'play from Steam: {exe}')
    time.sleep(2)
    while proc.poll() is None or saves.game_running(gd):
        time.sleep(1)
    ctx.log(f'play from Steam: the game ended (code {proc.returncode})')
    return 0


def main(argv=None):
    a = parse_args(sys.argv[1:] if argv is None else argv)
    winsys.dpi_aware()
    winsys.app_id()
    from ..paths import Layout
    ctx = Context(Layout(root=a.root) if a.root else None)
    if a.play and not a.selftest:
        code = play_only(ctx)
        if code is not None:
            return code
    lock = None
    if not a.selftest:
        try:
            lock = single_instance(ctx.layout, 20 if (a.continue_update or a.rolled_back) else 2)
        except locks.Busy as e:
            # A second window on the same folder would run its own jobs into the same game folder and settings. The
            # first one may be out of sight (hidden while the game runs), so the player is told who holds the folder
            ctx.log(f'not started: {e}')
            tell(f'OOT-PC is already running: {e}.\n\nIts window may be hidden while the game runs; it comes back '
                 'when you quit the game.')
            return 0
        try:
            rebrand.run(ctx.layout, ctx.log)
        except Exception as e:   # noqa: BLE001 - the move to the new names never keeps the launcher from starting
            ctx.log(f'rebrand: {e}')
    try:
        root = tk.Tk(className='OOT-PC')
        app = App(root, ctx, a)
        app.instance_lock = lock
        if a.selftest:
            from .selftest import run
            root.after(300, lambda: run(app, a.shots))
        root.mainloop()
    finally:
        if lock:
            lock.release()
    return 0
