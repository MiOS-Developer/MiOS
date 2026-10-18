// AI-hint: Shared notification settings and view registry for the island notifications (Notifications.qml). Reads
// [desktop.notifications] with the layered SSOT resolver (mios-toml-get --section: vendor/host/user) at start and
// whenever the theme bridge changes; until then (or if the SSOT cannot be read) the defaults below -- the vendor values
// of the same keys -- stand in. Every Notifications surface registers here so the shell's "notifications" IPC target
// (Config.qml) can report every output's blob. Geometry the keys leave at 0 falls back to the theme bridge
// ([theme.shell] notification_width_px, [desktop.topbar] popup_ms); nothing here hand-types a colour or radius.
// MIOS_TOML_GET overrides the resolver binary (tests point it at a fixture).
// AI-related: usr/share/mios/quickshell/Notifications.qml, usr/share/mios/quickshell/Config.qml,
// usr/share/mios/quickshell/Theme.qml, usr/share/mios/mios.toml [desktop.notifications]
pragma Singleton
import QtQuick
import Quickshell
import Quickshell.Hyprland
import Quickshell.Io

Singleton {
    id: model
    property Theme theme: Theme {}
    // [desktop.notifications] exactly as mios-toml-get --section prints it ({} until read).
    property var cfg: ({})
    property bool loaded: false

    function num(key, dflt) { const v = cfg[key]; return (typeof v === "number" && isFinite(v)) ? v : dflt }
    // "island": the only placement -- every notification grows out of the status island (top centre).
    readonly property string position: typeof cfg.position === "string" ? cfg.position : "island"
    // Expiry for notifications that leave it to the server (expire_timeout -1); critical / resident ones stay.
    readonly property int timeoutMs: Math.max(1000, num("timeout_ms", 5000))
    readonly property int maxVisible: Math.max(1, Math.round(num("max_visible", 4)))
    // Card width in shell units: width_px, or [theme.shell] notification_width_px when 0.
    readonly property real width: num("width_px", 0) > 0 ? num("width_px", 0) : theme.notificationWidth
    // Island -> card morph: morph_ms, or [desktop.topbar] popup_ms (the island menus' morph) when 0.
    readonly property int morphMs: {
        const own = num("morph_ms", 0)
        if (own > 0) return own
        const bar = theme.topbar || ({})
        return bar.popup_ms !== undefined ? bar.popup_ms : 200
    }
    readonly property int imageMaxHeight: Math.max(0, Math.round(num("image_max_height_px", 120)))
    readonly property int bodyMaxLines: Math.max(1, Math.round(num("body_max_lines", 6)))
    readonly property int actionsMax: Math.max(0, Math.round(num("actions_max", 6)))
    // Do not disturb: only critical notifications show (runtime toggle over IPC; the SSOT key is the default).
    property bool dndOverride: false
    property bool dndRuntime: false
    readonly property bool dnd: dndOverride ? dndRuntime : cfg.do_not_disturb === true

    // ---- compositor ----------------------------------------------------------------------------------------------------
    // The notification layer's box follows the blob (Notifications.qml); Hyprland animates every layer box change on its
    // layer animation, which would stretch the blob, so [desktop.notifications] no_anim (default on) asks it for a
    // no_anim layer rule on the "mios-notifications" namespace -- at start and after every config reload (a reload
    // rebuilds the rules). Scoped to that namespace; the guard keeps it to one rule per config generation.
    readonly property bool noAnim: cfg.no_anim !== false
    readonly property string layerNamespace: "mios-notifications"
    property Process ruleWriter: Process {
        command: ["hyprctl", "eval", "if not rawget(_G, 'mios_notifications_no_anim') then _G.mios_notifications_no_anim = " +
                  "hl.layer_rule({name = 'mios-notifications-no-anim', match = {namespace = '^(" + model.layerNamespace + ")$'}, no_anim = true}) end"]
        stdout: StdioCollector { onStreamFinished: model.ruleApplied = String(text).trim() === "ok" }
    }
    property bool ruleApplied: false
    function applyRule() { if (noAnim && Quickshell.env("HYPRLAND_INSTANCE_SIGNATURE") && !ruleWriter.running) ruleWriter.running = true }
    onLoadedChanged: applyRule()
    property Connections hyprEvents: Connections {
        target: Hyprland
        function onRawEvent(event) { if (event.name === "configreloaded") model.applyRule() }
    }

    // ---- views -------------------------------------------------------------------------------------------------------
    // The Notifications surfaces (one per output) report themselves here for the IPC state.
    property var views: []
    function register(v) { if (views.indexOf(v) < 0) views = views.concat([v]) }
    function unregister(v) { views = views.filter(x => x !== v) }

    // ---- loading -----------------------------------------------------------------------------------------------------
    property string resolver: Quickshell.env("MIOS_TOML_GET") || "/usr/bin/mios-toml-get"
    property Process reader: Process {
        command: ["sh", "-c", "\"$1\" --section desktop.notifications 2>/dev/null | tr -d '\\n'", "sh", model.resolver]
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    const t = JSON.parse(String(text).trim() || "{}")
                    if (t && typeof t === "object" && !Array.isArray(t)) { model.cfg = t; model.loaded = true }
                } catch (e) { /* unreadable: keep the last good table */ }
            }
        }
    }
    function reload() { if (!reader.running) reader.running = true }
    Component.onCompleted: { reload(); applyRule() }
    // The theme bridge is rewritten after every SSOT change (mios-sync-theme): reread the table then too.
    property Connections bridgeWatch: Connections {
        target: model.theme.bridge
        function onFileChanged() { model.reload() }
    }
}
