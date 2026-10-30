// AI-hint: SSOT-styled global search over native desktop entries, MiOS actions and the local plocate index.
import QtQuick
import Quickshell as QS
import Quickshell.Io
import Quickshell.Wayland

QS.PanelWindow {
    id: searchWindow
    required property QtObject shellState
    required property string outputName
    property QtObject theme: Theme {}
    property var files: []
    property string fileQuery: ""
    property string fileError: ""
    property string query: shellState.searchQuery.trim()
    property var results: {
        const q = query.toLowerCase()
        if (!q) return []
        let rows = []
        QS.DesktopEntries.applications.values.filter(e => !e.noDisplay).forEach(e => {
            const terms = e.name + " " + e.genericName + " " + (e.keywords || []).join(" ")
            if (!q || terms.toLowerCase().includes(q))
                rows.push({kind: "Application", label: e.name, detail: e.genericName || e.id, icon: e.icon, entry: e})
        })
        theme.searchActions.forEach(a => {
            if (!q || (a.label + " " + a.id).toLowerCase().includes(q))
                rows.push({kind: "MiOS action", label: a.label, detail: a.id, command: a.desktop_command})
        })
        if (fileQuery === query) files.forEach(path => rows.push({
            kind: "File", label: path.split("/").pop(), detail: path, path: path, icon: "text-x-generic"
        }))
        return rows.slice(0, theme.searchLimit)
    }
    property bool requestedOpen: shellState.searchOpen && (!shellState.searchScreen || shellState.searchScreen === outputName)
    visible: requestedOpen
    implicitWidth: Math.min(theme.searchWidth, screen.width - 2 * Glass.gap)
    property int shownRows: Math.max(0, Math.min(results.length, theme.searchVisibleRows,
        Math.floor((screen.height - margins.top - 4 * Glass.gap) / theme.searchRowHeight) - 2))
    implicitHeight: theme.searchRowHeight * (query ? shownRows + 2 : 1) + 2 * Glass.panelPad
    anchors.top: true
    margins.top: Math.round(screen.height * theme.searchTopFraction)
    exclusiveZone: 0
    WlrLayershell.namespace: "mios-search"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: requestedOpen ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None
    color: "transparent"

    function activate(row) {
        if (!row) return
        if (row.entry) AppLaunch.launch(row.entry, searchWindow.outputName, [])
        else if (row.path) Qt.openUrlExternally("file://" + row.path.split("/").map(encodeURIComponent).join("/"))
        else if (row.command) QS.Quickshell.execDetached(["sh", "-lc", row.command])
        shellState.searchOpen = false
    }
    onRequestedOpenChanged: if (requestedOpen) {
        Qt.callLater(() => { if (searchWindow.requestedOpen) { input.forceActiveFocus(); list.currentIndex = 0 } })
        debounce.restart()
    }
    onQueryChanged: {
        files = []
        fileError = ""
        Qt.callLater(() => { list.currentIndex = 0 })
        if (visible) debounce.restart()
    }
    Timer {
        id: debounce
        interval: searchWindow.theme.searchDebounce
        onTriggered: {
            if (!searchWindow.visible || searchWindow.query.length < searchWindow.theme.searchMinChars) return
            searchWindow.fileQuery = searchWindow.query
            locate.requested = searchWindow.query
            locate.exec(["plocate", "-e", "-i", "-0", "-l", String(searchWindow.theme.searchLimit), searchWindow.query.includes("/") ? "-w" : "-b", "--", searchWindow.query])
        }
    }
    Process {
        id: locate
        property string requested: ""
        stdout: StdioCollector {
            onStreamFinished: {
                if (locate.requested === searchWindow.query) {
                    searchWindow.fileQuery = locate.requested
                    searchWindow.files = text.split("\u0000").filter(p => p.startsWith("/"))
                }
            }
        }
        stderr: StdioCollector {
            onStreamFinished: if (text.trim() && locate.requested === searchWindow.query) searchWindow.fileError = "The local file index is unavailable."
        }
    }
    GlassSurface {
        id: box
        anchors.fill: parent
        // the island's glass (GlassSurface: Glass.corner rule and fill), no outline (hyprglass draws the rim)
        color: Glass.fill
        clip: true
        Column {
            anchors.fill: parent
            anchors.margins: Glass.panelPad
            spacing: 0
            TextInput {
                id: input
                width: parent.width
                height: searchWindow.theme.searchRowHeight
                verticalAlignment: TextInput.AlignVCenter
                color: searchWindow.theme.fg
                selectionColor: searchWindow.theme.accent
                selectedTextColor: searchWindow.theme.fg
                font.family: searchWindow.theme.fontFamily
                font.pixelSize: searchWindow.theme.titleFontSize
                text: searchWindow.shellState.searchQuery
                onTextEdited: searchWindow.shellState.searchQuery = text
                clip: true
                Text {
                    anchors.fill: parent
                    verticalAlignment: Text.AlignVCenter
                    visible: !input.text
                    text: "Search apps, files and MiOS actions"
                    color: searchWindow.theme.subtle
                    font: input.font
                }
                Keys.onEscapePressed: searchWindow.shellState.searchOpen = false
                Keys.onDownPressed: list.currentIndex = Math.max(0, Math.min(list.currentIndex + 1, list.count - 1))
                Keys.onUpPressed: list.currentIndex = Math.max(list.currentIndex - 1, 0)
                Keys.onReturnPressed: searchWindow.activate(searchWindow.results[list.currentIndex])
                Keys.onEnterPressed: searchWindow.activate(searchWindow.results[list.currentIndex])
            }
            ListView {
                id: list
                width: parent.width
                visible: searchWindow.query.length > 0
                height: searchWindow.shownRows * searchWindow.theme.searchRowHeight
                clip: true
                model: searchWindow.results
                currentIndex: 0
                delegate: GlassSurface {
                    required property var modelData
                    required property int index
                    width: list.width
                    height: searchWindow.theme.searchRowHeight
                    radius: Glass.inner(box.radius, Glass.panelPad)   // concentric with the box
                    color: index === list.currentIndex ? searchWindow.theme.withAlpha(searchWindow.theme.accent, searchWindow.theme.selectionOpacity) : "transparent"
                    Image {
                        id: icon
                        anchors.left: parent.left
                        anchors.leftMargin: Glass.gap
                        anchors.verticalCenter: parent.verticalCenter
                        width: searchWindow.theme.appIcon
                        height: width
                        source: QS.Quickshell.iconPath(modelData.icon || "system-run", true)
                    }
                    Column {
                        anchors.left: icon.right
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.margins: Glass.gap
                        Text {
                            width: parent.width
                            text: modelData.label
                            color: searchWindow.theme.fg
                            font.family: searchWindow.theme.fontFamily
                            elide: Text.ElideRight
                        }
                        Text {
                            width: parent.width
                            text: modelData.kind + " · " + modelData.detail
                            color: searchWindow.theme.subtle
                            font.family: searchWindow.theme.fontFamily
                            font.pixelSize: searchWindow.theme.fontSize
                            elide: Text.ElideMiddle
                        }
                    }
                    MouseArea {
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: { list.currentIndex = index; searchWindow.activate(modelData) }
                    }
                }
            }
            Text {
                width: parent.width
                height: searchWindow.theme.searchRowHeight
                verticalAlignment: Text.AlignVCenter
                visible: searchWindow.query.length > 0
                text: searchWindow.fileError || (searchWindow.results.length ? "↑ ↓ Select    Enter Open    Esc Close" : "No matching apps, actions or indexed files")
                color: searchWindow.theme.subtle
                font.family: searchWindow.theme.fontFamily
                elide: Text.ElideRight
            }
        }
    }
    IpcHandler {
        target: "search-results-" + searchWindow.outputName
        function snapshot(): string {
            return JSON.stringify({query: searchWindow.query, visible: searchWindow.visible,
                                   width: searchWindow.width, height: searchWindow.height, top: searchWindow.margins.top,
                                   selected: list.currentIndex, results: searchWindow.results.map(r => ({kind:r.kind,label:r.label,detail:r.detail})),
                                   fileError: searchWindow.fileError})
        }
    }
}
