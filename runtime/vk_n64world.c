// N64 Graphics' levels on the Vulkan side (vk_n64world.h). A remake texture that world.bin names (n64world.h) is
// uploaded as usual (vk_texture.c upload); right after, in the same upload batch, the N64 texture of its category in
// the area now is copied over every level and layer of its image. Nothing else changes: the image keeps its size,
// format, levels and layers, so the views, samplers and shaders that use it are the same, and the remake's UVs lay the
// N64 texture where they laid the remake's.
//
// - The colour (kind N64W_COLOUR): the N64 texture repeated over the image (more than once when the remake texture
//   spans more of the world than one N64 repeat does: repeat_of), sampled bilinearly, then each smaller level a 2x2
//   box of the one above. Only for a texture whose own content is opaque: a decal or a cut-out keeps the remake's.
// - The normal map of a replaced colour (N64W_NORMAL): flat, so the remake's surface detail does not light the N64
//   texture. RGBA8 and BC3 images get (128, 128, 255, 128), which reads flat whether the shader takes the normal from
//   red and green or from red and alpha; BC5 gets its middle.
// - The terrain's material array (N64W_ARRAY): each layer's category by its mean colour (layers_of: the level of 32
//   texels or less, decoded), grass, dirt or rock; the layer's mean alpha is kept as a constant (the terrain may blend
//   its layers by it).
// The images this writes are RGBA8 (ASTC decoded on the GPU or the CPU), BC1 and BC3 (ASTC encoded again on the GPU,
// or so in the game); others keep the remake's content (log once per format). Each content made is kept for reuse
// (made[]): one N64 texture stands for many remake textures of the same size.
#include "vk_n64world.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "host.h"
#include "n64world.h"
#include "nvn_tex.h"
#include "tex.h"
#include "vk.h"

enum { E_NONE, E_RGBA, E_BGRA, E_BC1, E_BC3, E_BC5U, E_BC5S };
#define MAX_LEVELS 16
#define MAX_LAYERS 256
#define MADE_N 48
#define MADE_BYTES (256ull << 20)
#define FLAT (-1)   // Made.tex of a flat normal map

static uint32_t umax(uint32_t a, uint32_t b) { return a > b ? a : b; }
static uint32_t umin(uint32_t a, uint32_t b) { return a < b ? a : b; }
static uint32_t mip(uint32_t v, unsigned lv) { return umax(v >> lv, 1); }

static int enc_of(VkFormat f) {
    switch (f) {
    case VK_FORMAT_R8G8B8A8_UNORM: case VK_FORMAT_R8G8B8A8_SRGB: return E_RGBA;
    case VK_FORMAT_B8G8R8A8_UNORM: case VK_FORMAT_B8G8R8A8_SRGB: return E_BGRA;
    case VK_FORMAT_BC1_RGB_UNORM_BLOCK: case VK_FORMAT_BC1_RGB_SRGB_BLOCK:
    case VK_FORMAT_BC1_RGBA_UNORM_BLOCK: case VK_FORMAT_BC1_RGBA_SRGB_BLOCK: return E_BC1;
    case VK_FORMAT_BC3_UNORM_BLOCK: case VK_FORMAT_BC3_SRGB_BLOCK: return E_BC3;
    case VK_FORMAT_BC5_UNORM_BLOCK: return E_BC5U;
    case VK_FORMAT_BC5_SNORM_BLOCK: return E_BC5S;
    default: return E_NONE;
    }
}

static uint64_t level_bytes(int enc, uint32_t w, uint32_t h) {
    uint64_t blocks = (uint64_t)((w + 3) / 4) * ((h + 3) / 4);
    return enc == E_RGBA || enc == E_BGRA ? (uint64_t)w * h * 4 : enc == E_BC1 ? blocks * 8 : blocks * 16;
}

// An image this can write: 2D (or a 2D array), single-sampled, not a cube, in a format above, its components in their
// own order.
static int usable(const VkTexImage *img, const TexVkFormat *f, int colour) {
    int e = enc_of(img->format);
    if (img->type != VK_IMAGE_TYPE_2D || img->depth > 1 || img->samples > 1 || f->chan[0] ||
        img->view_type == VK_IMAGE_VIEW_TYPE_CUBE || img->view_type == VK_IMAGE_VIEW_TYPE_CUBE_ARRAY ||
        img->levels > MAX_LEVELS || img->layers > MAX_LAYERS || !img->width || !img->height)
        return 0;
    if (e == E_NONE || (colour && (e == E_BC5U || e == E_BC5S))) {
        static uint32_t said[8];
        static int nsaid;
        int known = 0;
        for (int i = 0; i < nsaid; i++) known |= said[i] == (uint32_t)img->format;
        if (!known && nsaid < 8) {
            said[nsaid++] = (uint32_t)img->format;
            rt_log("n64: world: a texture of VkFormat %d keeps the remake's content (not a format this writes)",
                   (int)img->format);
        }
        return 0;
    }
    return 1;
}

// ---- BC1, BC3 and BC5 blocks of RGBA8 texels

static void fetch(const uint8_t *rgba, uint32_t w, uint32_t h, uint32_t bx, uint32_t by, uint8_t px[16][4]) {
    for (uint32_t y = 0; y < 4; y++)
        for (uint32_t x = 0; x < 4; x++)
            memcpy(px[y * 4 + x], rgba + ((size_t)umin(by * 4 + y, h - 1) * w + umin(bx * 4 + x, w - 1)) * 4, 4);
}

static uint16_t to565(int r, int g, int b) {
    return (uint16_t)(((r * 31 + 127) / 255) << 11 | ((g * 63 + 127) / 255) << 5 | ((b * 31 + 127) / 255));
}

static void from565(uint16_t c, int o[3]) {
    int r = c >> 11 & 31, g = c >> 5 & 63, b = c & 31;
    o[0] = r << 3 | r >> 2, o[1] = g << 2 | g >> 4, o[2] = b << 3 | b >> 2;
}

// Four-colour BC1: the endpoints the block's box, inset by a sixteenth; each texel the nearest of the four colours
// along the line between them.
static void bc1_block(uint8_t px[16][4], uint8_t out[8]) {
    int mn[3] = {255, 255, 255}, mx[3] = {0, 0, 0};
    for (int i = 0; i < 16; i++)
        for (int c = 0; c < 3; c++) {
            if (px[i][c] < mn[c]) mn[c] = px[i][c];
            if (px[i][c] > mx[c]) mx[c] = px[i][c];
        }
    for (int c = 0; c < 3; c++) {
        int in = (mx[c] - mn[c]) >> 4;
        mn[c] += in, mx[c] -= in;
    }
    uint16_t c0 = to565(mx[0], mx[1], mx[2]), c1 = to565(mn[0], mn[1], mn[2]);
    uint32_t idx = 0;
    if (c0 != c1) {   // c0 > c1: each channel of the maximum is at least the minimum's (four-colour mode)
        int e0[3], e1[3];
        from565(c0, e0), from565(c1, e1);
        int d[3] = {e1[0] - e0[0], e1[1] - e0[1], e1[2] - e0[2]};
        float dd = (float)(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]);
        for (int i = 0; i < 16; i++) {
            float t = dd > 0 ? (float)((px[i][0] - e0[0]) * d[0] + (px[i][1] - e0[1]) * d[1] +
                                       (px[i][2] - e0[2]) * d[2]) / dd : 0.0f;
            uint32_t q = t < 1.0f / 6 ? 0 : t < 0.5f ? 2 : t < 5.0f / 6 ? 3 : 1;
            idx |= q << (2 * i);
        }
    }
    out[0] = (uint8_t)c0, out[1] = (uint8_t)(c0 >> 8), out[2] = (uint8_t)c1, out[3] = (uint8_t)(c1 >> 8);
    memcpy(out + 4, &idx, 4);
}

// An eight-value BC4 block of 16 values (BC3's alpha, BC5's channels).
static void bc4_block(const uint8_t v[16], uint8_t out[8]) {
    int a0 = 0, a1 = 255;
    for (int i = 0; i < 16; i++) {
        if (v[i] > a0) a0 = v[i];
        if (v[i] < a1) a1 = v[i];
    }
    uint64_t bits = 0;
    if (a0 != a1)
        for (int i = 0; i < 16; i++) {
            int q = (int)((float)(a0 - v[i]) / (float)(a0 - a1) * 7.0f + 0.5f);
            uint64_t k = q <= 0 ? 0 : q >= 7 ? 1 : (uint64_t)q + 1;
            bits |= k << (3 * i);
        }
    out[0] = (uint8_t)a0, out[1] = (uint8_t)a1;
    for (int i = 0; i < 6; i++) out[2 + i] = (uint8_t)(bits >> (8 * i));
}

static void encode(int enc, const uint8_t *rgba, uint32_t w, uint32_t h, uint8_t *out) {
    if (enc == E_RGBA) {
        memcpy(out, rgba, (size_t)w * h * 4);
        return;
    }
    if (enc == E_BGRA) {
        for (size_t i = 0; i < (size_t)w * h; i++)
            out[4 * i] = rgba[4 * i + 2], out[4 * i + 1] = rgba[4 * i + 1], out[4 * i + 2] = rgba[4 * i],
            out[4 * i + 3] = rgba[4 * i + 3];
        return;
    }
    uint32_t bw = (w + 3) / 4, bh = (h + 3) / 4, bytes = enc == E_BC1 ? 8 : 16;
    for (uint32_t by = 0; by < bh; by++)
        for (uint32_t bx = 0; bx < bw; bx++) {
            uint8_t px[16][4], v[16], *o = out + ((size_t)by * bw + bx) * bytes;
            fetch(rgba, w, h, bx, by, px);
            if (enc == E_BC1) {
                bc1_block(px, o);
            } else if (enc == E_BC3) {
                for (int i = 0; i < 16; i++) v[i] = px[i][3];
                bc4_block(v, o);
                bc1_block(px, o + 8);
            } else if (enc == E_BC5U) {
                for (int c = 0; c < 2; c++) {
                    for (int i = 0; i < 16; i++) v[i] = px[i][c];
                    bc4_block(v, o + 8 * c);
                }
            } else {
                memset(o, 0, 16);   // BC5 SNORM: only the flat normal (0, 0) is written
            }
        }
}

// ---- the content of an image

typedef struct {
    int tex, rep, enc, alpha;          // tex: the N64 texture's index in world.bin, FLAT; alpha: -1 the texture's own
    uint32_t w, h, levels;
    uint8_t *data;
    uint64_t size, off[MAX_LEVELS];
    uint64_t used;
} Made;
static Made made[MADE_N];
static uint64_t made_bytes, made_tick;

// Level 0: the N64 texture repeated over w x h, bilinear with wrap-around: rep times along the way it repeats least,
// and along the other as many whole times as keep its texels square (a 64 x 32 rock over a square image repeats twice
// as often down it; stretched once each way it would draw at twice its height).
static void tile_level(const N64WorldTex *nt, int rep, uint32_t w, uint32_t h, int alpha, uint8_t *out) {
    float k = (float)h * (float)nt->w / ((float)w * (float)nt->h);   // the repeats down per repeat across, square texels
    float rx = (float)rep, ry = (float)rep;
    if (k >= 1.0f) ry = fmaxf(1.0f, roundf((float)rep * k));
    else rx = fmaxf(1.0f, roundf((float)rep / k));
    float sx = (float)nt->w * rx / (float)w, sy = (float)nt->h * ry / (float)h;
    int nw = (int)nt->w, nh = (int)nt->h;
    for (uint32_t y = 0; y < h; y++) {
        float v = ((float)y + 0.5f) * sy - 0.5f, fy = v - floorf(v);
        int y0 = (((int)floorf(v)) % nh + nh) % nh, y1 = (y0 + 1) % nh;
        for (uint32_t x = 0; x < w; x++) {
            float u = ((float)x + 0.5f) * sx - 0.5f, fx = u - floorf(u);
            int x0 = (((int)floorf(u)) % nw + nw) % nw, x1 = (x0 + 1) % nw;
            uint32_t t00 = nt->texels[y0 * nw + x0], t10 = nt->texels[y0 * nw + x1];
            uint32_t t01 = nt->texels[y1 * nw + x0], t11 = nt->texels[y1 * nw + x1];
            uint8_t *o = out + ((size_t)y * w + x) * 4;
            for (int c = 0; c < 4; c++) {
                float a = (float)(t00 >> (8 * c) & 255) * (1 - fx) + (float)(t10 >> (8 * c) & 255) * fx;
                float b = (float)(t01 >> (8 * c) & 255) * (1 - fx) + (float)(t11 >> (8 * c) & 255) * fx;
                o[c] = (uint8_t)(a * (1 - fy) + b * fy + 0.5f);
            }
            if (alpha >= 0) o[3] = (uint8_t)alpha;
        }
    }
}

static void half_level(const uint8_t *s, uint32_t sw, uint32_t sh, uint8_t *d, uint32_t dw, uint32_t dh) {
    for (uint32_t y = 0; y < dh; y++)
        for (uint32_t x = 0; x < dw; x++) {
            uint32_t x0 = umin(2 * x, sw - 1), x1 = umin(2 * x + 1, sw - 1);
            uint32_t y0 = umin(2 * y, sh - 1), y1 = umin(2 * y + 1, sh - 1);
            for (int c = 0; c < 4; c++)
                d[((size_t)y * dw + x) * 4 + c] =
                    (uint8_t)((s[((size_t)y0 * sw + x0) * 4 + c] + s[((size_t)y0 * sw + x1) * 4 + c] +
                               s[((size_t)y1 * sw + x0) * 4 + c] + s[((size_t)y1 * sw + x1) * 4 + c] + 2) / 4);
        }
}

static void made_drop(Made *m) {
    made_bytes -= m->size;
    free(m->data);
    memset(m, 0, sizeof *m);
}

// Every level of the content, encoded one after the other (each at a multiple of 16 bytes); kept in made[] for the
// next image of the same parameters. NULL without memory.
static const Made *content(int tex, const N64WorldTex *nt, int rep, int enc, int alpha, uint32_t w, uint32_t h,
                           uint32_t levels) {
    made_tick++;
    for (int i = 0; i < MADE_N; i++) {
        Made *m = &made[i];
        if (m->data && m->tex == tex && m->rep == rep && m->enc == enc && m->alpha == alpha && m->w == w &&
            m->h == h && m->levels == levels) {
            m->used = made_tick;
            return m;
        }
    }
    uint64_t size = 0, off[MAX_LEVELS];
    for (uint32_t lv = 0; lv < levels; lv++) off[lv] = size, size += (level_bytes(enc, mip(w, lv), mip(h, lv)) + 15) & ~15ull;
    uint8_t *data = calloc(1, size), *a = malloc((size_t)w * h * 4), *b = malloc((size_t)w * h * 4);
    if (!data || !a || !b) {
        free(data), free(a), free(b);
        return NULL;
    }
    if (tex == FLAT) {
        for (size_t i = 0; i < (size_t)w * h; i++) a[4 * i] = 128, a[4 * i + 1] = 128, a[4 * i + 2] = 255, a[4 * i + 3] = 128;
    } else {
        tile_level(nt, rep, w, h, alpha, a);
    }
    for (uint32_t lv = 0; lv < levels; lv++) {
        uint32_t lw = mip(w, lv), lh = mip(h, lv);
        if (lv) {
            half_level(a, mip(w, lv - 1), mip(h, lv - 1), b, lw, lh);
            uint8_t *t = a;
            a = b, b = t;
        }
        encode(enc, a, lw, lh, data + off[lv]);
    }
    free(a), free(b);
    // room: the least lately used go
    while (made_bytes + size > MADE_BYTES || made[MADE_N - 1].data) {
        int lru = -1;
        for (int i = 0; i < MADE_N; i++)
            if (made[i].data && (lru < 0 || made[i].used < made[lru].used)) lru = i;
        if (lru < 0) break;
        made_drop(&made[lru]);
        for (int i = lru; i + 1 < MADE_N; i++) made[i] = made[i + 1];   // keep the used ones first
        memset(&made[MADE_N - 1], 0, sizeof made[0]);
    }
    Made *m = NULL;
    for (int i = 0; i < MADE_N && !m; i++)
        if (!made[i].data) m = &made[i];
    if (!m) {
        free(data);
        return NULL;
    }
    *m = (Made){.tex = tex, .rep = rep, .enc = enc, .alpha = alpha, .w = w, .h = h, .levels = levels, .data = data,
                .size = size, .used = made_tick};
    memcpy(m->off, off, sizeof off);
    made_bytes += size;
    return m;
}

static void barrier(VkCommandBuffer cb, VkPipelineStageFlags2 src, VkAccessFlags2 sa, VkPipelineStageFlags2 dst,
                    VkAccessFlags2 da) {
    VkMemoryBarrier2 mb = {.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER_2, .srcStageMask = src, .srcAccessMask = sa,
                           .dstStageMask = dst, .dstAccessMask = da};
    VkDependencyInfo di = {.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO, .memoryBarrierCount = 1, .pMemoryBarriers = &mb};
    vkCmdPipelineBarrier2(cb, &di);
}
#define ALL_RW VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT, VK_ACCESS_2_MEMORY_READ_BIT | VK_ACCESS_2_MEMORY_WRITE_BIT

// The content into every level of the given layers of img, in the open upload batch (after the upload's own copies,
// which its barrier orders before this one's).
static int record(const VkTexImage *img, const Made *m, const uint32_t *layers, uint32_t nl) {
    VkBufferImageCopy *rg = calloc((size_t)m->levels * nl, sizeof *rg);
    if (!rg) return 0;
    VkCommandBuffer cb = vk_upload_begin();
    VkBuffer sb = VK_NULL_HANDLE;
    VkDeviceSize soff = 0;
    uint8_t *stg = vk_staging_at(m->size, &sb, &soff);
    if (!stg) {
        vk_upload_end();
        free(rg);
        return 0;
    }
    memcpy(stg, m->data, m->size);
    uint32_t k = 0;
    for (uint32_t lv = 0; lv < m->levels; lv++)
        for (uint32_t i = 0; i < nl; i++)
            rg[k++] = (VkBufferImageCopy){.bufferOffset = soff + m->off[lv],
                                          .imageSubresource = {img->aspect, lv, layers[i], 1},
                                          .imageExtent = {mip(m->w, lv), mip(m->h, lv), 1}};
    barrier(cb, ALL_RW, VK_PIPELINE_STAGE_2_COPY_BIT, VK_ACCESS_2_TRANSFER_WRITE_BIT);
    vkCmdCopyBufferToImage(cb, sb, img->image, VK_IMAGE_LAYOUT_GENERAL, k, rg);
    barrier(cb, VK_PIPELINE_STAGE_2_COPY_BIT, VK_ACCESS_2_TRANSFER_WRITE_BIT, ALL_RW);
    vk_upload_end();
    free(rg);
    return 1;
}

// How many times the N64 texture repeats over the image: once per repeat of the remake texture, or more when one of
// its repeats spans several of the N64 texture's in the N64 room.
static int repeat_of(int entry, const N64WorldTex *nt) {
    float rm = n64world_metres(entry), nm = nt->metres;
    if (rm <= 0 || nm <= 0) return 1;
    int r = (int)lroundf(rm / nm);
    return r < 1 ? 1 : r > 8 ? 8 : r;
}

// ---- the terrain's material array: each layer's category by its colour

typedef struct {
    int entry;
    uint32_t n;
    int8_t cat[MAX_LAYERS];
    uint8_t alpha[MAX_LAYERS];
} Layers;
static Layers lay[4];
static int nlay;

static const Layers *layers_of(int entry, const VkTexImage *img, const tex_t *t, const TexVkFormat *f) {
    for (int i = 0; i < nlay; i++)
        if (lay[i].entry == entry) return &lay[i];
    const nvn_texdesc *d = &t->d;
    if (nlay >= 4 || !f->astc_bw || nvn_tex_is_linear(d) || !t->mem) return NULL;
    unsigned gl = 0;
    while (mip(d->width, gl) > 32 && gl + 1 < d->levels) gl++;
    uint32_t lw = mip(d->width, gl), lh = mip(d->height, gl), bw = f->astc_bw, bh = f->astc_bh;
    uint32_t rb = (lw + bw - 1) / bw * 16, rows = (lh + bh - 1) / bh;
    uint8_t *blocks = malloc((size_t)rb * rows), *rgba = malloc((size_t)lw * lh * 4);
    if (!blocks || !rgba || !mem_read_hold(img->addr, t->L.size)) {
        free(blocks), free(rgba);
        return NULL;
    }
    Layers *L = &lay[nlay++];
    L->entry = entry, L->n = umin(img->layers, MAX_LAYERS);
    int counts[N64W_CATS + 1] = {0};
    for (uint32_t ly = 0; ly < L->n; ly++) {
        L->cat[ly] = -1, L->alpha[ly] = 255;
        if (nvn_deswizzle(d, gl, ly, t->mem, blocks, rb)) continue;
        astc_decode_ldr(blocks, lw, lh, bw, bh, f->astc_srgb != 0, rgba);
        uint64_t s[4] = {0};
        for (size_t i = 0; i < (size_t)lw * lh; i++)
            for (int c = 0; c < 4; c++) s[c] += rgba[4 * i + c];
        uint64_t n = (uint64_t)lw * lh;
        L->cat[ly] = (int8_t)n64world_colour_category((int)(s[0] / n), (int)(s[1] / n), (int)(s[2] / n));
        L->alpha[ly] = (uint8_t)(s[3] / n);
        counts[L->cat[ly] < 0 ? N64W_CATS : L->cat[ly]]++;
    }
    mem_read_release();
    free(blocks), free(rgba);
    rt_log("n64: world: texture array %ux%u, %u layers by colour: %d grass, %d dirt, %d rock, %d kept", d->width,
           d->height, L->n, counts[N64W_GRASS], counts[N64W_DIRT], counts[N64W_ROCK], counts[N64W_CATS]);
    return L;
}

// ---- the calls

int32_t vk_n64world_match(const VkTexImage *img, const tex_t *t) {
    static int nlog;
    if (!n64world_loaded()) return 0;
    if (!t->mem || t->L.size < N64W_KEY_BYTES) return -1;
    if (!mem_read_hold(img->addr, N64W_KEY_BYTES)) return 0;   // not mapped now: asked again at the next upload
    uint64_t k = n64world_key(t->mem, t->d.width, t->d.height);
    mem_read_release();
    int i = n64world_find(k);
    if (i < 0) return -1;
    nlog++;
    if (nlog <= 8 || !(nlog & (nlog - 1)))
        rt_log("n64: world: texture %#llx (%ux%u) is world.bin's entry %d (category %d, kind %d); %d found so far",
               (unsigned long long)img->addr, t->d.width, t->d.height, i, n64world_category(i), n64world_kind(i), nlog);
    return i + 1;
}

int vk_n64world_needs_opacity(int entry) { return n64world_kind(entry) == N64W_COLOUR; }

int vk_n64world_bc3_opaque(const tex_t *t, uint64_t addr) {
    if (!t->mem || !t->L.size || !mem_read_hold(addr, t->L.size)) return 0;
    const uint8_t *p = t->mem;
    int ok = 1;
    for (uint64_t i = 0; i + 16 <= t->L.size && ok; i += 16) {
        if (p[i] == 255 && p[i + 1] == 255) continue;   // both alpha endpoints 255: every texel is
        static const uint8_t zero[16];
        if (!memcmp(p + i, zero, 16)) continue;          // the padding between levels and layers
        ok = 0;
    }
    mem_read_release();
    return ok;
}

int vk_n64world_wants(int entry, int opaque, const VkTexImage *img, const TexVkFormat *f) {
    if (!n64world_state()) return 0;
    int kind = n64world_kind(entry);
    N64WorldTex nt;
    if (kind == N64W_ARRAY)
        return usable(img, f, 1) && (n64world_texture(N64W_GRASS, &nt) || n64world_texture(N64W_DIRT, &nt) ||
                                     n64world_texture(N64W_ROCK, &nt));
    if (kind == N64W_COLOUR && !opaque) return 0;
    return usable(img, f, kind == N64W_COLOUR) && n64world_texture(n64world_category(entry), &nt);
}

int vk_n64world_apply(const VkTexImage *img, const tex_t *t, const TexVkFormat *f, int entry, int opaque) {
    if (!vk_n64world_wants(entry, opaque, img, f)) return 0;
    int kind = n64world_kind(entry), enc = enc_of(img->format);
    uint32_t all[MAX_LAYERS];
    for (uint32_t i = 0; i < img->layers; i++) all[i] = i;
    N64WorldTex nt;
    if (kind == N64W_NORMAL) {
        const Made *m = content(FLAT, NULL, 0, enc, -1, img->width, img->height, img->levels);
        return m && record(img, m, all, img->layers);
    }
    if (kind == N64W_COLOUR) {
        if (!n64world_texture(n64world_category(entry), &nt)) return 0;
        const Made *m = content((int)nt.index, &nt, repeat_of(entry, &nt), enc, 255, img->width, img->height,
                                img->levels);
        return m && record(img, m, all, img->layers);
    }
    // the material array: the layers of each category and alpha (to a sixteenth) go in one copy
    const Layers *L = layers_of(entry, img, t, f);
    if (!L) return 0;
    uint8_t done[MAX_LAYERS] = {0};
    uint32_t group[MAX_LAYERS];
    int wrote = 0;
    for (uint32_t i = 0; i < L->n; i++) {
        if (done[i] || L->cat[i] < 0 || !n64world_texture(L->cat[i], &nt)) continue;
        uint32_t n = 0, asum = 0;
        for (uint32_t j = i; j < L->n; j++)
            if (!done[j] && L->cat[j] == L->cat[i] && L->alpha[j] >> 4 == L->alpha[i] >> 4)
                done[j] = 1, group[n++] = j, asum += L->alpha[j];
        const Made *m = content((int)nt.index, &nt, 1, enc, (int)(asum / n), img->width, img->height, img->levels);
        wrote |= m && record(img, m, group, n);
    }
    return wrote;
}
