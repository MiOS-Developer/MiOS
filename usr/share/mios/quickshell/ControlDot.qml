// AI-hint: One window-control button drawn exactly as the hyprbars overlay draws its buttons (upstream
// renderBarButtons / renderBarButtonsText): a filled circle of the button's size in its palette colour, and its
// icon in icon_palette at round(size x 0.62) px "sans", shown on every button of the row while any of them is
// hovered when icon_on_hover (else always). hyprbars has no pressed look and no hover colour change, so neither
// does this. `button` is a Glass.qml button ({color, iconColor, size, icon}); the hit area is hitWidth x hitHeight
// (default `hitSize` square; a mobile tab passes its whole box, the touch target) so the small circle stays easy to touch.
// AI-related: usr/share/mios/quickshell/Glass.qml, usr/share/mios/quickshell/PanelWindow.qml,
// usr/share/mios/hyprland/patches/hyprbars-overlay.patch, usr/share/mios/mios.toml [[desktop.hyprland.hyprbars.buttons]]
import QtQuick

Item {
    id: dot
    property var button: ({})
    property bool showIcon: false
    property real hitSize: width
    property real hitWidth: hitSize
    property real hitHeight: hitSize
    readonly property bool hovered: hit.containsMouse
    readonly property bool pressed: hit.pressed
    signal tapped()
    width: button.size || 0
    height: width

    GlassSurface {
        objectName: "dotFill"
        anchors.fill: parent        // a control-sized button: Glass.corner(size) = a circle, as the hyprbars buttons
        color: dot.button.color || "transparent"
    }
    Text {
        anchors.centerIn: parent
        visible: dot.showIcon && !!dot.button.icon
        text: dot.button.icon || ""
        textFormat: Text.PlainText
        color: dot.button.iconColor || "transparent"
        font.family: "sans"                                   // hyprbars renders button icons in "sans"
        font.pixelSize: Math.max(1, Math.round((dot.button.size || 0) * 0.62))   // hyprbars: size x 0.62
    }
    MouseArea {
        id: hit
        anchors.centerIn: parent
        width: dot.hitWidth
        height: dot.hitHeight
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: dot.tapped()
    }
}
