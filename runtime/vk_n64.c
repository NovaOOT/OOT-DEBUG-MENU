// N64 Graphics' drawing (vk_n64.h). Runs whatever upscaler is on, or none: vk_n64_pass and vk_n64_pass_end follow
// the frame's passes to the bloom composite ("where in the frame" below) and draw into its picture as that pass ends,
// with the scene's own depth attached, tested and written, the camera the scene's draws used this frame
// (vk_debug_cam.c vk_dbgcam_vp) and the jitter they were drawn with (vk_dlaa.c vk_dlaa_scene_jitter). The game writes
// depth as ndc depth * 0.5 + 0.5 from that view-projection (OpenGL clip depth), so the N64 vertices do the same
// (n64_common.glsl out_pos), and what the remake draws covers them where it is nearer. Which way the depth runs is read
// from the view-projection each frame (depth_grows). The post pass, the upscaler if any and the HUD then take the
// picture with N64 Link in it.
//
// Each N64 file goes to the GPU once, as three buffers: its vertices (a vertex buffer), its materials and its texels
// (storage buffers the fragment shader reads its tiles from: the N64's textures are small, and wrapping, mirroring and
// the three-point filter are done by hand as the hardware did them, not by samplers). The nodes' matrices and the light
// setting go up every frame. A batch is one draw, its material passed as the instance number. Opaque batches first,
// with depth writes; then the see-through ones, blended, without. An item drawn as Dark Link (the Dark Link mod,
// n64gfx_dark) has its colours turned near black in the fragment shader, and the batches of his eye nodes red
// (pc.dark).
#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "host.h"
#include "n64place.h"
#include "platform/platform.h"
#include "vk.h"
#include "vk_debug_cam.h"
#include "vk_dlaa.h"
#include "vk_exec.h"
#include "vk_n64.h"
#include "vk_rec.h"
#include "n64_vs_spv.h"
#include "n64_fs_spv.h"

#define READY_NS 250000000ull
#define FORMATS 4
#define CACHE 48   // files kept on the GPU

typedef struct {
    float vp[16];
    float eye[4];
    float test[4];
    float tunic[4];   // the drawn item's tunic colour (N64Item tunic)
    float dark[4];    // x: 1 drawn as Dark Link (N64Item dark); y: 1 for his eyes' batches
} Push;   // n64_common.glsl Push: 128 bytes, the most every device takes

typedef struct {
    VkFormat fmt;
    VkPipeline opa, xlu;
} Pipes;

typedef struct {
    const N64File *file;
    VkBuffer vb, mb, tb;
    VmaAllocation vm, mm, tm;
    uint64_t used;
} Gpu;

static struct {
    int tried, ok;
    PFN_vkCmdPushDescriptorSetKHR push;
    VkDescriptorSetLayout dsl;
    VkPipelineLayout layout;
    Pipes pipes[FORMATS];
    int npipes;
    VkImage dummy;
    VmaAllocation dummy_mem;
    VkImageView dummy_view;
    Gpu gpu[CACHE];
    uint64_t frame;
} G;

static volatile uint64_t g_ran_ns;

int vk_n64_ready(void) {
    uint64_t t = g_ran_ns;
    return t && plat_now_ns() - t < READY_NS;
}

static VkShaderModule module(const uint32_t *code, size_t size) {
    VkShaderModuleCreateInfo mi = {.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO, .codeSize = size, .pCode = code};
    VkShaderModule m = VK_NULL_HANDLE;
    return vkCreateShaderModule(vk_ctx()->device, &mi, NULL, &m) == VK_SUCCESS ? m : VK_NULL_HANDLE;
}

static int init(void) {
    G.tried = 1;
    const VkCtx *c = vk_ctx();
    G.push = (PFN_vkCmdPushDescriptorSetKHR)vk_dpa(c->device, "vkCmdPushDescriptorSetKHR");
    if (!G.push) return 0;
    VkDescriptorSetLayoutBinding b[5] = {
        {0, VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 1, VK_SHADER_STAGE_FRAGMENT_BIT, NULL},
        {1, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1, VK_SHADER_STAGE_FRAGMENT_BIT, NULL},
        {2, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1, VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, NULL},
        {3, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1, VK_SHADER_STAGE_VERTEX_BIT, NULL},
        {4, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1, VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, NULL}};
    VkDescriptorSetLayoutCreateInfo dci = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
                                           .flags = VK_DESCRIPTOR_SET_LAYOUT_CREATE_PUSH_DESCRIPTOR_BIT_KHR,
                                           .bindingCount = 5, .pBindings = b};
    if (vkCreateDescriptorSetLayout(c->device, &dci, NULL, &G.dsl) != VK_SUCCESS) return 0;
    VkPushConstantRange pr = {VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0, sizeof(Push)};
    VkPipelineLayoutCreateInfo li = {.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO, .setLayoutCount = 1,
                                     .pSetLayouts = &G.dsl, .pushConstantRangeCount = 1, .pPushConstantRanges = &pr};
    if (vkCreatePipelineLayout(c->device, &li, NULL, &G.layout) != VK_SUCCESS) return 0;
    G.ok = 1;
    return 1;
}

static VkPipeline pipeline(VkFormat fmt, int xlu) {
    const VkCtx *c = vk_ctx();
    VkShaderModule vs = module(n64_vs_spv, sizeof n64_vs_spv), fs = module(n64_fs_spv, sizeof n64_fs_spv);
    VkPipelineShaderStageCreateInfo st[2] = {
        {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, .stage = VK_SHADER_STAGE_VERTEX_BIT, .module = vs,
         .pName = "main"},
        {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, .stage = VK_SHADER_STAGE_FRAGMENT_BIT, .module = fs,
         .pName = "main"}};
    VkVertexInputBindingDescription bind = {0, sizeof(N64Vert), VK_VERTEX_INPUT_RATE_VERTEX};
    VkVertexInputAttributeDescription attr[4] = {
        {0, 0, VK_FORMAT_R32G32B32_SFLOAT, offsetof(N64Vert, x)},
        {1, 0, VK_FORMAT_R16G16_SINT, offsetof(N64Vert, s)},
        {2, 0, VK_FORMAT_R8G8B8A8_UNORM, offsetof(N64Vert, rgba)},
        {3, 0, VK_FORMAT_R16_UINT, offsetof(N64Vert, node)}};
    VkPipelineVertexInputStateCreateInfo vi = {.sType = VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO,
                                               .vertexBindingDescriptionCount = 1, .pVertexBindingDescriptions = &bind,
                                               .vertexAttributeDescriptionCount = 4, .pVertexAttributeDescriptions = attr};
    VkPipelineInputAssemblyStateCreateInfo ia = {.sType = VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO,
                                                 .topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST};
    VkPipelineViewportStateCreateInfo vp = {.sType = VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO};
    // the N64's own culling depends on each display list's geometry mode, which the mirroring of some placements turns
    // around: both faces are drawn
    VkPipelineRasterizationStateCreateInfo rs = {.sType = VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO,
                                                 .polygonMode = VK_POLYGON_MODE_FILL, .cullMode = VK_CULL_MODE_NONE,
                                                 .lineWidth = 1.0f};
    VkPipelineMultisampleStateCreateInfo ms = {.sType = VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO,
                                               .rasterizationSamples = VK_SAMPLE_COUNT_1_BIT};
    VkPipelineDepthStencilStateCreateInfo ds = {.sType = VK_STRUCTURE_TYPE_PIPELINE_DEPTH_STENCIL_STATE_CREATE_INFO,
                                                .depthTestEnable = VK_TRUE, .depthWriteEnable = !xlu,
                                                .depthCompareOp = VK_COMPARE_OP_LESS_OR_EQUAL};
    VkPipelineColorBlendAttachmentState ba = {
        .blendEnable = xlu ? VK_TRUE : VK_FALSE, .srcColorBlendFactor = VK_BLEND_FACTOR_SRC_ALPHA,
        .dstColorBlendFactor = VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA, .colorBlendOp = VK_BLEND_OP_ADD,
        .srcAlphaBlendFactor = VK_BLEND_FACTOR_ZERO, .dstAlphaBlendFactor = VK_BLEND_FACTOR_ONE,
        .alphaBlendOp = VK_BLEND_OP_ADD,
        .colorWriteMask = VK_COLOR_COMPONENT_R_BIT | VK_COLOR_COMPONENT_G_BIT | VK_COLOR_COMPONENT_B_BIT};
    VkPipelineColorBlendStateCreateInfo cbs = {.sType = VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO,
                                               .attachmentCount = 1, .pAttachments = &ba};
    VkDynamicState dyn[3] = {VK_DYNAMIC_STATE_VIEWPORT_WITH_COUNT, VK_DYNAMIC_STATE_SCISSOR_WITH_COUNT,
                             VK_DYNAMIC_STATE_DEPTH_COMPARE_OP};
    VkPipelineDynamicStateCreateInfo dsi = {.sType = VK_STRUCTURE_TYPE_PIPELINE_DYNAMIC_STATE_CREATE_INFO,
                                            .dynamicStateCount = 3, .pDynamicStates = dyn};
    VkFormat zf = VK_FORMAT_D32_SFLOAT;
    VkPipelineRenderingCreateInfo ri = {.sType = VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO,
                                        .colorAttachmentCount = 1, .pColorAttachmentFormats = &fmt,
                                        .depthAttachmentFormat = zf};
    VkGraphicsPipelineCreateInfo gi = {.sType = VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO, .pNext = &ri,
                                       .stageCount = 2, .pStages = st, .pVertexInputState = &vi,
                                       .pInputAssemblyState = &ia, .pViewportState = &vp, .pRasterizationState = &rs,
                                       .pMultisampleState = &ms, .pDepthStencilState = &ds, .pColorBlendState = &cbs,
                                       .pDynamicState = &dsi, .layout = G.layout};
    VkPipeline p = VK_NULL_HANDLE;
    if (!vs || !fs || vkCreateGraphicsPipelines(c->device, VK_NULL_HANDLE, 1, &gi, NULL, &p) != VK_SUCCESS) p = VK_NULL_HANDLE;
    if (vs) vkDestroyShaderModule(c->device, vs, NULL);
    if (fs) vkDestroyShaderModule(c->device, fs, NULL);
    return p;
}

static const Pipes *pipes_for(VkFormat fmt) {
    for (int i = 0; i < G.npipes; i++)
        if (G.pipes[i].fmt == fmt) return G.pipes[i].opa ? &G.pipes[i] : NULL;
    if (G.npipes == FORMATS) return NULL;
    Pipes *p = &G.pipes[G.npipes++];
    p->fmt = fmt;
    p->opa = pipeline(fmt, 0), p->xlu = pipeline(fmt, 1);
    if (!p->opa || !p->xlu) {
        rt_log("n64: the pipelines for light target format %d could not be made; N64 Graphics draws nothing", (int)fmt);
        p->opa = VK_NULL_HANDLE;
        return NULL;
    }
    return p;
}

// Binding 0 (the per-pixel distances an older path tested against) is not read with the scene's depth attached
// (pc.eye.w 2), but a push descriptor still needs an image: a 1x1 one, made once.
static int dummy_image(VkCommandBuffer cb) {
    if (G.dummy) return 1;
    const VkCtx *c = vk_ctx();
    VkImageCreateInfo ii = {.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO, .imageType = VK_IMAGE_TYPE_2D,
                            .format = VK_FORMAT_R32_SFLOAT, .extent = {1, 1, 1}, .mipLevels = 1, .arrayLayers = 1,
                            .samples = VK_SAMPLE_COUNT_1_BIT, .tiling = VK_IMAGE_TILING_OPTIMAL,
                            .usage = VK_IMAGE_USAGE_STORAGE_BIT};
    VmaAllocationCreateInfo ai = {.usage = VMA_MEMORY_USAGE_AUTO_PREFER_DEVICE};
    if (vmaCreateImage(c->vma, &ii, &ai, &G.dummy, &G.dummy_mem, NULL) != VK_SUCCESS) {
        G.dummy = VK_NULL_HANDLE;
        return 0;
    }
    VkImageViewCreateInfo vi = {.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO, .image = G.dummy,
                                .viewType = VK_IMAGE_VIEW_TYPE_2D, .format = VK_FORMAT_R32_SFLOAT,
                                .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1}};
    if (vkCreateImageView(c->device, &vi, NULL, &G.dummy_view) != VK_SUCCESS) {
        vmaDestroyImage(c->vma, G.dummy, G.dummy_mem);
        G.dummy = VK_NULL_HANDLE;
        return 0;
    }
    VkImageMemoryBarrier2 b = {.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2,
                               .srcStageMask = VK_PIPELINE_STAGE_2_NONE, .dstStageMask = VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT,
                               .oldLayout = VK_IMAGE_LAYOUT_UNDEFINED, .newLayout = VK_IMAGE_LAYOUT_GENERAL,
                               .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                               .image = G.dummy, .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1}};
    VkDependencyInfo di = {.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO, .imageMemoryBarrierCount = 1, .pImageMemoryBarriers = &b};
    vkCmdPipelineBarrier2(cb, &di);
    return 1;
}

// A buffer the CPU writes and the GPU reads, filled from src. Host memory the GPU reaches (or BAR memory): the files
// are a few MB, read once per frame at most.
static int host_buffer(const void *src, size_t size, VkBufferUsageFlags usage, VkBuffer *b, VmaAllocation *m) {
    if (!size) size = 16;
    VkBufferCreateInfo bi = {.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, .size = size, .usage = usage};
    VmaAllocationCreateInfo ai = {.usage = VMA_MEMORY_USAGE_AUTO,
                                  .flags = VMA_ALLOCATION_CREATE_HOST_ACCESS_SEQUENTIAL_WRITE_BIT | VMA_ALLOCATION_CREATE_MAPPED_BIT};
    VmaAllocationInfo info;
    if (vmaCreateBuffer(vk_ctx()->vma, &bi, &ai, b, m, &info) != VK_SUCCESS) return 0;
    if (src) memcpy(info.pMappedData, src, size);
    else memset(info.pMappedData, 0, size);
    vmaFlushAllocation(vk_ctx()->vma, *m, 0, VK_WHOLE_SIZE);
    return 1;
}

static void gpu_free(Gpu *g) {
    if (g->vb) vk_defer_buffer(g->vb, g->vm);
    if (g->mb) vk_defer_buffer(g->mb, g->mm);
    if (g->tb) vk_defer_buffer(g->tb, g->tm);
    memset(g, 0, sizeof *g);
}

// The file's buffers, uploaded on first use; the one used longest ago makes room.
static const Gpu *gpu_of(const N64File *f) {
    Gpu *old = &G.gpu[0];
    for (int i = 0; i < CACHE; i++) {
        if (G.gpu[i].file == f) {
            G.gpu[i].used = G.frame;
            return &G.gpu[i];
        }
        if (G.gpu[i].used < old->used) old = &G.gpu[i];
    }
    gpu_free(old);
    Gpu *g = old;
    const VkBufferUsageFlags sto = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
    if (!host_buffer(f->verts, (size_t)f->nverts * sizeof(N64Vert), VK_BUFFER_USAGE_VERTEX_BUFFER_BIT, &g->vb, &g->vm) ||
        !host_buffer(f->mats, (size_t)f->nmats * sizeof(N64Mat), sto, &g->mb, &g->mm) ||
        !host_buffer(f->texels, (size_t)f->ntexels * 4, sto, &g->tb, &g->tm)) {
        rt_log("n64: no memory for the %s %u", f->kind == N64G_SCENE ? "scene" : "object", f->number);
        gpu_free(g);
        return NULL;
    }
    g->file = f, g->used = G.frame;
    return g;
}

static int eye_of(const float *vp, float e[3]) {
    const float *a = vp, *b = vp + 4, *c = vp + 12;
    float det = a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0]);
    if (fabsf(det) < 1e-9f) return 0;
    float r[3] = {-a[3], -b[3], -c[3]};
    e[0] = (r[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (r[1] * c[2] - b[2] * r[2]) + a[2] * (r[1] * c[1] - b[1] * r[2])) / det;
    e[1] = (a[0] * (r[1] * c[2] - b[2] * r[2]) - r[0] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * r[2] - r[1] * c[0])) / det;
    e[2] = (a[0] * (b[1] * r[2] - r[1] * c[1]) - a[1] * (b[0] * r[2] - r[1] * c[0]) + r[0] * (b[0] * c[1] - b[1] * c[0])) / det;
    return 1;
}

// Whether the game's depth grows with distance from the view-projection: ndc depth of two points ahead of the eye.
static int depth_grows(const float *vp, const float eye[3]) {
    const float *z = vp + 8, *w = vp + 12;
    float f[3] = {w[0], w[1], w[2]}, n = sqrtf(f[0] * f[0] + f[1] * f[1] + f[2] * f[2]);
    if (!(n > 0)) return 1;
    float d[2];
    for (int k = 0; k < 2; k++) {
        float t = k ? 50.0f : 1.0f, p[3] = {eye[0] + f[0] / n * t, eye[1] + f[1] / n * t, eye[2] + f[2] / n * t};
        float cz = z[0] * p[0] + z[1] * p[1] + z[2] * p[2] + z[3], cw = w[0] * p[0] + w[1] * p[1] + w[2] * p[2] + w[3];
        d[k] = cw != 0 ? cz / cw : 0;
    }
    return d[1] >= d[0];
}

// Draws this frame's items into the light target cv (layout cl) with the scene depth zv (layout zl) attached.
static void draw_into(VkCommandBuffer cb, VkImageView cv, VkImageLayout cl, VkFormat fmt, uint32_t w, uint32_t h,
                      VkImageView zv, VkImageLayout zl, float jx, float jy) {
    if (!cv || !zv || w < 2 || h < 2) return;
    g_ran_ns = plat_now_ns();
    if (!G.tried) init();
    if (!G.ok || !dummy_image(cb)) return;
    G.frame++;
    static N64Item items[N64_ITEMS];
    N64Lights lights;
    memset(&lights, 0, sizeof lights);
    int n = n64_place_frame(items, N64_ITEMS, &lights);
    if (n <= 0) return;
    Push pc;
    memset(&pc, 0, sizeof pc);
    float aspect;
    if (!vk_dbgcam_vp(pc.vp, &aspect) || !eye_of(pc.vp, pc.eye)) return;
    const Pipes *P = pipes_for(fmt);
    if (!P) return;
    pc.eye[3] = 2.0f;   // the scene's own depth is attached (n64_vs.vert)
    VkCompareOp cmp = depth_grows(pc.vp, pc.eye) ? VK_COMPARE_OP_LESS_OR_EQUAL : VK_COMPARE_OP_GREATER_OR_EQUAL;
    pc.test[0] = 0.15f, pc.test[1] = 0.01f;
    pc.test[2] = 1.0f;   // brightness: the N64 colours as they are

    // this frame's node matrices (every item's, one after the other) and the light setting
    size_t nrows = 0;
    for (int i = 0; i < n; i++) nrows += items[i].file->nnodes;
    float *rows = malloc((nrows ? nrows : 1) * 12 * sizeof(float));
    if (!rows) return;
    uint32_t *first_node = calloc((size_t)n, sizeof *first_node);
    if (!first_node) {
        free(rows);
        return;
    }
    size_t at = 0;
    for (int i = 0; i < n; i++) {
        first_node[i] = (uint32_t)at;
        memcpy(rows + 12 * at, items[i].rows, (size_t)items[i].file->nnodes * 12 * sizeof(float));
        at += items[i].file->nnodes;
    }
    VkBuffer rb, lb;
    VmaAllocation rm, lm;
    int ok = host_buffer(rows, nrows * 12 * sizeof(float), VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, &rb, &rm);
    free(rows);
    if (!ok) {
        free(first_node);
        return;
    }
    if (!host_buffer(&lights, sizeof lights, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, &lb, &lm)) {
        vk_defer_buffer(rb, rm);
        free(first_node);
        return;
    }
    vk_defer_buffer(rb, rm), vk_defer_buffer(lb, lm);   // freed once this frame's submission is done

    VkMemoryBarrier2 mb = {.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER_2,
                           .srcStageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT, .srcAccessMask = VK_ACCESS_2_MEMORY_WRITE_BIT,
                           .dstStageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                           .dstAccessMask = VK_ACCESS_2_MEMORY_READ_BIT | VK_ACCESS_2_MEMORY_WRITE_BIT};
    VkDependencyInfo di = {.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO, .memoryBarrierCount = 1, .pMemoryBarriers = &mb};
    vkCmdPipelineBarrier2(cb, &di);
    VkRenderingAttachmentInfo ca = {.sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO, .imageView = cv,
                                    .imageLayout = cl, .loadOp = VK_ATTACHMENT_LOAD_OP_LOAD,
                                    .storeOp = VK_ATTACHMENT_STORE_OP_STORE};
    VkRenderingAttachmentInfo za = {.sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO, .imageView = zv,
                                    .imageLayout = zl, .loadOp = VK_ATTACHMENT_LOAD_OP_LOAD,
                                    .storeOp = VK_ATTACHMENT_STORE_OP_STORE};
    VkRenderingInfo ri = {.sType = VK_STRUCTURE_TYPE_RENDERING_INFO, .renderArea = {{0, 0}, {w, h}}, .layerCount = 1,
                          .colorAttachmentCount = 1, .pColorAttachments = &ca, .pDepthAttachment = &za};
    vkCmdBeginRendering(cb, &ri);
    VkViewport v = {jx, (float)h + jy, (float)w, -(float)h, 0.0f, 1.0f};
    VkRect2D sc = {{0, 0}, {w, h}};
    vkCmdSetViewportWithCount(cb, 1, &v);
    vkCmdSetScissorWithCount(cb, 1, &sc);
    vkCmdSetDepthCompareOp(cb, cmp);
    const VkShaderStageFlags stages = VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT;
    int draws = 0;
    for (int pass = 0; pass < 2; pass++) {
        vkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_GRAPHICS, pass ? P->xlu : P->opa);
        pc.test[3] = (float)pass;
        for (int i = 0; i < n; i++) {
            const N64File *f = items[i].file;
            uint32_t t = items[i].tunic;
            for (int k = 0; k < 4; k++) pc.tunic[k] = (float)((t >> (8 * k)) & 255) / 255.0f;
            pc.dark[0] = items[i].dark ? 1.0f : 0.0f, pc.dark[1] = 0.0f;
            vkCmdPushConstants(cb, G.layout, stages, 0, sizeof pc, &pc);
            // Dark Link's eyes: Link's eye nodes, one per eye texture (n64gfx.h N64_EYES after the items' rows), of
            // which n64pose.c turns one on
            uint32_t eyes[N64_EYES];
            int neyes = 0;
            const uint32_t face = N64_TUNICS + N64ROWS + N64XROWS + N64IROWS;
            if (items[i].dark && f->nplayer >= face + N64_EYES)
                for (int k = 0; k < N64_EYES; k++) eyes[neyes++] = f->player[face + k];
            const Gpu *g = gpu_of(f);
            if (!g) continue;
            VkDescriptorImageInfo dii = {VK_NULL_HANDLE, G.dummy_view, VK_IMAGE_LAYOUT_GENERAL};
            VkDescriptorBufferInfo bt = {g->tb, 0, VK_WHOLE_SIZE}, bm = {g->mb, 0, VK_WHOLE_SIZE},
                                   br = {rb, (VkDeviceSize)first_node[i] * 48, (VkDeviceSize)f->nnodes * 48 ? (VkDeviceSize)f->nnodes * 48 : 48},
                                   bl = {lb, 0, VK_WHOLE_SIZE};
            VkWriteDescriptorSet wr[5] = {
                {.sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstBinding = 0, .descriptorCount = 1,
                 .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, .pImageInfo = &dii},
                {.sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstBinding = 1, .descriptorCount = 1,
                 .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .pBufferInfo = &bt},
                {.sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstBinding = 2, .descriptorCount = 1,
                 .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .pBufferInfo = &bm},
                {.sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstBinding = 3, .descriptorCount = 1,
                 .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .pBufferInfo = &br},
                {.sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstBinding = 4, .descriptorCount = 1,
                 .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .pBufferInfo = &bl}};
            rec_push(G.push, cb, VK_PIPELINE_BIND_POINT_GRAPHICS, G.layout, 0, 5, wr);
            VkDeviceSize zero = 0;
            vkCmdBindVertexBuffers2(cb, 0, 1, &g->vb, &zero, NULL, NULL);
            for (uint32_t k = 0; k < f->nbatches; k++) {
                const N64Batch *b = &f->batches[k];
                if ((b->flags & 1) != pass || !b->count) continue;
                if (items[i].node_on && b->node < f->nnodes && !items[i].node_on[b->node]) continue;
                if (neyes) {
                    int eye = 0;
                    for (int e = 0; e < neyes; e++) eye |= b->node == eyes[e];
                    if ((float)eye != pc.dark[1]) {
                        pc.dark[1] = (float)eye;
                        vkCmdPushConstants(cb, G.layout, stages, 0, sizeof pc, &pc);
                    }
                }
                vkCmdDraw(cb, b->count, 1, b->first, b->mat);
                draws++;
            }
        }
    }
    vkCmdEndRendering(cb);
    free(first_node);
    (void)draws;
}

// ---------------------------------------------------------------- where in the frame
// Ocarina's frame, pass by pass (logged on a forest save with FSR at the scene's size; with no upscaler N64 Link is
// drawn at the same point. The post pass and the game's upscaler are compute dispatches):
//   the scene into the light target A (R11G11B10F) with the D32F scene depth, in several passes
//   the bloom chain: its first pass has two light-format targets smaller than A, no depth (bloom_start)
//   the composite: one full-screen draw into a second light target B with the scene depth, which adds the bloom,
//     applies the exposure and ends in the tonemap's curve: B holds the picture, linear 0..1
//   the post pass and the game's upscaler read B (DLSS or FSR take B there, vk_dlaa.c vk_dlaa_upscaler), then the HUD
// N64 Link is drawn into B as its pass ends, tested against the scene depth: so he is in the picture whatever reads
// it, and his colours are made for the tonemapped picture (n64place.c character_light). A frame without the bloom
// chain (a menu, a loading screen) draws no N64 Link, and the remake's stays (vk_n64_ready).
static struct {
    VkExecTarget color, depth;   // the last pass's light target and scene depth (A, then B)
    int armed, bloom, composite, drawn;
} W;
static volatile int32_t g_presented;

void vk_n64_frame(void) { __atomic_store_n(&g_presented, 1, __ATOMIC_RELEASE); }

static int light_format(VkFormat f) {
    return f == VK_FORMAT_B10G11R11_UFLOAT_PACK32 || f == VK_FORMAT_R16G16B16A16_SFLOAT;
}

static int plain(const VkExecTarget *t) { return t->image && t->samples <= 1 && !t->is3d; }

static int bloom_start(const VkExec *ex) {
    if (ex->pass_ncolor != 2 || ex->depth.image) return 0;
    for (int i = 0; i < 2; i++) {
        const VkExecTarget *t = &ex->color[i];
        if (!plain(t) || t->format != VK_FORMAT_B10G11R11_UFLOAT_PACK32 || t->width >= W.color.width ||
            t->height >= W.color.height)
            return 0;
    }
    return 1;
}

void vk_n64_pass(VkExec *ex) {
    if (__atomic_exchange_n(&g_presented, 0, __ATOMIC_ACQ_REL)) W.armed = W.bloom = W.composite = W.drawn = 0;
    if (!n64gfx_on()) return;
    const VkExecTarget *c = &ex->color[0], *z = &ex->depth;
    if (ex->pass_ncolor == 1 && plain(c) && light_format(c->format) && z->image && z->format == VK_FORMAT_D32_SFLOAT &&
        z->width >= DLAA_SCENE_MIN_W && c->width == z->width && c->height == z->height &&
        (!W.armed || (c->width >= W.color.width && c->height >= W.color.height))) {
        W.composite = W.bloom && c->image != W.color.image;
        W.color = *c, W.depth = *z, W.armed = 1;
    } else if (W.armed && bloom_start(ex)) {
        W.bloom = 1;
    }
}

void vk_n64_pass_end(VkExec *ex) {
    if (!W.composite || W.drawn || ex->color[0].image != W.color.image || ex->depth.image != W.depth.image) return;
    W.drawn = 1;
    static int told;
    if (!told++)
        rt_log("n64: drawn as the bloom composite's pass ends (light target %ux%u format %d)", W.color.width,
               W.color.height, (int)W.color.format);
    vk_exec_hook_turn();   // the replay workers' draws of a parallel group are done before these
    vk_exec_barrier(ex);   // the composite is done, and its targets are in GENERAL
    const VkExecTarget *t = &W.color, *d = &W.depth;
    VkImageView cv = vk_exec_target_view(t->img, t->format, VK_IMAGE_VIEW_TYPE_2D, VK_IMAGE_ASPECT_COLOR_BIT, t->level,
                                         t->base_layer, 1, 0);
    VkImageView zv = vk_exec_target_view(d->img, d->format, VK_IMAGE_VIEW_TYPE_2D, VK_IMAGE_ASPECT_DEPTH_BIT, d->level,
                                         d->base_layer, 1, 0);
    float jx, jy;
    vk_dlaa_scene_jitter(&jx, &jy);
    draw_into(ex->cb, cv, VK_IMAGE_LAYOUT_GENERAL, t->format, t->width, t->height, zv, VK_IMAGE_LAYOUT_GENERAL, jx, jy);
    // what reads the picture next (the post pass, the upscaler) sees what was drawn
    VkMemoryBarrier2 mb = {.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER_2, .srcStageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                           .srcAccessMask = VK_ACCESS_2_MEMORY_WRITE_BIT, .dstStageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                           .dstAccessMask = VK_ACCESS_2_MEMORY_READ_BIT | VK_ACCESS_2_MEMORY_WRITE_BIT};
    VkDependencyInfo di = {.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO, .memoryBarrierCount = 1, .pMemoryBarriers = &mb};
    vkCmdPipelineBarrier2(ex->cb, &di);
    ex->recorded += 4;
    // the draw bound its own pipeline, descriptors, vertex buffer and dynamic state: the replay's next draw (the pause
    // menu draws into this depth right after its preview's composite) records all of its own again
    vk_exec_draw_new_cb();
    ex->dirty = VK_EXEC_DIRTY_ALL;
}
