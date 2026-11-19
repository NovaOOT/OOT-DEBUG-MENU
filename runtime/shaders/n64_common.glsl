// Shared by the N64 Graphics shaders (vk_n64.c): the push constants and the buffers. A fragment shader defines
// N64_FRAGMENT before it includes this.

layout(push_constant) uniform Push {
    vec4 vp[4];      // the view-projection's rows: world (the remake's metres) to clip (the viewport flips y)
    vec4 eye;        // xyz: the eye; w: 1 when the scene's view distances are there to test against
    vec4 test;       // x: slack in metres; y: slack as a share of the distance; z: brightness; w: 1 in the see-through pass
    vec4 tunic;      // the environment colour of the materials flagged as the tunic's (Material e.w bit 0)
    vec4 dark;       // x: 1 drawn as Dark Link; y: 1 for his eyes (vk_n64.c draw_into)
} pc;

// Each node's place: rows of a 3x4 matrix, N64 units to world. A node's normal turns with its rows' rotation.
layout(std430, set = 0, binding = 3) readonly buffer Nodes { vec4 rows[]; } nodes;

struct Material {
    uvec4 a;   // combine high, combine low, othermode high, othermode low
    uvec4 b;   // geometry mode, primitive colour, environment colour, primitive LOD | texture on << 8
    uvec4 c;   // texture scale s, t (float bits), tile 0 texel offset, tile 0 w | h << 16
    uvec4 d;   // tile 0 uls | ult << 16, tile 0 modes, tile 1 texel offset, tile 1 w | h << 16
    uvec4 e;   // tile 1 uls | ult << 16, tile 1 modes, fog colour, flags (1: env is the tunic colour, pc.tunic)
};
layout(std430, set = 0, binding = 2) readonly buffer Materials { Material m[]; } mats;

// The scene's light setting in N64 space: ambient, two directional lights, fog colour and range (world metres).
layout(std430, set = 0, binding = 4) readonly buffer Light {
    vec4 ambient;
    vec4 dir1, col1, dir2, col2;
    vec4 fog;   // rgb; a: 1 when fog is on
    vec4 fog_range;   // x: where it starts, y: where it is whole (metres from the eye)
} light;


vec4 to_clip(vec3 p) {
    vec4 h = vec4(p, 1.0);
    return vec4(dot(pc.vp[0], h), dot(pc.vp[1], h), dot(pc.vp[2], h), dot(pc.vp[3], h));
}

// Drawn against the scene's own depth, which the game writes as ndc depth * 0.5 + 0.5 of this view-projection
// (vk_n64.c draw_into).
vec4 out_pos(vec4 c) { return vec4(c.x, c.y, (c.z + c.w) * 0.5, c.w); }

vec3 srgb_to_linear(vec3 c) { return pow(max(c, vec3(0.0)), vec3(2.2)); }

vec4 unpack_rgba(uint v) { return vec4(v & 255u, (v >> 8) & 255u, (v >> 16) & 255u, v >> 24) / 255.0; }

#ifdef N64_FRAGMENT
layout(set = 0, binding = 0, r32f) uniform readonly image2D dist;   // each pixel's view distance, 0 where the sky shows
layout(std430, set = 0, binding = 1) readonly buffer Texels { uint t[]; } texels;

// Whether something of the scene's own (a character, an object left in) is nearer at this pixel than w.
bool behind_scene(float w) {
    if (pc.eye.w < 0.5 || pc.eye.w > 1.5) return false;   // 2: the depth test does it
    float d = imageLoad(dist, ivec2(gl_FragCoord.xy)).r;
    return d > 0.0 && w > d * (1.0 + pc.test.y) + pc.test.x;
}
#endif
