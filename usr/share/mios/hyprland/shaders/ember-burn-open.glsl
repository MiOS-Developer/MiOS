#version 320 es
// MiOS original: the window kindles into place. The burn of ember-burn-close run backwards:
// a glowing front sweeps the surface in, leaving the content behind it.
// @duration 0.45
precision highp float;

in vec2 v_texcoord;
out vec4 fragColor;
uniform sampler2D tex;
uniform float progress;
uniform float seed;
uniform vec4 window_rect;
uniform vec2 surface_size;

const vec3 MIOS_HOT  = vec3(0.953, 0.361, 0.082); // @palette cursor
const vec3 MIOS_DEEP = vec3(0.863, 0.153, 0.106); // @palette error

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float vnoise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
}
float fbm(vec2 p) {
    float v = 0.0, a = 0.5;
    for (int i = 0; i < 4; ++i) { v += a * vnoise(p); p = p * 2.03 + 17.0; a *= 0.5; }
    return v;
}

void main() {
    float t = clamp(progress, 0.0, 1.0);
    vec2 local = (v_texcoord - window_rect.xy) / window_rect.zw;
    vec2 px = local * surface_size * window_rect.zw;
    vec4 src = texture(tex, v_texcoord);
    if (t >= 1.0) { fragColor = src; return; }

    float n = fbm(px / 90.0 + seed * 53.0) * 0.75 + local.y * 0.25;
    float front = t * 1.25;
    float d = front - n;                     // > 0: already kindled
    float alive = smoothstep(-0.015, 0.015, d);
    float glow  = (1.0 - smoothstep(0.0, 0.07, abs(d))) * (1.0 - t);
    vec3 fire   = mix(MIOS_DEEP, MIOS_HOT, smoothstep(0.05, 0.0, abs(d)));
    float alpha = src.a * max(alive, glow * 0.9);
    vec3 rgb = src.rgb * alive + fire * glow * src.a;
    fragColor = vec4(min(rgb, vec3(alpha)), alpha);
}
