#version 320 es
// MiOS original: the window burns away. A fractal-noise burn front eats the surface; just ahead
// of it the content chars and heat-shimmers, the front itself glows from the SSOT cursor orange
// through the error red into ash. Nothing is left at progress 1.
// @duration 0.62
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
const vec3 MIOS_ASH  = vec3(0.157, 0.133, 0.384); // @palette bg

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
    if (t >= 1.0) { fragColor = vec4(0.0); return; }
    vec2 local = (v_texcoord - window_rect.xy) / window_rect.zw;
    vec2 px = local * surface_size * window_rect.zw;

    // burn starts near the lower edge and climbs, broken up by fractal noise
    float n = fbm(px / 90.0 + seed * 53.0) * 0.75 + (1.0 - local.y) * 0.25;
    float front = t * 1.25;                 // > max(n) + band before t reaches 1
    float d = n - front;                    // < 0: burnt away

    // heat shimmer just ahead of the front
    float heat = 1.0 - smoothstep(0.0, 0.18, d);
    vec2 wob = vec2(vnoise(px / 9.0 + t * 40.0), vnoise(px / 9.0 - t * 40.0)) - 0.5;
    vec2 uv = v_texcoord + wob * heat * 4.0 / max(surface_size, vec2(1.0));
    vec4 src = texture(tex, uv);

    float alive = smoothstep(-0.015, 0.015, d);
    float glow  = (1.0 - smoothstep(0.0, 0.07, abs(d))) ;
    float char_ = 1.0 - smoothstep(0.02, 0.16, d);

    vec3 burnt = mix(src.rgb, MIOS_ASH * src.a, char_ * 0.85);
    vec3 fire  = mix(MIOS_DEEP, MIOS_HOT, smoothstep(0.05, 0.0, abs(d)));
    float alpha = src.a * max(alive, glow * 0.95);
    vec3 rgb = burnt * alive + fire * glow * src.a;
    fragColor = vec4(min(rgb, vec3(alpha)), alpha);
}
