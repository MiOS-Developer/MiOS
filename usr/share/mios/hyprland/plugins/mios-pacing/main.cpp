// AI-hint: mios-pacing -- a per-output compositor frame cap for Hyprland 0.56 (MiOS original source; [desktop.framerate]).
// Hyprland has no frame limiter: every damage asks aquamarine for a frame and CMonitorFrameScheduler::onFrame renders
// and commits it. This plugin hooks onFrame (exported, non-virtual; found through findFunctionsByName) and admits a
// frame only on phase-locked deadlines 1/max_fps apart (pacing.hpp). A deferred frame is not lost: the damage stays
// in the monitor's accumulator and a per-output timer calls scheduleFrame at the deadline. Clients pace at the cap
// (their frame callbacks are sent by the render), direct-scanout fullscreen games included. On an output running
// VRR (adaptive sync) the panel refresh follows the commits, so 60/90/120 inside the VRR range are real refresh rates
// with no modeset; an output without active VRR is NOT capped by default (require_vrr; a cap there would judder at
// 1/2-vblank cadences) -- miosd framerate switches its mode instead. Excluded outputs (exclude_prefixes: the RDP/VNC
// headless outputs) keep their own pacing. Options plugin:mios_pacing:{enabled,max_fps,exclude_prefixes,slack_us,
// require_vrr}, set at login by the mios-gen projection and live by miosd (hl.config). `hyprctl mios-pacing status`
// prints per output: cap, why uncapped, VRR state, refresh, rendered fps, admitted / deferred / rescheduled counts.
// Wallpaper rate (operator 2026-10-23: "wallpaper(s) are 1/2 rate globally--except for Unlocked mode(s)"): a wallpaper
// client cannot present at W on its own -- Qt's ShaderEffectSource requests a second, identical frame after every
// layer update, so the native living wallpaper stepping at W commits at 2W (= the desktop rate). With
// wallpaper_fps = W the plugin hooks CWLSurfaceResource::frame and releases the frame callbacks of the layer
// surfaces named in wallpaper_namespaces at most W times a second (phase-locked deadlines; a held callback is never
// dropped: a timer releases it at the deadline). The wallpaper then presents at W on every output (remote ones too:
// W is process-wide), and Host.qml steps once per presented frame. 0 = no throttle.
// Window frame rate (operator 2026-10-30: the island shows "<host> FPS/<window> FPS"): the plugin follows the focused
// window -- the window that has the controls, whatever its kind (native, Flatpak, game, VM, remote, nested, PiP,
// Theatre; Hypr3D focuses the aimed/captured window) -- and counts the client commits of its surface tree (toplevel
// and subsurfaces) that attach a buffer with damage: the rate the window itself presents, independent of the output
// rate and of 2D or Hypr3D rendering. Only the focused tree is listened to (no per-commit cost anywhere else).
// `hyprctl mios-pacing status` reports it as "window" ({address, class, output, fps, frames, surfaces}; null with no
// focused window) and `hyprctl mios-pacing window` prints that object alone.
// AI-related: usr/share/mios/hyprland/plugins/mios-pacing/pacing.hpp, usr/share/mios/hyprland/plugins/mios-pacing/Makefile,
// AI-related: usr/share/mios/mios.toml [desktop.framerate] [desktop.hyprland.mios-pacing], src/mios-rs/miosd/src/framerate.rs
// AI-related: usr/share/mios/quickshell/PanelWindow.qml, usr/share/mios/quickshell/Config.qml, usr/share/mios/quickshell/Nested.qml, tests/test-window-fps.py
// The protocol header first: a host SDK may carry an empty src/protocols/types/content-type-v1.hpp that shadows the
// generated one for the relative include in ContentType.hpp.
#include <hyprland/protocols/content-type-v1.hpp>
#include <hyprland/src/plugins/PluginAPI.hpp>
#include <hyprland/src/plugins/HookSystem.hpp>
#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/output/Monitor.hpp>
#include <hyprland/src/output/MonitorFrameScheduler.hpp>
#include <hyprland/src/state/MonitorState.hpp>
#include <hyprland/src/managers/eventLoop/EventLoopManager.hpp>
#include <hyprland/src/managers/eventLoop/EventLoopTimer.hpp>
#include <hyprland/src/event/EventBus.hpp>
#include <hyprland/src/config/ConfigValue.hpp>
#include <hyprland/src/config/values/types/BoolValue.hpp>
#include <hyprland/src/config/values/types/IntValue.hpp>
#include <hyprland/src/config/values/types/StringValue.hpp>
#include <hyprland/src/protocols/core/Compositor.hpp>
#include <hyprland/src/desktop/state/LayerState.hpp>
#include <hyprland/src/desktop/view/LayerSurface.hpp>
#include <hyprland/src/desktop/view/WLSurface.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/desktop/state/FocusState.hpp>
#include <hyprland/src/protocols/core/Subcompositor.hpp>
#include <hyprland/src/helpers/time/Time.hpp>
#include <hyprland/src/notification/NotificationOverlay.hpp>

#include "pacing.hpp"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <format>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace {
    HANDLE         PHANDLE         = nullptr;
    CFunctionHook* g_hook          = nullptr;
    CFunctionHook* g_frameHook     = nullptr;
    CFunctionHook* g_notifHook     = nullptr;
    CFunctionHook* g_notifDrawHook = nullptr;
    bool           g_exiting       = false;
    using origOnFrame              = void (*)(void*);
    using origSurfaceFrame         = void (*)(void*, const Time::steady_tp&);
    using origAddNotification      = SP<Notification::CNotification> (*)(
        Notification::CNotificationOverlay*,
        const std::string&,
        const CHyprColor&,
        float,
        eIcons,
        float
    );
    using origNotifDraw            = void (*)(Notification::CNotificationOverlay*, PHLMONITOR);

    struct {
        SP<Config::Values::CBoolValue>   enabled;
        SP<Config::Values::CIntValue>    maxFps;
        SP<Config::Values::CStringValue> exclude;
        SP<Config::Values::CIntValue>    slackUs;
        SP<Config::Values::CBoolValue>   requireVrr;
        SP<Config::Values::CIntValue>    wallpaperFps;
        SP<Config::Values::CStringValue> wallpaperNamespaces;
    } g_cfg;

    // One wallpaper layer surface whose frame callbacks are held to wallpaper_fps.
    struct SWallpaper {
        WP<CWLSurfaceResource> surface;
        MiosPacing::SGate      gate;
        MiosPacing::SMeter     sentMeter;
        SP<CEventLoopTimer>    timer;
        uint64_t               sent = 0, held = 0, released = 0;
        std::string            ns, output;
    };
    std::unordered_map<CWLSurfaceResource*, SWallpaper> g_wallpapers;

    struct SOutput {
        PHLMONITORREF        monitor;
        MiosPacing::SGate    gate;
        MiosPacing::SMeter   rendered;
        SP<CEventLoopTimer>  timer;
        uint64_t             admitted = 0, deferred = 0, rescheduled = 0;
        int                  cap      = 0;
        std::string          reason   = "no frame yet";
    };
    std::unordered_map<std::string, SOutput> g_outputs; // by connector name

    // The focused window whose committed frames are counted (see the header).
    struct SFocusedWindow {
        PHLWINDOWREF                     window;
        WP<CWLSurfaceResource>           root;
        std::vector<CHyprSignalListener> listeners;
        MiosPacing::SWindowRate          rate;
        uint64_t                         follows = 0;
    } g_focused;
    std::vector<CHyprSignalListener>         g_listeners;
    SP<SHyprCtlCommand>                      g_cmd;

    std::string str(const SP<Config::Values::CStringValue>& v) {
        return std::string{v->value()};
    }

    bool newScheduling() {
        static auto PNEW = CConfigValue<Config::INTEGER>("render:new_render_scheduling");
        return PNEW.good() && *PNEW != 0;
    }

    PHLMONITOR monitorOf(void* scheduler) {
        for (const auto& m : State::monitorState()->allMonitors())
            if (m && m->m_frameScheduler && static_cast<void*>(m->m_frameScheduler.get()) == scheduler)
                return m;
        return nullptr;
    }

    SOutput& outputOf(const PHLMONITOR& m) {
        auto& o = g_outputs[m->m_name];
        if (o.monitor.lock() != m) { // a new monitor under a known name (hotplug): fresh state
            if (o.timer && g_pEventLoopManager)
                g_pEventLoopManager->removeTimer(o.timer);
            o         = SOutput{};
            o.monitor = m;
        }
        return o;
    }

    bool vrrActive(const PHLMONITOR& m) {
        return m->m_output && m->m_output->state && m->m_output->state->state().adaptiveSync;
    }

    // The cap for this output now, with the reason when there is none.
    int capFor(const PHLMONITOR& m, std::string& reason) {
        const int CAP = static_cast<int>(g_cfg.maxFps->value());
        reason = std::string{MiosPacing::uncappedReason(g_cfg.enabled->value(), CAP, m->isMirror(), MiosPacing::excluded(m->m_name, str(g_cfg.exclude)),
                                                        g_cfg.requireVrr->value(), vrrActive(m), newScheduling(), m->m_refreshRate)};
        return reason.empty() ? CAP : 0;
    }

    void arm(SOutput& o, const PHLMONITOR& m, MiosPacing::Clock::time_point at) {
        if (!g_pEventLoopManager)
            return;
        if (!o.timer) {
            o.timer = makeShared<CEventLoopTimer>(
                std::nullopt,
                [ref = PHLMONITORREF{m}](SP<CEventLoopTimer>, void*) {
                    if (g_exiting)
                        return;
                    const auto MON = ref.lock();
                    if (!MON)
                        return;
                    if (auto it = g_outputs.find(MON->m_name); it != g_outputs.end())
                        ++it->second.rescheduled;
                    MON->scheduleFrame(Aquamarine::IOutput::AQ_SCHEDULE_NEEDS_FRAME);
                },
                nullptr);
            g_pEventLoopManager->addTimer(o.timer);
        }
        if (o.timer->armed())
            return; // already fires at (or before) this deadline
        const auto NOW = MiosPacing::Clock::now();
        o.timer->updateTimeout(at > NOW ? at - NOW : MiosPacing::Clock::duration::zero());
    }

    void hkOnFrame(void* thisptr) {
        const auto ORIG = reinterpret_cast<origOnFrame>(g_hook->m_original);
        if (g_exiting)
            return ORIG(thisptr);
        const auto MON = monitorOf(thisptr);
        if (!MON)
            return ORIG(thisptr);
        auto& o = outputOf(MON);
        o.cap   = capFor(MON, o.reason);
        if (o.cap <= 0) {
            o.gate.reset();
            ++o.admitted;
            return ORIG(thisptr);
        }
        const auto NOW = MiosPacing::Clock::now();
        if (MiosPacing::admit(o.gate, NOW, o.cap, std::chrono::microseconds(std::max<int64_t>(0, g_cfg.slackUs->value())))) {
            ++o.admitted;
            return ORIG(thisptr);
        }
        // Not due: no render, no commit, no frame callbacks; the damage waits for the deadline.
        ++o.deferred;
        arm(o, MON, o.gate.next);
    }

    // ---- wallpaper frame callbacks ----

    // The layer surface of `s` when it is a wallpaper (its namespace is listed), else null.
    PHLLS wallpaperLayer(const CWLSurfaceResource* s) {
        const std::string NAMES = str(g_cfg.wallpaperNamespaces);
        if (NAMES.empty())
            return nullptr;
        for (const auto& l : Desktop::layerState()->layers()) {
            if (!l || !MiosPacing::listed(l->m_namespace, NAMES))
                continue;
            const auto WL = l->wlSurface();
            if (WL && WL->resource().get() == s)
                return l;
        }
        return nullptr;
    }

    SWallpaper& wallpaperOf(CWLSurfaceResource* s, const PHLLS& ls) {
        auto& w = g_wallpapers[s];
        if (w.surface.expired() || w.surface.get() != s) { // a new surface at a recycled address: fresh state
            if (w.timer && g_pEventLoopManager)
                g_pEventLoopManager->removeTimer(w.timer);
            w         = SWallpaper{};
            w.surface = s->m_self;
        }
        w.ns     = ls->m_namespace;
        w.output = ls->m_monitor ? ls->m_monitor->m_name : "";
        return w;
    }

    void armWallpaper(SWallpaper& w) {
        if (!g_pEventLoopManager)
            return;
        if (!w.timer) {
            w.timer = makeShared<CEventLoopTimer>(
                std::nullopt,
                [wp = w.surface](SP<CEventLoopTimer>, void*) {
                    const auto S = wp.lock();
                    if (!S)
                        return;
                    if (auto it = g_wallpapers.find(S.get()); it != g_wallpapers.end())
                        ++it->second.released;
                    S->frame(Time::steadyNow()); // through the hook: due now, so the held callbacks go out
                },
                nullptr);
            g_pEventLoopManager->addTimer(w.timer);
        }
        if (w.timer->armed())
            return;
        const auto NOW = MiosPacing::Clock::now();
        w.timer->updateTimeout(w.gate.next > NOW ? w.gate.next - NOW : MiosPacing::Clock::duration::zero());
    }

    void hkSurfaceFrame(void* thisptr, const Time::steady_tp& now) {
        const auto ORIG = reinterpret_cast<origSurfaceFrame>(g_frameHook->m_original);
        auto*      S    = static_cast<CWLSurfaceResource*>(thisptr);
        if (g_exiting || S->m_current.callbacks.empty())
            return ORIG(thisptr, now); // nothing to hold (no callback pending: the common case)
        const auto LS = wallpaperLayer(S);
        if (!LS)
            return ORIG(thisptr, now);
        const int  FPS = static_cast<int>(g_cfg.wallpaperFps->value());
        auto&      w   = wallpaperOf(S, LS);
        const auto T   = MiosPacing::Clock::now();
        if (FPS <= 0) { // no throttle: counted only (the status shows what the wallpaper asks for unpaced)
            w.gate.reset();
            ++w.sent;
            w.sentMeter.tick(T);
            return ORIG(thisptr, now);
        }
        if (MiosPacing::admit(w.gate, T, FPS, std::chrono::microseconds(std::max<int64_t>(0, g_cfg.slackUs->value())))) {
            ++w.sent;
            w.sentMeter.tick(T);
            return ORIG(thisptr, now);
        }
        // Not due: the callbacks stay queued on the surface (a later commit appends, never drops) until the deadline.
        ++w.held;
        armWallpaper(w);
    }

    // Release every held callback now (unload, throttle off): nothing may wait for a timer that is gone.
    void releaseWallpapers() {
        for (auto& [ptr, w] : g_wallpapers) {
            if (w.timer && g_pEventLoopManager)
                g_pEventLoopManager->removeTimer(w.timer);
            w.timer.reset();
            if (const auto S = w.surface.lock())
                S->frame(Time::steadyNow());
        }
        g_wallpapers.clear();
    }

    // ---- focused window frame rate ----

    // Count the frames `s` commits and follow the subsurfaces it gains (and has) the same way.
    void watchSurface(const SP<CWLSurfaceResource>& s) {
        if (!s)
            return;
        const auto KEY = reinterpret_cast<std::uintptr_t>(s.get());
        g_focused.listeners.push_back(s->m_events.stateCommit.listen([KEY](auto state) {
            if (g_exiting || !state)
                return;
            if (MiosPacing::isFrame(state->updated.bits.buffer, !!state->buffer, !state->damage.empty() || !state->bufferDamage.empty()))
                g_focused.rate.tick(KEY, MiosPacing::Clock::now());
        }));
        g_focused.listeners.push_back(s->m_events.newSubsurface.listen([](auto sub) {
            if (!g_exiting && sub)
                watchSurface(sub->m_surface.lock());
        }));
        for (const auto& sub : s->m_subsurfaces)
            if (const auto SUB = sub.lock())
                watchSurface(SUB->m_surface.lock());
    }

    SP<CWLSurfaceResource> rootOf(const PHLWINDOW& w) {
        const auto WL = w ? w->wlSurface() : nullptr;
        return WL ? WL->resource() : nullptr;
    }

    // Follow `w` (null: nothing focused). A new window or a new root surface starts a fresh meter.
    void follow(const PHLWINDOW& w) {
        const auto ROOT = rootOf(w);
        if (w == g_focused.window.lock() && ROOT.get() == g_focused.root.get() && (ROOT || !w))
            return;
        g_focused.listeners.clear();
        g_focused.rate.clear();
        g_focused.window = w;
        g_focused.root   = ROOT;
        ++g_focused.follows;
        if (w && ROOT)
            watchSurface(ROOT);
    }

    void followFocused() {
        if (g_exiting)
            return;
        const auto FS = Desktop::focusState();
        follow(FS ? FS->window() : nullptr);
    }

    std::string jsonEscape(const std::string& s);

    std::string windowJson() {
        followFocused(); // a focus change that sent no event (a raw focus reset) is caught here
        const auto W = g_focused.window.lock();
        if (!W || !W->m_isMapped || !g_focused.root.lock())
            return "null";
        const auto MON = W->m_monitor.lock();
        return std::format("{{\"address\":\"0x{:x}\",\"class\":\"{}\",\"output\":\"{}\",\"fps\":{:.2f},\"frames\":{},\"surfaces\":{},\"follows\":{}}}",
                           reinterpret_cast<std::uintptr_t>(W.get()), jsonEscape(W->m_class), jsonEscape(MON ? MON->m_name : ""),
                           g_focused.rate.fps(MiosPacing::Clock::now()), g_focused.rate.frames, g_focused.rate.surfaces.size(), g_focused.follows);
    }

    // ---- global Hyprland notification forwarding to MiOS Global Notifications (top-center glass island) ----

    void forwardToMiosNotifications(const std::string& text, const CHyprColor& color, float timeMs, eIcons icon) {
        if (g_exiting || text.empty())
            return;

        std::string trimmed = text;
        while (!trimmed.empty() && (trimmed.front() == ' ' || trimmed.front() == '\t' || trimmed.front() == '\n'))
            trimmed.erase(0, 1);
        while (!trimmed.empty() && (trimmed.back() == ' ' || trimmed.back() == '\t' || trimmed.back() == '\n'))
            trimmed.pop_back();
        if (trimmed.empty())
            return;

        std::string title = "Hyprland";
        std::string body  = trimmed;
        if (trimmed.front() == '[') {
            if (const auto close = trimmed.find(']'); close != std::string::npos && close > 1) {
                const std::string tag = trimmed.substr(1, close - 1);
                if (tag == "hypr3d")
                    title = "Hypr3D";
                else if (tag == "mios-pacing")
                    title = "MiOS Pacing";
                else if (tag == "mios-wobbly")
                    title = "MiOS Wobbly";
                else
                    title = tag;
                body = trimmed.substr(close + 1);
                while (!body.empty() && (body.front() == ' ' || body.front() == ':'))
                    body.erase(0, 1);
            }
        } else if (const auto colon = trimmed.find(": "); colon != std::string::npos && colon > 0 && colon <= 32) {
            title = trimmed.substr(0, colon);
            body  = trimmed.substr(colon + 2);
        }
        if (body.empty())
            body = trimmed;

        std::string kind = "info";
        if (icon == ICON_ERROR)
            kind = "error";
        else if (icon == ICON_WARNING)
            kind = "warning";
        else if (icon == ICON_OK)
            kind = "success";
        else if (icon == ICON_NONE) {
            if (color.r > 0.75 && color.g < 0.45 && color.b < 0.45)
                kind = "error";
            else if (color.r > 0.75 && color.g > 0.45 && color.b < 0.45)
                kind = "warning";
            else if (color.g > 0.65 && color.r < 0.55)
                kind = "success";
        }

        const int timeoutMs = std::clamp(static_cast<int>(timeMs > 0.f ? timeMs : 3000.f), 1000, 10000);

        std::thread([title = std::move(title), body = std::move(body), kind = std::move(kind), timeoutMs]() {
            static std::mutex                          s_mtx;
            static std::unordered_map<std::string, int> s_replaceIds;
            std::lock_guard<std::mutex> lock(s_mtx);

            auto shQuote = [](const std::string& s) {
                std::string out = "'";
                for (char c : s) {
                    if (c == '\'')
                        out += "'\\''";
                    else
                        out += c;
                }
                out += "'";
                return out;
            };

            std::string cmd = "/usr/bin/notify-send -p -a " + shQuote(title) +
                              " -t " + std::to_string(timeoutMs) + " -h int:transient:1";
            if (kind == "error")
                cmd += " -u critical";
            const int prevId = s_replaceIds[title];
            if (prevId > 0)
                cmd += " -r " + std::to_string(prevId);
            cmd += " " + shQuote(title) + " " + shQuote(body) + " 2>/dev/null";

            bool sent = false;
            if (FILE* fp = ::popen(cmd.c_str(), "r")) {
                char buf[64]{};
                if (::fgets(buf, sizeof(buf), fp)) {
                    const int id = std::atoi(buf);
                    if (id > 0) {
                        s_replaceIds[title] = id;
                        sent = true;
                    }
                }
                if (::pclose(fp) == 0)
                    sent = true;
            }
            if (!sent) {
                const std::string qsCmd =
                    "/usr/bin/quickshell ipc --path /usr/share/mios/quickshell/Config.qml "
                    "--any-display call notifications post " +
                    shQuote(title) + " " + shQuote(body) + " " + shQuote(kind) +
                    " >/dev/null 2>&1";
                (void)::system(qsCmd.c_str());
            }
        }).detach();
    }

    SP<Notification::CNotification> hkAddNotification(
        Notification::CNotificationOverlay* self,
        const std::string& text,
        const CHyprColor& color,
        float timeMs,
        eIcons icon,
        float fontSize
    ) {
        const auto ORIG = reinterpret_cast<origAddNotification>(g_notifHook->m_original);
        auto ret = ORIG(self, text, color, timeMs, icon, fontSize);
        if (!g_exiting && self) {
            self->dismissNotifications(-1);
            forwardToMiosNotifications(text, color, timeMs, icon);
        }
        return ret;
    }

    void hkNotifDraw(Notification::CNotificationOverlay* self, PHLMONITOR mon) {
        if (!g_exiting && self) {
            self->dismissNotifications(-1);
            return;
        }
        const auto ORIG = reinterpret_cast<origNotifDraw>(g_notifDrawHook->m_original);
        ORIG(self, mon);
    }

    std::string jsonEscape(const std::string& s) {
        std::string out;
        for (const char c : s) {
            if (c == '"' || c == '\\')
                out += '\\';
            if (static_cast<unsigned char>(c) < 0x20)
                continue;
            out += c;
        }
        return out;
    }

    std::string statusJson() {
        const auto  NOW = MiosPacing::Clock::now();
        std::string out = std::format("{{\"version\":1,\"enabled\":{},\"max_fps\":{},\"exclude_prefixes\":\"{}\",\"slack_us\":{},\"require_vrr\":{},\"new_render_scheduling\":{},\"hooked\":{},\"outputs\":[",
                                      g_cfg.enabled->value() ? "true" : "false", g_cfg.maxFps->value(), jsonEscape(str(g_cfg.exclude)), g_cfg.slackUs->value(),
                                      g_cfg.requireVrr->value() ? "true" : "false", newScheduling() ? "true" : "false", g_hook ? "true" : "false");
        bool first = true;
        for (const auto& m : State::monitorState()->allMonitors()) {
            if (!m)
                continue;
            auto&       o = outputOf(m);
            std::string reason;
            const int   CAP = capFor(m, reason);
            out += std::format("{}{{\"name\":\"{}\",\"cap\":{},\"uncapped\":\"{}\",\"vrr_active\":{},\"refresh\":{:.3f},\"rendered_fps\":{:.2f},\"admitted\":{},\"deferred\":{},\"rescheduled\":{}}}",
                               first ? "" : ",", jsonEscape(m->m_name), CAP, jsonEscape(reason), vrrActive(m) ? "true" : "false", m->m_refreshRate, o.rendered.fps(NOW),
                               o.admitted, o.deferred, o.rescheduled);
            first = false;
        }
        out += std::format("],\"wallpaper_fps\":{},\"wallpaper_namespaces\":\"{}\",\"wallpaper_hooked\":{},\"wallpapers\":[", g_cfg.wallpaperFps->value(),
                           jsonEscape(str(g_cfg.wallpaperNamespaces)), g_frameHook ? "true" : "false");
        first = true;
        std::erase_if(g_wallpapers, [](const auto& e) { return e.second.surface.expired() && !(e.second.timer && e.second.timer->armed()); });
        for (auto& [ptr, w] : g_wallpapers) {
            out += std::format("{}{{\"namespace\":\"{}\",\"output\":\"{}\",\"callbacks_fps\":{:.2f},\"sent\":{},\"held\":{},\"released\":{}}}", first ? "" : ",",
                               jsonEscape(w.ns), jsonEscape(w.output), w.sentMeter.fps(NOW), w.sent, w.held, w.released);
            first = false;
        }
        return out + "],\"window\":" + windowJson() + "}";
    }

    std::string hyprctl(eHyprCtlOutputFormat, std::string request) {
        std::string_view rest{request};
        if (rest.starts_with("mios-pacing"))
            rest.remove_prefix(11);
        while (!rest.empty() && rest.front() == ' ')
            rest.remove_prefix(1);
        while (!rest.empty() && (rest.back() == ' ' || rest.back() == '\n'))
            rest.remove_suffix(1);
        if (rest.empty() || rest == "status")
            return statusJson();
        if (rest == "window")
            return windowJson();
        if (rest == "reset") {
            for (auto& [name, o] : g_outputs) {
                o.admitted = o.deferred = o.rescheduled = 0;
                o.rendered.t.clear();
            }
            for (auto& [ptr, w] : g_wallpapers) {
                w.sent = w.held = w.released = 0;
                w.sentMeter.t.clear();
            }
            g_focused.rate.clear();
            return "ok";
        }
        return "usage: hyprctl mios-pacing status | window | reset";
    }
}

APICALL EXPORT std::string PLUGIN_API_VERSION() {
    return HYPRLAND_API_VERSION;
}

APICALL EXPORT PLUGIN_DESCRIPTION_INFO PLUGIN_INIT(HANDLE handle) {
    PHANDLE = handle;
    if (std::string(__hyprland_api_get_hash()) != __hyprland_api_get_client_hash()) {
        HyprlandAPI::addNotification(PHANDLE, "[mios-pacing] headers do not match the running Hyprland; not loading", CHyprColor{1.0, 0.2, 0.2, 1.0}, 8000);
        throw std::runtime_error("[mios-pacing] version mismatch");
    }

    using namespace Config::Values;
    g_cfg.enabled    = makeShared<CBoolValue>("plugin:mios_pacing:enabled", "Cap local outputs at max_fps", true);
    g_cfg.maxFps     = makeShared<CIntValue>("plugin:mios_pacing:max_fps", "Frames per second each local output may commit (0 = no cap)", 0);
    g_cfg.exclude    = makeShared<CStringValue>("plugin:mios_pacing:exclude_prefixes", "Comma-separated output-name prefixes never capped", "HEADLESS-");
    g_cfg.slackUs    = makeShared<CIntValue>("plugin:mios_pacing:slack_us", "A frame due within this many microseconds passes (timer jitter)", 500);
    g_cfg.requireVrr = makeShared<CBoolValue>("plugin:mios_pacing:require_vrr", "Cap only outputs whose adaptive sync is active", true);
    g_cfg.wallpaperFps =
        makeShared<CIntValue>("plugin:mios_pacing:wallpaper_fps", "Frame callbacks per second for the wallpaper layer surfaces (0 = every output frame)", 0);
    g_cfg.wallpaperNamespaces =
        makeShared<CStringValue>("plugin:mios_pacing:wallpaper_namespaces", "Comma-separated layer namespaces wallpaper_fps applies to (exact names)", "mios-wallpaper");
    for (const SP<IValue>& v : std::initializer_list<SP<IValue>>{g_cfg.enabled, g_cfg.maxFps, g_cfg.exclude, g_cfg.slackUs, g_cfg.requireVrr, g_cfg.wallpaperFps,
                                                                  g_cfg.wallpaperNamespaces})
        HyprlandAPI::addConfigValueV2(PHANDLE, v);

    // The one function the cap lives in. Loud failure when Hyprland renamed it: miosd then falls back to modes.
    for (const auto& match : HyprlandAPI::findFunctionsByName(PHANDLE, "onFrame")) {
        if (match.demangled.find("CMonitorFrameScheduler::onFrame") == std::string::npos)
            continue;
        g_hook = HyprlandAPI::createFunctionHook(PHANDLE, match.address, reinterpret_cast<void*>(&hkOnFrame));
        break;
    }
    if (!g_hook || !g_hook->hook()) {
        HyprlandAPI::addNotification(PHANDLE, "[mios-pacing] CMonitorFrameScheduler::onFrame not found: no frame cap", CHyprColor{1.0, 0.2, 0.2, 1.0}, 8000);
        throw std::runtime_error("[mios-pacing] cannot hook CMonitorFrameScheduler::onFrame");
    }
    // The wallpaper throttle is optional: without the hook the frame cap still works and miosd keeps the wallpaper on its
    // own timer (the status reports wallpaper_hooked = false; miosd only marks the wallpaper paced when it is true).
    for (const auto& match : HyprlandAPI::findFunctionsByName(PHANDLE, "frame")) {
        if (match.demangled.find("CWLSurfaceResource::frame(") == std::string::npos)
            continue;
        g_frameHook = HyprlandAPI::createFunctionHook(PHANDLE, match.address, reinterpret_cast<void*>(&hkSurfaceFrame));
        break;
    }
    if (g_frameHook && !g_frameHook->hook()) {
        HyprlandAPI::removeFunctionHook(PHANDLE, g_frameHook);
        g_frameHook = nullptr;
    }
    if (!g_frameHook)
        HyprlandAPI::addNotification(PHANDLE, "[mios-pacing] CWLSurfaceResource::frame not found: the wallpaper is not paced", CHyprColor{1.0, 0.6, 0.2, 1.0}, 8000);

    // Intercept Hyprland's built-in corner notification overlay and route all notifications to the MiOS Global
    // Notification glass island (Notifications.qml, top-center under the status island).
    for (const auto& match : HyprlandAPI::findFunctionsByName(PHANDLE, "addNotification")) {
        if (match.demangled.find("CNotificationOverlay::addNotification(") == std::string::npos)
            continue;
        g_notifHook = HyprlandAPI::createFunctionHook(PHANDLE, match.address, reinterpret_cast<void*>(&hkAddNotification));
        break;
    }
    if (g_notifHook && !g_notifHook->hook()) {
        HyprlandAPI::removeFunctionHook(PHANDLE, g_notifHook);
        g_notifHook = nullptr;
    }
    for (const auto& match : HyprlandAPI::findFunctionsByName(PHANDLE, "draw")) {
        if (match.demangled.find("CNotificationOverlay::draw(") == std::string::npos)
            continue;
        g_notifDrawHook = HyprlandAPI::createFunctionHook(PHANDLE, match.address, reinterpret_cast<void*>(&hkNotifDraw));
        break;
    }
    if (g_notifDrawHook && !g_notifDrawHook->hook()) {
        HyprlandAPI::removeFunctionHook(PHANDLE, g_notifDrawHook);
        g_notifDrawHook = nullptr;
    }
    if (Notification::overlay())
        Notification::overlay()->dismissNotifications(-1);

    g_listeners.push_back(Event::bus()->m_events.render.pre.listen([](PHLMONITOR mon) {
        if (g_exiting || !mon)
            return;
        outputOf(mon).rendered.tick(MiosPacing::Clock::now());
    }));
    g_listeners.push_back(Event::bus()->m_events.window.active.listen([](PHLWINDOW w, Desktop::eFocusReason) {
        if (!g_exiting)
            follow(w);
    }));
    g_listeners.push_back(Event::bus()->m_events.window.destroy.listen([](PHLWINDOWREF w) {
        if (!g_exiting && (g_focused.window.expired() || w.get() == g_focused.window.get()))
            follow(nullptr);
    }));
    followFocused();
    g_cmd = HyprlandAPI::registerHyprCtlCommand(PHANDLE, SHyprCtlCommand{.name = "mios-pacing", .exact = false, .fn = hyprctl});

    return {"mios-pacing", "Per-output frame cap for the MiOS frame-rate policy (VRR outputs); focused-window frame meter", "MiOS", "1.1"};
}

APICALL EXPORT void PLUGIN_EXIT() {
    g_exiting = true;
    releaseWallpapers(); // the hooks are still installed here; with g_exiting they pass straight through
    g_listeners.clear();
    g_focused.listeners.clear();
    if (g_cmd)
        HyprlandAPI::unregisterHyprCtlCommand(PHANDLE, g_cmd);
    for (auto& [name, o] : g_outputs)
        if (o.timer && g_pEventLoopManager)
            g_pEventLoopManager->removeTimer(o.timer);
    g_outputs.clear();
    // The hook system unhooks and frees the plugin's hooks on unload; an output that had a frame deferred gets one
    // more scheduleFrame so nothing waits for a timer that is gone.
    for (const auto& m : State::monitorState()->allMonitors())
        if (m)
            m->scheduleFrame(Aquamarine::IOutput::AQ_SCHEDULE_NEEDS_FRAME);
}
