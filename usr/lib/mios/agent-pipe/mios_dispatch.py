# AI-hint: Verb->bash DISPATCH chokepoint extracted VERBATIM from server.py (refactor R7 wave).
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import re
import shlex
import socket as _socket
import time
import uuid
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import mios_sandbox
import mios_ruleof2          # the Rule-of-Two architectural gate (pure evaluator)
import mios_argval
from mios_argval import _arg_with_synonyms, _validate_enum_args
from mios_template import _template_to_cmd, _TEMPLATE_PH_RE, _TemplateAbort
import mios_quarantine       # the CaMeL dual-context quarantine gate (stricter superset)
import mios_hitl             # the unified HITL verdict resolver (mios_hitl.decide)
from mios_jsonsalvage import loads_lenient as _loads_lenient
import mios_scratchpad
from mios_firewall import _classify_verb_taint, _session_is_tainted, _is_external_url
from mios_policy import (
    _hitl_block_reason,
    _HITL_ARBITER_URL,
    _hitl_arbiter_verdict,
    _match_user_cfg,
    _dispatch_quota_reason,
    _dispatch_pdp_reason,
)
from mios_hitlflow import (
    _action_hash,
    _pending_hash,
    _hitl_record_pending,
    _hitl_gate,
)

log = logging.getLogger("mios-agent-pipe")

WEB_DISPATCH_JITTER_S = 0.15
DISPATCH_DEDUP = True
NATIVE_LOOP_DATE_IN_QUERY = True
LAUNCHER_SOCK = "/run/mios-launcher/launcher.sock"
SANDBOX_ENFORCE = False
_SANDBOX_SELF_CONFINED: tuple = ()
RULE_OF_TWO_MODE = "off"
QUARANTINE_MODE = "off"

_VERB_CATALOG: dict = {}
_VERB_ARG_SYNONYMS: dict = {}
_HIGH_PRIVILEGE_VERBS: frozenset = frozenset()
_LAUNCH_VERBS: frozenset = frozenset()
_dispatch_inflight: dict = {}
_web_sem = None
_TOOL_CONFLICT = None
_conv_key_var = None
_recency_ctx_var = None
_proposal_var = None
_dispatch_agent_var = None
_hitl_approved_var = None
_AGENT_REGISTRY: dict = {}

_resolve_verb_key = None
_current_date_str = None
_trace_span = None
_db_fire = None
_db_post = None
_db_create = None
_letta_dispatch_handler = None

def _emit_dispatch_dedup_event(tool: str, args: dict,
                               session_id: "Optional[str]") -> None:
    """Audit the single-flight collapse as the same `action_repeat_dedup`
    event the DAG cross-level guard emits -- one observability shape for both
    dedup paths (MiOS spec). Uses the module's injected event-DB writers."""
    try:
        _db_fire(_db_post(_db_create("event", {
            "source": "mios-agent-pipe",
            "kind": "action_repeat_dedup",
            "severity": "info",
            "summary": f"single-flight collapse: {tool}",
            "payload": {"tool": tool, "mode": "concurrent_single_flight"},
        }, now_fields=("ts",))))
    except Exception:
        pass

def _record_dispatch_tool_call_row(tool: str, result: dict,
                                   session_id: "Optional[str]") -> None:
    try:
        _row = {
            "tool": str(result.get("tool") or tool or ""),
            "args": result.get("args") if isinstance(result.get("args"), dict) else {},
            "result_preview": (result.get("output") or "")[:500],
            "success": bool(result.get("success")),
            "latency_ms": int(result.get("latency_ms", 0) or 0),
            "tainted": bool(result.get("tainted")),
            "taint_reason": (result.get("taint_reason") or "") or None,
        }
        sql = _db_create("tool_call", _row, now_fields=("ts",))
        if session_id:
            sql = sql.rstrip().rstrip(";") + f", session = {session_id};"
        _db_fire(_db_post(sql))
    except Exception:  # noqa: BLE001 -- degrade-open: verb already ran; audit is best-effort
        pass

def configure(*, verb_catalog=None, verb_arg_synonyms=None,
              high_privilege_verbs=None, launch_verbs=None,
              web_dispatch_jitter_s=None, dispatch_dedup=None,
              native_loop_date_in_query=None, launcher_sock=None,
              sandbox_enforce=None, sandbox_self_confined=None,
              rule_of_two_mode=None, quarantine_mode=None,
              dispatch_inflight=None, web_sem=None, tool_conflict=None,
              conv_key_var=None, recency_ctx_var=None, proposal_var=None,
              dispatch_agent_var=None, hitl_approved_var=None,
              resolve_verb_key=None, current_date_str=None,
              emit_dispatch_dedup_event=None,
              trace_span=None, db_fire=None, db_post=None,
              db_create=None, letta_dispatch_handler=None,
              agent_registry=None) -> None:
    """Inject server.py's verb catalog + arg-synonym map, config scalars (incl. the
    sandbox enforce knob + self-confined set), broker socket path, dispatch
    ContextVars + state and the runtime helpers the dispatch chokepoint calls back
    into. Partial injection supported (each guarded by `is not None`) so the
    late-bound native_loop_date_in_query can land in a second call."""
    global WEB_DISPATCH_JITTER_S, DISPATCH_DEDUP, NATIVE_LOOP_DATE_IN_QUERY
    global LAUNCHER_SOCK, SANDBOX_ENFORCE, _SANDBOX_SELF_CONFINED, RULE_OF_TWO_MODE
    global QUARANTINE_MODE
    global _VERB_CATALOG, _VERB_ARG_SYNONYMS, _HIGH_PRIVILEGE_VERBS, _LAUNCH_VERBS
    global _dispatch_inflight, _web_sem, _TOOL_CONFLICT
    global _conv_key_var, _recency_ctx_var, _proposal_var, _dispatch_agent_var
    global _hitl_approved_var, _AGENT_REGISTRY
    global _resolve_verb_key, _current_date_str
    global _emit_dispatch_dedup_event, _trace_span, _db_fire, _db_post, _db_create
    global _letta_dispatch_handler
    if agent_registry is not None:
        _AGENT_REGISTRY = agent_registry
    if verb_catalog is not None:
        _VERB_CATALOG = verb_catalog
        mios_argval.configure(verb_catalog=verb_catalog)
        from mios_template import compile_template
        for vspec in verb_catalog.values():
            if isinstance(vspec, dict):
                for k in ("cmd", "cmd_args", "cmd_positioned", "cmd_pixel", "cmd_resize"):
                    if k in vspec and isinstance(vspec[k], str):
                        compile_template(vspec[k])
    if letta_dispatch_handler is not None:
        _letta_dispatch_handler = letta_dispatch_handler
    if verb_arg_synonyms is not None:
        _VERB_ARG_SYNONYMS = verb_arg_synonyms
        mios_argval.configure(verb_arg_synonyms=verb_arg_synonyms)
    if high_privilege_verbs is not None:
        _HIGH_PRIVILEGE_VERBS = high_privilege_verbs
    if launch_verbs is not None:
        _LAUNCH_VERBS = launch_verbs
    if web_dispatch_jitter_s is not None:
        WEB_DISPATCH_JITTER_S = web_dispatch_jitter_s
    if dispatch_dedup is not None:
        DISPATCH_DEDUP = dispatch_dedup
    if native_loop_date_in_query is not None:
        NATIVE_LOOP_DATE_IN_QUERY = native_loop_date_in_query
    if launcher_sock is not None:
        LAUNCHER_SOCK = launcher_sock
    if sandbox_enforce is not None:
        SANDBOX_ENFORCE = sandbox_enforce
    if sandbox_self_confined is not None:
        _SANDBOX_SELF_CONFINED = sandbox_self_confined
    if rule_of_two_mode is not None:
        RULE_OF_TWO_MODE = rule_of_two_mode
    if quarantine_mode is not None:
        QUARANTINE_MODE = quarantine_mode
    if dispatch_inflight is not None:
        _dispatch_inflight = dispatch_inflight
    if web_sem is not None:
        _web_sem = web_sem
    if tool_conflict is not None:
        _TOOL_CONFLICT = tool_conflict
    if conv_key_var is not None:
        _conv_key_var = conv_key_var
    if recency_ctx_var is not None:
        _recency_ctx_var = recency_ctx_var
    if proposal_var is not None:
        _proposal_var = proposal_var
    if dispatch_agent_var is not None:
        _dispatch_agent_var = dispatch_agent_var
    if hitl_approved_var is not None:
        _hitl_approved_var = hitl_approved_var
    if resolve_verb_key is not None:
        _resolve_verb_key = resolve_verb_key
    if current_date_str is not None:
        _current_date_str = current_date_str
    if emit_dispatch_dedup_event is not None:
        _emit_dispatch_dedup_event = emit_dispatch_dedup_event
    if trace_span is not None:
        _trace_span = trace_span
    if db_fire is not None:
        _db_fire = db_fire
    if db_post is not None:
        _db_post = db_post
    if db_create is not None:
        _db_create = db_create

    # The builder holds its own copies of these three; forward them so ONE
    # configure() call still configures the whole chokepoint (task_251).
    _dispatch_cmd.configure(verb_catalog=verb_catalog,
                            sandbox_enforce=sandbox_enforce,
                            sandbox_self_confined=sandbox_self_confined)

# The command BUILDER moved to mios_pipe/routing/dispatch_cmd.py (task_251); the
# names are re-imported so this module's surface is byte-identical.
from mios_pipe.routing import dispatch_cmd as _dispatch_cmd   # noqa: E402
from mios_pipe.routing.dispatch_cmd import (   # noqa: E402,F401
    _dispatch_sandbox_profile,
    _sandbox_wrap_cmd,
    normalize_container_exec,
    _build_dispatch_cmd,
)

def _normalize_tool_name(tool: str) -> str:
    """Remove a trailing argument display without regex search backtracking."""
    text = str(tool or "").strip()
    if text.endswith(")"):
        # The former pattern's dot did not cross a newline. Keep that boundary
        # while finding the first opening parenthesis in the final line once.
        start = text.find("(", text.rfind("\n") + 1)
        if start >= 0:
            text = text[:start]
    return text.strip().strip("`'\"")


async def _dispatch_bounded(
    tool: str, args: dict, *,
    session_id: Optional[str] = None,
) -> dict:
    _t = _normalize_tool_name(tool)
    async with _trace_span("dispatch", verb=_t), _TOOL_CONFLICT.guard(_t):
        if _t == "web_search":
            if WEB_DISPATCH_JITTER_S > 0:
                await asyncio.sleep(random.uniform(0, WEB_DISPATCH_JITTER_S))
            async with _web_sem:
                return await _dispatch_mios_verb_inner(
                    tool, args, session_id=session_id)
        return await _dispatch_mios_verb_inner(tool, args, session_id=session_id)

async def dispatch_mios_verb(
    tool: str, args: dict, *,
    session_id: Optional[str] = None,
) -> dict:
    try:
        from mios_pipe.routing.chat import _replay_active, _replay_tool_queue, _record_active, _in_exec_tool_calls, _conv_key_var
        in_exec = _in_exec_tool_calls.get() if (_in_exec_tool_calls is not None and hasattr(_in_exec_tool_calls, "get")) else False
    except ImportError:
        in_exec = False
        _replay_active = None
        _record_active = None
        _conv_key_var = None

    tool_resolved = _resolve_verb_key(str(tool))

    if not in_exec and _replay_active is not None and _replay_active.get():
        q = _replay_tool_queue.get()
        if q:
            row = q.pop(0)
            meta = row.get("meta") or {}
            out = meta.get("output") or ""
            success = bool(meta.get("success", True))
            log.info("Replayed tool call (direct dispatch): %s -> %s", tool_resolved, str(out)[:200])
            return {"success": success, "output": out}
        else:
            log.warning("No recorded tool response in queue for direct dispatch: %s", tool_resolved)
            return {"success": False, "output": f"(error: no recorded tool response found in replay log for {tool_resolved})"}

    res = await _dispatch_mios_verb_live(tool, args, session_id=session_id)

    if not in_exec and _record_active is not None and _record_active.get():
        try:
            sess_id = session_id or (_conv_key_var.get() if (_conv_key_var is not None and hasattr(_conv_key_var, "get")) else "default")
            import uuid
            out_content = res.get("output") or res.get("result") or ""
            success = bool(res.get("success", True))

            row = {
                "id": f"session:tool:{uuid.uuid4().hex[:24]}",
                "kind": "tool_io",
                "owui_chat_id": sess_id,
                "meta": {
                    "tool": tool_resolved,
                    "args": args if isinstance(args, dict) else {},
                    "output": str(out_content),
                    "success": success
                }
            }
            if _db_create is not None:
                sql = _db_create("session", row, now_fields=("ts",), passport_sign=False)
                if _db_fire is not None and _db_post is not None:
                    _db_fire(_db_post(sql))
            log.info("Recorded tool call (direct dispatch) in session: %s", tool_resolved)
        except Exception as e:
            log.warning("Failed to record tool call (direct dispatch) in session: %s", e)

    return res

async def _dispatch_mios_verb_live(
    tool: str, args: dict, *,
    session_id: Optional[str] = None,
) -> dict:
    tool = _resolve_verb_key(str(tool))
    if isinstance(args, dict):
        args = {k: v for k, v in args.items() if v is not None}

    aname = (_dispatch_agent_var.get() or "").strip() if _dispatch_agent_var else ""
    if aname:
        acfg = _AGENT_REGISTRY.get(aname) or {} if _AGENT_REGISTRY else {}
        privilege_group = acfg.get("privilege_group") or "routine"

        vcfg = _VERB_CATALOG.get(tool) or {} if _VERB_CATALOG else {}
        verb_tier = vcfg.get("tier") or "routine"

        if verb_tier == "destructive" and privilege_group == "routine":
            verdict = "hitl"
            _acl_hitl_reason = f"ACL block: agent '{aname}' lacks privilege to run destructive verb '{tool}' without HITL approval."

            if _db_fire is not None and _db_post is not None and _db_create is not None:
                try:
                    _db_fire(_db_post(_db_create("event", {
                        "source": "agent-pipe",
                        "kind": "acl_decision",
                        "severity": "info",
                        "summary": f"ACL decision for {aname} calling {tool}: hitl",
                        "payload": {
                            "agent": aname,
                            "verb": tool,
                            "verdict": "hitl"
                        }
                    }, now_fields=("ts",))))
                except Exception:
                    pass

            try:
                _ah = _pending_hash(tool, args or {})
                _scope = _conv_key_var.get() or session_id
                _hitl_record_pending(tool, args or {}, _ah, _scope)
                if not isinstance(_proposal_var.get(), dict):
                    _proposal_var.set({"tool": tool, "args": args or {},
                                       "action_hash": _ah, "reason": _acl_hitl_reason})
            except Exception:
                pass

            return {"success": False, "output": "", "stderr": _acl_hitl_reason,
                    "exit_code": 126, "hitl_blocked": True}
        else:
            if _db_fire is not None and _db_post is not None and _db_create is not None:
                try:
                    _db_fire(_db_post(_db_create("event", {
                        "source": "agent-pipe",
                        "kind": "acl_decision",
                        "severity": "info",
                        "summary": f"ACL decision for {aname} calling {tool}: allow",
                        "payload": {
                            "agent": aname,
                            "verb": tool,
                            "verdict": "allow"
                        }
                    }, now_fields=("ts",))))
                except Exception:
                    pass

    _hitl_reason = _hitl_block_reason(tool, args)
    if _hitl_reason is None and _HITL_ARBITER_URL:
        _hitl_reason = await _hitl_arbiter_verdict(tool, args)  # #62 out-of-process arbiter
    if _hitl_reason is not None:
        try:
            _ah = _pending_hash(tool, args or {})   # NULL-free (pg TEXT-safe)
            _scope = _conv_key_var.get() or session_id
            _hitl_record_pending(tool, args or {}, _ah, _scope)
            if not isinstance(_proposal_var.get(), dict):
                _proposal_var.set({"tool": tool, "args": args or {},
                                   "action_hash": _ah, "reason": _hitl_reason})
        except Exception:  # noqa: BLE001
            pass
        return {"success": False, "output": "", "stderr": _hitl_reason,
                "exit_code": 126, "hitl_blocked": True}
    if tool == "web_search" and isinstance(args, dict):
        _rc = _recency_ctx_var.get()
        if _rc:
            if NATIVE_LOOP_DATE_IN_QUERY:
                try:
                    _q = str(args.get("query") or "").strip()
                    _ds = _current_date_str()
                    _ym = _ds[:7]
                    if _q and _ds not in _q and _ym not in _q:
                        args["query"] = _q + " " + _ym
                except Exception:  # noqa: BLE001 -- degrade-open
                    pass
            if not str(args.get("time_range") or "").strip() and _rc.get("time_range"):
                args["time_range"] = _rc["time_range"]
            try:
                if int(args.get("fanout") or 0) < int(_rc.get("fanout") or 0):
                    args["fanout"] = int(_rc["fanout"])
            except (TypeError, ValueError):
                args["fanout"] = int(_rc.get("fanout") or 0) or args.get("fanout")
    if not DISPATCH_DEDUP:
        return await _dispatch_bounded(tool, args, session_id=session_id)
    _a = args if isinstance(args, dict) else {}
    key = f"{_conv_key_var.get()}\x00{_action_hash(str(tool), _a)}"
    fut = _dispatch_inflight.get(key)
    if fut is not None:
        try:
            shared = await asyncio.shield(fut)
        except Exception:
            shared = None
        if isinstance(shared, dict):
            _emit_dispatch_dedup_event(str(tool), _a, session_id)
            dd = dict(shared)
            dd["deduped"] = True
            return dd
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    _dispatch_inflight[key] = fut
    try:
        res = await _dispatch_bounded(tool, args, session_id=session_id)
        if not fut.done():
            fut.set_result(res)
        return res
    except Exception as e:
        if not fut.done():
            fut.set_result({
                "success": False, "tool": tool,
                "args": _a, "output": "",
                "stderr": f"dispatch error: {e}", "exit_code": -1,
                "latency_ms": 0,
            })
        raise
    finally:
        _dispatch_inflight.pop(key, None)

def _emit_ro2_event(tool: str, args: dict, session_id: "Optional[str]",
                    verdict: "mios_ruleof2.RuleOfTwoVerdict", *, blocked: bool) -> None:
    try:
        _db_fire(_db_post(_db_create("event", {
            "source": "agent-pipe",
            "kind": "rule_of_two_block" if blocked else "rule_of_two_audit",
            "severity": "high" if blocked else "warn",
            "summary": (f"rule-of-two {'BLOCK' if blocked else 'audit'}: {tool} holds "
                        f"all 3 (untrusted-input + sensitive-access + state-change)"),
            "payload": {
                "tool": tool, "args": args,
                "rule_of_two": verdict.to_dict(),
                "agent": (_dispatch_agent_var.get() if _dispatch_agent_var else "") or "",
            },
        }, now_fields=("ts",))))
    except Exception:  # noqa: BLE001 -- audit is best-effort; never breaks dispatch
        pass

async def _rule_of_two_gate(tool: str, args: dict, *,
                            session_id: "Optional[str]" = None) -> "Optional[dict]":
    try:
        mode = mios_ruleof2.normalize_mode(RULE_OF_TWO_MODE)
        if mode == mios_ruleof2.MODE_OFF:
            return None
        vmeta = _VERB_CATALOG.get(tool) or {}
        a_tainted = False
        if session_id:
            a_tainted, _chain = await _session_is_tainted(session_id)
        verdict = mios_ruleof2.evaluate(
            session_tainted=a_tainted,
            permission_tier=vmeta.get("permission"),
            sensitive=vmeta.get("sensitive"),
            mode=mode)
        if not verdict.all_three:
            return None  # <=2 of {A,B,C} -> the invariant holds -> proceed
        if mode == mios_ruleof2.MODE_AUDIT:
            _emit_ro2_event(tool, args, session_id, verdict, blocked=False)
            return None  # non-blocking: observe before enforce
        approved = False
        try:
            if _hitl_approved_var is not None:
                _appr = _hitl_approved_var.get()
                approved = bool(_appr) and _appr == _pending_hash(tool, args or {})
        except Exception:  # noqa: BLE001
            approved = False
        if mios_hitl.decide(ro2_block=True, approved=approved) != mios_hitl.BLOCK:
            return None  # approved -> downgraded -> proceed
        _ah = _pending_hash(tool, args or {})
        try:
            _hitl_record_pending(tool, args or {}, _ah, session_id)
        except Exception:  # noqa: BLE001 -- recording is best-effort; the block still holds
            pass
        _emit_ro2_event(tool, args, session_id, verdict, blocked=True)
        _res = mios_hitl.block_result(tool, args, _ah)
        _res["rule_of_two_blocked"] = True
        return _res
    except Exception:  # noqa: BLE001 -- degrade-open: any error -> existing gate behaviour
        return None

def _emit_quarantine_event(tool: str, args: dict, session_id: "Optional[str]",
                           verdict: "mios_quarantine.QuarantineVerdict", *,
                           blocked: bool) -> None:
    try:
        _db_fire(_db_post(_db_create("event", {
            "source": "agent-pipe",
            "kind": "quarantine_block" if blocked else "quarantine_audit",
            "severity": "high" if blocked else "warn",
            "summary": (f"quarantine {'BLOCK' if blocked else 'audit'}: {tool} -- "
                        f"untrusted content drives a privileged "
                        f"(sensitive-read OR state-change) action"),
            "payload": {
                "tool": tool, "args": args,
                "quarantine": verdict.to_dict(),
                "agent": (_dispatch_agent_var.get() if _dispatch_agent_var else "") or "",
            },
        }, now_fields=("ts",))))
    except Exception:  # noqa: BLE001 -- audit is best-effort; never breaks dispatch
        pass

async def _quarantine_gate(tool: str, args: dict, *,
                           session_id: "Optional[str]" = None) -> "Optional[dict]":
    try:
        mode = mios_quarantine.normalize_mode(QUARANTINE_MODE)
        if mode == mios_quarantine.MODE_OFF:
            return None
        vmeta = _VERB_CATALOG.get(tool) or {}
        a_tainted = False
        if session_id:
            a_tainted, _chain = await _session_is_tainted(session_id)
            if not a_tainted and mios_scratchpad.SQLITE_VEC_ENABLE:
                scratchpad_dir = os.environ.get("MIOS_CONVERGE_MEMORY_SCRATCHPAD_DIR", "/tmp")
                if mios_scratchpad.has_tainted(session_id, scratchpad_dir):
                    a_tainted = True

        sensitive = bool(vmeta.get("sensitive"))
        is_side_effecting = mios_ruleof2.is_state_change(vmeta.get("permission"))
        if tool == "open_url" and _is_external_url(str((args or {}).get("url", ""))):
            is_side_effecting = True

        verdict = mios_quarantine.evaluate(
            session_tainted=a_tainted,
            permission_tier="write" if is_side_effecting else vmeta.get("permission"),
            sensitive=sensitive,
            mode=mode)
        if not verdict.bites:
            return None  # untainted OR non-privileged -> nothing to quarantine -> proceed
        if mode == mios_quarantine.MODE_AUDIT:
            _emit_quarantine_event(tool, args, session_id, verdict, blocked=False)
            return None  # non-blocking: observe before enforce
        approved = False
        try:
            if _hitl_approved_var is not None:
                _appr = _hitl_approved_var.get()
                approved = bool(_appr) and _appr == _pending_hash(tool, args or {})
        except Exception:  # noqa: BLE001
            approved = False
        if mios_hitl.decide(quarantine_block=True, approved=approved) != mios_hitl.BLOCK:
            return None  # approved -> downgraded -> proceed
        _ah = _pending_hash(tool, args or {})
        try:
            _hitl_record_pending(tool, args or {}, _ah, session_id)
        except Exception:  # noqa: BLE001 -- recording is best-effort; the block still holds
            pass
        _emit_quarantine_event(tool, args, session_id, verdict, blocked=True)
        _res = mios_hitl.block_result(tool, args, _ah)
        _res["quarantine_blocked"] = True
        return _res
    except Exception:  # noqa: BLE001 -- degrade-open: any error -> existing gate behaviour
        return None

async def _dispatch_mios_verb_inner(
    tool: str, args: dict, *,
    session_id: Optional[str] = None,
) -> dict:
    res = await _dispatch_mios_verb_inner_raw(tool, args, session_id=session_id)
    try:
        verdict = "allow"
        if res.get("firewall_blocked") or "firewall_block" in str(res.get("stderr") or ""):
            verdict = "block"
        elif res.get("hitl_blocked") or res.get("quarantine_blocked") or "hitl" in str(res.get("stderr") or "") or "quarantine" in str(res.get("stderr") or ""):
            verdict = "hitl"
        elif "quota_block" in str(res.get("stderr") or "") or "pdp_block" in str(res.get("stderr") or ""):
            verdict = "block"

        if _db_fire is not None and _db_post is not None and _db_create is not None:
            _db_fire(_db_post(_db_create("event", {
                "source": "agent-pipe",
                "kind": "firewall_decision",
                "severity": "high" if verdict != "allow" else "info",
                "summary": f"firewall decision: {tool} -> {verdict}",
                "payload": {
                    "tool": tool,
                    "args": args,
                    "verdict": verdict,
                    "session_id": session_id,
                }
            }, now_fields=("ts",))))
    except Exception:
        pass
    return res

async def _dispatch_mios_verb_inner_raw(
    tool: str, args: dict, *,
    session_id: Optional[str] = None,
) -> dict:
    tool = _normalize_tool_name(tool)
    if _letta_dispatch_handler:
        _res = await _letta_dispatch_handler(tool, args, session_id)
        if _res is not None:
            return _res
    _pdp_deny = _dispatch_pdp_reason(tool)
    if _pdp_deny is not None:
        _db_fire(_db_post(_db_create("event", {
            "source": "agent-pipe",
            "kind": "pdp_block",
            "severity": "high",
            "summary": _pdp_deny[:200],
            "payload": {"tool": tool, "args": args,
                        "agent": _dispatch_agent_var.get() or ""},
        }, now_fields=("ts",))))
        return {
            "success": False, "tool": tool, "args": args, "output": "",
            "stderr": f"pdp_block: {_pdp_deny}",
            "exit_code": 126, "latency_ms": 0,
            "pdp_blocked": True,
        }
    _q_deny = _dispatch_quota_reason(tool)
    if _q_deny is not None:
        _db_fire(_db_post(_db_create("event", {
            "source": "agent-pipe",
            "kind": "quota_block",
            "severity": "warn",
            "summary": _q_deny[:200],
            "payload": {"tool": tool, "user": (_match_user_cfg()[0] or "")},
        }, now_fields=("ts",))))
        return {
            "success": False, "tool": tool, "args": args, "output": "",
            "stderr": f"quota_block: {_q_deny}",
            "exit_code": 429, "latency_ms": 0,
            "quota_blocked": True,
        }
    if tool in _HIGH_PRIVILEGE_VERBS and session_id:
        is_tainted, chain = await _session_is_tainted(session_id)
        if is_tainted:
            _db_fire(_db_post(_db_create("event", {
                "source": "agent-pipe",
                "kind": "firewall_block",
                "severity": "high",
                "summary": f"refused {tool} (tainted session)",
                "payload": {
                    "tool": tool, "args": args,
                    "taint_chain": chain,
                },
            }, now_fields=("ts",))))
            return {
                "success": False, "tool": tool, "args": args,
                "output": "",
                "stderr": f"firewall_block: {tool} refused -- "
                          f"upstream taint: {chain}",
                "exit_code": -1, "latency_ms": 0,
                "tainted": True,
                "taint_reason": f"firewall_block:{chain[:200]}",
            }

    _ai_hitl = _hitl_block_reason(tool, args)
    if _ai_hitl is None and _HITL_ARBITER_URL:
        _ai_hitl = await _hitl_arbiter_verdict(tool, args)
    if _ai_hitl is not None:
        return {
            "success": False, "tool": tool, "args": args, "output": "",
            "stderr": _ai_hitl, "exit_code": 126, "latency_ms": 0,
            "hitl_blocked": True,
        }

    _hitl_block = await _hitl_gate(tool, args, session_id)
    if _hitl_block is not None:
        return _hitl_block

    if RULE_OF_TWO_MODE != mios_ruleof2.MODE_OFF:
        _ro2_block = await _rule_of_two_gate(tool, args, session_id=session_id)
        if _ro2_block is not None:
            return _ro2_block

    if QUARANTINE_MODE != mios_quarantine.MODE_OFF:
        _q_block = await _quarantine_gate(tool, args, session_id=session_id)
        if _q_block is not None:
            return _q_block

    _enum_err = _validate_enum_args(tool, args)
    if _enum_err is not None:
        return {
            "success": False, "tool": tool, "args": args,
            "output": "", "stderr": _enum_err,
            "exit_code": -1, "latency_ms": 0,
        }
    cmd = _build_dispatch_cmd(tool, args)
    if cmd is None:
        if tool in _VERB_CATALOG:
            v = _VERB_CATALOG[tool]
            required = [n for n, c in (v.get("params") or {}).items()
                        if isinstance(c, dict) and "default" not in c]
            stderr = (
                f"verb {tool!r} known but dispatch rejected: "
                f"args={list(args.keys())} required={required} "
                f"(check arg names; paths get auto-basenamed; "
                f"name equal to a known verb is refused as a defensive "
                f"check against planner emitting the probe tool name as "
                f"the launch target)"
            )
        else:
            stderr = (
                f"unknown verb {tool!r} (not in [verbs.*] catalog "
                f"of mios.toml; visible verbs: "
                f"{sorted(_VERB_CATALOG.keys())[:10]}...)"
            )
        return {
            "success": False, "tool": tool, "args": args,
            "output": "", "stderr": stderr,
            "exit_code": -1, "latency_ms": 0,
        }
    if not os.path.exists(LAUNCHER_SOCK):
        return {
            "success": False, "tool": tool, "args": args,
            "output": "", "stderr": f"broker socket missing at {LAUNCHER_SOCK}",
            "exit_code": -1, "latency_ms": 0,
        }
    try:
        _sbx_profile = _dispatch_sandbox_profile(tool)
        cmd, _sbx_ws = _sandbox_wrap_cmd(tool, cmd, _sbx_profile, session_id=session_id)
    except Exception:  # noqa: BLE001
        _sbx_profile, _sbx_ws = mios_sandbox.resolve_profile(""), None
    t0 = time.time()
    try:
        def _broker_io() -> str:
            s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            s.settimeout(float(os.environ.get("MIOS_BROKER_TIMEOUT_S", "60")))
            chunks: list[bytes] = []
            try:
                s.connect(LAUNCHER_SOCK)
                s.sendall(("CAPTURE_JSON: " + cmd + "\n").encode())
                while True:
                    buf = s.recv(65536)
                    if not buf:
                        break
                    chunks.append(buf)
            except _socket.timeout:
                pass
            finally:
                s.close()
            return b"".join(chunks).decode("utf-8", errors="replace").strip()
        raw = await asyncio.to_thread(_broker_io)
        try:
            j = _loads_lenient(raw) if raw else {}
        except json.JSONDecodeError:
            j = {}
        latency_ms = int((time.time() - t0) * 1000)
        if not j:
            return {
                "success": False, "tool": tool, "args": args,
                "output": "", "stderr": raw or "broker: empty response",
                "exit_code": -1, "latency_ms": latency_ms,
            }
        exit_code = int(j.get("exit_code", -1))
        v_tainted, v_reason = _classify_verb_taint(tool, args)
        _out = (j.get("stdout") or "")[:6000]
        _err = (j.get("stderr") or "")[:2000]
        if tool in _LAUNCH_VERBS and exit_code == 0:
            if not _out.strip():
                _tgt = args.get("name") or args.get("app") or args.get("url") or tool
                _out = f"Launched {_tgt} (verified)."
            _err = ""
        return {
            "success": exit_code == 0,
            "tool": tool, "args": args,
            "output": _out,
            "stderr": _err,
            "exit_code": exit_code,
            "latency_ms": latency_ms,
            "tainted": v_tainted,
            "taint_reason": v_reason,
            "sandbox": _sbx_profile.to_dict(),  # WS-A13 resolved confinement posture
        }
    except OSError as e:
        return {
            "success": False, "tool": tool, "args": args,
            "output": "", "stderr": f"broker: {e}",
            "exit_code": -1,
            "latency_ms": int((time.time() - t0) * 1000),
            "tainted": False,
            "taint_reason": "",
        }

dispatch_router = APIRouter()

@dispatch_router.post("/v1/dispatch")
async def dispatch_verb(body: dict) -> JSONResponse:
    """Dispatch a single MiOS verb. Body: {tool, args, session_id?}.
    Returns the same {success, output, stderr, exit_code, latency_ms,
    tainted, taint_reason} envelope as the DAG executor. Used by
    mios-mcp-server for MCP `tools/call`."""
    tool = str(body.get("tool", "")).strip()
    args = body.get("args") or {}
    if not isinstance(args, dict):
        args = {}
    session_id = body.get("session_id")
    result = await dispatch_mios_verb(tool, args, session_id=session_id)
    _record_dispatch_tool_call_row(tool, result, session_id)
    return JSONResponse(result)
