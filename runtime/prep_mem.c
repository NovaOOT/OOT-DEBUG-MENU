// The shader preparation's memory cap (prep_mem.h). The use is measured as what the system has in use now less what
// it had when the preparation began, plus this process's own commit then: the translator processes and the driver's
// compiler are counted with the workers that way without asking each of them. The larger of the commit's and the
// physical memory's growth counts. Something else on the PC that grows at the same time counts as well, which only
// makes the preparation slower while it does.
#include <stdint.h>
#include <stdio.h>

#include "cfg.h"
#include "host.h"
#include "platform/platform.h"
#include "prep_mem.h"

#define SAMPLE_NS 100000000ull   // the figures are read at most this often, by whichever worker asks first
#define WAIT_MS 50

static struct {
    PlatMutex lock;
    int on, running, waiting, over, critical, told;
    double cap_mb, own_mb, commit0_mb, phys0_mb, use_mb;
    uint64_t sampled_ns;
} M = {PLAT_MUTEX_INIT};

static void figures(double *commit, double *limit, double *phys, double *total, double *own) {
    double proc = -1;
    float pu = 0, pt = 0, pp = 0;
    plat_commit_info(&proc, commit, limit);
    plat_mem_info(&pu, &pt, &pp);
    *phys = pu, *total = pt;
    if (own) *own = proc > 0 ? proc : pp;
}

void prep_mem_begin(const char *what) {
    plat_mutex_lock(&M.lock);
    if (!M.on) {
        double commit = -1, limit = -1, phys = 0, total = 0, own = 0;
        figures(&commit, &limit, &phys, &total, &own);
        double gb = cfg_double("OOTPC_SHADER_PREPARE_RAM_GB");
        M.cap_mb = gb > 0 ? gb * 1024.0 : total > 0 ? total / 2 : 0;
        if (M.cap_mb > 0 && M.cap_mb < 1024.0) M.cap_mb = 1024.0;   // below 1 GiB one worker alone goes past it
        M.own_mb = own, M.commit0_mb = commit, M.phys0_mb = phys, M.on = 1;
        char cap[64];
        if (M.cap_mb > 0) snprintf(cap, sizeof cap, "%.1f GiB%s", M.cap_mb / 1024.0, gb > 0 ? "" : " (half the installed memory)");
        else snprintf(cap, sizeof cap, "none (the installed memory is unknown)");
        rt_log("prep_mem: %s: memory cap %s, %.0f of %.0f MB physical memory in use at the start", what, cap, phys, total);
        printf("prepare-shaders: memory cap %s\n", cap);
        fflush(stdout);
    }
    plat_mutex_unlock(&M.lock);
}

// Caller holds M.lock.
static void sample(void) {
    uint64_t now = plat_now_ns();
    if (M.sampled_ns && now - M.sampled_ns < SAMPLE_NS) return;
    M.sampled_ns = now;
    double commit = -1, limit = -1, phys = 0, total = 0;
    figures(&commit, &limit, &phys, &total, NULL);
    double grew = phys - M.phys0_mb;
    if (commit >= 0 && M.commit0_mb >= 0 && commit - M.commit0_mb > grew) grew = commit - M.commit0_mb;
    M.use_mb = M.own_mb + (grew > 0 ? grew : 0);
    int floor = (total > 0 && total - phys < PREP_MEM_FLOOR_PHYS_MB) ||
                (limit > 0 && commit >= 0 && limit - commit < PREP_MEM_FLOOR_COMMIT_MB);
    M.over = floor || (M.cap_mb > 0 && M.use_mb > M.cap_mb);
    M.critical = (total > 0 && total - phys < PREP_MEM_CRIT_PHYS_MB) ||
                 (limit > 0 && commit >= 0 && limit - commit < PREP_MEM_CRIT_COMMIT_MB);
    if (M.over && !M.told) {
        M.told = 1;
        const char *why = floor ? "the system is nearly out of memory" : "the memory cap is reached";
        rt_log("prep_mem: %s (%.0f MB used of a %.0f MB cap, %.0f MB physical memory free): workers wait while it is",
               why, M.use_mb, M.cap_mb, total - phys);
        printf("prepare-shaders: %s: fewer shaders are prepared at once until memory is free again\n", why);
        fflush(stdout);
    }
}

void prep_mem_enter(void) {
    plat_mutex_lock(&M.lock);
    M.running++;
    plat_mutex_unlock(&M.lock);
}

void prep_mem_leave(void) {
    plat_mutex_lock(&M.lock);
    M.running--;
    plat_mutex_unlock(&M.lock);
}

void prep_mem_gate(void) {
    if (!M.on) return;
    int waiting = 0;
    for (;;) {
        plat_mutex_lock(&M.lock);
        sample();
        // over the cap a worker steps aside while another still runs; the last one running goes on regardless
        if (!M.over || M.running - (waiting ? 0 : 1) <= 0) {
            if (waiting) M.running++, M.waiting--;
            plat_mutex_unlock(&M.lock);
            return;
        }
        if (!waiting) M.running--, M.waiting++, waiting = 1;
        plat_mutex_unlock(&M.lock);
        plat_sleep_ms(WAIT_MS);
    }
}

void prep_mem_status(int *over, int *critical, double *use_mb, double *cap_mb) {
    *over = *critical = 0, *use_mb = *cap_mb = 0;
    if (!M.on) return;
    plat_mutex_lock(&M.lock);
    sample();
    *over = M.over, *critical = M.critical, *use_mb = M.use_mb, *cap_mb = M.cap_mb;
    plat_mutex_unlock(&M.lock);
}
