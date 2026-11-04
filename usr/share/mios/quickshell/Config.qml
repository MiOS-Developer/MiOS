// AI-hint: MiOS shell surfaces share one app drawer and follow compositor output hotplug.
import QtQuick
import QtQuick.Shapes
import Quickshell.Wayland
import Quickshell as QS
import Quickshell.Hyprland
import Quickshell.Io
import Quickshell.Services.Notifications
import "." as MiOS

QS.ShellRoot {
    id: shellRoot
    function inputPath(runtime, name) {
        return runtime && typeof name === "string" && /^[a-z0-9][a-z0-9.-]*\.json$/.test(name)
            ? runtime + "/" + name : ""
    }
    readonly property string inputStatePath: inputPath(QS.Quickshell.env("XDG_RUNTIME_DIR") || "", MiOS.DockConfig.table.input_state_file)
    readonly property int inputHeartbeatMs: {
        const value = Number(MiOS.DockConfig.table.input_state_interval_ms)
        return isFinite(value) && value > 0 ? Math.max(1, Math.min(250, Math.floor(value))) : 0
    }
    readonly property var feedbackSettings: (MiOS.DockConfig.table.capture || {}).border_glow || ({})
    readonly property int feedbackLeaseMs: {
        const value = Number(feedbackSettings.lease_ms)
        return isFinite(value) && Math.floor(value) === value && value >= 100 && value <= 10000 ? value : 0
    }
    readonly property int feedbackTimeoutMs: Math.max(100, Math.min(250, feedbackLeaseMs))
    property string feedbackDesired: ""
    property string feedbackQueued: ""
    property string feedbackPending: ""
    property bool feedbackBusy: false
    property bool feedbackConfirmed: false
    property bool feedbackRoom: false
    property real feedbackSequence: 0
    property real feedbackAcceptedAt: 0
    property real feedbackNow: 0
    onFeedbackSettingsChanged: if (workspaceState && captureFeedback) shellRoot.publishInputState(false)

    function feedbackSpec(snapshot) {
        const cfg = feedbackSettings
        const output = String(snapshot.output || "")
        const color = String(cfg.color || "")
        const width = Number(cfg.width_px), period = Number(cfg.period_ms)
        const valid = output !== "" && !/[\x00-\x1f\x7f]/.test(output)
            && /^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$/.test(color)
            && isFinite(width) && width > 0 && width <= 32
            && isFinite(period) && Math.floor(period) === period && period >= 100 && period <= 20000
            && feedbackLeaseMs > 0
        return snapshot.open === true && cfg.enabled === true && valid
            ? {active: true, output: output, color: color, width_px: width, period_ms: period, lease_ms: feedbackLeaseMs}
            : {active: false}
    }
    function feedbackLua(spec, sequence) {
        const cfg = JSON.parse(spec)
        let fields = "{active=" + (cfg.active ? "true" : "false")
        if (cfg.active) fields += ",output=" + JSON.stringify(cfg.output) + ",color=" + JSON.stringify(cfg.color)
            + ",width_px=" + cfg.width_px + ",period_ms=" + cfg.period_ms + ",lease_ms=" + cfg.lease_ms
        fields += "}"
        // Only a reply produced by this request carries the complete marker. An echoed expression cannot match it.
        return "(function() local p=hl.plugin and hl.plugin.hypr3d;local room=false;"
            + "if p then local f=p.dock_capture_feedback;"
            + "if type(f)=='function' then local ok,v=pcall(f," + fields + ");"
            + "if not ok or type(v)~='boolean' then error('MiOS capture feedback unavailable',0) end;room=v;"
            + "elseif type(p.active)=='function' then local ok,v=pcall(p.active);"
            + "if not ok or type(v)~='boolean' then error('MiOS room status unavailable',0) end;room=v;"
            + "else error('MiOS room status unavailable',0) end end;"
            + "error('MIOS_DOCK_CAPTURE_FEEDBACK:'.." + JSON.stringify(String(sequence)) + "..':'..(room and 'room' or 'flat'),0) end)()"
    }
    function feedbackReply(text, sequence) {
        const value = String(text || "")
        if (value.length > 8192) return ""
        const matches = value.match(new RegExp("MIOS_DOCK_CAPTURE_FEEDBACK:" + sequence + ":(room|flat)\\b", "g"))
        return matches && matches.length === 1 ? (matches[0].endsWith(":room") ? "room" : "flat") : ""
    }
    function flatFeedbackAllowed(now) {
        return feedbackConfirmed && !feedbackRoom && feedbackPending === feedbackDesired
            && feedbackLeaseMs > 0 && now >= feedbackAcceptedAt && now - feedbackAcceptedAt < feedbackLeaseMs
    }
    function queueNativeFeedback(snapshot) {
        feedbackNow = Date.now()
        const desired = JSON.stringify(feedbackSpec(snapshot))
        if (desired !== feedbackDesired || !JSON.parse(desired).active) feedbackConfirmed = false
        feedbackDesired = desired
        feedbackQueued = desired
        pumpNativeFeedback()
    }
    function pumpNativeFeedback() {
        if (feedbackBusy || captureFeedback.running || feedbackQueued === "") return
        feedbackPending = feedbackQueued
        feedbackQueued = ""
        feedbackSequence += 1
        captureFeedback.reply = ""
        captureFeedback.expired = false
        feedbackBusy = true
        captureFeedback.command = ["/usr/bin/hyprctl", "eval", feedbackLua(feedbackPending, feedbackSequence)]
        captureFeedback.running = true
    }
    function finishNativeFeedback() {
        const reply = feedbackReply(captureFeedback.reply, feedbackSequence)
        feedbackNow = Date.now()
        feedbackConfirmed = !captureFeedback.expired && feedbackPending === feedbackDesired
            && JSON.parse(feedbackPending).active && reply !== ""
        if (feedbackConfirmed) {
            feedbackRoom = reply === "room"
            feedbackAcceptedAt = feedbackNow
            feedbackExpiry.restart()
        }
        feedbackBusy = false
        Qt.callLater(shellRoot.pumpNativeFeedback)
    }
    function timeoutNativeFeedback() {
        feedbackConfirmed = false
        captureFeedback.expired = true
        if (captureFeedback.running) captureFeedback.signal(9)
        else {
            // Failed process creation has no exited signal; the deadline also releases that request slot.
            feedbackBusy = false
            Qt.callLater(shellRoot.pumpNativeFeedback)
        }
    }
    function publishInputState(closed) {
        if (!workspaceState) return
        const snapshot = workspaceState.inputSnapshot(closed)
        if (inputStatePath) inputStateFile.setText(JSON.stringify(snapshot) + "\n")
        queueNativeFeedback(snapshot)
    }
    Process {
        id: captureFeedback
        property string reply: ""
        property bool expired: false
        stdout: StdioCollector { onStreamFinished: captureFeedback.reply += text.slice(0, 8193) }
        stderr: StdioCollector { onStreamFinished: captureFeedback.reply += text.slice(0, 8193) }
        onExited: shellRoot.finishNativeFeedback()
    }
    Timer {
        interval: shellRoot.feedbackTimeoutMs
        running: shellRoot.feedbackBusy
        repeat: false
        onTriggered: shellRoot.timeoutNativeFeedback()
    }
    Timer {
        id: feedbackExpiry
        interval: Math.max(1, shellRoot.feedbackLeaseMs)
        onTriggered: shellRoot.feedbackConfirmed = false
    }
    FileView {
        id: inputStateFile
        path: shellRoot.inputStatePath
        atomicWrites: true
        watchChanges: false
        printErrors: false
        onPathChanged: if (path) shellRoot.publishInputState(false)
    }
    Timer {
        interval: Math.max(1, shellRoot.inputHeartbeatMs)
        repeat: true
        running: shellRoot.inputStatePath !== "" && shellRoot.inputHeartbeatMs > 0
            && (workspaceState.drawerOpen || workspaceState.searchOpen)
        onTriggered: shellRoot.publishInputState(false)
    }
    Component.onDestruction: shellRoot.publishInputState(true)
    QtObject {
        id: workspaceState
        property bool drawerOpen: false
        property string drawerScreen: ""
        property bool searchOpen: false
        property string searchScreen: ""
        property string searchQuery: ""
        function inputSnapshot(closed) {
            const open = !closed && (drawerOpen || searchOpen)
            const surface = open ? (drawerOpen ? "drawer" : "search") : ""
            const output = open ? (drawerOpen ? drawerScreen : searchScreen) : ""
            return {version: 1, open: open, surface: surface, output: output, timestamp_ms: Date.now()}
        }
        onDrawerOpenChanged: shellRoot.publishInputState(false)
        onDrawerScreenChanged: shellRoot.publishInputState(false)
        onSearchOpenChanged: shellRoot.publishInputState(false)
        onSearchScreenChanged: shellRoot.publishInputState(false)
        // One settings model and one sampler for every output's island.
        property var topbarConfig: ({})
        property var topbarWrites: []
        property string topbarError: ""
        property var frameOutputs: []
        property var frameWindow: null   // mios-pacing's focused-window meter ({address, class, output, fps, ...})
        property var rdpStats: ({})
        signal phoneRequested()   // the topbar's inbound "remote" item asks the dock popups for the device controls
        property real frameSampleTime: Date.now() / 1000
        property string rdpOutput: "HEADLESS-MIOS-RDP"
        property var shaderStatus: ({})
        property string shaderError: ""
        property string remoteError: ""
        readonly property var topbarOptions: [
            {key: "app_menu", label: "Application menus"}, {key: "workspaces", label: "Workspace"},
            {key: "network", label: "Network"}, {key: "sound", label: "Sound"},
            {key: "batteries", label: "Device batteries"}, {key: "sysmon", label: "System monitor"},
            {key: "fps", label: "Frames per second"},
            {key: "date", label: "Date"}, {key: "clock", label: "Clock"}, {key: "power", label: "Power"}
        ]
        function topbarShown(key) { return !topbarConfig.visibility || topbarConfig.visibility[key] !== false }
        function setTopbarShown(key, shown) {
            topbarWrites = topbarWrites.concat([{key: key, shown: shown}])
            saveNextTopbar()
        }
        function saveNextTopbar() {
            if (topbarSettings.running) return
            if (topbarWrites.length) {
                const next = topbarWrites[0]
                topbarWrites = topbarWrites.slice(1)
                topbarSettings.command = ["/usr/bin/miosd", "topbar", "--item", next.key, "--visible", String(next.shown)]
            } else topbarSettings.command = ["/usr/bin/miosd", "topbar"]
            topbarSettings.running = true
        }
        function isRemoteOutput(name) { return name === rdpOutput || name.startsWith(rdpOutput + "-") }
        function fpsFor(name) {
            if (isRemoteOutput(name)) {
                const s = rdpStats
                return s.output === name && s.attached && Math.abs(frameSampleTime - Number(s.time)) < 10 && isFinite(s.fps) ? Number(s.fps) : -1
            }
            for (const output of frameOutputs) if (output.name === name && isFinite(output.rendered_fps)) return Math.round(output.rendered_fps)
            return -1
        }
        // The focused window's committed frames per second on the island of the output it is on; -1 = unknown (no
        // focused window, a plugin without the meter, or [desktop.topbar] window_fps = false).
        function windowFpsFor(name) {
            const w = frameWindow
            if (topbarConfig.window_fps === false || !w || w.output !== name || !isFinite(w.fps)) return -1
            return Math.round(Number(w.fps))
        }
        function shaderPane(address) {
            const supervisor = shaderStatus.supervisor || ({})
            for (const pane of (supervisor.panes || [])) if (pane.target === address && pane.state !== "closing") return pane
            for (const pane of (supervisor.queued || [])) if (pane.target === address) return pane
            return null
        }
        readonly property bool remoteAttached: rdpStats.attached === true && Math.abs(frameSampleTime - Number(rdpStats.time)) < 10
        function remoteAction(action) {
            if (remoteControl.running || !remoteAttached || !rdpStats.control_supported) return
            remoteError = ""
            remoteControl.command = ["/usr/libexec/mios/rdp-native/mios-rdp-server", "--control", action]
            remoteControl.running = true
        }
        function shaderAction(action, address) {
            if (shaderControl.running) return
            shaderError = ""
            const verb = action === "browse" ? (shaderPane(address) ? "setup" : "attach") : action
            let args = ["/usr/bin/miosd", "shaderglass", verb]
            if (action === "browse" && verb === "attach") args.push("--setup")
            if (action !== "stop") {
                if (!/^0x[0-9a-f]+$/i.test(address)) return
                args = args.concat(["--window", address])
            }
            shaderControl.command = args
            shaderControl.running = true
        }
        function openSearch(query) {
            searchScreen = Hyprland.focusedMonitor ? Hyprland.focusedMonitor.name : ""
            searchQuery = query
            drawerOpen = false
            searchOpen = true
        }
        function toggleSearch() {
            if (searchOpen) searchOpen = false
            else openSearch("")
        }

        function toggleDrawer() {
            drawerScreen = Hyprland.focusedMonitor ? Hyprland.focusedMonitor.name : ""
            searchOpen = false
            drawerOpen = !drawerOpen
        }
    }
    Process {
        id: topbarSettings
        command: ["/usr/bin/miosd", "topbar"]
        running: true
        property string result: ""
        stdout: StdioCollector { onStreamFinished: topbarSettings.result = text }
        stderr: StdioCollector { onStreamFinished: workspaceState.topbarError = text.trim() }
        onExited: (code, status) => {
            if (code === 0) {
                try { workspaceState.topbarConfig = JSON.parse(result) } catch (e) { workspaceState.topbarError = "Could not read topbar settings" }
            }
            if (workspaceState.topbarWrites.length) Qt.callLater(workspaceState.saveNextTopbar)
        }
    }
    FileView {
        path: QS.Quickshell.env("MIOS_USER_TOML") || ((QS.Quickshell.env("XDG_CONFIG_HOME") || ((QS.Quickshell.env("HOME") || "/home/mios") + "/.config")) + "/mios/mios.toml")
        watchChanges: true
        printErrors: false
        onFileChanged: if (!topbarSettings.running) workspaceState.saveNextTopbar()
    }
    Process {
        id: frameSample
        command: ["hyprctl", "mios-pacing", "status"]
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    const status = JSON.parse(text)
                    workspaceState.frameOutputs = status.outputs || []
                    workspaceState.frameWindow = status.window || null
                } catch (e) { workspaceState.frameOutputs = []; workspaceState.frameWindow = null }
            }
        }
        onExited: (code, status) => { if (code !== 0) { workspaceState.frameOutputs = []; workspaceState.frameWindow = null } }
    }
    Process {
        command: ["/usr/bin/mios-toml-get", "remote.rdp", "virtual_output", "HEADLESS-MIOS-RDP"]
        running: true
        stdout: StdioCollector { onStreamFinished: { if (text.trim()) workspaceState.rdpOutput = text.trim() } }
    }
    FileView {
        path: QS.Quickshell.env("MIOS_RDP_STATS_JSON") || ((QS.Quickshell.env("XDG_RUNTIME_DIR") || "/run/user/1000") + "/mios-rdp/stats.json")
        watchChanges: true
        preload: true
        printErrors: false
        onFileChanged: reload()
        onLoaded: { try { workspaceState.rdpStats = JSON.parse(text()) } catch (e) { workspaceState.rdpStats = ({}) } }
        onLoadFailed: workspaceState.rdpStats = ({})
    }
    Process {
        id: shaderSample
        command: ["/usr/bin/miosd", "shaderglass", "status"]
        stdout: StdioCollector {
            onStreamFinished: {
                try { workspaceState.shaderStatus = JSON.parse(text) }
                catch (e) { workspaceState.shaderStatus = ({}) }
            }
        }
    }
    Process {
        id: shaderControl
        stderr: StdioCollector { onStreamFinished: workspaceState.shaderError = text.trim() }
        onExited: (code, status) => { if (!shaderSample.running) shaderSample.running = true }
    }
    Process {
        id: remoteControl
        stderr: StdioCollector { onStreamFinished: workspaceState.remoteError = text.trim() }
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    const reply = JSON.parse(text)
                    if (reply.ok && reply.paused !== undefined) workspaceState.rdpStats = Object.assign({}, workspaceState.rdpStats, {paused: reply.paused})
                } catch (e) { workspaceState.remoteError = "Could not read the session response" }
            }
        }
    }
    Timer {
        interval: Math.max(500, workspaceState.topbarConfig.sample_interval_ms || 1000)
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: {
            workspaceState.frameSampleTime = Date.now() / 1000
            if (workspaceState.topbarShown("fps") && !frameSample.running) frameSample.running = true
            if (workspaceState.topbarShown("shaderglass") && !shaderSample.running) shaderSample.running = true
        }
    }
    IpcHandler {
        target: "drawer"
        function toggle(): void { workspaceState.toggleDrawer() }
        function close(): void { workspaceState.drawerOpen = false }
    }
    IpcHandler {
        target: "search"
        function toggle(): void { workspaceState.toggleSearch() }
        function open(query: string): void { workspaceState.openSearch(query) }
        function close(): void { workspaceState.searchOpen = false }
    }
    MiOS.Keymap {}
    // Notification bus: the shell's org.freedesktop.Notifications server and its own toasts (Shot.qml `shot toast`,
    // the "notifications" IPC post) post here; the Notifications surface of the focused output grows them out of its
    // status island (Notifications.qml, [desktop.notifications]).
    QtObject {
        id: notifBus
        signal posted(var toast)
        readonly property string screenName: Hyprland.focusedMonitor ? Hyprland.focusedMonitor.name : ""
        function toast(title, body) { post({ title: title, body: body || "", kind: "info" }) }
        // The output that shows it: the focused one, else (no focus known, or no shell surface there) the first.
        function post(t) {
            MiOS.NotifModel.recordHistory(t)
            const screens = QS.Quickshell.screens
            const on = screens.some(s => s.name === screenName) ? screenName : (screens.length ? screens[0].name : "")
            posted(Object.assign({ output: on }, t))
        }
        // Notification.expireTimeout carries the D-Bus expire_timeout (milliseconds; Quickshell documents seconds): accept
        // both. -1: the server's default ([desktop.notifications] timeout_ms); 0: never expires (the spec's meaning).
        function timeoutMs(t) { return t > 0 ? (t < 100 ? t * 1000 : t) : (t === 0 ? 0 : MiOS.NotifModel.timeoutMs) }
        function fromServer(n) {
            const critical = n.urgency === NotificationUrgency.Critical
            if (MiOS.NotifModel.dnd && !critical) { n.expire(); return }
            // n stays live: a replacement (replaces_id) updates the same card.
            post({ title: n.summary || n.appName || "", body: n.body || "", kind: critical ? "error" : "info",
                   image: n.image || "", n: n, critical: critical, expire_timeout: n.expireTimeout,
                   timeout: (n.resident || critical) ? 0 : timeoutMs(n.expireTimeout) })
        }
    }

    Component.onCompleted: {
        QS.Quickshell.inhibitReloadPopup()
        shellRoot.publishInputState(true)
    }

    Connections {
        target: QS.Quickshell
        function onReloadCompleted() {
            notifBus.post({
                title: "Quickshell Reloaded",
                body: "Shell configuration reloaded successfully.",
                kind: "success",
                timeout: 3000
            })
        }
        function onReloadFailed(errorString) {
            notifBus.post({
                title: "Quickshell Reload Failed",
                body: errorString || "An error occurred during QML reload.",
                kind: "error",
                critical: true,
                timeout: 0
            })
        }
    }
    NotificationServer {
        id: notifServer
        keepOnReload: true
        actionsSupported: true
        imageSupported: true
        bodySupported: true
        persistenceSupported: false
        onNotification: n => { n.tracked = true; notifBus.fromServer(n) }
        // A shell reload keeps the tracked notifications (keepOnReload): show the ones still open again.
        Component.onCompleted: Qt.callLater(() => {
            for (const n of (notifServer.trackedNotifications ? notifServer.trackedNotifications.values : []))
                if (n && n.lastGeneration) notifBus.fromServer(n)
        })
    }
    // Scripted access to the notifications: every output's blob state, a shell toast, dismiss, do-not-disturb.
    IpcHandler {
        target: "notifications"
        function state(): string {
            return JSON.stringify({ dnd: MiOS.NotifModel.dnd, focused: notifBus.screenName, loaded: MiOS.NotifModel.loaded,
                                    config: { position: MiOS.NotifModel.position, timeout_ms: MiOS.NotifModel.timeoutMs,
                                              max_visible: MiOS.NotifModel.maxVisible, width: MiOS.NotifModel.width,
                                              morph_ms: MiOS.NotifModel.morphMs },
                                    views: MiOS.NotifModel.views.map(v => v.snapshot()) })
        }
        function post(title: string, body: string, kind: string): void { notifBus.post({ title: title, body: body, kind: kind || "info" }) }
        function dismissAll(): void { for (const v of MiOS.NotifModel.views) v.dismissAll() }
        function dnd(on: bool): void { MiOS.NotifModel.dndOverride = true; MiOS.NotifModel.dndRuntime = on }
        function reload(): void { MiOS.NotifModel.reload() }
    }
    // mios-shot's selector, flash and toast endpoint (IPC target "shot").
    MiOS.Shot { bus: notifBus }
    QS.Variants {
        model: QS.Quickshell.screens
        QS.Scope {
            id: outputScope
            required property var modelData
            readonly property string outputName: modelData ? modelData.name : ""
            // Fullscreen edge reveal shared by the top row and the dock of this output.
            MiOS.EdgeReveal { id: edgeReveal; screen: modelData }
            MiOS.AppMenu { screen: modelData; shellState: workspaceState; reveal: edgeReveal }
            MiOS.PanelWindow { screen: modelData; shellState: workspaceState; reveal: edgeReveal }
            MiOS.Sidebar { screen: modelData; shellState: workspaceState; reveal: edgeReveal }
            MiOS.Notifications { screen: modelData; bus: notifBus; reveal: edgeReveal }
            MiOS.Search { screen: modelData; outputName: modelData.name; shellState: workspaceState }
            // One input-transparent feedback surface per local output; window borders are native shapes.
            QS.PanelWindow {
                id: captureGlow
                screen: outputScope.modelData
                anchors.top: true
                anchors.bottom: true
                anchors.left: true
                anchors.right: true
                color: "transparent"
                exclusiveZone: 0
                exclusionMode: QS.ExclusionMode.Ignore
                WlrLayershell.namespace: "mios-dock-capture-glow"
                WlrLayershell.layer: WlrLayer.Overlay
                WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
                mask: QS.Region { width: 0; height: 0 }
                readonly property var settings: (MiOS.DockConfig.table.capture || {}).border_glow || ({})
                readonly property real glowWidth: Number(settings.width_px)
                readonly property int periodMs: isFinite(Number(settings.period_ms)) ? Math.max(0, Math.floor(Number(settings.period_ms))) : 0
                readonly property color glowColor: settings.color || "transparent"
                // Output identity belongs to the variants model, independent of this window's visibility.
                readonly property var monitor: Hyprland.monitorFor(outputScope.modelData)
                readonly property bool localOutput: outputScope.outputName !== ""
                    && !workspaceState.isRemoteOutput(outputScope.outputName)
                    && !/^HEADLESS[-_]/i.test(outputScope.outputName)
                readonly property bool capturedHere: (workspaceState.drawerOpen
                    && (!workspaceState.drawerScreen || workspaceState.drawerScreen === outputScope.outputName))
                    || (workspaceState.searchOpen
                    && (!workspaceState.searchScreen || workspaceState.searchScreen === outputScope.outputName))
                visible: localOutput && capturedHere && shellRoot.flatFeedbackAllowed(shellRoot.feedbackNow)
                    && settings.enabled === true
                    && isFinite(glowWidth) && glowWidth > 0 && periodMs > 0
                property real phase: 0
                NumberAnimation on phase {
                    from: 0
                    to: 360
                    duration: Math.max(1, captureGlow.periodMs)
                    loops: Animation.Infinite
                    running: captureGlow.visible
                }
                function visibleWindowRects(monitor, viewportWidth, viewportHeight) {
                    const output = []
                    const list = monitor && monitor.activeWorkspace ? monitor.activeWorkspace.toplevels.values : []
                    const ox = monitor ? monitor.x : 0, oy = monitor ? monitor.y : 0
                    for (const window of (list || [])) {
                        const w = window ? window.lastIpcObject : null
                        if (!w || !w.address || w.hidden === true || w.mapped === false
                            || !w.at || w.at.length < 2 || !w.size || w.size.length < 2) continue
                        const cls = String(w.class || "")
                        const workspace = w.workspace ? String(w.workspace.name || "") : ""
                        if (/^(quickshell|phosh-osk|stevia)/i.test(cls) || workspace.startsWith("special:")) continue
                        const x = Number(w.at[0]) - ox, y = Number(w.at[1]) - oy
                        const width = Number(w.size[0]), height = Number(w.size[1])
                        if (![x, y, width, height].every(isFinite) || width <= 0 || height <= 0
                            || x + width <= 0 || y + height <= 0 || x >= viewportWidth || y >= viewportHeight) continue
                        output.push({address: String(w.address), x: x, y: y, width: width, height: height})
                    }
                    return output
                }
                readonly property var windowRects: visibleWindowRects(monitor, width, height)
                Repeater {
                    model: captureGlow.visible ? captureGlow.windowRects : []
                    Shape {
                        id: ring
                        required property var modelData
                        x: modelData.x
                        y: modelData.y
                        width: modelData.width
                        height: modelData.height
                        preferredRendererType: Shape.CurveRenderer
                        readonly property real line: Math.min(captureGlow.glowWidth, width / 2, height / 2)
                        readonly property real radius: Math.max(0.01, Math.min(MiOS.Glass.rounding, width / 2, height / 2))
                        readonly property real innerRadius: Math.max(0.01, radius - line)
                        ShapePath {
                            strokeWidth: -1
                            fillRule: ShapePath.OddEvenFill
                            fillGradient: ConicalGradient {
                                centerX: ring.width / 2
                                centerY: ring.height / 2
                                angle: captureGlow.phase
                                GradientStop { position: 0; color: captureGlow.glowColor }
                                GradientStop { position: 0.12; color: MiOS.Glass.theme.withAlpha(captureGlow.glowColor, MiOS.Glass.theme.scrimOpacity) }
                                GradientStop { position: 0.4; color: "transparent" }
                                GradientStop { position: 0.9; color: "transparent" }
                                GradientStop { position: 1; color: captureGlow.glowColor }
                            }
                            startX: ring.radius; startY: 0
                            PathLine { x: ring.width - ring.radius; y: 0 }
                            PathArc { x: ring.width; y: ring.radius; radiusX: ring.radius; radiusY: ring.radius }
                            PathLine { x: ring.width; y: ring.height - ring.radius }
                            PathArc { x: ring.width - ring.radius; y: ring.height; radiusX: ring.radius; radiusY: ring.radius }
                            PathLine { x: ring.radius; y: ring.height }
                            PathArc { x: 0; y: ring.height - ring.radius; radiusX: ring.radius; radiusY: ring.radius }
                            PathLine { x: 0; y: ring.radius }
                            PathArc { x: ring.radius; y: 0; radiusX: ring.radius; radiusY: ring.radius }
                            PathMove { x: ring.line + ring.innerRadius; y: ring.line }
                            PathLine { x: ring.width - ring.line - ring.innerRadius; y: ring.line }
                            PathArc { x: ring.width - ring.line; y: ring.line + ring.innerRadius; radiusX: ring.innerRadius; radiusY: ring.innerRadius }
                            PathLine { x: ring.width - ring.line; y: ring.height - ring.line - ring.innerRadius }
                            PathArc { x: ring.width - ring.line - ring.innerRadius; y: ring.height - ring.line; radiusX: ring.innerRadius; radiusY: ring.innerRadius }
                            PathLine { x: ring.line + ring.innerRadius; y: ring.height - ring.line }
                            PathArc { x: ring.line; y: ring.height - ring.line - ring.innerRadius; radiusX: ring.innerRadius; radiusY: ring.innerRadius }
                            PathLine { x: ring.line; y: ring.line + ring.innerRadius }
                            PathArc { x: ring.line + ring.innerRadius; y: ring.line; radiusX: ring.innerRadius; radiusY: ring.innerRadius }
                        }
                    }
                }
            }
        }
    }
}
