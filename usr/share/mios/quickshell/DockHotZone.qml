// AI-hint: Thin bottom-edge strip that reveals the auto-hidden dock on pointer contact. It also holds
// one edge margin of exclusive zone so tiled windows stop the same distance from the bottom as from the top bar.
// AI-related: usr/share/mios/quickshell/Sidebar.qml, usr/share/mios/mios.toml [theme.shell].dock_reveal_px
import QtQuick
import Quickshell as QS
import Quickshell.Wayland

QS.PanelWindow {
    id: zone
    required property QtObject theme
    property bool active: true
    property bool hovered: false
    // Over a fullscreen window: no exclusive zone (the fullscreen window keeps its size). The strip always
    // sits on the overlay layer, above fullscreen windows: a layer switch at the moment it maps is not
    // reliably applied, and a 2 px transparent input strip loses nothing by being on top.
    property bool overlay: false
    WlrLayershell.namespace: "mios-dock-hotzone"
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    WlrLayershell.layer: WlrLayer.Overlay
    anchors.bottom: true
    anchors.left: true
    anchors.right: true
    implicitHeight: Math.max(1, theme.dockRevealPx)
    exclusiveZone: visible && !overlay ? theme.edgeMargin : 0
    visible: active
    color: "transparent"
    onVisibleChanged: if (!visible) hovered = false
    // Fully transparent but still a real input region: the compositor delivers pointer enter here.
    Item {
        anchors.fill: parent
        HoverHandler { onHoveredChanged: zone.hovered = hovered }
    }
}
