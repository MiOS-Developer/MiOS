// AI-hint: The dock's tunables, resolved from SSOT [desktop.dock] (and its osk / previews / targets sub-tables) with the
// layered vendor/host/user resolver (mios-toml-get --section), the way Glass.qml reads its tables. Every size, delay and
// behaviour of the expanded dock (grows UPWARD from its bar at the bar's own width), the nested on-screen keyboard, the
// window previews, the app labels and the window-portability targets comes from here; the literals below are only the
// degrade-open fallback until the resolver answers and mirror the vendor defaults (tests/test-dock-hud.py checks that).
// Reloads with the theme bridge (an SSOT change re-syncs it). MIOS_TOML_GET overrides the resolver (tests: a fixture).
// AI-related: usr/share/mios/mios.toml [desktop.dock], usr/share/mios/quickshell/Sidebar.qml, DockExpanded.qml,
// DockPreview.qml, DockPopup.qml, usr/share/mios/configurator/mios.html, tools/native/mios-gen/src/dock.rs
pragma Singleton
import QtQuick
import Quickshell
import Quickshell.Io

Singleton {
    id: cfg
    property Theme theme: Theme {}
    property var table: ({})
    property bool loaded: false
    function sub(name) { return (cfg.table[name] && typeof cfg.table[name] === "object") ? cfg.table[name] : ({}) }
    function num(t, k, d) { const v = t[k]; return typeof v === "number" && isFinite(v) ? v : d }
    function bool(t, k, d) { const v = t[k]; return typeof v === "boolean" ? v : d }
    function str(t, k, d) { const v = t[k]; return typeof v === "string" && v !== "" ? v : d }

    // ---- expansion: the panel grows upward from the bar and keeps the bar's width -----------------------------------
    readonly property real expandHeightFraction: num(table, "expand_height_fraction", 0.62)
    readonly property int expandMinHeight: num(table, "expand_min_height_px", 260)
    readonly property int expandMaxHeight: num(table, "expand_max_height_px", 760)
    readonly property int expandMs: num(table, "expand_ms", 220)
    readonly property int expandMinWidth: num(table, "expand_min_width_px", 0)
    // The expanded height on an output of logical height h with `reserved` px above (top bar + gaps): the SSOT fraction,
    // clamped to [min, max] and never past the reserved rows -- the dock never takes the whole display.
    function expandedHeight(h, reserved) {
        const want = Math.round(h * expandHeightFraction)
        return Math.max(0, Math.min(Math.max(expandMinHeight, Math.min(expandMaxHeight, want)), h - reserved))
    }

    // ---- app labels -------------------------------------------------------------------------------------------------
    readonly property int labelLines: Math.max(1, num(table, "label_lines", 2))
    readonly property int labelMinFont: Math.max(1, num(table, "label_min_font_px", 9))
    readonly property int tooltipDelay: num(table, "tooltip_delay_ms", 450)

    // ---- window previews ([desktop.dock.previews]) ------------------------------------------------------------------
    readonly property var previews: sub("previews")
    readonly property bool previewsEnabled: bool(previews, "enabled", true)
    readonly property string previewsInclude: str(previews, "include", "all")      // all | minimized
    readonly property int previewHeight: num(previews, "height_px", 96)
    readonly property bool previewLive: bool(previews, "live", true)                // false: the last frame only
    readonly property int previewMax: num(previews, "max", 16)

    // ---- nested on-screen keyboard ([desktop.dock.osk]) -------------------------------------------------------------
    readonly property var osk: sub("osk")
    readonly property bool oskEnabled: bool(osk, "enabled", true)
    readonly property string oskShow: str(osk, "show", "expanded+touch")           // expanded | touch | expanded+touch
    readonly property int oskHeight: num(osk, "height_px", 220)
    readonly property bool oskOnExpand: oskEnabled && oskShow.split("+").indexOf("expanded") >= 0
    readonly property bool oskOnTouch: oskEnabled && oskShow.split("+").indexOf("touch") >= 0

    // ---- window portability targets ([desktop.dock.targets]) --------------------------------------------------------
    readonly property var targets: sub("targets")
    readonly property bool targetsEnabled: bool(targets, "enabled", true)
    readonly property int targetsPollMs: num(targets, "poll_ms", 3000)

    // ---- loading ----------------------------------------------------------------------------------------------------
    property string resolver: Quickshell.env("MIOS_TOML_GET") || "/usr/bin/mios-toml-get"
    property Process reader: Process {
        command: [cfg.resolver, "--section", "desktop.dock"]
        stdout: StdioCollector {
            onStreamFinished: {
                try { const t = JSON.parse(text); if (t && typeof t === "object") { cfg.table = t; cfg.loaded = true } }
                catch (e) { /* unreadable: stay on the fallback */ }
            }
        }
    }
    function reload() { if (!reader.running) reader.running = true }
    Component.onCompleted: reload()
    property Connections bridgeWatch: Connections {
        target: cfg.theme.bridge
        function onFileChanged() { cfg.reload() }
    }
}
