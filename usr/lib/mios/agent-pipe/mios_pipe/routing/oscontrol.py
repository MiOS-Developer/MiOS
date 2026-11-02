# AI-hint: OS-CONTROL fast-path responder + window enum/verify helpers extracted VERBATIM from server.py (refactor R9 wave).
# AI-doc: usr/share/doc/mios/manual/routing.md

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, AsyncGenerator, Optional

from fastapi.responses import JSONResponse, StreamingResponse

from mios_sse import _sse_status_phase, _sse_status, _sse_chunk, _sse_done
from mios_jsonsalvage import loads_lenient as _loads_lenient
from mios_dci import DCI_ENABLED, critic_then_maybe_flow
from mios_dispatch import dispatch_mios_verb
from mios_verity import polish_response
from mios_knowledge import _store_knowledge

log = logging.getLogger("mios-agent-pipe")

OS_CONTROL_LAUNCH_VERIFY_S = 16.0
OS_CONTROL_LAUNCH_POLL_S = 1.5
OS_CONTROL_RETRY_ATTEMPTS = 2
OS_CONTROL_RETRY_SETTLE_S = 1.2
OS_CONTROL_REPLY_MAX_TOKENS = 200
OS_CONTROL_ENUM_RETRY = 2
OS_CONTROL_ENUM_TIMEOUT_S = 6.0
OS_CONTROL_ENUM_RETRY_SETTLE_S = 0.7
_OS_CONTROL_ACTION_VERBS: frozenset = frozenset()
_LAUNCH_VERBS: frozenset = frozenset()

_conv_key_var = None
_get_client = None
_scratchpad_note = None
_db_fire = None
_db_post = None
_db_create = None
_inline_satisfaction_check = None
_strip_think_tags = None

_FASTPATH_VERBS: frozenset = frozenset()
_VERB_CATALOG: dict = {}

def configure(*, os_control_launch_verify_s=None, os_control_launch_poll_s=None,
              os_control_retry_attempts=None, os_control_retry_settle_s=None,
              os_control_reply_max_tokens=None, os_control_enum_retry=None,
              os_control_enum_timeout_s=None, os_control_enum_retry_settle_s=None,
              os_control_action_verbs=None, launch_verbs=None,
              conv_key_var=None, get_client=None, scratchpad_note=None,
              db_fire=None, db_post=None, db_create=None,
              inline_satisfaction_check=None, strip_think_tags=None,
              fastpath_verbs=None, verb_catalog=None) -> None:
    g = globals()
    if os_control_launch_verify_s is not None:
        g["OS_CONTROL_LAUNCH_VERIFY_S"] = os_control_launch_verify_s
    if os_control_launch_poll_s is not None:
        g["OS_CONTROL_LAUNCH_POLL_S"] = os_control_launch_poll_s
    if os_control_retry_attempts is not None:
        g["OS_CONTROL_RETRY_ATTEMPTS"] = os_control_retry_attempts
    if os_control_retry_settle_s is not None:
        g["OS_CONTROL_RETRY_SETTLE_S"] = os_control_retry_settle_s
    if os_control_reply_max_tokens is not None:
        g["OS_CONTROL_REPLY_MAX_TOKENS"] = os_control_reply_max_tokens
    if os_control_enum_retry is not None:
        g["OS_CONTROL_ENUM_RETRY"] = os_control_enum_retry
    if os_control_enum_timeout_s is not None:
        g["OS_CONTROL_ENUM_TIMEOUT_S"] = os_control_enum_timeout_s
    if os_control_enum_retry_settle_s is not None:
        g["OS_CONTROL_ENUM_RETRY_SETTLE_S"] = os_control_enum_retry_settle_s
    if os_control_action_verbs is not None:
        g["_OS_CONTROL_ACTION_VERBS"] = os_control_action_verbs
    if launch_verbs is not None:
        g["_LAUNCH_VERBS"] = launch_verbs
    if conv_key_var is not None:
        g["_conv_key_var"] = conv_key_var
    if get_client is not None:
        g["_get_client"] = get_client
    if scratchpad_note is not None:
        g["_scratchpad_note"] = scratchpad_note
    if db_fire is not None:
        g["_db_fire"] = db_fire
    if db_post is not None:
        g["_db_post"] = db_post
    if db_create is not None:
        g["_db_create"] = db_create
    if inline_satisfaction_check is not None:
        g["_inline_satisfaction_check"] = inline_satisfaction_check
    if strip_think_tags is not None:
        g["_strip_think_tags"] = strip_think_tags
    if fastpath_verbs is not None:
        g["_FASTPATH_VERBS"] = fastpath_verbs
    if verb_catalog is not None:
        g["_VERB_CATALOG"] = verb_catalog

_OSCONTROL_ENDPOINTS_CACHE: Optional[list] = None

def _load_oscontrol_endpoints() -> list:
    global _OSCONTROL_ENDPOINTS_CACHE
    try:
        import mios_db_config
        sec = mios_db_config.section(None, "os_control")
        if not isinstance(sec, dict):
            sec = {}
        cfg: dict = {}
        if "executor_endpoint" in sec:
            cfg.setdefault("__exec__", {}).update(
                {"endpoint": str(sec.get("executor_endpoint") or "")})
        nodes = sec.get("nodes") or {}
        if isinstance(nodes, dict):
            for nname, ncfg in nodes.items():
                if isinstance(ncfg, dict):
                    cfg.setdefault(nname, {}).update(ncfg)
        out: list = []
        if "__exec__" in cfg:
            url = (cfg["__exec__"].get("endpoint") or "").rstrip("/")
            if url:
                out.append({"label": "local-executor", "url": url})
        for nname, ncfg in cfg.items():
            if nname == "__exec__":
                continue
            url = str(ncfg.get("endpoint") or "").rstrip("/")
            if url:
                out.append({"label": nname, "url": url})
        _OSCONTROL_ENDPOINTS_CACHE = out
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("oscontrol endpoint discovery failed: %s", e)
        _OSCONTROL_ENDPOINTS_CACHE = []
        return []

def _execution_target(data: dict) -> Optional[dict]:
    """Only an authoritative receipt can identify the actual destination session."""
    target = data.get("execution_target") if isinstance(data, dict) else None
    keys = ("id", "platform", "session_id", "backend")
    if not isinstance(target, dict) or not all(
            isinstance(target.get(key), str) and target[key].strip() for key in keys):
        return None
    return {key: target[key].strip() for key in keys}

def _receipt_target(result: dict) -> Optional[dict]:
    try:
        return _execution_target(json.loads(result.get("output") or ""))
    except (TypeError, ValueError):
        return None

def _target_snapshot(snapshot: dict, target: Optional[dict]) -> dict:
    snapshot = snapshot or {}
    known = (snapshot.get("targets") or {}).get((target or {}).get("id"))
    available = bool(snapshot.get("ok") and target and known == target)
    windows = [window for window in snapshot.get("windows") or []
               if available and window.get("_execution_target_id") == target["id"]]
    errors = list(snapshot.get("errors") or [])
    if not available:
        errors.append("The launch destination session could not be matched to this window probe")
    return dict(snapshot, ok=available, count=len(windows), windows=windows, errors=errors)

def _probe_windows(data: dict, source: str) -> dict:
    if (not isinstance(data, dict) or not isinstance(data.get("windows"), list)
            or data.get("ok") is False or data.get("success") is False):
        raise ValueError("window probe returned no successful windows array")
    target = _execution_target(data)
    return {"ok": True, "execution_target": target,
            "windows": [dict(window, _source=source,
                             _execution_target_id=(target or {}).get("id"))
                        for window in data["windows"] if isinstance(window, dict)]}

async def _remote_enumerate_windows_one(ep: dict, timeout_s: float = 3.5) -> dict:
    """Keep endpoint failures distinct from a valid empty desktop snapshot."""
    label = ep.get("label") or "remote"
    try:
        client = await _get_client()
        response = await client.get((ep.get("url") or "") + "/windows", timeout=timeout_s)
        if response.status_code != 200:
            raise ValueError(f"HTTP {response.status_code}")
        return _probe_windows(response.json(), label)
    except Exception as error:
        log.debug("remote window probe %s failed: %s", label, error)
        return {"ok": False, "windows": [], "error": f"{label}: {error}"}

async def _enumerate_windows() -> dict:
    async def _local(verb: str, source: str) -> dict:
        try:
            result = await dispatch_mios_verb(verb, {})
            if not _command_succeeded(result):
                raise ValueError(result.get("stderr") or f"exit {result.get('exit_code')}")
            data = _loads_lenient((result.get("output") or "").strip())
            if isinstance(data, dict) and data.get("local") is True:
                context = _execution_target({"execution_target": result.get("operator_context")})
                stated = _execution_target(data)
                if context and stated and context != stated:
                    raise ValueError("native probe target differs from its operator context")
                if context and not stated:
                    data = dict(data, execution_target=context)
            return _probe_windows(data, source)
        except Exception as error:
            log.debug("%s window enumeration failed: %s", source, error)
            return {"ok": False, "windows": [], "error": f"{source}: {error}"}

    async def _snapshot_once() -> dict:
        probes = []
        if os.name != "nt":
            probes.append(("computer-use", _local("cu_window_list", "computer-use")))
        if os.name == "nt" or os.path.exists("/mnt/c/Windows/System32/tasklist.exe"):
            probes.append(("wsl", _local("list_windows", "wsl")))
        probes.extend((ep.get("label") or "remote", _remote_enumerate_windows_one(ep))
                      for ep in _load_oscontrol_endpoints())
        tasks = {asyncio.create_task(probe): label for label, probe in probes}
        done, pending = await asyncio.wait(tasks, timeout=OS_CONTROL_ENUM_TIMEOUT_S)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        windows, errors, sources, targets = [], [], {}, {}
        for task, label in tasks.items():
            if task not in done:
                sources[label] = False
                errors.append(f"{label}: window probe timed out")
                continue
            result = task.result()
            sources[label] = bool(result.get("ok"))
            windows.extend(result.get("windows") or [])
            target = result.get("execution_target")
            if result.get("ok") and target:
                targets[target["id"]] = target
            if result.get("error"):
                errors.append(str(result["error"]))
        return {"ok": any(sources.values()), "count": len(windows), "windows": windows,
                "sources": sources, "errors": errors, "targets": targets}

    snapshot = {}
    for attempt in range(max(1, OS_CONTROL_ENUM_RETRY + 1)):
        snapshot = await _snapshot_once()
        if snapshot["ok"]:
            break
        if attempt < OS_CONTROL_ENUM_RETRY:
            log.info("window enumeration unavailable -> retry %d/%d",
                     attempt + 1, OS_CONTROL_ENUM_RETRY)
            await asyncio.sleep(OS_CONTROL_ENUM_RETRY_SETTLE_S)
    return snapshot

def _window_key(w: dict) -> tuple:
    """Stable identity for diffing snapshots: prefer hwnd, else (title, proc)."""
    if not isinstance(w, dict):
        return ("?", str(w))
    hw = w.get("hwnd")
    if hw not in (None, "", 0):
        return (w.get("_execution_target_id") or w.get("_source"), "hwnd", str(hw))
    if w.get("id") not in (None, "", 0):
        return (w.get("_execution_target_id") or w.get("_source"), "id", str(w["id"]))
    return (w.get("_source"), "tp", str(w.get("title", "")), str(w.get("proc", "")))

def _window_diff(before: dict, after: dict) -> dict:
    """opened = windows in AFTER not in BEFORE; closed = the reverse."""
    before = before or {}
    after = after or {}
    b = {_window_key(w): w for w in (before.get("windows") or [])}
    a = {_window_key(w): w for w in (after.get("windows") or [])}
    opened = [a[k] for k in (a.keys() - b.keys())]
    closed = [b[k] for k in (b.keys() - a.keys())]
    return {"opened": opened, "closed": closed}

def _win_titles(wins: Optional[list]) -> str:
    out = []
    for w in (wins or [])[:12]:
        if isinstance(w, dict):
            t = str(w.get("title", "")).strip() or str(w.get("proc", "")).strip()
            if t:
                out.append(t)
    return ", ".join(out)

def _window_delta_text(diff: dict) -> str:
    bits = []
    if diff.get("opened"):
        bits.append(f"opened: {_win_titles(diff['opened'])}")
    if diff.get("closed"):
        bits.append(f"closed: {_win_titles(diff['closed'])}")
    return "; ".join(bits) or "no visible window change detected"

def _index_window_event(tool: str, args: dict, before: dict, after: dict,
                        diff: dict, session_id: Optional[str]) -> None:
    """RECORD + INDEX the before/after window snapshots + delta so FUTURE
    queries recall them (RAG: embedded knowledge row via _store_knowledge) and
    same-conversation agents see them (scratchpad). Fire-and-forget; the
 "check before, diff after" grounding the operator asked for."""
    target = ""
    if isinstance(args, dict):
        target = str(args.get("app") or args.get("title")
                     or args.get("name") or args.get("url") or "").strip()
    delta = _window_delta_text(diff)
    q = (f"open desktop windows after {tool} {target}".strip()
         if target else f"open desktop windows after {tool}")
    answer = (
        f"OS-control action `{tool}` (target={target!r}).\n"
        f"Open windows BEFORE ({(before or {}).get('count', 0)}): "
        f"[{_win_titles((before or {}).get('windows'))}].\n"
        f"Open windows AFTER ({(after or {}).get('count', 0)}): "
        f"[{_win_titles((after or {}).get('windows'))}].\n"
        f"Delta: {delta}.")
    try:
        _store_knowledge(query=q, answer=answer, session_id=session_id,
                         tool_history=[{"tool": tool, "args": args}])
    except Exception as e:
        log.debug("window event index skipped: %s", e)
    _scratchpad_note("os-control", f"{tool} {target} -> {delta}",
                     lane="window", phase="action")

def _os_target(args: dict) -> str:
    if not isinstance(args, dict):
        return ""
    return str(args.get("app") or args.get("title") or args.get("name")
               or args.get("url") or "").strip().lower()

def _win_hay(w: dict) -> str:
    return " ".join(str(w.get(key) or "") for key in
                    ("title", "proc", "class", "app_id", "initialClass")).lower()

async def _center_windows(wins: list) -> list:
    eps = {e.get("label"): e.get("url")
           for e in _load_oscontrol_endpoints() if e.get("url")}
    if not eps:
        return []
    best: dict = {}
    for w in (wins or []):
        if not isinstance(w, dict):
            continue
        src = w.get("_source")
        if src not in eps:                      # only movable Win32 windows
            continue
        hw = w.get("hwnd")
        if hw in (None, "", 0):
            continue
        ww = int(w.get("w") or 0)
        hh = int(w.get("h") or 0)
        if ww < 200 or hh < 120:                # skip popups / tooltips
            continue
        area = ww * hh
        if area >= (best.get(src, {}).get("_area", -1)):
            best[src] = {"hwnd": hw, "_area": area,
                         "title": w.get("title") or w.get("proc") or ""}
    if not best:
        return []
    done: list = []
    for src, w in best.items():
        try:
            client = await _get_client()
            await client.post(eps[src] + "/window/center",
                              json={"hwnd": w["hwnd"]}, timeout=5)
            done.append(str(w["title"]))
        except Exception as e:  # noqa: BLE001
            log.debug("auto-center on %s failed: %s", src, e)
    return done

def _launch_proc_patterns(args: dict, result: dict) -> list:
    pats: list = []
    blob = str(result.get("output") or "") + " " + str(result.get("stderr") or "")
    for m in re.finditer(r'(?:launching|fired|run|exec)\s+([A-Za-z][A-Za-z0-9._+-]{2,})', blob):
        ref = m.group(1).strip().strip('"\'')
        if "." in ref:
            leaf = ref.split(".")[-1].lower()
            if len(leaf) >= 3 and leaf not in pats:
                pats.append(leaf)
            if ref.lower() not in pats:
                pats.append(ref.lower())
        elif len(ref) >= 3 and ref.lower() not in pats:
            pats.append(ref.lower())
    t = _os_target(args)
    if t and len(t) >= 3 and t not in pats:
        pats.append(t)
    return pats

async def _proc_present(patterns: list) -> bool:
    """True if ANY pattern matches a running process command line (global
    `pgrep -if` or Windows host `tasklist.exe`). /proc is world-readable, so the
    agent uid sees EVERY user's process cmdlines -- including the operator's flatpak
    GUIs running under bwrap. On WSL2, also queries tasklist.exe for host processes."""
    is_wsl = os.path.exists("/mnt/c/Windows/System32/tasklist.exe")
    for pat in patterns:
        if not pat or len(pat) < 3:
            continue
        try:
            p = await asyncio.create_subprocess_exec(
                "pgrep", "-if", pat,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
            rc = await asyncio.wait_for(p.wait(), timeout=4)
            if rc == 0:
                return True
        except Exception:  # noqa: BLE001
            pass
        if is_wsl:
            try:
                exe_name = pat if pat.lower().endswith(".exe") else f"{pat}.exe"
                p = await asyncio.create_subprocess_exec(
                    "/mnt/c/Windows/System32/tasklist.exe", "/FI", f"IMAGENAME eq {exe_name}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL)
                out, _ = await asyncio.wait_for(p.communicate(), timeout=4)
                out_str = out.decode("utf-8", errors="replace")
                if exe_name.lower() in out_str.lower() and "INFO:" not in out_str:
                    return True
            except Exception:  # noqa: BLE001
                pass
    return False

def _command_succeeded(result: dict) -> bool:
    try:
        return bool(result.get("success")) and int(result.get("exit_code", -1)) == 0
    except (TypeError, ValueError):
        return False

def _action_outcome(tool: str, args: dict, result: dict, verified: bool) -> str:
    target = str(args.get("name") or args.get("app") or args.get("title")
                 or args.get("url") or "the requested target").strip()
    if not _command_succeeded(result):
        action = f"open {target}" if tool in _LAUNCH_VERBS else tool.replace("_", " ")
        return f"I couldn't {action}. The command failed (exit code {result.get('exit_code', 'unknown')})."
    if not verified:
        if tool in _LAUNCH_VERBS:
            return (f"The launch command for {target} succeeded, but I couldn't verify its window. "
                    "I can't confirm that it opened.")
        return f"I couldn't verify that {tool.replace('_', ' ')} took effect for {target}."
    if tool in _LAUNCH_VERBS:
        return f"{target} is open; its window was verified."
    return f"Verified: {tool.replace('_', ' ')} took effect for {target}."

def _verify_os_action(tool: str, args: dict, result: dict,
                      before: dict, after: dict, wdiff: dict) -> bool:
    """A failed command or unavailable window probe cannot verify an action."""
    ok = _command_succeeded(result)
    if not ok:
        return False
    if tool in _LAUNCH_VERBS or tool == "close_window":
        destination = _receipt_target(result)
        before, after = (_target_snapshot(before, destination),
                         _target_snapshot(after, destination))
        wdiff = _window_diff(before, after)
    target = _os_target(args)
    bc = (before or {}).get("count", 0)
    ac = (after or {}).get("count", 0)
    blind = (bc == 0 and ac == 0)
    if tool in _LAUNCH_VERBS:
        if not (after or {}).get("ok") or blind:
            return False
        wins = (after or {}).get("windows") or []
        if target and any(target in _win_hay(w) for w in wins):
            return True
        return False
    if tool == "close_window":
        if not (before or {}).get("ok") or not (after or {}).get("ok") or blind:
            return False
        wins = (after or {}).get("windows") or []
        if target:
            return not any(target in _win_hay(w) for w in wins)  # gone == success
        return bool(wdiff.get("closed")) or ok
    return ok

_LAST_OPENED_WINDOW: dict = {}
_LAST_OPENED_WINDOW_CAP = int(os.environ.get("MIOS_LAST_WINDOW_CAP", "256") or 256)

def _record_last_opened_window(wdiff: dict) -> None:
    """Remember the first window a launch opened for THIS conversation (best-effort)."""
    _key = _conv_key_var.get()
    if not _key or not isinstance(wdiff, dict):
        return
    _titles = [str(w.get("title") or "").strip()
               for w in (wdiff.get("opened") or [])
               if isinstance(w, dict) and str(w.get("title") or "").strip()]
    if not _titles:
        return
    if len(_LAST_OPENED_WINDOW) >= _LAST_OPENED_WINDOW_CAP:
        _LAST_OPENED_WINDOW.clear()  # crude bound; conversations are ephemeral
    _LAST_OPENED_WINDOW[_key] = _titles[0]
    log.info("recorded last-opened window for conv %r -> %r", _key, _titles[0])

async def _respond_os_control(
    tool: str, args: dict, refined: Optional[dict], *,
    streaming: bool, chat_id: str, model: str,
    session_id: Optional[str], last_user_text: str,
    persona_system: str = "", emit=None,
) -> Any:
    _args = args if isinstance(args, dict) else {}

    if streaming:
        async def _stream_os() -> AsyncGenerator[bytes, None]:
            yield _sse_status_phase(chat_id=chat_id, model=model, phase="prompt")
            yield _sse_status_phase(chat_id=chat_id, model=model, phase="route")
            _oq: asyncio.Queue = asyncio.Queue()
            _holder: dict = {}

            async def _work() -> None:
                try:
                    _holder["resp"] = await _respond_os_control(
                        tool, args, refined, streaming=False, chat_id=chat_id,
                        model=model, session_id=session_id,
                        last_user_text=last_user_text,
                        persona_system=persona_system, emit=_oq.put_nowait)
                except Exception as _e:  # noqa: BLE001
                    _holder["err"] = str(_e)
                finally:
                    _oq.put_nowait(None)

            _wtask = asyncio.create_task(_work())
            while True:
                try:
                    _s = await asyncio.wait_for(_oq.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    yield b": keepalive\n\n"
                    continue
                if _s is None:
                    break
                if isinstance(_s, dict):
                    yield _sse_status(chat_id=chat_id, model=model,
                                      emoji=str(_s.get("emoji", "·")),
                                      label=str(_s.get("label", "")),
                                      detail=_s.get("detail"))
            await _wtask
            _content = ""
            _resp = _holder.get("resp")
            try:
                _b = _loads_lenient(bytes(_resp.body).decode("utf-8"))
                _content = _b["choices"][0]["message"]["content"]
            except Exception:  # noqa: BLE001
                _content = "I couldn't complete or verify the OS-control action."
            yield _sse_chunk("", chat_id=chat_id, model=model, role="assistant")
            yield _sse_chunk(_content, chat_id=chat_id, model=model)
            yield _sse_status_phase(chat_id=chat_id, model=model,
                                    phase="tool_done", done=True)
            yield _sse_chunk("", chat_id=chat_id, model=model,
                             finish_reason="stop")
            yield _sse_done()
        return StreamingResponse(_stream_os(), media_type="text/event-stream")

    def _emit(emoji: str, label: str, detail=None) -> None:
        if emit:
            try:
                emit({"emoji": emoji, "label": label, "detail": detail})
            except Exception:  # noqa: BLE001
                pass

    if tool == "pc_type":
        _ckey = _conv_key_var.get()
        _lw = _LAST_OPENED_WINDOW.get(_ckey) if _ckey else None
        if _lw:
            try:
                eps = {e.get("label"): e.get("url") for e in _load_oscontrol_endpoints() if e.get("url")}
                _osc_url = next(iter(eps.values())) if eps else None
                if _osc_url:
                    _client = await _get_client()
                    _resp = await _client.post(_osc_url + "/window/focus", json={"title": _lw}, timeout=5)
                    if _resp.status_code == 200:
                        _data = _resp.json()
                        if _data.get("ok") and _data.get("matched"):
                            _hw = _data["matched"][0].get("hwnd")
                            if _hw:
                                _args["hwnd"] = _hw
                                log.info("standalone pc_type: context-focused and resolved hwnd %s for last-opened window %r before typing", _hw, _lw)
            except Exception as _ex:  # noqa: BLE001
                log.warning("failed to resolve target hwnd for standalone pc_type: %s", _ex)
    _is_launch = tool in _LAUNCH_VERBS
    _action = tool in _OS_CONTROL_ACTION_VERBS or _is_launch
    _before = _after = None
    _wdiff: dict = {}
    result: dict = {}
    _verified = False
    _tries = 0
    if _action and _is_launch:
        _emit("🚀", f"opening {_os_target(_args) or tool}")
        _before = await _enumerate_windows()
        result = await dispatch_mios_verb(tool, _args, session_id=session_id)
        _deadline = time.monotonic() + OS_CONTROL_LAUNCH_VERIFY_S
        while _command_succeeded(result):
            _tries += 1
            _emit("🔍", "checking it opened")
            _after = await _enumerate_windows()
            _wdiff = _window_diff(_before, _after)
            _win_verdict = _verify_os_action(
                tool, _args, result, _before, _after, _wdiff)
            _verified = _win_verdict
            if _verified or time.monotonic() >= _deadline:
                break
            log.info("os-control %s not yet confirmed (poll %d) -> wait %.1fs",
                     tool, _tries, OS_CONTROL_LAUNCH_POLL_S)
            await asyncio.sleep(OS_CONTROL_LAUNCH_POLL_S)
        destination = _receipt_target(result)
        _before, _after = (_target_snapshot(_before, destination),
                          _target_snapshot(_after, destination))
        _wdiff = _window_diff(_before, _after)
        if _verified:
            matching = [window for window in _wdiff.get("opened") or []
                        if _os_target(_args) and _os_target(_args) in _win_hay(window)]
            if matching:
                _emit("🎯", "centering it")
                _ctr = await _center_windows(matching)
                if _ctr:
                    log.info("auto-centered launched window(s): %s", ", ".join(_ctr))
                _record_last_opened_window({"opened": matching})
    else:
        _emit("🪟", (f"{tool.replace('_window', '').replace('_', ' ').strip()} "
                     f"{_os_target(_args)}").strip())
        _attempts = max(1, OS_CONTROL_RETRY_ATTEMPTS) if _action else 1
        for _i in range(_attempts):
            _tries = _i + 1
            _before = await _enumerate_windows() if _action else None
            result = await dispatch_mios_verb(tool, _args, session_id=session_id)
            _after = await _enumerate_windows() if _action else None
            _wdiff = _window_diff(_before, _after) if _action else {}
            _verified = (_verify_os_action(tool, _args, result, _before, _after, _wdiff)
                         if _action else bool(result.get("success")))
            if _verified or not _action or not _command_succeeded(result):
                break
            if _i < _attempts - 1:
                log.info("os-control %s NOT verified (try %d/%d) -> re-attempt",
                         tool, _tries, _attempts)
                await asyncio.sleep(OS_CONTROL_RETRY_SETTLE_S)
    ok = _command_succeeded(result)
    _eff_ok = ok and _verified if _action else ok
    if _action:
        if tool == "close_window":
            destination = _receipt_target(result)
            _before, _after = (_target_snapshot(_before, destination),
                              _target_snapshot(_after, destination))
            _wdiff = _window_diff(_before, _after)
        _index_window_event(tool, _args, _before, _after, _wdiff, session_id)
    _row = {
        "tool": tool,
        "args": _args,
        "result_preview": (result.get("output") or "")[:500],
        "success": ok,
        "latency_ms": int(result.get("latency_ms", 0)),
        "tainted": bool(result.get("tainted")),
        "taint_reason": (result.get("taint_reason") or "") or None,
    }
    if session_id:
        _db_fire(_db_post(
            _db_create("tool_call", _row, now_fields=("ts",)).rstrip(";")
            + f", session = {session_id};"))
    else:
        _db_fire(_db_post(_db_create("tool_call", _row, now_fields=("ts",))))
    envelope = {
        "tool_call": {
            "id": f"call_{int(time.time()*1000)}",
            "type": "function",
            "function": {"name": tool, "arguments": _args},
        },
        "tool_result": {
            "success": bool(result.get("success")),
            "output": (result.get("output") or "")[:2000],
            "stderr": (result.get("stderr") or "")[:2000],
            "exit_code": int(result.get("exit_code", -1)),
        },
    }
    if _action:
        envelope["window_change"] = {
            "verified": bool(_verified),
            "execution_target": _receipt_target(result),
            "attempts": _tries,
            "before_available": bool((_before or {}).get("ok")),
            "after_available": bool((_after or {}).get("ok")),
            "before_errors": (_before or {}).get("errors") or [],
            "after_errors": (_after or {}).get("errors") or [],
            "before_count": (_before or {}).get("count", 0),
            "after_count": (_after or {}).get("count", 0),
            "opened": [str(w.get("title") or w.get("proc") or "")
                       for w in _wdiff.get("opened", []) if isinstance(w, dict)],
            "closed": [str(w.get("title") or w.get("proc") or "")
                       for w in _wdiff.get("closed", []) if isinstance(w, dict)],
        }
    if DCI_ENABLED and not _action:
        _db_fire(critic_then_maybe_flow(last_user_text, envelope,
                                        session_id=session_id))
    symbol = "✅" if _eff_ok else "⚠️"
    envelope_block = (
        f"<details type=\"tool_calls\" done=\"true\">\n"
        f"<summary>{symbol} `{tool}`</summary>\n\n"
        f"```json\n{json.dumps(envelope, indent=2, default=str)}\n```\n"
        f"</details>")
    _refined_for_polish = refined or {
        "intent": "dispatch",
        "intended_outcome": f"perform the {tool} action the operator asked for",
        "refined_text": last_user_text,
    }
    if _action or not ok:
        # The observed command/window facts already determine this result. A
        # second inference can delay or contradict it; always render it directly.
        rendered = _action_outcome(tool, _args, result, _eff_ok) + "\n\n" + envelope_block
        _emit(symbol, "result ready")
        return JSONResponse(content={
            "id": chat_id, "object": "chat.completion", "created": int(time.time()),
            "model": model, "choices": [{"index": 0, "message": {
                "role": "assistant", "content": rendered}, "finish_reason": "stop"}],
        })
    await _inline_satisfaction_check(session_id, _refined_for_polish)
    _out = (result.get("output") or "").strip()
    _err = (result.get("stderr") or "").strip()
    _polish_src = (
        f"exit_code={int(result.get('exit_code', -1))}\n"
        f"stdout:\n{_out[:1500]}\n"
        + (f"stderr:\n{_err[:600]}\n" if _err else ""))
    _emit(symbol, "writing the result")
    polished_raw = await polish_response(
        "The OS-control verb `" + tool + "` ran (result below). Reply in 1-3 "
        "short sentences with exactly: (1) SUCCESS or failure -- grounded in "
        "`verified` (verified=True means it took effect; if False after the "
        "retries, say it did NOT and do not claim success); (2) the key DETAILS "
        "(what opened/closed/was focused, the app/window name); (3) one or two "
        "natural FOLLOW-UPS the operator might want next (e.g. focus it, move "
        "it, close it, open another). No preamble, no invented coordinates, no "
        "fabricated confirmation -- nothing beyond success + details + "
        "follow-ups.\n\n" + _polish_src,
        _refined_for_polish, session_id=session_id,
        original_user_text=last_user_text, persona_system=persona_system,
        max_tokens=OS_CONTROL_REPLY_MAX_TOKENS)
    polished = _strip_think_tags(polished_raw) if polished_raw else ""
    rendered = (f"{polished}\n\n{envelope_block}"
                if polished.strip() else envelope_block)
    return JSONResponse(content={
        "id": chat_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": rendered},
            "finish_reason": "stop",
        }],
    })

def _render_os_control_verbs() -> str:
    """One line per fast-path verb (name(sig) -- desc) for the refine prompt, so
    the micro maps a single concrete action to the right catalog verb WITHOUT a
    hardcoded keyword map. Covers OS-control + other deterministic single-action
    verbs (scheduling). Empty string when none are registered."""
    lines = []
    for name in sorted(_FASTPATH_VERBS):
        cfg = _VERB_CATALOG.get(name) or {}
        sig = cfg.get("sig", "")
        desc = (cfg.get("desc", "") or "").strip().replace("\n", " ")[:150]
        lines.append(f"  {name}({sig}) -- {desc}")
    return "\n".join(lines)
