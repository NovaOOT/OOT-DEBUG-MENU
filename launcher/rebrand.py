"""An install from before the project was renamed to OOT-PC, brought to the new names at the launcher's start.

Such an install keeps its folder (%LOCALAPPDATA%\\Programs\\OOT-DEBUG-MENU, usually); moving a folder the running
launcher works from is not worth the risk. An update brings the new stub inside the release (app/<version>/native/
OOT-PC.exe, tools/make_launcher.py), which goes beside the old OOT-DEBUG-MENU.exe, and then runs once with --rebrand
(launcher/native/common.h rebrand): the Start menu and Desktop shortcuts and the entry in Settings > Apps take the new
name and point at OOT-PC.exe. The old stub stays, so a taskbar pin to it keeps working; uninstall removes both
(launcher/winsys.py OWNED). data/rebranded.txt records that it is done.
"""
import os
import shutil

from . import winsys


def needed(layout):
    return (os.name == 'nt' and not layout.dev and layout.legacy_stub.is_file()
            and not (layout.data / 'rebranded.txt').is_file())


def run(layout, log=print):
    """Do what is left of the move to the new names; a step that fails is tried again at the next start."""
    if not needed(layout):
        return
    new = layout.root / 'OOT-PC.exe'
    if not new.is_file():
        if not layout.app_stub.is_file():
            log('rebrand: this release carries no OOT-PC.exe; the shortcuts keep their old name')
            return
        try:
            shutil.copy2(layout.app_stub, new)
        except OSError as e:
            log(f'rebrand: OOT-PC.exe could not be put into {layout.root}: {e}')
            return
    layout.stub = new
    if not winsys.stub_run(layout, ['--rebrand']):
        log('rebrand: the shortcuts and the Settings > Apps entry could not be renamed; trying again next start')
        return
    try:
        layout.data.mkdir(parents=True, exist_ok=True)
        (layout.data / 'rebranded.txt').write_text('OOT-DEBUG-MENU -> OOT-PC\n', encoding='utf-8')
    except OSError as e:
        log(f'rebrand: {e}')
        return
    log('rebrand: the shortcuts and the Settings > Apps entry now name OOT-PC')
