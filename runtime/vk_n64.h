// N64 Graphics: draws the N64 version's scenes and models (n64gfx.h) into the scene, in the remake's place
// (n64place.c says what goes where), at the scene's end, whatever upscaler is on or none (vk_n64.c says where).
#pragma once

#include <stdint.h>

#include "n64gfx.h"
#include "vk.h"

#define N64_ITEMS 160

// One N64 file drawn this frame: each node's place as the rows of a 3x4 matrix (N64 units to the remake's world
// metres), which nodes draw (NULL: all), the colour of the materials flagged N64MAT_TUNIC (r | g << 8 | b << 16 |
// a << 24), and whether it is drawn as Dark Link (n64gfx_dark: near black, its eye nodes red).
typedef struct N64Item {
    const N64File *file;
    const float (*rows)[12];
    const uint8_t *node_on;
    uint32_t tunic;
    uint32_t dark;
} N64Item;

// The scene's light setting for the shaders (n64_common.glsl Light): ambient, two lights (N64 directions), fog.
typedef struct {
    float ambient[4], dir1[4], col1[4], dir2[4], col2[4], fog[4], fog_range[4];
} N64Lights;

struct VkExec;
// vk_exec.c, as each pass begins (before the upscaler's hook) and ends: finds the bloom composite and draws into its
// picture as it ends.
void vk_n64_pass(struct VkExec *ex);
void vk_n64_pass_end(struct VkExec *ex);
void vk_n64_frame(void);   // vk_backend.c, at each present: the next frame may draw again
int vk_n64_ready(void);    // the draw point was reached lately: N64 Link is drawn in the remake player's place
