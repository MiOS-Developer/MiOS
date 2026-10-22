// AI-hint: Focused-app pills in the top bar row, at the screen's top-left, left of the centred status
// island: the app's name and, when the app exports one, its real menubar (File, View, Help ...).
// Menus follow the appmenu-exporter contract ($XDG_RUNTIME_DIR/mios/appmenu.json, activation via
// `miosd appmenu activate <app_id> <item_id>`): pills only when the snapshot is for the focused app,
// otherwise just the name. Nothing is invented. Each dropdown grows out of its pill (MeltPopup.qml).
// The row starts at the screen's left margin, or right of any window control tab (the hyprbars
// glass tab that protrudes above a window's top-left, [desktop.hyprland.hyprbars]) that reaches into
// the bar row: the focused window's under reveal_on "focus*", every visible window's otherwise.
// Geometry comes from the theme bridge (hyprbars), Hyprland's client list and the live reveal_on.
// Reserves no space; the status island owns the top exclusive zone. IPC target "appmenu".
// AI-related: usr/share/mios/quickshell/MeltPopup.qml, usr/share/mios/quickshell/PanelWindow.qml,
// usr/share/mios/quickshell/ShellGeometry.qml, mios.toml [desktop.topbar]
import QtQuick
import Quickshell as QS
import Quickshell.Hyprland
import Quickshell.Io
import Quickshell.Wayland

QS.PanelWindow {
    id: menus
    required property QtObject shellState
    property QtObject theme: Theme {}
    readonly property var bar: theme.topbar || ({})
    readonly property var top: ToplevelManager.activeToplevel
    readonly property string appId: top ? top.appId : ""
    readonly property var entry: appId ? QS.DesktopEntries.heuristicLookup(appId) : null
    readonly property string appName: !appId ? "" : (entry ? entry.name : appId)
    property int openMenu: -1

    // ---- exported menus (appmenu-exporter contract) ----------------------------------------------
    property var snap: ({})
    FileView {
        path: QS.Quickshell.env("MIOS_APPMENU_JSON") || ((QS.Quickshell.env("XDG_RUNTIME_DIR") || "/run/user/1000") + "/mios/appmenu.json")
        watchChanges: true
        preload: true
        printErrors: false
        onFileChanged: reload()
        onLoaded: menus.parse(text())
        onTextChanged: menus.parse(text())
        onLoadFailed: menus.snap = ({})
    }
    function parse(t) { try { snap = JSON.parse(t) || ({}) } catch (e) { snap = ({}) } }
    readonly property var menuItems: {
        const s = snap
        if (!appId || !s || s.app_id !== appId || !Array.isArray(s.menus)) return []
        return s.menus.filter(m => m && m.label && Array.isArray(m.items) && m.items.length)
    }
    onMenuItemsChanged: openMenu = -1
    onAppIdChanged: openMenu = -1

    // ---- window control tabs in the bar row -----------------------------------------------------
    // hyprbars announces every tab's live state on Hyprland's event socket (hyprbarstab>>ADDRESS,RAISED,
    // X,Y,W,H,INSET: RAISED flips to 1 as the tab starts to rise and to 0 as it starts to melt, the extent
    // is global layout px with its fillets). The pills step right only while a tab is actually up in the
    // bar row and slide back as it melts, over the tab's own fadeIn timing; focus alone moves nothing.
    // The tab has priority over the pills, also when it hangs inside a fullscreen window (INSET 1) under the
    // overlaid row; the status island and dock have priority over the tab.
    readonly property var tab: theme.hyprbars || ({})
    readonly property var hyprMonitor: screen ? Hyprland.monitorFor(screen) : null
    property var tabs: ({})   // address -> {x, y, w, h}, raised protruding tabs only
    function onTabEvent(data) {
        const f = String(data).split(",")
        if (f.length < 7) return
        const next = Object.assign({}, tabs)
        // Every raised tab counts, inset ones too: a fullscreen window's tab drops down INSIDE the window
        // (INSET 1) at the very top of the screen, right where the pills sit over it, so it must push them
        // exactly like a maximized window's protruding tab. An inset tab of an ordinary window hangs below
        // the bar row and is dropped by the row-band check in tabRects, so it still never moves the pills.
        if (f[1] === "1") next[f[0]] = { x: +f[2], y: +f[3], w: +f[4], h: +f[5] }
        else delete next[f[0]]
        tabs = next
    }
    readonly property var tabRects: {
        const m = hyprMonitor
        if (!m) return []
        const out = []
        for (const k in tabs) {
            const t = tabs[k]
            const r = { left: t.x - m.x, right: t.x + t.w - m.x, top: t.y - m.y }
            if (r.top >= theme.edgeMargin + theme.topHeight || r.top + t.h <= 0 || r.right <= 0 || r.left >= m.width) continue
            out.push(r)
        }
        return out.sort((a, b) => a.left - b.left)
    }
    // The row starts at the left margin and steps right past every raised tab that would sit under it;
    // tabs further right leave the row alone.
    readonly property real rowLeft: {
        let left = theme.edgeMargin
        for (const r of tabRects) {
            if (r.left > left + pills.implicitWidth) break
            if (r.right >= left) left = Math.ceil(r.right + theme.edgeMargin / 2)
        }
        return left
    }
    // rowLeft animated with the tab (hyprbars rises/melts on Hyprland's fadeIn: speed x 100 ms)
    property real shownLeft: rowLeft
    Behavior on shownLeft { NumberAnimation { duration: menus.tab.reveal_ms !== undefined ? menus.tab.reveal_ms : 450; easing.type: Easing.OutCubic } }
    Connections {
        target: Hyprland
        function onRawEvent(event) {
            if (event.name === "hyprbarstab") menus.onTabEvent(event.data)
            else if (event.name === "closewindow") menus.onTabEvent(String(event.data) + ",0,0,0,0,0,0")
        }
    }

    // Fullscreen edge reveal (EdgeReveal.qml): hidden under a fullscreen window, back on the overlay
    // layer above it with the status island while the pointer is at the top edge.
    property QtObject reveal: null
    readonly property bool overFullscreen: !!reveal && reveal.topActive
    onOpenMenuChanged: if (reveal) reveal.menuPopup = openMenu >= 0
    onVisibleChanged: if (!visible && reveal && reveal.menuHovered) Qt.callLater(() => { if (!menus.visible && menus.reveal) menus.reveal.menuHovered = false })
    WlrLayershell.namespace: "mios-appmenu"
    WlrLayershell.layer: overFullscreen ? WlrLayer.Overlay : WlrLayer.Top
    anchors.top: true
    anchors.left: true
    margins.top: theme.edgeMargin
    margins.left: shownLeft
    exclusiveZone: 0
    exclusionMode: QS.ExclusionMode.Ignore   // the true top edge, same row as the status island
    // Mobile outputs ([desktop.mobile]) carry the app menu on the window shoulder dot, not here.
    visible: appName !== "" && bar.app_menu !== false && !(screen && Mobile.isMobile(screen.name)) && (!overFullscreen || reveal.topShown)
    // Never runs under the island: what does not fit before it is clipped.
    readonly property var island: ShellGeometry.islands[screen ? screen.name : ""] || null
    readonly property real maxWidth: Math.max(1, island ? island.x - rowLeft - theme.edgeMargin : screen.width / 2 - rowLeft)
    implicitWidth: Math.max(1, Math.min(pills.implicitWidth, maxWidth))
    implicitHeight: theme.topHeight
    color: "transparent"

    component Pill: Rectangle {
        id: pillBox
        property alias text: label.text
        property bool strong: false
        property bool active: false
        signal activated()
        signal hovered()
        implicitWidth: label.implicitWidth + 2 * menus.theme.edgeMargin + 4
        implicitHeight: menus.theme.topHeight
        radius: Glass.cornerFor(height)
        color: active ? menus.theme.withAlpha(menus.theme.accent, menus.theme.selectionOpacity)
             : hit.containsMouse && !strong ? menus.theme.withAlpha(menus.theme.accent, menus.theme.hoverOpacity)
             : Glass.fill
        border.width: 0
        Text {
            id: label
            anchors.centerIn: parent
            // App-supplied strings (app name, menu labels) are never markup: AutoText would
            // render "<b>", "<img src=...>" or "<a href>" from any registered client.
            textFormat: Text.PlainText
            color: menus.theme.fg
            font.family: menus.theme.fontFamily
            font.pixelSize: menus.theme.fontSize
            font.bold: pillBox.strong
        }
        MouseArea {
            id: hit
            anchors.fill: parent
            enabled: !pillBox.strong
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: pillBox.activated()
            onContainsMouseChanged: if (containsMouse) pillBox.hovered()
        }
    }

    Row {
        id: pills
        spacing: menus.theme.edgeMargin / 2
        HoverHandler { onHoveredChanged: { const h = hovered; Qt.callLater(() => { if (menus.reveal) menus.reveal.menuHovered = menus.visible && h }) } }
        Pill { text: menus.appName; strong: true }
        Repeater {
            id: pillRepeater
            model: menus.menuItems
            delegate: Pill {
                required property var modelData
                required property int index
                text: modelData.label
                active: menus.openMenu === index
                onActivated: menus.openMenu = (menus.openMenu === index ? -1 : index)
                // Sliding across the row while a menu is open switches menus, like a menubar.
                onHovered: if (menus.openMenu >= 0 && menus.openMenu !== index) menus.openMenu = index
            }
        }
    }

    // ---- dropdown, grown out of its pill ---------------------------------------------------------
    readonly property var openMenuData: openMenu >= 0 && openMenu < menuItems.length ? menuItems[openMenu] : null
    property Item lastPill: null
    property var expanded: ({})
    onOpenMenuDataChanged: {
        expanded = ({})
        if (openMenuData) lastPill = pillRepeater.itemAt(openMenu)
    }
    function flatten(items, depth, out) {
        for (const it of items) {
            if (!it || !it.label) continue
            out.push({ item: it, depth: depth })
            if (it.submenu && it.submenu.length && expanded[it.id]) flatten(it.submenu, depth + 1, out)
        }
        return out
    }
    readonly property var rows: openMenuData ? flatten(openMenuData.items, 0, []) : []

    MeltPopup {
        id: dropdown
        anchor.window: menus
        theme: menus.theme
        trigger: menus.lastPill
        align: "left"
        open: menus.openMenuData !== null
        duration: menus.bar.popup_ms !== undefined ? menus.bar.popup_ms : 200
        hostScreenX: menus.margins.left
        panelWidth: Math.max(180, menuColumn.widest + 2 * menus.theme.edgeMargin)
        panelHeight: Math.max(menuColumn.implicitHeight,
                              menus.rows.reduce((h, r) => h + (r.item.separator_before ? 9 : 0) + (menus.theme.searchRowHeight - 12), 0)) + 2 * menus.theme.edgeMargin
        Column {
            id: menuColumn
            x: menus.theme.edgeMargin
            y: menus.theme.edgeMargin
            width: dropdown.panelWidth - 2 * menus.theme.edgeMargin
            readonly property real widest: {
                let w = 0
                for (let i = 0; i < children.length; i++) if (children[i].labelWidth) w = Math.max(w, children[i].labelWidth)
                return w
            }
            Repeater {
                model: menus.rows
                delegate: Column {
                    id: rowBox
                    required property var modelData
                    readonly property var it: modelData.item
                    readonly property bool hasSub: !!(it.submenu && it.submenu.length)
                    readonly property real labelWidth: itemLabel.implicitWidth + modelData.depth * 14 + 2 * menus.theme.edgeMargin + (hasSub ? 24 : 0)
                    width: menuColumn.width
                    Item {
                        visible: !!rowBox.it.separator_before
                        width: parent.width
                        height: visible ? 9 : 0
                        Rectangle { anchors.centerIn: parent; width: parent.width - 16; height: 1; color: menus.theme.outlineColor }
                    }
                    Rectangle {
                        width: parent.width
                        height: menus.theme.searchRowHeight - 12
                        radius: height / 2
                        color: itemHit.containsMouse && rowBox.it.enabled !== false
                               ? menus.theme.withAlpha(menus.theme.accent, menus.theme.hoverOpacity) : "transparent"
                        Text {
                            id: itemLabel
                            x: menus.theme.edgeMargin + rowBox.modelData.depth * 14
                            anchors.verticalCenter: parent.verticalCenter
                            text: rowBox.it.label
                            textFormat: Text.PlainText // app-supplied label: no rich-text injection
                            color: rowBox.it.enabled === false ? menus.theme.muted : menus.theme.fg
                            font.family: menus.theme.fontFamily
                            font.pixelSize: menus.theme.fontSize
                        }
                        Text {
                            visible: rowBox.hasSub
                            anchors.right: parent.right
                            anchors.rightMargin: menus.theme.edgeMargin
                            anchors.verticalCenter: parent.verticalCenter
                            text: menus.expanded[rowBox.it.id] ? "\u{f0140}" : "\u{f0142}"
                            textFormat: Text.PlainText
                            color: menus.theme.subtle
                            font.family: menus.theme.fontFamily
                            font.pixelSize: menus.theme.fontSize
                        }
                        MouseArea {
                            id: itemHit
                            anchors.fill: parent
                            hoverEnabled: true
                            enabled: rowBox.it.enabled !== false
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                if (rowBox.hasSub) {
                                    const next = Object.assign({}, menus.expanded)
                                    next[rowBox.it.id] = !next[rowBox.it.id]
                                    menus.expanded = next
                                    return
                                }
                                QS.Quickshell.execDetached(["/usr/bin/miosd", "appmenu", "activate", menus.snap.app_id, String(rowBox.it.id)])
                                menus.openMenu = -1
                            }
                        }
                    }
                }
            }
        }
    }
    HyprlandFocusGrab {
        windows: [menus, dropdown]
        active: dropdown.open
        onCleared: menus.openMenu = -1
    }

    IpcHandler {
        target: "appmenu"
        enabled: !Hyprland.focusedMonitor || !menus.screen || Hyprland.focusedMonitor.name === menus.screen.name
        function openMenu(index: int): void { menus.openMenu = index }
        function close(): void { menus.openMenu = -1 }
        function state(): string {
            return JSON.stringify({ app_id: menus.appId, app_name: menus.appName, visible: menus.visible,
                                    menus: menus.menuItems.map(m => m.label), open_menu: menus.openMenu,
                                    dropdown: dropdown.progress, width: menus.width, row_left: menus.rowLeft,
                                    tabs: menus.tabRects, shown_left: menus.shownLeft })
        }
    }
}
