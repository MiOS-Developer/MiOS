// AI-hint: Native GPU host for the canonical MiOS living wallpaper (renderer = "native"): one layer-shell background
//          window per output, each drawing MeshGradient.qml (two Qt RHI shaders, no Chromium). ONE clock for all
//          outputs: a single timer steps every output with the same sim time t = elapsed * speed at `fps` steps/s
//          (the HTML's pacing); Qt presents each step on that output's next frame callback.
//          Parameters arrive in the same URL query the Windows HTML reads (mios-wallpaperd wallpaper_url, SSOT):
//          a0..a15 w0..w15 bg fg mode fps speed intro dpr sim n | native: nsim | deterministic tests: t0 steps capture quit.
//          Live rate ([desktop.framerate], operator 2026-10-23: "wallpaper(s) are 1/2 rate globally--except for Unlocked
//          mode(s)"): `ctl` names miosd framerate's state file; it is read once a second and its wallpaper_fps (W = desktop
//          rate / 2, the full desktop rate when unlocked) becomes the step rate -- no restart, no window re-creation, the
//          sim clock stays wall time. `fps` in the URL is the look's reference rate (`ref` overrides it in tests): the SIM
//          shader compounds its per-step constants by ref / fps, so the field evolves the same per second at any rate.
//          No state file (the daemon stopped, the policy is off): the reference rate, exactly as before.
//          Compositor pacing (the state's wallpaper_paced): Qt's ShaderEffectSource asks for a second, identical frame
//          after every layer update, so a timer-driven step costs two commits and the surface presented at 2W (the
//          desktop rate). While mios-pacing holds this surface's frame callbacks to W, each window instead takes ONE
//          step per frame, just before the frame's sync (afterAnimating), so every commit carries a new step and the
//          wallpaper presents at W. A self-check falls back to the timer if steps run faster than 1.5 W (no pacing).
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
    readonly property real refFps: num("ref", num("fps", 30, 1, 240), 1, 240)
    property real fps: num("fps", 30, 1, 240)
    readonly property string ctl: query["ctl"] || ""
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
        running: host.fixedSteps === 0 && !host.paced
        triggeredOnStart: true
        onTriggered: host.tick(host.simTime())
    }

    // ---- compositor pacing: one step per presented frame while the compositor releases frames at W ----
    property bool pacedByCompositor: false        // the state file's wallpaper_paced
    property bool pacedRefused: false             // the self-check saw no pacing: own timer until the flag toggles
    readonly property bool paced: pacedByCompositor && !pacedRefused && fixedSteps === 0
    onPacedByCompositorChanged: pacedRefused = false
    function pacedFrom(text) {
        try {
            return JSON.parse(text).wallpaper_paced === true
        } catch (e) {
        }
        return false
    }
    function refusePacing(rate) {
        if (host.pacedRefused)
            return
        console.warn("mios-wallpaper: compositor pacing not effective (" + rate + " steps/s, rate " + host.fps + "): back to the own timer")
        host.pacedRefused = true
        host.pacedRetry.restart()
    }
    // a compositor that was reset (plugin reload, config reload) paces again once miosd re-applied: retry later
    property Timer pacedRetry: Timer {
        interval: 20000
        onTriggered: host.pacedRefused = false
    }

    // ---- the live wallpaper rate W from miosd framerate's state file (needs QML_XHR_ALLOW_FILE_READ=1) ----
    function rateFrom(text) {
        try {
            const w = Number(JSON.parse(text).wallpaper_fps)
            if (w >= 1)
                return Math.max(1, Math.min(240, Math.round(w)))
            if (w === 0)
                return host.fps                    // outputs not known yet (unlocked at startup): keep the current rate
        } catch (e) {
        }
        return host.refFps                         // no state file: the reference rate
    }
    function readRate() {
        const xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE)
                return
            const next = host.rateFrom(xhr.responseText)
            if (next !== host.fps) {
                console.info("mios-wallpaper: step rate", host.fps, "->", next, "(reference", host.refFps + ")")
                host.fps = next
            }
            const paced = host.pacedFrom(xhr.responseText)
            if (paced !== host.pacedByCompositor) {
                console.info("mios-wallpaper: compositor pacing", paced ? "on (one step per presented frame)" : "off (own timer)")
                host.pacedByCompositor = paced
            }
        }
        xhr.open("GET", "file://" + host.ctl)
        xhr.send()
    }
    property Timer rateWatch: Timer {
        interval: 1000
        repeat: true
        running: host.ctl !== "" && host.fixedSteps === 0
        triggeredOnStart: true
        onTriggered: host.readRate()
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
                refFps: host.refFps
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
                // entering paced mode: ask for the first frame (each paced frame then asks for the next)
                function onPacedChanged() {
                    if (host.paced)
                        win.update()
                }
            }
            // Paced: the step goes into the frame being prepared (GUI thread, before its sync); the compositor releases
            // that frame at W, so commits = steps = W. update() keeps the loop when nothing else asks for a frame.
            property int pacedSteps: 0
            onAfterAnimating: {
                if (!host.paced || !win.visible)
                    return
                win.pacedSteps++
                ocean.intro = host.introMs > 0 ? Math.min(1, (Date.now() - win.shownMs) / host.introMs) : 1
                ocean.step(host.simTime())
                win.update()
            }
            property Timer pacedCheck: Timer {
                interval: 2000
                repeat: true
                running: host.paced
                onRunningChanged: win.pacedSteps = 0
                onTriggered: {
                    const rate = win.pacedSteps / 2
                    win.pacedSteps = 0
                    if (rate > host.fps * 1.5 + 2)
                        host.refusePacing(rate)
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
