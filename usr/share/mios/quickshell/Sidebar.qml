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
    property real viewportWidth: 0 // optional native nested output width
    property QtObject theme: Theme {}
    readonly property var dockWindow: dock
    readonly property var phoneRect: dock.lampGeometry.phone_rect || [root.monX, root.monY, 48, 48]

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
    property bool tilePreviewHold: false
    property bool held: false
    property bool revealed: !(autohide || fullscreenReveal) || held || showDrawer || dockPopups.previewOpen || dockPopups.menuOpen
    function updateHold() {
        if (hotHovered || dockHovered || dockPopups.previewHovered || dockPopups.menuOpen) { hideTimer.stop(); held = true }
        else if (held) hideTimer.restart()
    }
    onHotHoveredChanged: updateHold()
    onDockHoveredChanged: updateHold()
    Timer {
        id: hideTimer
        interval: root.theme.dockHideDelay
        onTriggered: root.held = root.hotHovered || root.dockHovered || dockPopups.previewHovered || dockPopups.menuOpen
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
            || root.ownsClass(entry, appId)
    }
    function ownsClass(entry, cls) {
        if (!entry || !cls) return false
        const s = String(cls).trim()
        if (!s) return false
        const app = QS.DesktopEntries.heuristicLookup(s)
        if (app && app.id === entry.id) return true
        const classes = DockLamp.classesForEntry(entry, [])
        const lo = s.toLowerCase()
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
            if (h && h.wayland && h.wayland.appId === t.appId && (!t.title || h.title === t.title)) return DockLamp.addressOf(h)
        }
        return ""
    }
    function isToplevelMinimized(t) {
        const addr = addressForToplevel(t)
        if (addr) return DockLamp.isSpecialAddress(addr)
        if (!t || !t.appId) return false
        return DockLamp.minimizedWindows.some(m => (m.appId === t.appId || m.initialClass === t.appId)
            && (!t.title || !m.title || m.title === t.title))
    }
    function runningWindow(entry) {
        return ToplevelManager.toplevels.values.find(t => root.owns(entry, t) && !root.isToplevelMinimized(t))
    }
    function minimizedWindowFor(entry) {
        return dock.allMinimizedTiles.find(m => root.ownsClass(entry, m.appId) || root.ownsClass(entry, m.initialClass))
    }
    function waylandToplevelForAddress(addr) {
        const a = DockLamp.normAddress(addr)
        if (!a) return null
        const tls = Hyprland.toplevels ? Hyprland.toplevels.values : []
        for (let i = 0; i < tls.length; i++) {
            const h = tls[i]
            if (h && DockLamp.addressOf(h) === a && h.wayland) return h.wayland
        }
        return null
    }
    function windowsForEntry(entry) {
        if (!entry) return []
        const list = []
        const seenAddr = {}
        const wls = ToplevelManager.toplevels ? ToplevelManager.toplevels.values : []
        for (let i = 0; i < wls.length; i++) {
            const t = wls[i]
            if (!t || !root.owns(entry, t)) continue
            const addr = root.addressForToplevel(t)
            const min = root.isToplevelMinimized(t)
            if (addr) seenAddr[addr] = true
            list.push({
                address: addr,
                title: t.title || entry.name || t.appId || "Window",
                appId: t.appId || entry.id || "",
                minimized: min,
                activated: !!t.activated && !min,
                toplevel: t
            })
        }
        const mins = dock.allMinimizedTiles || []
        for (let j = 0; j < mins.length; j++) {
            const m = mins[j]
            if (!m || !m.address || seenAddr[m.address]) continue
            if (root.ownsClass(entry, m.appId) || root.ownsClass(entry, m.initialClass)) {
                seenAddr[m.address] = true
                list.push({
                    address: m.address,
                    title: m.title || entry.name || m.appId || m.address,
                    appId: m.appId || m.initialClass || "",
                    minimized: true,
                    activated: false,
                    toplevel: root.waylandToplevelForAddress(m.address)
                })
            }
        }
        return list
    }
    function windowsForMinTile(m) {
        if (!m || !m.address) return []
        return [{
            address: m.address,
            title: m.title || m.appId || m.address,
            appId: m.appId || m.initialClass || "",
            minimized: true,
            activated: false,
            toplevel: root.waylandToplevelForAddress(m.address)
        }]
    }
    property var starredApplications: {
        const out = []
        const ids = DockLamp.starredApps || []
        for (let i = 0; i < ids.length; i++) {
            const e = QS.DesktopEntries.heuristicLookup(ids[i])
            if (e && !e.noDisplay && !out.some(x => x.id === e.id)) out.push(e)
        }
        return out
    }
    function launch(entry, iconRect) {
        if (!entry) return
        const outName = screen ? screen.name : ""
        const rect = (Array.isArray(iconRect) && iconRect.length === 4) ? iconRect : dock.rectForEntry(entry)
        AppLaunch.launch(entry, outName, rect)
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
        readonly property real screenW: (root.viewportWidth > 0 ? root.viewportWidth : screen.width) / ui
        readonly property real screenH: screen.height / ui
        property int hoveredAppIndex: -1
        property int hoveredMinIndex: -1
        readonly property real hoverMagScale: 1.15

        readonly property var allMinimizedTiles: {
            const outName = root.screen ? root.screen.name : ""
            const monId = root.hyprMonitor ? root.hyprMonitor.id : -1
            const primary = !Hyprland.focusedMonitor || (root.screen && Hyprland.focusedMonitor.name === root.screen.name)
            return DockLamp.minimizedForOutput(outName, monId, primary)
        }
        property var dockApps: {
            let result = [], ids = new Set()
            const synthUnpinned = QS.Quickshell.env("MIOS_DOCK_SYNTH_UNPINNED") === "1"
            function add(e) {
                if (e && !e.noDisplay && !ids.has(e.id)) { ids.add(e.id); result.push(e) }
            }
            const pinnedList = DockLamp.effectivePinned(theme.dockPinned)
            pinnedList.forEach(id => add(QS.DesktopEntries.heuristicLookup(id)))
            ToplevelManager.toplevels.values.forEach(t => {
                if (!result.some(e => root.owns(e, t))) {
                    const looked = QS.DesktopEntries.heuristicLookup(t.appId)
                    if (looked) add(looked)
                    else if (t.appId && !root.isToplevelMinimized(t) && (synthUnpinned || /^lamptest\./.test(t.appId))) add({
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
            dock.allMinimizedTiles.forEach(m => {
                const cls = m.appId || m.initialClass || ""
                if (cls && !result.some(e => root.ownsClass(e, m.appId) || root.ownsClass(e, m.initialClass))) {
                    const looked = QS.DesktopEntries.heuristicLookup(cls) || (m.initialClass ? QS.DesktopEntries.heuristicLookup(m.initialClass) : null)
                    if (looked) add(looked)
                }
            })
            return result
        }
        readonly property var minimizedTiles: {
            return dock.allMinimizedTiles.filter(m => !dock.dockApps.some(e => root.ownsClass(e, m.appId) || root.ownsClass(e, m.initialClass)))
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
        WlrLayershell.layer: WlrLayer.Overlay
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
                dock.allMinimizedTiles.forEach(m => {
                    if (root.ownsClass(e, m.appId) || root.ownsClass(e, m.initialClass)) {
                        if (m.appId) extra.push(m.appId)
                        if (m.initialClass) extra.push(m.initialClass)
                    }
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
            list.push({kind: "phone", scale: 1})
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
                // One clamped global radius for the dock and drawer. Expanding the drawer changes
                // its size, not the glass material or the radius of its corners.
                radius: Glass.cornerFor(height)
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

                    // Starred / Favourites bar (shown when not filtering by search and at least 1 app is starred, or in reorder mode)
                    Column {
                        id: favouritesSection
                        width: parent.width
                        spacing: dock.theme.edgeMargin / 2
                        visible: !search.text && (root.starredApplications.length > 0 || DockLamp.reorderMode)

                        Row {
                            width: parent.width
                            spacing: dock.theme.edgeMargin

                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                text: "★ Starred / Favourites"
                                color: dock.theme.cursor
                                font.family: dock.theme.fontFamily
                                font.pixelSize: dock.theme.smallFontSize
                                font.bold: true
                            }
                            Rectangle {
                                visible: DockLamp.reorderMode
                                height: 22
                                width: doneReorderLabel.implicitWidth + 16
                                radius: dock.theme.radius / 2
                                color: doneReorderMouse.containsMouse
                                    ? dock.theme.withAlpha(dock.theme.success, dock.theme.selectionOpacity)
                                    : dock.theme.withAlpha(dock.theme.accent, dock.theme.hoverOpacity)
                                Text {
                                    id: doneReorderLabel
                                    anchors.centerIn: parent
                                    text: "✓ Done Re-organizing"
                                    color: dock.theme.fg
                                    font.family: dock.theme.fontFamily
                                    font.pixelSize: dock.theme.smallFontSize
                                }
                                MouseArea {
                                    id: doneReorderMouse
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: DockLamp.reorderMode = false
                                }
                            }
                        }

                        Row {
                            id: favouritesRow
                            width: parent.width
                            height: root.starredApplications.length > 0 ? dock.theme.appTile : 28
                            spacing: dock.theme.edgeMargin

                            Text {
                                visible: root.starredApplications.length === 0
                                anchors.verticalCenter: parent.verticalCenter
                                text: "Right-click any app to Star / Favourite or Pin to Dock"
                                color: dock.theme.muted
                                font.family: dock.theme.fontFamily
                                font.pixelSize: dock.theme.smallFontSize
                            }

                            Repeater {
                                model: root.starredApplications
                                delegate: Item {
                                    id: favTile
                                    required property var modelData
                                    required property int index
                                    width: dock.theme.appTile
                                    height: dock.theme.appTile

                                    Rectangle {
                                        anchors.fill: parent
                                        anchors.margins: dock.theme.edgeMargin / 2
                                        radius: dock.theme.radius
                                        color: favMouse.containsMouse
                                            ? dock.theme.withAlpha(dock.theme.accent, dock.theme.hoverOpacity)
                                            : dock.theme.withAlpha(dock.theme.subtle, dock.theme.outlineOpacity)
                                        border.width: DockLamp.reorderMode ? 1 : 0
                                        border.color: dock.theme.cursor
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
                                        anchors.top: parent.top
                                        anchors.right: parent.right
                                        anchors.margins: dock.theme.edgeMargin
                                        text: "★"
                                        color: dock.theme.cursor
                                        font.family: dock.theme.fontFamily
                                        font.pixelSize: dock.theme.smallFontSize
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
                                        id: favMouse
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        acceptedButtons: Qt.LeftButton | Qt.RightButton
                                        pressAndHoldInterval: 450
                                        cursorShape: Qt.PointingHandCursor
                                        onPressAndHold: DockLamp.reorderMode = !DockLamp.reorderMode
                                        onClicked: mouse => {
                                            if (mouse.button === Qt.RightButton) {
                                                const pt = favTile.mapToItem(null, mouse.x, mouse.y)
                                                const winLeft = Math.round((dock.screen.width - dock.width) / 2)
                                                const winTop = Math.round(dock.screen.height - dock.margins.bottom - dock.height)
                                                dockPopups.openContextMenu({
                                                    entry: modelData,
                                                    appId: modelData.id,
                                                    title: modelData.name,
                                                    icon: modelData.icon,
                                                    windows: root.windowsForEntry(modelData),
                                                    rect: dock.rectForEntry(modelData),
                                                    sourceKind: "icon",
                                                    screenX: winLeft + pt.x,
                                                    screenY: winTop + pt.y
                                                })
                                                return
                                            }
                                            if (DockLamp.reorderMode) return
                                            root.launch(modelData, null)
                                        }
                                    }

                                    // Re-organize overlay controls on starred tile (◀ ★ ▶)
                                    Row {
                                        visible: DockLamp.reorderMode
                                        anchors.top: parent.top
                                        anchors.horizontalCenter: parent.horizontalCenter
                                        anchors.topMargin: 4
                                        spacing: 4
                                        z: 4

                                        Rectangle {
                                            width: 18
                                            height: 18
                                            radius: 9
                                            color: dock.theme.withAlpha(dock.theme.bg, 0.85)
                                            Text { anchors.centerIn: parent; text: "◀"; color: dock.theme.fg; font.pixelSize: 10 }
                                            MouseArea {
                                                anchors.fill: parent
                                                onClicked: DockLamp.moveStar(favTile.modelData, -1)
                                            }
                                        }
                                        Rectangle {
                                            width: 18
                                            height: 18
                                            radius: 9
                                            color: dock.theme.withAlpha(dock.theme.error, 0.85)
                                            Text { anchors.centerIn: parent; text: "×"; color: dock.theme.fg; font.pixelSize: 11 }
                                            MouseArea {
                                                anchors.fill: parent
                                                onClicked: DockLamp.unstarApp(favTile.modelData)
                                            }
                                        }
                                        Rectangle {
                                            width: 18
                                            height: 18
                                            radius: 9
                                            color: dock.theme.withAlpha(dock.theme.bg, 0.85)
                                            Text { anchors.centerIn: parent; text: "▶"; color: dock.theme.fg; font.pixelSize: 10 }
                                            MouseArea {
                                                anchors.fill: parent
                                                onClicked: DockLamp.moveStar(favTile.modelData, 1)
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    GridView {
                        id: appGrid
                        width: parent.width
                        height: Math.max(0, dock.drawerHeight - dock.theme.dockHeight - search.height
                                         - (favouritesSection.visible ? favouritesSection.height + dock.theme.edgeMargin : 0)
                                         - 4 * dock.theme.edgeMargin)
                        cellWidth: dock.theme.appTile
                        cellHeight: dock.theme.appTile
                        clip: true
                        model: dock.drawerProgress > 0
                            ? root.applications.filter(e => !search.text || (e.name + " " + e.genericName + " " + e.keywords.join(" ")).toLowerCase().includes(search.text.toLowerCase()))
                            : []
                        delegate: Item {
                            id: gridTile
                            required property var modelData
                            width: appGrid.cellWidth
                            height: appGrid.cellHeight
                            readonly property bool isStarredApp: DockLamp.isStarred(modelData)
                            readonly property bool isPinnedApp: DockLamp.isPinned(modelData, dock.theme.dockPinned)
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
                                visible: gridTile.isStarredApp || gridTile.isPinnedApp
                                anchors.top: parent.top
                                anchors.right: parent.right
                                anchors.margins: dock.theme.edgeMargin
                                text: (gridTile.isStarredApp ? "★" : "") + (gridTile.isPinnedApp ? "•" : "")
                                color: dock.theme.cursor
                                font.family: dock.theme.fontFamily
                                font.pixelSize: dock.theme.smallFontSize
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
                                acceptedButtons: Qt.LeftButton | Qt.RightButton
                                pressAndHoldInterval: 450
                                cursorShape: Qt.PointingHandCursor
                                onPressAndHold: DockLamp.reorderMode = !DockLamp.reorderMode
                                onClicked: mouse => {
                                    if (mouse.button === Qt.RightButton) {
                                        const pt = gridTile.mapToItem(null, mouse.x, mouse.y)
                                        const winLeft = Math.round((dock.screen.width - dock.width) / 2)
                                        const winTop = Math.round(dock.screen.height - dock.margins.bottom - dock.height)
                                        dockPopups.openContextMenu({
                                            entry: modelData,
                                            appId: modelData.id,
                                            title: modelData.name,
                                            icon: modelData.icon,
                                            windows: root.windowsForEntry(modelData),
                                            rect: dock.rectForEntry(modelData),
                                            sourceKind: "icon",
                                            screenX: winLeft + pt.x,
                                            screenY: winTop + pt.y
                                        })
                                        return
                                    }
                                    root.launch(modelData, null)
                                }
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
                            readonly property var allWindows: root.windowsForEntry(modelData)
                            readonly property bool isPinnedTile: DockLamp.isPinned(modelData, dock.theme.dockPinned)
                            readonly property bool isStarredTile: DockLamp.isStarred(modelData)
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
                                border.width: DockLamp.reorderMode && appTile.isPinnedTile ? 1 : 0
                                border.color: dock.theme.cursor
                            }
                            Image {
                                anchors.centerIn: parent
                                width: dock.theme.appIcon
                                height: width
                                source: QS.Quickshell.iconPath(modelData.icon, true)
                                fillMode: Image.PreserveAspectFit
                            }
                            // Star badge on dock icon when starred/favourited
                            Text {
                                visible: appTile.isStarredTile && !DockLamp.reorderMode
                                anchors.top: parent.top
                                anchors.right: parent.right
                                anchors.topMargin: 2
                                anchors.rightMargin: 2
                                text: "★"
                                color: dock.theme.cursor
                                font.family: dock.theme.fontFamily
                                font.pixelSize: Math.max(8, dock.theme.smallFontSize - 2)
                            }
                            Rectangle {
                                visible: appTile.running !== undefined || appTile.minimizedWin !== undefined
                                anchors.bottom: parent.bottom
                                anchors.bottomMargin: dock.theme.edgeMargin / 2
                                anchors.horizontalCenter: parent.horizontalCenter
                                width: dock.theme.edgeMargin + (appTile.allWindows.length > 1 ? 6 : 0)
                                height: 3
                                radius: dock.theme.radius
                                color: appTile.running && appTile.running.activated ? dock.theme.cursor
                                     : (appTile.running ? dock.theme.subtle : dock.theme.warning)
                            }
                            // Long-highlight (hover >= 400ms) live window preview timer
                            Timer {
                                id: hoverPreviewTimer
                                interval: 400
                                repeat: false
                                onTriggered: {
                                    if (tileMouse.containsMouse && !DockLamp.reorderMode && !dock.expanded && appTile.allWindows.length > 0) {
                                        const r = dock.rectForEntry(appTile.modelData)
                                        const localCx = (r[0] - root.monX) + r[2] / 2
                                        dockPopups.showPreview({
                                            entry: appTile.modelData,
                                            title: appTile.modelData.name,
                                            icon: appTile.modelData.icon,
                                            centerX: localCx,
                                            windows: appTile.allWindows,
                                            rect: r,
                                            sourceKind: "icon"
                                        })
                                    }
                                }
                            }
                            MouseArea {
                                id: tileMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                acceptedButtons: Qt.LeftButton | Qt.RightButton
                                pressAndHoldInterval: 450
                                cursorShape: DockLamp.reorderMode ? Qt.SizeAllCursor : Qt.PointingHandCursor
                                property real pressStartX: 0
                                property bool draggedPin: false
                                onPressed: mouse => {
                                    pressStartX = mouse.x
                                    draggedPin = false
                                }
                                onPressAndHold: mouse => {
                                    if (mouse.button === Qt.LeftButton) {
                                        hoverPreviewTimer.stop()
                                        dockPopups.hidePreviewImmediate()
                                        DockLamp.reorderMode = !DockLamp.reorderMode
                                    }
                                }
                                onPositionChanged: mouse => {
                                    if (pressed && DockLamp.reorderMode && appTile.isPinnedTile) {
                                        const dx = mouse.x - pressStartX
                                        const step = Math.max(24, appTile.width)
                                        if (dx >= step) {
                                            if (DockLamp.movePin(appTile.modelData, 1, dock.theme.dockPinned)) {
                                                pressStartX = mouse.x
                                                draggedPin = true
                                            }
                                        } else if (dx <= -step) {
                                            if (DockLamp.movePin(appTile.modelData, -1, dock.theme.dockPinned)) {
                                                pressStartX = mouse.x
                                                draggedPin = true
                                            }
                                        }
                                    }
                                }
                                onContainsMouseChanged: {
                                    if (containsMouse) {
                                        dock.hoveredAppIndex = appTile.index
                                        root.tilePreviewHold = true
                                        if (appTile.allWindows.length > 0 && !DockLamp.reorderMode && !dock.expanded) {
                                            hoverPreviewTimer.restart()
                                        }
                                    } else {
                                        if (dock.hoveredAppIndex === appTile.index) dock.hoveredAppIndex = -1
                                        root.tilePreviewHold = false
                                        hoverPreviewTimer.stop()
                                        dockPopups.scheduleHidePreview()
                                    }
                                }
                                onClicked: mouse => {
                                    if (draggedPin) {
                                        draggedPin = false
                                        return
                                    }
                                    if (mouse.button === Qt.RightButton) {
                                        hoverPreviewTimer.stop()
                                        const r = dock.rectForEntry(modelData)
                                        const localCx = (r[0] - root.monX) + r[2] / 2
                                        dockPopups.openContextMenu({
                                            entry: modelData,
                                            appId: modelData.id || modelData.appId,
                                            title: modelData.name,
                                            icon: modelData.icon,
                                            windows: appTile.allWindows,
                                            rect: r,
                                            sourceKind: "icon",
                                            screenX: localCx,
                                            screenY: (r[1] - root.monY) - 12
                                        })
                                        return
                                    }
                                    if (DockLamp.reorderMode) return
                                    dockPopups.hidePreviewImmediate()
                                    if (appTile.minimizedWin && (!appTile.running || appTile.running.activated)) {
                                        DockLamp.restore(appTile.minimizedWin.address, "icon", dock.rectForEntry(modelData))
                                    } else if (appTile.running) {
                                        appTile.running.activate()
                                    } else {
                                        root.launch(modelData, dock.rectForEntry(modelData))
                                    }
                                }
                            }
                            // Inline reorganize badges (◀ × ▶) when DockLamp.reorderMode is active
                            Row {
                                visible: DockLamp.reorderMode && appTile.isPinnedTile
                                anchors.top: parent.top
                                anchors.horizontalCenter: parent.horizontalCenter
                                anchors.topMargin: 2
                                spacing: 2
                                z: 5

                                Rectangle {
                                    width: 14
                                    height: 14
                                    radius: 7
                                    color: dock.theme.withAlpha(dock.theme.bg, 0.9)
                                    Text { anchors.centerIn: parent; text: "◀"; color: dock.theme.fg; font.pixelSize: 8 }
                                    MouseArea {
                                        anchors.fill: parent
                                        onClicked: DockLamp.movePin(appTile.modelData, -1, dock.theme.dockPinned)
                                    }
                                }
                                Rectangle {
                                    width: 14
                                    height: 14
                                    radius: 7
                                    color: dock.theme.withAlpha(dock.theme.error, 0.9)
                                    Text { anchors.centerIn: parent; text: "×"; color: dock.theme.fg; font.pixelSize: 9 }
                                    MouseArea {
                                        anchors.fill: parent
                                        onClicked: DockLamp.unpinApp(appTile.modelData, dock.theme.dockPinned)
                                    }
                                }
                                Rectangle {
                                    width: 14
                                    height: 14
                                    radius: 7
                                    color: dock.theme.withAlpha(dock.theme.bg, 0.9)
                                    Text { anchors.centerIn: parent; text: "▶"; color: dock.theme.fg; font.pixelSize: 8 }
                                    MouseArea {
                                        anchors.fill: parent
                                        onClicked: DockLamp.movePin(appTile.modelData, 1, dock.theme.dockPinned)
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
                            readonly property var tileWindows: root.windowsForMinTile(modelData)
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
                            Timer {
                                id: minPreviewTimer
                                interval: 400
                                repeat: false
                                onTriggered: {
                                    if (minMouse.containsMouse && !DockLamp.reorderMode && !dock.expanded) {
                                        const addr = minTile.modelData.address
                                        const r = dock.rectForAddress(addr)
                                        const localCx = (r[0] - root.monX) + r[2] / 2
                                        dockPopups.showPreview({
                                            entry: minTile.entry,
                                            title: minTile.modelData.title || minTile.modelData.appId || addr,
                                            icon: minTile.iconName,
                                            centerX: localCx,
                                            windows: minTile.tileWindows,
                                            rect: r,
                                            sourceKind: "minimized"
                                        })
                                    }
                                }
                            }
                            MouseArea {
                                id: minMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                acceptedButtons: Qt.LeftButton | Qt.RightButton
                                pressAndHoldInterval: 450
                                cursorShape: Qt.PointingHandCursor
                                onPressAndHold: DockLamp.reorderMode = !DockLamp.reorderMode
                                onContainsMouseChanged: {
                                    if (containsMouse) {
                                        dock.hoveredMinIndex = minTile.index
                                        root.tilePreviewHold = true
                                        if (!DockLamp.reorderMode && !dock.expanded) minPreviewTimer.restart()
                                    } else {
                                        if (dock.hoveredMinIndex === minTile.index) dock.hoveredMinIndex = -1
                                        root.tilePreviewHold = false
                                        minPreviewTimer.stop()
                                        dockPopups.scheduleHidePreview()
                                    }
                                }
                                onClicked: mouse => {
                                    const addr = minTile.modelData.address
                                    const rect = dock.rectForAddress(addr)
                                    if (mouse.button === Qt.RightButton) {
                                        minPreviewTimer.stop()
                                        const localCx = (rect[0] - root.monX) + rect[2] / 2
                                        dockPopups.openContextMenu({
                                            entry: minTile.entry,
                                            appId: minTile.entry ? minTile.entry.id : minTile.modelData.appId,
                                            title: minTile.modelData.title || minTile.modelData.appId || addr,
                                            icon: minTile.iconName,
                                            windows: minTile.tileWindows,
                                            rect: rect,
                                            sourceKind: "minimized",
                                            screenX: localCx,
                                            screenY: (rect[1] - root.monY) - 12
                                        })
                                        return
                                    }
                                    dockPopups.hidePreviewImmediate()
                                    DockLamp.restore(addr, "minimized", rect)
                                }
                            }
                        }
                    }
                    Item {
                        id: phoneTile
                        width: dock.theme.dockHeight - dock.theme.edgeMargin
                        height: dock.theme.dockHeight
                        Rectangle {
                            anchors.fill: parent; anchors.topMargin: dock.theme.edgeMargin/2; anchors.bottomMargin: dock.theme.edgeMargin/2
                            radius: Glass.cornerFor(height)
                            color: phoneHit.containsMouse ? dock.theme.withAlpha(dock.theme.accent, dock.theme.hoverOpacity) : "transparent"
                        }
                        Text {
                            anchors.centerIn: parent; text: "\u{f011c}"
                            color: root.shellState.remoteAttached ? dock.theme.success : dock.theme.fg
                            font.family: dock.theme.fontFamily; font.pixelSize: dock.theme.appIcon
                        }
                        Timer { id: phoneHover; interval: 400; onTriggered: dockPopups.showPhone(root.phoneRect, false) }
                        MouseArea {
                            id: phoneHit
                            anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                            acceptedButtons: Qt.LeftButton | Qt.RightButton
                            onContainsMouseChanged: {
                                root.tilePreviewHold = containsMouse
                                if (containsMouse) phoneHover.restart()
                                else { phoneHover.stop(); dockPopups.scheduleHidePreview() }
                            }
                            onClicked: { phoneHover.stop(); dockPopups.showPhone(root.phoneRect, true) }
                        }
                    }
                }
            }
        }
        Connections {
            target: dock
            function onExpandedChanged() {
                if (dock.expanded) {
                    dockPopups.hidePreviewImmediate()
                    dockPopups.closeContextMenu()
                    search.forceActiveFocus()
                }
            }
        }
    }

    DockPopup {
        id: dockPopups
        screen: root.screen
        theme: root.theme
        dockRoot: root
    }
}
