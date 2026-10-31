// AI-hint: Toplevel-capture guard for Hyprland 0.56 (MiOS original; [desktop.hyprland.mios-pacing.config].capture_guard).
// Two compositor bugs sit on the hyprland-toplevel-export path every window preview uses (Quickshell ScreencopyView on a
// foreign toplevel, the dock's previews), and both take a desktop down:
//   1. SEGV. A window whose output was removed while it was pinned keeps an expired m_monitor (CMonitor::onDisconnect
//      moves workspaces; WorkspacePlacementController skips pinned windows), so `hyprctl clients` reports monitor -1.
//      The first capture of it by a client builds a CScreenshareSession, whose init() dereferences monitor() -- null --
//      in m_captureBox.scale(monitor()->m_scale): the compositor dies (Screenshare::CScreenshareSession::init, reached
//      from CToplevelExportClient::captureToplevel). Reproduced in a nested Hyprland 0.56.2: pin a floating window on a
//      headless output, remove the output, capture the window from a new client.
//   2. Client kill. A capture naming a window that is already gone returns without creating the frame object the client
//      allocated, so the client's next request on that id is a protocol error ("invalid object") and the compositor
//      disconnects the client -- the whole shell when the shell asked for a preview of a window that just closed.
// The guard hooks CToplevelExportClient::captureToplevel (exported; found through findFunctionsByName) and answers a
// capture it cannot serve -- no window, an unmapped window, or a window without a live output -- with a frame object that
// reports `failed` at once (the protocol's answer for "this capture will not happen"). The client keeps a valid id, shows
// its fallback and may retry; the compositor never builds a session for a window without an output. The window itself is
// not touched (no rehoming: the guard changes no compositor state). Everything else passes through unchanged.
// The decision is a pure function (verdict) so the host test checks it without a compositor.
// AI-related: usr/share/mios/hyprland/plugins/mios-pacing/main.cpp, usr/share/mios/hyprland/plugins/mios-pacing/tests/capture_guard_test.cpp,
// AI-related: usr/share/mios/mios.toml [desktop.hyprland.mios-pacing.config] [desktop.hyprland.compositor], tests/test-hyprland-capture-patch-live.py
#pragma once

namespace MiosCaptureGuard {

    enum class eVerdict {
        PASS,         // a mapped window with a live output: Hyprland captures it
        FAIL_NONE,    // no window behind the handle (closed): answer failed
        FAIL_UNMAPPED, // the window is unmapped (closing, fading out): answer failed
        FAIL_NO_OUTPUT, // the window lost its output (pinned across a removed output): answer failed, never build a session
    };

    struct SWindowFacts {
        bool exists     = false;
        bool mapped     = false;
        bool hasMonitor = false;
    };

    constexpr eVerdict verdict(const SWindowFacts& w, bool guardEnabled) {
        if (!guardEnabled)
            return eVerdict::PASS;
        if (!w.exists)
            return eVerdict::FAIL_NONE;
        if (!w.mapped)
            return eVerdict::FAIL_UNMAPPED;
        if (!w.hasMonitor)
            return eVerdict::FAIL_NO_OUTPUT;
        return eVerdict::PASS;
    }

    constexpr const char* name(eVerdict v) {
        switch (v) {
            case eVerdict::PASS: return "pass";
            case eVerdict::FAIL_NONE: return "no_window";
            case eVerdict::FAIL_UNMAPPED: return "unmapped";
            case eVerdict::FAIL_NO_OUTPUT: return "no_output";
        }
        return "pass";
    }
}
