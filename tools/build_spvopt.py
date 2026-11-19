"""Build ootpc_spvopt.dll, the SPIR-V optimiser runtime/spirv_opt.c loads, from the Vulkan SDK's SPIRV-Tools libraries.

    python tools/build_spvopt.py [--sdk <Vulkan SDK folder>] [--out build/spvopt]

The Vulkan SDK ships SPIRV-Tools' optimiser only as static libraries built with MSVC (SPIRV-Tools-opt.lib and
SPIRV-Tools.lib, dynamic C runtime), so this links them into one DLL with Visual Studio's linker (the Build Tools are
enough; vswhere finds them) and exports the optimiser's C interface (include/spirv-tools/libspirv.h) from it. The
libraries are SPIRV-Tools, Apache-2.0: that licence's text goes beside the DLL. The DLL needs the Visual C++
runtime; its three DLLs (msvcp140, vcruntime140, vcruntime140_1) are copied from the Build Tools' redistributable
folder beside it, so a machine without the runtime installed can load it from the game's folder.
tools/make_game.py place_spvopt puts the folder's files beside the game.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VSWHERE = Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Microsoft Visual Studio' / 'Installer' / 'vswhere.exe'

# what runtime/spirv_opt.c looks up
EXPORTS = ('spvOptimizerCreate', 'spvOptimizerDestroy', 'spvOptimizerSetMessageConsumer',
           'spvOptimizerRegisterPassesFromFlagsWhilePreservingTheInterface', 'spvOptimizerRun',
           'spvOptimizerOptionsCreate', 'spvOptimizerOptionsDestroy', 'spvOptimizerOptionsSetRunValidator',
           'spvBinaryDestroy')
CRT = ('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')


def vs_path():
    if not VSWHERE.is_file():
        sys.exit('Visual Studio (or its Build Tools) is not installed: vswhere.exe is missing')
    path = subprocess.run([str(VSWHERE), '-latest', '-products', '*', '-requires',
                           'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath'],
                          capture_output=True, text=True).stdout.strip()
    bat = Path(path) / 'VC' / 'Auxiliary' / 'Build' / 'vcvars64.bat'
    if not bat.is_file():
        sys.exit(f'no vcvars64.bat under {path or "(no Visual Studio with the C++ tools)"}')
    return Path(path), bat


def crt_dir(vs: Path):
    """The newest x64 Microsoft.VC*.CRT folder of the Build Tools' redistributables, or None."""
    found = sorted((vs / 'VC' / 'Redist' / 'MSVC').glob('*/x64/Microsoft.VC*.CRT'))
    found = [d for d in found if all((d / n).is_file() for n in CRT)]
    return found[-1] if found else None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--sdk', type=Path, default=Path(os.environ['VULKAN_SDK']) if os.environ.get('VULKAN_SDK') else None,
                    help='the Vulkan SDK folder (holds Lib/SPIRV-Tools-opt.lib); default %%VULKAN_SDK%%')
    ap.add_argument('--out', type=Path, default=ROOT / 'build' / 'spvopt')
    a = ap.parse_args()
    if not a.sdk:
        sys.exit('no Vulkan SDK: pass --sdk or set VULKAN_SDK')
    libs = [a.sdk / 'Lib' / 'SPIRV-Tools-opt.lib', a.sdk / 'Lib' / 'SPIRV-Tools.lib']
    for lib in libs:
        if not lib.is_file():
            sys.exit(f'{lib} is missing')
    a.out.mkdir(parents=True, exist_ok=True)
    vs, bat = vs_path()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'spvopt.def').write_text('LIBRARY ootpc_spvopt\nEXPORTS\n' + ''.join(f'    {e}\n' for e in EXPORTS))
        cmd = (f'call "{bat}" >nul && link /nologo /DLL /MACHINE:X64 /OPT:REF /OPT:ICF /DEF:"{tmp / "spvopt.def"}" '
               f'/OUT:"{a.out / "ootpc_spvopt.dll"}" /IMPLIB:"{tmp / "ootpc_spvopt.lib"}" '
               + ' '.join(f'"{lib}"' for lib in libs))
        script = tmp / 'build.cmd'
        # vcvars64.bat runs vswhere itself and needs it on PATH
        script.write_text('@echo off\r\nset "PATH=%PATH%;' + str(VSWHERE.parent) + '"\r\n' + cmd + '\r\n')
        r = subprocess.run(['cmd', '/c', str(script)])
    if r.returncode or not (a.out / 'ootpc_spvopt.dll').is_file():
        sys.exit('the link failed')
    crt = crt_dir(vs)
    if crt:
        for n in CRT:
            shutil.copy2(crt / n, a.out / n)
    else:
        print('build_spvopt: no Visual C++ redistributable folder found: the DLL needs the runtime installed')
    # The SDK carries no licence text for SPIRV-Tools: the Apache-2.0 text the repository already has (the menu
    # font's) is the same licence.
    shutil.copy2(ROOT / 'runtime' / 'ui' / 'fonts' / 'LICENSE-Roboto.txt', a.out / 'SPIRV-Tools-LICENSE.txt')
    print(f'build_spvopt: {a.out / "ootpc_spvopt.dll"}'
          f' ({(a.out / "ootpc_spvopt.dll").stat().st_size // 1024} KiB){", with the C++ runtime" if crt else ""}')


if __name__ == '__main__':
    main()
