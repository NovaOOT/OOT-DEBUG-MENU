// Summoning actors at the player (debug_tools.h, "Summoning"): any number of one actor, made a few at a time.
//
// dbg_summon_request asks for count actors of one name; the frame hook (dbg_summon_frame, from dbg_transform_frame)
// sends up to SEND_PER_FRAME creations a frame through the game's actor manager (dbg_frame_create) while fewer than
// IN_FLIGHT are still loading, and puts each in the world once made (dbg_frame_create_poll). There is no upper limit on
// the count: the queue only holds how many are still to send. Each actor appears within SCATTER metres of the player,
// a little above the ground, so a pile of them does not start inside one point.
//
// The manager reads the name through the pointer it is given for as long as a creation loads, so a new name is only
// taken once every creation of the previous one has ended; until then the request waits.
#include <math.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "host.h"
#include "debug_tools.h"
#include "debug_watch.h"
#include "platform/platform.h"

#define IN_FLIGHT 32
#define SEND_PER_FRAME 8
#define WAIT_FRAMES 600     // a creation still loading after this many frames is given up
#define SCATTER 1.0f        // metres around the player
#define LIFT 0.3f           // metres above the player's feet
#define ACTOR_POS 0x31c
#define ACTOR_AXES 0x2f8

typedef struct {
    _Alignas(16) uint64_t handle;
    int frames;
    int used;
} Flight;

static Flight flights[IN_FLIGHT];
static int in_flight;
static char name[64];            // the name the creations in flight read
static uint64_t name_ptr;
static uint32_t rng = 0x9e3779b9u;

// The request, from any thread: a name and a count waiting for the game thread to take them.
static PlatMutex req_lock = PLAT_MUTEX_INIT;
static char req_name[64];
static int64_t req_count;
static int req_new;

static _Atomic int64_t left;     // still to send
static _Atomic int64_t total, made, failed;
static _Atomic int stop_req;

int dbg_summon_request(const char *what, int64_t count) {
    if (!what || !*what || strlen(what) >= sizeof req_name || count <= 0) return 0;
    plat_mutex_lock(&req_lock);
    snprintf(req_name, sizeof req_name, "%s", what);
    req_count = count;
    req_new = 1;
    plat_mutex_unlock(&req_lock);
    dbg_watch_event("tool    summon %lld %s", (long long)count, what);
    return 1;
}

void dbg_summon_stop(void) {
    plat_mutex_lock(&req_lock);
    req_new = 0;
    plat_mutex_unlock(&req_lock);
    atomic_store(&stop_req, 1);
}

int dbg_summon_status(DbgSummon *s) {
    plat_mutex_lock(&req_lock);
    int waiting = req_new;
    plat_mutex_unlock(&req_lock);
    s->total = atomic_load(&total);
    s->made = atomic_load(&made);
    s->failed = atomic_load(&failed);
    s->left = atomic_load(&left);
    s->loading = in_flight;
    snprintf(s->name, sizeof s->name, "%s", name);
    return waiting || s->left > 0 || in_flight > 0;
}

static float frand(void) {   // 0..1
    rng ^= rng << 13, rng ^= rng >> 17, rng ^= rng << 5;
    return (rng >> 8) * (1.0f / 16777216.0f);
}

static void poll_all(Cpu *c) {
    for (int i = 0; i < IN_FLIGHT; i++) {
        Flight *f = &flights[i];
        if (!f->used) continue;
        uint64_t actor = 0;
        int r = dbg_frame_create_poll(c, &f->handle, &actor, ++f->frames > WAIT_FRAMES);
        if (r == 0) continue;
        if (r > 0) atomic_fetch_add(&made, 1);
        else atomic_fetch_add(&failed, 1);
        f->used = 0, f->handle = 0;
        in_flight--;
    }
}

static void send_some(Cpu *c) {
    uint64_t actor = dbg_player_actor();
    float p[3], ax[9];
    if (!actor || !plat_mem_read(actor + ACTOR_POS, p, sizeof p) || !plat_mem_read(actor + ACTOR_AXES, ax, sizeof ax))
        return;   // no player (a load): wait for one
    for (int sent = 0; sent < SEND_PER_FRAME && in_flight < IN_FLIGHT && atomic_load(&left) > 0; sent++) {
        int i = 0;
        while (flights[i].used) i++;
        Flight *f = &flights[i];
        const float r = SCATTER * sqrtf(frand()), a = 6.2831853f * frand();
        const float pos[3] = {p[0] + r * cosf(a), p[1] + LIFT, p[2] + r * sinf(a)};
        f->handle = 0;
        if (!dbg_frame_create(c, &name_ptr, pos, ax, &f->handle)) {
            // the manager refused: a name it does not know refuses every time, so the rest is dropped
            atomic_fetch_add(&failed, atomic_exchange(&left, 0));
            rt_log("summon: the manager refused %s", name);
            return;
        }
        f->used = 1, f->frames = 0;
        in_flight++;
        atomic_fetch_sub(&left, 1);
    }
}

void dbg_summon_frame(Cpu *c) {
    if (atomic_exchange(&stop_req, 0)) atomic_store(&left, 0);
    if (in_flight) poll_all(c);
    if (!in_flight) {   // the name is free to change
        plat_mutex_lock(&req_lock);
        if (req_new) {
            req_new = 0;
            snprintf(name, sizeof name, "%s", req_name);
            name_ptr = (uint64_t)(uintptr_t)name;
            atomic_store(&total, req_count);
            atomic_store(&made, 0);
            atomic_store(&failed, 0);
            atomic_store(&left, req_count);
            rt_log("summon: %lld %s", (long long)req_count, name);
        }
        plat_mutex_unlock(&req_lock);
    }
    if (atomic_load(&left) > 0) send_some(c);
}
