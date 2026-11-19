"""Write the IPS32 patch for one game build: the stub (src/stub.S, linked at the build's stub address) into main's
executable padding, and a branch to it in place of the frame site's instruction.

An IPS32 file is "IPS32", then records of a 4-byte big-endian offset, a 2-byte big-endian size and the bytes, then
"EEOF". The offset of a patch to an NSO counts from the start of the NSO's 0x100-byte header, so it is the address in
the module plus 0x100. The file is named by main's module id: <build id>.ips.
"""

import struct

import builds

NSO_HEADER = 0x100


def branch(at, to):
    """b to, encoded at address at."""
    delta = to - at
    if delta % 4 or not -(1 << 27) <= delta < (1 << 27):
        raise ValueError(f'{at:#x}: no b reaches {to:#x}')
    return 0x14000000 | ((delta >> 2) & 0x3FFFFFF)


def ips32(records):
    out = bytearray(b'IPS32')
    for addr, data in sorted(records):
        off = addr + NSO_HEADER
        if off == 0x45454F46:   # the offset would read as the end marker
            raise ValueError('patch offset collides with EEOF')
        for i in range(0, len(data), 0xFFFF):
            part = data[i:i + 0xFFFF]
            out += struct.pack('>IH', off + i, len(part)) + part
    return bytes(out + b'EEOF')


def records(ips):
    """[(address in the module, bytes)] of an IPS32 file."""
    if ips[:5] != b'IPS32' or ips[-4:] != b'EEOF':
        raise ValueError('not an IPS32 file')
    at, out = 5, []
    while at < len(ips) - 4:
        off, size = struct.unpack_from('>IH', ips, at)
        out.append((off - NSO_HEADER, ips[at + 6:at + 6 + size]))
        at += 6 + size
    return out


def site_word(b):
    return branch(b['frame_site'], builds.stub_addr(b) + 16)   # past the 16-byte marker


def make(b, stub):
    """stub: the linked stub's bytes, starting with the marker."""
    start = builds.stub_addr(b)
    room = ((b['text_end'] + 0xFFF) & ~0xFFF) - start
    if len(stub) > room:
        raise ValueError(f'the stub is {len(stub):#x} bytes, the padding holds {room:#x}')
    if stub[:8] != builds.MARKER:
        raise ValueError('the stub does not start with the marker')
    return ips32([(start, stub), (b['frame_site'], struct.pack('<I', site_word(b)))])
