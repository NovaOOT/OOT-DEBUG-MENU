// N64 Graphics: where the N64 version's rooms and models go in the remake's world each frame (vk_n64.c draws them).
#pragma once

#include "vk_n64.h"

// This frame's draws into items (at most max) and the scene's light setting; the number of items, 0 for none.
int n64_place_frame(N64Item *items, int max, N64Lights *lights);
