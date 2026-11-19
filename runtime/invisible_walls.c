// Remove invisible walls (the Mods tab's "Remove invisible walls", OOTPC_NO_INVISIBLE_WALLS; and the Randomizer tab's
// "Remove invisible walls", OOTPC_RANDO_INVISIBLE_WALLS = remove, while the Randomizer mod is on). The game's invisible walls
// are collision of material AirWall (id 27): polygons in an area's collision tiles
// (Phive/StaticCompoundBody/<area>[_X<i>_Z<j>].*.bphsc.zs) and in the shape files of the placed models' and AirWall
// actors' air walls (Phive/Shape/**/*Airwall*, 619 of their 634 materials are AirWall). Each material has an entry in
// a collision filter table beside the material table (debug_hook.c describes both): u32 main layers, u32 0, u64 the sub
// layers the surface collides with. The player collides through sub layers 0 and 5 (Unspecified, ClimbingPlayer), so
// an air wall that stops him has either bit; the indoor and camera volumes (sub mask 0x77bbffff) never do.
//
// When it is on, every collision file unpacked whole (ov_zstd.c: the host's decoder, and the game's own decoder where
// a frame goes to it) has those two bits cleared in the filter entry of each AirWall material that stops the player,
// before the game reads the file. Enemies, NPCs, the horse and the camera keep their walls (their own sub layers stay).
// What an area has loaded keeps its walls until it loads again. Checked on Death Mountain Trail (child, from Kakariko):
// the air wall along the path's edge at (-40, 103, 66) stops Link at the edge, and with the walls removed he jumps
// down to the path below. Not every wall that looks invisible is one: the collision of some models reaches above what
// is drawn in their own material (the castle moat's edge in Hyrule Field, a stone wall's collision 3 m above the
// grass), and those stay.
#include <stdint.h>
#include <string.h>

#include "cfg.h"
#include "host.h"
#include "invisible_walls.h"
#include "mods.h"
#include "randomizer.h"

#define MAT_AIRWALL 27
#define SUB_PLAYER ((1ull << 0) | (1ull << 5))
#define SUB_SENSOR 0x77bbffffull

static uint32_t rd32(const uint8_t *p, size_t at) {
    uint32_t v;
    memcpy(&v, p + at, 4);
    return v;
}

// The Randomizer mod is on (in the Mods list, this run).
static int rando_on(void) {
    for (int i = 0; i < mods_count(); i++) {
        const ModInfo *m = mods_at(i);
        if (m && !strcmp(m->name, RANDO_MOD)) return m->start_enabled != 0;
    }
    return 0;
}

int walls_removed(void) {
    static unsigned seen = ~0u;
    static int on;
    unsigned v = cfg_version();
    if (v != __atomic_load_n(&seen, __ATOMIC_ACQUIRE)) {
        const char *r = cfg_get("OOTPC_RANDO_INVISIBLE_WALLS");
        int o = cfg_bool("OOTPC_NO_INVISIBLE_WALLS") || (r && !strcmp(r, "remove") && rando_on());
        __atomic_store_n(&on, o, __ATOMIC_RELEASE);
        __atomic_store_n(&seen, v, __ATOMIC_RELEASE);
    }
    return __atomic_load_n(&on, __ATOMIC_ACQUIRE);
}

static struct {
    uint64_t files, walls;
} S;

// The filter entries of n materials of a table at mat with its filter table at fil (both inside p[0..size)).
static int clear_table(uint8_t *p, size_t size, size_t mat, size_t fil, size_t n) {
    if (mat > size || fil > size || n > (size - mat) / 16 || n > (size - fil) / 16) return 0;
    int k = 0;
    for (size_t m = 0; m < n; m++) {
        if (rd32(p, mat + 16 * m) != MAT_AIRWALL) continue;
        uint64_t sub;
        memcpy(&sub, p + fil + 16 * m + 8, 8);
        if (sub == SUB_SENSOR || !(sub & SUB_PLAYER)) continue;
        sub &= ~SUB_PLAYER;
        memcpy(p + fil + 16 * m + 8, &sub, 8);
        k++;
    }
    return k;
}

// A shape file ("Phive\0" 01 00) at ph: u32 at +0x10 its material table, +0x14 its filter table, +0x20 and +0x24 their
// sizes (offsets from ph); the filter table has an entry for each material when the sizes agree.
static int clear_shape(uint8_t *p, size_t size, size_t ph) {
    if (ph > size || size - ph < 0x28 || memcmp(p + ph, "Phive\0\x01\0", 8)) return 0;
    uint32_t mat = rd32(p, ph + 0x10), fil = rd32(p, ph + 0x14), mat_n = rd32(p, ph + 0x20), fil_n = rd32(p, ph + 0x24);
    if (mat_n != fil_n || mat_n % 16) return 0;
    return clear_table(p, size, ph + mat, ph + fil, mat_n / 16);
}

// A collision tile ("Phive\0" 02 02): from +0x0c u32 offsets up to the file's size; section 5 the material table,
// 6 the filter table, then the compounds, and the terrain's shape file second to last.
static int clear_tile(uint8_t *p, size_t size) {
    uint32_t off[64];
    int n = 0;
    while (n < 64 && 0x0c + 4 * (size_t)n + 4 <= size) {
        off[n] = rd32(p, 0x0c + 4 * (size_t)n);
        if (off[n++] >= size) break;
    }
    if (n < 12 || off[n - 1] != size || off[5] > off[6] || off[6] > off[7]) return 0;
    int k = 0;
    if (off[7] - off[6] == off[6] - off[5]) k += clear_table(p, size, off[5], off[6], (off[6] - off[5]) / 16);
    return k + clear_shape(p, size, off[n - 2]);
}

void walls_decoded(void *data, size_t size) {
    uint8_t *p = data;
    if (!p || size < 0x30 || memcmp(p, "Phive\0", 6) || !walls_removed()) return;
    int k = !memcmp(p + 6, "\x02\x02", 2) ? clear_tile(p, size) : clear_shape(p, size, 0);
    if (!k) return;
    uint64_t files = __atomic_add_fetch(&S.files, 1, __ATOMIC_RELAXED);
    uint64_t walls = __atomic_add_fetch(&S.walls, (uint64_t)k, __ATOMIC_RELAXED);
    if (files == 1 || !(files & 63))
        rt_log("walls: %llu collision files loaded without their invisible walls (%llu air wall materials opened)",
               (unsigned long long)files, (unsigned long long)walls);
}
