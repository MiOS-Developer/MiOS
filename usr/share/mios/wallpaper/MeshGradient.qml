// AI-hint: Native GPU living wallpaper (one output): the canonical living-wallpaper.html colour ocean rendered with
//          two Qt RHI fragment shaders instead of a Chromium page. SIM advects the colour+height field at a LOW
//          internal resolution (simScale of the dpr-capped canvas; RGBA16F ping-pong via a recursive
//          ShaderEffectSource updated only on step()), PRESENT upscales it bilinearly to the output. Every
//          kernel offset uses the HTML's reference grid (refSimScale) so the look matches the HTML.
// AI-related: usr/share/mios/wallpaper/Host.qml, usr/share/mios/wallpaper/shaders/ocean-*.frag, [theme.wallpaper.motion], [theme.wallpaper.native]
import QtQuick

Item {
    id: ocean

    // Inputs (Host.qml / tests; values come from the SSOT through the wallpaper URL query).
    property var colors: []                        // 16 x Qt.vector4d(r, g, b, role weight): ansi_0..ansi_15
    property vector4d bg: Qt.vector4d(0, 0, 0, 1)  // cleared-field colour ([colors].bg)
    property real dark: 1                          // [theme].mode == "dark"
    property real fps: 30                          // steps per second; one step advects by 1 / fps
    property int emitters: 24
    property real dprCap: 1.5                      // HTML DPR_CAP
    property real refSimScale: 0.6                 // HTML SIM_SCALE: reference grid for every kernel offset
    property real simScale: 0.25                   // native internal grid (fraction of the dpr-capped canvas)
    property real dpr: 1
    property real intro: 1                         // 0..1 login spill

    // The HTML's canvas (window * min(dpr, cap)) and its sim grid: the uv-space reference.
    readonly property real capDpr: Math.min(Math.max(dpr, 0.1), dprCap)
    readonly property int canvasW: Math.max(1, Math.round(width * capDpr))
    readonly property int canvasH: Math.max(1, Math.round(height * capDpr))
    readonly property real aspect: canvasW / canvasH
    readonly property vector2d refGrid: Qt.vector2d(Math.max(2, Math.round(canvasW * refSimScale)),
                                                    Math.max(2, Math.round(canvasH * refSimScale)))
    readonly property size simSize: Qt.size(Math.max(2, Math.round(canvasW * simScale)),
                                            Math.max(2, Math.round(canvasH * simScale)))

    property bool resetPending: true
    property bool requested: false                // a step() is in flight (implicit first-use renders do not count)
    property int completedSteps: 0
    signal stepCompleted()
    onSimSizeChanged: resetPending = true

    function p(i) { return colors.length > i ? colors[i] : Qt.vector4d(0, 0, 0, 0) }

    // One simulation step at sim time t (the same t for every output: Host.qml owns the clock).
    function step(t) {
        requested = true
        sim.u_time = t
        sim.u_reset = resetPending ? 1 : 0
        present.u_time = t
        field.scheduleUpdate()
    }

    ShaderEffect {
        id: sim
        width: ocean.width
        height: ocean.height
        blending: false                            // raw rgb + height(a) into the field, never alpha-blended
        property var u_prev: field
        property real u_time: 0
        property real u_dt: 1 / ocean.fps
        property real u_asp: ocean.aspect
        property real u_reset: 1
        property real u_emitters: ocean.emitters
        property vector2d u_ref: ocean.refGrid
        property vector4d u_bg: ocean.bg
        property vector4d p0: ocean.p(0)
        property vector4d p1: ocean.p(1)
        property vector4d p2: ocean.p(2)
        property vector4d p3: ocean.p(3)
        property vector4d p4: ocean.p(4)
        property vector4d p5: ocean.p(5)
        property vector4d p6: ocean.p(6)
        property vector4d p7: ocean.p(7)
        property vector4d p8: ocean.p(8)
        property vector4d p9: ocean.p(9)
        property vector4d p10: ocean.p(10)
        property vector4d p11: ocean.p(11)
        property vector4d p12: ocean.p(12)
        property vector4d p13: ocean.p(13)
        property vector4d p14: ocean.p(14)
        property vector4d p15: ocean.p(15)
        fragmentShader: Qt.resolvedUrl("shaders/ocean-sim.frag.qsb")
    }

    ShaderEffectSource {
        id: field
        visible: false
        sourceItem: sim
        hideSource: true
        live: false                                // rendered only by step(): the sim never runs per vsync
        recursive: true                            // ping-pong: the sim reads its previous field
        format: ShaderEffectSource.RGBA16F         // the HTML's RGBA16F field
        textureSize: ocean.simSize
        smooth: true
        wrapMode: ShaderEffectSource.ClampToEdge
        onScheduledUpdateCompleted: {
            if (!ocean.requested)
                return                             // Qt's first-use render, not a step: the next step still resets
            ocean.requested = false
            if (sim.u_reset > 0.5)
                ocean.resetPending = false
            ocean.completedSteps++
            ocean.stepCompleted()
        }
    }

    ShaderEffect {
        id: present
        anchors.fill: parent
        blending: false
        property var u_field: field
        property real u_time: 0
        property real u_asp: ocean.aspect
        property real u_dark: ocean.dark
        property real u_intro: ocean.intro
        property vector2d u_ref: ocean.refGrid
        fragmentShader: Qt.resolvedUrl("shaders/ocean-present.frag.qsb")
    }
}
