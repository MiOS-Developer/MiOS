// AI-hint: MiOS glass greeter: a Quickshell greetd client (Quickshell.Services.Greetd) run fullscreen inside
// cage by usr/libexec/mios/mios-greeter-session, which falls back to tuigreet whenever this greeter fails to
// start, crashes, hangs before "ready", or the operator presses F10. Keyboard-only: Tab/Shift+Tab move
// focus, Enter signs in, Up/Down pick a user, Left/Right or F2 pick a session, F11 reboot, F12 power off
// (each asks for a second press), Esc cancels. Colours/font come from the shared theme bridge
// (/etc/mios/theme/theme.json via Theme.qml -> ../quickshell/Theme.qml); nothing here is a palette.
// Status protocol with the launcher (MIOS_GREETER_STATUS file): "ready" once the window is up,
// "launched" after greetd accepted start_session, "fallback" when the operator asks for text login.
// AI-related: usr/libexec/mios/mios-greeter-session, tools/native/mios-gen/src/greeter.rs,
//   usr/share/mios/mios.toml [desktop.greeter], tests/greeter/
import QtQuick
import QtQuick.Shapes
import Quickshell
import Quickshell.Io
import Quickshell.Services.Greetd

ShellRoot {
    id: shell

    function env(name, dflt) {
        const v = Quickshell.env(name)
        return (v === undefined || v === null || String(v).length === 0) ? dflt : String(v)
    }

    readonly property bool dryRun: env("MIOS_GREETER_DRY_RUN", "0") === "1"
    readonly property string statusPath: env("MIOS_GREETER_STATUS", "")
    readonly property string statePath: env("MIOS_GREETER_STATE", "/var/cache/mios-greeter/state.json")
    readonly property string wallpaper: env("MIOS_GREETER_WALLPAPER", "")
    readonly property var rebootCmd: env("MIOS_GREETER_REBOOT_CMD", "systemctl reboot").split(" ").filter(s => s.length)
    readonly property var poweroffCmd: env("MIOS_GREETER_POWEROFF_CMD", "systemctl poweroff").split(" ").filter(s => s.length)

    Theme { id: theme }

    Users {
        id: users
        passwd: shell.env("MIOS_GREETER_PASSWD", "/etc/passwd")
        minUid: parseInt(shell.env("MIOS_GREETER_MIN_UID", "1000"), 10)
        maxUid: parseInt(shell.env("MIOS_GREETER_MAX_UID", "59999"), 10)
    }

    Sessions {
        id: sessions
        dirs: shell.env("MIOS_GREETER_SESSION_DIRS", "/usr/share/wayland-sessions:/usr/share/xsessions").split(":").filter(s => s.length)
        preferred: shell.env("MIOS_GREETER_DEFAULT_SESSION", "hyprland")
        onListChanged: login.syncSession()
    }

    // ---- launcher status + remembered choice -------------------------------------------------
    FileView {
        id: statusFile
        path: shell.statusPath
        preload: false
        blockWrites: true
        atomicWrites: true
        printErrors: false
    }
    property string statusWord: ""
    function writeStatus(s) {
        statusWord = s
        if (shell.statusPath.length > 0) statusFile.setText(s + "\n")
    }
    // Heartbeat: the launcher falls back to tuigreet if "ready" stops being refreshed (hung greeter).
    Timer {
        interval: 5000
        repeat: true
        running: shell.statusWord === "ready"
        onTriggered: shell.writeStatus("ready")
    }

    FileView {
        id: stateFile
        path: shell.statePath
        preload: true
        blockWrites: true
        atomicWrites: true
        printErrors: false
        onLoaded: {
            try {
                const st = JSON.parse(text())
                if (st.user && login.username.length === 0) login.setUser(st.user)
                if (st.session && shell.env("MIOS_GREETER_BOOT_SESSION", "").length === 0) {
                    login.rememberedSession = st.session; login.syncSession()
                }
            } catch (e) { /* first boot or unreadable: defaults stand */ }
        }
    }
    // Carry the operator's last tuigreet user over on the first glass boot.
    FileView {
        path: shell.env("MIOS_GREETER_TUIGREET_CACHE", "/var/cache/tuigreet") + "/lastuser"
        preload: true
        printErrors: false
        onLoaded: if (login.username.length === 0) login.setUser(text().trim())
    }

    Process { id: power }

    // ---- greetd conversation -----------------------------------------------------------------
    QtObject {
        id: login
        property string username: ""
        property int sessionIndex: 0
        property string rememberedSession: ""
        property string phase: "credentials"     // credentials | prompt | launching
        property bool busy: false
        property string message: ""
        property bool messageIsError: false
        property string promptText: ""
        property bool promptEcho: false
        property string pendingSecret: ""
        property bool secretSent: false
        property string confirm: ""              // "" | reboot | poweroff

        readonly property var session: sessions.list.length > 0
            ? sessions.list[Math.max(0, Math.min(sessionIndex, sessions.list.length - 1))] : null

        function setUser(name) {
            if (!name || name.length === 0) return
            username = name
            if (ui.userField) ui.userField.text = name
            if (ui.passwordField) ui.passwordField.forceActiveFocus()
        }
        function syncSession() {
            const want = rememberedSession.length > 0 ? rememberedSession : sessions.preferred
            const i = sessions.indexOfId(want)
            sessionIndex = i >= 0 ? i : 0
        }
        function cycleSession(step) {
            const n = sessions.list.length
            if (n > 0) sessionIndex = (sessionIndex + step + n) % n
        }
        function cycleUser(step) {
            const n = users.list.length
            if (n === 0) return
            let i = users.indexOfName(ui.userField.text)
            i = i < 0 ? 0 : (i + step + n) % n
            ui.userField.text = users.list[i].name
        }
        function say(text, isError) { message = text; messageIsError = isError }
        function reset(keepMessage) {
            busy = false
            phase = "credentials"
            pendingSecret = ""
            secretSent = false
            promptText = ""
            if (!keepMessage) message = ""
        }

        function submit() {
            if (busy) return
            confirm = ""
            if (phase === "prompt") {
                busy = true
                Greetd.respond(ui.promptField.text)
                ui.promptField.text = ""
                return
            }
            const user = ui.userField.text.trim()
            if (user.length === 0) { say("Enter a user name.", true); ui.userField.forceActiveFocus(); return }
            if (!session) { say("No login sessions found in " + sessions.dirs.join(", ") + ". Press F10 for text login.", true); return }
            if (!Greetd.available) { say("greetd is not reachable (GREETD_SOCK). Press F10 for text login.", true); return }
            username = user
            pendingSecret = ui.passwordField.text
            secretSent = false
            busy = true
            say("Signing in…", false)
            Greetd.createSession(user)
        }

        function cancel() {
            confirm = ""
            if (Greetd.state !== GreetdState.Inactive) Greetd.cancelSession()
            reset(false)
            ui.passwordField.text = ""
            ui.passwordField.forceActiveFocus()
        }

        function launchEnv() {
            const s = session
            const desk = s.desktopNames.split(";").filter(x => x.length)
            const out = ["XDG_SESSION_TYPE=" + s.type, "XDG_SESSION_DESKTOP=" + s.id]
            if (desk.length > 0) out.push("XDG_CURRENT_DESKTOP=" + desk.join(":"))
            return out
        }

        function textLogin() {
            if (Greetd.state !== GreetdState.Inactive) Greetd.cancelSession()
            shell.writeStatus("fallback")
            Qt.quit()
        }

        function powerAction(kind) {
            if (confirm !== kind) {
                confirm = kind
                say(kind === "reboot" ? "Press F11 again (or Enter on Reboot) to restart. Esc cancels."
                                      : "Press F12 again (or Enter on Power off) to shut down. Esc cancels.", false)
                return
            }
            confirm = ""
            const cmd = kind === "reboot" ? shell.rebootCmd : shell.poweroffCmd
            // Test mode never powers the host off with the default commands; tests pass stub commands.
            const explicit = shell.env(kind === "reboot" ? "MIOS_GREETER_REBOOT_CMD" : "MIOS_GREETER_POWEROFF_CMD", "").length > 0
            if (shell.dryRun && !explicit) { say("Test mode: would run " + cmd.join(" "), false); return }
            say(kind === "reboot" ? "Restarting…" : "Shutting down…", false)
            power.command = cmd
            power.startDetached()
        }
    }

    Connections {
        target: Greetd
        function onAuthMessage(message, error, responseRequired, echoResponse) {
            if (!responseRequired) {
                // PAM info/error text (e.g. "account expires soon"); Quickshell answers it for us.
                login.say(message, error)
                return
            }
            if (!echoResponse && !login.secretSent) {
                login.secretSent = true
                const s = login.pendingSecret
                login.pendingSecret = ""
                Greetd.respond(s)
                return
            }
            // A further PAM question (one-time code, password change, ...): ask the operator.
            login.busy = false
            login.phase = "prompt"
            login.promptText = message
            login.promptEcho = echoResponse
            login.say("", false)
            ui.promptField.forceActiveFocus()
        }
        function onAuthFailure(message) {
            login.reset(true)
            const raw = message ? String(message) : ""
            const plain = raw.length === 0 || /AUTH_ERR|authentication failure|USER_UNKNOWN/i.test(raw)
            login.say(plain ? "Wrong password or unknown user \u201c" + login.username + "\u201d. Try again, or F10 for text login."
                            : "Sign-in failed for " + login.username + ": " + raw, true)
            ui.passwordField.text = ""
            ui.passwordField.forceActiveFocus()
        }
        function onReadyToLaunch() {
            login.phase = "launching"
            login.say("Starting " + login.session.name + "…", false)
            Greetd.launch(login.session.argv, login.launchEnv(), false)
        }
        function onLaunched() {
            stateFile.setText(JSON.stringify({ user: login.username, session: login.session.id }) + "\n")
            shell.writeStatus("launched")
            Qt.quit()
        }
        function onError(error) {
            login.reset(true)
            login.say("greetd error: " + error, true)
            ui.passwordField.forceActiveFocus()
        }
    }

    // Without a greetd socket this greeter cannot log anyone in: hand over to tuigreet.
    Timer {
        interval: 4000
        running: !Greetd.available && !shell.dryRun
        onTriggered: login.textLogin()
    }

    // ---- surface -----------------------------------------------------------------------------
    FloatingWindow {
        id: ui
        title: "MiOS Greeter"
        color: theme.bg
        implicitWidth: 1280
        implicitHeight: 800
        property alias userField: userField
        property alias passwordField: passwordField
        property alias promptField: promptField

        Component.onCompleted: readyTimer.start()
        Timer {
            id: readyTimer
            interval: 250
            onTriggered: {
                shell.writeStatus("ready")
                if (login.username.length > 0) passwordField.forceActiveFocus()
                else userField.forceActiveFocus()
            }
        }

        // Backdrop: theme gradient, optional wallpaper, soft glow behind the card.
        Rectangle {
            anchors.fill: parent
            gradient: Gradient {
                GradientStop { position: 0.0; color: theme.bg }
                GradientStop { position: 0.6; color: Qt.darker(theme.bg, 1.35) }
                GradientStop { position: 1.0; color: Qt.darker(theme.accent, 1.6) }
            }
        }
        Image {
            anchors.fill: parent
            visible: status === Image.Ready
            source: shell.wallpaper.length > 0 ? "file://" + shell.wallpaper : ""
            fillMode: Image.PreserveAspectCrop
            asynchronous: true
        }

        FocusScope {
            id: root
            anchors.fill: parent
            focus: true

            Keys.onPressed: event => {
                switch (event.key) {
                case Qt.Key_F2: login.cycleSession(event.modifiers & Qt.ShiftModifier ? -1 : 1); event.accepted = true; break
                case Qt.Key_F3: login.cycleUser(1); userField.forceActiveFocus(); event.accepted = true; break
                case Qt.Key_F10: login.textLogin(); event.accepted = true; break
                case Qt.Key_F11: login.powerAction("reboot"); event.accepted = true; break
                case Qt.Key_F12: login.powerAction("poweroff"); event.accepted = true; break
                case Qt.Key_Escape: login.cancel(); event.accepted = true; break
                }
            }

            // Clock
            Column {
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.top: parent.top
                anchors.topMargin: parent.height * 0.08
                spacing: 2
                Text {
                    id: clockText
                    anchors.horizontalCenter: parent.horizontalCenter
                    color: theme.fg
                    font.family: theme.fontFamily
                    font.pixelSize: theme.fontSize * 6
                    font.weight: Font.Light
                    text: Qt.formatTime(clock.now, "HH:mm")
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    color: theme.subtle
                    font.family: theme.fontFamily
                    font.pixelSize: theme.fontSize + 4
                    text: Qt.formatDate(clock.now, "dddd d MMMM yyyy")
                }
                QtObject { id: clock; property date now: new Date() }
                Timer { interval: 1000; running: true; repeat: true; onTriggered: clock.now = new Date() }
            }

            // Soft radial accent glow behind the card (cage has no compositor blur to frost against).
            Shape {
                id: glow
                width: card.width * 1.8; height: card.height * 1.8
                anchors.centerIn: card
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeWidth: -1
                    fillGradient: RadialGradient {
                        centerX: glow.width / 2; centerY: glow.height / 2
                        focalX: centerX; focalY: centerY
                        centerRadius: Math.max(glow.width, glow.height) / 2
                        GradientStop { position: 0.0; color: theme.withAlpha(theme.accent, 0.42) }
                        GradientStop { position: 0.55; color: theme.withAlpha(theme.accent, 0.14) }
                        GradientStop { position: 1.0; color: theme.withAlpha(theme.accent, 0.0) }
                    }
                    PathRectangle { x: 0; y: 0; width: glow.width; height: glow.height }
                }
            }

            // Glass card
            Rectangle {
                id: card
                anchors.centerIn: parent
                anchors.verticalCenterOffset: parent.height * 0.06
                width: Math.min(parent.width - 48, 460)
                height: form.implicitHeight + 64
                radius: Math.min(theme.radius, 28)
                color: theme.withAlpha(theme.bg, Math.max(theme.notificationOpacity - 0.25, 0.55))
                border.width: 1
                border.color: theme.outlineColor
                // Frost highlight along the top edge.
                Rectangle {
                    anchors.fill: parent
                    anchors.margins: 1
                    radius: parent.radius
                    gradient: Gradient {
                        GradientStop { position: 0.0; color: theme.withAlpha(theme.fg, 0.10) }
                        GradientStop { position: 0.35; color: theme.withAlpha(theme.fg, 0.02) }
                        GradientStop { position: 1.0; color: theme.withAlpha(theme.fg, 0.0) }
                    }
                }

                Column {
                    id: form
                    anchors.fill: parent
                    anchors.margins: 32
                    spacing: 16

                    Text {
                        text: "Sign in to MiOS"
                        color: theme.fg
                        font.family: theme.fontFamily
                        font.pixelSize: theme.titleFontSize + 6
                        font.bold: true
                    }

                    GlassField {
                        id: userField
                        theme: theme
                        width: parent.width
                        label: "User"
                        placeholder: "user name"
                        hint: users.list.length > 1 ? "↑↓ / F3 switch" : ""
                        visible: login.phase !== "prompt"
                        enabled: !login.busy
                        KeyNavigation.tab: passwordField
                        KeyNavigation.backtab: poweroffButton
                        Keys.onUpPressed: login.cycleUser(-1)
                        Keys.onDownPressed: login.cycleUser(1)
                        onAccepted: passwordField.forceActiveFocus()
                    }

                    GlassField {
                        id: passwordField
                        theme: theme
                        width: parent.width
                        label: "Password"
                        placeholder: "password"
                        secret: true
                        visible: login.phase !== "prompt"
                        enabled: !login.busy
                        KeyNavigation.tab: sessionRow
                        KeyNavigation.backtab: userField
                        onAccepted: login.submit()
                    }

                    GlassField {
                        id: promptField
                        theme: theme
                        width: parent.width
                        label: login.promptText
                        secret: !login.promptEcho
                        visible: login.phase === "prompt"
                        enabled: !login.busy
                        KeyNavigation.tab: loginButton
                        onAccepted: login.submit()
                    }

                    // Session chooser: Left/Right (or F2) cycle, Enter signs in.
                    Rectangle {
                        id: sessionRow
                        width: parent.width
                        height: Math.round(theme.fontSize * 3.4)
                        radius: Math.min(theme.radius, height / 2)
                        activeFocusOnTab: true
                        color: theme.withAlpha(theme.bg, activeFocus ? 0.72 : 0.4)
                        border.width: activeFocus ? 2 : 1
                        border.color: activeFocus ? theme.cursor : theme.outlineColor
                        visible: login.phase !== "prompt"
                        KeyNavigation.tab: loginButton
                        KeyNavigation.backtab: passwordField
                        Keys.onLeftPressed: login.cycleSession(-1)
                        Keys.onRightPressed: login.cycleSession(1)
                        Keys.onReturnPressed: login.submit()
                        Keys.onEnterPressed: login.submit()
                        Text {
                            anchors.left: parent.left; anchors.leftMargin: 18
                            anchors.verticalCenter: parent.verticalCenter
                            text: "‹"
                            color: sessionRow.activeFocus ? theme.cursor : theme.muted
                            font.pixelSize: theme.fontSize + 10
                        }
                        Column {
                            anchors.centerIn: parent
                            Text {
                                anchors.horizontalCenter: parent.horizontalCenter
                                text: login.session ? login.session.name : "No sessions found"
                                color: login.session ? theme.fg : theme.error
                                font.family: theme.fontFamily
                                font.pixelSize: theme.fontSize + 3
                                font.bold: true
                            }
                            Text {
                                anchors.horizontalCenter: parent.horizontalCenter
                                text: "Session " + (sessions.list.length ? (login.sessionIndex + 1) + "/" + sessions.list.length : "") + "  ·  F2"
                                color: theme.muted
                                font.family: theme.fontFamily
                                font.pixelSize: theme.fontSize - 1
                            }
                        }
                        Text {
                            anchors.right: parent.right; anchors.rightMargin: 18
                            anchors.verticalCenter: parent.verticalCenter
                            text: "›"
                            color: sessionRow.activeFocus ? theme.cursor : theme.muted
                            font.pixelSize: theme.fontSize + 10
                        }
                    }

                    GlassButton {
                        id: loginButton
                        theme: theme
                        width: parent.width
                        primary: true
                        text: login.busy ? "Signing in…" : (login.phase === "prompt" ? "Continue" : "Sign in")
                        keyHint: "Enter"
                        KeyNavigation.tab: textLoginButton
                        KeyNavigation.backtab: login.phase === "prompt" ? promptField : sessionRow
                        onActivated: login.submit()
                    }

                    Text {
                        id: messageText
                        width: parent.width
                        wrapMode: Text.Wrap
                        visible: text.length > 0
                        text: login.message
                        color: login.messageIsError ? theme.error : theme.subtle
                        font.family: theme.fontFamily
                        font.pixelSize: theme.fontSize + 1
                        font.bold: login.messageIsError
                    }
                }
            }

            // Bottom bar: key hints + text-login/power buttons.
            Row {
                anchors.left: parent.left
                anchors.bottom: parent.bottom
                anchors.margins: 24
                Text {
                    color: theme.withAlpha(theme.fg, 0.7)
                    font.family: theme.fontFamily
                    font.pixelSize: theme.fontSize
                    text: "Tab next  ·  Enter sign in  ·  ↑↓ user  ·  ←→/F2 session  ·  Esc cancel"
                        + (shell.dryRun ? "  ·  TEST MODE" : "")
                }
            }
            Row {
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.margins: 18
                spacing: 10
                GlassButton {
                    id: textLoginButton
                    theme: theme
                    text: "Text login"
                    keyHint: "F10"
                    KeyNavigation.tab: rebootButton
                    KeyNavigation.backtab: loginButton
                    onActivated: login.textLogin()
                }
                GlassButton {
                    id: rebootButton
                    theme: theme
                    text: login.confirm === "reboot" ? "Confirm reboot" : "Reboot"
                    keyHint: "F11"
                    danger: login.confirm === "reboot"
                    KeyNavigation.tab: poweroffButton
                    KeyNavigation.backtab: textLoginButton
                    onActivated: login.powerAction("reboot")
                }
                GlassButton {
                    id: poweroffButton
                    theme: theme
                    text: login.confirm === "poweroff" ? "Confirm power off" : "Power off"
                    keyHint: "F12"
                    danger: login.confirm === "poweroff"
                    KeyNavigation.tab: userField
                    KeyNavigation.backtab: rebootButton
                    onActivated: login.powerAction("poweroff")
                }
            }
        }
    }
}
