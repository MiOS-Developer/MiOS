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
    // miosd passes the caller's title and message in the environment (Quickshell.env; Process has no env table)
    property string promptTitle: Quickshell.env("MIOS_SECRET_PROMPT_TITLE") || "MiOS Secure Authentication"
    property string promptMessage: Quickshell.env("MIOS_SECRET_PROMPT_MSG") || "Enter password or secret token:"

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
        GlassSurface {
            id: modalBox
            width: 440
            height: body.implicitHeight + 2 * Glass.panelPad
            anchors.centerIn: parent
            // the island's glass (GlassSurface): corner rule and fill at the notification legibility alpha, no outline
            color: theme.withAlpha(Glass.fillHex, theme.notificationOpacity)

            ColumnLayout {
                id: body
                anchors.fill: parent
                anchors.margins: Glass.panelPad
                spacing: Glass.gap

                // Header / Title
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Glass.gap

                    GlassSurface {
                        width: Glass.inset
                        height: Glass.control
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
                    font.pixelSize: theme.fontSize
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                // Password input box
                GlassSurface {
                    Layout.fillWidth: true
                    height: Glass.bar
                    color: theme.withAlpha(theme.bg, theme.notificationOpacity)
                    borderColor: passwordInput.activeFocus ? theme.cursor : theme.muted
                    hairline: true

                    TextInput {
                        id: passwordInput
                        anchors.fill: parent
                        anchors.leftMargin: Glass.bar / 2
                        anchors.rightMargin: Glass.bar / 2
                        verticalAlignment: TextInput.AlignVCenter
                        echoMode: TextInput.Password
                        color: theme.fg
                        font.family: theme.fontFamily
                        font.pixelSize: theme.fontSize
                        focus: true

                        onAccepted: submitSecret()
                        Keys.onEscapePressed: cancelPrompt()
                    }
                }

                // Action buttons
                RowLayout {
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignRight
                    spacing: Glass.gap

                    // Cancel button
                    GlassSurface {
                        width: 90
                        height: Glass.bar
                        color: cancelHover.hovered ? theme.outlineColor : "transparent"
                        borderColor: theme.muted
                        hairline: true

                        Text {
                            anchors.centerIn: parent
                            text: "Cancel"
                            color: theme.muted
                            font.family: theme.fontFamily
                            font.pixelSize: theme.fontSize
                        }

                        HoverHandler { id: cancelHover }
                        TapHandler { onTapped: cancelPrompt() }
                    }

                    // Unlock button
                    GlassSurface {
                        width: 100
                        height: Glass.bar
                        color: unlockHover.hovered ? theme.cursor : theme.accent

                        Text {
                            anchors.centerIn: parent
                            text: "Unlock"
                            color: theme.fg
                            font.family: theme.fontFamily
                            font.pixelSize: theme.fontSize
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
