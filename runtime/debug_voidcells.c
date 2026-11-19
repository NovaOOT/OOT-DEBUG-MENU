// Void-out cells for the debug menu's volume overlay (Debug tab, View, "Show void-out zones"): the places where the
// player's own fall check sends them back although no volume is there, such as the strip of Market_Adult at x 133 to 143,
// z -16 to -14 where the ground is solid underfoot.
//
// The check, traced in game (debug_game.c debug_abyss_type gives the cause "fall check, type 1"). Every frame the
// player's ground update (main+0x5f90a0) looks up the area's volume statistics for the player (main+0x543fe0, the
// player position about 0.84 m up, cached by whole metres) through the stats object *(*(actor+0x270)+0x460):
//   - when the point's 1 m cell has a record (its +0x37c bit 0 set), it reads the 4-bit field at record bit 245 times
//     2/3, when the 1-bit field at bit 244 is set; over 3 (a value of 5 or more) sets bit 19 of the state word at +0x13d0
//     of the player component;
//   - when the cell has none, the lookup takes the nearest cell that has one, and +0x115c holds how far that is; 8 m or
//     more sets bit 25 of the same word (seen at Market_Adult 141.6 102.8 -5.4: the nearest record 8.2 m away, the
//     state word 0x03005005). Nearer, that cell's own value decides. So a void cell voids the open space round it up
//     to where another record is nearer (flying 30 m up over the Market strip kept the strip's cell), and the space
//     8 m or more from every record is void too.
//   - and whatever the records say, bit 19 is set when bit 18 of the lookup's flags word (+0xa8) is, which is when the
//     point is outside the area's VolumeStatsAABB grown by 5 m across and below (not above): mapped in game round
//     Market_Adult, whose box is x -31..158, y 97..154, z -61..85, the bit came on past x 163 and -36, z 90 and -66 and
//     below y 92, at any distance from the records, and stayed off up to y 300.
// The abyss check (main+0x82c510) turns either bit (its test of +0x13d2 & 0x208) into void-out type 1. The fields are
// read through the record type's field table (main+0x3ee8840, 0x74 bytes a type: u32 total bits, a presence byte per
// field from +0x06, u32 bit offsets from +0x24); of the record types only type 6 has fields 18 and 19, at bits 244 and
// 245. A record shorter than 249 bits does not hold them, and the game then reads no value.
//
// The files. Phive/VolumeStats/<folder>/<area>[_X<i>_Z<j>].bpvsts.zs (zstd with the zs.zsdic dictionary): "Phive\0" 01 00,
// i32[3] at +0x14 the world position of the tile's low corner, u32[3] at +0x20 its count of root cells on x, y and z
// (placed 250 m apart, each 256 m wide from 3 m below its square, so a depth-8 cell is 1 m on whole metres), from +0x34
// three record widths in bits (u8) and from +0x3c their record type ids (u8), for the three deepest levels. Then from
// +0x40 each root cell, z fastest, then y, then x: u32 the size of its node levels in bytes, then 8 levels (depth 0, the
// root, to 7), each a u32 count and that many u32 nodes {first child's index << 8 | mask of the 8 children present},
// child bit (x | y << 1 | z << 2) for the upper half on each axis, the children of a level in order of their parents.
// Then 8 record streams, for depths 1 to 8: a u32 byte size and the bit-packed records, one per node of that depth in
// index order (none for depths 1 to 5 in the game's files, the three record types for depths 6 to 8). A node's record
// is at bit index * width of its stream. Checked against the game's own walks: the node indices it kept per depth at
// eight points of the Market and the bit offsets of its three records.
//
// Files named <area>_presence_* are variants the game chooses by the area's state (a door open, the water level); only
// the base tiles are read. The overlay's scan thread loads the area's tiles (dbg_void_cells_area) and, whenever the
// player has moved REGION_STEP metres, works out the void cells of the box REGION_HALF round them, inside the area's
// VolumeStatsAABB's top (above it the nearest-record rule would make all the sky void), merged into boxes; dbg_void_cells hands them out.
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "debug_tools.h"
#include "fs.h"
#include "host.h"
#include "platform/platform.h"

#define ROOT_STEP 250       // root cells are placed 250 m apart
#define ROOT_SIZE 256       // and each spans 256 m, 3 m past its square on every side
#define ROOT_MARGIN 3
#define DEPTHS 8
#define REC_TYPE 6          // the record type with the fall fields
#define FLAG_BIT 244        // field 18: the value below counts
#define VALUE_BIT 245       // field 19: 4 bits, times 2/3; over 3 sets the fall bit
#define VALUE_MIN 5         // the smallest value whose 2/3 is over 3
#define FAR2 64             // squared distance in metres to the nearest record from which the lookup sets bit 25
#define REGION_HALF 40      // the box worked out round the player, metres each way
#define REGION_STEP 8       // moved this far from its middle, the box is worked out again
#define PAD 9               // records this far outside the box across still count as the nearest
#define PAD_Y 40            // and this far below or above it
#define OUT_MARGIN 5        // past the VolumeStatsAABB by this much across or below, the lookup's bit 18 voids every point
#define BOX_MAX 65536

typedef struct { const uint8_t *p; size_t n; } Buf;
static uint32_t u32(const Buf *b, size_t o) { uint32_t v = 0; if (o <= b->n && b->n - o >= 4) memcpy(&v, b->p + o, 4); return v; }
static unsigned bits_at(const uint8_t *p, size_t n, uint64_t at, int width) {
    unsigned v = 0;
    for (int i = 0; i < width; i++) {
        uint64_t k = at + (uint64_t)i;
        if ((k >> 3) < n && ((p[k >> 3] >> (k & 7)) & 1)) v |= 1u << i;
    }
    return v;
}

typedef struct {
    const Buf *b;
    int sq[3];               // its 250 m square, in squares
    int lo[3];               // world position of its cell 0
    uint32_t count[DEPTHS];  // nodes per depth 0..7
    size_t at[DEPTHS];       // where each depth's nodes start
    size_t rec, rec_n;       // the depth-8 record stream
    int width;               // its record width; 0 when its records do not hold the fall fields
} Root;

static struct {
    PlatMutex lock;          // box, n
    float (*box)[6];         // lo xyz, hi xyz
    int n, cap;
    // owned by the scan thread
    char area[64];
    Buf *tiles;
    int ntiles;
    Root *roots;
    int nroots;
    int *hash, hsize;        // root index + 1 by square, open addressing
    int have_aabb;
    float aabb_lo[3], aabb_hi[3];
    int mid[3], have_mid;
} C;

static uint32_t sq_hash(const int s[3]) { return ((uint32_t)s[0] * 73856093u) ^ ((uint32_t)s[1] * 19349663u) ^ ((uint32_t)s[2] * 83492791u); }

static void hash_add(int i) {
    uint32_t h = sq_hash(C.roots[i].sq) & (uint32_t)(C.hsize - 1);
    while (C.hash[h]) h = (h + 1) & (uint32_t)(C.hsize - 1);
    C.hash[h] = i + 1;
}

static const Root *root_of(const int s[3]) {
    if (!C.hsize) return NULL;
    uint32_t h = sq_hash(s) & (uint32_t)(C.hsize - 1);
    for (; C.hash[h]; h = (h + 1) & (uint32_t)(C.hsize - 1)) {
        const Root *r = &C.roots[C.hash[h] - 1];
        if (r->sq[0] == s[0] && r->sq[1] == s[1] && r->sq[2] == s[2]) return r;
    }
    return NULL;
}

static int floor_div(int a, int b) { return a >= 0 ? a / b : -((-a + b - 1) / b); }

// The record of the 1 m cell at world metres w: -1 none, 1 void, 0 not void.
static int cell_at(const int w[3]) {
    int s[3] = {floor_div(w[0], ROOT_STEP), floor_div(w[1], ROOT_STEP), floor_div(w[2], ROOT_STEP)};
    const Root *r = root_of(s);
    if (!r || !r->count[0]) return -1;
    int c[3] = {w[0] - r->lo[0], w[1] - r->lo[1], w[2] - r->lo[2]};
    uint32_t cur = 0;
    for (int d = 0; d < DEPTHS; d++) {
        if (cur >= r->count[d]) return -1;
        uint32_t node = u32(r->b, r->at[d] + 4ull * cur), m = node & 0xff;
        int sh = DEPTHS - 1 - d, o = (c[0] >> sh & 1) | (c[1] >> sh & 1) << 1 | (c[2] >> sh & 1) << 2;
        if (!(m >> o & 1)) return -1;
        cur = (node >> 8) + (uint32_t)__builtin_popcount(m & ((1u << o) - 1));
    }
    if (!r->width) return 0;
    uint64_t at = (uint64_t)cur * (uint64_t)r->width;
    const uint8_t *p = r->b->p + r->rec;
    return bits_at(p, r->rec_n, at + FLAG_BIT, 1) && bits_at(p, r->rec_n, at + VALUE_BIT, 4) >= VALUE_MIN;
}

// One root cell at r: its node levels and depth-8 record stream. Returns the offset past it, 0 when it does not parse.
static size_t root_cell(const Buf *b, size_t r, const int lo[3], const int sq[3], const uint8_t width[3], const uint8_t type[3]) {
    Root rt = {b, {sq[0], sq[1], sq[2]}, {lo[0], lo[1], lo[2]}, {0}, {0}, 0, 0, 0};
    size_t o = r + 4;
    for (int d = 0; d < DEPTHS; d++) {
        rt.count[d] = u32(b, o), rt.at[d] = o + 4;
        if (rt.count[d] > (b->n - rt.at[d]) / 4) return 0;
        o = rt.at[d] + 4ull * rt.count[d];
    }
    for (int d = 0; d < DEPTHS; d++) {
        size_t sz = u32(b, o), st = o + 4;
        if (sz > b->n - st) return 0;
        if (d == DEPTHS - 1) rt.rec = st, rt.rec_n = sz;
        o = st + sz;
    }
    rt.width = type[2] == REC_TYPE && width[2] >= VALUE_BIT + 4 ? width[2] : 0;
    if (rt.count[0]) {
        Root *nr = realloc(C.roots, (size_t)(C.nroots + 1) * sizeof *C.roots);
        if (!nr) return 0;
        C.roots = nr, C.roots[C.nroots++] = rt;
    }
    return o;
}

static int tile(const Buf *b) {
    if (b->n < 0x40 || memcmp(b->p, "Phive\0\1\0", 8)) return 0;
    int32_t org[3];
    memcpy(org, b->p + 0x14, 12);
    uint32_t dx = u32(b, 0x20), dy = u32(b, 0x24), dz = u32(b, 0x28);
    if (!dx || !dy || !dz || dx > 64 || dy > 64 || dz > 64) return 0;
    uint8_t width[3], type[3];
    memcpy(width, b->p + 0x34, 3), memcpy(type, b->p + 0x3c, 3);
    size_t o = 0x40;
    for (uint32_t x = 0; x < dx; x++)
        for (uint32_t y = 0; y < dy; y++)
            for (uint32_t z = 0; z < dz; z++) {
                int sq[3] = {floor_div(org[0], ROOT_STEP) + (int)x, floor_div(org[1], ROOT_STEP) + (int)y, floor_div(org[2], ROOT_STEP) + (int)z};
                int lo[3] = {sq[0] * ROOT_STEP - ROOT_MARGIN, sq[1] * ROOT_STEP - ROOT_MARGIN, sq[2] * ROOT_STEP - ROOT_MARGIN};
                o = root_cell(b, o, lo, sq, width, type);
                if (!o) return 0;
            }
    return 1;
}

static const char *base_name(const char *p) {
    const char *s = strrchr(p, '/');
    return s ? s + 1 : p;
}

static void publish(float (*box)[6], int n, int cap) {
    plat_mutex_lock(&C.lock);
    free(C.box);
    C.box = box, C.n = n, C.cap = cap;
    plat_mutex_unlock(&C.lock);
}

void dbg_void_cells_area(const char *area) {
    if (!strcmp(C.area, area)) return;
    publish(NULL, 0, 0);
    for (int i = 0; i < C.ntiles; i++) free((void *)C.tiles[i].p);
    free(C.tiles), free(C.roots), free(C.hash);
    C.tiles = NULL, C.roots = NULL, C.hash = NULL, C.ntiles = C.nroots = C.hsize = 0, C.have_mid = 0;
    snprintf(C.area, sizeof C.area, "%s", area);
    size_t la = strlen(area);
    uint64_t t0 = plat_now_ns();
    for (uint64_t i = 0, fc = romfs_file_count(); i < fc; i++) {
        uint64_t off, sz;
        const char *p = romfs_entry(i, &off, &sz);
        if (!p || strncmp(p, "Phive/VolumeStats/", 18)) continue;
        const char *f = base_name(p), *rest = f + la;
        if (strncmp(f, area, la) || (strncmp(rest, "_X", 2) && strcmp(rest, ".bpvsts.zs"))) continue;
        size_t len = strlen(rest);
        if (len < 10 || strcmp(rest + len - 10, ".bpvsts.zs")) continue;
        size_t bn = 0;
        uint8_t *raw = dbg_romfs_unzs(p, "zs.zsdic", &bn);
        if (!raw) continue;
        Buf *nt = realloc(C.tiles, (size_t)(C.ntiles + 1) * sizeof *C.tiles);
        if (!nt) { free(raw); break; }
        C.tiles = nt, C.tiles[C.ntiles] = (Buf){raw, bn};
        int before = C.nroots;
        if (tile(&C.tiles[C.ntiles])) C.ntiles++;
        else C.nroots = before, free(raw);
    }
    // the tiles' Buf pointers moved while the list grew: point the roots at their tile again
    for (int t = 0, r = 0; t < C.ntiles; t++)
        for (; r < C.nroots && C.roots[r].b->p == C.tiles[t].p; r++) C.roots[r].b = &C.tiles[t];
    C.hsize = 16;
    while (C.hsize < C.nroots * 2) C.hsize *= 2;
    C.hash = calloc((size_t)C.hsize, sizeof *C.hash);
    if (!C.hash) C.hsize = 0;
    for (int i = 0; C.hash && i < C.nroots; i++) hash_add(i);
    C.have_aabb = dbg_volume_stats_box(area, C.aabb_lo, C.aabb_hi);
    rt_log("debug view: %s: volume statistics, %d tiles with %d root cells (%.0f ms)%s", area, C.ntiles, C.nroots,
           (double)(plat_now_ns() - t0) / 1e6, C.have_aabb ? "" : ", no VolumeStatsAABB");
}

// The nearest record of every cell of one line, in place (f: the squared distance to it so far, a large value
// where none; site: its cell index, -1 none): the lower envelope of the parabolas (q - p)^2 + f[p].
static void near1(float *f, int *site, int n, size_t stride, float *df, int *ds, int *v, float *z) {
    int k = 0;
    v[0] = 0, z[0] = -1e30f, z[1] = 1e30f;
    for (int q = 1; q < n; q++) {
        float fq = f[q * stride] + (float)q * q, s;
        for (;;) {
            int p = v[k];
            s = (fq - (f[p * stride] + (float)p * p)) / (2.0f * (float)(q - p));
            if (s <= z[k] && k > 0) k--;
            else break;
        }
        if (s <= z[k]) { v[0] = q, z[0] = -1e30f, z[1] = 1e30f, k = 0; continue; }
        k++, v[k] = q, z[k] = s, z[k + 1] = 1e30f;
    }
    k = 0;
    for (int q = 0; q < n; q++) {
        while (z[k + 1] < (float)q) k++;
        int p = v[k];
        df[q] = (float)(q - p) * (float)(q - p) + f[p * stride], ds[q] = site[p * stride];
    }
    for (int q = 0; q < n; q++) f[q * stride] = df[q], site[q * stride] = ds[q];
}

// The nearest record of every cell of the grid (index (y * n[2] + z) * n[0] + x), a line at a time along x, z, y.
static void nearest(float *f, int *site, const int n[3]) {
    int m = n[0] > n[1] ? n[0] : n[1];
    m = m > n[2] ? m : n[2];
    float *df = malloc(sizeof *df * (size_t)m), *z = malloc(sizeof *z * (size_t)(m + 1));
    int *ds = malloc(sizeof *ds * (size_t)m), *v = malloc(sizeof *v * (size_t)m);
    if (df && z && ds && v) {
        size_t nx = (size_t)n[0], nxz = nx * (size_t)n[2];
        for (int y = 0; y < n[1]; y++)
            for (int zz = 0; zz < n[2]; zz++) {
                size_t o = (size_t)y * nxz + (size_t)zz * nx;
                near1(f + o, site + o, n[0], 1, df, ds, v, z);
            }
        for (int y = 0; y < n[1]; y++)
            for (int x = 0; x < n[0]; x++) {
                size_t o = (size_t)y * nxz + (size_t)x;
                near1(f + o, site + o, n[2], nx, df, ds, v, z);
            }
        for (int zz = 0; zz < n[2]; zz++)
            for (int x = 0; x < n[0]; x++) {
                size_t o = (size_t)zz * nx + (size_t)x;
                near1(f + o, site + o, n[1], nxz, df, ds, v, z);
            }
    }
    free(df), free(z), free(ds), free(v);
}

void dbg_void_cells_update(const float me[3]) {
    if (!C.nroots) return;
    int at[3] = {(int)floorf(me[0]), (int)floorf(me[1]), (int)floorf(me[2])};
    if (C.have_mid && abs(at[0] - C.mid[0]) < REGION_STEP && abs(at[1] - C.mid[1]) < REGION_STEP && abs(at[2] - C.mid[2]) < REGION_STEP) return;
    C.mid[0] = at[0], C.mid[1] = at[1], C.mid[2] = at[2], C.have_mid = 1;
    // the grid: the box round the player and a margin each way, deeper below and above for the floor under open space
    int lo[3], n[3], pad[3] = {PAD, PAD_Y, PAD};
    for (int a = 0; a < 3; a++) lo[a] = at[a] - REGION_HALF - pad[a], n[a] = 2 * (REGION_HALF + pad[a]);
    size_t total = (size_t)n[0] * n[1] * n[2];
    float *f = malloc(total * sizeof *f);
    int *site = malloc(total * sizeof *site);
    int8_t *rec = malloc(total);
    uint8_t *g = calloc((total + 7) / 8, 1);
    if (!f || !site || !rec || !g) { free(f), free(site), free(rec), free(g); return; }
    for (int y = 0; y < n[1]; y++)
        for (int z = 0; z < n[2]; z++)
            for (int x = 0; x < n[0]; x++) {
                size_t i = ((size_t)y * n[2] + z) * n[0] + x;
                int w[3] = {lo[0] + x, lo[1] + y, lo[2] + z};
                rec[i] = (int8_t)cell_at(w);
                f[i] = rec[i] >= 0 ? 0 : 1e9f, site[i] = rec[i] >= 0 ? (int)i : -1;
            }
    nearest(f, site, n);
    free(f);
    // the void cells inside the box, below the top of the area's bounds (above it the nearest-record rule would draw
    // all the sky): outside the bounds grown by OUT_MARGIN across and below, every cell (the lookup's flag, bit 18 of
    // +0xa8); inside, a void record, or no record with the nearest record 8 m or more away across (the game's test is
    // on the horizontal distance) or a void one
    int any = 0;
    float out_lo[3], out_hi[3];
    for (int a = 0; a < 3; a++) out_lo[a] = C.aabb_lo[a] - OUT_MARGIN, out_hi[a] = C.aabb_hi[a] + OUT_MARGIN;
    for (int y = pad[1]; y < n[1] - pad[1]; y++)
        for (int z = PAD; z < n[2] - PAD; z++)
            for (int x = PAD; x < n[0] - PAD; x++) {
                size_t i = ((size_t)y * n[2] + z) * n[0] + x;
                int w[3] = {lo[0] + x, lo[1] + y, lo[2] + z}, v;
                if (C.have_aabb && w[1] >= C.aabb_hi[1]) continue;
                if (C.have_aabb && (w[0] >= out_hi[0] || w[0] + 1 <= out_lo[0] || w[2] >= out_hi[2] ||
                                    w[2] + 1 <= out_lo[2] || w[1] + 1 <= out_lo[1]))
                    v = 1;
                else if (rec[i] >= 0) v = rec[i];
                else if (site[i] < 0) v = 1;
                else {
                    int s = site[i], sx = s % n[0], sz = s / n[0] % n[2], dx = sx - x, dz = sz - z;
                    v = dx * dx + dz * dz >= FAR2 || rec[s] == 1;
                }
                if (v) g[i >> 3] |= (uint8_t)(1u << (i & 7)), any = 1;
            }
    free(site), free(rec);
    float (*box)[6] = NULL;
    int nb = 0, cap = 0;
#define IDX(x, y, z) (((size_t)(y) * n[2] + (z)) * n[0] + (x))
#define G(x, y, z) (g[IDX(x, y, z) >> 3] >> (IDX(x, y, z) & 7) & 1)
#define CLR(x, y, z) (g[IDX(x, y, z) >> 3] &= (uint8_t)~(1u << (IDX(x, y, z) & 7)))
    for (int y = 0; any && y < n[1]; y++)   // boxes grown along x, then z, then y
        for (int z = 0; z < n[2]; z++)
            for (int x = 0; x < n[0]; x++) {
                if (!G(x, y, z)) continue;
                int x1 = x + 1, z1 = z + 1, y1 = y + 1;
                while (x1 < n[0] && G(x1, y, z)) x1++;
                for (int ok = 1; ok && z1 < n[2];) {
                    for (int xx = x; xx < x1; xx++) if (!G(xx, y, z1)) { ok = 0; break; }
                    if (ok) z1++;
                }
                for (int ok = 1; ok && y1 < n[1];) {
                    for (int zz = z; zz < z1 && ok; zz++)
                        for (int xx = x; xx < x1; xx++) if (!G(xx, y1, zz)) { ok = 0; break; }
                    if (ok) y1++;
                }
                for (int yy = y; yy < y1; yy++)
                    for (int zz = z; zz < z1; zz++)
                        for (int xx = x; xx < x1; xx++) CLR(xx, yy, zz);
                if (nb >= BOX_MAX) continue;
                if (nb == cap) {
                    int nc = cap ? cap * 2 : 1024;
                    void *p = realloc(box, (size_t)nc * sizeof *box);
                    if (!p) continue;
                    box = p, cap = nc;
                }
                float *b = box[nb++];
                b[0] = (float)(lo[0] + x), b[1] = (float)(lo[1] + y), b[2] = (float)(lo[2] + z);
                b[3] = (float)(lo[0] + x1), b[4] = (float)(lo[1] + y1), b[5] = (float)(lo[2] + z1);
            }
#undef IDX
#undef G
#undef CLR
    free(g);
    publish(box, nb, cap);
}

int dbg_void_cells(float (*out)[6], int max, const float me[3], float range) {
    int k = 0;
    plat_mutex_lock(&C.lock);
    for (int i = 0; i < C.n && k < max; i++) {
        const float *b = C.box[i];
        if (me && range > 0) {   // the distance from the player to the box
            float d2 = 0;
            for (int j = 0; j < 3; j++) {
                float d = me[j] < b[j] ? b[j] - me[j] : me[j] > b[3 + j] ? me[j] - b[3 + j] : 0;
                d2 += d * d;
            }
            if (d2 > range * range) continue;
        }
        memcpy(out[k++], b, sizeof *out);
    }
    plat_mutex_unlock(&C.lock);
    return k;
}
