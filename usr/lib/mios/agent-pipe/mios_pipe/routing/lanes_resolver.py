# AI-hint: INFERENCE LANE-RESOLVER cluster extracted VERBATIM from server.py AI-related: ./server.py, ./mios_config.py, ./mios_lanes.py, ./tes...
# AI-doc: usr/share/doc/mios/manual/routing.md

from __future__ import annotations

import os
import logging

import mios_lanes   # WS-1 unified inference-lane resolver

from mios_config import (
    _TOOL_BACKEND,
    _TOOL_BACKEND_MODEL,
    _TOOL_BACKEND_HEAVY,
    _TOOL_BACKEND_HEAVY_MODEL,
    _HEAVY_PROBE_TTL,
    _toml_section,
)

log = logging.getLogger("mios-agent-pipe")

_get_client = None
_is_remote_endpoint = None

_INJECTED = frozenset((
    "_get_client", "_is_remote_endpoint",
))

def configure(**deps) -> None:
    """Inject server-side deps under their EXACT original names (one-way boundary).

    Called once from ``server.py`` after every injected symbol is defined. Each
    keyword equals the module global it sets.
    """
    g = globals()
    for _k, _v in deps.items():
        if _k in _INJECTED:
            g[_k] = _v

_gpu_state_cache = {"state": "AI_EXCLUSIVE", "ts": -1e9}

def get_gpu_arbiter_state() -> str:
    """Read the current GPU Arbiter state.
    Cached for 50ms TTL to prevent synchronous disk I/O thrashing during query routing storms.
    States: 'AI_EXCLUSIVE', 'DRAINING_SLEEP', 'VM_ATTACHED', 'RESTORING_AI'.
    """
    env_state = os.environ.get("MIOS_GPU_STATE")
    if env_state:
        return env_state.strip()
    import time as _t
    now = _t.monotonic()
    if (now - _gpu_state_cache["ts"]) < 0.050:
        return _gpu_state_cache["state"]
    state = "AI_EXCLUSIVE"
    for path in (
        os.environ.get("MIOS_GPU_STATE_FILE", "/run/mios/gpu-state"),
        "/run/mios-gpu-state",
        "/var/run/mios/gpu-state",
    ):
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if content:
                    state = content
                    break
        except Exception:
            pass
    _gpu_state_cache["state"] = state
    _gpu_state_cache["ts"] = now
    return state


_heavy_probe = {"ok": False, "ts": -1e9}

async def _heavy_lane_up() -> bool:
    """Is the SGLang heavy lane serving right now? Cached for _HEAVY_PROBE_TTL s so we
    probe at most once per window, never per request."""
    if get_gpu_arbiter_state() != "AI_EXCLUSIVE":
        # Proactive gaming failover: heavy GPU is detached/sleeping
        return False
    import time as _t
    now = _t.monotonic()
    if (now - _heavy_probe["ts"]) < _HEAVY_PROBE_TTL:
        return _heavy_probe["ok"]
    ok = False
    try:
        client = await _get_client()
        r = await client.get(f"{_TOOL_BACKEND_HEAVY}/models", timeout=2.0)
        ok = (r.status_code == 200)
    except Exception:  # noqa: BLE001 -- any failure => heavy is down, use light
        ok = False
    _heavy_probe["ok"] = ok
    _heavy_probe["ts"] = now
    return ok

_LANE_RESOLVER = None

def _lane_resolver():
    global _LANE_RESOLVER
    if _LANE_RESOLVER is not None:
        return _LANE_RESOLVER
    try:
        _ai = _toml_section("ai")
    except Exception:  # noqa: BLE001 -- degrade to env/defaults
        _ai = {}
    heavy_engine = (os.environ.get("MIOS_AGENT_PIPE_HEAVY_ENGINE")
                    or str(_ai.get("heavy_engine", "vllm"))).strip()
    _vllm_url = (os.environ.get("MIOS_AGENT_PIPE_TOOL_BACKEND_VLLM")
                 or _toml_section("nodes").get("local-vllm", {}).get("endpoint")
                 or f"http://localhost:{os.environ.get('MIOS_PORTS_VLLM', '8520')}/v1").rstrip("/")
    _vllm_model = os.environ.get("MIOS_AGENT_PIPE_TOOL_BACKEND_VLLM_MODEL",
                                 _TOOL_BACKEND_HEAVY_MODEL)
    _cpu_port = os.environ.get("MIOS_PORTS_CPU_NODE", "8510")
    lanes = {
        "light":    mios_lanes.Lane("light",    _TOOL_BACKEND,       _TOOL_BACKEND_MODEL),
        "cpu_node": mios_lanes.Lane("cpu_node", f"http://localhost:{_cpu_port}/v1", _TOOL_BACKEND_MODEL),
        "sglang":   mios_lanes.Lane("sglang",   _TOOL_BACKEND_HEAVY, _TOOL_BACKEND_HEAVY_MODEL),
        "vllm":     mios_lanes.Lane("vllm",     _vllm_url,           _vllm_model),
        mios_lanes.HEAVY: mios_lanes.Lane(
            mios_lanes.HEAVY, _TOOL_BACKEND_HEAVY, _TOOL_BACKEND_HEAVY_MODEL),
    }
    chain = mios_lanes.build_chain(heavy_engine, lanes.keys())
    try:
        if str(_ai.get("remote_escalation", "off")).strip().lower() in {
                "on", "true", "1", "yes", "enforce"}:
            for _nname, _ncfg in (_toml_section("nodes") or {}).items():
                if not isinstance(_ncfg, dict):
                    continue
                _nep = str(_ncfg.get("endpoint") or "").strip().rstrip("/")
                if _nep and _is_remote_endpoint(_nep) and f"remote:{_nname}" not in lanes:
                    _lid = f"remote:{_nname}"
                    lanes[_lid] = mios_lanes.Lane(
                        _lid, _nep, str(_ncfg.get("model") or _TOOL_BACKEND_MODEL))
                    chain = chain + [_lid]   # trailing = lowest preference = escalation
    except Exception:  # noqa: BLE001 -- escalation is best-effort; never break lanes
        pass

    async def _probe(url: str) -> bool:
        if get_gpu_arbiter_state() != "AI_EXCLUSIVE":
            if any(h in url for h in (":8520", ":11441", ":8530", "heavy", "vllm", "sglang")):
                return False
        client = await _get_client()
        r = await client.get(f"{url}/models", timeout=2.0)
        return r.status_code == 200

    _LANE_RESOLVER = mios_lanes.LaneResolver(
        lanes, {"heavy": chain, "tool": chain}, _probe,
        ttl=_HEAVY_PROBE_TTL,
        cooldown=float(os.environ.get("MIOS_AGENT_PIPE_LANE_COOLDOWN", "60")))
    try:
        log.info("lane resolver: heavy_engine=%s chain=%s", heavy_engine, chain)
    except Exception:  # noqa: BLE001
        pass
    return _LANE_RESOLVER

async def _pick_tool_backend() -> tuple:
    """(url, model) for the client-tools loop -- delegated to the WS-1 unified lane
    resolver: the preferred heavy reasoner when reachable, else the other heavy lane,
    else the always-on light lane (with per-lane cooldown so a dead lane fails over,
    never 404s). Degrade-open: any resolver error falls back to the legacy heavy/light
    probe so the agentic surface never hard-fails."""
    gpu_state = get_gpu_arbiter_state()
    if gpu_state != "AI_EXCLUSIVE":
        # Proactive Zero-Latency Gaming Failover: GPU is not in AI_EXCLUSIVE mode
        # (e.g. DRAINING_SLEEP, VM_ATTACHED). Immediately route to light (:8500)
        # or cpu_node (:8510) without any probe timeouts or errors!
        log.info("GPU arbiter in state %s: zero-latency routing to light/cpu lane (%s)", gpu_state, _TOOL_BACKEND)
        return _TOOL_BACKEND, _TOOL_BACKEND_MODEL

    try:
        lane = await _lane_resolver().pick("tool")
        if lane is not None:
            return lane.url, lane.model
    except Exception as _e:  # noqa: BLE001 -- degrade to the legacy probe
        log.debug("lane resolver failed (-> legacy pick): %s", _e)
    if await _heavy_lane_up():
        return _TOOL_BACKEND_HEAVY, _TOOL_BACKEND_HEAVY_MODEL
    return _TOOL_BACKEND, _TOOL_BACKEND_MODEL

def _lane_resolver_current():
    """The live ``_LANE_RESOLVER`` singleton (None until first resolved). server.py's
    cluster-health route reads it through this getter because the global is REBOUND at
    runtime by ``_lane_resolver`` and so is unsafe to inject/re-import by value."""
    return _LANE_RESOLVER

def evaluate_output_quality(output: str) -> tuple:
    """Evaluate local output quality and determine if escalation is triggered via quality_gate."""
    from mios_pipe.routing.quality_gate import evaluate_quality
    return evaluate_quality(output)

