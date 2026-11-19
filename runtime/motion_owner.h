#pragma once
#include <stdint.h>

// The model shapes' bone buffers known per frame: the player's and, with N64 Graphics, every character it stands in
// for (a room of statues holds a few hundred shapes)
#define MOTION_OWNER_BINDINGS 2048
#define MOTION_OWNER_RIGID_EQUIPMENT 1u
#define MOTION_OWNER_TRANSFORM 2u   // a transformation mask: its body stands in for the player's (motion_owner_transformed)
#define MOTION_OWNER_ZORA 4u        // with MOTION_OWNER_TRANSFORM: the Zora body (otherwise the Goron's)
#define MOTION_OWNER_PREVIEW 8u     // the pause menu's player preview (motion_owner_set_preview), not the player
// What N64 Link keeps of the remake's equipment while he stands in (vk_exec_draw.c): an item without an N64 model
// ("Item_*" such as the Lens of Truth "Armor_Glass*"), and, flagged MOTION_OWNER_N64_ITEM too, an item or piece of
// item that N64 Graphics draws itself while its items part is on (n64pose_items; n64pose.c read_gear: the ocarinas,
// the hand items, the hammer, boomerang and bottles, the Deku stick, the N64 game's eight masks), kept otherwise. The
// rest of the player's equipment (sword, shield, sheath, tunics) goes while N64 Link stands in.
#define MOTION_OWNER_KEPT 16u
#define MOTION_OWNER_N64_ITEM 32u
// Boots and gauntlets: drawn by N64 Link with his body when his file has them (n64pose_wear), not with the items.
#define MOTION_OWNER_N64_WEAR 64u
// A character's model that N64 Graphics draws as the N64 version's (n64actors.h): not the player's (kind
// MOTION_OWNER_STANDIN_KIND, no actor), tracked only so that vk_exec_draw.c can tell its draws.
#define MOTION_OWNER_N64_ACTOR 128u
#define MOTION_OWNER_STANDIN_KIND 300u
enum { MOTION_TRANSFORM_NONE, MOTION_TRANSFORM_GORON, MOTION_TRANSFORM_ZORA };
typedef struct {
    uint64_t player, actor, link, model, part, lifetime;
    uint32_t generation, kind, flags;
} MotionOwner;
typedef struct {
    uint64_t address, size, observed, buffer;
    MotionOwner owner;
    int ambiguous;
} MotionOwnerBinding;
typedef struct {
    MotionOwnerBinding bindings[MOTION_OWNER_BINDINGS];
    unsigned count;
} MotionOwnerSnapshot;

struct Cpu;
// Record the model responsible for an address query in a recognized model-binding call site.
void motion_owner_observe(struct Cpu *cpu, uint64_t address, uint64_t size);
// Capture identities alongside the submission's uniform bytes, before asynchronous replay.
void motion_owner_capture(MotionOwnerSnapshot *out);
void motion_owner_present(void);
void motion_owner_forget(uint64_t buffer);
void motion_owner_reset(void);
void motion_owner_forget_range(uint64_t address, uint64_t size);
int vk_usnap_motion_owner(uint64_t address, uint64_t size, MotionOwner *out);
int motion_owner_enabled(void);    // the player's models are tracked (selective motion, the debug menu's Hide meshes, N64 Graphics)
// The player's models as last tracked: its body, then each equipment slot's (kind = slot + 1), then the pause menu
// preview's body and equipment. Returns how many, at most MOTION_OWNER_MODELS.
#define MOTION_OWNER_MODELS 256
int motion_owner_models(MotionOwner *out, int max);
int motion_owner_selective(void);  // OOTPC_SELECTIVE_MOTION: their draws are drawn again into the motion vectors
// A transformation (the Transformations mod, enabled at the start, gives the Goron and Zora Masks a whole Goron or Zora
// body bound to the player's bones): while one of those masks is worn, the player's own draws and its other
// equipment's are skipped, the mask's body stays (vk_exec_draw.c), and the body's skeleton gets its own bone lengths
// back (dbg_lank_before). The worn body (MOTION_TRANSFORM_*), as of the last tracked frame.
int motion_owner_transformed(void);
uint64_t motion_owner_transform_model(void);  // the worn body's model, 0 when none
// The pause menu's player preview actor ("PouchPlayer_Child", found by a helper thread: debug_transform.c), so a
// transformation can hide its player body too. Its models are tracked with MOTION_OWNER_PREVIEW. 0 forgets it.
void motion_owner_set_preview(uint64_t actor);
uint64_t motion_owner_preview(void);  // the preview actor while it still holds that name, else 0
// Actors that may be the preview's equipment (debug_transform.c, from a heap scan): those hung from the preview actor
// are tracked with it, MOTION_OWNER_PREVIEW and their equipment flags, kind MOTION_OWNER_PREVIEW_KIND.
#define MOTION_OWNER_PREVIEW_GEAR 64
#define MOTION_OWNER_PREVIEW_KIND 100u
void motion_owner_set_preview_gear(const uint64_t *actors, int n);
// The items the game keeps as actors of their own rather than in the player's equipment slots, and hangs from him
// only while in use ("Item_Hammer", "Item_Boomerang", "Item_Bottle*"), as a heap scan of player's world found them
// (debug_transform.c). Each frame those that hang from him are tracked as his equipment (a kind past the slots').
#define MOTION_OWNER_PLAYER_ITEMS 24
void motion_owner_set_player_items(uint64_t player, const uint64_t *actors, int n);
// The models N64 Graphics stands in for this frame (n64actors_game_frame), tracked after the player's.
#define MOTION_OWNER_STANDINS 160   // a room of statues holds about a hundred
void motion_owner_set_standins(const uint64_t *models, int n);
// 1 when actor hangs from parent (its attachment chain, a few links deep), as the preview's equipment does.
int motion_owner_hangs_from(uint64_t actor, uint64_t parent);
