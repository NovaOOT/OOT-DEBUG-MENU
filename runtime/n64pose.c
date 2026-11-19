// N64 Link posed by the remake player's skeleton (n64pose.h explains the retarget).
//
// The remake's skeleton in memory: the model (an FModel) points at it from +0xf8. The skeleton points at its resource
// from +0x00 and at its world matrices from +0x20 (0x40 bytes a bone, four columns of four floats: the rotation and
// scale's three, then the translation). The resource is the model file's skeleton block as loaded: u32 flags at +0x04
// (0x1000: rotations are Euler angles in radians, x then y then z; else quaternions x, y, z, w), the bone records at
// +0x10 and their number, a u16, at +0x38. A bone record is 0x58 bytes: its name at +0x00 (a u16 length, then the
// text), its parent at +0x22 (0xffff for none), scale +0x30, rotation +0x3c and translation +0x4c (floats).
//
// N64 Link's limbs, as his skeleton numbers them: 0 root, 1 waist, 2 hips, 3-5 right thigh, shin and foot, 6-8 the
// left ones, 9 upper body, 10 head, 11 hat, 12 collar, 13-15 left upper arm, forearm and hand, 16-18 the right ones,
// 19 sheath, 20 torso. A limb's x axis runs along it and its z axis to his left; his model faces +z with y up, as the
// remake's bind pose does.
#include "n64pose.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "debug_tools.h"
#include "host.h"
#include "mods.h"
#include "motion_owner.h"
#include "n64actors.h"
#include "platform/platform.h"
#include "vk_n64.h"

#define LIMBS 21
#define REST_VALUES 67
#define MAX_BONES 1024
#define MAX_NODES 4096
#define STALE_NS 250000000ull   // a pose older than this is not drawn (the player is gone or the game is paused away)
#define SHIFT_SLACK_M 2.0f       // n64pose_world_ready: the model's distance from its actor that keeps the shift
#define CHILD_HIPS_M 0.75f      // a bind pose with its hips lower than this is the child's skeleton
// After the standing frame (tools/n64link.py): his mouth and the two mouthpieces, then the frame in which he plays.
#define OCARINA_FRAME (REST_VALUES + 9)
#define OCARINA_POINTS (OCARINA_FRAME + REST_VALUES)
#define UPPER_BODY 9

// The bones before NEEDED make the skeleton the player's; the ones after are used when there (else the wrist's).
enum { SKL_ROOT, WAIST, LEG1_R, LEG2_R, ANKLE_R, TOE_R, LEG1_L, LEG2_L, ANKLE_L, TOE_L, SPINE1, SPINE2, NECK, HEAD,
       ARM1_L, ARM2_L, WRIST_L, ARM1_R, ARM2_R, WRIST_R, NEEDED, WEAPON_L = NEEDED, WEAPON_R, NAMES };
static const char *const bone_names[NAMES] = {
    "Skl_Root", "Waist", "Leg_1_R", "Leg_2_R", "Ankle_R", "Toe_R", "Leg_1_L", "Leg_2_L", "Ankle_L", "Toe_L",
    "Spine_1", "Spine_2", "Neck", "Head", "Arm_1_L", "Arm_2_L", "Wrist_L", "Arm_1_R", "Arm_2_R", "Wrist_R",
    "Weapon_L", "Weapon_R"};

// A sword in his left hand: the remake's hangs from Weapon_L, which in the bind pose (arms hanging out and down) points
// its z axis ahead and its x axis back up the arm. N64 Link's sword hand has the blade along its x axis and the
// fingers along y. The hand then turns with Weapon_L, blade along its +z and fingers along its -x, so both swords
// point the same way (rows: the hand's axes in Weapon_L's).
static const float sword_in_weapon[3][3] = {{0, -1, 0}, {0, 0, -1}, {1, 0, 0}};
// A shield in his right hand: the remake's hangs from Weapon_R with its face along the bone's +y and its top along +z
// (measured on the Deku, Hylian and Mirror shield models). N64 Link's shield hands carry the shield with its face along
// the hand's -z and its top along +x. The hand turns with Weapon_R so the N64 shield faces where the remake's does:
// the same axes as the sword's.
static const float shield_in_weapon[3][3] = {{0, -1, 0}, {0, 0, -1}, {1, 0, 0}};

// The remake bone each limb follows.
static const uint8_t follow[LIMBS] = {SKL_ROOT, WAIST, WAIST, LEG1_R, LEG2_R, ANKLE_R, LEG1_L, LEG2_L, ANKLE_L, SPINE1,
                                      HEAD, HEAD, NECK, ARM1_L, ARM2_L, WRIST_L, ARM1_R, ARM2_R, WRIST_R, SPINE2,
                                      SPINE2};

// How a limb's offset is measured. AIM: the standing limb (or the limb named by `from`, for the hands, whose x axis
// does not run along the hand) is turned the shortest way onto the remake bind pose's direction between two joints.
// UP / DOWN: the limb is set upright or hanging with its z axis to his left. ROOT: no turn. REL: as the
// standing frame has it against the limb named by `from`. FLAT: as AIM, but turned about the vertical only, so a foot
// keeps the standing frame's sole flat on the ground (the remake's ankle-to-toe bone slopes down, and aiming along it
// would tip the toes into the floor).
enum { ROOT, AIM, UP, DOWN, REL, FLAT };
static const struct { uint8_t how, a, b, from; } offset_rule[LIMBS] = {
    [0] = {ROOT, 0, 0, 0},
    [1] = {DOWN, 0, 0, 0},
    [2] = {DOWN, 0, 0, 0},
    [3] = {AIM, LEG1_R, LEG2_R, 3},
    [4] = {AIM, LEG2_R, ANKLE_R, 4},
    [5] = {FLAT, ANKLE_R, TOE_R, 5},
    [6] = {AIM, LEG1_L, LEG2_L, 6},
    [7] = {AIM, LEG2_L, ANKLE_L, 7},
    [8] = {FLAT, ANKLE_L, TOE_L, 8},
    [9] = {UP, 0, 0, 0},
    [10] = {REL, 0, 0, 9},   // the head's x axis looks ahead, not up the neck: as the standing frame holds it
    [11] = {REL, 0, 0, 10},
    [12] = {UP, 0, 0, 0},
    [13] = {AIM, ARM1_L, ARM2_L, 13},
    [14] = {AIM, ARM2_L, WRIST_L, 14},
    [15] = {AIM, ARM2_L, WRIST_L, 14},
    [16] = {AIM, ARM1_R, ARM2_R, 16},
    [17] = {AIM, ARM2_R, WRIST_R, 17},
    [18] = {AIM, ARM2_R, WRIST_R, 17},
    [19] = {REL, 0, 0, 9},
    [20] = {UP, 0, 0, 0},
};

typedef float M3[3][3];   // row-major

static void m3_mul(const M3 a, const M3 b, M3 out) {
    M3 r;
    for (int i = 0; i < 3; i++)
        for (int j = 0; j < 3; j++) r[i][j] = a[i][0] * b[0][j] + a[i][1] * b[1][j] + a[i][2] * b[2][j];
    memcpy(out, r, sizeof r);
}
static void m3_tmul(const M3 a, const M3 b, M3 out) {   // a transposed, times b
    M3 r;
    for (int i = 0; i < 3; i++)
        for (int j = 0; j < 3; j++) r[i][j] = a[0][i] * b[0][j] + a[1][i] * b[1][j] + a[2][i] * b[2][j];
    memcpy(out, r, sizeof r);
}
static void m3_vec(const M3 a, const float v[3], float out[3]) {
    float r[3];
    for (int i = 0; i < 3; i++) r[i] = a[i][0] * v[0] + a[i][1] * v[1] + a[i][2] * v[2];
    memcpy(out, r, sizeof r);
}
static float norm3(const float v[3]) { return sqrtf(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]); }
static void sub3(const float a[3], const float b[3], float out[3]) {
    for (int k = 0; k < 3; k++) out[k] = a[k] - b[k];
}
// The rotation part of a matrix whose columns may carry a scale: each column made unit length.
static int unit_columns(M3 m) {
    for (int c = 0; c < 3; c++) {
        float n = sqrtf(m[0][c] * m[0][c] + m[1][c] * m[1][c] + m[2][c] * m[2][c]);
        if (!(n > 1e-6f) || !isfinite(n)) return 0;
        for (int r = 0; r < 3; r++) m[r][c] /= n;
    }
    return 1;
}
// N64 limb rotation: z, then y, then x, in 1/65536 turns (Rz Ry Rx).
static void rot_n64(const int16_t v[3], M3 out) {
    float ax = v[0] * 3.14159265f / 32768.0f, ay = v[1] * 3.14159265f / 32768.0f, az = v[2] * 3.14159265f / 32768.0f;
    float cx = cosf(ax), sx = sinf(ax), cy = cosf(ay), sy = sinf(ay), cz = cosf(az), sz = sinf(az);
    M3 r = {{cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx},
            {sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx},
            {-sy, cy * sx, cy * cx}};
    memcpy(out, r, sizeof r);
}
// The shortest rotation taking direction a onto direction b.
static void rot_between(const float a_in[3], const float b_in[3], M3 out) {
    float na = norm3(a_in), nb = norm3(b_in);
    float a[3] = {a_in[0] / na, a_in[1] / na, a_in[2] / na}, b[3] = {b_in[0] / nb, b_in[1] / nb, b_in[2] / nb};
    float v[3] = {a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]};
    float c = a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    if (c < -0.9999f) {   // opposite: half a turn about an axis across a
        float ax[3] = {fabsf(a[0]) < 0.9f ? 1.0f : 0.0f, fabsf(a[0]) < 0.9f ? 0.0f : 1.0f, 0};
        float p[3] = {a[1] * ax[2] - a[2] * ax[1], a[2] * ax[0] - a[0] * ax[2], a[0] * ax[1] - a[1] * ax[0]};
        float np = norm3(p);
        for (int i = 0; i < 3; i++) p[i] /= np;
        for (int i = 0; i < 3; i++)
            for (int j = 0; j < 3; j++) out[i][j] = 2 * p[i] * p[j] - (i == j);
        return;
    }
    M3 V = {{0, -v[2], v[1]}, {v[2], 0, -v[0]}, {-v[1], v[0], 0}}, VV;
    m3_mul(V, V, VV);
    float k = 1.0f / (1.0f + c);
    for (int i = 0; i < 3; i++)
        for (int j = 0; j < 3; j++) out[i][j] = (i == j) + V[i][j] + VV[i][j] * k;
}
// Columns x, y = z cross x, z.
static void frame_xz(const float x[3], const float z[3], M3 out) {
    float y[3] = {z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0]};
    for (int r = 0; r < 3; r++) out[r][0] = x[r], out[r][1] = y[r], out[r][2] = z[r];
}

// A rotation as a unit quaternion (x, y, z, w) and back, and the blend of two rotations by t.
static void m3_quat(const M3 m, float q[4]) {
    float tr = m[0][0] + m[1][1] + m[2][2];
    if (tr > 0) {
        float s = sqrtf(tr + 1.0f) * 2;
        q[3] = 0.25f * s, q[0] = (m[2][1] - m[1][2]) / s, q[1] = (m[0][2] - m[2][0]) / s, q[2] = (m[1][0] - m[0][1]) / s;
    } else if (m[0][0] > m[1][1] && m[0][0] > m[2][2]) {
        float s = sqrtf(1.0f + m[0][0] - m[1][1] - m[2][2]) * 2;
        q[3] = (m[2][1] - m[1][2]) / s, q[0] = 0.25f * s, q[1] = (m[0][1] + m[1][0]) / s, q[2] = (m[0][2] + m[2][0]) / s;
    } else if (m[1][1] > m[2][2]) {
        float s = sqrtf(1.0f + m[1][1] - m[0][0] - m[2][2]) * 2;
        q[3] = (m[0][2] - m[2][0]) / s, q[0] = (m[0][1] + m[1][0]) / s, q[1] = 0.25f * s, q[2] = (m[1][2] + m[2][1]) / s;
    } else {
        float s = sqrtf(1.0f + m[2][2] - m[0][0] - m[1][1]) * 2;
        q[3] = (m[1][0] - m[0][1]) / s, q[0] = (m[0][2] + m[2][0]) / s, q[1] = (m[1][2] + m[2][1]) / s, q[2] = 0.25f * s;
    }
}
static void quat_m3(const float q[4], M3 m) {
    float x = q[0], y = q[1], z = q[2], w = q[3];
    M3 r = {{1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)},
            {2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)},
            {2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)}};
    memcpy(m, r, sizeof r);
}
static void m3_blend(const M3 a, const M3 b, float t, M3 out) {   // normalised lerp of the quaternions, the short way
    float p[4], q[4], d = 0, n = 0;
    m3_quat(a, p);
    m3_quat(b, q);
    for (int k = 0; k < 4; k++) d += p[k] * q[k];
    for (int k = 0; k < 4; k++) q[k] = p[k] + t * ((d < 0 ? -q[k] : q[k]) - p[k]), n += q[k] * q[k];
    n = sqrtf(n);
    if (!(n > 1e-6f)) {
        memcpy(out, a, sizeof(M3));
        return;
    }
    for (int k = 0; k < 4; k++) q[k] /= n;
    quat_m3(q, out);
}

static int rd(uint64_t a, void *out, size_t n) { return a && plat_mem_read(a, out, n); }
static uint64_t ptr(uint64_t a) {
    uint64_t p = 0;
    return rd(a, &p, 8) ? p : 0;
}

// ---- what is measured once per remake skeleton resource and N64 object
typedef struct {
    uint64_t resource;
    int ok;
    uint16_t bone[NAMES];   // the remake bone of each name
    const N64File *file;
    M3 offset[LIMBS];       // limb rotation = remake bone rotation x offset
    float limb_at[LIMBS][3];   // each limb's joint, relative to its parent, in N64 units
    int8_t parent[LIMBS];
    float scale;            // N64 units to metres
    float lift[3];          // from the remake hips to the N64 root, in the hip bone's own axes (metres)
    int plays;              // the file has the frame in which he plays the ocarina: play[] holds
    M3 play[LIMBS];         // each limb's rotation in that frame against the upper body's (limb 9)
} Rig;

// The posed models: the player, and the pause menu's preview of him (motion_owner_preview), each with its own skeleton.
enum { POSE_PLAYER, POSE_PREVIEW, POSES };
static Rig rigs[POSES];
static PlatMutex rig_lock = PLAT_MUTEX_INIT;

typedef struct { M3 r; float t[3]; } Affine;

// The bind pose of every bone of the resource; 0 when it does not read as a skeleton.
static int bind_pose(uint64_t resource, Affine *world, int *count, Rig *g) {
    uint32_t flags = 0;
    uint16_t n = 0;
    uint64_t bones = ptr(resource + 0x10);
    if (!bones || !rd(resource + 4, &flags, 4) || !rd(resource + 0x38, &n, 2) || !n || n > MAX_BONES) return 0;
    static int16_t parent[MAX_BONES];
    static uint8_t done[MAX_BONES];
    for (int k = 0; k < NAMES; k++) g->bone[k] = 0xffff;
    for (int i = 0; i < n; i++) {
        uint64_t b = bones + (uint64_t)i * 0x58, name = ptr(b);
        uint16_t len = 0, par = 0;
        char text[64] = {0};
        float sc[3], q[4], t[3];
        if (!name || !rd(name, &len, 2) || !len || len >= sizeof text || !rd(name + 2, text, len) ||
            !rd(b + 0x22, &par, 2) || !rd(b + 0x30, sc, 12) || !rd(b + 0x3c, q, 16) || !rd(b + 0x4c, t, 12))
            return 0;
        for (int k = 0; k < NAMES; k++)
            if (!strcmp(text, bone_names[k])) g->bone[k] = (uint16_t)i;
        M3 r;
        if (flags & 0x1000) {
            float cx = cosf(q[0]), sx = sinf(q[0]), cy = cosf(q[1]), sy = sinf(q[1]), cz = cosf(q[2]), sz = sinf(q[2]);
            M3 e = {{cy * cz, sx * sy * cz - cx * sz, cx * sy * cz + sx * sz},
                    {cy * sz, sx * sy * sz + cx * cz, cx * sy * sz - sx * cz},
                    {-sy, sx * cy, cx * cy}};
            memcpy(r, e, sizeof r);
        } else {
            float x = q[0], y = q[1], z = q[2], w = q[3];
            M3 e = {{1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)},
                    {2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)},
                    {2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)}};
            memcpy(r, e, sizeof r);
        }
        for (int row = 0; row < 3; row++)
            for (int c = 0; c < 3; c++) world[i].r[row][c] = r[row][c] * sc[c];
        memcpy(world[i].t, t, sizeof t);
        parent[i] = par == 0xffff || par >= n ? -1 : (int16_t)par;
        done[i] = 0;
    }
    // locals to world, parents first (in whatever order the records come)
    for (int pass = 0, left = n; left && pass < 64; pass++)
        for (int i = 0; i < n; i++) {
            if (done[i] || (parent[i] >= 0 && !done[parent[i]])) continue;
            if (parent[i] >= 0) {
                const Affine *p = &world[parent[i]];
                Affine w;
                m3_mul(p->r, world[i].r, w.r);
                m3_vec(p->r, world[i].t, w.t);
                for (int k = 0; k < 3; k++) w.t[k] += p->t[k];
                world[i] = w;
            }
            done[i] = 1, left--;
        }
    for (int k = 0; k < NEEDED; k++)
        if (g->bone[k] == 0xffff || !done[g->bone[k]]) return 0;
    for (int k = NEEDED; k < NAMES; k++)
        if (g->bone[k] == 0xffff || !done[g->bone[k]]) g->bone[k] = g->bone[k == WEAPON_R ? WRIST_R : WRIST_L];
    *count = n;
    return 1;
}

static int build_rig(uint64_t resource, Rig *g) {
    static Affine world[MAX_BONES];
    int n = 0;
    memset(g, 0, sizeof *g);
    g->resource = resource;
    if (!bind_pose(resource, world, &n, g)) return 0;
    const Affine *hips = &world[g->bone[SKL_ROOT]];
    int child = hips->t[1] < CHILD_HIPS_M;
    const N64File *f = n64gfx_object(child ? 21 : 20);
    if (!f || f->nrest < REST_VALUES || f->nnodes < LIMBS || f->nnodes > MAX_NODES) return 0;
    g->file = f;
    for (int i = 0; i < LIMBS; i++) {
        const N64Node *nd = &f->nodes[i];
        if (nd->kind != 2 || nd->parent >= i || (i && nd->parent < 0)) return 0;
        g->parent[i] = (int8_t)nd->parent;
        g->limb_at[i][0] = nd->x, g->limb_at[i][1] = nd->y, g->limb_at[i][2] = nd->z;
    }
    // the standing frame's limb rotations in his model's space
    M3 stand[LIMBS];
    for (int i = 0; i < LIMBS; i++) {
        M3 r;
        rot_n64(&f->rest[3 + 3 * i], r);
        if (i) m3_mul(stand[g->parent[i]], r, stand[i]);
        else memcpy(stand[i], r, sizeof r);
    }
    M3 bind[NAMES];
    for (int k = 0; k < NAMES; k++) {
        memcpy(bind[k], world[g->bone[k]].r, sizeof(M3));
        if (!unit_columns(bind[k])) return 0;
    }
    static const float up[3] = {0, 1, 0}, down[3] = {0, -1, 0}, left[3] = {1, 0, 0};
    M3 want[LIMBS];
    for (int i = 0; i < LIMBS; i++) {
        switch (offset_rule[i].how) {
        case ROOT: memcpy(want[i], (M3){{1, 0, 0}, {0, 1, 0}, {0, 0, 1}}, sizeof(M3)); break;
        case UP: frame_xz(up, left, want[i]); break;
        case DOWN: frame_xz(down, left, want[i]); break;
        case REL: {
            M3 rel;
            int p = offset_rule[i].from;
            m3_tmul(stand[p], stand[i], rel);
            m3_mul(want[p], rel, want[i]);
            break;
        }
        default: {
            const float *a = world[g->bone[offset_rule[i].a]].t, *b = world[g->bone[offset_rule[i].b]].t;
            const M3 *src = &stand[offset_rule[i].from];
            float to[3] = {b[0] - a[0], b[1] - a[1], b[2] - a[2]}, along[3] = {(*src)[0][0], (*src)[1][0], (*src)[2][0]};
            if (offset_rule[i].how == FLAT) to[1] = along[1] = 0;
            if (norm3(to) < 1e-5f || norm3(along) < 1e-5f) return 0;
            M3 turn;
            rot_between(along, to, turn);
            m3_mul(turn, stand[i], want[i]);
        }
        }
        m3_tmul(bind[follow[i]], want[i], g->offset[i]);
    }
    // N64 units to metres: the remake's thighs and shins over his
    float remake = 0, n64 = 0;
    static const uint8_t legs[4][2] = {{LEG1_R, LEG2_R}, {LEG2_R, ANKLE_R}, {LEG1_L, LEG2_L}, {LEG2_L, ANKLE_L}};
    static const uint8_t leg_limbs[4] = {4, 5, 7, 8};   // each limb's joint offset is the length of the limb above
    for (int k = 0; k < 4; k++) {
        const float *a = world[g->bone[legs[k][0]]].t, *b = world[g->bone[legs[k][1]]].t;
        float d[3] = {b[0] - a[0], b[1] - a[1], b[2] - a[2]};
        remake += norm3(d), n64 += norm3(g->limb_at[leg_limbs[k]]);
    }
    if (!(n64 > 1.0f) || !(remake > 1e-3f)) return 0;
    g->scale = remake / n64;
    // the bind pose built the N64 way from the remake's hips: how far its ankles miss the remake's, kept as a lift
    float at[LIMBS][3];
    M3 rot[LIMBS];
    for (int i = 0; i < LIMBS; i++) {
        m3_mul(bind[follow[i]], g->offset[i], rot[i]);
        if (!i) {
            memcpy(at[0], hips->t, sizeof at[0]);
            continue;
        }
        float d[3];
        m3_vec(rot[g->parent[i]], g->limb_at[i], d);
        for (int k = 0; k < 3; k++) at[i][k] = at[g->parent[i]][k] + g->scale * d[k];
    }
    float miss[3];
    for (int k = 0; k < 3; k++)
        miss[k] = 0.5f * (world[g->bone[ANKLE_R]].t[k] - at[5][k] + world[g->bone[ANKLE_L]].t[k] - at[8][k]);
    for (int k = 0; k < 3; k++)   // into the hip bone's axes: its rotation transposed
        g->lift[k] = bind[SKL_ROOT][0][k] * miss[0] + bind[SKL_ROOT][1][k] * miss[1] + bind[SKL_ROOT][2][k] * miss[2];
    if (f->nrest >= OCARINA_POINTS) {   // the playing frame, each limb against the upper body
        M3 fr[LIMBS], r;
        for (int i = 0; i < LIMBS; i++) {
            rot_n64(&f->rest[OCARINA_FRAME + 3 + 3 * i], r);
            if (i) m3_mul(fr[g->parent[i]], r, fr[i]);
            else memcpy(fr[i], r, sizeof r);
        }
        for (int i = 0; i < LIMBS; i++) m3_tmul(fr[UPPER_BODY], fr[i], g->play[i]);
        g->plays = 1;
    }
    rt_log("n64: N64 %s Link follows the %s skeleton (%d bones; %.5f m a unit)", child ? "child" : "adult",
           g == &rigs[POSE_PREVIEW] ? "pause menu preview's" : "player's", n, (double)g->scale);
    g->ok = 1;
    return 1;
}

// ---- the latest poses, made on the game thread, read on the render thread
static struct {
    const N64File *file;
    float rows[MAX_NODES][12];
    uint8_t on[MAX_NODES];   // which nodes draw
    uint32_t nodes, tunic;
    uint64_t at_ns;
} poses[POSES];
static uint8_t node_on[MAX_NODES];
static PlatMutex pose_lock = PLAT_MUTEX_INIT;
static uint64_t posed_model[POSES];   // the player's model and the preview's (n64pose_game_frame)
static uint64_t preview_seen_ns;      // the render thread last met a draw of the preview (n64pose_preview_drawn)
#define PREVIEW_SEEN_NS 100000000ull

int n64pose_wanted(void) {
    static int known = -1;
    if (known < 0) {
        if (!RT_OOT) known = 0;
        else if (n64gfx_on()) known = 1;
        else if (mods_features_known()) known = 0;   // before the mods folder's scan, the answer waits
        else return 0;
    }
    return known && n64gfx_part(N64_PART_LINK);
}

// ---- what the player holds and wears, read on the game thread each frame
//
// The player's equipment actors (motion_owner_models) are named for what they are: "Weapon_Sword_*",
// "Weapon_Shield_*", "Armor_*". Each hangs from a bone named in its attachment record (the actor's component at
// +0x270, its record at +0x18): the record holds the bone's name inline at +0x94. A sword or shield in hand hangs
// from "Weapon_L" or "Weapon_R"; put away, from "Pod_A" on his back.
enum { SWORD_NONE, SWORD_KOKIRI, SWORD_MASTER, SWORD_BIGGORON, SWORD_BROKEN };
enum { SHIELD_NONE, SHIELD_DEKU, SHIELD_HYLIAN, SHIELD_MIRROR };
// What he holds and wears besides: the item in each hand (N64I_* of its own row, -1 none), the bottle and the Deku
// stick (in the left hand), the mask (N64I_MASK_*, -1 none), the boots, the gauntlets and the Goron's Bracelet.
enum { BOOTS_NONE, BOOTS_IRON, BOOTS_HOVER };
enum { GAUNTLETS_NONE, GAUNTLETS_SILVER, GAUNTLETS_GOLD };
typedef struct {
    uint8_t sword, sword_held, shield, shield_held, tunic;
    int8_t right_item, left_item, mask;
    uint8_t bottle, stick, stick_held, boots, gauntlets, bracelet;
} Gear;
// The player's, and the pause menu preview's from the equipment hung on it (what the menu has just chosen shows there
// before the player's own changes, as he takes it on once the menu closes); the player's while the preview has none.
static Gear gear, preview_gear;   // game thread
#define PREVIEW_GEAR_HOLD 30         // frames (read_gear)

static int name_of(uint64_t actor, char *out, size_t n) {
    memset(out, 0, n);
    return actor && rd(ptr(actor + 0x20), out, n - 1) && out[0];
}
static int bone_of(uint64_t actor, char bone[24]) {   // the bone the actor hangs from, "" when unknown
    memset(bone, 0, 24);
    uint64_t rec = ptr(ptr(actor + 0x270) + 0x18);
    return rec && rd(rec + 0x94, bone, 23) && bone[0];
}
static int held(uint64_t actor) {
    char bone[24];
    return bone_of(actor, bone) && !strncmp(bone, "Weapon_", 7);
}
static int has(const char *s, const char *part) { return strstr(s, part) != NULL; }
// A mask taken off, or an item put away, keeps its slot with its model hidden: bit 0x80 of the model's byte at +0x12
// (motion_owner.c collect_models).
static int shown(uint64_t model) {
    uint8_t b = 0x80;
    return model && rd(model + 0x12, &b, 1) && !(b & 0x80);
}
static int mask_of(const char *name) {   // "Armor_*_Mask_<name>"
    static const struct {
        const char *name;
        int8_t piece;
    } masks[] = {{"Keaton", N64I_MASK_KEATON}, {"Skull", N64I_MASK_SKULL}, {"Spooky", N64I_MASK_SPOOKY},
                 {"Bunny", N64I_MASK_BUNNY},   {"Goron", N64I_MASK_GORON}, {"Zora", N64I_MASK_ZORA},
                 {"Gerudo", N64I_MASK_GERUDO}, {"Truth", N64I_MASK_TRUTH}};
    const char *m = strstr(name, "_Mask_");
    for (size_t k = 0; m && k < sizeof masks / sizeof *masks; k++)
        if (has(m + 6, masks[k].name)) return masks[k].piece;
    return -1;
}

// The gear of the player (preview 0) or of the pause menu's preview, from the models motion_owner tracks; names, when
// given, lists them for the log. 0 when none of them is the owner's.
static int gear_of(const MotionOwner *m, int n, int preview, Gear *out, char *names, size_t nnames) {
    Gear g = {.right_item = -1, .left_item = -1, .mask = -1};
    size_t at = 0;
    uint64_t seen[64];
    int nseen = 0;
    if (names) names[0] = 0;
    for (int i = 0; i < n; i++) {
        if (!m[i].kind || !(m[i].flags & MOTION_OWNER_PREVIEW) != !preview) continue;
        int dup = 0;
        for (int k = 0; k < nseen; k++) dup |= seen[k] == m[i].actor;
        if (dup || nseen == 64) continue;
        seen[nseen++] = m[i].actor;
        char name[48];
        if (!name_of(m[i].actor, name, sizeof name)) continue;
        char bone[24];
        bone_of(m[i].actor, bone);
        if (names && at + strlen(name) + strlen(bone) + 12 < nnames)
            at += (size_t)snprintf(names + at, nnames - at, " %s@%s%s", name, bone[0] ? bone : "?",
                                   shown(m[i].model) ? "" : "(hidden)");
        if (!strncmp(name, "Weapon_Sword_", 13)) {
            g.sword = has(name, "Broken") ? SWORD_BROKEN
                      : has(name, "Long") || has(name, "Biggoron") || has(name, "Giant") ? SWORD_BIGGORON
                      : has(name, "Kokiri")                                                ? SWORD_KOKIRI
                                                                                           : SWORD_MASTER;
            g.sword_held = (uint8_t)held(m[i].actor);
        } else if (!strncmp(name, "Weapon_Shield_", 14)) {
            g.shield = has(name, "Deku") ? SHIELD_DEKU : has(name, "Mirror") ? SHIELD_MIRROR : SHIELD_HYLIAN;
            g.shield_held = (uint8_t)held(m[i].actor);
        } else if (!strncmp(name, "Item_Ocarina_", 13)) {   // carried on his hip, in hand while played
            if (held(m[i].actor) && !has(name, "Holder"))
                g.right_item = has(name, "Fairy") ? N64I_OCARINA_FAIRY : N64I_OCARINA_TIME;
        } else if (!strncmp(name, "Weapon_Range_", 13) || !strncmp(name, "Item_Boomerang", 14) ||
                   !strncmp(name, "Item_Hammer", 11) || !strncmp(name, "Item_Bottle", 11) ||
                   !strncmp(name, "Item_DekuStick", 14)) {   // in hand while used (some on his back while chosen)
            if (!shown(m[i].model) || !held(m[i].actor)) continue;
            if (has(name, "Bow")) g.right_item = N64I_BOW;
            else if (has(name, "SlingShot") || has(name, "Slingshot")) g.right_item = N64I_SLINGSHOT;
            else if (has(name, "shot")) g.right_item = N64I_HOOKSHOT;   // Hookshot, Longshot
            else if (has(name, "Boomerang")) g.left_item = N64I_BOOMERANG;
            else if (has(name, "Hammer")) g.left_item = N64I_HAMMER;
            else if (has(name, "Bottle")) g.left_item = N64I_BOTTLE_HAND, g.bottle = 1;
            else if (has(name, "DekuStick")) g.stick = g.stick_held = 1;
        } else if (!strncmp(name, "Armor_", 6) && has(name, "_Mask_")) {
            if (shown(m[i].model)) g.mask = (int8_t)mask_of(name);
        } else if (!strncmp(name, "Armor_", 6) && (has(name, "HeavyBoot") || has(name, "HoverBoot"))) {
            if (shown(m[i].model)) g.boots = has(name, "Hover") ? BOOTS_HOVER : BOOTS_IRON;
        } else if (!strncmp(name, "Armor_", 6) && has(name, "Gauntlet")) {
            if (!shown(m[i].model)) continue;
            if (has(name, "Goron")) g.bracelet = 1;
            else g.gauntlets = has(name, "Gold") ? GAUNTLETS_GOLD : GAUNTLETS_SILVER;
        } else if (!strncmp(name, "Armor_Adult_", 12) && !has(name, "Gauntlet") && !has(name, "Boot") &&
                   !has(name, "Mask")) {
            if (has(name, "Goron")) g.tunic = 1;
            else if (has(name, "Zora")) g.tunic = 2;
        }
    }
    *out = g;
    return nseen > 0;
}
static void read_gear(void) {
    MotionOwner m[MOTION_OWNER_MODELS];
    int n = motion_owner_models(m, MOTION_OWNER_MODELS);
    char names[1024];
    gear_of(m, n, 0, &gear, names, sizeof names);
    // The preview's equipment comes from a heap scan run now and then (debug_transform.c preview_frame): while a new
    // scan is due, its last gear holds a short while before the player's stands in.
    static int missed = PREVIEW_GEAR_HOLD;
    Gear pg;
    if (gear_of(m, n, 1, &pg, NULL, 0)) preview_gear = pg, missed = 0;
    else if (missed < PREVIEW_GEAR_HOLD) missed++;
    else preview_gear = gear;
    // what he carries, once per change, for the log
    static char last[1024];
    static int told;
    if (n && strcmp(last, names) && told < 200) {
        memcpy(last, names, sizeof last);
        told++;
        rt_log("n64: the player carries%s", names[0] ? names : " nothing");
    }
}

// The limbs that draw a model of the table in their place: 15 the left hand, 18 the right hand, 19 the sheath.
#define R_FOOT 5
#define L_FOOT 8
#define HEAD_LIMB 10
#define L_FOREARM 14
#define L_HAND 15
#define R_FOREARM 17
#define R_HAND 18
#define SHEATH 19
#define NEXTRA 3   // the pieces a limb draws besides its model or the row in its place
// The table row each of those limbs draws for this gear (n64gfx.h N64ROW_*), -1 for the limb's own model, -2 for
// nothing.
//
// The remake lets either age carry any sword or shield, the N64 game's lists only the age's own. With the file's
// pieces for the rest (N64X_*), a limb draws two nodes: the age's own closed hand and the other age's sword without
// its hand, the other age's sheath and the shield alone on the back, a closed hand and the shield alone in hand. extra:
// up to NEXTRA pieces drawn with the row (a piece: N64X_*, or ITEM(N64I_*)), -1 none. A row below -2 is the piece
// -3 - row drawn in the limb's place.
static int own_shield(const N64File *f, int with, int without) {   // the age's lists have this shield
    return f->player[N64_TUNICS + with] != f->player[N64_TUNICS + without];
}
#define ITEM(x) (N64XROWS + (x))
static int piece_ok(const N64File *f, int piece) {
    uint32_t i = (uint32_t)(N64_TUNICS + N64ROWS + piece);
    return piece >= 0 && i < f->nplayer && f->player[i] < f->nnodes;
}
static void add_piece(const N64File *f, int extra[LIMBS][NEXTRA], int limb, int piece) {
    if (!piece_ok(f, piece)) return;
    for (int k = 0; k < NEXTRA; k++)
        if (extra[limb][k] < 0) {
            extra[limb][k] = piece;
            return;
        }
}
// The hand with an item: the age's own row of it, else the age's closed hand and the other age's item alone.
static void hand_item(const N64File *f, int item, int limb, int closed, int row[LIMBS], int extra[LIMBS][NEXTRA]) {
    for (int k = 0; k < NEXTRA; k++) extra[limb][k] = -1;
    if (piece_ok(f, ITEM(item))) row[limb] = -3 - ITEM(item);
    else row[limb] = closed, add_piece(f, extra, limb, ITEM(item + 1));
}
// What he holds and wears besides sword and shield, over sword_rows' choice. The N64 game draws a mask over the head,
// boots over the feet, the gauntlets' plates over the forearms and hands, each in that limb's space.
static void item_rows(const Gear *g, const N64File *f, int row[LIMBS], int extra[LIMBS][NEXTRA]) {
    if (f->nplayer < N64_TUNICS + N64ROWS + N64XROWS + N64IROWS) return;
    if (g->right_item >= 0) hand_item(f, g->right_item, R_HAND, N64ROW_RH_CLOSED, row, extra);
    if (g->left_item >= 0) hand_item(f, g->left_item, L_HAND, N64ROW_LH_CLOSED, row, extra);
    if (g->bottle) add_piece(f, extra, L_HAND, ITEM(N64I_BOTTLE));
    if (g->stick) {
        for (int k = 0; k < NEXTRA; k++) extra[L_HAND][k] = -1;
        row[L_HAND] = N64ROW_LH_CLOSED;
        add_piece(f, extra, L_HAND, ITEM(N64I_DEKU_STICK));
    }
    if (g->mask >= 0) add_piece(f, extra, HEAD_LIMB, ITEM(g->mask));
    if (g->boots) {
        int iron = g->boots == BOOTS_IRON;
        add_piece(f, extra, L_FOOT, ITEM(iron ? N64I_IRON_BOOT_L : N64I_HOVER_BOOT_L));
        add_piece(f, extra, R_FOOT, ITEM(iron ? N64I_IRON_BOOT_R : N64I_HOVER_BOOT_R));
    }
    if (g->bracelet) add_piece(f, extra, L_FOREARM, ITEM(N64I_GORON_BRACELET));
    if (g->gauntlets) {   // a plate on each forearm, and one on each hand made for it open or closed
        int base = g->gauntlets == GAUNTLETS_GOLD ? N64I_GOLD_FOREARM_L : N64I_SILVER_FOREARM_L;
        int l_open = row[L_HAND] == -1 || row[L_HAND] == N64ROW_LH_OPEN;
        int r_open = row[R_HAND] == -1 || row[R_HAND] == N64ROW_RH_OPEN;
        add_piece(f, extra, L_FOREARM, ITEM(base));
        add_piece(f, extra, R_FOREARM, ITEM(base + 3));
        if (row[L_HAND] != -2) add_piece(f, extra, L_HAND, ITEM(base + (l_open ? 2 : 1)));
        if (row[R_HAND] != -2) add_piece(f, extra, R_HAND, ITEM(base + 3 + (r_open ? 2 : 1)));
    }
}
// A shield in his right hand. A child holds the Hylian Shield on his back, and a two-handed sword leaves no hand for a
// shield.
static int shield_held_in_hand(const Gear *g, int child) {
    int two = g->sword == SWORD_BIGGORON || g->sword == SWORD_BROKEN;
    return g->shield && g->shield_held && !(child && g->shield == SHIELD_HYLIAN) && !(two && g->sword_held);
}
static void sword_rows(const Gear *g, int child, const N64File *f, int row[LIMBS], int extra[LIMBS][NEXTRA]);
static void gear_rows(const Gear *g, int child, const N64File *f, int row[LIMBS], int extra[LIMBS][NEXTRA]) {
    for (int i = 0; i < LIMBS; i++) {
        row[i] = -1;
        for (int k = 0; k < NEXTRA; k++) extra[i][k] = -1;
    }
    sword_rows(g, child, f, row, extra);
    item_rows(g, f, row, extra);
}
static void sword_rows(const Gear *g, int child, const N64File *f, int row[LIMBS], int extra[LIMBS][NEXTRA]) {
    if (f->nplayer >= N64_TUNICS + N64ROWS + N64XROWS) {
        int foreign = child ? g->sword >= SWORD_MASTER : g->sword == SWORD_KOKIRI;
        int two = g->sword == SWORD_BIGGORON || g->sword == SWORD_BROKEN;
        if (g->sword && g->sword_held && foreign) {
            row[L_HAND] = N64ROW_LH_CLOSED;
            extra[L_HAND][0] = g->sword == SWORD_BIGGORON ? N64X_BIGGORON
                            : g->sword == SWORD_BROKEN ? N64X_BROKEN_KNIFE : N64X_SWORD;
        } else if (g->sword && g->sword_held)
            row[L_HAND] = g->sword == SWORD_BIGGORON ? N64ROW_LH_BIGGORON
                          : g->sword == SWORD_BROKEN ? N64ROW_LH_BROKEN_KNIFE : N64ROW_LH_SWORD;
        int s = g->shield;
        int in_hand = shield_held_in_hand(g, child);
        if (in_hand) {
            int with = N64ROW_RH_CLOSED + s;   // RH_DEKU, RH_HYLIAN, RH_MIRROR follow RH_CLOSED
            if (own_shield(f, with, N64ROW_RH_CLOSED)) row[R_HAND] = with;
            else row[R_HAND] = N64ROW_RH_CLOSED, extra[R_HAND][0] = N64X_HAND_DEKU + s - SHIELD_DEKU;
        } else if (two && g->sword_held)
            row[R_HAND] = N64ROW_RH_CLOSED;
        int back = in_hand ? SHIELD_NONE : s, sheathed = g->sword && !g->sword_held;
        if (!g->sword && !back) {
            row[SHEATH] = g->shield ? N64ROW_SHEATH_EMPTY : -2;
        } else if (g->sword && foreign) {   // the other age's sheath, the shield alone over it
            row[SHEATH] = -3 - (sheathed ? N64X_SHEATH_SWORD : N64X_SHEATH_EMPTY);
            if (back) extra[SHEATH][0] = N64X_BACK_DEKU + back - SHIELD_DEKU;
        } else {
            int base = sheathed ? N64ROW_SHEATH_SWORD : N64ROW_SHEATH_EMPTY;
            if (!back || own_shield(f, N64ROW_SHEATH_EMPTY + back, N64ROW_SHEATH_EMPTY)) row[SHEATH] = base + back;
            else row[SHEATH] = base, extra[SHEATH][0] = N64X_BACK_DEKU + back - SHIELD_DEKU;
        }
        return;
    }
    // a file without the pieces: the age's own lists for everything
    int two_hands = g->sword == SWORD_BIGGORON || g->sword == SWORD_BROKEN;
    if (g->sword && g->sword_held)
        row[L_HAND] = g->sword == SWORD_BIGGORON ? N64ROW_LH_BIGGORON
                      : g->sword == SWORD_BROKEN ? N64ROW_LH_BROKEN_KNIFE : N64ROW_LH_SWORD;
    int shield_in_hand = shield_held_in_hand(g, child);
    if (shield_in_hand)
        row[R_HAND] = g->shield == SHIELD_DEKU ? N64ROW_RH_DEKU : g->shield == SHIELD_MIRROR ? N64ROW_RH_MIRROR
                                                                                           : N64ROW_RH_HYLIAN;
    else if (two_hands && g->sword_held) row[R_HAND] = N64ROW_RH_CLOSED;
    int back = shield_in_hand ? SHIELD_NONE : g->shield;   // the shield the sheath's model carries
    if (!g->sword && !g->shield) row[SHEATH] = -2;
    else row[SHEATH] = (g->sword && !g->sword_held ? N64ROW_SHEATH_SWORD : N64ROW_SHEATH_EMPTY) + back;
}

// ---- the ocarina at his mouth
//
// The remake's Link holds his ocarina his own way; N64 Link posed by the same bones holds it at his chest, tilted. While
// he plays, his head and arms take the frame of his own animations in which he plays (against his upper body), then
// both wrists move by what the ocarina's mouthpiece still misses his mouth by (the mod's points: tools/n64link.py
// ocarina_points), each arm bending to reach, the hands keeping their turn. OCARINA_REACH_M: how far from the
// remake's head the remake's wrist is when it plays; OCARINA_LOWERED_M, when it lowers the ocarina: the pose fades in
// and out between the two, as he takes it out and away.
#define OCARINA_REACH_M 0.25f
#define OCARINA_LOWERED_M 0.45f
#define L_UPPER_ARM 13
#define R_UPPER_ARM 16

// The arm upper, upper + 1, upper + 2 (upper arm, forearm, hand) bent so the hand's joint reaches target: the elbow
// stays in the plane it bends in now. The hand keeps its turn.
static void reach(int upper, const float target[3], float s, float limb_at[LIMBS][3], M3 limb[LIMBS],
                  float at[LIMBS][3]) {
    int fore = upper + 1, hand = upper + 2;
    float l1 = s * norm3(limb_at[fore]), l2 = s * norm3(limb_at[hand]), to[3], n[3], elbow[3], pole[3];
    sub3(target, at[upper], to);
    float d = norm3(to);
    if (!(d > 1e-4f) || !(l1 > 1e-4f) || !(l2 > 1e-4f)) return;
    for (int k = 0; k < 3; k++) n[k] = to[k] / d;
    d = fminf(fmaxf(d, fabsf(l1 - l2) + 1e-4f), l1 + l2 - 1e-4f);
    float a = (l1 * l1 - l2 * l2 + d * d) / (2 * d), h = sqrtf(fmaxf(l1 * l1 - a * a, 0));
    sub3(at[fore], at[upper], pole);
    float along = pole[0] * n[0] + pole[1] * n[1] + pole[2] * n[2];
    for (int k = 0; k < 3; k++) pole[k] -= along * n[k];
    float np = norm3(pole);
    if (!(np > 1e-5f)) return;
    for (int k = 0; k < 3; k++) elbow[k] = at[upper][k] + a * n[k] + h * pole[k] / np;
    float was[3], want[3];
    M3 turn, r;
    sub3(at[fore], at[upper], was);
    sub3(elbow, at[upper], want);
    rot_between(was, want, turn);
    m3_mul(turn, limb[upper], r);
    memcpy(limb[upper], r, sizeof r);
    m3_mul(turn, limb[fore], r);   // the forearm turned with it, then onto the target
    float fwas[3], fwant[3];
    m3_vec(r, limb_at[hand], fwas);
    sub3(target, elbow, fwant);
    rot_between(fwas, fwant, turn);
    m3_mul(turn, r, limb[fore]);
    memcpy(at[fore], elbow, sizeof elbow);
    memcpy(at[hand], target, sizeof at[hand]);
}
static float smooth01(float x) {
    x = fminf(fmaxf(x, 0), 1);
    return x * x * (3 - 2 * x);
}
static void ocarina_to_mouth(Rig *g, int item, float played, M3 limb[LIMBS], float at[LIMBS][3]) {
    static const uint8_t posed[] = {HEAD_LIMB, L_UPPER_ARM, L_UPPER_ARM + 1, L_HAND, R_UPPER_ARM, R_UPPER_ARM + 1, R_HAND};
    M3 head_was, turn;   // the hat turns with the head: turn = new head x old head transposed
    memcpy(head_was, limb[HEAD_LIMB], sizeof head_was);
    for (size_t k = 0; k < sizeof posed; k++) {
        M3 want;
        m3_mul(limb[UPPER_BODY], g->play[posed[k]], want);
        m3_blend(limb[posed[k]], want, played, limb[posed[k]]);
    }
    for (int r = 0; r < 3; r++)
        for (int c = 0; c < 3; c++)
            turn[r][c] = limb[HEAD_LIMB][r][0] * head_was[c][0] + limb[HEAD_LIMB][r][1] * head_was[c][1] +
                         limb[HEAD_LIMB][r][2] * head_was[c][2];
    M3 hat;
    m3_mul(turn, limb[HEAD_LIMB + 1], hat);
    memcpy(limb[HEAD_LIMB + 1], hat, sizeof hat);
    for (int i = HEAD_LIMB; i < LIMBS; i++) {   // the joints again, parents first
        float d[3];
        m3_vec(limb[g->parent[i]], g->limb_at[i], d);
        for (int k = 0; k < 3; k++) at[i][k] = at[g->parent[i]][k] + g->scale * d[k];
    }
    const int16_t *pt = g->file->rest + REST_VALUES;
    const int16_t *tip = pt + (item == N64I_OCARINA_FAIRY ? 3 : 6);
    float mouth_l[3] = {pt[0], pt[1], pt[2]}, tip_l[3] = {tip[0], tip[1], tip[2]}, mouth[3], piece[3], miss[3];
    m3_vec(limb[HEAD_LIMB], mouth_l, mouth);
    m3_vec(limb[R_HAND], tip_l, piece);
    for (int k = 0; k < 3; k++)
        miss[k] = played * (at[HEAD_LIMB][k] + g->scale * mouth[k] - at[R_HAND][k] - g->scale * piece[k]);
    float r[3], l[3];
    for (int k = 0; k < 3; k++) r[k] = at[R_HAND][k] + miss[k], l[k] = at[L_HAND][k] + miss[k];
    reach(R_UPPER_ARM, r, g->scale, g->limb_at, limb, at);
    reach(L_UPPER_ARM, l, g->scale, g->limb_at, limb, at);
}

// ---- his face
//
// The mod draws his eyes once per eye texture and his mouth once per mouth (tools/n64link.py face_variants); one of
// each shows. The remake's face bones stay still in the skeleton's matrices, so the face is the N64 game's way: he
// blinks now and then (half shut, shut, half shut), and his mouth opens as he cries out: while his sword swings fast,
// as he jumps, and when he is hurt.
#define FACE_NODES (N64_TUNICS + N64ROWS + N64XROWS + N64IROWS)
#define BLINK_MIN_NS 1500000000ull   // between blinks: 1.5 to 5 s
#define BLINK_SPREAD_NS 3500000000ull
#define BLINK_STEP_NS 50000000ull    // each of the blink's three steps
#define CRY_NS 400000000ull          // the mouth stays open this long after a cry starts
#define CRY_EDGE_NS 70000000ull      // half open at its start and end
#define SWING_MPS 4.0f               // the sword's hilt this fast against his hips: a swing
#define JUMP_MPS 2.5f                // his hips rising this fast: a jump
enum { EYE_OPEN, EYE_HALF_SHUT, EYE_SHUT };
enum { MOUTH_SHUT, MOUTH_HALF_OPEN, MOUTH_OPEN };
typedef struct {
    uint64_t blink_at, cry_at, last_ns;
    float hilt[3], hips[3];
    int seen, health;
    uint32_t seed;
} Face;
static Face faces[POSES];
static uint64_t next_blink(Face *fc, uint64_t now) {
    fc->seed = fc->seed * 1664525u + 1013904223u;
    return now + BLINK_MIN_NS + (uint64_t)(fc->seed >> 8) % BLINK_SPREAD_NS;
}
// hilt: the remake's weapon bone; hips: his hip bone (model space, metres)
static void face_of(int who, const float hilt[3], const float hips[3], int *eye, int *mouth) {
    Face *fc = &faces[who];
    uint64_t now = plat_now_ns();
    float dt = fc->seen ? (float)(now - fc->last_ns) * 1e-9f : 0;
    if (!fc->seen || dt > 0.5f) {   // the first frame, or after a while away: start afresh
        fc->seed ^= (uint32_t)now;
        fc->blink_at = next_blink(fc, now), fc->cry_at = 0, fc->health = -1;
    } else if (dt > 1e-4f) {
        float rel[3], was[3], speed = 0;
        for (int k = 0; k < 3; k++) rel[k] = hilt[k] - hips[k], was[k] = fc->hilt[k] - fc->hips[k];
        sub3(rel, was, rel);
        speed = norm3(rel) / dt;
        float rise = (hips[1] - fc->hips[1]) / dt;
        int hurt = 0, cur, max;
        if (who == POSE_PLAYER && dbg_health_get(&cur, &max)) {
            hurt = fc->health >= 0 && cur < fc->health;
            fc->health = cur;
        }
        int sword = who == POSE_PLAYER ? gear.sword_held : 0;
        if ((sword && speed > SWING_MPS) || rise > JUMP_MPS || hurt)
            if (now - fc->cry_at > CRY_NS) fc->cry_at = now;
    }
    fc->seen = 1, fc->last_ns = now;
    memcpy(fc->hilt, hilt, sizeof fc->hilt);
    memcpy(fc->hips, hips, sizeof fc->hips);
    if (now >= fc->blink_at + 3 * BLINK_STEP_NS) fc->blink_at = next_blink(fc, now);
    uint64_t into = now >= fc->blink_at ? now - fc->blink_at : UINT64_MAX;
    *eye = into < BLINK_STEP_NS ? EYE_HALF_SHUT : into < 2 * BLINK_STEP_NS ? EYE_SHUT
         : into < 3 * BLINK_STEP_NS ? EYE_HALF_SHUT : EYE_OPEN;
    uint64_t cry = fc->cry_at ? now - fc->cry_at : UINT64_MAX;
    *mouth = cry < CRY_EDGE_NS || (cry >= CRY_NS - CRY_EDGE_NS && cry < CRY_NS) ? MOUTH_HALF_OPEN
           : cry < CRY_NS ? MOUTH_OPEN : MOUTH_SHUT;
}

void n64pose_game_frame(void) {
    int link = n64pose_wanted();
    if (!link && !n64actors_wanted()) return;
    n64actors_game_frame();
    if (link) read_gear();
    uint64_t a = dbg_player_actor(), c = a ? ptr(a + 0x270) : 0, comp = c ? ptr(c + 8) : 0;
    uint64_t group = comp ? ptr(comp + 0x18) : 0, models = group ? ptr(group + 0x18) : 0;
    __atomic_store_n(&posed_model[POSE_PLAYER], models ? ptr(models) : 0, __ATOMIC_RELEASE);
    // the preview's body: its first model (motion_owner tracks it while the preview actor is known)
    MotionOwner m[MOTION_OWNER_MODELS];
    int n = motion_owner_models(m, MOTION_OWNER_MODELS);
    uint64_t preview = 0;
    for (int i = 0; i < n && !preview; i++)
        if ((m[i].flags & MOTION_OWNER_PREVIEW) && !m[i].kind) preview = m[i].model;
    __atomic_store_n(&posed_model[POSE_PREVIEW], preview, __ATOMIC_RELEASE);
}

// The skeletons are posed in the space the model matrix places a model in, which lies a whole number of metres (per
// area) away from the world the actors' positions and the scene's camera use. The shift is the player actor's position
// less his model's, kept until it moves by more than SHIFT_SLACK_M (the model may stand a little off its actor), and
// rounded to whole metres. Every model of the area takes it (N64 Link and the characters, n64actors.c).
static float world_shift[3];
static int have_shift;
// The offset a model's pose is made relative to: the game poses each model's bones within a cell of the world, a
// whole number of 10 m steps from the origin, and keeps that cell's corner at model+0x2d0 (three floats). The hall of
// defeated foes' statues sit in cells of their own, 10 or 20 m from the player's. Zero when it does not read.
static void model_offset(uint64_t model, float out[3]) {
    out[0] = out[1] = out[2] = 0;
    float v[3];
    if (!model || !rd(model + 0x2d0, v, sizeof v)) return;
    for (int k = 0; k < 3; k++)
        if (!isfinite(v[k]) || fabsf(v[k]) > 1e5f) return;
    memcpy(out, v, sizeof v);
}

static void note_shift(uint64_t matrix) {
    float model_at[3], actor_at[3];
    if (!rd(matrix + 0x30, model_at, 12) || !dbg_player_pos(actor_at)) return;
    float raw[3], moved = 0;
    for (int k = 0; k < 3; k++)
        raw[k] = actor_at[k] - model_at[k], moved = fmaxf(moved, fabsf(raw[k] - world_shift[k]));
    if (!have_shift || moved > SHIFT_SLACK_M) {
        for (int k = 0; k < 3; k++) world_shift[k] = roundf(raw[k]);
        have_shift = 1;
    }
}

// Another model's pose for n64actors. A model posed away from the world's origin keeps its own offset (model_offset);
// the player's shift holds the player model's, so it is traded for this one's.
static int actor_ready(uint64_t model) {
    float sh[3], own[3], player[3];
    model_offset(model, own);
    model_offset(__atomic_load_n(&posed_model[POSE_PLAYER], __ATOMIC_ACQUIRE), player);
    for (int k = 0; k < 3; k++) sh[k] = world_shift[k] + own[k] - player[k];
    return n64actors_world_ready(model, sh);
}

// Models to hand on with the player's next pose (n64pose_world_ready): posed while the player's shift was not known
// yet, or before their bones' world matrices were written. Each is asked again for at most PENDING_TRIES frames.
#define PENDING 512
#define PENDING_TRIES 600
static uint64_t pending[PENDING];
static uint16_t pending_tries[PENDING];
static int npending;
static PlatMutex pending_lock = PLAT_MUTEX_INIT;

static void pend(uint64_t model, uint16_t tries) {
    plat_mutex_lock(&pending_lock);
    int i = 0;
    while (i < npending && pending[i] != model) i++;
    if (i == npending && npending < PENDING) pending[npending++] = model, pending_tries[i] = tries;
    plat_mutex_unlock(&pending_lock);
}

void n64pose_world_ready(uint64_t model, uint64_t matrix) {
    if (!model) return;
    int link = n64pose_wanted(), actors = n64actors_wanted();
    if (!link && !actors) return;
    int who = model == __atomic_load_n(&posed_model[POSE_PLAYER], __ATOMIC_ACQUIRE)    ? POSE_PLAYER
              : model == __atomic_load_n(&posed_model[POSE_PREVIEW], __ATOMIC_ACQUIRE) ? POSE_PREVIEW
                                                                                        : -1;
    if (who == POSE_PLAYER) {
        note_shift(matrix);
        // models posed before the player's shift was known (a statue is posed once, as its room loads) are handed on
        // now: their bones' world matrices still hold that pose
        static uint64_t take[PENDING];
        static uint16_t take_tries[PENDING];
        plat_mutex_lock(&pending_lock);
        int n = npending;
        memcpy(take, pending, n * sizeof *take), memcpy(take_tries, pending_tries, n * sizeof *take_tries);
        npending = 0;
        plat_mutex_unlock(&pending_lock);
        for (int i = 0; i < n; i++)
            if (actors && (!have_shift || actor_ready(take[i]) == 0) && take_tries[i] < PENDING_TRIES)
                pend(take[i], take_tries[i] + 1);
    }
    if (who < 0 && actors && (!have_shift || actor_ready(model) == 0)) pend(model, 0);
    if (!link) return;
    if (who < 0) {
        // Another model with a skeleton of Link's size (309 bones the child's, 327 the adult's) away from the origin
        // (the title screen keeps its own at the origin): a cutscene's own Link would be one. Noted once each, as the
        // game draws it and not N64 Link.
        static uint64_t noted[8];
        static int nnoted;
        uint64_t sk = ptr(model + 0xf8), res = sk ? ptr(sk) : 0;
        uint16_t nb = 0;
        float at[3] = {0};
        if (nnoted < 8 && res && rd(res + 0x38, &nb, 2) && (nb == 309 || nb == 327) && rd(matrix + 0x30, at, 12) &&
            fabsf(at[0]) + fabsf(at[1]) + fabsf(at[2]) > 0.01f) {
            int k = 0;
            while (k < nnoted && noted[k] != model) k++;
            if (k == nnoted) {
                noted[nnoted++] = model;
                rt_log("n64: a model with a skeleton of Link's (%u bones) that is not the player's is drawn at (%.1f %.1f "
                       "%.1f): the game's own", nb, (double)at[0], (double)at[1], (double)at[2]);
            }
        }
        return;
    }
    if (motion_owner_transformed()) return;   // a Goron or Zora body stands in: the remake draws it
    uint64_t skel = ptr(model + 0xf8), resource = skel ? ptr(skel) : 0, mats = skel ? ptr(skel + 0x20) : 0;
    if (!resource || !mats) return;
    Rig *g = &rigs[who];
    plat_mutex_lock(&rig_lock);
    if (g->resource != resource) build_rig(resource, g);
    if (!g->ok) {
        plat_mutex_unlock(&rig_lock);
        return;
    }
    // this frame's remake bones, then the limbs
    M3 rot[NAMES];
    float t[NAMES][3];
    for (int k = 0; k < NAMES; k++) {
        float m[16];
        if (!rd(mats + (uint64_t)g->bone[k] * 0x40, m, sizeof m)) {
            plat_mutex_unlock(&rig_lock);
            return;
        }
        for (int r = 0; r < 3; r++) rot[k][r][0] = m[r], rot[k][r][1] = m[4 + r], rot[k][r][2] = m[8 + r];
        t[k][0] = m[12], t[k][1] = m[13], t[k][2] = m[14];
        if (!unit_columns(rot[k]) || !isfinite(t[k][0] + t[k][1] + t[k][2])) {
            plat_mutex_unlock(&rig_lock);
            return;
        }
    }
    int child = g->file->number == 21;
    Gear gr = who == POSE_PREVIEW ? preview_gear : gear;
    if (n64gfx_part(N64_PART_ITEMS)) {
        // An item in a hand takes that hand, as the N64 game draws it: the sword goes to its sheath, the shield to his
        // back.
        if (gr.right_item >= 0) gr.shield_held = 0;
        if (gr.left_item >= 0 || gr.stick) gr.sword_held = 0;
    } else {   // the items part off: the remake draws them, N64 Link keeps only what he wears
        gr.right_item = gr.left_item = gr.mask = -1;
        gr.bottle = gr.stick = gr.stick_held = 0;
    }
    int row[LIMBS], extra[LIMBS][NEXTRA];
    gear_rows(&gr, child, g->file, row, extra);
    M3 limb[LIMBS];
    float at[LIMBS][3];
    for (int i = 0; i < LIMBS; i++) {   // a sword, or the Deku stick swung as one, follows the remake's weapon bone
        if (i == L_HAND && ((gr.sword && gr.sword_held) || (gr.stick && gr.stick_held)))
            m3_mul(rot[WEAPON_L], sword_in_weapon, limb[i]);
        else if (i == R_HAND && shield_held_in_hand(&gr, child) && g->bone[WEAPON_R] != g->bone[WRIST_R])
            m3_mul(rot[WEAPON_R], shield_in_weapon, limb[i]);
        else m3_mul(rot[follow[i]], g->offset[i], limb[i]);
        if (!i) {
            float lift[3];
            m3_vec(rot[SKL_ROOT], g->lift, lift);
            for (int k = 0; k < 3; k++) at[0][k] = t[SKL_ROOT][k] + lift[k];
            continue;
        }
        float d[3];
        m3_vec(limb[g->parent[i]], g->limb_at[i], d);
        for (int k = 0; k < 3; k++) at[i][k] = at[g->parent[i]][k] + g->scale * d[k];
    }
    if ((gr.right_item == N64I_OCARINA_FAIRY || gr.right_item == N64I_OCARINA_TIME) &&
        g->plays) {
        float far[3];
        sub3(t[WRIST_R], t[HEAD], far);
        float played = smooth01((OCARINA_LOWERED_M - norm3(far)) / (OCARINA_LOWERED_M - OCARINA_REACH_M));
        if (played > 0) ocarina_to_mouth(g, gr.right_item, played, limb, at);
    }
    int eye = EYE_OPEN, mouth = MOUTH_SHUT;
    face_of(who, t[WEAPON_L], t[SKL_ROOT], &eye, &mouth);
    const N64File *f = g->file;
    float s = g->scale;
    plat_mutex_unlock(&rig_lock);

    // into the world's space (note_shift); the preview is posed and seen in a space of its own: no shift
    float none[3] = {0, 0, 0}, *by = none;
    if (who == POSE_PLAYER) {
        if (!have_shift) return;
        by = world_shift;
    }

    // What the hands and the sheath limb draw: the limb's own model, or the table's model for the gear in its place
    // (the N64 game draws those lists instead of the limb's, with the limb's matrix).
    // the node drawn in the limb's place (-1 the limb's own, -2 nothing), and the nodes drawn with it (-1 none)
    int drawn[LIMBS], also[LIMBS][NEXTRA];
    for (int i = 0; i < LIMBS; i++) {
        drawn[i] = row[i] == -2 ? -2 : -1;
        for (int k = 0; k < NEXTRA; k++)
            also[i][k] = piece_ok(f, extra[i][k]) ? (int)f->player[N64_TUNICS + N64ROWS + extra[i][k]] : -1;
        if (f->nplayer < N64_TUNICS + N64ROWS) continue;
        if (row[i] <= -3) {   // a piece in place of the limb's model
            drawn[i] = piece_ok(f, -3 - row[i]) ? (int)f->player[N64_TUNICS + N64ROWS - 3 - row[i]] : -2;
            continue;
        }
        if (row[i] < 0) continue;
        uint32_t nd = f->player[N64_TUNICS + row[i]];
        if (nd == UINT32_MAX) drawn[i] = -2;
        else if (nd != (uint32_t)i) drawn[i] = (int)nd;
    }
    uint32_t tunic = f->nplayer >= N64_TUNICS ? f->player[gr.tunic < N64_TUNICS ? gr.tunic : 0] : 0xffffffffu;

    plat_mutex_lock(&pose_lock);
    __typeof__(poses[0]) *p = &poses[who];
    p->file = f, p->nodes = f->nnodes, p->tunic = tunic;
    memset(p->rows, 0, (size_t)f->nnodes * sizeof p->rows[0]);
    memset(p->on, 0, f->nnodes);
    for (int i = 0; i < LIMBS; i++) {
        for (int r = 0; r < 3; r++) {
            for (int c = 0; c < 3; c++) p->rows[i][4 * r + c] = s * limb[i][r][c];
            p->rows[i][4 * r + 3] = at[i][r] + by[r];
        }
        p->on[i] = drawn[i] == -1;
        for (int k = 0; k <= NEXTRA; k++) {   // a table's list and a piece are in the space of the limb they go with
            int nd = k ? also[i][k - 1] : drawn[i];
            if (nd < 0) continue;
            memcpy(p->rows[nd], p->rows[i], sizeof p->rows[0]);
            p->on[nd] = 1;
        }
    }
    if (f->nplayer >= FACE_NODES + N64_EYES + N64_MOUTHS) {   // with the head limb's matrix, as its vertices are bound
        uint32_t e = f->player[FACE_NODES + eye], m = f->player[FACE_NODES + N64_EYES + mouth];
        if (e < f->nnodes) p->on[e] = 1;
        if (m < f->nnodes) p->on[m] = 1;

    }
    p->at_ns = plat_now_ns();
    plat_mutex_unlock(&pose_lock);
}

static int fresh_locked(int who) { return poses[who].file && plat_now_ns() - poses[who].at_ns < STALE_NS; }

int n64pose_link(N64Item *item) {
    static float rows[MAX_NODES][12];
    if (!n64pose_wanted()) return 0;
    // the preview while its draws were met lately (the pause menu shows it in place of the scene), else the player
    int who = plat_now_ns() - __atomic_load_n(&preview_seen_ns, __ATOMIC_ACQUIRE) < PREVIEW_SEEN_NS ? POSE_PREVIEW
                                                                                                      : POSE_PLAYER;
    plat_mutex_lock(&pose_lock);
    int fresh = fresh_locked(who);
    const N64File *f = poses[who].file;
    uint32_t tunic = poses[who].tunic;
    if (fresh) {
        memcpy(rows, poses[who].rows, (size_t)poses[who].nodes * sizeof rows[0]);
        memcpy(node_on, poses[who].on, poses[who].nodes);
    }
    plat_mutex_unlock(&pose_lock);
    if (!fresh) return 0;
    item->file = f, item->rows = (const float(*)[12])rows, item->node_on = node_on, item->tunic = tunic;
    return 1;
}

int n64pose_wear(void) {
    plat_mutex_lock(&pose_lock);
    const N64File *f = poses[POSE_PLAYER].file;
    int items = f && f->nplayer >= N64_TUNICS + N64ROWS + N64XROWS + N64IROWS;
    plat_mutex_unlock(&pose_lock);
    return items;
}
int n64pose_items(void) { return n64gfx_part(N64_PART_ITEMS) && n64pose_wear(); }

int n64pose_active(void) {
    if (!n64pose_wanted() || !vk_n64_ready()) return 0;
    plat_mutex_lock(&pose_lock);
    int fresh = fresh_locked(POSE_PLAYER);
    plat_mutex_unlock(&pose_lock);
    return fresh;
}

int n64pose_preview_active(void) {
    if (!n64pose_wanted() || !vk_n64_ready()) return 0;
    plat_mutex_lock(&pose_lock);
    int fresh = fresh_locked(POSE_PREVIEW);
    plat_mutex_unlock(&pose_lock);
    return fresh;
}

uint64_t n64pose_preview_body(void) { return __atomic_load_n(&posed_model[POSE_PREVIEW], __ATOMIC_ACQUIRE); }

void n64pose_preview_drawn(void) { __atomic_store_n(&preview_seen_ns, plat_now_ns(), __ATOMIC_RELEASE); }
