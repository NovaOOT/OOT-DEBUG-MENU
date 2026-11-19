"""Check the built Switch patch (build/switch-patch/) against the game, without a console.

    python "switch patch/tools/check.py" [--exefs DIR ...]

For the module (subsdk9): the NSO header, the MOD0 header the loader reads, a DT_INIT inside the code, only
relocation types the loader handles, no exports. For each IPS patch whose game build has an extracted exefs (by
default build/*/exefs): every address in tools/builds.py read back from that main (its build id, MOD0, nnMain, the end
of its code, bss and mapping), the frame site's instruction before the patch, padding that was empty, and the
patched code disassembled with its branches and the slot load followed to where tools/builds.py says they go. Also
that the game's NPDM allows the two system calls the module makes.

Needs capstone for the disassembly (pip install capstone); without it that part is skipped.
"""

import argparse
import struct
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

import builds   # noqa: E402
import mkpatch  # noqa: E402

REPO = TOOLS.parent.parent
OUT = REPO / 'build' / 'switch-patch'

DT_NULL, DT_INIT, DT_RELA, DT_RELASZ, DT_TEXTREL, DT_JMPREL, DT_PLTRELSZ, DT_FLAGS = 0, 12, 7, 8, 22, 23, 2, 30
LOADER_RELOCS = {257: 'ABS64', 1025: 'GLOB_DAT', 1026: 'JUMP_SLOT', 1027: 'RELATIVE'}
SVCS = {0x06: 'QueryMemory', 0x27: 'OutputDebugString'}

problems = []


def bad(msg):
    problems.append(msg)
    print(f'  FAIL {msg}')


def ok(msg):
    print(f'  ok   {msg}')


def image(nso):
    """The NSO's segments at their addresses (stored plainly), and its header fields."""
    flags = struct.unpack_from('<I', nso, 0xC)[0]
    segs = [struct.unpack_from('<III', nso, 0x10 + i * 0x10) for i in range(3)]
    if flags & 7:
        raise ValueError('compressed segments; check the extracted exefs, which stores them plainly')
    end = segs[2][1] + segs[2][2]
    img = bytearray(end)
    for off, addr, size in segs:
        img[addr:addr + size] = nso[off:off + size]
    bss = struct.unpack_from('<I', nso, 0x3C)[0]
    return img, segs, bss, nso[0x40:0x54].hex().upper(), flags


def dynamic(img, at):
    tags = {}
    while True:
        tag, val = struct.unpack_from('<qQ', img, at)
        if tag == DT_NULL:
            return tags
        tags.setdefault(tag, []).append(val)
        at += 16


def check_module(path):
    print(f'{path.name}:')
    nso = path.read_bytes()
    if nso[:4] != b'NSO0':
        return bad('not an NSO')
    img, segs, bss, mid, flags = image(nso)
    (_, t_addr, t_size), (_, r_addr, r_size), (_, d_addr, d_size) = segs
    if t_addr or r_addr % 0x1000 or d_addr % 0x1000 or t_addr + t_size > r_addr or r_addr + r_size > d_addr:
        bad('segments are not at 0 and on pages in order')
    else:
        ok(f'text {t_size:#x}, ro {r_size:#x} at {r_addr:#x}, data {d_size:#x} at {d_addr:#x}, bss {bss:#x}, id {mid}')
    if (d_addr + d_size + bss) % 0x1000:
        bad('data and bss do not end on a page')
    mod0 = struct.unpack_from('<I', img, 4)[0]
    if img[mod0:mod0 + 4] != b'MOD0':
        return bad(f'no MOD0 at the offset the code gives ({mod0:#x})')
    dyn_rel, bss_s, bss_e, eh_s, eh_e, obj = struct.unpack_from('<6i', img, mod0 + 4)
    ok(f'MOD0 at {mod0:#x}: dynamic {mod0 + dyn_rel:#x}, bss {mod0 + bss_s:#x}-{mod0 + bss_e:#x}, '
       f'module object {mod0 + obj:#x}')
    end = d_addr + d_size + bss
    if not (d_addr <= mod0 + bss_s <= mod0 + obj < mod0 + obj + 0x200 <= mod0 + bss_e <= end):
        bad('the module object is not inside the bss')
    tags = dynamic(img, mod0 + dyn_rel)
    init = tags.get(DT_INIT, [None])[0]
    if init is None or not t_addr <= init < t_addr + t_size:
        bad(f'no DT_INIT inside the code ({init})')
    else:
        ok(f'DT_INIT at {init:#x}')
    if DT_TEXTREL in tags or tags.get(DT_FLAGS, [0])[0] & 4:
        bad('relocations in the code')
    kinds = {}
    for t_off, t_size_tag in ((DT_RELA, DT_RELASZ), (DT_JMPREL, DT_PLTRELSZ)):
        if t_off in tags:
            off, size = tags[t_off][0], tags[t_size_tag][0]
            for k in range(size // 24):
                r_off, r_info, _ = struct.unpack_from('<QQq', img, off + k * 24)
                kind = r_info & 0xFFFFFFFF
                kinds[kind] = kinds.get(kind, 0) + 1
                if not d_addr <= r_off < end:
                    bad(f'a relocation writes outside the data segment ({r_off:#x})')
    unknown = [k for k in kinds if k not in LOADER_RELOCS]
    if unknown:
        bad(f'relocation types the loader may not handle: {unknown}')
    else:
        ok('relocations: ' + ', '.join(f'{LOADER_RELOCS[k]} x{n}' for k, n in sorted(kinds.items())))
    str_off, _ = struct.unpack_from('<II', nso, 0x90)
    sym_off, sym_size = struct.unpack_from('<II', nso, 0x98)
    exports = []
    for i in range(1, sym_size // 24):
        name, _info, _other, shndx = struct.unpack_from('<IBBH', img, r_addr + sym_off + i * 24)
        if shndx:
            exports.append(img[r_addr + str_off + name:img.index(b'\0', r_addr + str_off + name)].decode())
    if exports:
        bad(f'exports {exports}')
    else:
        ok(f'{sym_size // 24 - 1} imports, no exports')


def find_main(exefs_dirs, build_id):
    for d in exefs_dirs:
        p = d / 'main'
        if p.is_file():
            with open(p, 'rb') as f:
                head = f.read(0x60)
            if head[:4] == b'NSO0' and head[0x40:0x54].hex().upper() == build_id:
                return d
    return None


def apply_ips(img, ips):
    writes = mkpatch.records(ips)
    for addr, data in writes:
        img[addr:addr + len(data)] = data
    return writes


def exports_of(img, nso):
    r_addr = struct.unpack_from('<I', nso, 0x24)[0]
    str_off, _ = struct.unpack_from('<II', nso, 0x90)
    sym_off, sym_size = struct.unpack_from('<II', nso, 0x98)
    out = {}
    for i in range(1, sym_size // 24):
        name, _info, _other, shndx, value = struct.unpack_from('<IBBHQ', img, r_addr + sym_off + i * 24)
        if shndx:
            out[img[r_addr + str_off + name:img.index(b'\0', r_addr + str_off + name)].decode()] = value
    return out


def svcs_allowed(npdm):
    aci_off = struct.unpack_from('<I', npdm, 0x70)[0]
    caps_off, caps_size = struct.unpack_from('<II', npdm, aci_off + 0x30)
    allowed = set()
    for c in struct.unpack_from(f'<{caps_size // 4}I', npdm, aci_off + caps_off):
        if (~c & (c + 1)).bit_length() - 1 == 4:   # four low bits set: a system call mask
            allowed |= {(c >> 29) * 24 + b for b in range(24) if c >> (5 + b) & 1}
    return allowed, struct.unpack_from('<Q', npdm, aci_off + 0x10)[0]


def check_patch(name, b, exefs, cs):
    print(f'{b["build_id"]}.ips (game {name}, {exefs}):')
    nso = (exefs / 'main').read_bytes()
    img, segs, bss, _, _ = image(nso)
    img += bytes(b['image_end'] - len(img))
    text_end = segs[0][1] + segs[0][2]
    image_end = segs[2][1] + segs[2][2] + bss
    mod0 = struct.unpack_from('<I', img, 4)[0]
    bss_end = mod0 + struct.unpack_from('<i', img, mod0 + 12)[0]
    nnmain = exports_of(img, nso).get('nnMain')
    for field, have in (('text_end', text_end), ('image_end', image_end), ('mod0', mod0), ('bss_end', bss_end),
                        ('nnmain', nnmain)):
        if have != b[field]:
            bad(f'{field}: main has {have:#x}, tools/builds.py {b[field]:#x}')
    ok('builds.py addresses match this main (code end, bss end, mapping end, MOD0, nnMain)')
    site = b['frame_site']
    if struct.unpack_from('<I', img, site)[0] != b['frame_word']:
        bad(f'main+{site:#x} does not hold {b["frame_word"]:#010x}')
    else:
        ok(f'main+{site:#x} holds {b["frame_word"]:#010x} before the patch')
    before = bytes(img)
    writes = apply_ips(img, (OUT / f'{b["build_id"]}.ips').read_bytes())
    stub = builds.stub_addr(b)
    for addr, data in writes:
        if addr == site:
            continue
        if addr != stub or any(before[addr:addr + len(data)]) or addr + len(data) > (text_end + 0xFFF) & ~0xFFF:
            bad(f'a write at main+{addr:#x} ({len(data):#x} bytes) is not into empty padding after the code')
        else:
            ok(f'stub: {len(data):#x} bytes into empty padding at main+{addr:#x}')
    slot = builds.slot_addr(b)
    if not bss_end <= slot < image_end - 8:
        bad(f'the slot main+{slot:#x} is not between the bss end and the mapping end')
    else:
        ok(f'slot main+{slot:#x}: {slot - bss_end:#x} bytes past main\'s bss, inside its mapping')
    if cs is None:
        return print('  (capstone missing: disassembly skipped)')
    code = bytes(img[stub + 16:stub + 0x100])
    ins = list(cs.disasm(code, stub + 16))
    first = next(cs.disasm(bytes(img[site:site + 4]), site))
    print(f'    main+{site:#x}: {first.mnemonic} {first.op_str}')
    if first.mnemonic != 'b' or first.operands[0].imm != stub + 16:
        bad('the frame site does not branch to the stub')
    adrp = next(i for i in ins if i.mnemonic == 'adrp')
    ldr = ins[ins.index(adrp) + 1]
    loaded = adrp.operands[1].imm + ldr.operands[1].mem.disp
    last = next(i for i in ins if i.mnemonic == 'b')
    before_last = ins[ins.index(last) - 1]
    for i in ins[:ins.index(last) + 1]:
        print(f'    main+{i.address:#x}: {i.mnemonic} {i.op_str}')
    if loaded != slot:
        bad(f'the stub loads main+{loaded:#x}, not the slot')
    if last.operands[0].imm != site + 4:
        bad('the stub does not branch back after the site')
    if f'{before_last.mnemonic} {before_last.op_str}' != 'ldr x8, [x0, #0xa8]':
        bad('the stub does not make the replaced load before going back')
    if not problems:
        ok('the site branches to the stub, the stub reads the slot, makes the replaced load and goes back')


def main():
    ap = argparse.ArgumentParser(description='Check the built Switch patch against the game.')
    ap.add_argument('--exefs', action='append', type=Path, help='an extracted exefs folder (main, main.npdm)')
    args = ap.parse_args()
    exefs_dirs = args.exefs or sorted(p for p in (REPO / 'build').glob('*/exefs') if p.is_dir())
    try:
        import capstone
        cs = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
        cs.detail = True
    except ImportError:
        cs = None

    check_module(OUT / 'subsdk9')
    for name, b in builds.BUILDS.items():
        exefs = find_main(exefs_dirs, b['build_id'])
        if exefs is None:
            print(f'{b["build_id"]}.ips (game {name}): no extracted main of this build, not checked')
            continue
        check_patch(name, b, exefs, cs)
        npdm = exefs / 'main.npdm'
        if npdm.is_file():
            allowed, program = svcs_allowed(npdm.read_bytes())
            missing = [n for s, n in SVCS.items() if s not in allowed]
            if missing:
                bad(f'the game may not call {missing}')
            elif f'{program:016X}' != builds.PROGRAM_ID:
                bad(f'program id {program:016X}, tools/builds.py {builds.PROGRAM_ID}')
            else:
                ok(f'NPDM: program {program:016X}, allows {", ".join(SVCS.values())}')
    print('PASS' if not problems else f'FAIL: {len(problems)} problem(s)')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
