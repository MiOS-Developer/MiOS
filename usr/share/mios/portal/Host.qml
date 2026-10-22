// AI-hint: Native MiOS Portal window. Standalone qml host (never inside Quickshell) that shows the
// agent-pipe Portal in a transparent window so the compositor glass (Hyprglass) shows behind the page.
// Every value arrives as key=value arguments after "--" from usr/bin/mios-portal, which reads
// mios.toml [desktop.portal], [ports] and [theme.web]; nothing here is a tunable.
// AI-related: usr/bin/mios-portal, usr/share/applications/mios-portal.desktop,
// usr/share/mios/wallpaper/Host.qml, usr/lib/mios/agent-pipe/mios_pipe/routing/portal.py
import QtQuick
import QtWebEngine

Window {
    id: root

    readonly property var opts: {
        const args = Qt.application.arguments;
        const out = {};
        for (let i = args.indexOf("--") + 1; i > 0 && i < args.length; i++) {
            const eq = args[i].indexOf("=");
            if (eq > 0)
                out[args[i].slice(0, eq)] = args[i].slice(eq + 1);
        }
        return out;
    }
    readonly property int pageAlpha: Math.max(0, Math.min(100, parseInt(opts.page_opacity_pct)))
    readonly property int surfaceAlpha: Math.max(0, Math.min(100, parseInt(opts.surface_opacity_pct)))

    // The page keeps its own palette; only its opaque surfaces become alpha-blended copies of the
    // same colours. The surface variables are read back from the page (whatever its theme resolved
    // them to) so no Portal colour is restated here. !important beats the page's inline <style>.
    readonly property string glassScript: "(function(){"
        + "var r=document.documentElement,cs=getComputedStyle(r),a=" + surfaceAlpha + ";"
        + "var css='html{background:transparent!important}"
        + "body{background-color:color-mix(in srgb,var(--bg) " + pageAlpha + "%,transparent)!important;background-attachment:scroll!important}"
        + ".bar{background:color-mix(in srgb,var(--bg) '+a+'%,transparent)!important}';"
        + "var d=['--card','--card2'].map(function(n){var v=cs.getPropertyValue(n).trim();"
        + "return v?n+':color-mix(in srgb,'+v+' '+a+'%,transparent)!important':'';}).filter(Boolean);"
        + "if(d.length)css+=':root{'+d.join(';')+'}';"
        + "var s=document.createElement('style');s.id='mios-portal-glass';s.textContent=css;"
        + "(document.head||r).appendChild(s);})();"

    title: opts.title || ""
    width: parseInt(opts.width)
    height: parseInt(opts.height)
    color: "transparent"
    // The Wayland app_id is "<reversed organization domain>.<executable name>". The launcher runs
    // the host as an executable named after [desktop.portal].app_id; clearing the domain the qml
    // runtime sets ("qt-project.org") before the first show makes the app_id exactly that name.
    visible: false
    Component.onCompleted: {
        Qt.application.domain = "";
        visible = true;
    }

    WebEngineView {
        id: view
        anchors.fill: parent
        backgroundColor: "transparent"
        url: root.opts.url || "about:blank"
        // Named, disk-backed profile (cookies persist by default): the Portal sign-in survives restarts.
        profile: WebEngineProfile {
            storageName: "mios-portal"
        }
        settings.playbackRequiresUserGesture: true
        userScripts.collection: [{
            name: "mios-portal-glass",
            injectionPoint: WebEngineScript.DocumentReady,
            worldId: WebEngineScript.ApplicationWorld,
            runsOnSubFrames: false,
            sourceCode: root.glassScript
        }]
        // Links that ask for a new window leave the Portal window for the default browser.
        onNewWindowRequested: request => Qt.openUrlExternally(request.requestedUrl)
        onTitleChanged: if (title.length) root.title = (root.opts.title ? root.opts.title + " — " : "") + title
    }
}
