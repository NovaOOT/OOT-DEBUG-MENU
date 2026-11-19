// Remove invisible walls (invisible_walls.c).
#pragma once
#include <stddef.h>

// 1 while invisible walls are to go: OOTPC_NO_INVISIBLE_WALLS, or OOTPC_RANDO_INVISIBLE_WALLS = remove with the
// Randomizer mod on. Read live.
int walls_removed(void);
// A file the host's zstd decoder has just unpacked whole into the game's memory (ov_zstd.c): a collision tile or shape
// file has its air walls opened to the player while walls_removed().
void walls_decoded(void *data, size_t size);
