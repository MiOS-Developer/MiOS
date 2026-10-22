// AI-hint: One mobile window-controls tab: the desktop controls tab (hyprbars overlay, MiOS patch) split in two
// around the status pill -- a narrow glass tab rising from the window's top edge with one hyprbars button
// centred in it (left: the window menu, right: close). The compositor draws these tabs itself (hyprbars mobile
// mode, window rule hyprbars:mobile) whenever the projection says so (Mobile.hyprbarsTabs); then this item is
// only the popup anchor and draws nothing. Otherwise (fallback) it draws the same silhouette with the same rules
// the plugin uses: box `tabWidth` x `rowHeight` (Glass.mobileTabWidth / touch target), top corners
// min(r, h) / 2 and concave fillets min(r / 2, h / 2) on both sides where it meets the window (r = the window's
// own rounding), filled with the tab's colour layer (Glass.fill); the compositor's hyprglass layer pass draws the
// glass behind it. The item's width includes both fillets; `x` is the fillet's outer end. The whole box is the
// button's hit area (touch target). One CurveRenderer path, no Canvas or shader effects: cheap on remote outputs.
// AI-related: usr/share/mios/quickshell/Glass.qml, usr/share/mios/quickshell/ControlDot.qml,
// usr/share/mios/quickshell/PanelWindow.qml, usr/share/mios/hyprland/patches/hyprbars-overlay.patch,
// tests/test-mobile-controls-tab.py
import QtQuick
import QtQuick.Shapes

Item {
    id: tab
    property real tabWidth: 0           // the tab's box (fillets excluded)
    property real rowHeight: 0          // the tab's height (the row, the touch target)
    property real windowRadius: 0       // the window's own top-corner rounding (corner and fillet rule)
    property var button: Glass.closeButton
    property bool showIcon: false
    property bool drawn: true           // false: the compositor draws this tab; this item only anchors popups
    readonly property alias dot: dotItem
    readonly property color fill: Glass.fill
    readonly property real rc: Glass.tabCorner(rowHeight, windowRadius)
    readonly property real rf: Glass.tabFillet(rowHeight, windowRadius)
    signal tapped()
    width: tabWidth + 2 * rf
    height: rowHeight

    readonly property real w: Math.max(0, tabWidth)
    readonly property real h: Math.max(0, rowHeight)
    readonly property real ci: Math.max(0.01, Math.min(rc, h / 2, w / 2))
    readonly property real fi: Math.max(0.01, Math.min(rf, h - ci))

    Shape {
        anchors.fill: parent
        visible: tab.drawn
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            fillColor: tab.fill
            strokeWidth: -1
            startX: tab.rf - tab.fi; startY: tab.h
            PathArc { x: tab.rf; y: tab.h - tab.fi; radiusX: tab.fi; radiusY: tab.fi; direction: PathArc.Counterclockwise }
            PathLine { x: tab.rf; y: tab.ci }
            PathArc { x: tab.rf + tab.ci; y: 0; radiusX: tab.ci; radiusY: tab.ci }
            PathLine { x: tab.rf + tab.w - tab.ci; y: 0 }
            PathArc { x: tab.rf + tab.w; y: tab.ci; radiusX: tab.ci; radiusY: tab.ci }
            PathLine { x: tab.rf + tab.w; y: tab.h - tab.fi }
            PathArc { x: tab.rf + tab.w + tab.fi; y: tab.h; radiusX: tab.fi; radiusY: tab.fi; direction: PathArc.Counterclockwise }
            PathLine { x: tab.rf - tab.fi; y: tab.h }
        }
    }
    // the box itself, for the input mask and popup anchoring (fillets excluded)
    Item {
        id: boxItem
        objectName: "tabBox"
        x: tab.rf
        width: tab.w
        height: tab.h
    }
    readonly property alias box: boxItem
    ControlDot {
        id: dotItem
        visible: tab.drawn
        enabled: tab.drawn
        button: tab.button
        showIcon: tab.showIcon
        hitWidth: tab.w
        hitHeight: tab.h
        x: tab.rf + (tab.w - width) / 2
        y: (tab.h - height) / 2
        onTapped: tab.tapped()
    }
}
