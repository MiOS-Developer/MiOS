// AI-hint: Native GPU host for the canonical MiOS living wallpaper (renderer = "native"): one layer-shell background
//          window per output, each drawing MeshGradient.qml (two Qt RHI shaders, no Chromium). ONE clock for all
//          outputs: a single timer steps every output with the same sim time t = elapsed * speed at `fps` steps/s
//          (the HTML's pacing); Qt presents each step on that output's next frame callback.
//          Parameters arrive in the same URL query the Windows HTML reads (mios-wallpaperd wallpaper_url, SSOT):
//          a0..a15 w0..w15 bg fg mode fps speed intro dpr sim n | native: nsim | deterministic tests: t0 steps capture quit.
// AI-related: usr/share/mios/wallpaper/MeshGradient.qml, usr/share/mios/wallpaper/WebHost.qml, tools/native/mios-wallpaperd, [theme.wallpaper]
import QtQuick
import QtQml.Models
import org.kde.layershell as LayerShell

QtObject {
    id: host
    readonly property string wallpaperUrl: Qt.application.arguments[Qt.application.arguments.length - 1]

    // ---- the shared parameter contract with living-wallpaper.html (defaults == the HTML's defaults) ----
    readonly property var query: {
        const out = {}
        const q = wallpaperUrl.indexOf("?")
        if (q < 0)
            return out
        for (const part of wallpaperUrl.substring(q + 1).split("&")) {
            const eq = part.indexOf("=")
            if (eq > 0)
                out[decodeURIComponent(part.substring(0, eq))] = decodeURIComponent(part.substring(eq + 1))
        }
        return out
    }
    readonly property var defaultPalette: ["282262", "DC271B", "3E7765", "F35C15", "1A407F", "734F39", "B7C9D7", "E7DFD3",
                                            "948E8E", "FF6B5C", "5FAA8E", "FF8540", "3D6BA8", "9D7660", "E0E0E0", "FFFFFF"]
    readonly property var defaultWeights: [1.00, 0.30, 0.60, 0.55, 0.85, 0.35, 0.70, 0.95, 0.55, 0.22, 0.30, 0.28, 0.45, 0.22, 0.55, 0.18]
    function num(key, def, lo, hi) {
        const v = parseFloat(query[key])
        return isNaN(v) ? def : Math.max(lo, Math.min(hi, v))
    }
    function rgb(hex, def) {
        let h = String(hex || def || "").replace(/^#/, "")
        if (h.length === 3)
            h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2]
        const c = [0, 2, 4].map(i => parseInt(h.substr(i, 2), 16) / 255)
        return c.map(x => isNaN(x) ? 0 : x)
    }
    readonly property var colors: {
        const out = []
        for (let i = 0; i < 16; i++) {
            const c = rgb(query["a" + i], defaultPalette[i])
            out.push(Qt.vector4d(c[0], c[1], c[2], num("w" + i, defaultWeights[i], 0, 1)))
        }
        return out
    }
    readonly property vector4d bg: {
        const c = rgb(query["bg"] || query["a0"], defaultPalette[0])
        return Qt.vector4d(c[0], c[1], c[2], 1)
    }
    readonly property real dark: (query["mode"] || "dark").toLowerCase() === "light" ? 0 : 1
    readonly property real fps: num("fps", 30, 1, 240)
    readonly property real speed: num("speed", 0.40, 0, 10)
    readonly property real introMs: num("intro", 3500, 0, 60000)
    readonly property real dprCap: num("dpr", 1.5, 0.25, 8)
    readonly property real refSimScale: num("sim", 0.6, 0.05, 2)
    readonly property int emitters: Math.round(num("n", 24, 1, 24))   // the SIM shader unrolls a constant 24-emitter loop
    readonly property real simScale: num("nsim", 0.25, 0.02, 2)
    // Deterministic mode (tests): fixed steps t = t0 + k * speed / fps, then optional capture + quit.
    readonly property real t0: num("t0", 0, 0, 1e7)
    readonly property int fixedSteps: Math.round(num("steps", 0, 0, 1e6))
    readonly property string capture: query["capture"] || ""
    readonly property bool quitAfter: query["quit"] === "1"

    // ---- ONE clock for every output ----
    readonly property double startedMs: Date.now()
    property int captured: 0
    signal tick(real t)
    function simTime() { return t0 + (Date.now() - startedMs) / 1000 * speed }

    property Timer clock: Timer {
        interval: Math.max(1, Math.round(1000 / host.fps))
        repeat: true
        running: host.fixedSteps === 0
        triggeredOnStart: true
        onTriggered: host.tick(host.simTime())
    }

    property Instantiator backgrounds: Instantiator {
        model: Qt.application.screens
        delegate: Window {
            id: win
            required property var modelData
            // Shown only after the screen is assigned (see WebHost.qml for the LayerShellQt output rules).
            visible: false
            screen: modelData
            x: modelData.virtualX
            y: modelData.virtualY
            Component.onCompleted: { win.screen = win.modelData; win.visible = true }
            width: modelData.width
            height: modelData.height
            color: "black"
            flags: Qt.FramelessWindowHint | Qt.WindowTransparentForInput
            LayerShell.Window.wantsToBeOnActiveScreen: false
            LayerShell.Window.scope: "mios-wallpaper"
            LayerShell.Window.layer: LayerShell.Window.LayerBackground
            LayerShell.Window.anchors: LayerShell.Window.AnchorTop | LayerShell.Window.AnchorBottom | LayerShell.Window.AnchorLeft | LayerShell.Window.AnchorRight
            LayerShell.Window.exclusionZone: -1
            LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityNone

            readonly property double shownMs: Date.now()
            property int k: 0

            MeshGradient {
                id: ocean
                anchors.fill: parent
                colors: host.colors
                bg: host.bg
                dark: host.dark
                fps: host.fps
                emitters: host.emitters
                dprCap: host.dprCap
                refSimScale: host.refSimScale
                simScale: host.simScale
                dpr: win.devicePixelRatio
                intro: 1
                onStepCompleted: if (host.fixedSteps > 0) win.fixedStep()
            }
            Connections {
                target: host
                function onTick(t) {
                    ocean.intro = host.introMs > 0 ? Math.min(1, (Date.now() - win.shownMs) / host.introMs) : 1
                    ocean.step(t)
                }
            }
            function fixedStep() {
                if (win.k >= host.fixedSteps) {
                    if (win.k === host.fixedSteps) {
                        win.k++
                        win.finish()
                    }
                    return
                }
                win.k++
                ocean.intro = host.introMs > 0 ? Math.min(1, win.k * (1000 / host.fps) / host.introMs) : 1
                ocean.step(host.t0 + win.k * host.speed / host.fps)
            }
            function finish() {
                if (host.capture === "") {
                    host.captured++
                    return
                }
                const name = host.capture + "-" + String(win.modelData.name).replace(/[^A-Za-z0-9_.-]/g, "_") + ".png"
                ocean.grabToImage(function (result) {
                    console.info("mios-wallpaper: captured", name, result.saveToFile(name))
                    host.captured++
                    if (host.quitAfter && host.captured >= Qt.application.screens.length)
                        Qt.quit()
                })
            }
            onVisibleChanged: if (visible && host.fixedSteps > 0) fixedStartTimer.start()
            property Timer fixedStartTimer: Timer { interval: 200; onTriggered: win.fixedStep() }
        }
    }
}
