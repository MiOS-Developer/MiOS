// AI-hint: A popup surface that grows out of its trigger (Emerge.qml) on a bar window. Rendered as a
// WlrLayershell overlay PanelWindow on NotifModel.layerNamespace so the compositor's hyprglass pass draws
// the global glass material (blur, refraction, fresnel rim, bevel and top specular highlight) across the
// entire morph and at rest. The panel sits directly below the bar, aligned to the trigger ("center",
// "left" or "right"), clamped on screen. hostScreenX defaults to a horizontally centred bar (the status
// island); hosts placed elsewhere (the top-left app menus) set it, and hostHeight, themselves.
// AI-related: usr/share/mios/quickshell/Emerge.qml, usr/share/mios/quickshell/PanelWindow.qml,
// usr/share/mios/quickshell/Glass.qml, usr/share/mios/quickshell/NotifModel.qml
import QtQuick
import Quickshell as QS
import Quickshell.Wayland

QS.PanelWindow {
    id: mp
    required property QtObject theme
    required property Item trigger
    property bool open: false
    property string align: "center"
    property real panelWidth: Glass.bar
    property real panelHeight: Glass.bar
    property real gap: Glass.gap                    // one gap below the bar it grows out of (design language)
    property int duration: 200
    property real triggerRadius: trigger ? Glass.corner(Math.min(trigger.width, trigger.height)) : 0
    property real panelRadius: Glass.panelRadius(panelWidth, panelHeight)
    default property alias content: emerge.content
    readonly property alias progress: emerge.progress

    component AnchorSpec: QtObject {
        property var window: null
    }
    readonly property AnchorSpec anchor: AnchorSpec {}
    property var host: anchor.window
    property real hostHeight: host ? host.height : Glass.bar
    property real hostScreenX: host ? (typeof host.hudScreenX === "number" ? host.hudScreenX
        : (host.mobile ? 0 : (host.screen ? (host.screen.width - host.width) / 2 : 0))) : 0
    property real hostScreenY: host && host.margins ? host.margins.top : Glass.gap
    readonly property real screenWidth: host && host.screen ? host.screen.width : panelWidth + 2 * Glass.gap
    // Trigger rectangle in the bar window's coordinates.
    // mapToItem() is not reactive: touch the geometry that moves the trigger (and `open`, so every
    // opening re-reads it) to keep the mapping current.
    readonly property rect trigRect: {
        if (!trigger) return Qt.rect(0, 0, Glass.bar, Glass.bar)
        const deps = [open, trigger.x, trigger.y, trigger.width, trigger.height,
                      trigger.parent ? trigger.parent.x : 0, trigger.parent ? trigger.parent.width : 0,
                      host ? host.width : 0, host ? host.height : 0]
        if (!deps.length) return Qt.rect(0, 0, Glass.bar, Glass.bar)
        const p0 = trigger.mapToItem(null, 0, 0)
        const p1 = trigger.mapToItem(null, trigger.width, trigger.height)
        return Qt.rect(p0.x, p0.y, Math.max(1, p1.x - p0.x), Math.max(1, p1.y - p0.y))
    }
    readonly property point trig: Qt.point(trigRect.x, trigRect.y)
    readonly property real wantX: align === "right" ? trigRect.x + trigRect.width - panelWidth
                                : align === "left" ? trigRect.x
                                : trigRect.x + trigRect.width / 2 - panelWidth / 2
    readonly property real popupX: Math.max(Glass.gap - hostScreenX,
                                            Math.min(wantX, screenWidth - hostScreenX - panelWidth - Glass.gap))
    readonly property rect fromScreenRect: Qt.rect(Math.round(hostScreenX + trigRect.x), Math.round(hostScreenY + trigRect.y),
                                                   Math.max(1, Math.round(trigRect.width)), Math.max(1, Math.round(trigRect.height)))
    readonly property rect toScreenRect: Qt.rect(Math.round(hostScreenX + popupX), Math.round(hostScreenY + hostHeight + gap),
                                                 Math.max(1, Math.round(panelWidth)), Math.max(1, Math.round(panelHeight)))
    readonly property rect morphBox: {
        const x0 = Math.min(fromScreenRect.x, toScreenRect.x)
        const y0 = Math.min(fromScreenRect.y, toScreenRect.y)
        const x1 = Math.max(fromScreenRect.x + fromScreenRect.width, toScreenRect.x + toScreenRect.width)
        const y1 = Math.max(fromScreenRect.y + fromScreenRect.height, toScreenRect.y + toScreenRect.height)
        return Qt.rect(x0, y0, Math.max(1, x1 - x0), Math.max(1, y1 - y0))
    }
    readonly property bool atRest: open && emerge.progress >= 1
    readonly property rect activeBox: atRest ? toScreenRect : morphBox

    screen: host ? host.screen : null
    WlrLayershell.namespace: Mobile.layerNamespace(NotifModel.layerNamespace, screen ? screen.name : "")
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    anchors { top: true; left: true }
    exclusiveZone: 0
    exclusionMode: QS.ExclusionMode.Ignore
    visible: open || emerge.shown
    color: "transparent"
    margins.left: activeBox.x
    margins.top: activeBox.y
    implicitWidth: activeBox.width
    implicitHeight: activeBox.height
    mask: QS.Region { item: emerge.glass }

    Emerge {
        id: emerge
        open: mp.open
        duration: mp.duration
        fromRect: Qt.rect(mp.fromScreenRect.x - mp.activeBox.x, mp.fromScreenRect.y - mp.activeBox.y,
                          mp.fromScreenRect.width, mp.fromScreenRect.height)
        toRect: Qt.rect(mp.toScreenRect.x - mp.activeBox.x, mp.toScreenRect.y - mp.activeBox.y,
                        mp.toScreenRect.width, mp.toScreenRect.height)
        fromRadius: mp.triggerRadius
        toRadius: mp.panelRadius
        fill: Glass.fill
        fromFill: Glass.fill
        outline: mp.atRest ? "transparent" : mp.theme.outlineColor
    }
}
