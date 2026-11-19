// Save states of the debug menu (debug_tools.h "Save states"): the game's state taken at any moment and put back at any
// time, also after a restart.
//
// A state holds the area and the age, the entrance the player last came in by, the player's position and facing,
// health and magic, the value of every flag of the game's flag stores (bool, int, float, string, vec3 and enum: the
// flags the save file keeps and those kept only while the game runs), and every actor of the area found in the heap
// (dbg_each_actor): its name, position, rotation and life. Left out are the flags that drive the area loading itself
// (is_sequence_key) and whatever an actor keeps inside itself beyond those (what an NPC is in the middle of saying,
// an enemy's attack under way).
//
// Saving runs on a helper thread: the game is paused (dbg_pause_set) while the flags, the player and the actors are
// read, about a second for the heap scan, so all of them are of the same frame.
//
// Loading: in the state's own area the flags are written and the player is moved at once. Elsewhere the state's
// entrance is warped to (debug_warp.c, as the area reload does) and the rest waits until the area has loaded and the
// player has stood there a moment (dbg_state_tick, once per presented frame). Health and magic go last: the player
// keeps its own copy of them and writes it over the flags. Then a helper thread pauses the game, scans the actors
// there are now and pairs them with the saved ones of the same name, nearest first (match_actors). On the next frame
// the game thread (dbg_state_frame) warps each paired actor to its saved pose and sets its life, deletes the actors
// the state did not have, and makes the saved ones that are missing, one at a time. Only world actors are paired,
// deleted and made (world_actor): enemies, NPCs, animals and objects; the player, its equipment, the terrain, events,
// effects, cameras and triggers are left to the game.
//
// A state is kept in memory and in debug_state_<n>.bin in the game's folder: "NXST", u32 version, the Head below,
// u32 flag bytes, u32 actors, the flags as {u32 key, u8 store (DBG_FLAG_), u8 length, the value's bytes}, then the
// actors (Actor). Version 1 files (the save file's flags alone, no actors) still load.
#include <math.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "debug_tools.h"
#include "host.h"
#include "platform/platform.h"

#define STATE_MAGIC "NXST"
#define STATE_VERSION 2u
#define ARRIVE_WAIT_NS 30000000000ull   // a warp that has not arrived by then is given up
#define SETTLE_NS 1000000000ull          // how long the player stands in the arrived area before the state is put back
#define PARK_QUIET_NS 120000000ull       // the frame hook has not run for this long: the game thread waits in the pause
#define PARK_WAIT_NS 1500000000ull       // ... at most this long, after which the state is read anyway
#define ACTORS_MAX 4096
#define SPAWN_MAX 128
#define SPAWN_WAIT_FRAMES 600
#define MOVE_EPS 0.02f                   // an actor this close to its saved place, turned the same, is not warped
// The game keeps only the actors near the player in the world (in Market_Adult the ReDeads 160 m away were not, and
// arriving at the market's entrance left those of the Temple of Time end out), so which actors exist depends on where
// the player stands. A load removes and makes only actors within NEAR_M of the saved place of the player, and scans
// the live ones once the player has stood there STREAM_WAIT_MS; paired actors are moved at any distance.
#define NEAR_M 40.0f
#define STREAM_WAIT_MS 2000
// u32 at +0x2c: 3 for an actor made but not in the world (the pooled pickups, menu actors, those kept from other
// areas: all at 0, 0, 0), 4 to 6 for one in the area (seen in Market_Adult, 1.1.1), 7 to 11 while it is being deleted
// (the delete request's own test). A state keeps, moves and deletes only actors in the world.
#define ACTOR_STATE 0x2c
#define IN_WORLD(st) ((st) >= 4u && (st) <= 6u)
#define ACTOR_POS 0x31c
#define ACTOR_AXES 0x2f8

typedef struct {
    char banc[64], start[64];   // the area and the entrance last come in by ("" when none was seen)
    int32_t child;              // 1 child, 0 adult
    float pos[3], face[3];
    int32_t health, magic;      // -1 when unknown
    int64_t saved_at;           // seconds since 1970, for the menu
    uint32_t nflags;
} Head;

typedef struct {
    char name[64];
    float pos[3], ax[9];   // ax: the 3x3 at +0x2f8
    int32_t life;          // -1: no life component
    uint32_t pad;
} Actor;
_Static_assert(sizeof(Actor) == 120, "the file's actor record");

typedef struct {
    Head h;
    uint8_t *flags;   // nflags records
    size_t size;
    Actor *actors;
    uint32_t nactors;
    int set;
} State;

static State states[DBG_STATES];
static PlatMutex lock = PLAT_MUTEX_INIT;
static char note[160];

enum { L_IDLE, L_ARRIVE, L_SETTLE };
static int load_phase, load_slot;
static uint64_t load_since;

// The helper thread's work: one save or one load's actors at a time.
enum { JOB_NONE, JOB_SAVE, JOB_LOAD };
static _Atomic int job;
static int job_slot;

static void file_name(int slot, char out[32]) { snprintf(out, 32, "debug_state_%d.bin", slot + 1); }

static int is_sequence_key(uint32_t key) {
    static uint32_t skip[8];
    static int n = -1;
    if (n < 0) {
        static const char *const names[] = {"Sequence_CurrentBanc", "Sequence_IsChildhood", "Sequence_IsLoadingBanc",
                                            "Sequence_ReservedNextBanc", "Sequence_ReservedNextStartPosName",
                                            "Sequence_ControlInEvent", "Sequence_IsPause"};
        int k = 0;
        for (size_t i = 0; i < sizeof names / sizeof *names; i++) skip[k++] = dbg_flag_key(names[i]);
        n = k;
    }
    for (int i = 0; i < n; i++)
        if (skip[i] == key) return 1;
    return 0;
}

// The actors a state pairs, deletes and makes (the name's prefix, as under Pack/Actor).
static int world_actor(const char *name) {
    if (!strncmp(name, "Npc_Navi", 8)) return 0;   // the companion follows the player on its own
    static const char *const kinds[] = {"Enemy_", "Npc_", "NPC_", "Animal_", "EnvironmentalAnimal", "Obj_", "FldObj_",
                                        "Horse", "Shootable"};
    for (size_t i = 0; i < sizeof kinds / sizeof *kinds; i++)
        if (!strncmp(name, kinds[i], strlen(kinds[i]))) return 1;
    return 0;
}

// The value of one flag: its store and bytes (bool 1, int / float / enum 4, vec3 12, string up to 64). 0 when unknown.
static int flag_read(uint32_t key, int *type, uint8_t v[64], int *len) {
    int t = dbg_flag_type(key);
    *type = t;
    switch (t) {
    case DBG_FLAG_BOOL: {
        int b = dbg_flag_get_bool(key);
        if (b < 0) return 0;
        v[0] = (uint8_t)b, *len = 1;
        return 1;
    }
    case DBG_FLAG_INT:
    case DBG_FLAG_FLOAT:
    case DBG_FLAG_ENUM: {
        uint32_t u;
        if (!dbg_flag_get_u32(t, key, &u)) return 0;
        memcpy(v, &u, 4), *len = 4;
        return 1;
    }
    case DBG_FLAG_VEC3: {
        float f[3];
        if (!dbg_flag_get_vec3(key, f)) return 0;
        memcpy(v, f, 12), *len = 12;
        return 1;
    }
    case DBG_FLAG_STRING: {
        char s[64];
        if (!dbg_flag_get_string(key, s)) return 0;
        s[63] = 0;
        *len = (int)strlen(s);
        memcpy(v, s, (size_t)*len);
        return 1;
    }
    default: return 0;
    }
}

// Writes a flag back when it differs; 1 when it was written.
static int flag_write(uint32_t key, int type, const uint8_t *v, int len) {
    uint8_t now[64];
    int nt, nl;
    if (flag_read(key, &nt, now, &nl) && nt == type && nl == len && !memcmp(now, v, (size_t)len)) return 0;
    switch (type) {
    case DBG_FLAG_BOOL: return len == 1 && dbg_flag_set_bool(key, v[0] != 0);
    case DBG_FLAG_INT:
    case DBG_FLAG_FLOAT:
    case DBG_FLAG_ENUM: {
        uint32_t u;
        if (len != 4) return 0;
        memcpy(&u, v, 4);
        return dbg_flag_set_u32(type, key, u);
    }
    case DBG_FLAG_VEC3: {
        float f[3];
        if (len != 12) return 0;
        memcpy(f, v, 12);
        return dbg_flag_set_vec3(key, f);
    }
    case DBG_FLAG_STRING: {
        char s[64];
        if (len > 63) return 0;
        memcpy(s, v, (size_t)len), s[len] = 0;
        return dbg_flag_set_string(key, s);
    }
    default: return 0;
    }
}

static void state_free(State *s) {
    free(s->flags);
    free(s->actors);
    memset(s, 0, sizeof *s);
}

static int state_write_file(int slot, const State *s) {
    char name[32];
    size_t na = (size_t)s->nactors * sizeof(Actor), n = 8 + sizeof s->h + 8 + s->size + na;
    uint8_t *buf = malloc(n);
    if (!buf) return 0;
    uint32_t ver = STATE_VERSION, fs = (uint32_t)s->size, nact = s->nactors;
    uint8_t *p = buf;
    memcpy(p, STATE_MAGIC, 4), memcpy(p + 4, &ver, 4), p += 8;
    memcpy(p, &s->h, sizeof s->h), p += sizeof s->h;
    memcpy(p, &fs, 4), memcpy(p + 4, &nact, 4), p += 8;
    if (s->size) memcpy(p, s->flags, s->size), p += s->size;
    if (na) memcpy(p, s->actors, na);
    file_name(slot, name);
    int ok = plat_file_write(name, buf, n) == 1;
    free(buf);
    return ok;
}

// The state of a slot from its file when it is not in memory yet (lock held).
static void state_read_file(int slot) {
    State *s = &states[slot];
    if (s->set) return;
    char name[32];
    uint64_t n = 0;
    file_name(slot, name);
    uint8_t *buf = plat_file_read(name, &n);
    uint32_t ver = 0;
    if (buf && n >= 8 + sizeof s->h && !memcmp(buf, STATE_MAGIC, 4) && (memcpy(&ver, buf + 4, 4), ver == 1 || ver == 2)) {
        Head h;
        memcpy(&h, buf + 8, sizeof h);
        const uint8_t *p = buf + 8 + sizeof h, *end = buf + n;
        size_t fs = (size_t)(end - p);
        uint32_t nact = 0;
        int ok = 1;
        if (ver == 2) {
            uint32_t f32 = 0;
            ok = end - p >= 8;
            if (ok) memcpy(&f32, p, 4), memcpy(&nact, p + 4, 4), p += 8;
            fs = f32;
            ok = ok && nact <= ACTORS_MAX && (size_t)(end - p) >= fs + (size_t)nact * sizeof(Actor);
        }
        uint8_t *f = ok && fs ? malloc(fs) : NULL;
        Actor *a = ok && nact ? malloc((size_t)nact * sizeof(Actor)) : NULL;
        if (ok && (!fs || f) && (!nact || a)) {
            if (fs) memcpy(f, p, fs);
            if (nact) memcpy(a, p + fs, (size_t)nact * sizeof(Actor));
            for (uint32_t i = 0; i < nact; i++) a[i].name[63] = 0;
            h.banc[63] = h.start[63] = 0;
            s->h = h, s->flags = f, s->size = fs, s->actors = a, s->nactors = nact, s->set = 1;
        } else {
            free(f), free(a);
        }
    }
    free(buf);
}

// Pauses the game until its thread waits in the pause; *was: it was paused already (and stays so).
static void pause_hold(int *was) {
    *was = dbg_paused();
    if (*was) return;
    dbg_pause_set(1);
    uint64_t t0 = plat_now_ns();
    while (plat_now_ns() - t0 < PARK_WAIT_NS && plat_now_ns() - dbg_frame_seen_ns() < PARK_QUIET_NS) plat_sleep_ms(5);
}
static void pause_release(int was) {
    if (!was) dbg_pause_set(0);
}

// One actor of the heap scan, with its pose and life; 0 when it is not in the world or unreadable.
static int actor_read(uint64_t actor, const char *name, Actor *r) {
    uint32_t st = 0;
    memset(r, 0, sizeof *r);
    if (strlen(name) >= sizeof r->name || !plat_mem_read(actor + ACTOR_STATE, &st, 4) || !IN_WORLD(st) ||
        !plat_mem_read(actor + ACTOR_POS, r->pos, sizeof r->pos) || !plat_mem_read(actor + ACTOR_AXES, r->ax, sizeof r->ax))
        return 0;
    for (int i = 0; i < 3; i++)
        if (!isfinite(r->pos[i])) return 0;
    for (int i = 0; i < 9; i++)
        if (!isfinite(r->ax[i])) return 0;
    snprintf(r->name, sizeof r->name, "%s", name);
    int life, max;
    r->life = dbg_actor_life(actor, &life, &max, NULL) ? life : -1;
    return 1;
}

typedef struct {
    Actor *a;
    uint64_t *addr;   // NULL: not wanted
    int n, world_only;
    uint64_t player;
} Scan;
static int scan_each(uint64_t actor, const char *name, void *ctx) {
    Scan *s = ctx;
    if (s->n >= ACTORS_MAX) return 0;
    if (actor == s->player || (s->world_only && !world_actor(name))) return 1;
    if (!actor_read(actor, name, &s->a[s->n])) return 1;
    if (s->addr) s->addr[s->n] = actor;
    s->n++;
    return 1;
}

// Every flag of every store (sequence keys left out) into s (flags, size, h.nflags). 0 when none was read.
static int flags_take(State *s) {
    size_t cap = 1 << 16;
    s->flags = malloc(cap);
    if (!s->flags) return 0;
    for (int t = 0; t < DBG_FLAG_TYPES; t++) {
        uint64_t base;
        int count;
        uint32_t size, value;
        if (!dbg_flag_store(t, &base, &count, &size, &value) || count <= 0) continue;
        uint32_t *keys = calloc((size_t)count, 4);
        if (!keys) continue;
        dbg_flag_store_keys(t, keys, count);
        for (int i = 0; i < count; i++) {
            uint8_t v[64];
            int type, len;
            if (!keys[i] || is_sequence_key(keys[i]) || !flag_read(keys[i], &type, v, &len) || type != t) continue;
            if (s->size + 6 + (size_t)len > cap) {
                uint8_t *g = realloc(s->flags, cap *= 2);
                if (!g) break;
                s->flags = g;
            }
            uint8_t *r = s->flags + s->size;
            memcpy(r, &keys[i], 4), r[4] = (uint8_t)type, r[5] = (uint8_t)len, memcpy(r + 6, v, (size_t)len);
            s->size += 6 + (size_t)len;
            s->h.nflags++;
        }
        free(keys);
    }
    return s->h.nflags > 0;
}

static void save_thread(void *arg) {
    (void)arg;
    int slot = job_slot, was;
    State s = {0};
    float pos[3];
    pause_hold(&was);
    int ok = dbg_player_pos(pos);
    if (ok) {
        if (!dbg_current_area(s.h.banc)) s.h.banc[0] = 0;
        char lb[64], ls[64];
        if (dbg_last_entrance(lb, ls) && !strcmp(lb, s.h.banc)) memcpy(s.h.start, ls, 64);
        s.h.child = dbg_is_child() == 1;
        memcpy(s.h.pos, pos, sizeof pos);
        if (!dbg_player_facing(s.h.face)) s.h.face[0] = 0, s.h.face[1] = 0, s.h.face[2] = 1;
        int cur, max;
        s.h.health = dbg_health_get(&cur, &max) ? cur : -1;
        s.h.magic = dbg_magic_get(&cur, &max) ? cur : -1;
        s.h.saved_at = (int64_t)time(NULL);
        ok = flags_take(&s);
    }
    if (ok) {
        Scan sc = {.a = malloc(ACTORS_MAX * sizeof(Actor)), .player = dbg_player_actor()};
        if (sc.a) {
            dbg_each_actor(scan_each, &sc);
            s.actors = sc.a, s.nactors = (uint32_t)sc.n;
        }
    }
    pause_release(was);
    if (!ok) {
        state_free(&s);
        snprintf(note, sizeof note, "State %d: the player or the game's flags could not be read", slot + 1);
    } else {
        int world = 0;
        for (uint32_t i = 0; i < s.nactors; i++) world += world_actor(s.actors[i].name);
        s.set = 1;
        int written = state_write_file(slot, &s);
        plat_mutex_lock(&lock);
        state_free(&states[slot]);
        states[slot] = s;
        plat_mutex_unlock(&lock);
        snprintf(note, sizeof note, "State %d saved: %s, %u flags, %d actors%s", slot + 1,
                 s.h.banc[0] ? s.h.banc : "this area", s.h.nflags, world, written ? "" : " (not written to its file)");
        rt_log("debug state: %s (%u actors in all)", note, s.nactors);
    }
    dbg_toast_show(note);
    atomic_store(&job, JOB_NONE);
}

int dbg_state_save(int slot) {
    if (slot < 0 || slot >= DBG_STATES) return 0;
    if (!dbg_player_pos((float[3]){0})) {
        snprintf(note, sizeof note, "No player to save");
        return 0;
    }
    uint64_t store_base;
    int store_count;
    uint32_t store_size, store_value;
    dbg_flag_type(dbg_flag_key("Sequence_CurrentBanc"));   // starts the search for the stores when none was made
    if (dbg_flags_locating() || !dbg_flag_store(DBG_FLAG_INT, &store_base, &store_count, &store_size, &store_value)) {
        snprintf(note, sizeof note, "The game's flags are still being found: try again in a moment");
        return 0;
    }
    int idle = JOB_NONE;
    if (load_phase != L_IDLE || !atomic_compare_exchange_strong(&job, &idle, JOB_SAVE)) {
        snprintf(note, sizeof note, "A state is still being saved or loaded");
        return 0;
    }
    job_slot = slot;
    if (!plat_thread_spawn(save_thread, NULL, "debug-state", 0)) {
        atomic_store(&job, JOB_NONE);
        snprintf(note, sizeof note, "State %d: the save could not start", slot + 1);
        return 0;
    }
    snprintf(note, sizeof note, "State %d: saving...", slot + 1);
    return 1;
}

// ---- the actors of a load

enum { J_WARP, J_DELETE, J_SPAWN };
typedef struct {
    int kind;
    uint64_t actor;   // J_WARP, J_DELETE: the live actor; J_SPAWN: the one made
    Actor want;       // J_WARP, J_SPAWN: the saved actor
    uint64_t name_ptr, handle;
} Job;
static Job *jobs;
static int njobs, job_next, job_frames, job_sent, jobs_started;
static _Atomic int jobs_ready;   // 1: the game thread runs them
static int done_moved, done_deleted, done_made, done_failed;
static Actor *load_actors;       // the loading state's actors (a copy)
static int nload_actors;
static float load_center[3];   // the player's saved place

typedef struct {
    float d2;
    int s, l;
} Pair;
static int cmp_pair(const void *a, const void *b) {
    float x = ((const Pair *)a)->d2, y = ((const Pair *)b)->d2;
    return x < y ? -1 : x > y;
}

static int same_pose(const Actor *a, const Actor *b) {
    for (int i = 0; i < 3; i++)
        if (fabsf(a->pos[i] - b->pos[i]) > MOVE_EPS) return 0;
    for (int i = 0; i < 9; i++)
        if (fabsf(a->ax[i] - b->ax[i]) > 0.01f) return 0;
    return 1;
}

static int near_to(const float a[3], const float c[3]) {
    float dx = a[0] - c[0], dy = a[1] - c[1], dz = a[2] - c[2];
    return dx * dx + dy * dy + dz * dz < NEAR_M * NEAR_M;
}

// The saved world actors (sv) against the live ones (lv at la): pairs of one name nearest first, then the jobs; only
// actors near c (the player's saved place) are made or removed.
static void match_actors(const Actor *sv, int ns, const Actor *lv, const uint64_t *la, int nl, const float c[3]) {
    char *s_used = calloc((size_t)ns + 1, 1), *l_used = calloc((size_t)nl + 1, 1);
    jobs = calloc((size_t)ns + (size_t)nl + 1, sizeof *jobs);
    njobs = 0;
    if (!s_used || !l_used || !jobs) goto out;
    for (int i = 0; i < ns; i++) {
        if (s_used[i] || !world_actor(sv[i].name)) continue;
        // the actors of this name, saved and live, paired nearest first
        int cs = 0, cl = 0;
        for (int k = i; k < ns; k++) cs += !strcmp(sv[k].name, sv[i].name);
        for (int k = 0; k < nl; k++) cl += !strcmp(lv[k].name, sv[i].name);
        Pair *p = cl ? malloc((size_t)cs * (size_t)cl * sizeof *p) : NULL;
        int np = 0;
        if (p)
            for (int a = i; a < ns; a++) {
                if (strcmp(sv[a].name, sv[i].name)) continue;
                for (int b = 0; b < nl; b++) {
                    if (strcmp(lv[b].name, sv[i].name)) continue;
                    float dx = sv[a].pos[0] - lv[b].pos[0], dy = sv[a].pos[1] - lv[b].pos[1], dz = sv[a].pos[2] - lv[b].pos[2];
                    p[np++] = (Pair){dx * dx + dy * dy + dz * dz, a, b};
                }
            }
        qsort(p, (size_t)np, sizeof *p, cmp_pair);
        for (int k = 0; k < np; k++) {
            if (s_used[p[k].s] || l_used[p[k].l]) continue;
            s_used[p[k].s] = l_used[p[k].l] = 1;
            const Actor *want = &sv[p[k].s], *now = &lv[p[k].l];
            if (!same_pose(want, now) || (want->life >= 0 && now->life >= 0 && want->life != now->life))
                jobs[njobs++] = (Job){.kind = J_WARP, .actor = la[p[k].l], .want = *want};
        }
        free(p);
        int spawned = 0;
        for (int a = i; a < ns; a++)
            if (!strcmp(sv[a].name, sv[i].name) && !s_used[a]) {
                s_used[a] = 1;
                if (near_to(sv[a].pos, c) && spawned++ < SPAWN_MAX) jobs[njobs++] = (Job){.kind = J_SPAWN, .want = sv[a]};
            }
        for (int b = 0; b < nl; b++) {
            if (strcmp(lv[b].name, sv[i].name)) continue;
            l_used[b] |= 2;   // this name is handled
        }
    }
    // live world actors of a name the state does not have at all, and those of its names left over
    for (int b = 0; b < nl; b++)
        if (!(l_used[b] & 1) && world_actor(lv[b].name) && near_to(lv[b].pos, c)) jobs[njobs++] = (Job){.kind = J_DELETE, .actor = la[b], .want = lv[b]};
out:
    free(s_used), free(l_used);
}

static void load_actors_thread(void *arg) {
    (void)arg;
    int was;
    Actor *lv = malloc(ACTORS_MAX * sizeof(Actor));
    uint64_t *la = malloc(ACTORS_MAX * 8);
    plat_sleep_ms(STREAM_WAIT_MS);   // the player is back at the saved place: the area round it loads
    pause_hold(&was);
    Scan sc = {.a = lv, .addr = la, .world_only = 1, .player = dbg_player_actor()};
    if (lv && la) dbg_each_actor(scan_each, &sc);
    match_actors(load_actors, nload_actors, lv, la, sc.n, load_center);
    free(lv), free(la);
    done_moved = done_deleted = done_made = done_failed = 0;
    job_next = job_frames = job_sent = jobs_started = 0;
    rt_log("debug state: %d saved actors, %d live world actors, %d jobs", nload_actors, sc.n, njobs);
    for (int i = 0; i < njobs; i++)
        if (jobs[i].kind != J_WARP)
            rt_log("debug state: %s %s at %.1f %.1f %.1f", jobs[i].kind == J_DELETE ? "remove" : "make", jobs[i].want.name,
                   jobs[i].want.pos[0], jobs[i].want.pos[1], jobs[i].want.pos[2]);
    atomic_store(&jobs_ready, 1);
    pause_release(was);   // the game thread takes the jobs on its next frame
}

static void jobs_end(void) {
    snprintf(note, sizeof note, "State %d loaded: %d actors moved, %d removed, %d made%s", load_slot + 1, done_moved,
             done_deleted, done_made, done_failed ? " (some could not be made)" : "");
    rt_log("debug state: %s, %d failed", note, done_failed);
    dbg_toast_show(note);
    free(jobs), jobs = NULL, njobs = 0;
    free(load_actors), load_actors = NULL, nload_actors = 0;
    atomic_store(&jobs_ready, 0);
    atomic_store(&job, JOB_NONE);
}

static void set_life(uint64_t actor, int want) {
    int life, max;
    uint64_t at = 0;
    if (want >= 0 && dbg_actor_life(actor, &life, &max, &at) && life != want) dbg_write32(at, (uint32_t)(want > max ? max : want));
}

void dbg_state_frame(struct Cpu *c) {
    if (!atomic_load(&jobs_ready)) return;
    if (!jobs_started) {   // first frame: every warp and delete at once
        jobs_started = 1;
        for (int i = 0; i < njobs; i++) {
            Job *j = &jobs[i];
            if (j->kind == J_SPAWN || !dbg_actor_named(j->actor, j->want.name)) continue;
            uint32_t st = 0;
            if (!plat_mem_read(j->actor + ACTOR_STATE, &st, 4) || !IN_WORLD(st)) continue;
            if (j->kind == J_WARP) {
                dbg_frame_warp_actor(c, j->actor, j->want.pos, j->want.ax);
                set_life(j->actor, j->want.life);
                done_moved++;
            } else {
                dbg_frame_delete_actor(c, j->actor);
                done_deleted++;
            }
        }
    }
    // then the actors to make, one at a time
    while (job_next < njobs && jobs[job_next].kind != J_SPAWN) job_next++;
    if (job_next >= njobs) {
        jobs_end();
        return;
    }
    Job *j = &jobs[job_next];
    if (!job_sent) {
        j->name_ptr = (uint64_t)(uintptr_t)j->want.name;
        job_sent = 1, job_frames = 0;
        if (!dbg_frame_create(c, &j->name_ptr, j->want.pos, j->want.ax, &j->handle)) done_failed++, job_next++, job_sent = 0;
        return;
    }
    uint64_t actor = 0;
    int r = dbg_frame_create_poll(c, &j->handle, &actor, ++job_frames > SPAWN_WAIT_FRAMES);
    if (r == 0) return;
    if (r > 0) {
        dbg_frame_warp_actor(c, actor, j->want.pos, j->want.ax);
        set_life(actor, j->want.life);
        done_made++;
    } else {
        done_failed++;
    }
    job_next++, job_sent = 0;
}

// Flags, then the player's place, then health and magic, then the actors' part on a helper thread (lock held).
static void apply(const State *s) {
    int written = 0;
    for (size_t at = 0; at + 6 <= s->size;) {
        const uint8_t *r = s->flags + at;
        uint32_t key;
        memcpy(&key, r, 4);
        int len = r[5];
        if (at + 6 + (size_t)len > s->size) break;
        written += flag_write(key, r[4], r + 6, len);
        at += 6 + (size_t)len;
    }
    if (!dbg_player_teleport_face(s->h.pos, s->h.face)) dbg_player_set_pos(s->h.pos);
    if (s->h.health >= 0) dbg_health_set(s->h.health);
    if (s->h.magic >= 0) dbg_magic_set(s->h.magic);
    snprintf(note, sizeof note, "State %d loaded: %d of %u flags changed", load_slot + 1, written, s->h.nflags);
    rt_log("debug state: %s", note);
    if (!s->nactors) {
        atomic_store(&job, JOB_NONE);
        return;
    }
    load_actors = malloc((size_t)s->nactors * sizeof(Actor));
    if (!load_actors) {
        atomic_store(&job, JOB_NONE);
        return;
    }
    memcpy(load_actors, s->actors, (size_t)s->nactors * sizeof(Actor));
    nload_actors = (int)s->nactors;
    memcpy(load_center, s->h.pos, sizeof load_center);
    if (!plat_thread_spawn(load_actors_thread, NULL, "debug-state", 0)) {
        free(load_actors), load_actors = NULL;
        atomic_store(&job, JOB_NONE);
        return;
    }
    snprintf(note, sizeof note, "State %d: %d of %u flags changed, putting the actors back", load_slot + 1, written,
             s->h.nflags);
}

int dbg_state_load(int slot) {
    if (slot < 0 || slot >= DBG_STATES) return 0;
    int idle = JOB_NONE;
    if (load_phase != L_IDLE || !atomic_compare_exchange_strong(&job, &idle, JOB_LOAD)) {
        snprintf(note, sizeof note, "A state is still being saved or loaded");
        return 0;
    }
    plat_mutex_lock(&lock);
    state_read_file(slot);
    State *s = &states[slot];
    int ok = 0;
    char cur[64];
    if (!s->set) snprintf(note, sizeof note, "State %d is empty", slot + 1);
    else if (!dbg_player_pos((float[3]){0})) snprintf(note, sizeof note, "Load a save first: there is no player");
    else if (dbg_current_area(cur) && !strcmp(cur, s->h.banc) && dbg_is_child() == s->h.child) {
        load_slot = slot, load_phase = L_IDLE;
        apply(s);
        ok = 1;
    } else {
        if (!dbg_warp_arm_to(s->h.banc, s->h.start) || !dbg_warp_now())
            snprintf(note, sizeof note, "The warp to %s did not start: %s", s->h.banc, dbg_warp_note());
        else {
            load_slot = slot, load_phase = L_ARRIVE, load_since = plat_now_ns();
            snprintf(note, sizeof note, "State %d: warping to %s", slot + 1, s->h.banc);
            ok = 1;
        }
    }
    if (!ok) atomic_store(&job, JOB_NONE);
    plat_mutex_unlock(&lock);
    return ok;
}

void dbg_state_tick(uint64_t now_ns) {
    if (load_phase == L_IDLE) return;
    plat_mutex_lock(&lock);
    const State *s = &states[load_slot];
    char cur[64];
    float pos[3];
    int here = s->set && dbg_current_area(cur) && !strcmp(cur, s->h.banc) && dbg_player_pos(pos) &&
               dbg_flag_get_bool(dbg_flag_key("Sequence_IsLoadingBanc")) != 1;
    if (!s->set) load_phase = L_IDLE, atomic_store(&job, JOB_NONE);
    else if (load_phase == L_ARRIVE) {
        if (here) load_phase = L_SETTLE, load_since = now_ns;
        else if (now_ns - load_since > ARRIVE_WAIT_NS) {
            load_phase = L_IDLE;
            atomic_store(&job, JOB_NONE);
            snprintf(note, sizeof note, "State %d: the warp to %s did not arrive", load_slot + 1, s->h.banc);
        }
    } else if (load_phase == L_SETTLE) {
        if (!here) load_since = now_ns;
        else if (now_ns - load_since > SETTLE_NS) {
            load_phase = L_IDLE;
            apply(s);
            dbg_toast_show(note);
        }
    }
    plat_mutex_unlock(&lock);
}

int dbg_state_info(int slot, char banc[64], int64_t *saved_at) {
    if (slot < 0 || slot >= DBG_STATES) return 0;
    plat_mutex_lock(&lock);
    state_read_file(slot);
    const State *s = &states[slot];
    int set = s->set;
    if (set) {
        if (banc) memcpy(banc, s->h.banc, 64);
        if (saved_at) *saved_at = s->h.saved_at;
    }
    plat_mutex_unlock(&lock);
    return set;
}

int dbg_state_loading(void) { return load_phase != L_IDLE || atomic_load(&job) == JOB_LOAD; }
const char *dbg_state_note(void) { return note; }
