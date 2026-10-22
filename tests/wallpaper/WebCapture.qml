// AI-hint: Test harness: loads the canonical living-wallpaper.html in QtWebEngine in deterministic mode (steps=N)
//          and prints the frozen frame (window.__miosWpCapture, a PNG data URL) to stdout, so the native host's
//          capture of the SAME animation time can be compared with it. Runs in an isolated nested compositor.
// AI-related: tests/test-wallpaper-native.py, usr/share/mios/branding/living-wallpaper.html, usr/share/mios/wallpaper/Host.qml
import QtQuick
import QtWebEngine

Window {
    id: win
    readonly property string pageUrl: Qt.application.arguments[Qt.application.arguments.length - 1]
    width: parseInt(Qt.application.arguments[Qt.application.arguments.length - 3])
    height: parseInt(Qt.application.arguments[Qt.application.arguments.length - 2])
    visible: true
    color: "black"
    flags: Qt.FramelessWindowHint
    WebEngineView {
        id: view
        anchors.fill: parent
        url: win.pageUrl
        onJavaScriptConsoleMessage: function (level, message, line, source) { console.warn("page:", message) }
    }
    Timer {
        interval: 250
        repeat: true
        running: true
        property int polls: 0
        onTriggered: {
            if (++polls > 480) { console.warn("mios-wallpaper-capture: timeout"); Qt.exit(2) }
            view.runJavaScript("window.__miosWpCapture || ''", function (r) {
                if (r && r.length > 0) {
                    console.info("MIOS_WP_CAPTURE " + r)
                    Qt.quit()
                }
            })
        }
    }
}
