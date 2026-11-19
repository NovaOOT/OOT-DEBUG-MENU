// N64 Graphics: the N64 version's Link posed by the remake player's skeleton every frame, in the player's place.
//
// The remake animates a skeleton of a few hundred bones; N64 Link has 21 limbs. Each limb follows one remake bone: its
// world rotation is that bone's this frame times a fixed offset, measured once against the remake skeleton's bind pose
// (from the bone records in memory) and a standing frame of N64 Link's own animations (the mod's rest pose,
// tools/n64link.py). The offset turns the standing limb so that it runs along the remake bone it follows in the bind
// pose (thighs, shins, feet, arms and hands), or upright and facing ahead (hips, torso), and keeps the rest of the
// standing frame's turn; the head, hat and sheath keep the standing frame's turn against the limb they hang from. The limbs' places come from their N64 joint positions, scaled to the remake's leg length
// and hung from the remake's hips, so the N64 body keeps its own proportions and its feet meet the remake's.
#pragma once

#include <stdint.h>

typedef struct N64Item N64Item;   // vk_n64.h

// Game thread, every frame (debug_game.c debug_frame): looks up the player's model again.
void n64pose_game_frame(void);
// Game thread: the skeleton world pose of model (an FModel) has just been computed (debug_game.c debug_lank_world).
// For the player's model this frame's limb matrices are made from it.
void n64pose_world_ready(uint64_t model, uint64_t matrix);
// Render thread: N64 Link on the player's latest pose into *item; 0 when there is none to draw.
int n64pose_link(N64Item *item);
// N64 Link stands in for the player now: vk_exec_draw.c skips the player's own body.
int n64pose_active(void);
// The player's N64 file has the item pieces (n64gfx.h N64I_*): N64 Link draws the items flagged MOTION_OWNER_N64_ITEM.
int n64pose_items(void);
// The file has the pieces he wears (boots, gauntlets, the Goron's Bracelet), drawn with N64 Link whatever the items
// part says: those flagged MOTION_OWNER_N64_WEAR go.
int n64pose_wear(void);
// The same for the pause menu's preview of the player (motion_owner_preview), whose body vk_exec_draw.c then skips,
// telling n64pose_preview_drawn: while its draws come, N64 Link is posed as the preview (the menu draws the preview as
// its scene, with a camera of its own).
int n64pose_preview_active(void);
void n64pose_preview_drawn(void);
uint64_t n64pose_preview_body(void);   // the preview's body model (its first), 0 when none is known
// The N64 Graphics mod is on (read once).
int n64pose_wanted(void);
