// AI-hint: Super+Escape keybind map: a centered glass overlay listing every desktop binding with its
// SSOT label (Theme.keymap, built by mios-sync-theme from [desktop.hyprland] bindings, launcher and
// terminal keys and [keybindings] actions). Escape or Super+Escape closes it.
// AI-related: usr/share/mios/mios.toml [desktop.hyprland].bindings, usr/libexec/mios/mios-sync-theme
import QtQuick
import Quickshell as QS
import Quickshell.Io
import Quickshell.Wayland

QS.PanelWindow {
    id: keymap
    property QtObject theme: Theme {}
    property bool open: false

    WlrLayershell.namespace: "mios-keymap"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: open ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None
    exclusionMode: QS.ExclusionMode.Ignore
    visible: open
    implicitWidth: card.implicitWidth
    implicitHeight: card.implicitHeight
    color: "transparent"

    IpcHandler {
        target: "keymap"
        function toggle(): void { keymap.open = !keymap.open }
        function close(): void { keymap.open = false }
    }

    Rectangle {
        id: card
        readonly property int columns: 2
        implicitWidth: grid.implicitWidth + 4 * keymap.theme.edgeMargin
        implicitHeight: title.implicitHeight + grid.implicitHeight + 6 * keymap.theme.edgeMargin
        radius: Math.min(keymap.theme.radius, 28)
        color: keymap.theme.withAlpha(keymap.theme.bg, keymap.theme.notificationOpacity)
        border.width: 1
        border.color: keymap.theme.outlineColor
        focus: keymap.open
        Keys.onEscapePressed: keymap.open = false

        Text {
            id: title
            anchors.top: parent.top
            anchors.topMargin: 2 * keymap.theme.edgeMargin
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Keybinds"
            color: keymap.theme.fg
            font.family: keymap.theme.fontFamily
            font.pixelSize: keymap.theme.titleFontSize
            font.bold: true
        }
        Grid {
            id: grid
            anchors.top: title.bottom
            anchors.topMargin: 2 * keymap.theme.edgeMargin
            anchors.horizontalCenter: parent.horizontalCenter
            columns: card.columns * 2
            columnSpacing: 2 * keymap.theme.edgeMargin
            rowSpacing: keymap.theme.edgeMargin
            // Column-major halves so the list reads top-to-bottom in each column.
            readonly property var rows: {
                const k = keymap.theme.keymap || []
                const half = Math.ceil(k.length / card.columns)
                const out = []
                for (let r = 0; r < half; r++)
                    for (let c = 0; c < card.columns; c++) {
                        const e = k[c * half + r]
                        out.push(e ? e.keys : "", e ? e.label : "")
                    }
                return out
            }
            Repeater {
                model: grid.rows
                delegate: Text {
                    required property var modelData
                    required property int index
                    text: modelData
                    color: index % 2 === 0 ? keymap.theme.cursor : keymap.theme.fg
                    font.family: keymap.theme.fontFamily
                    font.pixelSize: keymap.theme.fontSize
                    font.bold: index % 2 === 0
                }
            }
        }
    }
}
