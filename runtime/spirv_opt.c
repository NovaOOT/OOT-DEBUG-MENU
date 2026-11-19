// Optimisation of the translated shader modules before the driver compiles them (OOTPC_SPV_OPT).
//
// The translator writes every guest register as a function variable and loads each constant-buffer and storage-buffer
// word again at each use, so a large material repeats the same loads, address arithmetic and bit casts many times
// over. NVIDIA's compiler leaves most of that in place. The forward-lit water of Lake Hylia (fragment shader
// c4b721488d7ce9d1, two draws over most of the screen) took 7.5 ms of a 16 ms GPU frame at 1080p on an RTX 5070; with
// its function variables made SSA values, its repeated values computed once (value numbering) and the dead code
// removed, it takes under 1 ms and the image is the same. These three passes do not change the control flow, so they
// keep the reconvergence the shaders rely on (vk_pipeline.c spirv_reconverge runs after them). Passes that do change
// it were tried and left out: if-conversion changed the water's colour.
//
// The passes come from SPIRV-Tools' optimiser, loaded at run time: on Windows ootpc_spvopt.dll next to ocarina.exe
// (tools/build_spvopt.py links it from the Vulkan SDK's libraries; the Visual C++ runtime DLLs it needs sit beside it),
// on Linux the system's libSPIRV-Tools-opt.so or libSPIRV-Tools-shared.so, or the one in $VULKAN_SDK/lib. Without it
// the modules go to the driver as translated. A module the optimiser refuses (it validates its input first) also goes
// as it is.
//
// Results are kept in <shader cache>/opt/<input hash>.spv, so a module costs the optimiser's time (about 1 ms per
// 10000 words) once; each file ends with the FNV-1a hash of its words and one that does not match is made again.
//
// OOTPC_SPV_OPT=0 turns it off. OOTPC_SPV_OPT_MIN_WORDS (default 1024): smaller modules are left as they are.
// OOTPC_SPV_OPT_STAGES (default 6): the stages optimised, 1 vertex, 2 fragment, 4 compute, 8 the others. The time goes
// into the per-pixel and per-thread work; vertex modules are many and gain little, so the first start optimises
// about half as much without them.
#include "spirv_opt.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cfg.h"
#include "host.h"
#include "platform/platform.h"

// The C interface of SPIRV-Tools (include/spirv-tools/libspirv.h), the parts used here.
typedef struct spv_optimizer_t spv_optimizer_t;
typedef struct spv_optimizer_options_t *spv_optimizer_options;
typedef struct { uint32_t *code; size_t wordCount; } spv_binary_t;
typedef spv_binary_t *spv_binary;
typedef struct { size_t line, column, index; } spv_position_t;
typedef void (*spv_message_consumer)(int level, const char *source, const spv_position_t *pos, const char *msg);
// spv_target_env: the Vulkan environment of each SPIR-V version (1.3 and older, 1.4, 1.5, 1.6); spv_result_t
enum { SPV_ENV_VULKAN_1_1 = 18, SPV_ENV_VULKAN_1_1_SPIRV_1_4 = 21, SPV_ENV_VULKAN_1_2 = 23, SPV_ENV_VULKAN_1_3 = 25,
       SPV_SUCCESS = 0 };

static struct {
    spv_optimizer_t *(*create)(int env);
    void (*destroy)(spv_optimizer_t *);
    void (*consumer)(spv_optimizer_t *, spv_message_consumer);
    int (*passes)(spv_optimizer_t *, const char **flags, size_t n);   // bool in C++: the low byte
    int (*run)(spv_optimizer_t *, const uint32_t *, size_t, spv_binary *, spv_optimizer_options);
    spv_optimizer_options (*opts_create)(void);
    void (*opts_destroy)(spv_optimizer_options);
    void (*opts_validate)(spv_optimizer_options, int);
    void (*bin_destroy)(spv_binary);
} L;

static PlatMutex lock = PLAT_MUTEX_INIT;
static int state;                 // 0 not tried, 1 ready, -1 off or missing
static uint64_t min_words = 1024;
static unsigned stages = 6;       // OOTPC_SPV_OPT_STAGES (stage_bit): fragment and compute
static char opt_dir[1024];        // the disk cache, "" none
static SpirvOptStats st;

static const char *const PASSES[] = {"--ssa-rewrite", "--redundancy-elimination", "--eliminate-dead-code-aggressive"};

static uint64_t fnv(uint64_t h, const void *p, size_t n) {
    for (size_t i = 0; i < n; i++) h = (h ^ ((const uint8_t *)p)[i]) * 0x100000001b3ull;
    return h;
}

static void *lib_open(char *path, size_t cap) {
    void *l = NULL;
#ifdef _WIN32
    if (plat_exe_path(path, cap - 32) && plat_last_sep(path)) {
        char *sep = plat_last_sep(path);
        snprintf(sep + 1, cap - (size_t)(sep + 1 - path), "ootpc_spvopt.dll");
        if (plat_file_exists(path)) l = plat_lib_open(path);
    }
#else
    static const char *const names[] = {"libSPIRV-Tools-opt.so", "libSPIRV-Tools-shared.so"};
    const char *sdk = getenv("VULKAN_SDK");
    for (int i = 0; !l && i < 2; i++) {
        if (sdk && *sdk) {
            snprintf(path, cap, "%s/lib/%s", sdk, names[i]);
            if (plat_file_exists(path)) l = plat_lib_open(path);
        }
        if (!l && (l = plat_lib_open(names[i]))) snprintf(path, cap, "%s", names[i]);
    }
#endif
    return l;
}

static void quiet(int level, const char *source, const spv_position_t *pos, const char *msg) {
    (void)source, (void)pos;
    static volatile int32_t n;
    if (level <= 2 && plat_inc32(&n) <= 3) rt_log("spirv_opt: the optimiser refused a module: %s", msg ? msg : "?");
}

static int start(void) {
    if (!cfg_bool("OOTPC_SPV_OPT")) {
        rt_log("spirv_opt: off (OOTPC_SPV_OPT=0): shader modules go to the driver as translated");
        return -1;
    }
    const char *mw = cfg_get("OOTPC_SPV_OPT_MIN_WORDS"), *sm = cfg_get("OOTPC_SPV_OPT_STAGES");
    if (mw && *mw) min_words = strtoull(mw, NULL, 10);
    if (sm && *sm) stages = (unsigned)strtoul(sm, NULL, 0) & 15;
    char path[1100];
    void *l = lib_open(path, sizeof path);
    if (!l) {
        rt_log("spirv_opt: the SPIR-V optimiser library (ootpc_spvopt.dll beside the game, libSPIRV-Tools-opt.so on "
               "Linux) is missing or does not load: shader modules go to the driver as translated (OOTPC_SPV_OPT)");
        return -1;
    }
    *(void **)&L.create = plat_lib_sym(l, "spvOptimizerCreate");
    *(void **)&L.destroy = plat_lib_sym(l, "spvOptimizerDestroy");
    *(void **)&L.consumer = plat_lib_sym(l, "spvOptimizerSetMessageConsumer");
    *(void **)&L.passes = plat_lib_sym(l, "spvOptimizerRegisterPassesFromFlagsWhilePreservingTheInterface");
    *(void **)&L.run = plat_lib_sym(l, "spvOptimizerRun");
    *(void **)&L.opts_create = plat_lib_sym(l, "spvOptimizerOptionsCreate");
    *(void **)&L.opts_destroy = plat_lib_sym(l, "spvOptimizerOptionsDestroy");
    *(void **)&L.opts_validate = plat_lib_sym(l, "spvOptimizerOptionsSetRunValidator");
    *(void **)&L.bin_destroy = plat_lib_sym(l, "spvBinaryDestroy");
    if (!L.create || !L.destroy || !L.consumer || !L.passes || !L.run || !L.opts_create || !L.opts_destroy ||
        !L.opts_validate || !L.bin_destroy) {
        rt_log("spirv_opt: %s lacks the optimiser's C interface: shader modules go as translated", path);
        return -1;
    }
    const char *cache = cfg_get("OOTPC_SHADER_CACHE");
    char exe[1024];
    if (cache && *cache && strlen(cache) < sizeof opt_dir - 8) snprintf(opt_dir, sizeof opt_dir, "%s/opt", cache);
    else if (plat_exe_path(exe, sizeof exe) && plat_last_sep(exe)) {
        *plat_last_sep(exe) = 0;
        if (strlen(exe) < sizeof opt_dir - 24) snprintf(opt_dir, sizeof opt_dir, "%s/shader-cache/opt", exe);
    }
    if (opt_dir[0]) plat_file_mkdir_p(opt_dir);
    rt_log("spirv_opt: %s: passes %s %s %s on modules of %llu words and more; results kept in %s", path, PASSES[0],
           PASSES[1], PASSES[2], (unsigned long long)min_words, opt_dir[0] ? opt_dir : "no folder (made each start)");
    return 1;
}

// The OOTPC_SPV_OPT_STAGES bit of a module's first entry point: 1 vertex, 2 fragment, 4 compute, 8 any other stage.
static unsigned stage_bit(const uint32_t *w, uint64_t words) {
    for (uint64_t i = 5; i < words;) {
        uint32_t op = w[i] & 0xffff, n = w[i] >> 16;
        if (!n || i + n > words) break;
        if (op == 15 && n >= 2) return w[i + 1] == 0 ? 1 : w[i + 1] == 4 ? 2 : w[i + 1] == 5 ? 4 : 8;   // OpEntryPoint
        if (op == 54) break;   // OpFunction: past the entry points
        i += n;
    }
    return 8;
}

uint32_t *spirv_opt(const uint32_t *w, uint64_t words, uint64_t *out_words) {
    int s = __atomic_load_n(&state, __ATOMIC_ACQUIRE);
    if (!s) {
        plat_mutex_lock(&lock);
        if (!state) __atomic_store_n(&state, start(), __ATOMIC_RELEASE);
        s = state;
        plat_mutex_unlock(&lock);
    }
    if (s < 0 || !w || words < 5 || w[0] != 0x07230203u) return NULL;
    if (!(stage_bit(w, words) & stages)) return NULL;
    if (words < min_words) {
        __atomic_fetch_add(&st.small, 1, __ATOMIC_RELAXED);
        return NULL;
    }
    // the key covers the passes too: a change of the list makes new files
    uint64_t key = fnv(0xcbf29ce484222325ull, w, (size_t)words * 4);
    for (size_t i = 0; i < sizeof PASSES / sizeof *PASSES; i++) key = fnv(key, PASSES[i], strlen(PASSES[i]) + 1);
    char path[1100] = "";
    if (opt_dir[0]) {
        snprintf(path, sizeof path, "%s/%016llx.spv", opt_dir, (unsigned long long)key);
        uint64_t n = 0;
        uint8_t *b = plat_file_read(path, &n);
        if (b && n >= 28 && n % 4 == 0) {
            uint64_t body = n - 8, h;
            memcpy(&h, b + body, 8);
            if (fnv(0xcbf29ce484222325ull, b, (size_t)body) == h && ((uint32_t *)b)[0] == 0x07230203u) {
                __atomic_fetch_add(&st.cached, 1, __ATOMIC_RELAXED);
                *out_words = body / 4;
                return (uint32_t *)b;
            }
        }
        free(b);
    }
    uint64_t t0 = plat_now_ns();
    uint32_t minor = w[1] >> 8 & 0xff;   // the module's SPIR-V version, which the optimiser validates it against
    spv_optimizer_t *o = L.create(minor <= 3 ? SPV_ENV_VULKAN_1_1 : minor == 4 ? SPV_ENV_VULKAN_1_1_SPIRV_1_4
                                  : minor == 5 ? SPV_ENV_VULKAN_1_2 : SPV_ENV_VULKAN_1_3);
    if (!o) return NULL;
    L.consumer(o, quiet);
    uint32_t *res = NULL;
    spv_binary out = NULL;
    spv_optimizer_options op = L.opts_create();
    if (op && (L.passes(o, (const char **)PASSES, sizeof PASSES / sizeof *PASSES) & 0xff) &&
        L.run(o, w, (size_t)words, &out, op) == SPV_SUCCESS && out && out->code && out->wordCount >= 5 &&
        out->code[0] == 0x07230203u) {
        res = malloc(out->wordCount * 4 + 8);
        if (res) {
            memcpy(res, out->code, out->wordCount * 4);
            *out_words = out->wordCount;
        }
    }
    if (out) L.bin_destroy(out);
    if (op) L.opts_destroy(op);
    L.destroy(o);
    uint64_t dt = plat_now_ns() - t0;
    __atomic_fetch_add(&st.ns, dt, __ATOMIC_RELAXED);
    if (!res) {
        __atomic_fetch_add(&st.failed, 1, __ATOMIC_RELAXED);
        return NULL;
    }
    static volatile int32_t big;   // the first large modules, one line each (the costly ones to optimise and to run)
    if (words >= 32768 && plat_inc32(&big) <= 24)
        rt_log("spirv_opt: module %016llx of %llu words: %llu words, %.1f ms", (unsigned long long)key,
               (unsigned long long)words, (unsigned long long)*out_words, dt / 1e6);
    uint64_t n = __atomic_add_fetch(&st.done, 1, __ATOMIC_RELAXED);
    __atomic_fetch_add(&st.words_in, words, __ATOMIC_RELAXED);
    __atomic_fetch_add(&st.words_out, *out_words, __ATOMIC_RELAXED);
    if (path[0]) {
        uint64_t h = fnv(0xcbf29ce484222325ull, res, (size_t)*out_words * 4);
        memcpy((uint8_t *)res + *out_words * 4, &h, 8);
        plat_file_write_cached(path, res, (size_t)*out_words * 4 + 8);
    }
    if (n == 1 || n == 10 || n == 100 || n == 1000 || n % 5000 == 0)
        rt_log("spirv_opt: %llu modules optimised (%llu from the cache, %llu refused, %llu below the size), %llu words "
               "to %llu, %.1f ms in the optimiser", (unsigned long long)n,
               (unsigned long long)__atomic_load_n(&st.cached, __ATOMIC_RELAXED),
               (unsigned long long)__atomic_load_n(&st.failed, __ATOMIC_RELAXED),
               (unsigned long long)__atomic_load_n(&st.small, __ATOMIC_RELAXED),
               (unsigned long long)__atomic_load_n(&st.words_in, __ATOMIC_RELAXED),
               (unsigned long long)__atomic_load_n(&st.words_out, __ATOMIC_RELAXED),
               __atomic_load_n(&st.ns, __ATOMIC_RELAXED) / 1e6);
    return res;
}

void spirv_opt_stats(SpirvOptStats *s) {
    s->done = __atomic_load_n(&st.done, __ATOMIC_RELAXED);
    s->cached = __atomic_load_n(&st.cached, __ATOMIC_RELAXED);
    s->failed = __atomic_load_n(&st.failed, __ATOMIC_RELAXED);
    s->small = __atomic_load_n(&st.small, __ATOMIC_RELAXED);
    s->words_in = __atomic_load_n(&st.words_in, __ATOMIC_RELAXED);
    s->words_out = __atomic_load_n(&st.words_out, __ATOMIC_RELAXED);
    s->ns = __atomic_load_n(&st.ns, __ATOMIC_RELAXED);
    s->lib = __atomic_load_n(&state, __ATOMIC_ACQUIRE);
}
