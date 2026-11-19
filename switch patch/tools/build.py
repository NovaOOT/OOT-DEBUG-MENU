"""Build the Switch patch: the module (subsdk9) and one IPS patch per game build, into build/switch-patch/.

    python "switch patch/tools/build.py" [--llvm DIR] [--exefs DIR ...]

Needs LLVM (clang, ld.lld, llvm-objcopy) with the AArch64 target; any recent release has it. --llvm names its bin
folder; without it the tools are looked for on PATH, then in the usual install places (build.py llvm_dir). --exefs names an extracted exefs folder of the game (main, sdk):
with one, every symbol the module imports is checked against what that game's main and sdk export, as the loader
would resolve them. By default the build/*/exefs folders are used. Standard library only.

Output (build/switch-patch/):
    subsdk9                  the module
    <build id>.ips           main's patch for each build in tools/builds.py
    dist/console/            the files for a modded console's SD card (atmosphere/...)
    dist/emulator/           a mod folder for an emulator's mods directory
"""

import argparse
import shutil
import struct
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

import builds    # noqa: E402
import elf2nso   # noqa: E402
import mkpatch   # noqa: E402

SRC = TOOLS.parent / 'src'
REPO = TOOLS.parent.parent
OUT = REPO / 'build' / 'switch-patch'
MOD_NAME = 'OOT-PC Debug'

CFLAGS = ['--target=aarch64-none-elf', '-march=armv8-a', '-std=gnu11', '-O2', '-g0', '-Wall', '-Wextra',
          '-ffreestanding', '-fPIC', '-fvisibility=hidden', '-fno-plt', '-fno-common', '-fno-stack-protector',
          '-fno-math-errno', '-fno-asynchronous-unwind-tables', '-fno-unwind-tables', '-ffunction-sections',
          '-fdata-sections']
LDFLAGS = ['-shared', '-nostdlib', '-T', str(SRC / 'module.ld'), '--gc-sections', '-z', 'text',
           '-z', 'max-page-size=0x1000', '--hash-style=sysv', '-Bsymbolic', '--build-id=sha1',
           '-init=ootpc_module_init', '--no-undefined-version']


def fail(msg):
    sys.exit(f'build: {msg}')


def llvm_dir():
    """The first folder that holds all three LLVM tools: PATH, then the usual install places."""
    exe = '.exe' if sys.platform == 'win32' else ''
    names = ('clang', 'ld.lld', 'llvm-objcopy')
    found = [shutil.which(n) for n in names]
    if all(found) and len({Path(p).parent for p in found}) == 1:
        return Path(found[0]).parent
    home = Path.home()
    for d in (home / 'scoop' / 'apps' / 'llvm' / 'current' / 'bin', Path(r'C:\Program Files\LLVM\bin'),
              Path(r'C:\msys64\clang64\bin'), Path('/usr/bin'), Path('/opt/homebrew/opt/llvm/bin')):
        if all((d / (n + exe)).is_file() for n in names):
            return d
    return None


def tool(llvm, name):
    p = Path(llvm) / (name + ('.exe' if sys.platform == 'win32' else ''))
    if not p.is_file():
        fail(f'{p} not found')
    return str(p)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.stderr.write(r.stdout + r.stderr)
        fail(f'{Path(cmd[0]).name} failed')
    if r.stderr.strip():
        sys.stderr.write(r.stderr)


def builds_header(path):
    rows = []
    for name, b in builds.BUILDS.items():
        rows.append(f'    {{"{name}", {b["code"]}, {b["mod0"]:#x}, {b["nnmain"]:#x}, {builds.stub_addr(b):#x}, '
                    f'{b["frame_site"]:#x}, {mkpatch.site_word(b):#x}, {builds.slot_addr(b):#x}, '
                    f'{b["player_slot"]:#x}, {b["warp_actor"]:#x}}}, \\')
    path.write_text('// Made by switch patch/tools/build.py from tools/builds.py: the game builds the module knows.\n'
                    '// name, code, mod0, nnmain, marker, site, site_word, slot, player_slot, warp_actor\n'
                    '#define OOTPC_BUILDS \\\n' + '\n'.join(rows) + '\n\n', encoding='ascii')


def dynsyms(elf, secs=None):
    """(name, defined) of each dynamic symbol of an ELF."""
    secs = secs or elf2nso.sections(elf)
    _, sym_off, sym_size = secs['.dynsym']
    _, str_off, _ = secs['.dynstr']
    for i in range(1, sym_size // 24):
        name, _info, _other, shndx = struct.unpack_from('<IBBH', elf, sym_off + i * 24)
        yield elf[str_off + name:elf.index(b'\0', str_off + name)].decode(), shndx != 0


def nso_exports(path):
    """The names an NSO exports, from its dynamic symbol table (its segments may be stored compressed only in a raw
    dump; the build's extracted exefs stores them plainly)."""
    raw = path.read_bytes()
    if raw[:4] != b'NSO0' or struct.unpack_from('<I', raw, 0xC)[0] & 7:
        return None
    ro_file, ro_mem, ro_size = struct.unpack_from('<III', raw, 0x20)
    str_off, _ = struct.unpack_from('<II', raw, 0x90)
    sym_off, sym_size = struct.unpack_from('<II', raw, 0x98)
    ro = raw[ro_file:ro_file + ro_size]
    out = set()
    for i in range(1, sym_size // 24):
        name, _info, _other, shndx = struct.unpack_from('<IBBH', ro, sym_off + i * 24)
        if shndx:
            out.add(ro[str_off + name:ro.index(b'\0', str_off + name)].decode())
    return out


def check_imports(elf, exefs_dirs):
    imports = sorted(n for n, defined in dynsyms(elf) if not defined)
    exported = [n for n, defined in dynsyms(elf) if defined]
    if exported:
        fail(f'the module exports {exported}: it would take those names from the game\'s own modules')
    checked = 0
    for d in exefs_dirs:
        main, sdk = d / 'main', d / 'sdk'
        if not (main.is_file() and sdk.is_file()):
            continue
        have = (nso_exports(main) or set()) | (nso_exports(sdk) or set())
        if not have:
            continue
        missing = [n for n in imports if n not in have]
        if missing:
            fail(f'{d}: the game does not export {missing}')
        checked += 1
    print(f'imports: {len(imports)} ({", ".join(imports)}); checked against {checked} extracted exefs')


def main():
    ap = argparse.ArgumentParser(description='Build the Switch patch.')
    ap.add_argument('--llvm', help='the LLVM bin folder (clang, ld.lld, llvm-objcopy)')
    ap.add_argument('--exefs', action='append', type=Path, help='an extracted exefs folder to check imports against')
    args = ap.parse_args()
    llvm = args.llvm or llvm_dir()
    if not llvm:
        fail('no LLVM found (clang, ld.lld, llvm-objcopy); install it (scoop install llvm) or pass --llvm')
    clang, lld, objcopy = (tool(llvm, n) for n in ('clang', 'ld.lld', 'llvm-objcopy'))
    exefs_dirs = args.exefs or sorted(p for p in (REPO / 'build').glob('*/exefs') if p.is_dir())

    obj = OUT / 'obj'
    obj.mkdir(parents=True, exist_ok=True)
    builds_header(obj / 'builds.h')

    # the module
    objs = []
    for src in ('crt0.S', 'module.c'):
        o = obj / (src + '.o')
        run([clang, *CFLAGS, '-I', str(obj), '-c', str(SRC / src), '-o', str(o)])
        objs.append(str(o))
    elf_path = obj / 'module.elf'
    run([lld, *LDFLAGS, '-o', str(elf_path), *objs])
    elf = elf_path.read_bytes()
    check_imports(elf, exefs_dirs)
    nso, module_id = elf2nso.convert(elf)
    (OUT / 'subsdk9').write_bytes(nso)
    print(f'subsdk9: {len(nso)} bytes, module id {module_id[:20].hex().upper()}')

    # one patch per build
    stub_obj = obj / 'stub.o'
    run([clang, '--target=aarch64-none-elf', '-c', str(SRC / 'stub.S'), '-o', str(stub_obj)])
    patches = []
    for name, b in builds.BUILDS.items():
        stub_elf, stub_bin = obj / f'stub-{name}.elf', obj / f'stub-{name}.bin'
        run([lld, '-o', str(stub_elf), str(stub_obj), '-e', '_start', f'-Ttext={builds.stub_addr(b):#x}',
             f'--defsym=hook_slot={builds.slot_addr(b):#x}', f'--defsym=frame_return={b["frame_site"] + 4:#x}',
             f'--defsym=build_code={b["code"]}'])
        run([objcopy, '-O', 'binary', '--only-section=.text', str(stub_elf), str(stub_bin)])
        ips = OUT / f'{b["build_id"]}.ips'
        ips.write_bytes(mkpatch.make(b, stub_bin.read_bytes()))
        patches.append(ips)
        print(f'{ips.name}: game {name}, stub {stub_bin.stat().st_size:#x} bytes at main+{builds.stub_addr(b):#x}, '
              f'hook at main+{b["frame_site"]:#x}')

    # install trees
    dist = OUT / 'dist'
    if dist.exists():
        shutil.rmtree(dist)
    console = dist / 'console' / 'atmosphere'
    emulator = dist / 'emulator' / MOD_NAME / 'exefs'
    for d in (console / 'contents' / builds.PROGRAM_ID / 'exefs', emulator):
        d.mkdir(parents=True)
        shutil.copy2(OUT / 'subsdk9', d / 'subsdk9')
    for d in (console / 'exefs_patches' / 'ootpc_debug', emulator):
        d.mkdir(parents=True, exist_ok=True)
        for p in patches:
            shutil.copy2(p, d / p.name)
    print(f'install trees in {dist}')


if __name__ == '__main__':
    main()
