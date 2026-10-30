// AI-hint: Minimal native MiOS HUD for isolated compositor development sessions. No notification bus or shared desktop services.
// AI-related: tools/native/mios-gen/src/nested_session.rs, PanelWindow.qml, Sidebar.qml, desktop.nested
import QtQuick
import Quickshell as QS
import Quickshell.Hyprland
import Quickshell.Io
import "." as MiOS

QS.ShellRoot {
    QtObject {
        id: state
        property bool drawerOpen: false
        property string drawerScreen: ""
        property bool searchOpen: false
        property string searchScreen: ""
        property string searchQuery: ""
        property var rdpStats: ({})
        readonly property bool remoteAttached: false
        property string shaderError: ""
        property string remoteError: ""
        property var topbarConfig: ({})
        property var topbarOptions: []
        property string topbarError: ""
        // MIOS_NESTED_TOPBAR_ITEMS (comma list) lets a development session show more island items (live tests).
        readonly property var topbarItems: (QS.Quickshell.env("MIOS_NESTED_TOPBAR_ITEMS") || "clock,fps,workspaces").split(",").map(s => s.trim())
        function topbarShown(key) { return topbarItems.indexOf(key) >= 0 }
        function fpsFor(name) { return -1 }
        function isRemoteOutput(name) { return false }
        function shaderPane(address) { return null }
        function shaderAction(action, address) { shaderError = "ShaderGlass is managed in the owning session" }
        function toggleDrawer() {
            drawerScreen = Hyprland.focusedMonitor ? Hyprland.focusedMonitor.name : ""
            searchOpen = false
            drawerOpen = !drawerOpen
        }
        function toggleSearch() {
            searchScreen = Hyprland.focusedMonitor ? Hyprland.focusedMonitor.name : ""
            drawerOpen = false
            searchOpen = !searchOpen
        }
    }
    IpcHandler { target: "drawer"; function toggle(): void { state.toggleDrawer() } }
    IpcHandler { target: "search"; function toggle(): void { state.toggleSearch() } }
    QS.Variants {
        model: QS.Quickshell.screens
        QS.Scope {
            id: outputScope
            required property var modelData
            readonly property var nativeMonitor: Hyprland.monitorFor(modelData)
            readonly property var nativeGeometry: nativeMonitor ? nativeMonitor.lastIpcObject : null
            readonly property real viewportWidth: nativeGeometry ? nativeGeometry.width / nativeGeometry.scale : modelData.width
            onViewportWidthChanged: Qt.callLater(() => {
                nestedBar.nudgeReserve()
                nestedDock.dockWindow.nudgeReserve()
            })
            Timer {
                interval: Math.max(20, Number(QS.Quickshell.env("MIOS_NESTED_RESIZE_POLL_MS")) || 100)
                repeat: true; running: true
                onTriggered: Hyprland.refreshMonitors()
            }
            MiOS.EdgeReveal { id: reveal; screen: modelData }
            // The focused app's exported menus as glass pills (AppMenu.qml, appmenu.json in this session's runtime);
            // shown only when MIOS_NESTED_TOPBAR_ITEMS lists app_menu (topbarShown), like the desktop shell's Config.qml.
            MiOS.AppMenu { screen: modelData; shellState: state; reveal: reveal }
            MiOS.PanelWindow { id: nestedBar; viewportWidth: outputScope.viewportWidth; screen: modelData; shellState: state; reveal: reveal }
            MiOS.Sidebar { id: nestedDock; viewportWidth: outputScope.viewportWidth; screen: modelData; shellState: state; reveal: reveal }
            MiOS.Search { screen: modelData; outputName: modelData.name; shellState: state }
        }
    }
}
