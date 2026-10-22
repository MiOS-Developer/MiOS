// AI-hint: Per-output mobile layout verdict ([desktop.mobile]) and global scale policy result
// ([desktop.scaling]) for the shell. The Hyprland projection (mios-gen render-hyprland,
// render_hyprland::mobile_profile) runs every output through the policy (DPI x scale x resolution x
// orientation; mobile = scaled logical short side under the breakpoint, local or remote per
// applies_to) and publishes the verdict
// and the [desktop.mobile] table in $XDG_RUNTIME_DIR/mios-mobile-outputs.json on load and on every
// monitor add/remove/layout change. One decision, one place: the shell never re-derives it. A missing
// or unreadable file means desktop mode everywhere.
// Also shared by the mobile surfaces: the desktop's motion ([desktop.hyprland] animations / curves and
// [desktop.hyprland.config.animations] enabled: motion(leaf) -> duration and bezier, so the status island moves on the
// same curves as the windows and their tabs) and, while any output is mobile, one debounced
// Hyprland.refreshToplevels() after window/workspace/tab events, so every PanelWindow reads its windows' goal
// geometry (lastIpcObject at/size) to place the island between their controls tabs.
// AI-related: usr/share/mios/quickshell/PanelWindow.qml, usr/share/mios/quickshell/Sidebar.qml,
// tools/native/mios-gen/src/render.rs (mobile_profile), tools/native/mios-gen/src/display_scale.rs,
// usr/share/mios/mios.toml [desktop.mobile] [desktop.scaling] [desktop.hyprland] animations curves
pragma Singleton
import QtQuick
import Quickshell
import Quickshell.Hyprland
import Quickshell.Io

Singleton {
    id: mobile
    // { "<output>": {mobile, origin, logical_width, logical_height (as the compositor has it now),
    //   scale, scale_120, policy_logical_width, policy_logical_height (the policy's result),
    //   applied_scale, explicit, auto_apply, source, dpi} }
    property var outputs: ({})
    // [desktop.mobile] as projected (window_controls, notch_gap_px, shoulder_dot_px, app_menu, ...)
    property var config: ({})
    // true when the projection put hyprbars' mobile tabs (window rule hyprbars:mobile) on mobile outputs' windows:
    // the compositor then draws the window-controls tabs and the shell only anchors their popups (ControlsTab.qml)
    property bool hyprbarsTabs: false

    function parse(t) {
        try {
            const j = JSON.parse(t) || ({})
            outputs = j.outputs || ({})
            config = j.config || ({})
            hyprbarsTabs = j.hyprbars_tabs === true
        } catch (e) {
            // A torn read keeps the last good verdict; the next poll rereads.
        }
    }
    function isMobile(name) {
        const o = name ? outputs[name] : null
        return !!o && o.mobile === true && config.enabled !== false
    }
    // Policy scale for the output ([desktop.scaling]); 1 when unknown.
    function scaleOf(name) {
        const o = name ? outputs[name] : null
        return (o && o.scale > 0) ? o.scale : 1
    }
    // Logical size the policy targets ({width, height}); falls back to the compositor's current one.
    function logicalSize(name) {
        const o = name ? outputs[name] : null
        if (!o)
            return ({ width: 0, height: 0 })
        return ({ width: o.policy_logical_width || o.logical_width || 0,
                  height: o.policy_logical_height || o.logical_height || 0 })
    }
    // Extra factor the shell applies itself where the compositor could not apply the policy scale
    // (remote outputs while [desktop.scaling].apply_to omits "remote", or a remote output the RDP bridge
    // kept at scale 1): policy scale / applied scale. PanelWindow.qml (pill, shoulders) and Sidebar.qml
    // (dock, drawer) multiply their window geometry by it and scale their contents to match.
    function uiScale(name) {
        const o = name ? outputs[name] : null
        if (!o || !(o.scale > 0) || !(o.applied_scale > 0))
            return 1
        return Math.max(1, o.scale / o.applied_scale)
    }
    // [desktop.mobile.remote_effects] (the cheaper glass for remote outputs) when it applies to this output's
    // origin, else null. The compositor tags windows there with its hyprglass preset; shell surfaces opt in by
    // naming their layer `<namespace>-remote` (layerNamespace), which the projection maps to the same preset
    // (hyprglass layers are preset per namespace, not per output). Same look, fewer passes.
    function remoteEffects(name) {
        const o = name ? outputs[name] : null
        const r = config.remote_effects
        if (!o || !r || typeof r !== "object" || r.enabled === false) return null
        const on = Array.isArray(r.applies_to) ? r.applies_to : ["remote"]
        return on.indexOf(o.origin) >= 0 ? r : null
    }
    function layerNamespace(base, name) { return remoteEffects(name) ? base + "-remote" : base }

    property FileView file: FileView {
        path: Quickshell.env("MIOS_MOBILE_OUTPUTS_JSON") || ((Quickshell.env("XDG_RUNTIME_DIR") || "/run/user/1000") + "/mios-mobile-outputs.json")
        watchChanges: true
        preload: true
        // a layer's namespace is fixed when its surface is created: read the (small) verdict synchronously so
        // the first surfaces on an output already know whether it takes the remote effects profile
        blockLoading: true
        printErrors: false
        onFileChanged: reload()
        onLoaded: mobile.parse(text())
        onLoadFailed: { mobile.outputs = ({}); mobile.config = ({}); mobile.hyprbarsTabs = false }
    }
    // First read now (blockLoading: text() waits for the load), so the first surfaces see the verdict.
    Component.onCompleted: { mobile.parse(file.text()); mobile.reloadMotion() }
    // ---- desktop motion ([desktop.hyprland] animations / curves) ----------------------------------------------------
    property var animations: []
    property var curves: []
    property bool animationsEnabled: true
    // A named curve as [x1, y1, x2, y2]: [desktop.hyprland] curves, else Hyprland's built-ins ("linear", "default").
    function curve(name) {
        if (name === "linear") return [0, 0, 1, 1]
        for (const c of curves) {
            const p = c && c.name === name ? c.points : null
            if (Array.isArray(p) && p.length === 2 && Array.isArray(p[0]) && Array.isArray(p[1]))
                return [Number(p[0][0]), Number(p[0][1]), Number(p[1][0]), Number(p[1][1])]
        }
        return [0, 0.75, 0.15, 1]   // Hyprland's "default"
    }
    // An animations leaf as {duration (ms; speed is in deciseconds), bezier, enabled}; null when the SSOT has no such leaf.
    function motion(leaf) {
        for (const a of animations)
            if (a && a.leaf === leaf)
                return ({ duration: (animationsEnabled && a.enabled !== false) ? Math.max(0, Number(a.speed) || 0) * 100 : 0,
                          bezier: curve(a.bezier || "default"), enabled: animationsEnabled && a.enabled !== false })
        return null
    }
    property string resolver: Quickshell.env("MIOS_TOML_GET") || "/usr/bin/mios-toml-get"
    property Process motionReader: Process {
        command: ["sh", "-c", "for k in animations curves; do printf '%s\\t' \"$k\"; \"$1\" desktop.hyprland \"$k\" 2>/dev/null | tr -d '\\n'; printf '\\n'; done; " +
                  "printf 'enabled\\t'; \"$1\" desktop.hyprland.config.animations enabled true 2>/dev/null | tr -d '\\n'; printf '\\n'",
                  "sh", mobile.resolver]
        stdout: StdioCollector {
            onStreamFinished: {
                for (const line of String(text).split("\n")) {
                    const tab = line.indexOf("\t")
                    if (tab <= 0) continue
                    const k = line.slice(0, tab), v = line.slice(tab + 1).trim()
                    try {
                        if (k === "animations") { const a = JSON.parse(v); if (Array.isArray(a)) mobile.animations = a }
                        else if (k === "curves") { const c = JSON.parse(v); if (Array.isArray(c)) mobile.curves = c }
                        else if (k === "enabled") mobile.animationsEnabled = v !== "false"
                    } catch (e) { /* unreadable: keep the last good value */ }
                }
            }
        }
    }
    function reloadMotion() { if (!motionReader.running) motionReader.running = true }
    property string lastConfig: ""
    onConfigChanged: {
        // the projection republishes [desktop.mobile] on every Hyprland config load: reread the motion then too
        const s = JSON.stringify(config || {})
        if (s !== lastConfig) { lastConfig = s; reloadMotion() }
    }

    // ---- window geometry for the mobile island -----------------------------------------------------------------------
    readonly property bool anyMobile: {
        if (config.enabled === false) return false
        for (const k in outputs) if (outputs[k] && outputs[k].mobile === true) return true
        return false
    }
    // Events after which a window's goal geometry (or which windows are shown) may have changed; hyprbarstab is the
    // controls-tab plugin announcing a tab's new extent.
    readonly property var geometryEvents: ["openwindow", "closewindow", "movewindow", "movewindowv2", "changefloatingmode",
        "fullscreen", "workspace", "workspacev2", "activespecial", "activespecialv2", "monitoradded", "monitoraddedv2",
        "monitorremoved", "monitorremovedv2", "configreloaded", "hyprbarstab", "minimized", "pin"]
    function refreshSoon() {
        if (!refreshNow.running) refreshNow.start()
        refreshSettled.restart()
    }
    property Timer refreshNow: Timer { interval: 16; onTriggered: Hyprland.refreshToplevels() }
    // once more after a burst settles: a window's goal geometry can land after the event that announced it
    property Timer refreshSettled: Timer { interval: 250; onTriggered: Hyprland.refreshToplevels() }
    property Connections hyprEvents: Connections {
        target: Hyprland
        enabled: mobile.anyMobile
        function onRawEvent(event) { if (mobile.geometryEvents.indexOf(event.name) >= 0) mobile.refreshSoon() }
    }
    onAnyMobileChanged: if (anyMobile) refreshSoon()

    // The compositor replaces the file by rename (and may create it after the shell starts, e.g. on a
    // config reload); a cheap reread keeps the verdict current whatever the file watch saw.
    property Timer poll: Timer {
        interval: 2000
        repeat: true
        running: true
        onTriggered: mobile.file.reload()
    }
}
