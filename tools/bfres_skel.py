"""The skeleton of a remake model file (Model/<name>.bfres from the extracted romfs): each bone's name, parent and
bind transform (scale, rotation, translation), and the bind pose's world matrices.

The file's FSKL block holds, from its magic: u32 flags (rotation mode 0x1000: Euler XYZ in radians, else
quaternions xyzw), u64 offsets of the bone dictionary, the bone array, the matrix-to-bone list and the inverse model
matrices, then u16 counts (bones, smooth matrices, rigid matrices). A bone is 0x58 bytes, as the game also keeps it
in memory (runtime/debug_tools.c reads the same records): u64 name (a u16 length, then the text), user data, u16
index +0x20, parent +0x22 (0xffff for none), u32 flags +0x2c, f32 scale x3 +0x30, rotation x4 +0x3c, translation
x3 +0x4c.

tools/make_n64_mod.py uses it to fit N64 skeletons to the remake's (the N64 limb a remake bone moves).

Standard library only.
"""
import math
import struct

BONE = 0x58
EULER = 0x1000


def _name(d, off):
    n = struct.unpack_from('<H', d, off)[0]
    return d[off + 2:off + 2 + n].decode('utf-8', 'replace')


def _mat_euler(rx, ry, rz):
    """Rotation matrix (row-major 3x3) of Euler angles applied x, then y, then z."""
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    return [[cy * cz, sx * sy * cz - cx * sz, cx * sy * cz + sx * sz],
            [cy * sz, sx * sy * sz + cx * cz, cx * sy * sz - sx * cz],
            [-sy, sx * cy, cx * cy]]


def _mat_quat(x, y, z, w):
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]


def mat_mul(a, b):
    """4x4 row-major product."""
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


class Bone:
    __slots__ = ('name', 'parent', 'scale', 'rot', 'trans', 'local', 'world')


def read(path):
    """The bones of the model file at path, or of its bytes."""
    d = path if isinstance(path, (bytes, bytearray)) else open(path, 'rb').read()
    at = d.find(b'FSKL')
    if at < 0:
        raise ValueError('the model file has no skeleton')
    flags = struct.unpack_from('<I', d, at + 4)[0]
    array = struct.unpack_from('<Q', d, at + 0x10)[0]
    count = struct.unpack_from('<H', d, at + 0x38)[0]
    bones = []
    for i in range(count):
        o = array + BONE * i
        b = Bone()
        b.name = _name(d, struct.unpack_from('<Q', d, o)[0])
        p = struct.unpack_from('<H', d, o + 0x22)[0]
        b.parent = -1 if p == 0xFFFF else p
        b.scale = struct.unpack_from('<3f', d, o + 0x30)
        b.rot = struct.unpack_from('<4f', d, o + 0x3c)
        b.trans = struct.unpack_from('<3f', d, o + 0x4c)
        r = _mat_euler(*b.rot[:3]) if flags & EULER else _mat_quat(*b.rot)
        b.local = [[r[i][j] * b.scale[j] for j in range(3)] + [b.trans[i]] for i in range(3)] + [[0, 0, 0, 1]]
        bones.append(b)
    for b in bones:
        b.world = b.local if b.parent < 0 else mat_mul(bones[b.parent].world, b.local)
    return bones


if __name__ == '__main__':
    import sys
    for i, b in enumerate(read(sys.argv[1])):
        w = b.world
        print(i, b.name, b.parent, [round(w[k][3], 3) for k in range(3)])
