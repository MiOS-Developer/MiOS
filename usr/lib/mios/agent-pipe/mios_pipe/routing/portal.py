# AI-hint: WEB PORTAL helper logic + PWA asset builders + the swarm-roster probe, extracted VERBATIM from server.py (refactor R10 wave).
# AI-doc: usr/share/doc/mios/manual/routing.md

from __future__ import annotations

import asyncio
import base64
import glob
import hashlib
import hmac
import logging
import os
import re
import socket
import sys
import time
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Request, WebSocket, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from mios_jsonsalvage import loads_lenient as _loads_lenient
from mios_pipe.kernel.config import PROBE_VERIFY_TLS as _PROBE_VERIFY_TLS

log = logging.getLogger("mios-agent-pipe")

_probe_auth_headers = None
_agent_lane = None
_AGENT_REGISTRY = None
_sanitize_tool_text = None
websockets = None

def configure(*, probe_auth_headers=None, agent_lane=None,
              agent_registry=None, sanitize_tool_text=None,
              websockets=None) -> None:
    g = globals()
    if probe_auth_headers is not None:
        g["_probe_auth_headers"] = probe_auth_headers
    if agent_lane is not None:
        g["_agent_lane"] = agent_lane
    if agent_registry is not None:
        g["_AGENT_REGISTRY"] = agent_registry
    if sanitize_tool_text is not None:
        g["_sanitize_tool_text"] = sanitize_tool_text
    if websockets is not None:
        g["websockets"] = websockets

PORTAL_PUBLIC_HOST = (os.environ.get("MIOS_PUBLIC_HOST", "").strip()
                      or socket.gethostname() or "localhost")

def _portal_toml() -> dict:
    try:
        import mios_toml
        return mios_toml.load_merged()
    except Exception:
        return {}

_PORTAL_TOML = _portal_toml()

def _pcfg(section: str, key: str, default=None):
    return (_PORTAL_TOML.get(section) or {}).get(key, default)

def _portal_port(env: str, key: str, default: int) -> int:
    """One [ports] value: MIOS_PORT_* -> [ports].<key> -> the SSOT literal.

    Law 5/7: the literal is the SSOT's own number (check_port_fallbacks holds the
    two in agreement), not a stale one -- the previous defaults named stale and
    retired ports. A set-but-EMPTY env var (bare `KEY=` in install.env, which
    Law 10 permits) must not win over the config tier, and a non-numeric value
    must not 500 the portal page, so each candidate is tried in turn."""
    for cand in (os.environ.get(env), _pcfg("ports", key, None), default):
        if cand is None or not str(cand).strip():
            continue
        try:
            return int(str(cand).strip())
        except (TypeError, ValueError):
            continue
    return default

PORTAL_PASSWORD = (os.environ.get("MIOS_PORTAL_PASSWORD")
                   or _pcfg("portal", "password")
                   or os.environ.get("MIOS_IDENTITY_DEFAULT_PASSWORD")
                   or _pcfg("identity", "default_password") or "mios")
PORTAL_USER = (os.environ.get("MIOS_PORTAL_USER")
               or _pcfg("portal", "user")
               or _pcfg("identity", "username") or "mios")
_portal_rl = os.environ.get("MIOS_PORTAL_REQUIRE_LOGIN")
if _portal_rl is None:
    _portal_rl = str(_pcfg("portal", "require_login", True))
PORTAL_REQUIRE_LOGIN = _portal_rl.strip().lower() not in (
    "0", "false", "no", "off")
PORTAL_SESSION_TTL = int(os.environ.get("MIOS_PORTAL_SESSION_TTL")
                         or _pcfg("portal", "session_ttl") or 604800)
PORTAL_COOKIE = "mios_portal"
_portal_secret_cfg = (os.environ.get("MIOS_PORTAL_SECRET")
                      or _pcfg("portal", "secret") or "")

def _portal_session_key() -> bytes:
    """Stretch password-derived keys once per worker; explicit secrets stay stable.

    A fixed, versioned domain separator preserves restart/multiworker agreement
    without storing another secret. Changing the password or work factor revokes
    derived sessions. Upgrade from the old single-hash key requires a new login.
    """
    if _portal_secret_cfg:
        return _portal_secret_cfg.encode("utf-8")
    raw = os.environ.get("MIOS_PORTAL_SESSION_KDF_ITERATIONS")
    if raw in (None, ""):
        raw = _pcfg("portal", "session_kdf_iterations", 600000)
    if type(raw) not in (int, str):
        raise ValueError("portal.session_kdf_iterations must be an integer >= 600000")
    try:
        iterations = int(raw)
    except ValueError:
        raise ValueError("portal.session_kdf_iterations must be an integer >= 600000") from None
    if iterations < 600000:
        raise ValueError("portal.session_kdf_iterations must be an integer >= 600000")
    return hashlib.pbkdf2_hmac("sha256", PORTAL_PASSWORD.encode("utf-8"),
                               b"mios-portal-session|v2", iterations)

_PORTAL_SECRET = _portal_session_key()

def _portal_make_token(user: str) -> str:
    """Stateless signed session token: b64(user|exp).hmac(secret)."""
    exp = int(time.time()) + PORTAL_SESSION_TTL
    body = base64.urlsafe_b64encode(
        f"{user}|{exp}".encode("utf-8")).decode("ascii").rstrip("=")
    sig = hmac.new(_PORTAL_SECRET, body.encode("ascii"),
                   hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"

def _portal_token_ok(tok: Optional[str]) -> bool:
    if not tok or "." not in tok:
        return False
    body, _, sig = tok.partition(".")
    exp_sig = hmac.new(_PORTAL_SECRET, body.encode("ascii"),
                       hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, exp_sig):
        return False
    try:
        pad = "=" * (-len(body) % 4)
        _, _, exp = base64.urlsafe_b64decode(
            body + pad).decode("utf-8").partition("|")
        return int(exp) > int(time.time())
    except Exception:
        return False

def _portal_authed(request: Request) -> bool:
    if not PORTAL_REQUIRE_LOGIN:
        return True
    if _portal_token_ok(request.cookies.get(PORTAL_COOKIE)):
        return True
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return _portal_token_ok(auth[7:].strip())
    return False

def _portal_unit_hidden(quadlet_file: str) -> bool:
    base = os.path.basename(quadlet_file)
    if not base.endswith(".container"):
        return False
    unit = base[:-len(".container")] + ".service"
    try:
        import subprocess
        out = subprocess.run(
            ["systemctl", "show", "-p", "LoadState", "-p", "ConditionResult",
             "--value", unit], capture_output=True, text=True, timeout=4)
        vals = [v.strip() for v in out.stdout.splitlines()]
        load = vals[0] if len(vals) > 0 else ""   # -p order: LoadState first
        cond = vals[1] if len(vals) > 1 else ""    # then ConditionResult
        return load == "masked" or cond == "no"
    except Exception:  # noqa: BLE001 -- never hide a service on a query error
        return False

def _discover_portal_services() -> list[dict]:
    """Scan the Quadlet *.container files for io.podman_desktop.openInBrowser
    labels -> {name, port, local health URL}. Adds the host Cockpit service.
    Deduped by port, sorted by name. SSOT: the quadlet labels, not a list.
    Masked / condition-gated-OFF units are dropped (see _portal_unit_hidden)."""
    svcs: list[dict] = []
    seen: set[str] = set()
    for d in ("/etc/containers/systemd", "/usr/share/containers/systemd"):
        for f in sorted(glob.glob(os.path.join(d, "*.container"))):
            title = url = cname = ""
            try:
                for line in open(f, encoding="utf-8", errors="replace"):
                    s = line.strip()
                    if "openInBrowser=" in s:
                        url = s.split("openInBrowser=", 1)[1].strip()
                    elif "image.title=" in s:
                        title = s.split("image.title=", 1)[1].strip()
                    elif s.startswith("ContainerName="):
                        cname = s.split("=", 1)[1].strip()
            except OSError:
                continue
            m = re.search(r"(https?)://[^:/]+:(\d+)(/\S*)?", url)
            if not m:
                continue
            scheme, port, path = m.group(1), m.group(2), (m.group(3) or "/")
            if port in seen:
                continue
            if _portal_unit_hidden(f):
                continue
            seen.add(port)
            name = (title or os.path.basename(f).replace(".container", ""))
            name = name.replace("mios-", "").replace("-", " ").strip().title()
            if not cname:
                cname = os.path.basename(f).replace(".container", "")
            svcs.append({"name": name, "port": int(port), "path": path,
                         "container_name": cname, "kind": "",
                         "local": f"{scheme}://127.0.0.1:{port}{path}"})
    host_svcs = {"cockpit": ("Cockpit", "https", ""),
                 "code_server": ("Code Server", "http", ""),
                 "ttyd_bash": ("Terminal · Bash", "http", "terminal"),
                 "ttyd_powershell": ("Terminal · PowerShell", "http", "terminal")}
    try:
        import mios_toml
        merged = mios_toml.load_merged()
        ports = mios_toml.section(merged, "ports")
    except Exception:
        ports = {}
    for key, (label, scheme, kind) in host_svcs.items():
        p = ports.get(key)
        if not p or str(p) in seen:
            continue
        seen.add(str(p))
        svcs.append({"name": label, "port": int(p), "path": "/",
                     "container_name": "", "kind": kind,
                     "local": f"{scheme}://127.0.0.1:{p}/"})
    svcs.sort(key=lambda s: (s.get("kind") != "terminal", s["name"].lower()))
    return svcs

_PORTAL_SERVICES = _discover_portal_services()

def _host_stats() -> dict:
    """Cheap host telemetry from /proc (no psutil dependency)."""
    out: dict[str, Any] = {"cpu": os.cpu_count()}
    try:
        out["host"] = open("/proc/sys/kernel/hostname").read().strip()
    except OSError:
        pass
    try:
        out["load"] = open("/proc/loadavg").read().split()[:3]
    except OSError:
        pass
    try:
        mi: dict[str, int] = {}
        for line in open("/proc/meminfo"):
            k, _, v = line.partition(":")
            if v:
                mi[k.strip()] = int(v.split()[0])
        tot, avail = mi.get("MemTotal", 0), mi.get("MemAvailable", 0)
        if tot:
            out["mem_used_pct"] = round((tot - avail) * 100 / tot)
            out["mem_total_gb"] = round(tot / 1048576, 1)
    except (OSError, ValueError):
        pass
    try:
        out["uptime_s"] = int(float(open("/proc/uptime").read().split()[0]))
    except (OSError, ValueError):
        pass
    return out

_PODMAN_PS_SNAPSHOT = os.environ.get(
    "MIOS_PODMAN_PS_SNAPSHOT", "/var/lib/mios/podman-ps.json")

async def _podman_ps() -> dict:
    data = None
    try:
        with open(_PODMAN_PS_SNAPSHOT, "rb") as _f:
            data = _loads_lenient(_f.read() or b"[]")
    except Exception:
        data = None
    if data is None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "podman", "ps", "-a", "--format", "json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=4.0)
            data = _loads_lenient(out or b"[]")
        except Exception:
            try: proc.kill()
            except: pass
            return {"port": {}, "name": {}}
    by_port: dict[int, dict] = {}
    by_name: dict[str, dict] = {}
    for c in data if isinstance(data, list) else []:
        names = c.get("Names") or []
        info = {"container": (names[0] if names else (c.get("Id") or "")[:12]),
                "state": str(c.get("State", "")).lower(),
                "image": c.get("Image", "")}
        for nm in names:
            by_name[str(nm)] = info
        for p in (c.get("Ports") or []):
            hp = p.get("host_port") if isinstance(p, dict) else None
            if hp:
                by_port[int(hp)] = info
    return {"port": by_port, "name": by_name}

_PORTAL_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>MiOS</title>
<link rel="manifest" href="/portal/manifest.webmanifest">
<meta name="theme-color" content="#282262">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="MiOS">
<link rel="icon" href="/portal/icon.svg">
<link rel="icon" type="image/png" sizes="192x192" href="/portal/icon-192.png">
<link rel="apple-touch-icon" href="/portal/icon-192.png">
<style>
/* MiOS unified palette (mios.toml [colors]; Hokusai "Great Wave" + operator
   neutrals). Base tones are SSOT-injected at serve time; derived surfaces
   recompute from them via color-mix. */
:root{
--bg:#282262;        /* deep indigo (Hokusai sky) */
--panel:#1A407F;     /* operator blue (surfaces) */
--fg:#E7DFD3;        /* foam cream */
--mut:#B7C9D7;       /* pale blue-grey */
--accent:#F35C15;    /* sunset orange (interactive) */
--ok:#3E7765;        /* wave green */
--bad:#DC271B;       /* coral red */
--silver:#E0E0E0;--earth:#734F39;
--warn:#FF8540;      /* bright sunset  (ANSI 11) */
--info:#3D6BA8;      /* bright op-blue (ANSI 12) */
--ok2:#5FAA8E;       /* bright wave    (ANSI 10) */
--rust:#9D7660;      /* bright brown   (ANSI 13) */
--subtle:#B7C9D7;    /* pale blue-grey */
--card:color-mix(in srgb,var(--panel) 24%,var(--bg));
--card2:color-mix(in srgb,var(--panel) 42%,var(--bg));
--line:color-mix(in srgb,var(--mut) 24%,transparent);
--rad:12px;
--mono:ui-monospace,"Cascadia Code","Source Code Pro",Consolas,monospace;
--sans:-apple-system,"Segoe UI",system-ui,Roboto,sans-serif}
*{box-sizing:border-box}
body{margin:0;padding:env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left);color:var(--fg);font:15px/1.5 var(--sans);overflow-x:hidden;
background:radial-gradient(1100px 520px at 12% -12%, color-mix(in srgb,var(--accent) 13%,transparent),transparent 60%), radial-gradient(900px 500px at 100% 0%, color-mix(in srgb,var(--panel) 30%,transparent),transparent 55%), radial-gradient(820px 520px at 50% 118%, color-mix(in srgb,var(--info) 18%,transparent),transparent 60%),var(--bg);
background-attachment:fixed}
/* background-attachment:fixed is an iOS Safari render footgun (forces odd
   layer repaints during scroll that shove other elements around). Pin it on
   touch/narrow viewports. */
@media(max-width:900px){body{background-attachment:scroll}}
a{color:var(--accent);text-decoration:none}
.bar{display:flex;align-items:center;gap:16px;padding:14px 22px;
border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:30}
h1{margin:0;font-size:24px;letter-spacing:.5px}h1 b{color:var(--accent)}
.host{display:flex;gap:18px;flex-wrap:wrap;font-size:12.5px;color:var(--mut);margin-left:6px}
.host b{color:var(--fg)}
.spacer{flex:1}
.menu{position:relative}
.btn{background:var(--card2);border:1px solid var(--line);color:var(--fg);
border-radius:9px;padding:7px 12px;font-size:13px;cursor:pointer;transition:.15s}
.btn:hover,.btn.active{border-color:var(--accent);color:var(--accent)}
.btn.active{background:color-mix(in srgb,var(--accent) 15%,var(--card2))}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#1a1230;font-weight:700}
.btn.primary:hover{background:color-mix(in srgb,var(--accent) 84%,#fff);color:#1a1230}
.drop{position:absolute;right:0;top:110%;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px;min-width:200px;display:none;box-shadow:0 10px 30px rgba(0,0,0,.5)}
.drop.open{display:block}
.drop label{display:flex;justify-content:space-between;align-items:center;padding:7px 8px;font-size:13px;color:var(--mut);gap:10px}
.drop select,.drop input{background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:7px;padding:4px 7px;font-size:13px}
section{padding:18px 22px}
.h{display:flex;align-items:center;gap:10px;margin:4px 0 14px}
.h h2{font-size:15px;letter-spacing:.4px;text-transform:uppercase;color:var(--silver);margin:0;border-left:4px solid var(--accent);padding-left:9px}
.h .n{color:var(--ok);font-size:12px;font-weight:600}
/* Top column: SearXNG search + MiOS AI chat, centered, same width as the
   header. */
.top{width:min(760px,100%);margin:20px auto 8px;padding:0 18px}
.websearch{display:flex;gap:8px;margin:0 0 12px}
.websearch input{flex:1;background:var(--card);border:1px solid var(--line);color:var(--fg);border-radius:11px;padding:12px 16px;font-size:15px}
.websearch input:focus{outline:none;border-color:var(--accent)}
.hoststrip{width:min(760px,100%);margin:0 auto 2px;padding:8px 18px 0;display:flex;gap:18px;flex-wrap:wrap;justify-content:center;font-size:12.5px;color:var(--mut)}
.hoststrip b{color:var(--fg)}
/* chat window: portrait-ish 4:5 (taller than landscape, not phone-tall),
   inline + drag-resizable */
margin:0 auto;width:100%;aspect-ratio:2/3;resize:both;
min-width:280px;min-height:720px;max-width:100%}
.grid{display:grid;gap:13px;grid-template-columns:repeat(auto-fill,minmax(215px,1fr))}
/* Services grid: exactly 2 columns; 1 on narrow. */
/* Terminals stack in a SINGLE column so expanding one is purely vertical: it
   opens in place, never relocates a neighbour, and the row is wide enough for
   a real 80x20 embed. */
/* minmax(0,1fr) NOT plain 1fr: a bare 1fr is minmax(auto,1fr), whose auto
   minimum lets the track expand to the terminal's wide min-content and overflow
   the viewport (the chip then shifts off-screen / appears to float). */
@media(max-width:600px){
  body{font-size:16px}
  .bar{gap:8px;padding:10px 12px}
  .bar .spacer{display:none}
  .btn{min-height:44px;padding:9px 12px;font-size:14px}
  .top,.hoststrip,section{padding-left:12px;padding-right:12px}
  .websearch{flex-direction:column}
  .websearch .btn{width:100%}
  #grid,.grid{grid-template-columns:1fr}
  .embed-box{height:min(68vh,560px)}
  .card.term.exp .embed-box{height:min(56vh,460px)}
  .sheet{max-height:90vh;padding:16px}
}
.addr{font-family:var(--mono);font-size:11.5px;color:var(--mut);margin-top:8px;
white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.addr a{color:var(--mut)}.addr a:hover{color:var(--accent)}
.card{position:relative;background:var(--card);border:1px solid var(--line);
border-left:3px solid var(--mut);cursor:pointer;min-width:0;
border-radius:var(--rad);--card-pad-top:15px;--card-pad-x:15px;--card-pad-bottom:13px;padding:var(--card-pad-top) var(--card-pad-x) var(--card-pad-bottom);transition:.15s border-color,.15s transform}
.card.exp{cursor:default}
.card.exp:hover{transform:none}
.card.up{border-left-color:var(--ok)}
.card.down{border-left-color:var(--bad)}
.card:hover{border-color:var(--accent);transform:translateY(-2px)}
.row{display:flex;align-items:center;justify-content:space-between}
.name{font-size:15.5px;font-weight:600}
.dot{width:10px;height:10px;border-radius:50%;background:var(--mut)}
.dot.ok{background:var(--ok)}.dot.bad{background:var(--bad)}
/* swarm node tiles */
.lane{font-size:10px;text-transform:uppercase;letter-spacing:.4px;padding:1px 7px;
border-radius:20px;border:1px solid var(--line);color:var(--mut)}
.lane.gpu{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 45%,transparent)}
.lane.cpu{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 45%,transparent)}
.lane.mobile{color:var(--silver);border-color:color-mix(in srgb,var(--silver) 45%,transparent)}
.node .m{font-size:11.5px;color:var(--mut);margin-top:7px;font-family:var(--mono);
white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.node .ep{font-size:10.5px;color:color-mix(in srgb,var(--mut) 70%,transparent);
font-family:var(--mono);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:3px}
.node .tags{font-size:10px;color:var(--rust);margin-top:6px}
.meta{color:var(--mut);font-size:12px;margin-top:9px;display:flex;justify-content:space-between}
.port{font-family:ui-monospace,Menlo,monospace}
.kebab{position:absolute;top:9px;right:9px;z-index:5;background:transparent;border:0;
color:var(--mut);font-size:18px;cursor:pointer;line-height:1;padding:2px 6px;border-radius:6px}
.kebab:hover{background:var(--card2);color:var(--fg)}
.cdrop{position:absolute;top:30px;right:9px;z-index:6;background:var(--card2);
border:1px solid var(--line);border-radius:9px;padding:5px;display:none;min-width:140px}
.cdrop.open{display:block}
.cdrop button{display:block;width:100%;text-align:left;background:transparent;border:0;
color:var(--fg);font-size:13px;padding:7px 9px;border-radius:6px;cursor:pointer}
.cdrop button:hover{background:var(--card)}
.state{font-size:11px;padding:1px 7px;border-radius:20px;border:1px solid var(--line);color:var(--mut)}
.state.running{color:var(--ok2);border-color:color-mix(in srgb,var(--ok2) 40%,transparent)}
.search{display:flex;gap:8px;margin-bottom:14px}
.search input{flex:1;background:var(--card);border:1px solid var(--line);color:var(--fg);
border-radius:9px;padding:9px 12px;font-size:14px}
.app{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 13px}
.app .c{color:var(--accent);font-size:11px;text-transform:uppercase;letter-spacing:.4px}
.app .d{color:var(--mut);font-size:12.5px;margin-top:4px}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;z-index:50;
align-items:center;justify-content:center;padding:20px}
.modal.open{display:flex}
.sheet{background:var(--card);border:1px solid var(--line);border-radius:14px;
width:min(720px,100%);max-height:86vh;overflow:auto;padding:20px 22px}
.sheet h3{margin:0 0 4px;font-size:20px}
.kv{display:flex;gap:10px;font-size:13px;margin:6px 0;color:var(--mut)}
.kv b{color:var(--fg);font-weight:600;min-width:90px}
.kv code{font-family:ui-monospace,Menlo,monospace;color:var(--fg);word-break:break-all}
pre{background:#06090d;border:1px solid var(--line);border-radius:9px;padding:12px;
font-size:12px;max-height:340px;overflow:auto;color:#aeb9c4;white-space:pre-wrap}
.x{float:right;background:transparent;border:0;color:var(--mut);font-size:22px;cursor:pointer}
footer{color:var(--mut);font-size:12px;text-align:center;padding:16px}
.toast{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);background:var(--card2);
border:1px solid var(--line);border-radius:9px;padding:9px 16px;font-size:13px;display:none;z-index:60}
/* per-section header accents (more of the MiOS palette) */
.h h2.ac-info{border-left-color:var(--info)}
.h h2.ac-ok{border-left-color:var(--ok2)}
.h h2.ac-warn{border-left-color:var(--warn)}
/* ── Inline service embeds: each chip gets an expand
   toggle that opens the service as an inline iframe portal. ttyd terminals
   are pinned to the top and capped to an 80x20 char grid (the MiOS dashboard
   size -- also used for btop/console). The grid re-renders in place (see
   cards()) so an open terminal session survives the periodic refresh. */
.exp{position:absolute;top:9px;right:34px;z-index:5;background:transparent;border:0;
color:var(--mut);font-size:13px;cursor:pointer;line-height:1;padding:3px 6px;border-radius:6px;
transition:transform .15s,color .15s,background .15s;transform:rotate(-90deg)}
.exp:hover{background:var(--card2);color:var(--info)}
.card.term{border-left-color:var(--info)}
/* Expand IN PLACE -- the card grows downward inside its own grid cell and
   stays inline with its neighbours (no full-width span that reflows the grid). */
.card.exp{border-color:var(--accent);
box-shadow:0 8px 30px color-mix(in srgb,var(--accent) 22%,transparent)}
.card.exp .exp{transform:rotate(0deg);color:var(--accent)}
.embed{display:none}
/* Plain in-flow block (no z-index/relative): the chip grows DOWNWARD to
   contain it, so it can never float over the elements below. */
.card.exp .embed{display:block;margin-top:13px}
.embed-bar{display:flex;justify-content:space-between;align-items:center;font-size:10.5px;
color:var(--subtle);font-family:var(--mono);margin-bottom:6px;text-transform:uppercase;
letter-spacing:.5px}
.embed-bar a{color:var(--warn)}
/* The embed is an iframe in a FIXED-height, overflow:hidden box. On an iOS
   standalone PWA an iframe is composited into a SEPARATE layer positioned in
   VIEWPORT space -- it "detaches" and floats over the page as you scroll.
   /iostest proved the fix empirically (cards A-F): a plain
   iframe (B), a -webkit-overflow-scrolling:touch box (C = what build16 used),
   and <object> (F) ALL detach; promoting the iframe to its OWN compositing
   layer with transform:translateZ(0) (cards D + E) keeps it GLUED inside its
   frame. So: overflow:hidden box + translateZ(0) on the iframe. */
.embed-box{border:1px solid color-mix(in srgb,var(--info) 35%,var(--line));
border-radius:9px;background:#06090d;position:relative;height:480px;overflow:hidden}
.embed-box iframe{display:block;width:100%;height:100%;border:0;background:#06090d;
transform:translateZ(0)}
/* terminal embed = ttyd's OWN page (operator: "use ttyd's own page") in the same
   iframe. ttyd auto-fits its terminal to the iframe box, so there is no custom
   xterm, no FitAddon, no 1-column "rotated" text. Shorter box than a website. */
.card.term.exp .embed-box{height:360px}
/* System Config status card (WS-CONFIG): a read-only health summary, not a
   clickable disclosure -- neutralise the service-card hover-lift + reuse the
   modal .kv rows for the key/value lines. */
</style></head><body>
<div class="bar">
  <a href="/" id="logoLink" style="color:inherit;text-decoration:none;display:flex;align-items:center;"><h1>Mi<b>OS</b> <sup style="font-size:10px;color:var(--warn);font-weight:400">build18</sup></h1></a>
  <div class="spacer"></div>
  <button class="btn primary" id="installBtn">&#11015; Install</button>
  <button class="btn" id="chatToggle">&#128172; Chat</button>
  <button class="btn" id="settingsToggle">&#9881;&#65039; Settings</button>
  <div class="menu">
    <button class="btn" id="menuBtn">&#9776; Menu</button>
    <div class="drop" id="menu">
      <label>Refresh <select id="refresh">
        <option value="5000">5s</option><option value="15000">15s</option>
        <option value="30000">30s</option><option value="0">off</option></select></label>
      <label>Sort <select id="sort">
        <option value="name">name</option><option value="status">status</option>
        <option value="port">port</option></select></label>
      <label>Only down <input type="checkbox" id="onlydown"></label>
      <label><a href="/portal/stats" target="_blank">raw stats JSON</a></label>
      <label><a href="/iostest">&#129514; iOS embed test</a></label>
      <label><a href="/portal/logout">Log out &#8594;</a></label>
    </div>
  </div>
</div>

<div id="dashboard-view">
<div class="top">
  <form class="websearch" id="wsform">
    <input id="wsq" placeholder="Search the web with SearXNG&hellip;" autocomplete="off">
    <button class="btn" type="submit">&#128269; Search</button>
  </form>
  <div id="chatwrap"><iframe id="chat" title="MiOS AI"></iframe></div>
</div>

<div class="hoststrip" id="host"></div>

<section id="cfgsec">
  <div class="h"><h2 class="ac-info">System Config</h2><span class="n" id="cfgn"></span></div>
  <div class="grid" id="cfggrid">
    <div class="card" id="cfgcard">
      <div class="row"><span class="name">Configuration</span><span class="dot" id="cfgdot"></span></div>
      <div id="cfgbody"><div class="kv">loading&hellip;</div></div>
      <div class="meta"><a href="/configure" id="cfgedit">Edit in Settings &#8594;</a></div>
    </div>
  </div>
</section>

<section>
  <div class="h"><h2 class="ac-warn">Terminals</h2><span class="n" id="termn"></span>
    <span class="n" style="color:var(--subtle);font-weight:400">&middot; inline 80&times;20 pty &middot; bash &middot; pwsh &middot; btop</span></div>
  <div class="grid" id="terms"></div>
</section>

<section>
  <div class="h"><h2 class="ac-info">Swarm Nodes</h2><span class="n" id="swarmn"></span></div>
  <div class="grid" id="swarm"></div>
</section>

<section>
  <div class="h"><h2>Services</h2><span class="n" id="svcn"></span></div>
  <div class="grid" id="grid"></div>
</section>

<section>
  <div class="h"><h2 class="ac-ok">MiOS Apps</h2><span class="n">windows &middot; terminal &middot; TUIs</span></div>
  <div class="search">
    <input id="appq" placeholder="Search installed apps (e.g. browser, htop, steam)&hellip;">
    <button class="btn" id="appgo">Search</button>
  </div>
  <div class="grid" id="apps"></div>
</section>
</div>

<div id="settings-view" style="display:none; width:100%; height:calc(100vh - 65px); margin:0; padding:0; overflow:hidden;">
  <iframe id="settings-iframe" style="width:100%; height:100%; border:0; background:transparent;" src="about:blank"></iframe>
</div>

<div class="modal" id="modal"><div class="sheet" id="sheet"></div></div>
<div class="toast" id="toast"></div>
<footer id="foot">loading&hellip;</footer>

<script>
var S=[],OPTS={refresh:5000,sort:"name",onlydown:false},timer=null,chatSet=false,SEARX="";
function $(id){return document.getElementById(id);}
function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){
  return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function toast(t){var e=$("toast");e.textContent=t;e.style.display="block";
  setTimeout(function(){e.style.display="none";},1600);}
function fmtUp(s){if(!s)return"?";var d=Math.floor(s/86400),h=Math.floor(s%86400/3600),
  m=Math.floor(s%3600/60);return(d?d+"d ":"")+(h?h+"h ":"")+m+"m";}
function copy(t){navigator.clipboard&&navigator.clipboard.writeText(t);toast("copied "+t);}
function sorted(){var a=S.slice();
  if(OPTS.onlydown)a=a.filter(function(s){return !s.ok;});
  a.sort(function(x,y){
    var tx=x.kind=="terminal"?0:1,ty=y.kind=="terminal"?0:1;
    if(tx!=ty)return tx-ty;  // ttyd terminals pinned to the top (operator)
    return OPTS.sort=="status"?(x.ok-y.ok):
      OPTS.sort=="port"?(x.port-y.port):x.name.localeCompare(y.name);});
  return a;}
// Cards are rendered ONCE then updated in place so an expanded inline embed
// (esp. a live ttyd terminal session) survives the periodic stats refresh --
// wiping innerHTML every tick would tear down the iframe + drop the pty.
var cardEls={};
function metaTail(s){
  var st=s.state?' &middot; <span class="state '+esc(s.state)+'">'+esc(s.state)+'</span>':'';
  return (s.ok?(s.ms+" ms"):"down")+st;}
function buildCard(s){
  var addr=(s.url||"").replace(/^https?:\/\//,"").replace(/\/$/,"");
  var loc=(s.internal||"").replace(/^https?:\/\//,"").replace(/\/$/,"");
  var term=s.kind=="terminal";
  var el=document.createElement("div");
  el.className="card "+(s.ok?"up":"down")+(term?" term":"");
  el.setAttribute("data-p",s.port);
  el.innerHTML=
    '<button class="exp" data-x="'+s.port+'" title="Expand / collapse">&#9662;</button>'+
    '<button class="kebab" data-k="'+s.port+'">&#8942;</button>'+
    '<div class="cdrop" id="cd'+s.port+'">'+
      '<button data-act="embed" data-p="'+s.port+'">Embed inline</button>'+
      '<button data-act="open" data-u="'+esc(s.url)+'">Open</button>'+
      '<button data-act="copy" data-u="'+esc(s.url)+'">Copy URL</button>'+
      '<button data-act="detail" data-p="'+s.port+'">Details</button></div>'+
    '<div class="row"><span class="name">'+esc(s.name)+'</span>'+
      '<span class="dot '+(s.ok?"ok":"bad")+'"></span></div>'+
    '<div class="addr">&#128279; '+esc(addr)+'</div>'+
    (loc?'<div class="addr" style="opacity:.7">&#8627; '+esc(loc)+'</div>':'')+
    '<div class="meta"><span class="port">:'+s.port+'</span>'+
      '<span class="st">'+metaTail(s)+'</span></div>'+
    '<div class="embed" id="emb'+s.port+'" data-u="'+esc(s.url)+'">'+
      '<div class="embed-bar"><span>'+(term?"&#9000; terminal &middot; 80&times;20":"&#9636; embedded view")+'</span>'+
        '<a href="'+esc(s.url)+'" target="_blank" rel="noopener">open &#8599;</a></div>'+
      '<div class="embed-box"></div></div>';
  return el;}
function updateCard(el,s){
  var term=s.kind=="terminal",exp=el.classList.contains("exp");
  el.className="card "+(s.ok?"up":"down")+(term?" term":"")+(exp?" exp":"");
  var dot=el.querySelector(".row .dot");if(dot)dot.className="dot "+(s.ok?"ok":"bad");
  var st=el.querySelector(".st");if(st)st.innerHTML=metaTail(s);}
// Reconcile DOM order with MINIMAL moves: only (re)insert a card when it is NOT
// already at its target slot. Moving an <iframe> (or an ancestor) in the DOM
// RELOADS it -- re-appending every card each tick is what made an opened
// terminal "keep refreshing" and the layout churn. Untouched cards keep their
// live embeds intact.
function place(list,parent){
  list.forEach(function(s,i){
    var el=cardEls[s.port];
    if(!el){el=buildCard(s);cardEls[s.port]=el;}else updateCard(el,s);
    if(parent.children[i]!==el)parent.insertBefore(el,parent.children[i]||null);
  });
}
function cards(){
  var order=sorted(),live={};
  order.forEach(function(s){live[s.port]=1;});
  var T=order.filter(function(s){return s.kind=="terminal";});   // -> Terminals section (under chat)
  var O=order.filter(function(s){return s.kind!="terminal";});   // -> Services
  place(T,$("terms"));place(O,$("grid"));
  Object.keys(cardEls).forEach(function(p){
    if(!live[p]){cardEls[p].remove();delete cardEls[p];}});
  $("svcn").textContent=O.filter(function(s){return s.ok;}).length+" / "+O.length+" up";
  if($("termn"))$("termn").textContent=T.filter(function(s){return s.ok;}).length+" / "+T.length+" up";
}
// Every chip -- service AND ttyd terminal -- expands to the same thing: the
// target's OWN page in a plain in-flow iframe inside the touch-scroll .embed-box
// (see CSS). ttyd serves a complete, self-fitting terminal at its own HTTPS URL,
// so there is no hand-rolled xterm/WebSocket bridge here anymore (it rendered as
// rotated/floating in the iOS standalone PWA). The iframe is created once and
// preserved across stat refreshes (place() never re-parents an open card).
function toggleEmbed(p){
  var el=cardEls[p];if(!el)return;
  var open=el.classList.toggle("exp");
  if(!open)return;
  var box=el.querySelector(".embed-box");
  if(box&&!box._init){box._init=true;
    var u=el.querySelector(".embed").getAttribute("data-u");
    var f=document.createElement("iframe");f.setAttribute("loading","lazy");
    f.title="embed "+p;f.src=u;box.appendChild(f);}
}
function render(j){
  var h=j.host||{},hs=[];
  if(h.host)hs.push("<b>"+esc(h.host)+"</b>");
  if(h.load)hs.push("load <b>"+esc(h.load.join(" "))+"</b>");
  if(h.cpu)hs.push("<b>"+h.cpu+"</b> cpu");
  if(h.mem_used_pct!=null)hs.push("mem <b>"+h.mem_used_pct+"%</b>/"+(h.mem_total_gb||"?")+"G");
  if(h.uptime_s)hs.push("up <b>"+fmtUp(h.uptime_s)+"</b>");
  $("host").innerHTML=hs.join("");
  S=j.services||[];cards();
  if(!chatSet){var ow=S.filter(function(s){return s.port==_MIOS_PORT_OWUI;})[0];
    if(ow){$("chat").src=ow.url;chatSet=true;}}
  var sx=S.filter(function(s){return s.port==_MIOS_PORT_SEARXNG;})[0];if(sx)SEARX=sx.url;
  $("foot").textContent="refreshed "+new Date((j.ts||0)*1000).toLocaleTimeString();
}
function renderSwarm(j){
  var a=j.agents||[];
  $("swarmn").textContent=(j.up||0)+" / "+(j.total||a.length)+" nodes live";
  $("swarm").innerHTML=a.map(function(n){
    var ep=(n.endpoint||"").replace(/^https?:\/\//,"");
    var lm=(n.live_models&&n.live_models.length)?n.live_models.join(", "):(n.model||"?");
    var tag=n.health_gate?' &middot; <span class="tags">client</span>':
      (n.default?' &middot; <span class="tags">primary</span>':"");
    return '<div class="card node '+(n.reachable?"up":"down")+'">'+
      '<div class="row"><span class="name">'+esc(n.name)+'</span>'+
        '<span class="dot '+(n.reachable?"ok":"bad")+'"></span></div>'+
      '<div class="m">'+esc(lm)+'</div>'+
      '<div class="ep">'+esc(ep||"-")+'</div>'+
      '<div class="meta"><span class="lane '+esc(n.lane||"")+'">'+
        esc(n.lane||n.role||"node")+'</span>'+
        '<span>'+(n.reachable?(n.ms+" ms"):"down")+tag+'</span></div></div>';
  }).join("");
}
function tickSwarm(){fetch("/portal/swarm",{cache:"no-store"})
  .then(function(r){return r.json();}).then(renderSwarm).catch(function(){});}
function tick(){fetch("/portal/stats",{cache:"no-store"}).then(function(r){
    if(r.status==401){location.href="/login";return null;}  // session expired -> re-auth
    return r.json();})
  .then(function(j){if(j)render(j);}).catch(function(){$("foot").textContent="stats unavailable";});
  tickSwarm();}
function arm(){if(timer)clearInterval(timer);if(OPTS.refresh)timer=setInterval(tick,OPTS.refresh);}
// System Config card: a small read-only summary from /portal/config/status.
// Degrade-open -- an unauth (401 -> r.ok false) or failed fetch shows a graceful
// "config status unavailable" state, never an error crash (the main tick()
// still owns the session-expiry redirect to /login).
function renderConfig(j){
  var dot=$("cfgdot"),body=$("cfgbody"),n=$("cfgn");
  if(!body)return;
  if(!j||j.error){
    if(dot)dot.className="dot";
    if(n)n.textContent="unavailable";
    body.innerHTML='<div class="kv">config status unavailable</div>';
    return;}
  var ok=j.theme=="PASS",bad=j.theme=="FAIL";
  if(dot)dot.className="dot "+(ok?"ok":(bad?"bad":""));
  if(n)n.textContent=ok?"theme PASS":(bad?"theme FAIL":"theme unknown");
  body.innerHTML=
    '<div class="kv"><b>User</b><code>'+esc(j.user||"?")+'</code></div>'+
    '<div class="kv"><b>Version</b><code>'+esc(j.version||"?")+'</code></div>'+
    '<div class="kv"><b>Sections</b><code>'+esc(j.sections==null?"?":j.sections)+'</code></div>'+
    '<div class="kv"><b>Override</b><code>'+(j.override?"present":"none")+'</code></div>'+
    '<div class="kv"><b>Theme</b><code title="'+esc(j.theme_summary||"")+'">'+esc(j.theme||"unknown")+'</code></div>';}
function tickConfig(){
  fetch("/portal/config/status",{cache:"no-store"})
    .then(function(r){return r.ok?r.json():null;})
    .then(renderConfig).catch(function(){renderConfig(null);});}
function detail(p){
  fetch("/portal/service/"+p,{cache:"no-store"}).then(function(r){return r.json();}).then(function(d){
    $("sheet").innerHTML='<button class="x" onclick="closeM()">&times;</button>'+
      '<h3>'+esc(d.name)+' <span class="dot '+(d.ok?"ok":"bad")+'"></span></h3>'+
      '<div class="kv"><b>URL</b><code>'+esc(d.url)+'</code></div>'+
      '<div class="kv"><b>Internal</b><code>'+esc(d.internal)+'</code></div>'+
      (d.container?'<div class="kv"><b>Container</b><code>'+esc(d.container)+'</code></div>':'')+
      (d.state?'<div class="kv"><b>State</b><code>'+esc(d.state)+'</code></div>':'')+
      (d.image?'<div class="kv"><b>Image</b><code>'+esc(d.image)+'</code></div>':'')+
      '<div class="kv"><b>Open</b><code><a href="'+esc(d.url)+'" target="_blank">'+esc(d.url)+'</a></code></div>'+
      (d.logs?'<div class="kv"><b>Logs</b></div><pre>'+esc(d.logs)+'</pre>':'<div class="kv">no container logs</div>');
    $("modal").classList.add("open");
  });
}
function closeM(){$("modal").classList.remove("open");}
function searchApps(){var q=$("appq").value.trim();if(!q)return;
  $("apps").innerHTML='<div class="app">searching&hellip;</div>';
  fetch("/v1/app-search?limit=12&query="+encodeURIComponent(q)).then(function(r){return r.json();})
    .then(function(j){var hits=j.hits||[];
      $("apps").innerHTML=hits.length?hits.map(function(a){
        return '<div class="app"><div class="c">'+esc(a.category||"app")+'</div>'+
          '<div class="name">'+esc(a.name)+'</div>'+
          (a.description?'<div class="d">'+esc(a.description)+'</div>':'')+
          (a.launch?'<div class="d"><code>'+esc(a.launch)+'</code></div>':'')+'</div>';
      }).join(""):'<div class="app">no matches</div>';
    }).catch(function(){$("apps").innerHTML='<div class="app">app search unavailable</div>';});}
// events
document.addEventListener("click",function(e){
  var x=e.target.closest("[data-x]");
  if(x){toggleEmbed(x.getAttribute("data-x"));return;}
  var k=e.target.closest("[data-k]");
  document.querySelectorAll(".cdrop.open").forEach(function(d){
    if(!k||d.id!="cd"+k.getAttribute("data-k"))d.classList.remove("open");});
  if(k){var cd=$("cd"+k.getAttribute("data-k"));if(cd)cd.classList.toggle("open");return;}
  var b=e.target.closest("[data-act]");
  if(b){var act=b.getAttribute("data-act");
    if(act=="open")window.open(b.getAttribute("data-u"),"_blank");
    else if(act=="copy")copy(b.getAttribute("data-u"));
    else if(act=="detail")detail(b.getAttribute("data-p"));
    else if(act=="embed")toggleEmbed(b.getAttribute("data-p"));
    document.querySelectorAll(".cdrop.open").forEach(function(d){d.classList.remove("open");});return;}
  // Click anywhere on a chip (not a button, not inside the revealed embed, not
  // a link) toggles its expansion -- the whole chip is the disclosure control.
  // .card[data-p] = service/terminal chips only (swarm-node tiles have no data-p).
  var card=e.target.closest(".card[data-p]");
  if(card&&!e.target.closest(".embed")&&!e.target.closest("a")){
    document.querySelectorAll(".cdrop.open").forEach(function(d){d.classList.remove("open");});
    toggleEmbed(card.getAttribute("data-p"));return;}
  if(e.target.id=="modal")closeM();
  if(!e.target.closest("#menu")&&e.target.id!="menuBtn")$("menu").classList.remove("open");
});
$("menuBtn").onclick=function(){$("menu").classList.toggle("open");};
$("chatToggle").onclick=function(){$("chatwrap").classList.toggle("min");};
$("refresh").onchange=function(){OPTS.refresh=+this.value;arm();};
$("sort").onchange=function(){OPTS.sort=this.value;cards();};
$("onlydown").onchange=function(){OPTS.onlydown=this.checked;cards();};
$("appgo").onclick=searchApps;
$("appq").addEventListener("keydown",function(e){if(e.key=="Enter")searchApps();});
$("wsform").addEventListener("submit",function(e){e.preventDefault();
  var q=$("wsq").value.trim();if(!q)return;
  var base=(SEARX||("https://"+location.hostname+":"+_MIOS_PORT_SEARXNG+"/")).replace(/\/+$/,"");
  window.open(base+"/search?q="+encodeURIComponent(q),"_blank");});
if("serviceWorker" in navigator){
  navigator.serviceWorker.register("/sw.js",{updateViaCache:"none"})
    .then(function(r){r.update();}).catch(function(){});
  var _swReloaded=false;
  navigator.serviceWorker.addEventListener("controllerchange",function(){
    if(!_swReloaded){_swReloaded=true;location.reload();}});}
// PWA install option: capture the install prompt and
// expose it as an in-portal button; fall back to browser-menu instructions.
var deferredPrompt=null;
window.addEventListener("beforeinstallprompt",function(e){
  e.preventDefault();deferredPrompt=e;});
window.addEventListener("appinstalled",function(){
  deferredPrompt=null;$("installBtn").style.display="none";toast("MiOS installed");});
if(window.matchMedia&&window.matchMedia("(display-mode: standalone)").matches)
  $("installBtn").style.display="none";  // already running as the installed app
$("installBtn").onclick=function(){
  if(deferredPrompt){deferredPrompt.prompt();
    deferredPrompt.userChoice.then(function(){deferredPrompt=null;});}
  else{toast("Browser menu → Install app / Add to Home Screen");}};

function showView(view, push) {
  if (view === "settings") {
    $("dashboard-view").style.display = "none";
    $("settings-view").style.display = "block";
    $("settingsToggle").classList.add("active");
    var iframe = $("settings-iframe");
    if (!iframe.dataset.loaded) {
      iframe.src = "/portal/configurator";
      iframe.dataset.loaded = "1";
    }
    if (push) history.pushState({view: "settings"}, "", "/configure");
  } else {
    $("settings-view").style.display = "none";
    $("dashboard-view").style.display = "block";
    $("settingsToggle").classList.remove("active");
    if (push) history.pushState({view: "dashboard"}, "", "/");
  }
}
$("settingsToggle").onclick = function(e) {
  e.preventDefault();
  var isSettings = $("settings-view").style.display === "block";
  showView(isSettings ? "dashboard" : "settings", true);
};
$("logoLink").onclick = function(e) {
  e.preventDefault();
  showView("dashboard", true);
};
// "Edit in Settings ->" opens the configurator via the same in-app view switch
// as the gear; falls back to a real /configure navigation if JS is unavailable.
var _cfgEdit=$("cfgedit");
if(_cfgEdit)_cfgEdit.onclick=function(e){e.preventDefault();showView("settings",true);};
window.onpopstate = function(e) {
  if (location.pathname === "/configure") {
    showView("settings", false);
  } else {
    showView("dashboard", false);
  }
};
var initPath = location.pathname;
if (initPath === "/configure") {
  showView("settings", false);
} else {
  showView("dashboard", false);
}

tick();arm();tickConfig();
</script></body></html>"""

def _portal_theme_css() -> str:
    try:
        import mios_toml
        from mios_pipe.routing import portal_edge
        c = mios_toml.colors()
    except Exception:
        return ""
    roles = {"--bg": c.get("bg"), "--fg": c.get("fg"),
             "--panel": c.get("accent"), "--accent": c.get("cursor"),
             "--ok": c.get("success"), "--bad": c.get("error"),
             "--mut": c.get("subtle") or c.get("muted"),
             "--silver": c.get("silver"), "--earth": c.get("earth"),
             "--warn": c.get("warning") or c.get("ansi_11_bright_yellow"),
             "--info": c.get("info") or c.get("ansi_12_bright_blue"),
             "--ok2": c.get("ansi_10_bright_green") or c.get("success"),
             "--rust": c.get("ansi_13_bright_magenta") or c.get("earth"),
             "--subtle": c.get("subtle") or c.get("muted")}
    decl = ";".join(f"{k}:{v}" for k, v in roles.items() if isinstance(v, str) and v.startswith("#"))
    return (f"<style>:root{{{decl}}}</style>" if decl else "") + portal_edge.style_block(safe=True)

# The static PWA assets live in portal_assets (imported here, where they were
# defined, so the icon reads keep their place in the import sequence).
# Re-exported: the routes below, server.py and the tests read them as
# mios_portal.<name>.
from mios_pipe.routing.portal_assets import (  # noqa: E402,F401 -- re-exported
    _PORTAL_ICON, _read_portal_asset, _PORTAL_ICON_192, _PORTAL_ICON_512,
    _PORTAL_MANIFEST, _PORTAL_SW, _PORTAL_LOGIN_HTML, _IOSTEST_HTML,
)

async def _portal_swarm_probe(name: str, cfg: dict, client) -> dict:
    ep = (cfg.get("endpoint") or "").rstrip("/")
    t0 = time.time()
    reachable, live = False, []
    try:  # OpenAI /v1/models
        r = await client.get(f"{ep}/models", headers=_probe_auth_headers(ep))
        if r.status_code < 500:
            reachable = True
            live = [str(m.get("id")) for m in
                    ((r.json() or {}).get("data") or [])
                    if isinstance(m, dict) and m.get("id")]
    except Exception:
        pass
    return {"name": name, "role": cfg.get("role", ""),
            "lane": _agent_lane(cfg), "endpoint": ep,
            "model": cfg.get("model", ""), "live_models": live[:8],
            "reachable": reachable, "ms": int((time.time() - t0) * 1000),
            "default": bool(cfg.get("default")),
            "fanout": bool(cfg.get("fanout", True)),
            "health_gate": bool(cfg.get("health_gate")),
            "strengths": cfg.get("strengths") or []}

async def portal_stats_logic(request: Request) -> JSONResponse:
    """Live server-side health of every discovered MiOS service + host
    stats + best-effort container state. Self-signed backends checked
    insecurely. The single source the dashboard polls."""
    if not _portal_authed(request):
        return JSONResponse({"error": "auth required"}, status_code=401)
    pmap = await _podman_ps()

    async def _check(svc: dict, client) -> dict:
        t0 = time.time()
        ok = False
        try:
            r = await client.get(svc["local"])
            ok = r.status_code < 500
        except Exception:
            ok = False
        cinfo = (pmap["port"].get(svc["port"])
                 or pmap["name"].get(svc.get("container_name", ""), {}))
        return {"name": svc["name"], "port": svc["port"], "ok": ok,
                "ms": int((time.time() - t0) * 1000),
                "internal": svc["local"], "kind": svc.get("kind", ""),
                "container": cinfo.get("container", ""),
                "state": cinfo.get("state", ""),
                "image": cinfo.get("image", ""),
                "url": f"https://{PORTAL_PUBLIC_HOST}:{svc['port']}"
                       f"{svc.get('path', '/')}"}
    async with httpx.AsyncClient(verify=_PROBE_VERIFY_TLS, timeout=4.0,
                                 follow_redirects=False) as client:
        services = await asyncio.gather(
            *[_check(s, client) for s in _PORTAL_SERVICES])
    return JSONResponse({"host": _host_stats(), "services": services,
                         "ts": int(time.time())})

async def portal_service_detail_logic(port: int, request: Request) -> JSONResponse:
    """On-demand detail for one service (clicked in the dashboard): live
    status + container state/image + recent log lines (best-effort)."""
    if not _portal_authed(request):
        return JSONResponse({"error": "auth required"}, status_code=401)
    svc = next((s for s in _PORTAL_SERVICES if s["port"] == port), None)
    if not svc:
        return JSONResponse({"error": "unknown service"}, status_code=404)
    pmap = await _podman_ps()
    cinfo = (pmap["port"].get(port)
             or pmap["name"].get(svc.get("container_name", ""), {}))
    logs = ""
    cname = cinfo.get("container", "")
    if cname:
        try:
            proc = await asyncio.create_subprocess_exec(
                "podman", "logs", "--tail", "40", cname,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            logs = _sanitize_tool_text((out or b"").decode(
                "utf-8", "replace"))[-4000:]
        except Exception:
            try: proc.kill()
            except: pass
            logs = ""
    ok = False
    async with httpx.AsyncClient(verify=_PROBE_VERIFY_TLS, timeout=4.0,
                                 follow_redirects=False) as client:
        try:
            r = await client.get(svc["local"])
            ok = r.status_code < 500
        except Exception:
            ok = False
    return JSONResponse({
        "name": svc["name"], "port": port, "ok": ok,
        "internal": svc["local"],
        "url": f"https://{PORTAL_PUBLIC_HOST}:{port}{svc.get('path', '/')}",
        "container": cname, "state": cinfo.get("state", ""),
        "image": cinfo.get("image", ""), "logs": logs})

async def portal_swarm_logic(request: Request) -> JSONResponse:
    """Live SWARM roster ('emitters for all nodes/
    endpoints to confirm live the nodes/models/endpoints'): every registered
    agent/node with live reachability + the model(s) it actually serves
    (probed, not just configured). health_gate client nodes (mobile/Tailscale)
    show up/down as they join/leave the swarm."""
    if not _portal_authed(request):
        return JSONResponse({"error": "auth required"}, status_code=401)
    async with httpx.AsyncClient(verify=_PROBE_VERIFY_TLS, timeout=3.0,
                                 follow_redirects=False) as client:
        agents = await asyncio.gather(
            *[sys.modules["mios_portal"]._portal_swarm_probe(n, c, client)
              for n, c in _AGENT_REGISTRY.items()])
    agents.sort(key=lambda a: (not a["reachable"], a["name"]))
    up = sum(1 for a in agents if a["reachable"])
    return JSONResponse({"agents": agents, "up": up,
                         "total": len(agents), "ts": int(time.time())})

async def portal_term_ws_logic(ws: WebSocket, port: int):
    """Same-origin WebSocket bridge to a loopback ttyd. The operator's device
    reaches the portal but NOT ttyd's 127.0.0.1:<port> directly (loopback-only,
    not tailscale-served), so the native xterm embed connects here and we proxy
    to ttyd inside the VM -- works from any device with no per-port serve."""
    if PORTAL_REQUIRE_LOGIN and not _portal_token_ok(
            ws.cookies.get(PORTAL_COOKIE)):
        await ws.close(code=1008)
        return
    if port not in {s["port"] for s in _PORTAL_SERVICES
                    if s.get("kind") == "terminal"}:
        await ws.close(code=1008)
        return
    await ws.accept(subprotocol="tty")
    try:
        upstream = await websockets.connect(
            f"ws://127.0.0.1:{port}/ws", subprotocols=["tty"],
            max_size=None, open_timeout=10)
    except Exception as e:
        log.warning("portal term proxy: ttyd :%s connect failed -- %s", port, e)
        try:
            await ws.close(code=1011)
        except Exception:
            pass
        return

    async def client_to_ttyd():
        try:
            while True:
                m = await ws.receive()
                if m.get("type") == "websocket.disconnect":
                    break
                d = m.get("bytes")
                if d is None and m.get("text") is not None:
                    d = m["text"].encode("utf-8")
                if d is not None:
                    await upstream.send(d)
        except Exception:
            pass
        finally:
            try:
                await upstream.close()
            except Exception:
                pass

    async def ttyd_to_client():
        try:
            async for msg in upstream:
                if isinstance(msg, (bytes, bytearray)):
                    await ws.send_bytes(bytes(msg))
                else:
                    await ws.send_text(msg)
        except Exception:
            pass
        finally:
            try:
                await ws.close()
            except Exception:
                pass

    await asyncio.gather(client_to_ttyd(), ttyd_to_client())

async def portal_login_page_logic(request: Request, e: int = 0):
    if _portal_authed(request):
        return RedirectResponse("/", status_code=303)
    err = ('<div class="err">Incorrect password &mdash; try again.</div>'
           if e else "")
    html = _PORTAL_LOGIN_HTML.replace("{ERR}", err)
    return HTMLResponse(html.replace("</head>", _portal_theme_css() + "</head>"),
                        headers={"Cache-Control": "no-store, must-revalidate"})

async def portal_login_logic(request: Request):
    from urllib.parse import parse_qs
    body = (await request.body()).decode("utf-8", "replace")
    pw = (parse_qs(body).get("password") or [""])[0]
    wants_json = "application/json" in request.headers.get("accept", "")
    if PORTAL_REQUIRE_LOGIN and not hmac.compare_digest(pw, PORTAL_PASSWORD):
        if wants_json:
            return JSONResponse({"error": "incorrect password"}, status_code=401)
        return RedirectResponse("/login?e=1", status_code=303)
    token = _portal_make_token(PORTAL_USER)
    if wants_json:
        return JSONResponse({"token": token, "user": PORTAL_USER,
                              "expires_in": PORTAL_SESSION_TTL})
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(PORTAL_COOKIE, token,
                    max_age=PORTAL_SESSION_TTL, httponly=True,
                    samesite="lax", path="/")
    return resp

async def portal_page_logic(request: Request):
    if not _portal_authed(request):
        return RedirectResponse("/login", status_code=303)
    _port_owui = _portal_port("MIOS_PORTS_OPEN_WEBUI", "open_webui", 8200)
    _port_searxng = _portal_port("MIOS_PORTS_SEARXNG", "searxng", 8800)
    _port_inject = (
        f"<script>var _MIOS_PORT_OWUI={_port_owui};"
        f"var _MIOS_PORT_SEARXNG={_port_searxng};</script></head>"
    )
    return HTMLResponse(
        _PORTAL_HTML
        .replace("</head>", _portal_theme_css() + "</head>")
        .replace("</head>", _port_inject, 1),
        headers={"Cache-Control": "no-store, must-revalidate"})

async def portal_configure_page_logic(request: Request):
    """Serve the MiOS Configurator as a unified portal sub-page (auth-gated).
    Reads mios.html from disk at request time so live edits are reflected
    immediately without a process restart. Injects the SSOT palette so the
    configurator tracks the operator's theme just like the dashboard does."""
    if not _portal_authed(request):
        return RedirectResponse("/login", status_code=303)
    html_path = os.environ.get(
        "MIOS_CONFIGURATOR_HTML",
        "/usr/share/mios/configurator/mios.html")
    try:
        with open(html_path, "r", encoding="utf-8") as fh:
            html = fh.read()
    except OSError:
        log.warning("portal configure: configurator not found at %s", html_path)
        return HTMLResponse(
            "<h1 style='font-family:sans-serif;padding:40px'>Configurator not found</h1>",
            status_code=404)
    html = html.replace("</head>", _portal_theme_css() + "</head>", 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store, must-revalidate"})

portal_router = APIRouter()

@portal_router.get("/portal/stats")
async def portal_stats(request: Request) -> JSONResponse:
    """Live server-side health of every discovered MiOS service + host stats +
    best-effort container state -- the single source the dashboard polls. Calls
    portal_stats_logic (same module)."""
    return await portal_stats_logic(request)

@portal_router.get("/portal/service/{port}")
async def portal_service_detail(port: int, request: Request) -> JSONResponse:
    """On-demand detail for one clicked service (live status + container state/image
    + recent log lines). Calls portal_service_detail_logic (same module)."""
    return await portal_service_detail_logic(port, request)

@portal_router.get("/portal/swarm")
async def portal_swarm(request: Request) -> JSONResponse:
    """Live swarm roster -- every registered agent/node probed for reachability +
    the model(s) it actually serves. Calls portal_swarm_logic (same module)."""
    return await portal_swarm_logic(request)

@portal_router.get("/portal/icon.svg")
async def portal_icon() -> Response:
    return Response(_PORTAL_ICON, media_type="image/svg+xml")

@portal_router.get("/portal/icon-192.png")
async def portal_icon_192() -> Response:
    if not _PORTAL_ICON_192:
        return Response(_PORTAL_ICON, media_type="image/svg+xml")
    return Response(_PORTAL_ICON_192, media_type="image/png")

@portal_router.get("/portal/icon-512.png")
async def portal_icon_512() -> Response:
    if not _PORTAL_ICON_512:
        return Response(_PORTAL_ICON, media_type="image/svg+xml")
    return Response(_PORTAL_ICON_512, media_type="image/png")

@portal_router.get("/portal/manifest.webmanifest")
async def portal_manifest() -> Response:
    return Response(_PORTAL_MANIFEST, media_type="application/manifest+json")

@portal_router.get("/portal/xterm.js")
async def portal_xterm_js() -> Response:
    return Response(_read_portal_asset("xterm.js"),
                    media_type="application/javascript")

@portal_router.get("/portal/xterm.css")
async def portal_xterm_css() -> Response:
    return Response(_read_portal_asset("xterm.css"), media_type="text/css")

@portal_router.get("/portal/addon-fit.js")
async def portal_addon_fit() -> Response:
    return Response(_read_portal_asset("addon-fit.js"),
                    media_type="application/javascript")

@portal_router.websocket("/portal/term/{port}")
async def portal_term_ws(ws: WebSocket, port: int):
    """Same-origin WebSocket bridge to a loopback ttyd terminal. Calls
    portal_term_ws_logic (same module)."""
    return await portal_term_ws_logic(ws, port)

@portal_router.post("/portal/login")
async def portal_login(request: Request):
    """Portal login POST -- validate the password + set the signed session cookie.
    Calls portal_login_logic (same module)."""
    return await portal_login_logic(request)

@portal_router.get("/portal/logout")
async def portal_logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(PORTAL_COOKIE, path="/")
    return resp

@portal_router.get("/sw.js")
async def portal_sw() -> Response:
    return Response(_PORTAL_SW, media_type="application/javascript")

@portal_router.get("/login", response_class=HTMLResponse)
async def portal_login_page(request: Request, e: int = 0):
    """Portal login page. Calls portal_login_page_logic (same module)."""
    return await portal_login_page_logic(request, e)

@portal_router.get("/iostest", response_class=HTMLResponse)
async def iostest_page():
    return HTMLResponse(_IOSTEST_HTML,
                        headers={"Cache-Control": "no-store, must-revalidate"})

@portal_router.get("/portal/configurator", response_class=HTMLResponse)
async def portal_configurator_iframe(request: Request):
    """Serve the raw mios.html from disk to be loaded inside the portal iframe."""
    return await portal_configure_page_logic(request)

def run_db_reseed_bg():
    import tempfile
    import subprocess
    import sys

    if "/usr/lib/mios" not in sys.path:
        sys.path.insert(0, "/usr/lib/mios")
    import mios_toml
    from mios_pipe.kernel.config import to_toml

    tmp_path = None
    try:
        merged = mios_toml.load_merged()
        with tempfile.NamedTemporaryFile(suffix=".toml", delete=False, mode="w", encoding="utf-8") as tmp:
            tmp.write(to_toml(merged))
            tmp_path = tmp.name

        env = os.environ.copy()
        env["MIOS_TOML"] = tmp_path
        env["MIOS_VENDOR_TOML"] = tmp_path

        seeder_path = os.environ.get("MIOS_SEED_DB_CONFIG", "/usr/libexec/mios/seed-db-config.py")

        subprocess.run([sys.executable, seeder_path], env=env, check=True)
    except Exception as e:
        log.error("Failed to run background db-config re-seed: %s", e)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

@portal_router.get("/portal/config")
async def get_portal_config(request: Request):
    """GET /portal/config -> return the live layered mios.toml as text/plain."""
    if not _portal_authed(request):
        return JSONResponse({"error": "auth required"}, status_code=401)

    import sys
    if "/usr/lib/mios" not in sys.path:
        sys.path.insert(0, "/usr/lib/mios")
    import mios_toml
    from mios_pipe.kernel.config import to_toml

    try:
        merged_config = mios_toml.load_merged()
        toml_text = to_toml(merged_config)
        return Response(content=toml_text, media_type="text/plain")
    except Exception as e:
        log.error("Failed to load/serialize layered config: %s", e)
        return Response(content="Unable to load configuration", status_code=500, media_type="text/plain")

@portal_router.post("/portal/config")
async def post_portal_config(request: Request, background_tasks: BackgroundTasks):
    """POST /portal/config -> Receive replacement TOML, write to USER TOML layer,
    and trigger db-config re-seeding in background."""
    if not _portal_authed(request):
        return JSONResponse({"error": "auth required"}, status_code=401)

    body_bytes = await request.body()
    toml_text = body_bytes.decode("utf-8")

    try:
        import tomllib as _toml
    except ImportError:
        import tomli as _toml  # type: ignore

    try:
        parsed_config = _toml.loads(toml_text)
    except Exception as e:
        log.warning("Invalid TOML posted to /portal/config: %s", e)
        return JSONResponse({"error": "Invalid TOML syntax"}, status_code=400)

    from mios_pipe.kernel.config import write_user_config, validate_config

    live_config = None
    try:
        import sys
        if "/usr/lib/mios" not in sys.path:
            sys.path.insert(0, "/usr/lib/mios")
        import mios_toml
        live_config = mios_toml.load_merged()
    except Exception as e:
        log.warning("validate_config: could not load live config for drop-check: %s", e)

    ok, errors = validate_config(toml_text, live_config)
    if not ok:
        log.warning("Rejected unsafe config POST (422): %s", errors)
        return JSONResponse({"error": "validation failed", "errors": errors},
                            status_code=422)

    try:
        write_user_config(parsed_config)

        background_tasks.add_task(run_db_reseed_bg)

        return JSONResponse({"status": "ok"})
    except Exception as e:
        log.error("Failed to save config: %s", e)
        return JSONResponse({"error": "Unable to save configuration"}, status_code=500)

_THEME_RENDER_BIN = os.environ.get(
    "MIOS_THEME_RENDER", "/usr/libexec/mios/mios-dotfiles-render")

def _portal_user_toml_path() -> str:
    """The user-layer override path, resolved exactly like mios_toml's USER
    layer: $MIOS_USER_TOML, else $XDG_CONFIG_HOME/mios/mios.toml, else
    ~/.config/mios/mios.toml. Read-only -- used only to report presence."""
    return (os.environ.get("MIOS_USER_TOML")
            or os.path.join(
                os.environ.get("XDG_CONFIG_HOME",
                               os.path.expanduser("~/.config")),
                "mios", "mios.toml"))

def _portal_theme_check() -> dict:
    """Run ``mios-theme-render check`` and report the projection state WITHOUT
    ever writing. Returns {state, exit, summary}: state is 'PASS' (exit 0),
    'FAIL' (non-zero exit), or 'unknown' (the check could not be run at all --
    degrade-open, never raises). summary is the first PASS/FAIL line emitted."""
    import subprocess
    try:
        proc = subprocess.run([_THEME_RENDER_BIN, "check"],
                              capture_output=True, text=True, timeout=12)
    except Exception:  # noqa: BLE001 -- binary absent / timeout -> unknown
        return {"state": "unknown", "exit": None, "summary": ""}
    blob = (proc.stdout or "") + "\n" + (proc.stderr or "")
    summary = ""
    for line in blob.splitlines():
        if "PASS:" in line or "FAIL:" in line:
            summary = line.strip()
            break
    return {"state": "PASS" if proc.returncode == 0 else "FAIL",
            "exit": proc.returncode, "summary": summary}

def _portal_config_status() -> dict:
    merged: dict = {}
    try:
        import sys as _sys
        if "/usr/lib/mios" not in _sys.path:
            _sys.path.insert(0, "/usr/lib/mios")
        import mios_toml
        merged = mios_toml.load_merged() or {}
    except Exception:  # noqa: BLE001 -- fall back to the single-file tomllib read
        merged = _portal_toml() or {}
    identity = merged.get("identity") if isinstance(
        merged.get("identity"), dict) else {}
    meta = merged.get("meta") if isinstance(merged.get("meta"), dict) else {}
    user = str(identity.get("mios_user") or identity.get("username") or "")
    version = str(meta.get("mios_version") or "")
    sections = sum(1 for v in merged.values() if isinstance(v, dict))
    user_path = _portal_user_toml_path()
    try:
        override = os.path.isfile(user_path)
    except Exception:  # noqa: BLE001
        override = False
    theme = _portal_theme_check()
    return {"user": user, "version": version, "sections": sections,
            "override": override, "override_path": user_path,
            "theme": theme.get("state", "unknown"),
            "theme_exit": theme.get("exit"),
            "theme_summary": theme.get("summary", "")}

@portal_router.get("/portal/config/status")
async def get_portal_config_status(request: Request):
    if not _portal_authed(request):
        return JSONResponse({"error": "auth required"}, status_code=401)
    try:
        status = await asyncio.to_thread(_portal_config_status)
        return JSONResponse(status)
    except Exception as e:  # noqa: BLE001 -- never crash the dashboard fetch
        log.warning("portal config status unavailable: %s", e)
        return JSONResponse({"error": "unavailable"})

@portal_router.get("/configure", response_class=HTMLResponse)
async def portal_configure_page(request: Request):
    """MiOS Settings — the configurator as a unified portal sub-page.
    Serves the unified Portal shell HTML so it boots as settings view client-side."""
    return await portal_page_logic(request)

@portal_router.get("/", response_class=HTMLResponse)
async def portal_page(request: Request):
    """Portal dashboard page. Calls portal_page_logic (same module)."""
    return await portal_page_logic(request)
