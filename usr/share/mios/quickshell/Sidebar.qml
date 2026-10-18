// AI-hint: MiOS dock scope: the app dock (always shown; auto-hidden under a fullscreen window, revealed
// over it on the overlay layer from the bottom edge when [theme.shell].dock_fullscreen_reveal; optional
// auto-hide via DockHotZone when [theme.shell].dock_autohide = true), and the app drawer, which
// is the dock surface itself morphing (width, height, radius) out of the dock into the drawer -- the
// full glass pane size when drawer_full_pane, else the drawer_*_fraction size -- and back on close. Tiles come only from real desktop
// entries, running toplevels, and minimized windows on special:minimized (DockLamp.qml). Every size/behaviour knob is SSOT [theme.shell] via Theme.qml.
// On a mobile output ([desktop.mobile], Mobile.qml) the dock never auto-hides: it always reserves its
// row, so the full-size window ends above it and the dock never overlaps it. Mobile.uiScale(screen.name)
// scales the dock/drawer surface (window, margins, exclusive zone, contents) where the compositor left
// the output below the [desktop.scaling] policy scale.
// The dock's material and corner are the window controls' glass (Glass.qml, [desktop.hyprland.hyprbars]).
// Magic lamp contract (/home/mios/mios-desktop-work-1/lamp-contract.md, DockLamp.qml): publishes per-output
// icon rects and minimized window rects in global layout coordinates to $XDG_RUNTIME_DIR/mios-dock-icons.json,
// records pending dock launches to $XDG_RUNTIME_DIR/mios-lamp-launch.json before spawning, and routes dock
// minimize/restore clicks through the feature-detected hl.plugin.mios_lamp dispatchers.
// AI-related: usr/share/mios/mios.toml [theme.shell] [desktop.mobile] [desktop.hyprland.lamp], usr/share/mios/quickshell/Theme.qml,
// usr/share/mios/quickshell/Mobile.qml, usr/share/mios/quickshell/Glass.qml, usr/share/mios/quickshell/DockLamp.qml,
// usr/share/mios/quickshell/DockHotZone.qml, usr/share/mios/quickshell/AppDrawer.qml, tests/test-mios-lamp.py
import QtQuick
import Quickshell as QS
import Quickshell.Hyprland
import Quickshell.Wayland

QS.Scope {
    id: root
    required property var screen
    required property QtObject shellState
    property QtObject theme: Theme {}

    property bool showDrawer: shellState.drawerOpen && (!shellState.drawerScreen || shellState.drawerScreen === screen.name)
    property bool paneDrawer: theme.drawerFullPane   // drawer target size: full pane vs fraction
    // Fullscreen on this output's visible workspaces (EdgeReveal.qml); the foreign-toplevel flag is the
    // fallback when the shell runs without it.
    property QtObject reveal: null
    property bool fullscreen: reveal ? reveal.fullscreen
                                     : ToplevelManager.toplevels.values.some(t => t.fullscreen && t.screens.indexOf(screen) >= 0)
    // Under a fullscreen window the dock auto-hides: the bottom-edge hot zone reveals it on the overlay
    // layer above the window, reserving nothing ([theme.shell] dock_fullscreen_reveal).
    readonly property bool fullscreenReveal: fullscreen && theme.dockFullscreenReveal
    // Mobile output: auto-hide is off so the dock keeps its reserved row below the window.
    readonly property bool mobile: !!screen && Mobile.isMobile(screen.name)
    readonly property bool autohide: theme.dockAutohide && !mobile
    property var applications: QS.DesktopEntries.applications.values.filter(e => !e.noDisplay).sort((a,b) => a.name.localeCompare(b.name))
    readonly property var hyprMonitor: screen ? Hyprland.monitorFor(screen) : null
    readonly property real monX: (hyprMonitor && typeof hyprMonitor.x === "number") ? hyprMonitor.x : (screen ? screen.x : 0)
    readonly property real monY: (hyprMonitor && typeof hyprMonitor.y === "number") ? hyprMonitor.y : (screen ? screen.y : 0)

    // Auto-hide: either hover surface holds the dock open; the hide delay covers the gap between them.
    property bool hotHovered: false
    property bool dockHovered: false
    property bool held: false
    property bool revealed: !(autohide || fullscreenReveal) || held || showDrawer
    function updateHold() {
        if (hotHovered || dockHovered) { hideTimer.stop(); held = true }
        else if (held) hideTimer.restart()
    }
    onHotHoveredChanged: updateHold()
    onDockHoveredChanged: updateHold()
    Timer {
        id: hideTimer
        interval: root.theme.dockHideDelay
        onTriggered: root.held = root.hotHovered || root.dockHovered
    }

    // A window belongs to an entry by desktop id, or when a wrapper entry (mios-zen launching a
    // Flatpak) names the window's app id as its StartupWMClass or icon.
    function owns(entry, t) {
        if (!entry || !t) return false
        const appId = t.appId || ""
        if (!appId) return false
        const app = QS.DesktopEntries.heuristicLookup(appId)
        return (app && app.id === entry.id) || entry.id === appId || entry.id === (appId + ".desktop")
            || entry.startupClass === appId || entry.icon === appId || entry.appId === appId
    }
    function ownsClass(entry, cls) {
        if (!entry || !cls) return false
        const classes = DockLamp.classesForEntry(entry, [])
        const lo = String(cls).toLowerCase()
        return classes.some(c => String(c).toLowerCase() === lo)
    }
    // Match a foreign toplevel to its Hyprland client address (0x...) when available.
    function addressForToplevel(t) {
        if (!t) return ""
        const tls = Hyprland.toplevels ? Hyprland.toplevels.values : []
        for (let i = 0; i < tls.length; i++) {
            const h = tls[i]
            if (h && h.wayland === t) return DockLamp.addressOf(h)
        }
        for (let i = 0; i < tls.length; i++) {
            const h = tls[i]
            const o = h ? h.lastIpcObject : null
            if (o && o.class === t.appId && (!t.title || o.title === t.title)) return DockLamp.addressOf(h)
        }
        return ""
    }
    function isToplevelMinimized(t) {
        const addr = addressForToplevel(t)
        return addr ? DockLamp.isSpecialAddress(addr) : false
    }
    function runningWindow(entry) {
        return ToplevelManager.toplevels.values.find(t => root.owns(entry, t) && !root.isToplevelMinimized(t))
    }
    function minimizedWindowFor(entry) {
        return dock.minimizedTiles.find(m => root.ownsClass(entry, m.appId) || root.ownsClass(entry, m.initialClass))
    }
    function launch(entry, iconRect) {
        if (!entry) return
        const outName = screen ? screen.name : ""
        const rect = (Array.isArray(iconRect) && iconRect.length === 4) ? iconRect : dock.rectForEntry(entry)
        DockLamp.recordLaunch(entry, outName, rect)
        if (typeof entry.execute === "function") entry.execute()
        shellState.drawerOpen = false
    }

    DockHotZone {
        screen: root.screen
        theme: root.theme
        active: (root.autohide && !root.fullscreen) || root.fullscreenReveal
        overlay: root.fullscreenReveal
        onHoveredChanged: root.hotHovered = hovered
    }

    QS.PanelWindow {
        id: dock
        screen: root.screen
        property QtObject theme: root.theme
        property bool expanded: root.showDrawer
        // Extra shell scale for this output (Mobile.uiScale: policy scale / scale the compositor applied).
        // Sizes below are shell units; the window is `ui` times larger in compositor px and the
        // contents are scaled to match (`content`), so the dock keeps the policy size on an output the
        // compositor left unscaled.
        readonly property real ui: screen ? Mobile.uiScale(screen.name) : 1
        readonly property real screenW: screen.width / ui
        readonly property real screenH: screen.height / ui
        property int hoveredAppIndex: -1
        property int hoveredMinIndex: -1
        readonly property real hoverMagScale: 1.15

        property var dockApps: {
            let result = [], ids = new Set()
            const synthUnpinned = QS.Quickshell.env("MIOS_DOCK_SYNTH_UNPINNED") === "1"
            function add(e) {
                if (e && !e.noDisplay && !ids.has(e.id)) { ids.add(e.id); result.push(e) }
            }
            theme.dockPinned.forEach(id => add(QS.DesktopEntries.heuristicLookup(id)))
            ToplevelManager.toplevels.values.forEach(t => {
                if (root.isToplevelMinimized(t)) return
                if (!result.some(e => root.owns(e, t))) {
                    const looked = QS.DesktopEntries.heuristicLookup(t.appId)
                    if (looked) add(looked)
                    else if (t.appId && (synthUnpinned || /^lamptest\./.test(t.appId))) add({
                        id: t.appId + ".desktop",
                        appId: t.appId,
                        name: t.title || t.appId,
                        genericName: "",
                        keywords: [],
                        icon: t.appId,
                        startupClass: t.appId,
                        noDisplay: false,
                        execute: function() {}
                    })
                }
            })
            return result
        }
        readonly property var minimizedTiles: {
            const outName = root.screen ? root.screen.name : ""
            const monId = root.hyprMonitor ? root.hyprMonitor.id : -1
            const primary = !Hyprland.focusedMonitor || (root.screen && Hyprland.focusedMonitor.name === root.screen.name)
            return DockLamp.minimizedForOutput(outName, monId, primary)
        }

        property real drawerProgress: expanded ? 1 : 0
        // Full pane: the area under the top bar inset by drawer_pane_margin_px, down to the dock's own edge margin.
        property int drawerWidth: root.paneDrawer ? screenW - 2 * theme.drawerPaneMargin
                                                  : Math.round(screenW * theme.drawerWidthFraction)
        property int drawerHeight: root.paneDrawer
            ? screenH - (theme.topHeight + 2 * theme.edgeMargin) - theme.drawerPaneMargin - theme.edgeMargin
            : Math.round(screenH * theme.drawerHeightFraction)
        // Glass parity (Glass.qml): the dock is the window-control tab's material -- [desktop.hyprland.hyprbars]
        // pill_palette at pill_alpha over the compositor's hyprglass layer glass -- morphing to the drawer's
        // [theme.shell] drawer_fill_opacity_pct as it opens.
        property real fillOpacity: Glass.fillAlpha + ((root.paneDrawer ? theme.drawerOpacity : Glass.fillAlpha) - Glass.fillAlpha) * drawerProgress
        Behavior on drawerProgress {
            NumberAnimation {
                duration: (dock.theme.topbar && dock.theme.topbar.popup_ms !== undefined) ? dock.theme.topbar.popup_ms : 200
                easing.type: Easing.OutCubic
            }
        }
        // "mios-dock-remote" where [desktop.mobile.remote_effects] applies (cheaper hyprglass preset, same look).
        WlrLayershell.namespace: Mobile.layerNamespace("mios-dock", root.screen ? root.screen.name : "")
        WlrLayershell.layer: root.fullscreenReveal ? WlrLayer.Overlay : WlrLayer.Top
        WlrLayershell.keyboardFocus: expanded ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None
        anchors.bottom: true
        margins.bottom: Math.round(theme.edgeMargin * ui)
        visible: !reserveRebuild && ((root.revealed && (!root.fullscreen || root.fullscreenReveal)) || expanded || drawerProgress > 0)
        implicitWidth: Math.round(ui * Math.min(screenW - 2 * theme.edgeMargin,
                              tiles.implicitWidth + 2 * theme.edgeMargin +
                              (drawerWidth - tiles.implicitWidth - 2 * theme.edgeMargin) * drawerProgress))
        implicitHeight: Math.round(ui * (theme.dockHeight + (drawerHeight - theme.dockHeight) * drawerProgress))
        // Windows sit above the dock; the drawer morph overlays them (the zone never grows, so nothing reflows).
        // Auto-hide reserves nothing here: DockHotZone holds one edge margin so windows stop where the top gap does.
        // EdgeReveal's reserved-rows guard re-asserts the zone (reserveNudge, reserveRebuild) when the compositor lost it.
        exclusiveZone: zoneFor(root.autohide || !visible || root.fullscreen, theme.dockHeight, theme.edgeMargin, ui)
                       + (reserveNudge > 0 && !(root.autohide || !visible || root.fullscreen) ? reserveNudge : 0)
        function zoneFor(none, h, margin, scale) { return none ? 0 : Math.round((h + margin) * scale) }
        property int reserveNudge: 0
        property bool reserveRebuild: false
        // What the compositor then reserves on the bottom edge (the zone plus margins.bottom), from the current inputs.
        function reserveExpectedNow() {
            const z = zoneFor(root.autohide || !visible || root.fullscreen, theme.dockHeight, theme.edgeMargin, ui)
            return z > 0 ? z + Math.round(theme.edgeMargin * ui) : 0
        }
        function reserveDiag() {
            return { zone: exclusiveZone, mode: exclusionMode, layer: WlrLayershell.layer, visible: visible, nudge: reserveNudge,
                     fullscreen: root.fullscreen, autohide: root.autohide, ui: ui, height: theme.dockHeight, margin: theme.edgeMargin }
        }
        function nudgeReserve() { reserveNudge = 1; reserveNudgeBack.restart() }
        function recreateReserve() { reserveRebuild = true; Qt.callLater(() => { dock.reserveRebuild = false }) }
        Timer { id: reserveNudgeBack; interval: 250; onTriggered: dock.reserveNudge = 0 }
        color: "transparent"
        onVisibleChanged: {
            if (!visible) { root.dockHovered = false; dock.hoveredAppIndex = -1; dock.hoveredMinIndex = -1 }
            dock.publishLampGeometry()
        }

        // ---- Magic-lamp geometry publication (contract (a): global layout coordinates) ------------------------------
        function buildTileDescriptors() {
            const list = [{ kind: "drawer", scale: 1 }]
            const apps = dock.dockApps || []
            for (let i = 0; i < apps.length; i++) {
                const e = apps[i]
                if (!e) continue
                const rawId = String(e.id || e.appId || "")
                const appId = DockLamp.stripDesktop(rawId)
                const desktopId = rawId.endsWith(".desktop") ? rawId : rawId + ".desktop"
                const extra = []
                ToplevelManager.toplevels.values.forEach(t => {
                    if (root.owns(e, t) && t.appId) extra.push(t.appId)
                })
                list.push({
                    kind: "icon",
                    app_id: appId,
                    desktop_id: desktopId,
                    classes: DockLamp.classesForEntry(e, extra),
                    scale: dock.hoveredAppIndex === i ? dock.hoverMagScale : 1
                })
            }
            const mins = dock.minimizedTiles || []
            for (let j = 0; j < mins.length; j++) {
                const m = mins[j]
                if (!m || !m.address) continue
                list.push({
                    kind: "minimized",
                    address: m.address,
                    scale: dock.hoveredMinIndex === j ? dock.hoverMagScale : 1
                })
            }
            return list
        }

        property var lampGeometry: DockLamp.computeOutputGeometry({
            monX: root.monX,
            monY: root.monY,
            screenLogicalW: root.screen ? root.screen.width : 1920,
            screenLogicalH: root.screen ? root.screen.height : 1080,
            ui: dock.ui,
            edgeMargin: dock.theme.edgeMargin,
            dockHeight: dock.theme.dockHeight,
            dockRevealPx: dock.theme.dockRevealPx,
            visible: dock.visible,
            edge: "bottom",
            tiles: dock.buildTileDescriptors()
        })

        function publishLampGeometry() {
            if (!root.screen || !root.screen.name) return
            DockLamp.setOutput(root.screen.name, dock.lampGeometry)
        }

        function rectForEntry(entry) {
            const g = dock.lampGeometry
            if (!g) return [0, 0, 48, 48]
            if (entry && Array.isArray(g.icons)) {
                const targetId = DockLamp.stripDesktop(entry.id || entry.appId || "")
                for (const ic of g.icons) {
                    if (ic.app_id === targetId || ic.desktop_id === entry.id) return ic.rect
                }
            }
            return g.minimized_slot || g.dock_rect || [0, 0, 48, 48]
        }

        function rectForAddress(address) {
            const a = DockLamp.normAddress(address)
            const g = dock.lampGeometry
            if (g && Array.isArray(g.minimized)) {
                for (let i = 0; i < g.minimized.length; i++) {
                    const m = g.minimized[i]
                    if (m && m.address === a) return m.rect
                }
            }
            return g ? (g.minimized_slot || g.dock_rect || [0, 0, 48, 48]) : [0, 0, 48, 48]
        }

        onLampGeometryChanged: publishLampGeometry()
        Component.onCompleted: { publishLampGeometry(); if (root.reveal) root.reveal.bottomSurface = dock }
        Component.onDestruction: {
            if (root.reveal && root.reveal.bottomSurface === dock) root.reveal.bottomSurface = null
            if (root.screen && root.screen.name) DockLamp.removeOutput(root.screen.name)
        }

        // Clicking any other surface closes the drawer (the morph then runs in reverse). The grab arms
        // once the morph finishes: armed mid-resize it cleared at once and the drawer never opened.
        HyprlandFocusGrab {
            windows: [dock]
            active: dock.expanded && dock.drawerProgress >= 1
            onCleared: root.shellState.drawerOpen = false
        }

        Item {
            id: content
            width: dock.width / dock.ui
            height: dock.height / dock.ui
            scale: dock.ui
            transformOrigin: Item.TopLeft
            Rectangle {
                anchors.fill: parent
                // Radius morphs with the surface: dock corner -> pane corner.
                // Global window rounding (the same key the windows and their controls tab use); no outline:
                // the tab has none, its rim is the compositor's hyprglass fresnel.
                radius: Glass.rounding * (1 + dock.drawerProgress)
                color: dock.theme.withAlpha(Glass.fillHex, dock.fillOpacity)
                clip: true
                focus: dock.expanded
                Keys.onEscapePressed: root.shellState.drawerOpen = false
                HoverHandler { onHoveredChanged: root.dockHovered = hovered }

                Column {
                    anchors.fill: parent
                    anchors.margins: dock.theme.edgeMargin
                    spacing: dock.theme.edgeMargin
                    visible: dock.drawerProgress > 0
                    opacity: dock.drawerProgress
                    TextInput {
                        id: search
                        width: parent.width
                        height: dock.theme.appIcon
                        color: dock.theme.fg
                        selectionColor: dock.theme.accent
                        selectedTextColor: dock.theme.fg
                        font.family: dock.theme.fontFamily
                        clip: true
                        Text {
                            anchors.fill: parent
                            visible: !search.text && !search.activeFocus
                            text: "Search installed applications"
                            color: dock.theme.muted
                            font: search.font
                        }
                        Keys.onEscapePressed: root.shellState.drawerOpen = false
                        onAccepted: if (appGrid.count > 0) root.launch(appGrid.model[0], null)
                    }
                    GridView {
                        id: appGrid
                        width: parent.width
                        height: Math.max(0, dock.drawerHeight - dock.theme.dockHeight - search.height - 4 * dock.theme.edgeMargin)
                        cellWidth: dock.theme.appTile
                        cellHeight: dock.theme.appTile
                        clip: true
                        model: dock.drawerProgress > 0
                            ? root.applications.filter(e => !search.text || (e.name + " " + e.genericName + " " + e.keywords.join(" ")).toLowerCase().includes(search.text.toLowerCase()))
                            : []
                        delegate: Item {
                            required property var modelData
                            width: appGrid.cellWidth
                            height: appGrid.cellHeight
                            Rectangle {
                                anchors.fill: parent
                                anchors.margins: dock.theme.edgeMargin / 2
                                radius: dock.theme.radius
                                color: appMouse.containsMouse ? dock.theme.withAlpha(dock.theme.accent, dock.theme.hoverOpacity) : "transparent"
                            }
                            Image {
                                anchors.top: parent.top
                                anchors.topMargin: dock.theme.edgeMargin
                                anchors.horizontalCenter: parent.horizontalCenter
                                width: dock.theme.appIcon
                                height: width
                                source: QS.Quickshell.iconPath(modelData.icon, true)
                                fillMode: Image.PreserveAspectFit
                            }
                            Text {
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.bottom: parent.bottom
                                anchors.margins: dock.theme.edgeMargin
                                text: modelData.name
                                horizontalAlignment: Text.AlignHCenter
                                elide: Text.ElideRight
                                color: dock.theme.fg
                                font.family: dock.theme.fontFamily
                            }
                            MouseArea {
                                id: appMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: root.launch(modelData, null)
                            }
                        }
                    }
                }

                Row {
                    id: tiles
                    anchors.bottom: parent.bottom
                    anchors.horizontalCenter: parent.horizontalCenter
                    height: dock.theme.dockHeight
                    spacing: dock.theme.edgeMargin

                    Item {
                        width: dock.theme.dockHeight - dock.theme.edgeMargin
                        height: dock.theme.dockHeight
                        Text {
                            anchors.centerIn: parent
                            text: "▦"
                            color: dock.theme.fg
                            font.family: dock.theme.fontFamily
                            font.pixelSize: dock.theme.appIcon
                        }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: root.shellState.toggleDrawer()
                        }
                    }
                    Repeater {
                        id: appRepeater
                        model: dock.dockApps
                        delegate: Item {
                            id: appTile
                            required property var modelData
                            required property int index
                            property var running: root.runningWindow(modelData)
                            property var minimizedWin: root.minimizedWindowFor(modelData)
                            width: dock.theme.dockHeight - dock.theme.edgeMargin
                            height: dock.theme.dockHeight
                            scale: tileMouse.containsMouse ? dock.hoverMagScale : 1.0
                            Behavior on scale {
                                NumberAnimation { duration: 120; easing.type: Easing.OutCubic }
                            }
                            Rectangle {
                                anchors.fill: parent
                                anchors.topMargin: dock.theme.edgeMargin / 2
                                anchors.bottomMargin: dock.theme.edgeMargin / 2
                                radius: dock.theme.radius
                                color: tileMouse.containsMouse ? dock.theme.withAlpha(dock.theme.accent, dock.theme.hoverOpacity) : "transparent"
                            }
                            Image {
                                anchors.centerIn: parent
                                width: dock.theme.appIcon
                                height: width
                                source: QS.Quickshell.iconPath(modelData.icon, true)
                                fillMode: Image.PreserveAspectFit
                            }
                            Rectangle {
                                visible: appTile.running !== undefined || appTile.minimizedWin !== undefined
                                anchors.bottom: parent.bottom
                                anchors.bottomMargin: dock.theme.edgeMargin / 2
                                anchors.horizontalCenter: parent.horizontalCenter
                                width: dock.theme.edgeMargin
                                height: 3
                                radius: dock.theme.radius
                                color: appTile.running && appTile.running.activated ? dock.theme.cursor
                                     : (appTile.running ? dock.theme.subtle : dock.theme.warning)
                            }
                            MouseArea {
                                id: tileMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                acceptedButtons: Qt.LeftButton | Qt.RightButton
                                cursorShape: Qt.PointingHandCursor
                                onContainsMouseChanged: {
                                    if (containsMouse) dock.hoveredAppIndex = appTile.index
                                    else if (dock.hoveredAppIndex === appTile.index) dock.hoveredAppIndex = -1
                                }
                                onClicked: mouse => {
                                    if (mouse.button === Qt.RightButton) {
                                        if (appTile.running) {
                                            const addr = root.addressForToplevel(appTile.running)
                                            DockLamp.minimize(addr)
                                        } else if (appTile.minimizedWin) {
                                            DockLamp.restore(appTile.minimizedWin.address, "icon", dock.rectForEntry(modelData))
                                        }
                                        return
                                    }
                                    if (appTile.running) {
                                        if (appTile.running.activated && appTile.minimizedWin) {
                                            DockLamp.restore(appTile.minimizedWin.address, "icon", dock.rectForEntry(modelData))
                                        } else {
                                            appTile.running.activate()
                                        }
                                    } else if (appTile.minimizedWin) {
                                        DockLamp.restore(appTile.minimizedWin.address, "icon", dock.rectForEntry(modelData))
                                    } else {
                                        root.launch(modelData, dock.rectForEntry(modelData))
                                    }
                                }
                            }
                        }
                    }
                    Repeater {
                        id: minimizedRepeater
                        model: dock.minimizedTiles
                        delegate: Item {
                            id: minTile
                            required property var modelData
                            required property int index
                            readonly property var entry: modelData.appId ? QS.DesktopEntries.heuristicLookup(modelData.appId) : null
                            readonly property string iconName: entry && entry.icon ? entry.icon : (modelData.appId || "")
                            readonly property string iconSource: iconName ? QS.Quickshell.iconPath(iconName, true) : ""
                            width: dock.theme.dockHeight - dock.theme.edgeMargin
                            height: dock.theme.dockHeight
                            scale: minMouse.containsMouse ? dock.hoverMagScale : 1.0
                            Behavior on scale {
                                NumberAnimation { duration: 120; easing.type: Easing.OutCubic }
                            }
                            Rectangle {
                                anchors.fill: parent
                                anchors.topMargin: dock.theme.edgeMargin / 2
                                anchors.bottomMargin: dock.theme.edgeMargin / 2
                                radius: dock.theme.radius
                                color: minMouse.containsMouse
                                    ? dock.theme.withAlpha(dock.theme.accent, dock.theme.hoverOpacity)
                                    : dock.theme.withAlpha(dock.theme.subtle, dock.theme.outlineOpacity)
                            }
                            Image {
                                id: minIcon
                                visible: minTile.iconSource !== "" && status === Image.Ready
                                anchors.centerIn: parent
                                width: dock.theme.appIcon
                                height: width
                                opacity: 0.85
                                source: minTile.iconSource
                                fillMode: Image.PreserveAspectFit
                            }
                            Text {
                                visible: !minIcon.visible
                                anchors.centerIn: parent
                                text: (minTile.modelData.title || minTile.modelData.appId || "▫").slice(0, 2)
                                color: dock.theme.fg
                                font.family: dock.theme.fontFamily
                                font.pixelSize: dock.theme.fontSize
                            }
                            Rectangle {
                                anchors.bottom: parent.bottom
                                anchors.bottomMargin: dock.theme.edgeMargin / 2
                                anchors.horizontalCenter: parent.horizontalCenter
                                width: dock.theme.edgeMargin + 4
                                height: 3
                                radius: dock.theme.radius
                                color: dock.theme.warning
                            }
                            MouseArea {
                                id: minMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onContainsMouseChanged: {
                                    if (containsMouse) dock.hoveredMinIndex = minTile.index
                                    else if (dock.hoveredMinIndex === minTile.index) dock.hoveredMinIndex = -1
                                }
                                onClicked: {
                                    const addr = minTile.modelData.address
                                    const rect = dock.rectForAddress(addr)
                                    DockLamp.restore(addr, "minimized", rect)
                                }
                            }
                        }
                    }
                }
            }
        }
        Connections {
            target: dock
            function onExpandedChanged() { if (dock.expanded) search.forceActiveFocus() }
        }
    }
}
