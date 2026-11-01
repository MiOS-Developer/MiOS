// AI-hint: Self-contained interactive on-screen keyboard for MiOS Dock and mobile surfaces.
// Styled with Phosh OSK mobile/terminal navigation bar and complete QWERTY/symbols layout.
// Dispatches keystrokes directly into focused target inputs or active client windows via Hyprland sendshortcut.
// AI-related: usr/share/mios/quickshell/DockExpanded.qml, usr/share/mios/quickshell/Sidebar.qml, usr/share/mios/quickshell/Glass.qml
import QtQuick
import Quickshell as QS
import Quickshell.Hyprland
import Quickshell.Io

Item {
    id: keyboardRoot
    property QtObject theme: Theme {}
    property var targetInput: null

    // Modifier states
    property bool shiftActive: false
    property bool capsActive: false
    property bool ctrlActive: false
    property bool altActive: false
    property bool superActive: false
    property bool symbolsActive: false

    readonly property bool isUpper: shiftActive || capsActive

    implicitWidth: parent ? parent.width : 800 // design-exempt: width fallback
    implicitHeight: DockConfig.oskHeight

    GlassSurface {
        id: bgSurface
        anchors.fill: parent
        radius: Glass.cornerFor(height)
        color: theme.withAlpha(theme.bg, theme.panelOpacity)
    }

    Column {
        id: layoutCol
        anchors.fill: parent
        anchors.margins: Glass.inset
        spacing: Math.max(2, Math.floor((keyboardRoot.height - 2 * Glass.inset - 6 * rowHeight) / 5)) // design-exempt: dynamic row spacing

        readonly property real availHeight: keyboardRoot.height - 2 * Glass.inset
        readonly property real rowHeight: Math.floor((availHeight - 5 * 2) / 6) // design-exempt: 6 rows layout

        // Row 0: Phosh-styled terminal/navigation header bar
        Row {
            width: parent.width
            height: layoutCol.rowHeight
            spacing: Glass.inset / 2

            KeyButton { text: "Esc"; key: "Escape"; flex: 1.0; isModifier: true; onClicked: dispatchKey("Escape", "Escape") }
            KeyButton { text: "Tab"; key: "Tab"; flex: 1.0; isModifier: true; onClicked: dispatchKey("Tab", "\t") }
            KeyButton { text: "Ctrl"; key: "Control_L"; flex: 1.0; isModifier: true; active: keyboardRoot.ctrlActive; onClicked: keyboardRoot.ctrlActive = !keyboardRoot.ctrlActive }
            KeyButton { text: "Alt"; key: "Alt_L"; flex: 1.0; isModifier: true; active: keyboardRoot.altActive; onClicked: keyboardRoot.altActive = !keyboardRoot.altActive }
            KeyButton { text: "Super"; key: "Super_L"; flex: 1.1; isModifier: true; active: keyboardRoot.superActive; onClicked: keyboardRoot.superActive = !keyboardRoot.superActive }
            KeyButton { text: "|"; key: "bar"; flex: 0.8; onClicked: dispatchChar("|", "bar") }
            KeyButton { text: "/"; key: "slash"; flex: 0.8; onClicked: dispatchChar("/", "slash") }
            KeyButton { text: "-"; key: "minus"; flex: 0.8; onClicked: dispatchChar("-", "minus") }
            KeyButton { text: "←"; key: "Left"; flex: 0.9; isModifier: true; onClicked: dispatchKey("Left", "") }
            KeyButton { text: "↑"; key: "Up"; flex: 0.9; isModifier: true; onClicked: dispatchKey("Up", "") }
            KeyButton { text: "↓"; key: "Down"; flex: 0.9; isModifier: true; onClicked: dispatchKey("Down", "") }
            KeyButton { text: "→"; key: "Right"; flex: 0.9; isModifier: true; onClicked: dispatchKey("Right", "") }
        }

        // Row 1: Numbers / Symbols
        Row {
            width: parent.width
            height: layoutCol.rowHeight
            spacing: Glass.inset / 2

            Repeater {
                model: keyboardRoot.symbolsActive ? [
                    {t: "[", s: "{", k: "bracketleft"},
                    {t: "]", s: "}", k: "bracketright"},
                    {t: "{", s: "[", k: "braceleft"},
                    {t: "}", s: "]", k: "braceright"},
                    {t: "#", s: "#", k: "numbersign"},
                    {t: "%", s: "%", k: "percent"},
                    {t: "^", s: "^", k: "asciicircum"},
                    {t: "*", s: "*", k: "asterisk"},
                    {t: "+", s: "+", k: "plus"},
                    {t: "=", s: "=", k: "equal"},
                    {t: "\\", s: "|", k: "backslash"}
                ] : [
                    {t: "`", s: "~", k: "grave"},
                    {t: "1", s: "!", k: "1"},
                    {t: "2", s: "@", k: "2"},
                    {t: "3", s: "#", k: "3"},
                    {t: "4", s: "$", k: "4"},
                    {t: "5", s: "%", k: "5"},
                    {t: "6", s: "^", k: "6"},
                    {t: "7", s: "&", k: "7"},
                    {t: "8", s: "*", k: "8"},
                    {t: "9", s: "(", k: "9"},
                    {t: "0", s: ")", k: "0"},
                    {t: "-", s: "_", k: "minus"},
                    {t: "=", s: "+", k: "equal"}
                ]
                delegate: KeyButton {
                    text: keyboardRoot.shiftActive ? modelData.s : modelData.t
                    key: modelData.k
                    flex: 1.0
                    onClicked: dispatchChar(text, modelData.k)
                }
            }

            KeyButton {
                text: "⌫"; key: "BackSpace"; flex: 1.5; isModifier: true
                accentColor: theme.warning
                onClicked: dispatchBackspace()
            }
        }

        // Row 2: QWERTY Row 1
        Row {
            width: parent.width
            height: layoutCol.rowHeight
            spacing: Glass.inset / 2

            KeyButton { text: "⇥"; key: "Tab"; flex: 1.2; isModifier: true; onClicked: dispatchKey("Tab", "\t") }

            Repeater {
                model: keyboardRoot.symbolsActive
                    ? ["~", "`", "@", "$", "_", "&", "-", "+", "(", ")"]
                    : ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p"]
                delegate: KeyButton {
                    text: keyboardRoot.symbolsActive ? modelData : (keyboardRoot.isUpper ? modelData.toUpperCase() : modelData)
                    key: modelData
                    flex: 1.0
                    onClicked: dispatchChar(text, modelData)
                }
            }

            KeyButton {
                text: keyboardRoot.symbolsActive ? "<" : (keyboardRoot.shiftActive ? "{" : "[")
                key: "bracketleft"
                flex: 1.0
                onClicked: dispatchChar(text, "bracketleft")
            }
            KeyButton {
                text: keyboardRoot.symbolsActive ? ">" : (keyboardRoot.shiftActive ? "}" : "]")
                key: "bracketright"
                flex: 1.0
                onClicked: dispatchChar(text, "bracketright")
            }
        }

        // Row 3: Home Row
        Row {
            width: parent.width
            height: layoutCol.rowHeight
            spacing: Glass.inset / 2

            KeyButton {
                text: "Caps"; key: "Caps_Lock"; flex: 1.3; isModifier: true
                active: keyboardRoot.capsActive
                accentColor: theme.cursor
                onClicked: keyboardRoot.capsActive = !keyboardRoot.capsActive
            }

            Repeater {
                model: keyboardRoot.symbolsActive
                    ? ["*", "/", "=", "%", "\"", "'", ":", ";", "!", "?"]
                    : ["a", "s", "d", "f", "g", "h", "j", "k", "l"]
                delegate: KeyButton {
                    text: keyboardRoot.symbolsActive ? modelData : (keyboardRoot.isUpper ? modelData.toUpperCase() : modelData)
                    key: modelData
                    flex: 1.0
                    onClicked: dispatchChar(text, modelData)
                }
            }

            KeyButton {
                visible: !keyboardRoot.symbolsActive
                text: keyboardRoot.shiftActive ? ":" : ";"
                key: "semicolon"
                flex: 0.9
                onClicked: dispatchChar(text, "semicolon")
            }
            KeyButton {
                visible: !keyboardRoot.symbolsActive
                text: keyboardRoot.shiftActive ? "\"" : "'"
                key: "apostrophe"
                flex: 0.9
                onClicked: dispatchChar(text, "apostrophe")
            }

            KeyButton {
                text: "⏎"; key: "Return"; flex: 1.6; isModifier: true
                accentColor: theme.accent
                onClicked: dispatchEnter()
            }
        }

        // Row 4: Bottom Row
        Row {
            width: parent.width
            height: layoutCol.rowHeight
            spacing: Glass.inset / 2

            KeyButton {
                text: "⇧"; key: "Shift_L"; flex: 1.6; isModifier: true
                active: keyboardRoot.shiftActive
                accentColor: theme.accent
                onClicked: keyboardRoot.shiftActive = !keyboardRoot.shiftActive
            }

            Repeater {
                model: keyboardRoot.symbolsActive
                    ? ["\\", "|", "[", "]", "{", "}", "<", ">", ",", "."]
                    : ["z", "x", "c", "v", "b", "n", "m"]
                delegate: KeyButton {
                    text: keyboardRoot.symbolsActive ? modelData : (keyboardRoot.isUpper ? modelData.toUpperCase() : modelData)
                    key: modelData
                    flex: 1.0
                    onClicked: dispatchChar(text, modelData)
                }
            }

            KeyButton {
                visible: !keyboardRoot.symbolsActive
                text: keyboardRoot.shiftActive ? "<" : ","
                key: "comma"
                flex: 1.0
                onClicked: dispatchChar(text, "comma")
            }
            KeyButton {
                visible: !keyboardRoot.symbolsActive
                text: keyboardRoot.shiftActive ? ">" : "."
                key: "period"
                flex: 1.0
                onClicked: dispatchChar(text, "period")
            }
            KeyButton {
                visible: !keyboardRoot.symbolsActive
                text: keyboardRoot.shiftActive ? "?" : "/"
                key: "slash"
                flex: 1.0
                onClicked: dispatchChar(text, "slash")
            }

            KeyButton {
                text: "⇧"; key: "Shift_R"; flex: 1.6; isModifier: true
                active: keyboardRoot.shiftActive
                accentColor: theme.accent
                onClicked: keyboardRoot.shiftActive = !keyboardRoot.shiftActive
            }
        }

        // Row 5: Space / Controls Row
        Row {
            width: parent.width
            height: layoutCol.rowHeight
            spacing: Glass.inset / 2

            KeyButton {
                text: keyboardRoot.symbolsActive ? "ABC" : "?123"
                key: "symbols_toggle"
                flex: 1.5
                isModifier: true
                active: keyboardRoot.symbolsActive
                accentColor: theme.cursor
                onClicked: keyboardRoot.symbolsActive = !keyboardRoot.symbolsActive
            }

            KeyButton { text: "Ctrl"; key: "Control_L"; flex: 1.0; isModifier: true; active: keyboardRoot.ctrlActive; onClicked: keyboardRoot.ctrlActive = !keyboardRoot.ctrlActive }
            KeyButton { text: "Super"; key: "Super_L"; flex: 1.0; isModifier: true; active: keyboardRoot.superActive; onClicked: keyboardRoot.superActive = !keyboardRoot.superActive }
            KeyButton { text: "Alt"; key: "Alt_L"; flex: 1.0; isModifier: true; active: keyboardRoot.altActive; onClicked: keyboardRoot.altActive = !keyboardRoot.altActive }

            KeyButton {
                text: "Space"
                key: "space"
                flex: 4.5
                onClicked: dispatchChar(" ", "space")
            }

            KeyButton { text: "Alt"; key: "Alt_R"; flex: 1.0; isModifier: true; active: keyboardRoot.altActive; onClicked: keyboardRoot.altActive = !keyboardRoot.altActive }
            KeyButton { text: "Super"; key: "Super_R"; flex: 1.0; isModifier: true; active: keyboardRoot.superActive; onClicked: keyboardRoot.superActive = !keyboardRoot.superActive }
            KeyButton { text: "⌫"; key: "BackSpace"; flex: 1.2; isModifier: true; onClicked: dispatchBackspace() }
        }
    }

    // Key button delegate component
    component KeyButton: GlassSurface {
        id: btn
        property string text: ""
        property string key: ""
        property real flex: 1.0
        property bool isModifier: false
        property bool active: false
        property color accentColor: theme.accent
        signal clicked()

        readonly property real baseWidth: {
            const totalFlex = 13.0
            const availW = parent.width - (parent.children.length - 1) * (Glass.inset / 2)
            return (availW / totalFlex) * flex
        }

        width: Math.max(22, Math.round(baseWidth))
        height: parent.height
        radius: Glass.cornerFor(height)

        color: active
            ? theme.withAlpha(accentColor, theme.selectionOpacity)
            : hitArea.containsMouse
                ? theme.withAlpha(accentColor, theme.hoverOpacity)
                : theme.withAlpha(theme.bg, theme.panelOpacity)

        hairline: active || hitArea.containsMouse
        borderColor: active ? accentColor : theme.outlineColor

        Text {
            anchors.centerIn: parent
            text: btn.text
            color: btn.active ? btn.accentColor : theme.fg
            font.family: theme.fontFamily
            font.pixelSize: Math.max(10, Math.min(14, Math.round(btn.height * 0.42)))
            font.bold: btn.isModifier || btn.active
        }

        MouseArea {
            id: hitArea
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: btn.clicked()
        }
    }

    // Input dispatching logic
    function dispatchChar(charStr, xkbKey) {
        if (targetInput && (targetInput.activeFocus || targetInput.focus)) {
            targetInput.text = (targetInput.text || "") + charStr
            if (typeof targetInput.cursorPosition === "number")
                targetInput.cursorPosition = targetInput.text.length
        } else {
            sendHyprShortcut(buildMods(), xkbKey || charStr)
        }
        if (shiftActive && !capsActive) shiftActive = false
        clearTemporaryMods()
    }

    function dispatchKey(xkbKey, fallbackChar) {
        if (targetInput && (targetInput.activeFocus || targetInput.focus)) {
            if (fallbackChar) targetInput.text = (targetInput.text || "") + fallbackChar
        } else {
            sendHyprShortcut(buildMods(), xkbKey)
        }
        clearTemporaryMods()
    }

    function dispatchBackspace() {
        if (targetInput && (targetInput.activeFocus || targetInput.focus)) {
            if (targetInput.text && targetInput.text.length > 0) {
                targetInput.text = targetInput.text.slice(0, -1)
                if (typeof targetInput.cursorPosition === "number")
                    targetInput.cursorPosition = targetInput.text.length
            }
        } else {
            sendHyprShortcut(buildMods(), "BackSpace")
        }
    }

    function dispatchEnter() {
        if (targetInput && (targetInput.activeFocus || targetInput.focus)) {
            if (typeof targetInput.accepted === "function") targetInput.accepted()
        } else {
            sendHyprShortcut(buildMods(), "Return")
        }
        clearTemporaryMods()
    }

    function buildMods() {
        const m = []
        if (ctrlActive) m.push("CTRL")
        if (altActive) m.push("ALT")
        if (superActive) m.push("SUPER")
        if (shiftActive) m.push("SHIFT")
        return m.join(" ")
    }

    function clearTemporaryMods() {
        if (ctrlActive) ctrlActive = false
        if (altActive) altActive = false
        if (superActive) superActive = false
    }

    function sendHyprShortcut(mods, keyName) {
        const req = "sendshortcut " + (mods ? mods + "," : ",") + keyName + ","
        try {
            Hyprland.dispatch(req)
        } catch (e) {
            // Fallback command execution
            QS.Quickshell.execDetached(["hyprctl", "dispatch", "sendshortcut", (mods ? mods + "," : ",") + keyName + ","])
        }
    }

    // Automation / testing IPC endpoint
    IpcHandler {
        target: "virtualkeyboard"
        function press(k: string): bool {
            if (!k) return false
            if (k === "BackSpace" || k === "backspace") dispatchBackspace()
            else if (k === "Return" || k === "enter") dispatchEnter()
            else dispatchChar(k, k)
            return true
        }
        function state(): string {
            return JSON.stringify({
                visible: keyboardRoot.visible,
                width: keyboardRoot.width,
                height: keyboardRoot.height,
                shift: keyboardRoot.shiftActive,
                caps: keyboardRoot.capsActive,
                ctrl: keyboardRoot.ctrlActive,
                alt: keyboardRoot.altActive,
                super: keyboardRoot.superActive,
                symbols: keyboardRoot.symbolsActive,
                hasTarget: keyboardRoot.targetInput !== null
            })
        }
    }
}
