// AI-hint: Login-user catalogue for the MiOS glass greeter: human accounts from the passwd database
// (uid range MIOS_GREETER_MIN_UID..MIOS_GREETER_MAX_UID, interactive shell), ordered by uid.
// AI-related: usr/share/mios/greeter/shell.qml, usr/share/mios/mios.toml [desktop.greeter]
import QtQuick
import Quickshell.Io

Item {
    id: root
    property string passwd: "/etc/passwd"
    property int minUid: 1000
    property int maxUid: 59999
    // [{name, display, uid}]
    property var list: []

    function parse(txt) {
        const out = []
        for (const line of txt.split("\n")) {
            const f = line.split(":")
            if (f.length < 7) continue
            const uid = parseInt(f[2], 10)
            if (isNaN(uid) || uid < root.minUid || uid > root.maxUid) continue
            const shell = f[6].trim()
            if (/(nologin|\/false)$/.test(shell)) continue
            const gecos = (f[4] || "").split(",")[0].trim()
            out.push({ name: f[0], display: gecos || f[0], uid: uid })
        }
        out.sort((a, b) => a.uid - b.uid)
        list = out
    }

    function indexOfName(name) {
        for (let i = 0; i < list.length; i++)
            if (list[i].name === name) return i
        return -1
    }

    FileView {
        path: root.passwd
        preload: true
        printErrors: false
        onLoaded: root.parse(text())
    }
}
