// AI-hint: Glass overlay popups for the MiOS Dock & App Drawer (Sidebar.qml):
//   1. Live Window Preview Popup (previewWindow): triggered on long-highlight (~400ms hover) over any dock icon
//      that has open or minimized windows. Renders real-time live window views via Quickshell.Wayland ScreencopyView
//      (including minimized windows on special:minimized), with window title, Open/Minimized status pill,
//      click-to-activate/restore, minimize/restore button, and close button.
//   2. Right-Click Context Menu Popup (menuWindow): triggered on right-click on any dock icon, minimized tile,
//      starred/favourite app, or app drawer entry. Provides options to Pin/Remove Pin from Dock, Star/Favourite or
//      Remove Star/Favourite, Move Left/Right, Re-organize/Manage Pins & Favourites (long-click/touch+hold mode),
//      launch, pin, favourite, minimize, restore into the current workspace, and close. OS and shader controls live in the topbar.
// AI-related: usr/share/mios/quickshell/Sidebar.qml, usr/share/mios/quickshell/DockLamp.qml,
// usr/share/mios/quickshell/Glass.qml, usr/share/mios/quickshell/Theme.qml, usr/share/mios/quickshell/NotifModel.qml
import QtQuick
import Quickshell as QS
import Quickshell.Hyprland
import Quickshell.Io
import Quickshell.Wayland

QS.Scope {
    id: popups
    required property var screen
    required property QtObject theme
    required property var dockRoot
    readonly property var shellState: dockRoot.shellState || ({})
    readonly property var remoteStats: shellState.rdpStats || ({})
    readonly property bool remoteAttached: shellState.remoteAttached === true
    readonly property var remoteScreen: QS.Quickshell.screens.find(s => s.name === remoteStats.output) || null
    property string previewSettingsAddress: ""
    property bool pipEnabled: false
    property string pipError: ""
    property var pipTarget: null
    function refreshPip() {
        pipEnabled = false
        pipError = ""
        pipTarget = DockLamp.windowInfo(previewSettingsAddress)
        if (!pipTarget || phonePreview) return
        const tags = pipTarget.tags || []
        pipEnabled = tags.some(t => String(t).replace(/\*$/, "") === "mios-pip-managed")
    }
    onPreviewSettingsAddressChanged: refreshPip()
    function togglePip() {
        if (pipControl.running || !pipTarget) return
        const address = String(pipTarget.address || "")
        const pid = Number(pipTarget.pid || 0)
        const stable = String(pipTarget.stableId || "")
        if (!/^0x[0-9a-f]+$/i.test(address) || pid <= 0 || !/^[0-9a-f]+$/i.test(stable)) {
            pipError = "Native window identity unavailable"
            return
        }
        pipError = ""
        pipControl.command = ["/usr/bin/mios-pip-gravity", "toggle", "--window", address, "--pid", String(pid), "--stable-id", stable]
        pipControl.running = true
    }
    Process {
        id: pipControl
        stdout: StdioCollector {
            onStreamFinished: {
                try { const result = JSON.parse(text); if (result.address === popups.previewSettingsAddress) popups.pipEnabled = result.pip === true }
                catch (e) { popups.pipError = "Could not read native PiP state" }
            }
        }
        stderr: StdioCollector { onStreamFinished: if (text.trim()) popups.pipError = text.trim() }

    }
    property bool previewPinned: false
    readonly property bool phonePreview: previewSourceKind === "phone"
    readonly property var selectedShaderPane: typeof shellState.shaderPane === "function" ? shellState.shaderPane(previewSettingsAddress) : null
    function phoneWindows() {
        return [{title: remoteAttached ? String((remoteStats.client || {}).peer || "Remote device") : "No device connected",
                 address: "", minimized: false, activated: remoteAttached, toplevel: remoteScreen}]
    }
    onRemoteScreenChanged: if (phonePreview) previewWindows = phoneWindows()
    onRemoteAttachedChanged: if (phonePreview) previewWindows = phoneWindows()
    function showPhone(rect, pinned) {
        showPreview({title: "Phone", icon: "phone-symbolic", windows: phoneWindows(), rect: rect,
                     centerX: rect[0] - dockRoot.monX + rect[2] / 2, sourceKind: "phone", pinned: pinned})
    }

    component PreviewAction: Rectangle {
        id: action
        property string label: ""
        signal triggered()
        width: parent.width
        height: popups.theme.searchRowHeight - 8
        radius: height / 2
        color: actionHit.containsMouse ? popups.theme.withAlpha(popups.theme.accent, popups.theme.hoverOpacity) : Glass.fill
        Text {
            anchors.fill: parent; anchors.margins: popups.theme.edgeMargin
            text: action.label; textFormat: Text.PlainText; elide: Text.ElideRight
            verticalAlignment: Text.AlignVCenter
            font.family: popups.theme.fontFamily; font.pixelSize: popups.theme.smallFontSize
            color: action.enabled ? popups.theme.fg : popups.theme.muted
        }
        MouseArea { id: actionHit; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: action.triggered() }
    }

    readonly property real ui: screen ? Mobile.uiScale(screen.name) : 1
    readonly property real screenW: screen ? screen.width : 1920
    readonly property real screenH: screen ? screen.height : 1080
    readonly property real dockTotalH: Math.round((theme.dockHeight + 2 * theme.edgeMargin) * ui)

    // ---- Live Window Preview state (long-highlight on dock icon with open/minimized windows) -----------------------
    property bool previewOpen: false
    property bool previewHovered: false
    property var previewEntry: null
    property string previewTitle: ""
    property string previewIcon: ""
    property real previewCenterX: screenW / 2
    property var previewWindows: []
    property var previewTargetRect: [0, 0, 48, 48]
    property string previewSourceKind: "icon"

    readonly property int cardWidth: 236
    readonly property int cardHeight: 172
    readonly property int previewW: Math.max(cardWidth + 2 * theme.edgeMargin,
        Math.min(Math.round(screenW / ui) - 2 * theme.edgeMargin,
                 Math.max(1, previewWindows.length) * (cardWidth + theme.edgeMargin) + theme.edgeMargin))
    readonly property int previewH: cardHeight + 36 + 2 * theme.edgeMargin + ((phonePreview || previewSettingsAddress !== "") ? previewSettingsColumn.implicitHeight + theme.edgeMargin : 0)

    function showPreview(opts) {
        if (!opts || !Array.isArray(opts.windows) || opts.windows.length === 0) {
            hidePreviewImmediate()
            return
        }
        menuOpen = false
        previewSettingsAddress = ""
        previewPinned = opts.pinned === true
        previewEntry = opts.entry || null
        previewTitle = String(opts.title || (opts.entry ? opts.entry.name : "") || "Window")
        previewIcon = String(opts.icon || (opts.entry ? opts.entry.icon : "") || "")
        previewCenterX = (typeof opts.centerX === "number" && isFinite(opts.centerX)) ? opts.centerX : (screenW / 2)
        previewWindows = opts.windows
        previewTargetRect = opts.rect || [0, 0, 48, 48]
        previewSourceKind = opts.sourceKind || "icon"
        previewDismissTimer.stop()
        previewOpen = true
    }

    function scheduleHidePreview() {
        previewDismissTimer.restart()
    }

    function hidePreviewImmediate() {
        previewDismissTimer.stop()
        previewOpen = false
        previewHovered = false
        previewPinned = false
        previewSettingsAddress = ""
    }

    Timer {
        id: previewDismissTimer
        interval: 220
        repeat: false
        onTriggered: {
            if (!popups.previewPinned && !popups.previewHovered && !popups.dockRoot.tilePreviewHold) {
                popups.previewOpen = false
            }
        }
    }

    // ---- Right-Click Context Menu state ----------------------------------------------------------------------------
    property bool menuOpen: false
    property var menuEntry: null
    property string menuAppId: ""
    property string menuTitle: ""
    property string menuIcon: ""
    property real menuX: screenW / 2
    property real menuY: screenH - dockTotalH - 280
    property var menuWindows: []
    property var menuRect: [0, 0, 48, 48]
    property string menuSourceKind: "icon"

    readonly property bool menuIsPinned: menuAppId !== "" && DockLamp.isPinned(menuAppId, theme.dockPinned)
    readonly property bool menuIsStarred: menuAppId !== "" && DockLamp.isStarred(menuAppId)
    readonly property var menuFirstRunning: {
        for (let i = 0; i < menuWindows.length; i++) {
            if (menuWindows[i] && !menuWindows[i].minimized) return menuWindows[i]
        }
        return null
    }
    readonly property var menuFirstMinimized: {
        for (let i = 0; i < menuWindows.length; i++) {
            if (menuWindows[i] && menuWindows[i].minimized) return menuWindows[i]
        }
        return null
    }
    readonly property string menuTargetAddress: {
        if (menuFirstRunning && menuFirstRunning.address) return menuFirstRunning.address
        if (menuFirstMinimized && menuFirstMinimized.address) return menuFirstMinimized.address
        return ""
    }

    function openContextMenu(opts) {
        if (!opts) return
        hidePreviewImmediate()
        menuEntry = opts.entry || null
        const rawId = String(opts.appId || (opts.entry ? (opts.entry.id || opts.entry.appId) : "") || "")
        menuAppId = DockLamp.canonAppId(rawId)
        menuTitle = String(opts.title || (opts.entry ? opts.entry.name : "") || menuAppId || "Application")
        menuIcon = String(opts.icon || (opts.entry ? opts.entry.icon : "") || menuAppId)
        menuWindows = Array.isArray(opts.windows) ? opts.windows : []
        menuRect = Array.isArray(opts.rect) && opts.rect.length === 4 ? opts.rect : [0, 0, 48, 48]
        menuSourceKind = String(opts.sourceKind || "icon")
        const mw = Math.round(268 * ui)
        const mh = Math.round(menuContentCol.implicitHeight * ui) + Math.round(2 * theme.edgeMargin * ui)
        const reqX = (typeof opts.screenX === "number" && isFinite(opts.screenX)) ? opts.screenX : (screenW / 2)
        const reqY = (typeof opts.screenY === "number" && isFinite(opts.screenY)) ? opts.screenY : (screenH - dockTotalH - mh - 8)
        menuX = Math.max(Math.round(theme.edgeMargin * ui), Math.min(screenW - mw - Math.round(theme.edgeMargin * ui), Math.round(reqX - mw / 2)))
        menuY = Math.max(Math.round((theme.topHeight + 2 * theme.edgeMargin) * ui),
                         Math.min(screenH - dockTotalH - Math.max(220, mh) - Math.round(theme.edgeMargin * ui), Math.round(reqY)))
        menuOpen = true
    }

    function closeContextMenu() {
        menuOpen = false
    }

    // ---- 1. Live Window Preview PanelWindow ------------------------------------------------------------------------
    QS.PanelWindow {
        id: previewWin
        screen: popups.screen
        WlrLayershell.namespace: Mobile.layerNamespace(NotifModel.layerNamespace, popups.screen ? popups.screen.name : "")
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: popups.previewPinned ? WlrKeyboardFocus.OnDemand : WlrKeyboardFocus.None
        anchors { top: true; left: true }
        exclusiveZone: 0
        exclusionMode: QS.ExclusionMode.Ignore
        visible: (popups.previewOpen && popups.previewWindows.length > 0) || previewMorph.shown
        color: "transparent"

        readonly property int winW: Math.round(popups.previewW * popups.ui)
        readonly property int winH: Math.round(popups.previewH * popups.ui)
        readonly property real targetX: Math.max(Math.round(popups.theme.edgeMargin * popups.ui),
                               Math.min(Math.round(popups.screenW - winW - popups.theme.edgeMargin * popups.ui),
                                        Math.round(popups.previewCenterX - winW / 2)))
        readonly property real targetY: Math.max(Math.round((popups.theme.topHeight + 2 * popups.theme.edgeMargin) * popups.ui),
                              Math.round(popups.screenH - popups.dockTotalH - winH - (popups.theme.edgeMargin / 2) * popups.ui))
        readonly property rect fromRect: Qt.rect(popups.previewTargetRect[0] - popups.dockRoot.monX,
            popups.previewTargetRect[1] - popups.dockRoot.monY, popups.previewTargetRect[2], popups.previewTargetRect[3])
        readonly property rect unionRect: Qt.rect(Math.min(fromRect.x, targetX), Math.min(fromRect.y, targetY),
            Math.max(fromRect.x + fromRect.width, targetX + winW) - Math.min(fromRect.x, targetX),
            Math.max(fromRect.y + fromRect.height, targetY + winH) - Math.min(fromRect.y, targetY))
        readonly property rect activeRect: popups.previewOpen && previewMorph.progress >= 1 ? Qt.rect(targetX, targetY, winW, winH) : unionRect
        margins.left: activeRect.x
        margins.top: activeRect.y
        implicitWidth: activeRect.width
        implicitHeight: activeRect.height
        mask: QS.Region { item: previewMorph.glass }
        HyprlandFocusGrab {
            windows: [previewWin, popups.dockRoot.dockWindow]
            active: popups.previewOpen && popups.previewPinned
            onCleared: popups.hidePreviewImmediate()
        }
        Emerge {
            id: previewMorph
            open: popups.previewOpen
            duration: popups.theme.topbar.popup_ms !== undefined ? popups.theme.topbar.popup_ms : 200
            fromRect: Qt.rect(previewWin.fromRect.x - previewWin.activeRect.x, previewWin.fromRect.y - previewWin.activeRect.y,
                              previewWin.fromRect.width, previewWin.fromRect.height)
            toRect: Qt.rect(previewWin.targetX - previewWin.activeRect.x, previewWin.targetY - previewWin.activeRect.y, previewWin.winW, previewWin.winH)
            fromRadius: Glass.cornerFor(previewWin.fromRect.height)
            toRadius: Glass.rounding
            fill: Glass.fill
            fromFill: Glass.fill
        Item {
            width: popups.previewW
            height: popups.previewH
            scale: popups.ui
            transformOrigin: Item.TopLeft

            Item {
                id: previewGlass
                anchors.fill: parent
                clip: true

                HoverHandler {
                    onHoveredChanged: {
                        popups.previewHovered = hovered
                        if (hovered) previewDismissTimer.stop()
                        else popups.scheduleHidePreview()
                    }
                }

                Column {
                    anchors.fill: parent
                    anchors.margins: popups.theme.edgeMargin
                    spacing: popups.theme.edgeMargin / 2

                    // Header row: App icon + App title + window count badge
                    Row {
                        width: parent.width
                        height: 24
                        spacing: popups.theme.edgeMargin

                        Image {
                            width: 18
                            height: 18
                            anchors.verticalCenter: parent.verticalCenter
                            visible: popups.previewIcon !== ""
                            source: popups.previewIcon ? QS.Quickshell.iconPath(popups.previewIcon, true) : ""
                            fillMode: Image.PreserveAspectFit
                        }
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            text: popups.phonePreview ? "Phone" : popups.previewTitle + " (" + popups.previewWindows.length + ")"
                            color: popups.theme.fg
                            font.family: popups.theme.fontFamily
                            font.pixelSize: popups.theme.fontSize
                            font.bold: true
                            elide: Text.ElideRight
                        }
                    }

                    // Row of live window preview cards
                    Row {
                        id: previewCardsRow
                        height: popups.cardHeight
                        spacing: popups.theme.edgeMargin

                        Repeater {
                            model: popups.previewWindows
                            delegate: Rectangle {
                                id: winCard
                                required property var modelData
                                required property int index
                                width: popups.cardWidth
                                height: popups.cardHeight
                                radius: popups.theme.radius / 2
                                color: cardMouse.containsMouse
                                    ? popups.theme.withAlpha(popups.theme.accent, popups.theme.hoverOpacity)
                                    : popups.theme.withAlpha(popups.theme.bg, popups.theme.panelOpacity)
                                border.width: 1
                                border.color: modelData.activated
                                    ? popups.theme.cursor
                                    : popups.theme.outlineColor
                                clip: true

                                Column {
                                    anchors.fill: parent
                                    anchors.margins: 6
                                    spacing: 4

                                    // Card title bar: status dot + title + minimize/restore + close
                                    Item {
                                        width: parent.width
                                        height: 22

                                        Rectangle {
                                            id: stateBadge
                                            anchors.left: parent.left
                                            anchors.verticalCenter: parent.verticalCenter
                                            width: badgeLabel.implicitWidth + 10
                                            height: 18
                                            radius: 9
                                            color: winCard.modelData.minimized
                                                ? popups.theme.withAlpha(popups.theme.warning, 0.35)
                                                : popups.theme.withAlpha(popups.theme.success, 0.35)
                                            Text {
                                                id: badgeLabel
                                                anchors.centerIn: parent
                                                text: popups.phonePreview ? (popups.remoteAttached ? "Connected" : "Offline") : (winCard.modelData.minimized ? "Minimized" : "Live")
                                                color: popups.theme.fg
                                                font.family: popups.theme.fontFamily
                                                font.pixelSize: Math.max(9, popups.theme.smallFontSize - 1)
                                            }
                                        }

                                        Text {
                                            anchors.left: stateBadge.right
                                            anchors.leftMargin: 6
                                            anchors.right: cardActions.left
                                            anchors.rightMargin: 4
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: winCard.modelData.title || popups.previewTitle
                                            color: popups.theme.fg
                                            font.family: popups.theme.fontFamily
                                            font.pixelSize: popups.theme.smallFontSize
                                            elide: Text.ElideRight
                                        }

                                        Row {
                                            id: cardActions
                                            anchors.right: parent.right
                                            anchors.verticalCenter: parent.verticalCenter
                                            spacing: 4
                                            z: 3

                                            Rectangle {
                                                visible: !popups.phonePreview
                                                width: 18; height: 18; radius: 9
                                                color: Glass.fill
                                                Text { anchors.centerIn: parent; text: "⚙"; color: popups.theme.fg; font.pixelSize: 12 }
                                                MouseArea {
                                                    anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                                    onClicked: {
                                                        popups.previewSettingsAddress = popups.previewSettingsAddress === winCard.modelData.address ? "" : winCard.modelData.address
                                                        popups.previewPinned = true
                                                    }
                                                }
                                            }
                                            Rectangle {
                                                visible: !popups.phonePreview
                                                width: 18
                                                height: 18
                                                radius: 9
                                                color: minBtnMouse.containsMouse ? popups.theme.warning : popups.theme.withAlpha(popups.theme.subtle, 0.25)
                                                Text {
                                                    anchors.centerIn: parent
                                                    text: winCard.modelData.minimized ? "▴" : "▾"
                                                    color: popups.theme.fg
                                                    font.family: popups.theme.fontFamily
                                                    font.pixelSize: 10
                                                }
                                                MouseArea {
                                                    id: minBtnMouse
                                                    anchors.fill: parent
                                                    hoverEnabled: true
                                                    cursorShape: Qt.PointingHandCursor
                                                    onClicked: {
                                                        if (winCard.modelData.minimized && winCard.modelData.address) {
                                                            DockLamp.restore(winCard.modelData.address, popups.previewSourceKind, popups.previewTargetRect)
                                                        } else if (winCard.modelData.address) {
                                                            DockLamp.minimize(winCard.modelData.address)
                                                        }
                                                        popups.hidePreviewImmediate()
                                                    }
                                                }
                                            }

                                            Rectangle {
                                                width: 18
                                                height: 18
                                                radius: 9
                                                visible: !popups.phonePreview
                                                color: closeBtnMouse.containsMouse ? popups.theme.error : popups.theme.withAlpha(popups.theme.subtle, 0.25)
                                                Text {
                                                    anchors.centerIn: parent
                                                    text: "×"
                                                    color: popups.theme.fg
                                                    font.family: popups.theme.fontFamily
                                                    font.pixelSize: 11
                                                }
                                                MouseArea {
                                                    id: closeBtnMouse
                                                    anchors.fill: parent
                                                    hoverEnabled: true
                                                    cursorShape: Qt.PointingHandCursor
                                                    onClicked: {
                                                        if (winCard.modelData.address) {
                                                            DockLamp.closeWindow(winCard.modelData.address)
                                                        } else if (winCard.modelData.toplevel && typeof winCard.modelData.toplevel.close === "function") {
                                                            winCard.modelData.toplevel.close()
                                                        }
                                                        popups.hidePreviewImmediate()
                                                    }
                                                }
                                            }
                                        }
                                    }

                                    // Live ScreencopyView of the open or minimized Wayland toplevel
                                    Rectangle {
                                        width: parent.width
                                        height: parent.height - 26
                                        radius: popups.theme.radius / 3
                                        color: popups.theme.withAlpha(popups.theme.bg, 0.55)
                                        clip: true

                                        ScreencopyView {
                                            id: liveView
                                            anchors.centerIn: parent
                                            captureSource: popups.previewOpen && winCard.modelData.toplevel ? winCard.modelData.toplevel : null
                                            live: popups.previewOpen && captureSource !== null
                                            paintCursor: false
                                            constraintSize: Qt.size(popups.cardWidth - 16, popups.cardHeight - 42)
                                            visible: captureSource !== null && hasContent
                                        }

                                        // Fallback icon+title when screencopy is warming up or fixture-only
                                        Column {
                                            anchors.centerIn: parent
                                            spacing: 4
                                            visible: !liveView.visible
                                            Image {
                                                anchors.horizontalCenter: parent.horizontalCenter
                                                width: 36
                                                height: 36
                                                visible: popups.previewIcon !== ""
                                                source: popups.previewIcon ? QS.Quickshell.iconPath(popups.previewIcon, true) : ""
                                                fillMode: Image.PreserveAspectFit
                                            }
                                            Text {
                                                anchors.horizontalCenter: parent.horizontalCenter
                                                text: winCard.modelData.minimized ? "Minimized Window" : "Live Window"
                                                color: popups.theme.muted
                                                font.family: popups.theme.fontFamily
                                                font.pixelSize: popups.theme.smallFontSize
                                            }
                                        }
                                    }
                                }

                                MouseArea {
                                    id: cardMouse
                                    anchors.fill: parent
                                    anchors.topMargin: 24
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: {
                                        if (popups.phonePreview) return
                                        if (winCard.modelData.minimized && winCard.modelData.address) {
                                            DockLamp.restore(winCard.modelData.address, popups.previewSourceKind, popups.previewTargetRect)
                                        } else if (winCard.modelData.toplevel && typeof winCard.modelData.toplevel.activate === "function") {
                                            winCard.modelData.toplevel.activate()
                                        } else if (winCard.modelData.address) {
                                            DockLamp.restore(winCard.modelData.address, popups.previewSourceKind, popups.previewTargetRect)
                                        }
                                        popups.hidePreviewImmediate()
                                    }
                                }
                            }
                        }
                    }

                    Column {
                        id: previewSettingsColumn
                        width: parent.width
                        spacing: popups.theme.edgeMargin / 2
                        visible: popups.phonePreview || popups.previewSettingsAddress !== ""
                        Text {
                            width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
                            font.family: popups.theme.fontFamily; font.pixelSize: popups.theme.smallFontSize; color: popups.theme.fg
                            text: popups.phonePreview ? (popups.remoteAttached ? String(popups.remoteStats.output) + " · " + String(popups.remoteStats.codec || "negotiating") + "\n" + Number(popups.remoteStats.fps || 0).toFixed(1) + " RDP FPS · " + Math.round(popups.remoteStats.capture_to_ack_ms_p50 || 0) + " ms" : "No remote device connected") : "Window options"
                        }
                        PreviewAction {
                            visible: !popups.phonePreview
                            label: popups.pipEnabled ? "Picture-in-Picture: on" : "Picture-in-Picture: off"
                            enabled: popups.pipTarget !== null && !pipControl.running
                            onTriggered: popups.togglePip()
                        }
                        Text {
                            width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
                            text: popups.pipError; visible: text !== ""
                            color: popups.theme.error; font.pixelSize: popups.theme.smallFontSize
                        }
                        PreviewAction {
                            visible: !popups.phonePreview
                            label: popups.selectedShaderPane ? "Disable ShaderGlass" : "Enable ShaderGlass"
                            onTriggered: popups.shellState.shaderAction(popups.selectedShaderPane ? "detach" : "attach", popups.previewSettingsAddress)
                        }
                        PreviewAction {
                            visible: !popups.phonePreview
                            label: "Choose shader and parameters…"
                            onTriggered: {
                                popups.shellState.shaderAction("browse", popups.previewSettingsAddress)
                                popups.hidePreviewImmediate()
                            }
                        }
                        PreviewAction {
                            visible: popups.phonePreview
                            label: popups.remoteStats.paused ? "Resume display and input" : "Pause display and input"
                            enabled: popups.remoteAttached && popups.remoteStats.control_supported === true
                            onTriggered: popups.shellState.remoteAction(popups.remoteStats.paused ? "resume" : "pause")
                        }
                        PreviewAction {
                            visible: popups.phonePreview
                            label: "Disconnect device"
                            enabled: popups.remoteAttached && popups.remoteStats.control_supported === true
                            onTriggered: popups.shellState.remoteAction("disconnect")
                        }
                        Text {
                            width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
                            text: popups.phonePreview ? (popups.shellState.remoteError || "") : (popups.shellState.shaderError || "")
                            visible: text !== ""; color: popups.theme.error; font.pixelSize: popups.theme.smallFontSize
                        }
                    }
                }
            }
        }
        }
    }

    // ---- 2. Right-Click Context Menu PanelWindow -------------------------------------------------------------------
    QS.PanelWindow {
        id: menuWin
        screen: popups.screen
        WlrLayershell.namespace: Mobile.layerNamespace(NotifModel.layerNamespace, popups.screen ? popups.screen.name : "")
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: popups.menuOpen ? WlrKeyboardFocus.OnDemand : WlrKeyboardFocus.None
        anchors { top: true; left: true }
        exclusiveZone: 0
        exclusionMode: QS.ExclusionMode.Ignore
        visible: popups.menuOpen || menuMorph.shown
        color: "transparent"

        readonly property int boxW: Math.round(268 * popups.ui)
        readonly property int boxH: Math.round((menuContentCol.implicitHeight + 2 * popups.theme.edgeMargin) * popups.ui)
        readonly property real targetX: Math.max(Math.round(popups.theme.edgeMargin * popups.ui),
                               Math.min(Math.round(popups.screenW - boxW - popups.theme.edgeMargin * popups.ui),
                                        Math.round(popups.menuX)))
        readonly property real targetY: Math.max(Math.round((popups.theme.topHeight + 2 * popups.theme.edgeMargin) * popups.ui),
                              Math.min(Math.round(popups.screenH - popups.dockTotalH - boxH - (popups.theme.edgeMargin / 2) * popups.ui),
                                       Math.round(popups.menuY)))
        readonly property rect fromRect: Qt.rect(popups.menuRect[0] - popups.dockRoot.monX,
                                                  popups.menuRect[1] - popups.dockRoot.monY,
                                                  Math.max(1, popups.menuRect[2]), Math.max(1, popups.menuRect[3]))
        readonly property rect unionRect: Qt.rect(Math.min(fromRect.x, targetX), Math.min(fromRect.y, targetY),
            Math.max(fromRect.x + fromRect.width, targetX + boxW) - Math.min(fromRect.x, targetX),
            Math.max(fromRect.y + fromRect.height, targetY + boxH) - Math.min(fromRect.y, targetY))
        readonly property bool atRest: popups.menuOpen && menuMorph.progress >= 1
        readonly property rect activeRect: atRest ? Qt.rect(targetX, targetY, boxW, boxH) : unionRect
        margins.left: activeRect.x
        margins.top: activeRect.y
        implicitWidth: activeRect.width
        implicitHeight: activeRect.height
        mask: QS.Region { item: menuMorph.glass }

        HyprlandFocusGrab {
            windows: [menuWin]
            active: popups.menuOpen
            onCleared: popups.closeContextMenu()
        }

        Emerge {
            id: menuMorph
            open: popups.menuOpen
            duration: popups.theme.topbar.popup_ms !== undefined ? popups.theme.topbar.popup_ms : 200
            fromRect: Qt.rect(menuWin.fromRect.x - menuWin.activeRect.x, menuWin.fromRect.y - menuWin.activeRect.y,
                              menuWin.fromRect.width, menuWin.fromRect.height)
            toRect: Qt.rect(menuWin.targetX - menuWin.activeRect.x, menuWin.targetY - menuWin.activeRect.y, menuWin.boxW, menuWin.boxH)
            fromRadius: Glass.cornerFor(menuWin.fromRect.height)
            toRadius: Glass.rounding
            fill: Glass.fill
            fromFill: Glass.fill
        Item {
            width: 268
            height: menuContentCol.implicitHeight + 2 * popups.theme.edgeMargin
            scale: popups.ui
            transformOrigin: Item.TopLeft
            focus: popups.menuOpen
            Keys.onEscapePressed: popups.closeContextMenu()

            Item {
                anchors.fill: parent
                clip: true

                Column {
                    id: menuContentCol
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: popups.theme.edgeMargin
                    spacing: 4

                    // Header row: App icon + App name + Star/Pin badges
                    Row {
                        width: parent.width
                        height: 28
                        spacing: popups.theme.edgeMargin

                        Image {
                            width: 20
                            height: 20
                            anchors.verticalCenter: parent.verticalCenter
                            visible: popups.menuIcon !== ""
                            source: popups.menuIcon ? QS.Quickshell.iconPath(popups.menuIcon, true) : ""
                            fillMode: Image.PreserveAspectFit
                        }
                        Text {
                            width: parent.width - 68
                            anchors.verticalCenter: parent.verticalCenter
                            text: popups.menuTitle
                            color: popups.theme.fg
                            font.family: popups.theme.fontFamily
                            font.pixelSize: popups.theme.fontSize
                            font.bold: true
                            elide: Text.ElideRight
                        }
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            text: (popups.menuIsStarred ? "★ " : "") + (popups.menuIsPinned ? "📌" : "")
                            color: popups.theme.cursor
                            font.family: popups.theme.fontFamily
                            font.pixelSize: popups.theme.smallFontSize
                        }
                    }

                    Rectangle {
                        width: parent.width
                        height: 1
                        color: popups.theme.outlineColor
                    }

                    // Menu Item helper component
                    component MenuRow: Rectangle {
                        id: mrow
                        property string iconText: ""
                        property string label: ""
                        property bool visibleItem: true
                        property bool destructive: false
                        signal triggered()

                        visible: visibleItem
                        width: menuContentCol.width
                        height: visibleItem ? 28 : 0
                        radius: height / 2
                        color: rowMouse.containsMouse
                            ? popups.theme.withAlpha(destructive ? popups.theme.error : popups.theme.accent, popups.theme.hoverOpacity)
                            : Glass.fill

                        Row {
                            anchors.fill: parent
                            anchors.leftMargin: popups.theme.edgeMargin
                            anchors.rightMargin: popups.theme.edgeMargin
                            spacing: popups.theme.edgeMargin

                            Text {
                                width: 18
                                anchors.verticalCenter: parent.verticalCenter
                                text: mrow.iconText
                                color: mrow.destructive ? popups.theme.error : popups.theme.cursor
                                font.family: popups.theme.fontFamily
                                font.pixelSize: popups.theme.fontSize
                                horizontalAlignment: Text.AlignHCenter
                            }
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                text: mrow.label
                                color: mrow.destructive ? popups.theme.error : popups.theme.fg
                                font.family: popups.theme.fontFamily
                                font.pixelSize: popups.theme.smallFontSize
                                elide: Text.ElideRight
                            }
                        }

                        MouseArea {
                            id: rowMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                mrow.triggered()
                                popups.closeContextMenu()
                            }
                        }
                    }

                    // 1. Pin / Unpin to Dock
                    MenuRow {
                        visibleItem: popups.menuAppId !== ""
                        iconText: popups.menuIsPinned ? "⊘" : "📌"
                        label: popups.menuIsPinned ? "Remove Pin from Dock" : "Pin to Dock"
                        onTriggered: DockLamp.togglePin(popups.menuAppId, popups.theme.dockPinned)
                    }

                    // 2. Star / Favourite toggle
                    MenuRow {
                        visibleItem: popups.menuAppId !== ""
                        iconText: popups.menuIsStarred ? "☆" : "★"
                        label: popups.menuIsStarred ? "Remove favourite" : "Add favourite"
                        onTriggered: DockLamp.toggleStar(popups.menuAppId)
                    }

                    Rectangle {
                        width: parent.width
                        height: 1
                        color: popups.theme.outlineColor
                    }

                    // 5. Launch New Window
                    MenuRow {
                        visibleItem: popups.menuEntry !== null && typeof popups.menuEntry.execute === "function"
                        iconText: "＋"
                        label: "New window"
                        onTriggered: popups.dockRoot.launch(popups.menuEntry, popups.menuRect)
                    }

                    // 6. Focus / Restore / Minimize Window when running or minimized
                    MenuRow {
                        visibleItem: popups.menuFirstMinimized !== null
                        iconText: "▴"
                        label: "Restore here"
                        onTriggered: {
                            if (popups.menuFirstMinimized && popups.menuFirstMinimized.address) {
                                DockLamp.bringHere(popups.menuFirstMinimized.address, popups.menuSourceKind, popups.menuRect)
                            }
                        }
                    }
                    MenuRow {
                        visibleItem: popups.menuFirstRunning !== null
                        iconText: "▾"
                        label: "Minimize Window"
                        onTriggered: {
                            if (popups.menuFirstRunning && popups.menuFirstRunning.address) {
                                DockLamp.minimize(popups.menuFirstRunning.address)
                            }
                        }
                    }
                    MenuRow {
                        visibleItem: popups.menuFirstRunning !== null && popups.menuFirstRunning.toplevel !== null
                        iconText: "◉"
                        label: "Bring to this workspace"
                        onTriggered: {
                            if (popups.menuFirstRunning && popups.menuFirstRunning.toplevel) {
                                DockLamp.bringHere(popups.menuFirstRunning.address, popups.menuSourceKind, popups.menuRect)
                            }
                        }
                    }

                    // 8. Close Window
                    MenuRow {
                        visibleItem: popups.menuTargetAddress !== ""
                        iconText: "×"
                        label: "Close Window"
                        destructive: true
                        onTriggered: DockLamp.closeWindow(popups.menuTargetAddress)
                    }
                }
            }
        }
        }
    }

    IpcHandler {
        target: "dock-preview"
        enabled: !!popups.screen && (!Hyprland.focusedMonitor || Hyprland.focusedMonitor.name === popups.screen.name)
        function phone(): void { popups.showPhone(popups.dockRoot.phoneRect, true) }
        function close(): void { popups.hidePreviewImmediate(); popups.closeContextMenu() }
        function window(address: string): void {
            const t = Hyprland.toplevels.values.find(t => DockLamp.addressOf(t) === address)
            if (!t || !t.wayland) return
            const o = t.lastIpcObject || ({})
            const rect = DockLamp.rectForAddress(address, popups.screen.name)
            popups.showPreview({title: o.class || "Application", icon: o.class || "", windows: [{address: address, title: o.title || o.class || "", toplevel: t.wayland, minimized: false}], rect: rect, centerX: rect[0] - popups.dockRoot.monX + rect[2]/2, pinned: true})
            popups.previewSettingsAddress = address
        }
        function pip(): void { popups.togglePip() }
        function state(): string { return JSON.stringify({open: popups.previewOpen, phone: popups.phonePreview, settings: popups.previewSettingsAddress, pip: popups.pipEnabled, pipAvailable: popups.pipTarget !== null, pipError: popups.pipError, pinned: popups.previewPinned, width: previewWin.winW, height: previewWin.winH, icon: popups.previewTargetRect}) }
    }
}
