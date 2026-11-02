#!/usr/bin/env python3
# AI-hint: Contract tests for the dock HUD (no compositor needed): the expanded dock grows upward at the bar's width with
#          SSOT height/animation ([desktop.dock]); DockConfig's fallbacks equal the vendor SSOT; every [desktop.dock] key is
#          in the configurator; window previews capture only mapped windows on a live monitor and are flat (no borders,
#          margins or frames); app labels wrap/shrink and have a tooltip; the remote-device item lives in the topbar, not the
#          dock; the keyboard toggle state is user-tier. Each contract also runs against the pre-change tree from git
#          (NEG_REF, default main's merge-base) and must FAIL there: the negative control.
# AI-related: usr/share/mios/quickshell/Sidebar.qml, DockExpanded.qml, DockPreview.qml, DockConfig.qml, DockPopup.qml,
#             PanelWindow.qml, usr/share/mios/mios.toml [desktop.dock], usr/share/mios/configurator/mios.html
import os, re, subprocess, sys, tomllib, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QS = "usr/share/mios/quickshell"
NEG_REF = os.environ.get("NEG_REF")
if not NEG_REF:
    base = subprocess.run(["git", "-C", ROOT, "merge-base", "HEAD", "main"], capture_output=True, text=True).stdout.strip()
    chk = subprocess.run(["git", "-C", ROOT, "cat-file", "-e", f"{base}:{QS}/DockConfig.qml"], capture_output=True)
    NEG_REF = "436dd8de7" if chk.returncode == 0 else base



def read(rel, ref=None):
    if ref is None:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            return ""
        with open(p) as f:
            return f.read()
    r = subprocess.run(["git", "-C", ROOT, "show", f"{ref}:{rel}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def ssot(ref=None):
    t = read("usr/share/mios/mios.toml", ref)
    return tomllib.loads(t) if t else {}


# ---- contracts: each returns a list of problems ("" list = holds) ----------------------------------------------------
def grows_upward_at_bar_width(ref=None):
    s = read(f"{QS}/Sidebar.qml", ref)
    p = []
    if "drawerWidth - tiles.implicitWidth" in s or "drawerWidth" in s:
        p.append("dock width still morphs to the drawer width")
    if "DockConfig.expandedHeight(" not in s:
        p.append("expanded height not from [desktop.dock]")
    if "DockConfig.expandMs" not in s:
        p.append("expand animation not from [desktop.dock]")
    if not re.search(r"anchors\.bottom:\s*true", s):
        p.append("dock not anchored to the bottom edge (it must grow upward)")
    return p


def config_fallbacks_match_ssot(ref=None):
    d = ssot(ref).get("desktop", {}).get("dock")
    q = read(f"{QS}/DockConfig.qml", ref)
    if not d or not q:
        return ["no [desktop.dock] or DockConfig.qml"]
    p = []
    def fb(table, key):
        m = re.search(r'(?:num|bool|str)\(%s,\s*"%s",\s*("?[^,)"]*"?)\)' % (table, key), q)
        return m.group(1).strip('"') if m else None
    for key in ["expand_height_fraction", "expand_min_height_px", "expand_max_height_px", "expand_min_width_px", "expand_ms",
                "label_lines", "label_min_font_px", "tooltip_delay_ms"]:
        if str(fb("table", key)) != str(d[key]).lower():
            p.append(f"{key}: fallback {fb('table', key)} != SSOT {d[key]}")
    for sub, keys in {"previews": ["enabled", "include", "height_px", "live", "max"], "osk": ["enabled", "show", "height_px"],
                      "targets": ["enabled", "poll_ms"]}.items():
        for key in keys:
            if str(fb(sub, key)) != str(d[sub][key]).lower():
                p.append(f"{sub}.{key}: fallback {fb(sub, key)} != SSOT {d[sub][key]}")
    return p


def configurator_has_every_scalar(ref=None):
    d = ssot(ref).get("desktop", {}).get("dock")
    h = read("usr/share/mios/configurator/mios.html", ref)
    if not d:
        return ["no [desktop.dock]"]
    p = []
    def walk(t, prefix):
        for k, v in t.items():
            if isinstance(v, dict):
                walk(v, f"{prefix}.{k}")
            elif not isinstance(v, list) and k not in ("command", "layer", "namespace"):
                if f'data-key="{prefix}.{k}"' not in h:
                    p.append(f"{prefix}.{k} missing from the configurator")
    walk(d, "desktop.dock")
    return p


def previews_capture_safe_and_flat(ref=None):
    pv = read(f"{QS}/DockPreview.qml", ref)
    pop = read(f"{QS}/DockPopup.qml", ref)
    p = []
    if not pv:
        return ["no DockPreview.qml"]
    if not re.search(r"info\.monitor\s*>=\s*0", pv) or "info.mapped !== false" not in pv:
        p.append("preview may capture a window without a live monitor or unmapped")
    if "captureSource: preview.capturable ?" not in pv:
        p.append("capture source not gated on capturability")
    code = "\n".join(l for l in pv.splitlines() if not l.strip().startswith("//"))
    if "border." in code or "anchors.margins" in code:
        p.append("preview has a border or margins")
    card = pop[pop.find("id: winCard"):pop.find("id: cardMouse")] if "id: winCard" in pop else ""
    if "border.width" in card or re.search(r"anchors\.margins:\s*6", card):
        p.append("popup preview card still has a border/inset")
    if "DockPreview {" not in card:
        p.append("popup does not use the capture-safe DockPreview")
    raw = re.findall(r"ScreencopyView\s*\{[^}]*\}", pop)
    if any("phonePreview" not in r for r in raw):
        p.append("popup has an unguarded window ScreencopyView")
    return p


def labels_fit_and_tooltip(ref=None):
    ex = read(f"{QS}/DockExpanded.qml", ref)
    if not ex:
        return ["no DockExpanded.qml"]
    p = []
    for needle in ["maximumLineCount: DockConfig.labelLines", "fontSizeMode: Text.Fit", "minimumPixelSize: DockConfig.labelMinFont",
                   "wrapMode: Text.WrapAtWordBoundaryOrAnywhere", "tip.show(tile)", "tile.focused"]:
        if needle not in ex:
            p.append(f"label/tooltip contract missing: {needle}")
    return p


def remote_item_in_topbar_not_dock(ref=None):
    sb = read(f"{QS}/Sidebar.qml", ref)
    pw = read(f"{QS}/PanelWindow.qml", ref)
    p = []
    if "id: phoneTile" in sb:
        p.append("the remote-device tile is still in the dock")
    if 'shown("remote")' not in pw:
        p.append("no inbound remote item in the topbar")
    return p


def osk_toggle_user_tier(ref=None):
    sb = read(f"{QS}/Sidebar.qml", ref)
    p = []
    if "mios/dock-state.json" not in sb or "XDG_STATE_HOME" not in sb:
        p.append("keyboard toggle not persisted in the user state file")
    if '"dock", "osk"' not in sb:
        p.append("keyboard not launched through mios-gen dock osk")
    if "dock.expanded" not in sb[sb.find("id: oskProc"):sb.find("id: oskProc") + 600]:
        p.append("keyboard not tied to the expanded dock")
    return p


def shell_input_state_and_focus(ref=None):
    config = read(f"{QS}/Config.qml", ref)
    expanded = read(f"{QS}/DockExpanded.qml", ref)
    required = ["function inputSnapshot(closed)", "function inputPath(runtime, name)", "timestamp_ms: Date.now()",
                "input_state_file", "input_state_interval_ms", "Math.min(250", "atomicWrites: true",
                "Component.onDestruction: shellRoot.publishInputState(true)", "onDrawerOpenChanged:",
                "onSearchOpenChanged:", "onPathChanged:"]
    errors = [f"missing input-state contract: {item}" for item in required if item not in config]
    if "Qt.callLater(ex.focusSection)" not in expanded:
        errors.append("dock focus is not deferred until the expanded item is visible")
    if errors:
        return errors

    def method(text, name):
        start = text.index("function " + name + "(")
        brace = text.index("{", start)
        depth, end = 1, brace + 1
        while depth and end < len(text):
            depth += (text[end] == "{") - (text[end] == "}")
            end += 1
        return text[start:end]

    import json
    methods = json.dumps({name: method(text, name) for name, text in
                         [("inputSnapshot", config), ("inputPath", config), ("focusSection", expanded)]})
    script = r"""
const assert = require('assert'), vm = require('vm');
const methods = METHODS;
const ctx = vm.createContext({drawerOpen:false,searchOpen:false,drawerScreen:'LOCAL',searchScreen:'OTHER',Date:{now:()=>1000}});
const snapshot = vm.runInContext('('+methods.inputSnapshot+')',ctx);
assert.deepStrictEqual(JSON.parse(JSON.stringify(snapshot(false))),{version:1,open:false,surface:'',output:'',timestamp_ms:1000});
ctx.drawerOpen=true;
assert.equal(snapshot(false).surface,'drawer');assert.equal(snapshot(false).output,'LOCAL');
assert.equal(snapshot(true).open,false);assert.equal(snapshot(true).output,'');
ctx.drawerOpen=false;ctx.searchOpen=true;
assert.equal(snapshot(false).surface,'search');assert.equal(snapshot(false).output,'OTHER');
ctx.Date.now=()=>1250;assert.equal(snapshot(false).timestamp_ms,1250);
const path=vm.runInContext('('+methods.inputPath+')',ctx);
assert.equal(path('/run/user/1000','mios-shell-input.json'),'/run/user/1000/mios-shell-input.json');
for(const bad of ['../other.json','/tmp/other.json','x\ny.json','',null,{},'x.txt'])assert.equal(path('/run/user/1000',bad),'');
assert.equal(path('','mios-shell-input.json'),'');
let calls=[];
const focus=vm.createContext({open:false,visible:true,section:0,
 search:{forceActiveFocus(){calls.push('search');}},targetRow:{forceActiveFocus(){calls.push('targets');}},
 windowRow:{forceActiveFocus(){calls.push('windows');}},appGrid:{forceActiveFocus(){calls.push('apps');}}});
const focusSection=vm.runInContext('('+methods.focusSection+')',focus);
focusSection();assert.equal(calls.length,0);
focus.open=true;focus.visible=false;focusSection();assert.equal(calls.length,0);
focus.visible=true;
for(let i=0;i<4;i++){focus.section=i;focusSection();}
assert.deepStrictEqual(calls,['search','targets','windows','apps']);
""".replace("METHODS", methods)
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    if result.returncode:
        errors.append("input-state or deferred-focus behavior failed: " + result.stderr[-1200:])
    return errors


def dock_capture_feedback_and_keyboard(ref=None):
    config = read(f"{QS}/Config.qml", ref)
    sidebar = read(f"{QS}/Sidebar.qml", ref)
    required = ["id: captureGlow", "mask: QS.Region { width: 0; height: 0 }",
                "WlrLayershell.keyboardFocus: WlrKeyboardFocus.None", "exclusionMode: QS.ExclusionMode.Ignore",
                "function visibleWindowRects(monitor, viewportWidth, viewportHeight)", "ConicalGradient {",
                "angle: captureGlow.phase", "ShapePath.OddEvenFill", "Shape.CurveRenderer",
                "running: captureGlow.visible", "border_glow", "settings.width_px", "settings.period_ms",
                "id: outputScope", "screen: outputScope.modelData", "Hyprland.monitorFor(outputScope.modelData)",
                "workspaceState.isRemoteOutput(outputScope.outputName)", "/^HEADLESS[-_]/i"]
    errors = [f"missing capture feedback: {item}" for item in required if item not in config]
    glow = config[config.find("id: captureGlow"):]
    captured = re.search(r"readonly property bool capturedHere:([\s\S]*?)\n\s*visible:", glow)
    if not captured or "screen.name" in captured.group(1) or captured.group(1).count("outputScope.outputName") != 2:
        errors.append("capture visibility depends on mutable panel screen instead of stable output identity")
    focus = re.search(r"WlrLayershell.keyboardFocus:s*(.+)", sidebar)
    if not focus or "Exclusive" not in focus.group(1):
        errors.append("expanded dock cannot receive keyboard focus with the on-screen keyboard enabled")
    if errors:
        return errors

    start = config.index("function visibleWindowRects(")
    brace = config.index("{", start)
    depth, end = 1, brace + 1
    while depth and end < len(config):
        depth += (config[end] == "{") - (config[end] == "}")
        end += 1
    import json
    script = r"""
const assert=require('assert'),vm=require('vm');
const visible=vm.runInNewContext('('+METHOD+')');
const valid={address:'0x1',class:'Alacritty',at:[110,220],size:[200,100],workspace:{name:'1'}};
const windows=[valid,
 {...valid,address:'hidden',hidden:true},{...valid,address:'unmapped',mapped:false},
 {...valid,address:'special',workspace:{name:'special:minimized'}},
 {...valid,address:'keyboard',class:'phosh-osk-stevia'},{...valid,address:'shell',class:'quickshell'},
 {...valid,address:'outside',at:[10000,20000]},{...valid,address:'zero',size:[0,10]},
 {...valid,address:'bad',size:['invalid',10]}].map(lastIpcObject=>({lastIpcObject}));
const monitor={x:100,y:200,activeWorkspace:{toplevels:{values:windows}}};
const result=JSON.parse(JSON.stringify(visible(monitor,1920,1080)));
assert.deepStrictEqual(result,[{address:'0x1',x:10,y:20,width:200,height:100}]);
assert.deepStrictEqual(Array.from(visible(null,1920,1080)),[]);
const ctx=vm.createContext({expanded:true,root:{oskWanted:true},WlrKeyboardFocus:{Exclusive:'exclusive',None:'none'}});
assert.equal(vm.runInContext(FOCUS,ctx),'exclusive');
ctx.expanded=false;assert.equal(vm.runInContext(FOCUS,ctx),'none');
""".replace("METHOD", json.dumps(config[start:end])).replace("FOCUS", json.dumps(focus.group(1)))
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    if result.returncode:
        errors.append("window filtering or keyboard attachment failed: " + result.stderr[-1200:])
    return errors


def shell_input_attachment_lifecycle(ref=None):
    search = read(f"{QS}/Search.qml", ref)
    menu = read(f"{QS}/AppMenu.qml", ref)
    errors = []
    if re.search(r"^    Keys\.onEscapePressed:", search, re.M):
        errors.append("Search attaches Keys to its non-Item panel window")
    if "            Keys.onEscapePressed: searchWindow.shellState.searchOpen = false" not in search:
        errors.append("Search input lost its Escape handler")
    output = re.search(r"readonly property var outputScreen:\s*(.+)", menu)
    visible = re.search(r"^    visible:\s*(.+)", menu, re.M)
    if not output or not visible or "outputScreen" not in visible.group(1) or "screen.name" in visible.group(1):
        errors.append("AppMenu visibility depends on mutable panel screen")
    if errors:
        return errors
    import json
    script = r"""
const assert=require('assert'),vm=require('vm');
const ctx=vm.createContext({reveal:{screen:{name:'LOCAL',width:1920},topShown:false},
 appName:'Terminal',bar:{app_menu:true},shellState:{topbarShown:()=>true},overFullscreen:false,
 Mobile:{isMobile:()=>false}});
Object.defineProperty(ctx,'screen',{get(){throw Error('mutable window screen evaluated');}});
ctx.outputScreen=vm.runInContext(OUTPUT,ctx);assert.equal(ctx.outputScreen.name,'LOCAL');
const shown=()=>vm.runInContext(VISIBLE,ctx);
assert.equal(shown(),true);ctx.bar.app_menu=false;assert.equal(shown(),false);ctx.bar.app_menu=true;
ctx.Mobile.isMobile=()=>true;assert.equal(shown(),false);ctx.Mobile.isMobile=()=>false;
ctx.overFullscreen=true;assert.equal(shown(),false);ctx.reveal.topShown=true;assert.equal(shown(),true);
ctx.appName='';assert.equal(shown(),false);
""".replace("OUTPUT", json.dumps(output.group(1))).replace("VISIBLE", json.dumps(visible.group(1)))
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    if result.returncode:
        errors.append("AppMenu lifecycle-independent visibility failed: " + result.stderr[-1200:])
    return errors


def native_room_capture_feedback(ref=None):
    config = read(f"{QS}/Config.qml", ref)
    required = ["function feedbackSpec(", "function feedbackLua(", "function feedbackReply(",
                "function flatFeedbackAllowed(", "function queueNativeFeedback(", "function pumpNativeFeedback(",
                "function finishNativeFeedback(", "function timeoutNativeFeedback(", "id: captureFeedback",
                "running: shellRoot.feedbackBusy", "captureFeedback.signal(9)", "dock_capture_feedback",
                "shellRoot.flatFeedbackAllowed(shellRoot.feedbackNow)", "queueNativeFeedback(snapshot)",
                "Component.onDestruction: shellRoot.publishInputState(true)", "id: feedbackExpiry", "feedbackExpiry.restart()"]
    errors = [f"native room feedback missing: {item}" for item in required if item not in config]
    if "on_state" in config:
        errors.append("native feedback must not replace the compositor's state callback")
    glow = ssot(ref).get("desktop", {}).get("dock", {}).get("capture", {}).get("border_glow", {})
    lease = glow.get("lease_ms")
    if not isinstance(lease, int) or isinstance(lease, bool) or not 100 <= lease <= 10000:
        errors.append("native room feedback lease is not bounded in the SSOT")
    html = read("usr/share/mios/configurator/mios.html", ref)
    lease_input = re.search(r'<input[^>]*data-key="desktop\.dock\.capture\.border_glow\.lease_ms"[^>]*>', html)
    if not lease_input or 'min="100"' not in lease_input[0] or 'max="10000"' not in lease_input[0]:
        errors.append("native room feedback lease configurator bounds missing")
    if errors:
        return errors

    def method(name):
        start = config.index("function " + name + "(")
        brace = config.index("{", start)
        depth, end = 1, brace + 1
        while depth and end < len(config):
            depth += (config[end] == "{") - (config[end] == "}")
            end += 1
        return config[start:end]

    import json
    names = ["feedbackSpec", "feedbackLua", "feedbackReply", "flatFeedbackAllowed",
             "queueNativeFeedback", "pumpNativeFeedback", "finishNativeFeedback", "timeoutNativeFeedback"]
    methods = json.dumps({name: method(name) for name in names})
    script = r"""
const assert=require('assert'),vm=require('vm');
const methods=METHODS;
let now=100, callbacks=[],commands=[],killed=0,expiryRestarts=0;
const ctx=vm.createContext({
 feedbackSettings:{enabled:true,color:'#F35C15',width_px:4,period_ms:2400},feedbackLeaseMs:1000,
 feedbackDesired:'',feedbackQueued:'',feedbackPending:'',feedbackBusy:false,feedbackConfirmed:false,
 feedbackRoom:false,feedbackSequence:0,feedbackAcceptedAt:0,feedbackNow:0,
 Date:{now:()=>now},Qt:{callLater(fn){callbacks.push(fn);}},feedbackExpiry:{restart(){expiryRestarts++;}},
 captureFeedback:{running:false,reply:'',expired:false,signal(sig){assert.equal(sig,9);killed++;this.running=false;}}
});
Object.defineProperty(ctx.captureFeedback,'command',{set(argv){commands.push(argv);this.argv=argv;},get(){return this.argv;}});
for(const [name,source] of Object.entries(methods))ctx[name]=vm.runInContext('('+source+')',ctx);
ctx.shellRoot=ctx;
const open=output=>({open:true,output});
const pump=()=>{const pending=callbacks;callbacks=[];for(const fn of pending)fn();};
const complete=reply=>{ctx.captureFeedback.running=false;ctx.captureFeedback.reply=reply;ctx.finishNativeFeedback();pump();};
const reply=(mode,id=ctx.feedbackSequence)=>'MIOS_DOCK_CAPTURE_FEEDBACK:'+id+':'+mode;
assert.equal(ctx.flatFeedbackAllowed(now),false,'initial room status is unknown');
ctx.queueNativeFeedback(open('A'));
assert.equal(commands.length,1);assert.equal(ctx.feedbackBusy,true);
assert.equal(ctx.flatFeedbackAllowed(now),false,'opening cannot flash flat rectangles before a reply');
// Heartbeats coalesce and the newest close replaces every pending active request.
ctx.queueNativeFeedback(open('A'));ctx.queueNativeFeedback(open('B'));ctx.queueNativeFeedback({open:false,output:''});
assert.equal(commands.length,1);assert.equal(JSON.parse(ctx.feedbackQueued).active,false);
complete(reply('flat',1));
assert.equal(ctx.feedbackConfirmed,false,'old active reply cannot acknowledge a newer close');
assert.equal(commands.length,2);assert.equal(JSON.parse(ctx.feedbackPending).active,false);
complete(reply('room',2));assert.equal(ctx.flatFeedbackAllowed(now),false);
ctx.queueNativeFeedback(open('C'));complete(reply('flat'));
assert.equal(ctx.flatFeedbackAllowed(now),true,'fresh native 2D confirmation enables the flat fallback');
assert.equal(expiryRestarts,1,'native confirmation must arm its own expiry even when heartbeat is slower than lease');
const accepted=now;now+=1000;assert.equal(ctx.flatFeedbackAllowed(now),false,'confirmation expires with the native lease');
now=accepted-1;assert.equal(ctx.flatFeedbackAllowed(now),false,'clock rollback is not a fresh reply');
now=accepted+1;ctx.queueNativeFeedback(open('C'));complete(reply('room'));
assert.equal(ctx.flatFeedbackAllowed(now),false,'room scene and morph never use flat geometry');
ctx.queueNativeFeedback(open('C'));complete(reply('flat'));
assert.equal(ctx.flatFeedbackAllowed(now),true);
ctx.queueNativeFeedback(open('C'));ctx.timeoutNativeFeedback();
assert.equal(killed,1);assert.equal(ctx.flatFeedbackAllowed(now),false,'timeout revokes prior flat confirmation');
complete(reply('flat'));assert.equal(ctx.feedbackConfirmed,false,'late timed-out reply cannot resurrect feedback');
// Failed process creation may never emit exited; the deadline still releases the slot and keeps only the newest close.
ctx.queueNativeFeedback(open('D'));ctx.captureFeedback.running=false;
ctx.queueNativeFeedback({open:false,output:''});ctx.timeoutNativeFeedback();pump();
assert.equal(JSON.parse(ctx.feedbackPending).active,false);assert.equal(ctx.feedbackBusy,true);
complete(reply('flat'));assert.equal(ctx.feedbackConfirmed,false);
ctx.queueNativeFeedback(open('E'));complete(reply('flat',ctx.feedbackSequence-1));
assert.equal(ctx.feedbackConfirmed,false,'wrong request marker is stale');
ctx.queueNativeFeedback(open('E'));complete('not a native reply');
assert.equal(ctx.feedbackConfirmed,false);
assert.equal(ctx.feedbackReply(reply('flat')+'\n'+reply('room'),ctx.feedbackSequence),'','ambiguous reply rejected');
assert.equal(ctx.feedbackReply('x'.repeat(8193),ctx.feedbackSequence),'','unbounded reply rejected');
const valid=ctx.feedbackSpec(open('HDMI-"quoted\\output'));
assert.equal(valid.active,true);assert.equal(valid.lease_ms,1000);
for(const bad of ['', 'bad\noutput', 'bad\0output'])assert.equal(ctx.feedbackSpec(open(bad)).active,false);
for(const [key,bad] of [['width_px',0],['width_px',33],['width_px',NaN],['period_ms',99],['period_ms',20001],['period_ms',100.5],['color','red'],['color','#12345']]){
 const old=ctx.feedbackSettings[key];ctx.feedbackSettings[key]=bad;
 assert.equal(ctx.feedbackSpec(open('C')).active,false,key+' must be validated');
 ctx.feedbackSettings[key]=old;
}
ctx.feedbackLeaseMs=0;assert.equal(ctx.feedbackSpec(open('C')).active,false);ctx.feedbackLeaseMs=1000;
ctx.feedbackSettings.color='#F35C157F';assert.equal(ctx.feedbackSpec(open('C')).active,true);
const query=ctx.feedbackLua(JSON.stringify(valid),7),off=ctx.feedbackLua(JSON.stringify({active:false}),8);
assert.equal(ctx.feedbackReply(query,7),'','echoed request expression is not a reply marker');
console.log(JSON.stringify({query,off,output:valid.output}));
""".replace("METHODS", methods)
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    if result.returncode:
        return ["native feedback state-machine behavior failed: " + result.stderr[-1600:]]
    output = json.loads(result.stdout)
    import shutil
    lua = shutil.which("luajit") or shutil.which("lua")
    if not lua:
        return ["native feedback Lua verification requires a Lua interpreter"]
    cases = [
        ("native room", "hl={plugin={hypr3d={dock_capture_feedback=function(c) assert(c.active and c.output==OUTPUT and c.width_px==4 and c.period_ms==2400 and c.lease_ms==1000);return true end}}}", output["query"], "7:room"),
        ("native 2D", "hl={plugin={hypr3d={dock_capture_feedback=function(c)return false end}}}", output["query"], "7:flat"),
        ("absent plugin", "hl={plugin={}}", output["query"], "7:flat"),
        ("old plugin 2D", "hl={plugin={hypr3d={active=function()return false end}}}", output["query"], "7:flat"),
        ("old plugin room", "hl={plugin={hypr3d={active=function()return true end}}}", output["query"], "7:room"),
        ("old plugin unknown", "hl={plugin={hypr3d={}}}", output["query"], None),
        ("API failed", "hl={plugin={hypr3d={dock_capture_feedback=function()error('refused')end,active=function()return false end}}}", output["query"], None),
        ("API malformed", "hl={plugin={hypr3d={dock_capture_feedback=function()return 'false' end}}}", output["query"], None),
        ("cleanup", "hl={plugin={hypr3d={dock_capture_feedback=function(c)assert(c.active==false and c.output==nil and c.color==nil);return true end}}}", output["off"], "8:room"),
    ]
    for name, setup, query, expected in cases:
        body = setup.replace("OUTPUT", json.dumps(output["output"])) + "\nlocal ok,reply=pcall(function() " + query + " end)\nassert(not ok)\n"
        body += ("assert(reply==" + json.dumps("MIOS_DOCK_CAPTURE_FEEDBACK:" + expected) + ")"
                 if expected else "assert(not tostring(reply):find('MIOS_DOCK_CAPTURE_FEEDBACK:',1,true))")
        result = subprocess.run([lua, "-"], input=body, capture_output=True, text=True, timeout=5)
        if result.returncode:
            errors.append("native feedback Lua case " + name + " failed: " + result.stderr[-800:])
    return errors


CONTRACTS = [grows_upward_at_bar_width, config_fallbacks_match_ssot, configurator_has_every_scalar, previews_capture_safe_and_flat,
             labels_fit_and_tooltip, remote_item_in_topbar_not_dock, osk_toggle_user_tier, shell_input_state_and_focus, dock_capture_feedback_and_keyboard, shell_input_attachment_lifecycle, native_room_capture_feedback]


class Positive(unittest.TestCase):
    pass


class Negative(unittest.TestCase):
    pass


for c in CONTRACTS:
    setattr(Positive, f"test_{c.__name__}", lambda self, c=c: self.assertEqual(c(), [], c.__name__))
    setattr(Negative, f"test_{c.__name__}_fails_before", lambda self, c=c: self.assertNotEqual(c(NEG_REF), [], f"{c.__name__} already held at {NEG_REF}"))


if __name__ == "__main__":
    if not NEG_REF:
        sys.exit("NEG_REF unresolved")
    unittest.main(verbosity=2)
