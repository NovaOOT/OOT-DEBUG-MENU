"""Make the Steam Co-op mod: the mod that turns on co-op through Steam (runtime/coop.c, docs/coop.md).

The mod holds no game files: its ootpc-feature.txt names the feature (runtime/mods.h MOD_FEATURE_STEAM_COOP) and a
readme says what it does (launcher/mods.py make_feature_mod writes both). It goes into the game's mods folder like any
mod and is switched on and off there, on the launcher's Mods screen or on the menu's Mods tab.

    python tools/make_coop_mod.py --zip build/OOT-PC-Steam-Coop.zip     # the zip a player adds on the Mods screen
    python tools/make_coop_mod.py --game D:/Games/OOT-PC                # straight into that game's mods folder

Standard library only.
"""
import argparse
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from launcher import mods  # noqa: E402

NAME = mods.FEATURES[mods.STEAM_COOP]


def write_zip(dest):
    """The mod as a zip of its own folder: the Mods screen's add() unpacks it into mods/<NAME>."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        folder = mods.make_feature_mod(Path(tmp) / NAME, mods.STEAM_COOP)
        with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as z:
            for f in sorted(folder.iterdir()):
                z.write(f, f'{NAME}/{f.name}')
    return dest


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument('--zip', type=Path, help='write the mod as this zip')
    where.add_argument('--game', type=Path, help="put the mod into this game folder's mods folder")
    a = ap.parse_args()
    if a.zip:
        print(write_zip(a.zip))
    else:
        print(mods.make_feature_mod(mods.ensure_mods_dir(a.game) / NAME, mods.STEAM_COOP))


if __name__ == '__main__':
    main()
