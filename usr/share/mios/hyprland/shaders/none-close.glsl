#version 320 es
// MiOS: per-class "no close effect" override. @overlay hands the close back to Hyprland's own
// fadeOut, so this pass-through leaves the vanilla close animation untouched.
// @duration 0.01
// @overlay
precision highp float;
in vec2 v_texcoord;
out vec4 fragColor;
uniform sampler2D tex;
uniform float progress;
void main() { fragColor = texture(tex, v_texcoord) * (1.0 - step(1.0, progress)); }
