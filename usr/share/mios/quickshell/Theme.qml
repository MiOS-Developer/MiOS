// AI-hint: MiOS brand-theme bridge for Quickshell (Law 7 / SSOT). Values are
// NOT hand-typed here as the source of truth -- FileView loads
// /etc/mios/theme/theme.json, regenerated from usr/share/mios/mios.toml
// [colors]/[theme]/[theme.font] by usr/libexec/mios/mios-sync-theme. The
// literals below are ONLY the degrade-open fallback used before the first
// sync runs (or if the bridge file is unreadable) -- they mirror the current
// mios.toml vendor defaults, they are not a second palette. Run
// `mios-sync-theme` (or `systemctl start mios-sync-theme.service`) after
// editing mios.toml [colors] to refresh the bridge this file reads.
// AI-related: /etc/mios/theme/theme.json, usr/libexec/mios/mios-sync-theme
// usr/share/mios/quickshell/Config.qml, usr/share/mios/quickshell/PanelWindow.qml
//
// API note: uses Quickshell.Io's FileView (path / text() / textChanged()),
// per https://quickshell.org/docs/master/types/Quickshell.Io/FileView/ --
// if a future Quickshell release renames these, this is the one file that
// needs updating; Config.qml/PanelWindow.qml only ever bind to Theme's
// properties, never read the bridge file directly.

import QtQuick
import Quickshell as QS
import Quickshell.Io

QtObject {
    id: theme

    // Degrade-open fallback = mios.toml [colors] vendor defaults verbatim.
    property string bg:      "#282262"
    property string fg:      "#E7DFD3"
    property string accent:  "#1A407F"
    property string cursor:  "#F35C15"
    property string success: "#3E7765"
    property string warning: "#F35C15"
    property string error:   "#DC271B"
    property string subtle:  "#B7C9D7"
    property string muted:   "#948E8E"
    property string fontFamily: "GeistMono Nerd Font Mono"
    property int    radius:  28   // shared SSOT surface radius
    // The design language ([theme.shell] in mios.toml; the bridge's "design" table, mios-sync-theme). Read through
    // Glass.qml (bar, control, inset, gap, panelPad, corner(), cornerPower) -- surfaces never read these directly.
    property var design: ({ bar_px: 28, inset_px: 6, gap_px: 8, control_px: 16, radius_px: 28, panel_pad_px: 14,
                            tab_offset_px: 48, bar_centre_px: 22, hairline_px: 1, corner_power: 2.0 })
    property real   panelOpacity: 22 / 100
    property real outlineOpacity: 14 / 100
    readonly property color outlineColor: withAlpha(subtle, outlineOpacity)

    property int topHeight: 28
    property int topWidth: 640
    property int edgeMargin: 8
    property int dockHeight: 56
    property real drawerWidthFraction: 0.72
    property real drawerHeightFraction: 0.6
    property int appTile: 88
    property int appIcon: 32
    property var dockPinned: []
    property bool dockAutohide: "False" === "True"
    property int dockRevealPx: 2
    property int dockHideDelay: 600
    property bool dockFullscreenReveal: "True" === "True"
    property bool drawerFullPane: "True" === "True"
    property int drawerPaneMargin: 48
    property real drawerOpacity: 55 / 100
    property int notificationWidth: 310
    property real notificationOpacity: 92 / 100
    property real hoverOpacity: 50 / 100
    property real selectionOpacity: 65 / 100
    property real scrimOpacity: 60 / 100
    property int titleFontStep: 4
    readonly property int titleFontSize: fontSize + titleFontStep
    readonly property int smallFontSize: Math.max(1, fontSize - 1)

    property int fontSize: 12
    property int searchWidth: 640
    property int searchRowHeight: 40
    property real searchTopFraction: 0.22
    property int searchVisibleRows: 7
    property int searchLimit: 40
    property int searchDebounce: 120
    property int searchMinChars: 2
    property var searchActions: []
    // [desktop.topbar] from the bridge; PanelWindow falls back per key until the first sync.
    property var topbar: ({})
    // [desktop.hyprland.hyprbars] window control tab geometry (AppMenu.qml keeps the pills clear of it).
    property var hyprbars: ({})
    // Super+Escape keybind map: [{keys, label}] built from SSOT bindings by mios-sync-theme.
    property var keymap: []

    property int workspaceCount: 10
    property int agentPollMs: 5000
    property string launcher: ""
    property string terminalCommand: ""

    property FileView bridge: FileView {
        path: QS.Quickshell.env("MIOS_THEME_BRIDGE") || "/etc/mios/theme/theme.json"
        watchChanges: true
        preload: true
        onFileChanged: reload()
        onLoaded: theme.updateBridge(text())

        onTextChanged: theme.updateBridge(text())
    }

    function updateBridge(contents) {
            try {
                const t = JSON.parse(contents)
                if (t.search) {
                    theme.searchWidth = t.search.width_px
                    if (t.search.top_fraction !== undefined) theme.searchTopFraction = t.search.top_fraction
                    theme.searchRowHeight = t.search.row_height_px
                    theme.searchVisibleRows = t.search.visible_rows
                    theme.searchLimit = t.search.result_limit
                    theme.searchDebounce = t.search.debounce_ms
                    theme.searchMinChars = t.search.file_min_chars
                }
                if (t.search_actions) theme.searchActions = t.search_actions
                if (t.topbar) theme.topbar = t.topbar
                if (t.hyprbars) theme.hyprbars = t.hyprbars
                if (t.keymap) theme.keymap = t.keymap
                if (t.design) theme.design = t.design
                if (t.font_size !== undefined) theme.fontSize = t.font_size
                if (t.shell) {
                    theme.topHeight = t.shell.top_height_px
                    theme.topWidth = t.shell.top_width_px
                    theme.edgeMargin = t.shell.edge_margin_px
                    theme.dockHeight = t.shell.dock_height_px
                    theme.drawerWidthFraction = t.shell.drawer_width_fraction
                    theme.drawerHeightFraction = t.shell.drawer_height_fraction
                    theme.appTile = t.shell.app_tile_px
                    theme.appIcon = t.shell.app_icon_px
                    theme.dockPinned = t.shell.dock_pinned
                    theme.notificationWidth = t.shell.notification_width_px
                    theme.notificationOpacity = t.shell.notification_opacity_pct / 100
                    theme.hoverOpacity = t.shell.hover_opacity_pct / 100
                    theme.selectionOpacity = t.shell.selection_opacity_pct / 100
                    theme.scrimOpacity = t.shell.scrim_opacity_pct / 100
                    theme.titleFontStep = t.shell.title_font_step_px
                    if (t.shell.dock_autohide !== undefined) theme.dockAutohide = t.shell.dock_autohide
                    if (t.shell.dock_reveal_px !== undefined) theme.dockRevealPx = t.shell.dock_reveal_px
                    if (t.shell.dock_hide_delay_ms !== undefined) theme.dockHideDelay = t.shell.dock_hide_delay_ms
                    if (t.shell.dock_fullscreen_reveal !== undefined) theme.dockFullscreenReveal = t.shell.dock_fullscreen_reveal
                    if (t.shell.drawer_full_pane !== undefined) theme.drawerFullPane = t.shell.drawer_full_pane
                    if (t.shell.drawer_pane_margin_px !== undefined) theme.drawerPaneMargin = t.shell.drawer_pane_margin_px
                    if (t.shell.drawer_fill_opacity_pct !== undefined)
                        theme.drawerOpacity = t.acrylic === false ? 1 : t.shell.drawer_fill_opacity_pct / 100
                }
                if (t.workspace_count !== undefined) theme.workspaceCount = t.workspace_count
                if (t.agent_poll_ms !== undefined) theme.agentPollMs = t.agent_poll_ms
                if (t.launcher) theme.launcher = t.launcher
                if (t.terminal_command) theme.terminalCommand = t.terminal_command
                if (t.bg)          theme.bg = t.bg
                if (t.fg)          theme.fg = t.fg
                if (t.accent)      theme.accent = t.accent
                if (t.cursor)      theme.cursor = t.cursor
                if (t.success)     theme.success = t.success
                if (t.warning)     theme.warning = t.warning
                if (t.error)       theme.error = t.error
                if (t.subtle)      theme.subtle = t.subtle
                if (t.muted)       theme.muted = t.muted
                if (t.font_family) theme.fontFamily = t.font_family
                if (t.radius_px !== undefined)   theme.radius = t.radius_px
                if (t.shell && t.shell.fill_opacity_pct !== undefined)
                    theme.panelOpacity = t.acrylic === false ? 1 : t.shell.fill_opacity_pct / 100
                else if (t.opacity_pct !== undefined) theme.panelOpacity = t.acrylic === false ? 1 : t.opacity_pct / 100
                if (t.shell && t.shell.outline_opacity_pct !== undefined)
                    theme.outlineOpacity = t.shell.outline_opacity_pct / 100
            } catch (e) {
                console.warn("Theme.qml: /etc/mios/theme/theme.json present but unparsable -- staying on vendor fallback palette:", e)
            }
    }

    // Helper: hex "#RRGGBB" + alpha 0..1 -> "#AARRGGBB" for Qt color props.
    function withAlpha(hex, alpha) {
        const a = Math.round(alpha * 255).toString(16).padStart(2, "0")
        return "#" + a + hex.replace("#", "")
    }
}
