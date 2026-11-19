// Desert free roam: with the mod that turns the feature on (mods.h MOD_FEATURE_DESERT_FREE_ROAM), the game's void-out
// is held back in the Haunted Wasteland and the Desert Colossus, as the debug menu's No void-out does everywhere
// (debug_game.c debug_abyss_hits and debug_abyss_type ask desert_roam_holds as well as dbg_no_void_out_on).
//
// The mod's files take away what the data does there (tools/make_desert_roam_mod.py): the Areas that send the player
// back and the void cells of the volume statistics. What is left is in the player's fall check itself
// (debug_voidcells.c): it voids every point 8 m or more from any volume statistics record, and every point outside the
// area's VolumeStatsAABB grown by 5 m across and below, and the open sand and the edges of both areas are such points.
// Only the void-out is held back: the ground, the walls and the loading zones stay. So that a fall out of the world
// still ends, a player more than FALL_DEPTH metres below the bottom of the area's VolumeStatsAABB is let go.
#include "desert_roam.h"

#include <string.h>

#include "debug_tools.h"
#include "mods.h"
#include "platform/platform.h"

#define FALL_DEPTH 40.0f

static const char *const areas[] = {"HountedWasteland_Adult", "HountedWasteland_Child", "DesertColossus_Adult",
                                    "DesertColossus_Child"};

// The bottom of the area's VolumeStatsAABB, read once per area (both hooks may ask from different threads).
static PlatMutex lock = PLAT_MUTEX_INIT;
static char box_area[64];
static int box_ok;
static float box_bottom;

static int bottom_of(const char *area, float *bottom) {
    plat_mutex_lock(&lock);
    if (strcmp(box_area, area)) {
        float lo[3], hi[3];
        box_ok = dbg_volume_stats_box(area, lo, hi);
        box_bottom = box_ok ? lo[1] : 0;
        strncpy(box_area, area, sizeof box_area - 1);
        box_area[sizeof box_area - 1] = 0;
    }
    int ok = box_ok;
    *bottom = box_bottom;
    plat_mutex_unlock(&lock);
    return ok;
}

int desert_roam_holds(void) {
    if (!mods_feature(MOD_FEATURE_DESERT_FREE_ROAM, NULL, 0)) return 0;
    char area[64];
    if (!dbg_current_area(area)) return 0;
    int desert = 0;
    for (size_t i = 0; i < sizeof areas / sizeof *areas && !desert; i++) desert = !strcmp(area, areas[i]);
    if (!desert) return 0;
    float pos[3], bottom;
    if (bottom_of(area, &bottom) && dbg_player_pos(pos) && pos[1] < bottom - FALL_DEPTH) return 0;
    return 1;
}
