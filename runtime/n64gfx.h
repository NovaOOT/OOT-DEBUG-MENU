// The N64 Graphics mod's files (tools/make_n64_mod.py writes them, its header comment gives the layout): the N64
// version's scenes and objects as vertices, materials, texels and batches, read from the mod's n64 folder when the mod
// is on (runtime/mods.h MOD_FEATURE_N64). vk_n64.c draws them.
#pragma once

#include <stddef.h>
#include <stdint.h>

#define N64G_SCENE 1
#define N64G_OBJECT 2

typedef struct {
    float x, y, z;
    int16_t s, t;        // texels, 10.5 fixed
    uint8_t rgba[4];     // colour, or the normal and alpha when the material lights
    uint16_t node, pad;  // the room or limb the vertex moves with
} N64Vert;   // 24 bytes

typedef struct {
    uint32_t combine_hi, combine_lo, othermode_h, othermode_l, geometry, prim, env, lod_on;
    float scale[2];
    uint32_t tile[2][4];   // texel offset; w | h << 16; uls | ult << 16; clamp, mirror, masks, shifts, present bit 20
    uint32_t fog, flags;   // flags N64MAT_TUNIC
} N64Mat;   // 80 bytes
#define N64MAT_TUNIC 1u   // the environment colour is the player's tunic (vk_n64.c sets it per draw)

typedef struct {
    uint32_t first, count, mat;
    uint16_t node, flags;   // flags 1: see-through
} N64Batch;

typedef struct {
    int16_t x, y, z, parent;
    uint16_t first, count, kind, index;   // kind 1 room, 2 limb, 3 model (index: its display list's offset / 8)
} N64Node;

typedef struct {
    uint8_t ambient[3];
    int8_t dir1[3];
    uint8_t col1[3];
    int8_t dir2[3];
    uint8_t col2[3];
    uint8_t fog[3];
    uint8_t fog_near_be[2], far_be[2];
    uint8_t pad[2];
} N64Light;   // 24 bytes

typedef struct {
    uint8_t *data;
    size_t size;
    uint32_t kind, number;
    const N64Vert *verts;
    const N64Mat *mats;
    const uint32_t *texels;
    const N64Batch *batches;
    const N64Node *nodes;
    const N64Light *lights;
    const int16_t (*col_verts)[4];
    const uint16_t (*col_tris)[4];
    const float (*bounds)[6];
    const int16_t *rest;   // Link's objects: a standing frame of his animations (n64pose.c), else none
    // Link's objects: the Kokiri, Goron and Zora tunic colours (r | g << 8 | b << 16 | a << 24), then for each row of
    // the hand and sheath table (N64ROW_*) the node of this object's model for it, UINT32_MAX none; else none
    const uint32_t *player;
    uint32_t nverts, nmats, ntexels, nbatches, nnodes, nlights, ncol_verts, ncol_tris, nbounds, nrest, nplayer;
} N64File;

// The rows of the player's hand and sheath table, in the order tools/n64link.py ROW_ORDER writes them.
enum { N64ROW_RH_CLOSED, N64ROW_RH_DEKU, N64ROW_RH_HYLIAN, N64ROW_RH_MIRROR, N64ROW_SHEATH_SWORD,
       N64ROW_SHEATH_SWORD_DEKU, N64ROW_SHEATH_SWORD_HYLIAN, N64ROW_SHEATH_SWORD_MIRROR, N64ROW_SHEATH_EMPTY,
       N64ROW_SHEATH_EMPTY_DEKU, N64ROW_SHEATH_EMPTY_HYLIAN, N64ROW_SHEATH_EMPTY_MIRROR, N64ROW_LH_BIGGORON,
       N64ROW_LH_BROKEN_KNIFE, N64ROW_LH_OPEN, N64ROW_LH_CLOSED, N64ROW_LH_SWORD, N64ROW_RH_OPEN, N64ROWS };
#define N64_TUNICS 3   // Kokiri, Goron, Zora
// After the rows, the pieces for gear the age's own lists lack (tools/n64link.py EXTRAS; files made before them stop at
// the rows): the other age's swords without their hand and its sheath, and each shield alone on the back and in hand.
enum { N64X_SWORD, N64X_BIGGORON, N64X_BROKEN_KNIFE, N64X_SHEATH_SWORD, N64X_SHEATH_EMPTY, N64X_BACK_DEKU,
       N64X_BACK_HYLIAN, N64X_BACK_MIRROR, N64X_HAND_DEKU, N64X_HAND_HYLIAN, N64X_HAND_MIRROR, N64XROWS };
// After those, what he holds and wears (tools/n64link.py ITEM_ORDER; files made before them stop after N64X_*). Each
// hand item has the age's own row (the hand with the item, drawn in the hand's place) and the item alone (the other
// age's, drawn beside the age's closed hand); then pieces drawn beside a limb's own model, in its space.
enum { N64I_BOW, N64I_BOW_ALONE, N64I_SLINGSHOT, N64I_SLINGSHOT_ALONE, N64I_OCARINA_FAIRY, N64I_OCARINA_FAIRY_ALONE,
       N64I_OCARINA_TIME, N64I_OCARINA_TIME_ALONE, N64I_HOOKSHOT, N64I_HOOKSHOT_ALONE, N64I_HAMMER, N64I_HAMMER_ALONE,
       N64I_BOOMERANG, N64I_BOOMERANG_ALONE, N64I_BOTTLE_HAND, N64I_BOTTLE_HAND_ALONE, N64I_BOTTLE, N64I_DEKU_STICK,
       N64I_MASK_KEATON, N64I_MASK_SKULL, N64I_MASK_SPOOKY, N64I_MASK_BUNNY, N64I_MASK_GORON, N64I_MASK_ZORA,
       N64I_MASK_GERUDO, N64I_MASK_TRUTH, N64I_IRON_BOOT_L, N64I_IRON_BOOT_R, N64I_HOVER_BOOT_L, N64I_HOVER_BOOT_R,
       N64I_GORON_BRACELET, N64I_SILVER_FOREARM_L, N64I_SILVER_HAND_L, N64I_SILVER_HAND_L_OPEN, N64I_SILVER_FOREARM_R,
       N64I_SILVER_HAND_R, N64I_SILVER_HAND_R_OPEN, N64I_GOLD_FOREARM_L, N64I_GOLD_HAND_L, N64I_GOLD_HAND_L_OPEN,
       N64I_GOLD_FOREARM_R, N64I_GOLD_HAND_R, N64I_GOLD_HAND_R_OPEN, N64IROWS };
// After the items, Link's face (tools/n64link.py face_variants): his eyes with each eye texture, then his mouth with
// each mouth.
enum { N64_EYES = 8, N64_MOUTHS = 4 };

int n64gfx_on(void);   // the mod is on and its folder has the files
// The mod's parts, each switched on its own (the Mods tab, OOT-PC.ini [mods] N64_LINK, N64_ITEMS, N64_ACTORS,
// N64_WORLD, N64_SWORD_WALLS; all on by default) and read live: Link with his tunics, boots, gauntlets, swords and
// shields; the items he holds and the masks (with Link only); the enemies and other characters; the levels; swords
// and sticks that strike walls as in the N64 game. 1 while the mod is on and the part is.
enum { N64_PART_LINK, N64_PART_ITEMS, N64_PART_ACTORS, N64_PART_WORLD, N64_PART_SWORD_WALLS, N64_PARTS };
int n64gfx_part(int part);
const char *n64gfx_part_key(int part);   // "N64_LINK" ...
// The Dark Link mod (mods.h MOD_FEATURE_DARK_LINK) is on while N64 Link is drawn: he is drawn as the N64 game's Dark
// Link, near black with faint shading and red eyes (vk_n64.c, n64_fs.frag). OOT-PC.ini [mods] DARK_LINK_STYLE "remake"
// turns N64 Link off instead while that mod is on (n64gfx_part), so the remake's Dark Link plays.
int n64gfx_dark(void);
// The Dark Link mod is on and the remake's models play (N64 Link off, by its style or its switch): dark_link_fx.c.
int n64gfx_dark_remake(void);
// A scene's or an object's file, read once and kept; NULL when the mod is off or has no such file.
const N64File *n64gfx_scene(int number);
const N64File *n64gfx_object(int number);
// The mod's n64/actors.bin (tools/n64actors.py gives its layout), read once and kept; NULL when there is none.
const uint8_t *n64gfx_actors(size_t *size);
// The mod's n64/world.bin (tools/n64world.py gives its layout), read once and kept; NULL when there is none.
const uint8_t *n64gfx_world(size_t *size);
