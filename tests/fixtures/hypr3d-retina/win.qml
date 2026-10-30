// AI-hint: Hypr3D retina-spawn test window (tests/test-hypr3d-retina-spawn.py Nest.client): white, a 40x40 marker at (10,10) and one 10 px from the bottom-right corner
// (centroids span (w-60) x (h-60) logical px; their midpoint is the window centre), a 1-logical-px black/white
// grating band and text at the SSOT [theme.font] size (12 px) and 16 px.
// Arguments: <w> <h> [<marker colour A> <marker colour B>] [fixed] -- "fixed" sets the minimum size to the size
// (a client that refuses a smaller box: the push-back case).
import QtQuick
Window {
    id: w
    property var a: Qt.application.arguments
    // the arguments after the .qml file (the runtime may or may not keep the "--" separator)
    property var args: { var i = 0; while (i < a.length && !String(a[i]).endsWith(".qml")) i++; var r = a.slice(i + 1); return r.length && r[0] === "--" ? r.slice(1) : r }
    property int base: 0
    property int ww: Number(args[0]); property int hh: Number(args[1])
    property string ca: args.length > 3 ? args[2] : "#ff0000"
    property string cb: args.length > 3 ? args[3] : "#0000ff"
    property bool fixed: args[args.length - 1] === "fixed"
    width: ww; height: hh
    minimumWidth: fixed ? ww : 1; minimumHeight: fixed ? hh : 1
    visible: true; color: "#ffffff"; title: "h3r-win-" + ww + "x" + hh + "-" + ca
    Rectangle { x: 10; y: 10; width: 40; height: 40; color: w.ca }
    Rectangle { x: w.width - 50; y: w.height - 50; width: 40; height: 40; color: w.cb }
    Row { x: 60; y: 10
        Repeater { model: 120; Rectangle { width: 1; height: 40; color: index % 2 ? "#ffffff" : "#000000" } } }
    Column { x: 10; y: 60; spacing: 4
        Text { text: "MiOS Hypr3D 0123456789 xhg"; color: "#000000"; font.family: "GeistMono Nerd Font Mono"; font.pixelSize: 12 }
        Text { text: "The quick brown fox jumps"; color: "#000000"; font.family: "GeistMono Nerd Font Mono"; font.pixelSize: 12 }
        Text { text: "MiOS 16px xhg"; color: "#000000"; font.family: "GeistMono Nerd Font Mono"; font.pixelSize: 16 }
    }
}
