// AI-hint: MiOS shell surfaces share one app drawer and follow compositor output hotplug.
import QtQuick
import Quickshell as QS
import Quickshell.Hyprland
import Quickshell.Io
import Quickshell.Services.Notifications
import "." as MiOS

QS.ShellRoot {
    QtObject {
        id: workspaceState
        property bool drawerOpen: false
        property string drawerScreen: ""
        property bool searchOpen: false
        property string searchScreen: ""
        property string searchQuery: ""
        // One settings model and one sampler for every output's island.
        property var topbarConfig: ({})
        property var topbarWrites: []
        property string topbarError: ""
        property var frameOutputs: []
        property var rdpStats: ({})
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
                try { workspaceState.frameOutputs = JSON.parse(text).outputs || [] }
                catch (e) { workspaceState.frameOutputs = [] }
            }
        }
        onExited: (code, status) => { if (code !== 0) workspaceState.frameOutputs = [] }
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
            required property var modelData
            // Fullscreen edge reveal shared by the top row and the dock of this output.
            MiOS.EdgeReveal { id: edgeReveal; screen: modelData }
            MiOS.AppMenu { screen: modelData; shellState: workspaceState; reveal: edgeReveal }
            MiOS.PanelWindow { screen: modelData; shellState: workspaceState; reveal: edgeReveal }
            MiOS.Sidebar { screen: modelData; shellState: workspaceState; reveal: edgeReveal }
            MiOS.Notifications { screen: modelData; bus: notifBus; reveal: edgeReveal }
            MiOS.Search { screen: modelData; outputName: modelData.name; shellState: workspaceState }
        }
    }
}
