// AI-hint: MiOS Notification Center & History Tray. Displays persistent notification
// history recorded across system toasts, D-Bus notifications, and bridged Steam alerts.
// AI-related: usr/share/mios/quickshell/NotifModel.qml, usr/share/mios/quickshell/Notifications.qml,
// usr/share/mios/quickshell/GlassSurface.qml, usr/share/mios/quickshell/Theme.qml
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls
import Quickshell as QS
import "." as MiOS

Item {
    id: root
    property QtObject theme: Theme {}
    readonly property real gap: Glass.gap
    readonly property real inset: Glass.inset
    readonly property var items: MiOS.NotifModel.historyList || []
    readonly property int count: root.items.length

    implicitWidth: Math.max(320, MiOS.NotifModel.width)
    implicitHeight: layout.implicitHeight + 2 * Glass.panelPad

    ColumnLayout {
        id: layout
        anchors.fill: parent
        anchors.margins: Glass.panelPad
        spacing: Glass.gap

        // Header: Title, count badge, and Clear All button
        RowLayout {
            Layout.fillWidth: true
            spacing: Glass.gap

            Text {
                text: "Notifications"
                color: root.theme.fg
                font.family: root.theme.fontFamily
                font.pixelSize: root.theme.titleFontSize
                font.bold: true
                Layout.alignment: Qt.AlignVCenter
            }

            GlassSurface {
                visible: root.count > 0
                radius: Glass.corner(height)
                color: root.theme.withAlpha(root.theme.accent, root.theme.selectionOpacity)
                implicitWidth: badgeText.implicitWidth + Glass.inset * 2
                implicitHeight: badgeText.implicitHeight + Glass.gap / 2
                Layout.alignment: Qt.AlignVCenter

                Text {
                    id: badgeText
                    anchors.centerIn: parent
                    text: String(root.count)
                    color: root.theme.fg
                    font.family: root.theme.fontFamily
                    font.pixelSize: root.theme.smallFontSize
                    font.bold: true
                }
            }

            Item { Layout.fillWidth: true }

            GlassSurface {
                id: clearBtn
                visible: root.count > 0
                implicitWidth: clearLabel.implicitWidth + Glass.inset * 2
                implicitHeight: Glass.bar
                color: clearMa.containsMouse ? root.theme.withAlpha(root.theme.accent, root.theme.hoverOpacity)
                                             : root.theme.withAlpha(root.theme.subtle, root.theme.outlineOpacity)
                radius: Glass.corner(height)

                Text {
                    id: clearLabel
                    anchors.centerIn: parent
                    text: "Clear All"
                    color: root.theme.fg
                    font.family: root.theme.fontFamily
                    font.pixelSize: root.theme.smallFontSize
                }

                MouseArea {
                    id: clearMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: MiOS.NotifModel.clearHistory()
                }
            }
        }

        // Empty state message
        Item {
            visible: root.count === 0
            Layout.fillWidth: true
            implicitHeight: 2 * Glass.bar

            Text {
                anchors.centerIn: parent
                text: "No recent notifications"
                color: root.theme.subtle
                font.family: root.theme.fontFamily
                font.pixelSize: root.theme.fontSize
            }
        }

        // Notification List
        ListView {
            id: listView
            visible: root.count > 0
            Layout.fillWidth: true
            implicitHeight: Math.min(MiOS.NotifModel.historyMaxHeight, contentHeight)
            clip: true
            spacing: Glass.gap / 2
            model: root.items

            delegate: GlassSurface {
                id: cardDelegate
                required property var modelData
                required property int index
                width: listView.width
                implicitHeight: cardCol.implicitHeight + 2 * Glass.inset
                radius: Glass.radius
                specular: true
                color: Glass.fill

                ColumnLayout {
                    id: cardCol
                    anchors.fill: parent
                    anchors.margins: Glass.inset
                    spacing: Glass.gap / 2

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Glass.inset

                        Text {
                            text: cardDelegate.modelData.appName || "System"
                            color: root.theme.cursor
                            font.family: root.theme.fontFamily
                            font.pixelSize: root.theme.smallFontSize
                            font.bold: true
                        }

                        Item { Layout.fillWidth: true }

                        Text {
                            text: cardDelegate.modelData.time || ""
                            color: root.theme.muted
                            font.family: root.theme.fontFamily
                            font.pixelSize: root.theme.smallFontSize
                        }

                        // Close button for single notification
                        Text {
                            text: "✕"
                            color: dismissMa.containsMouse ? root.theme.error : root.theme.subtle
                            font.family: root.theme.fontFamily
                            font.pixelSize: root.theme.smallFontSize

                            MouseArea {
                                id: dismissMa
                                anchors.fill: parent
                                anchors.margins: -Glass.gap / 2
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: MiOS.NotifModel.removeHistory(cardDelegate.modelData.id)
                            }
                        }
                    }

                    Text {
                        visible: (cardDelegate.modelData.title || "") !== ""
                        text: cardDelegate.modelData.title || ""
                        color: root.theme.fg
                        font.family: root.theme.fontFamily
                        font.pixelSize: root.theme.fontSize
                        font.bold: true
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                    }

                    Text {
                        visible: (cardDelegate.modelData.body || "") !== ""
                        text: cardDelegate.modelData.body || ""
                        color: root.theme.subtle
                        font.family: root.theme.fontFamily
                        font.pixelSize: root.theme.smallFontSize
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        maximumLineCount: 4
                        elide: Text.ElideRight
                    }
                }
            }
        }
    }
}
