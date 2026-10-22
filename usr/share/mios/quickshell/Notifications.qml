// AI-hint: MiOS notifications: ONE glass blob per output that grows out of the status island. Every notification -- apps
// over org.freedesktop.Notifications (Config.qml NotificationServer; mios-shell owns the name) and the shell's own toasts
// (Shot.qml `shot toast`, the "notifications" IPC post) -- reaches the focused output over Config.qml's bus. The blob
// starts as the island itself (its drawn rectangle, radius and glass, ShellGeometry.drawnIslands, published by
// PanelWindow.qml; on a mobile output wherever the island sits between the window tabs, and it follows it) and grows on
// the island menus' morph (Emerge.qml, MeltPopup's gap below the bar, [desktop.notifications] morph_ms or [desktop.topbar]
// popup_ms) into the card centred under the island, clamped one global edge margin inside the output; the content fades
// in over the second half. More notifications grow the same blob downward into a stack ([desktop.notifications]
// max_visible; the oldest expires); a card leaving collapses out of it, the last one runs the morph backwards so the blob
// melts back into the island. Nothing of it appears anywhere else: with no island on the output it emerges from the
// island's place at the top centre (a bar-height dot). Over a fullscreen window the island is hidden (EdgeReveal.qml):
// the notification reveals the island alone, emerges once it is mapped, and the island hides again after the melt.
// Material: the island's (Glass.qml fill, no outline) on the glass-covered "mios-notifications" layer; hyprglass draws the
// mios preset behind it -- frost, tint, refraction, and the edge light (top specular, fresnel rim, bevel), which hyprglass
// lights on a layer's BOX. So the blob lives on two surfaces with fixed boxes, each mapped fresh with its box (a layer
// that changes size shows its old buffer scaled into the new box for a frame): "rest" -- the box is exactly the card /
// stack, the island's own edge light -- and "morph" -- the box holds the whole path of a change (island + stack while it
// grows out or melts back; old + new stack while a card grows in or collapses). A change maps the morph surface over the
// rest surface showing the same stack, then drops the rest surface, runs, and hands back the same way; notifications that
// arrive meanwhile wait for the change to settle. NotifModel asks Hyprland not to animate the layer (no_anim).
// Corner radius: the global rounding. Input: the blob alone (mask). Body click = the "default" action, one button per
// other action, the cross dismisses; image-data / image-path thumbnails; critical and resident notifications stay until
// dismissed; hovering pauses expiry; a replacement (replaces_id) updates its card in place and restarts its expiry;
// do-not-disturb shows critical ones only. Mobile: width clamped to the output minus the edge margins, touch-target
// ([desktop.hyprland.hyprbars.mobile] touch_target) hit areas, contents scaled by Mobile.uiScale like the island row.
// AI-related: usr/share/mios/quickshell/Config.qml, usr/share/mios/quickshell/NotifModel.qml,
// usr/share/mios/quickshell/Emerge.qml, usr/share/mios/quickshell/MeltPopup.qml, usr/share/mios/quickshell/PanelWindow.qml,
// usr/share/mios/quickshell/ShellGeometry.qml, usr/share/mios/quickshell/EdgeReveal.qml, usr/share/mios/quickshell/Glass.qml,
// usr/share/mios/quickshell/Mobile.qml, usr/share/mios/quickshell/Theme.qml, usr/share/mios/quickshell/Shot.qml,
// usr/share/mios/mios.toml [desktop.notifications] [desktop.topbar] [theme.shell], tests/test-notify-island.py

import QtQuick
import QtQuick.Layouts
import Quickshell as QS
import Quickshell.Hyprland
import Quickshell.Wayland

QS.Scope {
    id: root
    property var screen: null
    property QtObject theme: Theme {}
    // Config.qml's notification bus: posted(toast) for every server notification and shell toast.
    property QtObject bus: null
    // This output's fullscreen edge reveal (EdgeReveal.qml): reveals the island for the blob.
    property QtObject reveal: null
    readonly property string outputName: screen ? screen.name : ""

    // ---- the island this output's blob grows from ----------------------------------------------------------------------
    readonly property real ui: screen ? Mobile.uiScale(screen.name) : 1
    readonly property bool mobile: !!screen && Mobile.isMobile(screen.name)
    readonly property real margin: theme.edgeMargin * ui
    readonly property real outputWidth: screen ? screen.width : 0
    readonly property real outputHeight: screen ? screen.height : 0
    readonly property var drawn: ShellGeometry.drawnIslands[outputName] || null
    // The island can show for us: mapped now, or hidden under a fullscreen window and about to be revealed.
    readonly property bool islandRevealable: !!reveal && reveal.topActive
    readonly property bool islandUsable: !!drawn && drawn.height > 0 && (drawn.shown || islandRevealable)
    readonly property bool fallback: !islandUsable
    // The island rectangle (output-local px): the drawn one, or a bar-height dot at the island's place, top centre.
    readonly property rect islandRect: {
        if (!fallback) return Qt.rect(drawn.x, drawn.y, drawn.width, drawn.height)
        const h = Math.round(theme.topHeight * ui)
        return Qt.rect(Math.round((outputWidth - h) / 2), Math.round(theme.edgeMargin * ui), h, h)
    }
    readonly property real islandRadius: fallback ? Glass.cornerFor(theme.topHeight) * ui : drawn.radius
    // Card geometry: centred under the island, one MeltPopup gap below it, clamped inside the edge margins.
    readonly property real cardWidth: Math.max(1, Math.min(NotifModel.width * ui, outputWidth - 2 * margin))
    readonly property real gap: (theme.edgeMargin / 2) * ui
    readonly property real cardX: Math.max(margin, Math.min(islandRect.x + islandRect.width / 2 - cardWidth / 2,
                                                             outputWidth - margin - cardWidth))
    readonly property real cardY: islandRect.y + islandRect.height + gap
    readonly property real cardRadius: Glass.rounding * ui
    readonly property real maxStackHeight: Math.max(1, outputHeight - cardY - margin)
    // the stack as drawn now (cards growing in / collapsing out) and settled (every card full height)
    property real stackDrawn: 1
    property real stackTarget: 1
    readonly property real stackHeight: stackDrawn
    function restack() {
        let full = 0, drawn = 0
        for (let i = 0; i < stackRepeaterM.count; i++) {
            const it = stackRepeaterM.itemAt(i)
            if (it) {
                full += it.fullHeight
                drawn += it.fullHeight * it.appear
            }
        }
        stackTarget = Math.max(1, Math.min(maxStackHeight, full * ui))
        stackDrawn = Math.max(1, Math.min(maxStackHeight, drawn * ui))
    }
    readonly property rect stackRect: Qt.rect(cardX, cardY, cardWidth, stackHeight)
    readonly property rect finalRect: Qt.rect(cardX, cardY, cardWidth, stackTarget)

    // ---- notifications -------------------------------------------------------------------------------------------------
    // Cards shown on this output, oldest first ({uid} rows); each card's state is a CardLogic object in `store`.
    ListModel { id: cards }
    property var store: ({})
    property int serial: 0
    property var liveCards: []         // uids of cards not leaving
    property var pending: []           // notifications waiting for the current change to settle
    readonly property bool wanted: liveCards.length > 0 || pending.length > 0
    readonly property bool active: wanted || phase !== "idle" || cards.count > 0
    onActiveChanged: if (reveal) reveal.notifyPopup = active
    Component.onDestruction: { if (reveal) reveal.notifyPopup = false; NotifModel.unregister(root) }
    Component.onCompleted: NotifModel.register(root)

    function kindColor(kind) {
        if (kind === "success") return theme.success
        if (kind === "warning") return theme.warning
        if (kind === "error") return theme.error
        return theme.subtle   // info: the island's secondary glyph colour (accent is too dark on the glass)
    }
    function kindIcon(kind) {
        if (kind === "success") return "✔"
        if (kind === "warning") return "⚠"
        if (kind === "error") return "✖"
        return "ℹ"
    }

    function add(t) {
        // a notification already shown or waiting (a re-post of a carried-over one) keeps its card
        if (t.n) {
            for (const k in store) if (store[k].n === t.n) return
            for (const p of pending) if (p.n === t.n) return
        }
        pending = pending.concat([t])
        step()
    }
    function show(t, fresh) {
        const uid = ++serial
        const logic = cardLogic.createObject(root, { uid: uid, entry: Object.assign({ title: "", body: "", kind: "info", image: "",
                                                      actions: [], n: null, timeout: NotifModel.timeoutMs, critical: false }, t),
                                                      appear: fresh ? 1 : 0 })
        store[uid] = logic
        cards.append({ uid: uid })
        liveCards = liveCards.concat([uid])
        if (!fresh) logic.growIn.start()
    }
    function logicOf(uid) { return store[uid] || null }
    // A card starts leaving: no longer live (the change that removes it runs at the next step, or immediately if already changing).
    function leaving(uid) {
        liveCards = liveCards.filter(u => u !== uid)
        if (phase === "changing" && !handoff.running) {
            const l = store[uid]
            if (l && l.gone && !l.growOut.running) { l.growIn.stop(); l.growOut.start() }
            checkSettled()
        } else {
            step()
        }
    }
    function drop(uid) {
        for (let i = 0; i < cards.count; i++) if (cards.get(i).uid === uid) { cards.remove(i); break }
        const l = store[uid]
        delete store[uid]
        liveCards = liveCards.filter(u => u !== uid)
        if (l) l.destroy()
    }
    function dismissAll() {
        pending = []
        for (const k in store) store[k].dismiss()
    }

    Connections {
        target: root.bus
        function onPosted(t) { if (t && t.output === root.outputName) root.add(t) }
    }

    // ---- the change machine ------------------------------------------------------------------------------------------
    // idle -> opening (morph surface: island -> stack) -> rest (rest surface) -> changing (morph surface: the stack grows /
    // collapses) -> rest ... -> melting (morph surface: stack -> island) -> idle. Changes start only from idle or rest.
    property string phase: "idle"
    property bool waiting: false       // a notification waits for the island to be revealed
    readonly property bool islandReady: fallback || (drawn.shown === true)
    onIslandReadyChanged: step()
    property rect morphBox: Qt.rect(0, 0, 1, 1)
    property rect restBox: Qt.rect(0, 0, 1, 1)
    property rect restCard: Qt.rect(0, 0, 1, 1)    // the stack's rectangle when the rest surface was mapped
    property bool morphShown: false
    property bool restShown: false
    property bool morphOpen: false     // Emerge: grown out of the island
    function boxOf(rects) {
        let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9
        for (const r of rects) { x0 = Math.min(x0, r.x); y0 = Math.min(y0, r.y); x1 = Math.max(x1, r.x + r.width); y1 = Math.max(y1, r.y + r.height) }
        const bx = Math.max(0, Math.floor(x0)), by = Math.max(0, Math.floor(y0))
        return Qt.rect(bx, by, Math.max(1, Math.ceil(x1) - bx), Math.max(1, Math.ceil(y1) - by))
    }
    // the next change, when nothing runs
    function step() {
        if (phase === "idle") {
            if (!pending.length) return
            if (!islandReady && !forceOpen) { waiting = true; if (!revealTimeout.running) revealTimeout.start(); return }
            waiting = false
            revealTimeout.stop()
            // the newest max_visible open the blob; older ones expire unseen
            const take = pending; pending = []
            const keep = take.slice(Math.max(0, take.length - NotifModel.maxVisible))
            for (const t of take.slice(0, take.length - keep.length)) if (t.n) t.n.expire()
            for (const t of keep) show(t, true)
            Qt.callLater(beginOpening)
        } else if (phase === "rest") {
            if (!liveCards.length && !pending.length) { beginMelting(); return }
            const gone = Object.keys(store).some(k => store[k].gone)
            if (!pending.length && !gone) return
            beginChanging()
        }
    }
    // the stack keeps max_visible: the oldest expire
    function trim() {
        while (liveCards.length > NotifModel.maxVisible) {
            const l = store[liveCards[0]]
            if (l) l.expire(); else liveCards = liveCards.slice(1)
        }
    }
    // The island did not come (the reveal was turned off meanwhile): emerge from its place anyway.
    Timer {
        id: revealTimeout
        interval: Math.max(1, NotifModel.morphMs * 2)
        onTriggered: { root.waiting = false; root.forceOpen = true; root.step(); root.forceOpen = false }
    }
    property bool forceOpen: false

    function beginOpening() {
        restack()
        phase = "opening"
        morphBox = boxOf([islandRect, finalRect])
        blob.progress = 0.0001          // the blob AS the island, from its first frame
        morphOpen = false
        handoff.begin("morph", morphShown, () => { root.morphOpen = true })
        morphShown = true
        record("opening")
    }
    function beginChanging() {
        phase = "changing"
        const before = restBox
        const take = pending; pending = []
        for (const t of take) show(t, false)
        trim()
        // hold the new cards at zero height until the morph surface is up
        for (const k in store) if (store[k].appear < 1 && !store[k].gone) store[k].growIn.stop()
        Qt.callLater(() => {
            root.restack()
            root.morphBox = root.boxOf([before, root.finalRect, root.stackRect])
            handoff.begin("morph", root.morphShown, () => {
                root.restShown = false
                for (const k in root.store) {
                    const l = root.store[k]
                    if (l.gone) { l.growIn.stop(); l.growOut.start() } else if (l.appear < 1) l.growIn.start()
                }
                root.checkSettled()
            })
            root.morphShown = true
            root.record("changing")
        })
    }
    function beginMelting() {
        restack()
        phase = "melting"
        morphBox = boxOf([islandRect, finalRect, stackRect])
        handoff.begin("morph", morphShown, () => { root.restShown = false; root.morphOpen = false })
        morphShown = true
        record("melting")
    }
    // At rest the stack moved or resized (the mobile island moved between the window tabs, a replacement changed a card's
    // text): hand over to a fresh rest surface at the new place, once it holds still.
    onFinalRectChanged: if (phase === "rest") relayout.restart()
    Timer {
        id: relayout
        interval: Math.max(1, NotifModel.morphMs / 2)
        onTriggered: {
            const f = root.finalRect, c = root.restCard
            if (root.phase === "rest" && (Math.abs(f.x - c.x) > 0.5 || Math.abs(f.y - c.y) > 0.5 || Math.abs(f.width - c.width) > 0.5
                                          || Math.abs(f.height - c.height) > 0.5))
                root.beginChanging()
        }
    }
    // A change is over when every card stands at full height and the collapsed ones are gone.
    function checkSettled() {
        if (phase !== "changing" || handoff.running) return
        for (const k in store) {
            const l = store[k]
            if (l.gone && !l.growOut.running) { l.growIn.stop(); l.growOut.start() }
            if (l.gone || l.appear < 1) return
        }
        restack()
        if (!cards.count && !liveCards.length) { finishMelt(); return }
        toRest()
    }
    function toRest() {
        restack()
        restCard = finalRect
        restBox = boxOf([finalRect])
        handoff.begin("rest", restShown, () => {
            root.morphShown = false
            root.phase = "rest"
            root.record("rest")
            root.step()
        })
        restShown = true
    }
    // A surface mapped fresh is on screen once the compositor announces the layer (Hyprland's openlayer event) and has
    // drawn it a frame or two (hyprglass has its glass); only then may the surface it replaces go. A safety timeout covers
    // an event that never comes (another compositor).
    QtObject {
        id: handoff
        property string target: ""
        property var then: null
        property bool running: false
        property bool opened: false
        property bool swapped: false
        function begin(t, alreadyOpen, fn) {
            target = t; then = fn; running = true; opened = !!alreadyOpen; swapped = false; safety.restart()
        }
        function layer() { if (running && !opened) { opened = true; check() } }
        function swap(t) { if (running && t === target && !swapped) { swapped = true; check() } }
        function check() { if (opened && swapped && !settle.running) settle.restart() }
        function done() {
            if (!running) return
            running = false; safety.stop(); settle.stop()
            const fn = then; then = null
            if (fn) fn()
        }
    }
    Connections {
        target: Hyprland
        function onRawEvent(event) {
            if (event.name === "openlayer" && String(event.data).trim() === NotifModel.layerNamespace) handoff.layer()
        }
    }
    Timer { id: settle; interval: 34; onTriggered: handoff.done() }
    Timer { id: safety; interval: 500; onTriggered: handoff.done() }

    // ---- per-card state ------------------------------------------------------------------------------------------------
    Component {
        id: cardLogic
        QtObject {
            id: card
            property int uid: 0
            property var entry: ({})
            property var n: entry.n || null
            property string title: entry.title || ""
            property string body: entry.body || ""
            property string image: entry.image || ""
            property string kind: entry.kind || "info"
            property var actions: entry.n ? root.copyActions(entry.n) : (entry.actions || [])
            property bool gone: false
            // grown-in fraction of this card inside the blob (1 when it opened the blob itself)
            property real appear: 1
            property NumberAnimation growIn: NumberAnimation {
                target: card; property: "appear"; to: 1; duration: NotifModel.morphMs; easing.type: Easing.OutCubic
                onFinished: root.checkSettled()
            }
            property NumberAnimation growOut: NumberAnimation {
                target: card; property: "appear"; to: 0; duration: NotifModel.morphMs; easing.type: Easing.InCubic
                onFinished: { root.drop(card.uid); root.checkSettled() }
            }
            function leave() {
                if (gone) return
                gone = true
                root.leaving(uid)
            }
            function expire() { if (gone) return; const m = n; leave(); if (m) m.expire() }
            function dismiss() { if (gone) return; const m = n; leave(); if (m) m.dismiss() }
            function run(id) {
                if (!(actions || []).some(x => x && x.identifier === id)) return
                const m = n, own = (entry.actions || []).find(x => x && x.identifier === id)
                leave()
                // the notification's live action of that identifier (a replacement swaps the action objects; a closed
                // notification has none left); a shell toast's own callback otherwise
                try {
                    const live = m && m.actions ? m.actions.find(x => x && x.identifier === id) : null
                    if (live) live.invoke()
                    else if (!m && own && typeof own.invoke === "function") own.invoke()
                    // a resident notification stays open in the app's eyes; the card goes either way
                    if (m && m.resident) m.dismiss()
                } catch (e) { console.warn("Notifications: action", id, "failed:", e) }
            }
            // Expiry: the notification's own timeout (0 = until dismissed); runs while the blob is at rest, hovering pauses it.
            property Timer expiry: Timer {
                interval: Math.max(1000, card.entry.timeout || NotifModel.timeoutMs)
                running: (card.entry.timeout || 0) > 0 && !card.gone && root.phase === "rest" && !root.hovered
                onTriggered: card.expire()
            }
            // A replacement (replaces_id) updates this card in place and restarts its expiry.
            property Connections live: Connections {
                target: card.n
                ignoreUnknownSignals: true
                function onSummaryChanged() { card.title = card.n.summary || card.n.appName || ""; card.expiry.restart() }
                function onBodyChanged() { card.body = card.n.body || ""; card.expiry.restart() }
                function onImageChanged() { card.image = card.n.image || "" }
                function onActionsChanged() { card.actions = root.copyActions(card.n) }
                function onClosed() { card.leave() }
            }
        }
    }

    // ---- one card's view (drawn on both surfaces from the same state) --------------------------------------------------
    component CardView: Item {
        id: view
        required property int uid
        required property int index
        property bool interactive: true
        readonly property QtObject card: root.logicOf(uid)
        readonly property real appear: card ? card.appear : 0
        readonly property real pad: 2 * root.theme.edgeMargin
        readonly property real touch: root.mobile ? Glass.touchTarget : 0
        readonly property real fullHeight: content.implicitHeight + 2 * pad
        width: parent && parent.width > 0 ? parent.width : root.cardWidth / root.ui
        height: fullHeight * appear
        clip: true
        onFullHeightChanged: root.restack()
        onAppearChanged: root.restack()
        Component.onCompleted: root.restack()

        // hit rectangles (output-local px) for the IPC state and the tests
        function hits(origin) {
            function rect(it) {
                const p = it.mapToItem(null, 0, 0)   // window coordinates: the surface box's
                return { x: p.x + origin.x, y: p.y + origin.y, width: it.width * root.ui, height: it.height * root.ui }
            }
            const out = { card: rect(view), close: rect(closeHit), actions: [] }
            for (let i = 0; i < actionRepeater.count; i++) {
                const b = actionRepeater.itemAt(i)
                if (b) out.actions.push(Object.assign({ id: b.modelData.identifier }, rect(b)))
            }
            return out
        }

        // Clicking the body runs the "default" action (the buttons and the cross sit above it).
        MouseArea {
            anchors.fill: parent
            enabled: view.interactive && !!view.card && (view.card.actions || []).some(a => a && a.identifier === "default")
            cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
            onClicked: view.card.run("default")
        }
        // Hairline between stacked cards.
        Rectangle {
            visible: view.index > 0
            x: view.pad; width: parent.width - 2 * view.pad; height: 1
            color: root.theme.outlineColor
        }

        ColumnLayout {
            id: content
            x: view.pad; y: view.pad
            width: parent.width - 2 * view.pad
            spacing: root.theme.edgeMargin / 2
            opacity: Math.max(0, (view.appear - 0.5) * 2)

            RowLayout {
                Layout.fillWidth: true
                spacing: root.theme.edgeMargin
                Text {
                    text: root.kindIcon(view.card ? view.card.kind : "info")
                    color: root.kindColor(view.card ? view.card.kind : "info")
                    font.family: root.theme.fontFamily
                    font.pixelSize: root.theme.fontSize + 1
                    font.bold: true
                }
                Text {
                    text: view.card ? view.card.title : ""
                    textFormat: Text.PlainText   // app-supplied: never markup
                    color: root.theme.fg
                    font.family: root.theme.fontFamily
                    font.pixelSize: root.theme.fontSize
                    font.bold: true
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
                Item {
                    id: closeHit
                    // dismiss cross: a touch target on mobile
                    Layout.preferredWidth: Math.max(closeGlyph.implicitWidth + root.theme.edgeMargin, view.touch)
                    Layout.preferredHeight: Math.max(closeGlyph.implicitHeight, view.touch)
                    Text {
                        id: closeGlyph
                        anchors.centerIn: parent
                        text: "✕"
                        color: root.theme.subtle
                        font.family: root.theme.fontFamily
                        font.pixelSize: root.theme.smallFontSize
                    }
                    MouseArea {
                        anchors.fill: parent
                        enabled: view.interactive
                        cursorShape: Qt.PointingHandCursor
                        onClicked: view.card.dismiss()
                    }
                }
            }

            Image {
                visible: status === Image.Ready && NotifModel.imageMaxHeight > 0
                source: view.card ? view.card.image : ""
                asynchronous: true
                cache: false
                fillMode: Image.PreserveAspectFit
                Layout.fillWidth: true
                Layout.preferredHeight: visible ? Math.min(NotifModel.imageMaxHeight, width * implicitHeight / Math.max(1, implicitWidth)) : 0
                // decode at most twice the card's size, however large the app's image is
                sourceSize.width: 2 * NotifModel.width
                sourceSize.height: 2 * Math.max(1, NotifModel.imageMaxHeight)
            }

            Text {
                text: view.card ? view.card.body : ""
                visible: text !== ""
                textFormat: Text.PlainText
                color: root.theme.subtle
                font.family: root.theme.fontFamily
                font.pixelSize: root.theme.smallFontSize
                wrapMode: Text.Wrap
                maximumLineCount: NotifModel.bodyMaxLines
                elide: Text.ElideRight
                Layout.fillWidth: true
            }

            Flow {
                Layout.fillWidth: true
                spacing: root.theme.edgeMargin / 2
                visible: actionRepeater.count > 0
                Repeater {
                    id: actionRepeater
                    model: (view.card ? view.card.actions || [] : []).filter(a => a && a.identifier !== "default" && a.text).slice(0, NotifModel.actionsMax)
                    delegate: Rectangle {
                        id: actionButton
                        required property var modelData
                        width: Math.max(label.implicitWidth + 2 * root.theme.edgeMargin, view.touch)
                        height: Math.max(label.implicitHeight + root.theme.edgeMargin, view.touch)
                        radius: height / 2
                        color: am.containsMouse ? root.theme.withAlpha(root.theme.accent, root.theme.selectionOpacity)
                                                : root.theme.withAlpha(root.theme.subtle, root.theme.outlineOpacity)
                        Text {
                            id: label
                            anchors.centerIn: parent
                            text: actionButton.modelData.text
                            textFormat: Text.PlainText
                            color: root.theme.fg
                            font.family: root.theme.fontFamily
                            font.pixelSize: root.theme.smallFontSize
                        }
                        MouseArea {
                            id: am
                            anchors.fill: parent
                            enabled: view.interactive
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: view.card.run(actionButton.modelData.identifier)
                        }
                    }
                }
            }
        }
    }

    // ---- the surfaces --------------------------------------------------------------------------------------------------
    // Input region of a rounded rectangle (window px): two crossed rectangles and four corner circles -- the blob, not its
    // bounding box.
    component RoundedRegion: QS.Region {
        id: rr
        property real rx: 0
        property real ry: 0
        property real rw: 0
        property real rh: 0
        property real rad: 0
        readonly property int x0: Math.round(rx)
        readonly property int y0: Math.round(ry)
        readonly property int w0: Math.max(0, Math.round(rw))
        readonly property int h0: Math.max(0, Math.round(rh))
        readonly property int ir: Math.max(0, Math.floor(Math.min(rad, w0 / 2, h0 / 2)))
        QS.Region { x: rr.x0 + rr.ir; y: rr.y0; width: rr.w0 - 2 * rr.ir; height: rr.h0 }
        QS.Region { x: rr.x0; y: rr.y0 + rr.ir; width: rr.w0; height: rr.h0 - 2 * rr.ir }
        QS.Region { shape: QS.RegionShape.Ellipse; x: rr.x0; y: rr.y0; width: 2 * rr.ir; height: 2 * rr.ir }
        QS.Region { shape: QS.RegionShape.Ellipse; x: rr.x0 + rr.w0 - 2 * rr.ir; y: rr.y0; width: 2 * rr.ir; height: 2 * rr.ir }
        QS.Region { shape: QS.RegionShape.Ellipse; x: rr.x0; y: rr.y0 + rr.h0 - 2 * rr.ir; width: 2 * rr.ir; height: 2 * rr.ir }
        QS.Region { shape: QS.RegionShape.Ellipse; x: rr.x0 + rr.w0 - 2 * rr.ir; y: rr.y0 + rr.h0 - 2 * rr.ir; width: 2 * rr.ir; height: 2 * rr.ir }
    }
    readonly property bool hovered: morphHover.hovered || restHover.hovered
    component Surface: QS.PanelWindow {
        screen: root.screen
        WlrLayershell.namespace: NotifModel.layerNamespace
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
        // Placed from the output's top-left corner whatever reserves space there: the coordinates are the island's.
        anchors { top: true; left: true }
        exclusiveZone: 0
        exclusionMode: QS.ExclusionMode.Ignore
        color: "transparent"
    }

    // The morph surface: the blob while it changes (Emerge), its box the whole path of the change.
    Surface {
        id: morph
        margins.left: root.morphBox.x
        margins.top: root.morphBox.y
        implicitWidth: root.morphBox.width
        implicitHeight: root.morphBox.height
        visible: root.morphShown
        // input: the blob alone (nothing while it is hidden)
        mask: RoundedRegion {
            rx: blob.glass.x - root.morphBox.x
            ry: blob.glass.y - root.morphBox.y
            rw: blob.shown ? blob.glass.width : 0
            rh: blob.shown ? blob.glass.height : 0
            rad: blob.glass.radius
        }
        Item {
            id: morphContent
            x: -root.morphBox.x
            y: -root.morphBox.y
            width: root.outputWidth
            height: root.outputHeight
            Connections {
                target: morphContent.Window.window
                ignoreUnknownSignals: true
                function onFrameSwapped() { handoff.swap("morph") }
            }
            Emerge {
                id: blob
                open: root.morphOpen
                duration: NotifModel.morphMs
                fromRect: root.islandRect
                toRect: root.stackRect
                fromRadius: root.islandRadius
                toRadius: root.cardRadius
                // The island's material: the same fill over the same hyprglass layer pass, no outline (its rim is hyprglass').
                fill: Glass.fill
                fromFill: Glass.fill
                outline: "transparent"
                onProgressChanged: {
                    root.record()
                    if (root.phase === "opening" && open && progress >= 1) root.toRest()
                    else if (root.phase === "melting" && !open && progress <= 0) root.finishMelt()
                }
                Item {
                    width: root.cardWidth / root.ui
                    height: stackM.height
                    scale: root.ui
                    transformOrigin: Item.TopLeft
                    clip: true
                    HoverHandler { id: morphHover }
                    Column {
                        id: stackM
                        width: parent.width
                        Repeater {
                            id: stackRepeaterM
                            model: cards
                            onCountChanged: root.restack()
                            onItemRemoved: root.restack()
                            delegate: CardView { interactive: root.phase !== "rest" }
                        }
                    }
                }
            }
        }
    }

    // The rest surface: the card / stack at rest, its box exactly the glass (the island's edge light).
    Surface {
        id: rest
        margins.left: root.restBox.x
        margins.top: root.restBox.y
        implicitWidth: root.restBox.width
        implicitHeight: root.restBox.height
        visible: root.restShown
        mask: RoundedRegion { rx: restGlass.x; ry: restGlass.y; rw: restGlass.width; rh: restGlass.height; rad: restGlass.radius }
        Rectangle {
            id: restGlass
            x: root.restCard.x - root.restBox.x
            y: root.restCard.y - root.restBox.y
            width: root.restCard.width
            height: root.restCard.height
            radius: Math.min(root.cardRadius, width / 2, height / 2)
            color: Glass.fill
            clip: true
            Connections {
                target: restGlass.Window.window
                ignoreUnknownSignals: true
                function onFrameSwapped() { handoff.swap("rest") }
            }
            Item {
                width: root.restCard.width / root.ui
                height: stackR.height
                scale: root.ui
                transformOrigin: Item.TopLeft
                HoverHandler { id: restHover }
                Column {
                    id: stackR
                    width: parent.width
                    Repeater {
                        id: stackRepeaterR
                        model: cards
                        delegate: CardView { interactive: root.phase === "rest" }
                    }
                }
            }
        }
    }

    function finishMelt() {
        // melted back into the island: the stack is gone
        for (let i = cards.count - 1; i >= 0; i--) drop(cards.get(i).uid)
        morphShown = false
        restShown = false
        morphOpen = false
        phase = "idle"
        record("idle")
        step()
    }

    // The morph's last frames and the changes (IPC state "trace") for the tests.
    property var trace: []
    function record(event) {
        // the glass rectangle this progress draws (Emerge's own lerp; its bindings may not have run yet)
        const f = blob.fromRect, o = blob.toRect
        const w = Math.max(1, blob.lerp(f.width, o.width)), h = Math.max(1, blob.lerp(f.height, o.height))
        const x = blob.lerp(f.x, o.x), y = blob.lerp(f.y, o.y)
        const e = { t: Date.now(), p: Math.round(blob.progress * 10000) / 10000, x: x, y: y, w: w, h: h,
                    r: Math.min(blob.lerp(blob.fromRadius, blob.toRadius), w / 2, h / 2), open: blob.open, phase: phase,
                    box: [morphBox.x, morphBox.y, morphBox.width, morphBox.height] }
        if (event) e.event = event
        trace = (trace.length >= 200 ? trace.slice(trace.length - 199) : trace).concat([e])
    }

    // Plain copies ({identifier, text}) of a notification's actions: the NotificationAction objects die with it or with a
    // replacement, so a click looks the live one up by identifier (CardLogic.run).
    function copyActions(n) {
        try { return (n && n.actions ? n.actions : []).filter(a => !!a).map(a => ({ identifier: String(a.identifier), text: String(a.text || "") })) }
        catch (e) { return [] }
    }

    // Public API: notify("Title", "Body", "success")
    function notify(title, body, kind) { add({ title: title, body: body || "", kind: kind || "info" }) }

    // IPC state (Config.qml "notifications" target): geometry in output-local px.
    function snapshot() {
        function r(x) { return { x: x.x, y: x.y, width: x.width, height: x.height } }
        const atRest = phase === "rest"
        const rep = atRest ? stackRepeaterR : stackRepeaterM
        const origin = atRest ? restBox : morphBox
        const list = []
        for (let i = 0; i < rep.count; i++) {
            const it = rep.itemAt(i)
            const c = it ? it.card : null
            if (c) list.push({ uid: c.uid, title: c.title, body: c.body, kind: c.kind, critical: !!c.entry.critical,
                               timeout: c.entry.timeout, expire_timeout: c.entry.expire_timeout, leaving: c.gone, appear: c.appear,
                               image: c.image !== "", actions: (c.actions || []).map(a => a.identifier), height: it.height,
                               hits: it.hits(origin) })
        }
        const g = atRest ? { x: restBox.x + restGlass.x, y: restBox.y + restGlass.y, width: restGlass.width, height: restGlass.height,
                             radius: restGlass.radius, color: String(restGlass.color) }
                         : { x: blob.glass.x, y: blob.glass.y, width: blob.glass.width, height: blob.glass.height,
                             radius: blob.glass.radius, color: String(blob.glass.color) }
        return { output: outputName, mobile: mobile, ui: ui, phase: phase, active: active, wanted: wanted,
                 visible: morphShown || restShown, morph_shown: morphShown, rest_shown: restShown, waiting: waiting,
                 fallback: fallback, progress: blob.progress, shown: blob.shown || restShown,
                 namespace: NotifModel.layerNamespace, layer: "overlay",
                 box: r(atRest ? restBox : morphBox), morph_box: r(morphBox), rest_box: r(restBox),
                 surface: { width: atRest ? rest.width : morph.width, height: atRest ? rest.height : morph.height },
                 island: drawn, from: r(islandRect), from_radius: islandRadius,
                 to: r(finalRect), to_radius: cardRadius, glass: g,
                 card_width: cardWidth, gap: gap, hovered: hovered, pending: pending.length,
                 reveal: reveal ? { top_active: reveal.topActive, island_shown: reveal.islandShown, notify: reveal.notifyPopup } : null,
                 cards: list, live: liveCards.length, trace: trace }
    }
}
