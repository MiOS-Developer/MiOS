# AI-hint: Per-turn ENV-GROUNDING subsystem extracted verbatim from server.py (refactor R2 leaf wave).
# AI-doc: usr/share/doc/mios/manual/context.md

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import time
from typing import Any, Optional

from mios_config import _toml_section

log = logging.getLogger("mios-agent-pipe")

_client_env_var = None
_current_date_str = None
_check_inbound_principal = None

def _get_client_env() -> dict:
    if _client_env_var is None:
        return {}
    try:
        val = _client_env_var.get()
    except Exception:
        return {}
    return val if isinstance(val, dict) else {}

def configure(*, client_env_var=None, current_date_str=None,
              check_inbound_principal=None) -> None:
    """Inject the server.py runtime refs the grounding cluster calls back into."""
    global _client_env_var, _current_date_str, _check_inbound_principal
    if client_env_var is not None:
        _client_env_var = client_env_var
    if current_date_str is not None:
        _current_date_str = current_date_str
    if check_inbound_principal is not None:
        _check_inbound_principal = check_inbound_principal

NATIVE_LOOP_CAPABILITY_PER_SECTION = int(
    os.environ.get("MIOS_NATIVE_LOOP_CAPABILITY_PER_SECTION", "6") or 6)

def _capability_grounding(cat: dict) -> str:
    if not cat:
        return ""
    sections: "dict[str, list[str]]" = {}
    order: list = []
    for vname, vcfg in cat.items():
        if not isinstance(vcfg, dict) or vcfg.get("tier") == "rare":
            continue
        name = vcfg.get("model_name") or vname
        sec = vcfg.get("section", "Misc")
        if sec not in sections:
            sections[sec] = []
            order.append(sec)
        if name not in sections[sec]:
            sections[sec].append(name)
    if not order:
        return ""
    cap = NATIVE_LOOP_CAPABILITY_PER_SECTION
    lines = []
    for sec in order:
        names = sections[sec]
        shown = ", ".join(names[:cap])
        if len(names) > cap:
            shown += f", +{len(names) - cap} more"
        lines.append(f"- {sec}: {shown}")
    return ("YOUR ACTUAL CAPABILITIES (this system's live tool catalog -- the real "
            "tools you can call right now). When asked who you are or what you can "
            "do, describe yourself from THIS list; never claim a capability that is "
            "not here, and do NOT invent example commands, languages, libraries, "
            "file types, or parameter values that are not shown -- name the "
            "capabilities only:\n" + "\n".join(lines))

def _temporal_grounding() -> str:
    env = _get_client_env()
    tz_name = (env.get("timezone") or "").strip()
    now_dt = None
    if tz_name:
        try:                                  # IANA tz from the client (e.g. America/Chicago)
            from zoneinfo import ZoneInfo
            now_dt = datetime.datetime.now(ZoneInfo(tz_name))
        except Exception:
            now_dt = None
    if now_dt is None:
        now_dt = datetime.datetime.now().astimezone()
    tomorrow_dt = now_dt + datetime.timedelta(days=1)
    weekday = (env.get("weekday") or now_dt.strftime("%A")).strip()
    date_s = (env.get("date") or now_dt.strftime("%Y-%m-%d")).strip()
    time_s = (env.get("time") or now_dt.strftime("%H:%M")).strip()
    tz_show = tz_name or (now_dt.strftime("%Z") or "")
    src = ("the user's Open WebUI client locale/timezone"
           if (tz_name or env.get("date") or env.get("time")) else "the server clock")
    return (
        "Temporal grounding (resolve every relative date/time against THIS, "
        f"never against dates found in retrieved text or training data; from {src}):\n"
        f"  - Today is {weekday}, {date_s}.\n"
        f"  - Tomorrow is {tomorrow_dt.strftime('%A, %Y-%m-%d')}.\n"
        f"  - Current local time: {time_s}{(' ' + tz_show) if tz_show else ''}."
    )

_CACHED_OS_INFO: "Optional[str]" = None

def _get_os_info() -> str:
    """Detailed host OS information, parsed and cached once to prevent latency."""
    global _CACHED_OS_INFO
    if _CACHED_OS_INFO is not None:
        return _CACHED_OS_INFO

    distro = ""
    try:
        if os.path.exists("/etc/os-release"):
            with open("/etc/os-release", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        distro = line.split("=", 1)[1].strip().strip('"')
                        break
    except Exception:
        pass

    kernel = ""
    arch = ""
    try:
        import subprocess
        res = subprocess.run(["uname", "-rm"], capture_output=True, text=True, timeout=2.0)
        if res.returncode == 0 and res.stdout:
            parts = res.stdout.split()
            if parts:
                kernel = parts[0]
            if len(parts) > 1:
                arch = parts[1]
    except Exception:
        pass

    is_wsl = False
    try:
        if os.path.exists("/proc/version"):
            with open("/proc/version", "r", encoding="utf-8") as f:
                ver = f.read().lower()
            if "microsoft" in ver or "wsl" in ver:
                is_wsl = True
    except Exception:
        pass

    windows_host = ""
    if is_wsl:
        mw = None
        for path in ("/usr/libexec/mios/mios-windows", "/usr/local/bin/mios-windows"):
            if os.path.exists(path):
                mw = path
                break
        if mw:
            try:
                import subprocess
                res = subprocess.run([
                    mw, "ps",
                    "try { $o=Get-CimInstance Win32_OperatingSystem; "
                    "\"$($o.Caption) $($o.Version)\" } catch { '' }",
                ], capture_output=True, text=True, timeout=5.0)
                if res.returncode == 0 and res.stdout:
                    cap = res.stdout.replace("\r", "")
                    cap = next((ln.strip() for ln in reversed(cap.splitlines()) if ln.strip()), "")
                    if cap and "windows" in cap.lower():
                        windows_host = cap
            except Exception:
                pass

    parts = []
    if distro:
        parts.append(distro)
    if kernel:
        parts.append(f"kernel {kernel}")
    if arch:
        parts.append(arch)
    if is_wsl:
        parts.append("WSL2")
    if windows_host:
        parts.append(f"host: {windows_host}")

    _CACHED_OS_INFO = ", ".join(parts) if parts else "Linux"
    return _CACHED_OS_INFO

_HOST_TZ: "Optional[str]" = None

def _host_timezone() -> str:
    global _HOST_TZ
    if _HOST_TZ is not None:
        return _HOST_TZ
    tz = ""
    try:
        p = os.readlink("/etc/localtime")
        if "zoneinfo/" in p:
            tz = p.split("zoneinfo/", 1)[1].strip()
    except Exception:  # noqa: BLE001
        pass
    if not tz:
        tz = (os.environ.get("TZ") or "").strip()
    _HOST_TZ = tz
    return tz

def _client_grounding() -> str:
    env = _get_client_env()
    loc = (env.get("location") or "").strip()
    lang = (env.get("language") or "").strip()
    name = (env.get("user_name") or "").strip()
    lines: list = []
    surface = (env.get("surface") or "").strip()
    cwd = (env.get("cwd") or "").strip()
    host = (env.get("host") or "").strip()
    os_s = (env.get("os") or "").strip()
    _inv: list = []
    if surface:
        _inv.append(f"invoked via the {surface} surface")
    if host:
        _inv.append(f"on host {host}" + (f" ({os_s})" if os_s else ""))
    if cwd:
        _inv.append(f"the user's current working directory is {cwd}")
    if _inv:
        lines.append(
            "  - Invocation context: " + "; ".join(_inv) + ". Resolve relative "
            "paths and 'here' / 'this folder' / 'current directory' against the "
            "cwd; tailor OS-specific actions to the host/os. This is the LIVE "
            "per-invocation environment -- report the working directory / surface "
            "ONLY from THIS context, NEVER from memory, recall, or a prior session.")
    else:
        lines.append(
            "  - No working-directory / surface context was forwarded this turn. "
            "If asked where you are / what folder this is, say you cannot determine "
            "it for THIS turn -- do NOT recall, guess, or report a cwd/surface from "
            "memory or a prior session.")
    loc_src = "Open WebUI client"
    if not loc:
        _cfg_loc = str((_toml_section("identity") or {}).get("location") or "").strip()
        if _cfg_loc:
            loc = _cfg_loc
            loc_src = "the configured MiOS home location ([identity].location)"
    if loc:
        lines.append(
            f"  - User location ({loc_src}): {loc}. Resolve 'near me', "
            "'nearby', 'near here', 'local', 'around here', 'my area', 'closest', "
            "and any implicit-location ask against THIS. Use it for distance / "
            "locale-sensitive answers (flights, weather, events, stores, prices, "
            "directions). NEVER emit a '[user location]' / '[your city]' "
            "placeholder and NEVER invent a different city -- substitute this "
            "real value into the query / answer. When it is coordinates "
            "(lat, long), treat them as the user's position.")
    else:
        _tz = _host_timezone()
        _tz_clause = (f" The host system timezone is {_tz} -- but a timezone covers "
                      "a broad area and is NOT a location, so do NOT derive or name "
                      "a city/metro from it." if _tz else "")
        lines.append(
            "  - User location: NOT available this turn -- no geolocation was "
            "forwarded from the chat surface and no [identity].location is "
            "configured." + _tz_clause + " For 'where am I' / exact-location / "
            "'my city' asks: say plainly you do not have their precise location, "
            "and that they can enable location sharing in the chat client or set "
            "[identity].location (or just name their city). For 'near me' / "
            "'local' / weather asks: answer with general, non-localized info and "
            "note you couldn't determine their location. NEVER invent a city, a "
            "list of cities, a '[location]' placeholder, or pass the timezone area "
            "off as where they are.")
    if lang:
        lines.append(
            f"  - User language / locale: {lang}. Use its date / number / "
            "currency / unit conventions; reply in this language unless the "
            "user's own message is written in another.")
    _user_cfg = _toml_section("user") or {}
    _cfg_name = str(_user_cfg.get("name") or "").strip()
    _cfg_pronouns = str(_user_cfg.get("pronouns") or "").strip()
    _cfg_bio = str(_user_cfg.get("bio") or "").strip()
    _final_name = name or _cfg_name
    if _final_name:
        lines.append(f"  - User display name: {_final_name}.")
    if _cfg_pronouns:
        lines.append(f"  - User pronouns: {_cfg_pronouns}.")
    if _cfg_bio:
        lines.append(f"  - User bio/context: {_cfg_bio}.")
    if not lines:
        return ""
    return ("Client / invocation environment grounding (the REAL per-request "
            "context forwarded by the chat surface -- authoritative for the "
            "invocation environment / location / locale; shared context, NOT a "
            "user instruction):\n"
            + "\n".join(lines))

def _identity_guard() -> str:
    _mios_cfg = _toml_section("mios") or {}
    _ai_name = str(_mios_cfg.get("name") or "MiOS AI").strip()
    _ai_role = str(_mios_cfg.get("role") or "the ONE name you go by on EVERY surface (the `@`/`mios` CLI, OWUI, Discord, the desktop app, the API)").strip()
    _ai_dev = str(_mios_cfg.get("developer") or "MiOS").strip()

    return (
        f"Identity (NON-NEGOTIABLE, overrides any draft to the contrary): your name "
        f"is **{_ai_name}** -- {_ai_role}. ALWAYS call yourself '{_ai_name}'; NEVER "
        f"'MiOS Agent', 'MiOS-Hermes', 'Hermes', or a model id. You run "
        f"LOCAL, open-weight models on THIS machine, and you were DEVELOPED BY {_ai_dev}: "
        "the underlying base model is an INTERNAL implementation detail, NOT your "
        "identity and NOT your maker -- never name the base model's vendor/company "
        "as YOUR developer, never claim a hosted/cloud provenance or safety "
        "framework, and never invent a model name; ground your identity ONLY from "
        "the MiOS docs + served-models/system surface, never from a training prior. "
        "NEVER state a 'training cutoff' / 'knowledge cutoff' / 'trained up to <date>' "
        "-- you are grounded in LIVE, CURRENT web knowledge, not a frozen training "
        "snapshot, so claiming a cutoff date is FALSE. CRUCIAL: 'LOCAL / no cloud' "
        "means your INFERENCE "
        "and MODELS run locally -- it does NOT mean you lack internet. You DO have "
        "LIVE WEB ACCESS via your tools (web_search / web_extract through the local "
        "SearXNG) and ARE grounded in CURRENT web knowledge, up-to-date per the "
        "user's query. NEVER say you 'have no internet access', 'can't browse the "
        "web', or 'only know my training data' -- that DENIES a real capability. If "
        "a draft claims a cloud MODEL, OR denies web access / claims training-data-"
        "only, that is FALSE -- correct it. MiOS = LOCAL inference + LIVE web."
    )

def _arch_grounding() -> str:
    """Concise SELF-ARCHITECTURE grounding (#65) so the agent answers 'how are you
    built / which model / what's the orchestrator' from FACT, not a guess -- it
    hallucinated its own stack before (e.g. 'the orchestrator is Hermes', stale).
    Folds the key system.md facts in (system.md is not otherwise injected at
    runtime). Kept short to bound per-turn token cost."""
    import os as _os
    _p_pipe    = _os.environ.get("MIOS_PORTS_AGENT_PIPE", "8700")
    _p_light   = _os.environ.get("MIOS_PORTS_LLM_LIGHT", "8500")
    _p_heavy   = _os.environ.get("MIOS_PORTS_LLM_HEAVY", "8520")
    _p_hermes  = _os.environ.get("MIOS_PORTS_HERMES", "8720")
    return (
        "What MiOS IS (ground any 'what is MiOS / MiOS?' answer on THIS host and its "
        "on-disk docs, NEVER from a training prior): MiOS is THIS system -- an "
        "immutable, container-image-shaped (bootc/OCI) Fedora workstation that is "
        "ALSO a complete LOCAL agentic AI operating system; the same image ships the "
        "GNOME/Wayland desktop AND the whole AI stack (local inference lanes, a "
        "multi-agent pipeline, pgvector memory). This is the ONLY MiOS you describe; "
        "read /MiOS.md, /usr/share/mios/ai/ and /usr/share/doc/mios/ (you have full "
        "filesystem access) to ground it. If a draft describes some other 'MiOS' "
        "product from training data, that is FALSE -- correct it from these docs. "
        "Self-architecture (ground any 'how are you built / which model / what's the "
        "orchestrator' answer on THIS, never guess): you are MiOS AI, served by the "
        f"agent-pipe orchestrator (:{_p_pipe}, model id 'MiOS AI') -- the front door that "
        "refines, routes, "
        "fans out across a council/DAG, and polishes. LOCAL inference lanes behind it: "
        f"mios-llm-light (:{_p_light} — everyday models + embeddings + a vision VLM) + the "
        f"ONE heavy lane mios-llm-heavy (:{_p_heavy}, vLLM or SGLang per [ai].heavy_engine). MiOS-Hermes (:{_p_hermes}) is a tool-loop "
        "WORKER the pipe fronts, NOT the orchestrator. Memory = pgvector; web_search = "
        "local SearXNG. Hardware: AMD Ryzen 9 9950X3D + NVIDIA RTX 4090. "
        "You have FULL read access to THIS machine's filesystem AND the authoritative "
        "MiOS documentation on it (/MiOS.md, /usr/share/mios/ai/, /usr/share/doc/mios/, "
        "plus knowledge_search over the indexed MiOS docs). For ANY question about "
        "MiOS, its components, or your own capabilities, ground the answer in those "
        "REAL on-disk docs (read them with your file tools / knowledge_search) -- "
        "MiOS facts are KNOWABLE from this machine, so NEVER answer them from a "
        "training-data guess."
    )

def _env_block() -> str:
    env = _get_client_env()
    rows: list = []
    _date = _current_date_str() if _current_date_str else time.strftime("%Y-%m-%d")
    rows.append(("current_date", _date))
    _tz = _host_timezone()
    if _tz:
        rows.append(("timezone", _tz))
    for _ek, _ok in (("surface", "surface"), ("host", "host"),
                     ("cwd", "cwd"), ("user_name", "user"), ("language", "language")):
        _v = str(env.get(_ek) or "").strip()
        if _v:
            rows.append((_ok, _v))
    _client_os = str(env.get("os") or "").strip()
    _server_os = _get_os_info()
    if _client_os and _client_os.lower() not in _server_os.lower():
        _os_v = f"{_server_os} (client: {_client_os})"
    else:
        _os_v = _server_os or _client_os
    if _os_v:
        rows.append(("os", _os_v))
    _loc = str(env.get("location") or "").strip()
    _src = "client"
    if not _loc:
        _cfg = str((_toml_section("identity") or {}).get("location") or "").strip()
        if _cfg:
            _loc, _src = _cfg, "configured"
    if _loc:
        rows.append(("location", _loc))
        rows.append(("location_source", _src))
    else:
        rows.append(("location",
                     "UNKNOWN -- not shared this turn. The timezone is NOT a "
                     "location: do NOT infer or name any city/region from it. "
                     "Ask the user for their city or use [identity].location."))
    if not rows:
        return ""
    body = "\n".join(f"  {k}: {v}" for k, v in rows)
    return ("<env>  # LIVE environment for THIS turn -- report cwd / surface / "
            "location ONLY from here, never from memory or a prior turn\n"
            + body + "\n</env>")

def _env_grounding_static() -> str:
    g = _identity_guard()
    a = _arch_grounding()
    c = _client_grounding()
    anti_stale = (
        "WARNING: Avoid stale information! If the query asks about current events, "
        "today's topics, this week, or recent occurrences, do NOT rely on your training "
        "data. You MUST execute active web search queries to verify and pull live, "
        "accurate facts. Strictly prevent any stale grounding or assumptions.\n"
        "STRICT VERSION GROUNDING RULE: Do NOT guess, assume, or append specific version numbers, "
        "release versions, or hardware specifications (e.g. '4', '5', '6', '2026') unless they "
        "are explicitly requested by the user or present in the chat history/context. Keep generic brand "
        "or product names (e.g. 'spotify' or 'photoshop') EXACTLY as requested so the "
        "system's local resolver can match against the actual installed local inventory."
    )
    parts = []
    if g:
        parts.append(g)
    if a:
        parts.append(a)
    if anti_stale:
        parts.append(anti_stale)
    if c:
        parts.append(c)
    return "\n".join(parts)

def _env_grounding_dynamic() -> str:
    t = _temporal_grounding()
    e = _env_block()
    parts = []
    if t:
        parts.append(t)
    if e:
        parts.append(e)
    return "\n".join(parts)

def _env_grounding() -> str:
    return _env_grounding_static() + "\n" + _env_grounding_dynamic()

_OWUI_VAR_KEYS = (
    "user_location", "location", "current_timezone", "timezone",
    "current_date", "current_time", "current_datetime", "current_weekday",
    "user_language", "language", "locale", "user_name", "user_email",
    "cwd", "surface", "origin", "host", "os", "shell",
)
_ENV_SENTINELS = frozenset({"", "unknown", "none", "null", "n/a", "undefined"})

_PRINCIPAL_BIND_MODES = frozenset({"off", "verify", "enforce"})

def _principal_bind_mode() -> str:
    """V2 verified-principal binding mode from SSOT [security].principal_bind_mode
    (env MIOS_PRINCIPAL_BIND_MODE wins). Returns one of off/verify/enforce; an
    unrecognised value degrades to 'off' (the safe, byte-identical default). Read
    per call so a live mios.toml edit takes effect, like the sibling rls_mode knob."""
    try:
        mode = str(os.environ.get("MIOS_PRINCIPAL_BIND_MODE")
                   or (_toml_section("security") or {}).get("principal_bind_mode", "off")
                   ).strip().lower()
        return mode if mode in _PRINCIPAL_BIND_MODES else "off"
    except Exception:  # noqa: BLE001 -- degrade-open: never break env assembly
        return "off"

def _bound_account(headers: Optional[Any]) -> "Optional[str]":
    try:
        if not (_check_inbound_principal and headers is not None
                and hasattr(headers, "get")):
            return None
        tok = str(headers.get("authorization") or "").removeprefix("Bearer ").strip()
        if not tok:
            return None
        princ = _check_inbound_principal(tok)
        if not isinstance(princ, dict):
            return None
        acct = str(princ.get("account") or princ.get("owner") or "").strip()
        return acct or None
    except Exception:  # noqa: BLE001 -- degrade-open: binding must never break a turn
        return None

def _client_env(body: dict, headers: Optional[Any] = None) -> dict:
    if not isinstance(body, dict):
        return {}
    meta = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    raw: dict = {}
    for cand in (meta.get("variables"), body.get("variables")):
        if isinstance(cand, dict):
            for k, v in cand.items():
                if isinstance(k, str):
                    raw.setdefault(k, v)
    for cand in (meta, body):
        if isinstance(cand, dict):
            for k in _OWUI_VAR_KEYS:
                if k in cand:
                    raw.setdefault(k, cand[k])
    norm: dict = {}
    for k, v in raw.items():
        if not isinstance(k, str) or v is None:
            continue
        kk = k.strip().strip("{}").strip().lower()
        if not kk:
            continue
        sv = str(v).strip()
        if sv.lower() in _ENV_SENTINELS:
            continue
        norm.setdefault(kk, sv)
    loc = norm.get("user_location") or norm.get("location") or ""
    if loc:
        loc = re.sub(r"\s*\([-+]?\d+(?:\.\d+)?,\s*[-+]?\d+(?:\.\d+)?\)$", "", loc).strip()
    out = {
        "location":  loc,
        "timezone":  norm.get("current_timezone") or norm.get("timezone") or "",
        "date":      norm.get("current_date") or "",
        "time":      norm.get("current_time") or "",
        "datetime":  norm.get("current_datetime") or "",
        "weekday":   norm.get("current_weekday") or "",
        "language":  (norm.get("user_language") or norm.get("language")
                      or norm.get("locale") or ""),
        "user_name": norm.get("user_name") or "",
        "user_email": norm.get("user_email") or "",
        "cwd":       norm.get("cwd") or "",
        "surface":   norm.get("surface") or norm.get("origin") or "",
        "host":      norm.get("host") or "",
        "os":        norm.get("os") or "",
        "shell":     norm.get("shell") or "",
    }
    for _k, _v in norm.items():
        out.setdefault(_k, _v)
    if not out["user_name"]:
        u = body.get("user")
        if isinstance(u, str) and u.strip():
            out["user_name"] = u.strip()

    email = ""
    uid = ""
    if headers and hasattr(headers, "get"):
        email = (headers.get("x-openwebui-user-email") or "").strip()
        uid = (headers.get("x-openwebui-user-id") or "").strip()
    if not email:
        email = out.get("user_email") or ""
    if not uid:
        uid = norm.get("user_id") or ""
    if email or uid:
        owui_db = os.environ.get("MIOS_OWUI_DB", "/var/lib/mios/open-webui/webui.db")
        if os.path.isfile(owui_db):
            try:
                import sqlite3
                c = sqlite3.connect(f"file:{owui_db}?mode=ro", uri=True, timeout=10.0)
                c.row_factory = sqlite3.Row
                row = None
                if uid:
                    row = c.execute("SELECT timezone, info, settings, name, email FROM user WHERE id = ? LIMIT 1;", (uid,)).fetchone()
                if not row and email:
                    row = c.execute("SELECT timezone, info, settings, name, email FROM user WHERE email = ? LIMIT 1;", (email,)).fetchone()
                c.close()
                if row:
                    if row["timezone"] and not out.get("timezone"):
                        out["timezone"] = str(row["timezone"])
                    if row["name"] and not out.get("user_name"):
                        out["user_name"] = str(row["name"])
                    if row["email"] and not out.get("user_email"):
                        out["user_email"] = str(row["email"])
                    if row["info"]:
                        try:
                            info = json.loads(row["info"])
                            if isinstance(info, dict) and not out.get("location") and info.get("location"):
                                out["location"] = str(info["location"]).strip()
                        except Exception:
                            pass
                    if row["settings"]:
                        try:
                            settings = json.loads(row["settings"])
                            if isinstance(settings, dict) and not out.get("location"):
                                loc = settings.get("location") or settings.get("ui", {}).get("location")
                                if loc:
                                    out["location"] = str(loc).strip()
                        except Exception:
                            pass
            except Exception:
                pass
    if not out["location"]:
        try:
            for _m in (body.get("messages") or []):
                if not (isinstance(_m, dict) and _m.get("role") in ("system", "developer")):
                    continue
                _sys = _m.get("content")
                if not isinstance(_sys, str) or not _sys:
                    continue
                _mt = re.search(r"location\s*(?:is|[:=])\s*(.+?)(?:\n|\.\s|\.$|$)",
                                _sys, re.I)
                if _mt:
                    _rec = _mt.group(1).strip().rstrip(".").strip()
                    if ("{{" not in _rec and re.search(r"[0-9A-Za-z]", _rec)
                            and _rec.lower() not in _ENV_SENTINELS):
                        out["location"] = _rec
                        break
        except Exception:  # noqa: BLE001 -- recovery is best-effort
            pass
    try:
        _hdr_email = ""
        if headers is not None and hasattr(headers, "get"):
            _hdr_email = headers.get("x-openwebui-user-email") or ""
        log.info("client_env: fwd_var_keys=%s hdr_email=%r -> location=%r timezone=%r",
                 sorted(raw.keys())[:30], _hdr_email,
                 out.get("location"), out.get("timezone"))
    except Exception:  # noqa: BLE001
        pass
    _bind_mode = _principal_bind_mode()
    if _bind_mode != "off":
        try:
            _claimed = (out.get("user_name") or out.get("user_email") or "").strip()
            _bound = _bound_account(headers)
            if _bound:
                if _bind_mode == "enforce":
                    out["user_name"] = _bound
                    out["user_email"] = ""
                elif _bound != _claimed:  # verify: observe + audit, value unchanged
                    log.warning(
                        "principal-bind mismatch (mode=verify): claimed=%r "
                        "token-bound=%r -- using claimed (observe mode)",
                        _claimed, _bound)
        except Exception:  # noqa: BLE001 -- degrade-open: never break a turn
            pass
    return out

def _current_year() -> str:
    """Current 4-digit year. Prefers the USER's client date (the env-detected
    value the OWUI pipe forwarded) so a query anchors to the OPERATOR's NOW,
    falling back to the live system clock. NEVER hardcode the year (operator
 "use env detect for current values for all AI functions / fall
    back to embedding the current year")."""
    env = _get_client_env()
    for _src in (env.get("date"), env.get("datetime")):
        m = re.match(r"\s*(\d{4})", str(_src or ""))
        if m:
            return m.group(1)
    return str(time.localtime().tm_year)
