// AI-hint: mios-wobbly -- Compiz-style wobbly windows for Hyprland 0.56 (MiOS original source).
// A floating window tagged `mios-wobbly` (window rule `tag = "+mios-wobbly"`) and not `mios-nowobbly`
// is rendered, while it
// moves and until it settles, through a Hyprland IWindowTransformer: Hyprland draws the whole window
// (surfaces + decorations) into a work buffer, and transform() re-draws that buffer through a
// subdivided mesh whose vertices follow a degree-4 Bezier patch. The patch's 5x5 control points are
// a spring-mass model: shape springs between neighbours, a weak anchor to the window's real box,
// velocity friction, and one node pinned to the grab point (the cursor during a drag, else the
// top edge) while the window moves. The transformer is attached only while the mesh is in motion,
// so a resting window costs nothing. Window state is never changed: the plugin only draws.
// Frame pacing (wobble_model.hpp): the model is advanced at RENDER_PRE -- before beginRender() takes
// the frame's damage -- by exactly the time since the last frame, so the mesh drawn in a frame is
// the mesh damaged for that frame; RENDER_POST re-damages the drawn extent so the next frame clears
// it and is scheduled while the mesh moves. (Was: a fixed 1/240 s accumulator stepped at
// RENDER_PRE_WINDOWS, i.e. 1,2,2 substeps per 144 Hz frame and damage one frame late.)
// The magic lamp (minimize/restore/launch from the dock) lives in lamp.cpp on the same pipeline and pacing; a window
// the lamp owns is never wobbled. fxstats.hpp counts frames, draws and distinct meshes per output for both effects
// (`hyprctl mios-lamp stats`), the measurement behind the pacing fix.
// AI-related: usr/share/mios/hyprland/plugins/mios-wobbly/Makefile, usr/share/mios/hyprland/plugins/mios-wobbly/wobble_model.hpp, usr/share/mios/hyprland/plugins/mios-wobbly/lamp.cpp, usr/share/mios/hyprland/plugins/mios-wobbly/fxstats.hpp, usr/share/mios/hyprland/plugins/mios-wobbly/tests/model_test.cpp, usr/share/mios/mios.toml
#include <GLES3/gl32.h>

#include <hyprland/src/plugins/PluginAPI.hpp>
#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/desktop/Workspace.hpp>
#include <hyprland/src/desktop/state/WindowState.hpp>
#include <hyprland/src/render/Renderer.hpp>
#include <hyprland/src/render/OpenGL.hpp>
#include <hyprland/src/render/gl/GLFramebuffer.hpp>
#include <hyprland/src/render/transformer/Transformer.hpp>
#include <hyprland/src/event/EventBus.hpp>
#include <hyprland/src/output/Monitor.hpp>
#include <hyprland/src/pointer/PointerManager.hpp>
#include <hyprland/src/layout/LayoutManager.hpp>
#include <hyprland/src/layout/supplementary/DragController.hpp>
#include <hyprland/src/layout/target/Target.hpp>
#include <hyprland/src/managers/fullscreen/FullscreenController.hpp>
#include <hyprland/src/managers/input/InputManager.hpp>
#include <hyprland/src/config/values/types/BoolValue.hpp>
#include <hyprland/src/config/values/types/IntValue.hpp>
#include <hyprland/src/config/values/types/FloatValue.hpp>
#include <hyprland/src/config/values/types/StringValue.hpp>

#include "wobble_model.hpp"
#include "lamp.hpp"
#include "lampmath.hpp"
#include "fxstats.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <unordered_map>
#include <vector>

namespace {
    HANDLE PHANDLE = nullptr;

    using MiosWobble::N; // control points per axis (degree-4 Bezier patch)

    struct SConfig {
        SP<Config::Values::CBoolValue>   enabled;
        SP<Config::Values::CStringValue> tag, excludeTag;
        SP<Config::Values::CFloatValue>  springK, anchorK, friction, maxStretch, paneFade;
        SP<Config::Values::CIntValue>    grid, render, flipY;
    } g_cfg;

    struct SWobble {
        PHLWINDOWREF window;
        Vector2D     P[N][N], V[N][N];
        CBox         simBox;            // real box (global logical, no workspace slide offset)
        bool         initialized = false;
        int          pinI = -1, pinJ = -1;
        bool         dragPinned = false;
        float        sinceMove  = 1e9f;
        int          restFrames = 0;
        bool         attached   = false;
        CBox         lastDamage;
        float        paneFade   = 1.0f;
        // Filled by preWindowRender for the frame being drawn.
        bool          drawnValid = false;
        CBox          drawnBox;          // monitor-local logical
        PHLMONITORREF drawnMonitor;
    };

    std::unordered_map<Desktop::View::CWindow*, UP<SWobble>> g_states;
    std::vector<CHyprSignalListener>                          g_listeners;
    auto                                                      g_lastTick = std::chrono::steady_clock::now();
    bool                                                      g_exiting  = false;

    // GL objects, created lazily on the render path (context current), leaked on unload.
    GLuint g_prog = 0, g_vao = 0, g_vbo = 0, g_sampler = 0;
    GLint  g_locTex = -1, g_locGain = -1;
    bool   g_glFailed = false;

    Vector2D patch(const SWobble& s, float u, float v) {
        return MiosWobble::patch(s.P, u, v);
    }

    Vector2D target(const CBox& b, int i, int j) {
        return MiosWobble::target<Vector2D>(b, i, j);
    }

    bool tagged(const PHLWINDOW& w) {
        const auto tag = g_cfg.tag->value(), skip = g_cfg.excludeTag->value();
        if (tag.empty() || !w->m_ruleApplicator)
            return false;
        const auto& tags = w->m_ruleApplicator->m_tagKeeper;
        return tags.isTagged(tag) && (skip.empty() || !tags.isTagged(skip));
    }

    PHLWINDOW movedByDrag() {
        if (!g_layoutManager)
            return nullptr;
        const auto& dc = g_layoutManager->dragController();
        if (!dc || dc->mode() != MBIND_MOVE)
            return nullptr;
        const auto t = dc->target();
        return t ? t->window() : nullptr;
    }

    class CWobblyTransformer : public Render::IWindowTransformer {
      public:
        explicit CWobblyTransformer(PHLWINDOWREF w) : m_window(w) {}
        ~CWobblyTransformer() override {
            if (g_exiting) // GL teardown during unload is not worth the risk; leak the buffers
                (void)new std::vector<std::pair<Render::IFramebuffer*, SP<Render::GL::CGLFramebuffer>>>(std::move(m_out));
        }

        void preWindowRender(CSurfacePassElement::SRenderData* rd) override {
            const auto it = g_states.find(m_window.get());
            if (it == g_states.end())
                return;
            auto&      s   = *it->second;
            const auto w   = m_window.lock();
            const auto mon = rd ? rd->pMonitor.lock() : nullptr;
            s.drawnValid   = false;
            if (!w || !mon || g_pHyprRenderer->m_bRenderingSnapshot)
                return;
            const auto ws  = w->m_workspace;
            CBox       box = w->getFullWindowBoundingBox();
            box.translate((w->m_pinned || !ws ? Vector2D{} : ws->m_renderOffset->value()) + w->m_floatingOffset - mon->m_position);
            s.drawnBox     = box;
            s.drawnMonitor = mon;
            s.drawnValid   = true;
        }

        SP<Render::IFramebuffer> transform(SP<Render::IFramebuffer> in) override;

        PHLWINDOWREF                                                         m_window;
        std::vector<std::pair<Render::IFramebuffer*, SP<Render::GL::CGLFramebuffer>>> m_out;
    };

    bool ensureGL() {
        if (g_prog || g_glFailed)
            return !g_glFailed;
        const char* vs = "#version 300 es\nin vec2 aPos;\nin vec2 aUV;\nout vec2 vUV;\n"
                         "void main() { vUV = aUV; gl_Position = vec4(aPos, 0.0, 1.0); }\n";
        const char* fs = "#version 300 es\nprecision highp float;\nin vec2 vUV;\nuniform sampler2D uTex;\nuniform float uGain;\nout vec4 o;\n"
                         "void main() { o = clamp(texture(uTex, vUV) * uGain, 0.0, 1.0); }\n";
        auto compile = [](GLenum type, const char* src) -> GLuint {
            GLuint sh = glCreateShader(type);
            glShaderSource(sh, 1, &src, nullptr);
            glCompileShader(sh);
            GLint ok = 0;
            glGetShaderiv(sh, GL_COMPILE_STATUS, &ok);
            if (!ok) {
                glDeleteShader(sh);
                return 0;
            }
            return sh;
        };
        GLuint v = compile(GL_VERTEX_SHADER, vs), f = compile(GL_FRAGMENT_SHADER, fs);
        if (!v || !f) {
            g_glFailed = true;
            return false;
        }
        GLuint p = glCreateProgram();
        glAttachShader(p, v);
        glAttachShader(p, f);
        glBindAttribLocation(p, 0, "aPos");
        glBindAttribLocation(p, 1, "aUV");
        glLinkProgram(p);
        glDeleteShader(v);
        glDeleteShader(f);
        GLint ok = 0;
        glGetProgramiv(p, GL_LINK_STATUS, &ok);
        if (!ok) {
            glDeleteProgram(p);
            g_glFailed = true;
            return false;
        }
        g_prog    = p;
        g_locTex  = glGetUniformLocation(p, "uTex");
        g_locGain = glGetUniformLocation(p, "uGain");

        GLint prevVAO = 0, prevAB = 0;
        glGetIntegerv(GL_VERTEX_ARRAY_BINDING, &prevVAO);
        glGetIntegerv(GL_ARRAY_BUFFER_BINDING, &prevAB);
        glGenVertexArrays(1, &g_vao);
        glGenBuffers(1, &g_vbo);
        glBindVertexArray(g_vao);
        glBindBuffer(GL_ARRAY_BUFFER, g_vbo);
        glEnableVertexAttribArray(0);
        glEnableVertexAttribArray(1);
        glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 4 * sizeof(float), (void*)0);
        glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 4 * sizeof(float), (void*)(2 * sizeof(float)));
        glBindVertexArray((GLuint)prevVAO);
        glBindBuffer(GL_ARRAY_BUFFER, (GLuint)prevAB);

        glGenSamplers(1, &g_sampler);
        glSamplerParameteri(g_sampler, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glSamplerParameteri(g_sampler, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glSamplerParameteri(g_sampler, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glSamplerParameteri(g_sampler, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        return true;
    }

    SP<Render::IFramebuffer> CWobblyTransformer::transform(SP<Render::IFramebuffer> in) {
        if (!in || g_exiting || g_cfg.render->value() == 0)
            return in;
        const auto it = g_states.find(m_window.get());
        if (it == g_states.end())
            return in;
        const auto& s   = *it->second;
        const auto  mon = g_pHyprRenderer->m_renderData.pMonitor.lock();
        if (!s.drawnValid || !mon || s.drawnMonitor.lock() != mon || mon->m_transform != WL_OUTPUT_TRANSFORM_NORMAL)
            return in;
        const auto inTex = in->getTexture();
        if (!inTex || !inTex->m_texID)
            return in;
        const int fw = (int)in->m_size.x, fh = (int)in->m_size.y;
        if (fw < 1 || fh < 1 || fw > 16384 || fh > 16384 || !ensureGL())
            return in;

        SP<Render::GL::CGLFramebuffer> out;
        for (auto it = m_out.begin(); it != m_out.end(); ++it)
            if (it->first == in.get()) {
                out = it->second;
                std::rotate(it, it + 1, m_out.end()); // most recently used last
                break;
            }
        if (!out) {
            // content + blur matte each frame; when the monitor's work-buffer pool hands out other
            // buffers, evict the least recently used instead of reallocating every output buffer
            if (m_out.size() >= 4)
                m_out.erase(m_out.begin());
            out = makeShared<Render::GL::CGLFramebuffer>("mios-wobbly");
            m_out.emplace_back(in.get(), out);
        }
        if (!out->isAllocated() || out->m_size != in->m_size) {
            out->release();
            if (!out->alloc(fw, fh, in->m_drmFormat != DRM_FORMAT_INVALID ? in->m_drmFormat : DRM_FORMAT_ARGB8888))
                return in;
        }
        out->setImageDescription(in->imageDescription());

        // Mesh: undeformed sample points cover the drawn box; each vertex is displaced by the
        // Bezier patch's deviation from the real box.
        const int   R     = std::clamp((int)g_cfg.grid->value(), 4, 64);
        const bool  flip  = g_cfg.flipY->value() != 0;
        const float scale = mon->m_scale;
        const auto  B     = s.drawnBox;
        std::vector<float> grid((R + 1) * (R + 1) * 4);
        for (int gy = 0; gy <= R; ++gy) {
            for (int gx = 0; gx <= R; ++gx) {
                const float u = (float)gx / R, v = (float)gy / R;
                const Vector2D rest = {s.simBox.x + s.simBox.w * u, s.simBox.y + s.simBox.h * v};
                const Vector2D d    = patch(s, u, v) - rest;
                const Vector2D U    = {B.x + B.w * u, B.y + B.h * v};
                const Vector2D Dd   = U + d;
                float          tu = U.x * scale / fw, tv = U.y * scale / fh;
                float          pu = Dd.x * scale / fw, pv = Dd.y * scale / fh;
                if (flip) {
                    tv = 1.f - tv;
                    pv = 1.f - pv;
                }
                float* o = &grid[(gy * (R + 1) + gx) * 4];
                o[0] = pu * 2.f - 1.f;
                o[1] = pv * 2.f - 1.f;
                o[2] = tu;
                o[3] = tv;
            }
        }
        std::vector<float> tris;
        tris.reserve(R * R * 6 * 4);
        auto put = [&](int gx, int gy) {
            const float* g = &grid[(gy * (R + 1) + gx) * 4];
            tris.insert(tris.end(), g, g + 4);
        };
        for (int gy = 0; gy < R; ++gy)
            for (int gx = 0; gx < R; ++gx) {
                put(gx, gy), put(gx + 1, gy), put(gx + 1, gy + 1);
                put(gx, gy), put(gx + 1, gy + 1), put(gx, gy + 1);
            }

        // Save the GL state Hyprland relies on, draw, restore it exactly.
        GLint   prevProg = 0, prevVAO = 0, prevAB = 0, prevActive = 0, prevTex = 0, prevSampler = 0, prevDraw = 0, prevRead = 0;
        GLint   vp[4] = {0, 0, 0, 0};
        GLfloat cc[4] = {0, 0, 0, 0};
        glGetIntegerv(GL_CURRENT_PROGRAM, &prevProg);
        glGetIntegerv(GL_VERTEX_ARRAY_BINDING, &prevVAO);
        glGetIntegerv(GL_ARRAY_BUFFER_BINDING, &prevAB);
        glGetIntegerv(GL_ACTIVE_TEXTURE, &prevActive);
        glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &prevDraw);
        glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &prevRead);
        glGetIntegerv(GL_VIEWPORT, vp);
        glGetFloatv(GL_COLOR_CLEAR_VALUE, cc);
        glActiveTexture(GL_TEXTURE0);
        glGetIntegerv(GL_TEXTURE_BINDING_2D, &prevTex);
        glGetIntegerv(GL_SAMPLER_BINDING, &prevSampler);
        const bool scissor = glIsEnabled(GL_SCISSOR_TEST) == GL_TRUE;
        const bool blendOn = glIsEnabled(GL_BLEND) == GL_TRUE;

        auto& gl = Render::GL::g_pHyprOpenGL;
        gl->setCapStatus(GL_SCISSOR_TEST, false);
        gl->blend(false);
        glBindFramebuffer(GL_FRAMEBUFFER, out->getFBID());
        gl->setViewport(0, 0, fw, fh);
        glClearColor(0, 0, 0, 0);
        glClear(GL_COLOR_BUFFER_BIT);

        glUseProgram(g_prog);
        glBindVertexArray(g_vao);
        glBindBuffer(GL_ARRAY_BUFFER, g_vbo);
        glBufferData(GL_ARRAY_BUFFER, tris.size() * sizeof(float), tris.data(), GL_STREAM_DRAW);
        glBindTexture(GL_TEXTURE_2D, inTex->m_texID);
        glBindSampler(0, g_sampler);
        glUniform1i(g_locTex, 0);
        const float gain = 1.0f / std::max(s.paneFade, 0.05f);
        if (g_locGain >= 0)
            glUniform1f(g_locGain, gain);
        glDrawArrays(GL_TRIANGLES, 0, (GLsizei)(tris.size() / 4));

        glBindSampler(0, (GLuint)prevSampler);
        glBindTexture(GL_TEXTURE_2D, (GLuint)prevTex);
        glActiveTexture((GLenum)prevActive);
        glBindBuffer(GL_ARRAY_BUFFER, (GLuint)prevAB);
        glBindVertexArray((GLuint)prevVAO);
        glUseProgram((GLuint)prevProg);
        glBindFramebuffer(GL_DRAW_FRAMEBUFFER, (GLuint)prevDraw);
        glBindFramebuffer(GL_READ_FRAMEBUFFER, (GLuint)prevRead);
        glClearColor(cc[0], cc[1], cc[2], cc[3]);
        gl->setViewport(vp[0], vp[1], vp[2], vp[3]);
        gl->setCapStatus(GL_SCISSOR_TEST, scissor);
        gl->blend(blendOn);
        // frame-pacing counters: the drawn mesh and the model time it shows (the last tick)
        MiosFxStats::draw(MiosFxStats::WOBBLE, mon->m_name, mon->m_refreshRate, MiosFxStats::fnv(grid.data(), grid.size() * sizeof(float)), MiosFxStats::nowMs(),
                          std::chrono::duration<double, std::milli>(g_lastTick.time_since_epoch()).count());
        return out;
    }

    void detach(SWobble& s) {
        if (const auto w = s.window.lock()) {
            std::erase_if(w->m_transformers, [](const auto& t) { return dynamic_cast<CWobblyTransformer*>(t.get()) != nullptr; });
            w->alpha(Desktop::View::WINDOW_ALPHA_LAYOUT)->setValueAndWarp(1.f);
            if (s.lastDamage.w > 0)
                g_pHyprRenderer->damageBox(s.lastDamage);
            g_pHyprRenderer->damageWindow(w);
        }
        s.attached   = false;
        s.drawnValid = false;
        s.lastDamage = {};
        s.paneFade   = 1.f;
    }

    void attach(SWobble& s, const PHLWINDOW& w) {
        if (s.attached)
            return;
        w->m_transformers.emplace_back(makeUnique<CWobblyTransformer>(s.window));
        s.attached = true;
    }

    MiosWobble::SParams params() {
        return {(float)g_cfg.springK->value(), (float)g_cfg.anchorK->value(), (float)g_cfg.friction->value(), (float)g_cfg.maxStretch->value()};
    }

    void settleTo(SWobble& s, const CBox& box) {
        s.simBox = box;
        for (int i = 0; i < N; ++i)
            for (int j = 0; j < N; ++j) {
                s.P[i][j] = target(box, i, j);
                s.V[i][j] = {};
            }
    }

    void pinNearest(SWobble& s, const Vector2D& p) {
        double best = 1e18;
        for (int i = 0; i < N; ++i)
            for (int j = 0; j < N; ++j) {
                const double d = (target(s.simBox, i, j) - p).size();
                if (d < best) {
                    best   = d;
                    s.pinI = i;
                    s.pinJ = j;
                }
            }
    }

    // RENDER_POST: the extent drawn this frame must be repainted next frame (the mesh moves on), and
    // damaging it while the monitor is still rendering keeps the next frame scheduled.
    void damageDrawnExtents() {
        for (auto& [raw, s] : g_states)
            if (s->attached && s->lastDamage.w > 0)
                g_pHyprRenderer->damageBox(s->lastDamage);
    }

    void tick() {
        const auto now = std::chrono::steady_clock::now();
        float      dt  = std::chrono::duration<float>(now - g_lastTick).count();
        if (dt < 0.0005f)
            return; // several monitors starting a frame at the same instant; step once
        g_lastTick = now;
        dt         = std::min(dt, 0.05f);

        const bool enabled = g_cfg.enabled->value();
        const auto dragged = movedByDrag();
        const auto pointer = Pointer::mgr() ? Pointer::mgr()->position() : Vector2D{};

        for (auto it = g_states.begin(); it != g_states.end();) {
            if (!it->second->window.lock()) {
                it = g_states.erase(it);
                continue;
            }
            ++it;
        }

        for (const auto& w : Desktop::windowState()->windows()) {
            if (!w)
                continue;
            const bool eligible = enabled && w->m_isMapped && w->m_isFloating && !w->isHidden() && w->m_workspace && w->m_workspace->isVisible() &&
                !Fullscreen::controller()->isFullscreen(w) && tagged(w) && !MiosLampFx::owns(w.get()); // the lamp outranks the wobble
            auto it = g_states.find(w.get());
            if (!eligible) {
                if (it != g_states.end()) {
                    if (it->second->attached)
                        detach(*it->second);
                    g_states.erase(it);
                }
                continue;
            }
            CBox box = w->getFullWindowBoundingBox();
            box.translate(w->m_floatingOffset);
            if (box.w < 8 || box.h < 8)
                continue;
            if (it == g_states.end()) {
                auto s    = makeUnique<SWobble>();
                s->window = w;
                settleTo(*s, box);
                s->initialized = true;
                g_states.emplace(w.get(), std::move(s));
                continue;
            }
            auto&      s     = *it->second;
            const bool sized = std::abs(box.w - s.simBox.w) > 0.5 || std::abs(box.h - s.simBox.h) > 0.5;
            if (sized) { // resizes do not wobble: snap to the new shape
                if (s.attached)
                    detach(s);
                settleTo(s, box);
                s.pinI = s.pinJ = -1;
                continue;
            }
            const bool moved = std::abs(box.x - s.simBox.x) > 0.01 || std::abs(box.y - s.simBox.y) > 0.01;
            const CBox from  = s.simBox; // where the box was at the previous frame
            s.simBox         = box;
            s.sinceMove      = moved ? 0.f : s.sinceMove + dt;

            const bool isDragged = dragged && dragged.get() == w.get();
            if (isDragged) {
                if (!s.dragPinned) {
                    pinNearest(s, pointer);
                    s.dragPinned = true;
                }
            } else {
                s.dragPinned = false;
                if (moved && s.pinI < 0) {
                    if (box.containsPoint(pointer))
                        pinNearest(s, pointer);
                    else {
                        s.pinI = N / 2;
                        s.pinJ = 0;
                    }
                }
                if (s.sinceMove > 0.08f)
                    s.pinI = s.pinJ = -1;
            }

            if (moved || isDragged)
                attach(s, w);
            if (!s.attached)
                continue;

            // Exactly dt of physics, the box moving from its previous-frame to its current position
            // across the substeps: the deformation advances in step with the window on any refresh rate.
            MiosWobble::advance(s.P, s.V, from, box, s.pinI, s.pinJ, params(), dt);

            double dev = 0, vel = 0;
            CBox   ext = box;
            double x0 = box.x, y0 = box.y, x1 = box.x + box.w, y1 = box.y + box.h;
            for (int i = 0; i < N; ++i)
                for (int j = 0; j < N; ++j) {
                    dev = std::max(dev, (s.P[i][j] - target(box, i, j)).size());
                    vel = std::max(vel, s.V[i][j].size());
                    x0  = std::min(x0, s.P[i][j].x);
                    y0  = std::min(y0, s.P[i][j].y);
                    x1  = std::max(x1, s.P[i][j].x);
                    y1  = std::max(y1, s.P[i][j].y);
                }

            // The rigid hyprglass pane while the shape deforms (plugin:mios_wobbly:pane_fade): 1 keeps the glass at full
            // strength (the rigid pane shows beside the bent content), < 1 fades it with the deviation -- the content is
            // compensated by 1/paneFade in the shader, so >= 0.4 keeps 8-bit content free of banding (desk-042 (8)).
            const float paneMin = std::clamp((float)g_cfg.paneFade->value(), 0.05f, 1.0f);
            s.paneFade          = paneMin >= 1.0f ? 1.0f : (float)MiosLamp::wobblePaneFade(dev, paneMin, 2.0);
            w->alpha(Desktop::View::WINDOW_ALPHA_LAYOUT)->setValueAndWarp(s.paneFade);

            const auto ws    = w->m_workspace;
            const auto shift = (w->m_pinned || !ws) ? Vector2D{} : ws->m_renderOffset->value();
            ext              = CBox{x0, y0, x1 - x0, y1 - y0}.translate(shift).expand(3);
            CBox damage      = ext;
            if (s.lastDamage.w > 0) {
                const double ux0 = std::min(damage.x, s.lastDamage.x), uy0 = std::min(damage.y, s.lastDamage.y);
                const double ux1 = std::max(damage.x + damage.w, s.lastDamage.x + s.lastDamage.w);
                const double uy1 = std::max(damage.y + damage.h, s.lastDamage.y + s.lastDamage.h);
                damage           = CBox{ux0, uy0, ux1 - ux0, uy1 - uy0};
            }
            g_pHyprRenderer->damageBox(damage);
            s.lastDamage = ext;

            const bool resting = s.pinI < 0 && !isDragged && dev < 0.3 && vel < 4.0;
            s.restFrames       = resting ? s.restFrames + 1 : 0;
            if (s.restFrames > 3) {
                settleTo(s, box);
                detach(s);
            }
        }
    }
}

APICALL EXPORT std::string PLUGIN_API_VERSION() {
    return HYPRLAND_API_VERSION;
}

APICALL EXPORT PLUGIN_DESCRIPTION_INFO PLUGIN_INIT(HANDLE handle) {
    PHANDLE = handle;
    if (std::string(__hyprland_api_get_hash()) != __hyprland_api_get_client_hash()) {
        HyprlandAPI::addNotification(PHANDLE, "[mios-wobbly] headers do not match the running Hyprland; not loading", CHyprColor{1.0, 0.2, 0.2, 1.0}, 8000);
        throw std::runtime_error("[mios-wobbly] version mismatch");
    }

    using namespace Config::Values;
    g_cfg.enabled    = makeShared<CBoolValue>("plugin:mios_wobbly:enabled", "Wobble tagged floating windows while they move", true);
    g_cfg.tag        = makeShared<CStringValue>("plugin:mios_wobbly:tag", "Window tag that opts a window in (set by a window rule)", "mios-wobbly");
    g_cfg.excludeTag = makeShared<CStringValue>("plugin:mios_wobbly:exclude_tag", "Window tag that opts a window out even when tagged in", "mios-nowobbly");
    g_cfg.springK    = makeShared<CFloatValue>("plugin:mios_wobbly:spring_k", "Shape spring stiffness between mesh nodes (1/s^2)", 520.F);
    g_cfg.anchorK    = makeShared<CFloatValue>("plugin:mios_wobbly:anchor_k", "Pull of every node back to the real window box (1/s^2)", 40.F);
    g_cfg.friction   = makeShared<CFloatValue>("plugin:mios_wobbly:friction", "Velocity damping (1/s); higher settles faster", 11.F);
    g_cfg.maxStretch = makeShared<CFloatValue>("plugin:mios_wobbly:max_stretch", "Largest node deviation as a fraction of the window's short side", 0.3F);
    g_cfg.grid       = makeShared<CIntValue>("plugin:mios_wobbly:grid", "Render mesh subdivisions per axis", 24);
    g_cfg.paneFade   = makeShared<CFloatValue>("plugin:mios_wobbly:pane_fade", "Layout alpha the rigid glass pane fades to while the shape deforms (1: glass stays at full strength)", 1.F);
    g_cfg.render     = makeShared<CIntValue>("plugin:mios_wobbly:render", "1 draws the deformed mesh; 0 runs the model with a pass-through draw", 1);
    g_cfg.flipY      = makeShared<CIntValue>("plugin:mios_wobbly:flip_y", "Work-buffer row order (0 top-down, 1 bottom-up)", 0);
    for (const SP<IValue>& v : std::initializer_list<SP<IValue>>{g_cfg.enabled, g_cfg.tag, g_cfg.excludeTag, g_cfg.springK, g_cfg.anchorK, g_cfg.friction, g_cfg.maxStretch, g_cfg.grid,
                                                                 g_cfg.render, g_cfg.flipY, g_cfg.paneFade})
        HyprlandAPI::addConfigValueV2(PHANDLE, v);
    MiosLampFx::init(PHANDLE); // plugin:mios_lamp:*, hl.plugin.mios_lamp.*, `hyprctl mios-lamp`

    g_listeners.push_back(Event::bus()->m_events.render.stage.listen([](eRenderStage stage) {
        if (g_exiting || (stage != RENDER_PRE && stage != RENDER_POST))
            return;
        try {
            if (stage == RENDER_PRE) { // before beginRender() collects this frame's damage
                MiosLampFx::onRenderPre(); // first: a window the lamp takes over leaves the wobble in tick()
                tick();
            } else {
                damageDrawnExtents();
                MiosLampFx::onRenderPost();
            }
        } catch (...) {}
    }));
    g_listeners.push_back(Event::bus()->m_events.render.pre.listen([](PHLMONITOR mon) {
        if (g_exiting || !mon)
            return;
        MiosFxStats::output(mon->m_name, mon->m_refreshRate, MiosFxStats::nowMs());
        if (std::any_of(g_states.begin(), g_states.end(), [](const auto& kv) { return kv.second->attached; }))
            MiosFxStats::frame(MiosFxStats::WOBBLE, mon->m_name, mon->m_refreshRate);
        MiosLampFx::onFramePre(mon);
    }));
    g_listeners.push_back(Event::bus()->m_events.window.destroy.listen([](PHLWINDOWREF w) {
        if (w)
            g_states.erase(w.get());
    }));

    return {"mios-wobbly", "Compiz-style wobbly floating windows and magic lamp (MiOS mesh effects)", "MiOS", "1.1"};
}

APICALL EXPORT void PLUGIN_EXIT() {
    g_exiting = true;
    g_listeners.clear();
    MiosLampFx::exit();
    for (auto& [raw, s] : g_states)
        if (s->attached)
            detach(*s);
    g_states.clear();
}
