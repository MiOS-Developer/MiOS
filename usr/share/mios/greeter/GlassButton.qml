// AI-hint: Keyboard-operable glass pill button for the MiOS greeter (Enter/Space activate, focus ring).
// AI-related: usr/share/mios/greeter/shell.qml, usr/share/mios/quickshell/Theme.qml
import QtQuick

Rectangle {
    id: button
    required property var theme
    property string text: ""
    property string keyHint: ""
    property bool primary: false
    property bool danger: false
    signal activated()

    activeFocusOnTab: true
    implicitWidth: row.implicitWidth + 36
    implicitHeight: Math.round(theme.fontSize * 3.0)
    radius: height / 2
    readonly property string tint: danger ? theme.error : (primary ? theme.accent : theme.bg)
    color: theme.withAlpha(tint, activeFocus || mouse.containsMouse ? 0.92 : (primary ? 0.78 : 0.45))
    border.width: activeFocus ? 2 : 1
    border.color: activeFocus ? theme.cursor : theme.outlineColor
    Behavior on color { ColorAnimation { duration: 120 } }

    Keys.onReturnPressed: activated()
    Keys.onEnterPressed: activated()
    Keys.onSpacePressed: activated()

    Row {
        id: row
        anchors.centerIn: parent
        spacing: 8
        Text {
            text: button.text
            color: button.theme.fg
            font.family: button.theme.fontFamily
            font.pixelSize: button.theme.fontSize + 2
            font.bold: true
        }
        Text {
            visible: button.keyHint.length > 0
            text: button.keyHint
            color: button.theme.withAlpha(button.theme.fg, 0.6)
            font.family: button.theme.fontFamily
            font.pixelSize: button.theme.fontSize
            anchors.baseline: parent.children[0].baseline
        }
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        onClicked: { button.forceActiveFocus(); button.activated() }
    }
}
