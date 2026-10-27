# AI-hint: The agent-pipe CHAT-COMPLETIONS router-brain, extracted VERBATIM from AI-related: ./server.py, ./mios_vision.py, ./mios_oscontrol.py, ./mios_...
# AI-doc: usr/share/doc/mios/manual/routing.md

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import re
import time
import uuid
from typing import Any, AsyncGenerator, Optional

import httpx
import contextvars
import hashlib
import random

_record_active = contextvars.ContextVar("_record_active", default=False)
_replay_active = contextvars.ContextVar("_replay_active", default=False)
_replay_llm_queue = contextvars.ContextVar("_replay_llm_queue", default=[])
_replay_tool_queue = contextvars.ContextVar("_replay_tool_queue", default=[])
_in_exec_tool_calls = contextvars.ContextVar("_in_exec_tool_calls", default=False)

_orig_post = httpx.AsyncClient.post

async def _patched_post(self, url, *args, **kwargs):
    url_str = str(url)
    is_llm = "/chat/completions" in url_str

    if is_llm and _replay_active.get():
        q = _replay_llm_queue.get()
        if q:
            row = q.pop(0)
            meta = row.get("meta") or {}
            completion = meta.get("completion") or {}
            log.info("Replayed LLM call for URL: %s", url_str)
            return httpx.Response(status_code=200, json=completion)
        else:
            log.warning("No recorded LLM response in queue for: %s", url_str)
            return httpx.Response(status_code=200, json={"choices": [{"message": {"role": "assistant", "content": "(error: no recorded LLM response found)"}}]})

    resp = await _orig_post(self, url, *args, **kwargs)

    if is_llm and _record_active.get():
        try:
            session_id = _conv_key_var.get() if (_conv_key_var is not None and hasattr(_conv_key_var, "get")) else "default"
            json_body = kwargs.get("json") or {}
            resp_body = resp.json()

            db_w = globals().get("_db_write")
            if db_w is not None:
                import uuid
                row = {
                    "id": f"session:llm:{uuid.uuid4().hex[:24]}",
                    "kind": "llm_io",
                    "owui_chat_id": session_id,
                    "meta": {
                        "prompt": json_body.get("messages") or [],
                        "completion": resp_body
                    }
                }
                db_w("session", row, now_fields=("ts",), passport_sign=False)
                log.info("Recorded LLM call in session: %s", session_id)
        except Exception as e:
            log.warning("Failed to record LLM call in session: %s", e)

    return resp

httpx.AsyncClient.post = _patched_post

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

import mios_trace
import mios_pg as _mios_pg   # WS-9 Postgres client (canonical kanban upsert)
from mios_config import (   # layered mios.toml SSOT reader (aggregate-budget cluster + REFINE/ROUTER lanes)
    _cfg_num, _toml_section, REFINE_MODEL, REFINE_ENDPOINT, REFINE_TIMEOUT_S,
    ROUTER_MODEL, PLANNER_ENDPOINT, PLANNER_TIMEOUT_S)

from mios_agent_call import _call_agent_complete
from mios_dag_exec import _execute_dag_bounded, _execute_dag_emitting
from mios_dci import critic_then_maybe_flow
from mios_dispatch import dispatch_mios_verb
from mios_fanout import _pick_fanout_agents
from mios_grounding import _env_grounding
from mios_knowledge import _recall_knowledge, _store_knowledge
from mios_native_loop import _respond_local_state, _respond_native_loop_direct
from mios_oscontrol import _respond_os_control
from mios_planner import decompose_intent
import mios_preempt   # task_19/task_19: turn-boundary preemption seam (flag-gated, degrade-open)
from mios_refine import refine_intent
from mios_routing import _deterministic_action_route
from mios_sse import _sse_chunk, _sse_status, _stream_answer
from mios_swarm import _agent_dag_from_tasks, _respond_agent_dag
import mios_tokenize
from mios_tokenize import _usage_estimate, _normalize_usage
from mios_verity import polish_response
from mios_vision import _client_tools_complete, _has_client_tools, _vision_complete
from mios_web_research import _web_research_enrich
# Conversation history lives in chat_history. Re-exported: chat_completions_logic
# resolves these names here, so tests that patch chat.<name> keep working.
from mios_pipe.routing.chat_history import (  # noqa: F401 -- re-exported
    _drop_stale_tool_results, _summarize_evicted_messages,
    _get_gateway_session, _save_gateway_session,
)

log = logging.getLogger("mios-agent-pipe")

ASK_CLARIFY_ENABLE = None
AUTONOMOUS_PRIORITY = None
AUTO_FORCE_TOOL = None
BACKEND = None
GATEWAY_QUEUE = None
BACKEND_MODEL = None
CLIENT_TOOLS_PASSTHROUGH = None
COUNCIL_DEFAULT = None
DCI_ENABLED = None
KERNEL_ROUTE = None
KERNEL_DISPATCH = None
LOCAL_STATE_FASTPATH = None
MAX_DISPATCH_DEPTH = None
NATIVE_LOOP_ENABLE = None
NATIVE_LOOP_MATH_HINT = None
PLANNER_ENABLED = None
POLISH_ENABLED = None
SLOW_LANES = None
SLOW_LANE_BLOCK_CHARS = None
SWARM_DECOMPOSE_DEFAULT = None
SWARM_DECOMPOSE_MIN_WORDS = None
SWARM_MAX_WIDTH = None
SWARM_TRUST_ATOMIC = None
VISION_ENABLE = None
VISION_MODEL = None
WORKER_TOOLS_ENABLE = None
WORKER_TOOL_CTX = None
_AGENT_REGISTRY = None
_BACKEND_IS_LIGHT = None
_BACKEND_KEY = None
_BROWSER_ACTION_ALT = None
_FASTPATH_VERBS = None
_HOP_HEADER = None
_HUMAN_LABELS = None
_INGRESS_KEY = None
_KERNEL = None
_SRC_TURN_HEADER = None
_THINK_ORPHAN_RE = None
_TOOL_BACKEND = None
_TOOL_BACKEND_MODEL = None
_VERB_CATALOG = None
_VIA_HEADER = None
_agent_contract = None
_agent_lane = None
_agent_offload_engine = None
_build_agent_hint = None
_call_agent_stream = None
_casual_agent_label = None
_client_env = None
_client_env_var = None
_conv_key_var = None
_council_mode_var = None
_council_role_lens = None
_critic_refine_agent = None
_current_year = None
_db_create = None
_db_fire = None
_db_post = None
_depth_exhausted = None
_dispatch_depth = None
_endpoint_supports_tool_choice = None
_expand_facets = None
_extract_last_user_text = None
_filter_relevant_sources = None
_get_client = None
_inline_satisfaction_check = None
_is_action_domain = None
_lane_tool_cap = None
_live_agent_names = None
_loads_lenient = None
_maybe_run_pending_approval = None
_messages_have_image = None
_multi_task_preamble = None
_needs_compute = None
_node_status = None
_pick_agent = None
_plan_swarm = None
_rag_enrich = None
_read_tool_enrich = None
_recall_agent_memory = None
_role_system = None
_route_domain = None
_routed_domain_var = None
_sanitize_tool_text = None
_sched_priority = None
_scratchpad_key = None
_scratchpad_note = None
_scratchpad_rehydrate = None
_scratchpad_render = None
_seed_hop_from_headers = None
_sources_annotations = None
_sources_markdown = None
_sources_metadata = None
_sources_var = None
_span_id_var = None
_src_collected = None
_src_record_from_text = None
_src_turn_init = None
_src_turn_key = None
_src_turn_var = None
_sse_done = None
_sse_reasoning = None
_sse_status_phase = None
_strip_owui_scaffold = None
_strip_think_tags = None
_trace_id_var = None
_turn_volatile_var = None
_vram_checkpoint = None
_worker_tools_surface_async = None
_write_skill_md_fire = None
classify_intent = None
LETTA_MEMORY_BACKEND = False
_LETTA_CLIENT = None
_db_write = None
_embed_one = None
_scratchpad_for = None
EMB_MODEL = None
EMB_VERSION = None
_turn_tenant = None
SCRATCHPAD_PERSIST = False
_DEBUG_ENABLE = True

_INJECTED = frozenset((
    "LETTA_MEMORY_BACKEND", "_LETTA_CLIENT", "_DEBUG_ENABLE",
    "_db_write", "_embed_one", "_scratchpad_for", "EMB_MODEL", "EMB_VERSION", "_turn_tenant", "SCRATCHPAD_PERSIST",
    "ASK_CLARIFY_ENABLE", "AUTONOMOUS_PRIORITY", "AUTO_FORCE_TOOL", "BACKEND", "BACKEND_MODEL",
    "GATEWAY_QUEUE",
    "CLIENT_TOOLS_PASSTHROUGH", "COUNCIL_DEFAULT", "DCI_ENABLED", "KERNEL_ROUTE", "KERNEL_DISPATCH",
    "LOCAL_STATE_FASTPATH", "MAX_DISPATCH_DEPTH", "NATIVE_LOOP_ENABLE",
    "NATIVE_LOOP_MATH_HINT", "PLANNER_ENABLED", "POLISH_ENABLED", "SLOW_LANES",
    "SLOW_LANE_BLOCK_CHARS", "SWARM_DECOMPOSE_DEFAULT",
    "SWARM_DECOMPOSE_MIN_WORDS", "SWARM_MAX_WIDTH", "SWARM_TRUST_ATOMIC", "VISION_ENABLE",
    "VISION_MODEL", "WORKER_TOOLS_ENABLE", "WORKER_TOOL_CTX", "_AGENT_REGISTRY",
    "_BACKEND_IS_LIGHT", "_BACKEND_KEY", "_BROWSER_ACTION_ALT", "_FASTPATH_VERBS",
    "_HOP_HEADER", "_HUMAN_LABELS", "_INGRESS_KEY", "_KERNEL", "_SRC_TURN_HEADER",
    "_THINK_ORPHAN_RE", "_TOOL_BACKEND", "_TOOL_BACKEND_MODEL", "_VERB_CATALOG", "_VIA_HEADER",
    "_agent_contract", "_agent_lane", "_agent_offload_engine",
    "_build_agent_hint", "_call_agent_stream",
    "_casual_agent_label", "_client_env", "_client_env_var", "_conv_key_var",
    "_council_mode_var", "_council_role_lens", "_critic_refine_agent", "_current_year",
    "_db_create", "_db_fire", "_db_post", "_depth_exhausted", "_dispatch_depth",
    "_endpoint_supports_tool_choice", "_expand_facets", "_extract_last_user_text",
    "_filter_relevant_sources", "_get_client",
    "_inline_satisfaction_check", "_is_action_domain", "_lane_tool_cap",
    "_live_agent_names", "_loads_lenient", "_maybe_run_pending_approval",
    "_messages_have_image", "_multi_task_preamble", "_needs_compute",
    "_node_status", "_pick_agent", "_plan_swarm",
    "_rag_enrich", "_read_tool_enrich", "_recall_agent_memory",
    "_role_system", "_route_domain", "_routed_domain_var", "_sanitize_tool_text",
    "_sched_priority", "_scratchpad_key", "_scratchpad_note", "_scratchpad_rehydrate",
    "_scratchpad_render", "_seed_hop_from_headers",
    "_sources_annotations", "_sources_markdown", "_sources_metadata", "_sources_var",
    "_span_id_var", "_src_collected", "_src_record_from_text", "_src_turn_init",
    "_src_turn_key", "_src_turn_var", "_sse_done", "_sse_reasoning", "_sse_status_phase",
    "_strip_owui_scaffold", "_strip_think_tags", "_trace_id_var",
    "_turn_volatile_var", "_vram_checkpoint", "_worker_tools_surface_async",
    "_write_skill_md_fire", "classify_intent",
))

def configure(**deps) -> None:
    g = globals()
    if "_KERNEL" in deps and deps["_KERNEL"] is not None:
        deps["_KERNEL"].dispatcher._handlers["chat"] = _kernel_chat_handler
        deps["_KERNEL"].dispatcher._handlers["dispatch"] = _kernel_dispatch_handler
        deps["_KERNEL"].dispatcher._handlers["multi_task"] = _kernel_multi_task_handler
        deps["_KERNEL"].dispatcher._handlers["agent"] = _kernel_agent_handler
    for _k, _v in deps.items():
        if _k in _INJECTED:
            g[_k] = _v

def _pretty_name(n: str) -> str:
    """Display name for roster/credit emits -- strips the internal node:/a2a:
 namespacing so emits never show raw registry keys (
    'NO INTERNAL NAMES')."""
    s = str(n)
    for _pre in ("node:", "a2a:"):
        if s.startswith(_pre):
            return s[len(_pre):]
    return s

def _trim_sys_prefix(sys_prefix: list, lane: str) -> list:
    if lane not in SLOW_LANES or SLOW_LANE_BLOCK_CHARS <= 0:
        return sys_prefix
    _pin = _agent_contract()
    trimmed: list = []
    for m in sys_prefix:
        c = str(m.get("content", ""))
        if _pin and c == _pin:
            trimmed.append(m)
            continue
        if len(c) > SLOW_LANE_BLOCK_CHARS:
            c = (mios_tokenize.truncate_to_tokens(c, SLOW_LANE_BLOCK_CHARS // 4)
                 + "\n[...trimmed for the light lane...]")
        trimmed.append({**m, "content": c})
    return trimmed

async def _quick_chat_reply(user_text: str, history: list = None) -> str:
    if not user_text or not user_text.strip():
        return ""
    msgs = [{"role": "system",
             "content": ("You are MiOS AI. Reply to the user directly and "
                         "concisely, in ENGLISH by default -- switch to "
                         "another language ONLY if the user's own message "
                         "is clearly written in it. Never drift to a "
                         "language the user did not use. Plain text only -- "
                         "no tools, no JSON.\n\n" + _env_grounding())}]
    if history:
        for h in history[-2:]:
            if isinstance(h, dict) and h.get("role") in ("user", "assistant"):
                msgs.append({"role": h["role"],
                             "content": mios_tokenize.truncate_to_tokens(
                                 str(h.get("content", "")), 50)})  # WS-A5 seam (was [:200])
    msgs.append({"role": "user", "content": user_text[:500]})
    payload = {
        "model": REFINE_MODEL,
        "messages": msgs,
        "stream": False,
        "temperature": 0.5,
        "max_tokens": 1024,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        async with httpx.AsyncClient(timeout=REFINE_TIMEOUT_S) as s:
            r = await s.post(f"{REFINE_ENDPOINT}/v1/chat/completions", json=payload,
                             headers={"Content-Type": "application/json"})
            if r.status_code != 200:
                return ""
            body = r.json()
    except Exception:
        return ""
    msg = (((body.get("choices") or [{}])[0]).get("message")
           if isinstance(body, dict) else {}) or {}
    return (msg.get("content") or msg.get("reasoning_content") or "").strip()

async def _is_memory_question(user_text: str, facts: str) -> bool:
    """FOCUSED yes/no judge (using REFINE_MODEL) to verify if the user's question
    is ACTUALLY asking for the retrieved facts, to prevent false-positive
    deterministic short-circuits. Robust to synonyms and weak structural overlaps."""
    if not facts or not facts.strip():
        return False
    try:
        msgs = [{"role": "system",
                 "content": ("You are a strict YES/NO judge. The user is asking a question. "
                             "Does the following saved fact directly answer the user's question? "
                             "Reply with ONLY 'YES' or 'NO'.\n\nFacts:\n" + facts)},
                {"role": "user", "content": (user_text or "")[:500]}]
        payload = {"model": REFINE_MODEL, "messages": msgs,
                   "stream": False, "temperature": 0.1, "max_tokens": 10}
        async with httpx.AsyncClient(timeout=3.0) as s:
            r = await s.post(f"{REFINE_ENDPOINT}/v1/chat/completions", json=payload,
                             headers={"Content-Type": "application/json"})
            if r.status_code == 200:
                _lm = (((r.json().get("choices") or [{}])[0]).get("message") or {})
                ans = str(_lm.get("content") or "").strip().upper()
                return "YES" in ans
    except Exception:  # noqa: BLE001
        pass
    return False

async def _ask_for_location(user_text: str) -> str:
    """Brief reply asking the user for their city when the request NEEDS their
 physical location but none was forwarded this session (
    "check the weather for tomorrow" must ASK, never fabricate a city -- it
    invented "San Fernando del Valle de Catamarca, Argentina"). GENERATED in the
    user's language; NEVER guesses a location. Falls back to a sane default."""
    try:
        msgs = [{"role": "system", "content":
                 ("You are MiOS AI. The user's request needs their physical "
                  "location, but none was shared this session. Reply BRIEFLY (1-2 "
                  "sentences): say you can help but need their city/area, and ask "
                  "them to name it. Do NOT guess, assume, or state ANY city or "
                  "country. Reply in the user's language -- English by default, "
                  "match the user's language only if their message is clearly in "
                  "it. Plain text only.")},
                {"role": "user", "content": (user_text or "")[:500] + " /no_think"}]
        payload = {"model": REFINE_MODEL, "messages": msgs,
                   "stream": False, "temperature": 0.3, "max_tokens": 200}
        async with httpx.AsyncClient(timeout=REFINE_TIMEOUT_S) as s:
            r = await s.post(f"{REFINE_ENDPOINT}/v1/chat/completions", json=payload,
                             headers={"Content-Type": "application/json"})
            if r.status_code == 200:
                _lm = ((r.json().get("choices") or [{}])[0]).get("message") or {}
                c = (_lm.get("content") or _lm.get("reasoning_content") or "").strip()
                c = re.sub(r"<think>.*?</think>\s*", "", c,
                           flags=re.DOTALL | re.IGNORECASE).strip()
                if c:
                    return c
    except Exception:  # noqa: BLE001
        pass
    return ("I can help with that — but I don't have your location this session. "
            "Which city or area should I use?")

def _hints_write_action(refined: "Optional[dict]") -> bool:
    """True when refine hinted a state-changing (NON-read permission) verb -- the
    turn INTENDS an action, so the executor should be FORCED to emit the call
    rather than narrate it. Data-driven by verb permission (no English action-
    word list, per the no-hardcode binding). Skips chat-class turns."""
    if not isinstance(refined, dict):
        return False
    if refined.get("intent") not in ("agent", "multi_task", "dispatch"):
        return False
    for h in (refined.get("hint_tools") or []):
        perm = str((_VERB_CATALOG.get(str(h).strip()) or {}).get(
            "permission", "")).lower()
        if perm and perm != "read":
            return True
    return False

async def _needs_external_knowledge(user_text: str) -> bool:
    if not (user_text or "").strip():
        return False
    sys = (
        "A query about THIS local machine was received. Inspecting this machine "
        "reveals its OWN state + identity (which CPU/GPU/app/version it HAS, and live "
        "usage) but NOT external knowledge ABOUT those things. Decide, by MEANING not "
        "keywords: to FULLY answer the user, is EXTERNAL knowledge required that "
        "CANNOT be obtained by inspecting this machine -- e.g. a component's "
        "published or theoretical specifications, benchmarks, capabilities, ratings, "
        "reviews, prices, or whether an installed version is the newest? Examples: "
        "'what's open' / 'what GPU do I have' / 'list my games' / 'check service "
        "status' -> false (its own identity/state is the whole answer). 'the "
        "theoretical performance of my GPU' / 'is my installed X the latest version' "
        "/ 'how good is my CPU for AI' -> true (needs off-machine facts).")
    payload = {
        "model": ROUTER_MODEL,
        "messages": [{"role": "system", "content": sys},
                     {"role": "user", "content": user_text[:2000]}],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "kgap", "strict": True, "schema": {
                "type": "object",
                "properties": {"needs_external": {"type": "boolean"}},
                "required": ["needs_external"], "additionalProperties": False}}},
        "chat_template_kwargs": {"enable_thinking": False},
        "temperature": 0.0, "max_tokens": 30, "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=PLANNER_TIMEOUT_S) as s:
            r = await s.post(f"{PLANNER_ENDPOINT}/v1/chat/completions", json=payload,
                             headers={"Content-Type": "application/json"})
        if r.status_code != 200:
            return False
        content = ((r.json().get("choices") or [{}])[0].get("message", {})
                   .get("content") or "")
        return (_loads_lenient(content) or {}).get("needs_external") is True
    except Exception as e:  # noqa: BLE001 -- degrade-CLOSED (-> pure-local, unchanged)
        log.debug("knowledge-gap judge failed (-> local-only): %s", e)
        return False

def _shadow_queue_tasks(tasks: list[dict],
                        session_id: Optional[str]) -> list[dict]:
    if not isinstance(tasks, list) or not tasks:
        return []
    out: list[dict] = []
    for i, t in enumerate(tasks):
        if not isinstance(t, dict):
            continue
        tid = (
            "mt-"
            + (session_id or "anon")[:12].replace(":", "")
            + "-"
            + f"{i:02d}"
        )
        title = str(t.get("title") or t.get("refined_text") or "")[:200]
        status = "in_progress" if i == 0 else "todo"
        prio = t.get("priority")
        prio_str = str(prio) if prio is not None else None
        detail = json.dumps({
            "hermes_task_id": tid,
            "priority": prio_str,
            "tags": ["multi_task", "agent-pipe-refined"],
            "session_id": session_id,
        }, default=str)
        _db_fire(_mios_pg.execute(
            "INSERT INTO kanban (id, title, status, detail) "
            "VALUES (%(id)s, %(title)s, %(status)s, %(detail)s::jsonb) "
            "ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title, "
            "status = EXCLUDED.status, detail = EXCLUDED.detail, ts = now()",
            {"id": tid, "title": title, "status": status, "detail": detail},
            fetch=False))
        out.append({**t, "hermes_task_id": tid, "status": status})
    _db_fire(_db_post(_db_create("event", {
        "source": "mios-agent-pipe",
        "kind": "multi_task_queued",
        "severity": "info",
        "summary": f"queued {len(out)} tasks from refine",
        "payload": {"task_ids": [t["hermes_task_id"] for t in out],
                    "titles": [t.get("title", "") for t in out]},
    }, now_fields=("ts",))))
    return out

_BUDGET_TOML = _toml_section("budget")

def _budget_num(env: str, key: str, default, cast=int):
    """env override -> mios.toml [budget].<key> -> literal default (preserves 0)."""
    return _cfg_num(_BUDGET_TOML, env, key, default, cast)

BUDGET_CONV_TOKEN_CEIL = _budget_num(
    "MIOS_BUDGET_CONV_TOKEN_CEIL", "conversation_token_ceil", 2_000_000)
BUDGET_AUTO_TOKEN_CEIL = _budget_num(
    "MIOS_BUDGET_AUTO_TOKEN_CEIL", "autonomous_token_ceil", 400_000)
BUDGET_AUTO_MAX_INFLIGHT = _budget_num(
    "MIOS_BUDGET_AUTO_MAX_INFLIGHT", "autonomous_max_inflight", 1)
BUDGET_WINDOW_S = _budget_num("MIOS_BUDGET_WINDOW_S", "window_s", 3600, float)
BUDGET_ENABLE = str(os.environ.get(
    "MIOS_BUDGET_ENABLE",
    str(_BUDGET_TOML.get("enable", "true")))).strip().lower() not in {"0", "false", "no"}

_BUDGET_LEDGER: "collections.OrderedDict" = collections.OrderedDict()
_BUDGET_LEDGER_MAX = int(os.environ.get("MIOS_BUDGET_LEDGER_MAX", "1024"))
BUDGET_PER_TURN_ESTIMATE = _budget_num(
    "MIOS_BUDGET_PER_TURN_ESTIMATE", "per_turn_estimate", 8192)
_BUDGET_AUTO_INFLIGHT: dict = {}
BUDGET_INFLIGHT_TTL_S = _budget_num(
    "MIOS_BUDGET_INFLIGHT_TTL_S", "inflight_ttl_s", 900, float)
_BUDGET_LOCK = asyncio.Lock()

def _budget_bucket(key: str) -> "collections.deque":
    dq = _BUDGET_LEDGER.get(key)
    if dq is None:
        dq = collections.deque()
        _BUDGET_LEDGER[key] = dq
        while len(_BUDGET_LEDGER) > max(16, _BUDGET_LEDGER_MAX):
            _BUDGET_LEDGER.popitem(last=False)
    else:
        _BUDGET_LEDGER.move_to_end(key)
    return dq

def _budget_window_total(key: str, now: float) -> int:
    """Sum tokens debited to `key` within the rolling window; ages out the rest."""
    dq = _BUDGET_LEDGER.get(key)
    if not dq:
        return 0
    cutoff = now - BUDGET_WINDOW_S
    while dq and dq[0][0] < cutoff:
        dq.popleft()
    return sum(t for _ts, t in dq)

def _budget_debit(key: str, tokens: int, now: Optional[float] = None) -> None:
    """Record `tokens` against `key`'s window ledger (best-effort, degrade-open)."""
    if not BUDGET_ENABLE or tokens <= 0:
        return
    try:
        _now = now if now is not None else time.monotonic()
        _budget_bucket(key).append((_now, int(tokens)))
    except Exception:  # noqa: BLE001 -- ledger is a backstop, never crashes a turn
        log.debug("budget debit failed for %s", key, exc_info=True)

def _budget_prune_inflight(now: float) -> None:
    """Drop in-flight autonomous tokens older than the TTL (crash/abandon safety;
    caller holds _BUDGET_LOCK)."""
    if not _BUDGET_AUTO_INFLIGHT:
        return
    cutoff = now - BUDGET_INFLIGHT_TTL_S
    for tok in [t for t, ts in _BUDGET_AUTO_INFLIGHT.items() if ts < cutoff]:
        _BUDGET_AUTO_INFLIGHT.pop(tok, None)

async def _budget_admit(conv_key: str, autonomous_source: Optional[str],
                        turn_token: Optional[str] = None) -> tuple:
    if not BUDGET_ENABLE:
        return True, ""
    try:
        now = time.monotonic()
        async with _BUDGET_LOCK:
            conv_used = _budget_window_total("conv:" + conv_key, now)
            if BUDGET_CONV_TOKEN_CEIL > 0 and conv_used >= BUDGET_CONV_TOKEN_CEIL:
                return False, ("conversation token budget exhausted "
                               f"({conv_used}/{BUDGET_CONV_TOKEN_CEIL} in "
                               f"{int(BUDGET_WINDOW_S)}s)")
            if autonomous_source:
                _budget_prune_inflight(now)
                auto_used = _budget_window_total("auto:" + autonomous_source, now)
                if (BUDGET_AUTO_TOKEN_CEIL > 0
                        and auto_used >= BUDGET_AUTO_TOKEN_CEIL):
                    return False, ("autonomous token budget exhausted "
                                   f"({auto_used}/{BUDGET_AUTO_TOKEN_CEIL} in "
                                   f"{int(BUDGET_WINDOW_S)}s)")
                if (BUDGET_AUTO_MAX_INFLIGHT > 0
                        and len(_BUDGET_AUTO_INFLIGHT) >= BUDGET_AUTO_MAX_INFLIGHT):
                    return False, ("autonomous concurrency limit reached "
                                   f"({len(_BUDGET_AUTO_INFLIGHT)}/"
                                   f"{BUDGET_AUTO_MAX_INFLIGHT} in flight)")
            _budget_debit("conv:" + conv_key, BUDGET_PER_TURN_ESTIMATE, now)
            if autonomous_source:
                _budget_debit("auto:" + autonomous_source,
                              BUDGET_PER_TURN_ESTIMATE, now)
                if turn_token:
                    _BUDGET_AUTO_INFLIGHT[turn_token] = now
        return True, ""
    except Exception:  # noqa: BLE001 -- degrade-open: never block a turn on a bug
        log.warning("budget admit check failed, degrading open", exc_info=True)
        return True, ""

async def _budget_release_inflight(turn_token: Optional[str]) -> None:
    if not turn_token:
        return
    try:
        async with _BUDGET_LOCK:
            _BUDGET_AUTO_INFLIGHT.pop(turn_token, None)
    except Exception:  # noqa: BLE001
        log.debug("budget inflight release failed for %s", turn_token, exc_info=True)

async def chat_completions_logic(request: Request) -> Any:
    try:
        body_bytes = await request.body()
        body = _loads_lenient(body_bytes.decode("utf-8", errors="replace")) if body_bytes else {}
    except json.JSONDecodeError:
        return JSONResponse(
            content={"error": {"message": "invalid JSON body",
                               "type": "invalid_request_error"}},
            status_code=400,
        )

    streaming = bool(body.get("stream", False))
    messages = body.get("messages") or []
    if not messages or not isinstance(messages, list):
        return JSONResponse(
            content={"error": {"message": "you must provide a 'messages' array",
                               "type": "invalid_request_error",
                               "param": "messages", "code": None}},
            status_code=400)

    gateway_cfg = _toml_section("gateway") or {}
    meta = body.get("metadata") or {}
    session_id = str(meta.get("chat_id") or meta.get("session_id") or "")
    session_replay_opt_in = (
        bool(request.headers.get("x-mios-session-replay")) or
        bool(meta.get("session_replay")) or
        bool(gateway_cfg.get("session_replay", False))
    )

    if session_replay_opt_in and session_id:
        history = await _get_gateway_session(session_id)
        if history:
            if len(history) < len(messages):
                messages = history + messages[len(history):]
            body["messages"] = messages

    if isinstance(messages, list):
        filtered_messages = []
        for m in messages:
            if isinstance(m, dict) and m.get("role") in ("system", "developer"):
                content = str(m.get("content") or "")
                if ("# MiOS Grounding / Knowledge Base" in content
                        or "Operate under `/MiOS.md`." in content
                        or "You are the MiOS local agent." in content):
                    log.info("Stripped duplicate/canonical client system prompt from request messages")
                    continue
            filtered_messages.append(m)
        messages = filtered_messages
        body["messages"] = messages

    if str(body.get("tool_choice") or "").strip().lower() == "none":
        body.pop("tools", None)
        body.pop("tool_choice", None)
        log.info("tool_choice: 'none' -> stripped tool definitions; 0 tool tokens for plain chat")

    last_user_text = _extract_last_user_text(messages)
    _clean_user = _strip_owui_scaffold(last_user_text)
    if _clean_user != last_user_text:
        log.info("un-templated OWUI task scaffold: %d -> %d chars",
                 len(last_user_text), len(_clean_user))
        for _i in range(len(messages) - 1, -1, -1):
            _m = messages[_i]
            if isinstance(_m, dict) and _m.get("role") == "user" \
                    and isinstance(_m.get("content"), str):
                messages[_i] = {**_m, "content": _clean_user}
                break
        last_user_text = _clean_user
    model = body.get("model") or BACKEND_MODEL
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    _conv_key_var.set(_scratchpad_key(body, chat_id))
    await _scratchpad_rehydrate(_conv_key_var.get())

    _record_active.set(False)
    _replay_active.set(False)
    _replay_llm_queue.set([])
    _replay_tool_queue.set([])

    obs_cfg = _toml_section("observability") or {}
    record_mode = bool(obs_cfg.get("record_mode", False))
    replay_mode = bool(obs_cfg.get("replay_mode", False)) or bool(request.headers.get("x-mios-replay"))
    replay_sess_id = request.headers.get("x-mios-replay-session") or _conv_key_var.get()

    if replay_mode:
        try:
            from mios_pg import execute as pg_execute
            rows = await pg_execute(
                "SELECT kind, meta FROM session "
                "WHERE owui_chat_id = %s AND (kind = 'llm_io' OR kind = 'tool_io') "
                "ORDER BY chain_seq ASC",
                (replay_sess_id,),
                fetch=True
            )
            llm_q = [r for r in (rows or []) if r.get("kind") == "llm_io"]
            tool_q = [r for r in (rows or []) if r.get("kind") == "tool_io"]
            _replay_llm_queue.set(llm_q)
            _replay_tool_queue.set(tool_q)
            _replay_active.set(True)
            log.info("Replay mode active: loaded %d LLM responses, %d tool responses for session %s",
                     len(llm_q), len(tool_q), replay_sess_id)

            h = hashlib.md5(replay_sess_id.encode("utf-8"), usedforsecurity=False).hexdigest()
            seed = int(h, 16) % (2**32)
            random.seed(seed)
            try:
                import numpy as np
                np.random.seed(seed)
            except ImportError:
                pass
            log.info("Deterministic stochasticity seeded: %d", seed)
        except Exception as e:
            log.warning("Failed to initialize replay mode: %s", e)
    elif record_mode:
        _record_active.set(True)
        log.info("Record mode active for session: %s", _conv_key_var.get())

    if LETTA_MEMORY_BACKEND and _LETTA_CLIENT:
        try:
            import mios_tokenize
            _tok_count = mios_tokenize.count_messages(messages, tools=body.get("tools"))
            _letta_ctx_limit = 8000
            _fill = _tok_count / _letta_ctx_limit
            _session_id = _conv_key_var.get() or "default"
            if _fill >= 1.0:
                log.info("Letta context fill >= 100%% (%d tokens), triggering oldest context message flush", _tok_count)
                await _LETTA_CLIENT.sync_to_pg(_session_id, lambda table, fields: _db_fire(_db_post(_db_create(table, fields, now_fields=("ts",)))))
                await _LETTA_CLIENT.flush_oldest(_session_id)
            elif _fill >= 0.7:
                log.info("Letta context fill >= 70%% (%d tokens), triggering native summarization loop", _tok_count)
                await _LETTA_CLIENT.trigger_compaction(_session_id)
        except Exception as _letta_err:
            log.warning("Letta memory context threshold logic failed: %s", _letta_err)
    else:
        try:
            import mios_tokenize
            import os
            memory_cfg = _toml_section("memory") or {}
            n_ctx = int(os.environ.get("MIOS_MEMORY_N_CTX") or memory_cfg.get("n_ctx", 8000))
            compaction_interval = int(os.environ.get("MIOS_MEMORY_COMPACTION_INTERVAL") or memory_cfg.get("compaction_interval", 20))
            tool_result_ttl_turns = int(os.environ.get("MIOS_MEMORY_TOOL_RESULT_TTL_TURNS") or memory_cfg.get("tool_result_ttl_turns", 5))
            compaction_threshold_pct = float(os.environ.get("MIOS_MEMORY_COMPACTION_THRESHOLD_PCT") or memory_cfg.get("compaction_threshold_pct", 80)) / 100.0

            session_id = _conv_key_var.get() or "default"
            total_turns = sum(1 for m in messages if m.get("role") == "assistant")
            if total_turns >= compaction_interval:
                messages = _drop_stale_tool_results(messages, tool_result_ttl_turns)
                body["messages"] = messages

            tok_count = mios_tokenize.count_messages(messages, tools=body.get("tools"))
            fill = tok_count / n_ctx

            if fill >= compaction_threshold_pct:
                log.info("Native context fill >= %d%% (%d/%d tokens). Evicting oldest turns and summarizing.", int(compaction_threshold_pct * 100), tok_count, n_ctx)

                import mios_compact
                budget = int(n_ctx * 0.5)
                plan = mios_compact.plan_compaction(messages, budget=budget, keep_recent=4, keep_system=True)

                if plan.needed and plan.to_summarize:
                    summary = await _summarize_evicted_messages(plan.to_summarize)

                    if _scratchpad_for is not None:
                        _scratchpad_for(session_id).appendleft({
                            "ts": time.time(),
                            "agent": "system-summary",
                            "lane": "",
                            "phase": "",
                            "note": f"Recursive summary of evicted conversation history: {summary}",
                        })

                    if SCRATCHPAD_PERSIST and _db_write is not None:
                        try:
                            _db_write("scratch", {
                                "chat_id": session_id,
                                "agent": "system-summary",
                                "lane": "",
                                "phase": "",
                                "note": f"Recursive summary of evicted conversation history: {summary}",
                            }, now_fields=("ts",))
                        except Exception:
                            pass

                    emb_val = None
                    try:
                        if _embed_one is not None:
                            emb_val = await _embed_one(f"Summary of evicted conversation history: {summary}")
                    except Exception:
                        pass

                    fields = {
                        "fact": f"Summary of evicted conversation history: {summary}",
                        "scope": f"conversation:{session_id}",
                        "mem_key": f"archived-{time.time()}",
                        "source": "system",
                        "owner_user": _turn_tenant() if _turn_tenant else None,
                    }
                    if emb_val:
                        fields["emb"] = emb_val
                        fields["emb_model"] = EMB_MODEL
                        fields["emb_version"] = EMB_VERSION

                    if _db_write is not None:
                        try:
                            _db_write("agent_memory", fields)
                        except Exception:
                            pass

                    new_messages = []
                    system_msgs = [m for m in plan.to_keep if m.get("role") in ("system", "developer")]
                    other_msgs = [m for m in plan.to_keep if m.get("role") not in ("system", "developer")]

                    new_messages.extend(system_msgs)
                    new_messages.append({
                        "role": "system",
                        "content": f"System checkpoint -- recursive summary of evicted conversation history:\n{summary}"
                    })
                    new_messages.extend(other_msgs)

                    tokens_before = tok_count
                    messages = new_messages
                    body["messages"] = messages
                    tokens_after = mios_tokenize.count_messages(messages, tools=body.get("tools"))

                    if _db_write is not None:
                        try:
                            _db_write("event", {
                                "source": "agent-pipe",
                                "kind": "context_compaction",
                                "severity": "info",
                                "summary": f"Context compacted: {tokens_before} -> {tokens_after} tokens",
                                "payload": {
                                    "tokens_before": tokens_before,
                                    "tokens_after": tokens_after,
                                    "session_id": session_id
                                }
                            }, now_fields=("ts",))
                        except Exception:
                            pass

            elif fill >= 0.7:
                log.info("Native context fill >= 70%% (%d/%d tokens). Warning agent.", tok_count, n_ctx)
                if _db_write is not None:
                    try:
                        _db_write("event", {
                            "source": "agent-pipe",
                            "kind": "context_warning",
                            "severity": "medium",
                            "summary": f"Context fill warning: {tok_count} tokens is >= 70% of budget {n_ctx}",
                            "payload": {
                                "tokens": tok_count,
                                "budget": n_ctx,
                                "session_id": session_id
                            }
                        }, now_fields=("ts",))
                    except Exception:
                        pass

                messages.append({
                    "role": "system",
                    "content": f"WARNING: Conversation context is at {int(fill*100)}% capacity ({tok_count}/{n_ctx} tokens). Please use memory_append or memory_replace to save important information before it is evicted."
                })
        except Exception as _mem_err:
            log.warning("Native memory context threshold logic failed: %s", _mem_err)
    try:
        _atr = await _maybe_run_pending_approval(
            last_user_text, _conv_key_var.get(),
            chat_id=chat_id, model=model, streaming=streaming)
        if _atr is not None:
            return _atr
    except Exception as _atre:  # noqa: BLE001 -- never break the turn on the approval path
        log.debug("ask-to-run approval check skipped: %s", _atre)
    _sources_var.set([])
    _inbound_trace = (request.headers.get("x-mios-trace") or "").strip()
    _trace_id_var.set(_inbound_trace or mios_trace.new_trace_id())
    _span_id_var.set("")
    _seed_hop_from_headers(request.headers.get(_HOP_HEADER),
                           request.headers.get(_VIA_HEADER))
    _incoming_turn = request.headers.get(_SRC_TURN_HEADER)
    if _incoming_turn:
        _src_turn_var.set(_incoming_turn)
    else:
        _src_turn_var.set(_src_turn_key())
        _src_turn_init()
    _client_env_var.set(_client_env(body, request.headers))
    _rok_hdr = (request.headers.get("x-mios-reasoning-ok") or "").strip().lower()
    _reasoning_ok = None if not _rok_hdr else (_rok_hdr not in {"false", "0", "no"})
    _routed_domain_var.set(await _route_domain(last_user_text))
    _mflags = body.get("mios_flags")
    _mflags = _mflags if isinstance(_mflags, dict) else {}
    _force_council = bool(_mflags.get("force_council"))
    _force_delegate = bool(_mflags.get("force_delegate"))
    _force_tool = bool(_mflags.get("force_tool"))
    if _mflags:
        log.info("swarm flags: council=%s delegate=%s tool=%s",
                 _force_council, _force_delegate, _force_tool)
    _meta_top = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    _autonomous = bool(_meta_top.get("mios_autonomous"))
    _autonomous_source = None
    if _autonomous:
        _autonomous_source = str(
            _meta_top.get("mios_source") or _meta_top.get("rule_id")
            or _meta_top.get("schedule_id") or _meta_top.get("cron_id")
            or "autonomous")
        log.info("AUTONOMOUS turn (source=%s) -> bounded (no wide research fan-out on either path)",
                 _autonomous_source)

    from mios_pipe.routing.agent_call import _autonomous_var, _autonomous_source_var
    _autonomous_var.set(_autonomous)
    _autonomous_source_var.set(_autonomous_source)
    _persona_system = "\n\n".join(
        str(m.get("content") or "").strip()
        for m in messages
        if isinstance(m, dict) and m.get("role") in ("system", "developer")
        and str(m.get("content") or "").strip()
    )[:2000]

    if VISION_ENABLE and _messages_have_image(messages):
        log.info("vision: image turn -> %s", VISION_MODEL)
        return await _vision_complete(body, streaming, chat_id, model)

    if CLIENT_TOOLS_PASSTHROUGH and _has_client_tools(body):
        if _INGRESS_KEY:
            _auth = (request.headers.get("authorization") or "")
            if _auth.removeprefix("Bearer ").strip() != _INGRESS_KEY:
                return JSONResponse(
                    content={"error": {"message": "unauthorized",
                                       "type": "invalid_request_error"}},
                    status_code=401)
        _ct_user = ""
        for _m in reversed(body.get("messages") or []):
            if isinstance(_m, dict) and _m.get("role") == "user" and isinstance(_m.get("content"), str):
                _ct_user = _m["content"]
                break
        _ct_det = _deterministic_action_route(_ct_user) if _ct_user else None
        if _ct_det and str(_ct_det.get("tool") or "") in _FASTPATH_VERBS:
            log.info("client-tools: deterministic OS-action %s -> SERVER-SIDE "
                     "fast-path (bypassing client-tools hybrid)", _ct_det["tool"])
            return await _respond_os_control(
                _ct_det["tool"], _ct_det.get("args") or {}, _ct_det,
                streaming=streaming, chat_id=chat_id, model=model,
                session_id=None, last_user_text=_ct_user, persona_system="")
        if not _incoming_turn and _ct_user and _routed_domain_var.get(None) == "web":
            try:
                _refined = await refine_intent(_ct_user)
                if not _refined or _refined.get("intent") != "chat":
                    _search_q = str((_refined or {}).get("refined_text") or "").strip() or _ct_user
                    _time_sensitive = bool((_refined or {}).get("news") or (_refined or {}).get("needs_recency"))
                    if _time_sensitive and not re.search(r"\b(?:19|20)\d{2}\b", _search_q):
                        _search_q = f"{_search_q} {_current_year()}".strip()
                        if isinstance(_refined, dict):
                            _refined["refined_text"] = _search_q
                    _wtext = await _web_research_enrich(_search_q, _refined)
                    if _wtext.strip():
                        body = dict(body)
                        body["messages"] = list(body.get("messages") or []) + [{
                            "role": "system", "content": _wtext}]
                        log.info("client-tools: web-grounded research turn "
                                 "(prefetched deep web_research, anti-fabrication)")
            except Exception as _e:  # noqa: BLE001 -- degrade-open
                log.debug("client-tools web prefetch skipped: %s", _e)
        if _ct_user and _routed_domain_var.get(None) == "files":
            _mfn = (re.search(r"['\"]([^'\"]+\.[A-Za-z0-9]{1,8})['\"]", _ct_user)
                    or re.search(r"\b([\w.+-]+\.[A-Za-z0-9]{1,8})\b", _ct_user))
            _fn = _mfn.group(1).strip() if _mfn else None
            if _fn:
                _hits = []
                for _sv in ("everything_search", "fs_search"):
                    if _sv not in _VERB_CATALOG:
                        continue
                    try:
                        _sr = await dispatch_mios_verb(_sv, {"query": _fn},
                                                       session_id=None)
                        _st = (str(_sr.get("output") or _sr.get("result") or _sr)
                               if isinstance(_sr, dict) else str(_sr or "")).strip()
                        if _st and _st not in ("{}", "null", "[]", '""'):
                            _hits.append(_sv + " -> " + _st[:1500])
                    except Exception:  # noqa: BLE001 -- degrade-open
                        pass
                if _hits:
                    body = dict(body)
                    body["messages"] = list(body.get("messages") or []) + [{
                        "role": "system", "content":
                        "LIVE local file-search results for '" + _fn + "' on THIS "
                        "machine (real paths). To answer, read_file the ACTUAL path "
                        "below; do NOT invent a path or answer from memory:\n"
                        + "\n".join(_hits)}]
                    log.info("client-tools: file-search grounded files turn "
                             "(prefetched '%s')", _fn)
        log.info("client-tools passthrough: %d tool(s) -> %s (%s)",
                 len(body.get("tools") or []), _TOOL_BACKEND_MODEL, _TOOL_BACKEND)
        return await _client_tools_complete(body, streaming, chat_id, model)

    session_id: Optional[str] = None
    try:
        resp = await _db_post(_db_create(
            "session",
            {"platform": "mios-agent-pipe",
             "model": model},
            now_fields=("started_at",),
            extra="RETURN id",
            passport_sign=False,
        ))
        if isinstance(resp, list) and resp:
            last = resp[-1] or {}
            rows = last.get("result") or []
            if isinstance(rows, list) and rows:
                rid = rows[0].get("id")
                if rid:
                    session_id = str(rid)
    except Exception as e:
        log.debug("session open failed: %s", e)

    if streaming:
        outer_ctx = locals().copy()
        async def _stream_refine_and_dispatch() -> AsyncGenerator[bytes, None]:
            nonlocal last_user_text, messages
            refined = None
            if not _incoming_turn:
                yield _sse_status_phase(chat_id=chat_id, model=model, phase="refine")
                yield _sse_status(chat_id=chat_id, model=model, emoji="🧠", label="Refining intent & decomposing plan...")

                await _vram_checkpoint()

                queue = asyncio.Queue()
                async def _on_refine_token(token_val: str, is_re: bool):
                    await queue.put((token_val, is_re))

                refine_task = asyncio.create_task(
                    refine_intent(last_user_text, messages, on_token=_on_refine_token)
                )

                while not refine_task.done() or not queue.empty():
                    try:
                        token_val, is_re = await asyncio.wait_for(queue.get(), timeout=0.05)
                        if token_val and is_re:
                            yield _sse_reasoning(token_val, chat_id=chat_id, model=model,
                                                 reasoning_ok=_reasoning_ok)
                        queue.task_done()
                    except asyncio.TimeoutError:
                        continue

                refined = await refine_task
                if refined and refined.get("refined_text"):
                    _refined_q = str(refined["refined_text"]).strip()
                    if _refined_q:
                        last_user_text = _refined_q
                        for _i in range(len(messages) - 1, -1, -1):
                            _m = messages[_i]
                            if isinstance(_m, dict) and _m.get("role") == "user" \
                                    and isinstance(_m.get("content"), str):
                                messages[_i] = {**_m, "content": _refined_q}
                                break

            try:
                _turn_volatile_var.set(bool(refined and (
                    refined.get("local_state") or refined.get("news")
                    or refined.get("needs_location"))))
            except Exception:
                pass

            try:
                _turn_priority = float(_sched_priority(refined).get("score", 5.0))
            except Exception:
                _turn_priority = 5.0

            if _autonomous:
                _turn_priority = min(_turn_priority, AUTONOMOUS_PRIORITY)

            try:
                await mios_preempt.turn_boundary(task_id=str(chat_id), priority=_turn_priority)
            except Exception:
                pass

            if mios_preempt.QUEUE_ENABLE:
                try:
                    mios_preempt._TURN_QUEUE.enqueue(str(chat_id), _turn_priority)
                    mios_preempt._TURN_QUEUE.dispatch()
                except Exception:
                    pass

            if refined:
                _intent = refined.get("intent")
                _text = refined.get("refined_text")
                _tasks = refined.get("tasks") or []
                _label = f"Refined intent: {_intent}"
                if _text:
                    _label += f" | Plan: {_text}"
                if _tasks:
                    _label += f" ({len(_tasks)} tasks planned)"
                yield _sse_status(chat_id=chat_id, model=model, emoji="📋", label=_label)

                _refine_reasoning = f"💭 [refine] classified intent as '{_intent}'\n"
                if _text:
                    _refine_reasoning += f"🎯 refined query: '{_text}'\n"
                if refined.get("intended_outcome"):
                    _refine_reasoning += f"🏁 intended outcome: '{refined.get('intended_outcome')}'\n"
                if _tasks:
                    _refine_reasoning += "📋 planned tasks:\n"
                    for _t in _tasks:
                        _refine_reasoning += f"  - {_t.get('title')}: {_t.get('refined_text')} (web={_t.get('web')}, local={_t.get('local_state')})\n"

                yield _sse_reasoning(_refine_reasoning, chat_id=chat_id, model=model,
                                     reasoning_ok=_reasoning_ok)

            _budget_turn_token = (chat_id if (_autonomous and _autonomous_source) else None)
            _budget_ok, _budget_reason = await _budget_admit(
                _scratchpad_key(body, chat_id), _autonomous_source, _budget_turn_token)
            if not _budget_ok:
                log.warning("budget HARD-HALT: %s", _budget_reason)
                _halt_msg = (
                    "I'm pausing here: this conversation has reached its aggregate "
                    "compute budget for now (" + _budget_reason + "). Please try again "
                    "shortly, or rephrase as a single focused request.")
                async for _ab in _stream_answer(_halt_msg, chat_id=chat_id, model=model):
                    yield _ab
                yield _sse_chunk("", chat_id=chat_id, model=model, finish_reason="stop")
                yield _sse_done()
                return

            if _KERNEL:
                _kdec = _KERNEL.router.route(refined)
                log.info("kernel dispatch (stream): routing mode=%s", _kdec.mode)

                ctx_copy = outer_ctx.copy()
                ctx_copy.update({
                    "refined": refined,
                    "_turn_priority": _turn_priority,
                    "_budget_turn_token": _budget_turn_token,
                })
                try:
                    res = await _KERNEL.dispatcher.run(_kdec, **ctx_copy)
                    if isinstance(res, StreamingResponse):
                        async for chunk in res.body_iterator:
                            yield chunk
                    else:
                        try:
                            _content = json.loads(bytes(res.body).decode("utf-8"))["choices"][0]["message"]["content"]
                            yield _sse_chunk(_content, chat_id=chat_id, model=model)
                        except Exception:
                            pass
                except Exception as _run_err:
                    log.warning("streaming dispatcher execution failed: %s", _run_err)
                finally:
                    await _budget_release_inflight(_budget_turn_token)
            else:
                yield _sse_chunk("Error: Kernel not configured", chat_id=chat_id, model=model)
                yield _sse_done()

        _res_stream = StreamingResponse(_stream_refine_and_dispatch(), media_type="text/event-stream")
        if session_replay_opt_in and session_id:
            original_iterator = _res_stream.body_iterator
            async def _session_saving_iterator():
                assistant_reply = []
                async for chunk in original_iterator:
                    yield chunk
                    try:
                        chunk_str = chunk.decode("utf-8", errors="replace")
                        for line in chunk_str.splitlines():
                            if line.startswith("data: "):
                                data_str = line[6:].strip()
                                if data_str and data_str != "[DONE]":
                                    data_json = json.loads(data_str)
                                    choices = data_json.get("choices")
                                    if choices and isinstance(choices, list):
                                        delta = choices[0].get("delta")
                                        if delta and "content" in delta:
                                            assistant_reply.append(delta["content"])
                                        elif "message" in choices[0]:
                                            content = choices[0]["message"].get("content")
                                            if content:
                                                assistant_reply.append(content)
                    except Exception:
                        pass
                if assistant_reply:
                    reply_str = "".join(assistant_reply)
                    save_history = list(messages)
                    save_history.append({"role": "assistant", "content": reply_str})
                    await _save_gateway_session(session_id, save_history)
            _res_stream.body_iterator = _session_saving_iterator()
        return _res_stream

    refined = None
    if not _incoming_turn:
        await _vram_checkpoint()
        refined = await refine_intent(last_user_text, messages)
        if refined and refined.get("refined_text"):
            _refined_q = str(refined["refined_text"]).strip()
            if _refined_q:
                last_user_text = _refined_q
                for _i in range(len(messages) - 1, -1, -1):
                    _m = messages[_i]
                    if isinstance(_m, dict) and _m.get("role") == "user" \
                            and isinstance(_m.get("content"), str):
                        messages[_i] = {**_m, "content": _refined_q}
                        break
    try:
        _turn_volatile_var.set(bool(refined and (
            refined.get("local_state") or refined.get("news")
            or refined.get("needs_location"))))
    except Exception:  # noqa: BLE001
        pass
    try:
        _turn_priority = float(_sched_priority(refined).get("score", 5.0))
    except Exception:  # noqa: BLE001
        _turn_priority = 5.0
    if _autonomous:
        _turn_priority = min(_turn_priority, AUTONOMOUS_PRIORITY)
        log.info("autonomous turn priority clamped to %.2f (foreground preempts)",
                 _turn_priority)

    try:
        await mios_preempt.turn_boundary(task_id=str(chat_id), priority=_turn_priority)
    except Exception:  # noqa: BLE001 -- degrade-open: a seam failure never breaks a turn
        pass

    if mios_preempt.QUEUE_ENABLE:
        try:
            mios_preempt._TURN_QUEUE.enqueue(str(chat_id), _turn_priority)
            mios_preempt._TURN_QUEUE.dispatch()
        except Exception:  # noqa: BLE001 -- degrade-open: the queue never breaks a turn
            pass

    _budget_turn_token = (chat_id if (_autonomous and _autonomous_source) else None)
    _budget_ok, _budget_reason = await _budget_admit(
        _scratchpad_key(body, chat_id), _autonomous_source, _budget_turn_token)
    if not _budget_ok:
        log.warning("budget HARD-HALT: %s (chat=%s autonomous=%s)",
                    _budget_reason, chat_id, _autonomous)
        _halt_msg = (
            "I'm pausing here: this conversation has reached its aggregate "
            "compute budget for now (" + _budget_reason + "). Please try again "
            "shortly, or rephrase as a single focused request.")
        return JSONResponse(content={
            "id": chat_id, "object": "chat.completion",
            "created": int(time.time()), "model": model,
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": _halt_msg},
                         "finish_reason": "stop"}],
        })

    if _KERNEL:
        _kdec = _KERNEL.router.route(refined)
        log.info("kernel dispatch: routing mode=%s", _kdec.mode)
        ctx = locals().copy()
        try:
            res = await _KERNEL.dispatcher.run(_kdec, **ctx)
            if session_replay_opt_in and session_id and isinstance(res, JSONResponse):
                try:
                    res_body = json.loads(res.body.decode("utf-8"))
                    reply_content = res_body["choices"][0]["message"]["content"]
                    save_history = list(messages)
                    save_history.append({"role": "assistant", "content": reply_content})
                    await _save_gateway_session(session_id, save_history)
                except Exception as e:
                    log.warning("failed to save JSON response to session %s: %s", session_id, e)
            return res
        finally:
            await _budget_release_inflight(_budget_turn_token)
    raise RuntimeError("Kernel not configured")

async def responses_api_logic(request: Request) -> Any:
    try:
        body_bytes = await request.body()
        if not body_bytes:
            body = {}
        else:
            raw_text = body_bytes.decode("utf-8", errors="replace")
            body = _loads_lenient(raw_text)
            if body is None or not isinstance(body, dict):
                raise ValueError("invalid JSON body")
    except Exception:
        return JSONResponse(content={"error": {"message": "invalid JSON body",
                            "type": "invalid_request_error"}}, status_code=400)

    model = body.get("model") or BACKEND_MODEL
    inp = body.get("input")
    msgs: list = []
    if body.get("instructions"):
        msgs.append({"role": "system", "content": str(body["instructions"])})

    if isinstance(inp, str):
        if inp.strip():
            msgs.append({"role": "user", "content": inp})
    elif isinstance(inp, list):
        for it in inp:
            if not isinstance(it, dict):
                continue
            itype = it.get("type")
            if itype == "function_call":
                cid = it.get("call_id") or it.get("id") or f"call_{uuid.uuid4().hex[:12]}"
                fn_name = it.get("name") or ""
                fn_args = it.get("arguments")
                if not isinstance(fn_args, str):
                    fn_args = json.dumps(fn_args or {})
                msgs.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": cid,
                        "type": "function",
                        "function": {"name": fn_name, "arguments": fn_args}
                    }]
                })
            elif itype == "function_call_output":
                cid = it.get("call_id") or it.get("id") or ""
                out = it.get("output", "")
                if not isinstance(out, str):
                    out = json.dumps(out)
                msgs.append({"role": "tool", "tool_call_id": cid, "content": out})
            elif itype == "custom_tool_call":
                cid = it.get("call_id") or it.get("id") or f"call_{uuid.uuid4().hex[:12]}"
                fn_name = it.get("name") or ""
                raw_args = it.get("arguments") if it.get("arguments") is not None else it.get("input")
                fn_args = raw_args if isinstance(raw_args, str) else json.dumps(raw_args or {})
                msgs.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": cid,
                        "type": "function",
                        "function": {"name": fn_name, "arguments": fn_args}
                    }]
                })
            elif itype == "custom_tool_call_output":
                cid = it.get("call_id") or it.get("id") or ""
                out = it.get("output", "")
                if not isinstance(out, str):
                    out = json.dumps(out)
                msgs.append({"role": "tool", "tool_call_id": cid, "content": out})
            else:
                role = it.get("role") or "user"
                c = it.get("content")
                if isinstance(c, list):
                    c = "".join(p.get("text", "") for p in c if isinstance(p, dict)
                                and p.get("type") in ("input_text", "output_text", "text"))
                elif not isinstance(c, str):
                    c = str(c or "")
                msgs.append({"role": role, "content": c})

    if not msgs:
        return JSONResponse(content={"error": {"message": "you must provide 'input'",
            "type": "invalid_request_error", "param": "input", "code": None}},
            status_code=400)

    translated_tools = []
    for t in (body.get("tools") or []):
        if not isinstance(t, dict):
            continue
        if t.get("type") == "function":
            if "function" in t and isinstance(t["function"], dict):
                translated_tools.append(t)
            else:
                translated_tools.append({
                    "type": "function",
                    "function": {
                        "name": t.get("name") or "",
                        "description": t.get("description") or "",
                        "parameters": t.get("parameters") or {},
                    }
                })
        else:
            translated_tools.append(t)

    cc_payload: dict = {"model": model, "messages": msgs, "stream": False}
    if translated_tools:
        cc_payload["tools"] = translated_tools
    if body.get("tool_choice"):
        cc_payload["tool_choice"] = body["tool_choice"]

    streaming = bool(body.get("stream", False))
    _port = os.environ.get("MIOS_PORTS_AGENT_PIPE", "8700")

    if not streaming:
        try:
            async with httpx.AsyncClient(timeout=300.0) as s:
                r = await s.post(f"http://127.0.0.1:{_port}/v1/chat/completions",
                                 json=cc_payload,
                                 headers={"Content-Type": "application/json"})
            if getattr(r, "status_code", 200) >= 400:
                try:
                    err_body = r.json()
                except Exception:
                    err_body = {"error": {"message": getattr(r, "text", "") or f"Upstream error {r.status_code}", "type": "api_error"}}
                return JSONResponse(status_code=r.status_code, content=err_body)
            cc = r.json()
        except Exception:  # noqa: BLE001
            log.exception("responses relay backend failed")
            return JSONResponse(content={"error": {"message": "The response backend is unavailable",
                                "type": "api_error"}}, status_code=502)

        msg = (((cc.get("choices") or [{}])[0].get("message") or {}))
        answer = msg.get("content") or ""
        tool_calls = msg.get("tool_calls") or []
        output_items = []
        if answer or not tool_calls:
            output_items.append({
                "type": "message",
                "id": "msg_" + uuid.uuid4().hex[:24],
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": answer}],
            })
        for tc in tool_calls:
            output_items.append({
                "type": "function_call",
                "id": "item_" + uuid.uuid4().hex[:24],
                "call_id": tc.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                "name": (tc.get("function") or {}).get("name") or "",
                "arguments": (tc.get("function") or {}).get("arguments") or "{}",
                "status": "completed",
            })

        return JSONResponse(content={
            "id": "resp_" + uuid.uuid4().hex[:24],
            "object": "response",
            "created_at": int(time.time()),
            "model": model,
            "status": "completed",
            "output": output_items,
            "output_text": answer,
            "usage": _normalize_usage(cc.get("usage") or _usage_estimate("", answer)),
        })

    async def _stream_responses_events() -> AsyncGenerator[bytes, None]:
        resp_id = "resp_" + uuid.uuid4().hex[:24]
        created_at = int(time.time())

        # 1. response.created
        yield f"event: response.created\ndata: {json.dumps({'response': {'id': resp_id, 'object': 'response', 'created_at': created_at, 'model': model, 'status': 'in_progress', 'output': [], 'usage': None}})}\n\n".encode("utf-8")

        # 2. response.in_progress
        yield f"event: response.in_progress\ndata: {json.dumps({'response': {'id': resp_id, 'object': 'response', 'created_at': created_at, 'model': model, 'status': 'in_progress', 'output': [], 'usage': None}})}\n\n".encode("utf-8")

        try:
            async with httpx.AsyncClient(timeout=300.0) as s:
                r = await s.post(f"http://127.0.0.1:{_port}/v1/chat/completions",
                                 json=cc_payload,
                                 headers={"Content-Type": "application/json"})
            if getattr(r, "status_code", 200) >= 400:
                try:
                    err_body = r.json()
                except Exception:
                    err_body = {"error": {"message": getattr(r, "text", "") or f"Upstream error {r.status_code}", "type": "api_error"}}
                yield f"event: error\ndata: {json.dumps(err_body)}\n\n".encode("utf-8")
                return
            cc = r.json()
        except Exception:  # noqa: BLE001
            log.exception("responses relay backend failed in stream")
            yield f"event: error\ndata: {json.dumps({'error': {'message': 'The response backend is unavailable', 'type': 'api_error'}})}\n\n".encode("utf-8")
            return

        msg = (((cc.get("choices") or [{}])[0].get("message") or {}))
        answer = msg.get("content") or ""
        tool_calls = msg.get("tool_calls") or []
        output_items = []
        output_idx = 0

        if answer or not tool_calls:
            msg_id = "msg_" + uuid.uuid4().hex[:24]
            # 3. response.output_item.added
            yield f"event: response.output_item.added\ndata: {json.dumps({'output_index': output_idx, 'item': {'id': msg_id, 'type': 'message', 'status': 'in_progress', 'role': 'assistant', 'content': []}})}\n\n".encode("utf-8")
            # 4. response.content_part.added
            yield f"event: response.content_part.added\ndata: {json.dumps({'output_index': output_idx, 'content_index': 0, 'part': {'type': 'output_text', 'text': ''}})}\n\n".encode("utf-8")

            if answer:
                chunk_size = 16
                for i in range(0, len(answer), chunk_size):
                    delta = answer[i:i + chunk_size]
                    # 5. response.output_text.delta
                    yield f"event: response.output_text.delta\ndata: {json.dumps({'output_index': output_idx, 'content_index': 0, 'delta': delta})}\n\n".encode("utf-8")

            # 6. response.output_text.done
            yield f"event: response.output_text.done\ndata: {json.dumps({'output_index': output_idx, 'content_index': 0, 'text': answer})}\n\n".encode("utf-8")

            # 7. response.output_item.done
            item_obj = {
                "id": msg_id, "type": "message", "status": "completed",
                "role": "assistant", "content": [{"type": "output_text", "text": answer}]
            }
            yield f"event: response.output_item.done\ndata: {json.dumps({'output_index': output_idx, 'item': item_obj})}\n\n".encode("utf-8")
            output_items.append(item_obj)
            output_idx += 1

        for tc in tool_calls:
            item_id = "item_" + uuid.uuid4().hex[:24]
            call_id = tc.get("id") or f"call_{uuid.uuid4().hex[:12]}"
            fn_name = (tc.get("function") or {}).get("name") or ""
            fn_args = (tc.get("function") or {}).get("arguments") or "{}"
            if not isinstance(fn_args, str):
                fn_args = json.dumps(fn_args)

            # response.output_item.added
            yield f"event: response.output_item.added\ndata: {json.dumps({'output_index': output_idx, 'item': {'id': item_id, 'type': 'function_call', 'status': 'in_progress', 'call_id': call_id, 'name': fn_name, 'arguments': ''}})}\n\n".encode("utf-8")

            # response.function_call_arguments.delta
            yield f"event: response.function_call_arguments.delta\ndata: {json.dumps({'output_index': output_idx, 'call_id': call_id, 'delta': fn_args})}\n\n".encode("utf-8")

            # response.function_call_arguments.done
            yield f"event: response.function_call_arguments.done\ndata: {json.dumps({'output_index': output_idx, 'call_id': call_id, 'arguments': fn_args})}\n\n".encode("utf-8")

            # response.output_item.done
            item_obj = {
                "id": item_id, "type": "function_call", "status": "completed",
                "call_id": call_id, "name": fn_name, "arguments": fn_args
            }
            yield f"event: response.output_item.done\ndata: {json.dumps({'output_index': output_idx, 'item': item_obj})}\n\n".encode("utf-8")
            output_items.append(item_obj)
            output_idx += 1

        # 8. response.completed
        usage = _normalize_usage(cc.get("usage") or _usage_estimate("", answer))
        completed_resp = {
            "id": resp_id, "object": "response", "created_at": created_at,
            "model": model, "status": "completed", "output": output_items,
            "output_text": answer, "usage": usage
        }
        yield f"event: response.completed\ndata: {json.dumps({'response': completed_resp})}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"

    return StreamingResponse(_stream_responses_events(), media_type="text/event-stream")

chat_router = APIRouter()

@chat_router.post("/v1/responses")
async def responses_api(request: Request) -> Any:
    """OpenAI Responses API (Tier-2, additive) route. Calls responses_api_logic
    (same module)."""
    return await responses_api_logic(request)

@chat_router.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    """OpenAI Chat Completions route -- the capstone router-brain entrypoint. The
    router-brain body lives in ``chat_completions_logic`` (same module); this thin
    handler calls it directly. Every routing-precedence guard/branch lives in the
    logic function, behaviour-identical.
    """
    return await chat_completions_logic(request)

async def _kernel_chat_handler(decision, **ctx):
    import re, time
    from fastapi.responses import JSONResponse, StreamingResponse
    refined = ctx.get("refined") or {}
    _force_council = ctx.get("_force_council")
    _force_delegate = ctx.get("_force_delegate")
    last_user_text = ctx.get("last_user_text")
    messages = ctx.get("messages")
    streaming = ctx.get("streaming")
    chat_id = ctx.get("chat_id")
    model = ctx.get("model")
    session_id = ctx.get("session_id")
    _routed_domain_var = ctx.get("_routed_domain_var") or globals().get("_routed_domain_var")

    _chat_reply = ""
    if not _force_council and not _force_delegate:
        _mem_block = await _recall_agent_memory(last_user_text)
        _mem_scores = [float(_s) for _s in re.findall(r"\[(\d(?:\.\d+)?)\]", _mem_block or "")]
        _mem_top = max(_mem_scores) if _mem_scores else 0.0
        if _mem_block and _mem_block.strip() and _mem_top >= 0.6:
            _kept = []
            for _ln in _mem_block.splitlines():
                _ms = re.search(r"\[(\d(?:\.\d+)?)\]", _ln)
                if _ms and float(_ms.group(1)) >= 0.6:
                    _kept.append(_ln)
            _facts = re.sub(r"\[\d(?:\.\d+)?\]\s*", "", "\n".join(_kept))
            _seen_f = set()
            _flines = []
            for _l in _facts.splitlines():
                _l = _l.rstrip()
                _key = _l.strip().lower().lstrip("- ")
                if _l.strip() and _key not in _seen_f:
                    _seen_f.add(_key)
                    _flines.append(_l)
            _facts = "\n".join(_flines)
            if _facts.strip():
                if await _is_memory_question(last_user_text, _facts):
                    _chat_reply = "From what you've told me before:\n" + _facts
                    log.info("memory-recall short-circuit: surfaced saved facts deterministically")

    if (not _chat_reply and len((last_user_text or "").split()) < SWARM_DECOMPOSE_MIN_WORDS
            and not (refined.get("web") or refined.get("news")
                     or refined.get("domain_type") in ("external", "both")
                     or _routed_domain_var.get(None) == "web")
            and not _force_council and not _force_delegate):
        _chat_reply = str(refined.get("reply") or "").strip()
        if not _chat_reply:
            _chat_reply = await _quick_chat_reply(last_user_text, messages)
        if _chat_reply and _ANTIFAB_ENABLE and _contains_tool_result_block(_chat_reply):
            log.warning("anti-fab: chat reply narrated an unexecuted tool result; routing to real executor instead of shipping it")
            _chat_reply = ""

    if _chat_reply:
        reply = _chat_reply
        log.info("refine short-circuit: chat reply (no router/backend)")
        _store_knowledge(query=last_user_text, answer=reply, session_id=session_id, tool_history=[])
        _write_skill_md_fire(query=last_user_text, answer=reply, tool_history=[], session_id=session_id)
        if streaming:
            _dedup_refine = os.environ.get(
                "MIOS_DEDUP_REFINE_STATUS", "true").strip().lower() not in {"0", "false", "no", "off"}
            async def _stream_refine_chat():
                if not _dedup_refine:
                    yield _sse_status_phase(chat_id=chat_id, model=model, phase="prompt")
                    yield _sse_status_phase(chat_id=chat_id, model=model, phase="refine")
                yield _sse_chunk("", chat_id=chat_id, model=model, role="assistant")
                async for _b in _stream_answer(reply, chat_id=chat_id, model=model):
                    yield _b
                yield _sse_status_phase(chat_id=chat_id, model=model, phase="chat_done", done=True)
                yield _sse_chunk("", chat_id=chat_id, model=model, finish_reason="stop")
                yield _sse_done()
            return StreamingResponse(_stream_refine_chat(), media_type="text/event-stream")
        return JSONResponse(content={
            "id": chat_id, "object": "chat.completion", "created": int(time.time()),
            "model": model, "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
        })

    decision.mode = "agent"
    return await _KERNEL.dispatcher.run(decision, **ctx)

_ANTIFAB_ENABLE = os.environ.get("MIOS_VERITY_ANTIFAB_ENABLE", "true").lower() not in {"false", "0", "no", "off"}

def _contains_tool_result_block(text) -> bool:
    """True when `text` narrates a tool EXECUTION result (real-emitter sentinel or a
    success-claim JSON). Used to reject fabricated executions before they stream."""
    if not text:
        return False
    import re
    if re.search(r"🤝\s+\S+\s+output\s*:", text):
        return True
    if re.search(r'"success"\s*:\s*true[^{}]{0,400}?"tool"\s*:\s*"[^"]+"', text, re.DOTALL):
        return True
    if re.search(r'"tool"\s*:\s*"[^"]+"[^{}]{0,400}?"success"\s*:\s*true', text, re.DOTALL):
        return True
    return False

async def _kernel_dispatch_handler(decision, **ctx):
    refined = ctx.get("refined") or {}
    _force_council = ctx.get("_force_council")
    _force_delegate = ctx.get("_force_delegate")
    last_user_text = ctx.get("last_user_text")
    streaming = ctx.get("streaming")
    chat_id = ctx.get("chat_id")
    model = ctx.get("model")
    session_id = ctx.get("session_id")
    _persona_system = ctx.get("_persona_system")

    _os_tool = str(refined.get("tool") or "").strip()
    _os_args = refined.get("args") if isinstance(refined.get("args"), dict) else {}
    if _os_tool in _FASTPATH_VERBS and not _force_council and not _force_delegate:
        log.info("fast-path dispatch: %s args=%s (deterministic, no fan-out)", _os_tool, _os_args)
        return await _respond_os_control(
            _os_tool, _os_args, refined,
            streaming=streaming, chat_id=chat_id, model=model,
            session_id=session_id, last_user_text=last_user_text,
            persona_system=_persona_system)

    log.info("refine dispatch tool=%r not a fast-path verb -> agent", _os_tool)
    decision.mode = "agent"
    return await _KERNEL.dispatcher.run(decision, **ctx)

async def _kernel_multi_task_handler(decision, **ctx):
    refined = ctx.get("refined") or {}
    last_user_text = ctx.get("last_user_text")
    messages = ctx.get("messages")
    streaming = ctx.get("streaming")
    chat_id = ctx.get("chat_id")
    model = ctx.get("model")
    session_id = ctx.get("session_id")
    _persona_system = ctx.get("_persona_system")
    _force_council = ctx.get("_force_council")
    _force_delegate = ctx.get("_force_delegate")
    request = ctx.get("request")

    if (refined and refined.get("intent") == "multi_task"
            and isinstance(refined.get("tasks"), list)
            and len(refined["tasks"]) >= 2):
        queued = _shadow_queue_tasks(refined["tasks"], session_id)
        if queued:
            log.info(
                "multi_task: queued=%d active=%r others=%r",
                len(queued),
                queued[0].get("title", ""),
                [t.get("title", "") for t in queued[1:]],
            )
            if PLANNER_ENABLED:
                try:
                    _vdag = await decompose_intent(last_user_text)
                except Exception:  # noqa: BLE001
                    _vdag = None
                _vnodes = (_vdag.get("nodes") or []) if _vdag else []
                _has_act = any(
                    str((_VERB_CATALOG.get(str(n.get("tool"))) or {})
                        .get("permission", "")).lower() == "write"
                    for n in _vnodes)
                if _has_act and len(_vnodes) >= 2:
                    log.info("multi_task -> verb-DAG (action fires): %s",
                             [n.get("tool") or ("agent:" + str(n.get("agent")))
                              for n in _vnodes])
                    return await _respond_agent_dag(
                        _vdag, refined, request=request, streaming=streaming, chat_id=chat_id,
                        model=model, session_id=session_id,
                        last_user_text=last_user_text,
                        persona_system=_persona_system)

            if PLANNER_ENABLED:
                _adag = _agent_dag_from_tasks(queued)
                if len(_adag.get("nodes") or []) >= 2:
                    log.info("multi_task -> concurrent agent DAG (%d): %s",
                             len(_adag["nodes"]),
                             [n["agent"] for n in _adag["nodes"]])
                    return await _respond_agent_dag(
                        _adag, refined, request=request, streaming=streaming, chat_id=chat_id,
                        model=model, session_id=session_id,
                        last_user_text=last_user_text,
                        persona_system=_persona_system)

            active = queued[0]
            ctx["last_user_text"] = str(active.get("refined_text")
                                 or active.get("title", "")
                                 or last_user_text)
            for k in ("refined_text", "intended_outcome",
                      "target_agent", "hint_tools", "hint_skills"):
                if active.get(k) is not None:
                    refined[k] = active[k]
            refined["intent"] = "agent"
            refined["_multi_task_queue"] = queued
            refined["_multi_task_active_idx"] = 0
            decision.mode = "agent"
            return await _KERNEL.dispatcher.run(decision, **ctx)

    _swarm_nodes = await _plan_swarm(last_user_text, messages)
    if not _swarm_nodes:
        log.info("multi_task decomposition yielded 0 nodes -> falling back to full council")
        ctx["_force_council"] = True
        decision.mode = "agent"
        return await _KERNEL.dispatcher.run(decision, **ctx)

    if not SWARM_TRUST_ATOMIC and len(_swarm_nodes) == 1:
        log.info("multi_task yielded 1 node (trust_atomic=false) -> running as normal agent turn")
        refined["intent"] = "agent"
        refined["deep"] = True
        decision.mode = "agent"
        return await _KERNEL.dispatcher.run(decision, **ctx)

    _dag = _agent_dag_from_tasks(_swarm_nodes, last_user_text)
    return await _respond_agent_dag(
        _dag, refined, request=request, streaming=streaming, chat_id=chat_id, model=model,
        session_id=session_id, original_query=last_user_text,
        persona_system=_persona_system)

async def _kernel_agent_handler(decision, **ctx):
    refined = ctx.get("refined") or {}
    _force_council = ctx.get("_force_council")
    _force_delegate = ctx.get("_force_delegate")
    last_user_text = ctx.get("last_user_text")
    messages = ctx.get("messages")
    streaming = ctx.get("streaming")
    chat_id = ctx.get("chat_id")
    model = ctx.get("model")
    session_id = ctx.get("session_id")
    _routed_domain_var = ctx.get("_routed_domain_var") or globals().get("_routed_domain_var")
    _persona_system = ctx.get("_persona_system")
    _mflags = ctx.get("_mflags")
    reply_prefix = ctx.get("reply_prefix", "")

    _domain = _routed_domain_var.get(None)
    if (not refined.get("web") and not refined.get("news")
            and _domain != "web"
            and "local_state" in str(refined.get("domain", ""))
            and not _force_council and not _force_delegate):
        if await _needs_external_knowledge(last_user_text, messages):
            log.info("hybrid promotion: local_state query needs external facts -> web=True")
            refined["web"] = True
            refined["domain_type"] = "both"
            _routed_domain_var.set("both")

    if (LOCAL_STATE_FASTPATH and "local_state" in str(refined.get("domain", ""))
            and not refined.get("web") and not refined.get("news")
            and not _force_council and not _force_delegate):
        log.info("local-state fast-path: routing %r to system_status/local scripts", last_user_text)
        return await _respond_local_state(
            refined, streaming=streaming, chat_id=chat_id, model=model,
            session_id=session_id, last_user_text=last_user_text,
            persona_system=_persona_system)

    if not _force_council and not _force_delegate and _mflags.get("force_council") is None:
        _agent_match = _pick_agent(last_user_text)
        if _agent_match and _agent_match != "MiOS":
            log.info("legacy explicit route: %r matched -> forcing delegate to %s",
                     last_user_text, _agent_match)
            _force_delegate = _agent_match

    if not _force_council and _force_delegate:
        if _force_delegate == "MiOS":
            _force_delegate = ""
        else:
            log.info("force_delegate: skipping council, 100%% weight to %s", _force_delegate)

    _selected = [_force_delegate] if _force_delegate else []
    if _force_council or (not _force_delegate and (
            refined.get("deep") or _routed_domain_var.get(None) in ("web", "local_files")
            or refined.get("web") or refined.get("news"))):
        _selected = await _pick_fanout_agents(last_user_text, refined, _force_council)
        if len(_selected) > 1:
            log.info("council fan-out (%d): %s", len(_selected), _selected)
        elif len(_selected) == 1:
            log.info("council fan-out yielded 1 agent -> delegating to %s", _selected[0])
            _force_delegate = _selected[0]

    return await _respond_native_loop_direct(
        refined, force_delegate=_force_delegate, target_agents=_selected,
        streaming=streaming, chat_id=chat_id, model=model,
        session_id=session_id, last_user_text=last_user_text,
        persona_system=_persona_system, reply_prefix=reply_prefix,
        messages=messages)
