# AI-hint: Sub-agent TOOL LOOP for the OpenAI /v1 surface (MiOS is /v1-only), extracted verbatim from server.py (refactor R4 + a later move-home wave).
# AI-doc: usr/share/doc/mios/manual/routing.md

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from mios_toolexec import _exec_tool_calls, _rescue_tool_calls
from mios_jsonsalvage import loads_lenient as _loads_lenient

log = logging.getLogger("mios-agent-pipe")

def _tool_call_sig(tc: dict) -> str:
    """Stable (name + sorted-args) signature of a tool_call, for the loop's
    no-progress / runaway guard: if a round re-emits ONLY calls already made,
    the loop breaks instead of repeating forever (universal-loop slice 3)."""
    fn = tc.get("function") or {}
    name = str(fn.get("name") or "")
    args = fn.get("arguments")
    if isinstance(args, str):
        try:
            args = _loads_lenient(args)
        except Exception:  # noqa: BLE001
            pass
    try:
        a = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        a = str(args)
    return name + "\0" + a

_DISCLAIM_MARKERS = (
    "not available", "no data", "no information", "no specific",
    "provided context", "i cannot", "i can't", "unable to", "i do not have",
    "i don't have", "use my search", "use your search", "knowledge is current",
    "as of my knowledge", "no relevant", "could not find", "couldn't find",
    "no news", "try a different", "would you like me to",
)

def _looks_like_disclaimer(text: str) -> bool:
    t = (text or "").strip().lower()
    return bool(t) and any(m in t for m in _DISCLAIM_MARKERS)

def _tmsgs_indicate_failure(tmsgs: list) -> bool:
    """True if any tool-result message in this batch reports a genuine FAILURE (broker
    success=False, or a read-back/verification failure marker). A valid-but-EMPTY read
    result is NOT a failure (its dispatch succeeded) -> we never re-engage on empty
    results, only on real execution/verification failures. Bounds a supervisory
    tool-loop re-engage; does not judge answer content."""
    for _m in (tmsgs or []):
        _c = str((_m or {}).get("content") or "")
        if not _c:
            continue
        try:
            _d = _loads_lenient(_c)
            if isinstance(_d, dict):
                if _d.get("success") is False:
                    return True
                if "error" in _d:
                    return True
        except Exception:  # noqa: BLE001 -- not JSON, fall through to text markers
            pass
        if re.search(r'"success"\s*:\s*false|not[ _]verified|text_not_delivered'
                     r'|text_mismatch|dispatch error|no_verifiable_target|tool_execution_failed', _c, re.I):
            return True
    return False

SECONDARY_TOOL_MAX_ITERS = 15
SECONDARY_REPLAN_MAX = 5

_DAEMON_DIAGNOSE_MODEL = ""
_DAEMON_DIAGNOSE_ENDPOINT = ""
_DAEMON_DIAGNOSE_ENABLE = True

_apply_outbound_auth = None
_endpoint_supports_parallel_tools = None
_db_read = None
_db_create = None
_db_fire = None
_db_post = None

def configure(*, secondary_tool_max_iters=None, secondary_replan_max=None,
              daemon_diagnose_model=None, daemon_diagnose_endpoint=None,
              daemon_diagnose_enable=None,
              apply_outbound_auth=None,
              endpoint_supports_parallel_tools=None,
              db_read=None, db_create=None, db_fire=None, db_post=None) -> None:
    """Inject server.py's config scalars, the _DAEMON_DIAGNOSE_* constants and
    the runtime helpers the tool-loops + their guards call back into."""
    global SECONDARY_TOOL_MAX_ITERS, SECONDARY_REPLAN_MAX
    global _DAEMON_DIAGNOSE_MODEL, _DAEMON_DIAGNOSE_ENDPOINT
    global _DAEMON_DIAGNOSE_ENABLE
    global _apply_outbound_auth, _endpoint_supports_parallel_tools
    global _db_read, _db_create, _db_fire, _db_post
    if secondary_tool_max_iters is not None:
        SECONDARY_TOOL_MAX_ITERS = secondary_tool_max_iters
    if secondary_replan_max is not None:
        SECONDARY_REPLAN_MAX = secondary_replan_max
    if daemon_diagnose_model is not None:
        _DAEMON_DIAGNOSE_MODEL = daemon_diagnose_model
    if daemon_diagnose_endpoint is not None:
        _DAEMON_DIAGNOSE_ENDPOINT = daemon_diagnose_endpoint
    if daemon_diagnose_enable is not None:
        _DAEMON_DIAGNOSE_ENABLE = daemon_diagnose_enable
    if apply_outbound_auth is not None:
        _apply_outbound_auth = apply_outbound_auth
    if endpoint_supports_parallel_tools is not None:
        _endpoint_supports_parallel_tools = endpoint_supports_parallel_tools
    if db_read is not None:
        _db_read = db_read
    if db_create is not None:
        _db_create = db_create
    if db_fire is not None:
        _db_fire = db_fire
    if db_post is not None:
        _db_post = db_post

def _batch_has_write_verb(tcs: list) -> bool:
    """True if any call in the batch targets a state-changing verb, judged by
    the SSOT verb-catalog permission tier (write/interactive) -- NOT a hardcoded
    lexical read-only allowlist (Law 7). Unknown/read-tier verbs are read-only.
    Same permission classification reflect.py and the risk-tier sandbox use, so
    the write/failure gate generalises across every verb and stays SSOT-driven."""
    import mios_toolexec
    catalog = getattr(mios_toolexec, "_VERB_CATALOG", {}) or {}
    for tc in tcs:
        fn = tc.get("function") or {}
        vname = str(fn.get("name") or "").strip()
        perm = str((catalog.get(vname) or {}).get("permission", "")).lower()
        if perm in ("write", "interactive"):
            return True
    return False

_TOOL_NUDGE = (
    "You DID NOT call a tool. You are NOT knowledge-frozen and you DO have live "
    "tools (e.g. web_search) — do not disclaim, do not say 'no data' or 'use my "
    "tools'. CALL the relevant tool NOW to fetch what you need, then answer from "
    "the real results."
)

_REPLAN_NUDGE = (
    "One of your previous tool results reported a FAILURE or an UNVERIFIED outcome "
    "(the action did not actually take effect). Re-attempt that step now with a tool "
    "call. If it genuinely cannot succeed, say so plainly -- NEVER report success for "
    "an action that did not complete.")

async def _daemon_diagnose(client, failed_summary: str, goal: str) -> str:
    """DAEMON-DIAGNOSE ("the daemon monitors the pipeline and reports
    back"): a FRESH monitor-LLM pass over a FAILED step -- WHY it likely failed + a
    DIFFERENT concrete action to try -- so the closed-loop retry is GUIDED, not a blind
    re-run. A SECOND perspective (not the model that just gave up). Short + bounded +
    degrade-open: any error/empty/disabled -> '' (caller falls back to the generic nudge)."""
    if not _DAEMON_DIAGNOSE_ENABLE:
        return ""
    try:
        _prompt = (
            "You are the MiOS pipeline MONITOR. A step just FAILED.\n"
            f"User goal: {(goal or '')[:400]}\n"
            f"Failed step result: {(failed_summary or '')[:600]}\n"
            "In 1-2 sentences: WHY did it likely fail, and what DIFFERENT, concrete tool "
            "action should be tried next to fulfil the goal? Be specific and actionable. "
            "If it genuinely cannot succeed, say so plainly.")
        _body = {"model": _DAEMON_DIAGNOSE_MODEL,
                 "messages": [{"role": "user", "content": _prompt}],
                 "stream": False, "max_tokens": 220,
                 "chat_template_kwargs": {"enable_thinking": False}}
        _r = await client.post(
            f"{_DAEMON_DIAGNOSE_ENDPOINT}/chat/completions",
            content=json.dumps(_body).encode("utf-8"),
            headers={"Content-Type": "application/json"}, timeout=30)
        if _r.status_code != 200:
            return ""
        _ch = (_r.json().get("choices") or [])
        return str((_ch[0].get("message") if _ch else {})
                   .get("content") or "").strip()[:500]
    except Exception:  # noqa: BLE001 -- degrade-open, never block the retry
        return ""

async def _v1_secondary_tool_loop(client, ep: str, model: str, headers: dict,
                                  messages: list, tools: list, timeout,
                                  push, allow_write: bool = False, tool_choice=None,
                                  session_id: Optional[str] = None,
                                  terminal_response: Optional[dict] = None, max_tokens=None) -> list:
    import sys
    import time
    msgs = list(messages)

    agent_cfg = {}
    if "mios_config" in sys.modules:
        try:
            cfg_func = sys.modules["mios_config"]._toml_section
            agent_cfg = cfg_func("agent_pipe") or {}
        except Exception:
            pass
    reflexion_enable = str(agent_cfg.get("reflexion_enable", "true")).strip().lower() not in {"false", "0", "no", "off"}
    no_progress_window = int(agent_cfg.get("no_progress_window", 3))
    max_consecutive_failures = int(agent_cfg.get("max_consecutive_failures", 3))
    wall_clock_budget_s = float(agent_cfg.get("wall_clock_budget_s", 300))

    _tool_max_iters = int(agent_cfg.get("tool_max_iters", SECONDARY_TOOL_MAX_ITERS))
    _replan_max = int(agent_cfg.get("replan_max", SECONDARY_REPLAN_MAX))

    start_iter = 0
    if session_id and _db_read:
        try:
            ckpt_sql = f"SELECT id, meta FROM session WHERE owui_chat_id = '{session_id}' AND kind = 'checkpoint' ORDER BY ts DESC LIMIT 1"
            ckpt_rows = await _db_read(ckpt_sql, pg_sql=ckpt_sql)
            if ckpt_rows and ckpt_rows[0].get("meta"):
                meta = ckpt_rows[0]["meta"]
                if isinstance(meta, str):
                    meta = json.loads(meta)
                msgs = meta.get("messages") or msgs
                start_iter = int(meta.get("superstep_idx") or 0) + 1
                log.info("Resuming ReAct loop from superstep %d (checkpoint %s)", start_iter, ckpt_rows[0]["id"])
        except Exception as _ckpt_err:
            log.warning("Failed to load ReAct checkpoint: %s", _ckpt_err)

    _seen: set = set()   # tool-call signatures already made -> loop guard
    _nudged = False      # one-shot anti-disclaimer nudge already injected?
    _replan = 0          # supervisory closed-loop re-engages used (bounded)
    _last_failed = False # did the previous tool batch report a genuine failure?
    _hdrs = dict(headers or {})
    _hdrs.setdefault("Content-Type", "application/json")
    _apply_outbound_auth(_hdrs, ep)

    start_time = time.monotonic()
    consecutive_failures = 0
    blacklist = set()
    signature_history = []
    tool_name_history = []

    for _ in range(start_iter, max(1, _tool_max_iters)):
        if time.monotonic() - start_time >= wall_clock_budget_s:
            log.warning("secondary tool-loop hit wall-clock budget %.1fs -> terminating", wall_clock_budget_s)
            msgs.append({"role": "user", "content": f"SYSTEM ALERT: You have exceeded the maximum execution time budget of {wall_clock_budget_s} seconds for this turn. Do NOT make any more tool calls. Summarize the information you have gathered and provide a final answer to the user now."})
            break

        nb = {"model": model, "messages": msgs, "tools": tools, "stream": False,
              "parallel_tool_calls": _endpoint_supports_parallel_tools(ep)}
        if tool_choice and _ == 0:
            nb["tool_choice"] = tool_choice
        if max_tokens is not None:
            nb["max_tokens"] = max_tokens
        try:
            r = await client.post(
                f"{ep}/chat/completions",
                content=json.dumps(nb).encode("utf-8"),
                headers=_hdrs, timeout=timeout)
            if r.status_code != 200:
                if terminal_response is not None:
                    terminal_response["_backend_failed"] = True
                log.warning("v1 tool-loop non-200: status=%s model=%s url=%s body=%s",
                            r.status_code, model, f"{ep}/chat/completions", r.text[:240])
                break
            ch = (r.json().get("choices") or [])
            msg = (ch[0].get("message") if ch else {}) or {}
            finish_reason = (ch[0].get("finish_reason") if ch else None)
            if finish_reason not in ("stop", "tool_calls"):
                if terminal_response is not None:
                    terminal_response["_backend_failed"] = True
                    terminal_response["_incomplete"] = True
                log.warning("v1 tool-loop incomplete completion: finish_reason=%r", finish_reason)
                break
            _rc = msg.get("reasoning_content") or ""
            if not _rc and msg.get("content"):
                _m = re.search(r"<think>(.*?)</think>", msg["content"], re.DOTALL | re.IGNORECASE)
                if _m:
                    _rc = _m.group(1).strip()
            if _rc:
                push(f"\n\n🤖 {model} thinking:\n{_rc}\n")
        except Exception as e:  # noqa: BLE001 -- best-effort
            if terminal_response is not None:
                terminal_response["_backend_failed"] = True
            log.debug("v1 secondary tool-loop call failed: %s", e)
            break
        tcs = msg.get("tool_calls") or []
        if not tcs:
            _rescued = _rescue_tool_calls(msg.get("content") or "", tools)
            if _rescued:
                push(" 🛟")
                tcs = _rescued
                _c = msg.get("content") or ""
                _c = re.sub(r"<tool_call>.*?</tool_call>", "", _c, flags=re.DOTALL | re.IGNORECASE)
                _c = re.sub(r"```(?:json)?\s*\{.*?\}\s*```", "", _c, flags=re.DOTALL | re.IGNORECASE)
                _c = re.sub(r"<function=.*?</function>", "", _c, flags=re.DOTALL | re.IGNORECASE)
                msg["content"] = _c.strip()
        if not tcs:
            if finish_reason != "stop":
                if terminal_response is not None:
                    terminal_response["_backend_failed"] = True
                    terminal_response["_incomplete"] = True
                break
            if not _nudged and _looks_like_disclaimer(msg.get("content") or ""):
                _nudged = True
                push(" 🪤")
                msgs.append({"role": "assistant",
                             "content": msg.get("content") or ""})
                msgs.append({"role": "user", "content": _TOOL_NUDGE})
                continue
            if _last_failed and _replan < _replan_max:
                _replan += 1
                _last_failed = False
                push(f" 🔁{_replan}")
                log.info("tool-loop CLOSED-LOOP re-engage #%d: prior verb FAILED and the "
                         "model gave up -> fix-it nudge (bounded %d)",
                         _replan, _replan_max)
                _goal = next((str(_o.get("content") or "") for _o in (messages or [])
                              if isinstance(_o, dict) and _o.get("role") == "user"), "")
                _failsum = next((str(_o.get("content") or "") for _o in reversed(msgs)
                                 if isinstance(_o, dict) and _o.get("role") == "tool"
                                 and str(_o.get("content") or "").strip()), "")
                _diag = await _daemon_diagnose(client, _failsum, _goal)
                if _diag:
                    push(" 🩺")
                    log.info("daemon-diagnose: %s", _diag[:140])
                msgs.append({"role": "assistant",
                             "content": msg.get("content") or ""})
                msgs.append({"role": "user", "content": (
                    _REPLAN_NUDGE + ("\n\nMONITOR DIAGNOSIS (a second perspective): "
                                     + _diag if _diag else ""))})
                continue
            # Preserve the accepted completion for callers that already have an
            # answer; tool-limit/failure exits still require final synthesis.
            if terminal_response is not None and str(msg.get("content") or "").strip():
                terminal_response.update(msg)
            break

        _sigs = [_tool_call_sig(_tc) for _tc in tcs]
        if _sigs and all(_s in _seen for _s in _sigs):
            break   # loop guard: no NEW tool calls this round -> stop (runaway)
        _seen.update(_sigs)

        turn_names = tuple(sorted(str(tc.get("function", {}).get("name") or "") for tc in tcs))
        turn_sigs = tuple(sorted(_tool_call_sig(tc) for tc in tcs))
        signature_history.append(turn_sigs)
        tool_name_history.append(turn_names)

        if len(signature_history) >= no_progress_window:
            last_n_sigs = signature_history[-no_progress_window:]
            if len(set(last_n_sigs)) == 1:
                log.warning("tool-loop runaway detected: same tool signatures called consecutively %d times", no_progress_window)
                msgs.append({"role": "user", "content": "SYSTEM ALERT: Runaway loop detected (repeated identical tool calls). Terminating loop."})
                break

            last_n_names = tool_name_history[-no_progress_window:]
            if len(set(last_n_names)) == 1:
                log.warning("tool-loop runaway detected: same tool names called consecutively %d times", no_progress_window)
                msgs.append({"role": "user", "content": "SYSTEM ALERT: Runaway loop detected (repeated identical tool names). Terminating loop."})
                break

        active_tcs = []
        blacklisted_tmsgs = []
        for tc in tcs:
            sig = _tool_call_sig(tc)
            if sig in blacklist:
                log.warning("tool-loop intercepting blacklisted tool call: %s", sig)
                blacklisted_tmsgs.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "name": tc.get("function", {}).get("name"),
                    "content": json.dumps({
                        "success": False,
                        "error": "tool_execution_failed: This exact tool call previously failed in this turn. Do not retry the same call."
                    })
                })
            else:
                active_tcs.append(tc)

        msgs.append({"role": "assistant",
                     "content": msg.get("content") or "", "tool_calls": tcs})

        _tmsgs = []
        ran_read = False
        if active_tcs:
            _tmsgs, ran_read = await _exec_tool_calls(active_tcs, push, allow_write=allow_write)
        _tmsgs.extend(blacklisted_tmsgs)
        msgs.extend(_tmsgs)

        batch_failed = _tmsgs_indicate_failure(_tmsgs)
        if batch_failed:
            _last_failed = True
            consecutive_failures += 1
            for tc in active_tcs:
                tc_id = tc.get("id")
                tm = next((m for m in _tmsgs if m.get("tool_call_id") == tc_id), None)
                if tm and _tmsgs_indicate_failure([tm]):
                    blacklist.add(_tool_call_sig(tc))
        else:
            if _batch_has_write_verb(tcs) or not _last_failed:
                _last_failed = False
                consecutive_failures = 0

        if batch_failed and reflexion_enable:
            if consecutive_failures >= max_consecutive_failures:
                log.warning("tool-loop hit max_consecutive_failures=%d -> terminating", max_consecutive_failures)
                msgs.append({"role": "user", "content": "SYSTEM ALERT: Maximum consecutive tool failures reached. Terminating loop."})
                break

            failed_tc = None
            failed_tm = None
            for tc in active_tcs:
                tc_id = tc.get("id")
                tm = next((m for m in _tmsgs if m.get("tool_call_id") == tc_id), None)
                if tm and _tmsgs_indicate_failure([tm]):
                    failed_tc = tc
                    failed_tm = tm
                    break

            if failed_tc and failed_tm:
                try:
                    from mios_reflect import reflect_on_step_failure
                    failed_node = {
                        "tool": failed_tc.get("function", {}).get("name"),
                        "args": failed_tc.get("function", {}).get("arguments") or {}
                    }
                    if isinstance(failed_node["args"], str):
                        try:
                            failed_node["args"] = json.loads(failed_node["args"])
                        except Exception:
                            pass
                    failed_result = {
                        "exit_code": "?",
                        "stderr": failed_tm.get("content") or "",
                        "output": failed_tm.get("content") or ""
                    }
                    plan_context = {"summary": "Sub-agent tool execution"}

                    push(" 🧠[Reflecting]")
                    correction = await reflect_on_step_failure(
                        failed_node, failed_result, plan_context, session_id
                    )

                    if correction and isinstance(correction, dict) and correction.get("tool"):
                        rationale = correction.get("rationale") or "Correcting failed step"
                        push(f"\n\n🤖 Reflexion correction: {rationale}\n")

                        reflection_prompt = (
                            f"SYSTEM REFLEXION: A tool call failed. Corrective action suggested: "
                            f"tool={correction['tool']}, args={json.dumps(correction['args'])}. "
                            f"Analyze and proceed with this revised approach."
                        )
                        msgs.append({"role": "user", "content": reflection_prompt})

                        if _db_create and _db_fire and _db_post:
                            try:
                                sql = _db_create("event", {
                                    "source": "mios-agent-pipe",
                                    "kind": "reflexion_retry",
                                    "severity": "warning",
                                    "summary": f"Triggering structured reflexion: retry {correction['tool']}",
                                    "session_id": session_id,
                                    "act_type": "challenge"
                                }, now_fields=("ts",))
                                await _db_fire(_db_post(sql))
                            except Exception:
                                pass
                    else:
                        log.info("Reflexion returned no correction (unfixable) -> terminating")
                        msgs.append({"role": "user", "content": "SYSTEM ALERT: Reflection determined the failure is unfixable. Terminating loop."})
                        break
                except Exception as ref_err:
                    log.warning("Failed to run structured reflexion: %s", ref_err)
                    break

        if session_id and _db_create and _db_fire and _db_post:
            superstep_id = f"superstep_{_}"
            checkpoint_key = f"{session_id}:{superstep_id}"
            try:
                ckpt_meta = {
                    "messages": msgs,
                    "superstep_idx": _,
                }
                del_sql = f"DELETE FROM session WHERE id = '{checkpoint_key}'"
                _db_fire(_db_post(del_sql))
                ins_sql = _db_create("session", {
                    "id": checkpoint_key,
                    "kind": "checkpoint",
                    "owui_chat_id": session_id,
                    "meta": ckpt_meta
                }, now_fields=("ts",))
                _db_fire(_db_post(ins_sql))
                log.info("Saved ReAct superstep checkpoint %s", checkpoint_key)
            except Exception as _ckpt_save_err:
                log.warning("Failed to save ReAct checkpoint %s: %s", checkpoint_key, _ckpt_save_err)

        if not ran_read:
            break
    else:
        log.warning("secondary tool-loop hit MAX_ITERS=%d -> returning PARTIAL "
                    "(model kept requesting tools without a final answer)",
                    max(1, SECONDARY_TOOL_MAX_ITERS))
        msgs.append({"role": "user", "content": "SYSTEM ALERT: You have exhausted the maximum number of tool calls for this turn. Do NOT make any more tool calls. Summarize the information you have gathered and provide a final answer to the user now."})
    return msgs
