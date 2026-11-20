// Fly, hover and infinite Hover Boots for the debug menu (debug_tools.h), with the No void-out and Bomb T-Pose settings.
// Game builds 1.1.1 and 1.0.0 (their addresses through RT_OOT_ADDR).
//
// Fly and hover put the player where they want with the game's own actor warp (debug_warp.c dbg_frame_teleport) on
// every frame, from the debug frame hook (debug_game.c, game thread), so the physics body goes along and gravity never
// gets to act. Each keeps its own position and moves it with the left stick: fly at its own speed and also up and down,
// hover at a fixed height and about running speed (the game gives a player in the air no walking speed). Infinite Hover
// Boots leaves the player to the game and only makes the boots' own hover time endless (boots_frame).
#include <math.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cfg.h"
#include "debug_tools.h"
#include "host.h"
#include "input.h"
#include "layout.h"
#include "platform/platform.h"
#include "vk_debug_cam.h"

#define BTN_A (1ull << 0)
#define BTN_B (1ull << 1)
#define BTN_X (1ull << 2)
#define STICK_DEAD 0.15f
#define FRAME_DT_MAX 0.1f           // seconds: a longer gap (a loading screen) moves nothing
#define CAMERA_NAME "PlayerCamera"  // its position is the eye: the player minus it is the view direction

static _Atomic int fly_on, hover_on, boots_on, no_void_on, bomb_bug_on, bunny_on, mask_powers_on;
static _Atomic float bunny_factor = 1.5f;
static _Atomic float fly_speed = 8.0f;

void dbg_fly(int on) { atomic_store(&fly_on, on != 0); }
int dbg_fly_on(void) { return atomic_load(&fly_on); }

// The screen the game's UI is on: an enum flag without a known name (key 0x578d41e1) whose values are None, ItemMenu,
// EquipmentMenu, CollectMenu, SystemMenu, Save, Load, Option, ..., Ocarina, Title, Loading, GameOver, stored as the
// hash of the name (dbg_flag_key). It reads Ocarina from the moment the ocarina is taken out until it is put away, and
// None again then (watched in the game, 1.1.1).
#define UI_SCREEN_KEY 0x578d41e1u
#define UI_SCREEN_OCARINA 0x6a586f83u   // dbg_flag_key("Ocarina")

uint64_t dbg_fly_game_mask(void) {
    uint32_t screen;
    if (!atomic_load(&fly_on) || !dbg_flag_get_u32(DBG_FLAG_ENUM, UI_SCREEN_KEY, &screen)) return 0;
    return screen == UI_SCREEN_OCARINA ? BTN_B : 0;
}
void dbg_hover(int on) { atomic_store(&hover_on, on != 0); }
int dbg_hover_on(void) { return atomic_load(&hover_on); }
// Infinite Hover Boots, No void-out, Bomb T-Pose and the fly speed stay as set from one start to the next (debug_hover_boots,
// debug_fly_speed, debug_no_void_out, debug_bomb_bug; debug_tools.c dbg_keep). Fly and hover do not: a start in the air
// or inside a wall would surprise.
static void move_load(void) {
    static _Atomic int done;
    if (atomic_load(&done) || atomic_exchange(&done, 1)) return;
    atomic_store(&boots_on, cfg_bool("OOTPC_DEBUG_HOVER_BOOTS") != 0);
    atomic_store(&no_void_on, cfg_bool("OOTPC_DEBUG_NO_VOID_OUT") != 0);
    atomic_store(&bomb_bug_on, cfg_bool("OOTPC_DEBUG_BOMB_BUG") != 0);
    atomic_store(&bunny_on, cfg_bool("OOTPC_DEBUG_BUNNY") != 0);
    atomic_store(&mask_powers_on, cfg_bool("OOTPC_DEBUG_MASK_POWERS") != 0);
    float bf = strtof(cfg_value("OOTPC_DEBUG_BUNNY_SPEED"), NULL);
    atomic_store(&bunny_factor, bf >= 1.0f && bf <= 3.0f ? bf : 1.5f);
    float ms = strtof(cfg_value("OOTPC_DEBUG_FLY_SPEED"), NULL);
    atomic_store(&fly_speed, ms >= 1.0f && ms <= 100.0f ? ms : 8.0f);
}

void dbg_hover_boots(int on) {
    move_load();
    atomic_store(&boots_on, on != 0);
    dbg_keep("OOTPC_DEBUG_HOVER_BOOTS", on ? "1" : "0");
}
int dbg_hover_boots_on(void) { move_load(); return atomic_load(&boots_on); }
// No void-out: read by debug_game.c debug_abyss_hits, the host function of the abyss areas' patch site, on every
// frame such an area is near; kept from one start to the next (debug_no_void_out).
void dbg_no_void_out(int on) {
    move_load();
    atomic_store(&no_void_on, on != 0);
    dbg_keep("OOTPC_DEBUG_NO_VOID_OUT", on ? "1" : "0");
}
int dbg_no_void_out_on(void) { move_load(); return atomic_load(&no_void_on); }

// What sends the player back, as the game sees it (debug_game.c: debug_abyss_type gives the type the player's own
// check gathered, debug_abyss_hits DBG_ABYSS_ZONE when an abyss area has the player among its hits): the last cause
// seen, when, and where the player stood. Noted whether or not No void-out holds it back, so the debug overlay can
// say "void-out here" at spots that have no volume to draw (a fall check, the edge of the world).
static _Atomic uint32_t abyss_cause;
static _Atomic uint64_t abyss_ns;
static float abyss_at[3];
void dbg_abyss_note(uint32_t cause) {
    if (!cause) return;
    float p[3];
    if (dbg_player_pos(p)) memcpy(abyss_at, p, sizeof p);
    atomic_store(&abyss_cause, cause);
    atomic_store(&abyss_ns, plat_now_ns());
}
int dbg_abyss_last(uint32_t *cause, float pos[3], uint64_t *age_ns) {
    uint64_t t = atomic_load(&abyss_ns);
    if (!t) return 0;
    *cause = atomic_load(&abyss_cause);
    memcpy(pos, abyss_at, sizeof abyss_at);
    *age_ns = plat_now_ns() - t;
    return 1;
}
// Bomb T-Pose: bomb_frame below, on every frame; kept from one start to the next (debug_bomb_bug).
void dbg_bomb_bug(int on) {
    move_load();
    atomic_store(&bomb_bug_on, on != 0);
    dbg_keep("OOTPC_DEBUG_BOMB_BUG", on ? "1" : "0");
}
int dbg_bomb_bug_on(void) { move_load(); return atomic_load(&bomb_bug_on); }
void dbg_bunny(int on) {
    move_load();
    atomic_store(&bunny_on, on != 0);
    dbg_keep("OOTPC_DEBUG_BUNNY", on ? "1" : "0");
}
int dbg_bunny_on(void) { move_load(); return atomic_load(&bunny_on); }
float dbg_bunny_speed(void) { move_load(); return atomic_load(&bunny_factor); }
void dbg_set_bunny_speed(float f) {
    move_load();
    if (!(f >= 1.0f && f <= 3.0f)) return;
    atomic_store(&bunny_factor, f);
    char v[16];
    snprintf(v, sizeof v, "%.2f", f);
    dbg_keep("OOTPC_DEBUG_BUNNY_SPEED", v);
}
float dbg_fly_speed(void) { move_load(); return atomic_load(&fly_speed); }
void dbg_set_fly_speed(float ms) {
    move_load();
    ms = ms < 1.0f ? 1.0f : ms > 100.0f ? 100.0f : ms;
    atomic_store(&fly_speed, ms);
    char v[16];
    snprintf(v, sizeof v, "%g", ms);
    dbg_keep("OOTPC_DEBUG_FLY_SPEED", v);
}

// ---- the camera actor, found by a helper thread and checked on every use

static _Atomic uint64_t camera;
static _Atomic int camera_looking;

static void camera_thread(void *arg) {
    (void)arg;
    uint64_t a[2] = {0, 0};
    if (dbg_find_actors(CAMERA_NAME, a, 2) > 0) atomic_store(&camera, a[0]);
    atomic_store(&camera_looking, 0);
}

static int camera_pos(float out[3]) {
    uint64_t c = atomic_load(&camera);
    if (c && dbg_actor_named(c, CAMERA_NAME) && plat_mem_read(c + 0x31c, out, 12) && isfinite(out[0]) && isfinite(out[2])) return 1;
    atomic_store(&camera, 0);
    int zero = 0;
    if (atomic_compare_exchange_strong(&camera_looking, &zero, 1) && !plat_thread_spawn(camera_thread, NULL, "debug-camera", 0))
        atomic_store(&camera_looking, 0);
    return 0;
}

// ---- infinite Hover Boots
//
// How long the boots carry the player is in the player's game parameters (Player.game__player__PlayerGameParam):
// HoverBootsFloatingTime 84 and HoverBootsFloatingTimeAfterJump 42, frames, which the game keeps in memory as two floats
// in a row between 1.0 and 3.6 (seen in the game, 1.1.1; setting both higher there made the boots hover on). While the
// option is on both hold BOOTS_FOREVER, so the boots' own hover lasts until the player lands or takes them off; when it
// is switched off they are 84 and 42 again, which also ends a hover that has run longer. A helper thread finds the
// block in the heap; every frame checks it is still there and finds it again when it is not (a new player, a reload).
#define BOOTS_TIME 84.0f
#define BOOTS_TIME_JUMP 42.0f
#define BOOTS_FOREVER 1e6f        // frames: hours
#define BOOTS_BEFORE 1.0f         // the parameter in memory before the two times
#define BOOTS_AFTER 3.6f          // and after them
#define BOOTS_RETRY_NS 5000000000ull   // a search that found nothing waits this long before the next
#define HEAP_FROM 0x100000000ull       // the game's heap, as in debug_warp.c dbg_each_actor
#define HEAP_TO 0x700000000ull

static _Atomic uint64_t boots_at;   // the address of HoverBootsFloatingTime, 0 while not known
static _Atomic int boots_looking;
static _Atomic uint64_t boots_tried_ns;

static int boots_block(const float v[4], int forever) {
    return v[0] == BOOTS_BEFORE && v[3] == BOOTS_AFTER &&
           (forever ? v[1] == BOOTS_FOREVER && v[2] == BOOTS_FOREVER : v[1] == BOOTS_TIME && v[2] == BOOTS_TIME_JUMP);
}

static void boots_thread(void *arg) {
    (void)arg;
    enum { CHUNK = 1 << 20 };
    static float buf[CHUNK / 4 + 4];
    uint64_t found = 0;
    for (uint64_t addr = HEAP_FROM; addr < HEAP_TO && !found;) {
        uint64_t end = 0;
        int st = plat_mem_state(addr, HEAP_TO, &end);
        if (end <= addr) break;
        for (uint64_t at = addr; st == PLAT_PROT_RW && at < end && !found; at += CHUNK) {
            uint64_t len = end - at < CHUNK + 12 ? end - at : CHUNK + 12;   // 12 more bytes: a block on the seam
            if (len < 16 || !plat_mem_read(at, buf, len)) continue;
            for (uint64_t i = 0; i + 4 <= len / 4 && i < CHUNK / 4; i++)
                if ((buf[i + 1] == BOOTS_TIME || buf[i + 1] == BOOTS_FOREVER) && (boots_block(buf + i, 0) || boots_block(buf + i, 1))) {
                    found = at + i * 4 + 4;
                    break;
                }
        }
        addr = end;
    }
    if (found) atomic_store(&boots_at, found);
    atomic_store(&boots_tried_ns, plat_now_ns());
    atomic_store(&boots_looking, 0);
}

static void boots_frame(void) {
    int on = dbg_hover_boots_on();
    uint64_t at = atomic_load(&boots_at);
    float v[4];
    if (at && !(plat_mem_read(at - 4, v, sizeof v) && (boots_block(v, 0) || boots_block(v, 1)))) atomic_store(&boots_at, 0), at = 0;
    if (!at) {
        // Nothing to put back while off: a block found again holds the game's own times.
        int zero = 0;
        if (on && plat_now_ns() - atomic_load(&boots_tried_ns) > BOOTS_RETRY_NS &&
            atomic_compare_exchange_strong(&boots_looking, &zero, 1) && !plat_thread_spawn(boots_thread, NULL, "debug-boots", 0))
            atomic_store(&boots_looking, 0);
        return;
    }
    if (boots_block(v, on)) return;
    float t = on ? BOOTS_FOREVER : BOOTS_TIME, tj = on ? BOOTS_FOREVER : BOOTS_TIME_JUMP;
    uint32_t bt, btj;
    memcpy(&bt, &t, 4), memcpy(&btj, &tj, 4);
    dbg_write32(at, bt), dbg_write32(at + 4, btj);
}

// ---- PastyPixel's Bomb T-Pose
//
// The bug in the game (1.1.1; docs/bomb-t-pose.md has the whole account): the player's animation (the AS component,
// "?AS/Player_Adult.root.asb" or "?AS/Player_Child.root.asb", whose owner is at component+0x70) blends a list of layers,
// each through a bone mask. The face layer (its states are Face..., and DemoOcarina... while the ocarina plays)
// normally masks the face bones only. Played until the notes change, the ocarina's face state DemoOcarinaBreath gives
// that layer the breathing mask: the face, the chest and both arms. The face states that come after it (FaceDefault,
// FaceLocomotion) name no mask of their own, so the breathing mask stays until a state that does name one (a bomb
// pulled out standing still: its WeaponEquipOn sets the face mask again; standing idle a while does too). A bomb pulled
// out while moving moves the face layer to the body's Move node, which has no face animation: the layer fades into
// having no state, and stays so until FaceDefault starts (about 16 frames). For those frames the bones under the
// breathing mask take the stateless layer's pose instead of the carry's: both arms out to the sides, the bomb in the
// right hand held out at shoulder height. FaceDefault fading in brings the arms back up over the head.
//
// While the option is on, dbg_bomb_layers, called from the animation owner's update after it has updated its layers
// (debug_game.c debug_bomb_layers), keeps the breathing mask's definition on the face layer at all times (mask_define:
// the bits are built from it as the game builds them), as after the ocarina but with nothing that clears it: a state
// that sets a mask of its own (standing idle, a standing pull) has the breathing one back on the next frame. So every
// pull while moving goes as it does after the ocarina. The face states play as usual. When the option goes off, the
// definition the game gave the layer last goes back the same way.
#define ACTOR_COMPS 0x270        // the actor's component array, their number at +0x278
#define ACTOR_COMP_COUNT 0x278
#define COMP_NAME 0x8            // a component's resource name
#define AS_OWNER 0x70            // the AS component's animation owner
#define OWNER_LAYERS 0x198       // the owner's layers, LAYER_STEP apart
#define LAYER_STEP 0x48
#define LAYER_MAX 12
#define LAYER_CLASS RT_OOT_ADDR(0x4c29788, 0x4bc3b80)   // a layer's vtable, main+ (1.1.1, 1.0.0)
#define LAYER_MASK 0x50          // the layer's bone mask (MASK_*)
#define LAYER_STATE_NAMES 0x250  // the names of the layer's two state slots
// A layer's mask: its bits (from the pointer at +0x10, 0x80 bytes) are built from a definition that follows them (the
// pointer at +0x18; its method at vtable +0x30 builds them, given the mask's +0x8). The definition lists bones, each
// with a flag byte: the bone and the bones under it are set or cleared, in order (DEF_*).
#define MASK_SET 0x8
#define MASK_DEF 0x18
#define MASK_FLAGS 0x30          // bit 0: the definition changed, bits not built yet; bit 4: built, not yet taken up
#define DEF_BUILD 0x30           // the definition's method that builds the bits
#define DEF_FLAGS 0x28           // the flag bytes (pointer), their room at +0x20
#define DEF_VERSION 0x30         // counts the definitions the mask has been given
#define DEF_COUNT 0x34           // u16: the number of bones listed
#define DEF_BONES 0x40           // the bones (pointer; each an s16 skeleton number and an s16 bone), their room at +0x38
#define DEF_ROOM 16
#define LIST_COUNT 0x18          // the owner's layer list: the number of layers, their entries (LAYER_STEP apart)
#define LIST_LAYERS 0x20

// The breathing mask's definition as the game gives it with DemoOcarinaBreath (seen in the game, 1.1.1, both ages):
// Root and the bones under it, then Spine_2 (the chest, both arms, the head) the other way, then the bones that hold
// things (Weapon_L, Weapon_R) and the fingers back as Root. The face mask the game uses otherwise lists Root and
// Face_Root (bone 107 at both ages) the same way.
#define BREATH_BONES 14
static const uint8_t breath_flags[BREATH_BONES] = {0x83, 0x80, 0x83, 0x83, 0x83, 0x83, 0x83, 0x83, 0x83, 0x83, 0x83, 0x83, 0x83, 0x83};
static const int16_t breath_child[BREATH_BONES] = {0, 11, 23, 68, 20, 24, 29, 34, 39, 65, 69, 74, 79, 84};
static const int16_t breath_adult[BREATH_BONES] = {0, 6, 18, 66, 14, 19, 24, 29, 34, 62, 67, 72, 77, 82};

static uint64_t rd64(uint64_t at) {
    uint64_t v = 0;
    return plat_mem_read(at, &v, 8) ? v : 0;
}

static int comp_named(uint64_t comp, const char *part, char *name, size_t n) {
    uint64_t p = rd64(comp + COMP_NAME);
    if (!p || !plat_mem_read(p, name, n - 1)) return 0;
    name[n - 1] = 0;
    return strstr(name, part) != NULL;
}

// Found by bomb_frame on every frame (debug frame hook) and read by dbg_bomb_layers (the animation owner's update):
// the face layer (0: none known, or the option off) and the player's age.
static _Atomic uint64_t bomb_face;
static _Atomic int bomb_child, bomb_holding;   // bomb_holding: dbg_bomb_layers holds a mask, to be put back
static struct {
    uint64_t actor, comps, as;   // the player, its component array, its AS component
} bomb;
// Used by dbg_bomb_layers only: the layer given the breathing mask, and the definition it had before.
static struct {
    uint64_t layer;
    uint16_t count;
    uint8_t flags[DEF_ROOM];
    int16_t bones[DEF_ROOM][2];
} held;

static void bomb_components(uint64_t actor) {
    char name[96];
    uint64_t arr = rd64(actor + ACTOR_COMPS);
    uint32_t n = 0;
    bomb.actor = actor, bomb.comps = arr, bomb.as = 0;
    if (!arr || !plat_mem_read(actor + ACTOR_COMP_COUNT, &n, 4) || n > 1024) return;
    for (uint32_t i = 0; i < n && !bomb.as; i++) {
        uint64_t c = rd64(arr + 8ull * i);
        if (c && comp_named(c, ".root.asb", name, sizeof name)) atomic_store(&bomb_child, strstr(name, "_Child") != NULL), bomb.as = c;
    }
}

// The face layer: the one whose state slots are named Face... or DemoOcarina... (the names stay while it has no state).
static uint64_t face_layer(uint64_t owner) {
    for (int i = 0; i < LAYER_MAX; i++) {
        uint64_t l = rd64(owner + OWNER_LAYERS + (uint64_t)i * LAYER_STEP);
        if (!l || rd64(l) != MOD_MAIN_BASE + LAYER_CLASS) continue;
        for (int s = 0; s < 2; s++) {
            char nm[12] = "";
            uint64_t p = rd64(l + LAYER_STATE_NAMES + 8ull * s);
            if (p && plat_mem_read(p, nm, sizeof nm - 1) && (!strncmp(nm, "Face", 4) || !strncmp(nm, "DemoOcarina", 11))) return l;
        }
    }
    return 0;
}

static void bomb_forget(void) { atomic_store(&bomb_face, 0); }

static void bomb_frame(void) {
    uint64_t actor = dbg_player_actor();
    if ((!dbg_bomb_bug_on() && !atomic_load(&bomb_holding)) || !actor) {
        bomb_forget();
        return;
    }
    if (actor != bomb.actor || rd64(actor + ACTOR_COMPS) != bomb.comps || !bomb.as) {
        bomb_forget();   // a new player (a reload, an age switch)
        bomb_components(actor);
        if (!bomb.as) return;
    }
    uint64_t owner = rd64(bomb.as + AS_OWNER);
    atomic_store(&bomb_face, owner ? face_layer(owner) : 0);
}

// Gives a layer's mask this definition, as the game does: the version moves on, then the bits are built and marked built
// (the play routine takes them up from there).
static void mask_define(struct Cpu *c, uint64_t layer, uint16_t count, const uint8_t *flags, const int16_t (*bones)[2]) {
    uint64_t m = LD64(layer + LAYER_MASK), d = LD64(m + MASK_DEF), fl = LD64(d + DEF_FLAGS), bo = LD64(d + DEF_BONES);
    for (uint16_t i = 0; i < count; i++) {
        ST8(fl + i, flags[i]);
        ST16(bo + 4ull * i, (uint16_t)bones[i][0]);
        ST16(bo + 4ull * i + 2, (uint16_t)bones[i][1]);
    }
    ST16(d + DEF_COUNT, count);
    ST32(d + DEF_VERSION, LD32(d + DEF_VERSION) + 1);
    rt_call(c, LD64(LD64(d) + DEF_BUILD), 2, d, m + MASK_SET);
    ST8(m + MASK_FLAGS, (uint8_t)((LD8(m + MASK_FLAGS) & ~1u) | 0x10));
}

// Whether a layer's mask has the breathing definition; when it has not, its definition is kept in held.
static int mask_is_breath(uint64_t layer, const int16_t *breath, int keep) {
    uint64_t m = LD64(layer + LAYER_MASK), d = m ? LD64(m + MASK_DEF) : 0;
    if (!d || LD32(d + 0x20) < DEF_ROOM || LD32(d + 0x38) < DEF_ROOM) return -1;   // not the layout known
    uint64_t fl = LD64(d + DEF_FLAGS), bo = LD64(d + DEF_BONES);
    uint16_t n = LD16(d + DEF_COUNT);
    int same = n == BREATH_BONES;
    for (uint16_t i = 0; same && i < n; i++)
        same = LD8(fl + i) == breath_flags[i] && LD16(bo + 4ull * i) == 0 && (int16_t)LD16(bo + 4ull * i + 2) == breath[i];
    if (!same && keep && n <= DEF_ROOM) {
        held.count = n;
        for (uint16_t i = 0; i < n; i++)
            held.flags[i] = LD8(fl + i), held.bones[i][0] = (int16_t)LD16(bo + 4ull * i), held.bones[i][1] = (int16_t)LD16(bo + 4ull * i + 2);
    }
    return same;
}

void dbg_bomb_layers(struct Cpu *c, uint64_t list) {
    uint64_t face = atomic_load(&bomb_face), arr = LD64(list + LIST_LAYERS);
    uint32_t n = LD32(list + LIST_COUNT), i = 0;
    while (face && arr && i < n && i < LAYER_MAX && LD64(arr + LAYER_STEP * (uint64_t)i) != face) i++;
    if (!face || !arr || i >= n || i >= LAYER_MAX) {
        // Not this owner's layers, or no face layer known: a held layer that has gone (a new player) is forgotten
        // unwritten, as its memory may be in other use by now.
        if (held.layer && held.layer != face) held.layer = 0, atomic_store(&bomb_holding, 0);
        return;
    }
    const int16_t *breath = atomic_load(&bomb_child) ? breath_child : breath_adult;
    if (!atomic_load(&bomb_bug_on)) {
        // Switched off: the definition the game gave the layer last goes back, unless it has given another since.
        if (held.layer == face && mask_is_breath(face, breath, 0) == 1) mask_define(c, face, held.count, held.flags, held.bones);
        held.layer = 0;
        atomic_store(&bomb_holding, 0);
        return;
    }
    int is = mask_is_breath(face, breath, 1);
    if (is < 0) return;
    if (!is) {
        // A definition of the game's own (the one the layer had, or one a state gave since) is kept in held to go back
        // when the option goes off; the breathing one takes its place.
        int16_t bones[BREATH_BONES][2];
        for (int k = 0; k < BREATH_BONES; k++) bones[k][0] = 0, bones[k][1] = breath[k];
        mask_define(c, face, BREATH_BONES, breath_flags, (const int16_t (*)[2])bones);
        held.layer = face;
        atomic_store(&bomb_holding, 1);
    }
}

// ---- per frame

static uint64_t last_ns;
static float air_pos[3], fwd[3] = {0, 0, 1};
static int air_mode;   // which of FLY, HOVER owns air_pos (0: none)
enum { AIR_FLY = 1, AIR_HOVER };
#define WALK_MS 6.0f   // hover moves at about the player's running speed

static float axis(int32_t v) {
    float f = (float)v / 32767.0f;
    return fabsf(f) < STICK_DEAD ? 0.0f : f;
}

// The camera's direction along the ground into f (x and z, length 1), 0 when there is none to take. First the view the
// scene was drawn with last frame (vk_debug_cam.c vk_dbgcam_vp: its clip w row is the view direction), there from the
// first frame; then the player minus the camera actor's eye, which a helper thread has to find in the heap first;
// then the way the player faces. Without the first two, steering went by the last direction known, world +z at the
// first use: the stick was reversed or turned sideways for the seconds the search took after Fly was switched on.
static int air_forward(const float p[3], float f[3]) {
    float vp[16], cam[3], d[3] = {0, 0, 0};
    if (vk_dbgcam_vp(vp, NULL)) d[0] = vp[12], d[2] = vp[14];
    else if (camera_pos(cam)) d[0] = p[0] - cam[0], d[2] = p[2] - cam[2];
    else if (!dbg_player_facing(d)) return 0;
    float n = sqrtf(d[0] * d[0] + d[2] * d[2]);
    if (!(n > 1e-4f)) return 0;
    f[0] = d[0] / n, f[1] = 0, f[2] = d[2] / n;
    return 1;
}

// In the air the game gives the player no speed of its own, so both modes move air_pos with the left stick: up goes
// where the camera looks, right to its right, A held three times faster. Fly also rises on X and sinks on B.
static void air_step(struct Cpu *c, const float p[3], uint64_t b, int32_t lx, int32_t ly, float speed, int vertical, float dt) {
    air_forward(p, fwd);   // kept as it was when no direction is known this frame
    float sx = axis(lx), sy = axis(ly);
    if (b & BTN_A) speed *= 3.0f;
    float move[3] = {fwd[0] * sy - fwd[2] * sx, 0, fwd[2] * sy + fwd[0] * sx};
    air_pos[0] += move[0] * speed * dt;
    air_pos[2] += move[2] * speed * dt;
    if (vertical) air_pos[1] += (((b & BTN_X) ? 1.0f : 0.0f) - ((b & BTN_B) ? 1.0f : 0.0f)) * speed * dt;
    dbg_frame_teleport(c, air_pos, (sx != 0 || sy != 0) ? move : NULL);
}

// ---- Ragdoll
// The game's own player ragdoll as a bomb's blast starts it: the reaction Rolling_Medium, the one the AI node
// ExecutePlayerRolling (Player.action.rolling.module; its enter method main+0x27fb9c0) asks for when the player takes a
// hit of knock-back intensity Normal, which every bomb's attack has (Item_Bomb BombFruit and ExplosionBase AttackParam:
// DamageType Bomb, KnockbackIntensity Normal). The node turns the hit's intensity (the player component's +0x2d2) into
// the strength: 2 Rolling_Strong, 3 Rolling_Weak, any other Rolling_Medium (main+0x27fc7f0).
// The player's ragdoll controller is a component of the actor's physics owner ([[[[actor+0x270]+0x50]+0x20]+0x10], as
// main+0x837620 reads it), found by main+0x5f88c0. Its rolling request (RD_START) is the block rd_start fills, laid out
// as the node fills it for a blast behind the player on open ground (rd_fill_rolling); it sets the controller's state to 1. Its end (RD_END, the
// leave method of ExecuteEventPlayerImmediateRagdollDown, which ends whichever reaction runs) puts the body back under
// the animation, the player standing, and the state to 0. The state is not 0 while a ragdoll runs.
#define RD_FIND RT_OOT_ADDR(0x70005f88c0ull, 0x7000587460ull)    // physics owner -> ragdoll controller
#define RD_START RT_OOT_ADDR(0x7003217550ull, 0x70031bfdd0ull)   // controller, &request: a Rolling_* reaction
#define RD_END RT_OOT_ADDR(0x7003219090ull, 0x70031c18e0ull)     // controller, &{actor, 0}; returns at once with no ragdoll running
// The controller's other reactions, each started by its own function (controller, &request) that names the reaction
// and sets the state: DownLand 2, ImmediateDown 3 (the Wallmaster's drop), AirDead 4, DeadStand 5, DeadDown 6,
// Pressed 7. They read the request ImmediateDown's AI node fills (rd_fill_plain).
// Each frame a reaction runs, its AI node hands the controller the player's state (ExecutePlayerRolling's update,
// main+0x27fc270; ExecuteEventPlayerImmediateRagdollDown's, main+0x26b4040): RD_FEED (controller, &block) copies the
// block into the controller (+0x60 on), RD_STEP (controller) runs the controller's own update. Without them the
// reaction keeps the operation it started with: in water it never takes its InWater one and the body sinks.
// The block: the actor, the actor's water component (RD_WATER), the frame time in game frames, 0.0f, 1.0f, 3 when the
// player component's +0x7e8 is 1 (else 2), 0, then bytes: RD_MODE7 (the component's mode is 7), and for the rolling
// reactions twice RD_GROUND.
#define RD_FEED RT_OOT_ADDR(0x700321c040ull, 0x70031c4810ull)
#define RD_STEP RT_OOT_ADDR(0x70038911a0ull, 0x700382ffd0ull)
#define RD_WATER RT_OOT_ADDR(0x7000d95780ull, 0x7000d5dfd0ull)    // actor -> [actor+0x270]+0x268
#define RD_COMP RT_OOT_ADDR(0x7000944e50ull, 0x7000943fe0ull)     // actor -> the player component
#define RD_MODE7 RT_OOT_ADDR(0x700301aff0ull, 0x7002fc5d10ull)
#define RD_GROUND RT_OOT_ADDR(0x70030223e0ull, 0x7002fcce80ull)
#define RD_COMP_HEAVY 0x7e8   // both builds: the node method that reads it (1.1.1 main+0x27fc7b0, 1.0.0 main+0x27aac00)
static const struct { uint64_t v111, v100; } rd_starts[RD_KINDS - 4] = {
    {0x7003217e90ull, 0x70031c06e0ull},   // DownLand
    {0x7003218d30ull, 0x70031c1580ull},   // ImmediateDown
    {0x7003218bc0ull, 0x70031c1410ull},   // AirDead
    {0x7003218250ull, 0x70031c0aa0ull},   // DeadStand
    {0x70032188f0ull, 0x70031c1140ull},   // DeadDown
    {0x7003218f00ull, 0x70031c1750ull},   // Pressed
};
#define RD_STATE 0x17c
// The request's fields (main+0x3217550 reads them).
#define RQ_WEIGHT 0x08        // float, 1
#define RQ_BACK_ANGLE 0x0c    // float: the push's yaw less the yaw of the player's back, -pi to pi
#define RQ_SLOPE 0x10         // float: 0 on open ground (the node adds 10 degrees when a wall stands in the push)
#define RQ_FIXED 0x14         // the node's constants: 12.0f, 6.0f, 1, 2
#define RQ_STRENGTH 0x24      // 0 Rolling_Weak, 1 Rolling_Medium, 2 Rolling_Strong, 3 Rolling_RestraintThrown
#define RQ_SITUATION 0x28     // 0 OnGround, 1 InAir, 2 InWater, 3 Climb, 4 Ride (the reaction's operation)
#define RQ_PUSH 0x30          // float[3]: the direction the player is thrown, along the ground
#define RQ_SIDE 0x3c          // 2: thrown more than 75 degrees away from where the player faces (backwards); else 1
#define RD_AIR_MS 1.5f        // vertical speed over this: the player is in the air
static _Atomic int rd_req = -2;    // -2 nothing asked; else dbg_ragdoll's how
static _Atomic int rd_kind = RD_ROLLING_MEDIUM;   // the reaction dbg_ragdoll starts
static _Atomic uint64_t rd_actor, rd_ctrl;   // the controller found for that actor
static _Alignas(16) uint8_t rd_block[0x40];

static uint64_t ragdoll_owner(uint64_t actor) {
    uint64_t p = rd64(actor + 0x270);
    p = p ? rd64(p + 0x50) : 0;
    p = p ? rd64(p + 0x20) : 0;
    return p ? rd64(p + 0x10) : 0;
}

static uint32_t ragdoll_state(uint64_t ctrl) {
    uint32_t v = 0;
    return ctrl && plat_mem_read(ctrl + RD_STATE, &v, 4) ? v : 0;
}

int dbg_ragdoll(int how) {
    if (!dbg_player_actor()) return 0;
    atomic_store(&rd_req, how < 0 ? -1 : how != 0);
    return 1;
}

int dbg_ragdoll_kind(int kind) {
    if (kind >= 0 && kind < RD_KINDS) atomic_store(&rd_kind, kind);
    return atomic_load(&rd_kind);
}

uint32_t dbg_ragdoll_state(void) {
    uint64_t actor = dbg_player_actor();
    return actor && actor == atomic_load(&rd_actor) ? ragdoll_state(atomic_load(&rd_ctrl)) : 0;
}

int dbg_ragdoll_on(void) {
    uint64_t actor = dbg_player_actor();
    return actor && actor == atomic_load(&rd_actor) && ragdoll_state(atomic_load(&rd_ctrl)) != 0;
}

// The player gets up by himself, as a knocked-down enemy does: the enemies' common ragdoll setting
// (GameParameter/RagdollReactionParam/Enemy_Common, GroundDownTime 30 game frames) keeps the body down 30 frames once it
// lies on the ground, then stands it up. The player has no such setting, so the same time, a second at the game's 30
// frames a second, is counted here once the body (the actor's position follows it) has stopped moving, in game time
// (the game speed setting slows it too). RD_MAX_S ends a ragdoll that keeps sliding.
#define RD_DOWN_S 1.0f
#define RD_STILL_MS 0.4f   // ground and vertical speed under this: lying still (the body's own jitter stays under 0.15)
#define RD_MAX_S 10.0f
static float rd_still_s, rd_total_s;
static int rd_ours;   // a ragdoll this file started and will end

static void ragdoll_call(struct Cpu *c, uint64_t fn, uint64_t ctrl) {
    uint64_t a[8] = {ctrl, (uint64_t)(uintptr_t)rd_block};
    dbg_frame_call(c, fn, a);
}

// Fills rd_block as ExecutePlayerRolling does for a bomb going off behind the player on open ground: the push straight
// along where the player faces, so its angle to the player's back is pi and it counts as forwards.
static void rd_fill_rolling(uint32_t strength) {
    float face[3] = {0, 0, 1};
    if (!dbg_player_facing(face) || (face[0] == 0 && face[2] == 0)) face[0] = 0, face[2] = 1;
    const float push[3] = {face[0], 0.0f, face[2]};
    const float weight = 1.0f, back_angle = 3.14159265f, slope = 0.0f, fixed_f[2] = {12.0f, 6.0f};
    const uint32_t fixed_i[2] = {1, 2}, side = 1;
    const uint32_t situation = fabsf(dbg_vertical_speed()) > RD_AIR_MS ? 1 : 0;
    memcpy(rd_block + RQ_WEIGHT, &weight, 4);
    memcpy(rd_block + RQ_BACK_ANGLE, &back_angle, 4);
    memcpy(rd_block + RQ_SLOPE, &slope, 4);
    memcpy(rd_block + RQ_FIXED, fixed_f, 8);
    memcpy(rd_block + RQ_FIXED + 8, fixed_i, 8);
    memcpy(rd_block + RQ_STRENGTH, &strength, 4);
    memcpy(rd_block + RQ_SITUATION, &situation, 4);
    memcpy(rd_block + RQ_PUSH, push, 12);
    memcpy(rd_block + RQ_SIDE, &side, 4);
}

// The request ExecuteEventPlayerImmediateRagdollDown fills for its reaction (enter main+0x26b3ec0): weight 1.0 and the
// constants 1 and 2, the rest 0.
static void rd_fill_plain(void) {
    const float weight = 1.0f;
    const uint32_t fixed_i[2] = {1, 2};
    memcpy(rd_block + RQ_WEIGHT, &weight, 4);
    memcpy(rd_block + RQ_FIXED + 8, fixed_i, 8);
}

// Hands the controller this frame's state as the reaction's AI node does (RD_FEED), then runs its update (RD_STEP).
static _Alignas(16) uint8_t rd_feed[0x28];
static void ragdoll_feed(struct Cpu *c, uint64_t ctrl, uint64_t actor, float frames, int rolling) {
    uint64_t a[8] = {actor};
    uint64_t water = dbg_frame_call(c, RD_WATER, a);
    uint64_t comp = dbg_frame_call(c, RD_COMP, a);
    uint32_t heavy = 0;
    if (comp) plat_mem_read(comp + RD_COMP_HEAVY, &heavy, 4);
    const float zero_one[2] = {0.0f, 1.0f};
    const uint32_t kind = heavy == 1 ? 3 : 2;
    memset(rd_feed, 0, sizeof rd_feed);
    memcpy(rd_feed, &actor, 8);
    memcpy(rd_feed + 0x08, &water, 8);
    memcpy(rd_feed + 0x10, &frames, 4);
    memcpy(rd_feed + 0x14, zero_one, 8);
    memcpy(rd_feed + 0x1c, &kind, 4);
    rd_feed[0x24] = (uint8_t)(dbg_frame_call(c, RD_MODE7, a) & 1);
    if (rolling && comp) rd_feed[0x25] = rd_feed[0x26] = (uint8_t)(dbg_frame_call(c, RD_GROUND, a) & 1);
    uint64_t f[8] = {ctrl, (uint64_t)(uintptr_t)rd_feed};
    dbg_frame_call(c, RD_FEED, f);
    uint64_t s[8] = {ctrl};
    dbg_frame_call(c, RD_STEP, s);
}

static void ragdoll_frame(struct Cpu *c, float dt) {
    int how = atomic_exchange(&rd_req, -2);
    uint64_t actor = dbg_player_actor(), owner = actor ? ragdoll_owner(actor) : 0;
    if (!owner || actor != atomic_load(&rd_actor)) rd_ours = 0;   // no player, or a new one (a reload, an age switch)
    if (!owner) return;
    uint64_t ctrl = atomic_load(&rd_ctrl);
    if (how != -2 || actor != atomic_load(&rd_actor)) {
        if (how == -2) return;
        uint64_t a[8] = {owner};
        ctrl = dbg_frame_call(c, RD_FIND, a);
        if (!ctrl) return;
        atomic_store(&rd_ctrl, ctrl), atomic_store(&rd_actor, actor);
    }
    int on = ragdoll_state(ctrl) != 0;
    if (!on) rd_ours = 0;   // ended by the game, or never started
    memset(rd_block, 0, sizeof rd_block);
    memcpy(rd_block, &actor, 8);
    if (how == -1) how = !on;
    if (how == 1 && !on) {
        int kind = atomic_load(&rd_kind);
        if (kind < 4) {
            rd_fill_rolling((uint32_t)kind);
            ragdoll_call(c, RD_START, ctrl);
        } else {
            rd_fill_plain();
            ragdoll_call(c, RT_OOT_ADDR(rd_starts[kind - 4].v111, rd_starts[kind - 4].v100), ctrl);
        }
        rd_ours = 1, rd_still_s = rd_total_s = 0.0f;
        return;
    }
    if (how == 0 && on) {
        ragdoll_call(c, RD_END, ctrl);
        rd_ours = 0;
        return;
    }
    if (!rd_ours) return;
    float gdt = dt * dbg_time_scale();
    ragdoll_feed(c, ctrl, actor, gdt * 30.0f, atomic_load(&rd_kind) < 4);
    rd_total_s += gdt;
    int still = dbg_ground_speed() < RD_STILL_MS && fabsf(dbg_vertical_speed()) < RD_STILL_MS;
    rd_still_s = still ? rd_still_s + gdt : 0.0f;
    if (rd_still_s >= RD_DOWN_S || rd_total_s >= RD_MAX_S) {
        ragdoll_call(c, RD_END, ctrl);
        rd_ours = 0;
    }
}



// ---- the player's game parameters
// The PlayerGameParam blocks the game filled when the save loaded (debug_game.c debug_player_param): one for the child,
// one for the adult; the byte at +0x560 (IsChild) tells them apart. Fields are floats at the offsets the reader writes
// them to (main+0x3009910: GroundSpeedScale +0x2a0, JumpMaxInitSpeed +0x338, RollingMaxSpeed +0x3e8, SwimDashSpeedMps
// +0x4c4, SwimDiveSpeedMaxMps +0x4e0, SwimMaxSpeedMps +0x4f4, ...).
#define PARAM_IS_CHILD 0x560
#define PARAM_SIZE 0x570
enum { PARAM_BLOCKS = 4 };
static _Atomic uint64_t param_block[PARAM_BLOCKS];
static _Atomic int param_next;

void dbg_player_param(uint64_t block) {
    for (int i = 0; i < PARAM_BLOCKS; i++)
        if (atomic_load(&param_block[i]) == block) return;
    atomic_store(&param_block[atomic_fetch_add(&param_next, 1) % PARAM_BLOCKS], block);
}

// The block of the player's current age, 0 when none is known.
uint64_t dbg_player_param_block(void) {
    int child = dbg_is_child();
    if (child < 0) return 0;
    uint64_t best = 0;
    for (int k = 0; k < PARAM_BLOCKS; k++) {   // the newest first
        int i = ((atomic_load(&param_next) - 1 - k) % PARAM_BLOCKS + PARAM_BLOCKS) % PARAM_BLOCKS;
        uint64_t b = atomic_load(&param_block[i]);
        uint8_t is_child;
        if (b && plat_mem_read(b + PARAM_IS_CHILD, &is_child, 1) && (is_child != 0) == (child != 0)) return b;
        if (b && !best) best = b;
    }
    (void)best;
    return 0;
}

int dbg_player_param_get(uint32_t off, float *v) {
    uint64_t b = dbg_player_param_block();
    return b && off + 4 <= PARAM_SIZE && plat_mem_read(b + off, v, 4);
}

int dbg_player_param_set(uint32_t off, float v) {
    uint64_t b = dbg_player_param_block();
    if (!b || off + 4 > PARAM_SIZE || !isfinite(v)) return 0;
    plat_mem_prepare_write(b + off, 4);
    memcpy((void *)(uintptr_t)(b + off), &v, 4);
    return 1;
}

// ---- Mask abilities
// What the worn mask does (the string flag PlayerStatus.EquipHeadArmor names the head armour on), read four times a
// second. Running is the run animation's own motion, not a speed parameter, so the Bunny Hood runs faster by playing the
// player's animations faster (the AS component's rate, ANIM_RATE) while the player moves along the ground; it also jumps
// further (JumpMaxInitSpeed, StepJumpForwardSpeedMaxMps). A roll is the animation's motion too: the Goron Mask plays it
// twice as fast while the body layer is in its Rolling state. The Zora Mask swims and dives fast through the player's
// game parameters (dbg_player_param_set), as do the Goron Mask's rolling fields: each is scaled from the value it had, and that value
// is put back when the mask comes off, the option goes off or the block changes (a new save). Nothing changes in events.
#define ANIM_RATE 0x98                  // the AS component's playback rate, 1 normally
#define BUNNY_MOVING_MS 0.5f            // ground speed above which the faster animation plays
#define GORON_ROLL_RATE 2.0f            // a roll's animation rate under the Goron Mask
enum { MASK_NONE, MASK_BUNNY, MASK_ZORA, MASK_GORON };
static const struct {
    int mask;
    uint16_t off;   // the field in the player's game parameters
    float scale;    // 0: the Bunny speed
} mask_fields[] = {
    {MASK_BUNNY, 0x338, 0}, {MASK_BUNNY, 0x4a8, 0},                              // JumpMaxInitSpeed, StepJumpForwardSpeedMaxMps
    {MASK_ZORA, 0x4f4, 3.0f}, {MASK_ZORA, 0x4c4, 2.5f}, {MASK_ZORA, 0x4bc, 2.5f}, // SwimMaxSpeedMps, SwimDashSpeedMps, SwimDashHeavySpeedMps
    {MASK_ZORA, 0x4e0, 2.5f}, {MASK_ZORA, 0x4e4, 2.5f},                          // SwimDiveSpeedMaxMps, SwimDiveSpeedMinMps
    {MASK_ZORA, 0x4f8, 2.0f}, {MASK_ZORA, 0x4f0, 2.0f}, {MASK_ZORA, 0x520, 2.0f}, // SwimMaxUpSppedMps, SwimMaxDownSppedMps, SwimVerticalDashSpeed
    {MASK_ZORA, 0x4fc, 2.0f}, {MASK_ZORA, 0x500, 1.5f},                          // SwimSpeedMpsFollowStream, SwimSpeedMpsReverseStream
    {MASK_GORON, 0x3e8, 2.5f}, {MASK_GORON, 0x3f4, 2.5f}, {MASK_GORON, 0x350, 2.5f}, // RollingMaxSpeed, RollingMinSpeed, LockOnRollingMaxSpeed
};
enum { MASK_FIELDS = sizeof mask_fields / sizeof mask_fields[0] };
static uint32_t k_head, k_event;
static int mask_worn, mask_applied;           // the mask on, the one whose fields are scaled now
static uint64_t mask_checked, mask_block;     // when the flag was read; the block mask_orig came from
static float mask_orig[MASK_FIELDS], mask_bunny_applied;
static struct { uint64_t actor, comps, as; float written; } rate;   // the player's AS component and the rate put there

void dbg_mask_powers(int on) {
    move_load();
    atomic_store(&mask_powers_on, on != 0);
    dbg_keep("OOTPC_DEBUG_MASK_POWERS", on ? "1" : "0");
}
int dbg_mask_powers_on(void) { move_load(); return atomic_load(&mask_powers_on); }

static int mask_of(const char *head) {
    if (!strcmp(head, "Armor_Child_Mask_Bunny")) return MASK_BUNNY;
    if (!strcmp(head, "Armor_Child_Mask_Zora")) return MASK_ZORA;
    if (!strcmp(head, "Armor_Child_Mask_Goron")) return MASK_GORON;
    return MASK_NONE;
}
static int mask_enabled(int m) {
    return m == MASK_BUNNY ? atomic_load(&bunny_on) : m != MASK_NONE && atomic_load(&mask_powers_on);
}

// Puts the scaled fields back to what they were (only into the block they were read from).
static void mask_restore(void) {
    if (mask_applied && mask_block && mask_block == dbg_player_param_block())
        for (int i = 0; i < MASK_FIELDS; i++)
            if (mask_fields[i].mask == mask_applied) dbg_player_param_set(mask_fields[i].off, mask_orig[i]);
    mask_applied = 0, mask_block = 0;
}

static void mask_apply(int m) {
    uint64_t block = dbg_player_param_block();
    float bf = atomic_load(&bunny_factor);
    if (m == mask_applied && block == mask_block && (m != MASK_BUNNY || bf == mask_bunny_applied)) return;
    if (block != mask_block) mask_applied = 0;   // a new block holds the game's own values
    mask_restore();
    if (!m || !block) return;
    for (int i = 0; i < MASK_FIELDS; i++) {
        if (mask_fields[i].mask != m || !dbg_player_param_get(mask_fields[i].off, &mask_orig[i])) continue;
        dbg_player_param_set(mask_fields[i].off, mask_orig[i] * (mask_fields[i].scale ? mask_fields[i].scale : bf));
    }
    mask_applied = m, mask_block = block, mask_bunny_applied = bf;
}

// The player's AS component (rate.as), found again for a new player; 0 when there is none.
static uint64_t rate_find(void) {
    uint64_t actor = dbg_player_actor();
    if (!actor) return 0;
    if (actor != rate.actor || rd64(actor + ACTOR_COMPS) != rate.comps || !rate.as) {
        char name[96];
        uint64_t arr = rd64(actor + ACTOR_COMPS);
        uint32_t n = 0;
        rate.actor = actor, rate.comps = arr, rate.as = 0, rate.written = 0;
        if (!arr || !plat_mem_read(actor + ACTOR_COMP_COUNT, &n, 4) || n > 1024) return 0;
        for (uint32_t i = 0; i < n && !rate.as; i++) {
            uint64_t c = rd64(arr + 8ull * i);
            if (c && comp_named(c, ".root.asb", name, sizeof name)) rate.as = c;
        }
    }
    return rate.as;
}

// The body's animation layer (the owner's first) is in the state named so, in either of its two slots (the state
// being left and the one being entered): Wait, Move, Rolling, ...
static int body_state_is(const char *state) {
    uint64_t as = rate_find(), owner = as ? rd64(as + AS_OWNER) : 0, l = owner ? rd64(owner + OWNER_LAYERS) : 0;
    if (!l || rd64(l) != MOD_MAIN_BASE + LAYER_CLASS) return 0;
    for (int s = 0; s < 2; s++) {
        char nm[24] = "";
        uint64_t p = rd64(l + LAYER_STATE_NAMES + 8ull * s);
        if (p && plat_mem_read(p, nm, sizeof nm - 1) && !strcmp(nm, state)) return 1;
    }
    return 0;
}

// The player's animation rate: want, put back to 1 only where it is still the rate this code wrote.
static void rate_set(float want) {
    if (!rate_find()) return;
    float cur;
    if (!plat_mem_read(rate.as + ANIM_RATE, &cur, 4) || cur == want) return;
    if (cur != 1.0f && cur != rate.written) return;   // the game's own rate
    plat_mem_prepare_write(rate.as + ANIM_RATE, 4);
    memcpy((void *)(uintptr_t)(rate.as + ANIM_RATE), &want, 4);
    rate.written = want;
}

static void mask_frame(uint64_t now) {
    if (!k_head) k_head = dbg_flag_key("PlayerStatus.EquipHeadArmor"), k_event = dbg_flag_key("Sequence_ControlInEvent");
    if (now - mask_checked > 250000000ull) {
        char head[64];
        mask_worn = dbg_flag_get_string(k_head, head) ? mask_of(head) : MASK_NONE;
        if (!mask_enabled(mask_worn)) mask_worn = MASK_NONE;
        mask_checked = now;
        mask_apply(mask_worn);
    }
    float want = 1.0f;
    if (mask_worn && dbg_flag_get_bool(k_event) != 1) {
        if (mask_worn == MASK_BUNNY && dbg_ground_speed() > BUNNY_MOVING_MS) want = atomic_load(&bunny_factor);
        else if (mask_worn == MASK_GORON && body_state_is("Rolling")) want = GORON_ROLL_RATE;
    }
    if (want != 1.0f || (rate.written != 0 && rate.written != 1.0f)) rate_set(want);
}

void dbg_move_frame(struct Cpu *c) {
    uint64_t now = plat_now_ns();
    float dt = last_ns ? (float)(now - last_ns) * 1e-9f : 0.0f;
    last_ns = now;
    if (dt > FRAME_DT_MAX) dt = 0.0f;
    ragdoll_frame(c, dt);
    boots_frame();
    bomb_frame();
    mask_frame(now);
    float p[3];
    if (!dbg_player_pos(p)) {
        air_mode = 0;
        return;
    }

    // Which mode holds the player this frame: fly wins over hover.
    uint64_t b = 0;
    int32_t lx = 0, ly = 0, rx = 0, ry = 0;
    int mode = atomic_load(&fly_on) ? AIR_FLY : atomic_load(&hover_on) ? AIR_HOVER : 0;
    if (!mode) {
        air_mode = 0;
        return;
    }
    if (mode != air_mode) memcpy(air_pos, p, sizeof air_pos), air_mode = mode;
    input_poll_ui(&b, &lx, &ly, &rx, &ry);
    air_step(c, p, b, lx, ly, mode == AIR_FLY ? dbg_fly_speed() : WALK_MS, mode == AIR_FLY, dt);
}
