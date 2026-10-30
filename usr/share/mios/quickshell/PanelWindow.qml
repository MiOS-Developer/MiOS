// AI-hint: MiOS status island. Width follows its live contents: drawer mark, workspace, network/sound/battery, system
// monitor (Sysmon.qml: CPU, RAM, GPU, hottest temperature, network rate, [desktop.topbar.sysmon]; hover or tap opens
// its details), frames per second ("<output> FPS/<focused window> FPS", the window part from mios-pacing's
// focused-window meter, hidden when unknown), brief date (opens a native calendar),
// SSOT 12/24-hour clock, and a power menu with settings and account. Every command and format
// resolves from [desktop.topbar] through the SSOT bridge. Popups grow out of their trigger and melt
// back into it (MeltPopup.qml, [desktop.topbar] popup_ms); IPC target "topbar" opens them headlessly.
// Mobile layout profile ([desktop.mobile], Mobile.qml): on a portrait or phone-sized output the surface spans the
// output's top row and the island fills the room between the window-controls tabs of the windows shown there, one
// global edge margin clear of each tab ([theme.shell] edge_margin_px), at the global top margin and bar height, with
// the desktop island's glass and corner rule. The tab boxes follow the hyprbars mobile rule from the same SSOT keys
// (Glass.mobileTabWidth / mobileTabInset, bar_height) on each window's goal geometry (Mobile.qml refreshes it); with
// no window there the island spans the row between the edge margins. When tabs come, the island moves out of their
// way at once on the [desktop.mobile] island_motion leaf of [desktop.hyprland] animations (the curve the tabs rise
// on); when they go, it widens on the same curve once the island_hold leaves (the window and its tabs melting) have
// run, so it never runs under a tab. Under a window the window's controls are the desktop controls
// tab split in two around the island: a narrow tab rising from the window's top-left (window menu, incl. the app's
// exported menu) and one from its top-right (close), always shown, focused or not, bar_height tall and hit as a
// [desktop.hyprland.hyprbars.mobile] touch_target. The row is the bar height at the global top margin and reserves
// one edge margin above the windows, so each tab rises from its window's top edge beside the island. When the projection put hyprbars' mobile tabs on the
// window (Mobile.hyprbarsTabs), the compositor draws them -- the same renderer, glass and buttons as the desktop
// tab -- and the shell only anchors their popups; otherwise ControlsTab.qml draws them from the same keys and
// rules (fallback). Mobile.uiScale(screen.name) > 1 (the compositor left the output below the
// [desktop.scaling] policy scale) scales the whole row -- window size, margins, exclusive zone and contents.
// Glass parity (Glass.qml): the island and the tabs are the desktop window controls' material and buttons
// ([desktop.hyprland.hyprbars] + its hyprglass preset, global rounding); nothing here hand-types a colour or
// radius. The input region on mobile is the island (and the shell-drawn tabs): taps on compositor tabs reach hyprbars.
// AI-related: usr/share/mios/quickshell/Theme.qml, usr/share/mios/quickshell/MeltPopup.qml,
// usr/share/mios/quickshell/Emerge.qml, usr/share/mios/quickshell/Mobile.qml, usr/share/mios/quickshell/Glass.qml,
// usr/share/mios/quickshell/ControlsTab.qml, usr/share/mios/quickshell/ControlDot.qml,
// usr/share/mios/quickshell/Sysmon.qml, usr/share/mios/quickshell/SysmonItems.qml, usr/share/mios/quickshell/SysmonDetails.qml,
// usr/share/mios/mios.toml [desktop.topbar] [desktop.topbar.sysmon] [desktop.mobile] [desktop.hyprland.hyprbars],
// tests/test-island-sysmon.py
import QtQuick
import QtQuick.Controls
import Quickshell as QS
import Quickshell.Hyprland
import Quickshell.Io
import Quickshell.Wayland
import Quickshell.Networking
import Quickshell.Services.Pipewire
import Quickshell.Services.UPower

QS.PanelWindow {
    id: pill
    required property QtObject shellState
    property real viewportWidth: 0 // optional native nested output width while Qt screen metadata catches up
    readonly property real outputWidth: viewportWidth > 0 ? viewportWidth : (screen ? screen.width : 0)
    property QtObject theme: Theme {}
    readonly property var bar: theme.topbar || ({})
    readonly property var commands: bar.commands || ({})
    property string popup: ""   // "", "calendar" or "power"
    readonly property int popupMs: bar.popup_ms !== undefined ? bar.popup_ms : 200
    readonly property bool onFocusedScreen: !Hyprland.focusedMonitor || Hyprland.focusedMonitor.name === screen.name
    function shown(key) {
        return typeof shellState.topbarShown === "function" ? shellState.topbarShown(key) : (!bar.visibility || bar.visibility[key] !== false)
    }
    readonly property real outputFps: typeof shellState.fpsFor === "function" ? shellState.fpsFor(screen.name) : -1
    // The focused window's own frame rate beside the output's: "<host> FPS/<window> FPS"; the window part is hidden
    // when unknown (mios-pacing's focused-window meter; only the island of the window's output shows it).
    readonly property real windowFps: typeof shellState.windowFpsFor === "function" ? shellState.windowFpsFor(screen.name) : -1
    readonly property string fpsText: (remoteFrames ? (outputFps >= 0 ? outputFps.toFixed(1) + " RDP FPS" : "RDP FPS —")
                                                    : (outputFps >= 0 ? outputFps + " FPS" : "FPS —"))
                                      + (windowFps >= 0 ? "/" + windowFps + " FPS" : "")
    readonly property bool remoteFrames: typeof shellState.isRemoteOutput === "function" && shellState.isRemoteOutput(screen.name)
    property bool batteryHoverOpen: false
    property bool batteryPanelHovered: false
    readonly property bool batteryHovering: batteryItemsHover.hovered || batteryPanelHovered
    onBatteryHoveringChanged: {
        if (batteryHovering) { batteryLeave.stop(); batteryEnter.restart() }
        else { batteryEnter.stop(); batteryLeave.restart() }
    }
    Timer { id: batteryEnter; interval: Math.max(1, pill.popupMs); onTriggered: pill.batteryHoverOpen = true }
    Timer { id: batteryLeave; interval: Math.max(1, pill.popupMs); onTriggered: pill.batteryHoverOpen = false }
    // Extra shell scale for this output (Mobile.uiScale: policy scale / scale the compositor applied; 1
    // when the compositor scaled it). Window geometry is in compositor px, the row's contents in shell
    // units (`content` below).
    readonly property real ui: screen ? Mobile.uiScale(screen.name) : 1

    // ---- mobile layout profile (Mobile.qml) ------------------------------------------------------
    readonly property bool mobile: !!screen && Mobile.isMobile(screen.name)
    readonly property var mobileCfg: Mobile.config || ({})
    readonly property var hyprMonitor: screen ? Hyprland.monitorFor(screen) : null
    readonly property var screenWindows: hyprMonitor && hyprMonitor.activeWorkspace ? hyprMonitor.activeWorkspace.toplevels.values : []
    // The window the shoulders belong to: the focused one when it is on this output, else the
    // workspace's last (the one-window layout shows one at a time).
    readonly property var target: {
        const list = screenWindows
        if (!list || !list.length) return null
        for (let i = 0; i < list.length; i++) if (list[i].activated) return list[i]
        return list[list.length - 1]
    }
    readonly property string targetAddress: {
        if (!target || !target.address) return ""
        const a = String(target.address)
        const full = a.startsWith("0x") ? a : "0x" + a
        return /^0x[0-9a-fA-F]+$/.test(full) ? full : ""
    }
    readonly property bool shoulders: mobile && target !== null && !(reveal && reveal.fullscreen)
    // The compositor draws the tabs (hyprbars mobile mode) when the projection says it applied them.
    readonly property bool barsTabs: mobile && Mobile.hyprbarsTabs
    // The window's own top-corner rounding on this output: the global rounding unless [desktop.mobile]
    // window_rounding = false keeps mobile windows square. The tabs' corner/fillet rules follow it.
    readonly property real windowRadius: mobile && mobileCfg.window_rounding !== false ? Glass.rounding : 0
    // Mobile: the row is one touch target tall (the tabs' height); desktop: the bar height.
    readonly property real rowHeight: Glass.bar
    // Remote effects profile for this output (Glass.profileFor): "lite" where [desktop.mobile.remote_effects]
    // applies (the layer namespace below then selects the cheaper hyprglass preset), else "full". Published in
    // the "mobile" IPC state.
    readonly property string effects: screen ? Glass.profileFor(screen.name) : "full"
    readonly property bool dotHover: leftTab.dot.hovered || rightTab.dot.hovered
    readonly property bool dotIcons: !Glass.iconOnHover || dotHover
    // The window's left/right inset on a mobile output (workspace gaps_out); the tabs line up with it.
    readonly property int windowInset: mobileCfg.gaps_out_px !== undefined ? mobileCfg.gaps_out_px : Glass.gap
    readonly property var controls: Array.isArray(mobileCfg.window_controls) ? mobileCfg.window_controls : []

    // ---- mobile island geometry (output-local compositor px) -------------------------------------------------------
    readonly property real islandMargin: Glass.gap * ui         // the global edge margin, as everywhere
    // margins.top: one gap above a window's raised controls tab -- and flush with the top edge over a fullscreen window,
    // whose controls tab hangs from the screen's top edge (hyprbars inset tab): the row keeps the tab's centre line.
    readonly property real islandTop: Math.round((overFullscreen ? 0 : Glass.gap) * ui)
    readonly property real islandHeight: Math.round(rowHeight * ui)
    readonly property real tabBarHeight: Glass.barHeight
    // The controls-tab boxes (fillets excluded) of a window from its goal geometry, by the plugin's own rule
    // (hyprbars-overlay.patch mobileTabs/computeInset): bar_height tall, button + 2 x pill_padding wide, one rounding
    // plus one fillet in from each side, rising from the top edge -- or hanging inside it when there is no room above.
    // The shell-drawn fallback tabs sit in the island's row.
    function tabsOf(t) {
        const o = t ? t.lastIpcObject : null
        // (at/size arrive as sequence wrappers, not JS arrays: test the length)
        if (!o || !o.at || !o.size || !(o.at.length >= 2) || !(o.size.length >= 2) || o.hidden === true || o.mapped === false) return []
        const ox = hyprMonitor && typeof hyprMonitor.x === "number" ? hyprMonitor.x : (screen ? screen.x : 0)
        const oy = hyprMonitor && typeof hyprMonitor.y === "number" ? hyprMonitor.y : (screen ? screen.y : 0)
        const H = tabBarHeight, w = Glass.mobileTabWidth
        const inset = Glass.mobileTabInset(H, windowRadius)
        const x = Number(o.at[0]) - ox, y = Number(o.at[1]) - oy, ww = Number(o.size[0])
        if (!isFinite(x) || !isFinite(y) || !(ww > 0)) return []
        const top = !barsTabs ? islandTop : (y - H < 0 ? y : y - H)
        return [{ x: x + inset, y: top, w: w, h: barsTabs ? H : islandHeight, side: "left", address: String(o.address || "") },
                { x: x + ww - inset - w, y: top, w: w, h: barsTabs ? H : islandHeight, side: "right", address: String(o.address || "") }]
    }
    // every tab on this output's shown workspace (each window carries its own pair)
    readonly property var tabRects: {
        if (!mobile || overFullscreen) return []
        let out = []
        for (const t of (screenWindows || [])) out = out.concat(tabsOf(t))
        return out
    }
    // The island's goal: the largest contiguous free horizontal segment between [islandMargin, W - islandMargin]
    // that maintains at least islandMargin clearance from every tab on its row (1D free-span interval algorithm).
    readonly property var islandGoal: {
        const W = screen ? screen.width : 0
        if (W <= 2 * islandMargin) return ({ x: 0, w: 0 })

        const top = islandTop, bottom = islandTop + islandHeight
        const mid = W / 2

        // 1. Collect horizontal blocked intervals [r.x - islandMargin, r.x + r.w + islandMargin] for all tabs vertically intersecting the row
        let blocked = []
        for (const r of tabRects) {
            if (r.y >= bottom + islandMargin || r.y + r.h <= top - islandMargin) continue   // clear of the island's row
            const bStart = Math.max(islandMargin, r.x - islandMargin)
            const bEnd = Math.min(W - islandMargin, r.x + r.w + islandMargin)
            if (bEnd > bStart) blocked.push({ start: bStart, end: bEnd })
        }

        // 2. Sort and merge overlapping / adjacent blocked intervals
        blocked.sort((a, b) => a.start - b.start)
        let merged = []
        for (const b of blocked) {
            if (!merged.length) {
                merged.push({ start: b.start, end: b.end })
            } else {
                let prev = merged[merged.length - 1]
                if (b.start <= prev.end) {
                    prev.end = Math.max(prev.end, b.end)
                } else {
                    merged.push({ start: b.start, end: b.end })
                }
            }
        }

        // 3. Compute complementary free intervals within [islandMargin, W - islandMargin]
        let free = []
        let cur = islandMargin
        for (const m of merged) {
            if (m.start > cur) {
                free.push({ start: cur, end: m.start, len: m.start - cur })
            }
            cur = Math.max(cur, m.end)
        }
        if (cur < W - islandMargin) {
            free.push({ start: cur, end: W - islandMargin, len: (W - islandMargin) - cur })
        }

        if (!free.length) return ({ x: mid, w: 0 })

        // 4. Select optimal free interval (maximize length; tie-breaker: closest to screen midpoint W / 2)
        let best = null
        for (const f of free) {
            if (!best) {
                best = f
            } else if (f.len > best.len + 0.001) {
                best = f
            } else if (Math.abs(f.len - best.len) <= 0.001) {
                const fCenter = (f.start + f.end) / 2
                const bestCenter = (best.start + best.end) / 2
                if (Math.abs(fCenter - mid) < Math.abs(bestCenter - mid)) {
                    best = f
                }
            }
        }

        // 5. Inward snapping for integer pixel precision and safety invariant preservation
        const left = Math.ceil(best.start)
        const right = Math.floor(best.end)
        const w = Math.max(0, right - left)

        // 6. If available span w < islandHeight (28 px), collapse to width 0 at gap center
        if (w < islandHeight) {
            const c = (best.start + best.end) / 2
            return ({ x: c, w: 0 })
        }

        return ({ x: left, w: w })
    }
    // The drawn island (animated toward islandGoal).
    property real islandX: 0
    property real islandW: 0
    property bool islandPlaced: false
    property var islandTarget: ({ x: 0, w: 0 })
    readonly property var islandMotion: Mobile.motion(typeof mobileCfg.island_motion === "string" ? mobileCfg.island_motion : "fadeIn")
    readonly property int islandHoldMs: {
        const leaves = Array.isArray(mobileCfg.island_hold) ? mobileCfg.island_hold : ["windowsOut", "fadeOut"]
        let ms = 0
        for (const l of leaves) { const m = Mobile.motion(String(l)); if (m) ms = Math.max(ms, m.duration) }
        return ms
    }
    onIslandGoalChanged: placeIsland()
    function placeIsland() {
        const g = islandGoal
        if (!g) return
        if (!mobile || !islandPlaced || !visible || !islandMotion || islandMotion.duration <= 0) {
            islandAnim.stop(); islandHold.stop()
            islandX = g.x; islandW = g.w; islandTarget = g
            islandPlaced = mobile
            return
        }
        if (Math.abs(g.x - islandTarget.x) < 0.5 && Math.abs(g.w - islandTarget.w) < 0.5) return
        // an edge moving inward = a tab arrived: out of its way now; only outward = tabs left: after they melted
        const inward = g.x > islandX + 0.5 || g.x + g.w < islandX + islandW - 0.5
        if (inward) { islandHold.stop(); animateIsland() }
        else islandHold.restart()
    }
    function animateIsland() {
        const g = islandGoal, m = islandMotion
        if (!g || !m) return
        islandAnim.stop()
        islandTarget = g
        const curve = [m.bezier[0], m.bezier[1], m.bezier[2], m.bezier[3], 1, 1]
        for (const a of [islandAnimX, islandAnimW]) {
            a.duration = Math.round(m.duration)
            a.easing.type = Easing.BezierSpline
            a.easing.bezierCurve = curve
        }
        islandAnimX.from = islandX; islandAnimX.to = g.x
        islandAnimW.from = islandW; islandAnimW.to = g.w
        islandAnim.start()
    }
    ParallelAnimation {
        id: islandAnim
        NumberAnimation { id: islandAnimX; target: pill; property: "islandX" }
        NumberAnimation { id: islandAnimW; target: pill; property: "islandW" }
    }
    Timer { id: islandHold; interval: Math.max(1, pill.islandHoldMs); onTriggered: pill.animateIsland() }
    // The focused window's tabs anchor the window-menu popups (and are drawn here in the fallback).
    readonly property var targetTabs: tabsOf(target)
    // Width of the row's other visible items and their gaps (mobile: what the monitor items must leave room for).
    readonly property real rowFixedWidth: {
        let w = 0, n = 0
        for (const c of row.children) {
            if (c === sysmonItems || !c.visible) continue
            w += c.width; n++
        }
        return w + n * row.spacing
    }
    // Desktop: hovering the monitor items opens their details (no focus grab) after one popup_ms of intent; leaving
    // both the items and the popup closes them. A tap or click pins them (popup "sysmon", dismissed like the others).
    readonly property bool sysmonHovering: sysmonItems.hovered || sysmonPanelHover.hovered
    property bool sysmonHoverOpen: false
    onSysmonHoveringChanged: {
        if (sysmonHovering) { sysmonLeave.stop(); if (!mobile && Sysmon.hoverDetails) sysmonEnter.restart() }
        else { sysmonEnter.stop(); sysmonLeave.restart() }
    }
    Timer { id: sysmonEnter; interval: Math.max(1, pill.popupMs); onTriggered: pill.sysmonHoverOpen = true }
    Timer { id: sysmonLeave; interval: Math.max(1, pill.popupMs); onTriggered: pill.sysmonHoverOpen = false }
    onShouldersChanged: if (!shoulders && (popup === "controls" || popup === "appmenu")) popup = ""
    onMobileChanged: { popup = ""; islandPlaced = false; placeIsland(); publishIsland() }
    onUiChanged: publishIsland()
    // `{address}` in a [desktop.mobile].window_controls action is the target window; only a 0x-hex
    // address is ever substituted, so the shell command cannot be widened by a window.
    // [desktop.mobile].close_action, else the window_controls entry labelled "Close".
    readonly property string closeAction: {
        if (mobileCfg.close_action) return String(mobileCfg.close_action)
        for (let i = 0; i < controls.length; i++)
            if (String(controls[i].label || "").toLowerCase() === "close") return String(controls[i].action || "")
        return ""
    }
    function closeTarget(): void { runWindowControl(closeAction) }
    function toggleControls(): void { popup = (popup === "controls" || popup === "appmenu") ? "" : "controls" }

    function runWindowControl(action) {
        const address = pill.targetAddress
        pill.popup = ""
        if (!action || !address) return
        QS.Quickshell.execDetached(["sh", "-c", String(action).split("{address}").join(address)])
    }
    // Focused app's exported menu, the appmenu-exporter contract AppMenu.qml reads.
    property var menuSnap: ({})
    FileView {
        path: QS.Quickshell.env("MIOS_APPMENU_JSON") || ((QS.Quickshell.env("XDG_RUNTIME_DIR") || "/run/user/1000") + "/mios/appmenu.json")
        watchChanges: true
        preload: true
        printErrors: false
        onFileChanged: reload()
        onLoaded: { try { pill.menuSnap = JSON.parse(text()) || ({}) } catch (e) { pill.menuSnap = ({}) } }
        onLoadFailed: pill.menuSnap = ({})
    }
    readonly property string targetAppId: target && target.wayland ? target.wayland.appId : ""
    readonly property var targetEntry: targetAppId ? QS.DesktopEntries.heuristicLookup(targetAppId) : null
    readonly property string targetName: !targetAppId ? "" : (targetEntry ? targetEntry.name : targetAppId)
    // Menus flattened: each menu's label as a header, its items (submenus inline, two levels) below.
    readonly property var appMenuRows: {
        const s = menuSnap, out = []
        if (!targetAppId || !s || s.app_id !== targetAppId || s.address !== targetAddress || !Array.isArray(s.menus)) return out
        function walk(items, depth) {
            for (const it of items) {
                if (!it || !it.label || out.length >= 80) continue
                out.push({ item: it, depth: depth, header: false })
                if (depth < 3 && Array.isArray(it.submenu) && it.submenu.length) walk(it.submenu, depth + 1)
            }
        }
        for (const m of s.menus) {
            if (!m || !m.label) continue
            if (m.action) {
                out.push({ item: { id: m.action, label: m.label, enabled: m.enabled !== false }, depth: 0, header: false })
                continue
            }
            if (!Array.isArray(m.items) || !m.items.length) continue
            out.push({ item: m, depth: 0, header: true })
            walk(m.items, 1)
        }
        return out
    }

    // Fullscreen edge reveal (EdgeReveal.qml): under a fullscreen window the island hides, reserves
    // nothing, and comes back on the overlay layer above it while the pointer is at the top edge.
    property QtObject reveal: null
    readonly property bool overFullscreen: !!reveal && reveal.topActive
    readonly property string hudAlignment: "center"
    readonly property real hudScreenX: mobile ? 0 : (outputWidth - width) / 2
    // "mios-topbar-remote" where [desktop.mobile.remote_effects] applies (Mobile.layerNamespace): the
    // compositor draws this layer's glass with the remote preset.
    WlrLayershell.namespace: Mobile.layerNamespace("mios-topbar", screen ? screen.name : "")
    WlrLayershell.layer: WlrLayer.Overlay
    anchors.top: true
    // Desktop: a centred island as wide as its contents. Mobile: the surface spans the row and the island inside it
    // fills the room between the window tabs (islandGoal); the rest of the surface is transparent and takes no input.
    anchors.left: mobile
    anchors.right: mobile
    margins.top: islandTop
    implicitWidth: mobile ? outputWidth
                          : Math.min(Math.ceil((row.implicitWidth + Glass.bar) * ui), outputWidth - Math.round(2 * Glass.gap * ui))
    implicitHeight: Math.round(rowHeight * ui)
    // Design language ([theme.shell]): the zone is the bar row itself and the compositor adds margins.top, so a window
    // under the row starts at edge margin + bar -- its controls tab (bar tall, hyprbars) rises exactly into the row and
    // shares the island's and the app-menu pills' vertical centre (Glass.barCentre); desktop and mobile alike. Under a fullscreen window the zone is 0 (nothing
    // reserved). The mode stays Normal: Quickshell sets it to Normal whenever exclusiveZone is written, so a second
    // binding switching it to Ignore races that write and could leave the zone ignored. EdgeReveal's guard re-asserts
    // the zone (reserveNudge, reserveRebuild) when the compositor lost it.
    exclusiveZone: zoneFor(overFullscreen, rowHeight, 0, ui) + (overFullscreen ? 0 : reserveNudge)
    exclusionMode: QS.ExclusionMode.Normal
    function zoneFor(fs, row, margin, scale) { return fs ? 0 : Math.round((row + margin) * scale) }
    property int reserveNudge: 0
    property bool reserveRebuild: false
    // What the compositor then reserves on the top edge (the zone plus margins.top), from the current inputs.
    function reserveExpectedNow() {
        const z = zoneFor(overFullscreen, rowHeight, 0, ui)
        return z > 0 ? z + Math.round(Glass.gap * ui) : 0
    }
    function reserveDiag() {
        return { zone: exclusiveZone, mode: exclusionMode, layer: WlrLayershell.layer, visible: visible,
                 nudge: reserveNudge, over_fullscreen: overFullscreen, ui: ui, row: rowHeight, margin: Glass.gap }
    }
    function nudgeReserve() { reserveNudge = 1; reserveNudgeBack.restart() }
    function recreateReserve() { reserveRebuild = true; Qt.callLater(() => { pill.reserveRebuild = false }) }
    Timer { id: reserveNudgeBack; interval: 250; onTriggered: pill.reserveNudge = 0 }
    onRevealChanged: if (reveal) reveal.topSurface = pill
    Component.onDestruction: if (reveal && reveal.topSurface === pill) reveal.topSurface = null
    // islandShown: the top row's reveal, or a notification growing out of the island (EdgeReveal.qml).
    visible: (!overFullscreen || reveal.islandShown) && !reserveRebuild
    color: "transparent"
    // Input: desktop, the row; mobile, the pill and the shell-drawn tabs only -- the compositor's tabs under this
    // transparent surface must receive their own taps (hyprbars ignores input under a layer surface).
    mask: QS.Region {
        item: pill.mobile ? island : rowMask
        QS.Region { item: pill.mobile && pill.shoulders && !pill.barsTabs ? leftTab.box : null }
        QS.Region { item: pill.mobile && pill.shoulders && !pill.barsTabs ? rightTab.box : null }
    }
    Item { id: rowMask; width: pill.width; height: Math.round(pill.rowHeight * pill.ui) }
    onPopupChanged: if (reveal) reveal.islandPopup = popup !== "" || sysmonHoverOpen || batteryHoverOpen
    onSysmonHoverOpenChanged: if (reveal) reveal.islandPopup = popup !== "" || sysmonHoverOpen || batteryHoverOpen
    onBatteryHoverOpenChanged: if (reveal) reveal.islandPopup = popup !== "" || sysmonHoverOpen || batteryHoverOpen
    onVisibleChanged: if (!visible && reveal && reveal.islandHovered) Qt.callLater(() => { if (!pill.visible && pill.reveal) pill.reveal.islandHovered = false })

    // Publish the island's rectangle so the top-left app menus (AppMenu.qml) never run under it.
    function publishIsland() {
        if (!screen) return
        const h = Math.round(rowHeight * ui)
        const g = islandGoal
        if (mobile && g) ShellGeometry.setIsland(screen.name, Math.round(g.x), islandTop, Math.round(g.w), h)
        else ShellGeometry.setIsland(screen.name, Math.round(hudScreenX), islandTop, width, h)
    }
    onIslandTargetChanged: publishIsland()
    onWidthChanged: publishIsland()
    onHeightChanged: publishIsland()
    onHudScreenXChanged: publishIsland()
    Component.onCompleted: { placeIsland(); publishIsland(); if (reveal) reveal.topSurface = pill }
    // The island as drawn now (mobile: animated between the tabs), its radius and whether it is mapped: the
    // notification blob (Notifications.qml) starts as this rectangle and melts back into it.
    readonly property var drawnIsland: !screen ? null : ({
        x: mobile ? islandX : hudScreenX, y: islandTop,
        width: mobile ? Math.max(0, islandW) : width, height: Math.round(rowHeight * ui),
        radius: island.radius * ui, shown: visible && backingWindowVisible })
    onDrawnIslandChanged: if (drawnIsland) ShellGeometry.setDrawnIsland(screen.name, drawnIsland)

    function run(key) {
        const command = pill.commands[key]
        pill.popup = ""
        if (command) QS.Quickshell.execDetached(["sh", "-c", command])
    }

    readonly property var focusedEntry: {
        const top = ToplevelManager.activeToplevel
        return top ? QS.DesktopEntries.heuristicLookup(top.appId) : null
    }
    readonly property string focusedName: {
        const top = ToplevelManager.activeToplevel
        if (!top) return ""
        return focusedEntry ? focusedEntry.name : top.appId
    }

    PwObjectTracker { objects: [Pipewire.defaultAudioSink] }
    readonly property var sink: Pipewire.defaultAudioSink
    readonly property var battery: UPower.displayDevice
    readonly property bool hasBattery: battery && battery.isPresent && battery.isLaptopBattery
    readonly property var batteryDevices: UPower.devices.values.filter(d => d.ready && d.isPresent
        && d.type !== UPowerDeviceType.LinePower && (d.state !== UPowerDeviceState.Unknown || d.percentage > 0))
    function batteryName(d) { return d.model || UPowerDeviceType.toString(d.type) }
    function batteryKind(d) {
        if (d.type === UPowerDeviceType.Ups) return "UPS"
        if (d.type === UPowerDeviceType.GamingInput) return "Controller"
        if (d.type === UPowerDeviceType.Battery) return "BAT"
        if (d.type === UPowerDeviceType.BluetoothGeneric) return "BT"
        return UPowerDeviceType.toString(d.type)
    }
    function batteryIcon(d) {
        switch (d.type) {
        case UPowerDeviceType.GamingInput: return "\u{f02b4}"
        case UPowerDeviceType.Phone: return "\u{f011c}"
        case UPowerDeviceType.Mouse: return "\u{f037d}"
        case UPowerDeviceType.Keyboard: return "\u{f030c}"
        case UPowerDeviceType.Headphones: return "\u{f02cb}"
        case UPowerDeviceType.Headset: return "\u{f02ce}"
        case UPowerDeviceType.Speakers:
        case UPowerDeviceType.OtherAudio: return "\u{f04c3}"
        case UPowerDeviceType.Tablet:
        case UPowerDeviceType.Pda: return "\u{f04f6}"
        case UPowerDeviceType.Computer: return "\u{f0322}"
        case UPowerDeviceType.Wearable: return "\u{f0589}"
        case UPowerDeviceType.Camera: return "\u{f0100}"
        case UPowerDeviceType.Pen: return "\u{f03eb}"
        case UPowerDeviceType.BluetoothGeneric: return "\u{f00af}"
        case UPowerDeviceType.Ups: return "\u{f06a5}"
        default: return batteryLevelIcon(d)
        }
    }
    // The charge as a battery glyph (md battery-10 .. battery, battery-outline when empty, charging bolt battery while
    // charging). A laptop/system battery is shown by this glyph alone; every other device (controller, mouse, headset,
    // phone ...) by its device glyph followed by this one, so the kind and the level read at a glance.
    function batteryLevelIcon(d) {
        if (d.state === UPowerDeviceState.Charging) return "\u{f0084}"
        const level = Math.max(0, Math.min(10, Math.round(d.percentage * 10)))
        return level >= 10 ? "\u{f0079}" : level <= 0 ? "\u{f008e}" : String.fromCodePoint(0xf007a + level - 1)
    }
    function batteryIsSystem(d) { return d.type === UPowerDeviceType.Battery || d.type === UPowerDeviceType.Unknown }
    function batteryGlyphs(d) { return batteryIsSystem(d) ? batteryLevelIcon(d) : batteryIcon(d) + " " + batteryLevelIcon(d) }

    // ---- scroll volume ([desktop.topbar] volume_scroll*, volume_osd_ms) ------------------------------------------------
    // The wheel anywhere on the island steps the default sink's volume (one step per wheel notch; high-resolution
    // wheels and touchpads accumulate to a notch). Every volume or mute change -- the wheel, a media key, another app --
    // shows the level as a percentage chip in the island for volume_osd_ms, also when the sound item is hidden
    // ([desktop.topbar.visibility] sound = false); while the sound item is shown its own percentage carries it.
    readonly property bool volumeScroll: bar.volume_scroll !== false
    readonly property real volumeStepPct: bar.volume_scroll_step_pct > 0 ? bar.volume_scroll_step_pct : 5
    readonly property real volumeMax: (bar.volume_max_pct > 0 ? bar.volume_max_pct : 100) / 100
    readonly property int volumeOsdMs: bar.volume_osd_ms !== undefined ? bar.volume_osd_ms : 1500
    readonly property int volumePct: sink && sink.audio ? Math.round(sink.audio.volume * 100) : -1
    readonly property bool volumeMuted: !!(sink && sink.audio && sink.audio.muted)
    property bool volumeOsd: false
    property int volumeSeen: -1          // last level seen: the first reading (startup, a sink change) shows nothing
    property string volumeSeenSink: ""
    property real volumeWheelAcc: 0
    Timer { id: volumeOsdHide; interval: Math.max(1, pill.volumeOsdMs); onTriggered: pill.volumeOsd = false }
    function showVolumeOsd() { if (volumeOsdMs > 0) { volumeOsd = true; volumeOsdHide.restart() } }
    function volumeChanged() {
        const id = sink ? String(sink.id) : ""
        if (volumePct >= 0 && volumeSeen >= 0 && id === volumeSeenSink) showVolumeOsd()
        volumeSeen = volumePct
        volumeSeenSink = id
    }
    onVolumePctChanged: volumeChanged()
    onVolumeMutedChanged: volumeChanged()
    function volumeIcon(pct, muted) {
        return muted || pct <= 0 ? "\u{f075f}" : pct < 34 ? "\u{f057f}" : pct < 67 ? "\u{f0580}" : "\u{f057e}"
    }
    readonly property string volumeText: volumePct < 0 ? "" : volumeIcon(volumePct, volumeMuted) + " " + (volumeMuted ? "muted" : volumePct + "%")
    // One wheel event (angleDelta.y, 120 per notch); returns the new level in percent, -1 when nothing changed.
    function volumeWheel(deltaY) {
        if (!volumeScroll || !sink || !sink.audio || !deltaY) return -1
        volumeWheelAcc += deltaY
        const notches = volumeWheelAcc > 0 ? Math.floor(volumeWheelAcc / 120) : Math.ceil(volumeWheelAcc / 120)
        if (notches === 0) return -1
        volumeWheelAcc -= notches * 120
        // Wheel-up never lowers the level: a sink another app set above volume_max_pct stays where it is (up only
        // unmutes it); wheel-down steps down from wherever it is.
        const current = sink.audio.volume
        const stepped = Math.round(current * 100 + notches * volumeStepPct) / 100
        const next = notches > 0 ? Math.max(current, Math.min(volumeMax, stepped)) : Math.max(0, Math.min(current, stepped))
        if (sink.audio.muted && notches > 0) sink.audio.muted = false
        if (next !== current) sink.audio.volume = next
        showVolumeOsd()
        return Math.round(next * 100)
    }
    readonly property bool online: Networking.devices.values.some(d => d.connected)

    property date now: new Date()
    Timer {
        interval: pill.bar.show_seconds ? 1000 : 10000
        repeat: true
        running: true
        triggeredOnStart: true
        onTriggered: pill.now = new Date()
    }
    readonly property string timeText: {
        const h24 = pill.bar.clock_24h !== false
        const secs = pill.bar.show_seconds ? ":ss" : ""
        return Qt.formatDateTime(now, h24 ? "HH:mm" + secs : "h:mm" + secs + " AP")
    }

    component Glyph: Text {
        color: pill.theme.fg
        font.family: pill.theme.fontFamily
        font.pixelSize: pill.theme.fontSize
        verticalAlignment: Text.AlignVCenter
        height: Glass.bar
    }
    component Hit: MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        acceptedButtons: Qt.LeftButton | Qt.RightButton
    }

    // ---- mobile tabs: the desktop controls tab split around the pill ------------------------------
    // ControlsTab: the hyprbars tab's silhouette rules, material and one hyprbars button, all bound to Glass.qml
    // (SSOT). Drawn by the compositor when barsTabs (then only the popup anchor), by the shell otherwise.
    component Tab: ControlsTab {
        visible: pill.shoulders
        drawn: !pill.barsTabs
        rowHeight: pill.rowHeight
        tabWidth: Glass.mobileTabWidth
        windowRadius: pill.windowRadius
        showIcon: pill.dotIcons
    }
    // window edges and the tabs' box inset from them, in shell units
    readonly property real windowLeft: windowInset / ui
    readonly property real windowRight: (width - windowInset) / ui
    readonly property real tabInset: Glass.mobileTabInset(rowHeight, windowRadius)
    // Everything on the row is laid out in shell units and scaled by `ui` (Mobile.uiScale): where the
    // compositor could not apply the [desktop.scaling] policy scale to this output (e.g. a remote output
    // left at scale 1), the pill, shoulders and dots are still drawn at the policy's size.
    Item {
        id: content
        width: pill.width / pill.ui
        height: pill.height / pill.ui
        scale: pill.ui
        transformOrigin: Item.TopLeft

        GlassSurface {
            id: island
            // Desktop: the whole surface. Mobile: the room between the window tabs (islandX/islandW, animated).
            anchors.top: parent.top
            height: pill.rowHeight
            x: pill.mobile ? pill.islandX / pill.ui : 0
            width: pill.mobile ? Math.max(0, pill.islandW / pill.ui) : parent.width
            clip: pill.mobile
            // The window-control tab's material and corner rule (Glass.qml); no outline: the tab has none,
            // its rim is the compositor's hyprglass fresnel.
            HoverHandler { onHoveredChanged: if (pill.reveal) pill.reveal.islandHovered = hovered }
            // The wheel anywhere on the island steps the volume (items with their own wheel action keep it).
            WheelHandler {
                enabled: pill.volumeScroll
                acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
                onWheel: event => { event.accepted = pill.volumeWheel(event.angleDelta.y) >= 0 || !!pill.sink }
            }

            Row {
                id: row
                anchors.centerIn: parent
                spacing: Glass.gap

                Glyph {
                    visible: !pill.mobile && pill.shown("workspaces")
                    text: Hyprland.focusedWorkspace ? "\u{f0570} " + Hyprland.focusedWorkspace.id : ""
                    color: pill.theme.subtle
                }
                Glyph {
                    id: netGlyph
                    visible: pill.shown("network")
                    text: pill.online ? "\u{f06f3}" : "\u{f06f4}"
                    color: pill.online ? pill.theme.fg : pill.theme.muted
                    Hit { onClicked: pill.run("network") }
                }
                Glyph {
                    id: soundGlyph
                    visible: !pill.mobile && pill.shown("sound") && !!pill.sink && !!pill.sink.audio
                    text: pill.volumeText
                    font.bold: pill.volumeOsd
                    Accessible.name: !pill.sink || !pill.sink.audio ? "Sound" : "Volume " + Math.round(pill.sink.audio.volume * 100) + "%"
                    Hit {
                        onClicked: mouse => {
                            if (mouse.button === Qt.RightButton && pill.sink.audio) pill.sink.audio.muted = !pill.sink.audio.muted
                            else pill.run("sound")
                        }
                        onWheel: wheel => pill.volumeWheel(wheel.angleDelta.y)
                    }
                }
                Row {
                    id: batteryItems
                    spacing: Glass.gap
                    HoverHandler { id: batteryItemsHover }
                    Repeater {
                        model: pill.shown("batteries") ? pill.batteryDevices : []
                        delegate: Glyph {
                            required property var modelData
                            text: pill.batteryGlyphs(modelData) + " " + Math.round(modelData.percentage * 100) + "%"
                            textFormat: Text.PlainText
                            Accessible.name: pill.batteryName(modelData) + ", " + Math.round(modelData.percentage * 100) + "%"
                            color: modelData.state === UPowerDeviceState.Charging ? pill.theme.success : pill.theme.fg
                            Hit { onClicked: pill.popup = pill.popup === "batteries" ? "" : "batteries" }
                        }
                    }
                }
                SysmonItems {
                    id: sysmonItems
                    visible: pill.shown("sysmon") && fitted.length > 0
                    theme: pill.theme
                    keys: pill.mobile ? Sysmon.mobileItems : Sysmon.items
                    compact: pill.mobile
                    // mobile: what is left of the island between the tabs after the other items
                    maxWidth: pill.mobile ? Math.max(0, island.width - 2 * Glass.gap - pill.rowFixedWidth) : 1e6
                    onTapped: pill.popup = pill.popup === "sysmon" ? "" : "sysmon"
                }
                Glyph {
                    id: fpsGlyph
                    visible: pill.shown("fps")
                    text: pill.fpsText
                    Hit { onClicked: pill.popup = pill.popup === "sysmon" ? "" : "sysmon" }
                }

                Rectangle {
                    visible: (dateGlyph.visible || timeGlyph.visible) && (netGlyph.visible || soundGlyph.visible || batteryItems.width > 0 || sysmonItems.visible || fpsGlyph.visible)
                    width: Glass.hairline; height: Glass.control; y: (Glass.bar - height) / 2
                    color: pill.theme.outlineColor
                }
                Glyph {
                    id: dateGlyph
                    visible: !pill.mobile && pill.shown("date")
                    text: Qt.formatDateTime(pill.now, pill.bar.date_format || "ddd d MMM")
                    color: pill.theme.subtle
                    Hit { onClicked: pill.popup = pill.popup === "calendar" ? "" : "calendar" }
                }
                Glyph {
                    id: timeGlyph
                    visible: pill.shown("clock")
                    text: pill.timeText
                    font.bold: true
                    Hit { onClicked: pill.popup = pill.popup === "calendar" ? "" : "calendar" }
                }
                Rectangle {
                    visible: dateGlyph.visible || timeGlyph.visible || batteryItems.width > 0 || netGlyph.visible || soundGlyph.visible || sysmonItems.visible || fpsGlyph.visible
                    width: Glass.hairline; height: Glass.control; y: (Glass.bar - height) / 2
                    color: pill.theme.outlineColor
                }
                Glyph {
                    id: settingsGlyph
                    width: height
                    horizontalAlignment: Text.AlignHCenter
                    text: "\u{f0493}"
                    Accessible.name: "HUD appearance"
                    Hit { onClicked: pill.popup = pill.popup === "settings" ? "" : "settings" }
                }
                Glyph {
                    id: powerGlyph
                    visible: pill.shown("power")
                    width: height
                    horizontalAlignment: Text.AlignHCenter
                    text: "\u{f0425}"
                    Accessible.name: "This computer: power and session"
                    Hit { onClicked: pill.popup = pill.popup === "power" ? "" : "power" }
                }

            }
            // The scroll-volume percentage: a chip of the island's own glass centred over the row, shown for
            // volume_osd_ms after every volume or mute change while the sound item (which carries the level itself)
            // is hidden or the island is too narrow for it (mobile). Above the row so a crowded row never hides it.
            GlassSurface {
                id: volumeChip
                visible: pill.volumeOsd && pill.volumePct >= 0 && (!soundGlyph.visible || pill.mobile)
                anchors.centerIn: parent
                height: Glass.control   // a chip inside the bar: concentric with the island (inset all round)
                width: Math.min(parent.width - 2 * Glass.inset, volumeChipText.implicitWidth + 2 * Glass.inset)
                color: pill.theme.withAlpha(Glass.fillHex, pill.theme.notificationOpacity)
                Glyph {
                    id: volumeChipText
                    anchors.centerIn: parent
                    text: pill.volumeText
                    font.bold: true
                    color: pill.theme.fg
                    Accessible.name: "Volume " + (pill.volumeMuted ? "muted" : pill.volumePct + "%")
                }
                Hit { onClicked: pill.run("sound") }
            }
        }
        Tab {
            id: leftTab
            button: Glass.menuButton
            x: (pill.targetTabs.length ? pill.targetTabs[0].x / pill.ui : pill.windowLeft + pill.tabInset) - rf
            y: pill.targetTabs.length && pill.barsTabs ? (pill.targetTabs[0].y - pill.islandTop) / pill.ui : 0
            // Left tab: the window's menu (window controls + the app's exported menu).
            onTapped: pill.toggleControls()
        }
        Tab {
            id: rightTab
            button: Glass.closeButton
            x: (pill.targetTabs.length ? pill.targetTabs[1].x / pill.ui : pill.windowRight - pill.tabInset - tabWidth) - rf
            y: pill.targetTabs.length && pill.barsTabs ? (pill.targetTabs[1].y - pill.islandTop) / pill.ui : 0
            // Right tab: close the tabs' window (always visible, focused or not).
            onTapped: { pill.popup = ""; pill.closeTarget() }
        }
    }

    // Window controls ([desktop.mobile].window_controls), grown out of the left dot.
    MeltPopup {
        id: controlsPanel
        anchor.window: pill
        theme: pill.theme
        trigger: leftTab.box
        align: "left"
        open: pill.popup === "controls" && pill.shoulders
        duration: pill.popupMs
        panelWidth: 220
        panelHeight: Math.max(controlsColumn.implicitHeight,
                              ((pill.mobileCfg.app_menu !== false && pill.appMenuRows.length > 0 ? 1 : 0) + pill.controls.length) * Glass.bar) + 2 * Glass.panelPad
        Item {
            anchors.fill: parent
            Column {
                id: controlsColumn
                x: Glass.panelPad
                y: Glass.panelPad
                width: parent.width - 2 * Glass.panelPad
                GlassSurface {
                    // Entry into the app's exported menu (appmenu.json), shown when it has one.
                    visible: pill.mobileCfg.app_menu !== false && pill.appMenuRows.length > 0
                    width: controlsColumn.width
                    height: visible ? Glass.bar : 0
                    color: appMenuHit.containsMouse ? pill.theme.withAlpha(pill.theme.accent, pill.theme.hoverOpacity) : "transparent"
                    Row {
                        anchors.verticalCenter: parent.verticalCenter
                        x: Glass.gap
                        spacing: Glass.gap
                        Glyph { text: "\u2630"; textFormat: Text.PlainText; height: parent.parent.height }
                        Glyph { text: "App menu"; textFormat: Text.PlainText; height: parent.parent.height }
                    }
                    MouseArea {
                        id: appMenuHit
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: pill.popup = "appmenu"
                    }
                }
                Repeater {
                    model: pill.controls
                    delegate: GlassSurface {
                        required property var modelData
                        width: controlsColumn.width
                        height: Glass.bar
                        color: controlHit.containsMouse ? pill.theme.withAlpha(pill.theme.accent, pill.theme.hoverOpacity) : "transparent"
                        Row {
                            anchors.verticalCenter: parent.verticalCenter
                            x: Glass.gap
                            spacing: Glass.gap
                            Glyph { text: modelData.icon || ""; textFormat: Text.PlainText; height: parent.parent.height }
                            Glyph { text: modelData.label || ""; textFormat: Text.PlainText; height: parent.parent.height }
                        }
                        MouseArea {
                            id: controlHit
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: pill.runWindowControl(modelData.action)
                        }
                    }
                }
            }
        }
    }

    // The target app's exported menu (appmenu.json), grown out of the right dot.
    MeltPopup {
        id: appMenuPanel
        anchor.window: pill
        theme: pill.theme
        trigger: leftTab.box
        align: "left"
        open: pill.popup === "appmenu" && pill.shoulders
        duration: pill.popupMs
        panelWidth: Math.min(280, pill.width - 2 * Glass.gap)
        panelHeight: Math.min(appMenuColumn.implicitHeight, Math.round(pill.screen.height * pill.theme.drawerHeightFraction)) + 2 * Glass.panelPad
        Flickable {
            anchors.fill: parent
            anchors.margins: Glass.panelPad
            contentHeight: appMenuColumn.implicitHeight
            clip: true
            Column {
                id: appMenuColumn
                width: parent.width
                Glyph {
                    width: parent.width
                    text: pill.targetName
                    textFormat: Text.PlainText // app-supplied: never markup
                    font.bold: true
                    elide: Text.ElideRight
                    height: Glass.bar
                    leftPadding: Glass.gap
                }
                Glyph {
                    visible: pill.appMenuRows.length === 0
                    text: "No app menu"
                    color: pill.theme.muted
                    height: visible ? Glass.bar : 0
                    leftPadding: Glass.gap
                }
                Repeater {
                    model: pill.appMenuRows
                    delegate: GlassSurface {
                        id: menuRow
                        required property var modelData
                        readonly property var it: modelData.item
                        readonly property bool hasSub: !modelData.header && Array.isArray(it.submenu) && it.submenu.length > 0
                        readonly property bool active: !modelData.header && !hasSub && it.enabled !== false
                        width: appMenuColumn.width
                        height: Glass.bar
                        color: menuHit.containsMouse && active ? pill.theme.withAlpha(pill.theme.accent, pill.theme.hoverOpacity) : "transparent"
                        Glyph {
                            x: Glass.gap + Math.max(0, menuRow.modelData.depth - 1) * Glass.panelPad
                            width: parent.width - x - Glass.gap
                            height: parent.height
                            text: menuRow.it.label
                            textFormat: Text.PlainText // app-supplied: never markup
                            elide: Text.ElideRight
                            font.bold: menuRow.modelData.header
                            color: menuRow.modelData.header || menuRow.hasSub ? pill.theme.subtle
                                 : menuRow.it.enabled === false ? pill.theme.muted : pill.theme.fg
                        }
                        MouseArea {
                            id: menuHit
                            anchors.fill: parent
                            hoverEnabled: true
                            enabled: menuRow.active
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                QS.Quickshell.execDetached(["/usr/bin/miosd", "appmenu", "activate", pill.menuSnap.app_id, String(menuRow.it.id), "--window", pill.menuSnap.address])
                                pill.popup = ""
                            }
                        }
                    }
                }
            }
        }
    }

    // Scripted access to the mobile row (one live handler: the focused monitor's, when it is mobile).
    IpcHandler {
        target: "mobile"
        enabled: pill.mobile && pill.onFocusedScreen
        function openControls(): void { if (pill.shoulders) pill.popup = "controls" }
        // the compositor's left tab (hyprbars mobile_menu_action) toggles the window menu
        function toggleControls(): void { if (pill.shoulders) pill.toggleControls() }
        function openAppMenu(): void { if (pill.shoulders) pill.popup = "appmenu" }
        function close(): void { pill.popup = "" }
        function state(): string {
            return JSON.stringify({ output: pill.screen ? pill.screen.name : "", mobile: pill.mobile, shoulders: pill.shoulders,
                                    target: pill.targetAddress, app_id: pill.targetAppId, popup: pill.popup,
                                    island: { x: island.x, width: island.width, height: island.height, top: pill.islandTop,
                                              px: { x: pill.islandX, width: pill.islandW, y: pill.islandTop, height: pill.islandHeight },
                                              goal: pill.islandGoal, margin: pill.islandMargin, animating: islandAnim.running || islandHold.running,
                                              motion: pill.islandMotion, hold_ms: pill.islandHoldMs },
                                    tab_rects: pill.tabRects, surface: { width: pill.width, height: pill.height },
                                    windows: (pill.screenWindows || []).map(t => { const o = t.lastIpcObject || {}; return { address: o.address || "", at: o.at || null, size: o.size || null, hidden: o.hidden === true } }),
                                    sysmon: { keys: sysmonItems.fitted, width: sysmonItems.width, max_width: sysmonItems.maxWidth },
                                    tabs: { renderer: pill.barsTabs ? "hyprbars" : "shell", height: pill.rowHeight, width: Glass.mobileTabWidth,
                                            touch_target: Glass.touchTarget, left_x: leftTab.x + leftTab.rf, right_x: rightTab.x + rightTab.rf,
                                            window_left: pill.windowLeft, window_right: pill.windowRight },
                                    controls: pill.controls.length, menu_rows: pill.appMenuRows.length,
                                    ui: pill.ui, effects: pill.effects, namespace: pill.WlrLayershell.namespace, glass_loaded: Glass.loaded,
                                    glass: { fill: String(Glass.fill), island_radius: island.radius,
                                             tab_corner: leftTab.rc, tab_fillet: leftTab.rf, window_radius: pill.windowRadius,
                                             menu_dot: { color: String(leftTab.dot.button.color), size: leftTab.dot.width },
                                             close_dot: { color: String(rightTab.dot.button.color), size: rightTab.dot.width } } })
        }
    }

    // Scripted access to the island's popups (one live handler: the focused monitor's island).
    IpcHandler {
        target: "topbar"
        enabled: pill.onFocusedScreen
        function openCalendar(): void { pill.popup = "calendar" }
        function openPower(): void { pill.popup = "power" }
        function openSysmon(): void { pill.popup = "sysmon" }
        function openSettings(): void { pill.popup = "settings" }
        function openBatteries(): void { pill.popup = "batteries" }
        // The island's wheel handler, headlessly (angleDelta.y; 120 = one notch up): returns the new level or -1.
        function volumeWheel(deltaY: int): int { return pill.volumeWheel(deltaY) }
        function reloadSettings(): void { if (typeof pill.shellState.saveNextTopbar === "function") pill.shellState.saveNextTopbar() }
        function showItem(key: string, shown: bool): void { if (typeof pill.shellState.setTopbarShown === "function") pill.shellState.setTopbarShown(key, shown) }
        function close(): void { pill.popup = "" }
        function state(): string {
            return JSON.stringify({ popup: pill.popup, calendar: calendarPanel.progress, power: powerPanel.progress,
                                    sysmon: sysmonPanel.progress, sysmon_hover: pill.sysmonHoverOpen,
                                    sysmon_items: sysmonItems.fitted, island_width: pill.width, fps: pill.outputFps,
                                    window_fps: pill.windowFps, fps_text: fpsGlyph.visible ? fpsGlyph.text : "",
                                    fps_source: pill.remoteFrames ? "rdp-delivered" : "compositor",
                                    visibility: pill.shellState.topbarConfig ? pill.shellState.topbarConfig.visibility : ({}),
                                    batteries: pill.batteryDevices.map(d => ({name: pill.batteryName(d), kind: pill.batteryKind(d), icon: pill.batteryIcon(d),
                                                                               level_icon: pill.batteryLevelIcon(d), glyphs: pill.batteryGlyphs(d),
                                                                               percentage: Math.round(d.percentage * 100)})),
                                    volume: { percent: pill.volumePct, muted: pill.volumeMuted, osd: pill.volumeOsd, text: pill.volumeText,
                                              chip: volumeChip.visible, sound_item: soundGlyph.visible, scroll: pill.volumeScroll,
                                              step_pct: pill.volumeStepPct, osd_ms: pill.volumeOsdMs, sink: pill.sink ? String(pill.sink.name) : "" },
                                    batteries_rect: batteriesPanel.toScreenRect,
                                    hud: { x: pill.hudScreenX, width: pill.width, alignment: pill.hudAlignment, shown: pill.visible,
                                           autohide: !!pill.reveal && pill.reveal.topActive, layer: pill.WlrLayershell.layer,
                                           settings: settingsPanel.fromScreenRect, power: powerPanel.fromScreenRect },
                                    settings_rect: settingsPanel.fromScreenRect })
        }
    }
    // A click anywhere outside the island and its open popup dismisses the popup.
    HyprlandFocusGrab {
        windows: [pill, calendarPanel, powerPanel, controlsPanel, appMenuPanel, sysmonPanel, settingsPanel, batteriesPanel]
        active: pill.popup !== ""
        onCleared: pill.popup = ""
    }

    component SettingsAction: GlassSurface {
        id: action
        property string label: ""
        property bool selected: false
        signal triggered()
        width: parent.width
        height: Glass.bar
        color: actionHit.containsMouse ? pill.theme.withAlpha(pill.theme.accent, pill.theme.hoverOpacity) : "transparent"
        Text {
            anchors.fill: parent
            anchors.margins: Glass.gap
            verticalAlignment: Text.AlignVCenter
            text: action.label
            textFormat: Text.PlainText
            color: action.enabled ? pill.theme.fg : pill.theme.muted
            font.family: pill.theme.fontFamily
            font.pixelSize: pill.theme.fontSize
            elide: Text.ElideRight
        }
        MouseArea { id: actionHit; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: action.triggered() }
    }
    MeltPopup {
        id: settingsPanel
        anchor.window: pill
        theme: pill.theme
        trigger: settingsGlyph
        open: pill.popup === "settings"
        duration: pill.popupMs
        panelWidth: 280
        panelHeight: settingsRows.implicitHeight + 2 * Glass.panelPad
        Column {
            id: settingsRows
            x: Glass.panelPad; y: x
            width: settingsPanel.panelWidth - 2 * x
            spacing: 0
            Glyph { text: "HUD appearance · all outputs"; font.bold: true }
            Repeater {
                model: pill.shellState.topbarOptions || []
                delegate: SettingsAction {
                    required property var modelData
                    label: (pill.shown(modelData.key) ? "✓  " : "○  ") + modelData.label
                    onTriggered: pill.shellState.setTopbarShown(modelData.key, !pill.shown(modelData.key))
                }
            }
            Text {
                width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
                text: pill.shellState.topbarError || ""; visible: text !== ""
                color: pill.theme.error; font.pixelSize: pill.theme.smallFontSize
            }
        }
    }
    MeltPopup {
        id: batteriesPanel
        anchor.window: pill
        theme: pill.theme
        trigger: batteryItems
        open: pill.popup === "batteries" || pill.batteryHoverOpen
        duration: pill.popupMs
        panelWidth: Math.min(340, pill.outputWidth - 2 * Glass.gap)
        panelHeight: batteryRows.implicitHeight + 2 * Glass.panelPad
        Column {
            id: batteryRows
            x: Glass.panelPad; y: x; width: batteriesPanel.panelWidth - 2 * x
            HoverHandler { onHoveredChanged: pill.batteryPanelHovered = hovered }
            Glyph { text: "Device batteries"; font.bold: true }
            Repeater {
                model: pill.batteryDevices
                delegate: Item {
                    id: batteryRow
                    required property var modelData
                    width: batteryRows.width
                    height: Math.max(Glass.bar, batteryDetails.implicitHeight + 2 * Glass.inset)
                    GlassSurface {
                        anchors.fill: parent
                        color: batteryHit.containsMouse ? pill.theme.withAlpha(pill.theme.accent, pill.theme.hoverOpacity) : "transparent"
                    }
                    Glyph {
                        id: deviceIcon
                        x: Glass.gap
                        anchors.verticalCenter: parent.verticalCenter
                        width: Math.max(Glass.bar, implicitWidth + Glass.gap)
                        text: pill.batteryGlyphs(batteryRow.modelData)
                        color: batteryRow.modelData.state === UPowerDeviceState.Charging ? pill.theme.success : pill.theme.fg
                    }
                    Column {
                        id: batteryDetails
                        anchors.left: deviceIcon.right
                        anchors.right: parent.right
                        anchors.rightMargin: Glass.gap
                        anchors.verticalCenter: parent.verticalCenter
                        Text {
                            width: parent.width
                            text: pill.batteryName(batteryRow.modelData)
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                            font.family: pill.theme.fontFamily
                            font.pixelSize: pill.theme.fontSize
                            color: pill.theme.fg
                        }
                        Text {
                            width: parent.width
                            text: pill.batteryKind(batteryRow.modelData) + " · " + Math.round(batteryRow.modelData.percentage * 100) + "% · "
                                + UPowerDeviceState.toString(batteryRow.modelData.state)
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                            font.family: pill.theme.fontFamily
                            font.pixelSize: pill.theme.smallFontSize
                            color: pill.theme.muted
                        }
                    }
                    MouseArea {
                        id: batteryHit
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: pill.run("power_settings")
                    }
                }
            }
        }
    }




    // System monitor details (SysmonDetails.qml), grown out of the monitor items.
    MeltPopup {
        id: sysmonPanel
        anchor.window: pill
        theme: pill.theme
        trigger: sysmonItems
        open: (pill.popup === "sysmon" || pill.sysmonHoverOpen) && sysmonItems.visible
        duration: pill.popupMs
        panelWidth: Math.min(sysmonDetails.implicitWidth + 2 * Glass.panelPad, pill.outputWidth - 2 * Glass.gap)
        panelHeight: sysmonDetails.implicitHeight + 2 * Glass.panelPad
        // hover anywhere the popup takes the pointer (its glass: over the trigger as it grows, then the panel)
        Item {
            parent: sysmonPanel.contentItem
            anchors.fill: parent
            HoverHandler { id: sysmonPanelHover }
        }
        Item {
            anchors.fill: parent
            SysmonDetails {
                id: sysmonDetails
                theme: pill.theme
                live: sysmonPanel.open || sysmonPanel.visible
                x: Glass.panelPad
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }

    // Native month calendar, grown out of the date.
    MeltPopup {
        id: calendarPanel
        anchor.window: pill
        theme: pill.theme
        trigger: pill.mobile ? timeGlyph : dateGlyph
        open: pill.popup === "calendar"
        duration: pill.popupMs
        panelWidth: calendar.implicitWidth + 2 * Glass.panelPad
        panelHeight: calendar.implicitHeight + 2 * Glass.panelPad
        Item {
            anchors.fill: parent
            Column {
                id: calendar
                anchors.centerIn: parent
                spacing: Glass.inset
                property date shown: new Date(pill.now.getFullYear(), pill.now.getMonth(), 1)
                readonly property var locale: (pill.bar.week_starts_on === 0) ? Qt.locale("en_US") : Qt.locale("en_GB")
                Row {
                    spacing: Glass.gap
                    anchors.horizontalCenter: parent.horizontalCenter
                    Glyph {
                        text: "\u{f0141}"
                        Hit { onClicked: calendar.shown = new Date(calendar.shown.getFullYear(), calendar.shown.getMonth() - 1, 1) }
                    }
                    Glyph {
                        text: Qt.formatDate(calendar.shown, "MMMM yyyy")
                        font.bold: true
                        width: grid.width - 2 * (Glass.bar + Glass.gap)
                        horizontalAlignment: Text.AlignHCenter
                        Hit { onClicked: calendar.shown = new Date(pill.now.getFullYear(), pill.now.getMonth(), 1) }
                    }
                    Glyph {
                        text: "\u{f0142}"
                        Hit { onClicked: calendar.shown = new Date(calendar.shown.getFullYear(), calendar.shown.getMonth() + 1, 1) }
                    }
                }
                DayOfWeekRow {
                    locale: calendar.locale
                    width: grid.width
                    delegate: Text {
                        text: shortName
                        color: pill.theme.subtle
                        font.family: pill.theme.fontFamily
                        font.pixelSize: pill.theme.smallFontSize
                        horizontalAlignment: Text.AlignHCenter
                    }
                }
                MonthGrid {
                    id: grid
                    month: calendar.shown.getMonth()
                    year: calendar.shown.getFullYear()
                    locale: calendar.locale
                    width: 7 * Glass.bar
                    delegate: GlassSurface {
                        required property var model
                        implicitWidth: Glass.bar
                        implicitHeight: Glass.bar
                        color: model.today ? pill.theme.withAlpha(pill.theme.accent, pill.theme.selectionOpacity) : "transparent"
                        Text {
                            anchors.centerIn: parent
                            text: model.day
                            color: model.month === grid.month ? pill.theme.fg : pill.theme.muted
                            font.family: pill.theme.fontFamily
                            font.pixelSize: pill.theme.fontSize
                            font.bold: model.today
                        }
                    }
                }
            }
        }
    }

    // Power, session and system settings, grown out of the power glyph.
    MeltPopup {
        id: powerPanel
        anchor.window: pill
        theme: pill.theme
        trigger: powerGlyph
        align: "right"
        open: pill.popup === "power"
        duration: pill.popupMs
        panelWidth: 240
        readonly property var items: [
            { key: "settings", icon: "\u{f0493}", label: "Settings" },
            { key: "display", icon: "\u{f0379}", label: "Display" },
            { key: "sound", icon: "\u{f057e}", label: "Sound" },
            { key: "network", icon: "\u{f06f3}", label: "Network" },
            { key: "power_settings", icon: "\u{f06a5}", label: "Power" },
            { key: "account", icon: "\u{f0004}", label: "Account" },
            { key: "", icon: "", label: "" },
            { key: "lock", icon: "\u{f033e}", label: "Lock" },
            { key: "logout", icon: "\u{f0343}", label: "Log out" },
            { key: "suspend", icon: "\u{f04b2}", label: "Suspend" },
            { key: "reboot", icon: "\u{f0709}", label: "Restart" },
            { key: "poweroff", icon: "\u{f0425}", label: "Shut down" },
        ]
        readonly property real contentHeight: {
            let h = 0
            for (const it of items) {
                if (!it.key) h += Glass.gap
                else if (pill.commands[it.key]) h += Glass.bar
            }
            return Math.max(h, menu.implicitHeight)
        }
        panelHeight: contentHeight + 2 * Glass.panelPad
        Item {
            anchors.fill: parent
            Column {
                id: menu
                x: Glass.panelPad
                y: Glass.panelPad
                width: parent.width - 2 * Glass.panelPad
                Glyph { text: "This computer"; font.bold: true }
                Repeater {
                    model: powerPanel.items
                    delegate: GlassSurface {
                        required property var modelData
                        width: menu.width
                        height: modelData.key ? Glass.bar : Glass.gap
                        visible: !modelData.key || !!pill.commands[modelData.key]
                        color: rowHit.containsMouse && modelData.key ? pill.theme.withAlpha(pill.theme.accent, pill.theme.hoverOpacity) : "transparent"
                        Rectangle {
                            visible: !modelData.key
                            anchors.centerIn: parent
                            width: parent.width - 2 * Glass.gap
                            height: Glass.hairline
                            color: pill.theme.outlineColor
                        }
                        Row {
                            visible: !!modelData.key
                            anchors.verticalCenter: parent.verticalCenter
                            x: Glass.gap
                            spacing: Glass.gap
                            Glyph { text: modelData.icon; height: parent.parent.height }
                            Glyph { text: modelData.label; height: parent.parent.height }
                        }
                        MouseArea {
                            id: rowHit
                            anchors.fill: parent
                            hoverEnabled: true
                            enabled: !!modelData.key
                            cursorShape: Qt.PointingHandCursor
                            onClicked: pill.run(modelData.key)
                        }
                    }
                }
            }
        }
    }
}
