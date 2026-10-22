#version 320 es
// MiOS original: the reverse of glass-dissolve-close. The window emerges from the wallpaper as
// diffused, specular frosted glass: a small lit glass ring appears at the centre and grows outwards
// as a soft circle while the pane glides forward from the background (scales up), its colour coming
// out of the SSOT palette mesh the wallpaper is built from, then the frost clears into the window.
// "@palette <key>" lines are rewritten from SSOT [colors] at login.
// @duration 0.45
precision highp float;

in vec2 v_texcoord;
out vec4 fragColor;
uniform sampler2D tex;
uniform float progress;
uniform float seed;
uniform vec4 window_rect;
uniform vec2 surface_size;

const vec3 MESH_A = vec3(0.157, 0.133, 0.384); // @palette bg
const vec3 MESH_B = vec3(0.102, 0.251, 0.498); // @palette accent
const vec3 MESH_C = vec3(0.953, 0.361, 0.082); // @palette cursor
const vec3 FROST  = vec3(0.718, 0.788, 0.843); // @palette subtle
const float GLIDE_TO    = 0.86;   // scale the pane recedes to as it glides into the background
const float FEATHER     = 0.38;   // softness of the collapsing circle edge (fraction of the radius)
const float MAX_BLUR_PX = 34.0;   // diffused frost
const float FROST_MIX   = 0.30;   // frost tint
const float MESH_MIX    = 0.55;   // how far the colour drifts into the palette mesh
const float GRAIN       = 0.04;   // frosted-glass grain
const float LENS_PX     = 26.0;   // refraction at the collapsing circle's edge (thick lens rim)
const float DISPERSION  = 0.30;   // colour fringing in that refraction
const float RIM         = 0.70;   // fresnel ring riding the circle edge
const float SPECULAR    = 0.55;   // diagonal specular streak sliding across the glass
const float SHEEN       = 0.18;   // broad top-left sheen (light from above-left)
const float EDGE_PX     = 90.0;   // soft rounded falloff that dissolves the window's edges and corners first
const float GLASS_BODY  = 0.42;   // opacity of the frosted glass body the open paints inside the circle

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float vnoise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
}

// two-ring golden-angle blur: wide, soft, diffused frost
vec4 diffused(vec2 uv, float radius_px) {
    if (radius_px < 0.5) return texture(tex, uv);
    vec2 px = radius_px / max(surface_size, vec2(1.0));
    vec4 acc = texture(tex, uv);
    float wsum = 1.0;
    for (int i = 1; i < 20; i++) {
        float fi = float(i);
        float r = sqrt(fi / 20.0);
        float a = fi * 2.39996323;
        float w = exp(-2.0 * r * r);
        acc += texture(tex, uv + vec2(cos(a), sin(a)) * r * px) * w;
        wsum += w;
    }
    return acc / wsum;
}

// slowly drifting mesh gradient in the wallpaper palette
vec3 palette_mesh(vec2 q, float t) {
    float n1 = vnoise(q * 1.6 + vec2(seed * 3.0, t * 0.6));
    float n2 = vnoise(q * 2.3 + vec2(t * 0.4, seed * 7.0) + 5.1);
    vec3 c = mix(MESH_A, MESH_B, smoothstep(0.2, 0.8, n1));
    return mix(c, MESH_C, 0.35 * smoothstep(0.55, 0.95, n2));
}

void main() {
    float t = 1.0 - clamp(progress, 0.0, 1.0);             // close timeline, played backwards
    if (t >= 1.0) { fragColor = vec4(0.0); return; }
    vec2 local = (v_texcoord - window_rect.xy) / window_rect.zw;
    vec2 win_px = surface_size * window_rect.zw;

    // glide back: the pane eases to a smaller scale about its centre
    float ease = t * t * (3.0 - 2.0 * t);
    float s = mix(1.0, GLIDE_TO, ease);
    vec2 lu = 0.5 + (local - 0.5) / s;
    if (any(lessThan(lu, vec2(0.0))) || any(greaterThan(lu, vec2(1.0)))) { fragColor = vec4(0.0); return; }
    vec2 uv = window_rect.xy + lu * window_rect.zw;

    // slow circular collapse: radius shrinks from the corner distance to zero, with a wide feather
    vec2 c = (lu - 0.5) * win_px;
    float rmax = length(0.5 * win_px);
    float R = rmax * (1.0 - t);                              // slow, steady collapse from the corners inwards
    float feather = max(R * FEATHER, 8.0) + 40.0 * t;
    float d = length(c);
    float iris = 1.0 - smoothstep(R - feather, R + 0.25 * feather, d);
    // lens rim: band just inside the circle edge; the content bends inwards there
    float band = exp(-pow((d - (R - 0.45 * feather)) / max(0.35 * feather, 4.0), 2.0)) * smoothstep(0.02, 0.15, t);
    vec2 dir = d > 0.001 ? c / d : vec2(0.0);
    vec2 off = -dir * LENS_PX * band / max(surface_size, vec2(1.0));
    uv += off;

    // diffused frosted glass, drifting into the palette mesh as it recedes
    float frost = smoothstep(0.0, 0.6, t);
    vec4 base = diffused(uv, MAX_BLUR_PX * frost);
    vec3 rgb = mix(base.rgb, FROST * base.a, FROST_MIX * frost);
    rgb = mix(rgb, palette_mesh(lu, t) * base.a, MESH_MIX * smoothstep(0.25, 1.0, t));
    rgb += (hash(floor(lu * win_px) + seed * 97.0) - 0.5) * GRAIN * frost * base.a;
    // chromatic fringe in the lens rim
    float fr = texture(tex, uv + off * DISPERSION).r;
    float fb = texture(tex, uv - off * DISPERSION).b;
    rgb = mix(rgb, vec3(fr, rgb.g, fb), band * 0.8);
    // glass lighting: fresnel ring on the rim, a specular streak sliding across, top-left sheen
    float streak = exp(-pow((lu.x + lu.y - 0.2 - 1.6 * ease) * 6.0, 2.0)) * SPECULAR * (1.0 - 0.6 * t);
    float sheen = SHEEN * (1.0 - 0.5 * (lu.x + lu.y)) * frost;
    rgb += FROST * (RIM * band + streak + sheen) * base.a;

    // soft rounded edge from the first frame: the window's straight edges and corners melt into the
    // wallpaper at once instead of showing as a rectangle while the circle is still larger than it
    vec2 e = min(lu, 1.0 - lu) * win_px;                       // distance to the nearest edges in px
    float edge_w = EDGE_PX * smoothstep(0.0, 0.2, t);
    float corner = length(max(vec2(edge_w) - e, 0.0));          // rounded-corner distance into the falloff
    float edge = 1.0 - smoothstep(0.0, max(edge_w, 1e-3), corner);
    float settle = 1.0 - smoothstep(0.0, 0.12, t);          // last 12% of the open: resolve to the full window
    iris = mix(iris, 1.0, settle);
    edge = mix(edge, 1.0, settle);
    float alpha = base.a * iris * edge * (1.0 - 0.5 * smoothstep(0.8, 1.0, t));
    // The pane itself: a frosted glass body inside the growing circle, independent of what the
    // app has drawn yet (first frames are often empty or transparent), lit like the window glass.
    float body = GLASS_BODY * iris * edge * smoothstep(0.0, 0.25, t) * (1.0 - 0.6 * smoothstep(0.75, 1.0, t));
    vec3 glass_rgb = mix(palette_mesh(lu, t), FROST, 0.55) * body
                   + FROST * (RIM * band + streak + sheen) * body;
    rgb = rgb * (alpha / max(base.a, 1e-4));                 // premultiply the content
    rgb = rgb + glass_rgb * (1.0 - alpha);                   // glass body behind the content
    alpha = alpha + body * (1.0 - alpha);
    fragColor = vec4(min(rgb, vec3(alpha)), alpha);
    return;
    fragColor = vec4(min(rgb * (alpha / max(base.a, 1e-4)), vec3(alpha)), alpha);
}
