// A cap on the memory the shader preparation takes (ocarina.exe --prepare-shaders, --warm-shaders and
// --prepare-pipelines; prep_mem.c). The preparation runs a worker per logical processor, translator processes beside
// them and the graphics driver's compiler inside each worker, and nothing else bounds what they take together: on a PC
// with many threads and little memory they could fill it, and the whole system then fails allocations.
//
// OOTPC_SHADER_PREPARE_RAM_GB (OOT-PC.ini shader_prepare_ram_gb, the launcher's Settings "Max RAM for Shaders"): the
// most the preparation may add to what the system had in use when it began, plus this process's own memory then.
// 0, the default: half the installed memory. Whatever the cap, the workers also wait while the system has less than
// PREP_MEM_FLOOR_PHYS_MB of physical memory or PREP_MEM_FLOOR_COMMIT_MB of commit headroom left.
//
// A worker calls prep_mem_enter when it starts, prep_mem_gate before each job and prep_mem_leave when it ends. Over
// the cap, prep_mem_gate holds its worker until the use falls back under it; one worker always goes on, so the
// preparation still finishes, only on fewer threads. Without prep_mem_begin (play) every call returns at once.
#pragma once

#define PREP_MEM_FLOOR_PHYS_MB 1024.0
#define PREP_MEM_FLOOR_COMMIT_MB 2048.0
// Below these the system is critical: the watchdog of the driver builds (vk_pipeline.c lb_watchdog) ends the run.
#define PREP_MEM_CRIT_PHYS_MB 512.0
#define PREP_MEM_CRIT_COMMIT_MB 1024.0

void prep_mem_begin(const char *what);   // the cap and the baseline, at the first call; logs the cap
void prep_mem_enter(void);
void prep_mem_gate(void);
void prep_mem_leave(void);
// Fresh figures: *over when the use is past the cap or the floors above, *critical when past the critical lines;
// *use_mb and *cap_mb for the log. All 0 without prep_mem_begin.
void prep_mem_status(int *over, int *critical, double *use_mb, double *cap_mb);
