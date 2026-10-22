// Perf probe (test only): a 2x2 px overlay layer on each output named in MIOS_PROBE_OUTPUTS that changes every
// frame it is given (continuous damage = cursor-motion load) and prints, once per second, how many frames each
// output actually presented to it (= that output's achieved render rate under continuous damage).
import QtQuick
import QtQuick.Window
import org.kde.layershell as LayerShell

QtObject {
    id: root
    readonly property var wanted: (Qt.application.arguments[Qt.application.arguments.length - 1] || "HDMI-A-1").split(",")
    property var counts: ({})
    property Timer report: Timer {
        interval: 1000; repeat: true; running: true
        onTriggered: { console.warn("FPS " + JSON.stringify(root.counts)); root.counts = ({}) }
    }
    property Instantiator wins: Instantiator {
        model: Qt.application.screens.filter(s => root.wanted.indexOf(s.name) >= 0)
        delegate: Window {
            id: w
            required property var modelData
            visible: false
            screen: modelData
            x: modelData.virtualX
            y: modelData.virtualY
            Component.onCompleted: { w.screen = w.modelData; w.visible = true }
            width: 2; height: 2
            color: "transparent"
            flags: Qt.FramelessWindowHint | Qt.WindowTransparentForInput
            LayerShell.Window.scope: "mios-perfprobe"
            LayerShell.Window.layer: LayerShell.Window.LayerOverlay
            LayerShell.Window.anchors: LayerShell.Window.AnchorBottom | LayerShell.Window.AnchorRight
            LayerShell.Window.exclusionZone: -1
            LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityNone
            LayerShell.Window.wantsToBeOnActiveScreen: false
            Rectangle {
                id: px
                anchors.fill: parent
                property int k: 0
                color: Qt.rgba(0.5, 0.5, 0.5, (k % 2) ? 0.03 : 0.02)
            }
            onFrameSwapped: {
                px.k++
                const c = root.counts; c[w.modelData.name] = (c[w.modelData.name] || 0) + 1
            }
        }
    }
}
