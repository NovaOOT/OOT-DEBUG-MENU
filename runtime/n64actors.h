// N64 Graphics' enemies and other characters (the N64_ACTORS part): the N64 version's model of a character drawn in
// place of the remake's, each N64 limb riding one remake bone (tools/n64actors.py fits them; the mod's n64/actors.bin).
//
// Every skinned model passes n64pose_world_ready once its skeleton's world pose is made. A model whose skeleton (its
// bone names, hashed) is in actors.bin gets its N64 limbs placed from its bones that frame; vk_exec_draw.c then skips
// the remake model's own draws (motion_owner tracks them flagged MOTION_OWNER_N64_ACTOR), and vk_n64.c draws the N64
// one (n64place.c).
#pragma once

#include <stdint.h>

typedef struct N64Item N64Item;   // vk_n64.h

// The mod is on with its N64_ACTORS part and its actors.bin has entries.
int n64actors_wanted(void);
// Game thread, from n64pose_world_ready: model's skeleton was just posed; shift takes the model's space to the
// world's (n64pose.c note_shift). 1 posed, 0 a character whose pose is not made yet (ask again), -1 none to draw.
int n64actors_world_ready(uint64_t model, const float shift[3]);
// Game thread, every frame: the models now stood in for go to motion_owner (motion_owner_set_standins).
void n64actors_game_frame(void);
// Render thread: one item per character with a fresh pose; the number written, at most max.
int n64actors_items(N64Item *items, int max);
// Render thread: the N64 model stands in for this remake model now (its draws are skipped).
int n64actors_stands_in(uint64_t model);
