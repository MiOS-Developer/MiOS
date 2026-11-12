# AI-hint: Portal STATIC ASSETS -- the SVG/PNG icons, PWA web manifest, service worker, sign-in page, the status & goals view script and the iOS embed test page, split out of portal.py.
# AI-related: usr/lib/mios/agent-pipe/mios_pipe/routing/portal.py, usr/lib/mios/agent-pipe/test_mios_portal.py, usr/share/mios/portal, usr/lib/mios/agent-pipe/mios_pipe/portal_status.py
# AI-doc: usr/share/doc/mios/manual/routing.md

from __future__ import annotations

import json
import os

_PORTAL_ICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
                '<rect width="512" height="512" rx="104" fill="#282262"/>'
                '<path d="M48 372 q68 -86 136 0 t136 0 t144 0" stroke="#F35C15"'
                ' stroke-width="26" fill="none" stroke-linecap="round"/>'
                '<text x="256" y="250" font-family="system-ui,Segoe UI,sans-serif"'
                ' font-size="208" font-weight="700" fill="#E7DFD3"'
                ' text-anchor="middle">Mi</text></svg>')

def _read_portal_asset(name: str) -> bytes:
    """Read a baked portal asset (PNG icons) from /usr/share/mios/portal."""
    try:
        with open(os.path.join("/usr/share/mios/portal", name), "rb") as f:
            return f.read()
    except OSError:
        return b""

_PORTAL_ICON_192 = _read_portal_asset("icon-192.png")
_PORTAL_ICON_512 = _read_portal_asset("icon-512.png")
_PORTAL_MANIFEST = json.dumps({
    "id": "/", "name": "MiOS Portal", "short_name": "MiOS",
    "start_url": "/", "scope": "/", "display": "standalone",
    "orientation": "any", "background_color": "#282262",
    "theme_color": "#282262", "description": "MiOS service portal",
    "icons": [
        {"src": "/portal/icon-192.png", "sizes": "192x192",
         "type": "image/png", "purpose": "any"},
        {"src": "/portal/icon-512.png", "sizes": "512x512",
         "type": "image/png", "purpose": "any"},
        {"src": "/portal/icon-512.png", "sizes": "512x512",
         "type": "image/png", "purpose": "maskable"},
        {"src": "/portal/icon.svg", "sizes": "any", "type": "image/svg+xml"},
    ],
})
_PORTAL_SW = (
    "var C='mios-portal-v19';\n"
    "var SHELL=['/login','/portal/icon.svg','/portal/icon-192.png',"
    "'/portal/icon-512.png','/portal/manifest.webmanifest'];\n"
    "self.addEventListener('install',function(e){self.skipWaiting();"
    "e.waitUntil(caches.open(C).then(function(c){return c.addAll(SHELL);})"
    ".catch(function(){}));});\n"
    "self.addEventListener('activate',function(e){e.waitUntil("
    "caches.keys().then(function(ks){return Promise.all(ks.map(function(k){"
    "return k===C?null:caches.delete(k);}));}).then(function(){"
    "return self.clients.claim();}));});\n"
    "// Navigations are ALWAYS network (never cached) so the portal HTML can\n"
    "// never go stale in an installed PWA; static assets are cached for\n"
    "// offline + installability; /portal/status* is live data, never cached.\n"
    "self.addEventListener('fetch',function(e){var req=e.request;"
    "if(req.method!=='GET'||new URL(req.url).pathname.indexOf('/portal/status')===0)return;"
    "if(req.mode==='navigate'){"
    "e.respondWith(fetch(req).catch(function(){return caches.match('/login');}));return;}"
    "e.respondWith(fetch(req).then(function(r){"
    "if(r&&r.status===200&&!r.redirected&&"
    "new URL(req.url).origin===location.origin){var cp=r.clone();"
    "caches.open(C).then(function(c){c.put(req,cp);});}return r;})"
    ".catch(function(){return caches.match(req);}));});\n")

_PORTAL_LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>MiOS &middot; Sign in</title>
<meta name="theme-color" content="#282262">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="MiOS">
<link rel="manifest" href="/portal/manifest.webmanifest">
<link rel="icon" href="/portal/icon.svg">
<link rel="icon" type="image/png" sizes="192x192" href="/portal/icon-192.png">
<link rel="apple-touch-icon" href="/portal/icon-192.png">
<style>
:root{--bg:#282262;--panel:#1A407F;--fg:#E7DFD3;--mut:#B7C9D7;--accent:#F35C15;
--ok:#3E7765;--bad:#DC271B;--info:#3D6BA8;--warn:#FF8540;
--card:color-mix(in srgb,var(--panel) 24%,var(--bg));
--line:color-mix(in srgb,var(--mut) 24%,transparent);
--sans:-apple-system,"Segoe UI",system-ui,Roboto,sans-serif}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;color:var(--fg);font:15px/1.5 var(--sans);
padding:calc(20px + env(safe-area-inset-top)) calc(20px + env(safe-area-inset-right)) calc(20px + env(safe-area-inset-bottom)) calc(20px + env(safe-area-inset-left));
background:radial-gradient(1000px 500px at 15% -10%, color-mix(in srgb,var(--accent) 14%,transparent),transparent 60%), radial-gradient(900px 520px at 100% 0%, color-mix(in srgb,var(--panel) 32%,transparent),transparent 55%), radial-gradient(820px 520px at 50% 118%, color-mix(in srgb,var(--info) 18%,transparent),transparent 60%),var(--bg)}
form{background:var(--card);border:1px solid var(--line);border-radius:16px;
padding:30px 28px;width:min(360px,100%);box-shadow:0 18px 50px rgba(0,0,0,.5)}
.brand{font-size:34px;font-weight:700;letter-spacing:.5px;text-align:center}
.brand b{color:var(--accent)}
.sub{text-align:center;color:var(--mut);font-size:12.5px;letter-spacing:2px;
text-transform:uppercase;margin:2px 0 22px}
input{width:100%;background:var(--bg);color:var(--fg);border:1px solid var(--line);
border-radius:10px;padding:12px 14px;font-size:15px;margin-bottom:12px}
input:focus{outline:none;border-color:var(--accent)}
button{width:100%;background:var(--accent);border:0;color:#1a1230;font-weight:700;
font-size:15px;border-radius:10px;padding:12px;cursor:pointer}
button:hover{background:color-mix(in srgb,var(--accent) 85%,#fff)}
.err{background:color-mix(in srgb,var(--bad) 18%,transparent);
border:1px solid color-mix(in srgb,var(--bad) 50%,transparent);color:var(--fg);
border-radius:9px;padding:9px 12px;font-size:13px;margin-bottom:14px}
.hint{text-align:center;color:var(--mut);font-size:12px;margin-top:14px}
</style></head><body>
<form method="POST" action="/portal/login">
  <div class="brand">Mi<b>OS</b></div>
  <div class="sub">Portal</div>
  {ERR}
  <input type="password" name="password" placeholder="Password" autofocus
    autocomplete="current-password" required>
  <button type="submit">Sign in</button>
  <div class="hint">Sign in with your MiOS password.</div>
</form>
<script>
// Register the SW on the login page too so the app is installable BEFORE auth
// (the login screen is the first thing an unauthenticated visitor sees).
if("serviceWorker" in navigator){navigator.serviceWorker.register("/sw.js",{updateViaCache:"none"}).then(function(r){r.update();}).catch(function(){});}
</script>
</body></html>"""

_IOSTEST_HTML = r"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>MiOS iOS embed test</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#06090d;color:#E7DFD3;font:15px/1.5 -apple-system,system-ui,sans-serif}
header{position:sticky;top:0;background:#0d141d;border-bottom:2px solid #F35C15;padding:12px 16px;z-index:50}
header b{color:#F35C15}
.note{font-size:13px;color:#9fb0c0;padding:10px 16px}
.gap{height:280px;display:flex;align-items:center;justify-content:center;color:#3a4756;font-size:13px;letter-spacing:2px}
.card{margin:0 16px;border:2px solid #1A407F;border-radius:12px;padding:12px;background:#0d141d}
.lbl{font-weight:700;font-size:14px;margin-bottom:8px;color:#E7DFD3}
.lbl span{color:#9fb0c0;font-weight:400;font-size:12px}
/* the "chip frame" the embed must stay inside -- dashed so detachment is obvious */
.frame{border:2px dashed #F35C15;border-radius:8px;height:160px;background:#02040a;position:relative}
.frame iframe,.frame object{width:100%;height:100%;border:0;display:block}
.f-hidden{overflow:hidden}
.f-touch{overflow:auto;-webkit-overflow-scrolling:touch}
.f-contain{overflow:hidden;contain:layout paint}
.tz{transform:translateZ(0)}
.wrap-tz{transform:translateZ(0);overflow:hidden;height:100%;width:100%}
.ctrl{height:100%;display:flex;align-items:center;justify-content:center;font:800 30px sans-serif}
</style></head><body>
<header><b>MiOS</b> &mdash; iOS embed test &nbsp;<span style="font-size:12px;color:#9fb0c0">build A</span></header>
<div class="note">Scroll down slowly inside the <b>installed app</b>. Each lettered card has a dashed-orange frame with a bright box inside. Tell me which letters <b>STAY glued inside their frame</b> as you scroll, and which <b>float / stay fixed on screen / cover other cards</b> (detached). Card&nbsp;A is the control &mdash; it should always stay.</div>

<div class="gap">&#8595; scroll &#8595;</div>

<div class="card"><div class="lbl">CARD A <span>&mdash; plain &lt;div&gt; (control, must STAY)</span></div>
  <div class="frame"><div class="ctrl" style="background:#00e5ff;color:#000">INSIDE A</div></div></div>
<div class="gap">&#8595;</div>

<div class="card"><div class="lbl">CARD B <span>&mdash; &lt;iframe&gt; in overflow:hidden box</span></div>
  <div class="frame f-hidden"><iframe srcdoc="<body style='margin:0;height:100%;display:flex;align-items:center;justify-content:center;background:#ff4081;color:#000;font:800 30px sans-serif'>INSIDE B</body>"></iframe></div></div>
<div class="gap">&#8595;</div>

<div class="card"><div class="lbl">CARD C <span>&mdash; &lt;iframe&gt; in -webkit-overflow-scrolling:touch box (build16)</span></div>
  <div class="frame f-touch"><iframe srcdoc="<body style='margin:0;height:100%;display:flex;align-items:center;justify-content:center;background:#76ff03;color:#000;font:800 30px sans-serif'>INSIDE C</body>"></iframe></div></div>
<div class="gap">&#8595;</div>

<div class="card"><div class="lbl">CARD D <span>&mdash; &lt;iframe&gt; with transform:translateZ(0) (own layer)</span></div>
  <div class="frame f-hidden"><iframe class="tz" srcdoc="<body style='margin:0;height:100%;display:flex;align-items:center;justify-content:center;background:#ffea00;color:#000;font:800 30px sans-serif'>INSIDE D</body>"></iframe></div></div>
<div class="gap">&#8595;</div>

<div class="card"><div class="lbl">CARD E <span>&mdash; &lt;iframe&gt; inside a translateZ(0) wrapper</span></div>
  <div class="frame f-hidden"><div class="wrap-tz"><iframe srcdoc="<body style='margin:0;height:100%;display:flex;align-items:center;justify-content:center;background:#e040fb;color:#000;font:800 30px sans-serif'>INSIDE E</body>"></iframe></div></div></div>
<div class="gap">&#8595;</div>

<div class="card"><div class="lbl">CARD F <span>&mdash; &lt;object&gt; instead of &lt;iframe&gt;</span></div>
  <div class="frame f-hidden"><object data="data:text/html,<body style='margin:0;height:100%25;display:flex;align-items:center;justify-content:center;background:%23ff6e40;color:%23000;font:800 30px sans-serif'>INSIDE F</body>"></object></div></div>
<div class="gap">&#8595; end &#8595;</div>
</body></html>"""

# The one status & goals renderer (GET /portal/status.js), mounted into #statussec by the dashboard
# and by mios.html via MiosStatus.mount: createElement/textContent only, a relative URL, refresh_s
# cadence. portal_status.status_js sets RETRY_MAX_S, the first-poll backoff cap.
_PORTAL_STATUS_JS = r"""(function(){"use strict";
var RETRY_MAX_S=0;
var DEFAULT={card:"card",up:"up",down:"down",row:"row",name:"name",dot:"dot",kv:"kv",meta:"meta"};
function el(tag,cls,text){var e=document.createElement(tag);if(cls)e.className=cls;
  if(text!=null)e.textContent=String(text);return e;}
function tone(s){return s==="ok"?"ok":((s==="refused"||s==="corrupt"||s==="unavailable"||s==="failed")?"bad":"");}
function ago(s){return s==null?"-":(s<120?s+"s ago":Math.round(s/60)+"m ago");}
function cut(s,n){s=String(s==null?"":s);return s.length>n?s.slice(0,n-1)+"…":s;}
function clock(ms){return new Date(ms).toLocaleTimeString();}
function verdict(p){return p===true?"pass":(p===false?"fail":"not evaluated");}
function render(j,ui){var c=ui.cls,g=ui.grid;
  function card(title,state){var t=tone(state),k=el("div",c.card+(t==="ok"&&c.up?" "+c.up:(t==="bad"&&c.down?" "+c.down:"")));
    k.style.cursor="default";var r=el("div",c.row);r.appendChild(el("span",c.name,title));
    r.appendChild(el("span",c.dot+" "+t));k.appendChild(r);g.appendChild(k);return k;}
  function kv(k,v){var d=el("div",c.kv);d.appendChild(el("b",null,k));
    d.appendChild(el("code",null,(v==null||v==="")?"-":v));return d;}
  g.textContent="";
  (j.nodes||[]).forEach(function(n){var k=card(n.name+(n.local?" (this node)":""),n.state==="unknown"?"":n.state);
    k.appendChild(kv("State",n.state));k.appendChild(kv("MiOS",n.mios_version));
    k.appendChild(kv("Git",n.git_branch?(n.git_branch+" @ "+cut(n.git_head||"?",13)):cut(n.git_head,13)));
    k.appendChild(kv("Dirty",n.git_dirty));k.appendChild(kv("bootc",n.bootc_state));
    if(n.booted_image)k.appendChild(kv("Booted",n.booted_image));
    if(n.staged_image)k.appendChild(kv("Staged",n.staged_image));
    k.appendChild(kv("Image",n.image_ref));k.appendChild(kv("Profile",n.profile));
    k.appendChild(kv("OS",n.os_version));k.appendChild(kv("Spec",ago(n.spec_age_s)));});
  var a=j.agents||{},ak=card("Agents (MiOS-MCP relay)",a.state);
  ak.appendChild(kv("Registered",a.total));ak.appendChild(kv("Online",a.online+" (lease-based)"));
  ak.appendChild(kv("Queued",a.pending));ak.appendChild(kv("Frame",ago(a.age_s)));
  (a.rows||[]).forEach(function(r){var st=r.online===true?"online":(r.online===false?"offline":"unknown");
    ak.appendChild(kv(cut(r.label||r.agent_id,24),(r.subagent?"sub · ":"")+r.kind+" · "+st+
      (r.pending?" · "+r.pending+" queued":"")+(r.source!=="local"?" · "+r.source:"")));});
  (a.sources||[]).forEach(function(s){if(s.source!=="local")ak.appendChild(kv("Peer "+s.source,s.presence));});
  var o=j.goal;
  if(!o)card("Goal","absent").appendChild(el("div",c.kv,"no active goal record"));
  else{var gk=card("Goal · "+o.status,o.status==="COMPLETED"?"ok":"");
    gk.appendChild(el("div",c.kv,cut(o.objective,280)));gk.appendChild(kv("Stop when",cut(o.stopping_condition,200)));
    (o.criteria||[]).forEach(function(k){gk.appendChild(kv(k.id,verdict(k.passed)+" · "+cut(k.description,140)));});
    gk.appendChild(el("div",c.meta,"criteria as last recorded, not live"));}
  (j.goals||[]).forEach(function(d){var tk=card(cut(d.title,60),"");
    (d.conditions||[]).forEach(function(k){tk.appendChild(kv(k.id,cut(k.condition,140)));});
    tk.appendChild(el("div",c.meta,"declared target · "+d.path));});
  var t=j.tasks||{},b=t.by_status||{},kk=card("Tasks",t.state);
  ["in_progress","pending","completed","incomplete","cancelled","other"].forEach(function(k){kk.appendChild(kv(k,b[k]));});
  kk.appendChild(kv("Overrides",t.overrides));if(t.invalid_lines)kk.appendChild(kv("Invalid lines",t.invalid_lines));
  (t.in_progress||[]).forEach(function(r){kk.appendChild(kv("doing",cut((r.priority?r.priority+" ":"")+r.title,120)));});
  (t.ready||[]).forEach(function(r){kk.appendChild(kv("next",cut((r.priority?r.priority+" ":"")+r.title,120)));});
  var sv=j.services||[],known=sv.filter(function(s){return s.active!=="unknown";});
  if(sv.length){var sk=card("Critical services",!known.length?"":(known.some(function(s){return s.active!=="active";})?"failed":"ok"));
    sv.forEach(function(s){sk.appendChild(kv(s.name,s.active));});}
  var src=card("Sources","");
  (j.sources||[]).forEach(function(s){src.appendChild(kv(s.name,s.state+" · "+s.detail+(s.age_s!=null?" · "+ago(s.age_s):"")));});
  if(ui.ver){var local=(j.nodes||[]).filter(function(n){return n.local;})[0];
    ui.ver.textContent=local&&local.mios_version?local.mios_version:"";}}
function mount(ui){var c={},k,timer=null,every=0,retry=0,last=null,okAt=0;
  for(k in DEFAULT)c[k]=ui.cls&&ui.cls[k]!=null?ui.cls[k]:DEFAULT[k];ui.cls=c;
  function mark(since){ui.grid.style.opacity=since==null?"":"0.5";
    if(ui.note&&last)ui.note.textContent=last.node+" · "+(since==null?clock(last.ts*1000):"stale since "+clock(since));}
  function schedule(s){if(timer){clearTimeout(timer);timer=null;}
    if(s>0&&!document.hidden)timer=setTimeout(tick,s*1000);}
  function failed(){if(last){mark(okAt);schedule(every);return;}
    retry=Math.min(retry?retry*2:1,RETRY_MAX_S);schedule(retry);}
  function tick(){timer=null;fetch("/portal/status",{credentials:"same-origin",cache:"no-store"})
    .then(function(r){return r.ok?r.json():null;})
    .then(function(j){if(!j||j.object!=="mios.portal.status")return failed();
      last=j;okAt=Date.now();retry=0;every=j.refresh_s>0?j.refresh_s:0;render(j,ui);
      mark(j.stale_after_s>0&&Date.now()/1000-j.ts>j.stale_after_s?j.ts*1000:null);
      if(ui.reveal)ui.reveal();schedule(every);})
    .catch(failed);}
  document.addEventListener("visibilitychange",function(){
    if(document.hidden){if(timer){clearTimeout(timer);timer=null;}}else if(!timer)tick();});
  tick();return {tick:tick};}
window.MiosStatus={mount:mount};
var sec=document.getElementById("statussec");
if(sec){var head=el("div","h"),note=el("span","n"),grid=el("div","grid");
  head.appendChild(el("h2","ac-ok","Status & Goals"));head.appendChild(note);
  sec.textContent="";sec.appendChild(head);sec.appendChild(grid);
  mount({grid:grid,note:note,ver:document.getElementById("mios-ver"),reveal:function(){sec.hidden=false;}});}
})();
"""
