// AI-hint: Shared live geometry between shell surfaces (no data of its own). The status island
// publishes its on-screen rectangle per output so the top-left app menus (AppMenu.qml) stop short
// of it instead of drawing under it, and the island as drawn right now (mobile: while it moves between
// the window tabs), its corner radius and whether it is mapped, so the notification blob
// (Notifications.qml) grows out of it.
// AI-related: usr/share/mios/quickshell/PanelWindow.qml, usr/share/mios/quickshell/AppMenu.qml,
// usr/share/mios/quickshell/Notifications.qml
pragma Singleton
import QtQuick
import Quickshell

Singleton {
    // { "<output name>": {x, y, width, height} } in output-local logical pixels.
    property var islands: ({})
    function setIsland(output, x, y, width, height) {
        const next = Object.assign({}, islands)
        next[output] = { x: x, y: y, width: width, height: height }
        islands = next
    }
    // { "<output name>": {x, y, width, height, radius, shown} }: the drawn island (output-local logical px).
    property var drawnIslands: ({})
    function setDrawnIsland(output, r) {
        const next = Object.assign({}, drawnIslands)
        next[output] = r
        drawnIslands = next
    }
}
