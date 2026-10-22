#version 320 es
// MiOS original: the window glides up into place from 6% below, fading in on an ease-out.
// @duration 0.26
precision highp float;

in vec2 v_texcoord;
out vec4 fragColor;
uniform sampler2D tex;
uniform float progress;
uniform vec4 window_rect;

void main() {
    float t = clamp(progress, 0.0, 1.0);
    float e = 1.0 - pow(1.0 - t, 3.0);
    vec2 local = (v_texcoord - window_rect.xy) / window_rect.zw;
    vec2 lu = local - vec2(0.0, 0.06 * (1.0 - e));
    if (lu.y < 0.0 || lu.y > 1.0) { fragColor = vec4(0.0); return; }
    vec4 src = texture(tex, window_rect.xy + lu * window_rect.zw);
    fragColor = src * e;
}
