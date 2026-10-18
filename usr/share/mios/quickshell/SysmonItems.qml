// AI-hint: The status island's system-monitor items (Sysmon.qml values, [desktop.topbar.sysmon] items / mobile_items):
// each is a subtle icon followed by its value in the shell's monospace font, in a cell as wide as the widest value it
// can take, so the island never changes width as the numbers move and every icon stays put. Desktop shows "icon value"; compact (mobile outputs) drops the gap and, when the island between the
// window tabs is too narrow, drops items from the end of the list (the list order is the priority). An item appears
// once its source has produced a value; an unavailable source (no GPU, no sensors, permission denied) never shows.
// Hovering reports `hovered`; a tap or click emits `tapped` (the island opens SysmonDetails.qml from them).
// AI-related: usr/share/mios/quickshell/Sysmon.qml, usr/share/mios/quickshell/SysmonDetails.qml,
// usr/share/mios/quickshell/PanelWindow.qml, usr/share/mios/mios.toml [desktop.topbar.sysmon]
import QtQuick

Item {
    id: si
    required property QtObject theme
    property var keys: []
    property bool compact: false
    property real maxWidth: 1e6
    readonly property bool hovered: hit.containsMouse
    signal tapped()
    readonly property real gap: compact ? Math.round(theme.edgeMargin / 2) : theme.edgeMargin

    // Monospace cell width of the shell font: every item's width is its character count times this.
    TextMetrics {
        id: cell
        font.family: si.theme.fontFamily
        font.pixelSize: si.theme.fontSize
        text: "0000000000"
    }
    readonly property real cellWidth: cell.advanceWidth / 10
    function icon(k) {
        return k === "cpu" ? "\u{f0ee0}" : k === "ram" ? "\u{f035b}" : k === "gpu" ? "\u{f08ae}"
             : k === "temp" ? "\u{f050f}" : k === "net" ? "\u{f0045}" : ""
    }
    readonly property var available: keys.filter(k => Sysmon.ok(k))
    function cellsFor(k, withWatts) {
        const val = k === "net" ? (compact ? 9 : 10)
                  : (withWatts && k === "cpu" && Sysmon.cpuWattsOk) ? 9
                  : (withWatts && k === "gpu" && Sysmon.gpuWattsOk) ? 9
                  : 4
        return 2 + (compact ? 0 : 1) + val
    }
    readonly property real fullWattsWidth: {
        let w = 0
        for (let i = 0; i < available.length; i++) w += (i ? gap : 0) + Math.ceil(cellsFor(available[i], true) * cellWidth)
        return w
    }
    readonly property bool showWatts: !compact || fullWattsWidth <= maxWidth + 0.5
    // the value, trimmed: the cell (widthOf) is fixed, the text starts right after the icon
    function value(k) {
        const sp = compact ? "" : " "
        const v = k === "cpu" ? Sysmon.fmtPct(Sysmon.cpu).trim() + (showWatts && Sysmon.cpuWattsOk ? " " + Sysmon.fmtWatts(Sysmon.cpuWatts).trim() : "")
                : k === "ram" ? Sysmon.fmtPct(Sysmon.ram)
                : k === "gpu" ? Sysmon.fmtPct(Sysmon.gpu).trim() + (showWatts && Sysmon.gpuWattsOk ? " " + Sysmon.fmtWatts(Sysmon.gpuWatts).trim() : "")
                : k === "temp" ? Sysmon.fmtTemp(Sysmon.temp)
                : k === "net" ? Sysmon.fmtRate(Sysmon.netDown).trim() + sp + "\u{f005d}" + Sysmon.fmtRate(Sysmon.netUp).trim() : ""
        return v.trim()
    }
    // characters per item (icon, optional gap, value): fixed by the formats above
    function cells(k) { return cellsFor(k, showWatts) }
    function widthOf(k) { return Math.ceil(cells(k) * cellWidth) }
    // the leading items that fit in maxWidth
    readonly property var fitted: {
        const out = []
        let w = 0
        for (const k of available) {
            const next = w + (out.length ? gap : 0) + widthOf(k)
            if (next > maxWidth + 0.5) break
            out.push(k)
            w = next
        }
        return out
    }
    readonly property real contentWidth: {
        let w = 0
        for (let i = 0; i < fitted.length; i++) w += (i ? gap : 0) + widthOf(fitted[i])
        return w
    }
    implicitWidth: contentWidth
    implicitHeight: theme.topHeight
    visible: fitted.length > 0

    Row {
        spacing: si.gap
        Repeater {
            model: si.fitted
            delegate: Row {
                id: item
                required property string modelData
                width: si.widthOf(modelData)
                Text {
                    text: si.icon(item.modelData) + (si.compact ? "" : " ")
                    textFormat: Text.PlainText
                    color: si.theme.subtle
                    font.family: si.theme.fontFamily
                    font.pixelSize: si.theme.fontSize
                    height: si.height
                    verticalAlignment: Text.AlignVCenter
                }
                Text {
                    text: si.value(item.modelData)
                    textFormat: Text.PlainText
                    color: Sysmon.warn(item.modelData) ? si.theme.warning : si.theme.fg
                    font.family: si.theme.fontFamily
                    font.pixelSize: si.theme.fontSize
                    height: si.height
                    verticalAlignment: Text.AlignVCenter
                }
            }
        }
    }
    MouseArea {
        id: hit
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: si.tapped()
    }
}
