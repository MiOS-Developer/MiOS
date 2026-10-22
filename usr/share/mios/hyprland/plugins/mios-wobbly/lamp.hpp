// AI-hint: Magic lamp (Compiz-style genie) for minimize / restore / launch-from-dock, inside the mios-wobbly plugin
// (one compositor mesh backend: same transformer pipeline, frame pacing and GL state handling as the wobble).
// main.cpp calls these hooks; everything else lives in lamp.cpp. Contract with the dock:
// /home/mios/mios-desktop-work-1/lamp-contract.md (dock icon rects + pending launches in $XDG_RUNTIME_DIR, Lua
// dispatchers hl.plugin.mios_lamp.minimize/restore/toggle, `hyprctl mios-lamp status|stats`).
// AI-related: usr/share/mios/hyprland/plugins/mios-wobbly/lamp.cpp, usr/share/mios/hyprland/plugins/mios-wobbly/lampmath.hpp, usr/share/mios/hyprland/plugins/mios-wobbly/main.cpp
#pragma once

#include <hyprland/src/plugins/PluginAPI.hpp>

namespace MiosLampFx {
    void init(HANDLE handle);
    void exit();
    // RENDER_PRE (before beginRender collects the frame's damage): advance every lamp to now, damage its shape.
    void onRenderPre();
    // RENDER_POST: re-damage what was drawn so the next frame repaints it (and is scheduled while it moves).
    void onRenderPost();
    // render.pre: per-output frame counter for `hyprctl mios-lamp stats`.
    void onFramePre(PHLMONITOR mon);
    // The lamp animates (or holds) this window; the wobble must not attach to it meanwhile.
    bool owns(const Desktop::View::CWindow* w);
}
