// AI-hint: The expanded dock: what grows UPWARD out of the dock bar, at the bar's own width (Sidebar.qml sizes it from
// [desktop.dock]; it never takes the whole display). The dock is the localhost/sessions half of the HUD (the topbar is
// inbound/external/notifications/systems): top to bottom
//   search      filters the apps and the windows
//   targets     where windows can go: this compositor's outputs (local and the RDP remote display), nested sessions,
//               the host session, VMs -- each chip says how a move really happens ("moves live", "relaunches",
//               "streams only"), from `mios-gen dock targets` (tools/native/mios-gen/src/dock.rs); never a fake move
//   windows     previews of every window, minimized ones included (DockPreview.qml: flat, edge to edge, capture-safe);
//               select one, then pick a target (or drag the preview onto a chip) to move it
//   apps        the installed apps, labels on up to label_lines lines shrunk to fit, the full name in a tooltip on
//               hover and on keyboard/controller focus
//   keyboard    the slot the Phosh on-screen keyboard (stevia, `mios-gen dock osk`) is placed into, shown by the
//               keyboard toggle; the choice is remembered per output in the user state file
// Keyboard and controller (mios-inputd delivers pads as keys / `quickshell ipc call dock ...`): Tab / Shift+Tab move
// between sections, arrows inside one, Return activates, Menu or "m" opens the move picker for the selected window,
// Escape closes. Flat by design: no cards, frames or borders -- selection is a fill, everything sits on the dock glass.
// AI-related: usr/share/mios/quickshell/Sidebar.qml, DockConfig.qml, DockPreview.qml, DockLamp.qml, DockPopup.qml,
// tools/native/mios-gen/src/dock.rs, usr/share/mios/mios.toml [desktop.dock], usr/share/doc/mios/guides/hud.md
import QtQuick
import Quickshell as QS
import Quickshell.Hyprland
import Quickshell.Io
import Quickshell.Wayland

FocusScope {
    id: ex
    required property QtObject theme
    required property var dockRoot          // Sidebar.qml scope: launch(), applications, screen, shellState
    property bool open: false
    property bool oskShown: false
    signal oskToggled()
    signal closeRequested()

    readonly property real gap: theme.edgeMargin
    readonly property string query: search.text.trim().toLowerCase()
    property int section: 0                 // 0 search, 1 targets, 2 windows, 3 apps
    property var selectedWindow: null
    property string status: ""
    property bool dragging: false            // a preview is being dragged onto a target chip

    // ---- windows: every Hyprland window of this session, minimized (special workspaces) included ---------------------
    readonly property var windows: {
        if (!open || !DockConfig.previewsEnabled) return []
        const out = []
        for (const h of Hyprland.toplevels.values) {
            const o = h ? h.lastIpcObject : null
            if (!o || !o.address) continue
            const ws = o.workspace && o.workspace.name ? String(o.workspace.name) : ""
            const minimized = ws.startsWith("special:")
            if (DockConfig.previewsInclude === "minimized" && !minimized) continue
            const w = { address: DockLamp.normAddress(o.address), title: String(o.title || o.class || ""), appId: String(o.class || ""),
                        minimized: minimized, activated: !!h.activated, toplevel: h.wayland || null }
            if (ex.query && (w.title + " " + w.appId).toLowerCase().indexOf(ex.query) < 0) continue
            out.push(w)
        }
        return out.slice(0, DockConfig.previewMax)
    }
    function iconFor(appId) {
        const e = appId ? QS.DesktopEntries.heuristicLookup(appId) : null
        return e && e.icon ? e.icon : appId
    }
    onOpenChanged: if (open) { Hyprland.refreshToplevels(); targetsProc.refresh(); search.text = ""; section = 0; search.forceActiveFocus() }

    // ---- targets ------------------------------------------------------------------------------------------------------
    property var targets: []
    property var methods: ({})
    Process {
        id: targetsProc
        command: [DockConfig.backend, "dock", "targets"]
        function refresh() { if (DockConfig.targetsEnabled && !running) running = true }
        stdout: StdioCollector {
            onStreamFinished: {
                try { const v = JSON.parse(text); ex.targets = v.targets || []; ex.methods = v.methods || ({}) }
                catch (e) { ex.targets = []; }
            }
        }
    }
    Timer { interval: DockConfig.targetsPollMs; running: ex.open && DockConfig.targetsEnabled; repeat: true; onTriggered: targetsProc.refresh() }
    function methodLabel(t) {
        const m = t && t.move ? t.move.method : "none"
        return m === "monitor-move" ? "moves live" : m === "relaunch" ? "relaunches" : m === "waypipe-relaunch" ? "relaunches remotely"
             : (t && t.stream) ? "streams only" : "unavailable"
    }
    Process {
        id: moveProc
        property string label: ""
        stdout: StdioCollector { id: moveOut }
        stderr: StdioCollector { id: moveErr }
        onExited: (code) => {
            ex.status = code === 0 ? ("Moved to " + moveProc.label) : (moveErr.text.trim() || ("Move to " + moveProc.label + " failed"))
            Hyprland.refreshToplevels()
            targetsProc.refresh()
        }
    }
    function moveTo(t) {
        if (!t || moveProc.running) return
        if (!ex.selectedWindow) { ex.status = "Select a window first"; return }
        if (!t.move || t.move.method === "none" || t.available === false) { ex.status = t.label + ": " + ((t.move && t.move.note) || "cannot receive windows"); return }
        moveProc.label = t.label
        ex.status = "Moving to " + t.label + "…"
        moveProc.command = [DockConfig.backend, "dock", "move", "--target", String(t.id), "--address", String(ex.selectedWindow.address)]
        moveProc.running = true
    }

    Keys.onEscapePressed: ex.closeRequested()
    Keys.onTabPressed: ex.section = (ex.section + 1) % 4
    Keys.onBacktabPressed: ex.section = (ex.section + 3) % 4
    onSectionChanged: [search, targetRow, windowRow, appGrid][section].forceActiveFocus()

    Column {
        id: col
        anchors.fill: parent
        anchors.bottomMargin: ex.oskShown ? DockConfig.oskHeight : 0
        spacing: ex.gap

        // search + keyboard toggle
        Item {
            width: parent.width
            height: ex.theme.appIcon
            TextInput {
                id: search
                anchors.left: parent.left
                anchors.right: oskToggle.left
                anchors.rightMargin: ex.gap
                anchors.verticalCenter: parent.verticalCenter
                color: ex.theme.fg
                selectionColor: ex.theme.accent
                selectedTextColor: ex.theme.fg
                font.family: ex.theme.fontFamily
                font.pixelSize: ex.theme.fontSize
                clip: true
                Text {
                    anchors.fill: parent
                    visible: !search.text
                    text: "Search apps and windows"
                    color: ex.theme.muted
                    font: search.font
                }
                Keys.onDownPressed: ex.section = 1
                onAccepted: if (appGrid.count > 0) ex.dockRoot.launch(appGrid.model[0], null)
            }
            Item {
                id: oskToggle
                visible: DockConfig.oskEnabled
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                width: visible ? height : 0
                height: parent.height
                Rectangle {
                    anchors.fill: parent
                    radius: Glass.cornerFor(height)
                    color: ex.oskShown ? ex.theme.withAlpha(ex.theme.accent, ex.theme.selectionOpacity)
                         : oskHit.containsMouse ? ex.theme.withAlpha(ex.theme.accent, ex.theme.hoverOpacity) : "transparent"
                }
                Text {
                    anchors.centerIn: parent
                    text: "\u{f030c}"            // keyboard glyph (Nerd Font)
                    color: ex.theme.fg
                    font.family: ex.theme.fontFamily
                    font.pixelSize: ex.theme.appIcon * 0.6
                }
                MouseArea { id: oskHit; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: ex.oskToggled() }
            }
        }

        // targets: a flat row of chips; each says how a move really happens
        ListView {
            id: targetRow
            visible: DockConfig.targetsEnabled && ex.targets.length > 0
            width: parent.width
            height: visible ? ex.theme.searchRowHeight : 0
            orientation: ListView.Horizontal
            spacing: ex.gap
            clip: true
            model: ex.targets
            keyNavigationEnabled: true
            Keys.onReturnPressed: ex.moveTo(ex.targets[currentIndex])
            Keys.onEnterPressed: ex.moveTo(ex.targets[currentIndex])
            Keys.onDownPressed: ex.section = 2
            Keys.onUpPressed: ex.section = 0
            delegate: Item {
                id: chip
                required property var modelData
                required property int index
                width: chipCol.implicitWidth + 2 * ex.gap
                height: targetRow.height
                readonly property bool usable: modelData.available !== false && modelData.move && modelData.move.method !== "none"
                Rectangle {
                    anchors.fill: parent
                    radius: Glass.cornerFor(height)
                    color: (targetRow.activeFocus && targetRow.currentIndex === chip.index) || chipDrop.containsDrag
                           ? ex.theme.withAlpha(ex.theme.accent, ex.theme.selectionOpacity)
                           : chipHit.containsMouse ? ex.theme.withAlpha(ex.theme.accent, ex.theme.hoverOpacity) : "transparent"
                }
                Column {
                    id: chipCol
                    anchors.centerIn: parent
                    Text {
                        text: chip.modelData.label
                        color: chip.usable ? ex.theme.fg : ex.theme.muted
                        font.family: ex.theme.fontFamily
                        font.pixelSize: ex.theme.smallFontSize
                    }
                    Text {
                        text: ex.methodLabel(chip.modelData)
                        color: ex.theme.muted
                        font.family: ex.theme.fontFamily
                        font.pixelSize: Math.max(DockConfig.labelMinFont, ex.theme.smallFontSize - 2)
                    }
                }
                MouseArea { id: chipHit; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: { targetRow.currentIndex = chip.index; ex.moveTo(chip.modelData) } }
                DropArea { id: chipDrop; anchors.fill: parent; keys: ["mios-dock-window"]; onDropped: drop => ex.moveTo(chip.modelData) }
            }
        }

        // windows: previews, minimized ones included; edge to edge, no frames
        ListView {
            id: windowRow
            visible: ex.windows.length > 0
            width: parent.width
            height: visible ? DockConfig.previewHeight : 0
            orientation: ListView.Horizontal
            spacing: ex.gap
            clip: !ex.dragging
            model: ex.windows
            keyNavigationEnabled: true
            interactive: !ex.dragging
            onCurrentIndexChanged: if (activeFocus) ex.selectedWindow = ex.windows[currentIndex] || null
            onActiveFocusChanged: if (activeFocus) ex.selectedWindow = ex.windows[currentIndex] || null
            Keys.onReturnPressed: activateWindow(ex.windows[currentIndex])
            Keys.onEnterPressed: activateWindow(ex.windows[currentIndex])
            Keys.onMenuPressed: ex.section = 1
            Keys.onPressed: event => { if (event.key === Qt.Key_M) { ex.section = 1; event.accepted = true } }
            Keys.onDownPressed: ex.section = 3
            Keys.onUpPressed: ex.section = 1
            function activateWindow(w) {
                if (!w) return
                if (w.minimized) DockLamp.restore(w.address, "minimized", ex.dockRoot.dockWindow.rectForAddress(w.address))
                else if (w.toplevel) w.toplevel.activate()
                ex.closeRequested()
            }
            delegate: Item {
                id: wtile
                required property var modelData
                required property int index
                height: windowRow.height
                width: Math.round(height * 16 / 10)
                readonly property bool selected: ex.selectedWindow && ex.selectedWindow.address === modelData.address
                DockPreview {
                    id: pv
                    width: wtile.width          // not anchored: the drag moves it onto a target chip
                    height: wtile.height
                    z: drag.active ? 5 : 0
                    win: wtile.modelData
                    theme: ex.theme
                    icon: ex.iconFor(wtile.modelData.appId)
                    active: ex.open
                    focused: wtile.selected || (windowRow.activeFocus && windowRow.currentIndex === wtile.index)
                    Drag.active: drag.active
                    Drag.keys: ["mios-dock-window"]
                    Drag.hotSpot.x: width / 2
                    Drag.hotSpot.y: height / 2
                }
                DragHandler {
                    id: drag
                    target: pv
                    onActiveChanged: {
                        ex.dragging = active
                        if (active) ex.selectedWindow = wtile.modelData
                        else { pv.Drag.drop(); pv.x = 0; pv.y = 0 }
                    }
                }
                TapHandler {
                    acceptedButtons: Qt.LeftButton
                    onTapped: { if (wtile.selected) windowRow.activateWindow(wtile.modelData); else { ex.selectedWindow = wtile.modelData; windowRow.currentIndex = wtile.index } }
                }
                TapHandler {
                    acceptedButtons: Qt.RightButton
                    onTapped: { ex.selectedWindow = wtile.modelData; windowRow.currentIndex = wtile.index; ex.section = 1 }
                }
            }
        }

        Text {
            visible: ex.status !== ""
            width: parent.width
            text: ex.status
            textFormat: Text.PlainText
            elide: Text.ElideRight
            color: ex.theme.muted
            font.family: ex.theme.fontFamily
            font.pixelSize: ex.theme.smallFontSize
        }

        // apps
        GridView {
            id: appGrid
            width: Math.max(ex.theme.appTile, Math.floor(parent.width / ex.theme.appTile) * ex.theme.appTile)
            anchors.horizontalCenter: parent.horizontalCenter
            height: Math.max(0, col.height - y)
            cellWidth: ex.theme.appTile
            cellHeight: ex.theme.appTile
            clip: true
            keyNavigationEnabled: true
            model: {
                if (!ex.open) return []
                const starred = ex.dockRoot.starredApplications || []
                const rest = (ex.dockRoot.applications || []).filter(e => !starred.some(s => s.id === e.id))
                return starred.concat(rest).filter(e => !ex.query
                    || (e.name + " " + e.genericName + " " + (e.keywords || []).join(" ")).toLowerCase().indexOf(ex.query) >= 0)
            }
            Keys.onReturnPressed: ex.dockRoot.launch(model[currentIndex], null)
            Keys.onEnterPressed: ex.dockRoot.launch(model[currentIndex], null)
            Keys.onUpPressed: event => { if (currentIndex < Math.floor(width / cellWidth)) ex.section = 2; else moveCurrentIndexUp() }
            delegate: Item {
                id: tile
                required property var modelData
                required property int index
                width: appGrid.cellWidth
                height: appGrid.cellHeight
                readonly property bool focused: appGrid.activeFocus && appGrid.currentIndex === index
                readonly property bool starred: DockLamp.isStarred(modelData)
                readonly property bool labelTruncated: label.truncated
                readonly property real labelPixelSize: label.fontInfo.pixelSize
                readonly property int labelLines: label.lineCount
                Rectangle {
                    anchors.fill: parent
                    radius: Glass.cornerFor(Math.min(width, height))
                    color: tile.focused ? ex.theme.withAlpha(ex.theme.accent, ex.theme.selectionOpacity)
                         : tileHit.containsMouse ? ex.theme.withAlpha(ex.theme.accent, ex.theme.hoverOpacity) : "transparent"
                }
                Image {
                    id: tileIcon
                    anchors.top: parent.top
                    anchors.topMargin: ex.gap / 2
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: ex.theme.appIcon
                    height: width
                    source: QS.Quickshell.iconPath(tile.modelData.icon, true)
                    fillMode: Image.PreserveAspectFit
                }
                Text {
                    visible: tile.starred
                    anchors.top: parent.top
                    anchors.right: parent.right
                    anchors.margins: ex.gap / 2
                    text: "★"
                    color: ex.theme.cursor
                    font.pixelSize: ex.theme.smallFontSize
                }
                // The full name on up to label_lines lines, shrunk (not cut) to fit; the tooltip carries it whole.
                Text {
                    id: label
                    anchors.top: tileIcon.bottom
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.leftMargin: 2
                    anchors.rightMargin: 2
                    text: tile.modelData.name
                    textFormat: Text.PlainText
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignTop
                    wrapMode: Text.WrapAtWordBoundaryOrAnywhere
                    maximumLineCount: DockConfig.labelLines
                    fontSizeMode: Text.Fit
                    minimumPixelSize: DockConfig.labelMinFont
                    font.pixelSize: ex.theme.smallFontSize
                    font.family: ex.theme.fontFamily
                    elide: Text.ElideRight
                    color: ex.theme.fg
                }
                MouseArea {
                    id: tileHit
                    anchors.fill: parent
                    hoverEnabled: true
                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                    cursorShape: Qt.PointingHandCursor
                    onClicked: mouse => {
                        if (mouse.button === Qt.RightButton) { ex.dockRoot.openAppMenu(tile.modelData, tile); return }
                        ex.dockRoot.launch(tile.modelData, null)
                    }
                }
                Timer { id: tipDelay; interval: DockConfig.tooltipDelay; running: tileHit.containsMouse || tile.focused; onTriggered: tip.show(tile) }
                Connections { target: tileHit; function onContainsMouseChanged() { if (!tileHit.containsMouse && !tile.focused) tip.hideFor(tile) } }
                onFocusedChanged: if (!focused && !tileHit.containsMouse) tip.hideFor(tile)
            }
        }
    }

    // One tooltip for the grid: the full app name above the hovered or focused tile, kept inside the panel.
    Rectangle {
        id: tip
        property var owner: null
        visible: owner !== null
        z: 10
        width: Math.min(ex.width, tipText.implicitWidth + 2 * ex.gap)
        height: tipText.implicitHeight + ex.gap
        radius: Glass.cornerFor(height)
        color: ex.theme.withAlpha(ex.theme.bg, ex.theme.notificationOpacity)
        function show(t) {
            owner = t
            const p = t.mapToItem(ex, t.width / 2, 0)
            x = Math.max(0, Math.min(ex.width - width, p.x - width / 2))
            y = Math.max(0, p.y - height)
        }
        function hideFor(t) { if (owner === t) owner = null }
        Text {
            id: tipText
            anchors.centerIn: parent
            width: Math.min(implicitWidth, ex.width - 2 * ex.gap)
            text: tip.owner ? tip.owner.modelData.name : ""
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            color: ex.theme.fg
            font.family: ex.theme.fontFamily
            font.pixelSize: ex.theme.fontSize
        }
    }

    // Controller / scripted navigation (mios-inputd quickshell: actions): `quickshell ipc call dock <fn>`.
    IpcHandler {
        target: "dock"
        enabled: ex.open
        function next(): void { ex.section = (ex.section + 1) % 4 }
        function previous(): void { ex.section = (ex.section + 3) % 4 }
        function osk(): void { ex.oskToggled() }
        function state(): string {
            return JSON.stringify({ open: ex.open, section: ex.section, windows: ex.windows.length, targets: ex.targets.map(t => t.id),
                                    selected: ex.selectedWindow ? ex.selectedWindow.address : "", osk: ex.oskShown, status: ex.status,
                                    width: ex.width, height: ex.height })
        }
        function select(address: string): void {
            const i = ex.windows.findIndex(w => w.address === DockLamp.normAddress(address))
            if (i >= 0) { ex.selectedWindow = ex.windows[i]; windowRow.currentIndex = i }
        }
        function move(target: string): void { ex.moveTo(ex.targets.find(t => t.id === target)) }
        // Every visible app label: its name, lines used, the font it was fitted to and whether it is still cut.
        function labels(): string {
            const out = []
            for (let i = 0; i < appGrid.count; i++) {
                const it = appGrid.itemAtIndex(i)
                if (it) out.push({ name: it.modelData.name, lines: it.labelLines, px: it.labelPixelSize, truncated: it.labelTruncated })
            }
            return JSON.stringify(out)
        }
        function focusApp(index: int): void { ex.section = 3; appGrid.currentIndex = index }
        function tooltip(): string { return tip.owner ? tip.owner.modelData.name : "" }
    }
}
