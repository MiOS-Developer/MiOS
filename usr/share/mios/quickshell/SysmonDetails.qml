// AI-hint: Details of the island's system monitor (Sysmon.qml), shown in the popup that grows out of the items on
// hover or tap: CPU % and thread count, memory and swap used of total, every GPU the host still owns (passthrough
// devices are never listed) with its driver, every CPU/GPU temperature sensor with the hottest first, and the network
// rate per watched interface. Sources that are unavailable read "unavailable"; nothing here samples on its own.
// `live` is false while the popup is closed: the rows are then not rebuilt every sample, and the row delegates are
// reused (the Repeater counts rows) rather than recreated when the values change.
// AI-related: usr/share/mios/quickshell/Sysmon.qml, usr/share/mios/quickshell/SysmonItems.qml,
// usr/share/mios/quickshell/PanelWindow.qml, usr/share/mios/mios.toml [desktop.topbar.sysmon]
import QtQuick

Column {
    id: sd
    required property QtObject theme
    property bool live: true
    spacing: 2

    function line(label, value, detail, warn, sub) { return ({ label: label, value: value, detail: detail || "", warn: !!warn, sub: !!sub }) }
    readonly property var rows: live ? build() : []
    function build() {
        const out = []
        const na = "unavailable"
        out.push(line("CPU", Sysmon.cpuOk ? Sysmon.fmtPct(Sysmon.cpu) : "",
                      Sysmon.cpuOk ? (Sysmon.cpuWattsOk ? Sysmon.fmtWatts(Sysmon.cpuWatts).trim() + " · " : "") + Sysmon.cores + " threads" : na, Sysmon.warn("cpu")))
        out.push(line("Memory", Sysmon.ramOk ? Sysmon.fmtPct(Sysmon.ram) : "",
                      Sysmon.ramOk ? Sysmon.fmtKiB(Sysmon.ramUsedKiB) + " of " + Sysmon.fmtKiB(Sysmon.ramTotalKiB) : na, Sysmon.warn("ram")))
        if (Sysmon.ramOk && Sysmon.swapTotalKiB > 0)
            out.push(line("swap", "", Sysmon.fmtKiB(Sysmon.swapUsedKiB) + " of " + Sysmon.fmtKiB(Sysmon.swapTotalKiB), false, true))
        if (!Sysmon.gpus.length) out.push(line("GPU", "", na))
        for (const g of Sysmon.gpus)
            out.push(line(g.primary ? "GPU" : "", Sysmon.fmtPct(g.busy),
                          (g.watts !== null && g.watts !== undefined && !isNaN(g.watts) ? Sysmon.fmtWatts(g.watts).trim() + " · " : "") + g.card + " · " + (g.driver || "?"),
                          g.busy >= Sysmon.warnPct))
        const t = Sysmon.temps.slice().sort((a, b) => b.c - a.c)
        out.push(line("Temperature", Sysmon.tempOk ? Sysmon.fmtTemp(Sysmon.temp) : "", Sysmon.tempOk ? "hottest" : na, Sysmon.warn("temp")))
        for (const s of t)
            out.push(line(s.label, Sysmon.fmtTemp(s.c), (s.kind === "cpu" ? "CPU" : "GPU") + " · " + s.chip, s.c >= Sysmon.warnTemp, true))
        out.push(line("Network", Sysmon.netOk ? "\u{f0045}" + Sysmon.fmtRate(Sysmon.netDown).trim() : "",
                      Sysmon.netOk ? "\u{f005d}" + Sysmon.fmtRate(Sysmon.netUp).trim() + "  per second" : na))
        for (const n of Sysmon.nets)
            out.push(line(n.name, "\u{f0045}" + Sysmon.fmtRate(n.down).trim(), "\u{f005d}" + Sysmon.fmtRate(n.up).trim(), false, true))
        return out
    }
    readonly property real labelWidth: 12 * cell.advanceWidth / 10
    readonly property real valueWidth: 6 * cell.advanceWidth / 10
    TextMetrics { id: cell; font.family: sd.theme.fontFamily; font.pixelSize: sd.theme.fontSize; text: "0000000000" }

    Repeater {
        model: sd.rows.length
        delegate: Row {
            required property int index
            readonly property var modelData: sd.rows[index] || ({ label: "", value: "", detail: "", warn: false, sub: false })
            height: sd.theme.fontSize + 8
            Text {
                width: sd.labelWidth
                text: (modelData.sub ? "  " : "") + modelData.label
                textFormat: Text.PlainText
                elide: Text.ElideRight
                color: modelData.sub ? sd.theme.muted : sd.theme.subtle
                font.family: sd.theme.fontFamily
                font.pixelSize: modelData.sub ? sd.theme.smallFontSize : sd.theme.fontSize
                font.bold: !modelData.sub
                height: parent.height
                verticalAlignment: Text.AlignVCenter
            }
            Text {
                width: sd.valueWidth
                text: modelData.value
                textFormat: Text.PlainText
                color: modelData.warn ? sd.theme.warning : sd.theme.fg
                font.family: sd.theme.fontFamily
                font.pixelSize: modelData.sub ? sd.theme.smallFontSize : sd.theme.fontSize
                height: parent.height
                verticalAlignment: Text.AlignVCenter
            }
            Text {
                text: modelData.detail
                textFormat: Text.PlainText
                color: modelData.value === "" && modelData.detail === "unavailable" ? sd.theme.muted : sd.theme.subtle
                font.family: sd.theme.fontFamily
                font.pixelSize: modelData.sub ? sd.theme.smallFontSize : sd.theme.fontSize
                height: parent.height
                verticalAlignment: Text.AlignVCenter
            }
        }
    }
    Text {
        text: "every " + (Sysmon.intervalMs / 1000) + " s · /proc and /sys, read by the shell"
        textFormat: Text.PlainText
        color: sd.theme.muted
        font.family: sd.theme.fontFamily
        font.pixelSize: sd.theme.smallFontSize
        topPadding: 4
    }
}
