// AI-hint: QtWebEngine host for the canonical Windows MiOS wallpaper HTML (renderer = "web"). Kept as the
//          reference / negative control for the native GPU host (Host.qml) and as the fallback renderer;
//          one Chromium page per output, so it costs far more GPU than the native host.
// AI-related: usr/share/mios/wallpaper/Host.qml, usr/share/mios/branding/living-wallpaper.html, [theme.wallpaper]
import QtQuick
import QtQml.Models
import QtWebEngine
import org.kde.layershell as LayerShell

QtObject {
    readonly property url wallpaperUrl: Qt.application.arguments[Qt.application.arguments.length - 1]
    property Instantiator backgrounds: Instantiator {
        model: Qt.application.screens
        delegate: Window {
            id: win
            required property var modelData
            // Shown only after the screen is assigned: a window made visible first is mapped
            // on the default output and keeps that output.
            visible: false
            screen: modelData
            // Qt re-derives a window's screen from its position when it is shown: left at the default
            // (0,0) every copy maps on the output at the origin and keeps it. Placing each window at its
            // own output's virtual origin makes QWindow::screen() -- which LayerShellQt passes to the
            // compositor as the layer surface's wl_output -- that output.
            x: modelData.virtualX
            y: modelData.virtualY
            Component.onCompleted: { win.screen = win.modelData; win.visible = true }
            width: modelData.width
            height: modelData.height
            flags: Qt.FramelessWindowHint | Qt.WindowTransparentForInput
            // Use the window's own screen, never the focused output (LayerShellQt >= 6.6). Do not bind
            // LayerShell.Window.screen: it takes a QScreen and QML only has QQuickScreenInfo ("Unable to
            // assign QQuickScreenInfo to QScreen"), which leaves it null.
            LayerShell.Window.wantsToBeOnActiveScreen: false
            LayerShell.Window.scope: "mios-wallpaper"
            LayerShell.Window.layer: LayerShell.Window.LayerBackground
            LayerShell.Window.anchors: LayerShell.Window.AnchorTop | LayerShell.Window.AnchorBottom | LayerShell.Window.AnchorLeft | LayerShell.Window.AnchorRight
            LayerShell.Window.exclusionZone: -1
            LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityNone
            WebEngineView {
                anchors.fill: parent
                url: wallpaperUrl
                settings.playbackRequiresUserGesture: true
            }
        }
    }
}
