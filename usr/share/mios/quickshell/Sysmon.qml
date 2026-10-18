// AI-hint: Native system monitor for the status island ([desktop.topbar.sysmon]): one shared sampler for every output.
// The shell reads the kernel's own counters itself -- /proc/stat (CPU %), /proc/meminfo (RAM %), /proc/net/dev (network
// down/up rate), /sys/class/drm/card*/device/gpu_busy_percent (GPU %) and /sys/class/hwmon/*/temp*_input (hottest
// CPU/GPU temperature) -- through Quickshell FileViews: no helper process per sample, no new runtime dependency. One
// Timer drives every source (interval_ms, never faster than 500 ms; 1 Hz default) and only the sources a configured
// item needs are read. Which sensors, GPUs and interfaces exist is discovered by one short sh scan at start and every
// rediscover_s (hotplug); GPUs bound to a passthrough driver (passthrough_drivers: a VM owns the device) are never
// read, nor are their sensors. A missing or unreadable source (permission denied, no such GPU) only hides its item:
// nothing is logged, nothing throws. Tests point MIOS_SYSMON_ROOT at a fixture tree (a prefix for /proc and /sys) and
// MIOS_TOML_GET at a fixture resolver; IPC target "sysmon" (state) reports what the shell currently shows.
// AI-related: usr/share/mios/quickshell/SysmonItems.qml, usr/share/mios/quickshell/SysmonDetails.qml,
// usr/share/mios/quickshell/PanelWindow.qml, usr/share/mios/mios.toml [desktop.topbar.sysmon], tests/test-island-sysmon.py
pragma Singleton
import QtQuick
import QtQml.Models
import Quickshell
import Quickshell.Io

Singleton {
    id: sm

    // ---- configuration ([desktop.topbar.sysmon] through the layered resolver) -----------------------------------
    property var cfg: ({})
    property bool cfgLoaded: false
    readonly property var allItems: ["cpu", "ram", "gpu", "temp", "net"]
    function num(v, d) { return typeof v === "number" && isFinite(v) ? v : d }
    function strings(v, d) { return Array.isArray(v) ? v.filter(s => typeof s === "string") : d }
    function itemList(v) {
        const out = []
        for (const k of strings(v, allItems)) if (allItems.indexOf(k) >= 0 && out.indexOf(k) < 0) out.push(k)
        return out
    }
    readonly property bool enabled: cfg.enabled !== false
    readonly property int intervalMs: Math.round(Math.max(500, Math.min(60000, num(cfg.interval_ms, 1000))))
    readonly property var items: enabled ? itemList(cfg.items) : []
    readonly property var mobileItems: enabled ? itemList(cfg.mobile_items) : []
    readonly property bool hoverDetails: cfg.hover_details !== false
    readonly property real gpuSmoothing: Math.max(0.05, Math.min(1, num(cfg.gpu_smoothing, 0.5)))
    readonly property var cpuChips: strings(cfg.cpu_temp_chips, ["k10temp", "zenpower", "coretemp", "cpu_thermal"])
    readonly property var gpuChips: strings(cfg.gpu_temp_chips, ["amdgpu", "radeon", "i915", "xe", "nouveau"])
    readonly property var passthrough: strings(cfg.passthrough_drivers, ["vfio-pci", "pci-stub"])
    readonly property var netInterfaces: strings(cfg.net_interfaces, [])
    readonly property real warnPct: num(cfg.warn_pct, 85)
    readonly property real warnTemp: num(cfg.warn_temp_c, 85)
    readonly property int rediscoverMs: Math.round(Math.max(5, num(cfg.rediscover_s, 60)) * 1000)
    // A source is read only when a configured item (desktop or mobile) needs it.
    function wants(k) { return items.indexOf(k) >= 0 || mobileItems.indexOf(k) >= 0 }
    readonly property bool active: enabled && cfgLoaded && (items.length > 0 || mobileItems.length > 0)

    readonly property string root: Quickshell.env("MIOS_SYSMON_ROOT") || ""
    property string resolver: Quickshell.env("MIOS_TOML_GET") || "/usr/bin/mios-toml-get"

    // ---- what the island shows -----------------------------------------------------------------------------------
    property real cpu: NaN          // busy %, all CPUs, over the last interval
    property real cpuWatts: NaN     // CPU package power in W (RAPL energy_uj delta or hwmon power*_average/input)
    property int cores: 0
    property real ram: NaN          // (MemTotal - MemAvailable) / MemTotal %
    property real ramUsedKiB: 0
    property real ramTotalKiB: 0
    property real swapUsedKiB: 0
    property real swapTotalKiB: 0
    property real gpu: NaN          // primary GPU busy % (smoothed)
    property real gpuWatts: NaN     // primary GPU power in W (hwmon power*_average/input, smoothed)
    property var gpus: []           // [{card, driver, busy, watts, primary}]
    property real temp: NaN         // hottest CPU/GPU sensor, degrees C
    property var temps: []          // [{chip, kind, label, c}]
    property real netDown: NaN      // bytes/s, summed over the watched interfaces
    property real netUp: NaN
    property var nets: []           // [{name, down, up}]
    readonly property bool cpuOk: !isNaN(cpu)
    readonly property bool cpuWattsOk: !isNaN(cpuWatts)
    readonly property bool ramOk: !isNaN(ram)
    readonly property bool gpuOk: !isNaN(gpu)
    readonly property bool gpuWattsOk: !isNaN(gpuWatts)
    readonly property bool tempOk: !isNaN(temp)
    readonly property bool netOk: !isNaN(netDown)
    function ok(k) {
        return k === "cpu" ? cpuOk : k === "ram" ? ramOk : k === "gpu" ? gpuOk : k === "temp" ? tempOk : k === "net" ? netOk : false
    }
    property int samples: 0
    // MiOS-Tmux (mios-sysmon-line) reads the live line from this file: "EPOCH<TAB>LINE<TAB>COMPACT", rewritten every
    // sample (COMPACT feeds narrow/phone tmux clients: mios-sysmon-line --compact). Only the live shell writes it
    // (never a fixture root, unless a test names the file in MIOS_SYSMON_LINE_FILE).
    readonly property string lineFile: Quickshell.env("MIOS_SYSMON_LINE_FILE")
        || (root === "" && (Quickshell.env("XDG_RUNTIME_DIR") || "") !== "" ? Quickshell.env("XDG_RUNTIME_DIR") + "/mios-sysmon.line" : "")
    property FileView lineOut: FileView { path: sm.lineFile; atomicWrites: true; printErrors: false }
    onSamplesChanged: if (lineFile !== "" && (statusLine !== "" || compactLine !== ""))
        lineOut.setText(Math.floor(Date.now() / 1000) + "\t" + statusLine + "\t" + compactLine + "\n")

    // ---- formatting (fixed width in the monospace shell font, so the island never jitters) -------------------------
    function pad(s, n) { s = String(s); while (s.length < n) s = " " + s; return s }
    function fmtPct(v) { return isNaN(v) ? " --%" : pad(Math.round(Math.max(0, Math.min(100, v))), 3) + "%" }
    function fmtWatts(v) { return isNaN(v) ? " --W" : pad(Math.round(Math.max(0, v)), 3) + "W" }
    function fmtTemp(v) { return isNaN(v) ? " --°" : pad(Math.round(v), 3) + "°" }
    // bytes/s -> at most 4 characters: "999B", "1.2K", " 34K", "1.0M"
    function fmtRate(b) {
        if (!(b >= 0)) return "  --"
        const u = ["B", "K", "M", "G", "T"]
        let i = 0
        while (b >= 999.5 && i < u.length - 1) { b /= 1024; i++ }
        const s = (i > 0 && b < 9.95) ? b.toFixed(1) : String(Math.round(b))
        return pad(s + u[i], 4)
    }
    function fmtKiB(k) {
        const g = k / 1048576
        return g >= 100 ? g.toFixed(0) + " GiB" : g >= 1 ? g.toFixed(1) + " GiB" : (k / 1024).toFixed(0) + " MiB"
    }
    function warn(k) {
        return (k === "cpu" && cpu >= warnPct) || (k === "ram" && ram >= warnPct)
            || (k === "gpu" && gpu >= warnPct) || (k === "temp" && temp >= warnTemp)
    }
    readonly property string statusLine: {
        const parts = []
        for (const k of items) {
            if (!ok(k)) continue
            if (k === "cpu") parts.push("\u{f0ee0} " + fmtPct(cpu).trim() + (cpuWattsOk ? " " + fmtWatts(cpuWatts).trim() : ""))
            else if (k === "ram") parts.push("\u{f035b} " + fmtPct(ram).trim())
            else if (k === "gpu") parts.push("\u{f08ae} " + fmtPct(gpu).trim() + (gpuWattsOk ? " " + fmtWatts(gpuWatts).trim() : ""))
            else if (k === "temp") parts.push("\u{f050f} " + fmtTemp(temp).trim())
            else if (k === "net") parts.push("\u{f0045} " + fmtRate(netDown).trim() + " \u{f005d}" + fmtRate(netUp).trim())
        }
        return parts.join("  ")
    }
    // The mobile island's items (mobile_items) in its compact form -- icon+value, no watts -- for narrow MiOS-Tmux clients.
    readonly property string compactLine: {
        const parts = []
        for (const k of mobileItems) {
            if (!ok(k)) continue
            if (k === "cpu") parts.push("\u{f0ee0}" + fmtPct(cpu).trim())
            else if (k === "ram") parts.push("\u{f035b}" + fmtPct(ram).trim())
            else if (k === "gpu") parts.push("\u{f08ae}" + fmtPct(gpu).trim())
            else if (k === "temp") parts.push("\u{f050f}" + fmtTemp(temp).trim())
            else if (k === "net") parts.push("\u{f0045}" + fmtRate(netDown).trim() + "\u{f005d}" + fmtRate(netUp).trim())
        }
        return parts.join(" ")
    }

    // ---- parsers (pure; fixtures exercise every one) ---------------------------------------------------------------
    // /proc/stat: the aggregate "cpu" line -> {idle, total, cores}; guest time is already inside user/nice.
    function parseStat(t) {
        const lines = String(t || "").split("\n")
        let agg = null, cores = 0
        for (const l of lines) {
            if (/^cpu\s/.test(l)) agg = l
            else if (/^cpu\d+\s/.test(l)) cores++
        }
        if (!agg) return null
        const v = agg.trim().split(/\s+/).slice(1, 9).map(Number)
        if (v.length < 4 || v.some(x => !isFinite(x) || x < 0)) return null
        const idle = v[3] + (v[4] || 0)
        let total = 0
        for (const x of v) total += x
        return ({ idle: idle, total: total, cores: cores })
    }
    // /proc/meminfo -> kB fields; MemAvailable falls back to MemFree + Buffers + Cached on kernels without it.
    function parseMeminfo(t) {
        const f = {}
        for (const l of String(t || "").split("\n")) {
            const m = /^(\w+):\s+(\d+)/.exec(l)
            if (m) f[m[1]] = Number(m[2])
        }
        if (!(f.MemTotal > 0)) return null
        const avail = f.MemAvailable !== undefined ? f.MemAvailable : (f.MemFree || 0) + (f.Buffers || 0) + (f.Cached || 0)
        return ({ total: f.MemTotal, available: Math.min(avail, f.MemTotal),
                  swapTotal: f.SwapTotal || 0, swapFree: Math.min(f.SwapFree || 0, f.SwapTotal || 0) })
    }
    // /proc/net/dev -> {iface: {rx, tx}} (bytes); header lines and malformed rows are skipped.
    function parseNetDev(t) {
        const out = {}
        let any = false
        for (const l of String(t || "").split("\n")) {
            const c = l.indexOf(":")
            if (c < 0) continue
            const name = l.slice(0, c).trim()
            const v = l.slice(c + 1).trim().split(/\s+/).map(Number)
            if (!name || v.length < 9 || !isFinite(v[0]) || !isFinite(v[8])) continue
            out[name] = { rx: v[0], tx: v[8] }
            any = true
        }
        return any ? out : null
    }
    // gpu_busy_percent -> 0..100, else NaN
    function parsePercent(t) {
        const s = String(t || "").trim()
        if (!/^\d+$/.test(s)) return NaN
        const v = Number(s)
        return v <= 100 ? v : NaN
    }
    // temp*_input (millidegrees C) -> degrees; implausible readings (unwired inputs, -62, 0) are NaN
    function parseMilliC(t) {
        const s = String(t || "").trim()
        if (!/^-?\d+$/.test(s)) return NaN
        const c = Number(s) / 1000
        return (c > 0 && c < 150) ? c : NaN
    }
    // power*_average / power*_input -> Watts (standard hwmon >= 1e6 is uW; AMD APU SMU PPT 1000..999999 is mW)
    function parsePowerUw(t) {
        const s = String(t || "").trim()
        if (!/^\d+$/.test(s)) return NaN
        const v = Number(s)
        if (!(v > 0)) return NaN
        const w = v >= 1e6 ? v / 1e6 : v >= 1000 ? v / 1000 : v
        return (w > 0 && w < 2000) ? w : NaN
    }
    // powercap intel-rapl:*/energy_uj -> cumulative microjoules
    function parseEnergyUj(t) {
        const s = String(t || "").trim()
        if (!/^\d+$/.test(s)) return NaN
        const v = Number(s)
        return isFinite(v) && v >= 0 ? v : NaN
    }
    // the discovery scan's tab-separated records -> {temps, gpus, nets, cpuRapl, cpuPower, cpuSampler}
    function parseScan(t) {
        const temps = [], gpus = [], nets = []
        let cpuRapl = { path: "", max: 0 }, cpuPower = "", cpuSampler = ""
        for (const l of String(t || "").split("\n")) {
            const f = l.split("\t")
            if (f[0] === "temp" && f.length >= 5) {
                const chip = f[1], driver = f[2]
                const kind = cpuChips.indexOf(chip) >= 0 ? "cpu" : gpuChips.indexOf(chip) >= 0 ? "gpu" : ""
                if (!kind || passthrough.indexOf(driver) >= 0) continue
                temps.push({ chip: chip, kind: kind, path: f[3], label: f[4] || chip })
            } else if (f[0] === "gpu" && f.length >= 7) {
                if (passthrough.indexOf(f[2]) >= 0 || f[4] !== "1") continue
                gpus.push({ card: f[1], driver: f[2], path: f[3], connected: f[5] === "1", bootVga: f[6] === "1", powerPath: f[7] || "" })
            } else if (f[0] === "cpusampler" && f.length >= 2 && f[1] && !cpuSampler) {
                cpuSampler = f[1]
            } else if (f[0] === "cpurapl" && f.length >= 3 && f[1] && !cpuRapl.path) {
                cpuRapl = { path: f[1], max: Number(f[2]) || 0 }
            } else if (f[0] === "cpupwr" && f.length >= 3 && f[2] && !cpuPower) {
                if (cpuChips.indexOf(f[1]) >= 0) cpuPower = f[2]
            } else if (f[0] === "net" && f.length >= 2 && f[1]) {
                nets.push(f[1])
            }
        }
        // the primary GPU drives a connected display, else it is the boot VGA device, else the first
        gpus.sort((a, b) => (b.connected - a.connected) || (b.bootVga - a.bootVga) || a.card.localeCompare(b.card))
        return ({ temps: temps, gpus: gpus, nets: nets, cpuRapl: cpuRapl, cpuPower: cpuPower, cpuSampler: cpuSampler })
    }

    // ---- sampling ----------------------------------------------------------------------------------------------------
    property var prevStat: null
    property var prevRapl: null
    property var prevNet: null
    property string cpuRaplPath: ""
    property real cpuRaplMax: 0
    property string cpuPowerPath: ""
    property bool cpuPowerUw: false // cpuPowerPath is the sampler's file: always integer microwatts
    property var tempSources: []    // [{chip, kind, path, label}]
    property var gpuSources: []     // [{card, driver, path, connected, bootVga, powerPath}]
    property var gpuPowerSources: [] // subset of gpuSources with non-empty powerPath
    property var netNames: []       // physical interfaces (discovery); [] until the first scan
    property bool scanned: false
    property var tempValues: ({})   // path -> degrees
    property var gpuValues: ({})    // card -> smoothed %
    property var gpuPowerValues: ({}) // card -> smoothed W
    property var tempFails: ({})    // path -> consecutive failed reads
    property var gpuFails: ({})     // card -> consecutive failed reads
    property var gpuPowerFails: ({}) // card -> consecutive failed power reads

    function takeStat(t) {
        const s = parseStat(t)
        if (!s) { cpu = NaN; prevStat = null; return }
        cores = s.cores
        if (prevStat && s.total > prevStat.total) {
            const dt = s.total - prevStat.total
            const di = Math.max(0, s.idle - prevStat.idle)
            cpu = Math.max(0, Math.min(100, 100 * (dt - di) / dt))
        }
        prevStat = s
        samples++
    }
    function takeRapl(t, now) {
        const e = parseEnergyUj(t)
        if (isNaN(e)) { prevRapl = null; if (!cpuPowerPath) cpuWatts = NaN; return }
        if (prevRapl && now > prevRapl.t) {
            const dt = (now - prevRapl.t) / 1000
            const de = e >= prevRapl.e ? e - prevRapl.e : (cpuRaplMax > prevRapl.e ? cpuRaplMax - prevRapl.e + e : 0)
            const w = (de / 1e6) / dt
            if (w >= 0 && w < 2000)
                cpuWatts = isNaN(cpuWatts) ? w : cpuWatts + gpuSmoothing * (w - cpuWatts)
        }
        prevRapl = { e: e, t: now }
    }
    function takeCpuPower(t) {
        const s = String(t || "").trim()
        const w = !cpuPowerUw ? parsePowerUw(t) : (/^\d+$/.test(s) && Number(s) > 0 && Number(s) < 2e9 ? Number(s) / 1e6 : NaN)
        if (isNaN(w)) { if (!cpuRaplPath) cpuWatts = NaN; return }
        cpuWatts = isNaN(cpuWatts) ? w : cpuWatts + gpuSmoothing * (w - cpuWatts)
    }
    function takeMem(t) {
        const m = parseMeminfo(t)
        if (!m) { ram = NaN; return }
        ramTotalKiB = m.total
        ramUsedKiB = m.total - m.available
        swapTotalKiB = m.swapTotal
        swapUsedKiB = m.swapTotal - m.swapFree
        ram = 100 * ramUsedKiB / m.total
    }
    // Watched interfaces: [desktop.topbar.sysmon].net_interfaces, else every physical one (discovered), else
    // (before the first scan, or without a /sys) everything but loopback.
    function watchedIfaces(all) {
        if (netInterfaces.length) return netInterfaces.filter(n => all[n] !== undefined)
        if (scanned) return netNames.filter(n => all[n] !== undefined)
        return Object.keys(all).filter(n => n !== "lo")
    }
    function takeNet(t, now) {
        const n = parseNetDev(t)
        if (!n) { netDown = NaN; netUp = NaN; nets = []; prevNet = null; return }
        const names = watchedIfaces(n)
        if (prevNet && now > prevNet.t) {
            const dt = (now - prevNet.t) / 1000
            let down = 0, up = 0
            const rows = []
            for (const name of names) {
                const a = n[name], b = prevNet.ifaces[name]
                if (!a || !b) continue
                const d = Math.max(0, a.rx - b.rx) / dt, u = Math.max(0, a.tx - b.tx) / dt   // counter reset -> 0
                down += d; up += u
                rows.push({ name: name, down: d, up: u })
            }
            nets = rows
            netDown = names.length ? down : NaN
            netUp = names.length ? up : NaN
        }
        prevNet = { t: now, ifaces: n }
    }
    // Per-file readings land asynchronously; they are kept in plain maps (no bindings) and published once per tick.
    function takeGpu(card, t) {
        const v = parsePercent(t)
        if (isNaN(v)) {
            gpuFails[card] = (gpuFails[card] || 0) + 1
            if (gpuValues[card] === undefined || gpuFails[card] >= 3) delete gpuValues[card]
        } else {
            gpuFails[card] = 0
            gpuValues[card] = gpuValues[card] === undefined ? v : gpuValues[card] + gpuSmoothing * (v - gpuValues[card])
        }
        publishSoon.restart()
    }
    function failGpu(card) { takeGpu(card, "") }
    function takeGpuPower(card, t) {
        const w = parsePowerUw(t)
        if (isNaN(w)) {
            gpuPowerFails[card] = (gpuPowerFails[card] || 0) + 1
            if (gpuPowerValues[card] === undefined || gpuPowerFails[card] >= 3) delete gpuPowerValues[card]
        } else {
            gpuPowerFails[card] = 0
            gpuPowerValues[card] = gpuPowerValues[card] === undefined ? w : gpuPowerValues[card] + gpuSmoothing * (w - gpuPowerValues[card])
        }
        publishSoon.restart()
    }
    function publishGpu() {
        const rows = []
        for (const g of gpuSources) {
            const v = gpuValues[g.card]
            const w = gpuPowerValues[g.card]
            if (v !== undefined) rows.push({ card: g.card, driver: g.driver, busy: v, watts: w !== undefined ? w : null, primary: rows.length === 0 })
        }
        gpus = rows
        gpu = rows.length ? rows[0].busy : NaN
        gpuWatts = rows.length && rows[0].watts !== null ? rows[0].watts : NaN
    }
    function takeTemp(path, t) {
        const c = parseMilliC(t)
        if (isNaN(c)) {
            tempFails[path] = (tempFails[path] || 0) + 1
            if (tempValues[path] === undefined || tempFails[path] >= 3) delete tempValues[path]
        } else {
            tempFails[path] = 0
            tempValues[path] = c
        }
        publishSoon.restart()
    }
    property Timer publishSoon: Timer {
        interval: 40
        onTriggered: { sm.publishTemps(); sm.publishGpu() }
    }
    function publishTemps() {
        const rows = []
        let hot = NaN
        for (const s of tempSources) {
            const c = tempValues[s.path]
            if (c === undefined) continue
            rows.push({ chip: s.chip, kind: s.kind, label: s.label, c: c })
            if (isNaN(hot) || c > hot) hot = c
        }
        temps = rows
        temp = hot
    }

    function sample() {
        if (!active) return
        if (wants("cpu")) {
            statFile.reload()
            if (cpuRaplPath) raplFile.reload()
            else if (cpuPowerPath) cpuPowerFile.reload()
        }
        if (wants("ram")) memFile.reload()
        if (wants("net")) netFile.reload()
        if (wants("gpu")) {
            for (let i = 0; i < gpuFiles.count; i++) { const f = gpuFiles.objectAt(i); if (f) f.reload() }
            for (let i = 0; i < gpuPowerFiles.count; i++) { const f = gpuPowerFiles.objectAt(i); if (f) f.reload() }
        }
        if (wants("temp")) for (let i = 0; i < tempFiles.count; i++) { const f = tempFiles.objectAt(i); if (f) f.reload() }
    }
    property Timer tick: Timer {
        interval: sm.intervalMs
        repeat: true
        running: sm.active
        triggeredOnStart: true
        onTriggered: sm.sample()
    }
    onActiveChanged: {
        if (active) { Qt.callLater(rescan); return }
        cpu = NaN; cpuWatts = NaN; ram = NaN; gpu = NaN; gpuWatts = NaN; temp = NaN; netDown = NaN; netUp = NaN; prevStat = null; prevRapl = null; prevNet = null
    }

    property FileView statFile: FileView {
        path: sm.active && sm.wants("cpu") ? sm.root + "/proc/stat" : ""
        preload: true
        printErrors: false
        onLoaded: sm.takeStat(text())
        onLoadFailed: sm.takeStat("")
    }
    property FileView raplFile: FileView {
        path: sm.active && sm.wants("cpu") && sm.cpuRaplPath !== "" ? sm.cpuRaplPath : ""
        preload: true
        printErrors: false
        onLoaded: sm.takeRapl(text(), Date.now())
        onLoadFailed: sm.takeRapl("", Date.now())
    }
    property FileView cpuPowerFile: FileView {
        path: sm.active && sm.wants("cpu") && sm.cpuRaplPath === "" && sm.cpuPowerPath !== "" ? sm.cpuPowerPath : ""
        preload: true
        printErrors: false
        onLoaded: sm.takeCpuPower(text())
        onLoadFailed: sm.takeCpuPower("")
    }
    property FileView memFile: FileView {
        path: sm.active && sm.wants("ram") ? sm.root + "/proc/meminfo" : ""
        preload: true
        printErrors: false
        onLoaded: sm.takeMem(text())
        onLoadFailed: sm.takeMem("")
    }
    property FileView netFile: FileView {
        path: sm.active && sm.wants("net") ? sm.root + "/proc/net/dev" : ""
        preload: true
        printErrors: false
        onLoaded: sm.takeNet(text(), Date.now())
        onLoadFailed: sm.takeNet("", Date.now())
    }
    property Instantiator gpuFiles: Instantiator {
        model: sm.active && sm.wants("gpu") ? sm.gpuSources : []
        delegate: FileView {
            required property var modelData
            path: modelData.path
            preload: true
            printErrors: false
            onLoaded: sm.takeGpu(modelData.card, text())
            onLoadFailed: sm.failGpu(modelData.card)
        }
        onObjectRemoved: (index, object) => sm.publishSoon.restart()
    }
    property Instantiator gpuPowerFiles: Instantiator {
        model: sm.active && sm.wants("gpu") ? sm.gpuPowerSources : []
        delegate: FileView {
            required property var modelData
            path: modelData.powerPath
            preload: true
            printErrors: false
            onLoaded: sm.takeGpuPower(modelData.card, text())
            onLoadFailed: sm.takeGpuPower(modelData.card, "")
        }
        onObjectRemoved: (index, object) => sm.publishSoon.restart()
    }
    property Instantiator tempFiles: Instantiator {
        model: sm.active && sm.wants("temp") ? sm.tempSources : []
        delegate: FileView {
            required property var modelData
            path: modelData.path
            preload: true
            printErrors: false
            onLoaded: sm.takeTemp(modelData.path, text())
            onLoadFailed: sm.takeTemp(modelData.path, "")
        }
        onObjectRemoved: (index, object) => sm.publishSoon.restart()
    }

    // ---- discovery: which sensors, GPUs and interfaces exist (one short scan, never per sample) ------------------------
    // Builtins only per file (read), a readlink per device for its driver; globs, never a recursive walk.
    property Process scan: Process {
        command: ["sh", "-c",
            "r=$1\n" +
            "rd() { v=; IFS= read -r v 2>/dev/null < \"$1\"; [ -n \"$v\" ]; }\n" +
            "drv() { d=; [ -e \"$1/driver\" ] && d=$(readlink -f \"$1/driver\") && d=${d##*/}; }\n" +
            "apu() { set -- $(od -An -tu1 -j2 -N1 \"$1/gpu_metrics\" 2>/dev/null); [ \"${1:-0}\" = 2 ] || [ \"${1:-0}\" = 3 ]; }\n" +
            // CPU package power: the root sampler's coarse value first (mios-power-sampler: energy_uj stays root-only,
            // the PLATYPUS mitigation); a RAPL zone readable by us only when run as root or on a test fixture root.
            "s=\"$r/run/mios/power/cpu_package_uw\"\n" +
            "if [ -r \"$s\" ]; then printf 'cpusampler\\t%s\\n' \"$s\"; else\n" +
            "for p in \"$r\"/sys/class/powercap/intel-rapl:*; do\n" +
            "  [ -r \"$p/energy_uj\" ] || continue; rd \"$p/name\" || continue\n" +
            "  case $v in package*) m=0; rd \"$p/max_energy_range_uj\" && m=$v; printf 'cpurapl\\t%s\\t%s\\n' \"$p/energy_uj\" \"$m\"; break ;; esac\n" +
            "done\n" +
            "fi\n" +
            "for h in \"$r\"/sys/class/hwmon/hwmon*; do\n" +
            "  rd \"$h/name\" || continue; n=$v; drv \"$h/device\"\n" +
            "  for p in \"$h\"/power*_average \"$h\"/power*_input; do\n" +
            "    [ -r \"$p\" ] && printf 'cpupwr\\t%s\\t%s\\n' \"$n\" \"$p\" && break\n" +
            "  done\n" +
            "  for t in \"$h\"/temp*_input; do\n" +
            "    [ -r \"$t\" ] || continue\n" +
            "    l=${t##*/}; l=${l%_input}; rd \"${t%_input}_label\" && l=$v\n" +
            "    printf 'temp\\t%s\\t%s\\t%s\\t%s\\n' \"$n\" \"$d\" \"$t\" \"$l\"\n" +
            "  done\n" +
            "done\n" +
            "for c in \"$r\"/sys/class/drm/card*; do\n" +
            "  k=${c##*/}; case $k in *-*) continue ;; esac\n" +
            "  [ -d \"$c/device\" ] || continue; drv \"$c/device\"\n" +
            "  b=$c/device/gpu_busy_percent; ok=0; [ -r \"$b\" ] && ok=1\n" +
            "  con=0; for s in \"$c\"-*/status; do rd \"$s\" && [ \"$v\" = connected ] && con=1; done\n" +
            "  vga=0; rd \"$c/device/boot_vga\" && vga=$v\n" +
            "  p=; for hp in \"$c\"/device/hwmon/hwmon*/power1_average \"$c\"/device/hwmon/hwmon*/power1_input; do [ -r \"$hp\" ] && p=$hp && break; done\n" +
            // an APU's amdgpu power sensor (gpu_metrics table v2/v3; a dGPU's is v1) is the whole socket's, CPU included
            "  [ -n \"$p\" ] && apu \"$c/device\" && p=\n" +
            "  printf 'gpu\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\n' \"$k\" \"$d\" \"$b\" \"$ok\" \"$con\" \"$vga\" \"$p\"\n" +
            "done\n" +
            "for i in \"$r\"/sys/class/net/*; do [ -e \"$i/device\" ] && printf 'net\\t%s\\n' \"${i##*/}\"; done\n" +
            "exit 0\n",
            "sh", sm.root]
        stdout: StdioCollector {
            onStreamFinished: {
                const s = sm.parseScan(text)
                const nextTemps = {}, nextTempFails = {}
                for (const t of s.temps) {
                    if (sm.tempValues[t.path] !== undefined) nextTemps[t.path] = sm.tempValues[t.path]
                    if (sm.tempFails[t.path] !== undefined) nextTempFails[t.path] = sm.tempFails[t.path]
                }
                const nextGpus = {}, nextGpuFails = {}, nextGpuPower = {}, nextGpuPowerFails = {}
                for (const g of s.gpus) {
                    if (sm.gpuValues[g.card] !== undefined) nextGpus[g.card] = sm.gpuValues[g.card]
                    if (sm.gpuFails[g.card] !== undefined) nextGpuFails[g.card] = sm.gpuFails[g.card]
                    if (sm.gpuPowerValues[g.card] !== undefined) nextGpuPower[g.card] = sm.gpuPowerValues[g.card]
                    if (sm.gpuPowerFails[g.card] !== undefined) nextGpuPowerFails[g.card] = sm.gpuPowerFails[g.card]
                }
                sm.tempValues = nextTemps
                sm.tempFails = nextTempFails
                sm.gpuValues = nextGpus
                sm.gpuFails = nextGpuFails
                sm.gpuPowerValues = nextGpuPower
                sm.gpuPowerFails = nextGpuPowerFails
                if (sm.cpuRaplPath !== s.cpuRapl.path) { sm.prevRapl = null; sm.cpuRaplPath = s.cpuRapl.path }
                sm.cpuRaplMax = s.cpuRapl.max
                sm.cpuPowerPath = s.cpuSampler || s.cpuPower
                sm.cpuPowerUw = s.cpuSampler !== ""
                if (JSON.stringify(sm.tempSources) !== JSON.stringify(s.temps)) sm.tempSources = s.temps
                if (JSON.stringify(sm.gpuSources) !== JSON.stringify(s.gpus)) {
                    sm.gpuSources = s.gpus
                    sm.gpuPowerSources = s.gpus.filter(g => g.powerPath !== "")
                }
                sm.netNames = s.nets
                sm.scanned = true
                sm.publishSoon.restart()
            }
        }
    }
    function rescan() { if (active && (wants("cpu") || wants("temp") || wants("gpu") || wants("net")) && !scan.running) scan.running = true }
    property Timer rescanTimer: Timer {
        interval: sm.rediscoverMs
        repeat: true
        running: sm.active
        onTriggered: sm.rescan()
    }

    // ---- configuration loading -------------------------------------------------------------------------------------
    property Process reader: Process {
        command: [sm.resolver, "--section", "desktop.topbar.sysmon"]
        stdout: StdioCollector {
            onStreamFinished: {
                let next = ({})
                try { next = JSON.parse(text) || ({}) } catch (e) { next = ({}) }
                const same = JSON.stringify(next) === JSON.stringify(sm.cfg)
                sm.cfg = (typeof next === "object" && !Array.isArray(next)) ? next : ({})
                sm.cfgLoaded = true
                if (!same) Qt.callLater(sm.rescan)
            }
        }
        onExited: (code, status) => { if (code !== 0 && !sm.cfgLoaded) sm.cfgLoaded = true }
    }
    function reload() { if (!reader.running) reader.running = true }
    Component.onCompleted: reload()
    // no resolver (or it hangs): run on the defaults rather than never
    property Timer cfgFallback: Timer {
        interval: 3000
        running: !sm.cfgLoaded
        onTriggered: sm.cfgLoaded = true
    }
    // An SSOT change regenerates the theme bridge: reread the table then (Glass.qml does the same).
    property Theme theme: Theme {}
    property Connections bridgeWatch: Connections {
        target: sm.theme.bridge
        function onFileChanged() { sm.reload() }
    }

    property IpcHandler ipc: IpcHandler {
        target: "sysmon"
        function line(): string { return sm.statusLine }
        function state(): string {
            return JSON.stringify({ enabled: sm.enabled, active: sm.active, interval_ms: sm.intervalMs, items: sm.items,
                                    mobile_items: sm.mobileItems, samples: sm.samples, scanned: sm.scanned,
                                    cpu: sm.cpuOk ? sm.cpu : null, cpu_watts: sm.cpuWattsOk ? sm.cpuWatts : null, cores: sm.cores,
                                    ram: sm.ramOk ? sm.ram : null, ram_used_kib: sm.ramUsedKiB, ram_total_kib: sm.ramTotalKiB,
                                    swap_used_kib: sm.swapUsedKiB, swap_total_kib: sm.swapTotalKiB,
                                    gpu: sm.gpuOk ? sm.gpu : null, gpu_watts: sm.gpuWattsOk ? sm.gpuWatts : null, gpus: sm.gpus,
                                    temp: sm.tempOk ? sm.temp : null, temps: sm.temps,
                                    net_down: sm.netOk ? sm.netDown : null, net_up: sm.netOk ? sm.netUp : null, nets: sm.nets,
                                    status_line: sm.statusLine, compact_line: sm.compactLine,
                                    sources: { temps: sm.tempSources.map(s => s.path), gpus: sm.gpuSources.map(g => g.card + ":" + g.driver),
                                               nets: sm.netNames, cpu_power: sm.cpuRaplPath || sm.cpuPowerPath || null },
                                    text: { cpu: sm.fmtPct(sm.cpu), cpu_watts: sm.fmtWatts(sm.cpuWatts), ram: sm.fmtPct(sm.ram),
                                            gpu: sm.fmtPct(sm.gpu), gpu_watts: sm.fmtWatts(sm.gpuWatts),
                                            temp: sm.fmtTemp(sm.temp), down: sm.fmtRate(sm.netDown), up: sm.fmtRate(sm.netUp) } })
        }
        function reload(): void { sm.reload(); sm.rescan() }
    }
}
