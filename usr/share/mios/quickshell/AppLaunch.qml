// AI-hint: Shared desktop entry launcher. Rust preflight recovers missing portal mounts and bounds sandbox probes; GIO handles desktop activation, while the configured terminal opens TUIs. Checks time out with visible failure.
// AI-related: tools/native/mios-gen/src/app_preflight.rs, Sidebar.qml, AppDrawer.qml, Search.qml, usr/share/mios/mios.toml [desktop.launch]
pragma Singleton
import QtQuick
import Quickshell
import Quickshell.Io

Singleton {
    id: launches
    property var cfg: ({})
    property var jobs: ({})
    readonly property int timeoutMs: Math.max(1000, Number(cfg.overall_timeout_ms) || 15000) + 1000
    property Process policyReader: Process {
        command: [Quickshell.env("MIOS_TOML_GET") || "/usr/bin/mios-toml-get", "--section", "desktop.launch"]
        running: true
        stdout: StdioCollector {
            onStreamFinished: {
                try { launches.cfg = JSON.parse(text) } catch (e) { console.warn("Application launch policy could not be read") }
            }
        }
    }
    function failed(entry, message) {
        console.warn("Application launch failed:", entry ? entry.id : "", message)
        Quickshell.execDetached([cfg.notification_program || "notify-send", "Could not open " + (entry ? entry.name : "application"), message])
    }
    function finished(key) {
        const next = Object.assign({}, jobs)
        delete next[key]
        jobs = next
    }
    function launch(entry, output, rect) {
        if (!entry || !entry.id || jobs[entry.id]) return
        const job = checkFactory.createObject(launches, {entry: entry, outputName: output || "", iconRect: rect || []})
        const next = Object.assign({}, jobs)
        next[entry.id] = job
        jobs = next
        job.running = true
    }
    property Component checkFactory: Component {
        Process {
            id: check
            property var entry
            property string outputName: ""
            property var iconRect: []
            property string result: ""
            property bool expired: false
            property bool dispatching: false
            command: [Quickshell.env("MIOS_APP_PREFLIGHT") || "/usr/bin/mios-gen", "app-preflight", "--desktop", entry.id]
            stdout: StdioCollector { onStreamFinished: check.result = text }
            stderr: SplitParser { onRead: data => console.warn("Application preflight:", data) }
            property Timer limit: Timer {
                interval: launches.timeoutMs
                running: check.running
                onTriggered: {
                    check.expired = true
                    check.signal(9)
                    launches.failed(check.entry, "Application startup timed out. Please try again.")
                    launches.finished(check.entry.id)
                }
            }
            onExited: (code, status) => {
                if (!expired && dispatching) {
                    if (code !== 0) launches.failed(entry, "The application launch command failed. Please try again.")
                    launches.finished(entry.id)
                    Qt.callLater(() => check.destroy())
                    return
                }
                if (!expired) {
                    let response = ({})
                    try { response = JSON.parse(result) } catch (e) {}
                    if (code === 0 && response.ok && response.desktop_file) {
                        if (outputName && iconRect.length === 4)
                            DockLamp.recordLaunch(entry, outputName, iconRect)
                        if (entry.runInTerminal) {
                            const terminal = launches.cfg.terminal_command || []
                            if (!terminal.length) launches.failed(entry, "The application terminal is not configured.")
                            else Quickshell.execDetached({command: terminal.map(a => String(a).split("{name}").join(entry.name)).concat(Array.from(entry.command)), workingDirectory: entry.workingDirectory})
                        } else {
                            // GIO handles D-Bus activation and desktop field
                            // codes; execute() omits some of these.
                            let argv = [launches.cfg.desktop_launcher_program || "gio", "launch", response.desktop_file]
                            if (response.unset_environment && response.unset_environment.length) {
                                let prefix = [launches.cfg.environment_program || "env"]
                                response.unset_environment.forEach(name => { prefix.push("-u"); prefix.push(name) })
                                argv = prefix.concat(argv)
                            }
                            // Observe GIO's short activation request, never the
                            // lifetime of the application it starts.
                            check.command = argv
                            check.dispatching = true
                            Qt.callLater(() => check.running = true)
                            return
                        }
                    } else {
                        launches.failed(entry, response.error || "Application services are unavailable. Please try again.")
                    }
                    launches.finished(entry.id)
                }
                Qt.callLater(() => check.destroy())
            }
        }
    }
}
