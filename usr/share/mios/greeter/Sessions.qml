// AI-hint: Session catalogue for the MiOS glass greeter: lists *.desktop files in the greetd session
// directories (MIOS_GREETER_SESSION_DIRS, colon list, same dirs tuigreet --sessions reads), parses
// [Desktop Entry] Name/Exec/DesktopNames/Hidden, and turns Exec into the argv greetd start_session runs.
// AI-related: usr/share/mios/greeter/shell.qml, usr/share/wayland-sessions/, usr/libexec/mios/mios-greeter-session
pragma ComponentBehavior: Bound
import QtQuick
import QtQml.Models
import Qt.labs.folderlistmodel
import Quickshell.Io

Item {
    id: root
    // Directories to scan, in priority order (first directory wins on a duplicate id).
    property var dirs: []
    // Desktop-file id ("hyprland" for hyprland.desktop) selected when nothing is remembered.
    property string preferred: ""
    // Sorted [{id, name, comment, argv, desktopNames, type, path}] -- preferred session first.
    property var list: []
    property var _entries: ({})

    function parseDesktop(txt) {
        const e = {}
        let inEntry = false
        for (const raw of txt.split("\n")) {
            const line = raw.trim()
            if (line.length === 0 || line[0] === "#") continue
            if (line[0] === "[") { inEntry = line === "[Desktop Entry]"; continue }
            if (!inEntry) continue
            const i = line.indexOf("=")
            if (i < 0) continue
            const k = line.slice(0, i).trim()
            if (k.indexOf("[") >= 0) continue   // localized keys: the C locale value is enough here
            e[k] = line.slice(i + 1).trim()
        }
        return e
    }

    // Desktop Entry Exec -> argv: honours double/single quotes and backslash escapes,
    // drops field codes (%f %U ...) and turns %% into %.
    function splitExec(exec) {
        const out = []
        let cur = "", quote = "", have = false
        for (let i = 0; i < exec.length; i++) {
            const c = exec[i]
            if (quote) {
                if (c === quote) { quote = ""; continue }
                if (c === "\\" && quote === "\"" && i + 1 < exec.length) { cur += exec[++i]; continue }
                cur += c
                continue
            }
            if (c === "\"" || c === "'") { quote = c; have = true; continue }
            if (c === "\\" && i + 1 < exec.length) { cur += exec[++i]; have = true; continue }
            if (c === " " || c === "\t") {
                if (have) { out.push(cur); cur = ""; have = false }
                continue
            }
            cur += c
            have = true
        }
        if (have) out.push(cur)
        return out
            .filter(a => !/^%[fFuUdDnNickvm]$/.test(a))
            .map(a => a.replace(/%%/g, "%"))
    }

    function add(path, baseName, dirIndex, txt) {
        const e = parseDesktop(txt)
        const next = Object.assign({}, _entries)
        const argv = splitExec(e.Exec || "")
        const usable = argv.length > 0 && e.Hidden !== "true" && (e.Type || "Application") === "Application"
        if (usable)
            next[path] = {
                id: baseName,
                name: e.Name || baseName,
                comment: e.Comment || "",
                argv: argv,
                desktopNames: e.DesktopNames || "",
                type: path.indexOf("/xsessions/") >= 0 ? "x11" : "wayland",
                path: path,
                rank: dirIndex
            }
        else
            delete next[path]
        _entries = next
        rebuild()
    }

    function rebuild() {
        const seen = {}
        const all = Object.values(_entries).sort((a, b) => a.rank - b.rank)
        const out = []
        for (const s of all) {
            if (seen[s.id]) continue
            seen[s.id] = true
            out.push(s)
        }
        out.sort((a, b) => {
            if (a.id === root.preferred) return -1
            if (b.id === root.preferred) return 1
            return a.name.localeCompare(b.name)
        })
        list = out
    }

    function indexOfId(id) {
        for (let i = 0; i < list.length; i++)
            if (list[i].id === id) return i
        return -1
    }

    Instantiator {
        model: root.dirs
        delegate: QtObject {
            id: dirScan
            required property string modelData
            required property int index
            property FolderListModel folder: FolderListModel {
                folder: "file://" + dirScan.modelData
                nameFilters: ["*.desktop"]
                showDirs: false
                showHidden: false
            }
            property Instantiator files: Instantiator {
                model: dirScan.folder
                delegate: FileView {
                    id: entryFile
                    required property string filePath
                    required property string fileBaseName
                    path: filePath
                    preload: true
                    printErrors: false
                    onLoaded: root.add(entryFile.filePath, entryFile.fileBaseName, dirScan.index, entryFile.text())
                }
            }
        }
    }
}
