// Optimisation of the translated shader modules before the driver compiles them (OOTPC_SPV_OPT, spirv_opt.c).
#pragma once
#include <stdint.h>

// The module w (words long) with its repeated work removed, malloc'd, its length in *out_words; NULL when the module
// stays as it is (the option is off, the optimiser library is missing, the module is below the size limit or the
// optimiser refused it). Any thread.
uint32_t *spirv_opt(const uint32_t *w, uint64_t words, uint64_t *out_words);

// Counts for the log and the menu: modules optimised (from the optimiser and from the disk cache), left as they were,
// words before and after, and the optimiser's time.
typedef struct {
    uint64_t done, cached, failed, small, words_in, words_out, ns;
    int lib;   // 1 the library is loaded, 0 not tried yet, -1 missing or the option is off
} SpirvOptStats;
void spirv_opt_stats(SpirvOptStats *s);
