// AI-hint: THE one glass shape of the shell (operator rule: ONE QML glass component for every surface). Every body
// (status island, app-menu pills, popups, menus, cards, dock, previews, toolbars) and every highlight on a body (hover,
// selection, chips, rows) is a GlassSurface, so all of them share one corner curve and one radius rule from the design
// language ([theme.shell] in mios.toml, projected into the theme bridge's "design" table and read by Glass.qml):
//   radius  Glass.corner(min(width, height)) = min(R, h / 2) by default -- the same rule hyprglass' layer pass uses for
//           the layer box it draws the glass in (render.rs layer_geometry_source), so the colour layer and the glass
//           under it have one outline; nested highlights pass Glass.inner(outer radius, inset) (concentric corners).
//   curve   Glass.cornerPower: the superellipse exponent windows (decoration rounding_power), hyprglass and the layers
//           use. 2 draws an exact circular arc (Rectangle); above 2 one cubic per corner whose midpoint lies on
//           |x|^p + |y|^p = r^p (k = (2^(-1/p) - 1/2) / (3/8)), drawn by the CurveRenderer (no Canvas, no shader).
//   colour  Glass.fill for a body: the colour layer over the compositor's glass. The glass itself (frost, refraction,
//           tint, fresnel / specular / bevel lights, inner shadow) is hyprglass' "mios" preset drawn by the compositor
//           behind every non-transparent shell pixel; the shell never draws a second glass, shadow or highlight.
// Children are laid out inside the item as in a Rectangle; `border` is a hairline only (Glass.hairline wide).
// AI-related: usr/share/mios/quickshell/Glass.qml, usr/share/mios/quickshell/Theme.qml, usr/share/mios/mios.toml
// [theme.shell] design language, tools/native/mios-gen/src/render.rs design_tokens, tests/test-design-language.py
import QtQuick
import QtQuick.Shapes

Item {
    id: surface
    property color color: Glass.fill
    property real radius: Glass.corner(Math.min(width, height))
    property real power: Glass.cornerPower
    property color borderColor: "transparent"
    property bool hairline: false            // a Glass.hairline outline in borderColor
    property bool specular: false
    property color specularColor: Glass.theme ? Glass.theme.withAlpha(Glass.theme.fg, 0.20) : "#33ffffff"
    property color rimColor: Glass.rimHex ? (Glass.theme ? Glass.theme.withAlpha(Glass.rimHex, 0.45) : "#40ffffff") : "#40ffffff"
    readonly property real r: Math.max(0, Math.min(radius, width / 2, height / 2))
    readonly property bool circular: power <= 2.0001
    // the superellipse corner's cubic handle, as a fraction of the radius
    readonly property real k: (Math.pow(2, -1 / Math.max(2, power)) - 0.5) / 0.375

    Rectangle {
        id: bgRect
        anchors.fill: parent
        visible: surface.circular
        radius: surface.r
        color: surface.color
        border.width: (surface.hairline || surface.specular) ? Glass.hairline : 0
        border.color: surface.specular ? surface.rimColor : surface.borderColor
        clip: surface.specular
    }

    // Inner contour-following specular highlight border
    Rectangle {
        id: innerSpecular
        visible: (surface.specular || surface.hairline) && surface.circular && surface.r > Glass.hairline
        anchors.fill: parent
        anchors.margins: Glass.hairline
        radius: Math.max(0, surface.r - Glass.hairline)
        color: "transparent"
        border.width: Glass.hairline
        border.color: surface.specular ? surface.specularColor : Qt.rgba(1, 1, 1, 0.16)
    }

    // Top specular highlight gradient conforming to rounded contour
    Rectangle {
        visible: surface.specular && surface.circular && surface.r > Glass.hairline
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: Glass.hairline
        height: Math.min(surface.height / 2, 28)
        radius: Math.max(0, surface.r - Glass.hairline)
        gradient: Gradient {
            GradientStop { position: 0.0; color: Glass.theme ? Glass.theme.withAlpha(Glass.theme.fg, 0.16) : "#28ffffff" }
            GradientStop { position: 1.0; color: "transparent" }
        }
    }
    Shape {
        anchors.fill: parent
        visible: !surface.circular && surface.width > 0 && surface.height > 0
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            fillColor: surface.color
            strokeColor: (surface.hairline || surface.specular) ? (surface.specular ? surface.rimColor : surface.borderColor) : "transparent"
            strokeWidth: (surface.hairline || surface.specular) ? Glass.hairline : -1
            startX: surface.r; startY: 0
            PathLine { x: surface.width - surface.r; y: 0 }
            PathCubic { x: surface.width; y: surface.r
                        control1X: surface.width - (surface.r * (1 - surface.k)); control1Y: 0
                        control2X: surface.width; control2Y: surface.r * (1 - surface.k) }
            PathLine { x: surface.width; y: surface.height - surface.r }
            PathCubic { x: surface.width - surface.r; y: surface.height
                        control1X: surface.width; control1Y: surface.height - (surface.r * (1 - surface.k))
                        control2X: surface.width - (surface.r * (1 - surface.k)); control2Y: surface.height }
            PathLine { x: surface.r; y: surface.height }
            PathCubic { x: 0; y: surface.height - surface.r
                        control1X: surface.r * (1 - surface.k); control1Y: surface.height
                        control2X: 0; control2Y: surface.height - (surface.r * (1 - surface.k)) }
            PathLine { x: 0; y: surface.r }
            PathCubic { x: surface.r; y: 0
                        control1X: 0; control1Y: surface.r * (1 - surface.k)
                        control2X: surface.r * (1 - surface.k); control2Y: 0 }
        }
    }
}
