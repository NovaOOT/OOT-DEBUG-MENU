// N64 Graphics placement (n64place.h): N64 Link in the player's place (n64pose.c) and the characters that have an N64
// model in theirs (n64actors.c), lit by a fixed light setting.
// The N64 version's rooms are not placed: the remake rebuilt its areas by hand (about 21 N64 units a metre, rooms moved
// and reshaped), so they do not fit its world, and the remake keeps drawing its own.
#include "n64place.h"

#include <string.h>

#include "n64actors.h"
#include "n64gfx.h"
#include "n64pose.h"

// A daylight setting for characters: ambient, a light from above and ahead, a weaker one from behind (N64 lights
// point toward the light).
static void character_light(N64Lights *l) {
    memset(l, 0, sizeof *l);
    const float key[3] = {0.35f, 0.80f, 0.48f}, back[3] = {-0.35f, -0.20f, -0.91f};
    for (int k = 0; k < 3; k++) {
        l->ambient[k] = 0.42f;
        l->dir1[k] = key[k], l->col1[k] = 0.62f;
        l->dir2[k] = back[k], l->col2[k] = 0.22f;
    }
}

int n64_place_frame(N64Item *items, int max, N64Lights *lights) {
    int n = 0;
    character_light(lights);
    if (n < max && n64pose_link(&items[n])) items[n].dark = (uint32_t)n64gfx_dark(), n++;
    n += n64actors_items(items + n, max - n);
    return n;
}
