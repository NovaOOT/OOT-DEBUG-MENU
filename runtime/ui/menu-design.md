# OOT-PC interface artwork

The menu and launcher use the same authored artwork in `generated/menu-ui/`:

- `forest-chassis.png`: the gold frame, opaque green panels and ocarina illustrations. Everything outside the frame
  is transparent. The game remains visible there; the Windows launcher uses a shaped, tightly fitted window.
- `forest-widgets.png`: the emerald selection plate, gold primary button and ornamental divider. Controls draw live
  text above these nine-sliced sprites, so settings, translations and input bindings remain interactive.

Keep these originals with the workspace. Release packaging explicitly carries the two PNGs. They contain original
interface artwork, not extracted game data. Do not substitute a screenshot with settings text baked into it.

`tools/build_menu_art.py` packs the artwork into `generated/menu-ui/forest_pixels.h`. Both game build paths run it;
the generated header is included by `menu_skin.cpp` and its pixels are uploaded as custom font-atlas rectangles.
The executable therefore needs no loose UI images. The launcher loads the PNGs directly.

`python tools/menu_preview.py` draws the actual shared menu chrome and setting rows to `build/menu-preview/`.
Use `--size 1280x720 --out build/menu-preview-720` to inspect a smaller window. The preview uses representative
settings and a stand-in font; the running game still decides which settings are available.

`python -m launcher --selftest --shots build/launcher-preview` exercises the launcher's screens without starting
the game or running installation jobs. Its PNG captures preserve the window's transparent outline.
