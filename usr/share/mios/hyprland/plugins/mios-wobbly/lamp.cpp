// AI-hint: Magic lamp for Hyprland 0.56 (MiOS original source; part of mios-wobbly.so, the one MiOS mesh backend).
// minimize: the window bends into a genie tube and flows into its dock icon (lampmath.hpp), then moves silently to
// `minimized_workspace`; restore: moved back (workspace, floating geometry, focus) and played in reverse out of the
// dock; launch: a window mapping within launch_timeout_ms of a dock launch of its app grows out of that icon.
// Rendering reuses Hyprland's IWindowTransformer work buffer (surfaces + decorations incl. the hyprglass edge pass,
// which is redirected into it) redrawn through a (cols+1) x (rows+1) mesh at the frame's own time: progress is a
// pure function of the RENDER_PRE timestamp (same pacing as the wobble), damage for the shape is added before
// beginRender so it lands in the same frame, and RENDER_POST re-damages it so every output showing it gets the next
// vblank. hyprglass draws the window's glass PANE rigidly at the real box (main pass, not the redirected one), so
// the window's WINDOW_ALPHA_LAYOUT is driven to paneFade(t) (pane alpha = that ^ fade_gamma -> invisible) and the
// deformed content is multiplied back by 1/paneFade: the pane does not linger behind the genie while the edge pass,
// shadow and content keep their full strength. Precedence: the lamp owns minimize/restore (workspace moves, never a
// HyprWindowShade event) and dock launches (the window is tagged `anim_tag` before its first frame; the SSOT projects
// a window rule giving tagged windows the "none" open shader and no hyprbars tab); the wobble skips owned windows.
// AI-related: usr/share/mios/hyprland/plugins/mios-wobbly/lamp.hpp, usr/share/mios/hyprland/plugins/mios-wobbly/lampmath.hpp, usr/share/mios/hyprland/plugins/mios-wobbly/main.cpp, usr/share/mios/mios.toml [desktop.hyprland.lamp], /home/mios/mios-desktop-work-1/lamp-contract.md
#include <GLES3/gl32.h>

#include <hyprland/src/plugins/PluginAPI.hpp>
#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/desktop/Workspace.hpp>
#include <hyprland/src/desktop/state/WindowState.hpp>
#include <hyprland/src/desktop/state/FocusState.hpp>
#include <hyprland/src/desktop/state/ViewState.hpp>
#include <hyprland/src/desktop/state/ViewQuery.hpp>
#include <hyprland/src/desktop/rule/Rule.hpp>
#include <hyprland/src/desktop/rule/windowRule/WindowRuleApplicator.hpp>
#include <hyprland/src/render/Renderer.hpp>
#include <hyprland/src/render/OpenGL.hpp>
#include <hyprland/src/render/gl/GLFramebuffer.hpp>
#include <hyprland/src/render/transformer/Transformer.hpp>
#include <hyprland/src/event/EventBus.hpp>
#include <hyprland/src/output/Monitor.hpp>
#include <hyprland/src/managers/eventLoop/EventLoopManager.hpp>
#include <hyprland/src/managers/eventLoop/EventLoopTimer.hpp>
#include <hyprland/src/config/values/types/BoolValue.hpp>
#include <hyprland/src/config/values/types/IntValue.hpp>
#include <hyprland/src/config/values/types/FloatValue.hpp>
#include <hyprland/src/config/values/types/StringValue.hpp>

// C linkage: the plugin's private Lua 5.5 (liblua.a from the compositor SRPM), exactly like the hyprbars plugin
extern "C" {
#include <lua.h>
}

#include "lamp.hpp"
#include "lampmath.hpp"
#include "fxstats.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <deque>
#include <format>
#include <fstream>
#include <set>
#include <sstream>
#include <sys/stat.h>
#include <unordered_map>
#include <vector>

namespace {
    using namespace MiosLamp;
    using Clock = std::chrono::steady_clock;
    using Desktop::View::CWindow;

    HANDLE PH = nullptr;

    struct SCfg {
        SP<Config::Values::CBoolValue>   enabled;
        SP<Config::Values::CIntValue>    durOpen, durMinimize, durRestore, cols, rows, launchTimeout;
        SP<Config::Values::CStringValue> curve, events, hiddenDock, fallbackEdge, minimizedWs, tag, dockFile, launchFile;
        SP<Config::Values::CFloatValue>  paneAlpha;
    } g_cfg;

    enum class eKind : uint8_t {
        MINIMIZE,
        RESTORE,
        OPEN,
    };
    enum class ePhase : uint8_t {
        PENDING,   // restore: waiting for the pulse to move the window back
        RUNNING,   // animating
        FINISHING, // minimize: moved away, waiting until it is no longer drawn
        DONE,
    };

    const char* kindName(eKind k) {
        return k == eKind::MINIMIZE ? "minimize" : k == eKind::RESTORE ? "restore" : "open";
    }
    const char* phaseName(ePhase p) {
        return p == ePhase::PENDING ? "pending" : p == ePhase::RUNNING ? "running" : p == ePhase::FINISHING ? "finishing" : "done";
    }

    struct SSiblingSnapshot {
        std::string address;
        CBox        box;
    };

    struct SLamp {
        PHLWINDOWREF         window;
        eKind                kind  = eKind::MINIMIZE;
        ePhase               phase = ePhase::RUNNING;
        Clock::time_point    start, phaseSince, lastPre, lastDrawn;
        double               durS = 0.5, tFrom = 0.0, t = 0.0, modelMs = 0.0;
        SRect                target;
        std::optional<eEdge> edgeHint;
        std::string          source, address, targetSource;
        CBox                 lastExt;
        bool                 attached = false, tagged = false;
        // RESTORE: where the window goes back to
        CBox                 preMinBox;
        bool                 wasFloating = false;
        std::string          workspaceTo;
        std::string          preMinMonitorName;
        std::vector<SSiblingSnapshot> siblings;
        std::optional<CBox>  geometryTo;
        // filled by preWindowRender for the frame being drawn
        bool                 drawnValid = false;
        CBox                 drawnBox; // monitor-local logical
        PHLMONITORREF        drawnMonitor;
    };

    struct SMinimized {
        PHLWINDOWREF window;
        std::string  address, workspace, cls, title, monitor;
        bool         floating = false;
        CBox         geometry; // position/size goal (logical, no decorations)
        std::vector<SSiblingSnapshot> siblings;
        double       timeMs = 0;
    };

    std::unordered_map<CWindow*, UP<SLamp>> g_lamps;
    std::vector<SMinimized>                 g_minimized;
    std::deque<std::string>                 g_consumed; // launch ids already animated (bounded)
    std::vector<CHyprSignalListener>        g_listeners;
    SP<CEventLoopTimer>                     g_timer;
    Clock::time_point                       g_dockChecked;
    int64_t                                 g_dockMtimeNs = -1;
    SP<SHyprCtlCommand>                     g_cmd;
    bool                                    g_exiting = false;

    GLuint                                  g_prog = 0, g_vao = 0, g_vbo = 0, g_sampler = 0;
    GLint                                   g_locTex = -1, g_locGain = -1, g_locAlpha = -1;
    bool                                    g_glFailed = false;

    // ---- small helpers --------------------------------------------------------------------------------------

    double msOf(Clock::time_point tp) {
        return std::chrono::duration<double, std::milli>(tp.time_since_epoch()).count();
    }
    double epochMs() {
        return std::chrono::duration<double, std::milli>(std::chrono::system_clock::now().time_since_epoch()).count();
    }
    std::string str(const SP<Config::Values::CStringValue>& v) {
        return v ? std::string(v->value()) : std::string{};
    }
    std::string addressOf(const PHLWINDOW& w) {
        return std::format("0x{:x}", (uintptr_t)w.get());
    }
    SRect rectOf(const CBox& b) {
        return {b.x, b.y, b.w, b.h};
    }
    SRect monitorRect(const PHLMONITOR& m) {
        return m ? SRect{m->m_position.x, m->m_position.y, m->m_size.x, m->m_size.y} : SRect{};
    }
    Vector2D boxCenter(const CBox& b) {
        return {b.x + b.w / 2.0, b.y + b.h / 2.0};
    }
    bool eventOn(std::string_view ev) {
        if (!g_cfg.enabled->value())
            return false;
        std::stringstream ss(str(g_cfg.events));
        std::string       tok;
        while (std::getline(ss, tok, ',')) {
            tok.erase(std::remove_if(tok.begin(), tok.end(), [](unsigned char c) { return std::isspace(c); }), tok.end());
            if (tok == ev)
                return true;
        }
        return false;
    }
    SBezier curve() {
        double             a[4] = {0.42, 0.0, 0.25, 1.0};
        std::istringstream in(str(g_cfg.curve));
        for (double& v : a)
            if (!(in >> v))
                return SBezier::make(0.42, 0.0, 0.25, 1.0);
        return SBezier::make(a[0], a[1], a[2], a[3]);
    }
    SMeshParams meshParams() {
        SMeshParams p;
        p.cols = std::clamp((int)g_cfg.cols->value(), 1, 64);
        p.rows = std::clamp((int)g_cfg.rows->value(), 4, 128);
        return p;
    }
    double paneMin() {
        return std::clamp((double)g_cfg.paneAlpha->value(), 0.05, 1.0);
    }
    std::string minimizedWs() {
        auto s = str(g_cfg.minimizedWs);
        return s.empty() ? std::string("special:minimized") : s;
    }
    std::string animTag() {
        return str(g_cfg.tag);
    }
    std::string runtimePath(const SP<Config::Values::CStringValue>& v, const char* file) {
        auto p = str(v);
        if (!p.empty())
            return p;
        const char* rt = std::getenv("XDG_RUNTIME_DIR");
        return std::string(rt && *rt ? rt : "/tmp") + "/" + file;
    }
    std::string readSmall(const std::string& path) {
        std::ifstream f(path, std::ios::binary);
        if (!f)
            return {};
        std::string s;
        s.resize(1 << 18);
        f.read(s.data(), (std::streamsize)s.size());
        s.resize((size_t)f.gcount());
        return s;
    }
    std::string jsonEscape(std::string_view s) {
        std::string o;
        for (unsigned char c : s) {
            if (c == '"' || c == '\\') {
                o += '\\';
                o += (char)c;
            } else if (c < 0x20)
                o += std::format("\\u{:04x}", c);
            else
                o += (char)c;
        }
        return o;
    }
    // Workspace selector safe to splice into a Lua string literal.
    std::string workspaceSelector(const PHLWORKSPACE& ws) {
        if (!ws || ws->m_isSpecialWorkspace)
            return {};
        if (ws->m_id > 0)
            return std::to_string(ws->m_id);
        const bool safe = !ws->m_name.empty() && std::all_of(ws->m_name.begin(), ws->m_name.end(), [](unsigned char c) { return std::isalnum(c) || c == '-' || c == '_' || c == '.'; });
        return safe ? "name:" + ws->m_name : std::string{};
    }
    bool onMinimizedWs(const PHLWINDOW& w) {
        return w && w->m_workspace && w->m_workspace->m_isSpecialWorkspace && w->m_workspace->m_name == minimizedWs();
    }
    CBox windowBoxGlobal(const PHLWINDOW& w) {
        CBox       box = w->getFullWindowBoundingBox();
        const auto ws  = w->m_workspace;
        box.translate((w->m_pinned || !ws ? Vector2D{} : ws->m_renderOffset->value()) + w->m_floatingOffset);
        return box;
    }
    std::string dispatch(const std::string& lua) {
        return HyprlandAPI::invokeHyprctlCommand("dispatch", lua);
    }

    void armTimer() {
        if (g_timer && !g_exiting)
            g_timer->updateTimeout(std::chrono::milliseconds(8));
    }

    // ---- transformer ------------------------------------------------------------------------------------------

    bool ensureGL() {
        if (g_prog || g_glFailed)
            return !g_glFailed;
        const char* vs = "#version 300 es\nin vec2 aPos;\nin vec2 aUV;\nout vec2 vUV;\n"
                         "void main() { vUV = aUV; gl_Position = vec4(aPos, 0.0, 1.0); }\n";
        // premultiplied input: scaling every channel undoes the pane-hiding layout alpha exactly
        const char* fs = "#version 300 es\nprecision highp float;\nin vec2 vUV;\nuniform sampler2D uTex;\nuniform float uGain;\nuniform float uAlpha;\nout vec4 o;\n"
                         "void main() { o = clamp(texture(uTex, vUV) * uGain, 0.0, 1.0) * uAlpha; }\n";
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
        g_prog     = p;
        g_locTex   = glGetUniformLocation(p, "uTex");
        g_locGain  = glGetUniformLocation(p, "uGain");
        g_locAlpha = glGetUniformLocation(p, "uAlpha");

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

    class CLampTransformer : public Render::IWindowTransformer {
      public:
        explicit CLampTransformer(PHLWINDOWREF w) : m_window(w) {}
        ~CLampTransformer() override {
            if (g_exiting) // GL teardown during unload is not worth the risk; leak the buffers
                (void)new std::vector<std::pair<Render::IFramebuffer*, SP<Render::GL::CGLFramebuffer>>>(std::move(m_out));
        }

        void preWindowRender(CSurfacePassElement::SRenderData* rd) override {
            const auto it = g_lamps.find(m_window.get());
            if (it == g_lamps.end())
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
            s.lastDrawn    = Clock::now();
        }

        SP<Render::IFramebuffer> transform(SP<Render::IFramebuffer> in) override;

        PHLWINDOWREF                                                                  m_window;
        std::vector<std::pair<Render::IFramebuffer*, SP<Render::GL::CGLFramebuffer>>> m_out;
    };

    SP<Render::IFramebuffer> CLampTransformer::transform(SP<Render::IFramebuffer> in) {
        if (!in || g_exiting)
            return in;
        const auto it = g_lamps.find(m_window.get());
        if (it == g_lamps.end())
            return in;
        auto&      s   = *it->second;
        const auto mon = g_pHyprRenderer->m_renderData.pMonitor.lock();
        if (!s.drawnValid || !mon || s.drawnMonitor.lock() != mon || mon->m_transform != WL_OUTPUT_TRANSFORM_NORMAL)
            return in;
        const auto inTex = in->getTexture();
        if (!inTex || !inTex->m_texID)
            return in;
        const int fw = (int)in->m_size.x, fh = (int)in->m_size.y;
        if (fw < 1 || fh < 1 || fw > 16384 || fh > 16384 || !ensureGL())
            return in;

        const SRect win  = rectOf(s.drawnBox);
        const SRect icon = {s.target.x - mon->m_position.x, s.target.y - mon->m_position.y, s.target.w, s.target.h};
        const SRect winG = {win.x + mon->m_position.x, win.y + mon->m_position.y, win.w, win.h};
        const auto  prm  = meshParams();
        static std::vector<SVertex> mesh;
        if (!buildMesh(win, icon, chooseEdge(winG, s.target, s.edgeHint), s.t, prm, mesh))
            return in;

        SP<Render::GL::CGLFramebuffer> out;
        for (auto o = m_out.begin(); o != m_out.end(); ++o)
            if (o->first == in.get()) {
                out = o->second;
                std::rotate(o, o + 1, m_out.end()); // most recently used last
                break;
            }
        if (!out) {
            if (m_out.size() >= 4) // content + blur matte; evict the least recently used work buffer
                m_out.erase(m_out.begin());
            out = makeShared<Render::GL::CGLFramebuffer>("mios-lamp");
            m_out.emplace_back(in.get(), out);
        }
        if (!out->isAllocated() || out->m_size != in->m_size) {
            out->release();
            if (!out->alloc(fw, fh, in->m_drmFormat != DRM_FORMAT_INVALID ? in->m_drmFormat : DRM_FORMAT_ARGB8888))
                return in;
        }
        out->setImageDescription(in->imageDescription());

        const float scale = mon->m_scale;
        const int   C = prm.cols, R = prm.rows;
        static std::vector<float> tris;
        tris.clear();
        tris.reserve((size_t)C * R * 6 * 4);
        auto put = [&](int i, int j) {
            const auto& v = mesh[(size_t)j * (C + 1) + i];
            tris.push_back((float)(v.x * scale / fw) * 2.f - 1.f);
            tris.push_back((float)(v.y * scale / fh) * 2.f - 1.f);
            tris.push_back((float)((win.x + v.u * win.w) * scale / fw));
            tris.push_back((float)((win.y + v.v * win.h) * scale / fh));
        };
        for (int j = 0; j < R; ++j)
            for (int i = 0; i < C; ++i) {
                put(i, j), put(i + 1, j), put(i + 1, j + 1);
                put(i, j), put(i + 1, j + 1), put(i, j + 1);
            }
        const float gain  = (float)(1.0 / paneFade(s.t, paneMin()));
        const float alpha = (float)contentAlpha(s.t);

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
        glUniform1f(g_locGain, gain);
        glUniform1f(g_locAlpha, alpha);
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

        uint64_t h = MiosFxStats::fnv(tris.data(), tris.size() * sizeof(float));
        h          = MiosFxStats::fnv(&gain, sizeof(gain), h);
        h          = MiosFxStats::fnv(&alpha, sizeof(alpha), h);
        MiosFxStats::draw(MiosFxStats::LAMP, mon->m_name, mon->m_refreshRate, h, MiosFxStats::nowMs(), s.modelMs);
        return out;
    }

    // ---- state ------------------------------------------------------------------------------------------------

    void setTag(SLamp& s, const PHLWINDOW& w, bool on) {
        const auto tag = animTag();
        if (tag.empty() || !w || !w->m_ruleApplicator || s.tagged == on)
            return;
        s.tagged = on;
        if (w->m_ruleApplicator->m_tagKeeper.applyTag((on ? "+" : "-") + tag)) {
            w->m_ruleApplicator->propertiesChanged(Desktop::Rule::RULE_PROP_TAG);
            w->updateDecorationValues();
        }
    }

    void attach(SLamp& s, const PHLWINDOW& w) {
        if (s.attached)
            return;
        w->m_transformers.emplace_back(makeUnique<CLampTransformer>(s.window));
        s.attached = true;
    }

    void detach(SLamp& s, const PHLWINDOW& w) {
        if (w) {
            std::erase_if(w->m_transformers, [](const auto& t) { return dynamic_cast<CLampTransformer*>(t.get()) != nullptr; });
            if (s.lastExt.w > 0)
                g_pHyprRenderer->damageBox(s.lastExt);
            g_pHyprRenderer->damageWindow(w);
        }
        s.attached   = false;
        s.drawnValid = false;
        s.lastExt    = {};
    }

    // Window alphas while the lamp owns the window: the rigid glass pane fades out with the layout alpha (the content
    // is compensated in the shader); Hyprland's own fade-in / workspace fades must not play on top of the lamp.
    void applyAlphas(const SLamp& s, const PHLWINDOW& w) {
        using namespace Desktop::View;
        w->alpha(WINDOW_ALPHA_LAYOUT)->setValueAndWarp((float)paneFade(s.t, paneMin()));
        if (s.kind == eKind::OPEN && w->alpha(WINDOW_ALPHA_FADE)->value() < 1.f)
            w->alpha(WINDOW_ALPHA_FADE)->setValueAndWarp(1.f);
        if (s.kind == eKind::RESTORE) {
            if (w->alpha(WINDOW_ALPHA_MOVE_FROM_WORKSPACE)->value() < 1.f)
                w->alpha(WINDOW_ALPHA_MOVE_FROM_WORKSPACE)->setValueAndWarp(1.f);
            if (w->alpha(WINDOW_ALPHA_MOVE_TO_WORKSPACE)->value() < 1.f)
                w->alpha(WINDOW_ALPHA_MOVE_TO_WORKSPACE)->setValueAndWarp(1.f);
        }
    }

    void finish(SLamp& s) {
        const auto w = s.window.lock();
        detach(s, w);
        if (w) {
            w->alpha(Desktop::View::WINDOW_ALPHA_LAYOUT)->setValueAndWarp(1.f);
            setTag(s, w, false);
        }
    }

    double progressAt(const SLamp& s, Clock::time_point now) {
        const double tau = std::clamp(std::chrono::duration<double>(now - s.start).count() / std::max(s.durS, 0.016), 0.0, 1.0);
        const double e   = curve()(tau);
        return s.kind == eKind::MINIMIZE ? s.tFrom + (1.0 - s.tFrom) * e : s.tFrom * (1.0 - e);
    }
    double tauAt(const SLamp& s, Clock::time_point now) {
        return std::chrono::duration<double>(now - s.start).count() / std::max(s.durS, 0.016);
    }

    void markDockRead() {
        struct stat st {};
        g_dockMtimeNs = ::stat(runtimePath(g_cfg.dockFile, "mios-dock-icons.json").c_str(), &st) == 0 ? (int64_t)st.st_mtim.tv_sec * 1000000000LL + st.st_mtim.tv_nsec : -1;
        g_dockChecked = Clock::now();
    }

    STarget targetFor(const PHLWINDOW& w, bool restore, const std::optional<std::string>& sourceHint = std::nullopt, const std::string& overrideMonName = "") {
        PHLMONITOR mon = nullptr;
        if (!overrideMonName.empty()) {
            if (const auto wm = w->m_monitor.lock(); wm && wm->m_name == overrideMonName)
                mon = wm;
            else if (const auto fm = Desktop::focusState()->monitor(); fm && fm->m_name == overrideMonName)
                mon = fm;
            else {
                for (const auto& sib : Desktop::windowState()->windows()) {
                    if (sib) {
                        if (const auto sm = sib->m_monitor.lock(); sm && sm->m_name == overrideMonName) {
                            mon = sm;
                            break;
                        }
                    }
                }
            }
        }
        if (!mon)
            mon = w->m_monitor.lock();
        STargetQuery q;
        q.output           = !overrideMonName.empty() ? overrideMonName : (mon ? mon->m_name : std::string{});
        q.cls              = w->m_class;
        q.initialCls       = w->m_initialClass;
        q.address          = addressOf(w);
        q.monitor          = monitorRect(mon);
        q.restore          = restore;
        q.hiddenToEdge     = str(g_cfg.hiddenDock) != "icon";
        q.fallbackEdge     = edgeFromString(str(g_cfg.fallbackEdge)).value_or(eEdge::BOTTOM);
        q.sourcePreference = sourceHint.value_or(std::string{});
        markDockRead();
        return resolveTarget(parseDock(readSmall(runtimePath(g_cfg.dockFile, "mios-dock-icons.json"))), q);
    }

    void pruneMinimized() {
        std::erase_if(g_minimized, [](const SMinimized& m) {
            const auto w = m.window.lock();
            return !w || !w->m_isMapped || (!onMinimizedWs(w) && !g_lamps.contains(w.get()));
        });
    }

    void plainMinimize(const PHLWINDOW& w) {
        const auto lua = std::format("hl.dsp.window.move({{workspace = \"{}\", window = \"address:{}\", follow = false}})", minimizedWs(), addressOf(w));
        g_pEventLoopManager->doLater([lua] { dispatch(lua); });
    }

    SDispatchResult startMinimize(const PHLWINDOW& w) {
        if (!w || !w->m_isMapped)
            return {.success = false, .error = "mios_lamp.minimize: no such window"};
        if (onMinimizedWs(w))
            return {};
        if (const auto it = g_lamps.find(w.get()); it != g_lamps.end()) {
            auto& s = *it->second;
            if (s.kind == eKind::MINIMIZE)
                return {}; // already on its way
            if (s.phase == ePhase::PENDING) { // restore not started yet: the window simply stays minimized
                finish(s);
                g_lamps.erase(it);
                return {};
            }
            const auto now = Clock::now(); // reverse a restore / open from where it is
            s.tFrom        = progressAt(s, now);
            s.kind         = eKind::MINIMIZE;
            s.phase        = ePhase::RUNNING;
            s.start        = now;
            s.durS         = std::max(0.05, g_cfg.durMinimize->value() / 1000.0 * (1.0 - s.tFrom));
            armTimer();
            return {};
        }
        const auto mon = w->m_monitor.lock();
        const auto ws  = w->m_workspace;
        // No animation for: the lamp off, a window nobody sees, grouped windows (their layout alpha is the group's),
        // rotated outputs (the transformer does not handle output transforms).
        if (!eventOn("minimize") || !mon || !ws || !ws->isVisible() || w->isHidden() || w->m_group || mon->m_transform != WL_OUTPUT_TRANSFORM_NORMAL) {
            plainMinimize(w);
            return {};
        }
        pruneMinimized();
        std::erase_if(g_minimized, [&](const SMinimized& m) { return m.window.lock() == w; });
        SMinimized rec;
        rec.window    = w;
        rec.address   = addressOf(w);
        rec.workspace = workspaceSelector(ws);
        rec.cls       = w->m_class;
        rec.title     = w->m_title;
        rec.monitor   = mon->m_name;
        rec.floating  = w->m_isFloating;
        rec.geometry  = CBox{w->positionAnimation()->goal(), w->sizeAnimation()->goal()};
        rec.timeMs    = epochMs();
        for (const auto& sib : Desktop::windowState()->windows()) {
            if (sib && sib != w && sib->m_isMapped && !sib->m_isFloating && sib->m_workspace == ws) {
                rec.siblings.push_back({addressOf(sib), CBox{sib->positionAnimation()->goal(), sib->sizeAnimation()->goal()}});
            }
        }
        g_minimized.push_back(rec);

        auto       s   = makeUnique<SLamp>();
        const auto tgt = targetFor(w, false);
        s->window      = w;
        s->kind        = eKind::MINIMIZE;
        s->phase       = ePhase::RUNNING;
        s->start = s->phaseSince = s->lastPre = Clock::now();
        s->durS                              = std::max(0.05, g_cfg.durMinimize->value() / 1000.0);
        s->target                            = tgt.rect;
        s->edgeHint                          = tgt.edgeHint;
        s->source                            = tgt.source;
        s->targetSource                      = tgt.source;
        s->preMinBox                         = rec.geometry;
        s->wasFloating                       = rec.floating;
        s->address                           = rec.address;
        s->preMinMonitorName                 = rec.monitor;
        s->siblings                          = rec.siblings;
        attach(*s, w);
        setTag(*s, w, true);
        g_pHyprRenderer->damageWindow(w);
        g_lamps.emplace(w.get(), std::move(s));
        armTimer();
        return {};
    }

    PHLWINDOW lastMinimized() {
        pruneMinimized();
        for (auto it = g_minimized.rbegin(); it != g_minimized.rend(); ++it)
            if (const auto w = it->window.lock(); w && onMinimizedWs(w))
                return w;
        for (const auto& w : Desktop::windowState()->windows()) // minimized by an older build or by hand
            if (w && w->m_isMapped && onMinimizedWs(w))
                return w;
        return nullptr;
    }

    void doRestoreMoves(SLamp& s, const PHLWINDOW& w);

    SDispatchResult startRestore(PHLWINDOW w, const std::optional<std::string>& sourceHint = std::nullopt) {
        if (!w)
            w = lastMinimized();
        if (!w || !w->m_isMapped)
            return {.success = false, .error = "mios_lamp.restore: no minimized window"};
        auto       it  = g_lamps.find(w.get());
        const auto now = Clock::now();
        if (it != g_lamps.end()) {
            auto& s = *it->second;
            if (s.kind != eKind::MINIMIZE)
                return {}; // already restoring / opening
            if (s.phase == ePhase::RUNNING) { // reverse the minimize in place
                s.tFrom = progressAt(s, now);
                s.kind  = eKind::RESTORE;
                s.start = now;
                s.durS  = std::max(0.05, g_cfg.durRestore->value() / 1000.0 * s.tFrom);
                armTimer();
                return {};
            }
            // FINISHING: already moved (or moving) away -- bring it back like any minimized window
            finish(s);
            g_lamps.erase(it);
        }
        if (!onMinimizedWs(w)) { // not minimized: just raise it
            const auto sel = addressOf(w);
            g_pEventLoopManager->doLater([sel] { dispatch(std::format("hl.dsp.focus({{window = \"address:{}\"}})", sel)); });
            return {};
        }
        pruneMinimized();
        std::string                   wsTo;
        std::optional<CBox>           geo;
        CBox                          preBox;
        bool                          wasFlt = false;
        std::string                   preMonName;
        std::vector<SSiblingSnapshot> recSiblings;
        for (const auto& m : g_minimized)
            if (m.window.lock() == w) {
                wsTo        = m.workspace;
                preBox      = m.geometry;
                wasFlt      = m.floating;
                preMonName  = m.monitor;
                recSiblings = m.siblings;
                if (m.floating)
                    geo = m.geometry;
            }
        if (wsTo.empty()) { // minimized without the lamp (older build, by hand): the active workspace of its output
            const auto wm = w->m_monitor.lock();
            const auto fm = wm ? wm : Desktop::focusState()->monitor();
            wsTo          = fm ? workspaceSelector(fm->m_activeWorkspace) : std::string{};
        }
        if (!eventOn("restore") || wsTo.empty()) {
            const auto sel = addressOf(w);
            const auto ws  = wsTo.empty() ? std::string("e+0") : wsTo;
            g_pEventLoopManager->doLater([sel, ws] {
                dispatch(std::format("hl.dsp.window.move({{workspace = \"{}\", window = \"address:{}\", follow = false}})", ws, sel));
                dispatch(std::format("hl.dsp.focus({{window = \"address:{}\"}})", sel));
            });
            std::erase_if(g_minimized, [&](const SMinimized& m) { return m.window.lock() == w; });
            return {};
        }
        const auto tgt = targetFor(w, true, sourceHint, preMonName);
        auto s = makeUnique<SLamp>();
        s->window      = w;
        s->kind        = eKind::RESTORE;
        s->phase       = ePhase::RUNNING;
        s->tFrom = s->t = 1.0;
        s->start = s->phaseSince = s->lastPre = now;
        s->durS                              = std::max(0.05, g_cfg.durRestore->value() / 1000.0);
        s->target                            = tgt.rect;
        s->edgeHint                          = tgt.edgeHint;
        s->source                            = tgt.source;
        s->targetSource                      = sourceHint.value_or(tgt.source);
        s->preMinBox                         = preBox;
        s->wasFloating                       = wasFlt;
        s->address                           = addressOf(w);
        s->workspaceTo                       = wsTo;
        s->geometryTo                        = geo;
        s->preMinMonitorName                 = preMonName;
        s->siblings                          = recSiblings;
        attach(*s, w);
        setTag(*s, w, true);
        applyAlphas(*s, w);
        doRestoreMoves(*s, w);
        g_lamps.emplace(w.get(), std::move(s));
        g_pHyprRenderer->damageWindow(w);
        armTimer();
        return {};
    }

    // Timer side of a restore: put the window back, then aim the reverse lamp at where it came out of the dock.
    void doRestoreMoves(SLamp& s, const PHLWINDOW& w) {
        if (!s.wasFloating && !w->m_isFloating && s.preMinBox.w > 0 && s.preMinBox.h > 0) {
            // Find all currently mapped tiled windows on target workspace
            std::vector<PHLWINDOW> targetTiled;
            for (const auto& sib : Desktop::windowState()->windows()) {
                if (sib && sib != w && sib->m_isMapped && !sib->m_isFloating && sib->m_workspace &&
                    workspaceSelector(sib->m_workspace) == s.workspaceTo) {
                    targetTiled.push_back(sib);
                }
            }

            if (!targetTiled.empty()) {
                PHLWINDOW partner = nullptr;
                double bestDist = 1e9;
                const Vector2D preCenter = boxCenter(s.preMinBox);

                for (const auto& sib : targetTiled) {
                    const auto sibBox = CBox{sib->positionAnimation()->goal(), sib->sizeAnimation()->goal()};
                    const double d = (boxCenter(sibBox) - preCenter).size();
                    if (d < bestDist) {
                        bestDist = d;
                        partner = sib;
                    }
                }

                if (partner) {
                    const auto pBox = CBox{partner->positionAnimation()->goal(), partner->sizeAnimation()->goal()};
                    const double dx = preCenter.x - boxCenter(pBox).x;
                    const double dy = preCenter.y - boxCenter(pBox).y;
                    char dir = 'd';
                    if (std::abs(dx) / std::max(1.0, (double)pBox.w) > std::abs(dy) / std::max(1.0, (double)pBox.h)) {
                        dir = dx > 0 ? 'r' : 'l';
                    } else {
                        dir = dy > 0 ? 'd' : 'u';
                    }

                    // Focus partner and set dwindle preselect direction BEFORE moving w
                    dispatch(std::format("hl.dsp.focus({{window = \"address:{}\"}})", addressOf(partner)));
                    dispatch(std::format("hl.dsp.layout(\"preselect {}\")", dir));
                }
            }
        }

        const auto sel = "address:" + s.address;
        dispatch(std::format("hl.dsp.window.move({{workspace = \"{}\", window = \"{}\", follow = false}})", s.workspaceTo, sel));
        if (s.geometryTo && w->m_isFloating) {
            const auto g = *s.geometryTo;
            if ((w->positionAnimation()->goal() - g.pos()).size() > 0.5)
                dispatch(std::format("hl.dsp.window.move({{x = {}, y = {}, window = \"{}\"}})", (int)std::lround(g.x), (int)std::lround(g.y), sel));
            if ((w->sizeAnimation()->goal() - g.size()).size() > 0.5)
                dispatch(std::format("hl.dsp.window.resize({{x = {}, y = {}, window = \"{}\"}})", (int)std::lround(g.w), (int)std::lround(g.h), sel));
        } else if (!s.wasFloating && !w->m_isFloating && s.preMinBox.w > 0 && s.preMinBox.h > 0) {
            dispatch(std::format("hl.dsp.focus({{window = \"{}\"}})", sel));
            const auto curBox = CBox{w->positionAnimation()->goal(), w->sizeAnimation()->goal()};
            const bool match = std::abs(curBox.x - s.preMinBox.x) <= 6 && std::abs(curBox.y - s.preMinBox.y) <= 6 &&
                               std::abs(curBox.w - s.preMinBox.w) <= 12 && std::abs(curBox.h - s.preMinBox.h) <= 12;

            if (!match) {
                // If w needs to be promoted to root level (e.g. left column spanning full height)
                if ((s.preMinBox.h > curBox.h * 1.4) || (s.preMinBox.w > curBox.w * 1.4)) {
                    dispatch("hl.dsp.layout(\"movetoroot\")");
                }

                // Check if w is swapped with another sibling occupying s.preMinBox
                const auto postBox = CBox{w->positionAnimation()->goal(), w->sizeAnimation()->goal()};
                if (std::abs(postBox.x - s.preMinBox.x) > 10 || std::abs(postBox.y - s.preMinBox.y) > 10) {
                    const double curCx = boxCenter(postBox).x;
                    const double curCy = boxCenter(postBox).y;
                    const double preCx = boxCenter(s.preMinBox).x;
                    const double preCy = boxCenter(s.preMinBox).y;

                    if (std::abs(preCx - curCx) > std::abs(preCy - curCy)) {
                        dispatch(preCx < curCx ? "swapwindow l" : "swapwindow r");
                    } else {
                        dispatch(preCy < curCy ? "swapwindow u" : "swapwindow d");
                    }
                }
            }
        }
        dispatch(std::format("hl.dsp.focus({{window = \"{}\"}})", sel));
        Desktop::focusState()->fullWindowFocus(w, Desktop::FOCUS_REASON_OTHER);
        Desktop::windowState()->raise(w);
        // the window lands under its final geometry: skip Hyprland's move animation, the lamp is the motion
        w->positionAnimation()->warp();
        w->sizeAnimation()->warp();
        // Warp all sibling tiled windows on target workspace so the layout snaps immediately without backgrounding w
        const auto targetWs = w->m_workspace;
        for (const auto& sib : Desktop::windowState()->windows()) {
            if (sib && sib != w && sib->m_isMapped && sib->m_workspace == targetWs && !sib->m_isFloating) {
                sib->positionAnimation()->warp();
                sib->sizeAnimation()->warp();
            }
        }
        if (!s.target.valid()) {
            const auto tgt = targetFor(w, true, s.targetSource, s.preMinMonitorName);
            s.target       = tgt.rect;
            s.edgeHint     = tgt.edgeHint;
            s.source       = tgt.source;
            s.targetSource = tgt.source;
        }
        std::erase_if(g_minimized, [&](const SMinimized& m) { return m.window.lock() == w; });
    }

    SDispatchResult toggle(PHLWINDOW w) {
        if (!w)
            w = Desktop::focusState()->window();
        if (w && (onMinimizedWs(w) || (g_lamps.contains(w.get()) && g_lamps[w.get()]->kind == eKind::MINIMIZE)))
            return startRestore(w);
        return startMinimize(w);
    }

    void onWindowOpen(const PHLWINDOW& w) {
        if (!w || g_exiting || !eventOn("launch") || g_lamps.contains(w.get()))
            return;
        const auto mon = w->m_monitor.lock();
        if (!mon || mon->m_transform != WL_OUTPUT_TRANSFORM_NORMAL || !w->m_workspace || !w->m_workspace->isVisible())
            return;
        const auto pending = parseLaunches(readSmall(runtimePath(g_cfg.launchFile, "mios-lamp-launch.json")));
        if (pending.empty())
            return;
        const auto hit = matchLaunch(pending, epochMs(), std::max<double>(500, g_cfg.launchTimeout->value()), w->m_class, w->m_initialClass, mon->m_name,
                                     [](const std::string& id) { return std::find(g_consumed.begin(), g_consumed.end(), id) != g_consumed.end(); });
        if (!hit)
            return;
        g_consumed.push_back(hit->id);
        while (g_consumed.size() > 256)
            g_consumed.pop_front();
        // the dock's edge for this output, when it publishes one
        const auto dock = parseDock(readSmall(runtimePath(g_cfg.dockFile, "mios-dock-icons.json")));
        auto       s    = makeUnique<SLamp>();
        s->window       = w;
        s->kind         = eKind::OPEN;
        s->phase        = ePhase::RUNNING;
        s->tFrom = s->t = 1.0;
        s->start = s->phaseSince = s->lastPre = Clock::now();
        s->durS                              = std::max(0.05, g_cfg.durOpen->value() / 1000.0);
        s->target                            = hit->rect;
        if (const auto d = dock.find(mon->m_name); d != dock.end())
            s->edgeHint = d->second.edge;
        s->source  = "launch";
        s->address = addressOf(w);
        attach(*s, w);
        setTag(*s, w, true);
        applyAlphas(*s, w);
        g_lamps.emplace(w.get(), std::move(s));
        armTimer();
    }

    // The dock rewrites mios-dock-icons.json as icons magnify/move/reveal; follow it while lamps run (contract (a)).
    void followDock(Clock::time_point now) {
        if (now - g_dockChecked < std::chrono::milliseconds(50))
            return;
        g_dockChecked = now;
        struct stat st {};
        const int64_t mt = ::stat(runtimePath(g_cfg.dockFile, "mios-dock-icons.json").c_str(), &st) == 0 ? (int64_t)st.st_mtim.tv_sec * 1000000000LL + st.st_mtim.tv_nsec : -1;
        if (mt == g_dockMtimeNs)
            return;
        g_dockMtimeNs = mt;
        if (mt < 0) // the dock file went away: keep aiming where the animation started
            return;
        for (auto& [raw, sp] : g_lamps) {
            auto&      s = *sp;
            const auto w = s.window.lock();
            if (!w || s.phase != ePhase::RUNNING || (s.kind == eKind::OPEN && s.source == "launch"))
                continue;
            if (s.kind == eKind::RESTORE && (s.targetSource == "minimized" || s.source == "minimized")) {
                const auto tgt = targetFor(w, true, "minimized", s.preMinMonitorName);
                if (tgt.source == "minimized" && tgt.rect.valid()) {
                    s.target   = tgt.rect;
                    s.edgeHint = tgt.edgeHint;
                }
                continue; // Do NOT overwrite with "slot", "edge", or "fallback"
            }
            if (s.kind == eKind::RESTORE && (s.targetSource == "icon" || s.source == "icon")) {
                const auto tgt = targetFor(w, true, "icon", s.preMinMonitorName);
                if (tgt.source == "icon" && tgt.rect.valid()) {
                    s.target   = tgt.rect;
                    s.edgeHint = tgt.edgeHint;
                }
                continue;
            }
            const auto tgt = targetFor(w, s.kind == eKind::RESTORE, std::nullopt, s.preMinMonitorName);
            s.target       = tgt.rect;
            s.edgeHint     = tgt.edgeHint;
            s.source       = tgt.source;
        }
    }

    void pulse() {
        if (g_exiting)
            return;
        const auto             now = Clock::now();
        followDock(now);
        std::vector<CWindow*> drop;
        for (auto& [raw, sp] : g_lamps) {
            auto&      s = *sp;
            const auto w = s.window.lock();
            if (!w || !w->m_isMapped) {
                drop.push_back(raw);
                continue;
            }
            switch (s.phase) {
                case ePhase::PENDING:
                    doRestoreMoves(s, w);
                    s.phase = ePhase::RUNNING;
                    s.start = s.phaseSince = now;
                    g_pHyprRenderer->damageWindow(w);
                    break;
                case ePhase::RUNNING: {
                    if (tauAt(s, now) < 1.0) {
                        // watchdog: an output that stopped rendering (DPMS, nothing else damaged) still finishes
                        if (now - s.lastPre > std::chrono::milliseconds(60))
                            g_pHyprRenderer->damageWindow(w);
                        break;
                    }
                    if (s.kind == eKind::MINIMIZE) {
                        s.t = 1.0;
                        applyAlphas(s, w);
                        dispatch(std::format("hl.dsp.window.move({{workspace = \"{}\", window = \"address:{}\", follow = false}})", minimizedWs(), s.address));
                        s.phase      = ePhase::FINISHING;
                        s.phaseSince = now;
                    } else
                        s.phase = ePhase::DONE;
                    break;
                }
                case ePhase::FINISHING:
                    // Hyprland keeps drawing a window it moved to a hidden workspace while its own fade runs; the
                    // transformer stays on (content alpha 0) until the window is no longer drawn at all.
                    if ((now - s.lastDrawn > std::chrono::milliseconds(80) && now - s.phaseSince > std::chrono::milliseconds(50)) ||
                        now - s.phaseSince > std::chrono::milliseconds(2500))
                        s.phase = ePhase::DONE;
                    break;
                case ePhase::DONE: break;
            }
            if (s.phase == ePhase::DONE)
                drop.push_back(raw);
        }
        for (auto* raw : drop) {
            const auto it = g_lamps.find(raw);
            if (it == g_lamps.end())
                continue;
            finish(*it->second);
            g_lamps.erase(it);
        }
        if (!g_lamps.empty())
            armTimer();
    }

    PHLWINDOW resolveWindow(const std::optional<std::string>& sel) {
        if (!sel || sel->empty())
            return nullptr;
        return Desktop::viewState()->query().selector(*sel).runWindow();
    }

    SDispatchResult runKind(int kind, const std::optional<std::string>& sel, const std::optional<std::string>& source = std::nullopt) {
        if (g_exiting)
            return {.success = false, .error = "mios_lamp: unloading"};
        PHLWINDOW w = resolveWindow(sel);
        if (sel && !sel->empty() && !w)
            return {.success = false, .error = "mios_lamp: no window matches " + *sel};
        try {
            if (kind == 0)
                return startMinimize(w ? w : Desktop::focusState()->window());
            if (kind == 1)
                return startRestore(w, source);
            return toggle(w);
        } catch (const std::exception& e) { return {.success = false, .error = std::string("mios_lamp: ") + e.what()}; }
    }

    // ---- Lua: hl.plugin.mios_lamp.minimize/restore/toggle({window = "..."}) -> dispatcher -----------------------

    int luaRun(lua_State* L, int kind) {
        std::optional<std::string> sel;
        if (lua_isstring(L, lua_upvalueindex(1)))
            sel = lua_tostring(L, lua_upvalueindex(1));
        std::optional<std::string> source;
        if (lua_isstring(L, lua_upvalueindex(2)))
            source = lua_tostring(L, lua_upvalueindex(2));
        const auto res = runKind(kind, sel, source);
        lua_newtable(L);
        lua_pushboolean(L, res.success);
        lua_setfield(L, -2, "ok");
        lua_pushboolean(L, false);
        lua_setfield(L, -2, "pass_event");
        if (!res.success) {
            lua_pushstring(L, res.error.c_str());
            lua_setfield(L, -2, "error");
        }
        return 1;
    }
    int luaRunMinimize(lua_State* L) {
        return luaRun(L, 0);
    }
    int luaRunRestore(lua_State* L) {
        return luaRun(L, 1);
    }
    int luaRunToggle(lua_State* L) {
        return luaRun(L, 2);
    }
    int luaFactory(lua_State* L, lua_CFunction run) {
        if (lua_istable(L, 1)) {
            lua_getfield(L, 1, "window");
            if (!lua_isstring(L, -1)) {
                lua_pop(L, 1);
                lua_pushnil(L);
            }
            lua_getfield(L, 1, "source");
            if (!lua_isstring(L, -1)) {
                lua_pop(L, 1);
                lua_pushnil(L);
            }
        } else {
            lua_pushnil(L);
            lua_pushnil(L);
        }
        lua_pushcclosure(L, run, 2);
        return 1;
    }
    int luaMinimize(lua_State* L) {
        return luaFactory(L, luaRunMinimize);
    }
    int luaRestore(lua_State* L) {
        return luaFactory(L, luaRunRestore);
    }
    int luaToggle(lua_State* L) {
        return luaFactory(L, luaRunToggle);
    }

    // ---- hyprctl mios-lamp ... -----------------------------------------------------------------------------------

    std::string statusJson() {
        pruneMinimized();
        std::string out = std::format("{{\"version\":1,\"enabled\":{},\"events\":\"{}\",\"animating\":[", g_cfg.enabled->value() ? "true" : "false", jsonEscape(str(g_cfg.events)));
        bool        first = true;
        for (const auto& [raw, s] : g_lamps) {
            out += std::format("{}{{\"address\":\"{}\",\"kind\":\"{}\",\"phase\":\"{}\",\"t\":{:.4f},\"target\":[{:.1f},{:.1f},{:.1f},{:.1f}],\"source\":\"{}\"}}", first ? "" : ",",
                               s->address, kindName(s->kind), phaseName(s->phase), s->t, s->target.x, s->target.y, s->target.w, s->target.h, jsonEscape(s->source));
            first = false;
        }
        out += "],\"minimized\":[";
        first = true;
        for (const auto& m : g_minimized) {
            out += std::format("{}{{\"address\":\"{}\",\"workspace\":\"{}\",\"class\":\"{}\",\"title\":\"{}\",\"monitor\":\"{}\",\"time_ms\":{:.0f}}}", first ? "" : ",", m.address,
                               jsonEscape(m.workspace), jsonEscape(m.cls), jsonEscape(m.title), jsonEscape(m.monitor), m.timeMs);
            first = false;
        }
        return out + "]}";
    }

    std::string hyprctl(eHyprCtlOutputFormat, std::string request) {
        std::string_view rest{request};
        if (rest.starts_with("mios-lamp"))
            rest.remove_prefix(9);
        while (!rest.empty() && rest.front() == ' ')
            rest.remove_prefix(1);
        const auto  sp  = rest.find(' ');
        std::string cmd{rest.substr(0, sp)};
        std::string arg = sp == std::string_view::npos ? std::string{} : std::string(rest.substr(sp + 1));
        while (!arg.empty() && arg.back() == ' ')
            arg.pop_back();
        if (cmd.empty() || cmd == "status")
            return statusJson();
        if (cmd == "stats") {
            if (arg == "reset") {
                MiosFxStats::reset();
                return "ok";
            }
            if (arg == "raw")
                return MiosFxStats::rawJson();
            return MiosFxStats::json();
        }
        if (cmd == "minimize" || cmd == "restore" || cmd == "toggle") {
            const auto r = runKind(cmd == "minimize" ? 0 : cmd == "restore" ? 1 : 2, arg.empty() ? std::nullopt : std::optional<std::string>(arg));
            return r.success ? "ok" : r.error;
        }
        return "usage: hyprctl mios-lamp status | stats [reset|raw] | minimize|restore|toggle [window selector]";
    }
}

namespace MiosLampFx {
    bool owns(const Desktop::View::CWindow* w) {
        return w && g_lamps.contains(const_cast<CWindow*>(w));
    }

    void onFramePre(PHLMONITOR mon) {
        if (mon && !g_lamps.empty())
            MiosFxStats::frame(MiosFxStats::LAMP, mon->m_name, mon->m_refreshRate);
    }

    void onRenderPre() {
        if (g_lamps.empty() || g_exiting)
            return;
        const auto now   = Clock::now();
        const auto nowMs = msOf(now);
        const auto prm   = meshParams();
        static std::vector<SVertex> mesh;
        for (auto& [raw, sp] : g_lamps) {
            auto&      s = *sp;
            const auto w = s.window.lock();
            if (!w)
                continue;
            s.lastPre = now;
            if (s.phase == ePhase::RUNNING)
                s.t = progressAt(s, now);
            s.modelMs = nowMs;
            applyAlphas(s, w);
            if (!s.attached || !w->m_workspace || !w->m_workspace->isVisible())
                continue;
            const SRect win = rectOf(windowBoxGlobal(w));
            if (!buildMesh(win, s.target, chooseEdge(win, s.target, s.edgeHint), s.t, prm, mesh))
                continue;
            const SRect b   = meshBounds(mesh);
            CBox        ext = CBox{b.x, b.y, b.w, b.h}.expand(6);
            const SRect u   = unite(rectOf(ext), rectOf(s.lastExt));
            g_pHyprRenderer->damageBox(CBox{u.x, u.y, u.w, u.h});
            s.lastExt = ext;
        }
    }

    void onRenderPost() {
        for (auto& [raw, s] : g_lamps)
            if (s->attached && s->lastExt.w > 0 && s->phase != ePhase::DONE)
                g_pHyprRenderer->damageBox(s->lastExt);
    }

    void init(HANDLE handle) {
        PH = handle;
        using namespace Config::Values;
        g_cfg.enabled       = makeShared<CBoolValue>("plugin:mios_lamp:enabled", "Magic lamp for minimize / restore / launch from the dock", true);
        g_cfg.durOpen       = makeShared<CIntValue>("plugin:mios_lamp:duration_open_ms", "Launch-from-dock lamp length (ms)", 420);
        g_cfg.durMinimize   = makeShared<CIntValue>("plugin:mios_lamp:duration_minimize_ms", "Minimize lamp length (ms)", 520);
        g_cfg.durRestore    = makeShared<CIntValue>("plugin:mios_lamp:duration_restore_ms", "Restore lamp length (ms)", 440);
        g_cfg.curve         = makeShared<CStringValue>("plugin:mios_lamp:curve", "Easing: cubic bezier \"x1 y1 x2 y2\"", "0.42 0.0 0.25 1.0");
        g_cfg.cols          = makeShared<CIntValue>("plugin:mios_lamp:mesh_columns", "Mesh columns across the funnel", 8);
        g_cfg.rows          = makeShared<CIntValue>("plugin:mios_lamp:mesh_rows", "Mesh rows along the funnel (curve smoothness)", 32);
        g_cfg.events        = makeShared<CStringValue>("plugin:mios_lamp:events", "Comma list of launch, minimize, restore", "launch,minimize,restore");
        g_cfg.launchTimeout = makeShared<CIntValue>("plugin:mios_lamp:launch_timeout_ms", "A window mapping this long after a dock launch still grows out of its icon", 8000);
        g_cfg.hiddenDock    = makeShared<CStringValue>("plugin:mios_lamp:hidden_dock", "edge: an auto-hidden dock's target is its edge strip; icon: its revealed rect", "edge");
        g_cfg.fallbackEdge  = makeShared<CStringValue>("plugin:mios_lamp:fallback_edge", "Output edge used without dock data (bottom|top|left|right)", "bottom");
        g_cfg.minimizedWs   = makeShared<CStringValue>("plugin:mios_lamp:minimized_workspace", "Where minimized windows go", "special:minimized");
        g_cfg.tag           = makeShared<CStringValue>("plugin:mios_lamp:anim_tag", "Tag on a window while the lamp owns it (window rules key off it)", "mios-lamp-anim");
        g_cfg.dockFile      = makeShared<CStringValue>("plugin:mios_lamp:dock_file", "Dock icon rects (empty: $XDG_RUNTIME_DIR/mios-dock-icons.json)", "");
        g_cfg.launchFile    = makeShared<CStringValue>("plugin:mios_lamp:launch_file", "Pending dock launches (empty: $XDG_RUNTIME_DIR/mios-lamp-launch.json)", "");
        g_cfg.paneAlpha     = makeShared<CFloatValue>("plugin:mios_lamp:pane_alpha", "Layout alpha the rigid glass pane fades to while the lamp runs (content compensated)", 0.4F);
        for (const SP<IValue>& v : std::initializer_list<SP<IValue>>{g_cfg.enabled, g_cfg.durOpen, g_cfg.durMinimize, g_cfg.durRestore, g_cfg.curve, g_cfg.cols, g_cfg.rows, g_cfg.events,
                                                                     g_cfg.launchTimeout, g_cfg.hiddenDock, g_cfg.fallbackEdge, g_cfg.minimizedWs, g_cfg.tag, g_cfg.dockFile,
                                                                     g_cfg.launchFile, g_cfg.paneAlpha})
            HyprlandAPI::addConfigValueV2(PH, v);

        HyprlandAPI::addLuaFunction(PH, "mios_lamp", "minimize", &luaMinimize);
        HyprlandAPI::addLuaFunction(PH, "mios_lamp", "restore", &luaRestore);
        HyprlandAPI::addLuaFunction(PH, "mios_lamp", "toggle", &luaToggle);
        g_cmd = HyprlandAPI::registerHyprCtlCommand(PH, SHyprCtlCommand{.name = "mios-lamp", .exact = false, .fn = hyprctl});

        g_timer = makeShared<CEventLoopTimer>(std::nullopt, [](SP<CEventLoopTimer>, void*) {
            try {
                pulse();
            } catch (...) {}
        }, nullptr);
        g_pEventLoopManager->addTimer(g_timer);

        g_listeners.push_back(Event::bus()->m_events.window.moveToWorkspace.listen([](PHLWINDOW w, PHLWORKSPACE ws) {
            if (!w || !ws || ws->m_name != minimizedWs())
                return;
            for (const auto& m : g_minimized)
                if (m.window.lock() == w)
                    return;
            const auto mon = w->m_monitor.lock();
            std::string origWs = (w->m_workspace && w->m_workspace->m_name != minimizedWs()) ? workspaceSelector(w->m_workspace) : std::string{};
            if (origWs.empty()) {
                const auto wm = w->m_monitor.lock();
                const auto fm = wm ? wm : Desktop::focusState()->monitor();
                origWs        = fm ? workspaceSelector(fm->m_activeWorkspace) : std::string{"1"};
            }
            SMinimized rec;
            rec.window    = w;
            rec.address   = addressOf(w);
            rec.workspace = origWs;
            rec.cls       = w->m_class;
            rec.title     = w->m_title;
            rec.monitor   = mon ? mon->m_name : std::string{};
            rec.floating  = w->m_isFloating;
            rec.geometry  = CBox{w->positionAnimation()->goal(), w->sizeAnimation()->goal()};
            rec.timeMs    = epochMs();
            for (const auto& sib : Desktop::windowState()->windows()) {
                if (sib && sib != w && sib->m_isMapped && !sib->m_isFloating && sib->m_workspace && workspaceSelector(sib->m_workspace) == origWs) {
                    rec.siblings.push_back({addressOf(sib), CBox{sib->positionAnimation()->goal(), sib->sizeAnimation()->goal()}});
                }
            }
            g_minimized.push_back(rec);
        }));

        g_listeners.push_back(Event::bus()->m_events.window.open.listen([](PHLWINDOW w) {
            try {
                onWindowOpen(w);
            } catch (...) {}
        }));
        g_listeners.push_back(Event::bus()->m_events.window.openLate.listen([](PHLWINDOW w) {
            // mapWindow configures the fade-in after window.open: cancel it once more for a lamp launch
            const auto it = w ? g_lamps.find(w.get()) : g_lamps.end();
            if (it != g_lamps.end())
                applyAlphas(*it->second, w);
        }));
        g_listeners.push_back(Event::bus()->m_events.window.destroy.listen([](PHLWINDOWREF w) {
            if (w)
                g_lamps.erase(w.get());
        }));
    }

    void exit() {
        g_exiting = true;
        g_listeners.clear();
        if (g_timer) {
            g_timer->cancel();
            g_pEventLoopManager->removeTimer(g_timer);
            g_timer.reset();
        }
        for (auto& [raw, s] : g_lamps)
            finish(*s);
        g_lamps.clear();
        if (g_cmd)
            HyprlandAPI::unregisterHyprCtlCommand(PH, g_cmd);
        g_cmd.reset();
    }
}
