// N64 Graphics' sword walls (n64sword.h).
//
// The blade. The player's skeleton (his model at [[[[[player+0x270]+0x08]+0x18]+0x18]], the model's skeleton at +0xf8,
// laid out as n64pose.c describes) is posed in the space the model matrix places it in, a whole number of metres away
// from the world the actors and the collision use: the shift is the player's position less the model matrix's
// translation, rounded to metres and kept until it moves by more than SHIFT_SLACK_M (as n64pose.c note_shift keeps
// it). The weapon in his left hand hangs from Weapon_L with its blade along the bone's +z; which weapon it is comes from
// the models motion_owner tracks for him: the one named Weapon_Sword_*, Item_DekuStick* or Item_Hammer* that hangs from
// Weapon_L (its attachment record [[actor+0x270]+0x18] names the bone at +0x94) and is shown (bit 0x80 of the model's
// byte at +0x12 clear).
//
// The strike. While the player's current action verb is an attack (the BSA's request [[[player+0x270]+0x188]+0x1c0]:
// its verb at +0x28, or at +0x4c when that holds 0x11a; the verbs index the game's verb name table, attack_verb) and
// the blade's tip moves faster than SWING_MPS against him, three segments are tested against the collision polygons
// within reach of him (debug_hook.c dbg_hook_polys): the blade from BLADE_FROM of its length to the tip, the tip's path
// since the last frame and the blade middle's. A polygon counts when it is a wall (its normal within WALL_NY of level),
// not an invisible one (AirWall), the hilt is on its open side and the tip moves into it. None is looked for again for
// COOLDOWN_NS.
//
// The rebound. The scheduler (debug_game.c n64_sword_wall) is told the strike (n64sword_take) from the strike until
// the rebound action has started, at most HOLD_NS: the scheduler goes back to no reaction on any frame the hit state
// stops asking for one before the action's start has set its flag (bit 3 of the player state's [[player+0x270]+0x358]
// +0xe4c, which then keeps the scheduler's choice), so a single frame's AttackCollide only cut the swing short.
#include "n64sword.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "debug_tools.h"
#include "host.h"
#include "motion_owner.h"
#include "n64gfx.h"
#include "platform/platform.h"

#define SWING_MPS 4.0f            // the tip this fast against the player: a swing
#define BLADE_FROM 0.15f          // the tested blade starts this far along it (the hand itself does not strike)
#define WALL_NY 0.5f              // |normal.y| below this: a wall, not a floor or a ceiling
#define REACH_EXTRA_M 0.8f       // polygons within the blade's length and this of him are tested (his hand's reach)
#define MAX_POLYS 4096
#define SHIFT_SLACK_M 2.0f
#define HOLD_NS 400000000ull      // the scheduler is told a strike at most this long, until the rebound starts
#define COOLDOWN_NS 700000000ull  // no new strike this long after one
#define MAX_BONES 1024

static int rd(uint64_t a, void *out, size_t n) { return a && plat_mem_read(a, out, n); }
static uint64_t ptr(uint64_t a) {
    uint64_t p = 0;
    return rd(a, &p, 8) ? p : 0;
}

// The player's current action verb (the game's verb name table: 0x70 BasicAttack ... 0x7e HoverRangedAttack, 0xac
// LeftHandAttack, 0xb4 ConsecutiveAttack, 0xdd FallAttack), -1 when unknown.
static int current_verb(uint64_t actor) {
    uint64_t c = actor ? ptr(actor + 0x270) : 0, bsa = c ? ptr(c + 0x188) : 0, req = bsa ? ptr(bsa + 0x1c0) : 0;
    uint32_t v = 0;
    if (!req || !rd(req + 0x28, &v, 4)) return -1;
    if (v == 0x11a && !rd(req + 0x4c, &v, 4)) return -1;
    return (int)v;
}
static int attack_verb(int v) {
    return (v >= 0x70 && v <= 0x7e && v != 0x79 && v != 0x7a) || v == 0xac || v == 0xb4 || v == 0xdd;
}

int n64sword_wants_collision(void) { return n64gfx_part(N64_PART_SWORD_WALLS); }

// ---- the weapon in his left hand: its blade length in metres, 0 for none
static float held_blade(char *name, size_t n) {
    MotionOwner m[MOTION_OWNER_MODELS];
    int count = motion_owner_models(m, MOTION_OWNER_MODELS);
    for (int i = 0; i < count; i++) {
        if (!m[i].kind || (m[i].flags & MOTION_OWNER_PREVIEW) || !m[i].actor) continue;
        char nm[48] = {0}, bone[24] = {0};
        uint8_t hidden = 0x80;
        uint64_t rec = ptr(ptr(m[i].actor + 0x270) + 0x18);
        if (!rd(ptr(m[i].actor + 0x20), nm, sizeof nm - 1) || !rec || !rd(rec + 0x94, bone, sizeof bone - 1) ||
            strcmp(bone, "Weapon_L") || !rd(m[i].model + 0x12, &hidden, 1) || (hidden & 0x80))
            continue;
        float len = 0;
        if (!strncmp(nm, "Weapon_Sword_", 13))
            len = strstr(nm, "Broken")                                                      ? 0.30f
                  : strstr(nm, "Long") || strstr(nm, "Biggoron") || strstr(nm, "Giant") ? 1.20f
                  : strstr(nm, "Kokiri")                                                    ? 0.55f
                                                                                           : 0.85f;
        else if (!strncmp(nm, "Item_DekuStick", 14)) len = 0.80f;
        else if (!strncmp(nm, "Item_Hammer", 11)) len = 0.75f;
        if (len > 0) {
            snprintf(name, n, "%s", nm);
            return len;
        }
    }
    return 0;
}

// ---- the Weapon_L bone of a skeleton resource, looked up once per resource
static int weapon_bone(uint64_t resource) {
    static uint64_t seen;
    static int bone = -1;
    if (resource == seen) return bone;
    seen = resource, bone = -1;
    uint64_t bones = ptr(resource + 0x10);
    uint16_t n = 0;
    if (!bones || !rd(resource + 0x38, &n, 2) || !n || n > MAX_BONES) return -1;
    for (int i = 0; i < n; i++) {
        uint64_t name = ptr(bones + (uint64_t)i * 0x58);
        uint16_t len = 0;
        char text[16] = {0};
        if (name && rd(name, &len, 2) && len == 8 && rd(name + 2, text, len) && !strcmp(text, "Weapon_L")) {
            bone = i;
            break;
        }
    }
    return bone;
}

// ---- geometry
static void sub3(const float a[3], const float b[3], float o[3]) { o[0] = a[0] - b[0], o[1] = a[1] - b[1], o[2] = a[2] - b[2]; }
static float dot3(const float a[3], const float b[3]) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
static float norm3(const float a[3]) { return sqrtf(dot3(a, a)); }

// The point where segment a-b crosses the convex polygon q, in *at; 0 when it does not.
static int crosses(const DbgHookPoly *q, const float nrm[3], const float a[3], const float b[3], float at[3]) {
    float d = dot3(nrm, q->p[0]), da = dot3(nrm, a) - d, db = dot3(nrm, b) - d;
    if ((da > 0) == (db > 0) || da == db) return 0;
    float t = da / (da - db);
    for (int k = 0; k < 3; k++) at[k] = a[k] + (b[k] - a[k]) * t;
    // inside: on the same side of every edge, seen along the normal
    int sign = 0;
    for (int i = 0; i < q->n; i++) {
        const float *p0 = q->p[i], *p1 = q->p[(i + 1) % q->n];
        float e[3], r[3], c[3];
        sub3(p1, p0, e), sub3(at, p0, r);
        c[0] = e[1] * r[2] - e[2] * r[1], c[1] = e[2] * r[0] - e[0] * r[2], c[2] = e[0] * r[1] - e[1] * r[0];
        float s = dot3(c, nrm);
        if (s == 0) continue;
        if (!sign) sign = s > 0 ? 1 : -1;
        else if ((s > 0 ? 1 : -1) != sign) return 0;
    }
    return 1;
}

// Newell's normal of the polygon, unit length; 0 for a degenerate one.
static int normal_of(const DbgHookPoly *q, float n[3]) {
    n[0] = n[1] = n[2] = 0;
    for (int i = 0; i < q->n; i++) {
        const float *a = q->p[i], *b = q->p[(i + 1) % q->n];
        n[0] += (a[1] - b[1]) * (a[2] + b[2]);
        n[1] += (a[2] - b[2]) * (a[0] + b[0]);
        n[2] += (a[0] - b[0]) * (a[1] + b[1]);
    }
    float l = norm3(n);
    if (!(l > 1e-6f)) return 0;
    for (int k = 0; k < 3; k++) n[k] /= l;
    return 1;
}

// ---- state
// Only the player's model's pose touches it; the strike itself is handed to the scheduler through atomics, as the
// pose may be computed on another thread than the scheduler's.
static struct {
    float shift[3];
    int have_shift;
    float tip[3], mid[3];          // last frame's blade, relative to the player
    uint64_t last_ns;
    int have_last;
    uint64_t struck_ns;            // the last strike, 0 for none
    float at[3];                   // where it struck, written before struck_ns
    char weapon_name[48];          // the weapon's actor name, written before struck_ns
    int held;                      // its hold has ended: the rebound started or HOLD_NS ran out (both atomic)
} S;

// The spark and the clink come from the game's ReactionHit system actor, which plays the hit effects and sounds of
// attacks that do not take: they are played at the contact point the way the game plays its item sparkle on that
// actor (main+0x4c3850):
// - the actor: the system actor whose name hash (main+0x1739e0) is that of "ReactionHit", from the list at
//   [[SYSTEM_ACTORS]]+0x50 (nodes linked at +8, the hash at node-0x20, the entry at node-0x20+8; without one, the
//   default entry [DEFAULT_ENTRY]), resolved to the actor by ACTOR_OF_ENTRY (its result through x8: the actor, and at
//   +8 a byte that is 1 when a reference was taken, given back by decrementing the actor's +0x1b8);
// - the emit: XLINK_EMIT_KEY (the XLink holder [[actor+0x270]+0x68], a pointer to the key, two handles, and 0 for the
//   effect user alone or 1 for the sound user alone) fills the handles (pool byte, index s16 at +2, serial u32 at +4);
// - the place: a handle's instance is [[HANDLE_BASES]+8*pool] + [[HANDLE_SIZES]+8*pool] * index, live while its u32
//   at +0x20 is the serial. An effect takes the point at +0xc8, scale 1 at +0xd4 to +0xdc and bits 0x24 at +0x1ac; a
//   sound the point at +0xe8 and bit 0x400 at +0xb8 and +0xbc.
// The keys follow the game's hit reaction tables (Reaction/TableParam/HitEffect and HitSound in the Bootup pack): a
// blade an enemy shrugs off (HitIneffectual___Enemy, GuardAttackCollide___Enemy) plays the effect "no effect (enemy)",
// the spark burst, and the sound Hit_Mukou, whose stone and metal variants each layer the sword's own ring
// (Hit_Mukou_AdditionalSword_*). The sounds are named here per weapon (sounds_for), since the properties the game's
// containers choose by are not set on this call. Addresses for 1.1.1 and 1.0.0 (tools/version_map.py; the emit
// function's 94 callers match one for one): the scheduler site that calls this exists in patches-v111.txt and
// patches-v100.txt.
#define SYSTEM_ACTORS RT_OOT_ADDR(0x7004c829b8ull, 0x7004c1cce0ull)
#define DEFAULT_ENTRY RT_OOT_ADDR(0x7004c8d030ull, 0x7004c27168ull)
#define NAME_HASH RT_OOT_ADDR(0x70001739e0ull, 0x7000001d80ull)
#define ACTOR_OF_ENTRY RT_OOT_ADDR(0x70004bedd0ull, 0x70000e31d0ull)
#define XLINK_EMIT_KEY RT_OOT_ADDR(0x700043fd50ull, 0x700051e470ull)
#define HANDLE_BASES RT_OOT_ADDR(0x7004c7e260ull, 0x7004c185b8ull)
#define HANDLE_SIZES RT_OOT_ADDR(0x7004c7e268ull, 0x7004c185c0ull)
// the effect key in UTF-8 (Japanese for "no effect (enemy)")
#define WALL_EFFECT "\xe7\x84\xa1\xe5\x8a\xb9\xef\xbc\x88\xe6\x95\xb5\xef\xbc\x89"

// The sounds for a weapon (its actor name): up to two, NULL when there is no second.
static void sounds_for(const char *weapon, const char *out[2]) {
    out[0] = "Hit_Mukou_Stone", out[1] = NULL;
    if (!strncmp(weapon, "Item_DekuStick", 14)) out[0] = "Hit_Obj_DekuStick";
    else if (!strncmp(weapon, "Weapon_Sword_", 13)) {
        int giant = strstr(weapon, "Long") || strstr(weapon, "Biggoron") || strstr(weapon, "Giant");
        out[1] = strstr(weapon, "Broken") ? "Hit_Mukou_AdditionalSword_Broken"
                 : giant                  ? "Hit_Mukou_AdditionalSword_Giant"
                 : strstr(weapon, "Kokiri") ? "Hit_Mukou_AdditionalSword_Kokiri"
                                            : "Hit_Mukou_AdditionalSword_Master";
    }
}

// The instance a handle names, 0 when it names none or one no longer live.
static uint64_t handle_instance(const uint8_t *h) {
    int8_t pool = (int8_t)h[0];
    int16_t index;
    uint32_t serial, live = 0;
    memcpy(&index, h + 2, 2), memcpy(&serial, h + 4, 4);
    if (pool < 0 || index < 0) return 0;
    uint64_t base = ptr(ptr(HANDLE_BASES) + 8ull * (uint64_t)pool);
    uint64_t size = ptr(ptr(HANDLE_SIZES) + 8ull * (uint64_t)pool), inst = base + size * (uint64_t)index;
    return base && rd(inst + 0x20, &live, 4) && live == serial && plat_mem_readable(inst, 0x1b0) ? inst : 0;
}

// Plays key on the ReactionHit actor's effect (kind 0) or sound (kind 1) user at the point; 1 when it started.
static int play_at(Cpu *c, uint64_t holder, int kind, const char *key, const float at[3]) {
    static const char *keyp;    // guest code reads host memory directly
    static uint8_t handles[16];
    static const uint8_t none[8] = {0xff, 0, 0xff, 0xff, 0, 0, 0, 0};
    keyp = key;
    memcpy(handles, none, 8), memcpy(handles + 8, none, 8);
    rt_call(c, XLINK_EMIT_KEY, 4, holder, (uint64_t)(uintptr_t)&keyp, (uint64_t)(uintptr_t)handles, (uint64_t)kind);
    uint64_t inst = handle_instance(handles + 8 * kind);
    if (!inst) return 0;
    if (kind == 0) {
        static const float one[3] = {1, 1, 1};
        memcpy((void *)(uintptr_t)(inst + 0xc8), at, 12);
        memcpy((void *)(uintptr_t)(inst + 0xd4), one, 12);
        ST32(inst + 0x1ac, LD32(inst + 0x1ac) | 0x24);
    } else {
        memcpy((void *)(uintptr_t)(inst + 0xe8), at, 12);
        ST32(inst + 0xb8, LD32(inst + 0xb8) | 0x400);
        ST32(inst + 0xbc, LD32(inst + 0xbc) | 0x400);
    }
    return 1;
}

static void sword_effects(Cpu *c) {
    if (!RT_V111 && !RT_V100) return;
    float at[3];
    memcpy(at, S.at, sizeof at);
    char weapon[sizeof S.weapon_name];
    memcpy(weapon, S.weapon_name, sizeof weapon);
    weapon[sizeof weapon - 1] = 0;
    const char *sounds[2];
    sounds_for(weapon, sounds);
    // The scheduler's caller-saved registers stay as they were: the patched load changes only x8 and x9.
    uint64_t x[31], nzcv = c->nzcv, fpsr = c->fpsr;
    __uint128_t v[32];
    memcpy(x, c->x, sizeof x);
    memcpy(v, c->v, sizeof v);
    static const char reaction_hit[] = "ReactionHit";
    static uint8_t got[16];
    uint32_t id = (uint32_t)rt_call(c, NAME_HASH, 2, (uint64_t)(uintptr_t)reaction_hit, (uint64_t)0);
    uint64_t mgr = ptr(ptr(SYSTEM_ACTORS)), head = mgr ? mgr + 0x50 : 0, entry = 0;
    for (uint64_t node = head ? ptr(head + 8) : 0, n = 0; node && node != head && n < 4096; node = ptr(node + 8), n++) {
        uint32_t h = 0;
        if (rd(node - 0x20, &h, 4) && h == id) {
            entry = node - 0x20 + 8;
            break;
        }
    }
    if (!entry) entry = ptr(DEFAULT_ENTRY);
    memset(got, 0, sizeof got);
    c->x[8] = (uint64_t)(uintptr_t)got;
    rt_call(c, ACTOR_OF_ENTRY, 1, entry);
    uint64_t actor;
    memcpy(&actor, got, 8);
    uint64_t comp = actor ? ptr(actor + 0x270) : 0, holder = comp ? ptr(comp + 0x68) : 0;
    if (!holder) {
        rt_log("n64: sword walls: no ReactionHit actor (entry %#llx, actor %#llx)", (unsigned long long)entry,
               (unsigned long long)actor);
    } else {
        int effect = play_at(c, holder, 0, WALL_EFFECT, at), sound0 = play_at(c, holder, 1, sounds[0], at);
        int sound1 = sounds[1] ? play_at(c, holder, 1, sounds[1], at) : 0;
        rt_log("n64: sword walls: ReactionHit %#llx: the spark %s, %s %s, %s %s, at (%.2f %.2f %.2f)",
               (unsigned long long)actor, effect ? "played" : "did not start", sounds[0],
               sound0 ? "played" : "did not start", sounds[1] ? sounds[1] : "no second sound",
               sounds[1] ? (sound1 ? "played" : "did not start") : "", (double)at[0], (double)at[1], (double)at[2]);
    }
    if (actor && got[8] == 1) {
        uint32_t *refs = (uint32_t *)(uintptr_t)(actor + 0x1b8);
        if ((int32_t)__atomic_load_n(refs, __ATOMIC_RELAXED) >= 1) __atomic_fetch_sub(refs, 1, __ATOMIC_ACQ_REL);
    }
    memcpy(c->x, x, sizeof x);
    memcpy(c->v, v, sizeof v);
    c->nzcv = nzcv, c->fpsr = fpsr;
}

static uint64_t player_model(void) {
    uint64_t a = dbg_player_actor(), c = a ? ptr(a + 0x270) : 0, comp = c ? ptr(c + 8) : 0;
    uint64_t group = comp ? ptr(comp + 0x18) : 0, models = group ? ptr(group + 0x18) : 0;
    return models ? ptr(models) : 0;
}

void n64sword_world_ready(uint64_t model, uint64_t matrix) {
    if (!model || model != player_model() || !n64gfx_part(N64_PART_SWORD_WALLS)) return;
    uint64_t now = plat_now_ns();
    float me[3], model_at[3];
    if (!dbg_player_pos(me) || !rd(matrix + 0x30, model_at, 12)) return;
    float raw[3], moved = 0;
    for (int k = 0; k < 3; k++) raw[k] = me[k] - model_at[k], moved = fmaxf(moved, fabsf(raw[k] - S.shift[k]));
    if (!S.have_shift || moved > SHIFT_SLACK_M) {
        for (int k = 0; k < 3; k++) S.shift[k] = roundf(raw[k]);
        S.have_shift = 1;
    }
    char weapon[48];
    float len = held_blade(weapon, sizeof weapon);
    uint64_t skel = ptr(model + 0xf8), resource = skel ? ptr(skel) : 0, mats = skel ? ptr(skel + 0x20) : 0;
    int bone = resource ? weapon_bone(resource) : -1;
    float m[16];
    if (len <= 0 || bone < 0 || !mats || !rd(mats + (uint64_t)bone * 0x40, m, sizeof m)) {
        S.have_last = 0;
        return;
    }
    float dir[3] = {m[8], m[9], m[10]}, l = norm3(dir);
    if (!(l > 1e-4f) || !isfinite(m[12] + m[13] + m[14])) {
        S.have_last = 0;
        return;
    }
    float hilt[3], from[3], tip[3], mid[3], rtip[3], rmid[3];
    for (int k = 0; k < 3; k++) {
        dir[k] /= l;
        hilt[k] = m[12 + k] + S.shift[k];
        from[k] = hilt[k] + dir[k] * len * BLADE_FROM;
        tip[k] = hilt[k] + dir[k] * len;
        mid[k] = hilt[k] + dir[k] * len * 0.6f;
        rtip[k] = tip[k] - me[k], rmid[k] = mid[k] - me[k];
    }
    float dt = S.have_last ? (float)(now - S.last_ns) * 1e-9f : 0, was_tip[3], was_mid[3];
    for (int k = 0; k < 3; k++) was_tip[k] = S.tip[k] + me[k], was_mid[k] = S.mid[k] + me[k];
    float step[3];
    sub3(rtip, S.tip, step);
    int swing = S.have_last && dt > 1e-4f && dt < 0.1f && norm3(step) / dt > SWING_MPS;
    memcpy(S.tip, rtip, sizeof S.tip), memcpy(S.mid, rmid, sizeof S.mid);
    S.last_ns = now, S.have_last = 1;
    uint64_t last = __atomic_load_n(&S.struck_ns, __ATOMIC_ACQUIRE);
    if (!swing || (last && now - last < COOLDOWN_NS)) return;
    int verb = current_verb(dbg_player_actor());
    if (!attack_verb(verb)) {   // a fast blade outside an attack: drawing, putting away, a cut-off swing's return
        static int told[8], ntold;
        int k = 0;
        while (k < ntold && told[k] != verb) k++;
        if (k == ntold && ntold < 8) {
            told[ntold++] = verb;
            rt_log("n64: sword walls: a fast blade with action verb %#x, not an attack: not tested", (unsigned)verb);
        }
        return;
    }

    static DbgHookPoly polys[MAX_POLYS];
    float range = len + REACH_EXTRA_M;
    int n = dbg_hook_polys(polys, MAX_POLYS, range, DBG_POLY_COLLISION), cut = 0;
    float reached = dbg_hook_reach(DBG_POLY_COLLISION, &cut);
    static int told_cut;
    if (cut && reached < range && !told_cut) {
        told_cut = 1;
        rt_log("n64: sword walls: %d polygons reach only %.2f of %.2f m: walls further out are not tested", n,
               (double)reached, (double)range);
    }
    static int told_none;
    if (!n && !told_none) {
        told_none = 1;
        char st[160];
        dbg_hook_status(st, sizeof st);
        rt_log("n64: sword walls: a swing of the %s with no collision polygons near (%s)", weapon, st);
    }
    const float *seg[3][2] = {{from, tip}, {was_tip, tip}, {was_mid, mid}};
    float moved_tip[3];
    sub3(tip, was_tip, moved_tip);
    for (int i = 0; i < n; i++) {
        float nrm[3], at[3], h[3];
        if (polys[i].air || !normal_of(&polys[i], nrm) || fabsf(nrm[1]) >= WALL_NY) continue;
        sub3(hilt, polys[i].p[0], h);
        float side = dot3(nrm, h);
        if (side < 0)   // the normal toward the hilt's side
            for (int k = 0; k < 3; k++) nrm[k] = -nrm[k];
        if (side == 0 || dot3(moved_tip, nrm) >= 0) continue;   // the hand in the wall's plane, or the tip leaving it
        for (int s = 0; s < 3; s++) {
            if (!crosses(&polys[i], nrm, seg[s][0], seg[s][1], at)) continue;
            __atomic_store_n(&S.held, 0, __ATOMIC_RELAXED);
            memcpy(S.at, at, sizeof S.at);
            snprintf(S.weapon_name, sizeof S.weapon_name, "%s", weapon);
            __atomic_store_n(&S.struck_ns, now, __ATOMIC_RELEASE);
            rt_log("n64: sword walls: the %s (%.2f m, verb %#x) struck a wall at (%.2f %.2f %.2f), normal (%.2f %.2f "
                   "%.2f), tip (%.2f %.2f %.2f), segment %d, %d polygons near", weapon, (double)len, (unsigned)verb,
                   (double)at[0], (double)at[1], (double)at[2], (double)nrm[0], (double)nrm[1], (double)nrm[2],
                   (double)tip[0], (double)tip[1], (double)tip[2], s, n);
            return;
        }
    }
}

int n64sword_take(Cpu *c, uint32_t f0, uint32_t f4) {
    uint64_t at = __atomic_load_n(&S.struck_ns, __ATOMIC_ACQUIRE);
    if (!at || __atomic_load_n(&S.held, __ATOMIC_ACQUIRE)) return 0;
    uint64_t now = plat_now_ns(), comp = ptr(dbg_player_actor() + 0x270), state = comp ? ptr(comp + 0x358) : 0;
    uint32_t flags = 0;
    int started = state && rd(state + 0xe4c, &flags, 4) && (flags & 8);
    if (started || now - at > HOLD_NS || !n64gfx_part(N64_PART_SWORD_WALLS)) {
        __atomic_store_n(&S.held, 1, __ATOMIC_RELEASE);
        rt_log("n64: sword walls: %s after %llu ms", started ? "the rebound has started" : "no rebound started",
               (unsigned long long)((now - at) / 1000000u));
        return 0;
    }
    static uint64_t told;
    if (told != at) {
        told = at;
        rt_log("n64: sword walls: the reaction scheduler is told the strike (hit state %u %u)", f0, f4);
        sword_effects(c);
    }
    return 1;
}
