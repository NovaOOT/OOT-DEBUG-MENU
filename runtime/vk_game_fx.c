// The game's optional screen effects, each with its own setting (OOT-PC.ini [display] GAME_AO and the rows after it
// in the menu's Graphics tab): an effect turned off has its compute passes left out, and the images they would have
// written are filled with the value that means "no effect", so the passes that read them carry on unchanged.
//
// - Ambient occlusion (GAME_AO): a pass at half the output's size darkens creases from the depth buffer and two
//   blur passes (one each way, twice) smooth it, into an 8-bit image the lighting multiplies by. White is no
//   occlusion. Cheap on a fast card (no measurable change in Hyrule Field even at 4K on an RTX 5070); a look option
//   more than a speed one.
//
// The passes are known by the FNV-1a hash of their guest code (vk_exec_draw.c cs_guest_hash), as the anti-aliasing's
// are (vk_aa.c). OOTPC_AA_LOG=1 with aa other than game logs every compute program's hash, dispatch size and output,
// which is how these were found. A game build missing here keeps its passes whatever the settings ask.
#include <stddef.h>
#include <stdint.h>

#include "cfg.h"
#include "vk_exec.h"

enum { FX_AO };

static const struct { uint64_t hash; int fx; } PASSES[] = {
    {0x5aa6796fd0951cdfull, FX_AO},   // 1.1.1: the occlusion (60x17 groups at 1080p, 960x540 out)
    {0xcc7b702b4f3f6fdbull, FX_AO},   // 1.1.1 and 1.0.0: the blur across
    {0x33813e6f6fac267full, FX_AO},   // 1.1.1 and 1.0.0: the blur down
    {0x03d763b77fb8a9efull, FX_AO},   // 1.0.0: the occlusion (its blurs are 1.1.1's)
};

// fill: what the pass's storage images take while it is left out
static const struct { const char *key; float fill; } FX[] = {
    [FX_AO] = {"OOTPC_GAME_AO", 1.0f},
};

// 1: the dispatch is left out; *fill is then the value its storage images take
int vk_game_fx_skip(uint64_t guest_hash, float *fill) {
    if (!guest_hash) return 0;
    for (size_t i = 0; i < sizeof PASSES / sizeof PASSES[0]; i++) {
        if (PASSES[i].hash != guest_hash) continue;
        int fx = PASSES[i].fx;
        if (cfg_bool(FX[fx].key)) return 0;
        *fill = FX[fx].fill;
        return 1;
    }
    return 0;
}
