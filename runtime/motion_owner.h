#pragma once
#include <stdint.h>

#define MOTION_OWNER_BINDINGS 512
#define MOTION_OWNER_RIGID_EQUIPMENT 1u
#define MOTION_OWNER_TRANSFORM 2u   // a transformation mask: its body stands in for the player's (motion_owner_transformed)
#define MOTION_OWNER_ZORA 4u        // with MOTION_OWNER_TRANSFORM: the Zora body (otherwise the Goron's)
#define MOTION_OWNER_PREVIEW 8u     // the pause menu's player preview (motion_owner_set_preview), not the player
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
int motion_owner_enabled(void);    // the player's models are tracked (selective motion, or the debug menu's Hide meshes)
int motion_owner_selective(void);  // NX2NAVI_SELECTIVE_MOTION: their draws are drawn again into the motion vectors
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
