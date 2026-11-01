// AI-hint: One flat window preview for the dock (expanded panel strip and hover popup): the window's rendered output edge
// to edge -- no margin, border, frame or card -- with its title on a thin scrim and the app icon as the fallback.
// Capture safety (Hyprland 0.56.2 SEGVs in Screenshare::CScreenshareSession::init when a toplevel without an output is
// captured; capturing a window that just closed disconnects the shell): the view asks for a capture only for a window
// Hyprland reports mapped and on a live monitor (monitor >= 0), only while the preview is shown, and drops the source the
// moment the window leaves that state. The compositor side is guarded too (mios-pacing capture_guard.hpp), so neither
// side alone keeps the desktop up. live = false ([desktop.dock.previews].live) keeps the last frame instead of streaming.
// AI-related: usr/share/mios/quickshell/DockConfig.qml, DockExpanded.qml, DockPopup.qml, DockLamp.qml,
// usr/share/mios/hyprland/plugins/mios-pacing/capture_guard.hpp, tests/test-dock-hud.py
import QtQuick
import Quickshell as QS
import Quickshell.Wayland

Item {
    id: preview
    required property var win            // {address, title, appId, minimized, activated, toplevel}
    required property QtObject theme
    property string icon: ""
    property bool active: true           // the surface showing this preview is open
    property bool focused: false
    property bool showTitle: true
    clip: true

    // Hyprland's view of the window (hyprctl clients via HyprlandToplevel.lastIpcObject).
    readonly property var info: win && win.address ? DockLamp.windowInfo(win.address) : null
    // A capture is requested only for a window that can be captured: a live foreign-toplevel handle, mapped, on an
    // output. Unknown to Hyprland (no address match) = not captured: the icon stands in.
    readonly property bool capturable: DockConfig.previewsEnabled && active && !!win && !!win.toplevel
                                       && !!info && info.mapped !== false && typeof info.monitor === "number" && info.monitor >= 0
    readonly property bool hasFrame: view.hasContent && view.captureSource !== null

    ScreencopyView {
        id: view
        anchors.fill: parent
        captureSource: preview.capturable ? preview.win.toplevel : null
        live: preview.capturable && DockConfig.previewLive
        paintCursor: false
        constraintSize: Qt.size(preview.width, preview.height)
        visible: preview.hasFrame
    }
    // Fallback: the app icon on the dock's own glass (no tile of its own).
    Image {
        anchors.centerIn: parent
        visible: !preview.hasFrame
        width: Math.min(preview.theme.appIcon, preview.height - 4)
        height: width
        source: preview.icon ? QS.Quickshell.iconPath(preview.icon, true) : ""
        fillMode: Image.PreserveAspectFit
        opacity: preview.win && preview.win.minimized ? 0.7 : 1
    }
    // Title on a thin scrim along the bottom edge; the focus/active state is the scrim's accent, not a border.
    GlassSurface {
        visible: preview.showTitle
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: titleText.implicitHeight + Glass.gap / 2
        radius: Glass.cornerFor(height)
        color: preview.focused ? preview.theme.withAlpha(preview.theme.accent, preview.theme.selectionOpacity)
                               : preview.theme.withAlpha(preview.theme.bg, preview.theme.scrimOpacity)
        Text {
            id: titleText
            anchors.fill: parent
            anchors.leftMargin: Glass.gap / 2
            anchors.rightMargin: Glass.gap / 2
            verticalAlignment: Text.AlignVCenter
            text: preview.win ? String(preview.win.title || preview.win.appId || "") : ""
            textFormat: Text.PlainText
            elide: Text.ElideRight
            color: preview.win && preview.win.minimized ? preview.theme.muted : preview.theme.fg
            font.family: preview.theme.fontFamily
            font.pixelSize: preview.theme.smallFontSize
        }
    }
}
