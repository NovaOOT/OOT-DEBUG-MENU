"""Turn the module's ELF (a shared object linked with src/module.ld) into an NSO, the format the system loads.

An NSO is a 0x100-byte header and the three segments: text, read-only data and data, each given by its offset in the
file, its address in the module and its size, then the bss size after data. The segments are stored plainly (no
compression flags, no hash flags: the loader checks a hash only when its flag is set). The header also gives the
module id (the ELF's GNU build id, padded to 0x20 bytes) and the dynamic symbol and string tables, relative to the
read-only segment.
"""

import struct
import sys
from pathlib import Path

PAGE = 0x1000
PT_LOAD = 1


def fail(msg):
    sys.exit(f'elf2nso: {msg}')


def sections(elf):
    shoff, = struct.unpack_from('<Q', elf, 0x28)
    shentsize, shnum, shstrndx = struct.unpack_from('<HHH', elf, 0x3A)
    heads = [struct.unpack_from('<IIQQQQIIQQ', elf, shoff + i * shentsize) for i in range(shnum)]
    names_off = heads[shstrndx][4]
    out = {}
    for name, _type, _flags, addr, off, size, *_ in heads:
        n = elf[names_off + name:elf.index(b'\0', names_off + name)].decode()
        out[n] = (addr, off, size)
    return out


def convert(elf):
    if elf[:4] != b'\x7fELF' or elf[4] != 2 or struct.unpack_from('<H', elf, 0x12)[0] != 183:
        fail('not a 64-bit AArch64 ELF')
    phoff, = struct.unpack_from('<Q', elf, 0x20)
    phentsize, phnum = struct.unpack_from('<HH', elf, 0x36)
    loads = []
    for i in range(phnum):
        p_type, p_flags, p_off, p_vaddr, _, p_filesz, p_memsz, _ = struct.unpack_from('<IIQQQQQQ', elf, phoff + i * phentsize)
        if p_type == PT_LOAD:
            loads.append((p_flags, p_off, p_vaddr, p_filesz, p_memsz))
    if [l[0] for l in loads] != [5, 4, 6]:
        fail(f'expected three PT_LOAD segments r-x, r--, rw-, got flags {[l[0] for l in loads]}')
    if loads[0][2] != 0 or any(l[2] % PAGE for l in loads):
        fail('the segments must start at 0 and on pages')
    for (_, _, v, fs, ms), (_, _, nv, _, _) in zip(loads, loads[1:]):
        if v + ms > nv:
            fail('segments overlap')
    if loads[0][3] != loads[0][4] or loads[1][3] != loads[1][4]:
        fail('only the data segment may have bss')

    secs = sections(elf)
    ro_addr = loads[1][2]
    for need in ('.dynsym', '.dynstr', '.note.gnu.build-id'):
        if need not in secs:
            fail(f'no {need} section')
    note = secs['.note.gnu.build-id']
    namesz, descsz, ntype = struct.unpack_from('<III', elf, note[1])
    desc_off = note[1] + 12 + ((namesz + 3) & ~3)
    module_id = elf[desc_off:desc_off + descsz].ljust(0x20, b'\0')[:0x20]

    head = bytearray(0x100)
    head[0:4] = b'NSO0'
    offset = 0x100
    body = b''
    for i, (_, p_off, vaddr, filesz, _) in enumerate(loads):
        data = elf[p_off:p_off + filesz]
        struct.pack_into('<III', head, 0x10 + i * 0x10, offset, vaddr, filesz)
        struct.pack_into('<I', head, 0x60 + i * 4, filesz)
        body += data
        offset += filesz
    data_end = loads[2][2] + loads[2][3]
    image_end = (loads[2][2] + loads[2][4] + PAGE - 1) & ~(PAGE - 1)
    struct.pack_into('<I', head, 0x3C, image_end - data_end)
    head[0x40:0x60] = module_id
    for at, name in ((0x90, '.dynstr'), (0x98, '.dynsym')):
        addr, _, size = secs[name]
        struct.pack_into('<II', head, at, addr - ro_addr, size)
    return bytes(head) + body, module_id


def main():
    if len(sys.argv) != 3:
        sys.exit('usage: elf2nso.py IN.elf OUT')
    nso, module_id = convert(Path(sys.argv[1]).read_bytes())
    Path(sys.argv[2]).write_bytes(nso)
    print(f'{sys.argv[2]}: {len(nso)} bytes, module id {module_id[:20].hex().upper()}')


if __name__ == '__main__':
    main()
