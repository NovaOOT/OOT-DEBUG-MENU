// The N64 Graphics mod's files (n64gfx.h): found through the mod that turns the feature on (mods.c mods_feature), read
// whole on first use and kept for the run, each checked against its own section table before anything points into it.
#include "n64gfx.h"

#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cfg.h"
#include "host.h"
#include "mods.h"
#include "platform/platform.h"

#define N64G_VERSION 3
#define SECTIONS 12
#define MAX_SCENES 128
#define MAX_OBJECTS 512

static PlatMutex lock = PLAT_MUTEX_INIT;
static int g_state;   // 0 not looked yet, 1 on, -1 off
static char g_dir[2400];
static N64File *g_scene[MAX_SCENES], *g_object[MAX_OBJECTS];
static uint8_t g_tried_scene[MAX_SCENES], g_tried_object[MAX_OBJECTS];

static int look(void) {
    if (g_state) return g_state > 0;
    if (!mods_features_known()) return 0;   // asked before the mods folder was scanned: ask again later
    g_state = -1;
    char name[256];
    if (!mods_feature(MOD_FEATURE_N64, name, sizeof name)) return 0;
    snprintf(g_dir, sizeof g_dir, "%s/%s/n64", mods_dir(), name);
    char probe[2500];
    snprintf(probe, sizeof probe, "%s/manifest.json", g_dir);
    if (!plat_file_exists(probe)) {
        rt_log("n64: the mod \"%s\" is on but has no n64/manifest.json; make it with tools/make_n64_mod.py", name);
        return 0;
    }
    rt_log("n64: N64 Graphics on from the mod \"%s\"", name);
    g_state = 1;
    return 1;
}

int n64gfx_on(void) {
    plat_mutex_lock(&lock);
    int on = look();
    plat_mutex_unlock(&lock);
    return on;
}

static const char *const part_keys[N64_PARTS] = {"N64_LINK", "N64_ITEMS", "N64_ACTORS", "N64_WORLD", "N64_SWORD_WALLS"};
const char *n64gfx_part_key(int part) { return part >= 0 && part < N64_PARTS ? part_keys[part] : ""; }

// The Dark Link mod is on (mods.h MOD_FEATURE_DARK_LINK); 0 until the mods folder's scan has finished.
static int dark_mod(void) {
    static _Atomic int known;   // 0 not looked yet, 1 on, -1 off
    int k = atomic_load(&known);
    if (!k) {
        if (!mods_features_known()) return 0;
        k = mods_feature(MOD_FEATURE_DARK_LINK, NULL, 0) ? 1 : -1;
        atomic_store(&known, k);
    }
    return k > 0;
}

// The switches, parsed again when a setting changes (cfg_version). With the Dark Link mod on and its style "remake"
// (OOTPC_DARK_LINK_STYLE), N64 Link and his items are off: the player keeps the remake's models, which that mod makes
// dark.
int n64gfx_part(int part) {
    static _Atomic unsigned seen = ~0u;
    static _Atomic int bits, remake_style;
    if (part < 0 || part >= N64_PARTS || !n64gfx_on()) return 0;
    unsigned v = cfg_version();
    if (v != atomic_load(&seen)) {
        int b = 0;
        for (int k = 0; k < N64_PARTS; k++) {
            char name[40];
            snprintf(name, sizeof name, "OOTPC_%s", part_keys[k]);
            const char *s = cfg_get(name);
            if (!s || !*s || strcmp(s, "0")) b |= 1 << k;
        }
        const char *style = cfg_get("OOTPC_DARK_LINK_STYLE");
        atomic_store(&remake_style, style && !strcmp(style, "remake"));
        atomic_store(&bits, b);
        atomic_store(&seen, v);
    }
    int b = atomic_load(&bits);
    if ((part == N64_PART_LINK || part == N64_PART_ITEMS) && atomic_load(&remake_style) && dark_mod()) return 0;
    if (part == N64_PART_ITEMS && !(b & (1 << N64_PART_LINK))) return 0;   // the items hang from N64 Link
    return (b >> part) & 1;
}

int n64gfx_dark(void) { return n64gfx_part(N64_PART_LINK) && dark_mod(); }
int n64gfx_dark_remake(void) { return dark_mod() && !n64gfx_part(N64_PART_LINK); }

static N64File *parse(uint8_t *data, size_t size, const char *path) {
    static const size_t elem[SECTIONS] = {sizeof(N64Vert), 1, sizeof(N64Mat), 4, sizeof(N64Batch), sizeof(N64Node),
                                          sizeof(N64Light), 8, 8, 24, 2, 4};
    uint32_t off[SECTIONS], cnt[SECTIONS];
    if (size < 16 + 8 * SECTIONS || memcmp(data, "N64G", 4)) goto bad;
    uint32_t version, kind, number;
    memcpy(&version, data + 4, 4), memcpy(&kind, data + 8, 4), memcpy(&number, data + 12, 4);
    if (version != N64G_VERSION) goto bad;
    for (int k = 0; k < SECTIONS; k++) {
        memcpy(&off[k], data + 16 + 8 * k, 4), memcpy(&cnt[k], data + 20 + 8 * k, 4);
        if (off[k] > size || (uint64_t)cnt[k] * elem[k] > size - off[k] || (off[k] & 3)) goto bad;
    }
    N64File *f = calloc(1, sizeof *f);
    if (!f) goto bad;
    f->data = data, f->size = size, f->kind = kind, f->number = number;
    f->verts = (const N64Vert *)(data + off[0]), f->nverts = cnt[0];
    f->mats = (const N64Mat *)(data + off[2]), f->nmats = cnt[2];
    f->texels = (const uint32_t *)(data + off[3]), f->ntexels = cnt[3];
    f->batches = (const N64Batch *)(data + off[4]), f->nbatches = cnt[4];
    f->nodes = (const N64Node *)(data + off[5]), f->nnodes = cnt[5];
    f->lights = (const N64Light *)(data + off[6]), f->nlights = cnt[6];
    f->col_verts = (const int16_t(*)[4])(data + off[7]), f->ncol_verts = cnt[7];
    f->col_tris = (const uint16_t(*)[4])(data + off[8]), f->ncol_tris = cnt[8];
    f->bounds = (const float(*)[6])(data + off[9]), f->nbounds = cnt[9];
    f->rest = (const int16_t *)(data + off[10]), f->nrest = cnt[10];
    f->player = (const uint32_t *)(data + off[11]), f->nplayer = cnt[11];
    // every index the drawer follows stays inside its section
    for (uint32_t i = 0; i < f->nbatches; i++) {
        const N64Batch *b = &f->batches[i];
        if (b->mat >= f->nmats || b->first > f->nverts || b->count > f->nverts - b->first || b->node >= f->nnodes)
            goto bad_free;
    }
    for (uint32_t i = 0; i < f->nverts; i++)   // the shader reads each vertex's node's matrix
        if (f->verts[i].node >= f->nnodes) goto bad_free;
    for (uint32_t i = 3; i < f->nplayer; i++)
        if (f->player[i] != UINT32_MAX && f->player[i] >= f->nnodes) goto bad_free;
    for (uint32_t i = 0; i < f->nmats; i++)
        for (int t = 0; t < 2; t++) {
            const uint32_t *tl = f->mats[i].tile[t];
            uint64_t texels = (uint64_t)(tl[1] & 0xffff) * (tl[1] >> 16);
            if ((tl[3] >> 20 & 1) && (tl[0] > f->ntexels || texels > f->ntexels - tl[0])) goto bad_free;
        }
    return f;
bad_free:
    free(f);
bad:
    rt_log("n64: %s is not a file this version reads; make the mod again with tools/make_n64_mod.py", path);
    free(data);
    return NULL;
}

static N64File *load(const char *what, int number) {
    char path[2600];
    snprintf(path, sizeof path, "%s/%s_%03d.n64g", g_dir, what, number);
    if (!plat_file_exists(path)) return NULL;
    uint64_t size = 0;
    uint8_t *data = plat_file_read(path, &size);
    if (!data) return NULL;
    return parse(data, (size_t)size, path);
}

const N64File *n64gfx_scene(int number) {
    if (number < 0 || number >= MAX_SCENES) return NULL;
    plat_mutex_lock(&lock);
    N64File *f = NULL;
    if (look()) {
        if (!g_tried_scene[number]) g_tried_scene[number] = 1, g_scene[number] = load("scene", number);
        f = g_scene[number];
    }
    plat_mutex_unlock(&lock);
    return f;
}

const N64File *n64gfx_object(int number) {
    if (number < 0 || number >= MAX_OBJECTS) return NULL;
    plat_mutex_lock(&lock);
    N64File *f = NULL;
    if (look()) {
        if (!g_tried_object[number]) g_tried_object[number] = 1, g_object[number] = load("object", number);
        f = g_object[number];
    }
    plat_mutex_unlock(&lock);
    return f;
}

const uint8_t *n64gfx_actors(size_t *size) {
    static uint8_t *data;
    static uint64_t n;
    static int tried;
    plat_mutex_lock(&lock);
    if (look() && !tried) {
        tried = 1;
        char path[2600];
        snprintf(path, sizeof path, "%s/actors.bin", g_dir);
        if (plat_file_exists(path)) data = plat_file_read(path, &n);
    }
    plat_mutex_unlock(&lock);
    *size = data ? (size_t)n : 0;
    return data;
}

const uint8_t *n64gfx_world(size_t *size) {
    static uint8_t *data;
    static uint64_t n;
    static int tried;
    plat_mutex_lock(&lock);
    if (look() && !tried) {
        tried = 1;
        char path[2600];
        snprintf(path, sizeof path, "%s/world.bin", g_dir);
        if (plat_file_exists(path)) data = plat_file_read(path, &n);
    }
    plat_mutex_unlock(&lock);
    *size = data ? (size_t)n : 0;
    return data;
}
