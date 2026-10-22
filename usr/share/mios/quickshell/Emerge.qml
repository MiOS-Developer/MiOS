// AI-hint: Shared "emerge from the trigger" morph for every shell popup. The glass body starts as the
// trigger's own rectangle (same radius, same glass) and grows into the popup's final rectangle; closing
// runs the same morph backwards, so the popup melts back into what opened it and never appears
// anywhere else on screen. Coordinates are in this item's parent space; content is laid out at the
// final size and fades in over the second half of the morph.
// AI-related: usr/share/mios/quickshell/MeltPopup.qml, usr/share/mios/quickshell/AppMenu.qml,
// usr/share/mios/quickshell/PanelWindow.qml, mios.toml [desktop.topbar] popup_ms
import QtQuick

Item {
    id: em
    property bool open: false
    property rect fromRect: Qt.rect(0, 0, 0, 0)   // the trigger
    property rect toRect: Qt.rect(0, 0, 0, 0)     // the open popup
    property real fromRadius: 0
    property real toRadius: 0
    property int duration: 200
    property color fill: "transparent"
    property color fromFill: fill     // the trigger's glass; blends into `fill` as the body grows
    property color outline: "transparent"
    property real progress: 0
    readonly property bool shown: progress > 0
    readonly property alias glass: glass
    default property alias content: body.data

    anchors.fill: parent
    visible: shown

    function lerp(a, b) { return a + (b - a) * progress }
    function run() {
        const to = open ? 1 : 0
        if (anim.running && anim.to === to) return
        anim.stop()
        if (progress === to) return
        anim.to = to
        anim.duration = Math.max(1, Math.round(duration * Math.abs(to - progress)))
        anim.easing.type = open ? Easing.OutCubic : Easing.InCubic
        anim.start()
    }
    onOpenChanged: run()
    Component.onCompleted: run()

    NumberAnimation { id: anim; target: em; property: "progress" }

    Rectangle {
        id: glass
        x: em.lerp(em.fromRect.x, em.toRect.x)
        y: em.lerp(em.fromRect.y, em.toRect.y)
        width: Math.max(1, em.lerp(em.fromRect.width, em.toRect.width))
        height: Math.max(1, em.lerp(em.fromRect.height, em.toRect.height))
        radius: Math.min(em.lerp(em.fromRadius, em.toRadius), width / 2, height / 2)
        color: Qt.rgba(em.lerp(em.fromFill.r, em.fill.r), em.lerp(em.fromFill.g, em.fill.g),
                       em.lerp(em.fromFill.b, em.fill.b), em.lerp(em.fromFill.a, em.fill.a))
        border.width: 1
        border.color: em.outline
        clip: true
        Item {
            id: body
            x: em.toRect.x - glass.x
            y: em.toRect.y - glass.y
            width: em.toRect.width
            height: em.toRect.height
            opacity: Math.max(0, (em.progress - 0.5) * 2)
        }
    }
}
