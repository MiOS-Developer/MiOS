# AI-hint: Shared sub-agent COMPLETION-call primitive extracted verbatim from server.py (refactor R3 dispatch-substrate wave).
# AI-doc: usr/share/doc/mios/manual/routing.md

from __future__ import annotations

import asyncio
import contextlib
import httpx
import json
import logging
import time
from typing import Optional

from mios_config import _AUTH_HOSTPORTS, _DISPATCH_TOML, _toml_section
import os
KV_SLOTS_DIR = (os.environ.get("MIOS_KV_SLOTS_DIR", "")
                or str(_DISPATCH_TOML.get("kv_slots_dir", "") or "")).strip()
from mios_endpoints import _endpoint_is_llamacpp
from mios_jsonsalvage import loads_lenient as _loads_lenient
from mios_kvfork import (validate_fork as _kvfork_validate,
                         plan_fork as _kvfork_plan,
                         fork_outcome as _kvfork_outcome,
                         kv_filename as _kvfork_kv_filename)
import mios_preempt
import mios_tokenize

log = logging.getLogger("mios-agent-pipe")

HEALTHGATE_CONNECT_TIMEOUT = 6.0
HEALTHGATE_READ_TIMEOUT = 120.0
SECONDARY_TOOL_LOOP = True
KV_FORK_ENABLE = False
_SRC_TURN_HEADER = "X-MiOS-Turn"
KV_PAGING_ENABLE = False
KV_PAGING_SLOT = 0
KV_PAGING_TIMEOUT = 12.0
KV_SLOT_PERSIST = True
RR_ENABLE = False
PRIORITY_QUEUE_ENABLE = False
RR_SLICE_TOKENS = 512
RR_SLICE_TIMEOUT = 120.0
RR_QUANTUM_S = 8.0
LLM_NUM_PREDICT_CAP = 2048
LLM_NUM_PREDICT_CAP_CPU = 512

_AGENT_REGISTRY: dict = {}

import collections
import contextvars
_autonomous_var: contextvars.ContextVar[bool] = contextvars.ContextVar("autonomous", default=False)
_autonomous_source_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("autonomous_source", default=None)
_dispatch_depth_var: contextvars.ContextVar[int] = contextvars.ContextVar("dispatch_depth", default=0)

_IN_FLIGHT_PROMPTS: dict[str, asyncio.Future] = {}
_IN_FLIGHT_LOCK = asyncio.Lock()

_SESSION_TOKENS: dict[str, collections.deque] = {}
_AUTONOMOUS_SOURCE_TOKENS: dict[str, collections.deque] = {}
_BUDGET_LOCK = asyncio.Lock()

def _get_window_total(ledger: dict, key: str, now: float, window_s: float = 3600.0) -> int:
    dq = ledger.get(key)
    if not dq:
        return 0
    if isinstance(dq, int):
        return dq
    if not isinstance(dq, collections.deque):
        return 0
    cutoff = now - window_s
    while dq and dq[0][0] < cutoff:
        dq.popleft()
    return sum(t for _ts, t in dq)

def _add_to_window(ledger: dict, key: str, tokens: int, now: float) -> None:
    if key not in ledger:
        ledger[key] = collections.deque()
    ledger[key].append((now, tokens))

_KV_LOCKS: dict = {}
_KV_RESIDENT: dict = {}
_BACKEND_KEY = ""
_GLOBAL_PRIORITY_GATE = None
_PREEMPT = None
_NODE_LIVE: dict = {}

_SloShed = None
_admit = None
_agent_binding = None
_agent_offload_engine = None
_apply_outbound_auth = None
_conv_key_var = None
_current_trace_id = None
_dispatch_agent_var = None
_dispatch_priority = None
_endpoint_sem = None
_harvest_sub_sources = None
_hop_via_headers = None
_kv_fork_parent_var = None
_lane_sem = None
_lane_sem_key = None
_model_active = None
_is_slow_lane_ep = None
_opt_int_mb = None
_priority_gate = None
_should_health_probe = None
_src_turn_key = None
_strip_agent_chrome = None
_strip_think_tags = None
_v1_secondary_tool_loop = None

COST_ACCOUNTING_ENABLE = False
_COST_LEDGER = None
_COST_MODEL = None
_is_remote_endpoint = None

_otel_tracer = None

def configure(*, healthgate_connect_timeout=None, healthgate_read_timeout=None,
              secondary_tool_loop=None, kv_fork_enable=None, src_turn_header=None,
              agent_registry=None, sloshed=None, admit=None, agent_binding=None,
              agent_offload_engine=None, apply_outbound_auth=None,
              conv_key_var=None, current_trace_id=None, dispatch_agent_var=None,
              dispatch_priority=None, endpoint_sem=None, harvest_sub_sources=None,
              hop_via_headers=None, kv_fork_parent_var=None,
              lane_sem=None, lane_sem_key=None, model_active=None,
              opt_int_mb=None, priority_gate=None,
              cost_accounting_enable=None, cost_ledger=None, cost_model=None,
              is_remote_endpoint=None, is_slow_lane_ep=None, node_live=None,
              llm_num_predict_cap=None, llm_num_predict_cap_cpu=None,
              should_health_probe=None,
              src_turn_key=None, strip_agent_chrome=None, strip_think_tags=None,
              v1_secondary_tool_loop=None,
              kv_paging_enable=None, kv_paging_slot=None, kv_paging_timeout=None,
              kv_slot_persist=None,
              rr_enable=None, priority_queue_enable=None, rr_slice_tokens=None,
              rr_slice_timeout=None, rr_quantum_s=None, kv_locks=None,
              kv_resident=None, backend_key=None, global_priority_gate=None,
              preempt=None, otel_tracer=None) -> None:
    """Inject server.py's config scalars, the agent registry, the lane/admission
    gates, the binding/priority helpers, the secondary tool-loops, the KV helpers
    and the header/trace helpers the two dispatch functions call back into."""
    global HEALTHGATE_CONNECT_TIMEOUT, HEALTHGATE_READ_TIMEOUT
    global SECONDARY_TOOL_LOOP, KV_FORK_ENABLE, _SRC_TURN_HEADER, _AGENT_REGISTRY
    global _SloShed, _admit, _agent_binding, _agent_offload_engine
    global _apply_outbound_auth, _conv_key_var, _current_trace_id
    global _dispatch_agent_var, _dispatch_priority, _endpoint_sem
    global _harvest_sub_sources, _hop_via_headers, _kv_fork_parent_var
    global _lane_sem, _lane_sem_key, _model_active
    global _opt_int_mb
    global _priority_gate, _is_slow_lane_ep, _NODE_LIVE
    global LLM_NUM_PREDICT_CAP, LLM_NUM_PREDICT_CAP_CPU
    global COST_ACCOUNTING_ENABLE, _COST_LEDGER, _COST_MODEL, _is_remote_endpoint
    global _should_health_probe, _src_turn_key, _strip_agent_chrome
    global _strip_think_tags, _v1_secondary_tool_loop
    global KV_PAGING_ENABLE, KV_PAGING_SLOT, KV_PAGING_TIMEOUT, RR_ENABLE
    global PRIORITY_QUEUE_ENABLE, RR_SLICE_TOKENS, RR_SLICE_TIMEOUT, RR_QUANTUM_S
    global _KV_LOCKS, _KV_RESIDENT, _BACKEND_KEY, _GLOBAL_PRIORITY_GATE, _PREEMPT
    global KV_SLOT_PERSIST
    global _otel_tracer
    if otel_tracer is not None:
        _otel_tracer = otel_tracer
    if healthgate_connect_timeout is not None:
        HEALTHGATE_CONNECT_TIMEOUT = healthgate_connect_timeout
    if healthgate_read_timeout is not None:
        HEALTHGATE_READ_TIMEOUT = healthgate_read_timeout
    if secondary_tool_loop is not None:
        SECONDARY_TOOL_LOOP = secondary_tool_loop
    if kv_fork_enable is not None:
        KV_FORK_ENABLE = kv_fork_enable
    if src_turn_header is not None:
        _SRC_TURN_HEADER = src_turn_header
    if agent_registry is not None:
        _AGENT_REGISTRY = agent_registry
    if sloshed is not None:
        _SloShed = sloshed
    if admit is not None:
        _admit = admit
    if agent_binding is not None:
        _agent_binding = agent_binding
    if agent_offload_engine is not None:
        _agent_offload_engine = agent_offload_engine
    if apply_outbound_auth is not None:
        _apply_outbound_auth = apply_outbound_auth
    if conv_key_var is not None:
        _conv_key_var = conv_key_var
    if current_trace_id is not None:
        _current_trace_id = current_trace_id
    if dispatch_agent_var is not None:
        _dispatch_agent_var = dispatch_agent_var
    if dispatch_priority is not None:
        _dispatch_priority = dispatch_priority
    if endpoint_sem is not None:
        _endpoint_sem = endpoint_sem
    if harvest_sub_sources is not None:
        _harvest_sub_sources = harvest_sub_sources
    if hop_via_headers is not None:
        _hop_via_headers = hop_via_headers
    if kv_fork_parent_var is not None:
        _kv_fork_parent_var = kv_fork_parent_var
    if lane_sem is not None:
        _lane_sem = lane_sem
    if lane_sem_key is not None:
        _lane_sem_key = lane_sem_key
    if model_active is not None:
        _model_active = model_active
    if opt_int_mb is not None:
        _opt_int_mb = opt_int_mb
    if priority_gate is not None:
        _priority_gate = priority_gate
    if cost_accounting_enable is not None:
        COST_ACCOUNTING_ENABLE = cost_accounting_enable
    if cost_ledger is not None:
        _COST_LEDGER = cost_ledger
    if cost_model is not None:
        _COST_MODEL = cost_model
    if is_remote_endpoint is not None:
        _is_remote_endpoint = is_remote_endpoint
    if is_slow_lane_ep is not None:
        _is_slow_lane_ep = is_slow_lane_ep
    if node_live is not None:
        _NODE_LIVE = node_live
    if llm_num_predict_cap is not None:
        LLM_NUM_PREDICT_CAP = llm_num_predict_cap
    if llm_num_predict_cap_cpu is not None:
        LLM_NUM_PREDICT_CAP_CPU = llm_num_predict_cap_cpu
    if should_health_probe is not None:
        _should_health_probe = should_health_probe
    if src_turn_key is not None:
        _src_turn_key = src_turn_key
    if strip_agent_chrome is not None:
        _strip_agent_chrome = strip_agent_chrome
    if strip_think_tags is not None:
        _strip_think_tags = strip_think_tags
    if v1_secondary_tool_loop is not None:
        _v1_secondary_tool_loop = v1_secondary_tool_loop
    if kv_paging_enable is not None:
        KV_PAGING_ENABLE = kv_paging_enable
    if kv_paging_slot is not None:
        KV_PAGING_SLOT = kv_paging_slot
    if kv_paging_timeout is not None:
        KV_PAGING_TIMEOUT = kv_paging_timeout
    if kv_slot_persist is not None:
        KV_SLOT_PERSIST = kv_slot_persist
    KV_PAGING_ENABLE = KV_PAGING_ENABLE and KV_SLOT_PERSIST
    if rr_enable is not None:
        RR_ENABLE = rr_enable
    if priority_queue_enable is not None:
        PRIORITY_QUEUE_ENABLE = priority_queue_enable
    if rr_slice_tokens is not None:
        RR_SLICE_TOKENS = rr_slice_tokens
    if rr_slice_timeout is not None:
        RR_SLICE_TIMEOUT = rr_slice_timeout
    if rr_quantum_s is not None:
        RR_QUANTUM_S = rr_quantum_s
    if kv_locks is not None:
        _KV_LOCKS = kv_locks
    if kv_resident is not None:
        _KV_RESIDENT = kv_resident
    if backend_key is not None:
        _BACKEND_KEY = backend_key
    if global_priority_gate is not None:
        _GLOBAL_PRIORITY_GATE = global_priority_gate
    if preempt is not None:
        _PREEMPT = preempt

def _trip_breaker(name: str, cfg: dict) -> None:
    try:
        if name and _should_health_probe(cfg):
            _NODE_LIVE[str(name)] = (time.time(), False)
    except Exception:  # noqa: BLE001
        pass

def _num_predict_cap_for(ep: str) -> int:
    """Token ceiling for THIS dispatch -- the short slow-lane cap on a CPU/iGPU
    endpoint, the full cap otherwise (runaway fix: a slow lane can't be allowed to
    grind a full-length generation for hundreds of seconds of pegged cores)."""
    return LLM_NUM_PREDICT_CAP_CPU if _is_slow_lane_ep(ep) else LLM_NUM_PREDICT_CAP

def _record_cost(cfg: dict, ep: str, t0: float, body: dict, text: str) -> None:
    """WS-RES-GOV observe-only: record one dispatch's energy/$ cost into the
    ledger. No-op unless COST_ACCOUNTING_ENABLE; degrade-open (accounting must
    never break a turn). Token counts come from the tokenizer seam (energy is
    dominated by elapsed x watts; tokens matter only for a remote $/Mtok lane)."""
    if not COST_ACCOUNTING_ENABLE:
        return
    try:
        _msgs = (body or {}).get("messages") or []
        _ptok = mios_tokenize.count_messages(_msgs)
        _ctok = mios_tokenize.count_text(text)
        _COST_LEDGER.record(_COST_MODEL.estimate(
            lane=_lane_sem_key(cfg), elapsed_s=max(0.0, time.time() - t0),
            prompt_tokens=_ptok, completion_tokens=_ctok,
            is_remote=_is_remote_endpoint(ep)))
    except Exception:  # noqa: BLE001
        pass

def _host_threshold_val(key: str, default: Any) -> Any:
    try:
        ai = _toml_section("ai")
        thresholds = ai.get("host_thresholds") or {}
        return thresholds.get(key, default)
    except Exception:
        return default

def _get_gpu_vram_usage() -> float:
    import subprocess
    try:
        p = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,nounits,noheader"],
            capture_output=True, text=True, timeout=3
        )
        if p.returncode == 0 and p.stdout:
            parts = p.stdout.strip().split(",")
            if len(parts) == 2:
                used = float(parts[0].strip())
                total = float(parts[1].strip())
                return (used / total) * 100.0
    except Exception:
        pass
    return 0.0

def _get_cpu_load() -> float:
    try:
        import psutil
        return float(psutil.cpu_percent())
    except Exception:
        pass
    return 0.0

def _get_budget_ceil(key: str, default: int) -> int:
    try:
        budget = _toml_section("budget")
        return int(budget.get(key, default))
    except Exception:
        return default

def _hash_request(ep: str, body: dict) -> str:
    import hashlib
    msgs = body.get("messages") or []
    msgs_str = json.dumps(msgs, sort_keys=True)
    model = body.get("model") or ""
    h = hashlib.sha256()
    h.update(str(ep).encode("utf-8"))
    h.update(str(model).encode("utf-8"))
    h.update(msgs_str.encode("utf-8"))
    return h.hexdigest()

from typing import Any
async def _call_agent_complete(name, cfg, body, headers, client,
                               *, prefer_cpu: bool = True,
                               priority: Optional[float] = None) -> tuple:
    depth = _dispatch_depth_var.get()
    max_depth = _get_budget_ceil("max_dispatch_depth", 5)
    if depth >= max_depth:
        log.warning("Max dispatch depth exceeded (%d >= %d) for agent %s", depth, max_depth, name)
        raise RecursionError(f"Max dispatch depth exceeded ({depth}/{max_depth})")

    depth_token = _dispatch_depth_var.set(depth + 1)

    try:
        session_id = (_conv_key_var.get() if _conv_key_var else "") or ""
        conv_ceil = _get_budget_ceil("conversation_token_ceil", 2000000)
        now = time.monotonic()
        if session_id and conv_ceil > 0:
            async with _BUDGET_LOCK:
                used = _get_window_total(_SESSION_TOKENS, session_id, now)
            if used > conv_ceil:
                log.warning("Conversation token budget exceeded (%d > %d) for session %s. Trimming history.", used, conv_ceil, session_id)
                msgs = body.get("messages")
                if isinstance(msgs, list) and len(msgs) > 5:
                    system_msgs = [m for m in msgs if isinstance(m, dict) and m.get("role") in ("system", "developer")]
                    recent_msgs = [m for m in msgs if isinstance(m, dict) and m.get("role") not in ("system", "developer")][-4:]
                    body["messages"] = system_msgs + recent_msgs

        if _autonomous_var.get():
            auto_ceil = _get_budget_ceil("autonomous_token_ceil", 400000)
            src = _autonomous_source_var.get() or "autonomous"
            if auto_ceil > 0:
                async with _BUDGET_LOCK:
                    used = _get_window_total(_AUTONOMOUS_SOURCE_TOKENS, src, now)
                if used > auto_ceil:
                    log.warning("Autonomous source %s token budget exceeded (%d > %d). Trimming history.", src, used, auto_ceil)
                    msgs = body.get("messages")
                    if isinstance(msgs, list) and len(msgs) > 5:
                        system_msgs = [m for m in msgs if isinstance(m, dict) and m.get("role") in ("system", "developer")]
                        recent_msgs = [m for m in msgs if isinstance(m, dict) and m.get("role") not in ("system", "developer")][-4:]
                        body["messages"] = system_msgs + recent_msgs

        _engine = _agent_offload_engine(cfg) if prefer_cpu else None
        _ep, _adm_model = _agent_binding(cfg, _engine)

        big_model = _host_threshold_val("big_ram_model", "mistral-magistral-small-2509")
        is_heavy = (_adm_model == big_model or (_engine in ("vllm", "sglang") or (isinstance(_ep, str) and f":{os.environ.get('MIOS_PORTS_LLM_HEAVY', '8520')}" in _ep)))
        if is_heavy:
            cpu_load = _get_cpu_load()
            vram_pct = _get_gpu_vram_usage()
            max_cpu = _host_threshold_val("max_cpu_percent", 85.0)
            max_vram = _host_threshold_val("max_vram_percent", 90.0)
            if (cpu_load > max_cpu) or (vram_pct > max_vram):
                log.info("Host pressure exceeded (CPU: %.1f%% > %.1f%% or VRAM: %.1f%% > %.1f%%). Degrading heavy dispatch %s to light lane.",
                         cpu_load, max_cpu, vram_pct, max_vram, name)
                _engine = "cpu"
                _ep, _adm_model = _agent_binding(cfg, _engine)
                light_model = _host_threshold_val("small_ram_model", "granite4.1:8b")
                if not _adm_model:
                    _adm_model = light_model

        _prio = priority if priority is not None else _dispatch_priority(cfg)
        if _autonomous_var.get():
            _prio = -100.0

        req_hash = _hash_request(_ep or "", body)

        is_first = False
        fut = None
        async with _IN_FLIGHT_LOCK:
            if req_hash in _IN_FLIGHT_PROMPTS:
                fut = _IN_FLIGHT_PROMPTS[req_hash]
                log.info("Request dedup: prompt hash %s is already in-flight. Sharing result.", req_hash)
            else:
                fut = asyncio.get_running_loop().create_future()
                _IN_FLIGHT_PROMPTS[req_hash] = fut
                is_first = True

        if not is_first:
            return await fut

        async def _execute_query():
            _est = _opt_int_mb(cfg.get("vram_mb"))   # Phase-1 per-worker VRAM (0 = unknown)
            try:
                await _admit(_ep, _adm_model, _engine or _lane_sem_key(cfg), _prio, _est,
                             foreground=False)
            except _SloShed:  # WS-SCHED-SLO: best_effort shed -> drop this node
                log.info("SLO shed: best_effort fan-out %s dropped under contention", name)
                return name, ""

            if _rr_eligible(body, _ep, cfg, _engine):
                _conv = _conv_key_var.get() or name
                _lane_key = _engine or _lane_sem_key(cfg)
                _t = await _rr_run(client, _ep, _adm_model,
                                   body.get("messages") or [], conv=_conv,
                                   priority=_prio, max_tokens=body.get("max_tokens"),
                                   headers=headers, lane_key=_lane_key, est_vram=_est,
                                   cfg=cfg)
                return name, _strip_agent_chrome(_t)

            async with _priority_gate(_prio):
                async with _endpoint_sem(_ep):
                    async with _lane_sem(_engine or _lane_sem_key(cfg)):
                        await _model_active(_ep, _adm_model, 1, _est)
                        _cost_t0 = time.time()
                        try:
                            _n, _t = await _call_agent_complete_inner(
                                name, cfg, body, headers, client, prefer_cpu=prefer_cpu)
                        finally:
                            await _model_active(_ep, _adm_model, -1, _est)
                        _record_cost(cfg, _ep, _cost_t0, body, _t)   # WS-RES-GOV observe-only
                        return _n, _strip_agent_chrome(_t)

        try:
            res = await _execute_query()

            _n, _t = res
            _msgs = (body or {}).get("messages") or []
            _ptok = mios_tokenize.count_messages(_msgs)
            _ctok = mios_tokenize.count_text(_t)
            tokens_used = _ptok + _ctok

            if session_id:
                async with _BUDGET_LOCK:
                    _add_to_window(_SESSION_TOKENS, session_id, tokens_used, time.monotonic())

            if _autonomous_var.get():
                src = _autonomous_source_var.get() or "autonomous"
                async with _BUDGET_LOCK:
                    _add_to_window(_AUTONOMOUS_SOURCE_TOKENS, src, tokens_used, time.monotonic())

            async with _IN_FLIGHT_LOCK:
                _IN_FLIGHT_PROMPTS.pop(req_hash, None)
                if not fut.done():
                    fut.set_result(res)
            return res
        except Exception as e:
            async with _IN_FLIGHT_LOCK:
                _IN_FLIGHT_PROMPTS.pop(req_hash, None)
                if not fut.done():
                    fut.set_exception(e)
            raise e

    finally:
        _dispatch_depth_var.reset(depth_token)

async def _call_agent_complete_inner(name: str, cfg: dict, body: dict,
                               headers: dict, client,
                               *, prefer_cpu: bool = True,
                               _failover_depth: int = 0) -> tuple:
    if _otel_tracer:
        from opentelemetry.trace import SpanKind
        req_model = body.get("model") or cfg.get("model") or ""
        with _otel_tracer.start_as_current_span(
            "invoke_agent",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.system": "mios",
                "gen_ai.request.model": req_model,
                "session_id": (_conv_key_var.get() if _conv_key_var else "") or "",
            }
        ) as span:
            res_name, res_text = await _call_agent_complete_inner_orig(
                name, cfg, body, headers, client,
                prefer_cpu=prefer_cpu, _failover_depth=_failover_depth
            )
            _eng = _agent_offload_engine(cfg) if prefer_cpu else None
            _, _mdl = _agent_binding(cfg, _eng)
            actual_model = _mdl or cfg.get("model") or ""
            if actual_model:
                span.set_attribute("gen_ai.response.model", actual_model)
            return res_name, res_text
    else:
        return await _call_agent_complete_inner_orig(
            name, cfg, body, headers, client,
            prefer_cpu=prefer_cpu, _failover_depth=_failover_depth
        )

async def _call_agent_complete_inner_orig(name: str, cfg: dict, body: dict,
                               headers: dict, client,
                               *, prefer_cpu: bool = True,
                               _failover_depth: int = 0) -> tuple:
    _dispatch_agent_var.set(name)  # WS-A9: scope the dispatching agent for the PDP gate
    _eng = _agent_offload_engine(cfg) if prefer_cpu else None
    ep, _mdl = _agent_binding(cfg, _eng)
    async def _try_failover(reason: str) -> tuple:
        if _failover_depth >= 3:
            return name, ""
        for fname in (cfg.get("failover_agents") or []):
            fcfg = _AGENT_REGISTRY.get(fname)
            if not isinstance(fcfg, dict):
                continue
            rn, rt = await _call_agent_complete_inner(
                fname, fcfg, body, headers, client,
                prefer_cpu=False,
                _failover_depth=_failover_depth + 1)
            if rt and rt.strip():
                log.info("failover: %s -> %s (%s) ok", name, fname, reason)
                return rn, rt
        return name, ""
    if not ep:
        rn, rt = await _try_failover("no endpoint")
        if rt and rt.strip():
            return rn, rt
        return name, ""
    _to = (httpx.Timeout(connect=HEALTHGATE_CONNECT_TIMEOUT,
                         read=HEALTHGATE_READ_TIMEOUT, write=10.0, pool=10.0)
           if _should_health_probe(cfg) else None)
    try:
        nb = dict(body)
        nb["stream"] = False
        nb.pop("_allow_write", None)
        nb.pop("num_ctx", None)
        if not nb.get("max_tokens"):
            _np = (nb.get("options") or {}).get("num_predict")
            nb["max_tokens"] = int(_np) if _np else _num_predict_cap_for(ep)
        nb.pop("options", None)
        nb.pop("think", None)
        nb.setdefault("chat_template_kwargs", {"enable_thinking": False})
        if _mdl:
            nb["model"] = _mdl
        _hdrs = dict(headers or {})
        _apply_outbound_auth(_hdrs, ep)
        _tk = _src_turn_key()
        if _tk:
            _hdrs[_SRC_TURN_HEADER] = _tk
        _hdrs.update(_hop_via_headers())   # P0 cross-hop recursion bound
        _tid = _current_trace_id()
        if _tid:
            _hdrs["X-MiOS-Trace"] = _tid
        _kv_parent = _kv_fork_parent_var.get() or ""
        if KV_FORK_ENABLE and _kv_parent and _kv_parent != (_conv_key_var.get() or ""):
            _child_conv = f"{_kv_parent}#fork:{name}"
            try:
                if (await _kv_fork(client, ep, cfg, _eng, _kv_parent, _child_conv)).get("forked"):
                    _conv_key_var.set(_child_conv)   # page the forked child slot file
            except Exception:  # noqa: BLE001 -- degrade-open: a fork miss -> cold start
                pass
        async with _kv_paging(client, ep, cfg, _eng):
            if SECONDARY_TOOL_LOOP and body.get("tools"):
                sess_id = (_conv_key_var.get() if _conv_key_var else None) or None
                nb["messages"] = await _v1_secondary_tool_loop(
                    client, ep, nb.get("model") or cfg.get("model"),
                    headers, nb.get("messages") or [], body["tools"], _to,
                    lambda _s: None, session_id=sess_id)
            r = await client.post(
                f"{ep}/chat/completions",
                content=json.dumps(nb).encode("utf-8"), headers=_hdrs,
                timeout=_to)
        if r.status_code != 200:
            rn, rt = await _try_failover(f"http {r.status_code}")
            if rt and rt.strip():
                return rn, rt
            return name, ""
        _rj = r.json()
        ch = (_rj.get("choices") or [])
        msg = (ch[0].get("message") if ch else {}) or {}
        _content = str(msg.get("content") or "")
        try:   # harvest the sub-agent's real sources into THIS (parent) turn
            _harvest_sub_sources(_rj, _content)
        except Exception:  # noqa: BLE001
            pass
        return name, _strip_think_tags(_content)
    except Exception as e:
        log.info("fanout secondary %s failed: %s", name, e)
        _trip_breaker(name, cfg)
        rn, rt = await _try_failover(f"exception {type(e).__name__}")
        if rt and rt.strip():
            return rn, rt
        return name, ""

async def _call_agent_stream_inner(name: str, cfg: dict, body: dict,
                                   headers: dict, client, q,
                                   *, prefer_cpu: bool = True) -> tuple:
    if _otel_tracer:
        from opentelemetry.trace import SpanKind
        req_model = body.get("model") or cfg.get("model") or ""
        with _otel_tracer.start_as_current_span(
            "invoke_agent",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.system": "mios",
                "gen_ai.request.model": req_model,
                "session_id": (_conv_key_var.get() if _conv_key_var else "") or "",
            }
        ) as span:
            res_name, res_text = await _call_agent_stream_inner_orig(
                name, cfg, body, headers, client, q,
                prefer_cpu=prefer_cpu
            )
            _eng = _agent_offload_engine(cfg) if prefer_cpu else None
            _, _mdl = _agent_binding(cfg, _eng)
            actual_model = _mdl or cfg.get("model") or ""
            if actual_model:
                span.set_attribute("gen_ai.response.model", actual_model)
            return res_name, res_text
    else:
        return await _call_agent_stream_inner_orig(
            name, cfg, body, headers, client, q,
            prefer_cpu=prefer_cpu
        )

async def _call_agent_stream_inner_orig(name: str, cfg: dict, body: dict,
                                   headers: dict, client, q,
                                   *, prefer_cpu: bool = True) -> tuple:
    _dispatch_agent_var.set(name)  # WS-A9: scope the dispatching agent for the PDP gate
    _eng = _agent_offload_engine(cfg) if prefer_cpu else None
    ep, _mdl = _agent_binding(cfg, _eng)
    if not ep:
        return name, ""
    _to = (httpx.Timeout(connect=HEALTHGATE_CONNECT_TIMEOUT,
                         read=HEALTHGATE_READ_TIMEOUT, write=10.0, pool=10.0)
           if _should_health_probe(cfg) else None)
    parts: list = []

    def _push(frag: str) -> None:
        if frag and q is not None:
            try:
                q.put_nowait(("SF", name, frag))
            except Exception:
                pass

    try:
        nb = dict(body)
        nb["stream"] = True
        nb.pop("_allow_write", None)
        nb.pop("num_ctx", None)
        if not nb.get("max_tokens"):
            _np = (nb.get("options") or {}).get("num_predict")
            nb["max_tokens"] = int(_np) if _np else _num_predict_cap_for(ep)
        nb.pop("options", None)
        nb.pop("think", None)
        nb.setdefault("chat_template_kwargs", {"enable_thinking": False})
        if _mdl:
            nb["model"] = _mdl
        if SECONDARY_TOOL_LOOP and body.get("tools"):
            sess_id = (_conv_key_var.get() if _conv_key_var else None) or None
            nb["messages"] = await _v1_secondary_tool_loop(
                client, ep, nb.get("model") or cfg.get("model"),
                headers, nb.get("messages") or [], body["tools"], _to, _push,
                session_id=sess_id)
        _hdrs = dict(headers or {})
        _apply_outbound_auth(_hdrs, ep)
        _tk = _src_turn_key()
        if _tk:
            _hdrs[_SRC_TURN_HEADER] = _tk
        _hdrs.update(_hop_via_headers())   # P0 cross-hop recursion bound
        _tid = _current_trace_id()
        if _tid:
            _hdrs["X-MiOS-Trace"] = _tid
        async with client.stream(
                "POST", f"{ep}/chat/completions",
                content=json.dumps(nb).encode("utf-8"), headers=_hdrs,
                timeout=_to) as r:
            if r.status_code != 200:
                return name, ""
            _nonsse: list = []
            async for line in r.aiter_lines():
                if not line:
                    continue
                if not line.startswith("data:"):
                    _nonsse.append(line)        # a non-streaming endpoint's body
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = _loads_lenient(data)
                except (json.JSONDecodeError, ValueError):
                    continue
                ch = chunk.get("choices") or []
                if not ch:
                    continue
                delta = ch[0].get("delta") or {}
                _content = delta.get("content") or ""
                frag = _content or (delta.get("reasoning_content") or delta.get("reasoning") or "")
                if _content:
                    parts.append(_content)
                if frag:
                    _push(frag)
            if not parts and _nonsse:
                try:
                    _obj = _loads_lenient("".join(_nonsse))
                    _m = ((_obj.get("choices") or [{}])[0].get("message") or {})
                    _c = (_m.get("content") or "").strip()
                    if _c:
                        parts.append(_c)
                        _push(_c)
                except Exception:  # noqa: BLE001
                    pass
        return name, _strip_think_tags("".join(parts))
    except Exception as e:
        log.info("fanout secondary %s (stream) failed: %s", name, e)
        return name, ""

def _kv_base(ep: str) -> str:
    """The llama-server root (strip a trailing /v1) where /slots lives."""
    return ep[:-3].rstrip("/") if (ep or "").endswith("/v1") else (ep or "").rstrip("/")

def _kv_filename(conv: str) -> str:
    """A filesystem-safe slot-save filename for one conversation's KV. The file
    lands under the server's --slot-save-path on the llama.cpp host. WS-A4:
    delegates to mios_kvfork.kv_filename so the naming has ONE source (the fork
    child-filename derivation and this paging filename can never diverge)."""
    return _kvfork_kv_filename(conv)

def _kv_lock(key: str) -> "asyncio.Lock":
    lk = _KV_LOCKS.get(key)
    if lk is None:
        lk = asyncio.Lock()
        _KV_LOCKS[key] = lk
    return lk

_SAVED_CONVS: set = set()
_LLM_LIGHT_YAML_CACHE: dict = {}
_ENDPOINT_SLOTS_CACHE: dict = {}

def _stable_hash(s: str) -> int:
    import hashlib
    return int(hashlib.md5(s.encode("utf-8")).hexdigest(), 16)

def _get_llm_light_config() -> dict:
    global _LLM_LIGHT_YAML_CACHE
    if _LLM_LIGHT_YAML_CACHE:
        return _LLM_LIGHT_YAML_CACHE
    yaml_path = os.environ.get("MIOS_LLM_LIGHT_YAML", "/usr/share/mios/llamacpp/mios-llm-light.yaml")
    if not os.path.exists(yaml_path):
        return {}
    try:
        import yaml
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if isinstance(data, dict):
                _LLM_LIGHT_YAML_CACHE = data
                return data
    except Exception as e:
        log.warning("Failed to parse mios-llm-light.yaml: %s", e)
    return {}

def _is_gemma_or_qwen(model: str) -> bool:
    if not model:
        return False
    model_lower = model.lower()
    if "gemma" in model_lower or "qwen" in model_lower:
        return True
    try:
        cfg = _get_llm_light_config()
        models = cfg.get("models", {})
        for key, entry in models.items():
            aliases = [a.lower() for a in entry.get("aliases", [])]
            if key.lower() == model_lower or model_lower in aliases:
                if "gemma" in key.lower() or "qwen" in key.lower():
                    return True
                cmd = str(entry.get("cmd", "")).lower()
                if "gemma" in cmd or "qwen" in cmd:
                    return True
    except Exception as e:
        log.debug("Error in _is_gemma_or_qwen: %s", e)
    return False

async def _get_slot_count(client, ep: str, model: Optional[str] = None) -> int:
    base = _kv_base(ep)
    cache_key = f"{base}#{model or ''}"
    if cache_key in _ENDPOINT_SLOTS_CACHE:
        return _ENDPOINT_SLOTS_CACHE[cache_key]
    urls = []
    if model:
        urls.append(f"{base}/upstream/{model}/slots")
    urls.append(f"{base}/slots")
    for url in urls:
        try:
            r = await client.get(url, timeout=KV_PAGING_TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    n = len(data)
                    if n > 0:
                        _ENDPOINT_SLOTS_CACHE[cache_key] = n
                        return n
        except Exception as e:
            log.debug("Failed to get slots count from %s: %s", url, e)
    return 1

async def _kv_slot_action(client, ep: str, action: str, conv: str,
                          model: "Optional[str]" = None,
                          slot_id: Optional[int] = None) -> bool:
    """POST one llama.cpp slot save|restore for conversation `conv`. Best-effort:
    returns False (never raises) on any failure.

    Passes swa_full=true / --swa-full=true for Gemma/Qwen family models on restore."""
    sid = slot_id if slot_id is not None else KV_PAGING_SLOT
    base = _kv_base(ep)
    urls = []
    if model:
        urls.append(f"{base}/upstream/{model}/slots/{sid}")
    urls.append(f"{base}/slots/{sid}")

    is_swa = (action == "restore" and _is_gemma_or_qwen(model))

    params = {"action": action}
    if is_swa:
        params["swa_full"] = "true"
        params["--swa-full"] = "true"

    body = {"filename": _kv_filename(conv)}
    if is_swa:
        body["swa_full"] = True
        body["--swa-full"] = True

    for url in urls:
        try:
            r = await client.post(
                url,
                params=params,
                content=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                timeout=KV_PAGING_TIMEOUT)
            if r.status_code == 200:
                return True
            log.debug("kv %s %s conv=%s -> %s", action, url, conv, r.status_code)
        except Exception as e:  # noqa: BLE001 -- paging is best-effort
            log.debug("kv %s %s failed: %s", action, url, e)
    return False

@contextlib.asynccontextmanager
async def _kv_paging(client, ep: str, cfg: dict, engine):
    if not (KV_PAGING_ENABLE and ep and _endpoint_is_llamacpp(ep, cfg, engine)):
        yield
        return
    conv = _conv_key_var.get()
    if not conv:
        yield
        return
    model = (cfg or {}).get("model")

    n_slots = await _get_slot_count(client, ep, model)
    slot_id = _stable_hash(conv) % n_slots
    key = f"{_kv_base(ep)}#{slot_id}"

    async with _kv_lock(key):
        resident = _KV_RESIDENT.get(key)
        if resident != conv:
            if resident is not None:                       # page OUT (unload)
                await _kv_slot_action(client, ep, "save", resident, model, slot_id)
                _SAVED_CONVS.add(resident)
                _KV_RESIDENT[key] = None

            has_snapshot = (conv in _SAVED_CONVS)
            if not has_snapshot and KV_SLOTS_DIR:
                import os
                has_snapshot = os.path.exists(os.path.join(KV_SLOTS_DIR, _kv_filename(conv)))

            if has_snapshot:
                await _kv_slot_action(client, ep, "restore", conv, model, slot_id)  # page IN
            _KV_RESIDENT[key] = conv
        try:
            yield
        finally:
            await _kv_slot_action(client, ep, "save", conv, model, slot_id)
            _SAVED_CONVS.add(conv)

async def _kv_fork(client, ep: str, cfg: dict, engine, src_conv: str,
                  dst_conv: str) -> dict:
    if not (KV_FORK_ENABLE and KV_PAGING_ENABLE and ep
            and _endpoint_is_llamacpp(ep, cfg, engine)):
        return {"forked": False, "reason": "kv_fork disabled or endpoint not llama.cpp"}
    ok, reason = _kvfork_validate(src_conv, dst_conv)
    if not ok:
        return {"forked": False, "reason": reason}

    model = (cfg or {}).get("model")
    n_slots = await _get_slot_count(client, ep, model)
    slot_id = _stable_hash(dst_conv) % n_slots
    key = f"{_kv_base(ep)}#{slot_id}"

    async with _kv_lock(key):
        resident = _KV_RESIDENT.get(key)
        if resident is not None and resident != dst_conv:
            await _kv_slot_action(client, ep, "save", resident, model, slot_id)
            _SAVED_CONVS.add(resident)
            _KV_RESIDENT[key] = None

        restore_ok = False
        save_ok = False
        for action, conv, _fname in _kvfork_plan(src_conv, dst_conv):
            res = await _kv_slot_action(client, ep, action, conv, model, slot_id)
            if action == "restore":
                restore_ok = res
            else:
                save_ok = res
        forked, reason = _kvfork_outcome(restore_ok, save_ok)
        if forked:
            _KV_RESIDENT[key] = dst_conv  # the slot now holds the child's KV
            _SAVED_CONVS.add(dst_conv)
    return {"forked": forked, "reason": reason}

def _rr_eligible(body: dict, ep: str, cfg: dict, engine) -> bool:
    """A fan-out dispatch is RR-preemptible only when preemption can both HELP and
    be done safely: RR is on, the priority gate is active (it is what re-orders
    waiters), the lane is a llama.cpp /slots lane (save/restore actually work),
    and this is a PLAIN completion -- no tools[] -> no multi-step tool loop to
    bisect mid-flight (that needs the WS-A11 Context seam)."""
    return bool(RR_ENABLE and PRIORITY_QUEUE_ENABLE
                and not (body or {}).get("tools")
                and (body or {}).get("messages")
                and ep and _endpoint_is_llamacpp(ep, cfg, engine))

async def _rr_slice(client, ep: str, model, messages, max_tokens, headers, slot_id: Optional[int] = None):
    """One bounded completion slice on a llama.cpp /v1 lane. cache_prompt + a
    pinned id_slot reuse the warm (or just-restored) KV so only the new suffix is
    decoded. Returns (text, finished); `finished` is True on a real stop/EOS
    (finish_reason not in {length, ''}) -- else the slice hit the token budget and
    more remains."""
    sid = slot_id if slot_id is not None else KV_PAGING_SLOT
    payload = {"model": model, "messages": messages,
               "max_tokens": int(max_tokens), "stream": False,
               "cache_prompt": True, "id_slot": sid,
               "chat_template_kwargs": {"enable_thinking": False}}
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    if _BACKEND_KEY and ep.split("://")[-1].split("/")[0] in _AUTH_HOSTPORTS:
        for _k in [k for k in hdrs if k.lower() == "authorization"]:
            hdrs.pop(_k)
        hdrs["Authorization"] = f"Bearer {_BACKEND_KEY}"
    r = await client.post(ep.rstrip("/") + "/chat/completions",
                           content=json.dumps(payload).encode("utf-8"),
                           headers=hdrs, timeout=RR_SLICE_TIMEOUT)
    r.raise_for_status()
    j = r.json()
    ch = (j.get("choices") or [{}])[0]
    text = str((ch.get("message") or {}).get("content") or "")
    finished = (ch.get("finish_reason") or "") not in ("length", "")
    return text, finished

async def _rr_run(client, ep: str, model, messages, *, conv: str,
                  priority: float, max_tokens, headers=None,
                  lane_key: str = "", est_vram: int = 0,
                  cfg: Optional[dict] = None) -> str:
    """Interruptible chunked decode (WS-A12). Enforces unified lock order
    _GLOBAL_PRIORITY_GATE -> endpoint semaphore -> lane semaphore.
    Across preemption, releases lane + endpoint semaphores before yielding the
    priority gate, ensuring no dispatch holds an endpoint permit while waiting
    on the gate (T-1190). Returns the full assistant text. Degrade-open: ANY failure
    falls back to one completion of the whole budget; the partial is never lost."""
    held = False
    partial, produced = "", 0
    total = int(max_tokens or RR_SLICE_TOKENS)

    @contextlib.asynccontextmanager
    async def _ep_lane_scope():
        sem = _endpoint_sem(ep) if _endpoint_sem else contextlib.nullcontext()
        lkey = lane_key or (_lane_sem_key(cfg) if (_lane_sem_key and cfg) else "")
        lsem = _lane_sem(lkey) if (_lane_sem and lkey) else contextlib.nullcontext()
        async with sem:
            async with lsem:
                if _model_active and est_vram:
                    try:
                        await _model_active(ep, model, 1, est_vram)
                    except Exception:
                        pass
                try:
                    yield
                finally:
                    if _model_active and est_vram:
                        try:
                            await _model_active(ep, model, -1, est_vram)
                        except Exception:
                            pass

    try:
        n_slots = await _get_slot_count(client, ep, model)
        slot_id = _stable_hash(conv) % n_slots
        if _GLOBAL_PRIORITY_GATE is not None:
            await _GLOBAL_PRIORITY_GATE.acquire(priority)
            held = True
        q = mios_preempt.Quantum(time.monotonic(), RR_QUANTUM_S)
        while produced < total:
            preempted = False
            async with _ep_lane_scope():
                msgs = list(messages)
                if partial:                       # continue the assistant turn
                    msgs.append({"role": "assistant", "content": partial})
                want = min(RR_SLICE_TOKENS, total - produced)
                text, finished = await _rr_slice(client, ep, model, msgs, want, headers, slot_id)
                partial += text
                produced += want
                if finished or not text:
                    break
                try:
                    head = _GLOBAL_PRIORITY_GATE.head_priority() if _GLOBAL_PRIORITY_GATE else None
                except Exception:  # noqa: BLE001
                    head = None
                action = mios_preempt.decide(
                    finished=False,
                    quantum_expired=q.expired(time.monotonic()),
                    higher_priority_waiting=(head is not None and head > priority),
                    can_suspend=_PREEMPT.can_admit() if _PREEMPT else False)
                if action != mios_preempt.PREEMPT:
                    continue
                slot = _PREEMPT.acquire_slot() if _PREEMPT else None
                if slot is None:                  # lost the slot race -> keep running
                    continue
                await _kv_slot_action(client, ep, "save", conv, model, slot_id)
                _PREEMPT.suspend(mios_preempt.Snapshot(conv, priority, produced, partial, slot))
                preempted = True

            if preempted:
                if held and _GLOBAL_PRIORITY_GATE is not None:
                    _GLOBAL_PRIORITY_GATE.release()
                    held = False
                try:
                    if _GLOBAL_PRIORITY_GATE is not None:
                        await _GLOBAL_PRIORITY_GATE.acquire(priority)  # blocks till we're next
                        held = True
                finally:
                    if _PREEMPT:
                        _PREEMPT.discharge(conv)      # free our snapshot slot
                async with _ep_lane_scope():
                    await _kv_slot_action(client, ep, "restore", conv, model, slot_id)
                q = mios_preempt.Quantum(time.monotonic(), RR_QUANTUM_S)  # fresh quantum
        return partial
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- degrade-open: one shot for the whole budget
        log.warning("RR preemptible decode failed; single-completion fallback",
                    exc_info=True)
        if _PREEMPT and _PREEMPT.is_suspended(conv):
            _PREEMPT.discharge(conv)
        if not held and _GLOBAL_PRIORITY_GATE is not None:
            try:
                await _GLOBAL_PRIORITY_GATE.acquire(priority)
                held = True
            except Exception:  # noqa: BLE001
                pass
        try:
            async with _ep_lane_scope():
                n_slots = await _get_slot_count(client, ep, model)
                slot_id = _stable_hash(conv) % n_slots
                text, _ = await _rr_slice(client, ep, model, list(messages), total, headers, slot_id)
                return (partial + text) if partial else text
        except Exception:  # noqa: BLE001
            return partial
    finally:
        if held and _GLOBAL_PRIORITY_GATE is not None:
            try:
                _GLOBAL_PRIORITY_GATE.release()
            except Exception:  # noqa: BLE001
                pass
