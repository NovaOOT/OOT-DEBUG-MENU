// N64 Graphics' levels (the N64_WORLD part): the remake's world textures drawn with the N64 version's textures of the
// area in their place. tools/n64world.py writes the mod's n64/world.bin (its header comment gives the layout): which
// remake textures stand for which category of surface (grass, rock, wood ...) and, per area, the N64 texture of each
// category. A remake texture is known by n64world_key of its storage; vk_n64world.c writes the N64 texture over its
// image after each upload (vk_texture.c upload).
//
// Every function here but n64world_foliage runs with the texture lock held (vk_texture.c), so the state needs no
// lock of its own.
#pragma once

#include <stdint.h>

enum { N64W_GRASS, N64W_DIRT, N64W_ROCK, N64W_WOOD, N64W_LEAF, N64W_WATER, N64W_ROOF, N64W_PATH, N64W_CATS };
enum { N64W_COLOUR = 1, N64W_NORMAL = 2, N64W_ARRAY = 3 };   // what an entry is (tools/n64world.py KIND_*)
#define N64W_KEY_BYTES 4096

// world.bin is read and has entries (whether the part is on or not). Asked again until the mods folder is known.
int n64world_loaded(void);
// A remake texture's name: FNV-1a 64 over u32 width, u32 height and the first N64W_KEY_BYTES of its storage
// (tools/n64world.py key).
uint64_t n64world_key(const uint8_t *first, uint32_t width, uint32_t height);
int n64world_find(uint64_t key);   // the entry of that key, -1 none
int n64world_kind(int entry);
int n64world_category(int entry);
float n64world_metres(int entry);  // metres one repeat of the remake texture covers, 0 not known
// What the replacement depends on: whether the part is on, and the area (Sequence_CurrentBanc, polled at most every
// 250 ms). The value changes whenever either does; 0 while nothing is replaced.
uint32_t n64world_state(void);
const char *n64world_area(void);   // the area n64world_state saw last ("" none)
// The N64 texture drawn for a category in the area now: its texels (RGBA8, r in the low byte), size, the metres one
// repeat covers in the N64 room (0 not known), whether its alpha is a cut-out; its index in world.bin. 0 when there is
// none (the part is off, the area is not in world.bin, or the area has no texture of that category).
typedef struct {
    const uint32_t *texels;
    uint32_t w, h, keep_alpha, index;
    float metres;
} N64WorldTex;
int n64world_texture(int category, N64WorldTex *out);
// A texture array layer's category by its mean colour (r, g, b 0..255): grass green or olive, path grey-brown, dirt
// brown, rock grey or blue-grey; -1 for the others (light grey, blue, dark), which stay the remake's.
int n64world_colour_category(int r, int g, int b);
// The foliage the remake keeps while an area is drawn with N64 textures (OOTPC_N64_WORLD_FOLIAGE, live): 0 full,
// 1 reduced (no grass and moss: the default), 2 off (every indirect mesh draw skipped, as OOTPC_FOLIAGE = off does);
// -1 while no area is replaced. off is not the default: some areas draw buildings and walls with indirect mesh draws
// too, and Gerudo Fortress lost its fortress to it. Any thread (vk_exec_draw.c foliage_skip), without the texture lock.
int n64world_foliage(void);
