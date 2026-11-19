// N64 Graphics, its sword walls part (n64gfx.h N64_PART_SWORD_WALLS): a sword, Deku stick or hammer swung into a wall
// bounces off it, as in the N64 game, through the remake's own rebound (the player's AttackCollide reaction).
//
// The remake's player still has that reaction (Player.action.reactionattackcollide.module, the Sword_Rebound
// animations), but only an enemy's guard starts it; its weapon passes through the level. Each frame the blade is
// placed from the player's skeleton (the Weapon_L bone, blade along its +z) and, while it moves fast, tested against
// the area's collision polygons (debug_hook.c, read on its helper thread while this part is on). A strike is kept a
// moment for the reaction scheduler, which debug_game.c n64_sword_wall lets pick AttackCollide.
#pragma once

#include <stdint.h>

#include "cpu.h"

// Game thread: the skeleton world pose of model (an FModel) has just been computed (debug_game.c debug_lank_world);
// matrix is the model's matrix. For the player's model the blade is tested against the walls.
void n64sword_world_ready(uint64_t model, uint64_t matrix);
// Game thread, from the player's reaction scheduler (debug_game.c n64_sword_wall): 1 from a strike until the rebound
// action has started (or a short while has passed), which the scheduler then answers with the rebound. f0 and f4 are
// the hit state's two reaction kinds as the game left them (logged). The first time a strike is told, the game's
// own spark and clink for a deflected hit are played at the contact point through c (its registers are left as they
// were).
int n64sword_take(Cpu *c, uint32_t f0, uint32_t f4);
// The part is on: debug_view.c dbg_view_kinds keeps debug_hook.c reading the area's collision.
int n64sword_wants_collision(void);
