// N64 Graphics' levels (n64world.h): the mod's world.bin, checked whole once it is read, and the area the game is in.
#include "n64world.h"

#include <stdio.h>
#include <string.h>

#include "cfg.h"
#include "debug_tools.h"
#include "host.h"
#include "n64gfx.h"

#define VERSION 1
#define AREA_BYTES 48
#define TEX_BYTES 16
#define ENTRY_BYTES 16
#define AREA_POLL_NS 250000000ull

static int g_tried;   // 1 read and checked, -1 none or refused
static const uint8_t *g_data;
static uint32_t g_nareas, g_ntex, g_nent, g_ntexels;
static const uint8_t *g_areas, *g_tex, *g_ent;
static const uint32_t *g_texels;

static int g_area = -1;          // index of the area now, -1 none
static char g_area_name[64];
static uint64_t g_area_at;       // kern_now_ns of the last poll
static uint32_t g_state, g_state_seen = ~0u;
static int g_active;   // n64world_state's last answer was an area replaced (read without the lock: n64world_foliage)

static uint32_t rd32(const uint8_t *p) { uint32_t v; memcpy(&v, p, 4); return v; }
static uint16_t rd16(const uint8_t *p) { uint16_t v; memcpy(&v, p, 2); return v; }
static uint64_t rd64(const uint8_t *p) { uint64_t v; memcpy(&v, p, 8); return v; }

static int load(void) {
    if (g_tried) return g_tried > 0;
    if (!n64gfx_on()) return 0;   // asked before the mods folder was known, or the mod is off: ask again later
    size_t n = 0;
    const uint8_t *d = n64gfx_world(&n);
    g_tried = -1;
    if (!d) {
        rt_log("n64: world: the mod has no n64/world.bin; make it with tools/n64world.py (the levels stay the remake's)");
        return 0;
    }
    if (n < 32 || memcmp(d, "N64W", 4) || rd32(d + 4) != VERSION) goto bad;
    g_nareas = rd32(d + 8), g_ntex = rd32(d + 12), g_nent = rd32(d + 16), g_ntexels = rd32(d + 20);
    uint64_t need = 32 + (uint64_t)g_nareas * AREA_BYTES + (uint64_t)g_ntex * TEX_BYTES +
                    (uint64_t)g_nent * ENTRY_BYTES + (uint64_t)g_ntexels * 4;
    if (need > n) goto bad;
    g_areas = d + 32;
    g_tex = g_areas + (size_t)g_nareas * AREA_BYTES;
    g_ent = g_tex + (size_t)g_ntex * TEX_BYTES;
    g_texels = (const uint32_t *)(g_ent + (size_t)g_nent * ENTRY_BYTES);   // 32 + multiples of 16: 4-byte aligned
    for (uint32_t i = 0; i < g_ntex; i++) {
        const uint8_t *t = g_tex + (size_t)i * TEX_BYTES;
        uint64_t first = rd32(t), w = rd16(t + 4), h = rd16(t + 6);
        if (!w || !h || w > 1024 || h > 1024 || first + w * h > g_ntexels) goto bad;
    }
    for (uint32_t i = 0; i < g_nareas; i++)
        for (int c = 0; c < N64W_CATS; c++) {
            uint16_t ti = rd16(g_areas + (size_t)i * AREA_BYTES + 32 + 2 * c);
            if (ti != 0xFFFF && ti >= g_ntex) goto bad;
        }
    for (uint32_t i = 0; i < g_nent; i++) {
        const uint8_t *e = g_ent + (size_t)i * ENTRY_BYTES;
        if (e[8] >= N64W_CATS || e[9] < N64W_COLOUR || e[9] > N64W_ARRAY) goto bad;
        if (i && rd64(e) <= rd64(e - ENTRY_BYTES)) goto bad;   // ascending: n64world_find searches
    }
    g_data = d;
    g_tried = 1;
    rt_log("n64: world: %u remake textures, %u areas, %u N64 textures (world.bin)", g_nent, g_nareas, g_ntex);
    return g_nent > 0;
bad:
    rt_log("n64: world: n64/world.bin is not a file this version reads; make it again with tools/n64world.py");
    return 0;
}

int n64world_loaded(void) { return load() && g_nent > 0; }

uint64_t n64world_key(const uint8_t *first, uint32_t width, uint32_t height) {
    uint64_t h = 0xcbf29ce484222325ull;
    uint8_t wh[8];
    memcpy(wh, &width, 4), memcpy(wh + 4, &height, 4);
    for (int i = 0; i < 8; i++) h = (h ^ wh[i]) * 0x100000001b3ull;
    for (int i = 0; i < N64W_KEY_BYTES; i++) h = (h ^ first[i]) * 0x100000001b3ull;
    return h;
}

int n64world_find(uint64_t key) {
    if (!g_data) return -1;
    uint32_t lo = 0, hi = g_nent;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        uint64_t k = rd64(g_ent + (size_t)mid * ENTRY_BYTES);
        if (k == key) return (int)mid;
        if (k < key) lo = mid + 1;
        else hi = mid;
    }
    return -1;
}

static const uint8_t *entry(int i) { return g_data && i >= 0 && (uint32_t)i < g_nent ? g_ent + (size_t)i * ENTRY_BYTES : NULL; }
int n64world_kind(int i) { const uint8_t *e = entry(i); return e ? e[9] : 0; }
int n64world_category(int i) { const uint8_t *e = entry(i); return e ? e[8] : -1; }
float n64world_metres(int i) { const uint8_t *e = entry(i); return e ? e[10] / 8.0f : 0; }

const char *n64world_area(void) { return g_area_name; }

uint32_t n64world_state(void) {
    if (!n64world_loaded()) return 0;
    int on = n64gfx_part(N64_PART_WORLD);
    uint64_t now = kern_now_ns();
    if (on && now - g_area_at >= AREA_POLL_NS) {
        g_area_at = now;
        char a[64];
        if (dbg_current_area(a) && a[0]) {
            const char *s = strrchr(a, '/');
            s = s ? s + 1 : a;
            if (strcmp(s, g_area_name)) {
                snprintf(g_area_name, sizeof g_area_name, "%s", s);
                g_area = -1;
                for (uint32_t i = 0; i < g_nareas; i++)
                    if (!strncmp((const char *)g_areas + (size_t)i * AREA_BYTES, s, 31) && strlen(s) < 32) g_area = (int)i;
                rt_log("n64: world: the area is %s%s", s, g_area < 0 ? ", which world.bin does not list (it keeps "
                       "the remake's textures)" : "");
            }
        }
    }
    uint32_t st = on && g_area >= 0 ? 1u + (uint32_t)g_area : 0;
    __atomic_store_n(&g_active, st != 0, __ATOMIC_RELAXED);
    if (st != g_state_seen) {
        g_state_seen = st;
        g_state++;
    }
    return st ? g_state : 0;
}

int n64world_texture(int category, N64WorldTex *out) {
    if (!g_data || g_area < 0 || category < 0 || category >= N64W_CATS || !n64gfx_part(N64_PART_WORLD)) return 0;
    uint16_t ti = rd16(g_areas + (size_t)g_area * AREA_BYTES + 32 + 2 * category);
    if (ti == 0xFFFF) return 0;
    const uint8_t *t = g_tex + (size_t)ti * TEX_BYTES;
    out->texels = g_texels + rd32(t);
    out->w = rd16(t + 4), out->h = rd16(t + 6);
    out->keep_alpha = rd16(t + 8) & 1;
    out->metres = t[10] / 8.0f;
    out->index = ti;
    return 1;
}

int n64world_colour_category(int r, int g, int b) {
    int mx = r > g ? (r > b ? r : b) : (g > b ? g : b), mn = r < g ? (r < b ? r : b) : (g < b ? g : b);
    if (mx < 16) return -1;
    float s = (float)(mx - mn) / (float)mx;
    if (s < 0.12f) return mx > 200 ? -1 : N64W_ROCK;   // grey: rock; light grey (snow, ice) stays the remake's
    float d = (float)(mx - mn), h;
    if (mx == r) h = 60.0f * ((float)(g - b) / d);
    else if (mx == g) h = 60.0f * (2.0f + (float)(b - r) / d);
    else h = 60.0f * (4.0f + (float)(r - g) / d);
    if (h < 0) h += 360.0f;
    // the remake's grass layers are olive (hue 46-67), so green starts at 45
    if (h >= 45.0f && h < 170.0f) return N64W_GRASS;
    if (h < 45.0f || h >= 330.0f) return s < 0.30f ? N64W_PATH : N64W_DIRT;   // grey-brown gravel, else earth
    if (h < 260.0f && s < 0.30f) return N64W_ROCK;   // blue-grey stone
    return -1;
}

int n64world_foliage(void) {
    if (!__atomic_load_n(&g_active, __ATOMIC_RELAXED)) return -1;
    static unsigned seen = ~0u;
    static int mode = 1;
    unsigned v = cfg_version();
    if (v != seen) {
        seen = v;
        const char *f = cfg_get("OOTPC_N64_WORLD_FOLIAGE");
        mode = f && !strcmp(f, "full") ? 0 : f && !strcmp(f, "off") ? 2 : 1;
    }
    return mode;
}
