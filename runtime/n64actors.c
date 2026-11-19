// N64 Graphics' enemies and other characters (n64actors.h).
//
// The model's skeleton in memory as n64pose.c reads the player's: the model points at it from +0xf8, the skeleton at
// its resource from +0x00 and at its bones' world matrices from +0x20 (0x40 bytes each, four columns of four floats);
// the resource holds the bone records at +0x10 (0x58 bytes each, the name first: a u16 length, then the text) and
// their number, a u16, at +0x38. A model the game hides keeps bit 0x80 of its byte at +0x12 set. The model's name
// ("Enemy_Tektite_Blue", the name of its file) is found from +0xd8: a pointer to a record whose +0x08 points at it.
#include "n64actors.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "host.h"
#include "mods.h"
#include "motion_owner.h"
#include "n64gfx.h"
#include "platform/platform.h"
#include "vk_n64.h"

#define SLOTS 128   // characters posed at once (a room of statues holds about a hundred)
#define STALE_NS 150000000ull   // a pose older than this is not drawn: the character is gone or no longer posed
#define KNOWN 512               // skeleton resources remembered with their entry (or none)
#define MAX_BONES 1024
#define NO_BONE 0xFFFF

typedef struct {
    uint32_t hash, name;   // the bones' hash, the model's name's (0: any)
    uint16_t bones, object, first_node, limbs;
    const uint8_t *limb;   // limb records of rec bytes: u16 bone, u16 the limb it hangs on, 12 f32 (, 3 f32 joint)
    uint32_t rec;          // 52 (version 2), 64 (version 3: with the limb it hangs on and the joint there)
} Entry;

static Entry *entries;
static int nentries, loaded;
static PlatMutex lock = PLAT_MUTEX_INIT;

static void load(void) {
    if (loaded) return;
    size_t n = 0;
    const uint8_t *d = n64gfx_actors(&n);
    if (!d && !mods_features_known()) return;   // the mods folder is not scanned yet: ask again later
    loaded = 1;
    uint32_t version, count;
    if (!d || n < 12 || memcmp(d, "N64A", 4)) return;
    memcpy(&version, d + 4, 4), memcpy(&count, d + 8, 4);
    if ((version != 2 && version != 3) || count > 4096 || !(entries = calloc(count ? count : 1, sizeof *entries))) return;
    size_t at = 12;
    for (uint32_t i = 0; i < count; i++) {
        Entry e;
        if (at + 16 > n) break;
        memcpy(&e.hash, d + at, 4), memcpy(&e.name, d + at + 4, 4), memcpy(&e.bones, d + at + 8, 2);
        memcpy(&e.object, d + at + 10, 2), memcpy(&e.first_node, d + at + 12, 2), memcpy(&e.limbs, d + at + 14, 2);
        e.limb = d + at + 16;
        e.rec = version == 3 ? 64 : 52;
        at += 16 + (size_t)e.limbs * e.rec;
        if (at > n) break;
        entries[nentries++] = e;
    }
    rt_log("n64: %d characters have an N64 model (actors.bin)", nentries);
}

int n64actors_wanted(void) {
    if (!n64gfx_part(N64_PART_ACTORS)) return 0;
    plat_mutex_lock(&lock);
    load();
    int n = nentries;
    plat_mutex_unlock(&lock);
    return n > 0;
}

static int rd(uint64_t a, void *out, size_t n) { return a && plat_mem_read(a, out, n); }
static uint64_t ptr(uint64_t a) {
    uint64_t p = 0;
    return rd(a, &p, 8) ? p : 0;
}

// FNV-1a over each bone name and a zero byte (tools/n64actors.py bone_hash); 0 when the records do not read.
static uint32_t bone_hash(uint64_t resource, uint16_t *count) {
    uint16_t n = 0;
    uint64_t bones = ptr(resource + 0x10);
    if (!bones || !rd(resource + 0x38, &n, 2) || !n || n > MAX_BONES) return 0;
    uint32_t h = 0x811C9DC5u;
    for (int i = 0; i < n; i++) {
        uint64_t name = ptr(bones + (uint64_t)i * 0x58);
        uint16_t len = 0;
        char text[128];
        if (!name || !rd(name, &len, 2) || !len || len >= sizeof text || !rd(name + 2, text, len)) return 0;
        for (int k = 0; k <= len; k++) h = (h ^ (uint8_t)(k < len ? text[k] : 0)) * 0x01000193u;
    }
    *count = n;
    return h;
}

// FNV-1a over a model's name and a zero byte, as the bones' (tools/n64actors.py bone_hash([model])); 0 when it does
// not read. The name is a u16 length and the text, or the text alone.
static uint32_t name_hash(uint64_t model) {
    char text[96] = {0};
    uint64_t at = ptr(ptr(model + 0xd8) + 0x8);
    if (!at || !rd(at, text, sizeof text - 1)) return 0;
    const char *s = text;
    if ((unsigned char)text[0] < 0x20 && text[1] == 0) s = text + 2;   // a u16 length first
    size_t len = strnlen(s, sizeof text - 1 - (size_t)(s - text));
    if (!len) return 0;
    uint32_t h = 0x811C9DC5u;
    for (size_t k = 0; k <= len; k++) h = (h ^ (uint8_t)(k < len ? s[k] : 0)) * 0x01000193u;
    return h;
}

// The entry of a model with this skeleton resource and name, -1 none: of the entries with the skeleton's hash, the one
// with the model's name, else the first. Remembered per resource and name (under lock).
static struct { uint64_t resource; uint32_t name; int entry; } known[KNOWN];
static int nknown, next_known;
static int entry_of(uint64_t resource, uint32_t name) {
    for (int i = 0; i < nknown; i++)
        if (known[i].resource == resource && known[i].name == name) return known[i].entry;
    uint16_t n = 0;
    uint32_t h = bone_hash(resource, &n);
    int e = -1;
    for (int i = 0; h && i < nentries; i++)
        if (entries[i].hash == h && entries[i].bones == n && (e < 0 || (name && entries[i].name == name))) {
            e = i;
            if (name && entries[i].name == name) break;
        }
    if (e >= 0) {
        const N64File *f = n64gfx_object(entries[e].object);
        if (!f || entries[e].first_node + entries[e].limbs > f->nnodes) {
            rt_log("n64: character entry %d (object %u) does not fit its N64 file (%s; nodes %u+%u of %u)", e,
                   entries[e].object, f ? "loaded" : "missing", entries[e].first_node, entries[e].limbs,
                   f ? f->nnodes : 0);
            e = -1;
        }
    }
    int at = nknown < KNOWN ? nknown++ : next_known++ % KNOWN;
    known[at].resource = resource, known[at].name = name, known[at].entry = e;
    return e;
}

// A character's latest pose: each node's rows for its N64 file, which nodes draw.
typedef struct {
    uint64_t model, at_ns;
    uint64_t resource;   // the skeleton resource it was posed from, and its root bone's world matrix then
    float root_m[16];
    const N64File *file;
    float (*rows)[12];
    uint8_t *on;
    uint32_t cap;   // nodes the arrays hold
} Slot;
static Slot slots[SLOTS];

static Slot *slot_for(uint64_t model, uint64_t now) {
    Slot *free_slot = NULL, *oldest = &slots[0];
    for (int i = 0; i < SLOTS; i++) {
        if (slots[i].model == model) return &slots[i];
        if (!free_slot && (!slots[i].model || now - slots[i].at_ns > STALE_NS)) free_slot = &slots[i];
        if (slots[i].at_ns < oldest->at_ns) oldest = &slots[i];
    }
    return free_slot ? free_slot : oldest;
}

// ---- the gap check (OOTPC_N64_GAPS=1, for making tools/n64actors.py's TABLE): a rigid N64 model's limb joint keeps
// one place in its parent limb's space, so how far it wanders there while the remake's bones move it shows a part that
// comes apart from the one it hangs on. Each limb's range there (N64 units) is kept per entry; every ten seconds the
// widest are logged with the object, the limb and the entry's model name hash. Under lock.
typedef struct { float lo[3], hi[3]; int seen; } Gap;
static Gap **gaps;   // per entry, per limb

static void gap_note(int e, const Entry *en, const N64File *f, const Slot *s) {
    static int on = -1;
    if (on < 0) {
        char v[8] = {0};
        on = plat_getenv("OOTPC_N64_GAPS", v, sizeof v) && v[0] == '1';
    }
    if (!on || (!gaps && !(gaps = calloc((size_t)nentries, sizeof *gaps)))) return;
    if (!gaps[e] && !(gaps[e] = calloc(en->limbs, sizeof **gaps))) return;
    for (int l = 0; l < en->limbs; l++) {
        int node = en->first_node + l, p = f->nodes[node].parent;
        if (!s->on[node]) continue;
        while (p >= 0 && !(p >= en->first_node && p < en->first_node + en->limbs && s->on[p])) p = f->nodes[p].parent;
        if (p < 0) continue;
        const float *a = s->rows[p], *c = s->rows[node];
        float d[3] = {c[3] - a[3], c[7] - a[7], c[11] - a[11]};
        // the parent limb's 3x3 inverted (rows a[0..2], a[4..6], a[8..10])
        float m00 = a[0], m01 = a[1], m02 = a[2], m10 = a[4], m11 = a[5], m12 = a[6], m20 = a[8], m21 = a[9], m22 = a[10];
        float det = m00 * (m11 * m22 - m12 * m21) - m01 * (m10 * m22 - m12 * m20) + m02 * (m10 * m21 - m11 * m20);
        if (fabsf(det) < 1e-30f) continue;
        float loc[3] = {((m11 * m22 - m12 * m21) * d[0] + (m02 * m21 - m01 * m22) * d[1] + (m01 * m12 - m02 * m11) * d[2]) / det,
                        ((m12 * m20 - m10 * m22) * d[0] + (m00 * m22 - m02 * m20) * d[1] + (m02 * m10 - m00 * m12) * d[2]) / det,
                        ((m10 * m21 - m11 * m20) * d[0] + (m01 * m20 - m00 * m21) * d[1] + (m00 * m11 - m01 * m10) * d[2]) / det};
        Gap *g = &gaps[e][l];
        for (int k = 0; k < 3; k++) {
            if (!g->seen || loc[k] < g->lo[k]) g->lo[k] = loc[k];
            if (!g->seen || loc[k] > g->hi[k]) g->hi[k] = loc[k];
        }
        g->seen++;
    }
    static uint64_t last;
    uint64_t now = plat_now_ns();
    if (now - last < 10000000000ull) return;
    last = now;
    for (int i = 0; i < nentries; i++) {
        if (!gaps[i]) continue;
        for (int l = 0; l < entries[i].limbs; l++) {
            const Gap *g = &gaps[i][l];
            float w = 0;
            for (int k = 0; k < 3; k++) w = fmaxf(w, g->hi[k] - g->lo[k]);
            if (g->seen && w > 100.0f)
                rt_log("n64 gap: object %u limb %d (entry %08x) wanders %.0f units in its parent's space (%d frames)",
                       entries[i].object, l, entries[i].name, w, g->seen);
        }
    }
}

int n64actors_world_ready(uint64_t model, const float shift[3]) {
    uint8_t hidden = 0x80;
    if (!rd(model + 0x12, &hidden, 1) || (hidden & 0x80)) return -1;
    uint64_t skel = ptr(model + 0xf8), resource = skel ? ptr(skel) : 0, mats = skel ? ptr(skel + 0x20) : 0;
    if (!resource || !mats) return -1;
    plat_mutex_lock(&lock);
    int e = entry_of(resource, name_hash(model));
    if (e < 0) {
        plat_mutex_unlock(&lock);
        return -1;
    }
    // the root bone's world matrix is still zero when a model's first pose is being made (a statue's only one): not
    // ready yet, asked again later (n64pose.c)
    float root_col[3] = {0};
    if (!rd(mats, root_col, sizeof root_col) ||
        root_col[0] * root_col[0] + root_col[1] * root_col[1] + root_col[2] * root_col[2] < 1e-8f) {
        plat_mutex_unlock(&lock);
        return 0;
    }
    const Entry *en = &entries[e];
    const N64File *f = n64gfx_object(en->object);
    uint64_t now = plat_now_ns();
    Slot *s = slot_for(model, now);
    if (s->cap < f->nnodes) {
        free(s->rows), free(s->on);
        s->rows = calloc(f->nnodes, sizeof *s->rows), s->on = calloc(f->nnodes, 1);
        s->cap = s->rows && s->on ? f->nnodes : 0;
        if (!s->cap) {
            free(s->rows), free(s->on), s->rows = NULL, s->on = NULL, s->model = 0;
            plat_mutex_unlock(&lock);
            return -1;
        }
    }
    memset(s->on, 0, f->nnodes);
    int ok = 1;
    float root[3] = {0}, root_scale = 1.0f;
    if (rd(mats, root, sizeof root)) {
        float len = sqrtf(root[0] * root[0] + root[1] * root[1] + root[2] * root[2]);
        if (len > 1e-3f && len < 1e3f) root_scale = len;
    }
    for (int l = 0; l < en->limbs && ok; l++) {
        const uint8_t *r = en->limb + (size_t)l * en->rec;
        uint16_t bone;
        float m[12], b[16];
        memcpy(&bone, r, 2), memcpy(m, r + 4, sizeof m);
        if (bone == NO_BONE) continue;
        if (!rd(mats + (uint64_t)bone * 0x40, b, sizeof b)) {
            ok = 0;
            break;
        }
        // a rigid N64 limb takes the bone's turn and place, and the whole model's scale (the root bone's), not the
        // bone's own (an animation may stretch or shrink a remake bone, and an N64 limb stretched with it comes apart
        // from the others)
        for (int c = 0; c < 3; c++) {
            float len = sqrtf(b[4 * c] * b[4 * c] + b[4 * c + 1] * b[4 * c + 1] + b[4 * c + 2] * b[4 * c + 2]);
            if (len > 1e-6f)
                for (int r = 0; r < 3; r++) b[4 * c + r] *= root_scale / len;
        }
        // the bone's matrix (columns) times the limb's (rows of 3x4), then into the world's space
        float *out = s->rows[en->first_node + l];
        for (int row = 0; row < 3; row++)
            for (int c = 0; c < 4; c++) {
                float v = b[row] * m[c] + b[4 + row] * m[4 + c] + b[8 + row] * m[8 + c];
                if (c == 3) v += b[12 + row] + shift[row];
                out[4 * row + c] = v;
                ok &= isfinite(v);
            }
        s->on[en->first_node + l] = 1;
    }
    // each limb's joint where the limb it hangs on holds it in the bind pose (parents come first in the N64 tree): the
    // turns stay each bone's, but a remake bone that moves apart from its parent (a stretch, a bend at a joint the N64
    // limb does not have) no longer carries the piece off the one it hangs on
    for (int l = 0; l < en->limbs && ok && en->rec >= 64; l++) {
        const uint8_t *r = en->limb + (size_t)l * en->rec;
        uint16_t up;
        float j[3];
        memcpy(&up, r + 2, 2), memcpy(j, r + 52, sizeof j);
        if (up == NO_BONE || up >= l || !s->on[en->first_node + l] || !s->on[en->first_node + up]) continue;
        const float *a = s->rows[en->first_node + up];
        float *out = s->rows[en->first_node + l];
        for (int row = 0; row < 3; row++)
            out[4 * row + 3] = a[4 * row] * j[0] + a[4 * row + 1] * j[1] + a[4 * row + 2] * j[2] + a[4 * row + 3];
    }
    if (ok) gap_note(e, en, f, s);
    if (ok) {
        s->model = model, s->file = f, s->at_ns = now, s->resource = resource;
        if (!rd(mats, s->root_m, sizeof s->root_m)) memset(s->root_m, 0, sizeof s->root_m);
    }
    else if (s->model == model) s->model = 0;
    plat_mutex_unlock(&lock);
    return ok ? 1 : -1;
}

// A model that is not posed this frame but still stands as it was: the game remakes a model's world pose only on
// frames its pose changed (a statue's once), so a slot outlives STALE_NS while the model is shown, has the same
// skeleton resource and its root bone's world matrix has not moved. Under lock.
static int still_standing(const Slot *s) {
    uint8_t hidden = 0x80;
    float m[16];
    if (!rd(s->model + 0x12, &hidden, 1) || (hidden & 0x80)) return 0;
    uint64_t skel = ptr(s->model + 0xf8), mats = skel ? ptr(skel + 0x20) : 0;
    if (!skel || ptr(skel) != s->resource || !rd(mats, m, sizeof m)) return 0;
    return !memcmp(m, s->root_m, sizeof m);
}

void n64actors_game_frame(void) {
    uint64_t models[SLOTS];
    int n = 0;
    if (n64actors_wanted()) {
        uint64_t now = plat_now_ns();
        plat_mutex_lock(&lock);
        for (int i = 0; i < SLOTS; i++)
            if (slots[i].model && now - slots[i].at_ns >= STALE_NS / 2 && now - slots[i].at_ns < STALE_NS * 4) {
                if (still_standing(&slots[i])) slots[i].at_ns = now;
            }
        for (int i = 0; i < SLOTS; i++)
            if (slots[i].model && now - slots[i].at_ns < STALE_NS) models[n++] = slots[i].model;
        plat_mutex_unlock(&lock);
    }
    static int told = -1;
    if (n != told && (n == 0 || told < 0 || n > told + 4 || n < told - 4)) {
        rt_log("n64: %d characters drawn as N64 now", n);
        told = n;
    }
    motion_owner_set_standins(models, n);
}

int n64actors_items(N64Item *items, int max) {
    // the render thread's copies, kept until the next frame's draw has used them
    static float (*rows[SLOTS])[12];
    static uint8_t *on[SLOTS];
    static uint32_t cap[SLOTS];
    if (!n64actors_wanted() || max <= 0) return 0;
    int n = 0;
    uint64_t now = plat_now_ns();
    plat_mutex_lock(&lock);
    for (int i = 0; i < SLOTS && n < max; i++) {
        const Slot *s = &slots[i];
        if (!s->model || now - s->at_ns > STALE_NS || !s->file) continue;
        uint32_t nodes = s->file->nnodes;
        if (cap[n] < nodes) {
            free(rows[n]), free(on[n]);
            rows[n] = malloc(nodes * sizeof *rows[n]), on[n] = malloc(nodes);
            cap[n] = rows[n] && on[n] ? nodes : 0;
            if (!cap[n]) continue;
        }
        memcpy(rows[n], s->rows, nodes * sizeof *rows[n]);
        memcpy(on[n], s->on, nodes);
        items[n] = (N64Item){.file = s->file, .rows = (const float(*)[12])rows[n], .node_on = on[n], .tunic = 0};
        n++;
    }
    plat_mutex_unlock(&lock);
    return n;
}

int n64actors_stands_in(uint64_t model) {
    if (!model || !vk_n64_ready()) return 0;
    uint64_t now = plat_now_ns();
    int yes = 0;
    plat_mutex_lock(&lock);
    for (int i = 0; i < SLOTS && !yes; i++) yes = slots[i].model == model && now - slots[i].at_ns < STALE_NS;
    plat_mutex_unlock(&lock);
    return yes;
}
