// Dynamic resolution from the GPU's own timestamps: the scene's resolution follows how long the GPU takes per frame,
// so a heavy view renders fewer pixels and keeps the frame time, and a light one goes back to the player's choice. The
// scene size is the one the game composes in (resolution.c), and it moves in the steps DLSS draws from: DLAA (the
// output's own size), quality, balanced, performance and ultra performance, 1, 2/3, 0.58, 1/2 and 1/3 of the output on
// each axis.
//
// OOTPC_DYN_RES=1 turns it on (off by default: it changes the picture's sharpness). While DLSS is on (OOTPC_DLAA) and
// OOTPC_DLSS is DLAA or one of those upscaling modes, that mode is the sharpest step it uses; the lowest is
// performance, or ultra performance when that is the player's own mode. Without DLSS, in a game whose scene share can
// be set (widescreen.c, Ocarina), it steps the scene's share of the picture instead: the player's share times 1,
// 0.875, 0.75, 0.66, 0.58 or 0.5 (dynres_plain_factor), which the picture's upscaling stand-in enlarges
// (vk_tonemap.c); the game's own dynamic resolution (a share of 0) is left to itself. While it runs it asks for GPU timestamps
// (perf_gpu_timing_want; vk_sync.c times every submission part) and reads their running total (vk_gpu_busy_all) once
// per presented frame. A frame's cost is the larger of the GPU's busy time and the time between presents less the
// frame pacer's sleeps: while the CPU waits for the GPU (the frame's first submission waits for the last frame's GPU
// work), the GPU alone stays under the target although the frame does not. It averages the cost and moves one step at
// a time: down when the average is over
// OOTPC_DYN_RES_TARGET milliseconds (default 0: one drawn frame at the rate the frame-rate limit asks the game to draw,
// frame generation's factor taken out, dynres_target) by 8 %, up when it is under 75 % of it, and holds
// each step for OOTPC_DYN_RES_STEP frames (default 45), because a size that changes every few frames is worse to look
// at than a steady one. The step in use goes to vk_dlaa.c dlss_mode as an override (dynres_override), which applies it
// as it applies the menu's choice; the player's setting itself never changes.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cfg.h"
#include "gfx.h"
#include "host.h"
#include "vk.h"
#include "vk_dlssg.h"

#define DYN_LEVELS 5
#define DYN_PERFORMANCE 3
static const char *const DYN_MODE[DYN_LEVELS] = {"dlaa", "quality", "balanced", "performance", "ultra_performance"};
#define PLAIN_LEVELS 6
static const double PLAIN_FACTOR[PLAIN_LEVELS] = {1.0, 0.875, 0.75, 0.66, 0.58, 0.5};
static const char *const PLAIN_NAME[PLAIN_LEVELS] = {"100 %", "87.5 %", "75 %", "66 %", "58 %", "50 %"};

static struct {
    int on;               // dynamic resolution is acting now
    int plain;            // ... on the scene share (no DLSS): the levels are PLAIN_FACTOR's
    int base, low;        // the player's step (the sharpest used) and the lowest one used
    int level;            // the step in use
    int settle;           // frames the step has been held
    int step;             // frames to hold a step
    double avg_ns;        // moving average of a frame's cost (dynres_frame)
    double target_ns;
    int target_auto;      // OOTPC_DYN_RES_TARGET 0: the target follows the limit and frame generation (dynres_target)
    uint64_t last_busy;   // vk_gpu_busy_all at the previous presented frame
    uint64_t last_at, last_slept;   // that frame's time, and the pacer's sleeps by then (gfx_pace_slept_ns)
    unsigned cfg_seen;    // cfg_version the settings were read at
    char base_mode[32];
} D = {.cfg_seen = ~0u};

// The automatic target: one drawn frame at the rate the frame-rate limit asks the game to draw (frame_rate.c
// frame_limit_draw_hz), which takes frame generation's factor out: with 120 on screen at 2x a drawn frame has 16.7 ms.
// Aimed at the screen's rate instead, the scene dropped to DLSS performance where DLAA held the 60 drawn frames
// the picture needed. Unlimited: one frame at the monitor's rate over that factor.
static double dynres_target(void) {
    double hz = frame_limit_draw_hz();
    // frame generation set but off for the moment (a loading screen): the target it will have again, not the screen's
    if (hz >= 20 && vk_dlssg_wanted() && vk_dlssg_multiplier() < 2) hz /= 2;
    if (hz < 20) {
        double mon = gfx_window_refresh_hz();
        int gen = vk_dlssg_multiplier();
        hz = (mon >= 30 ? mon : 60) / (gen > 1 ? gen : 1);
    }
    return 1e9 / hz;
}

static int mode_index(const char *m) {
    for (int i = 0; i < DYN_LEVELS; i++)
        if (m && !strcmp(m, DYN_MODE[i])) return i;
    return -1;
}

// The settings, again whenever one changed (the menu switches them live).
static void dynres_read(void) {
    D.cfg_seen = cfg_version();
    const char *m = cfg_value("OOTPC_DLSS");
    int want = cfg_bool("OOTPC_DYN_RES"), base = mode_index(m);
    double ms = cfg_double("OOTPC_DYN_RES_TARGET");
    D.target_auto = ms < 1;
    D.target_ns = D.target_auto ? dynres_target() : ms * 1e6;
    D.step = cfg_int("OOTPC_DYN_RES_STEP");
    if (D.step < 8) D.step = 8;
    int dlss = base >= 0 && cfg_bool("OOTPC_DLAA"), plain = !dlss && wide_scene_settable();
    int on = want && (dlss || plain);
    char mode[40];
    snprintf(mode, sizeof mode, "%s%s", plain ? "plain " : "", m);   // a change of either restarts at the top step
    if (on && (!D.on || strcmp(mode, D.base_mode))) {   // (re)start at the player's step
        D.plain = plain;
        D.base = D.level = plain ? 0 : base;
        D.low = plain ? PLAIN_LEVELS - 1 : base > DYN_PERFORMANCE ? base : DYN_PERFORMANCE;
        D.settle = 0, D.avg_ns = 0, D.last_busy = vk_gpu_busy_all(), D.last_at = 0;
        if (plain)
            rt_log("resolution: dynamic resolution on: target %.1f ms, the scene share from %s down to %s",
                   D.target_ns / 1e6, PLAIN_NAME[0], PLAIN_NAME[D.low]);
        else
            rt_log("resolution: dynamic resolution on: target %.1f ms, DLSS %s down to %s", D.target_ns / 1e6,
                   DYN_MODE[D.base], DYN_MODE[D.low]);
    } else if (!on && D.on) {
        rt_log("resolution: dynamic resolution off: the resolution as set");
    } else if (want && !on) {
        static int said;
        if (!said++) rt_log("resolution: dynamic resolution needs DLSS (dlaa, quality, balanced, performance or ultra "
                            "performance) or a game whose scene share can be set; DLSS is %s", m);
    }
    snprintf(D.base_mode, sizeof D.base_mode, "%s", mode);
    D.on = on;
    perf_gpu_timing_want(on);
}

const char *dynres_override(void) { return D.on && !D.plain && D.level != D.base ? DYN_MODE[D.level] : NULL; }

double dynres_plain_factor(void) { return D.on && D.plain ? PLAIN_FACTOR[D.level] : 1.0; }

static const char *level_name(int l) { return D.plain ? PLAIN_NAME[l] : DYN_MODE[l]; }

// Once per presented frame, before vk_dlaa.c reads the mode.
void dynres_frame(void) {
    if (cfg_version() != D.cfg_seen) dynres_read();
    if (!D.on) return;
    uint64_t now = vk_gpu_busy_all(), d = now - D.last_busy;
    D.last_busy = now;
    uint64_t at = kern_now_ns(), slept = gfx_pace_slept_ns(), wall = D.last_at ? at - D.last_at : 0,
             s = slept - D.last_slept;
    D.last_at = at, D.last_slept = slept;
    if (!d || d > 1000000000ull) return;   // no timing this frame yet, or a stall that says nothing about the scene
    uint64_t work = wall > s ? wall - s : 0;
    if (work > 1000000000ull) work = 0;    // a pause (menu, load)
    double cost = (double)(work > d ? work : d);
    D.avg_ns = D.avg_ns > 0 ? D.avg_ns * 0.9 + cost * 0.1 : cost;
    static int frames, said;
    if (++frames % 600 == 0 && said < 12) {
        said++;
        rt_log("resolution: dynamic resolution: %.2f ms a frame, %s %s, target %.2f ms", D.avg_ns / 1e6,
               D.plain ? "scene share" : "DLSS", level_name(D.level), D.target_ns / 1e6);
    }
    if (D.target_auto) D.target_ns = dynres_target();   // the multiplier and the limit change while it runs
    if (++D.settle < D.step) return;
    int to = D.level;
    if (D.avg_ns > D.target_ns * 1.08 && D.level < D.low) to = D.level + 1;
    else if (D.avg_ns < D.target_ns * 0.75 && D.level > D.base) to = D.level - 1;
    if (to == D.level) return;
    rt_log("resolution: dynamic resolution: %.2f ms a frame against %.2f ms: %s %s -> %s", D.avg_ns / 1e6,
           D.target_ns / 1e6, D.plain ? "scene share" : "DLSS", level_name(D.level), level_name(to));
    D.level = to, D.settle = 0;
}
