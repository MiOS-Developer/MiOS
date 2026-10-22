// AI-hint: Per-output fullscreen edge reveal. While a real fullscreen window (Hyprland fullscreen
// mode 2/3; a maximized window keeps the normal bars) is on one of this output's visible workspaces,
// the top bar (status island + app-menu pills) and the dock stay hidden and reveal as overlay-layer
// surfaces above the fullscreen window when the pointer touches the top / bottom screen edge, then
// hide again once the pointer has left them for the hide delay. Nothing reserves space meanwhile, so
// the fullscreen window is never resized. Top: [desktop.topbar] fullscreen_reveal*; the bottom edge
// reuses the dock's own hot zone ([theme.shell] dock_fullscreen_reveal, Sidebar.qml/DockHotZone.qml).
// A notification reveals the status island alone (islandShown) while its blob grows out of it (Notifications.qml).
// Reserved rows: the status island (top) and the dock (bottom) register here (topSurface / bottomSurface); a guard
// checks that the compositor's reserved area for this output holds their rows while no fullscreen window hides them
// and re-asserts lost zones (IPC target "reserve-<output>": state / reassert; tests/test-shell-reserve.py).
// AI-related: usr/share/mios/quickshell/PanelWindow.qml, usr/share/mios/quickshell/AppMenu.qml,
// usr/share/mios/quickshell/Notifications.qml,
// usr/share/mios/quickshell/Sidebar.qml, usr/share/mios/quickshell/Config.qml, mios.toml [desktop.topbar]
import QtQuick
import Quickshell as QS
import Quickshell.Hyprland
import Quickshell.Io
import Quickshell.Wayland

QS.Scope {
    id: edge
    required property var screen
    property QtObject theme: Theme {}
    readonly property var bar: theme.topbar || ({})
    readonly property bool topEnabled: bar.fullscreen_reveal !== false
    readonly property int revealPx: bar.fullscreen_reveal_px !== undefined ? bar.fullscreen_reveal_px : 2
    readonly property int hideDelay: bar.fullscreen_hide_delay_ms !== undefined ? bar.fullscreen_hide_delay_ms : 600

    // ---- fullscreen on this output ---------------------------------------------------------------
    // Hyprland's client list, not the foreign-toplevel fullscreen flag: that one stays set for a
    // fullscreen window on a workspace nobody is looking at.
    readonly property var monitor: screen ? Hyprland.monitorFor(screen) : null
    readonly property bool fullscreen: {
        const m = monitor
        if (!m) return false
        const ws = m.activeWorkspace ? m.activeWorkspace.id : null
        const mo = m.lastIpcObject || {}
        const sp = mo.specialWorkspace ? mo.specialWorkspace.id : 0
        return Hyprland.toplevels.values.some(tl => {
            const o = tl.lastIpcObject
            return !!o && !!o.workspace && o.fullscreen >= 2 && o.monitor === m.id && !o.hidden
                && (o.workspace.id === ws || (sp !== 0 && o.workspace.id === sp))
        })
    }
    function refresh() { Hyprland.refreshToplevels(); Hyprland.refreshMonitors() }
    Connections {
        target: Hyprland
        function onRawEvent(event) {
            const n = event.name
            if (n === "fullscreen" || n === "workspacev2" || n === "activespecialv2" || n === "activespecial"
                || n === "openwindow" || n === "closewindow" || n === "movewindowv2" || n === "focusedmonv2"
                || n === "activewindowv2")
                edge.refresh()
            // anything that re-arranges the output's layers or outputs: verify the reserved rows once it settled
            if (n === "fullscreen" || n === "openlayer" || n === "closelayer" || n === "monitoraddedv2"
                || n === "monitorremovedv2" || n === "configreloaded" || n === "workspacev2")
                reserveSettle.restart()
        }
    }
    Component.onCompleted: refresh()

    // ---- reserved rows guard ---------------------------------------------------------------------
    // The island (PanelWindow.qml) and the dock (Sidebar.qml) reserve their rows with layer-shell exclusive zones;
    // Hyprland's reserved area for the output is what tiled windows (and miosd placement) keep clear of, and one
    // surface's bad zone empties all of it: Hyprland drops the whole reserved area when the zones no longer fit the
    // output. Quickshell only sends layer state that differs from what it believes it committed, so a zone the
    // compositor lost (or a surface stuck at a stale zone / mode) is never re-sent by itself. Each surface reports
    // the reservation it expects (reserveExpectedNow(): its zone plus the edge margin Hyprland adds, 0 when it
    // reserves nothing, computed from the current inputs, not from a binding that could be stale); whenever the
    // compositor holds less while no fullscreen window hides the bars, twice in a row (transitions settle first),
    // the guard re-asserts: 1. nudge (zone + 1, then zone: always re-sent), 2. recreate the surface (a new layer
    // surface sends its whole state), 3. back off and report -- another client's oversized zone empties the area.
    property var topSurface: null
    property var bottomSurface: null
    readonly property var reservedNow: {
        const o = monitor ? monitor.lastIpcObject : null
        const r = o ? o.reserved : null
        return r && r.length === 4 ? [Number(r[0]), Number(r[1]), Number(r[2]), Number(r[3])] : null
    }
    property int reserveStage: 0          // 0 watching, 1 nudged, 2 recreated, 3 backed off
    property int reserveShortCount: 0
    property int reserveReasserts: 0
    property int reserveBackoff: 0
    property string reserveLast: ""
    function reserveExpected() {
        return { top: topSurface && typeof topSurface.reserveExpectedNow === "function" ? topSurface.reserveExpectedNow() : 0,
                 bottom: bottomSurface && typeof bottomSurface.reserveExpectedNow === "function" ? bottomSurface.reserveExpectedNow() : 0 }
    }
    function reserveSnapshot() {
        const r = reservedNow, e = reserveExpected()
        return { output: screen ? screen.name : "", fullscreen: fullscreen, reserved: r, expected: e,
                 top_short: !!r && !fullscreen && e.top > 0 && r[1] < e.top,
                 bottom_short: !!r && !fullscreen && e.bottom > 0 && r[3] < e.bottom,
                 stage: reserveStage, reasserts: reserveReasserts, last: reserveLast, qt_bindings_frozen: qtBindingsFrozen,
                 top: topSurface && typeof topSurface.reserveDiag === "function" ? topSurface.reserveDiag() : null,
                 bottom: bottomSurface && typeof bottomSurface.reserveDiag === "function" ? bottomSurface.reserveDiag() : null }
    }
    property int reserveFailures: 0
    // Frozen C++ property bindings. Quickshell builds before upstream 1bd5b083 (2026-03-17, "hyprland/ipc: add null
    // checks and ws preinit to toplevel object init") return from HyprlandToplevel::updateFromObject() between
    // Qt::beginPropertyUpdateGroup() and endPropertyUpdateGroup() whenever a j/clients entry names a workspace the shell
    // does not track; the update group then never closes, and every computed (bindable) property in the shell process
    // stops following its inputs -- including the exclusive zone Quickshell sends for each layer surface, which keeps the
    // value it had (0 for surfaces created afterwards, e.g. after a reload). Neither a nudge nor a new surface can send
    // the right zone then; only a new shell process can. Probe: a fresh Item bound to another (Item.width is a bindable
    // property) and a fresh WrapperItem (computed C++ bindings) must follow their inputs.
    property bool qtBindingsFrozen: false
    property bool reserveFrozenNotified: false
    function probeQtBindings() {
        let frozen = false
        try {
            const o = Qt.createQmlObject('import QtQuick; import Quickshell.Widgets; Item { id: a; width: 10; '
                + 'property Item b: Item { width: a.width + 1 } '
                + 'property Item w: WrapperItem { margin: 3; Item { implicitWidth: 50; implicitHeight: 20 } } }', edge, "reserveProbe")
            o.width = 20
            frozen = o.b.width !== 21 || o.w.implicitWidth !== 56
            o.destroy()
        } catch (e) {
            console.warn("[reserve] binding probe failed: " + e)
        }
        if (frozen && !qtBindingsFrozen)
            console.warn("[reserve] " + (screen ? screen.name : "?") + ": Qt property bindings are frozen in this shell process "
                         + "(Quickshell HyprlandToplevel update-group leak, fixed upstream in 1bd5b083): the layer surfaces keep "
                         + "sending stale exclusive zones; restart the shell (systemctl --user restart mios-shell)")
        qtBindingsFrozen = frozen
        return frozen
    }
    function reserveAct(snap, why, force) {
        const targets = [(force ? snap.expected.top > 0 : snap.top_short) ? topSurface : null,
                         (force ? snap.expected.bottom > 0 : snap.bottom_short) ? bottomSurface : null].filter(t => !!t)
        if (!targets.length) return
        reserveReasserts++
        if (reserveStage === 0) {
            reserveStage = 1
            reserveLast = "nudge"
            for (const t of targets) t.nudgeReserve()
        } else if (reserveStage === 1 && !probeQtBindings()) {
            reserveStage = 2
            reserveLast = "recreate"
            for (const t of targets) t.recreateReserve()
        } else {
            // neither helped: not this shell's zones (another client's oversized zone empties the area); retry later,
            // less and less often (1, 2, 4 ... 10 minutes)
            reserveStage = 3
            reserveLast = "backoff"
            reserveBackoff = Math.min(120, 12 * Math.pow(2, reserveFailures))
            reserveFailures++
            if (qtBindingsFrozen) {
                reserveLast = "frozen"
                // Hyprland's own notification (the shell's toasts are bindings too); at most once an hour across outputs
                // and shell reloads (a marker in the runtime dir)
                if (!reserveFrozenNotified) {
                    reserveFrozenNotified = true
                    QS.Quickshell.execDetached(["sh", "-c",
                        'f="${XDG_RUNTIME_DIR:-/tmp}/mios-shell-bindings-frozen.notified"; '
                        + '[ -n "$(find "$f" -mmin -60 2>/dev/null)" ] && exit 0; : > "$f"; exec hyprctl notify 0 30000 0 "$1"', "sh",
                        "MiOS shell: its Qt property bindings froze (Quickshell update-group leak), so the bar and dock reserve no "
                        + "space and tiled windows run under them. Restart the shell: systemctl --user restart mios-shell"])
                }
            }
        }
        console.info("[reserve] " + (snap.output || "?") + ": compositor reserved " + JSON.stringify(snap.reserved)
                     + " < expected " + JSON.stringify(snap.expected) + " (" + why + "); " + reserveLast
                     + "; top " + JSON.stringify(snap.top) + "; bottom " + JSON.stringify(snap.bottom))
        reserveSettle.restart()
    }
    function reserveEvaluate(why) {
        const snap = reserveSnapshot()
        if (!snap.reserved) return snap
        if (!snap.top_short && !snap.bottom_short) {
            if (reserveStage > 0)
                console.info("[reserve] " + snap.output + ": reserved " + JSON.stringify(snap.reserved) + " restored after " + reserveLast)
            reserveStage = 0
            reserveFailures = 0
            reserveShortCount = 0
            return snap
        }
        if (reserveStage >= 3) return snap
        reserveShortCount++
        if (reserveShortCount >= 2) { reserveShortCount = 0; reserveAct(snap, why) }
        else reserveSettle.restart()
        return snap
    }
    // verify a short while after something re-arranged the output (and right after load)
    Timer {
        id: reserveSettle
        interval: 1200
        onTriggered: { Hyprland.refreshMonitors(); reserveVerify.restart() }
    }
    Timer {
        id: reserveVerify
        interval: 400
        onTriggered: edge.reserveEvaluate("check")
    }
    // and now and then: a zone can go missing without any event this guard listens to
    Timer {
        interval: 5000
        repeat: true
        running: !!edge.topSurface || !!edge.bottomSurface
        onTriggered: {
            if (edge.reserveStage >= 3 && --edge.reserveBackoff <= 0) edge.reserveStage = 0
            Hyprland.refreshMonitors()
            reserveVerify.restart()
        }
    }
    onTopSurfaceChanged: reserveSettle.restart()
    onBottomSurfaceChanged: reserveSettle.restart()
    IpcHandler {
        target: "reserve-" + (edge.screen ? edge.screen.name : "")
        function state(): string { return JSON.stringify(edge.reserveSnapshot()) }
        function probe(): string { return JSON.stringify({ qt_bindings_frozen: edge.probeQtBindings() }) }
        // re-assert now (stage 1, then 2 on the next call while still short)
        function reassert(): string {
            const snap = edge.reserveSnapshot()
            if (edge.reserveStage >= 3) edge.reserveStage = 0
            edge.reserveAct(snap, "ipc", true)
            return JSON.stringify(edge.reserveSnapshot())
        }
    }

    // ---- top edge: status island + app-menu pills ------------------------------------------------
    // Each top surface reports its hover / open popup here; any of them holds the row up.
    property bool zoneHovered: false
    property bool islandHovered: false
    property bool menuHovered: false
    property bool islandPopup: false
    property bool menuPopup: false
    readonly property bool topActive: topEnabled && fullscreen
    property bool topHeld: false
    readonly property bool topShown: topActive && (topHeld || islandPopup || menuPopup)
    readonly property bool topWanted: zoneHovered || islandHovered || menuHovered || islandPopup || menuPopup
    onTopWantedChanged: {
        if (topWanted) { topTimer.stop(); topHeld = true }
        else if (topHeld) topTimer.restart()
    }
    onTopActiveChanged: if (!topActive) { topTimer.stop(); topHeld = false }
    Timer {
        id: topTimer
        interval: edge.hideDelay
        onTriggered: edge.topHeld = edge.topWanted
    }
    // A notification (Notifications.qml) reveals the status island alone -- not the app-menu pills -- while its blob
    // is out, and lets it hide again the hide delay after the blob melted back into it.
    property bool notifyPopup: false
    property bool notifyHeld: false
    readonly property bool islandShown: topShown || (topActive && notifyHeld)
    onNotifyPopupChanged: {
        if (notifyPopup) { notifyTimer.stop(); notifyHeld = true }
        else if (notifyHeld) notifyTimer.restart()
    }
    Timer {
        id: notifyTimer
        interval: edge.hideDelay
        onTriggered: edge.notifyHeld = edge.notifyPopup
    }

    QS.PanelWindow {
        id: zone
        screen: edge.screen
        WlrLayershell.namespace: "mios-topbar-hotzone"
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
        anchors.top: true
        anchors.left: true
        anchors.right: true
        implicitHeight: Math.max(1, edge.revealPx)
        exclusiveZone: 0
        exclusionMode: QS.ExclusionMode.Ignore
        visible: edge.topActive
        color: "transparent"
        onVisibleChanged: if (!visible) edge.zoneHovered = false
        // Fully transparent but a real input region: the compositor delivers pointer enter here.
        Item {
            anchors.fill: parent
            HoverHandler { onHoveredChanged: edge.zoneHovered = hovered }
        }
    }
}
