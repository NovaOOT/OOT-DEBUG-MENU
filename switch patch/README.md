# Switch patch

The OOT-PC debug tools as a mod for the game itself: a code module the game loads (`subsdk9`) and one IPS patch to
main per game build, for a modded console or an emulator. The game's files are not changed: the system's loader adds
the module and applies the patch when the game starts. This is a first working skeleton: a frame hook on the game's
thread and three tools, with no on-screen menu yet.

Supported builds: 1.1.1 and 1.0.0 (program `0400486024DEC000`). Both patches can be installed together; each one
applies only to its own build.

## What it does

Hold **L + R** and press a direction on the D-pad:

| D-pad | Tool |
|---|---|
| Left | keep Link's position and facing |
| Right | put Link back at the kept position, through the game's own actor warp |
| Down | write Link's position to the log |

The D-pad press also reaches the game (D-pad right calls Navi). Messages go to the debug log
(`OutputDebugString`), which emulators show in their log window, each line starting with `[OOT-PC]`. The first ones
say whether the hook is installed: `hook installed, game 1.1.1, main at ...`, then `frame hook running`.

## How it loads

1. The loader maps `subsdk9` next to the game's modules, resolves its imports (`nnMain` from main, three
   `nn::hid::GetNpadStates` functions from sdk) and calls its `DT_INIT` function, `src/module.c`
   `ootpc_module_init`, before the game starts.
2. The IPS patch whose name matches main's build id writes a 16-byte marker and the hook stub (`src/stub.S`) into
   the empty executable padding after main's code, and replaces the frame site's `ldr x8, [x0, #0xa8]` (the
   framework's per-frame update, right after its prologue) with a branch to the stub.
3. `ootpc_module_init` finds main's base (`nnMain` less its offset), tells the build by main's MOD0 header, checks
   the marker, the branch and that the hook slot is empty, then writes `ootpc_frame`'s address to the slot. The slot
   is in the mapped memory past the end of main's own bss, which nothing in the game uses.
4. Each frame the stub saves the registers, calls `ootpc_frame` when the slot is set, restores them, makes the load
   it replaced and branches back. Without the module the slot is zero and the stub only makes the load; without the
   patch the module stays off.

Every address is in `tools/builds.py`, one table per build. `tools/check.py` reads each one back from your
extracted main.

## Layout

| Path | What |
|---|---|
| `src/module.c` | the module: start, frame hook, input, the tools, the log |
| `src/horizon.h` | the system calls (QueryMemory, OutputDebugString) and the SDK functions it imports |
| `src/crt0.S`, `src/module.ld` | the module header (MOD0) and its layout |
| `src/stub.S` | the hook stub the patch writes into main |
| `tools/builds.py` | the supported builds and every address used in them |
| `tools/build.py` | builds everything into `build/switch-patch/` |
| `tools/elf2nso.py`, `tools/mkpatch.py` | the module's ELF to NSO; the IPS32 patch |
| `tools/check.py` | checks the result against the game, without a console |
| `tools/patch_nup.py`, `patch-nup.bat` | write a copy of a `.nup` with the module and the patched main in it |

## Building

Needs LLVM with the AArch64 target (clang, ld.lld, llvm-objcopy). Any recent release has it, so no Switch-specific
toolchain is needed. Python with the standard library builds; `tools/check.py` also uses capstone for its
disassembly.

```powershell
python "switch patch\tools\build.py" --llvm C:\Users\Justin\scoop\apps\llvm\current\bin
```

```powershell
python "switch patch\tools\check.py"
```

`build.py` checks every import against the main and sdk of each extracted exefs under `build\` (or `--exefs DIR`).
`check.py` applies each patch to the extracted main of its build and checks the addresses, the replaced instruction,
the padding, the stub's branches and slot, the module's MOD0, DT_INIT, relocations and exports, and that the game's
NPDM allows both system calls.

Output, all in `build\switch-patch\` (ignored by git):

| Path | What |
|---|---|
| `subsdk9` | the module |
| `<build id>.ips` | main's patch for 1.1.1 (`E0F3CCFA...`) and 1.0.0 (`33FA6C2C...`) |
| `dist\console\atmosphere\` | for a modded console's SD card: `contents\0400486024DEC000\exefs\subsdk9` and `exefs_patches\ootpc_debug\*.ips` |
| `dist\emulator\OOT-PC Debug\` | a mod folder for an emulator's mods directory for the game: `exefs\subsdk9` and the patches |

## Patching a .nup

`patch-nup.bat` writes a copy of a `.nup` dump with the debug tools in it: drag the `.nup` onto it. For each
package whose main is a supported build (the base game 1.0.0, the update 1.1.1), main gets that build's patch and
exefs gets the module as `subsdk9`; other packages are left as they are. The `.nup` given is only read. The copy goes
next to it as `... (OOT-PC debug).nup` and takes about as much space again. `NUP_DRY_RUN=1` checks everything and
writes nothing.

It needs uv, which fetches Python 3.14 and the zbic package on the first run, and LLVM to build the module (when
LLVM is missing it uses a module built before). The batch file runs `tools/build.py`, then `tools/patch_nup.py`:

- The copy is the whole `.nup`, with the patched main, the module and the package metas appended after the data,
  and the new, slightly larger table of contents written at 0x80 where the old table and metas were. The emulator
  refuses a table anywhere but between 0x80 and the start of the data (`patch_nup.py` `check_layout` applies its
  rules before writing). The old main stays in the file, unused. `patch_nup.py --repair COPY` fixes a copy made
  before this change in place, which had its table at the end.
- Only main's text segment changes. It is unpacked, patched (it grows by the stub's size into the padding before
  the read-only segment), hashed and packed again with ZBIC, so main keeps its flags. zbic's default level 3 gives
  output that does not unpack to the same bytes on this code, so the patcher uses level 9 and checks that the
  result unpacks to the same bytes. `--plain-text` stores the segment unpacked instead.
- The new table lists every file of the old one with the same entry, except main and the new module. The patcher
  checks that before writing, then reads the copy back with `tools/nup.py`.

## Not done yet

- Run on a console or an emulator. Everything above is checked against the game's files, but no loader has
  loaded it yet.
- An on-screen menu, drawn through the game's NVN.
- The other tools of the PC debug menu. Most of the game-memory ones carry over (warps, reload area, flags, items,
  health, fly, no void-out); some need a heap search (health) or the game's thread (warps), which this hook
  provides.
