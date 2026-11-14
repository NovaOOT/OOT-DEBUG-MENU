// Spawning actors at run time (debug_tools.h, "Spawning actors"): console spawn.
//
// The game makes an actor through its actor manager (game 1.1.1: 0x7000372a70), the call the AI node that creates
// actors makes (its enter function, 0x70026e62b0). Its arguments, as that node passes them:
//   x0  the manager: [[MANAGER_GLOBAL]]
//   x1  a pointer to the actor's name ("Npc_Goron_GON"); the manager adds "Pack/Actor/" itself
//   x2  the creation record (CreateArg below)
//   x3  an 8-byte handle, zero to start with; the manager points it at a state whose u16 tells how the creation went
//       (3: the actor is made, 4: it failed; 0: still loading)
//   x4  1, x5 to x7 0
// and words on the caller's stack, which the manager reads: a byte 0 at sp+0, at sp+8 an optional pointer to a u32 the
// manager sets to why it refused (1: the manager is not taking actors now, 2: no name, others further in), and 0 at
// sp+0x10. A call from the frame hook puts them below the game thread's stack pointer for the length of the call.
//
// A made actor is not in the world yet: the AI node then takes it from the handle (0x7003aaefd0: handle, 1, 0), which
// with 1 wakes the actor and adds it to the actor manager's lists, and clears the handle. Until then the actor exists
// but is neither updated nor drawn. The frame hook polls the handle and takes the actor the same way.
#include <math.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "host.h"
#include "motion_owner.h"
#include "save_tree.h"
#include "debug_tools.h"
#include "platform/platform.h"

#define ACTOR_CREATE 0x7000372a70ull
#define ACTOR_TAKE 0x7003aaefd0ull      // handle, 1, 0 -> the actor, now in the world
#define HANDLE_RELEASE 0x7000b2fbc0ull  // handle: drops a failed creation
#define HANDLE_MADE 3
#define HANDLE_FAILED 4
#define SPAWN_WAIT_FRAMES 600
#define MANAGER_GLOBAL 0x7004c7f3e0ull
#define DEFAULT_SCALE 0x7004c7e0b8ull   // points to the 12-byte scale the node copies (1, 1, 1)
#define ACTOR_POS 0x31c
#define ACTOR_AXES 0x2f8

typedef struct {
    float pos[3];        // +0x00
    float rot[9];        // +0x0c: rotation, 3x3
    float scale[3];      // +0x30
    uint32_t pad3c;
    uint64_t instance;   // +0x40: const char *, the instance name ("" for none)
    uint8_t zero[0x50];  // +0x48 .. +0x97
    uint8_t kind;        // +0x98: 3, as the node sets it
    uint8_t pad99[7];
} CreateArg;
_Static_assert(sizeof(CreateArg) == 0xa0, "the node's creation record is 0xa0 bytes");

enum {
    SPAWN_IDLE = DBG_SPAWN_IDLE,
    SPAWN_QUEUED = DBG_SPAWN_QUEUED,
    SPAWN_SENT = DBG_SPAWN_SENT,
    SPAWN_DONE = DBG_SPAWN_DONE,
    SPAWN_FAILED = DBG_SPAWN_FAILED
};
static _Atomic int spawn_state;
static char spawn_name[64];
static float spawn_off[2];
static _Alignas(16) CreateArg spawn_arg;
static uint64_t spawn_name_ptr;
static _Alignas(16) uint64_t spawn_handle;
static uint64_t spawn_result;
static uint32_t spawn_error;
static uint64_t spawn_actor;
static int spawn_frames;
static const char kNoInstance[] = "";

int dbg_spawn_request(const char *name, float dx, float dz) {
    int idle = SPAWN_IDLE;
    if (!name || !*name || strlen(name) >= sizeof spawn_name || !dbg_player_actor()) return 0;
    int done = atomic_load(&spawn_state);
    if (done == SPAWN_DONE || done == SPAWN_FAILED) atomic_compare_exchange_strong(&spawn_state, &done, SPAWN_IDLE);
    if (!atomic_compare_exchange_strong(&spawn_state, &idle, SPAWN_QUEUED + 100)) return 0;
    snprintf(spawn_name, sizeof spawn_name, "%s", name);
    spawn_off[0] = dx, spawn_off[1] = dz;
    atomic_store(&spawn_state, SPAWN_QUEUED);
    return 1;
}

int dbg_spawn_status(uint64_t *actor, uint32_t *error) {
    *actor = spawn_actor;
    *error = spawn_error;
    return atomic_load(&spawn_state);
}

static void spawn_run(Cpu *c) {
    uint64_t actor = dbg_player_actor();
    float p[3], ax[9];
    if (!actor || !plat_mem_read(actor + ACTOR_POS, p, sizeof p) || !plat_mem_read(actor + ACTOR_AXES, ax, sizeof ax)) {
        atomic_store(&spawn_state, SPAWN_IDLE);
        return;
    }
    uint64_t mgr_ptr = 0, mgr = 0, scale = 0;
    if (!plat_mem_read(MANAGER_GLOBAL, &mgr_ptr, 8) || !mgr_ptr || !plat_mem_read(mgr_ptr, &mgr, 8) || !mgr ||
        !plat_mem_read(DEFAULT_SCALE, &scale, 8) || !scale) {
        atomic_store(&spawn_state, SPAWN_IDLE);
        return;
    }
    memset(&spawn_arg, 0, sizeof spawn_arg);
    // the offset is in the player's frame: dx to the right, dz ahead (the facing is the third axis column)
    spawn_arg.pos[0] = p[0] + ax[0] * spawn_off[0] + ax[6] * spawn_off[1];
    spawn_arg.pos[1] = p[1];
    spawn_arg.pos[2] = p[2] + ax[2] * spawn_off[0] + ax[8] * spawn_off[1];
    memcpy(spawn_arg.rot, ax, sizeof ax);
    if (!plat_mem_read(scale, spawn_arg.scale, sizeof spawn_arg.scale)) {
        spawn_arg.scale[0] = spawn_arg.scale[1] = spawn_arg.scale[2] = 1;
    }
    spawn_arg.instance = (uint64_t)(uintptr_t)kNoInstance;
    spawn_arg.kind = 3;
    spawn_handle = 0;
    spawn_actor = 0;
    spawn_frames = 0;
    spawn_name_ptr = (uint64_t)(uintptr_t)spawn_name;

    uint64_t sp0 = c->sp, sp = (sp0 - 0x40) & ~15ull;
    memset((void *)(uintptr_t)sp, 0, 0x40);
    spawn_error = 0;
    *(uint64_t *)(uintptr_t)(sp + 8) = (uint64_t)(uintptr_t)&spawn_error;
    c->sp = sp;
    const uint64_t a[8] = {mgr, (uint64_t)(uintptr_t)&spawn_name_ptr, (uint64_t)(uintptr_t)&spawn_arg,
                           (uint64_t)(uintptr_t)&spawn_handle, 1, 0, 0, 0};
    spawn_result = dbg_frame_call(c, ACTOR_CREATE, a);
    c->sp = sp0;
    rt_log("debug spawn: %s at %.2f %.2f %.2f -> %#llx, handle %#llx, error %u", spawn_name, spawn_arg.pos[0],
           spawn_arg.pos[1], spawn_arg.pos[2], (unsigned long long)spawn_result, (unsigned long long)spawn_handle,
           spawn_error);
    atomic_store(&spawn_state, spawn_handle ? SPAWN_SENT : SPAWN_FAILED);
}

// Takes the actor once the manager has made it (a few frames: its files load first).
static void spawn_poll(Cpu *c) {
    uint64_t st = 0;
    uint16_t s = 0;
    if (!(st = spawn_handle) || !plat_mem_read(st, &s, sizeof s)) {
        atomic_store(&spawn_state, SPAWN_FAILED);
        return;
    }
    if (s == HANDLE_MADE) {
        const uint64_t a[8] = {(uint64_t)(uintptr_t)&spawn_handle, 1, 0};
        spawn_actor = dbg_frame_call(c, ACTOR_TAKE, a);
        rt_log("debug spawn: %s is actor %#llx after %d frames", spawn_name, (unsigned long long)spawn_actor,
               spawn_frames);
        atomic_store(&spawn_state, spawn_actor ? SPAWN_DONE : SPAWN_FAILED);
    } else if (s == HANDLE_FAILED || ++spawn_frames > SPAWN_WAIT_FRAMES) {
        const uint64_t a[8] = {(uint64_t)(uintptr_t)&spawn_handle};
        dbg_frame_call(c, HANDLE_RELEASE, a);
        rt_log("debug spawn: %s failed (handle state %u, %d frames)", spawn_name, s, spawn_frames);
        atomic_store(&spawn_state, SPAWN_FAILED);
    }
}

// The pause menu's player preview, for motion_owner (motion_owner_set_preview). The game keeps it from the first time
// the menu opens; a heap scan finds it (a second or so, so a helper thread), again when it is gone, at most every
// PREVIEW_RETRY_FRAMES while a transformation is worn.
#define PREVIEW_NAME "PouchPlayer_Child"
#define PREVIEW_RETRY_FRAMES 600
static atomic_int preview_busy;
static void preview_find(void *arg) {
    (void)arg;
    uint64_t found = 0;
    motion_owner_set_preview(dbg_find_actors(PREVIEW_NAME, &found, 1) > 0 ? found : 0);
    atomic_store(&preview_busy, 0);
}
static void preview_frame(void) {
    static unsigned wait;
    if (!motion_owner_transformed() || motion_owner_preview()) return;
    if (wait) { wait--; return; }
    wait = PREVIEW_RETRY_FRAMES;
    int idle = 0;
    if (atomic_compare_exchange_strong(&preview_busy, &idle, 1) && !plat_thread_spawn(preview_find, NULL, "debug-preview", 0))
        atomic_store(&preview_busy, 0);
}

// ---- the game's own save to a slot, as its System page's save node does (SeqExecuteSaveGameData, its action
// 0x7000f8c8e0). The node finds the save component through the game root [[ROOT_GLOBAL]]: the root's array at +0x1f0
// (at least 13 entries, the entries at +0x58) holds at entry 12 the object whose first method returns a component by
// its type key (the variable [SAVE_TYPE_KEY], which holds [SAVE_TYPE_SOURCE] + 0x10 once the game first used it). When
// the component is idle (SAVE_IDLE returns 1; the node waits otherwise) it then sets a value by name hash on the object at
// [[SAVE_DATA_GLOBAL]] (SAVE_MARK, value 0) and asks the component to save the slot (0 to 5). The game writes the
// slot's files through its save worker over the next frames.
#define ROOT_GLOBAL 0x7004c7e178ull
#define SAVE_TYPE_KEY 0x7004c7e150ull
#define SAVE_TYPE_SOURCE 0x7004c7e160ull
#define SAVE_IDLE 0x7000f8cc10ull       // (save component) -> 1 when no save or load runs
#define SAVE_DATA_GLOBAL 0x7004c7e180ull
#define SAVE_MARK 0x7000ba6d10ull       // (data, 0, name hash)
#define SAVE_MARK_HASH 0x5e776346u
#define SAVE_TO_SLOT 0x70011d4910ull    // (save component, slot)
#define SAVE_AUTO 0x7000f8cb80ull       // (save component, 0): an autosave, to the slot the game plays
static atomic_int gsave_slot = -1;
int dbg_game_save_request(int slot) {
    int none = -1;
    return slot >= 0 && slot <= DBG_GAME_SAVE_AUTO && atomic_compare_exchange_strong(&gsave_slot, &none, slot);
}
static uint64_t rd64(uint64_t a) {
    uint64_t v = 0;
    return a && plat_mem_read(a, &v, 8) ? v : 0;
}
// The game's load of a slot, as its load node does (SeqExecuteLoadSaveData, its action 0x7000d64460): a message to the
// save system at [[LOAD_TARGET_GLOBAL]] with the slot (LOAD_POST), which reads the slot's save into the game's flags.
#define LOAD_TARGET_GLOBAL 0x7004c7fcd8ull
#define LOAD_POST 0x7000d645f0ull       // (save system, slot)
static atomic_int gload_slot = -1;
int dbg_game_load_request(int slot) {
    int none = -1;
    return slot >= 0 && slot < 6 && atomic_compare_exchange_strong(&gload_slot, &none, slot);
}
static void gload_run(Cpu *c) {
    int slot = atomic_exchange(&gload_slot, -1);
    if (slot < 0) return;
    uint64_t sys = 0, var = 0;
    if (!plat_mem_read(LOAD_TARGET_GLOBAL, &var, 8) || !var || !plat_mem_read(var, &sys, 8) || !sys) {
        rt_log("debug load: no save system");
        return;
    }
    uint64_t a[8] = {sys, (uint64_t)slot};
    dbg_frame_call(c, LOAD_POST, a);
    rt_log("debug load: slot %d requested (save system %#llx)", slot, (unsigned long long)sys);
}

static void gsave_run(Cpu *c) {
    int slot = atomic_exchange(&gsave_slot, -1);
    if (slot < 0) return;
    uint64_t root = rd64(rd64(ROOT_GLOBAL)), arr = rd64(root + 0x1f0), key = rd64(SAVE_TYPE_KEY);
    uint32_t n = 0;
    uint64_t holder = arr && plat_mem_read(arr + 0x50, &n, 4) && n >= 13 ? rd64(rd64(arr + 0x58) + 0x60) : 0;
    uint64_t get = rd64(rd64(holder)), data = rd64(rd64(SAVE_DATA_GLOBAL));
    if (!holder || !get || !key || !data) {
        rt_log("debug save: the save component is not there (root %#llx, holder %#llx, key %#llx)",
               (unsigned long long)root, (unsigned long long)holder, (unsigned long long)key);
        return;
    }
    uint64_t tv = 0;
    if (plat_mem_read(key, &tv, 8) && !tv) {   // the game sets it the same way on its first use
        tv = rd64(SAVE_TYPE_SOURCE) + 0x10;
        memcpy((void *)(uintptr_t)key, &tv, 8);
    }
    uint64_t a[8] = {holder, key};
    uint64_t comp = dbg_frame_call(c, get, a);
    if (!comp) {
        rt_log("debug save: no save component");
        return;
    }
    uint64_t i[8] = {comp};
    if (!(dbg_frame_call(c, SAVE_IDLE, i) & 1)) {
        rt_log("debug save: the game is busy saving or loading; slot %d not saved", slot);
        return;
    }
    if (slot == DBG_GAME_SAVE_AUTO) {   // the node's other target: what the game does when it autosaves
        uint64_t b[8] = {comp, 0};
        dbg_frame_call(c, SAVE_AUTO, b);
        rt_log("debug save: autosave requested (save component %#llx)", (unsigned long long)comp);
        return;
    }
    uint64_t m[8] = {data, 0, SAVE_MARK_HASH};
    dbg_frame_call(c, SAVE_MARK, m);
    uint64_t b[8] = {comp, (uint64_t)slot};
    dbg_frame_call(c, SAVE_TO_SLOT, b);
    rt_log("debug save: slot %d requested (save component %#llx)", slot, (unsigned long long)comp);
}

void dbg_transform_frame(Cpu *c) {
    gsave_run(c);
    gload_run(c);
    save_tree_tick();
    preview_frame();
    int st = atomic_load(&spawn_state);
    if (st == SPAWN_QUEUED) spawn_run(c);
    else if (st == SPAWN_SENT) spawn_poll(c);
}
