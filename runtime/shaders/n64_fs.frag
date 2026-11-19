#version 450
// N64 Graphics (vk_n64.c): the N64's colour combiner over its texture units, in one or two cycles, with its texture
// tiles' wrapping, mirroring and clamping, its three-point filter, alpha compare and fog.
#define N64_FRAGMENT
#include "n64_common.glsl"

layout(location = 0) in vec2 uv;
layout(location = 1) in vec4 shade_in;
layout(location = 2) in float vw;
layout(location = 3) flat in uint mat;

layout(location = 0) out vec4 o;

Material M;
vec4 tex0, tex1, prim, env, shade, comb;

// One texel of a tile at integer coordinates, wrapped, mirrored or clamped the tile's way.
int wrap(int i, int n, uint cm, uint mask) {
    if ((cm & 2u) != 0u || mask == 0u) {
        if (mask == 0u || (cm & 2u) != 0u && (i < 0 || i >= n)) return clamp(i, 0, n - 1);
    }
    int m = 1 << int(mask);
    int r = i & (m - 1);
    if ((cm & 1u) != 0u && ((i >> int(mask)) & 1) != 0) r = m - 1 - r;
    return min(r, n - 1);
}

vec4 texel(uint base, int w, int h, uvec2 cm, uvec2 mask, ivec2 p) {
    int x = wrap(p.x, w, cm.x, mask.x), y = wrap(p.y, h, cm.y, mask.y);
    return unpack_rgba(texels.t[base + uint(y * w + x)]);
}

float shift_scale(uint s) { return s == 0u ? 1.0 : s <= 10u ? 1.0 / float(1u << s) : float(1u << (16u - s)); }

vec4 sample_tile(uint base, uint wh, uint origin, uint modes) {
    if ((modes & (1u << 20)) == 0u) return vec4(0.0);
    int w = int(wh & 0xffffu), h = int(wh >> 16);
    uvec2 cm = uvec2(modes & 3u, (modes >> 2) & 3u), mask = uvec2((modes >> 4) & 15u, (modes >> 8) & 15u);
    vec2 sh = vec2(shift_scale((modes >> 12) & 15u), shift_scale((modes >> 16) & 15u));
    vec2 p = uv * sh - vec2(origin & 0xffffu, origin >> 16) / 4.0;
    uint filt = (M.a.z >> 12) & 3u;   // othermode high: G_TF_POINT 0, otherwise filtered
    if (filt == 0u) return texel(base, w, h, cm, mask, ivec2(floor(p)));
    // the three-point filter: the nearest corner and its two neighbours, by which half of the cell the point is in
    vec2 q = p - 0.5;
    ivec2 i = ivec2(floor(q));
    vec2 f = q - vec2(i);
    vec4 t00 = texel(base, w, h, cm, mask, i), t10 = texel(base, w, h, cm, mask, i + ivec2(1, 0)),
         t01 = texel(base, w, h, cm, mask, i + ivec2(0, 1)), t11 = texel(base, w, h, cm, mask, i + ivec2(1, 1));
    if (f.x + f.y <= 1.0) return t00 + f.x * (t10 - t00) + f.y * (t01 - t00);
    return t11 + (1.0 - f.x) * (t01 - t11) + (1.0 - f.y) * (t10 - t11);
}

vec3 c_a(uint i) {
    switch (i) {
        case 0u: return comb.rgb; case 1u: return tex0.rgb; case 2u: return tex1.rgb; case 3u: return prim.rgb;
        case 4u: return shade.rgb; case 5u: return env.rgb; case 6u: return vec3(1.0);
        case 7u: return vec3(fract(sin(dot(gl_FragCoord.xy, vec2(12.9898, 78.233))) * 43758.5453));
    }
    return vec3(0.0);
}
vec3 c_b(uint i) {
    switch (i) {
        case 0u: return comb.rgb; case 1u: return tex0.rgb; case 2u: return tex1.rgb; case 3u: return prim.rgb;
        case 4u: return shade.rgb; case 5u: return env.rgb;
    }
    return vec3(0.0);
}
vec3 c_c(uint i) {
    switch (i) {
        case 0u: return comb.rgb; case 1u: return tex0.rgb; case 2u: return tex1.rgb; case 3u: return prim.rgb;
        case 4u: return shade.rgb; case 5u: return env.rgb; case 7u: return vec3(comb.a);
        case 8u: return vec3(tex0.a); case 9u: return vec3(tex1.a); case 10u: return vec3(prim.a);
        case 11u: return vec3(shade.a); case 12u: return vec3(env.a); case 13u: return vec3(0.0);
        case 14u: return vec3(float(M.b.w & 255u) / 255.0);
    }
    return vec3(0.0);
}
vec3 c_d(uint i) {
    switch (i) {
        case 0u: return comb.rgb; case 1u: return tex0.rgb; case 2u: return tex1.rgb; case 3u: return prim.rgb;
        case 4u: return shade.rgb; case 5u: return env.rgb; case 6u: return vec3(1.0);
    }
    return vec3(0.0);
}
float a_abd(uint i) {
    switch (i) {
        case 0u: return comb.a; case 1u: return tex0.a; case 2u: return tex1.a; case 3u: return prim.a;
        case 4u: return shade.a; case 5u: return env.a; case 6u: return 1.0;
    }
    return 0.0;
}
float a_c(uint i) {
    switch (i) {
        case 1u: return tex0.a; case 2u: return tex1.a; case 3u: return prim.a; case 4u: return shade.a;
        case 5u: return env.a; case 6u: return float(M.b.w & 255u) / 255.0;
    }
    return 0.0;
}

// One cycle of the combiner: (a - b) * c + d for colour and alpha.
vec4 cycle(uint a, uint b, uint c, uint d, uint aa, uint ab, uint ac, uint ad) {
    vec3 rgb = (c_a(a) - c_b(b)) * c_c(c) + c_d(d);
    float al = (a_abd(aa) - a_abd(ab)) * a_c(ac) + a_abd(ad);
    return clamp(vec4(rgb, al), 0.0, 1.0);
}

void main() {
    if (behind_scene(vw)) discard;
    M = mats.m[mat];
    uint hi = M.a.x, lo = M.a.y;
    prim = unpack_rgba(M.b.y), shade = shade_in;
    env = (M.e.w & 1u) != 0u ? pc.tunic : unpack_rgba(M.b.z);
    tex0 = tex1 = vec4(0.0);
    if (((M.b.w >> 8) & 255u) != 0u) {
        tex0 = sample_tile(M.c.z, M.c.w, M.d.x, M.d.y);
        tex1 = sample_tile(M.d.z, M.d.w, M.e.x, M.e.y);
        if ((M.e.y & (1u << 20)) == 0u) tex1 = tex0;
    }
    comb = vec4(0.0);
    // G_SETCOMBINE: high word a0:4 c0:5 Aa0:3 Ac0:3 a1:4 c1:5; low word b0:4 b1:4 Aa1:3 Ac1:3 d0:3 Ab0:3 Ad0:3 d1:3 Ab1:3 Ad1:3
    uint a0 = (hi >> 20) & 15u, c0 = (hi >> 15) & 31u, aa0 = (hi >> 12) & 7u, ac0 = (hi >> 9) & 7u;
    uint a1 = (hi >> 5) & 15u, c1 = hi & 31u;
    uint b0 = (lo >> 28) & 15u, b1 = (lo >> 24) & 15u, aa1 = (lo >> 21) & 7u, ac1 = (lo >> 18) & 7u;
    uint d0 = (lo >> 15) & 7u, ab0 = (lo >> 12) & 7u, ad0 = (lo >> 9) & 7u, d1 = (lo >> 6) & 7u, ab1 = (lo >> 3) & 7u;
    uint ad1 = lo & 7u;
    comb = cycle(a0, b0, c0, d0, aa0, ab0, ac0, ad0);
    if (((M.a.z >> 20) & 3u) == 1u) comb = cycle(a1, b1, c1, d1, aa1, ab1, ac1, ad1);   // G_CYC_2CYCLE
    vec4 col = comb;
    // alpha compare (G_AC_THRESHOLD, or coverage times alpha on cut-outs): see-through texels are dropped
    uint ac = M.a.w & 3u;
    bool cut = ac != 0u || (M.a.w & 0x1000u) != 0u;
    bool xlu = pc.test.w > 0.5;
    if (cut && col.a < 0.5) discard;
    if (xlu && col.a < 1.0 / 255.0) discard;
    if (!xlu) col.a = 1.0;
    // Dark Link (the Dark Link mod, vk_n64.c): near black, keeping a faint share of each colour's light and dark so
    // his shape and the light on him still read. On his eyes (pc.dark.y) the eye texture's own texel decides: its
    // iris and pupil glow red, its white and the skin drawn around the eye are as dark as the rest of him.
    if (pc.dark.x > 0.5) {
        float l = dot(col.rgb, vec3(0.299, 0.587, 0.114));
        vec3 body = vec3(0.03, 0.03, 0.04) + vec3(0.22, 0.22, 0.27) * l;
        float iris = 0.0;
        if (pc.dark.y > 0.5) {
            vec3 t = tex0.rgb;
            float tl = dot(t, vec3(0.299, 0.587, 0.114));
            float white = smoothstep(0.55, 0.75, min(t.r, min(t.g, t.b)));
            float skin = smoothstep(0.04, 0.14, t.r - t.b) * smoothstep(0.30, 0.45, tl);
            iris = (1.0 - white) * (1.0 - skin);
        }
        col.rgb = mix(body, vec3(1.0, 0.06, 0.04), iris);
    }
    // fog: G_FOG with the scene's range
    if ((M.b.x & 0x10000u) != 0u && light.fog.a > 0.5) {
        float f = clamp((vw - light.fog_range.x) / max(light.fog_range.y - light.fog_range.x, 1e-3), 0.0, 1.0);
        col.rgb = mix(col.rgb, light.fog.rgb, f);
    }
    o = vec4(srgb_to_linear(col.rgb) * pc.test.z, col.a);
}
