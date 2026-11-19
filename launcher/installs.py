"""The game versions installed side by side (setting `installs`): each version of Ocarina of Time the player built has
a folder of its own, so it keeps its own saves (profile/, saves/), its own OOT-PC.ini, mods and build. The setting
`game_dir` is the selected one's folder, and everything else in the launcher (Play, Saves, Mods, the Save Tree, a
rebuild or an update) acts on that folder alone.

Each entry is {'version': '1.1.1', 'dir': 'C:\\...'}. A launcher from before versions knew one game folder: that folder
is the 1.1.1 install (the only version it could build), added the first time the list is read."""
import json
import os
from pathlib import Path

DEFAULT_VERSION = '1.1.1'   # the update, and what every build before game versions made (tools/make_game.py)


def _key(version):
    """Sorts versions newest first: '1.1.1' before '1.0.0'."""
    try:
        return tuple(-int(x) for x in version.split('.'))
    except ValueError:
        return (0,)


def _same(a, b):
    try:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    except (TypeError, ValueError):
        return False


def entries(settings):
    """The installs, newest version first, each version once. The game folder chosen before versions existed becomes
    the 1.1.1 install. Changes the settings (not saved) when it had to tidy them; returns the list."""
    seen, out = set(), []
    for e in settings.get('installs') or []:
        if (isinstance(e, dict) and isinstance(e.get('version'), str) and isinstance(e.get('dir'), str) and e['dir']
                and e['version'] not in seen):
            seen.add(e['version'])
            out.append({'version': e['version'], 'dir': e['dir']})
    gd = settings.get('game_dir')
    if gd and not any(_same(e['dir'], gd) for e in out):
        version = DEFAULT_VERSION if DEFAULT_VERSION not in seen else built_game_version(gd)
        if version and version not in seen:
            out.append({'version': version, 'dir': gd})
    out.sort(key=lambda e: _key(e['version']))
    if out != settings.get('installs'):
        settings['installs'] = out
    return out


def selected(settings):
    """The install whose folder is game_dir, or None (no game installed yet)."""
    gd = settings.get('game_dir')
    return next((e for e in entries(settings) if gd and _same(e['dir'], gd)), None)


def selected_version(settings):
    e = selected(settings)
    return e['version'] if e else DEFAULT_VERSION


def find(settings, version):
    return next((e for e in entries(settings) if e['version'] == version), None)


def select(settings, version):
    """Makes version's install the one the launcher acts on. False when that version is not installed."""
    e = find(settings, version)
    if not e:
        return False
    settings['game_dir'] = e['dir']
    settings.save()
    return True


def add(settings, version, game_dir):
    """Records game_dir as version's install (replacing an older folder of that version) and selects it."""
    out = [e for e in entries(settings) if e['version'] != version and not _same(e['dir'], game_dir)]
    out.append({'version': version, 'dir': str(game_dir)})
    out.sort(key=lambda e: _key(e['version']))
    settings['installs'] = out
    settings['game_dir'] = str(game_dir)
    settings.save()


def forget(settings, version):
    """Drops version from the list; its folder stays on disk. The selection moves to the newest version left."""
    out = [e for e in entries(settings) if e['version'] != version]
    settings['installs'] = out
    if not any(_same(e['dir'], settings.get('game_dir') or '') for e in out):
        settings['game_dir'] = out[0]['dir'] if out else ''
    settings.save()


def default_dir(base_dir, version):
    """Where a newly installed version goes: beside the selected game folder, named after it and the version
    (C:\\Games\\OOT-PC -> C:\\Games\\OOT-PC-1.0.0). base_dir: that folder, or the default one."""
    base = Path(base_dir)
    return str(base.with_name(f'{base.name}-{version}'))


def built_game_version(game_dir):
    """The game version a folder's last build checked (build-cache/state.json, 'game' of its check step), else the
    default: every build before versions were recorded was 1.1.1."""
    try:
        state = json.loads((Path(game_dir) / 'build-cache' / 'state.json').read_text(encoding='utf-8'))
        v = state.get('game')
        return v if isinstance(v, str) and v else DEFAULT_VERSION
    except (OSError, ValueError, AttributeError):
        return DEFAULT_VERSION
