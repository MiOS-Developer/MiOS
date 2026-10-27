// AI-hint: The window-control glass for the shell, resolved from the SAME SSOT keys the desktop window controls
// (hyprbars overlay tab, mios-gen render-hyprland) are projected from, so the mobile shoulders, their dots, the
// status pill and the dock are the desktop tab's material and buttons rather than copies of its values:
//   material  [desktop.hyprland.hyprbars.config] pill_palette at pill_alpha (an SSOT percentage reference such as
//             "theme.opacity"): the window colour layer the tab lays over the glass. The glass itself (frost,
//             tint, specular streak, fresnel rim, lens fringe) is hyprglass' layer pass with glass_preset (the
//             [desktop.hyprland.hyprglass] preset the tab and the window bodies use), drawn by the compositor
//             behind every non-transparent shell pixel -- the shell never draws a second, hand-tuned glass.
//   geometry  [desktop.hyprland.config.decoration] rounding (the global window rounding) and the tab rules of the
//             hyprbars overlay patch: corner = min(rounding, height) / 2, fillet = min(rounding / 2, height / 2).
//   buttons   [[desktop.hyprland.hyprbars.buttons]] palette / icon_palette / size / icon; the close button is the
//             one whose action closes the window. icon_on_hover, pill_padding, bar_button_padding as the tab.
//   lights    the preset's <default_theme> palette_fresnel / fresnel_alpha (rim) for the menu dot fallback.
// Colour names resolve through [colors]. Read with mios-toml-get (the layered vendor/host/user resolver) at
// start and whenever the theme bridge or the mobile projection changes; until then (or if the SSOT cannot be
// read) the Theme.qml bridge values -- themselves SSOT projections -- stand in. MIOS_TOML_GET overrides the
// resolver binary (tests point it at a fixture).
// AI-related: usr/share/mios/mios.toml [desktop.hyprland.hyprbars] [desktop.hyprland.hyprglass] [colors]
// [theme] [desktop.mobile] [desktop.mobile.remote_effects], usr/share/mios/hyprland/patches/hyprbars-overlay.patch,
// tools/native/mios-gen/src/render.rs (hyprbars glass_preset projection), usr/share/mios/quickshell/PanelWindow.qml,
// usr/share/mios/quickshell/Sidebar.qml, usr/share/mios/quickshell/ControlDot.qml,
// usr/share/mios/quickshell/NotchShoulder.qml, usr/share/mios/quickshell/Mobile.qml
pragma Singleton
import QtQuick
import Quickshell
import Quickshell.Io

Singleton {
    id: glass
    property Theme theme: Theme {}
    // "<section>" -> table, exactly as mios-toml-get --section prints it.
    property var ssot: ({})
    property bool loaded: false
    readonly property var sections: ["desktop.hyprland.hyprbars", "desktop.hyprland.hyprglass",
        "desktop.hyprland.config.decoration", "colors", "theme", "desktop.mobile"]

    function table(name) { return glass.ssot[name] || ({}) }
    readonly property var bars: table("desktop.hyprland.hyprbars")
    readonly property var barsCfg: bars.config || ({})
    // [desktop.hyprland.hyprbars.mobile]: the plugin's mobile tabs (touch target, menu button, which button closes)
    readonly property var barsMobile: bars.mobile || ({})
    readonly property var hyprglass: table("desktop.hyprland.hyprglass")
    readonly property var palette: table("colors")
    readonly property string presetName: barsCfg.glass_preset || ((hyprglass.config || {}).default_preset || "")
    readonly property var preset: (hyprglass.presets || {})[presetName] || ({})
    readonly property var toned: preset[(hyprglass.config || {}).default_theme || "dark"] || ({})

    // "#RRGGBB" of a [colors] key; "" when unknown.
    function colour(name) {
        const c = name ? glass.palette[name] : undefined
        return (typeof c === "string" && /^#[0-9a-fA-F]{6}$/.test(c)) ? c : ""
    }
    // A 0..100 SSOT percentage: a number, or a "<section>.<key>" reference into the loaded tables.
    function percent(v) {
        if (typeof v === "number") return v
        if (typeof v !== "string") return NaN
        const parts = v.split(".")
        let cur = glass.ssot[parts[0]]
        for (let i = 1; i < parts.length && cur && typeof cur === "object"; i++) cur = cur[parts[i]]
        if (typeof cur === "number") return cur
        const dot = v.lastIndexOf(".")
        const t = dot > 0 ? glass.ssot[v.slice(0, dot)] : undefined
        const n = t ? t[v.slice(dot + 1)] : undefined
        return typeof n === "number" ? n : NaN
    }

    // ---- geometry -----------------------------------------------------------------------------------
    readonly property real rounding: {
        const r = table("desktop.hyprland.config.decoration").rounding
        return typeof r === "number" ? r : theme.radius
    }
    readonly property real barHeight: typeof barsCfg.bar_height === "number" ? barsCfg.bar_height : theme.topHeight
    // The hyprbars tab rules, for any surface of height h.
    function cornerFor(h) { return Math.min(glass.rounding, Math.max(0, h) / 2) }
    function filletFor(h) { return Math.min(glass.rounding / 2, h / 2) }
    // The same rules for a window of rounding r (the hyprbars tab follows its own window's rounding).
    function tabCorner(h, r) { return Math.min(Math.max(0, r), h) / 2 }
    function tabFillet(h, r) { return Math.min(Math.max(0, r) / 2, h / 2) }
    // Mobile tabs (hyprbars mobile mode, ControlsTab.qml): [desktop.hyprland.hyprbars.mobile] touch_target is the
    // minimum hit width and height of each tab (logical px); the drawn tab is barHeight tall and button + 2*pillPadding
    // wide, and its box starts one window rounding plus one fillet in from the window's side.
    readonly property real touchTarget: typeof barsMobile.touch_target === "number" && barsMobile.touch_target > 0
                                        ? barsMobile.touch_target : barHeight
    readonly property real mobileTabWidth: closeButton.size + 2 * pillPadding
    function mobileTabInset(h, r) { return Math.max(0, r) + tabFillet(h, r) }
    readonly property real pillPadding: typeof barsCfg.pill_padding === "number" ? barsCfg.pill_padding : theme.edgeMargin
    readonly property real buttonPadding: typeof barsCfg.bar_button_padding === "number" ? barsCfg.bar_button_padding : theme.edgeMargin
    readonly property bool iconOnHover: barsCfg.icon_on_hover !== false

    // ---- material -----------------------------------------------------------------------------------
    readonly property string fillHex: colour(barsCfg.pill_palette) || theme.bg
    readonly property real fillAlpha: {
        const p = percent(barsCfg.pill_alpha)
        return (p >= 0 && p <= 100) ? p / 100 : theme.panelOpacity
    }
    readonly property color fill: theme.withAlpha(fillHex, fillAlpha)
    readonly property string rimHex: colour(toned.palette_fresnel) || theme.subtle

    // ---- buttons ------------------------------------------------------------------------------------
    function button(b) {
        return ({ color: colour(b.palette) || theme.error, iconColor: colour(b.icon_palette) || theme.bg,
                  size: typeof b.size === "number" && b.size > 0 ? b.size : Math.round(barHeight / 2),
                  icon: typeof b.icon === "string" ? b.icon : "", action: String(b.action || ""),
                  closes: /window\.close\b|killactive/.test(String(b.action || "")) })
    }
    readonly property var buttons: (Array.isArray(bars.buttons) ? bars.buttons : []).map(b => glass.button(b || {}))
    // The right mobile tab's button: [desktop.hyprland.hyprbars.mobile] close_button (the index the plugin uses),
    // else the button whose action closes the window.
    readonly property var closeButton: (typeof barsMobile.close_button === "number" ? buttons[barsMobile.close_button] : undefined)
                                       || buttons.find(b => b.closes) || buttons[0] || button({})
    // The mobile left tab's button opens the window menu: hyprbars' button shape and size (the close button's);
    // colour, icon colour and icon from [desktop.hyprland.hyprbars.mobile] menu_palette / menu_icon_palette /
    // menu_icon -- the keys the plugin's mobile tabs are projected from. Unset colours fall back to the glass rim
    // colour (the preset's palette_fresnel) and the close button's icon colour.
    readonly property var menuButton: ({
        color: colour(barsMobile.menu_palette) || rimHex,
        iconColor: colour(barsMobile.menu_icon_palette) || closeButton.iconColor,
        size: closeButton.size,
        icon: typeof barsMobile.menu_icon === "string" ? barsMobile.menu_icon : "",
        action: String(barsMobile.menu_action || ""), closes: false })

    // ---- remote effects profile -----------------------------------------------------------------------
    // [desktop.mobile.remote_effects] (Mobile.remoteEffects): on outputs it applies to, the shell's layers take
    // the `-remote` namespace the projection maps to remote_effects.preset (hyprglass preset derived from the
    // same base preset: same tint/fresnel/bevel colours, fewer blur passes, no specular/fresnel/fringe). The
    // shell itself draws the same flat fill and circles either way; "lite" names that profile.
    function profileFor(outputName) { return Mobile.remoteEffects(outputName) ? "lite" : "full" }

    // ---- loading ------------------------------------------------------------------------------------
    property string resolver: Quickshell.env("MIOS_TOML_GET") || "/usr/bin/mios-toml-get"
    property Process reader: Process {
        command: ["sh", "-c", "r=$1; shift; for s in \"$@\"; do printf '%s\\t' \"$s\"; \"$r\" --section \"$s\" 2>/dev/null | tr -d '\\n'; printf '\\n'; done",
                  "sh", glass.resolver].concat(glass.sections)
        stdout: StdioCollector {
            onStreamFinished: glass.parse(text)
        }
    }
    function parse(t) {
        const next = {}
        for (const line of String(t).split("\n")) {
            const tab = line.indexOf("\t")
            if (tab <= 0) continue
            try { next[line.slice(0, tab)] = JSON.parse(line.slice(tab + 1)) || ({}) } catch (e) { /* section unreadable: fallback */ }
        }
        if (Object.keys(next).length) { glass.ssot = next; glass.loaded = true }
    }
    function reload() { if (!reader.running) reader.running = true }
    Component.onCompleted: reload()
    // The theme bridge is rewritten by mios-sync-theme and the mobile projection on every Hyprland config
    // load: both follow an SSOT change, so either reloads the glass.
    property Connections bridgeWatch: Connections {
        target: glass.theme.bridge
        function onFileChanged() { glass.reload() }
    }
    property string lastMobile: ""
    property Connections mobileWatch: Connections {
        target: Mobile
        function onConfigChanged() {
            const s = JSON.stringify(Mobile.config || {})
            if (s !== glass.lastMobile) { glass.lastMobile = s; glass.reload() }
        }
    }
}
