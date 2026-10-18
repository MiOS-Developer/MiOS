// AI-hint: Glass text field for the MiOS greeter (label + rounded translucent input, accent ring on focus).
// AI-related: usr/share/mios/greeter/shell.qml, usr/share/mios/quickshell/Theme.qml
import QtQuick

FocusScope {
    id: field
    required property var theme
    property string label: ""
    property string placeholder: ""
    property string hint: ""
    property bool secret: false
    property alias text: input.text
    property alias input: input
    signal accepted()

    implicitHeight: caption.implicitHeight + box.height + 6
    implicitWidth: 360

    Text {
        id: caption
        text: field.label
        color: field.theme.subtle
        font.family: field.theme.fontFamily
        font.pixelSize: field.theme.fontSize + 1
        font.bold: true
    }
    Text {
        anchors.right: parent.right
        anchors.baseline: caption.baseline
        text: field.hint
        visible: field.activeFocus && field.hint.length > 0
        color: field.theme.muted
        font.family: field.theme.fontFamily
        font.pixelSize: field.theme.fontSize
    }

    Rectangle {
        id: box
        anchors.top: caption.bottom
        anchors.topMargin: 6
        width: parent.width
        height: Math.round(field.theme.fontSize * 3.4)
        radius: Math.min(field.theme.radius, height / 2)
        color: field.theme.withAlpha(field.theme.bg, field.activeFocus ? 0.72 : 0.5)
        border.width: field.activeFocus ? 2 : 1
        border.color: field.activeFocus ? field.theme.cursor : field.theme.outlineColor
        Behavior on border.color { ColorAnimation { duration: 120 } }

        TextInput {
            id: input
            anchors.fill: parent
            anchors.leftMargin: box.radius * 0.6 + 8
            anchors.rightMargin: box.radius * 0.6 + 8
            verticalAlignment: TextInput.AlignVCenter
            focus: true
            clip: true
            color: field.theme.fg
            selectionColor: field.theme.accent
            selectedTextColor: field.theme.fg
            font.family: field.theme.fontFamily
            font.pixelSize: field.theme.fontSize + 4
            echoMode: field.secret ? TextInput.Password : TextInput.Normal
            passwordCharacter: "•"
            inputMethodHints: field.secret ? (Qt.ImhHiddenText | Qt.ImhSensitiveData | Qt.ImhNoPredictiveText) : Qt.ImhNoPredictiveText
            onAccepted: field.accepted()
        }
        Text {
            anchors.fill: input
            verticalAlignment: Text.AlignVCenter
            text: field.placeholder
            visible: input.text.length === 0
            color: field.theme.muted
            font: input.font
        }
    }
}
