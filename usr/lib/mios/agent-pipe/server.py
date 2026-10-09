# AI-hint: FastAPI gateway service on the `agent_pipe` port that routes, dispatches, and proxies chat/embedding requests from external interfaces (Discord, Slack) to th...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
from __future__ import annotations

import asyncio
import base64
import collections
import contextlib
import functools
import contextvars
import datetime
import glob
import hashlib
import hmac
import json
import logging
import collections.abc
import os

class _StrippedEnviron(collections.abc.MutableMapping):
    def __init__(self, original):
        self._original = original

    def __getitem__(self, key):
        val = self._original[key]
        if isinstance(val, str):
            return val.strip("'\"")
        return val

    def __setitem__(self, key, value):
        self._original[key] = value

    def __delitem__(self, key):
        del self._original[key]

    def __iter__(self):
        return iter(self._original)

    def __len__(self):
        return len(self._original)

os.environ = _StrippedEnviron(os.environ)
import random
import re
import shlex
import socket as _socket
import sys
import time
import uuid
from typing import Any, AsyncGenerator, Optional

import httpx
import websockets
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse,
                               Response, StreamingResponse)
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="[mios-agent-pipe] %(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("mios-agent-pipe")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mios_jsonsalvage import loads_lenient as _loads_lenient   # noqa: E402
from mios_owui import (strip_owui_scaffold as _strip_owui_scaffold,  # noqa: E402
                       OWUI_TEMPLATE_MARKERS as _OWUI_TEMPLATE_MARKERS)
from mios_sched import (PriorityGate,   # noqa: E402  -- WS-1 priority scheduler queue
                        _lane_tool_cap, _agent_offload_engine,
                        _resolve_autonomous_priority, _sched_priority, _lane_sem_key)
from mios_evict import (evict_where as _evict_where,  # noqa: E402  -- WS-A3 parameterized pg
                        order_by as _evict_order_by,
                        count_sql as _evict_count_sql,
                        select_ids_sql as _evict_select_ids_sql,
                        delete_ids_sql as _evict_delete_ids_sql,
                        evict_params as _evict_params,
                        parse_count as _evict_parse_count,
                        parse_ids as _evict_parse_ids,
                        plan_sweep as _evict_plan_sweep)
from mios_hitl import (parse_scope as _hitl_parse_scope,  # noqa: E402
                       requires_approval as _hitl_requires,
                       gate_outcome as _hitl_gate_outcome,
                       block_result as _hitl_block_result)
from mios_aci import normalize_output as _aci_normalize   # noqa: E402  -- WS-5 ACI
from mios_kvfork import (validate_fork as _kvfork_validate,  # noqa: E402  -- WS-8 KV-cache fork
                        plan_fork as _kvfork_plan,
                        fork_outcome as _kvfork_outcome,
                        kv_filename as _kvfork_kv_filename)   # WS-A4 de-dup target
import mios_kvgc   # noqa: E402,F401  -- WS-A4 KV slot-file GC planner; retained for
import mios_secset   # noqa: E402  -- WS-A14 SSOT-derived high-privilege/taint sets
import mios_hopbudget   # noqa: E402  -- WS-4 hop-budget recursion guard + effort scaling
import mios_preempt   # noqa: E402  -- WS-A12 RR-preemption state machine + snapshot contract
import mios_sandbox   # noqa: E402  -- WS-A13 risk-tier dispatch-sandbox profile resolver
import mios_cua   # noqa: E402  -- WS-8 perceive->act->verify computer-use loop core
import mios_interop   # noqa: E402  -- WS-11 3-projection (A2A skill shape) interop
import mios_router   # noqa: E402  -- WS-A11/WS-3 pure routing decision
import mios_dispatcher   # noqa: E402  -- WS-A11/WS-3 pure mode dispatcher
import mios_kernel   # noqa: E402  -- WS-A11/WS-3 Kernel facade (Router+Dispatcher+managers)
import mios_memguard   # noqa: E402  -- WS-MEM-VALIDATE write-time memory-poisoning guard (ASI08)
import mios_slo   # noqa: E402  -- WS-SCHED-SLO deadline/SLO classes + fail-closed shed
import mios_blades   # noqa: E402  -- V4/V5 blade (machine) topology + per-blade capacity model
import mios_cost   # noqa: E402  -- WS-RES-GOV cost/energy accounting (CLASSic Cost axis)
import mios_promptver   # noqa: E402  -- WS-LIFECYCLE-VER versioned hop-prompt registry
_PROMPT_REGISTRY = mios_promptver.PromptRegistry()
import mios_smartroute   # noqa: E402  -- WS-A16 cost/quality SmartRouting (local-first escalation)
import mios_codemode as _codemode   # noqa: E402  -- WS-2 Code Mode pure helpers
import mios_pg as _mios_pg   # noqa: E402  -- WS-9 Postgres+pgvector client
import mios_lanes   # noqa: E402  -- WS-1 unified inference-lane resolver
import mios_a2a_principal as _a2a_pp   # noqa: E402  -- WS-6 signed delegation principal
import mios_reputation   # noqa: E402  -- #54 zero-trust peer reputation
import mios_quota   # noqa: E402  -- WS-6 per-user quota / rate-limit (inert until configured)
import mios_capreg   # noqa: E402  -- WS-2 unified RBAC-filtered capability manifest
import mios_gateway_queue
from mios_psi import get_psi_monitor, PSIMonitor  # noqa: E402  -- T-485 Linux PSI telemetry & load shedding
_PSI_MONITOR: Optional[PSIMonitor] = None
_PSI_MONITOR_TASK: Optional[asyncio.Task] = None
_PSI_THROTTLED: bool = False
_INFERENCE_THROTTLE_CALLBACKS: list = []

def is_inference_throttled() -> bool:
    """True if inference workers should be throttled due to PSI pressure."""
    if _PSI_THROTTLED:
        return True
    try:
        mon = _PSI_MONITOR or get_psi_monitor()
        if mon and (mon.is_critical() or mon.is_throttled()):
            return True
    except Exception:
        pass
    return False

def register_inference_throttle_callback(callback) -> None:
    """Register a callback invoked when throttling state transitions."""
    if callback not in _INFERENCE_THROTTLE_CALLBACKS:
        _INFERENCE_THROTTLE_CALLBACKS.append(callback)

async def _signal_inference_throttling(throttled: bool, event: dict) -> None:
    """Signals throttling events to inference workers and event subscribers."""
    global _PSI_THROTTLED
    _PSI_THROTTLED = throttled

    # 1. Notify GatewayWorker if active
    global _GATEWAY_WORKER
    if _GATEWAY_WORKER is not None:
        try:
            setattr(_GATEWAY_WORKER, "throttled", throttled)
        except Exception:
            pass

    # 2. Broadcast via AgentEventHub if available
    global _event_hub
    if "_event_hub" in globals() and _event_hub is not None:
        try:
            await _event_hub.broadcast(
                "throttling" if throttled else "psi_pressure",
                {
                    "throttled": throttled,
                    "level": event.get("level", "CRITICAL" if throttled else "NORMAL"),
                    "event": event,
                }
            )
        except Exception:
            pass

    # 3. Invoke any registered inference worker callbacks
    import inspect
    for cb in list(_INFERENCE_THROTTLE_CALLBACKS):
        try:
            if inspect.iscoroutinefunction(cb):
                await cb(throttled, event)
            else:
                cb(throttled, event)
        except Exception as ex:
            log.warning("Inference throttle callback failed: %s", ex)

_GATEWAY_QUEUE = None
_GATEWAY_WORKER = None
_GATEWAY_TASK = None
_MCP_POOL = None
import mios_crl   # noqa: E402  -- WS-A10 principal/cert revocation list (inert until a CRL file exists)
import mios_gossip   # noqa: E402  -- WS-A18 epidemic peer discovery (inert until [gossip].interval_min>0)
_A2A_REPUTATION = mios_reputation.PeerReputation()   # outbound-peer reliability
import mios_selfimprove   # noqa: E402,F401  -- #64 analyzer; now consumed via mios_daemons (_selfimprove_report moved there), retained for import-surface parity
import mios_toolconflict   # noqa: E402  -- WS-A7 per-verb dispatch conflict/parallel-limit gate
import mios_trace   # noqa: E402  -- WS-A8 per-request trace/span observability
import mios_pdp as _pdp   # noqa: E402  -- WS-A9 policy decision point (capability gate)
import mios_embed_backfill as _embf   # noqa: E402  -- WS-A2 embedding-version backfill planner
import mios_memory   # noqa: E402  -- WS-A15 pluggable MemoryProvider seam
import mios_tokenize   # noqa: E402  -- WS-A5 tokenizer seam (token accounting)
import mios_ctxpack    # noqa: E402  -- WS-A5 priority token-budget context packer
import mios_compact    # noqa: E402  -- WS-A5 rolling-summary compaction planner
from mios_config import (   # noqa: E402
    PORT,
    MCP_SERVER_PORT,
    _LIGHT_BASE,
    _LIGHT_LANE,
    ROUTER_ENABLED,
    ROUTER_MODEL,
    ROUTER_ENDPOINT,
    ROUTER_TIMEOUT_S,
    ROUTER_MAX_TOKENS,
    PLANNER_ENABLED,
    PLANNER_MODEL,
    PLANNER_ENDPOINT,
    PLANNER_TIMEOUT_S,
    PLANNER_MAX_TOKENS,
    PLANNER_MAX_NODES,
    PLANNER_REFLEXION_CAP,
    _ROUTER_SYSTEM,
    REFINE_ENABLED,
    REFINE_MODEL,
    REFINE_ENDPOINT,
    REFINE_TIMEOUT_S,
    REFINE_ATTEMPTS,
    REFINE_MAX_TOKENS,
    REFINE_BYPASS_CHARS,
    REFINE_KEEP_ALIVE,
    JUDGE_EXAMPLES,
    _PG_ENABLED,
    _PG_PRIMARY,
    CONSENSUS_ENABLED,
    CONSENSUS_LANES,
    CONSENSUS_THRESHOLD,
    CONSENSUS_MIN_LANES,
    CONSENSUS_TIMEOUT_S,
    CONSENSUS_WEIGHT_FLOOR,
    DRIFT_MONITOR_ENABLED,
    DRIFT_MONITOR_THRESHOLD,
    DRIFT_MONITOR_WINDOW,
    DRIFT_MONITOR_MIN_SAMPLES,
    DRIFT_MONITOR_AXES,
    MEMORY_CONSOLIDATE_ENABLED,
    MEMORY_CONSOLIDATE_INTERVAL_S,
    MEMORY_CONSOLIDATE_MAX_GROUPS,
    POLISH_ENABLED,
    POLISH_MODEL,
    POLISH_ENDPOINT,
    POLISH_TIMEOUT_S,
    POLISH_MAX_TOKENS,
    BACKEND,
    _BACKEND_IS_LIGHT,
    BACKEND_MODEL,
    _BACKEND_HOSTPORT,
    _HERMES_ENDPOINT,
    _HERMES_WORKER_ENDPOINT,
    _AUTH_HOSTPORTS,
    _AGENT_AUTH_BY_HOSTPORT,
    CLIENT_TOOLS_PASSTHROUGH,
    _TOOL_BACKEND,
    _TOOL_BACKEND_MODEL,
    _TOOL_BACKEND_HEAVY,
    _TOOL_BACKEND_HEAVY_MODEL,
    _HEAVY_PROBE_TTL,
    _INGRESS_KEY,
    _STACK_MODEL,
    _MICRO_MODEL,
    _MICRO_ENDPOINT,
    _MICRO_BASE,
    _toml_section,
    _cfg_num,
    _dispatch_toml,
    _DISPATCH_TOML,
    _dispatch_num,
    KV_SLOT_PERSIST,
)
from mios_dci import (   # noqa: E402
    DCI_ENABLED,
    DCI_MODEL,
    DCI_ENDPOINT,
    DCI_TIMEOUT_S,
    DCI_MAX_TOKENS,
    _DCI_ACTS,
    _DCI_ACT_NAMES,
    _DCI_ACT_SCHEMA,
    _DCI_CRITIC_SYSTEM,
    DCI_FLOW_ENABLED,
    DCI_FLOW_R_MAX,
    DCI_FLOW_TIMEOUT_S,
    _PERSONA_ALLOWED_ACTS,
    _persona_prompt,
    _DCI_FRAMER_SYSTEM,
    _DCI_EXPLORER_SYSTEM,
    _DCI_CHALLENGER_SYSTEM,
    _DCI_INTEGRATOR_SYSTEM,
    _DCI_PERSONAS,
    _dci_call_persona,
    run_dci_flow,
    DCI_FLOW_TRIGGER_CONF,
    critic_then_maybe_flow,
    dci_critic_pass,
)

from mios_pipe.access.authn import (
    _apply_outbound_auth,
    _load_backend_key,
    _load_caller_keys,
    _check_inbound_principal,
    _probe_auth_headers,
    _bind_host,
    configure as _configure_authn,
)

# MIOS_WEB_RESEARCH_* env vars stay as runtime overrides; the trailing literal is
_WEB_TOML = _toml_section("web_research")
_AGENT_PIPE_TOML = _toml_section("agent_pipe") or {}
# MIOS_KNOWLEDGE_* env vars stay as runtime overrides; the trailing literal is
_KN_TOML = _toml_section("knowledge")
WEB_CONCURRENCY = int(os.environ.get("MIOS_WEB_CONCURRENCY", "3"))
WEB_DISPATCH_JITTER_S = float(os.environ.get("MIOS_WEB_DISPATCH_JITTER_S", "0.15"))
_web_sem = asyncio.Semaphore(max(1, WEB_CONCURRENCY))

TRACE_ENABLE = os.environ.get("MIOS_TRACE_ENABLE", "1").strip().lower() \
    not in ("0", "false", "no", "off", "")
TRACE_MAX_TRACES = int(os.environ.get("MIOS_TRACE_MAX_TRACES", "256") or 256)
TRACE_MAX_SPANS = int(os.environ.get("MIOS_TRACE_MAX_SPANS_PER_TRACE", "128") or 128)
_TRACER = mios_trace.Tracer(enabled=TRACE_ENABLE, max_traces=TRACE_MAX_TRACES,
                            max_spans_per_trace=TRACE_MAX_SPANS)
_trace_id_var: "contextvars.ContextVar" = contextvars.ContextVar("mios_trace_id", default="")
_span_id_var: "contextvars.ContextVar" = contextvars.ContextVar("mios_span_id", default="")

_otel_toml = _toml_section("observability") or {}
_DEBUG_ENABLE = (
    str(os.environ.get("MIOS_DEBUG_ENABLE") or _otel_toml.get("debug", "true"))
    .strip().lower() not in {"false", "0", "no", "off", ""}
)
_OTEL_ENABLE = (
    str(os.environ.get("MIOS_OTEL_ENABLE") or _otel_toml.get("otel_enable", "false"))
    .strip().lower() not in {"false", "0", "no", "off", ""}
)
# Law 5/7: the collector's port resolves from the SSOT name, never a bare literal.
# [observability].otel_endpoint ships a ${MIOS_PORTS_OTELCOL_OTLP} placeholder and
# os.path.expandvars leaves it VERBATIM when the var is unset, so an unexpanded
# value is not an endpoint -- drop it and rebuild from the resolved port.
_OTEL_PORT = os.environ.get("MIOS_PORTS_OTELCOL_OTLP", "8575")
_otel_cfg_endpoint = str(_otel_toml.get("otel_endpoint") or "").strip()
if "${" in _otel_cfg_endpoint:
    _otel_cfg_endpoint = ""
_OTEL_ENDPOINT = (
    str(os.environ.get("MIOS_OTEL_ENDPOINT") or _otel_cfg_endpoint
        or "http://localhost:%s" % _OTEL_PORT)
    .strip()
)

_otel_tracer = None
if _OTEL_ENABLE:
    try:
        from opentelemetry import trace as otel_trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource

        provider = TracerProvider(resource=Resource.create({"service.name": "mios-agent-pipe"}))
        processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=_OTEL_ENDPOINT))
        provider.add_span_processor(processor)
        otel_trace.set_tracer_provider(provider)
        _otel_tracer = otel_trace.get_tracer("mios-agent-pipe")
        logging.getLogger("mios-agent-pipe").info("OpenTelemetry trace provider initialized (endpoint: %s)", _OTEL_ENDPOINT)
    except Exception as otel_err:
        logging.getLogger("mios-agent-pipe").warning("Failed to initialize OpenTelemetry trace provider: %s", otel_err)

def _current_trace_id() -> str:
    """The active request's trace id ('' when untraced)."""
    try:
        return _trace_id_var.get() or ""
    except Exception:  # noqa: BLE001
        return ""

@contextlib.asynccontextmanager
async def _trace_span(name: str, **attrs):
    """Open a span under the current trace/parent (contextvars), record it on
    exit with duration + ok/error status. Near-no-op when tracing is disabled or
    no trace is active (degrade-open)."""
    tid = _current_trace_id()
    if not (_TRACER.enabled and tid):
        yield None
        return
    span = _TRACER.start_span(name, trace_id=tid,
                              parent_id=(_span_id_var.get() or ""), attrs=attrs)
    token = _span_id_var.set(span.span_id)
    try:
        yield span
    except BaseException as e:  # noqa: BLE001 -- record the failure then re-raise
        span.finish("error", type(e).__name__)
        raise
    finally:
        _span_id_var.reset(token)
        if not span.ended:
            span.finish("ok")
        _TRACER.record(span)

def _traced_stage(name: str):
    """Decorator: emit a span around each call of an async pipeline-stage fn."""
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*a, **kw):
            async with _trace_span(name):
                return await fn(*a, **kw)
        return wrapper
    return deco

EMB_MODEL = os.environ.get("MIOS_PGVECTOR_EMB_MODEL", "nomic-embed-text")
EMB_VERSION = os.environ.get("MIOS_PGVECTOR_EMB_VERSION", "nomic-768-v1")
CATALOG_FAIL_MODE = str(os.environ.get("MIOS_CATALOG_FAIL_MODE", "warn")).strip().lower()
SCRATCHPAD_PERSIST = str(os.environ.get("MIOS_SCRATCHPAD_PERSIST", "1")).strip().lower() \
    not in ("0", "false", "no", "off", "")

WEB_RESEARCH_ENABLED = os.environ.get(
    "MIOS_WEB_RESEARCH_ENABLED", "true").lower() not in {"false", "0", "no"}
WEB_RESEARCH_PASSES = max(1, _cfg_num(_WEB_TOML, "MIOS_WEB_RESEARCH_PASSES", "passes", 4))
WEB_RESEARCH_RESULTS = int(os.environ.get("MIOS_WEB_RESEARCH_RESULTS", "8"))
WEB_RESEARCH_FANOUT = int(os.environ.get(
    "MIOS_WEB_RESEARCH_FANOUT", os.environ.get("MIOS_WEB_FANOUT", "3")))
WEB_RESEARCH_FETCH_N = int(os.environ.get("MIOS_WEB_RESEARCH_FETCH_N", "5"))
WEB_RESEARCH_FETCH_CHARS = int(os.environ.get("MIOS_WEB_RESEARCH_FETCH_CHARS", "3000"))
WEB_RESEARCH_BLOCK_CHARS = int(os.environ.get("MIOS_WEB_RESEARCH_BLOCK_CHARS", "1200"))
WEB_RESEARCH_SEARCH_TIMEOUT = float(os.environ.get("MIOS_WEB_RESEARCH_SEARCH_TIMEOUT_S", "30"))
WEB_RESEARCH_FETCH_TIMEOUT = float(os.environ.get("MIOS_WEB_RESEARCH_FETCH_TIMEOUT_S", "12"))
WEB_RESEARCH_CRAWL_FALLBACK = os.environ.get(
    "MIOS_WEB_RESEARCH_CRAWL_FALLBACK", "true").lower() not in {"false", "0", "no"}
WEB_RESEARCH_MIN_CHARS = int(os.environ.get("MIOS_WEB_RESEARCH_MIN_CHARS", "300"))
WEB_RESEARCH_CRAWL_TIMEOUT = _cfg_num(_WEB_TOML, "MIOS_WEB_RESEARCH_CRAWL_TIMEOUT_S", "crawl_timeout_s", 45, float)
WEB_RESEARCH_CRAWL_MAX = int(os.environ.get("MIOS_WEB_RESEARCH_CRAWL_MAX", "6"))
WEB_RESEARCH_USE_NEWS_CATEGORY = os.environ.get(
    "MIOS_WEB_RESEARCH_USE_NEWS_CATEGORY", "true").lower() not in {"false", "0", "no"}
WEB_RESEARCH_TIME_RANGE = os.environ.get("MIOS_WEB_RESEARCH_TIME_RANGE", "").strip()
WEB_RESEARCH_MAX_ATTEMPTS = max(1, _cfg_num(_WEB_TOML, "MIOS_WEB_RESEARCH_MAX_ATTEMPTS", "max_attempts", 5))
_JUDGE_MODEL = os.environ.get(
    "MIOS_WEB_RESEARCH_JUDGE_MODEL", os.environ.get("MIOS_DAEMON_MODEL", _STACK_MODEL))
_JUDGE_ENDPOINT = os.environ.get(
    "MIOS_WEB_RESEARCH_JUDGE_ENDPOINT",
    os.environ.get("MIOS_DAEMON_ENDPOINT", _LIGHT_BASE + "/v1")).rstrip("/")  # mios-llm-light (WS-0B)
_JUDGE_BASE = (_JUDGE_ENDPOINT[:-3].rstrip("/") if _JUDGE_ENDPOINT.endswith("/v1") else _JUDGE_ENDPOINT)

HEALTHGATE_CONNECT_TIMEOUT = float(os.environ.get("MIOS_AGENT_HEALTHGATE_CONNECT_S", "6"))
HEALTHGATE_READ_TIMEOUT = float(os.environ.get("MIOS_AGENT_HEALTHGATE_READ_S", "120"))

NODE_LIVENESS_TTL_S = float(os.environ.get("MIOS_NODE_LIVENESS_TTL_S", "45"))
NODE_LIVENESS_CONNECT_S = float(os.environ.get("MIOS_NODE_LIVENESS_CONNECT_S", "6"))
_NODE_LIVE: dict = {}  # name -> (probed_ts, reachable)

def _is_remote_endpoint(ep: str) -> bool:
    """True when `ep` is a non-empty REMOTE endpoint (a tailnet/LAN host that can
    come and go), False for empty or localhost/127.0.0.1/::1 (always-local lanes)."""
    ep = str(ep or "").strip()
    if not ep:
        return False
    netloc = ep.split("://", 1)[-1].split("/", 1)[0]
    host = (netloc.rsplit(":", 1)[0] if ":" in netloc else netloc).strip("[]").lower()
    return host not in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "")

def _should_health_probe(cfg: dict) -> bool:
    if cfg.get("health_gate"):
        return True
    return _is_remote_endpoint(cfg.get("endpoint", ""))

SLOW_LANES = set(x.strip() for x in os.environ.get(
    "MIOS_SLOW_LANES", "igpu,mobile,accelerator,cpu").split(",") if x.strip())
SLOW_LANE_BLOCK_CHARS = int(os.environ.get("MIOS_SLOW_LANE_BLOCK_CHARS", "1500"))
DEEPEN_LANES = set(x.strip() for x in os.environ.get(
    "MIOS_DEEPEN_LANES",
    str(_toml_section("dispatch").get("deepen_lanes", "gpu,accelerator"))
    ).split(",") if x.strip())
from mios_pipe.scheduler.admission import (
    _parse_lane_caps,
    _priority_gate,
    configure as _configure_admission,
)

LANE_TOOL_CAP = _parse_lane_caps(
    os.environ.get("MIOS_LANE_TOOL_CAP")
    or str(_toml_section("dispatch").get("lane_tool_cap", "igpu:15,mobile:15")))

SLOW_LANE_TOOL_CAP = int(os.environ.get(
    "MIOS_SLOW_LANE_TOOL_CAP",
    str(_toml_section("dispatch").get("slow_lane_tool_cap", 12))) or 12)

DEFAULT_TOOL_CAP = int(os.environ.get(
    "MIOS_DEFAULT_TOOL_CAP",
    str(_toml_section("dispatch").get("default_tool_cap", 24))) or 24)

DAG_NODE_MAX_TOKENS = _dispatch_num("MIOS_DAG_NODE_MAX_TOKENS", "dag_node_max_tokens", 800)
DAG_NODE_SLOW_MAX_TOKENS = _dispatch_num(
    "MIOS_DAG_NODE_SLOW_MAX_TOKENS", "dag_node_slow_max_tokens", 350)
DAG_NODE_RETRY = _dispatch_num("MIOS_DAG_NODE_RETRY", "dag_node_retry", 1)
DAG_NODE_DEADLINE_S = _dispatch_num("MIOS_DAG_NODE_DEADLINE_S", "dag_node_deadline_s", 75, float)
DAG_NODE_DEADLINE_SLOW_S = _dispatch_num(
    "MIOS_DAG_NODE_DEADLINE_SLOW_S", "dag_node_deadline_slow_s", 150, float)
SWARM_DEEPEN_ENABLED = (os.environ.get(
    "MIOS_SWARM_DEEPEN", str(_DISPATCH_TOML.get("deepen_enabled", True)))
    .strip().lower() not in ("0", "false", "no"))
SWARM_SATURATE = (os.environ.get(
    "MIOS_SWARM_SATURATE", str(_DISPATCH_TOML.get("swarm_saturate", True)))
    .strip().lower() not in ("0", "false", "no"))
DEEPEN_MAX_ITERS = _dispatch_num("MIOS_SWARM_DEEPEN_ITERS", "deepen_iters", 12)
DEEPEN_DEADLINE_S = _dispatch_num(
    "MIOS_SWARM_DEEPEN_DEADLINE_S", "deepen_deadline_s", 120, float)
DEEPEN_WEB_TIMEOUT_S = _dispatch_num(
    "MIOS_SWARM_DEEPEN_WEB_S", "deepen_web_timeout_s", 20, float)
DEEPEN_FETCH = (os.environ.get(
    "MIOS_SWARM_DEEPEN_FETCH", str(_DISPATCH_TOML.get("deepen_fetch", True)))
    .strip().lower() not in ("0", "false", "no"))
DEEPEN_EARLY_EXIT = (os.environ.get(
    "MIOS_SWARM_DEEPEN_EARLY_EXIT",
    str(_DISPATCH_TOML.get("deepen_early_exit", False)))
    .strip().lower() not in ("0", "false", "no", ""))
DEEPEN_JUDGE_TIMEOUT_S = _dispatch_num(
    "MIOS_SWARM_DEEPEN_JUDGE_S", "deepen_judge_timeout_s", 6, float)

READ_TOOL_ENRICH_ENABLED = os.environ.get(
    "MIOS_READ_TOOL_ENRICH_ENABLED", "true").lower() not in {"false", "0", "no"}
READ_TOOL_ENRICH_MAX = int(os.environ.get("MIOS_READ_TOOL_ENRICH_MAX", "3"))
READ_TOOL_ENRICH_TIMEOUT = float(os.environ.get("MIOS_READ_TOOL_ENRICH_TIMEOUT_S", "12"))
READ_TOOL_ENRICH_CHARS = int(os.environ.get("MIOS_READ_TOOL_ENRICH_CHARS", "1500"))
_ACI_TOML = _toml_section("aci")
ACI_MAX_LINES = _cfg_num(_ACI_TOML, "MIOS_ACI_MAX_LINES", "max_lines", 160, int)
ACI_HEAD_FRAC = _cfg_num(_ACI_TOML, "MIOS_ACI_HEAD_FRAC", "head_frac", 0.6, float)
_CODE_MODE_TOML = _toml_section("code_mode")
CODE_MODE_ENABLE = _codemode.is_enabled(_CODE_MODE_TOML)
CODE_MODE_HEAVY_ONLY = str(
    os.environ.get("MIOS_CODE_MODE_HEAVY_ONLY")
    or _CODE_MODE_TOML.get("heavy_lane_only", True)
).strip().lower() not in {"false", "0", "no"}
_WEB_ENRICH_VERBS = {"web_search", "web_extract", "crawl"}
SECONDARY_TOOL_LOOP = os.environ.get(
    "MIOS_SECONDARY_TOOL_LOOP", "true").lower() not in {"false", "0", "no"}
# MIOS_SECONDARY_TOOL_ITERS to trade thoroughness against per-node cost.
SECONDARY_TOOL_MAX_ITERS = int(os.environ.get("MIOS_SECONDARY_TOOL_ITERS", "") or _AGENT_PIPE_TOML.get("tool_max_iters", 15))
AUTO_FORCE_TOOL = os.environ.get(
    "MIOS_AUTO_FORCE_TOOL", "true").lower() not in {"false", "0", "no"}
from mios_sse import STATUS_AS_REASONING  # noqa: E402
AGENT_CONCURRENCY = _dispatch_num("MIOS_AGENT_CONCURRENCY", "agent_concurrency", 3)
_agent_sem = asyncio.Semaphore(max(1, AGENT_CONCURRENCY))

GLOBAL_DISPATCH_CONCURRENCY = _dispatch_num(
    "MIOS_GLOBAL_CONCURRENCY", "global_concurrency",
    max(8, (os.cpu_count() or 8) - 4))
_GLOBAL_DISPATCH_SEM = asyncio.Semaphore(max(1, GLOBAL_DISPATCH_CONCURRENCY))

# MIOS_PRIORITY_QUEUE flips on, and ANY error falls back to the proven plain FIFO
PRIORITY_QUEUE_ENABLE = str(os.environ.get("MIOS_PRIORITY_QUEUE")
                            or _DISPATCH_TOML.get("priority_queue_enable", "true")
                            ).strip().lower() in {"1", "true", "yes"}
PRIORITY_STARVATION_S = _dispatch_num("MIOS_PRIORITY_STARVATION_MS",
                                  "priority_starvation_ms", 4000, float) / 1000.0

_ADMISSION_TOML = _toml_section("admission") or {}
MULTIBLADE_ENABLE = str(os.environ.get("MIOS_MULTIBLADE_ENABLE")
                        or _ADMISSION_TOML.get("multiblade_enable", "false")
                        ).strip().lower() in {"1", "true", "yes"}
TENANT_QUOTA_ENABLE = str(os.environ.get("MIOS_TENANT_QUOTA_ENABLE")
                          or _ADMISSION_TOML.get("tenant_quota_enable", "false")
                          ).strip().lower() in {"1", "true", "yes"}
TENANT_MAX_CONCURRENCY = _cfg_num(_ADMISSION_TOML, "MIOS_TENANT_MAX_CONCURRENCY",
                                  "tenant_max_concurrency", 0)
_GLOBAL_PRIORITY_GATE = PriorityGate(
    GLOBAL_DISPATCH_CONCURRENCY, PRIORITY_STARVATION_S,
    tenant_cap=(TENANT_MAX_CONCURRENCY if TENANT_QUOTA_ENABLE else 0))

LLM_NUM_PREDICT_CAP = _dispatch_num(
    "MIOS_LLM_NUM_PREDICT_CAP", "llm_num_predict_cap", 2048)
LLM_NUM_PREDICT_CAP_CPU = _dispatch_num(
    "MIOS_LLM_NUM_PREDICT_CAP_CPU", "llm_num_predict_cap_cpu", 512)
TURN_DEADLINE_S = _dispatch_num("MIOS_TURN_DEADLINE_S", "turn_deadline_s", 600, float)
REQUEST_CANCEL_ENABLE = _dispatch_num(
    "MIOS_REQUEST_CANCEL_ENABLE", "request_cancel_enable", 1, int) != 0
REQUEST_CANCEL_POLL_S = _dispatch_num(
    "MIOS_REQUEST_CANCEL_POLL_S", "request_cancel_poll_s", 2.0, float)
_CHAT_CANCEL: dict = {}

MAX_DISPATCH_DEPTH = _dispatch_num("MIOS_MAX_DISPATCH_DEPTH", "max_dispatch_depth", int(_DISPATCH_TOML.get("default_hop_budget", 2)))
_dispatch_depth_var: "contextvars.ContextVar" = contextvars.ContextVar(
    "mios_dispatch_depth", default=0)

def _dispatch_depth() -> int:
    """Current fan-out hop depth for this async context (0 at the turn entry)."""
    try:
        return int(_dispatch_depth_var.get(0))
    except Exception:  # noqa: BLE001
        return 0

def _enter_dispatch_hop() -> int:
    """Increment + return the new fan-out depth for THIS context (child tasks
    created after this inherit it). Call once per fan-out hop before dispatching
    secondaries so a nested swarm sees a higher depth and degrades closed."""
    d = _dispatch_depth() + 1
    try:
        _dispatch_depth_var.set(d)
    except Exception:  # noqa: BLE001
        pass
    return d

def _depth_exhausted() -> bool:
    """True when a further fan-out hop would exceed MAX_DISPATCH_DEPTH -> the
    caller must degrade CLOSED to single-agent (no _plan_swarm / no fanout)."""
    return mios_hopbudget.depth_exhausted(_dispatch_depth(), MAX_DISPATCH_DEPTH)  # WS-4 pure guard

_HOP_HEADER = "X-MiOS-Hop"      # dispatch depth seen so far (Max-Forwards-style budget)
_VIA_HEADER = "X-MiOS-Via"      # comma-separated agent-id chain (Via-style loop detect)
_via_chain_var: "contextvars.ContextVar" = contextvars.ContextVar(
    "mios_via_chain", default="")

def _hop_via_headers() -> dict:
    """Headers to stamp on a worker sub-dispatch so the recursion bound survives the
    HTTP hop: the receiving worker's depth (this hop + 1) and our self-id appended to
    the Via chain. (A2A_SELF_ID resolved at call time -- defined later in the module.)"""
    try:
        _chain = mios_hopbudget.append_via(_via_chain_var.get(), A2A_SELF_ID)  # WS-4
        return {_HOP_HEADER: str(_dispatch_depth() + 1), _VIA_HEADER: _chain}
    except Exception:  # noqa: BLE001 -- never break a dispatch on the loop-guard
        return {}

def _seed_hop_from_headers(hop_hdr, via_hdr) -> None:
    """At chat_completions entry: seed the dispatch depth FROM the incoming X-MiOS-Hop
    (so the bound crosses the HTTP hop) and record the Via chain. If our OWN id is
    already in the chain, force degrade-closed (no further fan-out) -> a re-entrant
    loop answers single-agent instead of recursing. Degrade-open on any error."""
    try:
        if hop_hdr is not None and str(hop_hdr).strip():
            _dispatch_depth_var.set(mios_hopbudget.seed_depth(hop_hdr))  # WS-4
    except Exception:  # noqa: BLE001
        pass
    _via = str(via_hdr or "").strip()
    try:
        _via_chain_var.set(_via)
        if mios_hopbudget.is_loop(_via, A2A_SELF_ID):  # WS-4 pure loop guard
            _dispatch_depth_var.set(max(MAX_DISPATCH_DEPTH, _dispatch_depth()))
            log.warning("loop guard: self-id %s already in Via %r -> degrade-closed "
                        "(single-agent, no fan-out)", A2A_SELF_ID, _via)
    except Exception:  # noqa: BLE001
        pass

_AUTO_PRIO_WORDS = {"low": 1.0, "normal": 5.0, "medium": 5.0, "high": 9.0}
sys.modules["mios_sched"].configure(_AUTO_PRIO_WORDS=_AUTO_PRIO_WORDS)

AUTONOMOUS_PRIORITY = _resolve_autonomous_priority()

COUNCIL_MAX_DEFAULT = _dispatch_num("MIOS_COUNCIL_MAX", "council_max", 4)
COUNCIL_DEFAULT = str(os.environ.get(
    "MIOS_COUNCIL_DEFAULT",
    str((_toml_section("dispatch") or {}).get("council_default", "true")))
).strip().lower() not in ("0", "false", "no")
SWARM_MAX_WIDTH = _dispatch_num("MIOS_SWARM_MAX_WIDTH", "swarm_max_width", 6)
EFFORT_DEFAULT = (os.environ.get("MIOS_EFFORT") or "max").strip().lower()
DAG_EMPTY_NATIVE_FALLBACK = str(os.environ.get(
    "MIOS_DAG_EMPTY_NATIVE_FALLBACK",
    str((_toml_section("dispatch") or {}).get("dag_empty_native_fallback", "true")))
).strip().lower() not in ("0", "false", "no")
SWARM_TRUST_ATOMIC = str(os.environ.get(
    "MIOS_SWARM_TRUST_ATOMIC",
    str((_toml_section("dispatch") or {}).get("swarm_trust_atomic", "true")))
).strip().lower() not in ("0", "false", "no")
SWARM_MAX_CPU_NODES = _dispatch_num("MIOS_SWARM_MAX_CPU_NODES", "swarm_max_cpu_nodes", 2)

# MIOS_AGENT_CONCURRENCY); override one lane via MIOS_AGENT_LANE_CONCURRENCY_<LANE>
_LANE_SEMS: dict = {}
_ENDPOINT_SEMS: dict = {}
ENDPOINT_CONCURRENCY = _dispatch_num("MIOS_AGENT_ENDPOINT_CONCURRENCY",
                                 "endpoint_concurrency", 2)

ADMIT_ENABLE = str(os.environ.get("MIOS_ADMIT_ENABLE")
                   or _DISPATCH_TOML.get("admit_enable", "false")).lower() in {"1", "true", "yes"}
ADMIT_LOAD_CEIL = _dispatch_num("MIOS_ADMIT_LOAD_CEIL", "admit_load_ceil",
                            max(2, (os.cpu_count() or 4)) * 2, float)
ADMIT_MEM_PCT = _dispatch_num("MIOS_ADMIT_MEM_PCT", "admit_mem_pct", 92, float)
ADMIT_MAX_WAIT = _dispatch_num("MIOS_ADMIT_MAX_WAIT", "admit_max_wait", 8.0, float)
SLO_SHED_ENABLE = (
    str(os.environ.get("MIOS_SLO_SHED_ENABLE")
        or _DISPATCH_TOML.get("slo_shed_enable", "false"))
    .strip().lower() not in {"false", "0", "no", "off", ""})

mios_slo.configure(
    budgets={
        mios_slo.INTERACTIVE: float((_toml_section("slo") or {}).get(
            "interactive_budget_s", 8.0)),
        mios_slo.BEST_EFFORT: float((_toml_section("slo") or {}).get(
            "best_effort_budget_s", 120.0)),
    },
    default_priority=float((_toml_section("slo") or {}).get("default_priority", 7.0)),
    interactive_priority=float((_toml_section("slo") or {}).get(
        "interactive_priority", 7.0)),
)

from mios_pipe import vram_scheduler as _vram_scheduler
from mios_pipe.vram_scheduler import (
    _SloShed,
    _lane_sem,
    _endpoint_key,
    _endpoint_sem,
    _admit,
    configure as _configure_vram_scheduler,
    _LANE_SEMS,
    _ENDPOINT_SEMS,
    _ACTIVE_MODELS,
    _ACTIVE_LOCK,
    _ENDPOINT_RESERVED,
    _HOST_STATS_CACHE,
    _RESIDENT_CACHE,
    _ADMIT_SEQ,
)

RUNAWAY_REAP_ENABLE = str(os.environ.get("MIOS_RUNAWAY_REAP")
                          or _DISPATCH_TOML.get("runaway_reap", "true")
                          ).strip().lower() in {"1", "true", "yes"}

SWARM_DECOMPOSE_DEFAULT = os.environ.get(
    "MIOS_SWARM_DECOMPOSE_DEFAULT", "true").lower() not in {"false", "0", "no"}
SWARM_DECOMPOSE_MIN_WORDS = int(
    os.environ.get("MIOS_SWARM_DECOMPOSE_MIN_WORDS", "6"))
SWARM_MODEL = os.environ.get("MIOS_SWARM_MODEL", _STACK_MODEL)

LAUNCHER_SOCK = os.environ.get(
    "MIOS_LAUNCHER_SOCK", "/run/mios-launcher/launcher.sock",
)

_BACKEND_KEY = _load_backend_key()

_API_REQUIRE_AUTH = str(
    os.environ.get("MIOS_API_REQUIRE_AUTH")
    or (_toml_section("security") or {}).get("api_require_auth", "false")
).strip().lower() in {"1", "true", "yes"}
_CALLER_KEYS_PATH = str(
    os.environ.get("MIOS_CALLER_KEYS_PATH")
    or (_toml_section("security") or {}).get("api_caller_keys_path")
    or "/etc/mios/ai/v1/caller-keys.json")
_AUTH_GATED_PREFIXES = ("/v1/", "/a2a")
_AUTH_OPEN_PATHS = frozenset({
    "/v1/models", "/.well-known/agent-card.json", "/.well-known/agent.json",
    "/.well-known/agent-passport.json", "/a2a/card", "/health",
    "/v1/cluster/health",
    "/v1/system/psi",
    "/v1/agents"})
_CALLER_KEYS_CACHE: dict = {"mtime": -1.0, "keys": {}}

_configure_authn(
    backend_key=_BACKEND_KEY,
    ingress_key=_INGRESS_KEY,
    api_require_auth=_API_REQUIRE_AUTH,
    caller_keys_path=_CALLER_KEYS_PATH,
    auth_hostports=_AUTH_HOSTPORTS,
    agent_auth_by_hostport=_AGENT_AUTH_BY_HOSTPORT,
)

# Law 5/7: :8000 was the RETIRED SurrealDB lane, and this value is pushed into
# mios_pipe/db.py via _configure_db(db_url=...) -- so the stale literal here
# OVERRODE db.py's already-correct SSOT resolution. Resolve the same way it does.
_DB_PORT = os.environ.get("MIOS_PORTS_PGVECTOR", "8600")
DB_URL = os.environ.get("MIOS_DB_URL", "http://localhost:%s" % _DB_PORT)
DB_USER = os.environ.get("MIOS_DB_USER", "root")
DB_PASS = os.environ.get("MIOS_DB_PASS", "root")
DB_NS = os.environ.get("MIOS_DB_NS", "mios")
DB_DB = os.environ.get("MIOS_DB_DB", "mios")
_DB_AUTH = "Basic " + base64.b64encode(f"{DB_USER}:{DB_PASS}".encode()).decode()

PASSPORT_ENABLE = os.environ.get(
    "MIOS_PASSPORT_ENABLE", "true",
).lower() not in {"false", "0", "no"}
PASSPORT_ALGO = os.environ.get("MIOS_PASSPORT_ALGO", "ed25519")
PASSPORT_KEY_DIR = os.environ.get(
    "MIOS_PASSPORT_KEY_DIR", "/var/lib/mios/agent-passports")
PASSPORT_AGENT_NAME = os.environ.get(
    "MIOS_PASSPORT_AGENT_NAME", "agent-pipe")
PASSPORT_VERIFY_ON_READ = os.environ.get(
    "MIOS_PASSPORT_VERIFY_ON_READ", "false",
).lower() in {"true", "1", "yes"}

from mios_a2a_principal import (  # noqa: E402
    _passport_canonical_json,
    _passport_op_hash,
    _passport_load_priv,
    _passport_kid,
    _passport_load_public,
    _passport_sign,
    _passport_verify,
    _passport_priv,
    _passport_pub_cache,
    _passport_load_attempted,
)
sys.modules["mios_a2a_principal"].configure(
    passport_enable=PASSPORT_ENABLE,
    passport_algo=PASSPORT_ALGO,
    passport_key_dir=PASSPORT_KEY_DIR,
    passport_agent_name=PASSPORT_AGENT_NAME,
)
_db_down_until: float = 0.0

_TOKENIZER_BACKEND = str(os.environ.get("MIOS_TOKENIZER_BACKEND", "tiktoken")).strip().lower()
if _TOKENIZER_BACKEND not in ("", "heuristic"):
    _tok_backend = mios_tokenize.make_backend(
        _TOKENIZER_BACKEND,
        encoding=(os.environ.get("MIOS_TOKENIZER_ENCODING", "") or None),
        path=(os.environ.get("MIOS_TOKENIZER_PATH", "") or None),
        cache_dir=(os.environ.get("MIOS_TOKENIZER_CACHE_DIR", "") or None))
    if _tok_backend is not None:
        mios_tokenize.set_backend(_tok_backend)
        log.info("WS-A5: tokenizer backend %s installed (%s)",
                 _TOKENIZER_BACKEND, mios_tokenize.backend_name())
    else:
        log.warning("WS-A5: tokenizer_backend=%r unavailable (dep/asset missing) -- "
                    "using the heuristic (offline-safe)", _TOKENIZER_BACKEND)

@contextlib.asynccontextmanager
async def lifespan(app):
    async def _warm():
        try:
            await _ensure_verb_embeddings()
        except Exception as e:
            log.warning("verb embed warmup failed: %s", e)
        try:
            await _refresh_app_inventory()
        except Exception as e:
            log.warning("app inventory warmup failed: %s", e)
    asyncio.create_task(_warm())

    if AUDIT_CHAIN_ENABLE:
        await mios_audit.seed_from_db(_mios_pg.execute)
        await mios_audit.seed_session_from_db(_mios_pg.execute)

    if KV_GC_ENABLE and KV_SLOTS_DIR:
        asyncio.create_task(_kv_gc_loop())
        log.info("kv-gc loop on (interval=%ss ttl=%ss max=%d bytes dir=%s)",
                 KV_GC_INTERVAL_S, KV_GC_TTL_S, KV_GC_MAX_BYTES, KV_SLOTS_DIR)

    if KNOWLEDGE_EVICT_ENABLE or KNOWLEDGE_EVICT_DRYRUN:
        asyncio.create_task(_knowledge_evict_loop())
        log.info("knowledge-evict loop on (enable=%s dry_run=%s interval=%ss "
                 "ttl=%sd max_rows=%s batch=%s)",
                 KNOWLEDGE_EVICT_ENABLE, KNOWLEDGE_EVICT_DRYRUN,
                 KNOWLEDGE_EVICT_INTERVAL_S, KNOWLEDGE_EVICT_TTL_DAYS,
                 KNOWLEDGE_EVICT_MAX_ROWS, KNOWLEDGE_EVICT_BATCH)

    if AGENT_MEMORY_RECALL_ENABLED:
        asyncio.create_task(_consolidate_memory_loop())

    if (_qn := await sys.modules["mios_policy"].quota_preload()):
        log.info("quota ledger: %d principal budget(s) restored", _qn)

    for _pn, _pc in (("router", _ROUTER_SYSTEM), ("refine", _REFINE_SYSTEM),
                     ("polish", _POLISH_SYSTEM), ("planner", _PLANNER_SYSTEM),
                     ("reflect", _REFLECT_SYSTEM), ("swarm", _SWARM_SYSTEM),
                     ("dci_critic", _DCI_CRITIC_SYSTEM),
                     ("dci_framer", _DCI_FRAMER_SYSTEM),
                     ("dci_explorer", _DCI_EXPLORER_SYSTEM),
                     ("dci_challenger", _DCI_CHALLENGER_SYSTEM),
                     ("dci_integrator", _DCI_INTEGRATOR_SYSTEM),
                     ("local_state", _LOCAL_STATE_SYSTEM)):
        try:
            _PROMPT_REGISTRY.register(_pn, _pc)
        except Exception:  # noqa: BLE001
            pass

    p = _offline_posture()
    if p["offline"]:
        log.info("offline-guard: OK -- all %d inference endpoints are "
                 "local/tailnet (offline computation intact)",
                 len(p["checks"]))
    else:
        for c in p["external_endpoints"]:
            log.warning("offline-guard: VIOLATION -- %s -> %s is EXTERNAL "
                        "(cloud compute breaks MiOS offline-first law)",
                        c["role"], c["endpoint"])

    asyncio.create_task(_mcp_client_startup())

    if MEMBERSHIP_WATCH_ENABLE:
        asyncio.create_task(_membership_watch_loop())

    try:
        if int(_toml_section("gossip").get("interval_min", 0)) > 0:
            asyncio.create_task(_gossip_loop())
    except Exception:  # noqa: BLE001
        pass

    await _reputation_restore()

    async def _flush_loop() -> None:
        while True:
            try:
                await asyncio.sleep(REPUTATION_FLUSH_S)
                await _reputation_flush()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                pass

    if _PG_PRIMARY and REPUTATION_FLUSH_S > 0:
        asyncio.create_task(_flush_loop())

    try:
        if int(_toml_section("selfimprove").get("interval_min", 0)) > 0:
            asyncio.create_task(_selfimprove_loop())
    except Exception:  # noqa: BLE001
        pass

    asyncio.create_task(_a2a_client_startup())

    global _GATEWAY_QUEUE, _GATEWAY_WORKER, _GATEWAY_TASK, _MCP_POOL
    mcp_pool_enable = os.environ.get("MIOS_CONVERGE_IMAGE_MCP_POOL_ENABLE", "false").lower() in ("true", "1", "yes", "on")
    if mcp_pool_enable:
        tools_cfg = _toml_section("tools") or {}
        mcp_servers = tools_cfg.get("mcp_servers") or {}
        from mios_gateway_queue import MCPClientPool
        _MCP_POOL = MCPClientPool(mcp_servers)
        await _MCP_POOL.startup()
        sys.modules["mios_a2a"].configure(mcp_pool=_MCP_POOL)

    conv_gw_mode = os.environ.get("MIOS_CONVERGE_GATEWAY_MODE", "http")
    if conv_gw_mode == "queue":
        q_maxsize = int(os.environ.get("MIOS_CONVERGE_GATEWAY_QUEUE_MAXSIZE", "64"))
        w_concurrency = int(os.environ.get("MIOS_CONVERGE_GATEWAY_WORKER_CONCURRENCY", "4"))

        mios_gateway_queue.configure(
            verb_catalog=_VERB_CATALOG,
            recipes=_toml_section("recipes") or {},
            skills=_cap_skills(),
            trace_span=_trace_span
        )

        # Law 5: the legacy hermes port is RETIRED. [ai].endpoint is
        # "http://localhost:${MIOS_PORTS_AGENT_PIPE}/v1" -- mirror that resolution.
        _pipe_port = os.environ.get("MIOS_PORTS_AGENT_PIPE", "8700")
        ai_endpoint = os.environ.get(
            "MIOS_AI_ENDPOINT", "http://localhost:%s/v1" % _pipe_port)
        ai_model = os.environ.get("MIOS_AI_MODEL", "granite4.1:8b")
        tools = mios_gateway_queue.get_tools(ceiling="interactive")

        _GATEWAY_QUEUE = mios_gateway_queue.GatewayQueue(maxsize=q_maxsize)
        sys.modules["mios_chat"].GATEWAY_QUEUE = _GATEWAY_QUEUE
        _GATEWAY_WORKER = mios_gateway_queue.GatewayWorker(tools=tools, endpoint=ai_endpoint, model_name=ai_model, mcp_pool=_MCP_POOL)
        _GATEWAY_TASK = asyncio.create_task(_GATEWAY_WORKER.run(_GATEWAY_QUEUE, concurrency=w_concurrency))
        log.info("GatewayQueue + GatewayWorker started with maxsize=%d concurrency=%d", q_maxsize, w_concurrency)

    try:
        _psi_mon = get_psi_monitor()
        _psi_mon.register_throttle_callback(
            lambda throttled, evt: asyncio.create_task(_signal_inference_throttling(throttled, evt))
        )
        await _psi_mon.start()
        log.info("Linux PSI monitor started (available=%s, level=%s)", _psi_mon.available, _psi_mon.current_level)
    except Exception as _psi_err:
        log.warning("Linux PSI monitor startup exception: %s", _psi_err)

    yield

    try:
        _psi_mon = get_psi_monitor()
        await _psi_mon.stop()
    except Exception:
        pass

    if _GATEWAY_TASK:
        log.info("GatewayQueue shutting down...")
        _GATEWAY_TASK.cancel()
        try:
            await asyncio.wait_for(_GATEWAY_TASK, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

    if _MCP_POOL:
        log.info("MCP Client Pool shutting down...")
        await _MCP_POOL.shutdown()

    clients = list(_MCP_STDIO_CLIENTS.values())
    if clients:
        await asyncio.gather(*(c.close() for c in clients),
                             return_exceptions=True)

app = FastAPI(
    title="MiOS Agent Pipe",
    version="0.2.0",
    description=(
        "Gateway-agnostic router + dispatch + pgvector-state chain "
        "fronting hermes-agent."
    ),
    lifespan=lifespan,
)

def _check_user_cephfs(uid_str: str, tenant_id: str, fs_name: str, keyring_dir: str):
    import os
    import subprocess
    import json
    keyring_path = f"{keyring_dir}/client.{uid_str}"
    keyring_present = os.path.exists(keyring_path)

    subvolume_exists = False
    subvolume_path = ""

    try:
        cmd = ["ceph", "fs", "subvolume", "info", fs_name, f"{uid_str}-home", "--group_name", f"{tenant_id}-users", "--format", "json"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if proc.returncode == 0:
            subvolume_exists = True
            info = json.loads(proc.stdout)
            subvolume_path = info.get("path", "")
    except Exception:
        pass

    return {
        "uid": int(uid_str),
        "keyring_present": keyring_present,
        "subvolume_exists": subvolume_exists,
        "subvolume_path": subvolume_path
    }

@app.get("/v1/storage/cephfs/users")
async def cephfs_users():
    import os
    cephfs_enable = os.environ.get("MIOS_STORAGE_CEPHFS_ENABLE", "false").lower() in ("true", "1", "yes", "on")
    if not cephfs_enable:
        return {"enabled": False}

    tenant_id = os.environ.get("MIOS_STORAGE_CEPHFS_TENANT_ID", "mios")
    fs_name = os.environ.get("MIOS_STORAGE_CEPHFS_FS_NAME", "cephfs")
    keyring_dir = os.environ.get("MIOS_STORAGE_CEPHFS_KEYRING_DIR", "/etc/ceph/keyring.d")

    users = []
    if os.path.exists(keyring_dir):
        try:
            for name in os.listdir(keyring_dir):
                if name.startswith("client."):
                    uid_str = name.split(".", 1)[1]
                    if uid_str.isdigit():
                        info = _check_user_cephfs(uid_str, tenant_id, fs_name, keyring_dir)
                        users.append(info)
        except Exception:
            pass
    return users

@app.get("/v1/storage/cephfs/health")
async def cephfs_health():
    import os
    import subprocess
    import json
    cephfs_enable = os.environ.get("MIOS_STORAGE_CEPHFS_ENABLE", "false").lower() in ("true", "1", "yes", "on")
    if not cephfs_enable:
        return {"enabled": False}

    health_data = {"status": "UNKNOWN"}
    df_data = {}

    try:
        proc_h = subprocess.run(["ceph", "health", "--format", "json"], capture_output=True, text=True, timeout=5)
        if proc_h.returncode == 0:
            health_data = json.loads(proc_h.stdout)
    except Exception as e:
        log.warning("CephFS health probe failed: %s", e)
        health_data = {"status": "UNAVAILABLE", "error": "CephFS health probe unavailable"}

    try:
        proc_d = subprocess.run(["ceph", "df", "--format", "json"], capture_output=True, text=True, timeout=5)
        if proc_d.returncode == 0:
            df_data = json.loads(proc_d.stdout)
    except Exception:
        pass

    return {
        "health": health_data,
        "df": df_data
    }

@app.post("/v1/inference/lora/load")
async def lora_load(request: Request):
    heavy_engine = str(_toml_section("ai").get("heavy_engine") or "").strip().lower()
    if heavy_engine != "vllm":   # multi-LoRA is a vLLM engine feature of the heavy lane
        return JSONResponse(
            status_code=400,
            content={"error": "LoRA loading is only supported when [ai].heavy_engine is 'vllm'"}
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid JSON body"})

    lora_name = body.get("lora_name")
    lora_path = body.get("lora_path")
    if not lora_name or not lora_path:
        return JSONResponse(status_code=400, content={"error": "lora_name and lora_path are required"})

    url = f"{_TOOL_BACKEND_HEAVY}/load_lora_adapter"
    client = await _get_client()
    try:
        r = await client.post(url, json={"lora_name": lora_name, "lora_path": lora_path}, timeout=30.0)
        return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))
    except Exception as e:
        log.error("Failed to load LoRA adapter on heavy backend: %s", e)
        return JSONResponse(status_code=500, content={"error": "Failed to load LoRA adapter"})

@app.get("/v1/inference/lora/list")
async def lora_list():
    heavy_engine = str(_toml_section("ai").get("heavy_engine") or "").strip().lower()
    if heavy_engine != "vllm":
        return {"adapters": [], "enabled": False}

    url = f"{_TOOL_BACKEND_HEAVY}/models"
    client = await _get_client()
    try:
        r = await client.get(url, timeout=5.0)
        if r.status_code != 200:
            return {"adapters": [], "enabled": True}

        models_data = r.json()
        adapters = []
        for item in models_data.get("data") or []:
            if item.get("parent") or item.get("root"):
                adapters.append({
                    "id": item.get("id"),
                    "parent": item.get("parent") or item.get("root") or _TOOL_BACKEND_HEAVY_MODEL
                })
        return {"adapters": adapters, "enabled": True}
    except Exception as e:
        log.error("Failed to list LoRA adapters on heavy backend: %s", e)
        return {"adapters": [], "enabled": True}

@app.post("/v1/ast/diff")
async def ast_diff_endpoint(request: Request):
    """Compute AST structural diff across Python, Rust, Go, TypeScript, C (T-780)."""
    try:
        body = await request.json()
        orig = body.get("original", "")
        mod = body.get("modified", "")
        lang = body.get("language", "python")
        from mios_ast_diff import AstDiffEngine
        engine = AstDiffEngine()
        result = engine.compute_ast_diff(orig, mod, lang)
        return JSONResponse(result)
    except Exception as e:
        log.warning("AST diff request failed: %s", e)
        return JSONResponse(status_code=400, content={"error": "Invalid AST diff request"})

@app.post("/v1/ast/review")
async def ast_review_endpoint(request: Request):
    """2-peer review merge gating on structural diffs (T-780)."""
    try:
        body = await request.json()
        action = body.get("action", "create")
        from mios_ast_diff import global_gate
        if action == "create":
            orig = body.get("original", "")
            mod = body.get("modified", "")
            lang = body.get("language", "python")
            title = body.get("title", "")
            patch_id = body.get("patch_id")
            rec = global_gate.create_review(orig, mod, lang, title, patch_id)
            return JSONResponse(rec)
        elif action == "vote":
            review_id = body.get("review_id", "")
            role = body.get("role", "")
            decision = body.get("decision", "")
            comment = body.get("comment", "")
            rec = global_gate.submit_vote(review_id, role, decision, comment)
            return JSONResponse(rec)
        elif action == "status":
            review_id = body.get("review_id", "")
            rec = global_gate.get_review(review_id)
            return JSONResponse(rec)
        else:
            return JSONResponse(status_code=400, content={"error": f"Unknown action: {action}"})
    except Exception as e:
        log.warning("AST review request failed: %s", e)
        return JSONResponse(status_code=400, content={"error": "Invalid AST review request"})

from mios_pipe.kernel.httpclient import (   # noqa: E402  -- WS-A6/T-226 chokepoint
    _batch_request_hook, _get_client, configure as _configure_httpclient)

import mios_audit   # noqa: E402
AUDIT_CHAIN_ENABLE = str(
    os.environ.get("MIOS_AUDIT_CHAIN_ENABLE")
    or _toml_section("audit").get("chain_enable", "true")).strip().lower() \
    in {"1", "true", "yes"}

from mios_pipe.db import (
    client as _db_client,
    post as _db_post,
    read as _db_read,
    update as _db_update,
    configure as _configure_db,
)
from mios_pipe.dbwrite import (
    _db_create,
    _db_fire,
    _pg_mirror,
    _db_write,
    configure as _configure_dbwrite,
)
from mios_pipe.observability.session_events import (
    configure as _configure_session_events,
)

_configure_db(
    pg_primary=globals().get("_PG_PRIMARY", False),
    mios_pg=globals().get("_mios_pg"),
    db_ns=globals().get("DB_NS"),
    db_db=globals().get("DB_DB"),
    db_url=globals().get("DB_URL"),
    db_auth=globals().get("_DB_AUTH"),
)

_configure_dbwrite(
    pg_enabled=globals().get("_PG_ENABLED", True),
    pg_primary=globals().get("_PG_PRIMARY", False),
    current_trace_id=globals().get("_current_trace_id"),
    span_id_var=globals().get("_span_id_var"),
    passport_sign=globals().get("_passport_sign"),
    db_post=globals().get("_db_post"),
)

_configure_session_events(
    pg_mirror=_pg_mirror,
    db_create=_db_create,
    db_fire=_db_fire,
    db_post=_db_post,
)

sys.modules["mios_dci"].configure(
    db_post=_db_post,
    db_create=_db_create,
    db_fire=_db_fire,
    apply_outbound_auth=_apply_outbound_auth,
)

VRAM_CHECKPOINT_ENABLE = os.environ.get(
    "MIOS_VRAM_CHECKPOINT", "true").lower() not in {"false", "0", "no"}
VRAM_BUDGET_MB = int(os.environ.get("MIOS_VRAM_BUDGET_MB", "23000"))
VRAM_TURN_HEADROOM_MB = int(os.environ.get("MIOS_VRAM_TURN_HEADROOM_MB", "16000"))
VRAM_COLOAD_ENABLE = os.environ.get(
    "MIOS_VRAM_COLOAD", "true").lower() not in {"false", "0", "no"}
VRAM_COLOAD_RESERVE_MB = _dispatch_num(
    "MIOS_VRAM_COLOAD_RESERVE_MB", "vram_coload_reserve_mb", 3000)
VRAM_COLOAD_EST_MB = _dispatch_num(
    "MIOS_VRAM_COLOAD_EST_MB", "vram_coload_est_mb", 5000)

from mios_pipe.scheduler.vram import (
    _checkpoint_keep_models,
    _engine_resident,
    _norm_model_tag,
    _host_stats_cached,
    _resident_cached,
    _over_global_ceiling,
    _blade_vram_budget,
    _over_blade_ceiling,
    _is_warm,
    _engine_unload,
    _vram_checkpoint,
    _model_active,
    _model_is_active,
    _dispatch_priority,
    _reclaim_idle_vram,
    configure as _configure_sched_vram,
)

_orig_over_global_ceiling = _over_global_ceiling
_orig_host_stats_cached = _host_stats_cached

def _over_global_ceiling(load_ceil: Optional[float] = None) -> bool:
    """T-485: True when host load/mem is over ceiling OR Linux PSI critical pressure is reached."""
    try:
        mon = get_psi_monitor()
        if mon and mon.is_critical():
            return True
    except Exception:
        pass
    return _orig_over_global_ceiling(load_ceil)

def _host_stats_cached(ttl: float = 1.0) -> dict:
    """T-485: Injects Linux PSI telemetry into host stats."""
    stats = _orig_host_stats_cached(ttl)
    try:
        mon = get_psi_monitor()
        if mon:
            stats["psi"] = mon.get_metrics_dict()
    except Exception:
        pass
    return stats

_configure_vram_scheduler(
    log=log,
    _toml_section=_toml_section,
    _DISPATCH_TOML=_DISPATCH_TOML,
    AGENT_CONCURRENCY=AGENT_CONCURRENCY,
    ENDPOINT_CONCURRENCY=ENDPOINT_CONCURRENCY,
    SLO_SHED_ENABLE=SLO_SHED_ENABLE,
    ADMIT_ENABLE=ADMIT_ENABLE,
    ADMIT_MAX_WAIT=ADMIT_MAX_WAIT,
    MULTIBLADE_ENABLE=MULTIBLADE_ENABLE,
    _over_blade_ceiling=_over_blade_ceiling,
    _over_global_ceiling=_over_global_ceiling,
    _is_warm=_is_warm,
    _blade_vram_budget=_blade_vram_budget,
    VRAM_BUDGET_MB=VRAM_BUDGET_MB,
    _resident_cached=_resident_cached,
    _norm_model_tag=_norm_model_tag,
    VRAM_COLOAD_EST_MB=VRAM_COLOAD_EST_MB,
    VRAM_COLOAD_ENABLE=VRAM_COLOAD_ENABLE,
    VRAM_COLOAD_RESERVE_MB=VRAM_COLOAD_RESERVE_MB,
    _reclaim_idle_vram=_reclaim_idle_vram,
    _dispatch_num=_dispatch_num,
)


_OFFLOAD_ENGINES = ("cpu", "igpu", "accelerator")  # local light lanes, off the dGPU

_CPU_LANE_HINTS = tuple(h.strip() for h in os.environ.get(
    "MIOS_CPU_LANE_HINTS",
    str(_DISPATCH_TOML.get("cpu_lane_hints", "8458,8450"))).split(",")
    if h.strip())
_CPU_LANE_MICRO_MODEL = (os.environ.get("MIOS_CPU_LANE_MICRO_MODEL")
                         or str(_DISPATCH_TOML.get("cpu_lane_micro_model", "granite4.1:8b")))  # qwen3:1.7b retired

def _cap_cpu_lane_model(ep: str, model: str) -> str:
    _local = ("localhost" in (ep or "")) or ("127.0.0.1" in (ep or ""))
    if (_local and _CPU_LANE_MICRO_MODEL
            and any(h and h in (ep or "") for h in _CPU_LANE_HINTS)):
        return _CPU_LANE_MICRO_MODEL
    return model

def _is_slow_lane_ep(ep: str) -> bool:
    """True for a CPU/iGPU light-lane endpoint (same _CPU_LANE_HINTS the model-cap
    uses): local CPU :11435, the remote potato CPU (…:11435) and the Windows iGPU
    :11436 all match; the dGPU and remote GPU lanes do not."""
    return bool(ep) and any(h and h in ep for h in _CPU_LANE_HINTS)

def _agent_binding(cfg: dict, engine: Optional[str] = None) -> tuple:
    """Resolve (endpoint, model) to run an agent on a SPECIFIC engine. With
    engine=None, or no binding for that engine, fall back to the agent's default
    endpoint/model -- so this never strands a dispatch. A light-lane (CPU/iGPU)
    endpoint is force-capped to the micro model (_cap_cpu_lane_model)."""
    if engine:
        b = (cfg.get("engines") or {}).get(str(engine).lower().strip())
        if isinstance(b, dict) and b.get("endpoint"):
            _ep = str(b["endpoint"]).rstrip("/")
            return (_ep, _cap_cpu_lane_model(
                _ep, str(b.get("model") or cfg.get("model", ""))))
    _ep = str(cfg.get("endpoint", "")).rstrip("/")
    if not _ep:
        _ep = BACKEND.rstrip("/")
    return _ep, _cap_cpu_lane_model(_ep, str(cfg.get("model", "")))

DISPATCH_OFFLOAD_CPU = str(os.environ.get("MIOS_DISPATCH_OFFLOAD_CPU")
                          or _DISPATCH_TOML.get("offload_cpu", "false")
                          ).strip().lower() in {"1", "true", "yes"}

from mios_endpoints import (  # noqa: E402
    _binding_api,
    _NO_TOOL_CHOICE_API,
    _NO_TOOL_CHOICE_HINTS,
    _endpoint_supports_tool_choice,
    _PARALLEL_TOOLS_HINTS,
    _endpoint_supports_parallel_tools,
    _LLAMACPP_API,
    _KV_PAGING_HINTS,
    _endpoint_is_llamacpp,
)

KV_PAGING_ENABLE = (
    str(os.environ.get("MIOS_KV_PAGING")
        or _DISPATCH_TOML.get("kv_paging_enable", "true"))
    .strip().lower() not in {"false", "0", "no", "off"})
KV_PAGING_SLOT = _dispatch_num("MIOS_KV_PAGING_SLOT", "kv_paging_slot", 0)
KV_PAGING_TIMEOUT = _dispatch_num(
    "MIOS_KV_PAGING_TIMEOUT", "kv_paging_timeout", 12.0, cast=float)
_KV_RESIDENT: dict = {}
_KV_LOCKS: dict = {}
KV_FORK_ENABLE = (
    str(os.environ.get("MIOS_KV_FORK")
        or _DISPATCH_TOML.get("kv_fork_enable", "false"))
    .strip().lower() not in {"false", "0", "no", "off", ""})
KV_FORK_MAX_BRANCHES = _dispatch_num("MIOS_KV_FORK_MAX_BRANCHES", "kv_fork_max_branches", 4)
KV_GC_ENABLE = (
    str(os.environ.get("MIOS_KV_GC")
        or _DISPATCH_TOML.get("kv_gc_enable", "true"))
    .strip().lower() not in {"false", "0", "no", "off", ""})
KV_GC_INTERVAL_S = _dispatch_num("MIOS_KV_GC_INTERVAL_S", "kv_gc_interval_s", 900, cast=float)
KV_GC_TTL_S = _dispatch_num("MIOS_KV_GC_TTL_S", "kv_gc_ttl_s", 86400, cast=float)
KV_GC_MAX_BYTES = _dispatch_num("MIOS_KV_GC_MAX_BYTES", "kv_gc_max_bytes", 2000000000)
KV_SLOTS_DIR = (os.environ.get("MIOS_KV_SLOTS_DIR", "")
                or str(_DISPATCH_TOML.get("kv_slots_dir", "") or "")).strip()
RR_ENABLE = (
    str(os.environ.get("MIOS_RR_ENABLE")
        or _DISPATCH_TOML.get("rr_enable", "false"))
    .strip().lower() not in {"false", "0", "no", "off", ""})
RR_QUANTUM_S = _dispatch_num("MIOS_RR_QUANTUM_S", "rr_quantum_s", 8.0, cast=float)
RR_MAX_SUSPENDED = _dispatch_num("MIOS_RR_MAX_SUSPENDED", "rr_max_suspended", 4)
RR_SLICE_TOKENS = _dispatch_num("MIOS_RR_SLICE_TOKENS", "rr_slice_tokens", 512)
RR_SLICE_TIMEOUT = _dispatch_num("MIOS_RR_SLICE_TIMEOUT_S", "rr_slice_timeout_s", 120.0, cast=float)
_PREEMPT = mios_preempt.PreemptScheduler(max_suspended=RR_MAX_SUSPENDED)
mios_preempt.configure(head_priority=_GLOBAL_PRIORITY_GATE.head_priority)
BATCH_ENABLE = (
    str(os.environ.get("MIOS_BATCH_ENABLE")
        or _DISPATCH_TOML.get("batch_enable", "false"))
    .strip().lower() not in {"false", "0", "no", "off", ""})
BATCH_INTERVAL_S = _dispatch_num("MIOS_BATCH_INTERVAL_S", "batch_interval_s", 0.05, cast=float)
BATCH_MAX_SIZE = _dispatch_num("MIOS_BATCH_MAX_SIZE", "batch_max_size", 8)
BATCH_NATIVE_HINTS = [h.strip() for h in str(
    os.environ.get("MIOS_BATCH_NATIVE_HINTS")
    or _DISPATCH_TOML.get("batch_native_hints", "")).split(",") if h.strip()]
_configure_httpclient(batch_enable=BATCH_ENABLE, batch_interval_s=BATCH_INTERVAL_S,
                      batch_max_size=BATCH_MAX_SIZE, batch_native_hints=BATCH_NATIVE_HINTS)
SMARTROUTE_ENABLE = str(os.environ.get("MIOS_SMARTROUTE_ENABLE", "")).strip().lower() \
    in ("1", "true", "yes", "on")
SMARTROUTE_BUDGET = float(os.environ.get("MIOS_SMARTROUTE_BUDGET", "0") or 0)
_COST_CFG = _toml_section("cost") or {}
COST_ACCOUNTING_ENABLE = (
    str(os.environ.get("MIOS_COST_ACCOUNTING_ENABLE")
        or _COST_CFG.get("enable", "false"))
    .strip().lower() not in {"false", "0", "no", "off", ""})
COST_BUDGET_USD = float(_COST_CFG.get("budget_usd", 0.0) or 0.0)
_COST_MODEL = mios_cost.CostModel(
    gpu_watts=float(_COST_CFG.get("gpu_watts", 350.0) or 350.0),
    usd_per_kwh=float(_COST_CFG.get("usd_per_kwh", 0.0) or 0.0),
    remote_usd_per_mtok=float(_COST_CFG.get("remote_usd_per_mtok", 0.0) or 0.0))
_COST_LEDGER = mios_cost.CostLedger()

SANDBOX_ENFORCE = (
    str(os.environ.get("MIOS_SANDBOX_ENFORCE")
        or _DISPATCH_TOML.get("sandbox_enforce", "false"))
    .strip().lower() not in {"false", "0", "no", "off", ""})
_SANDBOX_SELF_CONFINED = ("mios-sandbox-exec", "mios-coderun")

from mios_provider_translate import (  # noqa: E402
    ANTH_REJECT_KEYS as _ANTH_REJECT_KEYS,
    GEMINI_DROP_KEYS as _GEMINI_DROP_KEYS,
    scrub_schema as _scrub_schema,
    oai_tools_to_anthropic as _oai_tools_to_anthropic,
    oai_tools_to_gemini as _oai_tools_to_gemini,
    args_obj as _args_obj,
    oai_msgs_to_anthropic as _oai_msgs_to_anthropic,
    anthropic_resp_to_oai as _anthropic_resp_to_oai,
    oai_msgs_to_gemini as _oai_msgs_to_gemini,
    gemini_resp_to_oai as _gemini_resp_to_oai,
)

def _opt_int_mb(v) -> int:
    """Coerce an optional [nodes.*] vram_mb / ram_mb to int MB; 0 when unset/bad
    (0 = 'unknown' -> per-endpoint admission falls back to the flat estimate)."""
    try:
        return int(float(v)) if v is not None and str(v).strip() != "" else 0
    except Exception:  # noqa: BLE001
        return 0

from mios_agentreg import (  # noqa: E402
    _build_agent_engines,
    _load_agent_registry,
    _load_node_pool,
    _agent_lane,
    _render_agent_catalog,
    _role_system,
    _dedup_pool_by_target,
)
sys.modules["mios_agentreg"].configure(
    is_remote_endpoint=_is_remote_endpoint,
    opt_int_mb=_opt_int_mb,
    logger=log,
    catalog_fail_mode=CATALOG_FAIL_MODE,
    nodes_research_only=_vram_scheduler.NODES_RESEARCH_ONLY,
)

_AGENT_REGISTRY = _load_agent_registry()
try:
    _load_node_pool(_AGENT_REGISTRY)
except Exception as _e:  # noqa: BLE001 -- never block startup on the node pool
    log.warning("node pool injection failed: %s", _e)

_LOCAL_BLADE = ""
_BLADE_POOL: dict = {}
_ENDPOINT_BLADE: dict = {}

def _rebuild_blade_topology() -> None:
    """(Re)build the V4/V5 blade maps from the current registry + [blades.*] SSOT."""
    global _LOCAL_BLADE, _BLADE_POOL, _ENDPOINT_BLADE
    try:
        _LOCAL_BLADE = mios_blades.local_blade_name()
        _BLADE_POOL = mios_blades.load_blade_pool(
            _LOCAL_BLADE, VRAM_BUDGET_MB, ADMIT_LOAD_CEIL)
        _ENDPOINT_BLADE = mios_blades.endpoint_blade_map(
            _AGENT_REGISTRY, _endpoint_key, _LOCAL_BLADE)
        _configure_sched_vram(
            blade_pool=_BLADE_POOL,
            endpoint_blade=_ENDPOINT_BLADE,
            local_blade=_LOCAL_BLADE,
        )
    except Exception as _e:  # noqa: BLE001 -- the admission helpers already degrade-open
        log.warning("blade topology build failed: %s; local-scalar fallback", _e)

_rebuild_blade_topology()

_configure_sched_vram(
    lane_priority=_vram_scheduler._LANE_PRIORITY,
    agent_lane=_agent_lane,
    host_stats=_host_stats_cached,
    endpoint_key=_endpoint_key,
    blade_pool=_BLADE_POOL,
    endpoint_blade=_ENDPOINT_BLADE,
    local_blade=_LOCAL_BLADE,
    vram_budget_mb=VRAM_BUDGET_MB,
    vram_turn_headroom_mb=VRAM_TURN_HEADROOM_MB,
    admit_load_ceil=ADMIT_LOAD_CEIL,
    admit_mem_pct=ADMIT_MEM_PCT,
    multiblade_enable=MULTIBLADE_ENABLE,
    vram_reclaim_idle=_vram_scheduler.VRAM_RECLAIM_IDLE,
)

def _load_dispatch_cfg() -> dict:
    cfg = {"enable": True, "fanout_min": 1, "fanout_max": 2,
           "mode": "relevance"}
    try:
        dd = _dispatch_toml()
        cfg["enable"] = bool(dd.get("enable", True))
        cfg["fanout_min"] = max(1, int(dd.get("fanout_min", 1)))
        cfg["fanout_max"] = max(cfg["fanout_min"], int(dd.get("fanout_max", 2)))
        cfg["mode"] = str(dd.get("mode", "relevance")).lower().strip() \
            or "relevance"
    except Exception as e:
        log.warning("dispatch cfg load failed: %s; using defaults", e)
    try:
        cfg["fanout_max"] = max(1, int(
            os.environ.get("MIOS_DISPATCH_FANOUT_MAX", cfg["fanout_max"])))
    except (TypeError, ValueError):
        pass
    cfg["mode"] = os.environ.get("MIOS_DISPATCH_MODE", cfg["mode"]).lower().strip() \
        or "relevance"
    return cfg

_DISPATCH_CFG = _load_dispatch_cfg()

sys.modules["mios_sched"].configure(
    LANE_TOOL_CAP=LANE_TOOL_CAP,
    SLOW_LANES=SLOW_LANES,
    SLOW_LANE_TOOL_CAP=SLOW_LANE_TOOL_CAP,
    DEFAULT_TOOL_CAP=DEFAULT_TOOL_CAP,
    DISPATCH_OFFLOAD_CPU=DISPATCH_OFFLOAD_CPU,
    _OFFLOAD_ENGINES=_OFFLOAD_ENGINES,
    _agent_lane=_agent_lane,
)

from mios_promptfmt import (  # noqa: E402  (pure prompt text-block formatters, moved verbatim)
    _council_role_lens,
    _format_satisfaction_block,
    _format_tool_history,
    _build_agent_hint,
    _multi_task_preamble,
)

def _agent_skill_tags(cfg: dict) -> list[str]:
    """Canonical skill tags for an agent: role + inference lane + declared
    strengths. SINGLE SSOT shared by the A2A AgentCard (publish side ->
    skill.tags) and _pick_fanout_agents (consume side -> routing key) so an
    agent's advertised capabilities and the key the orchestrator routes on
    can never drift. Clean human/agent-facing labels (NOT snake_case-split);
    the router expands sub-tokens for matching internally."""
    tags = {
        str(cfg.get("role", "general")).lower().strip(),
        _agent_lane(cfg),
    }
    for s in (cfg.get("strengths") or []):
        s = str(s).lower().strip()
        if s:
            tags.add(s)
    return sorted(t for t in tags if t)

from mios_fanout import _pick_fanout_agents  # noqa: E402

_AGENT_CHROME_RE = re.compile(
    r"^[ \t]*>?[ \t]*\w{2,10}[ \t]*·[ \t]*[\w./:+-]{2,}[ \t]*$",
    re.MULTILINE)

def _strip_agent_chrome(text: str) -> str:
    """Remove a sub-agent's leaked mode/model chrome line(s) from its output.
    Structural + idempotent; returns the original unchanged if nothing matched."""
    if not text:
        return text
    stripped = _AGENT_CHROME_RE.sub("", text)
    return stripped.strip() if stripped.strip() != text.strip() else text

from mios_agent_call import (  # noqa: E402
    _call_agent_complete, _call_agent_complete_inner, _call_agent_stream_inner,
    _record_cost,
    _kv_base, _kv_filename, _kv_lock, _kv_slot_action, _kv_paging, _kv_fork,
    _rr_eligible, _rr_slice, _rr_run,
    _trip_breaker, _num_predict_cap_for)

from mios_toolexec import (   # noqa: E402
    _RESCUE_FENCE_RE, _RESCUE_TOOLCALL_RE)

from mios_toolexec import (   # noqa: E402
    _norm_tool_call, _rescue_tool_calls, _verb_result_cap,
    _cap_verb_result, _format_tool_error, _exec_tool_calls,
    _record_mcp_tool_call, _allowed_tool_names)

from mios_secondary_loop import _tool_call_sig  # noqa: E402

from mios_secondary_loop import _DISCLAIM_MARKERS, _looks_like_disclaimer  # noqa: E402

from mios_secondary_loop import _TOOL_NUDGE  # noqa: E402

SECONDARY_REPLAN_MAX = int(os.environ.get("MIOS_SECONDARY_REPLAN_MAX", "") or _AGENT_PIPE_TOML.get("replan_max", 5))
DAG_REPLAN_MAX = int(os.environ.get("MIOS_DAG_REPLAN_MAX", "1") or 1)
from mios_secondary_loop import _REPLAN_NUDGE  # noqa: E402

from mios_secondary_loop import _tmsgs_indicate_failure  # noqa: E402

_DAEMON_DIAGNOSE_MODEL = os.environ.get("MIOS_DAEMON_MODEL", _STACK_MODEL)
_DAEMON_DIAGNOSE_ENDPOINT = os.environ.get(
    "MIOS_DAEMON_ENDPOINT", _LIGHT_BASE + "/v1").rstrip("/")
_DAEMON_DIAGNOSE_ENABLE = os.environ.get(
    "MIOS_DAEMON_DIAGNOSE", "true").strip().lower() not in ("0", "false", "no")

from mios_secondary_loop import _daemon_diagnose  # noqa: E402

from mios_secondary_loop import _v1_secondary_tool_loop  # noqa: E402

from mios_pipe.streaming import (
    call_agent_stream as _call_agent_stream,
    configure as _configure_streaming
)

_configure_streaming(
    _agent_offload_engine=_agent_offload_engine,
    _agent_binding=_agent_binding,
    _dispatch_priority=_dispatch_priority,
    _opt_int_mb=_opt_int_mb,
    _admit=_admit,
    _SloShed=_SloShed,
    _priority_gate=_priority_gate,
    _endpoint_sem=_endpoint_sem,
    _lane_sem=_lane_sem,
    _lane_sem_key=_lane_sem_key,
    _model_active=_model_active,
    _call_agent_stream_inner=_call_agent_stream_inner,
    _strip_agent_chrome=_strip_agent_chrome,
)

from mios_verbcatalog import (  # noqa: E402
    _load_verb_catalog,
    _verb_arg_synonyms_from_catalog,
    _render_verb_catalog,
    _identity_answer,
    _load_verb_arg_synonyms,
    _build_model_name_map,
    _resolve_verb_key,
    _load_recipe_catalog,
    _render_recipe_catalog,
    _recipe_to_openai_tool,
    _verb_to_openai_tool,
)
sys.modules["mios_verbcatalog"].configure(CATALOG_FAIL_MODE=CATALOG_FAIL_MODE)

NATIVE_LOOP_CAPABILITY_GROUNDING = os.environ.get(
    "MIOS_NATIVE_LOOP_CAPABILITY_GROUNDING", "true").strip().lower() not in (
        "0", "false", "no")
from mios_grounding import (   # noqa: E402
    _current_year,
    NATIVE_LOOP_CAPABILITY_PER_SECTION,
    _capability_grounding,
    _temporal_grounding,
    _CACHED_OS_INFO,
    _get_os_info,
    _HOST_TZ,
    _host_timezone,
    _client_grounding,
    _identity_guard,
    _arch_grounding,
    _env_block,
    _env_grounding,
    _OWUI_VAR_KEYS,
    _ENV_SENTINELS,
    _client_env,
)

_VERB_CATALOG = _load_verb_catalog()
_TOOL_CONFLICT = mios_toolconflict.ConflictGate.from_catalog(_VERB_CATALOG)

_MODEL_NAME_TO_VERB = _build_model_name_map(_VERB_CATALOG)

sys.modules["mios_verbcatalog"].configure(
    _VERB_CATALOG=_VERB_CATALOG, _MODEL_NAME_TO_VERB=_MODEL_NAME_TO_VERB)

_VERB_ARG_SYNONYMS = _load_verb_arg_synonyms()
_VERB_CATALOG_RENDERED = _render_verb_catalog(_VERB_CATALOG)

from mios_routing import (  # noqa: E402
    _load_routing_domains,
    _load_routing_phrases,
    _load_launch_fillers,
    _deterministic_action_route,
)

_ROUTING_DOMAINS, _ROUTING_ENABLE = _load_routing_domains()

from mios_classify import classify_intent, _route_domain  # noqa: E402
sys.modules["mios_classify"].configure(
    verb_catalog=_VERB_CATALOG,
    routing_domains=_ROUTING_DOMAINS,
    routing_enable=_ROUTING_ENABLE,
    db_create=_db_create,
    db_post=_db_post,
    db_fire=_db_fire,
)

_LAUNCH_FILLERS = _load_launch_fillers()
_LAUNCH_LEAD_WORDS = frozenset(_load_routing_phrases("launch_target_lead_phrases"))
_LAUNCH_TRAIL_WORDS = frozenset(_load_routing_phrases("launch_target_trail_phrases"))
_REMEMBER_TRIGGERS = _load_routing_phrases("remember_trigger_phrases")
_WEB_SEARCH_TRIGGERS = _load_routing_phrases("web_search_trigger_phrases")
_WEB_SEARCH_CONTEXTS = _load_routing_phrases("web_search_trigger_contexts")
_LOCATION_SENSITIVE_PHRASES = _load_routing_phrases("location_sensitive_phrases")
_BROWSER_ACTION_VERBS = _load_routing_phrases("browser_action_verbs")
_BROWSER_ACTION_ALT = "|".join(re.escape(p) for p in _BROWSER_ACTION_VERBS)
_COMPOUND_CONJUNCTIONS = _load_routing_phrases("compound_conjunctions")
_COMPOUND_ACTIONS = _load_routing_phrases("compound_actions")
_COMPOUND_CONNECTIVES = _load_routing_phrases("compound_connectives")
_COMPOUND_CONJ_ALT = "|".join(re.escape(p) for p in _COMPOUND_CONJUNCTIONS)
_COMPOUND_ACTION_ALT = "|".join(re.escape(p) for p in _COMPOUND_ACTIONS)
_COMPOUND_CONNECTIVE_ALT = "|".join(re.escape(p) for p in _COMPOUND_CONNECTIVES)

_OS_CONTROL_SECTION = os.environ.get(
    "MIOS_OS_CONTROL_SECTION", "Window / app launch")
_OS_CONTROL_VERBS = frozenset(
    name for name, cfg in _VERB_CATALOG.items()
    if str(cfg.get("section", "")) == _OS_CONTROL_SECTION
)
_SCHEDULE_SECTION = os.environ.get("MIOS_SCHEDULE_SECTION", "Automation / scheduling")
_SCHEDULE_VERBS = frozenset(
    name for name, cfg in _VERB_CATALOG.items()
    if str(cfg.get("section", "")) == _SCHEDULE_SECTION
)
_MEMORY_VERBS = {"remember", "recall", "memory", "memory_append", "memory_replace", "memory_update", "memory_forget"}
_PC_INPUT_SECTION = os.environ.get("MIOS_PC_INPUT_SECTION", "PC input")
_PC_INPUT_VERBS = frozenset(
    name for name, cfg in _VERB_CATALOG.items()
    if str(cfg.get("section", "")) == _PC_INPUT_SECTION
)
_FASTPATH_VERBS = _OS_CONTROL_VERBS | _SCHEDULE_VERBS | _MEMORY_VERBS | _PC_INPUT_VERBS

from mios_oscontrol import (   # noqa: E402  (R9: OS-control fast-path + window verify, moved verbatim)
    _OSCONTROL_ENDPOINTS_CACHE, _load_oscontrol_endpoints,
    _remote_enumerate_windows_one, _enumerate_windows, _window_key,
    _window_diff, _win_titles, _window_delta_text, _index_window_event,
    _os_target, _win_hay, _center_windows, _launch_proc_patterns,
    _proc_present, _verify_os_action, _LAST_OPENED_WINDOW,
    _LAST_OPENED_WINDOW_CAP, _record_last_opened_window, _respond_os_control,
    _render_os_control_verbs,
)
sys.modules["mios_oscontrol"].configure(
    fastpath_verbs=_FASTPATH_VERBS, verb_catalog=_VERB_CATALOG)

_OS_CONTROL_VERBS_RENDERED = _render_os_control_verbs()

_OS_CONTROL_ACTION_VERBS = frozenset(
    name for name in _OS_CONTROL_VERBS
    if str((_VERB_CATALOG.get(name) or {}).get("permission", "")).lower() == "write"
)
_LAUNCH_VERBS = frozenset({"open_app", "launch_app", "launch_verified", "open_url"})

_LAUNCH_TRIGGERS = frozenset(
    v.split("_", 1)[0] for v in _LAUNCH_VERBS if v in _FASTPATH_VERBS)

sys.modules["mios_routing"].configure(
    logger=log,
    compound_action_alt=_COMPOUND_ACTION_ALT,
    compound_connective_alt=_COMPOUND_CONNECTIVE_ALT,
    fastpath_verbs=_FASTPATH_VERBS,
    launch_triggers=_LAUNCH_TRIGGERS,
    launch_fillers=_LAUNCH_FILLERS,
    launch_lead_words=_LAUNCH_LEAD_WORDS,
    launch_trail_words=_LAUNCH_TRAIL_WORDS,
)

OS_CONTROL_RETRY_ATTEMPTS = int(os.environ.get("MIOS_OS_CONTROL_RETRY", "2") or 2)
OS_CONTROL_RETRY_SETTLE_S = float(
    os.environ.get("MIOS_OS_CONTROL_RETRY_SETTLE_S", "1.2") or 1.2)
OS_CONTROL_LAUNCH_VERIFY_S = float(
    os.environ.get("MIOS_OS_CONTROL_LAUNCH_VERIFY_S", "16") or 16)
OS_CONTROL_LAUNCH_POLL_S = float(
    os.environ.get("MIOS_OS_CONTROL_LAUNCH_POLL_S", "1.5") or 1.5)
OS_CONTROL_ENUM_RETRY = int(os.environ.get("MIOS_OS_CONTROL_ENUM_RETRY", "2") or 2)
OS_CONTROL_ENUM_RETRY_SETTLE_S = float(
    os.environ.get("MIOS_OS_CONTROL_ENUM_RETRY_SETTLE_S", "0.7") or 0.7)
OS_CONTROL_ENUM_TIMEOUT_S = float(
    os.environ.get("MIOS_OS_CONTROL_ENUM_TIMEOUT_S", "6") or 6)
TYPE_RETRY_MAX = int(os.environ.get("MIOS_TYPE_RETRY_MAX", "2") or 2)
OS_CONTROL_REPLY_MAX_TOKENS = int(
    os.environ.get("MIOS_OS_CONTROL_REPLY_MAX_TOKENS", "200"))

_RECIPE_CATALOG = _load_recipe_catalog()
_RECIPE_CATALOG_RENDERED = _render_recipe_catalog(_RECIPE_CATALOG)

_AGENT_CATALOG_RENDERED = _render_agent_catalog(_AGENT_REGISTRY)

_AGENT_CONTRACT_PATHS = (
    os.path.expanduser("~/.config/mios/MiOS.md"),
    "/etc/mios/MiOS.md",
    "/MiOS.md",
    os.path.expanduser("~/.config/mios/ai/agent-contract.md"),
    "/etc/mios/ai/agent-contract.md",
    "/usr/share/mios/ai/agent-contract.md",
)

def _load_agent_contract() -> str:
    for _p in _AGENT_CONTRACT_PATHS:
        try:
            with open(_p, "r", encoding="utf-8") as _f:
                _txt = _f.read().strip()
            if _txt:
                _body = "\n".join(
                    ln for ln in _txt.splitlines()
                    if not ln.lstrip().startswith(">")).strip()
                return _body or _txt
        except (OSError, UnicodeDecodeError):
            continue
    return ""

_AGENT_CONTRACT = _load_agent_contract()

def _agent_contract() -> str:
    """The universal runtime contract presented to EVERY agent + sub-agent.
    Empty string when the overlay .md is missing (degrade open)."""
    return _AGENT_CONTRACT

_ROLE_SYSTEM_DIR = "/etc/mios/ai/v1/role-systems"

sys.modules["mios_agentreg"].configure(
    agent_registry=_AGENT_REGISTRY,
    agent_binding=_agent_binding,
    endpoint_key=_endpoint_key,
    role_system_dir=_ROLE_SYSTEM_DIR,
    effort_default=EFFORT_DEFAULT,
    swarm_max_width=SWARM_MAX_WIDTH,
)

WORKER_TOOLS_ENABLE = os.environ.get(
    "MIOS_WORKER_TOOLS", "true").lower() not in {"false", "0", "no"}
WORKER_TOOLS_SCOPE = os.environ.get("MIOS_WORKER_TOOLS_SCOPE", "all").strip().lower()
WORKER_TOOL_CTX = int(os.environ.get("MIOS_WORKER_TOOL_CTX", "16384") or 16384)
WORKER_TOOL_CTX_SLOW = int(os.environ.get("MIOS_WORKER_TOOL_CTX_SLOW", "6144") or 6144)
WORKER_TOOL_CTX_MAX = int(os.environ.get("MIOS_WORKER_TOOL_CTX_MAX", str(_DISPATCH_TOML.get("worker_tool_ctx_max", 24576))) or 24576)
CHILD_TOOL_SELECT = (os.environ.get("MIOS_CHILD_TOOL_SELECT") or str(_DISPATCH_TOML.get("child_tool_select", True))).strip().lower() not in {"false", "0", "no"}
CTX_FIT = (os.environ.get("MIOS_CTX_FIT") or str(_DISPATCH_TOML.get("ctx_fit", True))).strip().lower() not in {"false", "0", "no"}
CHILD_TOOL_FLOOR = int(os.environ.get("MIOS_CHILD_TOOL_FLOOR", str(_DISPATCH_TOML.get("child_tool_floor", 6))) or 6)
_WORKER_TOOLS_CACHE: "Optional[list]" = None
_WORKER_TOOLS_FULL_CACHE: "Optional[list]" = None
STABLE_PREFIX = (os.environ.get("MIOS_STABLE_TOOL_PREFIX")
                 or str(_DISPATCH_TOML.get("stable_tool_prefix", False))
                 ).strip().lower() not in {"false", "0", "no"}
STABLE_PREFIX_TAIL = int(os.environ.get("MIOS_STABLE_PREFIX_TAIL",
                         str(_DISPATCH_TOML.get("stable_prefix_tail", 10))) or 10)
STABLE_PREFIX_HINT = (os.environ.get("MIOS_STABLE_PREFIX_HINT")
                      or str(_DISPATCH_TOML.get("stable_prefix_hint", False))
                      ).strip().lower() not in {"false", "0", "no"}
TOOL_RERANK = (os.environ.get("MIOS_TOOL_RERANK")
               or str(_DISPATCH_TOML.get("tool_rerank", True))
               ).strip().lower() not in {"false", "0", "no"}
RERANK_FANOUT = int(os.environ.get("MIOS_RERANK_FANOUT",
                    str(_DISPATCH_TOML.get("rerank_fanout", 3))) or 3)   # over-fetch K = fanout*N
RERANK_MIN_K = int(os.environ.get("MIOS_RERANK_MIN_K",
                   str(_DISPATCH_TOML.get("rerank_min_k", 24))) or 24)   # floor on the window
RERANK_RRF_K = int(os.environ.get("MIOS_RERANK_RRF_K",
                   str(_DISPATCH_TOML.get("rerank_rrf_k", 60))) or 60)   # RRF constant (web-search uses 60)
RERANK_MMR_LAMBDA = max(0.0, min(1.0, float(os.environ.get("MIOS_RERANK_MMR_LAMBDA",
                       str(_DISPATCH_TOML.get("rerank_mmr_lambda", 0.8))) or 0.8)))  # relevance vs diversity (0.8 = no recall regression in eval)
RERANK_SKIP_MARGIN = float(os.environ.get("MIOS_RERANK_SKIP_MARGIN",
                       str(_DISPATCH_TOML.get("rerank_skip_margin", 0.08))) or 0.08)  # confident-cut skip
from mios_worker_tools import (   # noqa: E402
    _tool_priority,
    _priority_fallback_score,
    _is_core_tool,
    _stable_name,
    _tok,
    _ensure_verb_lexicon,
    _bm25,
    _rank_positions,
    _fuse_then_diversify,
    _VERB_LEXICON,
    _VERB_LEXICON_LOCK,
)
_WORKER_TOOLS_CORE_CACHE: "Optional[list]" = None

from mios_pipe.routing.toolsurface import (
    _worker_tools_surface,
    _worker_tools_surface_async,
    _select_child_tools,
    _tool_pref_block,
    configure as _configure_toolsurface,
)

SCRATCHPAD_ENABLE = os.environ.get(
    "MIOS_SCRATCHPAD_ENABLE", "true").lower() not in {"false", "0", "no"}
SCRATCHPAD_MAX = int(os.environ.get("MIOS_SCRATCHPAD_MAX", "60"))
SCRATCHPAD_INJECT = int(os.environ.get("MIOS_SCRATCHPAD_INJECT", "12"))
SCRATCHPAD_TTL_S = int(os.environ.get("MIOS_SCRATCHPAD_TTL_S", "3600"))
SCRATCHPAD_SUMMARY_CHARS = int(
    os.environ.get("MIOS_SCRATCHPAD_SUMMARY_CHARS", "280"))
SCRATCHPAD_MAX_CHATS = int(os.environ.get("MIOS_SCRATCHPAD_MAX_CHATS", "256"))
_SCRATCHPADS: "collections.OrderedDict" = collections.OrderedDict()
_conv_key_var: "contextvars.ContextVar" = contextvars.ContextVar(
    "mios_conv_key", default="default")

_client_env_var: "contextvars.ContextVar" = contextvars.ContextVar(
    "mios_client_env", default=None)

def _turn_tenant() -> "Optional[str]":
    try:
        env = _client_env_var.get()
        env = env if isinstance(env, dict) else {}
        owner = str(env.get("user_name") or env.get("user_email") or "").strip()
        return owner or None
    except Exception:  # noqa: BLE001 -- degrade-open: tenant binding never breaks a turn
        return None

_configure_admission(
    priority_queue_enable=PRIORITY_QUEUE_ENABLE,
    tenant_quota_enable=TENANT_QUOTA_ENABLE,
    global_priority_gate=_GLOBAL_PRIORITY_GATE,
    turn_tenant=_turn_tenant,
)

_dispatch_agent_var: "contextvars.ContextVar" = contextvars.ContextVar(
    "mios_dispatch_agent", default="")

_kv_fork_parent_var: "contextvars.ContextVar" = contextvars.ContextVar(
    "mios_kv_fork_parent", default="")

_routed_domain_var: "contextvars.ContextVar" = contextvars.ContextVar(
    "mios_routed_domain", default=None)

_orch_ctx_var: "contextvars.ContextVar" = contextvars.ContextVar(
    "mios_orch_ctx", default=None)
_recency_ctx_var: "contextvars.ContextVar" = contextvars.ContextVar(
    "mios_recency_ctx", default=None)

_turn_volatile_var: "contextvars.ContextVar" = contextvars.ContextVar(
    "mios_turn_volatile", default=False)

_council_mode_var: "contextvars.ContextVar" = contextvars.ContextVar(
    "mios_council_mode", default="single-agent")

_hitl_blocked_var: "contextvars.ContextVar" = contextvars.ContextVar(
    "mios_hitl_blocked", default=None)

_proposal_var: "contextvars.ContextVar" = contextvars.ContextVar(
    "mios_proposal", default=None)
_hitl_approved_var: "contextvars.ContextVar" = contextvars.ContextVar(
    "mios_hitl_approved", default=None)

_sources_var: "contextvars.ContextVar" = contextvars.ContextVar(
    "mios_sources", default=None)
MAX_SOURCES = _dispatch_num("MIOS_MAX_SOURCES", "max_sources", 8)

_SOURCES_REGISTRY: "dict" = {}
_SOURCES_REGISTRY_CAP = _dispatch_num("MIOS_SOURCES_REGISTRY_CAP",
                                      "sources_registry_cap", 64)
_SRC_TURN_HEADER = "X-MiOS-Turn"
_src_turn_var: "contextvars.ContextVar" = contextvars.ContextVar(
    "mios_src_turn", default=None)

from mios_pipe.context.scratchpad import (
    _scratchpad_key,
    _scratchpad_for,
    _scratchpad_rehydrate,
    _scratchpad_note,
    _scratchpad_render,
    configure as _configure_scratchpad,
)

from mios_refine import (  # noqa: E402
    _REFINE_SYSTEM,
    _REFINE_SYSTEM_LITE,
    _critic_refine_agent,
    _salvage_refine_dispatch,
    refine_intent,
)
refine_intent = _traced_stage("refine")(refine_intent)  # noqa: E402  WS-A8 span

RAG_ENABLED = os.environ.get(
    "MIOS_AGENT_PIPE_RAG_ENABLED", "true").lower() not in {"false", "0", "no"}
RAG_BIN = os.environ.get("MIOS_RAG_BIN", "/usr/libexec/mios/mios-rag")
RAG_K = int(os.environ.get("MIOS_AGENT_PIPE_RAG_K", "4"))

async def _rag_enrich(query: str) -> str:
    """Enrich stage: pull RAG context from the vector store
    (mios-rag query, nomic-embed + cosine) so EVERY agent/sub-agent turn
 sees relevant MiOS knowledge in-loop ("RAG in
    the loop for all agents every turn"). Returns a formatted context
    block, or '' on miss/error -- best-effort, never blocks the turn."""
    if not RAG_ENABLED or not query or not query.strip():
        return ""
    try:
        proc = await asyncio.create_subprocess_exec(
            RAG_BIN, "query", query[:500], "--k", str(RAG_K),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        d = _loads_lenient((out or b"{}").decode("utf-8", "replace") or "{}")
    except Exception as e:
        try: proc.kill()
        except Exception: pass
        log.debug("rag enrich skipped: %s", e)
        return ""
    hits = d.get("hits") or []
    lines = [f"- ({h.get('source', '')}) {str(h.get('text', '')).strip()[:320]}"
             for h in hits if isinstance(h, dict) and h.get("text")]
    if not lines:
        return ""
    return ("MiOS knowledge relevant to this request (retrieved; cite/use "
            "if helpful, ignore if not):\n" + "\n".join(lines))

def _current_date_str() -> str:
    env = _client_env_var.get() if isinstance(_client_env_var.get(), dict) else {}
    for _src in (env.get("date"), env.get("datetime")):
        m = re.match(r"\s*(\d{4}-\d{2}-\d{2})", str(_src or ""))
        if m:
            return m.group(1)
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d")

sys.modules["mios_grounding"].configure(   # noqa: E402
    client_env_var=_client_env_var,
    current_date_str=_current_date_str,
    check_inbound_principal=_check_inbound_principal,
)

from mios_web_research import (   # noqa: E402
    _is_port_open,
    _web_research_enrich,
    _url_has_path,
    _clean_web_text,
    _anchor_tokens,
    _shares_anchor,
    _MD_IMG_RE,
    _EMPTY_LINK_RE,
    _NAV_BULLET_RE,
    _INLINE_LINK_RE,
    _DATA_URI_RE,
    _EMPTY_BULLET_RE,
    _MULTI_BLANK_RE,
    _ANCHOR_STOPWORDS,
    _ANCHOR_TOKEN_RE,
    _src_turn_key,
    _src_turn_init,
    _src_record,
    _src_collected,
    _sources_markdown,
    _sources_metadata,
    _sources_annotations,
    _filter_relevant_sources,
    _src_record_from_text,
    _harvest_sub_sources,
    _SRC_LINE_RE,
    _SRC_URL_RE,
)

async def _read_tool_enrich(refined: Optional[dict],
                            session_id: Optional[str]) -> str:
    """Pipeline-side READ-ONLY capability runner ("all...
    skills and recipes fire on ALL endpoints"). For the refine-hinted verbs that
    are permission=read AND take NO required args (live system state), the
    PIPELINE runs them itself + injects the real output for EVERY agent -- so a
    system-state turn is grounded on the iGPU/phone too, not only the
    tool-looping primary. SAFETY: write/launch verbs + recipes are NEVER
    auto-fired here (binding no-live-launch rule); web verbs go to
    _web_research_enrich, KB search to _rag_enrich. Best-effort + bounded."""
    if not READ_TOOL_ENRICH_ENABLED or not refined:
        return ""
    ran: dict = {}
    _hints = list(refined.get("hint_tools") or [])
    _explicit_hints = set(_hints)
    _max = READ_TOOL_ENRICH_MAX
    _core_set: set = set()
    _inv_filter = str((refined or {}).get("inventory_filter") or "").strip()
    if refined.get("local_state"):
        _scope = str((refined or {}).get("state_scope") or "").lower().strip()
        if _scope == "live":
            _core = ["list_windows", "process_list", "container_status",
                     "system_status"]
        elif _scope == "inventory":
            _core = ["mios_apps"]
        else:
            _core = ["system_status", "mios_apps", "process_list",
                     "container_status", "list_windows"]
        _core_set = set(_core)
        _hints = _core + [h for h in _hints if h not in _core]
        _max = max(_max, len(_core) + 1)
    _dom = _routed_domain_var.get(None)
    if _dom and _dom in _ROUTING_DOMAINS:
        _dvset = set(_ROUTING_DOMAINS[_dom].get("verbs") or [])
        if _dvset:
            _keep = _dvset | _explicit_hints
            if refined.get("local_state"):
                _keep |= _core_set
            else:
                _core_set = _core_set & _dvset
            _hints = [h for h in _hints if h in _keep]
    for _t in _hints:
        tool = str(_t).strip()
        if not tool or tool in ran or tool in _WEB_ENRICH_VERBS:
            continue
        v = _VERB_CATALOG.get(tool)
        if not v or v.get("permission") != "read":
            continue
        if tool not in _core_set and any(
                isinstance(c, dict) and "default" not in c
                for c in (v.get("params") or {}).values()):
            continue
        if len(ran) >= _max:
            break
        _targs = ({"filter": _inv_filter}
                  if tool == "mios_apps" and _inv_filter else {})
        try:
            res = await asyncio.wait_for(
                dispatch_mios_verb(tool, _targs, session_id=session_id),
                timeout=READ_TOOL_ENRICH_TIMEOUT)
        except Exception as e:  # noqa: BLE001 -- best-effort
            log.debug("read-tool enrich %s failed: %s", tool, e)
            continue
        out = (json.dumps(res, ensure_ascii=False)
               if isinstance(res, (dict, list)) else str(res)).strip()
        if out and out not in ("{}", "null", '""', "[]"):
            ran[tool] = _cap_verb_result(tool, out)
            if isinstance(refined, dict):  # per-step emit log (end-to-end)
                refined.setdefault("_readtool_steps", []).append(
                    {"emoji": "🔧", "label": "tool", "detail": tool})
    if not ran:
        return ""
    log.info("read-tool enrich: ran %s", list(ran.keys()))
    blocks = [f"### {t}\n{o}" for t, o in ran.items()]
    return ("LIVE MiOS STATE -- the pipeline ran these READ-only tools for this "
            "turn; GROUND your answer on the real output below. CITE it; report "
            "ONLY what is shown; NEVER invent system state. If a block ends with "
            "⟪… OUTPUT TRUNCATED …⟫ the list is INCOMPLETE -- say it continues "
            "('…and more not shown') and do NOT fabricate the omitted entries, "
            "PIDs, names, or counts:\n\n" + "\n\n".join(blocks))

_POLISH_SYSTEM = (
    "Write your answer in ENGLISH. Use another language ONLY if the\n"
    "operator's ORIGINAL message (in the user turn) is itself clearly\n"
    "written in that language -- then reply in that ONE language only.\n"
    "Never add a translation, never switch language mid-reply, and never\n"
    "drift to a language the operator did not use.\n"
    "\n"
    "You are MiOS-Agent's FINAL pass. The material below is the COMBINED\n"
    "context + outputs of MULTIPLE global agents (a primary + council/swarm\n"
    "nodes) plus the live web/system data gathered this turn. COMPILE them\n"
    "into ONE user-facing answer:\n"
    "  1. VERITY-CHECK across the agents + the live data -- keep what they\n"
    "     corroborate or the fetched data supports; drop the unsupported or\n"
    "     contradicted. What multiple agents agree on, or the live data backs,\n"
    "     is trustworthy: USE it.\n"
    "  2. COMBINE every agent's REAL findings into one coherent reply; the BEST\n"
    "     grounded answer among them wins -- NEVER the weakest or the punting\n"
    "     one. If one agent answered substantively and another hedged, deliver\n"
    "     the substantive answer.\n"
    "  3. ANSWER in the USER'S OWN TONE, MATCHING the user's own verbosity.\n"
    "     DEFAULT MEDIUM (a few tight paragraphs or a short list, enriched with\n"
    "     the live data) -- but a terse question gets a tighter reply and a long/\n"
    "     elaborate one gets more. If the user EXPLICITLY asks for detail / depth\n"
    "     / 'explain fully' / 'comprehensive' / 'everything', go to MAXIMUM\n"
    "     detail. Mirror how the user wrote.\n"
    "If ANY agent or the live data produced a real, grounded answer, the final\n"
    "reply MUST deliver it -- never collapse to a punt because the primary draft\n"
    "hedged. Do not attribute the answer to the agents, do not editorialise, and\n"
    "strip internal-reasoning leaks (thought / reasoning / plan lines, tool-call\n"
    "envelopes, thinking blocks). Add no FABRICATED content -- combine what is\n"
    "actually present.\n"
    "\n"
    "OUTPUT ONLY THE ANSWER TEXT. No preamble, no meta-commentary about\n"
    "reformatting, no restating the question, no thinking\n"
    "blocks, no answer-label header. The operator sees your output\n"
    "verbatim, so any preamble reads as if the assistant answered twice.\n"
    "Start directly with the answer.\n"
    "\n"
    "NEVER NARRATE YOUR OWN PROCESS. The operator sees only the answer\n"
    "itself -- never commentary about the draft, the tool history, what\n"
    "the response 'should' do, nor any analysis or strategy header.\n"
    "\n"
    "SYNTHESIS: build the answer from the information actually present in\n"
    "the draft and tool results. If a usable answer is already there,\n"
    "never undercut it with a claim that the data is unavailable or the\n"
    "request cannot be met -- contradicting your own content is a defect.\n"
    "Read the request by its evident intent and answer with what the\n"
    "information supports.\n"
    "\n"
    "NO NON-ANSWERS (operator-binding): NEVER reply that something 'could\n"
    "not be provided because no tools were invoked' or that no data was\n"
    "gathered. That is a dead-end failure, not an answer. A greeting or\n"
    "open-ended turn -- 'how's it going', 'get me up to speed', 'what's\n"
    "new' -- is CONVERSATIONAL: answer it naturally and warmly from the\n"
    "draft (the sub-agents' replies ARE your material). If the operator\n"
    "wants live specifics not in hand, give what you do have and OFFER the\n"
    "concrete next step ('I can pull your live system status / recent\n"
    "activity -- want it?') -- never a flat refusal. This does NOT loosen\n"
    "the side-effect rules below: still never CLAIM an action happened\n"
    "without its tool -- but 'I haven't done X yet; want me to?' is a real\n"
    "answer, while 'X could not be provided because no tool ran' is not.\n"
    "\n"
    "WEB GROUNDING (anti-fabrication): for VOLATILE / LOOKUP facts that needed\n"
    "the search -- current events, live status, prices, dates, proper names,\n"
    "specific figures tied to a recent or local query -- state ONLY what the\n"
    "fetched results say; if they don't cover it, say so plainly and do NOT\n"
    "invent the specifics. BUT a failed or irrelevant search must NOT suppress a\n"
    "STABLE GENERAL-KNOWLEDGE answer the model reliably knows (science, biology,\n"
    "math, history, how-things-work): if the results are empty/off-topic, ANSWER\n"
    "FROM YOUR OWN KNOWLEDGE rather than refusing (e.g. 'how many\n"
    "cells in the eye' searched the stopword 'many', got dictionary defs, and the\n"
    "stack refused a basic-biology answer -- a complete failure). Attach a\n"
    "citation [n] ONLY to the source that actually supports that claim; NEVER\n"
    "reuse one source's number for an unrelated claim. An honest, knowledgeable\n"
    "answer beats both a fabrication AND a needless refusal.\n"
    "\n"
    "NO INVENTED FIGURES OR TIPS (hard rule): every PRICE and every PERCENTAGE\n"
    "you write must be copyable verbatim from the draft / research / sources\n"
    "above. Do NOT append booking 'tips', 'deals as low as $X', '~N% cheaper',\n"
    "'book on <weekday>', or any specific figure that is not already in that\n"
    "material -- not even as a helpful extra. If you cannot point to it in the\n"
    "sources, do not write it. A confident invented number is the worst defect.\n"
    "\n"
    "ONE ANSWER: when several sub-agent drafts are present, MERGE them into a\n"
    "single clean reply -- dedupe, drop repetition, reconcile conflicts. Do\n"
    "NOT concatenate the agents' separate takes or repeat a point once per\n"
    "agent.\n"
    "\n"
    "GROUND TRUTH: the tool_result.success field in the tool history is\n"
    "authoritative for what actually happened. Decide first whether this\n"
    "turn's tools succeeded. When every relevant result is success=true\n"
    "(or stdout shows the window was presented to the operator), the turn\n"
    "succeeded: report it plainly. Do not invent a failure and do not\n"
    "distrust a confirmed success. Surface a failure ONLY when a result\n"
    "is actually success=false, or the history carries a repeat-call halt\n"
    "marker -- and then do it cleanly: quote the failing tool's stderr\n"
    "verbatim, name the verb and its args, and give one concrete next\n"
    "step, as a plain statement to the operator. Misreading success as\n"
    "failure is as serious a defect as the reverse. A launched / opened /\n"
    "started claim is valid only when a matching result is success=true;\n"
    "drop any success the history does not back and report what actually\n"
    "happened instead.\n"
    "\n"
    "INVOKED-TOOL CHECK: the user turn may list 'Tools the agent ACTUALLY\n"
    "invoked this turn'. A claim that a SIDE-EFFECTING action completed --\n"
    "sent, posted, delivered, messaged, launched, opened, created, saved,\n"
    "installed, deleted, scheduled -- is valid ONLY if a tool that plausibly\n"
    "performs it is in that invoked list. If the draft asserts such an action\n"
    "but NO matching tool was invoked (or the invoked list is empty), the\n"
    "action did NOT happen: do NOT repeat the false claim. Instead say plainly\n"
    "what was actually produced, or that the action could not be completed --\n"
    "and, if a required detail is missing (e.g. no destination configured),\n"
    "name it. A fabricated 'done' is a serious defect.\n"
    "\n"
    "LOCALE: language is governed by the rule at the very top (English by\n"
    "default). Never pass through foreign-locale text leaked from the\n"
    "draft's reasoning. Keep every measurement in the units the tool\n"
    "results returned; never silently convert a figure.\n"
    "\n"
    "NO FABRICATION: never introduce a fact, name, figure, date, or claim\n"
    "that is not already in the draft or the tool results. If the draft\n"
    "addresses the operator by a personal name the request did not supply,\n"
    "REMOVE the name -- never invent or guess an identity. If the draft\n"
    "asserts an external/current fact with no tool result behind it, do\n"
    "not present it as confirmed; keep only what the tools returned.\n"
    "\n"
    "SOURCE LINKS: when the tool results carry source URLs, surface them\n"
    "verbatim so the operator can verify; never invent, alter, or guess a\n"
    "URL, and never attach one to a claim the results do not support.\n"
    "\n"
    "VERBATIM TOKENS: copy every path, URL, id, port, tag, size, or\n"
    "percentage from the draft character-for-character. Never re-tokenise,\n"
    "spell-correct, or 'fix' such a token; if you cannot read one, omit\n"
    "its line rather than guess.\n"
    "\n"
    "Output the polished answer ONLY -- no prose around it, no JSON.\n"
)

from mios_reflect import (   # noqa: E402
    _inline_satisfaction_check,
    reflect_on_step_failure,
    _recent_satisfaction_verdicts,
    _recent_tool_history,
    _judge_answer_satisfied,
)
from mios_persona import (   # noqa: E402  -- T-384 Dynamic Agent Persona Synthesis
    DomainCategory,
    PersonaClassifier,
    PersonaSynthesizer,
    classify_intent as classify_persona_intent,
    synthesize_persona_prompt,
    get_domain_guidelines,
)
from mios_deliberate import (   # noqa: E402  -- T-385 Bounded Reflection Loop Convergence
    DeliberationConfig,
    DeliberationState,
    DeliberationTurn,
    SemanticDeltaCalculator,
    BoundedDeliberationEngine,
    calculate_semantic_delta,
    run_bounded_deliberation,
)

_THINK_TAGS = r"think|thinking|thought|reasoning|reflection|scratchpad"
_THINK_BLOCK_RE = re.compile(
    rf"<({_THINK_TAGS})\b[^>]*>.*?</\1>\s*", re.DOTALL | re.IGNORECASE)
_THINK_UNCLOSED_RE = re.compile(
    rf"<({_THINK_TAGS})\b[^>]*>.*$", re.DOTALL | re.IGNORECASE)
_THINK_ORPHAN_RE = re.compile(
    rf"</?({_THINK_TAGS})\b[^>]*>\s*", re.IGNORECASE)
_THINK_OPENERS = ("<think", "<thought", "<reason", "<reflect", "<scratch")
_THINK_CAP_RE = re.compile(
    rf"<({_THINK_TAGS})\b[^>]*>(.*?)</\1>", re.DOTALL | re.IGNORECASE)
_THINK_CAP_UNCLOSED_RE = re.compile(
    rf"<({_THINK_TAGS})\b[^>]*>(.*)$", re.DOTALL | re.IGNORECASE)

KNOWLEDGE_STORE_ENABLED = os.environ.get(
    "MIOS_KNOWLEDGE_STORE", "true").strip().lower() not in ("0", "false", "no")
KNOWLEDGE_STORE_GATE_UNSATISFIED = os.environ.get(
    "MIOS_KNOWLEDGE_STORE_GATE_UNSATISFIED", "true").strip().lower() not in ("0", "false", "no")
KNOWLEDGE_TABLE = (os.environ.get("MIOS_KNOWLEDGE_TABLE", "knowledge").strip()
                   or "knowledge")
MEMORY_GUARD_MODE = (os.environ.get("MIOS_MEMORY_GUARD_MODE")
                     or (_toml_section("pgvector").get("memory_guard_mode", "off"))
                     ).strip().lower()
KNOWLEDGE_ANSWER_MAX = int(
    os.environ.get("MIOS_KNOWLEDGE_ANSWER_MAX", "8000") or 8000)
KNOWLEDGE_RECALL_ENABLED = os.environ.get(
    "MIOS_KNOWLEDGE_RECALL", "true").strip().lower() not in ("0", "false", "no")
KNOWLEDGE_RECALL_K = int(os.environ.get("MIOS_KNOWLEDGE_RECALL_K", "3") or 3)
KNOWLEDGE_RECALL_CANDIDATES = int(
    os.environ.get("MIOS_KNOWLEDGE_RECALL_CANDIDATES", "60") or 60)
KNOWLEDGE_RECALL_MIN_SCORE = float(
    os.environ.get("MIOS_KNOWLEDGE_RECALL_MIN_SCORE", "0.62") or 0.62)
KNOWLEDGE_RECALL_STRICT_SCORE = _cfg_num(
    _KN_TOML, "MIOS_KNOWLEDGE_RECALL_STRICT_SCORE", "recall_strict_score", 0.82, float)
KNOWLEDGE_RECALL_PREF_MIN_SCORE = _cfg_num(
    _KN_TOML, "MIOS_KNOWLEDGE_RECALL_PREF_MIN_SCORE", "recall_pref_min_score", 0.50, float)
_RECALL_POSSESSIVE_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in (_load_routing_phrases("recall_possessives") or _load_routing_phrases("launch_target_lead_phrases"))) + r")\b",
    re.I
)

from mios_knowledge import (   # noqa: E402
    _recall_floor,
    _row_age_seconds,
    _humanize_age,
    _recency_mult,
    _knowledge_sources,
    _store_knowledge,
    _store_knowledge_task,
    _recall_knowledge_pg,
    _recall_knowledge,
    _db_count,
    _evict_select_ids,
    _evict_delete_ids,
    _evict_knowledge,
    _knowledge_evict_loop,
    _rls_owner,
    _recall_agent_memory,
    kg_lookup,
)

KNOWLEDGE_RANK_OUTCOME = _cfg_num(_KN_TOML, "MIOS_KNOWLEDGE_RANK_OUTCOME", "rank_outcome", 0.05, float)
KNOWLEDGE_RANK_HOT = _cfg_num(_KN_TOML, "MIOS_KNOWLEDGE_RANK_HOT", "rank_hot", 0.03, float)
KNOWLEDGE_RANK_ACCESS = _cfg_num(_KN_TOML, "MIOS_KNOWLEDGE_RANK_ACCESS", "rank_access", 0.02, float)
KNOWLEDGE_RANK_AGE = _cfg_num(_KN_TOML, "MIOS_KNOWLEDGE_RANK_AGE", "rank_age", 0.0, float)
KNOWLEDGE_RECALL_HALFLIFE_DAYS = _cfg_num(
    _KN_TOML, "MIOS_KNOWLEDGE_RECALL_HALFLIFE_DAYS", "recall_halflife_days", 7.0, float)
_skip_vol_cfg = _KN_TOML.get("store_skip_volatile") if isinstance(_KN_TOML, dict) else None
KNOWLEDGE_STORE_SKIP_VOLATILE = (
    bool(_skip_vol_cfg) if _skip_vol_cfg is not None
    else str(os.environ.get("MIOS_KNOWLEDGE_STORE_SKIP_VOLATILE", "1")).strip().lower()
    in {"1", "true", "yes"})
KNOWLEDGE_HOT_THRESHOLD = _cfg_num(_KN_TOML, "MIOS_KNOWLEDGE_HOT_THRESHOLD", "hot_threshold", 5, int)
KNOWLEDGE_EVICT_ENABLE = str(os.environ.get("MIOS_KNOWLEDGE_EVICT")
                             or _KN_TOML.get("evict_enable", "false")
                             ).strip().lower() in {"1", "true", "yes"}
KNOWLEDGE_EVICT_DRYRUN = str(os.environ.get("MIOS_KNOWLEDGE_EVICT_DRYRUN")
                             or _KN_TOML.get("evict_dryrun", "false")
                             ).strip().lower() in {"1", "true", "yes"}
KNOWLEDGE_EVICT_INTERVAL_S = _cfg_num(_KN_TOML, "MIOS_KNOWLEDGE_EVICT_INTERVAL_S", "evict_interval_s", 3600, int)
KNOWLEDGE_EVICT_TTL_DAYS = _cfg_num(_KN_TOML, "MIOS_KNOWLEDGE_EVICT_TTL_DAYS", "evict_ttl_days", 90, int)
KNOWLEDGE_EVICT_MAX_ROWS = _cfg_num(_KN_TOML, "MIOS_KNOWLEDGE_EVICT_MAX_ROWS", "evict_max_rows", 50000, int)
KNOWLEDGE_EVICT_MIN_ACCESS = _cfg_num(_KN_TOML, "MIOS_KNOWLEDGE_EVICT_MIN_ACCESS", "evict_min_access", 1, int)
KNOWLEDGE_EVICT_BATCH = _cfg_num(_KN_TOML, "MIOS_KNOWLEDGE_EVICT_BATCH", "evict_batch", 500, int)
_KNOWLEDGE_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")

SKILLS_EPISODIC_DIR = os.environ.get(
    "MIOS_SKILLS_EPISODIC_DIR", "/var/lib/mios/ai/skills/episodic")
SKILLS_EPISODIC_ENABLED = os.environ.get(
    "MIOS_SKILLS_EPISODIC_ENABLED",
    "true").lower() not in {"false", "0", "no"}

# MIOS_AGENT_MEMORY_RECALL=1. Same allowed-injection class as _recall_knowledge
AGENT_MEMORY_RECALL_ENABLED = str(
    os.environ.get("MIOS_AGENT_MEMORY_RECALL", "0")).strip().lower() in {"1", "true", "yes"}
AGENT_MEMORY_TABLE = os.environ.get("MIOS_AGENT_MEMORY_TABLE", "agent_memory")
AGENT_MEMORY_RECALL_K = int(os.environ.get("MIOS_AGENT_MEMORY_RECALL_K", "3"))
AGENT_MEMORY_RECALL_MIN_SCORE = float(
    os.environ.get("MIOS_AGENT_MEMORY_RECALL_MIN_SCORE", "0.45"))

_MEMORY_PROVIDER_NAME = str(
    os.environ.get("MIOS_MEMORY_PROVIDER")
    or (_toml_section("pgvector") or {}).get("memory_provider")
    or "pgvector").strip().lower()
try:
    _MEMORY = mios_memory.get_memory_provider(_MEMORY_PROVIDER_NAME, _mios_pg)
except ValueError as _e:
    log.error("WS-A15 memory provider: %s -- falling back to pgvector", _e)
    _MEMORY = mios_memory.get_memory_provider("pgvector", _mios_pg)

mios_memory.configure_letta(
    toml_section_func=_toml_section,
    conv_key_var=_conv_key_var,
    db_create=_db_create,
    db_post=_db_post,
    db_fire=_db_fire
)

from mios_daemons import _kv_gc_sweep_once, _kv_gc_loop, _consolidate_memory_loop   # noqa: E402,F401

from mios_verity import (   # noqa: E402
    VERITY_FACTCHECK,
    VERITY_FACTCHECK_MAX_Q,
    _verity_factcheck,
    _strip_ungrounded_figures,
    polish_response,
    _clarify_question,
)

SKILLS_ENABLED = os.environ.get(
    "MIOS_SKILLS_ENABLE", "true",
).lower() not in {"false", "0", "no"}
SKILLS_MIN_LENGTH = int(os.environ.get("MIOS_SKILLS_MIN_LENGTH", "2"))
SKILLS_MAX_LENGTH = int(os.environ.get("MIOS_SKILLS_MAX_LENGTH", "8"))
SKILLS_MIN_SUPPORT = int(os.environ.get("MIOS_SKILLS_MIN_SUPPORT", "3"))
SKILLS_WINDOW_HOURS = int(os.environ.get("MIOS_SKILLS_WINDOW_HOURS", "168"))
SKILLS_AUTO_PROMOTE_THRESHOLD = float(os.environ.get(
    "MIOS_SKILLS_AUTO_PROMOTE_THRESHOLD", "0.85"))

from mios_skills import (  # noqa: E402
    _skill_fetch, _skill_list, execute_skill, _skill_to_openai_tool,
    _make_schema_strict, _mcp_tool_to_openai_tool,
    _skill_render_args, _skill_invocation_open, _skill_invocation_close,
    _skill_attribute_tool_call, _PARAM_TOKEN_RE, _SKILL_INV_META,
    _slug_for_skill, _render_skill_md, _write_skill_md_fire,
)

CRITIC_REFINE_ENABLED = os.environ.get(
    "MIOS_AGENT_PIPE_CRITIC_REFINE", "1") not in ("0", "false", "False", "")
CRITIC_REFINE_MAX = int(os.environ.get(
    "MIOS_AGENT_PIPE_CRITIC_REFINE_MAX", "1"))
CRITIC_REFINE_MIN_CHARS = int(os.environ.get(
    "MIOS_AGENT_PIPE_CRITIC_REFINE_MIN_CHARS", "500"))

_HIGH_PRIVILEGE_CURATED = {
    "service_restart",
    "container_restart",
    "pc_type",
    "pc_key",
    "pc_click",
    "text_create",
    "text_str_replace",
    "text_insert",
    "powershell_run",
    "minimize_window",
    "maximize_window",
    "restore_window",
    "resize_window",
    "position_window",
    "winget_install",
    "winget_upgrade",
    "winget_uninstall",
    "flatpak_install",
    "flatpak_upgrade",
    "flatpak_uninstall",
    "pkg",
    "window_op",
    "windows_input",
    "linux_input",
    "file_edit",
    "memory",
    "run_code",
    "agent_route",
    "document",
}
_HIGH_PRIVILEGE_VERBS = mios_secset.high_privilege_set(
    _HIGH_PRIVILEGE_CURATED,
    (_toml_section("security") or {}).get("firewall_high_privilege_verbs"))
_TAINT_VERBS = mios_secset.taint_verb_set(
    ("web_search", "web_extract", "crawl", "web_scrape"),
    (_toml_section("security") or {}).get("taint_verbs"))

_DEFAULT_ALLOWLIST_HOSTS = {
    "localhost", "127.0.0.1", "::1",
    "host.containers.internal",
    "mios-llm-light", "mios-open-webui", "mios-hermes", "mios-pgvector",
    "mios-forge", "mios-searxng", "mios-agents",
}
_env_allowlist = os.environ.get("MIOS_SECURITY_ALLOWLIST_HOSTS", "").strip()
if _env_allowlist:
    _ALLOWLIST_HOSTS = {
        h.strip().lower() for h in _env_allowlist.split(",") if h.strip()
    }
else:
    _ALLOWLIST_HOSTS = set(_DEFAULT_ALLOWLIST_HOSTS)

from mios_firewall import (   # noqa: E402
    _is_external_url,
    _classify_verb_taint,
    _session_is_tainted,
)

PROVENANCE_TAINT_ENABLE = str(
    os.environ.get("MIOS_SECURITY_PROVENANCE_TAINT")
    or _toml_section("security").get("provenance_taint", "false")
).strip().lower() in {"1", "true", "yes"}

RULE_OF_TWO_MODE = str(
    os.environ.get("MIOS_SECURITY_RULE_OF_TWO_MODE")
    or _toml_section("security").get("rule_of_two_mode", "off")
).strip().lower()

# MIOS_SECURITY_QUARANTINE_MODE): off (default) | audit | enforce. The STRICTER superset
QUARANTINE_MODE = str(
    os.environ.get("MIOS_SECURITY_QUARANTINE_MODE")
    or _toml_section("security").get("quarantine_mode", "off")
).strip().lower()

def _is_action_domain(domain: Optional[str]) -> bool:
    """Data-driven action-vs-research split: a routed [routing.domains] domain is
    an ACTION domain (decompose into EXECUTABLE tool steps, not research facets)
    iff ANY of its SSOT verbs is permission=='write'. No keyword/app/English
    literals -- the distinction is verb PERMISSION metadata from mios.toml, so a
 new write-verb in any domain becomes 'action' automatically.
    (swarm researched 'send a discord message' instead of performing it)."""
    if not domain:
        return False
    verbs = (_ROUTING_DOMAINS.get(domain) or {}).get("verbs") or []
    return any(str((_VERB_CATALOG.get(str(v)) or {}).get("permission", "")).lower()
               == "write" for v in verbs)

@_traced_stage("route")  # WS-A8: emit a span around domain routing

async def _needs_compute(user_text: str) -> bool:
    if not (user_text or "").strip():
        return False
    sys = (
        "Decide, by MEANING not keywords: to fully and CORRECTLY answer the user, is a "
        "non-trivial CALCULATION required that a language model cannot do reliably in its "
        "head -- e.g. multi-digit or exact arithmetic, statistics, unit/currency "
        "conversion, counting, or a date/time difference? Examples: 'what is "
        "19387*4472', 'split an $80 bill three ways', 'how many days until Nov 3', 'how "
        "old is someone born in 1991' -> true. 'what is the capital of France', "
        "'summarize this article', 'what is 2+2' (trivial) -> false.")
    payload = {
        "model": ROUTER_MODEL,
        "messages": [{"role": "system", "content": sys},
                     {"role": "user", "content": user_text[:2000]}],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "compute", "strict": True, "schema": {
                "type": "object",
                "properties": {"needs_compute": {"type": "boolean"}},
                "required": ["needs_compute"], "additionalProperties": False}}},
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
        return (_loads_lenient(content) or {}).get("needs_compute") is True
    except Exception as e:  # noqa: BLE001 -- degrade-CLOSED (-> no compute prefetch)
        log.debug("compute-need judge failed (-> no compute): %s", e)
        return False

from mios_planner import (   # noqa: E402
    decompose_intent,
    _topological_order,
    _dag_levels,
)

_REFLECT_SYSTEM = (
    "You are MiOS-Agent's single-step reflection pass. A planner\n"
    "emitted a multi-step plan; one step's dispatch FAILED. Read the\n"
    "failed step + the captured error + the surrounding plan, and\n"
    "emit ONE corrected step as JSON. Do NOT re-plan the whole\n"
    "chain. Do NOT add commentary. Just the correction.\n"
    "\n"
    "Output shape (EXACT):\n"
    '{"tool": "<verb>", "args": {...}, "rationale": "<one line>"}\n'
    "\n"
    "Rules:\n"
    "- Keep the same node id if possible; downstream nodes may have\n"
    "  #E<id> refs to it.\n"
    "- If the failure was 'unknown verb', pick a different verb that\n"
    "  does the same thing (open_app vs launch_app, etc.).\n"
    "- If the failure was 'missing arg', add the arg.\n"
    "- If the failure was 'tool returned exit 2 with stderr X', look\n"
    "  at stderr for the actual cause + adjust args (a path that\n"
    "  doesn't exist, a flag the tool doesn't accept, a query that\n"
    "  needs quoting differently).\n"
    "- If the failure looks irrecoverable from a single-step swap,\n"
    "  emit {\"tool\":\"\",\"args\":{},\"rationale\":\"unfixable\"} and\n"
    "  the dispatcher will abort the chain.\n"
)

from mios_pipe.observability.session_events import (
    _emit_session_event,
    _sanitize_tool_text,
    configure as _configure_session_events,
)

_HITL_TOML = _toml_section("hitl")
HITL_ENABLE = str(os.environ.get("MIOS_HITL_ENABLE")
                  or _HITL_TOML.get("enable", "true")).strip().lower() \
    in {"1", "true", "yes"}
HITL_MODE = str(os.environ.get("MIOS_HITL_MODE")
                or _HITL_TOML.get("mode", "log")).strip().lower()
HITL_SCOPE = _hitl_parse_scope(
    str(os.environ.get("MIOS_HITL_VERBS") or _HITL_TOML.get("verbs", "")),
    _HIGH_PRIVILEGE_VERBS)

from mios_hitlflow import (   # noqa: E402
    _hitl_is_approved, _hitl_record_pending, _hitl_gate,
    hitlflow_router, hitl_pending, hitl_approve)

_ATR_TOML = _toml_section("ai") or {}
ASK_TO_RUN_ENABLE = str(
    os.environ.get("MIOS_ASK_TO_RUN")
    or _ATR_TOML.get("ask_to_run", "true")).strip().lower() in {"1", "true", "yes"}
try:
    ASK_TO_RUN_TTL_S = int(os.environ.get("MIOS_ASK_TO_RUN_TTL")
                           or _ATR_TOML.get("ask_to_run_ttl_s", 1800))
except (TypeError, ValueError):
    ASK_TO_RUN_TTL_S = 1800
ASK_CLARIFY_ENABLE = str(
    os.environ.get("MIOS_ASK_CLARIFY")
    or _ATR_TOML.get("ask_clarify", "true")).strip().lower() in {"1", "true", "yes"}
ASK_CLARIFY_JUDGE_ENABLE = str(
    os.environ.get("MIOS_ASK_CLARIFY_JUDGE")
    or _ATR_TOML.get("ask_clarify_judge", "false")).strip().lower() in {"1", "true", "yes"}

from mios_hitlflow import _classify_approval_reply  # noqa: E402

from mios_hitlflow import (   # noqa: E402
    _read_recent_pending, _mark_pending_decided,
    _ask_to_run_completion, _maybe_run_pending_approval)

from mios_hitlflow import _recent_reflections  # noqa: E402

from mios_hitlflow import _action_hash, _pending_hash  # noqa: E402

DISPATCH_DEDUP = os.environ.get(
    "MIOS_DISPATCH_DEDUP", "true").lower() not in {"false", "0", "no"}
_dispatch_inflight: dict[str, "asyncio.Future"] = {}

from mios_dag_exec import (   # noqa: E402  (R8: DAG execution entrypoints, moved verbatim)
    _deepen_until_barrier, _execute_dag_node, _record_dag_node_row,
    _execute_dag_saturated, RUN_TEMPLATE_ENABLE, _run_template_class, load_run_templates,
    _capture_run_template, execute_dag, _execute_dag_bounded,
    _execute_dag_emitting,
    _EK_REF_RE, _EK_FIELD_REF_RE, _smart_extract_from_jsonish,
    _substitute_ek_refs, _fit_context, _node_deepens, _reap_cpu_lane,
)

from mios_swarm import _agent_dag_from_tasks, _reroute_dead_nodes  # noqa: E402

_SWARM_SYSTEM_HEAD = (
    "You are the MiOS SWARM planner. Split the user's request into INDEPENDENT "
    "sub-tasks that can run in PARALLEL, and assign each to the best sub-agent "
    "from the roster below. This is multi-agent delegation -- weigh the whole "
    "roster, route by each agent's strengths, do not funnel everything to one.\n"
    "\n"
    "Emit JSON ONLY (no prose, no markdown):\n"
    '{"subtasks":[{"agent":"<exact roster name>","task":"<self-contained sub-task '
    'in the user\'s language>","query":"<clean web-search phrase for this facet>"}, '
    "...]}\n"
    "\n"
    "Rules:\n"
    "- Use EXACT agent names from the roster; the executor rejects unknown ones.\n"
    "- Split into the DISTINCT facets the request GENUINELY has -- different "
    "sub-topics, angles, or dimensions, each DIRECTLY about the request. Give "
    "as many REAL facets as the request supports (usually 2-5). NEVER invent an "
    "unrelated or filler facet (a local venue, a 'verify against a database' "
    "meta-task, a dictionary definition) just to produce more -- the dispatcher "
    "spreads your real facets across ALL live nodes, so you do NOT need one per "
    "agent. Match each agent's strengths to its facet.\n"
    "- Each must be SELF-CONTAINED -- the assigned agent sees ONLY its own task "
    "string, not the others.\n"
    "- `query` is a CLEAN web-search phrase for the facet: the actual TOPIC to "
    "find, phrased as a search a person would type -- NOT an imperative. NEVER "
    "begin it with Summarize / Compile / Research / List / Find / Get (a search "
    "engine then matches a dictionary entry or a generic tool, not your topic). "
    "Disambiguate any word a search engine would mis-match, and for anything "
    "time-sensitive anchor it to the CURRENT date above (never a past year).\n"
    "- NEVER emit a GENERIC catch-all `query` like 'current events and news', "
    "'latest news', 'what is happening', 'trending topics' -- a search engine "
    "matches the WORD ('current' -> a banking app, a dictionary entry) not real "
    "news, and the facet comes back empty. Each `query` MUST name a CONCRETE "
    "subject, region, or sector. For a vague 'what's new / world news today' ask "
    "with no subject, SPLIT into concrete named facets each with its own concrete "
    "query, e.g. 'top world headlines <current date>', 'global economy news "
    "<current date>', 'technology news <current date>', 'major geopolitics news "
    "<current date>' -- never the single meta-phrase.\n"
    "- GROUND every facet in the user's ACTUAL words PLUS the recent conversation "
    "below. A terse follow-up ('research it deeper', 'do that every 30 minutes', "
    "'find the cheapest', 'set one up') inherits the SUBJECT already established "
    "earlier in the chat -- carry that exact subject (its place, route, product, "
    "topic) forward; never switch to a different topic or region. NEVER invent a "
    "concrete detail -- a city, route, price, date, brand, or name -- that the "
    "user did not state and is not in the conversation: a fabricated constraint "
    "makes the search match unrelated junk and the answer comes back empty. If the "
    "request is genuinely under-specified and the chat gives no subject, emit a "
    "broad on-topic facet, NOT a guessed-specific one.\n"
    "- Independent only: do NOT emit sub-tasks that depend on each other's output "
    "(they run concurrently).\n"
    "- SPLIT BY DEFAULT. Almost every substantive question has independent ANGLES "
    "worth parallel work, and a multi-facet swarm beats one generalist pass. For "
    "ANY research / informational / 'what's happening' / comparison / open-ended "
    "/ broad / multi-aspect request, split into 2-5 DISTINCT FACETS -- "
    "different sub-topics, angles, regions, sectors, or dimensions -- each "
    "researched INDEPENDENTLY in parallel (e.g. world news -> geopolitics, economy, tech, "
    "climate, culture; a product -> features, pricing, alternatives, reviews; a "
    "'this week in X' -> the 3-4 biggest distinct stories/areas in X). Each facet "
    "must STAND ALONE and produce its own answer; do NOT make one task 'search' "
    "and another 'summarise' (that is a dependent pipeline, not a parallel "
    "swarm).\n"
    "- Emit {\"subtasks\":[]} for a TRULY ATOMIC ask (a single bare fact, a "
    "single concrete action) OR for a DEPENDENT PIPELINE: a single goal whose "
    "later step needs an earlier step's RESULT -- the final step acts on a value "
    "the earlier steps must first resolve. Parallel workers CANNOT run a "
    "dependent pipeline (each would act on an unresolved placeholder, fabricate "
    "the missing value, or act on the literal description); one agent runs the "
    "tool-calling loop and sequences those steps in order, so return []. Only a "
    "question with INDEPENDENT breadth -- angles that each stand alone and need "
    "no other's result -- is splittable: SPLIT that into distinct facets.\n"
    "- A request that includes an INTERACTIVE web/app ACTION (sign up, log in, set "
    "up an account/alert, book, fill + submit a form, post) STILL decomposes its "
    "RESEARCH facets normally (context, options, best settings) -- the action "
    "itself is dispatched SEPARATELY to the browser agent, so just split the "
    "research as usual; do not emit a 'go click the button' facet.\n"
    "\n"
    "Sub-agent roster:\n"
)
_SWARM_SYSTEM = _SWARM_SYSTEM_HEAD + _AGENT_CATALOG_RENDERED

from mios_swarm import _plan_swarm, _expand_facets  # noqa: E402
_plan_swarm = _traced_stage("plan")(_plan_swarm)  # noqa: E402  WS-A8 span

from mios_swarm import _respond_agent_dag  # noqa: E402

from mios_dispatch import (   # noqa: E402
    _TEMPLATE_PH_RE, _TemplateAbort, _template_to_cmd, _build_dispatch_cmd,
    _dispatch_bounded, dispatch_mios_verb, _dispatch_mios_verb_inner,
    _emit_dispatch_dedup_event,
    _arg_with_synonyms, _validate_enum_args,
    _dispatch_sandbox_profile, _sandbox_wrap_cmd,
    dispatch_router, dispatch_verb,
)
app.include_router(dispatch_router)
from mios_pipe.routing import applet_webresearch as _applet_webresearch  # noqa: E402
from mios_pipe.routing.applet_webresearch import router as _webresearch_router  # noqa: E402
_applet_webresearch.configure(dispatch=dispatch_mios_verb)
app.include_router(_webresearch_router)
sys.modules["mios_dispatch"].configure(
    verb_catalog=_VERB_CATALOG,
    verb_arg_synonyms=_VERB_ARG_SYNONYMS,
    high_privilege_verbs=_HIGH_PRIVILEGE_VERBS,
    launch_verbs=_LAUNCH_VERBS,
    web_dispatch_jitter_s=WEB_DISPATCH_JITTER_S,
    dispatch_dedup=DISPATCH_DEDUP,
    launcher_sock=LAUNCHER_SOCK,
    sandbox_enforce=SANDBOX_ENFORCE,
    sandbox_self_confined=_SANDBOX_SELF_CONFINED,
    rule_of_two_mode=RULE_OF_TWO_MODE,
    quarantine_mode=QUARANTINE_MODE,
    dispatch_inflight=_dispatch_inflight,
    web_sem=_web_sem,
    tool_conflict=_TOOL_CONFLICT,
    conv_key_var=_conv_key_var,
    recency_ctx_var=_recency_ctx_var,
    proposal_var=_proposal_var,
    dispatch_agent_var=_dispatch_agent_var,
    hitl_approved_var=_hitl_approved_var,
    resolve_verb_key=_resolve_verb_key,
    current_date_str=_current_date_str,
    emit_dispatch_dedup_event=_emit_dispatch_dedup_event,
    trace_span=_trace_span,
    db_fire=_db_fire,
    db_post=_db_post,
    db_create=_db_create,
    letta_dispatch_handler=mios_memory.letta_dispatch_handler,
    agent_registry=_AGENT_REGISTRY,
)

sys.modules["mios_skills"].configure(
    db_read=_db_read,
    db_post=_db_post,
    db_update=_db_update,
    db_write=_db_write,
    pg_mirror=_pg_mirror,
    dispatch_verb=dispatch_mios_verb,
    skills_enabled=SKILLS_ENABLED,
    skills_episodic_dir=SKILLS_EPISODIC_DIR,
    skills_episodic_enabled=SKILLS_EPISODIC_ENABLED,
)

_ROUTE_ON_CARD_SKILLS = os.environ.get(
    "MIOS_A2A_ROUTE_ON_CARD_SKILLS",
    str((_toml_section("a2a") or {}).get("route_on_card_skills", "false"))
).strip().lower() in ("1", "true", "yes", "on")

sys.modules["mios_fanout"].configure(
    agent_registry=_AGENT_REGISTRY,
    dispatch_cfg=_DISPATCH_CFG,
    depth_exhausted=_depth_exhausted,
    dispatch_depth=_dispatch_depth,
    lane_sem_key=_lane_sem_key,
    dedup_pool_by_target=_dedup_pool_by_target,
    over_global_ceiling=_over_global_ceiling,
    agent_lane=_agent_lane,
    agent_skill_tags=_agent_skill_tags,
    max_dispatch_depth=MAX_DISPATCH_DEPTH,
    council_max_default=COUNCIL_MAX_DEFAULT,
    admit_enable=ADMIT_ENABLE,
    route_on_card_skills=_ROUTE_ON_CARD_SKILLS,
    db_create=_db_create,
    db_post=_db_post,
    db_fire=_db_fire,
)

sys.modules["mios_refine"].configure(
    logger=log,
    agent_registry=_AGENT_REGISTRY,
    verb_catalog=_VERB_CATALOG,
    routed_domain_var=_routed_domain_var,
    over_global_ceiling=_over_global_ceiling,
    resolve_verb_key=_resolve_verb_key,
    route_domain=_route_domain,
    db_fire=_db_fire,
    db_post=_db_post,
    db_create=_db_create,
    refine_enabled=REFINE_ENABLED,
    refine_model=REFINE_MODEL,
    refine_endpoint=REFINE_ENDPOINT,
    refine_max_tokens=REFINE_MAX_TOKENS,
    refine_timeout_s=REFINE_TIMEOUT_S,
    refine_attempts=REFINE_ATTEMPTS,
    os_control_verbs_rendered=_OS_CONTROL_VERBS_RENDERED,
    browser_action_alt=_BROWSER_ACTION_ALT,
    web_search_triggers=_WEB_SEARCH_TRIGGERS,
    web_search_contexts=_WEB_SEARCH_CONTEXTS,
    remember_triggers=_REMEMBER_TRIGGERS,
    fastpath_verbs=_FASTPATH_VERBS,
    routing_enable=_ROUTING_ENABLE,
    routing_domains=_ROUTING_DOMAINS,
    emit_session_event=_emit_session_event,
    critic_refine_enabled=CRITIC_REFINE_ENABLED,
    critic_refine_max=CRITIC_REFINE_MAX,
    critic_refine_min_chars=CRITIC_REFINE_MIN_CHARS,
    chat_chars=(_toml_section("refine") or {}).get("chat_chars"),
    dispatch_chars=(_toml_section("refine") or {}).get("dispatch_chars"),
    promote_chars=(_toml_section("refine") or {}).get("promote_chars"),
    dispatch_arg_max_words=(_toml_section("refine") or {}).get("dispatch_arg_max_words"),
)
from mios_refine import _REFINE_SYSTEM  # noqa: E402,F811

sys.modules["mios_planner"].configure(
    verb_catalog_rendered=_VERB_CATALOG_RENDERED,
    recipe_catalog_rendered=_RECIPE_CATALOG_RENDERED,
    agent_catalog_rendered=_AGENT_CATALOG_RENDERED,
    routed_domain_var=_routed_domain_var,
    is_action_domain=_is_action_domain,
    verb_catalog=_VERB_CATALOG,
    routing_domains=_ROUTING_DOMAINS,
    build_dispatch_cmd=_build_dispatch_cmd,
    agent_registry=_AGENT_REGISTRY,
    short_prompt_chars=(_toml_section("planner") or {}).get("short_prompt_chars"),
    short_prompt_words=(_toml_section("planner") or {}).get("short_prompt_words"),
    replay_templates=load_run_templates)   # T-225 intent-keyed replay source
from mios_planner import (  # noqa: E402,F401
    _PLANNER_SYSTEM, _planner_system_for, _action_domain_verbs)

from mios_sse import (  # noqa: E402
    _sse_chunk, _sse_reasoning, _load_status_labels, _HUMAN_LABELS,
    _sse_status_phase, _sse_status, _enrich_step_emits, _node_context,
    _node_status, _stream_answer, _iter_answer_chunks, _sse_done,
    _TAIL_KIND_EMOJI, _HERMES_TAIL_PATH, _tail_latest_status,
)
from mios_turn import (  # noqa: E402
    _extract_last_user_text, _pick_agent, _casual_agent_label, _live_agent_names,
    _split_think_tags, _strip_think_tags,
)
sys.modules["mios_sse"].configure(
    debug_enable=_DEBUG_ENABLE,
    surface_default=str(os.environ.get("MIOS_SURFACE_DEFAULT") or _otel_toml.get("surface_default", "clean")).strip().lower()
)
sys.modules["mios_turn"].configure(
    _AGENT_REGISTRY=_AGENT_REGISTRY,
    _NODE_LIVE=_NODE_LIVE,
    _should_health_probe=_should_health_probe,
    _probe_auth_headers=_probe_auth_headers,
    NODE_LIVENESS_TTL_S=NODE_LIVENESS_TTL_S,
    NODE_LIVENESS_CONNECT_S=NODE_LIVENESS_CONNECT_S,
    _THINK_OPENERS=_THINK_OPENERS,
    _THINK_CAP_RE=_THINK_CAP_RE,
    _THINK_CAP_UNCLOSED_RE=_THINK_CAP_UNCLOSED_RE,
    _THINK_ORPHAN_RE=_THINK_ORPHAN_RE,
)

_CAP_SKILLS_DIR = os.environ.get("MIOS_SKILLS_SEED_DIR", "/usr/share/mios/skills")
_CAP_SKILLS_CACHE: "Optional[dict]" = None

def _cap_skills() -> dict:
    """Load the structured JSON skills once (cached). Degrade-open -> {}."""
    global _CAP_SKILLS_CACHE
    if _CAP_SKILLS_CACHE is None:
        try:
            _CAP_SKILLS_CACHE = mios_capreg.load_skills_from_dir(_CAP_SKILLS_DIR)
        except Exception:  # noqa: BLE001
            _CAP_SKILLS_CACHE = {}
    return _CAP_SKILLS_CACHE

from mios_a2a import (   # noqa: E402
    A2A_PROTOCOL_VERSION,
    AGENT_PASSPORT_VERSION,
    AGNTCY_OASF_SCHEMA_VERSION,
    _agent_card_signature,
    _build_agent_card,
    _canonical_json,
    _build_agent_passport,
    _build_agntcy_manifest,
    _a2a_messages_for,
    _a2a_context,
    _A2A_TASKS,
    _A2A_TASKS_LOCK,
    _A2A_TASKS_MAX,
    _A2A_TERMINAL,
    _A2A_ERR_TASK_NOT_FOUND,
    _A2A_ERR_TASK_NOT_CANCELABLE,
    _A2A_ERR_UNSUPPORTED_OP,
    _a2a_now,
    _a2a_text_from_message,
    _a2a_make_task,
    _a2a_task_record,
    _A2A_PUSH_CONFIGS,
    _A2A_PUSH_LOCK,
    _a2a_make_push_cfg_id,
    _a2a_fire_push_notifications,
    _a2a_dispatch_send,
    _a2a_rpc_ok,
    _a2a_rpc_err,
    _a2a_jsonrpc_dispatch,
    _A2A_STREAM_ENABLED,
    _a2a_sse,
    _a2a_stream_response,
    _A2A_PRINCIPAL_REQUIRE,
    _a2a_principal_metadata,
    _CRL_PATH,
    _CRL_CACHE,
    _load_crl,
    _a2a_verify_principal,
    a2a_router,
    a2a_skill_directory,
    a2a_context_get,
    a2a_context_get_v1,
    a2a_jsonrpc,
    a2a_jsonrpc_alias,
    a2a_peers_reload,
    caller_key_revoke,
    a2a_agent_card,
    a2a_agent_card_legacy,
    agent_passport,
    agntcy_manifest_wellknown,
    a2a_peers_list,
    a2a_skills_list,
    a2a_dispatch,
    passport_verify,
    passport_public_key,
    a2a_agent_card_alias,
    agntcy_manifest_v1,
)
# _match_user_cfg / _user_rbac_filter are injected into a2a + http_caps below
# and were referenced here without ever being imported -- a module-scope
# NameError that made server.py unimportable.
from mios_policy import (   # noqa: E402
    _perm_rank,
    _effective_perm,
    _agent_rbac_filter,
    _dispatch_pdp_reason,
    _match_user_cfg,
    _user_rbac_filter,
    _PERMISSION_TIERS,
)

sys.modules["mios_a2a"].configure(
    app=app,
    agent_registry=_AGENT_REGISTRY,
    verb_catalog=_VERB_CATALOG,
    scratchpads=_SCRATCHPADS,
    agent_lane=_agent_lane,
    agent_skill_tags=_agent_skill_tags,
    match_user_cfg=_match_user_cfg,
    cap_skills=_cap_skills,
    get_client=_get_client,
    api_require_auth=_API_REQUIRE_AUTH,
    client_env_var=_client_env_var,
    passport_load_priv=_passport_load_priv,
    passport_canonical_json=_passport_canonical_json,
    passport_kid=_passport_kid,
    passport_sign=_passport_sign,
    passport_verify=_passport_verify,
    passport_algo=PASSPORT_ALGO,
    passport_enable=PASSPORT_ENABLE,
    passport_agent_name=PASSPORT_AGENT_NAME,
)
app.include_router(a2a_router)

_DRIFT_AXIS_LABELS = {
    # Each axis names how to pull ONE label out of a satisfaction-verdict row.
    "verdict": lambda row: str(row.get("kind") or ""),
    "intent": lambda row: str((row.get("payload") or {}).get("refine_intent") or ""),
}

def _drift_payload(row) -> dict:
    """Normalize a verdict row's payload, which arrives as a dict from pg and
    as a JSON string from the legacy seam."""
    p = (row or {}).get("payload")
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except Exception:  # noqa: BLE001
            return {}
    return p if isinstance(p, dict) else {}

def _drift_live_window(rows: list, axis: str):
    """Fold verdict rows into one axis's (distribution, observations).
    Empty labels and unknown axes yield nothing to compare; see ch53."""
    from mios_pipe.observability import drift_monitor as _drift
    pick = _DRIFT_AXIS_LABELS.get(axis)
    if pick is None:
        return {}, 0
    labels = []
    for row in rows:
        row = {**row, "payload": _drift_payload(row)}
        try:
            label = pick(row)
        except Exception:  # noqa: BLE001
            continue
        if label:
            labels.append(label)
    return _drift.histogram(labels), len(labels)

async def _drift_baseline(axis: str) -> dict:
    """The frozen reference distribution for one axis, or {} when none exists
    yet. Degrades to {} on any DB slip -- an observe-only alarm never 500s."""
    sql = ("SELECT dist FROM drift_snapshot WHERE axis = '" + str(axis) + "' "
           "AND kind = 'baseline' ORDER BY ts DESC LIMIT 1;")
    try:
        r = await _db_read(sql, pg_sql=(
            "SELECT dist FROM drift_snapshot WHERE axis = %(axis)s "
            "AND kind = 'baseline' ORDER BY ts DESC LIMIT 1"),
            pg_params={"axis": str(axis)})
    except Exception:  # noqa: BLE001
        return {}
    rows = ((r or [{}])[-1] or {}).get("result") or []
    if not isinstance(rows, list) or not rows:
        return {}
    d = (rows[0] or {}).get("dist")
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except Exception:  # noqa: BLE001
            return {}
    return d if isinstance(d, dict) else {}

def _drift_snapshot(axis: str, dist: dict, samples: int, kind: str) -> None:
    """Record one drift_snapshot row through the unified write seam.
    Best-effort: a failed write re-seeds on the next poll, never 500s."""
    try:
        _db_write("drift_snapshot", {
            "kind": kind,
            "axis": str(axis),
            "dist": dist,
            "samples": int(samples),
            "source": "mios-agent-pipe-drift",
        }, now_fields=("ts",))
    except Exception:  # noqa: BLE001
        log.debug("drift: could not record %s snapshot for %s", kind, axis)

@app.get("/v1/drift")
async def v1_drift() -> JSONResponse:
    """CONS-02 Goodhart alarm: JSD of each live verdict/intent window against
    its frozen baseline. Observe-only; see manual ch53."""
    from mios_pipe.observability import drift_monitor as _drift
    if not DRIFT_MONITOR_ENABLED:
        return JSONResponse({"enabled": False, "axes": {}, "alerting": False})
    try:
        rows = await _recent_satisfaction_verdicts(int(DRIFT_MONITOR_WINDOW))
    except Exception:  # noqa: BLE001
        rows = []
    axes = DRIFT_MONITOR_AXES or list(_DRIFT_AXIS_LABELS)
    baseline: dict = {}
    live: dict = {}
    counts: dict = {}
    seeded: list = []
    for axis in axes:
        live_dist, n = _drift_live_window(rows, axis)
        live[axis] = live_dist
        counts[axis] = n
        base = await _drift_baseline(axis)
        if not base and live_dist:
            # A window compared against itself scores 0.0, so seeding the
            # baseline here starts the alarm quiet rather than self-firing.
            _drift_snapshot(axis, live_dist, n, "baseline")
            seeded.append(axis)
            base = live_dist
        elif live_dist:
            _drift_snapshot(axis, live_dist, n, "sample")
        baseline[axis] = base
    report = _drift.compare(
        baseline, live,
        threshold=float(DRIFT_MONITOR_THRESHOLD),
        min_samples=int(DRIFT_MONITOR_MIN_SAMPLES),
        live_counts=counts)
    if _drift.is_alerting(report):
        _emit_session_event({
            "kind": "drift_alert",
            "summary": (f"verdict-distribution drift on '{report['max_axis']}': "
                        f"JSD {report['max_divergence']:.3f} >= "
                        f"{report['threshold']:.3f}"),
            "payload": report,
            "source": "mios-agent-pipe-drift",
        }, None)
    return JSONResponse({"enabled": True, "seeded": seeded,
                         "window": int(DRIFT_MONITOR_WINDOW),
                         "samples": counts, **report})

@app.get("/v1/agents")
async def v1_agents_directory(request: Request) -> JSONResponse:
    try:
        _card = _build_agent_card()
    except Exception:  # noqa: BLE001 -- discovery must never 500 on a card slip
        _card = {}
    _prov = (_card.get("provider") or {}) if isinstance(_card, dict) else {}
    author = _prov.get("organization") or os.environ.get(
        "MIOS_A2A_AGENT_NAME", "MiOS")
    node_version = (_card.get("version") if isinstance(_card, dict) else None) \
        or app.version
    base = str(request.base_url).rstrip("/")
    node_card = f"{base}/.well-known/agent-card.json"
    _remote_kinds = ("remote-http", "a2a", "edge", "node", "mobile")
    roster: list[dict] = []
    for name, cfg in (_AGENT_REGISTRY or {}).items():
        cfg = cfg if isinstance(cfg, dict) else {}
        ep = str(cfg.get("endpoint", "")).rstrip("/")
        is_remote = str(cfg.get("kind", "")).lower() in _remote_kinds and bool(ep)

        peer_id = cfg.get("a2a_peer_id")
        cardless = False
        if peer_id:
            try:
                import sys
                a2a_peers = getattr(sys.modules.get("mios_pipe.federation.a2a_client"), "_A2A_PEERS", {})
                peer_state = a2a_peers.get(peer_id)
                if peer_state and isinstance(peer_state.get("card"), dict) and peer_state["card"].get("_cardless"):
                    cardless = True
            except Exception:
                pass

        card_url = node_card
        if is_remote:
            if cardless:
                card_url = f"{ep}/v1/models"
            else:
                card_url = f"{ep}/.well-known/agent-card.json"

        caps = cfg.get("strengths") or []
        if not isinstance(caps, list):
            caps = [caps] if caps else []

        roster.append({
            "author": author,
            "name": name,
            "version": node_version,
            "role": str(cfg.get("role", "general")),
            "kind": str(cfg.get("kind", "")),
            "capabilities": [str(c) for c in caps],
            "card": card_url,
            "a2a": f"{ep}/a2a" if is_remote else f"{base}/a2a",
        })
    return JSONResponse({
        "object": "list",
        "provider": {"organization": author},
        "version": node_version,
        "count": len(roster),
        "card": node_card,
        "data": sorted(roster, key=lambda a: a["name"]),
    })

KERNEL_ROUTE = (
    str(os.environ.get("MIOS_KERNEL_ROUTE")
        or _DISPATCH_TOML.get("kernel_route", "false"))
    .strip().lower() not in {"false", "0", "no", "off", ""})

KERNEL_DISPATCH = (
    str(os.environ.get("MIOS_KERNEL_DISPATCH")
        or _DISPATCH_TOML.get("kernel_dispatch", "false"))
    .strip().lower() not in {"false", "0", "no", "off", ""})

async def _kernel_dag_handler(decision, *, refined=None, session_id=None, **ctx):
    """Dispatcher 'dag' handler -> the real DAG runner (a genuine Stage-2
    delegation; the other modes' bodies are still inline -> Stage 2b)."""
    import mios_pipe.routing.conductor as mios_conductor
    from mios_pipe.kernel.config import _toml_section
    _orch = _toml_section("orchestration")
    conductor_enable = str(_orch.get("conductor_enable", "false")).lower() in {"true", "1", "yes", "on"}

    if conductor_enable and refined and "workflow" in refined:
        mios_conductor.configure(dispatch_verb=dispatch_mios_verb)
        return await mios_conductor.execute_conductor_workflow(
            refined["workflow"], refined.get("params", {}), session_id=session_id)
    return await execute_dag(refined or {}, session_id=session_id)

def _kernel_stage2b(mode: str):
    async def _handler(decision, **ctx):
        raise NotImplementedError(
            f"kernel execution for mode {mode!r} has no registered dispatcher handler in chat.py.")
    return _handler

_KERNEL = mios_kernel.Kernel(
    router=mios_router.Router(),
    dispatcher=mios_dispatcher.Dispatcher({
        "dag": _kernel_dag_handler,
        "chat": _kernel_stage2b("chat"),
        "dispatch": _kernel_stage2b("dispatch"),
        "multi_task": _kernel_stage2b("multi_task"),
        "agent": _kernel_stage2b("agent"),
    }, default_mode="agent"),
    scheduler=_GLOBAL_PRIORITY_GATE,        # SchedulerManager seam (live gate + RR)
    memory=_MEMORY,                          # MemoryManager seam (pgvector provider)
    context={"kv_paging": KV_PAGING_ENABLE},  # ContextManager seam (KV/tokenize)
    tools=_VERB_CATALOG,                     # ToolManager seam (verb surface)
    access=_pdp)                             # AccessManager seam (PDP gate)

_OFFLINE_ENFORCE = os.environ.get(
    "MIOS_OFFLINE_ENFORCE", "true").lower() not in {"false", "0", "no"}

def _is_local_endpoint(url: str) -> bool:
    """True if `url`'s host is LOCAL to the operator (loopback / tailnet /
    private LAN / container DNS), False for a public/cloud host. Conservative:
    an unparseable or empty url is treated as local (it's not a cloud egress)."""
    if not url:
        return True
    try:
        host = url.split("://", 1)[-1].split("/", 1)[0].split("@")[-1]
        host = host.rsplit(":", 1)[0].strip("[]").lower()  # strip :port + ipv6 brackets
    except Exception:  # noqa: BLE001
        return True
    if not host:
        return True
    if host in ("localhost", "0.0.0.0", "::1") or host.startswith("127."):
        return True
    if "." not in host and ":" not in host:
        return True
    if host == "host.containers.internal" or host.endswith(".ts.net") \
            or host.endswith(".local") or host.endswith(".internal"):
        return True
    parts = host.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        a, b = int(parts[0]), int(parts[1])
        if a == 10:
            return True                       # 10.0.0.0/8
        if a == 192 and b == 168:
            return True                       # 192.168.0.0/16
        if a == 172 and 16 <= b <= 31:
            return True                       # 172.16.0.0/12
        if a == 100 and 64 <= b <= 127:
            return True                       # 100.64.0.0/10 tailnet
        return False                          # any other public IPv4
    return False

def _offline_posture() -> dict:
    """Classify every configured inference/embedding endpoint + agent binding
    as local-or-external. Used by the startup guard + /v1/offline-status."""
    checks: list = []

    def _add(role: str, url: str) -> None:
        checks.append({"role": role, "endpoint": url or "",
                       "local": _is_local_endpoint(url or "")})

    _add("refine", REFINE_ENDPOINT)
    _add("polish", POLISH_ENDPOINT)
    _add("router", ROUTER_ENDPOINT)
    _add("planner", PLANNER_ENDPOINT)
    _add("micro", _MICRO_ENDPOINT)
    _add("verb_embed", _VERB_EMBED_URL)
    try:
        _add("backend", BACKEND_ENDPOINT)
    except NameError:
        pass
    for name, cfg in (_AGENT_REGISTRY or {}).items():
        _add(f"agent:{name}", cfg.get("endpoint") or "")
        if cfg.get("cpu_endpoint"):
            _add(f"agent:{name}.cpu", cfg.get("cpu_endpoint"))
    external = [c for c in checks if not c["local"]]
    return {
        "enforced": _OFFLINE_ENFORCE,
        "offline": not external,
        "external_endpoints": external,
        "checks": checks,
    }

_MCP_CLIENT_TOOLS: dict = {}      # "mcp.<sid>.<tool>" -> tool metadata
_MCP_CLIENT_LOCK = asyncio.Lock()

from mios_mcp import (  # noqa: E402
    _MCP_REGISTRY_PATHS,
    _MCP_CLIENT_SERVERS,
    _MCP_STDIO_CLIENTS,
    _MCP_ENV_RE,
    _mcp_load_registry,
    _mcp_render_headers,
    _mcp_http_rpc,
    _McpStdioClient,
    _mcp_probe_stdio,
    _mcp_probe_server,
    _mcp_client_startup,
    _mcp_call_tool,
    mcp_router,
    mcp_clients,
    mcp_tools_list,
    mcp_dispatch,
)
app.include_router(mcp_router)

_A2A_PEER_REGISTRY_PATHS = [
    "/usr/share/mios/ai/v1/a2a-peers.json",                          # vendor
    "/etc/mios/ai/v1/a2a-peers.json",                                # host
    os.path.expanduser("~/.config/mios/ai/v1/a2a-peers.json"),       # user
]
_A2A_PEERS: dict = {}             # peer_id -> {url, status, card, skills, …}
_A2A_PEER_SKILLS: dict = {}       # skill_id -> [peer_id, …]
_A2A_PEERS_LOCK = asyncio.Lock()
try:
    _A2A_CFG = _toml_section("a2a") or {}
except Exception:  # noqa: BLE001
    _A2A_CFG = {}
A2A_COUNCIL = os.environ.get(
    "MIOS_A2A_COUNCIL", str(_A2A_CFG.get("council", "false"))
).strip().lower() in ("1", "true", "yes", "on")
A2A_SELF_ID = str(os.environ.get(
    "MIOS_A2A_SELF_ID", _A2A_CFG.get("self_id", "local-mios"))).strip().lower()

from mios_a2a_client import (   # noqa: E402
    _a2a_self_peer_url,
    _a2a_fetch_card,
    _a2a_tailnet_candidates,
    _a2a_load_peers,
    _a2a_probe_peer,
    _a2a_autodiscover_peers,
    _a2a_client_startup,
    _a2a_send_message_to_peer,
    _a2a_extract_text,
)
sys.modules["mios_a2a_client"].configure(
    a2a_peers=_A2A_PEERS,
    a2a_peer_skills=_A2A_PEER_SKILLS,
    a2a_peers_lock=_A2A_PEERS_LOCK,
    a2a_reputation=_A2A_REPUTATION,
    agent_registry=_AGENT_REGISTRY,
    a2a_peer_registry_paths=_A2A_PEER_REGISTRY_PATHS,
    a2a_council=A2A_COUNCIL,
    a2a_self_id=A2A_SELF_ID,
    get_client=_get_client,
    route_on_card_skills=_ROUTE_ON_CARD_SKILLS,
    invalidate_worker_cache=lambda: globals().__setitem__(
        "_WORKER_TOOLS_FULL_CACHE", None),
)
sys.modules["mios_a2a"].configure(
    a2a_peers=_A2A_PEERS,
    a2a_peer_skills=_A2A_PEER_SKILLS,
    a2a_peers_lock=_A2A_PEERS_LOCK,
    a2a_reputation=_A2A_REPUTATION,
    a2a_send_message_to_peer=_a2a_send_message_to_peer,
    passport_load_public=_passport_load_public,
)
globals()["a2a_jsonrpc_logic"] = sys.modules["mios_a2a"].a2a_jsonrpc_logic
globals()["a2a_skills_list_logic"] = sys.modules["mios_a2a"].a2a_skills_list_logic
globals()["a2a_dispatch_logic"] = sys.modules["mios_a2a"].a2a_dispatch_logic
globals()["passport_verify_logic"] = sys.modules["mios_a2a"].passport_verify_logic
globals()["passport_public_key_logic"] = (
    sys.modules["mios_a2a"].passport_public_key_logic)

MEMBERSHIP_WATCH_ENABLE = str(
    os.environ.get("MIOS_MEMBERSHIP_WATCH")
    or (_A2A_CFG.get("membership_watch", "true"))).strip().lower() in {"1", "true", "yes"}
try:
    MEMBERSHIP_WATCH_INTERVAL_S = int(
        os.environ.get("MIOS_MEMBERSHIP_WATCH_INTERVAL")
        or (_A2A_CFG.get("membership_watch_interval_s", 30)))
except (TypeError, ValueError):
    MEMBERSHIP_WATCH_INTERVAL_S = 30
_MEMBERSHIP_WATCH_PATHS = list(_A2A_PEER_REGISTRY_PATHS) + [
    "/usr/share/mios/mios.toml", "/etc/mios/mios.toml",
    os.path.expanduser("~/.config/mios/mios.toml")]

async def _reload_membership(reason: str = "manual") -> dict:
    """Re-read the agent/node registry + A2A peer registry from disk and refresh the
    LIVE module caches WITHOUT a restart (FED-G3). Removes 'restart to add an agent'.
    Degrade-open: a partial failure logs + still refreshes what it can."""
    global _AGENT_REGISTRY, _WORKER_TOOLS_FULL_CACHE
    out: dict = {"reason": reason}
    try:
        _reg = _load_agent_registry()
        _load_node_pool(_reg)
        _AGENT_REGISTRY = _reg
        _rebuild_blade_topology()
        sys.modules["mios_fanout"].configure(agent_registry=_AGENT_REGISTRY)
        sys.modules["mios_refine"].configure(agent_registry=_AGENT_REGISTRY)
        sys.modules["mios_planner"].configure(agent_registry=_AGENT_REGISTRY)
        sys.modules["mios_agent_call"].configure(agent_registry=_AGENT_REGISTRY)
        sys.modules["mios_policy"].configure(agent_registry=_AGENT_REGISTRY)
        sys.modules["mios_dispatch"].configure(agent_registry=_AGENT_REGISTRY)
        sys.modules["mios_dag_exec"].configure(agent_registry=_AGENT_REGISTRY)
        sys.modules["mios_swarm"].configure(agent_registry=_AGENT_REGISTRY)
        sys.modules["mios_chat"].configure(_AGENT_REGISTRY=_AGENT_REGISTRY)
        sys.modules["mios_turn"].configure(_AGENT_REGISTRY=_AGENT_REGISTRY)
        sys.modules["mios_portal"].configure(agent_registry=_AGENT_REGISTRY)
        sys.modules["mios_agentreg"].configure(agent_registry=_AGENT_REGISTRY)
        out["agents"] = len(_reg)
    except Exception as e:  # noqa: BLE001
        out["agents_error"] = f"{type(e).__name__}: {e}"[:160]
        log.warning("membership reload: agent registry refresh failed: %s", e)
    try:
        async with _A2A_PEERS_LOCK:
            _A2A_PEERS.clear()          # drop stale peers; re-probe repopulates the live ones
        await _a2a_client_startup()     # re-load + re-probe from disk
        out["a2a_peers"] = len(_A2A_PEERS)
    except Exception as e:  # noqa: BLE001
        out["a2a_error"] = f"{type(e).__name__}: {e}"[:160]
        log.warning("membership reload: a2a peer refresh failed: %s", e)
    _WORKER_TOOLS_FULL_CACHE = None      # force re-merge of the worker tool surface
    log.info("membership reloaded (%s): %s", reason, out)
    return out

from mios_daemons import _membership_watch_loop   # noqa: E402

sys.modules["mios_a2a"].configure(
    check_inbound_principal=_check_inbound_principal,
    reload_membership=_reload_membership,
)

from mios_daemons import _gossip_loop   # noqa: E402

REPUTATION_FLUSH_S = _dispatch_num("MIOS_REPUTATION_FLUSH_S", "reputation_flush_s",
                                   300.0, cast=float)

from mios_daemons import _reputation_restore, _reputation_flush   # noqa: E402

_SELFIMPROVE_SEEN: set = set()

sys.modules["mios_daemons"].configure(
    _get_client=_get_client,
    _A2A_PEERS=_A2A_PEERS,
    _A2A_PEERS_LOCK=_A2A_PEERS_LOCK,
    _A2A_REPUTATION=_A2A_REPUTATION,
    _reload_membership=_reload_membership,
    _SELFIMPROVE_SEEN=_SELFIMPROVE_SEEN,
    _MEMBERSHIP_WATCH_PATHS=_MEMBERSHIP_WATCH_PATHS,
    MEMBERSHIP_WATCH_INTERVAL_S=MEMBERSHIP_WATCH_INTERVAL_S,
    _PG_PRIMARY=_PG_PRIMARY,
    KV_SLOTS_DIR=KV_SLOTS_DIR,
    KV_GC_TTL_S=KV_GC_TTL_S,
    KV_GC_MAX_BYTES=KV_GC_MAX_BYTES,
    KV_GC_INTERVAL_S=KV_GC_INTERVAL_S,
    _KV_RESIDENT=_KV_RESIDENT,
    MEMORY_CONSOLIDATE_ENABLED=MEMORY_CONSOLIDATE_ENABLED,
    MEMORY_CONSOLIDATE_INTERVAL_S=MEMORY_CONSOLIDATE_INTERVAL_S,
    MEMORY_CONSOLIDATE_MAX_GROUPS=MEMORY_CONSOLIDATE_MAX_GROUPS,
)
from mios_daemons import _selfimprove_loop, _selfimprove_report   # noqa: E402
from mios_daemons import (daemons_router, selfimprove_report_ep,   # noqa: E402,F401
                          selfimprove_proposals_ep)
app.include_router(daemons_router)

_VERB_EMBED_MODEL = os.environ.get(
    "MIOS_AI_EMBED_MODEL", "nomic-embed-text")
_VERB_EMBED_URL = os.environ.get(
    "MIOS_VERB_EMBED_URL", _LIGHT_BASE + "/v1/embeddings")

_EMBED_FAIL_LOG_INTERVAL = float(
    os.environ.get("MIOS_EMBED_FAIL_LOG_INTERVAL", "60") or 60)
_embed_fail_last_log = 0.0
_embed_fail_suppressed = 0

async def _embed_one(text: str, prefix: Optional[str] = "search_query: ") -> Optional[list[float]]:
    """Single-vector embed over OpenAI /v1/embeddings ({input} -> {data:[{embedding}]})
    -- MiOS is /v1-only; the nomic lane runs on llama.cpp (mios-llm-light). Returns
    None on failure (caller falls back to substring match)."""
    if not text or not text.strip():
        return None
    if prefix and not text.startswith(prefix):
        text = prefix + text
    client = await _get_client()
    try:
        r = await client.post(
            _VERB_EMBED_URL,
            content=json.dumps(
                {"model": _VERB_EMBED_MODEL, "input": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        if r.status_code != 200:
            return None
        data = r.json()
        v = None
        _d = data.get("data")
        if isinstance(_d, list) and _d:
            v = _d[0].get("embedding")
        if isinstance(v, list) and v:
            return [float(x) for x in v]
    except Exception as e:
        global _embed_fail_last_log, _embed_fail_suppressed
        now = time.time()
        if now - _embed_fail_last_log >= _EMBED_FAIL_LOG_INTERVAL:
            if _embed_fail_suppressed:
                log.warning(
                    "embed call failed: %s (+%d more suppressed in last %.0fs)",
                    e, _embed_fail_suppressed, _EMBED_FAIL_LOG_INTERVAL)
            else:
                log.warning("embed call failed: %s", e)
            _embed_fail_last_log = now
            _embed_fail_suppressed = 0
        else:
            _embed_fail_suppressed += 1
    return None

from mios_toolsearch import (  # noqa: E402
    _cosine,
    _verb_embed_text,
    _verb_embed_fingerprint,
)

sys.modules["mios_knowledge"].configure(   # noqa: E402
    db_fire=_db_fire,
    db_post=_db_post,
    db_create=_db_create,
    db_update=_db_update,
    db_read=_db_read,
    pg_mirror=_pg_mirror,
    recent_satisfaction_verdicts=_recent_satisfaction_verdicts,
    embed_one=_embed_one,
    cosine=_cosine,
    anchor_tokens=_anchor_tokens,
    shares_anchor=_shares_anchor,
    memory=_MEMORY,
    pg_primary=_PG_PRIMARY,
    turn_volatile_var=_turn_volatile_var,
    client_env_var=_client_env_var,
    agent_memory_recall_enabled=AGENT_MEMORY_RECALL_ENABLED,
    agent_memory_table=AGENT_MEMORY_TABLE,
    agent_memory_recall_k=AGENT_MEMORY_RECALL_K,
    agent_memory_recall_min_score=AGENT_MEMORY_RECALL_MIN_SCORE,
    recall_possessive_re=_RECALL_POSSESSIVE_RE,
    knowledge_url_re=_KNOWLEDGE_URL_RE,
    emb_model=EMB_MODEL,
    emb_version=EMB_VERSION,
    knowledge_table=KNOWLEDGE_TABLE,
    knowledge_store_enabled=KNOWLEDGE_STORE_ENABLED,
    knowledge_store_skip_volatile=KNOWLEDGE_STORE_SKIP_VOLATILE,
    knowledge_store_gate_unsatisfied=KNOWLEDGE_STORE_GATE_UNSATISFIED,
    knowledge_answer_max=KNOWLEDGE_ANSWER_MAX,
    memory_guard_mode=MEMORY_GUARD_MODE,
    knowledge_recall_enabled=KNOWLEDGE_RECALL_ENABLED,
    knowledge_recall_k=KNOWLEDGE_RECALL_K,
    knowledge_recall_candidates=KNOWLEDGE_RECALL_CANDIDATES,
    knowledge_recall_min_score=KNOWLEDGE_RECALL_MIN_SCORE,
    knowledge_recall_pref_min_score=KNOWLEDGE_RECALL_PREF_MIN_SCORE,
    knowledge_recall_strict_score=KNOWLEDGE_RECALL_STRICT_SCORE,
    knowledge_rank_outcome=KNOWLEDGE_RANK_OUTCOME,
    knowledge_rank_hot=KNOWLEDGE_RANK_HOT,
    knowledge_rank_access=KNOWLEDGE_RANK_ACCESS,
    knowledge_rank_age=KNOWLEDGE_RANK_AGE,
    knowledge_recall_halflife_days=KNOWLEDGE_RECALL_HALFLIFE_DAYS,
    knowledge_hot_threshold=KNOWLEDGE_HOT_THRESHOLD,
    knowledge_evict_enable=KNOWLEDGE_EVICT_ENABLE,
    knowledge_evict_min_access=KNOWLEDGE_EVICT_MIN_ACCESS,
    knowledge_evict_ttl_days=KNOWLEDGE_EVICT_TTL_DAYS,
    knowledge_evict_max_rows=KNOWLEDGE_EVICT_MAX_ROWS,
    knowledge_evict_batch=KNOWLEDGE_EVICT_BATCH,
    knowledge_evict_interval_s=KNOWLEDGE_EVICT_INTERVAL_S,
    knowledge_rag_hybrid=(os.environ.get("MIOS_RAG_HYBRID", "").strip().lower() not in ("0", "false", "no") if "MIOS_RAG_HYBRID" in os.environ else _toml_section("ai").get("rag_hybrid", False)),
    knowledge_rag_rerank=(os.environ.get("MIOS_RAG_RERANK", "").strip().lower() not in ("0", "false", "no") if "MIOS_RAG_RERANK" in os.environ else _toml_section("ai").get("rag_rerank", False)),
)

sys.modules["mios_web_research"].configure(   # noqa: E402
    is_action_domain=_is_action_domain,
    current_date_str=_current_date_str,
    current_year=_current_year,
    routed_domain_var=_routed_domain_var,
    client_env_var=_client_env_var,
    sources_var=_sources_var,
    conv_key_var=_conv_key_var,
    src_turn_var=_src_turn_var,
    sources_registry=_SOURCES_REGISTRY,
    sources_registry_cap=_SOURCES_REGISTRY_CAP,
    max_sources=MAX_SOURCES,
    web_enrich_verbs=_WEB_ENRICH_VERBS,
    location_sensitive_phrases=_LOCATION_SENSITIVE_PHRASES,
    judge_model=_JUDGE_MODEL,
    judge_endpoint=_JUDGE_ENDPOINT,
    web_research_enabled=WEB_RESEARCH_ENABLED,
    web_research_passes=WEB_RESEARCH_PASSES,
    web_research_results=WEB_RESEARCH_RESULTS,
    web_research_fanout=WEB_RESEARCH_FANOUT,
    web_research_fetch_n=WEB_RESEARCH_FETCH_N,
    web_research_fetch_chars=WEB_RESEARCH_FETCH_CHARS,
    web_research_block_chars=WEB_RESEARCH_BLOCK_CHARS,
    web_research_search_timeout=WEB_RESEARCH_SEARCH_TIMEOUT,
    web_research_fetch_timeout=WEB_RESEARCH_FETCH_TIMEOUT,
    web_research_crawl_fallback=WEB_RESEARCH_CRAWL_FALLBACK,
    web_research_min_chars=WEB_RESEARCH_MIN_CHARS,
    web_research_crawl_timeout=WEB_RESEARCH_CRAWL_TIMEOUT,
    web_research_crawl_max=WEB_RESEARCH_CRAWL_MAX,
    web_research_use_news_category=WEB_RESEARCH_USE_NEWS_CATEGORY,
    web_research_time_range=WEB_RESEARCH_TIME_RANGE,
    web_research_recency_range=(os.environ.get("MIOS_WEB_RESEARCH_RECENCY_RANGE", "").strip()
                                or str(_WEB_TOML.get("recency_range") or "month")),
    web_research_max_attempts=WEB_RESEARCH_MAX_ATTEMPTS,
)

sys.modules["mios_worker_tools"].configure(
    verb_catalog=_VERB_CATALOG,
    resolve_verb_key=_resolve_verb_key,
    cosine=_cosine,
    verb_embed_fingerprint=_verb_embed_fingerprint,
    verb_embed_text=_verb_embed_text,
    tool_rerank=TOOL_RERANK,
    rerank_fanout=RERANK_FANOUT,
    rerank_min_k=RERANK_MIN_K,
    rerank_rrf_k=RERANK_RRF_K,
    rerank_mmr_lambda=RERANK_MMR_LAMBDA,
    rerank_skip_margin=RERANK_SKIP_MARGIN,
    bm25_k1=float(os.environ.get("MIOS_BM25_K1",
                  str((_toml_section("worker_tools") or {}).get("bm25_k1", 1.2))) or 1.2),
    bm25_b=float(os.environ.get("MIOS_BM25_B",
                 str((_toml_section("worker_tools") or {}).get("bm25_b", 0.75))) or 0.75),
    priority_fallback_scores=((_toml_section("worker_tools") or {}).get("priority_fallback_scores")
                              or [0.55, 0.45, 0.30, 0.25, 0.15]),
    tool_priority_core_first=str(
        os.environ.get("MIOS_TOOL_PRIORITY_CORE_FIRST")
        or (_toml_section("worker_tools") or {}).get("tool_priority_core_first", True)
    ).strip().lower() not in {"false", "0", "no"},
)

sys.modules["mios_toolexec"].configure(
    read_tool_enrich_chars=READ_TOOL_ENRICH_CHARS,
    read_tool_enrich_timeout=READ_TOOL_ENRICH_TIMEOUT,
    aci_max_lines=ACI_MAX_LINES,
    aci_head_frac=ACI_HEAD_FRAC,
    code_mode_enable=CODE_MODE_ENABLE,
    code_mode_heavy_only=CODE_MODE_HEAVY_ONLY,
    max_dispatch_depth=MAX_DISPATCH_DEPTH,
    verb_catalog=_VERB_CATALOG,
    recipe_catalog=_RECIPE_CATALOG,
    high_privilege_verbs=_HIGH_PRIVILEGE_VERBS,
    web_enrich_verbs=_WEB_ENRICH_VERBS,
    orch_ctx_var=_orch_ctx_var,
    dispatch_mios_verb=dispatch_mios_verb,
    mcp_call_tool=_mcp_call_tool,
    classify_verb_taint=_classify_verb_taint,
    sanitize_tool_text=_sanitize_tool_text,
    plan_swarm=_plan_swarm,
    live_agent_names=_live_agent_names,
    agent_dag_from_tasks=_agent_dag_from_tasks,
    respond_agent_dag=_respond_agent_dag,
    depth_exhausted=_depth_exhausted,
    dispatch_depth=_dispatch_depth,
    enter_dispatch_hop=_enter_dispatch_hop,
    resolve_verb_key=_resolve_verb_key,
    session_is_tainted=_session_is_tainted,
    db_fire=_db_fire,
    db_post=_db_post,
    db_create=_db_create,
    src_record=_src_record,
    otel_tracer=_otel_tracer,
)

sys.modules["mios_firewall"].configure(
    taint_verbs=_TAINT_VERBS,
    provenance_taint_enable=PROVENANCE_TAINT_ENABLE,
    allowlist_hosts=_ALLOWLIST_HOSTS,
    mcp_client_tools=_MCP_CLIENT_TOOLS,
    db_read=_db_read,
    text_view_taint_prefixes=((_toml_section("security") or {}).get(
        "text_view_taint_prefixes") or None),
    internal_tld_suffixes=((_toml_section("security") or {}).get(
        "internal_tld_suffixes") or None),
)

sys.modules["mios_policy"].configure(
    verb_catalog=_VERB_CATALOG,
    recipe_catalog=_RECIPE_CATALOG,
    agent_registry=_AGENT_REGISTRY,
    hitl_approved_var=_hitl_approved_var,
    hitl_blocked_var=_hitl_blocked_var,
    client_env_var=_client_env_var,
    dispatch_agent_var=_dispatch_agent_var,
    pending_hash=_pending_hash,
    get_client=_get_client,
    db_fire=_db_fire,
    db_post=_db_post,
    db_create=_db_create,
)

sys.modules["mios_secondary_loop"].configure(
    secondary_tool_max_iters=SECONDARY_TOOL_MAX_ITERS,
    secondary_replan_max=SECONDARY_REPLAN_MAX,
    daemon_diagnose_model=_DAEMON_DIAGNOSE_MODEL,
    daemon_diagnose_endpoint=_DAEMON_DIAGNOSE_ENDPOINT,
    daemon_diagnose_enable=_DAEMON_DIAGNOSE_ENABLE,
    apply_outbound_auth=_apply_outbound_auth,
    endpoint_supports_parallel_tools=_endpoint_supports_parallel_tools,
    db_read=_db_read,
    db_create=_db_create,
    db_fire=_db_fire,
    db_post=_db_post,
)

sys.modules["mios_agent_call"].configure(
    healthgate_connect_timeout=HEALTHGATE_CONNECT_TIMEOUT,
    healthgate_read_timeout=HEALTHGATE_READ_TIMEOUT,
    secondary_tool_loop=SECONDARY_TOOL_LOOP,
    kv_fork_enable=KV_FORK_ENABLE,
    src_turn_header=_SRC_TURN_HEADER,
    agent_registry=_AGENT_REGISTRY,
    sloshed=_SloShed,
    admit=_admit,
    agent_binding=_agent_binding,
    agent_offload_engine=_agent_offload_engine,
    apply_outbound_auth=_apply_outbound_auth,
    conv_key_var=_conv_key_var,
    current_trace_id=_current_trace_id,
    dispatch_agent_var=_dispatch_agent_var,
    dispatch_priority=_dispatch_priority,
    endpoint_sem=_endpoint_sem,
    harvest_sub_sources=_harvest_sub_sources,
    hop_via_headers=_hop_via_headers,
    kv_fork_parent_var=_kv_fork_parent_var,
    lane_sem=_lane_sem,
    lane_sem_key=_lane_sem_key,
    model_active=_model_active,
    opt_int_mb=_opt_int_mb,
    priority_gate=_priority_gate,
    cost_accounting_enable=COST_ACCOUNTING_ENABLE,
    cost_ledger=_COST_LEDGER,
    cost_model=_COST_MODEL,
    is_remote_endpoint=_is_remote_endpoint,
    is_slow_lane_ep=_is_slow_lane_ep,
    node_live=_NODE_LIVE,
    llm_num_predict_cap=LLM_NUM_PREDICT_CAP,
    llm_num_predict_cap_cpu=LLM_NUM_PREDICT_CAP_CPU,
    should_health_probe=_should_health_probe,
    src_turn_key=_src_turn_key,
    strip_agent_chrome=_strip_agent_chrome,
    strip_think_tags=_strip_think_tags,
    v1_secondary_tool_loop=_v1_secondary_tool_loop,
    kv_paging_enable=KV_PAGING_ENABLE,
    kv_paging_slot=KV_PAGING_SLOT,
    kv_paging_timeout=KV_PAGING_TIMEOUT,
    kv_slot_persist=KV_SLOT_PERSIST,
    rr_enable=RR_ENABLE,
    priority_queue_enable=PRIORITY_QUEUE_ENABLE,
    rr_slice_tokens=RR_SLICE_TOKENS,
    rr_slice_timeout=RR_SLICE_TIMEOUT,
    rr_quantum_s=RR_QUANTUM_S,
    kv_locks=_KV_LOCKS,
    kv_resident=_KV_RESIDENT,
    backend_key=_BACKEND_KEY,
    global_priority_gate=_GLOBAL_PRIORITY_GATE,
    preempt=_PREEMPT,
    otel_tracer=_otel_tracer,
)

sys.modules["mios_dag_exec"].configure(
    deepen_fetch=DEEPEN_FETCH,
    deepen_deadline_s=DEEPEN_DEADLINE_S,
    deepen_max_iters=DEEPEN_MAX_ITERS,
    deepen_web_timeout_s=DEEPEN_WEB_TIMEOUT_S,
    deepen_early_exit=DEEPEN_EARLY_EXIT,
    deepen_judge_timeout_s=DEEPEN_JUDGE_TIMEOUT_S,
    judge_answer_satisfied=_judge_answer_satisfied,
    dag_node_max_tokens=DAG_NODE_MAX_TOKENS,
    dag_node_slow_max_tokens=DAG_NODE_SLOW_MAX_TOKENS,
    dag_node_retry=DAG_NODE_RETRY,
    dag_node_deadline_s=DAG_NODE_DEADLINE_S,
    dag_node_deadline_slow_s=DAG_NODE_DEADLINE_SLOW_S,
    slow_lanes=SLOW_LANES,
    kv_fork_enable=KV_FORK_ENABLE,
    worker_tools_enable=WORKER_TOOLS_ENABLE,
    worker_tool_ctx=WORKER_TOOL_CTX,
    worker_tool_ctx_slow=WORKER_TOOL_CTX_SLOW,
    planner_reflexion_cap=PLANNER_REFLEXION_CAP,
    swarm_saturate=SWARM_SATURATE,
    request_cancel_enable=REQUEST_CANCEL_ENABLE,
    request_cancel_poll_s=REQUEST_CANCEL_POLL_S,
    turn_deadline_s=TURN_DEADLINE_S,
    pg_primary=_PG_PRIMARY,
    ctx_fit=CTX_FIT,
    worker_tool_ctx_max=WORKER_TOOL_CTX_MAX,
    deepen_lanes=DEEPEN_LANES,
    runaway_reap_enable=RUNAWAY_REAP_ENABLE,
    light_lane=_LIGHT_LANE,
    agent_registry=_AGENT_REGISTRY,
    chat_cancel=_CHAT_CANCEL,
    kv_fork_parent_var=_kv_fork_parent_var,
    conv_key_var=_conv_key_var,
    dispatch_mios_verb=dispatch_mios_verb,
    call_agent_stream=_call_agent_stream,
    reflect_on_step_failure=reflect_on_step_failure,
    sanitize_tool_text=_sanitize_tool_text,
    scratchpad_note=_scratchpad_note,
    scratchpad_render=_scratchpad_render,
    agent_contract=_agent_contract,
    role_system=_role_system,
    agent_lane=_agent_lane,
    worker_tools_surface_async=_worker_tools_surface_async,
    lane_tool_cap=_lane_tool_cap,
    a2a_send_message_to_peer=_a2a_send_message_to_peer,
    a2a_extract_text=_a2a_extract_text,
    get_client=_get_client,
    db_fire=_db_fire,
    db_post=_db_post,
    db_create=_db_create,
    db_read=_db_read,
    pg_mirror=_pg_mirror,
)

from mios_toolsearch import (   # noqa: E402
    _VERB_EMBEDDINGS,
    _VERB_EMBEDDINGS_LOCK,
    _MCP_EMBEDDINGS,
    _tool_embedding,
    _mcp_embed_new_tools,
    _ensure_verb_embeddings,
    _load_persisted_embeddings,
    _save_persisted_embeddings,
    _refresh_app_inventory,
    _APP_EMBEDDINGS,
    _APP_INV_MTIME,
    _APP_INV_LOCK,
    _APP_INV_CACHE_FILE,
    _APP_EMBED_PERSIST,
    _VERB_EMBED_PERSIST,
    toolsearch_router,
    tool_search,
    app_search,
)
sys.modules["mios_toolsearch"].configure(
    get_client=_get_client,
    verb_catalog=_VERB_CATALOG,
    mcp_client_tools=_MCP_CLIENT_TOOLS,
    mcp_client_lock=_MCP_CLIENT_LOCK,
    loads_lenient=_loads_lenient,
    embed_one=_embed_one,
)
app.include_router(toolsearch_router)

sys.modules["mios_mcp"].configure(
    get_client=_get_client,
    mcp_client_tools=_MCP_CLIENT_TOOLS,
    mcp_client_lock=_MCP_CLIENT_LOCK,
    mcp_embed_new_tools=_mcp_embed_new_tools,
    invalidate_worker_cache=lambda: globals().__setitem__(
        "_WORKER_TOOLS_FULL_CACHE", None),
)

from mios_portal import (  # noqa: E402
    PORTAL_PUBLIC_HOST, _portal_toml, _PORTAL_TOML, _pcfg, PORTAL_PASSWORD,
    PORTAL_USER, _portal_rl, PORTAL_REQUIRE_LOGIN, PORTAL_SESSION_TTL,
    PORTAL_COOKIE, _portal_secret_cfg, _PORTAL_SECRET, _portal_make_token,
    _portal_token_ok, _portal_authed, _portal_unit_hidden,
    _discover_portal_services, _PORTAL_SERVICES, _host_stats,
    _PODMAN_PS_SNAPSHOT, _podman_ps, _PORTAL_HTML, _portal_theme_css,
    _PORTAL_ICON, _read_portal_asset, _PORTAL_ICON_192, _PORTAL_ICON_512,
    _PORTAL_MANIFEST, _PORTAL_SW, _PORTAL_LOGIN_HTML, _IOSTEST_HTML,
    portal_router,
    portal_stats,
    portal_service_detail,
    portal_swarm,
    portal_icon,
    portal_icon_192,
    portal_icon_512,
    portal_manifest,
    portal_xterm_js,
    portal_xterm_css,
    portal_addon_fit,
    portal_term_ws,
    portal_login,
    portal_logout,
    portal_sw,
    portal_login_page,
    iostest_page,
    portal_page,
)
sys.modules["mios_portal"].configure(
    probe_auth_headers=_probe_auth_headers, agent_lane=_agent_lane,
    agent_registry=_AGENT_REGISTRY, sanitize_tool_text=_sanitize_tool_text,
    websockets=websockets)
app.include_router(portal_router)

from mios_http_caps import (  # noqa: E402
    _skill_to_mcp_resource, _recipe_to_mcp_resource, _verb_to_mcp_resource,
    http_caps_router, v1_peers, list_resources, read_resource,
    v1_capabilities, v1_capabilities_dag, v1_route, cost_ledger,
    trace_read, trace_recent, offline_status, prompt_registry_view,
    run_templates_list,
    list_verbs, list_verbs_openai_tools, list_tools, kg_lookup_endpoint,
    skills_list, skills_show, skills_run, skills_openai_tools,
    dci_deliberate, dci_schema,
    list_models, embeddings,
)
sys.modules["mios_http_caps"].configure(
    verb_catalog=_VERB_CATALOG, a2a_peers=_A2A_PEERS,
    a2a_peers_lock=_A2A_PEERS_LOCK, kernel=_KERNEL, cost_ledger=_COST_LEDGER,
    cost_model=_COST_MODEL, cost_accounting_enable=COST_ACCOUNTING_ENABLE,
    cost_budget_usd=COST_BUDGET_USD, tracer=_TRACER, backend=BACKEND,
    verb_to_openai_tool=_verb_to_openai_tool,
    recipe_to_openai_tool=_recipe_to_openai_tool,
    skill_to_openai_tool=_skill_to_openai_tool,
    load_recipe_catalog=_load_recipe_catalog, skill_list=_skill_list,
    skill_fetch=_skill_fetch, user_rbac_filter=_user_rbac_filter,
    match_user_cfg=_match_user_cfg, toml_section=_toml_section,
    cap_skills=_cap_skills, get_client=_get_client, kg_lookup=kg_lookup,
    execute_skill=execute_skill, run_dci_flow=run_dci_flow,
    offline_posture=_offline_posture,
    prompt_registry=_PROMPT_REGISTRY, db_read=_db_read,
    run_template_enable=RUN_TEMPLATE_ENABLE,
    mcp_client_tools=_MCP_CLIENT_TOOLS,
    mcp_client_lock=_MCP_CLIENT_LOCK)
app.include_router(http_caps_router)

from mios_audit import audit_router, chain_verify   # noqa: E402,F401
mios_audit.configure(chain_enable=AUDIT_CHAIN_ENABLE, pg_execute=_mios_pg.execute)
app.include_router(audit_router)

VISION_ENABLE = os.environ.get(
    "MIOS_AGENT_PIPE_VISION", "true").lower() not in ("0", "false", "no", "")
VISION_MODEL = os.environ.get("MIOS_AGENT_PIPE_VISION_MODEL", "qwen3-vl:4b")
VISION_ENDPOINT = os.environ.get(
    "MIOS_AGENT_PIPE_VISION_ENDPOINT", _LIGHT_BASE).rstrip("/")

from mios_vision import (   # noqa: E402  (R9: VISION responders, moved verbatim)
    _messages_have_image, _vision_backend_failed, _vision_msg_response,
    _vision_unavailable_response, _resolve_media_url_from_html,
    _vision_inline_remote_images, _vision_complete,
    _VISION_UNAVAILABLE_MSG, _VISION_FETCH_FAILED_MSG, _VISION_MAX_BYTES,
)

CUA_ENABLE = (
    str(os.environ.get("MIOS_CUA_ENABLE")
        or _DISPATCH_TOML.get("cua_enable", "false"))
    .strip().lower() not in {"false", "0", "no", "off", ""})
CUA_MAX_STEPS = _dispatch_num("MIOS_CUA_MAX_STEPS", "cua_max_steps", 12)

from mios_cua import (   # noqa: E402  (WS-8 computer-use I/O half, moved verbatim)
    _cua_extract_png, _cua_screenshot_uri, _cua_vlm_json, _cua_loop,
    cua_router, v1_computer_use,
)
sys.modules["mios_cua"].configure(
    cua_enable=CUA_ENABLE,
    dispatch_mios_verb_inner=_dispatch_mios_verb_inner,
    get_client=_get_client,
    vision_backend_failed=_vision_backend_failed,
    backend_key=_BACKEND_KEY,
    vision_model=VISION_MODEL,
    vision_endpoint=VISION_ENDPOINT,
    cua_max_steps=CUA_MAX_STEPS,
    hidpi_scale_factor=float(
        os.environ.get("MIOS_HIDPI_SCALE_FACTOR")
        or _toml_section("computer_use").get("hidpi_scale_factor", 1.0)
    ),
)
app.include_router(cua_router)

from mios_lanes_resolver import (   # noqa: E402  (lane-resolver cluster, moved verbatim)
    _heavy_lane_up, _lane_resolver, _pick_tool_backend, _heavy_probe, _LANE_RESOLVER,
)
sys.modules["mios_lanes_resolver"].configure(
    _get_client=_get_client,
    _is_remote_endpoint=_is_remote_endpoint,
)

from mios_vision import (   # noqa: E402  (R9: client-tools hybrid loop, moved verbatim)
    _has_client_tools, _client_tools_mios_surface, _client_tools_is_mios,
    _client_tools_inject_identity, _client_tools_backend, _client_tools_loop,
    _client_tools_wrap, _client_tools_sse, _name_is_verb,
    _client_tools_stream_relay, _client_tools_complete, _client_tools_relay,
    _CLIENT_TOOLS_IDENTITY,
)
sys.modules["mios_vision"].configure(
    vision_model=VISION_MODEL,
    vision_endpoint=VISION_ENDPOINT,
    backend_key=_BACKEND_KEY,
    default_tool_cap=DEFAULT_TOOL_CAP,
    verb_catalog=_VERB_CATALOG,
    get_client=_get_client,
    verb_to_openai_tool=_verb_to_openai_tool,
    resolve_verb_key=_resolve_verb_key,
    agent_contract=_agent_contract,
    pick_tool_backend=_pick_tool_backend,
    select_child_tools=_select_child_tools,
    tool_call_sig=_tool_call_sig,
)

sys.modules["mios_oscontrol"].configure(
    os_control_launch_verify_s=OS_CONTROL_LAUNCH_VERIFY_S,
    os_control_launch_poll_s=OS_CONTROL_LAUNCH_POLL_S,
    os_control_retry_attempts=OS_CONTROL_RETRY_ATTEMPTS,
    os_control_retry_settle_s=OS_CONTROL_RETRY_SETTLE_S,
    os_control_reply_max_tokens=OS_CONTROL_REPLY_MAX_TOKENS,
    os_control_enum_retry=OS_CONTROL_ENUM_RETRY,
    os_control_enum_timeout_s=OS_CONTROL_ENUM_TIMEOUT_S,
    os_control_enum_retry_settle_s=OS_CONTROL_ENUM_RETRY_SETTLE_S,
    os_control_action_verbs=_OS_CONTROL_ACTION_VERBS,
    launch_verbs=_LAUNCH_VERBS,
    conv_key_var=_conv_key_var,
    get_client=_get_client,
    scratchpad_note=_scratchpad_note,
    db_fire=_db_fire,
    db_post=_db_post,
    db_create=_db_create,
    inline_satisfaction_check=_inline_satisfaction_check,
    strip_think_tags=_strip_think_tags,
)

LOCAL_STATE_FASTPATH = os.environ.get(
    "MIOS_LOCAL_STATE_FASTPATH", "true").lower() not in {"false", "0", "no"}

_LOCAL_STATE_SYSTEM = (
    "You answer a question about THIS computer's OWN live state -- installed "
    "apps/games, hardware, running processes, open windows, containers -- using "
    "ONLY the LIVE TOOL OUTPUT provided, the AUTHORITATIVE freshly-collected "
    "ground truth for THIS machine.\n"
    "HARD RULES:\n"
    "- The tool output IS the answer. ENUMERATE every relevant item it lists "
    "(EVERY game across EVERY category -- windows-game / steam / epic / store / "
    "gog / flatpak -- EVERY app, etc.). Do NOT omit, sample, or shrink to a few.\n"
    "- NEVER claim something is 'not installed' / 'no games found' / 'not "
    "available' / 'no X detected' if it APPEARS in the output. Do NOT reason "
    "about what 'should' or 'could' exist from the OS type -- report ONLY what "
    "the output actually contains.\n"
    "- NEVER invent an entry that is not in the output.\n"
    "- A category with zero entries may be noted as empty, but you MUST still "
    "list every category that HAS entries.\n"
    "- Use the tool output's OWN section labels and units. Report each figure "
    "under the SAME category the output gives it -- never relabel (e.g. do NOT "
    "place GPU/VRAM figures under a 'CPU' heading). If a value's category is "
    "unclear or absent (e.g. the output carries no CPU-utilisation field), OMIT "
    "it rather than guess or borrow another section's number.\n"
    "- Present the items ONLY -- no meta-commentary about the DATA itself: no "
    "notes about duplicates, 'unique entries', counts you derived, parsing, "
    "formatting, or 'per output logic'. If an item appears twice, list it once.\n"
    "- Clean markdown (grouped lists or a table). No 'based on the telemetry' "
    "preamble, no narration. Reply in the user's language.\n")

def _polish_post(endpoint, model, messages, max_tokens, temperature=0.0):
    """(url, payload) for a polish/format call on an OpenAI /v1 endpoint. MiOS is
    /v1-only (llama.cpp / mios-llm-light), so this always targets
    /v1/chat/completions (normalising a trailing /v1 already on the endpoint)."""
    base = str(endpoint or "").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return (base + "/v1/chat/completions",
            {"model": model, "messages": messages, "stream": False,
             "max_tokens": max_tokens, "temperature": temperature})

sys.modules["mios_verity"].configure(   # noqa: E402
    refine_timeout_s=REFINE_TIMEOUT_S,
    refine_endpoint=REFINE_ENDPOINT,
    refine_model=REFINE_MODEL,
    web_enrich_verbs=_WEB_ENRICH_VERBS,
    web_research_search_timeout=WEB_RESEARCH_SEARCH_TIMEOUT,
    polish_enabled=POLISH_ENABLED,
    polish_system=_POLISH_SYSTEM,
    polish_endpoint=POLISH_ENDPOINT,
    polish_model=POLISH_MODEL,
    polish_max_tokens=POLISH_MAX_TOKENS,
    polish_timeout_s=POLISH_TIMEOUT_S,
    ask_clarify_judge_enable=ASK_CLARIFY_JUDGE_ENABLE,
    polish_post=_polish_post,
    recent_tool_history=_recent_tool_history,
    format_tool_history=_format_tool_history,
    recent_satisfaction_verdicts=_recent_satisfaction_verdicts,
    format_satisfaction_block=_format_satisfaction_block,
    store_knowledge=_store_knowledge,
    write_skill_md_fire=_write_skill_md_fire,
    proposal_var=_proposal_var,
    abbreviations=(_toml_section("verity").get("sentence_abbreviations") or None),
)

sys.modules["mios_reflect"].configure(   # noqa: E402
    db_read=_db_read,
    db_write=_db_write,
    emit_session_event=_emit_session_event,
    verb_catalog=_VERB_CATALOG,
    refine_enabled=REFINE_ENABLED,
    refine_model=REFINE_MODEL,
    refine_endpoint=REFINE_ENDPOINT,
    refine_timeout_s=REFINE_TIMEOUT_S,
    reflect_system=_REFLECT_SYSTEM,
    judge_examples=JUDGE_EXAMPLES,
    consensus_enabled=CONSENSUS_ENABLED,
    consensus_lanes=CONSENSUS_LANES,
    consensus_threshold=CONSENSUS_THRESHOLD,
    consensus_min_lanes=CONSENSUS_MIN_LANES,
    consensus_timeout_s=CONSENSUS_TIMEOUT_S,
    consensus_weight_floor=CONSENSUS_WEIGHT_FLOOR,
)

NATIVE_LOOP_ENABLE = str(
    os.environ.get("MIOS_NATIVE_LOOP", "true")).strip().lower() not in {"false", "0", "no"}
NATIVE_LOOP_TIMEOUT_S = int(os.environ.get("MIOS_NATIVE_LOOP_TIMEOUT_S", "120") or 120)
NATIVE_LOOP_STREAM_TOKENS = os.environ.get(
    "MIOS_NATIVE_LOOP_STREAM_TOKENS", "true").strip().lower() not in ("0", "false", "no")
NATIVE_LOOP_STREAM_CHUNK = int(os.environ.get("MIOS_NATIVE_LOOP_STREAM_CHUNK", "20") or 20)
NATIVE_LOOP_STREAM_DELAY_MS = int(os.environ.get("MIOS_NATIVE_LOOP_STREAM_DELAY_MS", "10") or 10)
NATIVE_LOOP_TOOL_CAP = int(os.environ.get("MIOS_NATIVE_LOOP_TOOL_CAP", "36") or 36)
NATIVE_LOOP_BREADTH_GUIDANCE = (os.environ.get("MIOS_NATIVE_LOOP_BREADTH_GUIDANCE")
                                or "true").strip().lower() not in {"false", "0", "no"}
_NATIVE_LOOP_BREADTH_PROSE = (
    "RESEARCH STRATEGY: for a BROAD or multi-facet request (e.g. 'everything/all about "
    "X', \"today's trending\", a comparison, a survey), do NOT send the whole instruction "
    "to web_search as one query -- search engines need short, sharp TERMS. Either call "
    "web_search SEVERAL times with one distinct concise term per facet, OR call "
    "dispatch_to_nodes to fan the facets across nodes. Then synthesize ONE structured, "
    "cited report covering every facet -- never reply that nothing was found when tool "
    "results are present. For TIME-SENSITIVE facets (trending/latest/today/this period) "
    "set web_search's time_range to 'day' or 'week' (and category='news' for breaking "
    "headlines) so results are fresh dated stories, not evergreen year-overview pages. "
    "Start each facet with a SHORT broad query to survey, then narrow -- avoid long, "
    "over-specific first queries.")
NATIVE_LOOP_PERSISTENCE = (os.environ.get("MIOS_NATIVE_LOOP_PERSISTENCE")
                           or "true").strip().lower() not in {"false", "0", "no"}
_NATIVE_LOOP_PERSISTENCE_PROSE = (
    "AGENT PERSISTENCE: you are an autonomous agent -- keep working until the user's "
    "request is COMPLETELY resolved before you end your turn. Do NOT stop after a single "
    "tool call or hand back a thin, partial answer: if the first results are sparse, "
    "search again with sharper terms, fetch the most relevant pages, or fan the facets "
    "across nodes until you can answer fully. Never ask the user to narrow, specify, or "
    "rephrase something you can research yourself -- research it. Use tools to gather "
    "anything you are unsure of; do NOT guess or fabricate. Plan before each tool call "
    "and reflect on the result before the next.")
NATIVE_LOOP_REFLECTION = (os.environ.get("MIOS_NATIVE_LOOP_REFLECTION")
                          or "true").strip().lower() not in {"false", "0", "no"}
NATIVE_LOOP_RECENCY_DEFAULTS = (os.environ.get("MIOS_NATIVE_LOOP_RECENCY_DEFAULTS")
                                or "true").strip().lower() not in {"false", "0", "no"}
NATIVE_LOOP_RECENCY_FANOUT = int(
    os.environ.get("MIOS_NATIVE_LOOP_RECENCY_FANOUT", "4") or 4)
NATIVE_LOOP_RECENCY_RANGE = (os.environ.get("MIOS_NATIVE_LOOP_RECENCY_RANGE")
                             or "day").strip()
NATIVE_LOOP_QUERY_REFORMULATE = str(os.environ.get("MIOS_NATIVE_LOOP_QUERY_REFORMULATE")
    or _DISPATCH_TOML.get("native_loop_query_reformulate", "true")).strip().lower() not in {"false", "0", "no"}
NATIVE_LOOP_DATE_IN_QUERY = str(os.environ.get("MIOS_NATIVE_LOOP_DATE_IN_QUERY")
    or _DISPATCH_TOML.get("native_loop_date_in_query", "true")).strip().lower() not in {"false", "0", "no"}
sys.modules["mios_dispatch"].configure(
    native_loop_date_in_query=NATIVE_LOOP_DATE_IN_QUERY,
)
NATIVE_LOOP_DATE_ANCHOR = str(os.environ.get("MIOS_NATIVE_LOOP_DATE_ANCHOR")
    or _DISPATCH_TOML.get("native_loop_date_anchor", "true")).strip().lower() not in {"false", "0", "no"}
NATIVE_LOOP_MATH_HINT = str(os.environ.get("MIOS_NATIVE_LOOP_MATH_HINT")
    or _DISPATCH_TOML.get("native_loop_math_hint", "true")).strip().lower() not in {"false", "0", "no"}
_NATIVE_LOOP_REFLECTION_PROSE = (
    "RESULT SUFFICIENCY: after every web_search or fetch, judge in your reasoning whether "
    "the results actually answer the facet (goal met: yes/no). If they are thin, off-topic, "
    "or evergreen/undated when the question is time-sensitive, do NOT conclude nothing was "
    "found -- name what is missing and issue a sharper reformulated query (different terms, "
    "a recency window via time_range, or split the facet) before answering. Only state that "
    "information is unavailable AFTER you have reformulated, re-searched, and the tool still "
    "returns nothing relevant. When you DO have relevant current results, PRESENT them as the "
    "answer directly and confidently: a 'trending'/'latest' report is a SELECTION of the top "
    "current items, so covering several IS success -- never preface or close with 'no "
    "comprehensive report is available', and never ask the user to narrow or specify a "
    "request you can already partly answer. Deliver the digest of what you found.")

from mios_native_loop import (  # noqa: E402
    _respond_native_loop_direct, _respond_local_state,
    _format_local_state, _formulate_web_query, _formulate_compute_snippet,
)

from mios_tokenize import _usage_estimate  # noqa: E402

sys.modules["mios_hitlflow"].configure(
    hitl_enable=HITL_ENABLE,
    hitl_mode=HITL_MODE,
    hitl_scope=HITL_SCOPE,
    ask_to_run_enable=ASK_TO_RUN_ENABLE,
    ask_to_run_ttl_s=ASK_TO_RUN_TTL_S,
    router_model=ROUTER_MODEL,
    planner_endpoint=PLANNER_ENDPOINT,
    planner_timeout_s=PLANNER_TIMEOUT_S,
    pg_primary=_PG_PRIMARY,
    db_read=_db_read,
    db_post=_db_post,
    db_create=_db_create,
    db_fire=_db_fire,
    db_update=_db_update,
    pg_mirror=_pg_mirror,
    emit_session_event=_emit_session_event,
    row_age_seconds=_row_age_seconds,
    usage_estimate=_usage_estimate,
    passport_sign=_passport_sign,
    hitl_approved_var=_hitl_approved_var,
    dispatch_mios_verb=dispatch_mios_verb,
)
app.include_router(hitlflow_router)

sys.modules["mios_native_loop"].configure(
    _LOCAL_STATE_SYSTEM=_LOCAL_STATE_SYSTEM,
    _polish_post=_polish_post,
    BACKEND=BACKEND,
    BACKEND_MODEL=BACKEND_MODEL,
    _BACKEND_KEY=_BACKEND_KEY,
    _BACKEND_HOSTPORT=_BACKEND_HOSTPORT,
    REFINE_ENDPOINT=REFINE_ENDPOINT,
    REFINE_MODEL=REFINE_MODEL,
    STABLE_PREFIX=STABLE_PREFIX,
    STABLE_PREFIX_HINT=STABLE_PREFIX_HINT,
    STABLE_PREFIX_TAIL=STABLE_PREFIX_TAIL,
    NATIVE_LOOP_TOOL_CAP=NATIVE_LOOP_TOOL_CAP,
    NATIVE_LOOP_TIMEOUT_S=NATIVE_LOOP_TIMEOUT_S,
    NATIVE_LOOP_CAPABILITY_GROUNDING=NATIVE_LOOP_CAPABILITY_GROUNDING,
    NATIVE_LOOP_PERSISTENCE=NATIVE_LOOP_PERSISTENCE,
    _NATIVE_LOOP_PERSISTENCE_PROSE=_NATIVE_LOOP_PERSISTENCE_PROSE,
    NATIVE_LOOP_BREADTH_GUIDANCE=NATIVE_LOOP_BREADTH_GUIDANCE,
    _NATIVE_LOOP_BREADTH_PROSE=_NATIVE_LOOP_BREADTH_PROSE,
    NATIVE_LOOP_REFLECTION=NATIVE_LOOP_REFLECTION,
    _NATIVE_LOOP_REFLECTION_PROSE=_NATIVE_LOOP_REFLECTION_PROSE,
    NATIVE_LOOP_RECENCY_RANGE=NATIVE_LOOP_RECENCY_RANGE,
    NATIVE_LOOP_RECENCY_FANOUT=NATIVE_LOOP_RECENCY_FANOUT,
    NATIVE_LOOP_RECENCY_DEFAULTS=NATIVE_LOOP_RECENCY_DEFAULTS,
    NATIVE_LOOP_MATH_HINT=NATIVE_LOOP_MATH_HINT,
    NATIVE_LOOP_DATE_ANCHOR=NATIVE_LOOP_DATE_ANCHOR,
    NATIVE_LOOP_QUERY_REFORMULATE=NATIVE_LOOP_QUERY_REFORMULATE,
    NATIVE_LOOP_STREAM_TOKENS=NATIVE_LOOP_STREAM_TOKENS,
    NATIVE_LOOP_STREAM_CHUNK=NATIVE_LOOP_STREAM_CHUNK,
    NATIVE_LOOP_STREAM_DELAY_MS=NATIVE_LOOP_STREAM_DELAY_MS,
    _ROUTING_DOMAINS=_ROUTING_DOMAINS,
    _VERB_CATALOG=_VERB_CATALOG,
    _routed_domain_var=_routed_domain_var,
    _orch_ctx_var=_orch_ctx_var,
    _recency_ctx_var=_recency_ctx_var,
    dispatch_mios_verb=dispatch_mios_verb,
    _usage_estimate=_usage_estimate,
    _identity_answer=_identity_answer,
    _agent_contract=_agent_contract,
    _capability_grounding=_capability_grounding,
    _env_grounding=_env_grounding,
    _recall_agent_memory=_recall_agent_memory,
    _recall_knowledge=_recall_knowledge,
    _rag_enrich=_rag_enrich,
    _tool_pref_block=_tool_pref_block,
    _current_date_str=_current_date_str,
    _worker_tools_surface_async=_worker_tools_surface_async,
    _read_tool_enrich=_read_tool_enrich,
    _needs_compute=_needs_compute,
    _src_record=_src_record,
    _src_collected=_src_collected,
    _src_record_from_text=_src_record_from_text,
    _endpoint_supports_parallel_tools=_endpoint_supports_parallel_tools,
    _filter_relevant_sources=_filter_relevant_sources,
    _sources_markdown=_sources_markdown,
    _sources_annotations=_sources_annotations,
    _sources_metadata=_sources_metadata,
    _store_knowledge=_store_knowledge,
    _iter_answer_chunks=_iter_answer_chunks,
    _write_skill_md_fire=_write_skill_md_fire,
    _worker_tools_core_cache=(lambda: _WORKER_TOOLS_CORE_CACHE),
    _DEBUG_ENABLE=_DEBUG_ENABLE,
)

sys.modules["mios_swarm"].configure(
    swarm_max_width=SWARM_MAX_WIDTH,
    swarm_max_cpu_nodes=SWARM_MAX_CPU_NODES,
    swarm_deepen_enabled=SWARM_DEEPEN_ENABLED,
    slow_lane_block_chars=SLOW_LANE_BLOCK_CHARS,
    dag_replan_max=DAG_REPLAN_MAX,
    dag_empty_native_fallback=DAG_EMPTY_NATIVE_FALLBACK,
    slow_lanes=SLOW_LANES,
    max_dispatch_depth=MAX_DISPATCH_DEPTH,
    swarm_model=SWARM_MODEL,
    swarm_system_head=_SWARM_SYSTEM_HEAD,
    agent_catalog_rendered=_AGENT_CATALOG_RENDERED,
    depth_exhausted=_depth_exhausted,
    dispatch_depth=_dispatch_depth,
    render_agent_catalog=_render_agent_catalog,
    agent_registry=_AGENT_REGISTRY,
    verb_catalog=_VERB_CATALOG,
    routed_domain_var=_routed_domain_var,
    pick_agent=_pick_agent,
    dedup_pool_by_target=_dedup_pool_by_target,
    is_slow_lane_ep=_is_slow_lane_ep,
    agent_lane=_agent_lane,
    live_agent_names=_live_agent_names,
    read_tool_enrich=_read_tool_enrich,
    respond_native_loop_direct=_respond_native_loop_direct,
    strip_think_tags=_strip_think_tags,
    filter_relevant_sources=_filter_relevant_sources,
    sources_markdown=_sources_markdown,
    sources_annotations=_sources_annotations,
    sources_metadata=_sources_metadata,
    src_collected=_src_collected,
    src_record_from_text=_src_record_from_text,
    usage_estimate=_usage_estimate,
    db_read=_db_read,
    db_fire=_db_fire,
    db_post=_db_post,
    db_create=_db_create,
    embed_one=_embed_one,
)

from mios_pipe.auth import (
    usage_completeness_mw as _usage_completeness_mw,
    inbound_auth_mw as _inbound_auth_mw,
    configure as _configure_auth
)

_configure_scratchpad(
    conv_key_var=_conv_key_var,
    mios_pg=_mios_pg,
    db_write=_db_write,
)

_configure_toolsurface(
    worker_tools_scope=WORKER_TOOLS_SCOPE,
    child_tool_select=CHILD_TOOL_SELECT,
    stable_prefix=STABLE_PREFIX,
    stable_prefix_tail=STABLE_PREFIX_TAIL,
    child_tool_floor=CHILD_TOOL_FLOOR,
    code_mode_enable=CODE_MODE_ENABLE,
    verb_catalog=_VERB_CATALOG,
    recipe_catalog=_RECIPE_CATALOG,
    routing_domains=_ROUTING_DOMAINS,
    routed_domain_var=_routed_domain_var,
    dispatch_toml=_DISPATCH_TOML,
    mcp_client_lock=_MCP_CLIENT_LOCK,
    mcp_client_tools=_MCP_CLIENT_TOOLS,
    verb_embeddings=_VERB_EMBEDDINGS,
    verb_to_openai_tool=_verb_to_openai_tool,
    recipe_to_openai_tool=_recipe_to_openai_tool,
    skill_to_openai_tool=_skill_to_openai_tool,
    mcp_tool_to_openai_tool=_mcp_tool_to_openai_tool,
    skill_list=_skill_list,
    resolve_verb_key=_resolve_verb_key,
    tool_embedding=_tool_embedding,
    cosine=_cosine,
    ensure_verb_embeddings=_ensure_verb_embeddings,
    embed_one=_embed_one,
)

_configure_auth(
    loads_lenient=globals().get("_loads_lenient"),
    usage_estimate=globals().get("_usage_estimate"),
    council_mode_var=globals().get("_council_mode_var"),
    api_require_auth=globals().get("_API_REQUIRE_AUTH", False),
    auth_open_paths=globals().get("_AUTH_OPEN_PATHS", ()),
    auth_gated_prefixes=globals().get("_AUTH_GATED_PREFIXES", ()),
    check_inbound_principal=globals().get("_check_inbound_principal"),
)

app.middleware("http")(_usage_completeness_mw)
app.middleware("http")(_inbound_auth_mw)

_PSI_THROTTLE_STATUS = PSIMonitor.throttle_status()

@app.middleware("http")
async def _psi_shed_load_mw(request: Request, call_next):
    """T-485: Gracefully throttle requests with HTTP 429/503 during critical Linux PSI pressure."""
    try:
        mon = get_psi_monitor()
        if mon and mon.is_critical():
            path = request.url.path
            if (path not in _AUTH_OPEN_PATHS and
                    not path.startswith(("/v1/system/", "/v1/cluster/health", "/health", "/.well-known/"))):
                reason = mon.get_shed_reason() or "Linux PSI critical pressure stall"
                status = _PSI_THROTTLE_STATUS if _PSI_THROTTLE_STATUS in (429, 503) else 429
                return JSONResponse(
                    status_code=status,
                    content={
                        "error": {
                            "message": f"Request throttled due to system saturation: {reason}",
                            "type": "pressure_stall_shed",
                            "code": status,
                            "psi": mon.get_metrics_dict(),
                        }
                    },
                    headers={"Retry-After": "2"},
                )
    except Exception as _e:
        log.debug("PSI load shed middleware check error: %s", _e)
    return await call_next(request)


__import__("mios_chat")
sys.modules["mios_chat"].configure(
    _db_write=_db_write,
    _embed_one=_embed_one,
    _scratchpad_for=_scratchpad_for,
    EMB_MODEL=EMB_MODEL,
    EMB_VERSION=EMB_VERSION,
    _turn_tenant=_turn_tenant,
    SCRATCHPAD_PERSIST=SCRATCHPAD_PERSIST,
    LETTA_MEMORY_BACKEND=mios_memory.LETTA_MEMORY_BACKEND,
    _LETTA_CLIENT=mios_memory._LETTA_CLIENT,
    ASK_CLARIFY_ENABLE=ASK_CLARIFY_ENABLE,
    AUTONOMOUS_PRIORITY=AUTONOMOUS_PRIORITY,
    AUTO_FORCE_TOOL=AUTO_FORCE_TOOL,
    BACKEND=BACKEND,
    BACKEND_MODEL=BACKEND_MODEL,
    CLIENT_TOOLS_PASSTHROUGH=CLIENT_TOOLS_PASSTHROUGH,
    COUNCIL_DEFAULT=COUNCIL_DEFAULT,
    DCI_ENABLED=DCI_ENABLED,
    KERNEL_ROUTE=KERNEL_ROUTE,
    KERNEL_DISPATCH=KERNEL_DISPATCH,
    LOCAL_STATE_FASTPATH=LOCAL_STATE_FASTPATH,
    MAX_DISPATCH_DEPTH=MAX_DISPATCH_DEPTH,
    NATIVE_LOOP_ENABLE=NATIVE_LOOP_ENABLE,
    NATIVE_LOOP_MATH_HINT=NATIVE_LOOP_MATH_HINT,
    PLANNER_ENABLED=PLANNER_ENABLED,
    POLISH_ENABLED=POLISH_ENABLED,
    SLOW_LANES=SLOW_LANES,
    SLOW_LANE_BLOCK_CHARS=SLOW_LANE_BLOCK_CHARS,
    SWARM_DECOMPOSE_DEFAULT=SWARM_DECOMPOSE_DEFAULT,
    SWARM_DECOMPOSE_MIN_WORDS=SWARM_DECOMPOSE_MIN_WORDS,
    SWARM_MAX_WIDTH=SWARM_MAX_WIDTH,
    SWARM_TRUST_ATOMIC=SWARM_TRUST_ATOMIC,
    VISION_ENABLE=VISION_ENABLE,
    VISION_MODEL=VISION_MODEL,
    WORKER_TOOLS_ENABLE=WORKER_TOOLS_ENABLE,
    WORKER_TOOL_CTX=WORKER_TOOL_CTX,
    _AGENT_REGISTRY=_AGENT_REGISTRY,
    _BACKEND_IS_LIGHT=_BACKEND_IS_LIGHT,
    _BACKEND_KEY=_BACKEND_KEY,
    _BROWSER_ACTION_ALT=_BROWSER_ACTION_ALT,
    _FASTPATH_VERBS=_FASTPATH_VERBS,
    _HOP_HEADER=_HOP_HEADER,
    _HUMAN_LABELS=_HUMAN_LABELS,
    _INGRESS_KEY=_INGRESS_KEY,
    _KERNEL=_KERNEL,
    _SRC_TURN_HEADER=_SRC_TURN_HEADER,
    _THINK_ORPHAN_RE=_THINK_ORPHAN_RE,
    _TOOL_BACKEND=_TOOL_BACKEND,
    _TOOL_BACKEND_MODEL=_TOOL_BACKEND_MODEL,
    _VERB_CATALOG=_VERB_CATALOG,
    _VIA_HEADER=_VIA_HEADER,
    _agent_contract=_agent_contract,
    _agent_lane=_agent_lane,
    _agent_offload_engine=_agent_offload_engine,
    _build_agent_hint=_build_agent_hint,
    _call_agent_stream=_call_agent_stream,
    _casual_agent_label=_casual_agent_label,
    _client_env=_client_env,
    _client_env_var=_client_env_var,
    _conv_key_var=_conv_key_var,
    _council_mode_var=_council_mode_var,
    _council_role_lens=_council_role_lens,
    _critic_refine_agent=_critic_refine_agent,
    _current_year=_current_year,
    _db_create=_db_create,
    _db_fire=_db_fire,
    _db_post=_db_post,
    _depth_exhausted=_depth_exhausted,
    _dispatch_depth=_dispatch_depth,
    _endpoint_supports_tool_choice=_endpoint_supports_tool_choice,
    _expand_facets=_expand_facets,
    _extract_last_user_text=_extract_last_user_text,
    _filter_relevant_sources=_filter_relevant_sources,
    _get_client=_get_client,
    _inline_satisfaction_check=_inline_satisfaction_check,
    _is_action_domain=_is_action_domain,
    _lane_tool_cap=_lane_tool_cap,
    _live_agent_names=_live_agent_names,
    _loads_lenient=_loads_lenient,
    _maybe_run_pending_approval=_maybe_run_pending_approval,
    _messages_have_image=_messages_have_image,
    _multi_task_preamble=_multi_task_preamble,
    _needs_compute=_needs_compute,
    _node_status=_node_status,
    _pick_agent=_pick_agent,
    _plan_swarm=_plan_swarm,
    _rag_enrich=_rag_enrich,
    _read_tool_enrich=_read_tool_enrich,
    _recall_agent_memory=_recall_agent_memory,
    _role_system=_role_system,
    _route_domain=_route_domain,
    _routed_domain_var=_routed_domain_var,
    _sanitize_tool_text=_sanitize_tool_text,
    _sched_priority=_sched_priority,
    _scratchpad_key=_scratchpad_key,
    _scratchpad_note=_scratchpad_note,
    _scratchpad_rehydrate=_scratchpad_rehydrate,
    _scratchpad_render=_scratchpad_render,
    _seed_hop_from_headers=_seed_hop_from_headers,
    _sources_annotations=_sources_annotations,
    _sources_markdown=_sources_markdown,
    _sources_metadata=_sources_metadata,
    _sources_var=_sources_var,
    _span_id_var=_span_id_var,
    _src_collected=_src_collected,
    _src_record_from_text=_src_record_from_text,
    _src_turn_init=_src_turn_init,
    _src_turn_key=_src_turn_key,
    _src_turn_var=_src_turn_var,
    _sse_done=_sse_done,
    _sse_reasoning=_sse_reasoning,
    _sse_status_phase=_sse_status_phase,
    _strip_owui_scaffold=_strip_owui_scaffold,
    _strip_think_tags=_strip_think_tags,
    _trace_id_var=_trace_id_var,
    _turn_volatile_var=_turn_volatile_var,
    _vram_checkpoint=_vram_checkpoint,
    _worker_tools_surface_async=_worker_tools_surface_async,
    _write_skill_md_fire=_write_skill_md_fire,
    classify_intent=classify_intent,
    _DEBUG_ENABLE=_DEBUG_ENABLE,
)
from mios_chat import (   # noqa: E402
    _BUDGET_TOML, _budget_num, BUDGET_CONV_TOKEN_CEIL, BUDGET_AUTO_TOKEN_CEIL,
    BUDGET_AUTO_MAX_INFLIGHT, BUDGET_WINDOW_S, BUDGET_ENABLE, _BUDGET_LEDGER,
    _BUDGET_LEDGER_MAX, BUDGET_PER_TURN_ESTIMATE, _BUDGET_AUTO_INFLIGHT,
    BUDGET_INFLIGHT_TTL_S, _BUDGET_LOCK, _budget_bucket, _budget_window_total,
    _budget_debit, _budget_prune_inflight, _budget_admit, _budget_release_inflight,
)
from mios_chat import (   # noqa: E402
    _quick_chat_reply, _is_memory_question, _ask_for_location,
)
from mios_chat import (   # noqa: E402
    _pretty_name, _trim_sys_prefix,
)
from mios_chat import (   # noqa: E402
    _hints_write_action, _needs_external_knowledge, _shadow_queue_tasks,
)
from mios_chat import chat_router, responses_api, chat_completions   # noqa: E402
app.include_router(chat_router)
globals()["chat_completions_logic"] = sys.modules["mios_chat"].chat_completions_logic
globals()["responses_api_logic"] = sys.modules["mios_chat"].responses_api_logic
globals()["hitl_approve_logic"] = sys.modules["mios_hitlflow"].hitl_approve_logic
globals()["v1_computer_use_logic"] = sys.modules["mios_cua"].v1_computer_use_logic

__import__("mios_clusterhealth")
from mios_clusterhealth import (   # noqa: E402
    _resolve_failover_chain,
    _probe_one_endpoint,
    _lane_sched_stats,
    _kernel_managers_detail,
    clusterhealth_router, cluster_health, scheduler_state, health,
)
sys.modules["mios_clusterhealth"].configure(
    app=app,
    _AGENT_REGISTRY=_AGENT_REGISTRY,
    _GLOBAL_PRIORITY_GATE=_GLOBAL_PRIORITY_GATE,
    _KV_RESIDENT=_KV_RESIDENT,
    _TOOL_CONFLICT=_TOOL_CONFLICT,
    _TRACER=_TRACER,
    _PREEMPT=_PREEMPT,
    _COST_LEDGER=_COST_LEDGER,
    _KERNEL=_KERNEL,
    _ALLOWLIST_HOSTS=_ALLOWLIST_HOSTS,
    _HIGH_PRIVILEGE_VERBS=_HIGH_PRIVILEGE_VERBS,
    _HIGH_PRIVILEGE_CURATED=_HIGH_PRIVILEGE_CURATED,
    _TAINT_VERBS=_TAINT_VERBS,
    _agent_lane=_agent_lane,
    _over_global_ceiling=_over_global_ceiling,
    _host_stats_cached=_host_stats_cached,
    _toml_section=_toml_section,
    _probe_auth_headers=_probe_auth_headers,
    _LANE_SEMS=_LANE_SEMS,
    _MEMORY=_MEMORY,
    _VERB_CATALOG=_VERB_CATALOG,
    _PERMISSION_TIERS=_PERMISSION_TIERS,
    _passport_load_priv=_passport_load_priv,
    _passport_kid=_passport_kid,
    AGENT_CONCURRENCY=AGENT_CONCURRENCY,
    _PG_PRIMARY=_PG_PRIMARY,
    ADMIT_ENABLE=ADMIT_ENABLE,
    ADMIT_LOAD_CEIL=ADMIT_LOAD_CEIL,
    ADMIT_MEM_PCT=ADMIT_MEM_PCT,
    PRIORITY_QUEUE_ENABLE=PRIORITY_QUEUE_ENABLE,
    PRIORITY_STARVATION_S=PRIORITY_STARVATION_S,
    KV_FORK_ENABLE=KV_FORK_ENABLE,
    KV_PAGING_ENABLE=KV_PAGING_ENABLE,
    KV_PAGING_SLOT=KV_PAGING_SLOT,
    KV_FORK_MAX_BRANCHES=KV_FORK_MAX_BRANCHES,
    KNOWLEDGE_EVICT_ENABLE=KNOWLEDGE_EVICT_ENABLE,
    KNOWLEDGE_EVICT_DRYRUN=KNOWLEDGE_EVICT_DRYRUN,
    KNOWLEDGE_EVICT_INTERVAL_S=KNOWLEDGE_EVICT_INTERVAL_S,
    KNOWLEDGE_EVICT_TTL_DAYS=KNOWLEDGE_EVICT_TTL_DAYS,
    KNOWLEDGE_EVICT_MAX_ROWS=KNOWLEDGE_EVICT_MAX_ROWS,
    KNOWLEDGE_EVICT_BATCH=KNOWLEDGE_EVICT_BATCH,
    RR_ENABLE=RR_ENABLE,
    RR_QUANTUM_S=RR_QUANTUM_S,
    RR_SLICE_TOKENS=RR_SLICE_TOKENS,
    BATCH_ENABLE=BATCH_ENABLE,
    BATCH_INTERVAL_S=BATCH_INTERVAL_S,
    BATCH_MAX_SIZE=BATCH_MAX_SIZE,
    BATCH_NATIVE_HINTS=BATCH_NATIVE_HINTS,
    SMARTROUTE_ENABLE=SMARTROUTE_ENABLE,
    SMARTROUTE_BUDGET=SMARTROUTE_BUDGET,
    SLO_SHED_ENABLE=SLO_SHED_ENABLE,
    COST_ACCOUNTING_ENABLE=COST_ACCOUNTING_ENABLE,
    COST_BUDGET_USD=COST_BUDGET_USD,
    KERNEL_ROUTE=KERNEL_ROUTE,
    KERNEL_DISPATCH=KERNEL_DISPATCH,
    SKILLS_ENABLED=SKILLS_ENABLED,
    SKILLS_MIN_LENGTH=SKILLS_MIN_LENGTH,
    SKILLS_MAX_LENGTH=SKILLS_MAX_LENGTH,
    SKILLS_MIN_SUPPORT=SKILLS_MIN_SUPPORT,
    SKILLS_WINDOW_HOURS=SKILLS_WINDOW_HOURS,
    SKILLS_AUTO_PROMOTE_THRESHOLD=SKILLS_AUTO_PROMOTE_THRESHOLD,
    PASSPORT_ENABLE=PASSPORT_ENABLE,
    PASSPORT_ALGO=PASSPORT_ALGO,
    PASSPORT_AGENT_NAME=PASSPORT_AGENT_NAME,
    PASSPORT_KEY_DIR=PASSPORT_KEY_DIR,
    PASSPORT_VERIFY_ON_READ=PASSPORT_VERIFY_ON_READ,
    LAUNCHER_SOCK=LAUNCHER_SOCK,
    DB_URL=DB_URL,
)
app.include_router(clusterhealth_router)
globals()["cluster_health_logic"] = sys.modules["mios_clusterhealth"].cluster_health_logic
globals()["scheduler_state_logic"] = sys.modules["mios_clusterhealth"].scheduler_state_logic
globals()["health_logic"] = sys.modules["mios_clusterhealth"].health_logic

# T-485: Surface Linux PSI metrics in /v1/cluster/health and provide system metrics endpoint
_orig_cluster_health_logic = sys.modules["mios_clusterhealth"].cluster_health_logic
async def _psi_cluster_health_logic() -> JSONResponse:
    resp = await _orig_cluster_health_logic()
    try:
        mon = get_psi_monitor()
        body = json.loads(bytes(resp.body).decode("utf-8"))
        body["psi"] = mon.get_metrics_dict()
        return JSONResponse(status_code=resp.status_code, content=body, headers=dict(resp.headers))
    except Exception:
        return resp

sys.modules["mios_clusterhealth"].cluster_health_logic = _psi_cluster_health_logic
globals()["cluster_health_logic"] = _psi_cluster_health_logic

_orig_health_logic = sys.modules["mios_clusterhealth"].health_logic
async def _psi_health_logic() -> dict[str, Any]:
    res = await _orig_health_logic()
    try:
        mon = get_psi_monitor()
        res["psi"] = {
            "available": mon.available,
            "level": mon.current_level,
            "highest_resource": mon.latest_metrics.highest_resource,
            "highest_avg10": round(mon.latest_metrics.highest_avg10, 2),
        }
    except Exception:
        pass
    return res

sys.modules["mios_clusterhealth"].health_logic = _psi_health_logic
globals()["health_logic"] = _psi_health_logic

@app.get("/v1/system/psi")
async def v1_system_psi() -> JSONResponse:
    """T-485: Linux PSI telemetry, resource stall averages, and load-shedding status."""
    return JSONResponse(get_psi_monitor().get_metrics_dict())

globals()["portal_stats_logic"] = sys.modules["mios_portal"].portal_stats_logic
globals()["portal_service_detail_logic"] = sys.modules["mios_portal"].portal_service_detail_logic
globals()["portal_swarm_logic"] = sys.modules["mios_portal"].portal_swarm_logic
globals()["portal_term_ws_logic"] = sys.modules["mios_portal"].portal_term_ws_logic
globals()["portal_login_page_logic"] = sys.modules["mios_portal"].portal_login_page_logic
globals()["portal_login_logic"] = sys.modules["mios_portal"].portal_login_logic
globals()["portal_page_logic"] = sys.modules["mios_portal"].portal_page_logic

from mios_webhook import WebhookReceiver as _WebhookReceiver  # noqa: E402
_webhook_receiver = _WebhookReceiver()

@app.post("/v1/webhooks/{source}")
async def v1_incoming_webhook(source: str, request: Request) -> JSONResponse:
    raw_body = await request.body()
    headers = dict(request.headers)
    success, status_msg, details = await _webhook_receiver.ingest_webhook(
        raw_body=raw_body,
        headers=headers,
        source=source,
    )
    if not success:
        return JSONResponse(status_code=401, content={"error": status_msg, "details": details})

    code = 200 if status_msg == "duplicate" else 202
    return JSONResponse(status_code=code, content={"status": status_msg, **details})

from mios_events import AgentEventHub as _AgentEventHub  # noqa: E402
_event_hub = _AgentEventHub()

@app.websocket("/v1/events/ws")
async def v1_events_websocket(websocket: WebSocket):
    token = websocket.query_params.get("token") or websocket.headers.get("authorization")
    if not _event_hub.authenticate(token):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    session_id = websocket.query_params.get("session_id")
    q = _event_hub.register(websocket, session_id=session_id)
    try:
        while True:
            send_task = asyncio.create_task(q.get())
            recv_task = asyncio.create_task(websocket.receive_text())
            done, pending = await asyncio.wait([send_task, recv_task], return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()

            if send_task in done:
                msg = send_task.result()
                await websocket.send_text(msg)
            if recv_task in done:
                _ = recv_task.result()
    except Exception:
        pass
    finally:
        _event_hub.unregister(websocket)

def main() -> int:
    host = _bind_host(_API_REQUIRE_AUTH, os.environ.get("MIOS_BIND_HOST", ""))
    log.info("starting on %s:%d -> backend=%s model=%s "
             "router_enabled=%s router_model=%s",
             host, PORT, BACKEND, BACKEND_MODEL,
             ROUTER_ENABLED, ROUTER_MODEL)
    uvicorn.run(
        app,
        host=host,
        port=PORT,
        log_level="info",
        access_log=False,
    )
    return 0

if __name__ == "__main__":
    sys.exit(main())
