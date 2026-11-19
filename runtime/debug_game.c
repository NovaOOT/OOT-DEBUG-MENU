// The debug tools' frame hook (recomp/patches-v111.txt, set dbgframe): the framework's per-frame update
// (main+0x6e6be0) calls the patch page at +0xe70 in place of its `ldr x8, [x0, #0xa8]`. The host function makes that
// load and then runs debug_warp.c dbg_game_frame on the game thread, where guest functions may be called. Also the
// Force age hook (set dbgage, below). Built per install, as its address depends on the version's patch page.
#include "debug_tools.h"
#include "desert_roam.h"
#include "host.h"
#include "n64pose.h"
#include "dark_link_fx.h"
#include "n64sword.h"

#if __has_include("patches.h")
#include "patches.h"
#endif

#ifdef RT_PATCH_PAGE
#define HOST_DEBUG_FRAME (RT_PATCH_PAGE + 0xe70)

static void debug_frame(Cpu *c) {
    c->x[8] = LD64(c->x[0] + 0xa8);
    dbg_game_frame(c);
    n64pose_game_frame();
}

__attribute__((constructor)) static void debug_frame_register(void) { rt_register_host(HOST_DEBUG_FRAME, debug_frame); }

// Force age (set dbgage). main+0x943550, which builds an actor's resource set, calls the patch page at +0xe00 in place
// of its `ldr x8, [x0, #0x20]`, with the set in x0: the host function makes that load after debug_warp.c
// dbg_resource_set has noted the player's set. main+0x945610, which gives a set its age and fills in that age's
// resources, calls +0xe50 in place of its `mov w20, w1`, with the set in x0 and the area's age in w1: the host function
// puts the age dbg_resource_age returns in both w1 and w20, which the rest of the function reads.
#define HOST_DEBUG_RESOURCE_SET (RT_PATCH_PAGE + 0xe00)
#define HOST_DEBUG_RESOURCE_AGE (RT_PATCH_PAGE + 0xe50)

static void debug_resource_set(Cpu *c) {
    dbg_resource_set(c->x[0]);
    c->x[8] = LD64(c->x[0] + 0x20);
}

static void debug_resource_age(Cpu *c) {
    uint32_t child = dbg_resource_age(c->x[0], (uint32_t)c->x[1] & 1);
    c->x[1] = child;
    c->x[20] = child;
}

__attribute__((constructor)) static void debug_resource_set_register(void) {
    rt_register_host(HOST_DEBUG_RESOURCE_SET, debug_resource_set);
    rt_register_host(HOST_DEBUG_RESOURCE_AGE, debug_resource_age);
}
// The model's skeleton world-matrix thunk takes the embedded ModelObj (FModel + 0xd8). The original thunk loads
// its skeleton pointer and tail-calls the world's scale-mode dispatcher. The host wrapper keeps that call's ABI,
// including the caller's link register, and restores local translations after the resulting world pose is ready.
// N64 Graphics' N64 Link (n64pose.c) reads the player's world pose there too.
#define HOST_DEBUG_LANK (RT_PATCH_PAGE + 0xe40)
#define SKELETON_WORLD RT_OOT_ADDR(0x70014e01c0ull, 0x700149bb60ull)   // 1.1.1, 1.0.0
static void debug_lank_world(Cpu *c) {
    uint64_t object = c->x[0], lr = c->x[30], matrix = c->x[1];
    int changed = dbg_lank_before(object - 0xd8);
    uint64_t skeleton = LD64(object + 0x20);
    rt_call(c, SKELETON_WORLD, 2, skeleton, matrix);
    if (changed) dbg_lank_after();
    n64pose_world_ready(object - 0xd8, matrix);
    n64sword_world_ready(object - 0xd8, matrix);
    c->x[30] = lr;
}
__attribute__((constructor)) static void debug_lank_register(void) { rt_register_host(HOST_DEBUG_LANK, debug_lank_world); }

// N64 Graphics' sword walls (n64sword.c): the player's reaction scheduler (main+0x82bbb0) picks the AttackCollide
// reaction, the weapon's rebound, when the hit state at [[player+0x270]+0x2b8] holds 8 at +0xf0 or +0xf4. Its
// `ldp w8, w9, [x8, #0xf0]` there calls +0xe80: the host function makes the load, and gives 8 in w9 from a weapon's
// strike on a wall until the rebound has started (n64sword_take). The function saved x30 in its prologue and reloads
// x8 and x9 after a call.
#define HOST_N64_SWORD_WALL (RT_PATCH_PAGE + 0xe80)
static void n64_sword_wall(Cpu *c) {
    uint64_t state = c->x[8];
    uint32_t f0 = LD32(state + 0xf0), f4 = LD32(state + 0xf4);
    c->x[8] = f0;
    c->x[9] = n64sword_take(c, f0, f4) ? 8 : f4;
    dark_link_fx_frame(c);   // the same scheduler runs once a frame for the player
}
__attribute__((constructor)) static void n64_sword_register(void) { rt_register_host(HOST_N64_SWORD_WALL, n64_sword_wall); }

// Items at any age (debug_ages.c): the pause menu item table's entry reader (main+0x33a2500) calls +0xe30 in place of
// its closing `mov w0, w21`, with the entry it has just read in x20; the host function notes it, then makes the move.
#define HOST_DEBUG_POUCH_ENTRY (RT_PATCH_PAGE + 0xe30)
static void debug_pouch_entry(Cpu *c) {
    dbg_pouch_entry(c->x[20]);
    c->x[0] = (uint32_t)c->x[21];
}
__attribute__((constructor)) static void debug_pouch_register(void) { rt_register_host(HOST_DEBUG_POUCH_ENTRY, debug_pouch_entry); }

// No void-out (debug_move.c dbg_no_void_out): the abyss areas' shared per-frame method (main+0x31e4610, the
// HoldSensorHitInfoGiveAbyss* nodes; the Condition one calls it once its condition holds) calls +0xe20 in place of
// its `ldr w24, [x1, #0x30]`, the number of sensor hits in the list at x1. The host function makes the load, or gives
// 0 while No void-out is on, and in the desert while Desert free roam holds it back (desert_roam.c): the method then
// sends no abyss hit to anything in the area and returns 0, as it does for an empty list. x30 is restored from the
// frame before the method returns.
#define HOST_DEBUG_ABYSS_HITS (RT_PATCH_PAGE + 0xe20)
static void debug_abyss_hits(Cpu *c) {
    uint32_t hits = LD32(c->x[1] + 0x30);
    if (hits) dbg_abyss_note(DBG_ABYSS_ZONE);
    c->x[24] = dbg_no_void_out_on() || (hits && desert_roam_holds()) ? 0 : hits;
}

// The player's own check (main+0x82c510, every frame from the reaction scheduler main+0x82bbb0) gathers every cause
// of a void-out (the areas above, falls with nowhere to land, and the rest) into one abyss type, which it stores at
// player+0x191c and in a game-wide record; a non-zero type makes the scheduler pick the PreAbyss reaction. Its
// `str w8, [x19, #0x191c]` calls +0xe10: the host function stores 0 in place of the type while No void-out is on (or
// Desert free roam holds it back, as above), and leaves 0 in w8, so the game-wide record the function writes next reads
// no abyss either.
#define HOST_DEBUG_ABYSS_TYPE (RT_PATCH_PAGE + 0xe10)
static void debug_abyss_type(Cpu *c) {
    dbg_abyss_note((uint32_t)c->x[8]);   // the cause, for the debug overlay and the console, before it is held back
    if (dbg_no_void_out_on() || ((uint32_t)c->x[8] && desert_roam_holds())) c->x[8] = 0;
    ST32(c->x[19] + 0x191c, (uint32_t)c->x[8]);
}

// Bomb T-Pose (debug_move.c dbg_bomb_layers): the animation owner's update (main+0x29d740, its layer list in x19: their
// number at +0x18, the layers at +0x20, 0x48 apart) has updated every layer when it reaches main+0x29e548, where it goes
// through them again; its `ldrsw x8, [x19, #0x18]` there calls +0xec0. The host function lets dbg_bomb_layers set up
// the player's face layer (it may call the game), then makes the load.
#define HOST_DEBUG_BOMB_LAYERS (RT_PATCH_PAGE + 0xec0)
static void debug_bomb_layers(Cpu *c) {
    uint64_t list = c->x[19];
    dbg_bomb_layers(c, list);
    c->x[8] = (uint64_t)(int64_t)(int32_t)LD32(list + 0x18);
}
__attribute__((constructor)) static void debug_bomb_register(void) { rt_register_host(HOST_DEBUG_BOMB_LAYERS, debug_bomb_layers); }

// Player parameters (debug_move.c dbg_player_param): the PlayerGameParam reader (main+0x3009910) calls +0xe60 in place
// of its `add x8, x0, #0x2a0`, with the block it is about to fill in x0. The host function notes the block, then makes
// the add.
#define HOST_DEBUG_PLAYER_PARAM (RT_PATCH_PAGE + 0xe60)
static void debug_player_param(Cpu *c) {
    dbg_player_param(c->x[0]);
    c->x[8] = c->x[0] + 0x2a0;
}
__attribute__((constructor)) static void debug_param_register(void) { rt_register_host(HOST_DEBUG_PLAYER_PARAM, debug_player_param); }

__attribute__((constructor)) static void debug_abyss_register(void) {
    rt_register_host(HOST_DEBUG_ABYSS_HITS, debug_abyss_hits);
    rt_register_host(HOST_DEBUG_ABYSS_TYPE, debug_abyss_type);
}

#endif
