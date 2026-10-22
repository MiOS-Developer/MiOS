// AI-hint: MiOS Native Wayland Secret & Keyring Prompt Dialog for Quickshell.
// Secure modal dialog for operator passwords, private keys, and keyring unlocks.
// Theme and colors are SSOT-driven from Theme.qml (/etc/mios/theme/theme.json).
// AI-related: usr/share/mios/quickshell/Theme.qml, src/mios-rs/miosd/src/lib.rs

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Wayland
import Quickshell.Io

PanelWindow {
    id: secretPromptWindow
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"

    property QtObject theme: Theme {}
    property string promptTitle: Process.env["MIOS_SECRET_PROMPT_TITLE"] || "MiOS Secure Authentication"
    property string promptMessage: Process.env["MIOS_SECRET_PROMPT_MSG"] || "Enter password or secret token:"

    // Backdrop overlay
    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(0, 0, 0, theme.scrimOpacity)

        MouseArea {
            anchors.fill: parent
            onClicked: {
                // Clicking outside does not dismiss to prevent accidental closure
            }
        }

        // Centered modal card
        Rectangle {
            id: modalBox
            width: 440
            height: 240
            anchors.centerIn: parent
            radius: theme.radius
            color: theme.bg
            border.color: theme.outlineColor
            border.width: 1

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 24
                spacing: 16

                // Header / Title
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10

                    Rectangle {
                        width: 8
                        height: 22
                        radius: theme.radius
                        color: theme.cursor // Orange accent stripe
                    }

                    Text {
                        text: promptTitle
                        color: theme.fg
                        font.family: theme.fontFamily
                        font.pixelSize: theme.titleFontSize
                        font.bold: true
                        Layout.fillWidth: true
                    }
                }

                // Subtitle / Description
                Text {
                    text: promptMessage
                    color: theme.subtle
                    font.family: theme.fontFamily
                    font.pixelSize: theme.fontSize + 1
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                // Password input box
                Rectangle {
                    Layout.fillWidth: true
                    height: 40
                    radius: theme.radius
                    color: Qt.darker(theme.bg, 1.6)
                    border.color: passwordInput.activeFocus ? theme.cursor : theme.muted
                    border.width: 1

                    TextInput {
                        id: passwordInput
                        anchors.fill: parent
                        anchors.leftMargin: 12
                        anchors.rightMargin: 12
                        verticalAlignment: TextInput.AlignVCenter
                        echoMode: TextInput.Password
                        color: theme.fg
                        font.family: theme.fontFamily
                        font.pixelSize: theme.fontSize + 2
                        focus: true

                        onAccepted: submitSecret()
                        Keys.onEscapePressed: cancelPrompt()
                    }
                }

                // Action buttons
                RowLayout {
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignRight
                    spacing: 12

                    // Cancel button
                    Rectangle {
                        width: 90
                        height: 36
                        radius: theme.radius
                        color: cancelHover.hovered ? theme.outlineColor : "transparent"
                        border.color: theme.muted
                        border.width: 1

                        Text {
                            anchors.centerIn: parent
                            text: "Cancel"
                            color: theme.muted
                            font.family: theme.fontFamily
                            font.pixelSize: theme.fontSize + 1
                        }

                        HoverHandler { id: cancelHover }
                        TapHandler { onTapped: cancelPrompt() }
                    }

                    // Unlock button
                    Rectangle {
                        width: 100
                        height: 36
                        radius: theme.radius
                        color: unlockHover.hovered ? theme.cursor : theme.accent

                        Text {
                            anchors.centerIn: parent
                            text: "Unlock"
                            color: theme.fg
                            font.family: theme.fontFamily
                            font.pixelSize: theme.fontSize + 1
                            font.bold: true
                        }

                        HoverHandler { id: unlockHover }
                        TapHandler { onTapped: submitSecret() }
                    }
                }
            }
        }
    }

    function submitSecret() {
        if (passwordInput.text.length > 0) {
            console.log(passwordInput.text);
            Qt.quit();
        }
    }

    function cancelPrompt() {
        Qt.exit(1);
    }
}
