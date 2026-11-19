// The save tree (save_tree.h). One thread, "save-tree": every POLL_MS it reads the game's flag stores whole
// (debug_tools.c dbg_flag_store) and keeps the changes since the last node, asks the game for a save of the slot it
// plays once they have settled (debug_transform.c dbg_game_save_request), turns each save the game commits
// (fs_host.c live_copy) into a node, and loads a node the launcher asked for. The tree's files are its own; the game's
// save is only read here, apart from a load.
#include <stdarg.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "cfg.h"
#include "debug_tools.h"
#include "debug_watch.h"
#include "fs.h"
#include "gfx.h"
#include "host.h"
#include "input.h"
#include "platform/platform.h"
#include "save_share.h"
#include "save_slots.h"
#include "save_tree.h"
#include "save_zip.h"

#define POLL_MS 100
#define QUIET_MS 1500          // changes are saved once none came for this long
#define MAX_WAIT_MS 6000       // or this long after the first, while they keep coming
#define MIN_GAP_MS 4000        // between two saves the tree asks for
#define ANSWER_MS 5000         // a save asked for that did not come in this long: the tree makes it itself (built_node)
#define NOISY_HITS 8           // a flag is left out from then on when its last NOISY_FAST changes came within
#define NOISY_FAST 4           // NOISY_FAST_MS (a timer), or its last NOISY_HITS within NOISY_SLOW_MS (a clock)
#define NOISY_FAST_MS 30000
#define NOISY_SLOW_MS 600000
#define UNMUTE_MS 300000       // a flag left out comes back once it has not changed for this long (rupees after a burst)
#define PENDING_MAX 512
#define CHANGES_KEPT 64        // changes written into a node's line; "n" counts them all
#define SLOTS 6                // the game's save slots slot_00 .. slot_05 (its slot_06 .. slot_11 are second copies)

enum { T_BOOL = DBG_FLAG_BOOL, T_TYPES = DBG_FLAG_TYPES };
static const uint32_t kWidth[T_TYPES] = {
    [DBG_FLAG_BOOL] = 1, [DBG_FLAG_INT] = 4, [DBG_FLAG_FLOAT] = 4, [DBG_FLAG_STRING] = 64, [DBG_FLAG_VEC3] = 12, [DBG_FLAG_ENUM] = 4,
};
static const char *const kType[T_TYPES] = {"Bool", "Int", "Float", "String", "Vector3", "Enum"};

static _Atomic int started, restart_due;
static char root_dir[PLAT_PATH_MAX];   // <saves>/tree
static char status_text[160];

// ---- events from the game's threads (fs_host.c), taken by the tree thread
typedef struct Event {
    struct Event *next;
    int kind;                 // EV_READ, EV_COMMIT
    int slot;
    uint8_t *caption, *progress;
    size_t caption_n, progress_n;
    char save_kind[16], place[64];
} Event;
enum { EV_READ, EV_COMMIT };
static PlatMutex ev_lock = PLAT_MUTEX_INIT;
static Event *ev_head, **ev_tail = &ev_head;

static void ev_push(Event *e) {
    e->next = NULL;
    plat_mutex_lock(&ev_lock);
    *ev_tail = e;
    ev_tail = &e->next;
    plat_mutex_unlock(&ev_lock);
}

static Event *ev_take(void) {
    plat_mutex_lock(&ev_lock);
    Event *e = ev_head;
    if (e && !(ev_head = e->next)) ev_tail = &ev_head;
    plat_mutex_unlock(&ev_lock);
    return e;
}

static void ev_free(Event *e) {
    free(e->caption);
    free(e->progress);
    free(e);
}

// The slot folder's two files. 1 when both were read.
static int slot_files(const char *dir, Event *e) {
    char p[PLAT_PATH_MAX + 32];
    uint64_t n = 0;
    snprintf(p, sizeof p, "%s/caption.sav", dir);
    e->caption = plat_file_read(p, &n);
    e->caption_n = (size_t)n;
    snprintf(p, sizeof p, "%s/progress.sav", dir);
    n = 0;
    e->progress = plat_file_read(p, &n);
    e->progress_n = (size_t)n;
    return e->caption && e->progress;
}

// debug_save_tree: 0 off, 1 a save for each lasting thing the player does (background_flag leaves the rest out),
// 2 a save for every flag the game changes, background, unnamed and fast-changing ones too.
enum { MODE_OFF, MODE_CHOICES, MODE_ALL };
static int tree_mode(void) {
    const char *v = cfg_get("OOTPC_DEBUG_SAVE_TREE");
    if (!v || !*v) return MODE_CHOICES;
    int m = atoi(v);
    return m <= 0 ? MODE_OFF : m >= 2 ? MODE_ALL : MODE_CHOICES;
}

static int tree_on(void) { return tree_mode() != MODE_OFF; }

void save_tree_note_read(const char *path, int slot) {
    if (!atomic_load(&started) || slot < 0 || slot >= SLOTS || !tree_on()) return;
    Event *e = calloc(1, sizeof *e);
    if (!e) return;
    char dir[PLAT_PATH_MAX];
    const char *cut = strrchr(path, '/');
    const char *cut2 = strrchr(path, '\\');
    if (cut2 > cut) cut = cut2;
    snprintf(dir, sizeof dir, "%.*s", cut ? (int)(cut - path) : 0, path);
    e->kind = EV_READ;
    e->slot = slot;
    if (!slot_files(dir, e)) {
        ev_free(e);
        return;
    }
    ev_push(e);
}

void save_tree_committed(const char *dir, int slot, const char *kind, const char *place) {
    if (!atomic_load(&started) || slot < 0 || slot >= SLOTS || !tree_on()) return;
    Event *e = calloc(1, sizeof *e);
    if (!e) return;
    e->kind = EV_COMMIT;
    e->slot = slot;
    snprintf(e->save_kind, sizeof e->save_kind, "%s", kind ? kind : "");
    snprintf(e->place, sizeof e->place, "%s", place ? place : "");
    if (!slot_files(dir, e)) {
        ev_free(e);
        return;
    }
    ev_push(e);
}

// ---- the nodes in memory: what finding a node by its save or by its step needs
typedef struct {
    int32_t parent;
    uint32_t plen;            // progress.sav's size
    uint64_t hash;            // of the whole progress.sav
    uint64_t sig;             // of the step from the parent (save_tree.c step_sig)
    char *dir;                // its folder under the tree, "<place>/<number> <age> - <what happened>" (node_folder)
} TNode;
static TNode *nodes;
static int nn, ncap;
static _Atomic int cur_node = -1, node_count;

// u64 -> node id maps, open addressing; key 0 is a free cell.
typedef struct { uint64_t *k; int32_t *v; size_t cap, n; } Map;
static Map by_hash, by_step;

static uint64_t mix64(uint64_t x) {
    x ^= x >> 33, x *= 0xff51afd7ed558ccdull, x ^= x >> 33, x *= 0xc4ceb9fe1a85ec53ull, x ^= x >> 33;
    return x ? x : 1;
}

static int map_put(Map *m, uint64_t key, int32_t v) {
    if ((m->n + 1) * 2 > m->cap) {
        Map g = {.cap = m->cap ? m->cap * 2 : 4096};
        g.k = calloc(g.cap, sizeof *g.k);
        g.v = malloc(g.cap * sizeof *g.v);
        if (!g.k || !g.v) return free(g.k), free(g.v), 0;
        for (size_t i = 0; i < m->cap; i++)
            if (m->k[i]) {
                size_t j = m->k[i] & (g.cap - 1);
                while (g.k[j]) j = (j + 1) & (g.cap - 1);
                g.k[j] = m->k[i], g.v[j] = m->v[i], g.n++;
            }
        free(m->k), free(m->v);
        *m = g;
    }
    size_t j = key & (m->cap - 1);
    while (m->k[j] && m->k[j] != key) j = (j + 1) & (m->cap - 1);
    if (!m->k[j]) m->k[j] = key, m->n++;
    m->v[j] = v;
    return 1;
}

static int map_get(const Map *m, uint64_t key) {
    if (!m->cap) return -1;
    for (size_t j = key & (m->cap - 1); m->k[j]; j = (j + 1) & (m->cap - 1))
        if (m->k[j] == key) return m->v[j];
    return -1;
}

static uint64_t fnv64(uint64_t h, const void *data, size_t n) {
    const uint8_t *p = data;
    for (size_t i = 0; i < n; i++) h = (h ^ p[i]) * 0x100000001b3ull;
    return h;
}
#define FNV0 0xcbf29ce484222325ull

static uint64_t step_key(int parent, uint64_t sig) { return mix64(sig ^ mix64((uint64_t)(uint32_t)parent + 1)); }

static int node_add(int parent, uint32_t plen, uint64_t hash, uint64_t sig, const char *dir) {
    if (nn == ncap) {
        int cap = ncap ? ncap * 2 : 4096;
        TNode *g = realloc(nodes, (size_t)cap * sizeof *g);
        if (!g) return -1;
        nodes = g, ncap = cap;
    }
    char *d = strdup(dir);
    if (!d) return -1;
    nodes[nn] = (TNode){parent, plen, hash, sig, d};
    map_put(&by_hash, mix64(hash), nn);
    if (parent >= 0) map_put(&by_step, step_key(parent, sig), nn);
    atomic_store(&node_count, nn + 1);
    return nn++;
}

int save_tree_current(void) { return atomic_load(&cur_node); }
int save_tree_count(void) { return atomic_load(&node_count); }
int save_tree_restart_due(void) { return !fs_save_busy() && atomic_exchange(&restart_due, 0); }

// ---- files
static void status(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void status(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(status_text, sizeof status_text, fmt, ap);
    va_end(ap);
    char p[PLAT_PATH_MAX + 32], line[200];
    snprintf(p, sizeof p, "%s/status.txt", root_dir);
    int n = snprintf(line, sizeof line, "%s\n", status_text);
    plat_file_write_cached(p, line, (size_t)n);
}

static void current_write(int id) {
    atomic_store(&cur_node, id);
    char p[PLAT_PATH_MAX + 32], line[32];
    snprintf(p, sizeof p, "%s/current.txt", root_dir);
    int n = snprintf(line, sizeof line, "%d\n", id);
    plat_file_write_cached(p, line, (size_t)n);
}

// ---- node files: a node is a save folder as the game writes one, <its folder>/slot_NN with the slot's caption.sav
// and progress.sav unchanged, so it can be copied out, zipped or imported as it is.
static void slot_dir(const char *dir, int slot, char *out, size_t cap) {
    snprintf(out, cap, "%s/%s/slot_%02d", root_dir, dir, slot);
}

static void node_slot_dir(int id, int slot, char *out, size_t cap) { slot_dir(nodes[id].dir, slot, out, cap); }

// progress.sav is written last: a folder without it is a node whose writing did not finish.
static int node_write(const char *dir, int slot, const uint8_t *caption, size_t cn, const uint8_t *prog, size_t pn) {
    char d[PLAT_PATH_MAX + 64], p[PLAT_PATH_MAX + 96];
    slot_dir(dir, slot, d, sizeof d);
    plat_file_mkdir_p(d);
    snprintf(p, sizeof p, "%s/caption.sav", d);
    if (!plat_file_write_cached(p, caption, cn)) return 0;
    snprintf(p, sizeof p, "%s/progress.sav", d);
    return plat_file_write_cached(p, prog, pn);
}

// The slot whose folder node id holds, -1 when it has none.
static int node_slot(int id) {
    char d[PLAT_PATH_MAX + 64], p[PLAT_PATH_MAX + 96];
    for (int s = 0; s < SLOTS; s++) {
        node_slot_dir(id, s, d, sizeof d);
        snprintf(p, sizeof p, "%s/progress.sav", d);
        if (plat_file_exists(p)) return s;
    }
    return -1;
}

// A node's caption.sav (when caption is given) and progress.sav, malloc'd, and its slot. 1 on success.
static int node_read(int id, uint8_t **caption, size_t *cn, uint8_t **prog, size_t *pn, int *slot_out) {
    if (caption) *caption = NULL;
    *prog = NULL;
    int slot = id >= 0 && id < nn ? node_slot(id) : -1;
    if (slot < 0) return 0;
    char d[PLAT_PATH_MAX + 64], p[PLAT_PATH_MAX + 96];
    node_slot_dir(id, slot, d, sizeof d);
    uint64_t n = 0;
    if (caption) {
        snprintf(p, sizeof p, "%s/caption.sav", d);
        if (!(*caption = plat_file_read(p, &n))) return 0;
        *cn = (size_t)n;
    }
    snprintf(p, sizeof p, "%s/progress.sav", d);
    n = 0;
    if (!(*prog = plat_file_read(p, &n))) {
        if (caption) free(*caption), *caption = NULL;
        return 0;
    }
    *pn = (size_t)n;
    if (slot_out) *slot_out = slot;
    return 1;
}

// ---- tree.jsonl
static void index_load(void) {
    char p[PLAT_PATH_MAX + 32];
    snprintf(p, sizeof p, "%s/tree.jsonl", root_dir);
    FILE *f = fopen(p, "rb");
    if (!f) return;
    static char line[1 << 16];
    while (fgets(line, sizeof line, f)) {
        int id, parent;
        unsigned plen;
        unsigned long long hash, sig;
        if (sscanf(line, "{\"id\":%d,\"parent\":%d,\"plen\":%u,\"hash\":\"%llx\",\"sig\":\"%llx\"", &id, &parent, &plen,
                   &hash, &sig) != 5)
            continue;
        if (id != nn || parent >= nn) continue;   // lines of an id out of order: a torn write, left out
        char dir[PLAT_PATH_MAX];
        const char *d = strstr(line, ",\"dir\":\"");
        size_t dn = d ? strcspn(d + 8, "\"") : 0;
        if (d && dn && dn < sizeof dir) snprintf(dir, sizeof dir, "%.*s", (int)dn, d + 8);
        else snprintf(dir, sizeof dir, "nodes/%d/%d", id / 1000, id);
        node_add(parent, plen, hash, sig, dir);
    }
    fclose(f);
}

static void json_append(char **buf, size_t *n, size_t *cap, const char *fmt, ...) __attribute__((format(printf, 4, 5)));
static void json_append(char **buf, size_t *n, size_t *cap, const char *fmt, ...) {
    for (;;) {
        va_list ap;
        va_start(ap, fmt);
        int k = vsnprintf(*buf ? *buf + *n : NULL, *buf ? *cap - *n : 0, fmt, ap);
        va_end(ap);
        if (k < 0) return;
        if (*buf && *n + (size_t)k < *cap) {
            *n += (size_t)k;
            return;
        }
        size_t c = (*cap ? *cap : 1024) * 2;
        while (c < *n + (size_t)k + 1) c *= 2;
        char *g = realloc(*buf, c);
        if (!g) return;
        *buf = g, *cap = c;
    }
}

// ---- flags
typedef struct {
    uint64_t base;
    int count;
    uint32_t size, off;
    uint8_t *raw, *val;
    uint32_t *key;
    uint8_t *muted;                 // MUTE_*
    uint32_t (*hits)[NOISY_HITS];   // the times (ms) of the last changes, oldest first
} Snap;
static Snap snaps[T_TYPES];
static int mode = MODE_CHOICES;   // tree_mode as the stores were last read whole (a change reads them whole again)
enum { MUTE_NONE, MUTE_NOISY, MUTE_ALWAYS };   // MUTE_NOISY: for changing too often, until UNMUTE_MS pass quietly
static int rebase = 1;

typedef struct {
    uint8_t type;
    uint32_t key;
    uint8_t was[64], now[64];
} Change;
static Change pend[PENDING_MAX];
static int npend, pend_more;      // pend_more: changes past PENDING_MAX, counted only
static uint64_t first_ms, last_ms;

// ---- which changes start a branch: a lasting thing the player did (a rock broken, a chest opened, someone talked to,
// a cutscene watched, an item, a door, a switch, a room cleared). Left out: what goes on by itself (people walking their
// daily routes, the clock), where Link stands and looks, counts that go up and down in play (rupees, ammo, life),
// hints, menus, enemies that always come back, settings, and flags without a name. Every save holds them all the same;
// they only never start one, and two steps that differ in them alone are the same step.
static int starts_with(const char *s, const char *p) { return !strncmp(s, p, strlen(p)); }

static int background_flag(int t, uint32_t key) {
    if (t == DBG_FLAG_FLOAT || t == DBG_FLAG_VEC3 || t == DBG_FLAG_STRING) return 1;   // positions, turns, names
    const char *nm = dbg_flag_name(key), *where = dbg_flag_where(key);
    if (!nm) return 1;
    if (where && (starts_with(where, "Component/AIScheduleParam/") || starts_with(where, "Sequence/"))) return 1;
    static const char *const kPrefix[] = {
        "Sequence_", "System_", "UI_", "CaptionData.", "ActionGuide_", "DebugParam.", "PlayerStatus.", "Player_",
        "IsEnableUse", "HavePlayedNaviBalloonMessage.", "FindTargetNearDoor.", "Npc_TimeSignal.", "OptionData.",
    };
    for (size_t i = 0; i < sizeof kPrefix / sizeof *kPrefix; i++)
        if (starts_with(nm, kPrefix[i])) return 1;
    static const char *const kPart[] = {
        "_MeetingPosition", "GasaGosoPosition", "NoAttention", "IsInTheCage", "DoorClosed", "AlwaysRespawn", "StockNum",
        "_Assembled", "RailPoint", "RelayPoint", "CustomerArrived",
        "ForMessage",   // numbers put into a text box ("SimpleIntForMessage", "SimpleHourForMessageAs24Hour")
    };
    for (size_t i = 0; i < sizeof kPart / sizeof *kPart; i++)
        if (strstr(nm, kPart[i])) return 1;
    // someone reaching a stop on their route ("SaburoArrived_Action4", "Npc_Hylia_012_Child_AriivedHome"), not a story
    // arrival ("IsArrived_LonLonRanch_AfterMalonAppear") or a talk about one
    if ((strstr(nm, "Arrived") || strstr(nm, "Ariived")) && !starts_with(nm, "HaveTalked") &&
        (strstr(nm, "Npc_") || strstr(nm, "Action") || strstr(nm, "Position") || strstr(nm, "Home")))
        return 1;
    for (const char *a = strstr(nm, "_Action"); a; a = strstr(a + 1, "_Action"))   // a step of a routine: "_Action3"
        if (a[7] >= '0' && a[7] <= '9') return 1;
    return 0;
}

// Keys always left out: noisy.txt, written by hand.
static Map noisy;
static void noisy_load(void) {
    char p[PLAT_PATH_MAX + 32];
    snprintf(p, sizeof p, "%s/noisy.txt", root_dir);
    FILE *f = fopen(p, "rb");
    if (!f) return;
    unsigned k;
    while (fscanf(f, "%x", &k) == 1) map_put(&noisy, mix64(k), 1);
    fclose(f);
}

static void value_text(int t, const uint8_t *v, char *out, size_t n) {
    uint32_t u;
    float f[3];
    switch (t) {
    case DBG_FLAG_BOOL: snprintf(out, n, "%s", v[0] ? "on" : "off"); break;
    case DBG_FLAG_INT: memcpy(&u, v, 4), snprintf(out, n, "%d", (int32_t)u); break;
    case DBG_FLAG_FLOAT: memcpy(f, v, 4), snprintf(out, n, "%.9g", f[0]); break;
    case DBG_FLAG_ENUM: {
        memcpy(&u, v, 4);
        const char *nm = dbg_flag_name(u);
        if (nm) snprintf(out, n, "%s", nm);
        else snprintf(out, n, "%08x", u);
        break;
    }
    case DBG_FLAG_VEC3: memcpy(f, v, 12), snprintf(out, n, "%.1f %.1f %.1f", f[0], f[1], f[2]); break;
    default: snprintf(out, n, "%.*s", (int)strnlen((const char *)v, 64), (const char *)v);
    }
}

static void pend_note(int t, uint32_t key, const uint8_t *was, const uint8_t *now, uint64_t ms) {
    uint32_t w = kWidth[t];
    for (int i = 0; i < npend; i++)
        if (pend[i].key == key && pend[i].type == t) {
            memcpy(pend[i].now, now, w);
            if (!memcmp(pend[i].was, pend[i].now, w)) pend[i] = pend[--npend];   // back where it was: no change
            last_ms = ms;
            return;
        }
    if (npend == PENDING_MAX) {
        pend_more++;
        return;
    }
    Change *c = &pend[npend++];
    memset(c, 0, sizeof *c);
    c->type = (uint8_t)t, c->key = key;
    memcpy(c->was, was, w);
    memcpy(c->now, now, w);
    if (npend == 1) first_ms = ms;
    last_ms = ms;
}

static void pend_drop_key(uint32_t key) {
    for (int i = 0; i < npend; i++)
        if (pend[i].key == key) pend[i--] = pend[--npend];
}

// Reads store t whole: 2 a new baseline (the store moved or was not there), 1 read as before, 0 unreadable.
static int snap_read(int t) {
    Snap *s = &snaps[t];
    uint64_t base;
    int count;
    uint32_t size, off;
    if (!dbg_flag_store(t, &base, &count, &size, &off) || count <= 0) return 0;
    if (base != s->base || count != s->count) {
        free(s->raw), free(s->val), free(s->key), free(s->muted), free(s->hits);
        memset(s, 0, sizeof *s);
        s->raw = malloc((size_t)count * size);
        s->val = malloc((size_t)count * kWidth[t]);
        s->key = malloc((size_t)count * sizeof *s->key);
        s->muted = calloc((size_t)count, 1);
        s->hits = calloc((size_t)count, sizeof *s->hits);
        if (!s->raw || !s->val || !s->key || !s->muted || !s->hits) {
            free(s->raw), free(s->val), free(s->key), free(s->muted), free(s->hits);
            memset(s, 0, sizeof *s);
            return 0;
        }
        s->base = base, s->count = count, s->size = size, s->off = off;
        dbg_flag_store_keys(t, s->key, count);
        for (int i = 0; i < count; i++)
            s->muted[i] = mode != MODE_ALL && (!s->key[i] || map_get(&noisy, mix64(s->key[i])) >= 0 ||
                                               background_flag(t, s->key[i]))
                              ? MUTE_ALWAYS
                              : MUTE_NONE;
        if (!plat_mem_read(base, s->raw, (size_t)count * size)) return 0;
        return 2;
    }
    return plat_mem_read(base, s->raw, (size_t)count * size) ? 1 : 0;
}

static void snap_take(int t) {
    Snap *s = &snaps[t];
    for (int i = 0; i < s->count; i++) memcpy(s->val + (size_t)i * kWidth[t], s->raw + (size_t)i * s->size + s->off, kWidth[t]);
}

static void snap_compare(int t, uint64_t ms) {
    Snap *s = &snaps[t];
    uint32_t w = kWidth[t];
    for (int i = 0; i < s->count; i++) {
        const uint8_t *now = s->raw + (size_t)i * s->size + s->off;
        uint8_t *was = s->val + (size_t)i * w;
        if (!memcmp(now, was, w)) continue;
        if (mode == MODE_ALL) {
            pend_note(t, s->key[i], was, now, ms);   // every flag: no muting for changing too often either
        } else if (s->muted[i] != MUTE_ALWAYS) {
            uint32_t *h = s->hits[i], at = (uint32_t)ms | 1;
            if (s->muted[i] == MUTE_NOISY && at - h[NOISY_HITS - 1] > UNMUTE_MS)
                s->muted[i] = MUTE_NONE, memset(h, 0, sizeof s->hits[i]);
            memmove(h, h + 1, (NOISY_HITS - 1) * sizeof *h);
            h[NOISY_HITS - 1] = at;
            uint32_t fast = h[NOISY_HITS - NOISY_FAST];
            if (s->muted[i] == MUTE_NONE) {
                if ((fast && at - fast < NOISY_FAST_MS) || (h[0] && at - h[0] < NOISY_SLOW_MS)) {
                    s->muted[i] = MUTE_NOISY;
                    pend_drop_key(s->key[i]);
                } else {
                    pend_note(t, s->key[i], was, now, ms);
                }
            }
        }
        memcpy(was, now, w);
    }
}

static int change_cmp(const void *a, const void *b) {
    const Change *x = a, *y = b;
    if (x->type != y->type) return x->type < y->type ? -1 : 1;
    return x->key < y->key ? -1 : x->key > y->key;
}

// The step's signature: the flags changed, their new values and the place.
static uint64_t step_sig(const char *place) {
    qsort(pend, (size_t)npend, sizeof *pend, change_cmp);
    uint64_t h = fnv64(FNV0, place, strlen(place));
    for (int i = 0; i < npend; i++) {
        h = fnv64(h, &pend[i].type, 1);
        h = fnv64(h, &pend[i].key, 4);
        h = fnv64(h, pend[i].now, kWidth[pend[i].type]);
    }
    return h;
}

// A short label: the first change that reads like progress (a bool switched on), else the first one, and how many more.
static void step_label(char *out, size_t cap, const char *kind, const char *place) {
    int pick = -1;
    for (int i = 0; i < npend && pick < 0; i++)
        if (pend[i].type == T_BOOL && pend[i].now[0]) pick = i;
    if (pick < 0 && npend) pick = 0;
    if (pick < 0) {
        snprintf(out, cap, "%s%s%s", !strcmp(kind, "load") ? "Loaded save" : !strcmp(kind, "manual") ? "Saved" : "Autosave",
                 *place ? " - " : "", place);
        return;
    }
    char nb[16], a[80], b[80];
    const char *nm = dbg_flag_name(pend[pick].key);
    if (!nm) snprintf(nb, sizeof nb, "flag %08x", pend[pick].key), nm = nb;
    value_text(pend[pick].type, pend[pick].was, a, sizeof a);
    value_text(pend[pick].type, pend[pick].now, b, sizeof b);
    int more = npend - 1 + pend_more;
    char tail[32] = "";
    if (more > 0) snprintf(tail, sizeof tail, " (+%d more)", more);
    if (pend[pick].type == T_BOOL) snprintf(out, cap, "%s%s%s", nm, pend[pick].now[0] ? "" : " off", tail);
    else snprintf(out, cap, "%s: %s -> %s%s", nm, a, b, tail);
}

// ---- folder names: "<place>/<number> <age> - <what happened>", e.g. "Kakariko Village/000137 Child - Talked to Kokiri
// Mido". The number (the node's id) keeps a place's saves in the order they were made.
static int skip_word(const char *w, size_t n) {
    static const char *const kSkip[] = {"Npc", "NPC", "Obj", "Enemy", "Default", "DefaultActor", "Child", "Adult",
                                        "Exp", "Level", "Item", "Weapon", "Armor", "Pouch"};
    for (size_t i = 0; i < sizeof kSkip / sizeof *kSkip; i++)
        if (strlen(kSkip[i]) == n && !strncmp(w, kSkip[i], n)) return 1;
    int digits = 1, hex4 = n == 4;
    for (size_t i = 0; i < n; i++) {
        char c = w[i];
        if (c < '0' || c > '9') digits = 0;
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) hex4 = 0;
    }
    return digits || hex4;
}

// "KakarikoVillage_ShiroArrived" -> "Kakariko Village Shiro Arrived": parts split at '_' and '.', words at case changes;
// numbers and words that say nothing (Npc, Child, Item) left out.
static void name_words(const char *in, char *out, size_t cap) {
    size_t k = 0;
    for (const char *p = in; *p;) {
        size_t n = strcspn(p, "_.");
        if (n && !skip_word(p, n)) {
            if (k && k + 1 < cap) out[k++] = ' ';
            for (size_t i = 0; i < n && k + 2 < cap; i++) {
                char c = p[i], b = i ? p[i - 1] : 0, a = i + 1 < n ? p[i + 1] : 0;
                int up = c >= 'A' && c <= 'Z', b_lo = b >= 'a' && b <= 'z', b_up = b >= 'A' && b <= 'Z';
                int a_lo = a >= 'a' && a <= 'z', dig = c >= '0' && c <= '9';
                if (i && ((up && b_lo) || (up && b_up && a_lo) || (dig && (b_lo || b_up)))) out[k++] = ' ';
                out[k++] = c;
            }
        }
        p += n;
        p += strspn(p, "_.");
    }
    out[k] = 0;
}

// Keeps what a Windows folder name can hold: letters, digits, spaces and a few marks; no runs of spaces, nothing
// trailing (a folder name may not end in a space or a dot).
static void folder_safe(char *s, size_t cap) {
    size_t k = 0;
    for (size_t i = 0; s[i]; i++) {
        char c = s[i];
        int ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || strchr(" '(),+-&", c);
        if (!ok) c = ' ';
        if (c == ' ' && (!k || s[k - 1] == ' ')) continue;
        if (k + 1 < cap) s[k++] = c;
    }
    while (k && (s[k - 1] == ' ' || s[k - 1] == '.')) k--;
    s[k] = 0;
}

// What a change says, the most telling first: an item, a purchase, a dungeon's map or key, a defeated enemy, a first
// visit, a cutscene, a talk; then any other flag switched on; then the rest.
static const struct { const char *prefix, *say; } kSay[] = {
    {"IsGet.", "Got "}, {"HavePlayedGetEventAtShop.", "Bought "}, {"HavePlayedGetEvent.", "Got "}, {"DungeonInfo.", ""},
    {"DefeatedEnemyNum.", "Defeated "}, {"IsVisitLocation.", "First visit to "}, {"HavePlayedEvent.", "Saw "},
    {"HaveTalked_Special.", "Talked to "}, {"HaveTalked.", "Talked to "},
};
static int change_rank(const Change *c, const char *nm) {
    if (!nm) return 0;
    for (size_t i = 0; i < sizeof kSay / sizeof *kSay; i++)
        if (starts_with(nm, kSay[i].prefix)) return 100 - (int)i;
    return c->type == T_BOOL && c->now[0] ? 20 : 10;
}

// The folder of new node id: the place without its age, then the number, the age and what happened.
static void node_folder(int id, const char *kind, const char *place, char *out, size_t cap) {
    char base[64], where[96], what[160] = "", title[200];
    snprintf(base, sizeof base, "%s", place);
    const char *age = "";
    size_t bn = strlen(base);
    if (bn > 6 && !strcmp(base + bn - 6, "_Child")) base[bn - 6] = 0, age = " Child";
    else if (bn > 6 && !strcmp(base + bn - 6, "_Adult")) base[bn - 6] = 0, age = " Adult";
    name_words(base, where, sizeof where);
    folder_safe(where, sizeof where);
    if (!*where) snprintf(where, sizeof where, "Somewhere");
    int pick = -1, best = -1;
    for (int i = 0; i < npend; i++) {
        int r = change_rank(&pend[i], dbg_flag_name(pend[i].key));
        if (r > best) best = r, pick = i;
    }
    if (pick >= 0 && dbg_flag_name(pend[pick].key)) {   // flags without a name never start a step (background_flag)
        const char *nm = dbg_flag_name(pend[pick].key), *say = "";
        char rest[128];
        if (best >= 90) {
            size_t i = (size_t)(100 - best);
            say = kSay[i].say;
            nm += strlen(kSay[i].prefix);
        } else if (*base && starts_with(nm, base) && nm[strlen(base)] == '_') {
            nm += strlen(base) + 1;   // "KakarikoVillage_GatheringTokenGold..." in Kakariko Village: the place is the folder
        }
        name_words(nm, rest, sizeof rest);
        int more = npend - 1 + pend_more;
        char tail[24] = "";
        if (pend[pick].type == T_BOOL && !pend[pick].now[0]) snprintf(tail, sizeof tail, " (undone)");
        if (more > 0) snprintf(tail + strlen(tail), sizeof tail - strlen(tail), " (+%d)", more);
        snprintf(what, sizeof what, "%s%s%s", say, rest, tail);
    }
    if (!strcmp(kind, "load")) snprintf(title, sizeof title, "Loaded save");
    else if (!strcmp(kind, "manual")) snprintf(title, sizeof title, "Your save%s%s", *what ? " - " : "", what);
    else snprintf(title, sizeof title, "%s", *what ? what : "Autosave");
    title[70] = 0;
    folder_safe(title, sizeof title);
    snprintf(out, cap, "%s/%06d%s - %s", where, id, age, title);
}

// ---- making nodes
static uint8_t *cur_prog;          // the whole progress.sav of the current node
static size_t cur_prog_n;
static int cur_slot = -1;          // the slot the game plays
static uint64_t asked_ms;          // when the tree last asked for a save, 0 when no answer is due
// The area where the game last took a save request and wrote nothing (no_save_area_watch): there the tree makes its
// saves itself without asking first. Empty once the game writes a save or the player is in another area.
static char no_save_area[64];

static void set_current(int id, uint8_t *prog, size_t n) {
    free(cur_prog);
    cur_prog = prog, cur_prog_n = n;
    current_write(id);
}

// A node for the save in e (its caption and progress), from the changes pending, under the current node.
static void node_from(Event *e, const char *kind) {
    char place[64], label[256], tm[32];
    snprintf(place, sizeof place, "%s", e->place);
    if (!*place) dbg_current_area(place);
    int parent = atomic_load(&cur_node);
    uint64_t hash = fnv64(FNV0, e->progress, e->progress_n), sig = step_sig(place);
    int same = map_get(&by_hash, mix64(hash));
    int step = parent >= 0 && (npend || strcmp(kind, "load")) ? map_get(&by_step, step_key(parent, sig)) : -1;
    if (same >= 0 || step >= 0) {   // a save the tree has, or a step it made before from here: that node
        int id = same >= 0 ? same : step;
        uint8_t *prog = NULL;
        size_t pn = 0;
        if (same >= 0) prog = e->progress, pn = e->progress_n, e->progress = NULL;
        else node_read(id, NULL, NULL, &prog, &pn, NULL);
        set_current(id, prog, pn);
        npend = pend_more = 0;
        status("On node %d of %d (%s again)", id, nn, same >= 0 ? "a save the tree has" : "a step made before");
        return;
    }
    int id = nn;
    char dir[PLAT_PATH_MAX];
    node_folder(id, kind, place, dir, sizeof dir);
    if (!node_write(dir, e->slot, e->caption, e->caption_n, e->progress, e->progress_n)) {
        status("A node could not be written (disk full?)");
        return;
    }
    if (node_add(parent, (uint32_t)e->progress_n, hash, sig, dir) != id) return;
    step_label(label, sizeof label, kind, place);
    struct tm t;
    plat_local_time(&t);
    snprintf(tm, sizeof tm, "%04d-%02d-%02d %02d:%02d:%02d", t.tm_year + 1900, t.tm_mon + 1, t.tm_mday, t.tm_hour, t.tm_min,
             t.tm_sec);
    char *j = NULL, esc[512];
    size_t jn = 0, jc = 0;
    json_append(&j, &jn, &jc, "{\"id\":%d,\"parent\":%d,\"plen\":%u,\"hash\":\"%016llx\",\"sig\":\"%016llx\"", id, parent,
                (unsigned)e->progress_n, (unsigned long long)hash, (unsigned long long)sig);
    json_append(&j, &jn, &jc, ",\"dir\":\"%s\"", dir);   // folder_safe: no quotes or backslashes to escape
    json_append(&j, &jn, &jc, ",\"time\":\"%s\",\"slot\":%d,\"kind\":\"%s\"", tm, e->slot, kind);
    json_append(&j, &jn, &jc, ",\"area\":\"%s\"", sz_json_escape(place, esc, sizeof esc));
    json_append(&j, &jn, &jc, ",\"label\":\"%s\",\"n\":%d,\"changes\":[", sz_json_escape(label, esc, sizeof esc), npend + pend_more);
    for (int i = 0; i < npend && i < CHANGES_KEPT; i++) {
        char nb[16], a[80], b[80], ea[200], eb[200];
        const char *nm = dbg_flag_name(pend[i].key);
        if (!nm) snprintf(nb, sizeof nb, "flag %08x", pend[i].key), nm = nb;
        value_text(pend[i].type, pend[i].was, a, sizeof a);
        value_text(pend[i].type, pend[i].now, b, sizeof b);
        json_append(&j, &jn, &jc, "%s[\"%s\",\"%s\",\"%s\",\"%s\"]", i ? "," : "", kType[pend[i].type],
                    sz_json_escape(nm, esc, sizeof esc), sz_json_escape(a, ea, sizeof ea), sz_json_escape(b, eb, sizeof eb));
    }
    json_append(&j, &jn, &jc, "]}\n");
    char p[PLAT_PATH_MAX + 32];
    snprintf(p, sizeof p, "%s/tree.jsonl", root_dir);
    FILE *f = j ? fopen(p, "ab") : NULL;
    if (f) fwrite(j, 1, jn, f), plat_file_sync(f), fclose(f);
    free(j);
    set_current(id, e->progress, e->progress_n);
    e->progress = NULL;
    npend = pend_more = 0;
    status("Node %d: %s", id, label);
}

static void on_event(Event *e) {
    if (e->kind == EV_READ) {   // a slot loads: its save is the node the game is on from here
        cur_slot = e->slot;
        rebase = 1;
        npend = pend_more = 0;
        atomic_store(&cur_node, -1);
        free(cur_prog);
        cur_prog = NULL;
        node_from(e, "load");
        return;
    }
    cur_slot = e->slot;
    const char *kind = asked_ms ? "flag" : *e->save_kind ? e->save_kind : "autosave";
    asked_ms = 0;
    no_save_area[0] = 0;   // the game writes saves again
    // A save made when nothing that starts a branch changed (the game's own autosave while people walk about) is the
    // node the game is on still. A manual save is a point the player chose: it is a node.
    int cur = atomic_load(&cur_node);
    if (cur >= 0 && !npend && !pend_more && strcmp(kind, "manual")) {
        status("On node %d of %d (a save with nothing new)", cur, nn);
        return;
    }
    node_from(e, kind);
}

// ---- loading a node the launcher asked for (load.txt): its slot becomes the game's save at the next start
static int export_visit(void *ctx, const char *rel, const uint8_t *data, size_t n);
typedef struct {
    int count, cap;
    char **rel;
    uint8_t **data;
    size_t *size;
} Files;

static int files_add(Files *f, const char *rel, const uint8_t *data, size_t n) {
    if (f->count == f->cap) {
        int cap = f->cap ? f->cap * 2 : 32;
        char **r = realloc(f->rel, (size_t)cap * sizeof *r);
        if (r) f->rel = r;
        uint8_t **d = realloc(f->data, (size_t)cap * sizeof *d);
        if (d) f->data = d;
        size_t *s = realloc(f->size, (size_t)cap * sizeof *s);
        if (s) f->size = s;
        if (!r || !d || !s) return 0;
        f->cap = cap;
    }
    uint8_t *c = malloc(n ? n : 1);
    char *nm = strdup(rel);
    if (!c || !nm) return free(c), free(nm), 0;
    memcpy(c, data, n);
    f->rel[f->count] = nm, f->data[f->count] = c, f->size[f->count] = n;
    f->count++;
    return 1;
}

static int export_visit(void *ctx, const char *rel, const uint8_t *data, size_t n) { return files_add(ctx, rel, data, n); }

static void files_free(Files *f) {
    for (int i = 0; i < f->count; i++) free(f->rel[i]), free(f->data[i]);
    free(f->rel), free(f->data), free(f->size);
}

// caption.sav's save time, which the game's Load list shows and sorts by (newest first): Int values under keys the flag
// list has no names for (fs_host.c caption_read has the layout), found by their values in a save made at a known time.
static const uint32_t kCaptionTime[6] = {
    0x9811a3f7u, 0xdfd840d3u, 0xbd46f485u, 0x23f3d75eu, 0x27853bf7u, 0x5ee50bedu,   // year, month, day, hour, minute, second
};

// Gives the caption the time now, so the save put in a slot heads the Load list, under its cursor.
static void caption_stamp_now(uint8_t *b, size_t n) {
    uint32_t w[3];
    if (n < 0x30) return;
    memcpy(w, b, sizeof w);
    if (w[0] != 0x01020304u) return;
    uint32_t end = w[2] < n ? w[2] : (uint32_t)n;
    struct tm t;
    plat_local_time(&t);
    int32_t v[6] = {t.tm_year + 1900, t.tm_mon + 1, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec};
    for (uint32_t at = 0x28; at + 8 <= end; at += 8) {
        uint32_t key;
        memcpy(&key, b + at, 4);
        for (int i = 0; i < 6; i++)
            if (key == kCaptionTime[i]) memcpy(b + at + 4, &v[i], 4);
    }
}

// ---- a save made here from the running game's flags, for where the game makes none (save_tree.h save_tree_build)
// A save file (progress.sav, caption.sav): words {0x01020304, ?, end of the table}, then from 0x28 to that end a table
// of 8-byte entries {key, value}. A Bool, Int, Float or Enum entry holds its value; a String64 or Vector3 entry holds the
// offset of its 64 or 12 bytes further on. Arrays and the types the flag stores lack (debug_tools.h DBG_FLAG_*) keep
// what the template has.
static int build_from_stores(uint8_t *b, size_t n, int *patched, int *changed) {
    uint32_t w[3];
    if (n < 0x30) return 0;
    memcpy(w, b, sizeof w);
    if (w[0] != 0x01020304u) return 0;
    uint8_t *raw[T_TYPES] = {0};
    uint32_t *keys[T_TYPES] = {0}, size[T_TYPES] = {0}, off[T_TYPES] = {0};
    Map at = {0};
    int ok = 1;
    for (int t = 0; ok && t < T_TYPES; t++) {
        uint64_t base;
        int count;
        if (!dbg_flag_store(t, &base, &count, &size[t], &off[t]) || count <= 0) continue;
        raw[t] = malloc((size_t)count * size[t]);
        keys[t] = malloc((size_t)count * sizeof *keys[t]);
        if (!raw[t] || !keys[t] || !plat_mem_read(base, raw[t], (size_t)count * size[t])) {
            ok = 0;
            break;
        }
        dbg_flag_store_keys(t, keys[t], count);
        for (int i = 0; ok && i < count; i++)
            if (keys[t][i]) ok = map_put(&at, mix64(keys[t][i]), (int32_t)((uint32_t)t << 24 | (uint32_t)i));
    }
    int np = 0, nc = 0;
    uint32_t end = w[2] < n ? w[2] : (uint32_t)n;
    for (uint32_t e = 0x28; ok && e + 8 <= end; e += 8) {
        uint32_t key, val;
        memcpy(&key, b + e, 4);
        memcpy(&val, b + e + 4, 4);
        int v = key ? map_get(&at, mix64(key)) : -1;
        if (v < 0) continue;
        int t = (int)((uint32_t)v >> 24), i = v & 0xffffff;
        const uint8_t *src = raw[t] + (size_t)i * size[t] + off[t];
        uint8_t *dst;
        size_t len;
        uint32_t inl;
        if (t == DBG_FLAG_STRING || t == DBG_FLAG_VEC3) {
            len = t == DBG_FLAG_STRING ? 64 : 12;
            if (val < end || (size_t)val + len > n) continue;   // not an offset past the table: not this kind of entry
            if (t == DBG_FLAG_VEC3) src += 12;   // the copy the game saves (debug_tools.c store_info)
            dst = b + val;
        } else {
            inl = t == DBG_FLAG_BOOL ? src[0] != 0 : 0;
            if (t != DBG_FLAG_BOOL) memcpy(&inl, src, 4);
            src = (const uint8_t *)&inl, dst = b + e + 4, len = 4;
        }
        np++;
        if (memcmp(dst, src, len)) memcpy(dst, src, len), nc++;
    }
    for (int t = 0; t < T_TYPES; t++) free(raw[t]), free(keys[t]);
    free(at.k), free(at.v);
    if (patched) *patched = np;
    if (changed) *changed = nc;
    return ok && np > 0;
}

int save_tree_build(uint8_t *data, size_t n, int *patched, int *changed) { return build_from_stores(data, n, patched, changed); }

// A node where the game makes no save (temp_saves): the current node's two files with the running game's flags written
// over them. Arrays keep the current node's values. 1 when the node was made.
static int built_node(void) {
    int cur = atomic_load(&cur_node);
    Event *e = calloc(1, sizeof *e);
    if (!e) return 0;
    e->kind = EV_COMMIT;
    int ok = cur >= 0 && node_read(cur, &e->caption, &e->caption_n, &e->progress, &e->progress_n, &e->slot) &&
             build_from_stores(e->progress, e->progress_n, NULL, NULL);
    if (ok) {
        build_from_stores(e->caption, e->caption_n, NULL, NULL);
        caption_stamp_now(e->caption, e->caption_n);
        node_from(e, "flag");
    }
    ev_free(e);
    return ok;
}

// The game's UI screen (debug_move.c UI_SCREEN_KEY): the hashes of the values the route to the Load screen passes.
#define UI_SCREEN_KEY 0x578d41e1u
#define UI_NONE 0xb6eede09u     // in play, no menu
#define UI_ITEMS 0x96ba09feu    // the pause menu's Items tab
#define UI_SYSTEM 0x76e3413eu   // its System tab
#define UI_SAVE 0x42814d01u     // the Save list
#define UI_LOAD 0x3b849e33u     // the Load list and the screen that asks to load a save
#define UI_OPTION 0xa60ba395u   // Options

static uint32_t ui_screen(void) {
    uint32_t v = 0;
    return dbg_flag_get_u32(DBG_FLAG_ENUM, UI_SCREEN_KEY, &v) ? v : 0;
}

static void press(const char *button) {
    char s[32];
    snprintf(s, sizeof s, "0 %s 4", button);
    input_script_live(s);
}

// Waits up to ms for the UI screen to be want, pressing repeat (when given) every gap_ms meanwhile. 1 when it is.
static int wait_screen(uint32_t want, int ms, const char *repeat, int gap_ms) {
    uint64_t t0 = plat_now_ns(), next = 0;
    while (plat_now_ns() - t0 < (uint64_t)ms * 1000000ull) {
        if (ui_screen() == want) return 1;
        if (repeat && plat_now_ns() >= next) press(repeat), next = plat_now_ns() + (uint64_t)gap_ms * 1000000ull;
        plat_sleep_ms(15);
    }
    return ui_screen() == want;
}

// The route under the held picture. From play: the pause menu (Plus; it opens on the tab it was left on), R until the
// System tab (past the last tab R stays there), Load (the button under Save, where the cursor starts), then A on the
// list's newest save, which the save just put in a slot is (caption_stamp_now). The list takes A only once the game
// has read the captions, so A repeats until SelectedSaveDataListBtnIndex, which the list sets as it opens the screen
// that asks to load the save, is no longer the -1 put there first. Up then puts that screen's cursor on Load (Up stops
// at the top button). 1 when the screen is up.
static int load_route(void) {
    uint32_t sel = dbg_flag_key("SelectedSaveDataListBtnIndex");
    uint32_t s = ui_screen();
    if (s != UI_NONE && s != UI_ITEMS && s != UI_SYSTEM) return 0;   // a cutscene, the ocarina, another screen
    if (!dbg_flag_set(sel, -1)) return 0;
    if (s == UI_NONE) {
        press("PLUS");
        uint64_t t0 = plat_now_ns();   // the menu did not open (an event holds the game): R would quick-select an item
        while (ui_screen() == UI_NONE && plat_now_ns() - t0 < 1000000000ull) plat_sleep_ms(15);
        if (ui_screen() == UI_NONE) return 0;
    }
    if (!wait_screen(UI_SYSTEM, 3000, "R", 150)) return 0;
    // Down from Save, where the tab's cursor starts, then A. A press lost to the tab's animation is made good: A again
    // when nothing opened, back and Down when Save opened (the Down was lost), back and Up when Options did (the cursor
    // was on Load already).
    const char *move = "DOWN";
    for (int tries = 0; tries < 5; tries++) {
        plat_sleep_ms(tries ? 400 : 200);
        if (move) press(move), plat_sleep_ms(150);
        press("A");
        uint64_t t0 = plat_now_ns();
        while ((s = ui_screen()) == UI_SYSTEM && plat_now_ns() - t0 < 1500000000ull) plat_sleep_ms(15);
        if (s == UI_LOAD) break;
        if (s == UI_SYSTEM) {
            move = NULL;
            continue;
        }
        if (s != UI_SAVE && s != UI_OPTION) return 0;
        move = s == UI_SAVE ? "DOWN" : "UP";
        press("B");
        if (!wait_screen(UI_SYSTEM, 3000, NULL, 0)) return 0;
    }
    if (s != UI_LOAD) return 0;
    uint64_t t0 = plat_now_ns(), next = 0;
    int v = -1;
    while (dbg_flag_get(sel, &v) && v < 0 && plat_now_ns() - t0 < 8000000000ull) {
        if (plat_now_ns() >= next) press("A"), next = plat_now_ns() + 150000000ull;
        plat_sleep_ms(15);
    }
    if (v < 0) return 0;
    for (int i = 0; i < 4; i++) plat_sleep_ms(i ? 150 : 300), press("UP");
    plat_sleep_ms(100);
    return 1;
}

// The game's own screen that asks to load the save in the slot, opened from play while a frame of the game stays on
// screen (gfx_hold_picture), so the player sees the game, then that screen. The game first reads its save list again
// (load_node asked for it), for the slot's new caption. 0 when it could not be reached; the route then stops wherever
// it got to.
static int open_load_screen(void) {
    uint64_t t0 = plat_now_ns();
    for (;;) {
        int st = dbg_save_list_state();
        if (st > 0 || plat_now_ns() - t0 > 3000000000ull) break;
        if (st < 0) dbg_save_list_reload();   // the list was still being read: ask again
        plat_sleep_ms(20);
    }
    int held = gfx_hold_picture();
    for (int i = 0; held && !gfx_picture_held() && i < 20; i++) plat_sleep_ms(10);
    int ok = load_route();
    if (held) gfx_hold_release();
    return ok;
}

// ---- the game's temporary save mode (System_IsSaveTemporaryStorage, set by the game in the escape from the collapsing
// castle): the game's save to a slot does nothing (it returns at once while the flag is set, and in that area the save
// it queues with the flag cleared is never written either), and the Load list cannot read the slots meanwhile (its
// reading waits; the list stays empty). The flag is not in the save files, only in the running game. In that mode the
// tree makes the save itself (built_node). A load turns the mode off for the Load list (temp_off) and turns it back on
// once the player is back in play in the same area without having loaded (temp_watch).
#define TEMP_PLAY_MS 3000   // in play this long, in the area, nothing loading: the load was cancelled
static int temp_restore;    // the mode was turned off for a load
static char temp_area[64];  // the area it was turned off in
static uint64_t temp_play_since;

static int flag_on(const char *name) { return dbg_flag_get_bool(dbg_flag_key(name)) == 1; }
static int temp_saves(void) { return flag_on("System_IsSaveTemporaryStorage"); }

static void temp_off(void) {
    if (!temp_saves() || !dbg_current_area(temp_area)) return;
    if (!dbg_flag_set_bool(dbg_flag_key("System_IsSaveTemporaryStorage"), 0)) return;
    temp_restore = 1;
    temp_play_since = 0;
}

static void temp_watch(uint64_t ms) {
    char area[64];
    if (!temp_restore || !dbg_current_area(area)) return;
    if (strcmp(area, temp_area)) {   // another area: the save was loaded, the mode stays off
        temp_restore = 0;
        return;
    }
    if (ui_screen() != UI_NONE || flag_on("Sequence_IsLoadingBanc") || flag_on("System_IsSaveLoading")) {
        temp_play_since = 0;
        return;
    }
    if (!temp_play_since) temp_play_since = ms;
    if (ms - temp_play_since < TEMP_PLAY_MS) return;
    dbg_flag_set_bool(dbg_flag_key("System_IsSaveTemporaryStorage"), 1);
    temp_restore = 0;
    status("No load: the game keeps its saves in this area aside again");
}

static void load_node(int id) {
    uint8_t *cap = NULL, *prog = NULL;
    size_t cn = 0, pn = 0;
    int slot = 0;
    if (!node_read(id, &cap, &cn, &prog, &pn, &slot)) {
        status("Node %d could not be read", id);
        return;
    }
    int at_title = save_title_showing();
    if (!at_title) caption_stamp_now(cap, cn);
    Files f = {0};
    int ok = fs_save_export(export_visit, &f) >= 0;
    char want_c[32], want_p[32];
    snprintf(want_c, sizeof want_c, "slot_%02d/caption.sav", slot);
    snprintf(want_p, sizeof want_p, "slot_%02d/progress.sav", slot);
    int had_c = 0, had_p = 0;
    for (int i = 0; ok && i < f.count; i++) {
        if (!strcmp(f.rel[i], want_c)) free(f.data[i]), f.data[i] = cap, f.size[i] = cn, cap = NULL, had_c = 1;
        else if (!strcmp(f.rel[i], want_p)) free(f.data[i]), f.data[i] = prog, f.size[i] = pn, prog = NULL, had_p = 1;
    }
    if (ok && !had_c) ok = files_add(&f, want_c, cap, cn);
    if (ok && !had_p) ok = files_add(&f, want_p, prog, pn);
    free(cap), free(prog);
    if (ok) ok = !fs_save_import_stage(f.count, (const char *const *)f.rel, (const uint8_t *const *)f.data, f.size);
    files_free(&f);
    if (!ok) {
        status("Node %d could not be staged as the save", id);
        return;
    }
    char tag[64];
    snprintf(tag, sizeof tag, "save tree node %d", id);
    fs_save_import_tag(tag);
    // At the title nothing of the save is held yet, so it can be the save at once. In play it can be too: the game loads
    // it through its own Load screen, which reads the slot from the disk; what the game holds of the save it plays is
    // what that load replaces. Only when the save cannot be swapped now (a save being written) does the game restart.
    if (fs_save_import_now() == 0) {
        if (!at_title) temp_off();   // before the list reads the slots again
        dbg_save_list_reload();
        if (at_title) {
            status("Node %d is the save in slot %d now: load it from the title", id, slot);
        } else {
            npend = pend_more = 0;
            asked_ms = 0;
            if (open_load_screen()) status("Node %d is in slot %d: choose Load to play it", id, slot);
            else status("Node %d is in slot %d: open Load in the pause menu's System tab, it is the newest save", id, slot);
        }
    } else {
        atomic_store(&restart_due, 1);
        status("Node %d is staged in slot %d: the game restarts to load it", id, slot);
    }
}

static void load_poll(void) {
    char p[PLAT_PATH_MAX + 32];
    snprintf(p, sizeof p, "%s/load.txt", root_dir);
    uint64_t n = 0;
    uint8_t *b = plat_file_read(p, &n);
    if (!b) return;
    plat_fs_unlink(p);
    int id = atoi((const char *)b);
    free(b);
    if (id >= 0 && id < nn) load_node(id);
    else status("No node %d to load", id);
}

// ---- the thread
static uint64_t now_ms(void) { return plat_now_ns() / 1000000ull; }

static void tree_thread(void *arg) {
    (void)arg;
    plat_file_mkdir_p(root_dir);
    index_load();
    noisy_load();
    status("%d nodes; waiting for a save to be played", nn);
    char p[PLAT_PATH_MAX + 32];
    snprintf(p, sizeof p, "%s/current.txt", root_dir);
    current_write(-1);
    uint64_t last_load_poll = 0;
    for (;;) {
        uint64_t ms = now_ms();
        for (Event *e; (e = ev_take());) on_event(e), ev_free(e);
        if (ms - last_load_poll >= 500) load_poll(), last_load_poll = ms;
        int m = tree_mode();
        if (m != mode) {   // the setting changed: what is muted changes, so every store is read whole again
            mode = m;
            for (int t = 0; t < T_TYPES; t++) snaps[t].base = 0;
            npend = pend_more = 0;
            status("%s", m == MODE_OFF ? "Off" : m == MODE_ALL ? "Saving at every flag change" : "Saving at every choice");
        }
        int playing = mode != MODE_OFF && cur_slot >= 0 && !save_title_showing();
        int any = 0;
        for (int t = 0; playing && t < T_TYPES; t++) {
            int r = snap_read(t);
            if (!r) continue;
            any = 1;
            if (r == 2 || rebase) snap_take(t);
            else snap_compare(t, ms);
        }
        if (any) rebase = 0;
        temp_watch(ms);
        char area[64];
        if (no_save_area[0] && (!dbg_current_area(area) || strcmp(area, no_save_area))) no_save_area[0] = 0;
        if (asked_ms && ms - asked_ms > ANSWER_MS && !fs_save_busy()) {
            // The game took the request and wrote nothing: some areas and events never save (seen in HyruleField_Child
            // after the Ocarina of Time scene, with System_IsSaveTemporaryStorage off). A late save of the game finds
            // nothing pending and makes no second node (on_event).
            asked_ms = 0;
            if (playing && npend) built_node();
            if (dbg_current_area(area)) {
                snprintf(no_save_area, sizeof no_save_area, "%s", area);
                rt_log("save tree: the game does not save in %s; the tree makes its saves there itself", area);
            }
        }
        if (playing && npend && !asked_ms && !fs_save_busy() && (ms - last_ms >= QUIET_MS || ms - first_ms >= MAX_WAIT_MS)) {
            static uint64_t last_ask;
            if (ms - last_ask < MIN_GAP_MS) {
            } else if (temp_saves() || no_save_area[0]) {   // the game would not save: the tree makes the save itself
                if (built_node()) last_ask = ms;
            } else if (dbg_game_save_request(cur_slot)) {
                asked_ms = last_ask = ms;
            }
        }
        plat_sleep_ns((uint64_t)POLL_MS * 1000000ull);
    }
}

void save_tree_tick(void) {
    if (atomic_load(&started) || !tree_on()) return;
    char saves[PLAT_PATH_MAX];
    if (!save_share_saves_dir(saves, sizeof saves)) return;
    if (atomic_exchange(&started, 1)) return;
    snprintf(root_dir, sizeof root_dir, "%s/tree", saves);
    if (!plat_thread_spawn(tree_thread, NULL, "save-tree", 0)) {
        rt_log("save tree: the thread could not start");
        return;
    }
    rt_log("save tree: on, in %s", root_dir);
}
