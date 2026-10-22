// AI-hint: Full-pane glass app drawer: search field, running windows and the installed-app grid.
// Opened by the dock's grid tile, Super+Space and `quickshell ipc ... call drawer toggle`.
// Tiles come only from real desktop entries (QS.DesktopEntries) and live toplevels (ToplevelManager).
// AI-related: usr/share/mios/quickshell/Sidebar.qml, usr/share/mios/mios.toml [theme.shell].drawer_*
import QtQuick
import Quickshell as QS
import Quickshell.Hyprland
import Quickshell.Wayland

QS.PanelWindow {
    id: drawer
    required property QtObject theme
    required property QtObject shellState
    property var applications: []
    property bool open: false
    property string query: ""
    readonly property var running: ToplevelManager.toplevels.values.filter(t => t.appId || t.title)
    readonly property var matches: !open ? [] : applications.filter(e => !query ||
        (e.name + " " + e.genericName + " " + e.keywords.join(" ")).toLowerCase().includes(query.toLowerCase()))

    WlrLayershell.namespace: "mios-drawer"
    WlrLayershell.keyboardFocus: open ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None
    anchors.top: true
    anchors.bottom: true
    anchors.left: true
    anchors.right: true
    // Exclusive zone 0 keeps the pane inside the area the top bar (and dock hot zone) already reserve.
    margins.top: theme.drawerPaneMargin
    margins.bottom: theme.drawerPaneMargin
    margins.left: theme.drawerPaneMargin
    margins.right: theme.drawerPaneMargin
    exclusiveZone: 0
    visible: open
    color: "transparent"

    function close() { shellState.drawerOpen = false }
    function launch(entry) {
        entry.execute()
        close()
    }
    function iconFor(appId) {
        const e = QS.DesktopEntries.heuristicLookup(appId)
        return e && e.icon ? QS.Quickshell.iconPath(e.icon, true) : ""
    }
    onOpenChanged: {
        if (open) {
            search.text = ""
            appGrid.currentIndex = 0
            search.forceActiveFocus()
        }
    }

    // Clicking any other surface clears the grab and closes the drawer.
    HyprlandFocusGrab {
        windows: [drawer]
        active: drawer.open
        onCleared: drawer.close()
    }

    Rectangle {
        id: pane
        anchors.fill: parent
        radius: drawer.theme.radius
        color: drawer.theme.withAlpha(drawer.theme.bg, drawer.theme.drawerOpacity)
        border.width: 1
        border.color: drawer.theme.outlineColor
        clip: true

        Column {
            id: content
            anchors.fill: parent
            anchors.margins: drawer.theme.edgeMargin * 3
            spacing: drawer.theme.edgeMargin * 2

            Rectangle {
                width: Math.min(parent.width, drawer.theme.searchWidth)
                anchors.horizontalCenter: parent.horizontalCenter
                height: drawer.theme.searchRowHeight
                radius: drawer.theme.radius
                color: drawer.theme.withAlpha(drawer.theme.bg, drawer.theme.panelOpacity)
                border.width: 1
                border.color: search.activeFocus ? drawer.theme.withAlpha(drawer.theme.accent, drawer.theme.selectionOpacity)
                                                 : drawer.theme.outlineColor
                TextInput {
                    id: search
                    anchors.fill: parent
                    anchors.leftMargin: drawer.theme.edgeMargin * 2
                    anchors.rightMargin: drawer.theme.edgeMargin * 2
                    verticalAlignment: TextInput.AlignVCenter
                    color: drawer.theme.fg
                    selectionColor: drawer.theme.accent
                    selectedTextColor: drawer.theme.fg
                    font.family: drawer.theme.fontFamily
                    font.pixelSize: drawer.theme.titleFontSize
                    clip: true
                    onTextChanged: { drawer.query = text; appGrid.currentIndex = 0 }
                    Text {
                        anchors.fill: parent
                        verticalAlignment: Text.AlignVCenter
                        visible: !search.text
                        text: "Search installed applications"
                        color: drawer.theme.muted
                        font: search.font
                    }
                    Keys.onEscapePressed: drawer.close()
                    Keys.onDownPressed: appGrid.moveCurrentIndexDown()
                    Keys.onUpPressed: appGrid.moveCurrentIndexUp()
                    Keys.onTabPressed: appGrid.moveCurrentIndexRight()
                    Keys.onBacktabPressed: appGrid.moveCurrentIndexLeft()
                    onAccepted: if (appGrid.count > 0) drawer.launch(drawer.matches[Math.max(0, appGrid.currentIndex)])
                }
            }

            Text {
                visible: runningRow.visible
                text: "Running"
                color: drawer.theme.muted
                font.family: drawer.theme.fontFamily
                font.pixelSize: drawer.theme.smallFontSize
            }
            Flickable {
                id: runningRow
                visible: drawer.running.length > 0 && !drawer.query
                width: parent.width
                height: visible ? drawer.theme.dockHeight : 0
                contentWidth: runningItems.implicitWidth
                flickableDirection: Flickable.HorizontalFlick
                clip: true
                Row {
                    id: runningItems
                    height: parent.height
                    spacing: drawer.theme.edgeMargin
                    Repeater {
                        model: drawer.running
                        delegate: Rectangle {
                            required property var modelData
                            height: runningItems.height
                            width: Math.min(drawer.theme.notificationWidth, runLabel.implicitWidth + drawer.theme.appIcon + drawer.theme.edgeMargin * 5)
                            radius: drawer.theme.radius
                            color: runMouse.containsMouse ? drawer.theme.withAlpha(drawer.theme.accent, drawer.theme.hoverOpacity)
                                 : modelData.activated ? drawer.theme.withAlpha(drawer.theme.accent, drawer.theme.panelOpacity)
                                 : "transparent"
                            border.width: 1
                            border.color: drawer.theme.outlineColor
                            Item {
                                id: runIcon
                                anchors.left: parent.left
                                anchors.leftMargin: drawer.theme.edgeMargin * 2
                                anchors.verticalCenter: parent.verticalCenter
                                width: drawer.theme.appIcon
                                height: width
                                Image {
                                    id: runImage
                                    anchors.fill: parent
                                    source: drawer.iconFor(modelData.appId)
                                    fillMode: Image.PreserveAspectFit
                                }
                                // No desktop entry icon: the window's own initial instead of an invented glyph.
                                Rectangle {
                                    anchors.fill: parent
                                    visible: runImage.status !== Image.Ready
                                    radius: width / 2
                                    color: drawer.theme.withAlpha(drawer.theme.accent, drawer.theme.selectionOpacity)
                                    Text {
                                        anchors.centerIn: parent
                                        text: (modelData.appId || modelData.title || "?").charAt(0).toUpperCase()
                                        color: drawer.theme.fg
                                        font.family: drawer.theme.fontFamily
                                        font.pixelSize: drawer.theme.titleFontSize
                                    }
                                }
                            }
                            Text {
                                id: runLabel
                                anchors.left: runIcon.right
                                anchors.leftMargin: drawer.theme.edgeMargin
                                anchors.right: parent.right
                                anchors.rightMargin: drawer.theme.edgeMargin * 2
                                anchors.verticalCenter: parent.verticalCenter
                                text: modelData.title || modelData.appId
                                elide: Text.ElideRight
                                color: drawer.theme.fg
                                font.family: drawer.theme.fontFamily
                                font.pixelSize: drawer.theme.fontSize
                            }
                            MouseArea {
                                id: runMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: { modelData.activate(); drawer.close() }
                            }
                        }
                    }
                }
            }

            Text {
                text: drawer.query ? drawer.matches.length + " matching applications" : "Applications"
                color: drawer.theme.muted
                font.family: drawer.theme.fontFamily
                font.pixelSize: drawer.theme.smallFontSize
            }
            GridView {
                id: appGrid
                // Centre the columns that fit; tiles never stretch past the SSOT tile size.
                width: Math.max(1, Math.floor(content.width / drawer.theme.appTile)) * drawer.theme.appTile
                x: (content.width - width) / 2
                height: content.height - y
                cellWidth: drawer.theme.appTile
                cellHeight: drawer.theme.appTile
                clip: true
                keyNavigationEnabled: true
                highlightFollowsCurrentItem: true
                model: drawer.matches
                delegate: Item {
                    required property var modelData
                    required property int index
                    width: appGrid.cellWidth
                    height: appGrid.cellHeight
                    Rectangle {
                        anchors.fill: parent
                        anchors.margins: drawer.theme.edgeMargin / 2
                        radius: drawer.theme.radius
                        color: appMouse.containsMouse ? drawer.theme.withAlpha(drawer.theme.accent, drawer.theme.hoverOpacity)
                             : index === appGrid.currentIndex && drawer.query ? drawer.theme.withAlpha(drawer.theme.accent, drawer.theme.selectionOpacity)
                             : "transparent"
                    }
                    Image {
                        anchors.top: parent.top
                        anchors.topMargin: drawer.theme.edgeMargin * 1.5
                        anchors.horizontalCenter: parent.horizontalCenter
                        width: drawer.theme.appIcon
                        height: width
                        source: QS.Quickshell.iconPath(modelData.icon, true)
                        fillMode: Image.PreserveAspectFit
                    }
                    Text {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        anchors.margins: drawer.theme.edgeMargin
                        text: modelData.name
                        horizontalAlignment: Text.AlignHCenter
                        elide: Text.ElideRight
                        color: drawer.theme.fg
                        font.family: drawer.theme.fontFamily
                        font.pixelSize: drawer.theme.smallFontSize
                    }
                    MouseArea {
                        id: appMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: drawer.launch(modelData)
                    }
                }
            }
        }
    }
}
