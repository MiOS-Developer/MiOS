// AI-hint: The mios-shot selector, flash and toast endpoints in mios-shell (IPC target "shot"). `select <socket>`: mios-shot
// has already captured and frozen every output (its own mios-shot-freeze layers); this connects to its private socket,
// reads one session line (outputs, visible windows with their stableId, focused output, cursor) and draws, on every
// output, a transparent overlay (namespace mios-shot: a scrim with the selection cut out, the window under the pointer
// outlined) plus one glass toolbar (namespace mios-shot-bar, the unified Glass material) on the focused output. Drag =
// region, click = the window under the pointer (output mode: the output), toolbar/keys pick the mode and the action; one
// result line goes back and the overlay closes. The socket closing (mios-shot timed out or died) closes it too.
// `flash <output|"">` draws the post-capture flash; `toast <title> <body>` posts to the shell's notification toasts
// (mios-shot's fallback when no org.freedesktop.Notifications server answers). `finish <json>` / `state` drive and read
// a selection from tests. Keys: Enter = default action, Ctrl+C copy, Ctrl+S save, E edit, Space region/window,
// O output mode, Tab next output, arrows nudge 1 px (Shift 10 px), Esc cancel.
// AI-related: tools/native/mios-shot/src/main.rs (select_shell, session_json), usr/share/mios/quickshell/Config.qml,
// usr/share/mios/quickshell/Glass.qml, usr/share/mios/quickshell/Mobile.qml, usr/share/mios/mios.toml [desktop.screenshot]
import QtQuick
import Quickshell as QS
import Quickshell.Io
import Quickshell.Wayland

QS.Scope {
    id: shot
    property QtObject theme: Theme {}
    // Config.qml's notification bus (toast fallback).
    property QtObject bus: null

    property bool active: false
    property var session: ({})
    property string mode: "region"          // what a click or a drag picks: region | window | output
    property string action: "copysave"      // copy | save | copysave | edit
    property bool confirm: false            // keep a dragged selection for adjusting; commit with Enter / toolbar
    property real selX: 0
    property real selY: 0
    property real selW: 0
    property real selH: 0
    property bool dragging: false
    readonly property bool hasSel: selW >= 1 && selH >= 1
    property var hoverWin: null
    property string hoverOutput: ""
    property string focusOutput: ""
    property real pointerX: -1
    property real pointerY: -1

    // The toolbar maps a moment after the overlays: same layer, so the later surface stacks on top.
    property bool barShown: false
    onActiveChanged: { barShown = false; if (active) barTimer.restart() }
    property Timer barTimer: Timer { interval: 60; onTriggered: shot.barShown = shot.active }

    // Toolbar entries. group: mode (what a click/drag picks), action (what happens to the image), do (buttons), gap.
    readonly property var tools: [
        { group: "mode", key: "region", glyph: "\u2B1A", label: "Region" },
        { group: "mode", key: "window", glyph: "\u25A3", label: "Window" },
        { group: "mode", key: "output", glyph: "\u25AD", label: "Screen" },
        { group: "gap" },
        { group: "action", key: "copy", glyph: "\u29C9", label: "Copy" },
        { group: "action", key: "save", glyph: "\u2913", label: "Save" },
        { group: "action", key: "copysave", glyph: "\u29C9\u2913", label: "Both" },
        { group: "action", key: "edit", glyph: "\u270E", label: "Edit" },
        { group: "gap" },
        { group: "do", key: "commit", glyph: "\u2713", label: "" },
        { group: "do", key: "cancel", glyph: "\u2715", label: "" }
    ]

    readonly property var outputs: Array.isArray(session.outputs) ? session.outputs : []
    readonly property var windows: Array.isArray(session.windows) ? session.windows : []

    function outputGeom(name) {
        return outputs.find(o => o.name === name) || null
    }
    function outputAt(x, y) {
        const o = outputs.find(o => x >= o.x && x < o.x + o.w && y >= o.y && y < o.y + o.h)
        return o ? o.name : ""
    }
    function windowAt(x, y) {
        return windows.find(w => x >= w.x && x < w.x + w.w && y >= w.y && y < w.y + w.h) || null
    }
    function pointAt(x, y) {
        pointerX = x
        pointerY = y
        hoverOutput = outputAt(x, y) || hoverOutput
        hoverWin = mode === "output" ? null : windowAt(x, y)
    }

    Socket {
        id: sock
        onConnectionStateChanged: if (!connected && shot.active) shot.close()
        parser: SplitParser {
            onRead: data => shot.onSession(data)
        }
    }

    function begin(path) {
        if (sock.connected) finish({ cancel: true })
        sock.path = path
        sock.connected = true
    }
    function onSession(line) {
        let s
        try { s = JSON.parse(line) } catch (e) { finish({ cancel: true }); return }
        session = s
        selX = 0; selY = 0; selW = 0; selH = 0
        dragging = false
        mode = "region"
        action = s.copy === false ? "save" : (s.save === false ? "copy" : "copysave")
        confirm = s.confirm === true
        focusOutput = s.focused || (outputs.length ? outputs[0].name : "")
        hoverOutput = focusOutput
        const c = Array.isArray(s.cursor) ? s.cursor : null
        if (c && typeof c[0] === "number") pointAt(c[0], c[1])
        active = true
    }
    function finish(result) {
        if (sock.connected) {
            sock.write(JSON.stringify(result) + "\n")
            sock.flush()
        }
        close()
    }
    function close() {
        active = false
        dragging = false
        session = ({})
        sock.connected = false
    }
    // Commit what is picked now with action `act`.
    function commit(act) {
        const a = act || action
        if (mode === "output")
            finish({ kind: "output", name: hoverOutput || focusOutput, action: a })
        else if (hasSel && mode === "region")
            finish({ kind: "region", rect: [selX, selY, selW, selH], action: a })
        else if (hoverWin)
            finish({ kind: "window", id: hoverWin.id, action: a })
        else if (hasSel)
            finish({ kind: "region", rect: [selX, selY, selW, selH], action: a })
        else
            finish({ kind: "output", name: hoverOutput || focusOutput, action: a })
    }
    function nudge(dx, dy, grow) {
        if (!hasSel) return
        if (grow) { selW = Math.max(1, selW + dx); selH = Math.max(1, selH + dy) }
        else { selX += dx; selY += dy }
    }
    function nextOutput() {
        if (!outputs.length) return
        const i = outputs.findIndex(o => o.name === hoverOutput)
        hoverOutput = outputs[(i + 1) % outputs.length].name
        mode = "output"
    }

    // ---- flash ------------------------------------------------------------------------------------
    property string flashTarget: ""
    property int flashSerial: 0
    function flash(output) {
        flashTarget = output || ""
        flashSerial++
    }

    IpcHandler {
        target: "shot"
        function select(path: string): void { shot.begin(path) }
        function flash(output: string): void { shot.flash(output) }
        function toast(title: string, body: string): void { if (shot.bus) shot.bus.toast(title, body) }
        function finish(json: string): void {
            if (!shot.active) return
            let r
            try { r = JSON.parse(json) } catch (e) { r = { cancel: true } }
            shot.finish(r)
        }
        function state(): string {
            return JSON.stringify({ active: shot.active, mode: shot.mode, action: shot.action, outputs: shot.outputs.length,
                                    windows: shot.windows.length, focused: shot.focusOutput,
                                    hover_window: shot.hoverWin ? shot.hoverWin.id : "", selection: [shot.selX, shot.selY, shot.selW, shot.selH] })
        }
    }

    QS.Variants {
        model: QS.Quickshell.screens
        QS.Scope {
            id: perScreen
            required property var modelData
            readonly property var geom: shot.outputGeom(modelData.name) || ({ x: modelData.x, y: modelData.y, w: modelData.width, h: modelData.height })
            readonly property real ui: Mobile.uiScale(modelData.name)

            // ---- selection overlay -------------------------------------------------------------------
            QS.PanelWindow {
                id: overlay
                screen: perScreen.modelData
                visible: shot.active
                color: "transparent"
                anchors { top: true; bottom: true; left: true; right: true }
                exclusionMode: QS.ExclusionMode.Ignore
                WlrLayershell.layer: WlrLayer.Overlay
                WlrLayershell.namespace: "mios-shot"
                WlrLayershell.keyboardFocus: shot.active && perScreen.modelData.name === shot.focusOutput
                                             ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None

                readonly property real ox: perScreen.geom.x
                readonly property real oy: perScreen.geom.y
                // The highlighted rectangle in this overlay's coordinates: the selection, else what a click would take.
                readonly property var hi: {
                    if (shot.mode === "output")
                        return shot.hoverOutput === perScreen.modelData.name ? { x: 0, y: 0, w: width, h: height } : null
                    if (shot.hasSel && shot.mode === "region")
                        return { x: shot.selX - ox, y: shot.selY - oy, w: shot.selW, h: shot.selH }
                    if (shot.hoverWin && !shot.dragging)
                        return { x: shot.hoverWin.x - ox, y: shot.hoverWin.y - oy, w: shot.hoverWin.w, h: shot.hoverWin.h }
                    return null
                }
                readonly property color scrim: shot.theme.withAlpha("#000000", 0.38)

                Item {
                    id: keys
                    anchors.fill: parent
                    focus: true
                    Keys.onPressed: event => {
                        const ctrl = event.modifiers & Qt.ControlModifier
                        const step = (event.modifiers & Qt.ShiftModifier) ? 10 : 1
                        const grow = event.modifiers & Qt.AltModifier
                        switch (event.key) {
                        case Qt.Key_Escape: shot.finish({ cancel: true }); break
                        case Qt.Key_Return: case Qt.Key_Enter: shot.commit(); break
                        case Qt.Key_C: if (ctrl) shot.commit("copy"); break
                        case Qt.Key_S: if (ctrl) shot.commit("save"); break
                        case Qt.Key_E: shot.commit("edit"); break
                        case Qt.Key_Space: shot.mode = shot.mode === "window" ? "region" : "window"; break
                        case Qt.Key_O: shot.mode = "output"; break
                        case Qt.Key_Tab: shot.nextOutput(); break
                        case Qt.Key_Left: shot.nudge(-step, 0, grow); break
                        case Qt.Key_Right: shot.nudge(step, 0, grow); break
                        case Qt.Key_Up: shot.nudge(0, -step, grow); break
                        case Qt.Key_Down: shot.nudge(0, step, grow); break
                        default: return
                        }
                        event.accepted = true
                    }
                }

                // Scrim: four bands around the highlight (or the whole output when nothing is highlighted).
                Rectangle { color: overlay.scrim; x: 0; y: 0; width: parent.width; height: overlay.hi ? Math.max(0, overlay.hi.y) : parent.height }
                Rectangle {
                    visible: !!overlay.hi
                    color: overlay.scrim
                    x: 0; y: overlay.hi ? Math.max(0, overlay.hi.y) : 0
                    width: overlay.hi ? Math.max(0, overlay.hi.x) : 0
                    height: overlay.hi ? Math.max(0, Math.min(parent.height, overlay.hi.y + overlay.hi.h) - Math.max(0, overlay.hi.y)) : 0
                }
                Rectangle {
                    visible: !!overlay.hi
                    color: overlay.scrim
                    x: overlay.hi ? Math.min(parent.width, overlay.hi.x + overlay.hi.w) : 0
                    y: overlay.hi ? Math.max(0, overlay.hi.y) : 0
                    width: overlay.hi ? Math.max(0, parent.width - x) : 0
                    height: overlay.hi ? Math.max(0, Math.min(parent.height, overlay.hi.y + overlay.hi.h) - Math.max(0, overlay.hi.y)) : 0
                }
                Rectangle {
                    visible: !!overlay.hi
                    color: overlay.scrim
                    x: 0
                    y: overlay.hi ? Math.min(parent.height, overlay.hi.y + overlay.hi.h) : parent.height
                    width: parent.width
                    height: Math.max(0, parent.height - y)
                }
                // Outline of the highlight.
                Rectangle {
                    visible: !!overlay.hi
                    x: overlay.hi ? overlay.hi.x - 1 : 0
                    y: overlay.hi ? overlay.hi.y - 1 : 0
                    width: overlay.hi ? overlay.hi.w + 2 : 0
                    height: overlay.hi ? overlay.hi.h + 2 : 0
                    color: "transparent"
                    border.width: 2
                    border.color: shot.theme.cursor
                }
                // Size label under (or inside) the selection.
                Rectangle {
                    visible: shot.hasSel && shot.mode === "region" && overlay.hi !== null
                             && perScreen.modelData.name === (shot.outputAt(shot.selX, shot.selY) || shot.focusOutput)
                    readonly property real below: overlay.hi ? overlay.hi.y + overlay.hi.h + 6 : 0
                    x: overlay.hi ? Math.max(4, Math.min(parent.width - width - 4, overlay.hi.x)) : 0
                    y: below + height > parent.height - 4 ? (overlay.hi ? overlay.hi.y + 6 : 0) : below
                    width: sizeText.implicitWidth + 2 * shot.theme.edgeMargin
                    height: sizeText.implicitHeight + shot.theme.edgeMargin
                    radius: height / 2
                    color: shot.theme.withAlpha(shot.theme.bg, 0.85)
                    Text {
                        id: sizeText
                        anchors.centerIn: parent
                        text: Math.round(shot.selW) + " × " + Math.round(shot.selH)
                        color: shot.theme.fg
                        font.family: shot.theme.fontFamily
                        font.pixelSize: shot.theme.smallFontSize
                    }
                }

                MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.CrossCursor
                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                    property real sx: 0
                    property real sy: 0
                    property bool moved: false
                    onPressed: mouse => {
                        if (mouse.button === Qt.RightButton) { shot.finish({ cancel: true }); return }
                        sx = overlay.ox + mouse.x
                        sy = overlay.oy + mouse.y
                        moved = false
                        shot.focusOutput = perScreen.modelData.name
                    }
                    onPositionChanged: mouse => {
                        const gx = overlay.ox + mouse.x, gy = overlay.oy + mouse.y
                        if (pressed && shot.mode !== "output") {
                            if (!moved && Math.abs(gx - sx) + Math.abs(gy - sy) < 4) return
                            moved = true
                            shot.mode = "region"
                            shot.dragging = true
                            shot.selX = Math.min(sx, gx)
                            shot.selY = Math.min(sy, gy)
                            shot.selW = Math.abs(gx - sx)
                            shot.selH = Math.abs(gy - sy)
                        } else {
                            shot.pointAt(gx, gy)
                        }
                    }
                    onReleased: mouse => {
                        if (mouse.button !== Qt.LeftButton || !shot.active) return
                        const was = shot.dragging
                        shot.dragging = false
                        if (was && shot.hasSel) {
                            // Snap the edges to whole logical px.
                            const x0 = Math.floor(shot.selX), y0 = Math.floor(shot.selY)
                            shot.selW = Math.ceil(shot.selX + shot.selW) - x0
                            shot.selH = Math.ceil(shot.selY + shot.selH) - y0
                            shot.selX = x0
                            shot.selY = y0
                            if (!shot.confirm) shot.commit()
                        } else if (!was) {
                            shot.pointAt(overlay.ox + mouse.x, overlay.oy + mouse.y)
                            shot.selW = 0
                            shot.selH = 0
                            shot.commit()
                        }
                    }
                }
            }

            // ---- glass toolbar (focused output) -------------------------------------------------------
            QS.PanelWindow {
                id: bar
                screen: perScreen.modelData
                visible: shot.barShown && perScreen.modelData.name === shot.focusOutput
                color: "transparent"
                anchors.top: true
                // Below the top bar row (its edge margin + height) with one more edge margin of air.
                margins.top: Math.round((shot.theme.topHeight + 2 * shot.theme.edgeMargin) * perScreen.ui)
                exclusionMode: QS.ExclusionMode.Ignore
                WlrLayershell.layer: WlrLayer.Overlay
                // The shell's glass namespace rule (Mobile.layerNamespace): same material as the top bar on this output.
                WlrLayershell.namespace: Mobile.layerNamespace("mios-shot-bar", perScreen.modelData.name)
                WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
                implicitWidth: Math.ceil((tools.implicitWidth + 2 * shot.theme.edgeMargin) * perScreen.ui)
                implicitHeight: Math.ceil(row.height * perScreen.ui)

                readonly property real rowH: Math.max(Glass.barHeight + shot.theme.edgeMargin, Glass.touchTarget)
                // Glyphs only when the labelled toolbar would not fit the output (phones, the remote display).
                readonly property bool compact: (measure.implicitWidth + 4 * shot.theme.edgeMargin) * perScreen.ui > perScreen.geom.w
                Row {
                    id: measure
                    visible: false
                    spacing: Math.round(shot.theme.edgeMargin / 2)
                    Repeater {
                        model: shot.tools
                        delegate: Text {
                            required property var modelData
                            text: (modelData.glyph || "") + (modelData.label ? "  " + modelData.label : "")
                            font.family: shot.theme.fontFamily
                            font.pixelSize: shot.theme.fontSize
                            width: modelData.group === "gap" ? shot.theme.edgeMargin : implicitWidth + 2 * shot.theme.edgeMargin
                        }
                    }
                }

                Rectangle {
                    id: row
                    width: tools.implicitWidth + 2 * shot.theme.edgeMargin
                    height: bar.rowH
                    scale: perScreen.ui
                    transformOrigin: Item.TopLeft
                    // The window-control tab's material and corner rule (Glass.qml); hyprglass draws the glass behind it.
                    radius: Glass.cornerFor(height)
                    color: Glass.fill

                    Row {
                        id: tools
                        anchors.centerIn: parent
                        spacing: Math.round(shot.theme.edgeMargin / 2)
                        Repeater {
                            // group: mode (what a click/drag picks), action (what happens to the image), do (buttons)
                            model: shot.tools
                            delegate: Rectangle {
                                id: tool
                                required property var modelData
                                readonly property bool on: (modelData.group === "mode" && shot.mode === modelData.key)
                                                           || (modelData.group === "action" && shot.action === modelData.key)
                                visible: modelData.key === "commit" ? (shot.hasSel || shot.mode === "output" || shot.confirm)
                                         : modelData.key === "edit" ? shot.session.editor === true : true
                                width: modelData.group === "gap" ? (bar.compact ? 0 : shot.theme.edgeMargin)
                                       : Math.max(bar.rowH - shot.theme.edgeMargin, txt.implicitWidth + 2 * shot.theme.edgeMargin)
                                height: bar.rowH - shot.theme.edgeMargin
                                radius: height / 2
                                color: modelData.group === "gap" ? "transparent"
                                       : on ? shot.theme.withAlpha(shot.theme.accent, shot.theme.selectionOpacity)
                                       : (ma.containsMouse ? shot.theme.withAlpha(shot.theme.subtle, shot.theme.hoverOpacity * 0.5) : "transparent")
                                Text {
                                    id: txt
                                    visible: tool.modelData.group !== "gap"
                                    anchors.centerIn: parent
                                    text: (tool.modelData.glyph || "") + (tool.modelData.label && !bar.compact ? "  " + tool.modelData.label : "")
                                    color: shot.theme.fg
                                    font.family: shot.theme.fontFamily
                                    font.pixelSize: shot.theme.fontSize
                                    font.bold: tool.on
                                }
                                MouseArea {
                                    id: ma
                                    enabled: tool.modelData.group !== "gap"
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: {
                                        const m = tool.modelData
                                        if (m.group === "mode") {
                                            shot.mode = m.key
                                            if (m.key === "output" && !shot.hoverOutput) shot.hoverOutput = shot.focusOutput
                                        } else if (m.group === "action") shot.action = m.key
                                        else if (m.key === "commit") shot.commit()
                                        else shot.finish({ cancel: true })
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // ---- post-capture flash -------------------------------------------------------------------
            QS.PanelWindow {
                id: flashWin
                screen: perScreen.modelData
                visible: flashFx.running
                color: "transparent"
                anchors { top: true; bottom: true; left: true; right: true }
                exclusionMode: QS.ExclusionMode.Ignore
                WlrLayershell.layer: WlrLayer.Overlay
                WlrLayershell.namespace: "mios-shot-flash"
                WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
                mask: QS.Region {}
                Rectangle {
                    id: flashRect
                    anchors.fill: parent
                    color: "#ffffff"
                    opacity: 0
                }
                NumberAnimation {
                    id: flashFx
                    target: flashRect
                    property: "opacity"
                    from: 0.55
                    to: 0
                    duration: 220
                    easing.type: Easing.OutQuad
                }
                Connections {
                    target: shot
                    function onFlashSerialChanged() {
                        if (shot.flashTarget === "" || shot.flashTarget === perScreen.modelData.name) flashFx.restart()
                    }
                }
            }
        }
    }
}
