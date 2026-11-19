"""Build and exercise the actual settings-row navigation without game files or a GPU."""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import menu_preview


def main():
    out = ROOT / 'build' / 'menu-sections-check'
    out.mkdir(parents=True, exist_ok=True)
    menu_preview.build(out)
    cxx = shutil.which('clang++') or shutil.which('g++')
    obj = out / 'menu_sections_check.o'
    subprocess.run([cxx, '-std=gnu++17', '-O1', '-w', '-I', str(ROOT / 'runtime'),
                    '-I', str(ROOT / 'runtime/third_party/imgui'), '-c', str(Path(__file__).with_suffix('.cpp')),
                    '-o', str(obj)], check=True)
    objects = [str(out / (p.stem + '.o')) for p in menu_preview.SOURCES if p.name != 'menu_preview.cpp']
    exe = out / ('menu_sections_check.exe' if sys.platform == 'win32' else 'menu_sections_check')
    subprocess.run([cxx, str(obj), *objects, '-o', str(exe), *([] if sys.platform == 'win32' else ['-lm'])], check=True)
    subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    main()
