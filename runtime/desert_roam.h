// Desert free roam (desert_roam.c): the built-in feature the "Desert free roam" mod turns on
// (tools/make_desert_roam_mod.py, mods.h MOD_FEATURE_DESERT_FREE_ROAM).
#pragma once

// 1 when the game's void-out is to be held back now: the feature is on, the current area is the Haunted Wasteland or
// the Desert Colossus (either age), and the player is not far below the area's ground. Any thread.
int desert_roam_holds(void);
