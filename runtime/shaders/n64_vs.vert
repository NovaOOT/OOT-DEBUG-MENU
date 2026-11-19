#version 450
// N64 Graphics (vk_n64.c): a vertex of an N64 scene or object placed by its node's matrix. The material comes as the
// instance number (each batch is drawn with firstInstance set to its material).
#include "n64_common.glsl"

layout(location = 0) in vec3 pos;
layout(location = 1) in ivec2 st;
layout(location = 2) in vec4 rgba;   // the colour, or the normal (signed bytes) and alpha when the material lights
layout(location = 3) in uint node;

layout(location = 0) out vec2 uv;           // texels before the tile's shift and origin
layout(location = 1) out vec4 shade;
layout(location = 2) out float vw;
layout(location = 3) flat out uint mat;

void main() {
    vec4 r0 = nodes.rows[3 * node], r1 = nodes.rows[3 * node + 1], r2 = nodes.rows[3 * node + 2];
    vec4 h = vec4(pos, 1.0);
    vec3 w = vec3(dot(r0, h), dot(r1, h), dot(r2, h));
    vec4 c = to_clip(w);
    gl_Position = out_pos(c);
    vw = c.w;
    mat = gl_InstanceIndex;
    Material m = mats.m[mat];
    uv = vec2(st) / 32.0 * vec2(uintBitsToFloat(m.c.x), uintBitsToFloat(m.c.y));
    if ((m.b.x & 0x20000u) != 0u) {   // G_LIGHTING: the colour bytes are the normal
        vec3 b = floor(rgba.xyz * 255.0 + 0.5);
        vec3 m = mix(b, b - 256.0, step(128.0, b));
        vec3 n = normalize(vec3(dot(r0.xyz, m), dot(r1.xyz, m), dot(r2.xyz, m)) + vec3(1e-6));   // turned with the node
        vec3 l = light.ambient.rgb + light.col1.rgb * max(dot(n, light.dir1.xyz), 0.0) +
                 light.col2.rgb * max(dot(n, light.dir2.xyz), 0.0);
        shade = vec4(min(l, vec3(1.0)), rgba.a);
    } else shade = rgba;
}
