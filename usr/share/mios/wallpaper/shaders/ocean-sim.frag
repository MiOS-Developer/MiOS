// AI-hint: Native (Qt RHI) port of the SIM pass of usr/share/mios/branding/living-wallpaper.html -- advects + diffuses
//          the colour(rgb)+height(a) field and blends in the 24 wandering SSOT emitters. Same math as the HTML;
//          only the sampling grid is coarser: every kernel offset is expressed in the HTML's reference grid
//          (u_ref = canvas * sim_scale) so the look does not depend on the native internal resolution.
// AI-related: usr/share/mios/wallpaper/MeshGradient.qml, usr/share/mios/branding/living-wallpaper.html, [theme.wallpaper.motion]
// Build: qsb --glsl "300es,330" -O -o ocean-sim.frag.qsb ocean-sim.frag   (mios-wallpaper-shaders, tests/test-wallpaper-native.py)
#version 440

layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float u_time;      // sim time (wall seconds * speed), shared by every output
    float u_dt;        // advection step (1 / fps), as in the HTML
    float u_asp;       // canvas aspect (W / H)
    float u_reset;     // 1 = start from the cleared field (bg, height 0), as the HTML's FBO clear
    float u_emitters;  // emitter count (HTML: 24)
    vec2 u_ref;        // HTML reference sim grid (texels) -> kernel offsets in the same uv units
    vec4 u_bg;         // cleared-field colour (SSOT bg)
    vec4 p0;  vec4 p1;  vec4 p2;  vec4 p3;  vec4 p4;  vec4 p5;  vec4 p6;  vec4 p7;
    vec4 p8;  vec4 p9;  vec4 p10; vec4 p11; vec4 p12; vec4 p13; vec4 p14; vec4 p15;   // rgb = ansi_N, a = role weight
};
layout(binding = 1) uniform sampler2D u_prev;

// HTML uv: origin bottom-left (gl_FragCoord); Qt item/texture uv: origin top-left.
vec4 prevAt(vec2 uvGL) { return texture(u_prev, vec2(uvGL.x, 1.0 - uvGL.y)); }

float hash21(vec2 p){ p=fract(p*vec2(123.34,345.45)); p+=dot(p,p+34.345); return fract(p.x*p.y); }
float vnoise(vec2 p){ vec2 i=floor(p), f=fract(p);
  float a=hash21(i), b=hash21(i+vec2(1.,0.)), c=hash21(i+vec2(0.,1.)), d=hash21(i+vec2(1.,1.));
  vec2 u=f*f*f*(f*(f*6.-15.)+10.); return mix(mix(a,b,u.x),mix(c,d,u.x),u.y); }
float fbm(vec2 p){ float s=0., a=.55; for(int k=0;k<4;k++){ s+=a*vnoise(p); p=p*1.9+3.1; a*=.5; } return s; }
float pot(vec2 p, float t){ return fbm(p*0.9 + vec2(0.0,-t*0.15)); }
vec2 curl(vec2 p, float t){ float e=0.006;
  float n1=pot(p+vec2(0.,e),t), n2=pot(p-vec2(0.,e),t), n3=pot(p+vec2(e,0.),t), n4=pot(p-vec2(e,0.),t);
  return vec2(n1-n2, -(n3-n4))/(2.0*e); }

void emit(vec2 uv, out vec3 sc, out float sw){
  vec4 pal[16] = vec4[16](p0, p1, p2, p3, p4, p5, p6, p7, p8, p9, p10, p11, p12, p13, p14, p15);
  sc=vec3(0.0); sw=0.0;
  int n = int(u_emitters + 0.5);
  // Same CONSTANT trip count as the HTML (24): the compiler unrolls it and folds each emitter's seed at compile
  // time exactly as it does for the HTML. A runtime bound would evaluate sin(i*78.2+3.1) on the GPU instead and
  // move the emitters (seeds amplify the last bits of sin() by 43758.5).
  for(int k=0;k<24;k++){ if (k < n) { float i=float(k);
    int ci=k-(k/16)*16; float wk=pal[ci].a;
    vec2 seed=fract(sin(vec2(i*12.9+1.7, i*78.2+3.1))*43758.5);
    float ph=seed.x*40.0; float amp=0.34+0.10*seed.y;
    vec2 pos=vec2(0.5)+amp*vec2(
      0.55*sin(u_time*(0.030+0.015*seed.x)+ph)+0.45*sin(u_time*(0.021+0.010*seed.y)+ph*1.7),
      0.55*cos(u_time*(0.027+0.013*seed.y)+ph)+0.45*sin(u_time*(0.019+0.009*seed.x)+ph*2.1));
    float osc=0.5+0.5*sin(u_time*(0.14+0.03*i)+i*2.1);
    float gate=smoothstep(1.0-wk, 1.0-wk+0.20, osc);
    float breath=(0.35+0.65*osc)*gate*(0.45+0.55*wk);
    float r=0.13+0.06*wk;
    vec2 d=(uv-pos)*vec2(u_asp,1.0);
    float g=breath*exp(-dot(d,d)/(2.0*r*r));
    sc+=pal[ci].rgb*g; sw+=g; } }
}

vec4 fetchBlur(vec2 c){
  if (u_reset > 0.5) return vec4(u_bg.rgb, 0.0);
  vec2 tx=2.6/u_ref;
  vec4 b=prevAt(c)*4.0;
  b+=(prevAt(c+vec2(tx.x,0.0))+prevAt(c-vec2(tx.x,0.0))+prevAt(c+vec2(0.0,tx.y))+prevAt(c-vec2(0.0,tx.y)))*2.0;
  b+=prevAt(c+tx)+prevAt(c-tx)+prevAt(c+vec2(tx.x,-tx.y))+prevAt(c+vec2(-tx.x,tx.y));
  return b/16.0; }

void main(){
  vec2 uv=vec2(qt_TexCoord0.x, 1.0 - qt_TexCoord0.y);
  vec2 pv=vec2(uv.x*u_asp, uv.y);
  vec2 v=curl(pv*0.5, u_time*0.06)*0.18; v.y+=0.006;
  vec2 srcp=pv - v*u_dt; vec2 src=vec2(srcp.x/u_asp, srcp.y);
  vec4 fld=fetchBlur(src);
  fld.a =mix(fld.a, 0.0, 0.020);
  vec3 sc; float sw; emit(uv, sc, sw);
  vec3 tgt = sw>1e-4 ? sc/sw : fld.rgb;
  float w = clamp(sw,0.0,1.0)*0.06;
  vec3 col=mix(fld.rgb, tgt, w);
  float h = clamp(fld.a + sw*0.018, 0.0, 2.5);
  fragColor=vec4(col, h);
}
