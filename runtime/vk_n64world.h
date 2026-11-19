// N64 Graphics' levels on the Vulkan side (n64world.h): the N64 texture written over a remake texture's image after its
// upload. vk_texture.c calls these with the texture lock held.
#pragma once

#include <stdint.h>

#include "tex.h"

// The world.bin entry of the texture t (vk_texture.c Tex.w64): entry + 1, -1 when world.bin names it not, 0 when
// world.bin is not read yet (asked again at the next upload). Reads the first bytes of its storage at img->addr.
int32_t vk_n64world_match(const VkTexImage *img, const tex_t *t);
// The entry needs to know whether the texture's own content is opaque before its colour is replaced (a texture with
// alpha, a decal or a cut-out, keeps the remake's: the N64 texture would fill it in).
int vk_n64world_needs_opacity(int entry);
// 1 when a BC3 texture's storage holds alpha 255 in every block (its alpha endpoints); 0 otherwise.
int vk_n64world_bc3_opaque(const tex_t *t, uint64_t addr);
// Whether vk_n64world_apply would write the image in the state now (n64world_state), without writing it.
int vk_n64world_wants(int entry, int opaque, const VkTexImage *img, const TexVkFormat *f);
// Right after an upload into img (the open upload batch then runs it after the upload's own copies): writes the N64
// texture of the entry's category in the area now over every level and layer of img, encoded to img's format.
// opaque: the texture's own content is opaque (vk_n64world_needs_opacity). 1 when it wrote the image, 0 when the
// image keeps what the upload wrote (the part is off, the area has no texture of the category, the format is one
// this does not write).
int vk_n64world_apply(const VkTexImage *img, const tex_t *t, const TexVkFormat *f, int entry, int opaque);
