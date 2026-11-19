"""Pack the authored menu skin from generated/menu-ui into the runtime's font atlas include.

The artwork is independent of the game. Both the launcher and the menu use the same PNG; the executable embeds
RGB565 pixels so the skin needs no image decoder or loose files when the game is started. Outputs stay in generated/.
"""
import struct
import zlib
from pathlib import Path



def png_rgb(path, alpha=False):
    data = path.read_bytes()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError(f'{path}: not a PNG')
    at, packed = 8, bytearray()
    while at < len(data):
        n = struct.unpack_from('>I', data, at)[0]
        tag, payload = data[at+4:at+8], data[at+8:at+8+n]
        if tag == b'IHDR':
            w, h, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', payload)
            if depth != 8 or color not in (2, 6) or compression or filtering or interlace:
                raise ValueError('Menu art must be an 8-bit RGB/RGBA non-interlaced PNG')
        elif tag == b'IDAT':
            packed.extend(payload)
        at += 12+n
    channels = 3 if color == 2 else 4
    stride = w*channels
    raw = zlib.decompress(packed)
    if len(raw) != h*(stride+1) or w > 4096 or h > 4096:
        raise ValueError('Invalid menu artwork size')
    previous = bytearray(stride)
    rgb = bytearray()
    for y in range(h):
        start = y*(stride+1)
        kind, row = raw[start], bytearray(raw[start+1:start+1+stride])
        if kind > 4:
            raise ValueError('Unsupported PNG filter')
        for x in range(stride):
            left = row[x-channels] if x >= channels else 0
            up, corner = previous[x], previous[x-channels] if x >= channels else 0
            if kind == 1: row[x] = (row[x]+left)&255
            elif kind == 2: row[x] = (row[x]+up)&255
            elif kind == 3: row[x] = (row[x]+(left+up)//2)&255
            elif kind == 4:
                p = left+up-corner
                a,b,c = abs(p-left),abs(p-up),abs(p-corner)
                row[x] = (row[x]+(left if a <= b and a <= c else up if b <= c else corner))&255
        if alpha and channels == 4: rgb.extend(row)
        elif alpha:
            for x in range(0,stride,3): rgb.extend(row[x:x+3]+b'\xff')
        elif channels == 3: rgb.extend(row)
        else:
            for x in range(0,stride,4): rgb.extend(row[x:x+3])
        previous = row
    return w,h,rgb


def build(root=None):
    from check_content import GENERATED_MARKER
    root = Path(root) if root else Path(__file__).resolve().parents[1]
    folder = root/'generated'/'menu-ui'
    source, output = folder/'forest-chassis.png', folder/'forest_pixels.h'
    if not source.is_file():
        return False
    widgets=folder/'forest-widgets.png'
    if output.is_file() and output.stat().st_mtime > max(source.stat().st_mtime,Path(__file__).stat().st_mtime,
                                                       widgets.stat().st_mtime if widgets.is_file() else 0):
        return True
    w,h,rgb = png_rgb(source)
    with output.open('w',encoding='ascii') as f:
        f.write(f'// {GENERATED_MARKER}\n// Authored menu artwork; no game data. Built by tools/build_menu_art.py.\n')
        f.write(f'static const int kForestW={w}, kForestH={h};\nstatic const unsigned short kForestPixels[]={{\n')
        line=[]
        for x in range(0,len(rgb),3):
            r,g,b=rgb[x:x+3]
            line.append(f'0x{((r>>3)<<11)|((g>>2)<<5)|(b>>3):04x},')
            if len(line)==24:
                f.write(''.join(line)+'\n');line=[]
        f.write(''.join(line)+'\n};\n')
        _,_,rgba=png_rgb(source,True)
        f.write('static const unsigned char kForestAlpha[]={\n')
        for x in range(0,w*h,48):
            f.write(','.join(str(rgba[k*4+3]) for k in range(x,min(x+48,w*h)))+',\n')
        f.write('};\n')
        if widgets.is_file():
            w,h,rgba=png_rgb(widgets,True)
            f.write(f'static const int kWidgetsW={w}, kWidgetsH={h};\nstatic const unsigned int kWidgetsPixels[]={{\n')
            line=[]
            for x in range(0,len(rgba),4):
                r,g,b,a=rgba[x:x+4]
                line.append(f'0x{a:02x}{b:02x}{g:02x}{r:02x},')
                if len(line)==16:
                    f.write(''.join(line)+'\n');line=[]
            f.write(''.join(line)+'\n};\n#define OOTPC_WIDGET_ART 1\n')
    return True


if __name__ == '__main__':
    if not build():
        raise SystemExit('Menu art not found: generated/menu-ui/forest-chassis.png')
