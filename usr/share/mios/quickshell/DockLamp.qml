// AI-hint: Magic-lamp dock bridge (lamp-contract.md): publishes per-output dock geometry, icon rects and minimized
// window rects to $XDG_RUNTIME_DIR/mios-dock-icons.json (atomic temp+rename, throttled <= 30 writes/s), records pending
// dock launches to $XDG_RUNTIME_DIR/mios-lamp-launch.json (10 s prune, atomic temp+rename) before spawning so the
// mios-wobbly lamp plugin animates new windows out of their dock icon, tracks windows on special:minimized, and routes
// dock minimize/restore actions through the feature-detected hl.plugin.mios_lamp dispatchers with plain workspace-move
// fallback when the plugin is not loaded.
// AI-related: usr/share/mios/quickshell/Sidebar.qml, usr/share/mios/hyprland/plugins/mios-wobbly/lamp.cpp,
// usr/share/mios/hyprland/plugins/mios-wobbly/lampmath.hpp, usr/share/mios/mios.toml [desktop.hyprland.lamp],
// /home/mios/mios-desktop-work-1/lamp-contract.md, tests/test-mios-lamp.py
pragma Singleton
import QtQuick
import Quickshell
import Quickshell.Hyprland
import Quickshell.Io

Singleton {
    id: lamp

    readonly property string runtimeDir: Quickshell.env("XDG_RUNTIME_DIR") || "/run/user/1000"
    readonly property string dockFile: Quickshell.env("MIOS_DOCK_ICONS_JSON") || (runtimeDir + "/mios-dock-icons.json")
    readonly property string launchFile: Quickshell.env("MIOS_LAMP_LAUNCH_JSON") || (runtimeDir + "/mios-lamp-launch.json")
    readonly property string minimizedWorkspace: Quickshell.env("MIOS_MINIMIZED_WORKSPACE") || "special:minimized"
    readonly property string prefsFile: Quickshell.env("MIOS_DOCK_PREFS_JSON")
        || ((Quickshell.env("XDG_CONFIG_HOME") || ((Quickshell.env("HOME") || "/home/mios") + "/.config")) + "/mios/dock-prefs.json")

    // ---- Pin / Star / Favourite & Reorganize persistence -----------------------------------------------------------
    property var customPinned: null
    property var starredApps: []
    property bool reorderMode: false
    property int prefsWriteCount: 0
    property string queuedPrefsJson: ""

    property FileView prefsView: FileView {
        path: lamp.prefsFile
        watchChanges: false
        preload: true
        printErrors: false
        onLoaded: lamp.parsePrefs(text())
        onLoadFailed: {
            if (lamp.customPinned === null) lamp.customPinned = null
        }
    }

    function canonAppId(entryOrId) {
        if (!entryOrId) return ""
        if (typeof entryOrId === "string") return stripDesktop(entryOrId.trim())
        return stripDesktop(String(entryOrId.id || entryOrId.appId || entryOrId.startupClass || "").trim())
    }

    function parsePrefs(raw) {
        if (prefsWriter.running || lamp.queuedPrefsJson !== "") return
        try {
            const j = JSON.parse(String(raw || "").trim() || "{}")
            if (j && typeof j === "object") {
                if (Array.isArray(j.pinned)) {
                    const p = []
                    for (const id of j.pinned) {
                        const c = canonAppId(id)
                        if (c && p.indexOf(c) < 0) p.push(c)
                    }
                    lamp.customPinned = p
                }
                if (Array.isArray(j.starred)) {
                    const s = []
                    for (const id of j.starred) {
                        const c = canonAppId(id)
                        if (c && s.indexOf(c) < 0) s.push(c)
                    }
                    lamp.starredApps = s
                }
            }
        } catch (e) {
            /* keep current state on malformed JSON */
        }
    }

    function effectivePinned(defaultPinned) {
        if (Array.isArray(lamp.customPinned)) return lamp.customPinned
        const out = []
        const src = Array.isArray(defaultPinned) ? defaultPinned : []
        for (const id of src) {
            const c = canonAppId(id)
            if (c && out.indexOf(c) < 0) out.push(c)
        }
        return out
    }

    function isPinned(entryOrId, defaultPinned) {
        const c = canonAppId(entryOrId)
        if (!c) return false
        const list = effectivePinned(defaultPinned)
        return list.some(id => id.toLowerCase() === c.toLowerCase())
    }

    function isStarred(entryOrId) {
        const c = canonAppId(entryOrId)
        if (!c) return false
        return lamp.starredApps.some(id => id.toLowerCase() === c.toLowerCase())
    }

    function savePrefs() {
        const payload = JSON.stringify({
            version: 1,
            updated_ms: Date.now(),
            pinned: Array.isArray(lamp.customPinned) ? lamp.customPinned : [],
            starred: Array.isArray(lamp.starredApps) ? lamp.starredApps : []
        })
        if (!prefsWriter.running) {
            prefsWriter.payload = payload
            prefsWriter.running = true
        } else {
            queuedPrefsJson = payload
        }
    }

    property Process prefsWriter: Process {
        property string payload: ""
        command: ["sh", "-c",
            "f=$1; j=$2; d=${f%/*}; [ -n \"$d\" ] || d=.; mkdir -p \"$d\" 2>/dev/null || true; " +
            "t=\"$d/.${f##*/}.tmp.$$\"; printf '%s\\n' \"$j\" > \"$t\" && mv -f \"$t\" \"$f\"",
            "sh", lamp.prefsFile, prefsWriter.payload]
        onExited: (code, status) => {
            if (code === 0) lamp.prefsWriteCount++
            if (lamp.queuedPrefsJson) {
                prefsWriter.payload = lamp.queuedPrefsJson
                lamp.queuedPrefsJson = ""
                prefsWriter.running = true
            }
        }
    }

    function pinApp(entryOrId, defaultPinned) {
        const c = canonAppId(entryOrId)
        if (!c) return false
        const list = effectivePinned(defaultPinned).slice()
        if (!list.some(id => id.toLowerCase() === c.toLowerCase())) {
            list.push(c)
            lamp.customPinned = list
            savePrefs()
            return true
        }
        return false
    }

    function unpinApp(entryOrId, defaultPinned) {
        const c = canonAppId(entryOrId)
        if (!c) return false
        const list = effectivePinned(defaultPinned).filter(id => id.toLowerCase() !== c.toLowerCase())
        lamp.customPinned = list
        savePrefs()
        return true
    }

    function togglePin(entryOrId, defaultPinned) {
        if (isPinned(entryOrId, defaultPinned)) {
            unpinApp(entryOrId, defaultPinned)
            return false
        }
        pinApp(entryOrId, defaultPinned)
        return true
    }

    function movePin(entryOrId, delta, defaultPinned) {
        const c = canonAppId(entryOrId)
        if (!c || !delta) return false
        const list = effectivePinned(defaultPinned).slice()
        const idx = list.findIndex(id => id.toLowerCase() === c.toLowerCase())
        if (idx < 0) return false
        const target = Math.max(0, Math.min(list.length - 1, idx + delta))
        if (target === idx) return false
        const item = list.splice(idx, 1)[0]
        list.splice(target, 0, item)
        lamp.customPinned = list
        savePrefs()
        return true
    }

    function reorderPinTo(fromIndex, toIndex, defaultPinned) {
        const list = effectivePinned(defaultPinned).slice()
        if (fromIndex < 0 || fromIndex >= list.length || toIndex < 0 || toIndex >= list.length || fromIndex === toIndex) return false
        const item = list.splice(fromIndex, 1)[0]
        list.splice(toIndex, 0, item)
        lamp.customPinned = list
        savePrefs()
        return true
    }

    function starApp(entryOrId) {
        const c = canonAppId(entryOrId)
        if (!c) return false
        const list = (lamp.starredApps || []).slice()
        if (!list.some(id => id.toLowerCase() === c.toLowerCase())) {
            list.push(c)
            lamp.starredApps = list
            savePrefs()
            return true
        }
        return false
    }

    function unstarApp(entryOrId) {
        const c = canonAppId(entryOrId)
        if (!c) return false
        const list = (lamp.starredApps || []).filter(id => id.toLowerCase() !== c.toLowerCase())
        lamp.starredApps = list
        savePrefs()
        return true
    }

    function toggleStar(entryOrId) {
        if (isStarred(entryOrId)) {
            unstarApp(entryOrId)
            return false
        }
        starApp(entryOrId)
        return true
    }

    function moveStar(entryOrId, delta) {
        const c = canonAppId(entryOrId)
        if (!c || !delta) return false
        const list = (lamp.starredApps || []).slice()
        const idx = list.findIndex(id => id.toLowerCase() === c.toLowerCase())
        if (idx < 0) return false
        const target = Math.max(0, Math.min(list.length - 1, idx + delta))
        if (target === idx) return false
        const item = list.splice(idx, 1)[0]
        list.splice(target, 0, item)
        lamp.starredApps = list
        savePrefs()
        return true
    }

    function reorderStarTo(fromIndex, toIndex) {
        const list = (lamp.starredApps || []).slice()
        if (fromIndex < 0 || fromIndex >= list.length || toIndex < 0 || toIndex >= list.length || fromIndex === toIndex) return false
        const item = list.splice(fromIndex, 1)[0]
        list.splice(toIndex, 0, item)
        lamp.starredApps = list
        savePrefs()
        return true
    }

    function closeWindow(address) {
        const a = normAddress(address)
        if (!a) return "error: invalid address"
        const expr = "hl.dsp.window.close({window = \"address:" + a + "\"})"
        lastDispatched = expr
        Quickshell.execDetached(["hyprctl", "dispatch", expr])
        refreshSoon()
        return expr
    }

    function toggleShaderGlass(address) {
        const a = normAddress(address)
        if (a) {
            Quickshell.execDetached(["/usr/bin/mios-shaderglass", "attach", "--window", a])
            return "attach:" + a
        }
        Quickshell.execDetached(["/usr/bin/mios-shaderglass", "toggle"])
        return "toggle"
    }

    // Per-output state reported by each Sidebar.qml instance: { "<output>": { visible, edge, dock_rect, edge_rect, minimized_slot, icons, minimized } }
    property var outputs: ({})
    // Pending launches younger than 10 s: [{ id, time_ms, app_id, desktop_id, classes, output, rect }]
    property var pendingLaunches: []
    // Recently restored windows retained for 800ms so followDock never clobbers restore target
    property var restoringWindows: ({})
    property int writeCount: 0
    property int launchWriteCount: 0
    property int launchSeq: 0
    property string lastDispatched: ""

    function pruneRestoring() {
        const now = Date.now()
        let changed = false
        const next = Object.assign({}, restoringWindows)
        for (const k in next) {
            if (next[k] && typeof next[k].time_ms === "number" && now - next[k].time_ms >= 800) {
                delete next[k]
                changed = true
            }
        }
        if (changed) {
            restoringWindows = next
            refreshSoon()
        }
    }

    property Timer pruneRestoringTimer: Timer {
        interval: 100
        repeat: true
        running: Object.keys(lamp.restoringWindows).length > 0
        onTriggered: lamp.pruneRestoring()
    }

    // Optional fixture clients for window-less tests (MIOS_DOCK_FIXTURE_JSON).
    property var fixtureClients: []
    property FileView fixtureView: FileView {
        path: Quickshell.env("MIOS_DOCK_FIXTURE_JSON") || ""
        watchChanges: path !== ""
        preload: path !== ""
        printErrors: false
        onFileChanged: reload()
        onLoaded: lamp.parseFixture(text())
        onLoadFailed: lamp.fixtureClients = []
    }
    function parseFixture(t) {
        try {
            const j = JSON.parse(t) || ({})
            lamp.fixtureClients = Array.isArray(j.clients) ? j.clients : []
        } catch (e) {
            lamp.fixtureClients = []
        }
    }

    // ---- address & class helpers -----------------------------------------------------------------------------------
    function validAddr(a) {
        if (a === undefined || a === null) return false
        const s = String(a)
        if (!s || s !== s.trim()) return false
        const full = s.startsWith("0x") ? s : "0x" + s
        return /^0x[0-9a-fA-F]+$/.test(full) && !/[\r\n]/.test(full)
    }
    function normAddress(a) {
        if (!a) return ""
        const s = String(a).trim()
        if (!s) return ""
        const full = s.startsWith("0x") ? s : "0x" + s
        return (/^0x[0-9a-fA-F]+$/.test(full) && !/[\r\n]/.test(full)) ? full.toLowerCase() : ""
    }
    function addressOf(tl) {
        if (!tl) return ""
        if (tl.lastIpcObject && tl.lastIpcObject.address) return normAddress(tl.lastIpcObject.address)
        if (tl.address) return normAddress(tl.address)
        return ""
    }
    function windowInfo(address) {
        const a = normAddress(address)
        if (!a) return null
        for (const tl of Hyprland.toplevels.values) {
            if (addressOf(tl) === a) return tl.lastIpcObject || null
        }
        for (const c of lamp.fixtureClients) if (normAddress(c.address) === a) return c
        return null
    }
    function stripDesktop(id) {
        const s = String(id || "")
        return s.endsWith(".desktop") ? s.slice(0, -8) : s
    }
    function classesForEntry(entry, extraClasses) {
        const out = []
        function add(v) {
            if (!v || typeof v !== "string") return
            const s = v.trim()
            if (!s) return
            if (out.indexOf(s) < 0) out.push(s)
            const lo = s.toLowerCase()
            if (out.indexOf(lo) < 0) out.push(lo)
        }
        if (entry) {
            const base = stripDesktop(entry.id || entry.appId || "")
            add(base)
            add(entry.appId || "")
            add(entry.startupClass || "")
            add(entry.icon || "")
            const dot = base.lastIndexOf(".")
            if (dot >= 0 && dot + 1 < base.length) add(base.slice(dot + 1))
        }
        if (Array.isArray(extraClasses)) {
            for (const c of extraClasses) add(c)
        }
        return out
    }

    // ---- minimized windows (special:minimized) ---------------------------------------------------------------------
    // Origin output of a minimized window, authoritative: Hyprland keeps ONE monitor per workspace, so every window on
    // special:minimized reports the monitor of whichever window was minimized last (or re-homed after an unplug); the
    // lamp plugin records each window's own origin (`hyprctl mios-lamp status` -> minimized[].monitor). The QML
    // caches below only cover a plugin that is not loaded, and they are lost when the shell restarts.
    property var pluginOrigin: ({})
    function parseLampStatus(t) {
        try {
            const j = JSON.parse(t)
            const m = {}
            if (j && Array.isArray(j.minimized)) {
                for (const e of j.minimized) {
                    const a = normAddress(e && e.address)
                    if (a && e.monitor) m[a] = String(e.monitor)
                }
            }
            lamp.pluginOrigin = m
        } catch (e) {
            // no plugin / not JSON (plain hyprctl error text): keep the caches as the only source
            lamp.pluginOrigin = ({})
        }
    }
    property Process lampStatus: Process {
        command: ["hyprctl", "mios-lamp", "status"]
        stdout: StdioCollector { onStreamFinished: lamp.parseLampStatus(text) }
    }
    function refreshLampStatus() {
        if (!lampStatus.running) lampStatus.running = true
    }
    property var originOutput: ({})
    property var originMonitorId: ({})
    function isLiveMonitorName(name) {
        if (!name) return false
        if (lamp.fixtureView.path !== "") return true
        const mons = Hyprland.monitors ? Hyprland.monitors.values : []
        if (!mons || mons.length === 0) return true
        for (let j = 0; j < mons.length; j++) {
            const m = mons[j]
            if (m && String(m.name) === name) return true
        }
        return false
    }
    function monitorIdForName(name) {
        if (!name) return -1
        const mons = Hyprland.monitors ? Hyprland.monitors.values : []
        for (let j = 0; j < mons.length; j++) {
            const m = mons[j]
            if (m && String(m.name) === name) return m.id
        }
        return -1
    }
    function monitorNameForId(monId) {
        if (monId === undefined || monId === null || monId < 0) return ""
        const mons = Hyprland.monitors ? Hyprland.monitors.values : []
        for (let j = 0; j < mons.length; j++) {
            const m = mons[j]
            if (m && m.id === monId && m.name) return String(m.name)
        }
        return ""
    }
    readonly property var minimizedWindows: {
        const res = []
        const seen = {}
        const targetWs = lamp.minimizedWorkspace
        for (const c of lamp.fixtureClients) {
            if (!c || c.mapped === false) continue
            const wsName = c.workspace && typeof c.workspace.name === "string" ? c.workspace.name : String(c.workspace || "")
            if (!wsName.startsWith("special:")) {
                const addr = normAddress(c.address)
                const monName = String(c.monitorName || c.monitor || "")
                if (addr && monName) lamp.originOutput[addr] = monName
            }
            if (wsName !== targetWs) continue
            const addr = normAddress(c.address)
            if (!addr || seen[addr]) continue
            seen[addr] = true
            const plugMon = lamp.pluginOrigin[addr] || ""
            res.push({
                address: addr,
                appId: String(c.class || c.initialClass || c.appId || ""),
                initialClass: String(c.initialClass || c.class || ""),
                title: String(c.title || c.class || addr),
                monitor: c.monitor !== undefined ? c.monitor : "",
                monitorName: plugMon || String(c.monitorName || c.monitor || "")
            })
        }
        if (lamp.fixtureView.path !== "") {
            for (const k in lamp.restoringWindows) {
                const rw = lamp.restoringWindows[k]
                if (rw && !seen[rw.address]) {
                    seen[rw.address] = true
                    res.push({
                        address: rw.address,
                        appId: rw.appId || "",
                        initialClass: rw.initialClass || rw.appId || "",
                        title: rw.title || rw.address,
                        monitor: rw.monitor !== undefined ? rw.monitor : "",
                        monitorName: rw.monitorName || ""
                    })
                }
            }
            return res
        }
        const tls = Hyprland.toplevels ? Hyprland.toplevels.values : []
        for (let i = 0; i < tls.length; i++) {
            const tl = tls[i]
            const o = tl ? tl.lastIpcObject : null
            const wsName = (o && o.workspace && o.workspace.name) ? String(o.workspace.name)
                         : (tl && tl.workspace && tl.workspace.name ? String(tl.workspace.name) : "")
            const tlWsName = (tl && tl.workspace && tl.workspace.name) ? String(tl.workspace.name) : ""
            const isSpecial = wsName.startsWith("special:") || tlWsName.startsWith("special:")
            const oMonId = (o && o.monitor !== undefined) ? o.monitor : -1
            const monNameFromO = monitorNameForId(oMonId)
            if (!isSpecial) {
                const addr = addressOf(tl)
                const monName = (o && o.monitorName ? String(o.monitorName) : "")
                              || monNameFromO
                              || (tl.monitor && tl.monitor.name ? String(tl.monitor.name) : "")
                if (addr && monName) lamp.originOutput[addr] = monName
                if (addr && oMonId >= 0) lamp.originMonitorId[addr] = oMonId
            }
            if (!isSpecial) continue
            if (wsName !== targetWs && tlWsName !== targetWs) continue
            if (o && o.mapped === false) continue
            const addr = addressOf(tl)
            if (!addr || seen[addr]) continue
            seen[addr] = true
            const cls = (o && o.class) ? String(o.class) : (tl.wayland ? String(tl.wayland.appId || "") : "")
            const initCls = (o && o.initialClass) ? String(o.initialClass) : cls
            const title = (o && o.title) ? String(o.title) : String(tl.title || cls || addr)
            const savedOrig = lamp.originOutput[addr] || ""
            const validOrig = isLiveMonitorName(savedOrig) ? savedOrig : ""
            const savedMonId = lamp.originMonitorId[addr] !== undefined ? lamp.originMonitorId[addr] : -1
            const validSavedMonName = monitorNameForId(savedMonId)
            const plugMon = lamp.pluginOrigin[addr] || ""
            const validPlug = isLiveMonitorName(plugMon) ? plugMon : ""
            const plugMonId = validPlug ? monitorIdForName(validPlug) : -1
            const effMonId = plugMonId >= 0 ? plugMonId : (validSavedMonName ? savedMonId : oMonId)
            const monName = validPlug
                          || validOrig
                          || monNameFromO
                          || validSavedMonName
            res.push({
                address: addr,
                appId: cls,
                initialClass: initCls,
                title: title,
                monitor: effMonId,
                monitorName: monName
            })
        }
        for (const k in lamp.restoringWindows) {
            const rw = lamp.restoringWindows[k]
            if (rw && !seen[rw.address]) {
                seen[rw.address] = true
                res.push({
                    address: rw.address,
                    appId: rw.appId || "",
                    initialClass: rw.initialClass || rw.appId || "",
                    title: rw.title || rw.address,
                    monitor: rw.monitor !== undefined ? rw.monitor : -1,
                    monitorName: rw.monitorName || ""
                })
            }
        }
        return res
    }

    function isMinimizedAddress(addr) {
        const a = normAddress(addr)
        if (!a) return false
        for (const m of minimizedWindows) if (m.address === a) return true
        return false
    }

    function isSpecialAddress(addr) {
        const a = normAddress(addr)
        if (!a) return false
        if (isMinimizedAddress(a)) return true
        const tls = Hyprland.toplevels ? Hyprland.toplevels.values : []
        for (let i = 0; i < tls.length; i++) {
            const tl = tls[i]
            if (addressOf(tl) !== a) continue
            const o = tl ? tl.lastIpcObject : null
            const wsName = (o && o.workspace && o.workspace.name) ? String(o.workspace.name)
                         : (tl && tl.workspace && tl.workspace.name ? String(tl.workspace.name) : "")
            if (wsName.startsWith("special:")) return true
        }
        return false
    }

    function minimizedForOutput(outputName, monitorId, isPrimary) {
        const out = []
        for (const m of minimizedWindows) {
            const savedOrig = lamp.originOutput[m.address] || ""
            const orig = (savedOrig && isLiveMonitorName(savedOrig)) ? savedOrig : (isLiveMonitorName(m.monitorName) ? m.monitorName : "")
            if (!outputName) {
                out.push(m)
            } else if (orig === outputName) {
                out.push(m)
            } else if (!orig && (m.monitorName === outputName || (monitorId !== undefined && monitorId >= 0 && m.monitor === monitorId))) {
                out.push(m)
            } else if (!orig && isPrimary) {
                out.push(m)
            }
        }
        return out
    }

    // ---- feature-detected dispatchers (contract (c)) ---------------------------------------------------------------
    function minimizeExpr(address) {
        const a = normAddress(address)
        const ws = lamp.minimizedWorkspace
        if (a) {
            return "(hl.plugin.mios_lamp and hl.plugin.mios_lamp.minimize({window = \"address:" + a + "\"}) or " +
                   "hl.dsp.window.move({workspace = \"" + ws + "\", window = \"address:" + a + "\", follow = false}))"
        }
        return "(hl.plugin.mios_lamp and hl.plugin.mios_lamp.minimize() or " +
               "hl.dsp.window.move({workspace = \"" + ws + "\", follow = false}))"
    }

    function restoreExpr(address, source) {
        const a = normAddress(address)
        const src = (source === "icon" || source === "minimized") ? source : ""
        const srcArg = src ? (", source = \"" + src + "\"") : ""
        if (a) {
            return "(hl.plugin.mios_lamp and hl.plugin.mios_lamp.restore({window = \"address:" + a + "\"" + srcArg + "}) or " +
                   "hl.dsp.window.move({workspace = \"e+0\", window = \"address:" + a + "\"}))"
        }
        return "(hl.plugin.mios_lamp and hl.plugin.mios_lamp.restore(" + (src ? "{source = \"" + src + "\"}" : "") +
               ") or hl.dsp.window.move({workspace = \"e+0\"}))"
    }

    function minimize(address) {
        if (address !== undefined && address !== null && String(address) !== "") {
            if (!validAddr(address)) return "error: invalid address"
        }
        const expr = minimizeExpr(address)
        lastDispatched = expr
        Quickshell.execDetached(["hyprctl", "dispatch", expr])
        refreshSoon()
        refreshAfterLamp.restart()
        return expr
    }

    function recordRestore(address, source, targetRect) {
        const a = normAddress(address)
        if (!a) return
        pruneRestoring()
        let meta = null
        for (const m of minimizedWindows) {
            if (m.address === a) { meta = m; break }
        }
        const next = Object.assign({}, restoringWindows)
        next[a] = {
            address: a,
            appId: meta ? meta.appId : "",
            initialClass: meta ? meta.initialClass : "",
            title: meta ? meta.title : a,
            monitor: meta ? meta.monitor : -1,
            monitorName: meta ? meta.monitorName : "",
            rect: (Array.isArray(targetRect) && targetRect.length === 4) ? targetRect : [0, 0, 48, 48],
            source: (source === "icon" || source === "minimized") ? source : "minimized",
            time_ms: Date.now()
        }
        restoringWindows = next
    }

    function restore(address, source, targetRect) {
        if (address !== undefined && address !== null && String(address) !== "") {
            if (!validAddr(address)) return "error: invalid address"
        }
        const a = normAddress(address)
        const src = (source === "icon" || source === "minimized") ? source : ""
        recordRestore(a, src, targetRect)
        const expr = restoreExpr(a, src)
        lastDispatched = expr
        Quickshell.execDetached(["hyprctl", "dispatch", expr])
        refreshSoon()
        refreshAfterLamp.restart()
        return expr
    }

    // Collect a stored or running window into the workspace the operator is using now.
    function bringHere(address, source, targetRect) {
        if (!validAddr(address)) return "error: invalid address"
        const a = normAddress(address)
        recordRestore(a, source, targetRect)
        const commands = "dispatch hl.dsp.window.move({workspace = \"e+0\", window = \"address:" + a + "\", follow = false}); " +
                         "dispatch hl.dsp.focus({window = \"address:" + a + "\"})"
        lastDispatched = commands
        Quickshell.execDetached(["hyprctl", "--batch", commands])
        refreshSoon()
        refreshAfterLamp.restart()
        return commands
    }

    function rectForAddress(address, outputName) {
        const a = normAddress(address)
        if (!a) return [0, 0, 48, 48]
        const outs = lamp.outputs
        if (outputName && outs[outputName]) {
            const mins = outs[outputName].minimized || []
            for (let i = 0; i < mins.length; i++) {
                if (mins[i] && mins[i].address === a) return mins[i].rect
            }
            return outs[outputName].minimized_slot || outs[outputName].dock_rect || [0, 0, 48, 48]
        }
        for (const name in outs) {
            const mins = outs[name].minimized || []
            for (let i = 0; i < mins.length; i++) {
                if (mins[i] && mins[i].address === a) return mins[i].rect
            }
        }
        return [0, 0, 48, 48]
    }

    // ---- pure geometry calculator for an output's dock (used by Sidebar.qml and unit tests) ------------------------
    // params: { monX, monY, screenW, screenH, ui, edgeMargin, dockHeight, dockRevealPx, visible, edge,
    //           tiles: [{ kind: "drawer"|"icon"|"minimized", app_id, desktop_id, classes, address, scale }] }
    function computeOutputGeometry(p) {
        const ui = (p && p.ui > 0) ? Number(p.ui) : 1
        const monX = (p && isFinite(p.monX)) ? Number(p.monX) : 0
        const monY = (p && isFinite(p.monY)) ? Number(p.monY) : 0
        const screenLogicalW = (p && p.screenLogicalW > 0) ? Number(p.screenLogicalW) : 1920
        const screenLogicalH = (p && p.screenLogicalH > 0) ? Number(p.screenLogicalH) : 1080
        const edgeMargin = (p && p.edgeMargin > 0) ? Number(p.edgeMargin) : 8
        const dockHeight = (p && p.dockHeight > 0) ? Number(p.dockHeight) : 56
        const revealPx = (p && p.dockRevealPx > 0) ? Number(p.dockRevealPx) : 2
        const visible = !(p && p.visible === false)
        const edge = (p && typeof p.edge === "string" && p.edge) ? p.edge : "bottom"
        const list = (p && Array.isArray(p.tiles)) ? p.tiles : []

        let tileBaseW = dockHeight - edgeMargin
        let tileBaseH = dockHeight
        let spacing = edgeMargin
        const n = Math.max(1, list.length)
        let rowShellW = n * tileBaseW + Math.max(0, n - 1) * spacing
        const maxShellW = (screenLogicalW / ui) - 2 * edgeMargin
        if (rowShellW > maxShellW) {
            spacing = Math.max(2, Math.floor((maxShellW - n * tileBaseW) / Math.max(1, n - 1)))
            rowShellW = n * tileBaseW + Math.max(0, n - 1) * spacing
            if (rowShellW > maxShellW) {
                tileBaseW = Math.max(16, Math.floor((maxShellW - Math.max(0, n - 1) * spacing) / n))
                tileBaseH = Math.min(dockHeight, Math.round(tileBaseW * (dockHeight / (dockHeight - edgeMargin))))
                rowShellW = n * tileBaseW + Math.max(0, n - 1) * spacing
            }
        }
        const dockShellW = Math.min(maxShellW, rowShellW + 2 * edgeMargin)
        const dockW = Math.round(ui * dockShellW)
        const dockH = Math.round(ui * dockHeight)
        const marginBottom = Math.round(edgeMargin * ui)
        const dockLocalX = Math.round((screenLogicalW - dockW) / 2)
        const dockLocalY = Math.round(screenLogicalH - marginBottom - dockH)
        const dockGlobalX = monX + dockLocalX
        const dockGlobalY = monY + dockLocalY

        const edgeH = Math.max(1, Math.round(revealPx * ui))
        const edgeRect = [dockGlobalX, monY + screenLogicalH - edgeH, dockW, edgeH]
        const dockRect = [dockGlobalX, dockGlobalY, dockW, dockH]

        const rowOffsetShellX = Math.max(0, (dockShellW - rowShellW) / 2)
        const icons = []
        const minimized = []
        let phoneRect = null
        let cursorShellX = rowOffsetShellX

        for (let i = 0; i < list.length; i++) {
            const it = list[i] || ({})
            const mag = (typeof it.scale === "number" && it.scale > 0) ? it.scale : 1
            const w = Math.round(tileBaseW * ui * mag)
            const h = Math.round(tileBaseH * ui * mag)
            const cx = dockGlobalX + (cursorShellX + tileBaseW / 2) * ui
            const cy = dockGlobalY + (tileBaseH / 2) * ui
            const rx = Math.round(cx - w / 2)
            const ry = Math.round(cy - h / 2)
            const rect = [rx, ry, w, h]
            if (it.kind === "phone") phoneRect = rect
            if (it.kind === "icon" && it.app_id) {
                icons.push({
                    app_id: String(it.app_id),
                    desktop_id: String(it.desktop_id || (it.app_id + ".desktop")),
                    classes: Array.isArray(it.classes) && it.classes.length ? it.classes : [String(it.app_id)],
                    rect: rect
                })
            } else if (it.kind === "minimized" && it.address) {
                minimized.push({
                    address: normAddress(it.address),
                    rect: rect
                })
            }
            cursorShellX += tileBaseW + spacing
        }

        // Next free slot at the right end of the dock row (clamped inside the output).
        const slotW = Math.round(tileBaseW * ui)
        const slotH = Math.round(tileBaseH * ui)
        const slotX = Math.min(monX + screenLogicalW - marginBottom - slotW,
                               Math.round(dockGlobalX + cursorShellX * ui))
        const slotY = dockGlobalY
        const minimizedSlot = phoneRect || [slotX, slotY, slotW, slotH]

        return ({
            visible: visible,
            edge: edge,
            dock_rect: dockRect,
            edge_rect: edgeRect,
            minimized_slot: minimizedSlot,
            phone_rect: phoneRect,
            icons: icons,
            minimized: minimized
        })
    }

    // ---- atomic writers (<= 30 writes/s for dock icons; immediate for launches) ------------------------------------
    property bool dockDirty: false
    property string queuedDockJson: ""
    property real lastDockWriteMs: 0

    function setOutput(outputName, stateObj) {
        if (!outputName || !stateObj) return
        const prev = outputs[outputName]
        const nextJson = JSON.stringify(stateObj)
        if (prev && JSON.stringify(prev) === nextJson) return
        const next = Object.assign({}, outputs)
        next[outputName] = stateObj
        outputs = next
        scheduleDockWrite()
    }

    function removeOutput(outputName) {
        if (!outputName || !outputs[outputName]) return
        const next = Object.assign({}, outputs)
        delete next[outputName]
        outputs = next
        scheduleDockWrite()
    }

    function pruneStaleOutputs() {
        if (lamp.fixtureView.path !== "") return
        const mons = Hyprland.monitors ? Hyprland.monitors.values : []
        if (!mons || mons.length === 0) return
        let changed = false
        const next = Object.assign({}, outputs)
        for (const k in next) {
            if (!isLiveMonitorName(k)) {
                delete next[k]
                changed = true
            }
        }
        if (changed) {
            outputs = next
            scheduleDockWrite()
        }
    }

    function buildDockPayload() {
        const filtered = {}
        for (const k in lamp.outputs) {
            if (isLiveMonitorName(k)) filtered[k] = lamp.outputs[k]
        }
        return JSON.stringify({
            version: 1,
            updated_ms: Date.now(),
            outputs: filtered
        })
    }

    function scheduleDockWrite() {
        dockDirty = true
        const elapsed = Date.now() - lastDockWriteMs
        if (elapsed >= 34 && !dockWriter.running) {
            flushDockWrite()
        } else if (!throttleTimer.running) {
            throttleTimer.interval = Math.max(1, Math.min(34, Math.round(34 - elapsed)))
            throttleTimer.start()
        }
    }

    function flushDockWrite() {
        throttleTimer.stop()
        if (!dockDirty && !queuedDockJson) return
        queuedDockJson = buildDockPayload()
        dockDirty = false
        if (!dockWriter.running) {
            lastDockWriteMs = Date.now()
            dockWriter.payload = queuedDockJson
            queuedDockJson = ""
            dockWriter.running = true
        }
    }

    property Timer throttleTimer: Timer {
        interval: 34
        repeat: false
        onTriggered: lamp.flushDockWrite()
    }

    property Process dockWriter: Process {
        property string payload: ""
        command: ["sh", "-c",
            "f=$1; j=$2; d=${f%/*}; [ -n \"$d\" ] || d=.; mkdir -p \"$d\" 2>/dev/null || true; " +
            "t=\"$d/.${f##*/}.tmp.$$\"; printf '%s\\n' \"$j\" > \"$t\" && mv -f \"$t\" \"$f\"",
            "sh", lamp.dockFile, dockWriter.payload]
        onExited: (code, status) => {
            if (code === 0) lamp.writeCount++
            if (lamp.dockDirty || lamp.queuedDockJson) lamp.scheduleDockWrite()
        }
    }

    // ---- launch recording (contract (b): 10 s prune, atomic write before spawning) ---------------------------------
    property string queuedLaunchJson: ""

    function recordLaunch(entry, outputName, rect) {
        const now = Date.now()
        const kept = []
        for (const p of pendingLaunches) {
            if (p && typeof p.time_ms === "number" && now - p.time_ms < 10000) kept.push(p)
        }
        const rawId = entry ? String(entry.id || entry.appId || entry.name || "app") : "app"
        const appId = stripDesktop(rawId)
        const desktopId = rawId.endsWith(".desktop") ? rawId : rawId + ".desktop"
        const classes = classesForEntry(entry, [])
        const r = (Array.isArray(rect) && rect.length === 4) ? rect.map(Number) : [0, 0, 48, 48]
        launchSeq++
        const item = {
            id: String(now) + "-" + String(launchSeq) + "-" + appId,
            time_ms: now,
            app_id: appId,
            desktop_id: desktopId,
            classes: classes.length ? classes : [appId],
            output: String(outputName || ""),
            rect: r
        }
        kept.push(item)
        pendingLaunches = kept
        const json = JSON.stringify({ version: 1, pending: kept })
        if (!launchWriter.running) {
            launchWriter.payload = json
            launchWriter.running = true
        } else {
            queuedLaunchJson = json
        }
        return item
    }

    property Process launchWriter: Process {
        property string payload: ""
        command: ["sh", "-c",
            "f=$1; j=$2; d=${f%/*}; [ -n \"$d\" ] || d=.; mkdir -p \"$d\" 2>/dev/null || true; " +
            "t=\"$d/.${f##*/}.tmp.$$\"; printf '%s\\n' \"$j\" > \"$t\" && mv -f \"$t\" \"$f\"",
            "sh", lamp.launchFile, launchWriter.payload]
        onExited: (code, status) => {
            if (code === 0) lamp.launchWriteCount++
            if (lamp.queuedLaunchJson) {
                launchWriter.payload = lamp.queuedLaunchJson
                lamp.queuedLaunchJson = ""
                launchWriter.running = true
            }
        }
    }

    // ---- Hyprland event listeners to keep minimizedWindows & monitor positions fresh -------------------------------
    readonly property var watchEvents: [
        "openwindow", "closewindow", "movewindow", "movewindowv2", "workspace", "workspacev2",
        "activespecial", "activespecialv2", "minimized", "configreloaded", "focusedmonv2",
        "activewindowv2", "monitoradded", "monitoraddedv2", "monitorremoved", "monitorremovedv2"
    ]
    function refreshSoon() {
        if (!refreshFast.running) refreshFast.start()
        refreshSettled.restart()
    }
    property Timer refreshFast: Timer {
        interval: 16
        onTriggered: { Hyprland.refreshToplevels(); Hyprland.refreshMonitors(); lamp.pruneStaleOutputs() }
    }
    property Timer refreshSettled: Timer {
        interval: 250
        onTriggered: { Hyprland.refreshToplevels(); Hyprland.refreshMonitors(); lamp.pruneStaleOutputs(); lamp.refreshLampStatus() }
    }
    // A lamp minimize moves the window to special:minimized after duration_minimize_ms (~520 ms).
    property Timer refreshAfterLamp: Timer {
        interval: 600
        onTriggered: { Hyprland.refreshToplevels(); Hyprland.refreshMonitors(); lamp.pruneStaleOutputs(); lamp.refreshLampStatus() }
    }
    property Connections hyprWatch: Connections {
        target: Hyprland
        function onRawEvent(event) {
            if (lamp.watchEvents.indexOf(event.name) >= 0) lamp.refreshSoon()
        }
    }
    Component.onCompleted: {
        if (fixtureView.path !== "") parseFixture(fixtureView.text())
        if (prefsView.path !== "") parsePrefs(prefsView.text())
        refreshSoon()
    }

    // ---- IPC target "docklamp" for headless inspection and end-to-end verification ---------------------------------
    property IpcHandler ipc: IpcHandler {
        target: "docklamp"
        function state(): string {
            return JSON.stringify({
                version: 1,
                dock_file: lamp.dockFile,
                launch_file: lamp.launchFile,
                prefs_file: lamp.prefsFile,
                write_count: lamp.writeCount,
                launch_write_count: lamp.launchWriteCount,
                prefs_write_count: lamp.prefsWriteCount,
                last_dispatched: lamp.lastDispatched,
                minimized_windows: lamp.minimizedWindows,
                plugin_origin: lamp.pluginOrigin,
                restoring_windows: lamp.restoringWindows,
                custom_pinned: lamp.customPinned,
                starred_apps: lamp.starredApps,
                reorder_mode: lamp.reorderMode,
                outputs: lamp.outputs,
                pending: lamp.pendingLaunches
            })
        }
        function flush(): string {
            lamp.flushDockWrite()
            return state()
        }
        function minimize(address: string): string {
            return lamp.minimize(address)
        }
        function restore(address: string, source: string): string {
            return lamp.restore(address, source)
        }
        function pin(appId: string): string {
            lamp.pinApp(appId, lamp.customPinned || [])
            return state()
        }
        function unpin(appId: string): string {
            lamp.unpinApp(appId, lamp.customPinned || [])
            return state()
        }
        function togglePin(appId: string): string {
            lamp.togglePin(appId, lamp.customPinned || [])
            return state()
        }
        function movePin(appId: string, delta: int): string {
            lamp.movePin(appId, delta, lamp.customPinned || [])
            return state()
        }
        function star(appId: string): string {
            lamp.starApp(appId)
            return state()
        }
        function unstar(appId: string): string {
            lamp.unstarApp(appId)
            return state()
        }
        function toggleStar(appId: string): string {
            lamp.toggleStar(appId)
            return state()
        }
        function moveStar(appId: string, delta: int): string {
            lamp.moveStar(appId, delta)
            return state()
        }
        function setReorderMode(on: bool): string {
            lamp.reorderMode = on
            return state()
        }
        function launch(appId: string, output: string): string {
            const out = lamp.outputs[output] || ({})
            let rect = out.minimized_slot || [0, 0, 48, 48]
            if (Array.isArray(out.icons)) {
                for (const ic of out.icons) {
                    if (ic.app_id === appId || ic.desktop_id === appId || (Array.isArray(ic.classes) && ic.classes.indexOf(appId) >= 0)) {
                        rect = ic.rect
                        break
                    }
                }
            }
            const item = lamp.recordLaunch({ id: appId + ".desktop", appId: appId, startupClass: appId, icon: appId }, output, rect)
            return JSON.stringify(item)
        }
    }
}
