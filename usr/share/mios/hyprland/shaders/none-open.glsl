#version 320 es
// MiOS: per-class "no open effect" override. A specific +shader_open: tag outranks the global
// +shader_open_default:, so excluded windows get this pass-through instead of the global effect.
// @duration 0.01
precision highp float;
in vec2 v_texcoord;
out vec4 fragColor;
uniform sampler2D tex;
uniform float progress;
void main() { fragColor = texture(tex, v_texcoord); }
