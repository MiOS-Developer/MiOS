// AI-hint: MiOS Displays -- the monitor settings app (arrange outputs, resolution, refresh rate, scale, rotation, enable/disable, primary) on the SSOT glass Theme. Every change goes through the native backend `mios-displays` (validated live apply via hyprctl eval hl.monitor, "keep these settings?" timed revert, save into the [desktop.hyprland.monitors] mios.d drop-in that mios-gen re-projects). IpcHandler "displays" drives the same paths for scripted use.
// AI-related: tools/native/mios-displays/src/main.rs, usr/share/mios/quickshell/Theme.qml, usr/share/mios/mios.toml [desktop.displays] [desktop.hyprland.monitors], usr/share/applications/mios-displays.desktop

import QtQuick
import QtQuick.Layouts
import Quickshell as QS
import Quickshell.Io
import "." as MiOS

QS.ShellRoot {
    id: root

    MiOS.Theme { id: theme }

    // Backend state (mios-displays list).
    property var monitors: []
    property var saved: ({})
    property var settings: ({})
    // Unapplied edits keyed by output: {mode, scale, transform, position, disabled}.
    property var edits: ({})
    property string selected: ""
    property int remaining: 0
    property string status: ""
    property bool statusError: false
    property bool busy: false
    property string primary: ""
    property var queue: []
    // Guided first-logon mode (mios-displays gui --setup / firstrun): the runtime
    // recommendation is preselected and Finish saves it and writes the per-user marker.
    readonly property bool setupMode: QS.Quickshell.env("MIOS_DISPLAYS_SETUP") === "1"
    property bool suggested: false
    property var recommendation: []
    property string queueAction: ""

    readonly property var selectedMonitor: monitors.find(m => m.name === selected) || null
    readonly property bool pending: remaining > 0
    readonly property bool dirty: Object.keys(edits).length > 0

    function value(name, key) {
        const e = edits[name]
        if (e && e[key] !== undefined) return e[key]
        const m = monitors.find(o => o.name === name)
        return m ? m[key] : undefined
    }
    function edit(name, key, v) {
        const all = Object.assign({}, edits)
        const e = Object.assign({}, all[name] || {})
        const m = monitors.find(o => o.name === name)
        if (m && m[key] === v) delete e[key]
        else e[key] = v
        if (Object.keys(e).length) all[name] = e
        else delete all[name]
        edits = all
    }
    function sizeOf(mode) { return mode ? mode.split("@")[0] : "" }
    function rateOf(mode) { return mode && mode.includes("@") ? mode.split("@")[1] : "" }
    function setStatus(text, error) { status = text; statusError = !!error }

    function run(args, action) {
        busy = true
        backend.action = action
        if (action !== "list") console.info("mios-displays " + args.join(" "))
        backend.exec(["mios-displays"].concat(args))
    }
    function refresh() { run(["list"], "list") }

    function applyEdits() {
        if (!dirty || busy) return
        const cmds = []
        for (const name of Object.keys(edits)) {
            const e = edits[name]
            const args = ["apply", name]
            if (e.disabled !== undefined) args.push(e.disabled ? "--disable" : "--enable")
            if (e.mode !== undefined) args.push("--mode", e.mode)
            if (e.scale !== undefined) args.push("--scale", String(e.scale))
            if (e.transform !== undefined) args.push("--transform", String(e.transform))
            if (e.position !== undefined) args.push("--position", e.position)
            cmds.push(args)
        }
        // Enable before disabling so an output stays lit between steps.
        cmds.sort((a, b) => (a.includes("--disable") ? 1 : 0) - (b.includes("--disable") ? 1 : 0))
        queue = cmds
        queueAction = "apply"
        nextInQueue()
    }
    function nextInQueue() {
        if (!queue.length) { queueAction = ""; refresh(); return }
        const q = queue.slice()
        const args = q.shift()
        queue = q
        run(args, "apply")
    }
    function recommend() { run(["suggest"], "suggest") }
    function finish() { save() }
    function skip() { run(["setup-done"], "setup-done") }
    function keep() { run(["confirm"], "confirm") }
    function revert() { run(["revert"], "revert") }
    function save() {
        const args = ["save"]
        if (primary) args.push("--primary", primary)
        run(args, "save")
    }

    Process {
        id: backend
        property string action: ""
        stdout: StdioCollector {
            onStreamFinished: {
                let r = null
                try { r = JSON.parse(text) } catch (e) { r = { ok: false, error: "mios-displays returned no JSON" } }
                root.busy = false
                const action = backend.action
                if (!r.ok) {
                    root.setStatus(r.error || "mios-displays failed", true)
                    root.queue = []
                    root.queueAction = ""
                    if (action !== "list") root.refresh()
                    return
                }
                if (action === "list") {
                    root.monitors = r.monitors || []
                    root.saved = r.saved || {}
                    root.settings = r.settings || {}
                    if (!root.selectedMonitor && root.monitors.length)
                        root.selected = (root.monitors.find(m => m.focused) || root.monitors[0]).name
                    const p = Object.keys(root.saved).find(k => root.saved[k].primary === true)
                    if (!root.primary) root.primary = p || ""
                    root.remaining = r.pending && r.pending.remaining ? r.pending.remaining : 0
                    if (root.setupMode && !root.suggested) { root.suggested = true; root.recommend() }
                } else if (action === "suggest") {
                    root.recommendation = r.outputs || []
                    for (const o of root.recommendation) {
                        root.edit(o.output, "mode", o.mode)
                        root.edit(o.output, "scale", o.scale)
                    }
                    root.setStatus(root.recommendation.map(o => o.output + ": " + o.mode.replace("@", " @ ") + " Hz, " + Math.round(o.scale * 100) + " %"
                        + (o.dpi ? " (" + Math.round(o.dpi) + " dpi)" : "")).join("   ")
                        + (root.dirty ? "   — Apply to try it." : "   — already in use."), false)
                } else if (action === "setup-done") {
                    Qt.quit()
                } else if (action === "apply") {
                    root.edits = ({})
                    if (r.monitors) root.monitors = r.monitors
                    if (r.pending) root.remaining = r.pending.remaining
                    root.setStatus("Applied " + r.applied.output + ".", false)
                    root.nextInQueue()
                } else if (action === "confirm") {
                    root.remaining = 0
                    root.setStatus(root.setupMode ? "Kept. Finish to save it for every login." : "Kept. Save to make this layout permanent.", false)
                } else if (action === "revert") {
                    root.remaining = 0
                    root.edits = ({})
                    root.setStatus(r.reverted ? "Reverted to the previous settings." : "Nothing to revert.", false)
                    root.refresh()
                } else if (action === "save") {
                    root.setStatus("Saved to " + r.path + " (re-projected into Hyprland).", false)
                    if (root.setupMode) root.skip()
                    else root.refresh()
                }
            }
        }
    }

    Timer {
        interval: 1000
        repeat: true
        running: root.remaining > 0
        onTriggered: {
            root.remaining = Math.max(0, root.remaining - 1)
            if (root.remaining === 0) {
                root.setStatus("Not confirmed: reverted to the previous settings.", false)
                revertCheck.restart()
            }
        }
    }
    // The backend watchdog performs the revert; re-read once it has run.
    Timer { id: revertCheck; interval: 1500; onTriggered: root.refresh() }

    IpcHandler {
        target: "displays"
        function list(): string { return JSON.stringify({ monitors: root.monitors, edits: root.edits, remaining: root.remaining, status: root.status }) }
        function select(output: string): void { root.selected = output }
        function set(output: string, key: string, value: string): void {
            const v = (key === "scale") ? Number(value) : (key === "transform") ? parseInt(value) : (key === "disabled") ? value === "true" : value
            root.edit(output, key, v)
        }
        function apply(): void { root.applyEdits() }
        function keep(): void { root.keep() }
        function revert(): void { root.revert() }
        function save(): void { root.save() }
        function refresh(): void { root.edits = ({}); root.refresh() }
        function recommend(): void { root.recommend() }
        function finish(): void { root.finish() }
        function skip(): void { root.skip() }
    }

    Component.onCompleted: refresh()

    // ---- widgets --------------------------------------------------------------------------

    component Label: Text {
        color: theme.fg
        font.family: theme.fontFamily
        font.pixelSize: theme.fontSize
        verticalAlignment: Text.AlignVCenter
    }

    component Pill: Rectangle {
        id: pill
        property string text: ""
        property bool primaryAction: false
        signal clicked()
        implicitWidth: pillLabel.implicitWidth + theme.edgeMargin * 4
        implicitHeight: theme.searchRowHeight - theme.edgeMargin
        radius: height / 2
        opacity: enabled ? 1 : 0.4
        color: primaryAction ? theme.withAlpha(theme.accent, pillMouse.containsMouse ? 1 : theme.selectionOpacity)
                             : theme.withAlpha(theme.subtle, pillMouse.containsMouse ? theme.hoverOpacity / 2 : 0.08)
        border.width: 1
        border.color: theme.outlineColor
        Label { id: pillLabel; anchors.centerIn: parent; text: pill.text }
        MouseArea {
            id: pillMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: pill.clicked()
        }
    }

    // A value chooser: click opens the option list in the window's overlay layer.
    component Chooser: Rectangle {
        id: chooser
        property var options: []        // [{value, label}]
        property var current
        signal chosen(var value)
        Layout.fillWidth: true
        implicitHeight: theme.searchRowHeight - theme.edgeMargin
        radius: theme.edgeMargin
        color: theme.withAlpha(theme.subtle, chooserMouse.containsMouse ? theme.hoverOpacity / 2 : 0.08)
        border.width: 1
        border.color: theme.outlineColor
        readonly property var currentOption: options.find(o => String(o.value) === String(current))
        Label {
            anchors.fill: parent
            anchors.leftMargin: theme.edgeMargin * 2
            anchors.rightMargin: theme.edgeMargin * 2
            text: (chooser.currentOption ? chooser.currentOption.label : String(chooser.current === undefined ? "" : chooser.current)) + "  ▾"
            elide: Text.ElideRight
        }
        MouseArea {
            id: chooserMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: overlay.open(chooser)
        }
    }

    // ---- window ---------------------------------------------------------------------------

    QS.FloatingWindow {
        id: win
        title: "MiOS Displays"
        // [desktop.displays] window/canvas geometry; literals are the degrade-open fallback.
        implicitWidth: root.settings.window_width_px || 760
        implicitHeight: root.settings.window_height_px || 620
        color: "transparent"
        // Closing the toplevel ends the app (it is its own Quickshell instance).
        onClosed: Qt.quit()

        Rectangle {
            anchors.fill: parent
            radius: theme.radius
            // A settings form needs a readable pane: [desktop.displays].fill_opacity_pct over the shell glass.
            color: theme.withAlpha(theme.bg, Math.max(theme.panelOpacity, (root.settings.fill_opacity_pct || 88) / 100))
            border.width: 1
            border.color: theme.outlineColor

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: theme.edgeMargin * 3
                spacing: theme.edgeMargin * 1.5

                RowLayout {
                    Layout.fillWidth: true
                    Label { text: root.setupMode ? "Display setup" : "Displays"; font.pixelSize: theme.titleFontSize; font.bold: true; Layout.fillWidth: true }
                    Label { text: root.busy ? "working…" : ""; color: theme.muted }
                    Pill { text: "Recommended"; enabled: !root.busy && !root.pending; onClicked: root.recommend() }
                    Pill { text: "Refresh"; enabled: !root.busy; onClicked: { root.edits = ({}); root.refresh() } }
                }

                // Guided first-logon intro.
                Label {
                    visible: root.setupMode
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    color: theme.subtle
                    text: "Welcome to MiOS. Each connected display has its recommended settings preselected: native resolution, "
                        + "the highest refresh rate it offers, and a scale from its physical size. Apply to try them "
                        + "(they revert in " + (root.settings.confirm_seconds || 15) + " s unless you keep them), adjust anything, then Finish."
                }

                // Arrangement: drag outputs; positions snap to neighbouring edges.
                Rectangle {
                    id: canvas
                    Layout.fillWidth: true
                    Layout.preferredHeight: root.settings.canvas_height_px || 220
                    radius: theme.edgeMargin * 2
                    color: theme.withAlpha(theme.bg, 0.35)
                    border.width: 1
                    border.color: theme.outlineColor
                    clip: true

                    function logical(m) {
                        const mode = root.value(m.name, "mode") || m.mode
                        const sz = root.sizeOf(mode).split("x").map(Number)
                        const t = root.value(m.name, "transform") || 0
                        const s = root.value(m.name, "scale") || 1
                        const w = (t % 2 ? sz[1] : sz[0]) / s, h = (t % 2 ? sz[0] : sz[1]) / s
                        const pos = String(root.value(m.name, "position") || "0x0").split("x").map(Number)
                        return { x: isNaN(pos[0]) ? m.x : pos[0], y: isNaN(pos[1]) ? m.y : pos[1], w: w || 1, h: h || 1 }
                    }
                    readonly property var boxes: root.monitors.filter(m => !root.value(m.name, "disabled")).map(m => Object.assign({ name: m.name }, logical(m)))
                    readonly property real minX: boxes.length ? Math.min(...boxes.map(b => b.x)) : 0
                    readonly property real minY: boxes.length ? Math.min(...boxes.map(b => b.y)) : 0
                    readonly property real spanW: boxes.length ? Math.max(...boxes.map(b => b.x + b.w)) - minX : 1
                    readonly property real spanH: boxes.length ? Math.max(...boxes.map(b => b.y + b.h)) - minY : 1
                    readonly property real k: Math.min((width - 80) / spanW, (height - 60) / spanH)
                    readonly property real ox: (width - spanW * k) / 2
                    readonly property real oy: (height - spanH * k) / 2

                    Repeater {
                        model: canvas.boxes
                        Rectangle {
                            id: box
                            required property var modelData
                            x: canvas.ox + (modelData.x - canvas.minX) * canvas.k
                            y: canvas.oy + (modelData.y - canvas.minY) * canvas.k
                            width: modelData.w * canvas.k
                            height: modelData.h * canvas.k
                            radius: theme.edgeMargin
                            color: theme.withAlpha(modelData.name === root.selected ? theme.accent : theme.subtle,
                                                   modelData.name === root.selected ? theme.selectionOpacity : 0.18)
                            border.width: modelData.name === root.primary ? 2 : 1
                            border.color: modelData.name === root.primary ? theme.cursor : theme.outlineColor
                            Column {
                                anchors.centerIn: parent
                                Label { anchors.horizontalCenter: parent.horizontalCenter; text: box.modelData.name; font.bold: true }
                                Label {
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    text: Math.round(box.modelData.w) + " × " + Math.round(box.modelData.h)
                                    color: theme.subtle
                                    font.pixelSize: theme.smallFontSize
                                }
                            }
                            MouseArea {
                                anchors.fill: parent
                                drag.target: canvas.boxes.length > 1 ? box : null
                                cursorShape: canvas.boxes.length > 1 ? Qt.SizeAllCursor : Qt.PointingHandCursor
                                onPressed: root.selected = box.modelData.name
                                onReleased: {
                                    if (canvas.boxes.length < 2) return
                                    let lx = (box.x - canvas.ox) / canvas.k + canvas.minX
                                    let ly = (box.y - canvas.oy) / canvas.k + canvas.minY
                                    const snap = root.settings.snap_px || 64   // logical px
                                    for (const o of canvas.boxes) {
                                        if (o.name === box.modelData.name) continue
                                        for (const [cand, edge] of [[o.x + o.w, lx], [o.x - box.modelData.w, lx], [o.x, lx]])
                                            if (Math.abs(cand - edge) < snap) lx = cand
                                        for (const [cand, edge] of [[o.y + o.h, ly], [o.y - box.modelData.h, ly], [o.y, ly]])
                                            if (Math.abs(cand - edge) < snap) ly = cand
                                    }
                                    root.edit(box.modelData.name, "position", Math.round(lx) + "x" + Math.round(ly))
                                }
                            }
                        }
                    }
                    Label {
                        anchors.bottom: parent.bottom
                        anchors.right: parent.right
                        anchors.margins: theme.edgeMargin
                        text: canvas.boxes.length > 1 ? "Drag to arrange" : ""
                        color: theme.muted
                        font.pixelSize: theme.smallFontSize
                    }
                }

                // Output tabs (includes disabled outputs, which have no box above).
                Flow {
                    Layout.fillWidth: true
                    spacing: theme.edgeMargin
                    Repeater {
                        model: root.monitors
                        Pill {
                            required property var modelData
                            text: modelData.name + (root.value(modelData.name, "disabled") ? " (off)" : "")
                            primaryAction: modelData.name === root.selected
                            onClicked: root.selected = modelData.name
                        }
                    }
                }

                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    columnSpacing: theme.edgeMargin * 2
                    rowSpacing: theme.edgeMargin
                    visible: !!root.selectedMonitor
                    readonly property var m: root.selectedMonitor || ({ name: "", modes: [] })
                    readonly property string mode: root.value(m.name, "mode") || ""

                    Label { text: "Output"; color: theme.subtle }
                    Label { text: parent.m.name + "  —  " + (parent.m.description || ""); elide: Text.ElideRight; Layout.fillWidth: true }

                    Label { text: "Enabled"; color: theme.subtle }
                    Chooser {
                        options: [{ value: false, label: "On" }, { value: true, label: "Off" }]
                        current: !!root.value(parent.m.name, "disabled")
                        onChosen: v => root.edit(parent.m.name, "disabled", v)
                    }

                    Label { text: "Resolution"; color: theme.subtle }
                    Chooser {
                        options: [...new Set(parent.m.modes.map(root.sizeOf))].map(s => ({ value: s, label: s.replace("x", " × ") }))
                        current: root.sizeOf(parent.mode)
                        onChosen: v => {
                            const best = parent.m.modes.filter(md => root.sizeOf(md) === v)
                                .sort((a, b) => Number(root.rateOf(b)) - Number(root.rateOf(a)))[0]
                            root.edit(parent.m.name, "mode", best)
                        }
                    }

                    Label { text: "Refresh rate"; color: theme.subtle }
                    Chooser {
                        options: parent.m.modes.filter(md => root.sizeOf(md) === root.sizeOf(parent.mode))
                            .map(md => ({ value: md, label: root.rateOf(md) + " Hz" }))
                        current: parent.mode
                        onChosen: v => root.edit(parent.m.name, "mode", v)
                    }

                    Label { text: "Scale"; color: theme.subtle }
                    Chooser {
                        options: (root.settings.scales || [1]).map(s => ({ value: s, label: Math.round(s * 100) + " %" }))
                        current: root.value(parent.m.name, "scale")
                        onChosen: v => root.edit(parent.m.name, "scale", v)
                    }

                    Label { text: "Rotation"; color: theme.subtle }
                    Chooser {
                        options: (root.settings.transforms || ["Normal"]).map((t, i) => ({ value: i, label: t }))
                        current: root.value(parent.m.name, "transform")
                        onChosen: v => root.edit(parent.m.name, "transform", v)
                    }

                    Label { text: "Primary"; color: theme.subtle }
                    Chooser {
                        options: [{ value: true, label: "Primary display" }, { value: false, label: "Secondary" }]
                        current: root.primary === parent.m.name
                        onChosen: v => root.primary = v ? parent.m.name : (root.primary === parent.m.name ? "" : root.primary)
                    }
                }

                Item { Layout.fillHeight: true }

                // "Keep these settings?" bar.
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: theme.searchRowHeight + theme.edgeMargin
                    visible: root.pending
                    radius: theme.edgeMargin * 2
                    color: theme.withAlpha(theme.warning, 0.25)
                    border.width: 1
                    border.color: theme.withAlpha(theme.warning, 0.6)
                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: theme.edgeMargin * 2
                        anchors.rightMargin: theme.edgeMargin
                        Label { text: "Keep these display settings? Reverting in " + root.remaining + " s"; Layout.fillWidth: true }
                        Pill { text: "Revert"; enabled: !root.busy; onClicked: root.revert() }
                        Pill { text: "Keep"; primaryAction: true; enabled: !root.busy; onClicked: root.keep() }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        text: root.status
                        color: root.statusError ? theme.error : theme.subtle
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                    }
                    Pill { text: "Reset"; enabled: root.dirty && !root.busy; onClicked: root.edits = ({}) }
                    Pill { text: "Apply"; primaryAction: true; enabled: root.dirty && !root.busy && !root.pending; onClicked: root.applyEdits() }
                    Pill {
                        visible: root.setupMode
                        text: "Skip"
                        enabled: !root.busy && !root.pending
                        onClicked: root.skip()
                    }
                    Pill {
                        text: root.setupMode ? "Finish" : "Save"
                        enabled: !root.dirty && !root.busy && !root.pending && root.monitors.length > 0
                        onClicked: root.save()
                    }
                }
            }

            // Option list for the open Chooser.
            Rectangle {
                id: overlay
                property Item owner: null
                function open(c) { owner = c; visible = true }
                visible: false
                anchors.fill: parent
                color: theme.withAlpha(theme.bg, 0.5)
                radius: theme.radius
                MouseArea { anchors.fill: parent; onClicked: overlay.visible = false }
                Rectangle {
                    readonly property point at: overlay.owner ? overlay.owner.mapToItem(overlay, 0, overlay.owner.height) : Qt.point(0, 0)
                    x: at.x
                    y: Math.min(at.y, overlay.height - height - theme.edgeMargin)
                    width: overlay.owner ? overlay.owner.width : 0
                    height: Math.min(optionList.contentHeight + theme.edgeMargin, overlay.height * 0.6)
                    radius: theme.edgeMargin
                    color: theme.bg
                    border.width: 1
                    border.color: theme.outlineColor
                    ListView {
                        id: optionList
                        anchors.fill: parent
                        anchors.margins: theme.edgeMargin / 2
                        clip: true
                        model: overlay.owner ? overlay.owner.options : []
                        delegate: Rectangle {
                            required property var modelData
                            width: optionList.width
                            height: theme.searchRowHeight - theme.edgeMargin
                            radius: theme.edgeMargin / 2
                            readonly property bool isCurrent: overlay.owner && String(modelData.value) === String(overlay.owner.current)
                            color: optMouse.containsMouse ? theme.withAlpha(theme.accent, theme.selectionOpacity)
                                 : isCurrent ? theme.withAlpha(theme.subtle, 0.15) : "transparent"
                            Label { anchors.fill: parent; anchors.leftMargin: theme.edgeMargin * 2; text: parent.modelData.label }
                            MouseArea {
                                id: optMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                onClicked: { overlay.owner.chosen(parent.modelData.value); overlay.visible = false }
                            }
                        }
                    }
                }
            }
        }
    }
}
