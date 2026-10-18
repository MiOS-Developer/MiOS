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
