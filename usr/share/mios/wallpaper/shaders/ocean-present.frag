// AI-hint: Native (Qt RHI) port of the PRES pass of usr/share/mios/branding/living-wallpaper.html -- the calm-ocean
//          presentation of the advected colour field (gentle wave normal from the height channel + ambient swell,
//          mode grade, sub-LSB dither, login spill from the centre). Upscales the low-resolution field bilinearly;
//          the height-gradient taps use the HTML's reference grid (u_ref) so shading strength matches the HTML.
// AI-related: usr/share/mios/wallpaper/MeshGradient.qml, usr/share/mios/wallpaper/shaders/ocean-sim.frag
// Build: qsb --glsl "300es,330" -O -o ocean-present.frag.qsb ocean-present.frag
#version 440

layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float u_time;
    float u_asp;
    float u_dark;      // 1 = dark grade, 0 = light grade ([theme].mode)
    float u_intro;     // 0..1 login spill progress
    vec2 u_ref;        // HTML reference sim grid (texels)
};
layout(binding = 1) uniform sampler2D u_field;

vec4 fieldAt(vec2 uvGL) { return texture(u_field, vec2(uvGL.x, 1.0 - uvGL.y)); }

float hash21(vec2 p){ p=fract(p*vec2(123.34,345.45)); p+=dot(p,p+34.345); return fract(p.x*p.y); }

void main(){
  vec2 uv=vec2(qt_TexCoord0.x, 1.0 - qt_TexCoord0.y);
  vec2 tx=1.35/u_ref;
  float hL=fieldAt(uv-vec2(tx.x,0.0)).a;
  float hR=fieldAt(uv+vec2(tx.x,0.0)).a;
  float hD=fieldAt(uv-vec2(0.0,tx.y)).a;
  float hU=fieldAt(uv+vec2(0.0,tx.y)).a;
  vec2 gb=vec2(hR-hL, hU-hD);
  vec2 pw=vec2(uv.x*u_asp, uv.y);
  float p1=dot(pw,vec2(3.1,1.7))+u_time*0.33, p2=dot(pw,vec2(-1.9,3.3))-u_time*0.26;
  vec2 gs=0.5*cos(p1)*vec2(3.1,1.7)+0.4*cos(p2)*vec2(-1.9,3.3);
  vec2 grad=gb*2.2 + gs*0.05;
  vec3 Nrm=normalize(vec3(-grad, 1.0));
  vec2 wuv=uv + gb*0.10 + gs*0.004;
  vec3 col=fieldAt(wuv).rgb;
  vec3 Ld=normalize(vec3(-0.30,0.50,0.81));
  float diff=0.5+0.5*dot(Nrm,Ld);
  col *= mix(0.93, 1.08, diff);
  // GRADE (mode-aware tone only), verbatim from the HTML
  float _L = dot(col, vec3(0.299,0.587,0.114));
  col = pow(max(col,0.0), vec3(mix(1.04, 1.32, u_dark)));
  col *= mix(1.0, 0.68, u_dark);
  float _knee = mix(0.70, 0.50, u_dark);
  col = col / (1.0 + max(vec3(0.0), col - _knee) * mix(0.9, 2.2, u_dark));
  col = mix(col, col * mix(0.95, 0.70, u_dark), smoothstep(0.55, 0.95, _L));
  col *= 0.99;
  col+=(hash21(gl_FragCoord.xy+fract(u_time))-0.5)/300.0;
  float _d=length((uv-0.5)*vec2(u_asp,1.0));
  float _front=u_intro*1.25;
  // smoothstep(_front, _front-0.18, _d) of the HTML, written out (GLSL leaves edge0 >= edge1 undefined)
  float s=clamp((_d-_front)/(-0.18), 0.0, 1.0); s=s*s*(3.0-2.0*s);
  col=mix(vec3(0.0), col, s);
  fragColor=vec4(clamp(col,0.0,1.0),1.0);
}
