# AI-hint: Stdlib unit test for mios_clusterhealth -- the cluster/scheduler/health route LOGIC extracted VERBATIM from server.py (refactor ROUTE-SURFACE wave).
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Stdlib unit tests for mios_clusterhealth (refactor ROUTE-SURFACE) -- stubbed, no I/O."""

import asyncio
import json
import sys
import types

import mios_clusterhealth as M

_REAL_PROBE = M._probe_one_endpoint
_REAL_LANE_SCHED = M._lane_sched_stats
_REAL_KERNEL_DETAIL = M._kernel_managers_detail
_REAL_FAILOVER = M._resolve_failover_chain

_fails = 0

class _FakeResp:
    """Minimal stand-in for an httpx response (status_code + .json())."""

    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

class _FakeClient:
    """Async client whose .get(url) is routed through a handler -- no network."""

    def __init__(self, handler):
        self._handler = handler

    async def get(self, url, **kwargs):
        return self._handler(url, **kwargs)

def check(name, ok, detail=""):
    global _fails
    if ok:
        print(f"[PASS] {name}")
    else:
        _fails += 1
        print(f"[FAIL] {name} :: {detail}")

def _body(resp):
    """Decode a fastapi JSONResponse rendered body into a dict."""
    return json.loads(bytes(resp.body).decode("utf-8"))

class _FakeResolver:
    def snapshot(self):
        return {"engine": "heavy", "cooldown": 0}

def _install_resolver(current):
    fake = types.ModuleType("mios_lanes_resolver")
    fake._lane_resolver_current = lambda: current
    sys.modules["mios_lanes_resolver"] = fake

_REGISTRY = {
    "hermes": {"role": "gateway", "default": True, "enabled": True,
               "endpoint": "http://hermes:8642", "model": "m"},
    "opencode": {"role": "coder", "default": False, "enabled": True,
                 "endpoint": "http://opencode:8633", "model": "c"},
    "disabled_peer": {"role": "x", "default": False, "enabled": False,
                      "endpoint": "http://x:9", "model": "z"},
}

def _resolve_failover_chain(name):
    cfg = _REGISTRY[name]
    return [{"name": name, "endpoint": cfg["endpoint"],
             "model": cfg["model"], "kind": "primary"}]

def _probe_results(name):
    return name != "disabled_peer"

async def _probe_one_endpoint(client, ep, timeout_s=3.0):
    up = ep not in ("http://x:9",)
    return (up, ["model-a", "model-b"] if up else [], 12 if up else 0)

def _agent_lane(cfg):
    return "gpu"

class _Gate:
    def stats(self):
        return {"queued": 0, "in_flight": 1, "cap": 3}

class _Tracer:
    def stats(self):
        return {"buffered": 0, "enabled": True}

    def recent(self, n):
        return []

class _Conflict:
    def stats(self):
        return {"serialized": [], "in_flight": 0}

class _Preempt:
    def stats(self):
        return {"suspended": 0, "free_slots": 3}

class _Ledger:
    def over_budget(self, b):
        return False

    def snapshot(self):
        return {"wh": 0.0, "usd": 0.0, "tokens": 0}

_KERNEL = types.SimpleNamespace(
    managers=lambda: {"scheduler": True, "memory": True},
    dispatcher=types.SimpleNamespace(modes=lambda: ["dag", "chat", "agent"]),
)

def _configure_common():
    M.configure(
        _AGENT_REGISTRY=_REGISTRY,
        _resolve_failover_chain=_resolve_failover_chain,
        _probe_one_endpoint=_probe_one_endpoint,
        _agent_lane=_agent_lane,
        _lane_sched_stats=lambda: [{"lane": "gpu", "cap": 3, "in_flight": 1,
                                    "available": 2, "queued": 0}],
        AGENT_CONCURRENCY=3,
        _PG_PRIMARY=True,
        ADMIT_ENABLE=False,
        _over_global_ceiling=lambda: False,
        ADMIT_LOAD_CEIL=8.0,
        ADMIT_MEM_PCT=90.0,
        _host_stats_cached=lambda: {"load": 0.1, "mem_pct": 33.0},
        PRIORITY_QUEUE_ENABLE=False,
        PRIORITY_STARVATION_S=30.0,
        _GLOBAL_PRIORITY_GATE=_Gate(),
        KV_FORK_ENABLE=False,
        KV_PAGING_ENABLE=True,
        KV_PAGING_SLOT=0,
        KV_FORK_MAX_BRANCHES=4,
        _KV_RESIDENT={},
        KNOWLEDGE_EVICT_ENABLE=False,
        KNOWLEDGE_EVICT_DRYRUN=True,
        KNOWLEDGE_EVICT_INTERVAL_S=3600,
        KNOWLEDGE_EVICT_TTL_DAYS=30,
        KNOWLEDGE_EVICT_MAX_ROWS=1000,
        KNOWLEDGE_EVICT_BATCH=100,
        _TOOL_CONFLICT=_Conflict(),
        _TRACER=_Tracer(),
        RR_ENABLE=False,
        RR_QUANTUM_S=0.5,
        RR_SLICE_TOKENS=256,
        _PREEMPT=_Preempt(),
        BATCH_ENABLE=False,
        BATCH_INTERVAL_S=0.05,
        BATCH_MAX_SIZE=8,
        BATCH_NATIVE_HINTS=[],
        SMARTROUTE_ENABLE=False,
        SMARTROUTE_BUDGET=2,
        SLO_SHED_ENABLE=False,
        COST_ACCOUNTING_ENABLE=False,
        COST_BUDGET_USD=0.0,
        _COST_LEDGER=_Ledger(),
        _KERNEL=_KERNEL,
        _kernel_managers_detail=lambda: {"scheduler": {"queued": 0}},
        KERNEL_ROUTE=False,
        app=types.SimpleNamespace(version="9.9.9-test"),
        _ALLOWLIST_HOSTS={"localhost", "127.0.0.1"},
        _HIGH_PRIVILEGE_VERBS={"shell_exec", "container_restart"},
        _HIGH_PRIVILEGE_CURATED={"shell_exec"},
        _TAINT_VERBS={"web_extract"},
        _toml_section=lambda s: ({"firewall_high_privilege_verbs": ["container_restart"]}
                                 if s == "security" else {}),
        _passport_load_priv=lambda: None,
        _passport_kid=lambda: "kid-test",
        SKILLS_ENABLED=True,
        SKILLS_MIN_LENGTH=10,
        SKILLS_MAX_LENGTH=4000,
        SKILLS_MIN_SUPPORT=2,
        SKILLS_WINDOW_HOURS=168,
        SKILLS_AUTO_PROMOTE_THRESHOLD=5,
        PASSPORT_ENABLE=False,
        PASSPORT_ALGO="EdDSA",
        PASSPORT_AGENT_NAME="MiOS AI",
        PASSPORT_KEY_DIR="/var/lib/mios/ai/passport",
        PASSPORT_VERIFY_ON_READ=False,
        LAUNCHER_SOCK="/nonexistent/mios-launch.sock",
        DB_URL="http://localhost:8000",
    )

def t_probe_one_endpoint():
    M.configure(_probe_auth_headers=lambda ep: {"Authorization": "Bearer t"})
    r, lm, ms = asyncio.run(_REAL_PROBE(
        _FakeClient(lambda url, **k: _FakeResp(200, {"data": [{"id": "m1"},
                                                              {"id": "m2"}]})),
        "http://ep/v1"))
    check("probe: openai /models reachable + ids", r is True and lm == ["m1", "m2"])
    check("probe: latency_ms is int", isinstance(ms, int) and ms >= 0)
    r0, lm0, ms0 = asyncio.run(_REAL_PROBE(_FakeClient(lambda url, **k: None), ""))
    check("probe: empty ep -> (False,[],0)", r0 is False and lm0 == [] and ms0 == 0)

    def _raise(url, **k):
        raise RuntimeError("conn refused")
    rd, lmd, _ = asyncio.run(_REAL_PROBE(_FakeClient(_raise), "http://dead/v1"))
    check("probe: unreachable -> down", rd is False and lmd == [])

    def _no_v1(url, **k):
        if url.endswith("/models"):
            raise RuntimeError("no openai surface")
        return _FakeResp(200, {"models": [{"name": "n1"}]})
    rt, lmt, _ = asyncio.run(_REAL_PROBE(_FakeClient(_no_v1), "http://ep/v1"))
    check("probe: no /v1/models surface -> down (no /api/tags fallback)",
          rt is False and lmt == [])

def t_lane_sched_stats():
    sems = {"gpu": asyncio.Semaphore(3), "cpu": asyncio.Semaphore(2)}
    M.configure(_LANE_SEMS=sems, AGENT_CONCURRENCY=4)
    by = {r["lane"]: r for r in _REAL_LANE_SCHED()}
    check("lane: both lanes present (sorted introspection)",
          set(by) == {"cpu", "gpu"})
    check("lane: gpu available == idle permits", by["gpu"]["available"] == 3)
    check("lane: gpu queued == 0 (no waiters)", by["gpu"]["queued"] == 0)
    check("lane: in_flight == cap - available",
          by["gpu"]["in_flight"] == max(0, by["gpu"]["cap"] - 3)
          and isinstance(by["gpu"]["cap"], int))

def t_kernel_managers_detail():
    M.configure(
        _GLOBAL_PRIORITY_GATE=_Gate(),
        _PREEMPT=_Preempt(),
        _MEMORY=types.SimpleNamespace(),
        _PG_PRIMARY=True,
        KV_PAGING_ENABLE=True,
        _VERB_CATALOG={"a": 1, "b": 2, "c": 3},
        _PERMISSION_TIERS={"public", "user", "admin"},
    )
    d = _REAL_KERNEL_DETAIL()
    check("kernel: scheduler seam from gate.stats()",
          d["scheduler"] == {"queued": 0, "in_flight": 1, "cap": 3})
    check("kernel: memory provider type name + pg_primary",
          d["memory"]["provider"] == "SimpleNamespace"
          and d["memory"]["pg_primary"] is True)
    check("kernel: context kv_paging", d["context"]["kv_paging"] is True)
    check("kernel: tools verb count", d["tools"]["verbs"] == 3)
    check("kernel: access pdp + tiers",
          d["access"]["pdp"] is True
          and set(d["access"]["tiers"]) == {"public", "user", "admin"})

def t_resolve_failover_chain():
    reg = {
        "a0": {"endpoint": "http://h0/v1", "model": "m0",
               "cpu_endpoint": "http://h0cpu", "cpu_model": "m0cpu",
               "failover_agents": ["a1", "a0", "ghost"]},
        "a1": {"endpoint": "http://h1/v1", "model": "m1"},
        "a2": {"endpoint": "http://h2", "model": "m2", "cpu_endpoint": "http://h2"},
    }
    M.configure(_AGENT_REGISTRY=reg)

    chain = _REAL_FAILOVER("a0")
    kinds = [h["kind"] for h in chain]
    check("failover: chain kinds primary->failover->cpu-twin",
          kinds == ["primary", "failover", "cpu-twin"], str(kinds))
    check("failover: self-loop + dangling ref skipped (one failover only)",
          [h["name"] for h in chain] == ["a0", "a1", "a0.cpu"],
          str([h["name"] for h in chain]))
    check("failover: cpu twin carries cpu_model + cpu_endpoint",
          chain[2]["model"] == "m0cpu" and chain[2]["endpoint"] == "http://h0cpu")

    chain2 = _REAL_FAILOVER("a2")
    check("failover: cpu_endpoint==endpoint adds no twin",
          [h["kind"] for h in chain2] == ["primary"], str(chain2))

    check("failover: unknown agent -> []", _REAL_FAILOVER("nope") == [])

def t_cluster_health():
    _install_resolver(_FakeResolver())
    _configure_common()
    b = _body(asyncio.run(M.cluster_health_logic()))
    check("cluster: object", b["object"] == "mios.cluster.health")
    names = {a["name"]: a for a in b["agents"]}
    check("cluster: all 3 agents present", set(names) ==
          {"hermes", "opencode", "disabled_peer"}, str(set(names)))
    check("cluster: hermes effective_up", names["hermes"]["effective_up"] is True)
    check("cluster: disabled_peer down", names["disabled_peer"]["effective_up"] is False)
    check("cluster: single_point_of_failure flagged (single primary hop)",
          names["hermes"]["single_point_of_failure"] is True)
    check("cluster: council_peers_up == 1 (opencode only)", b["council_peers_up"] == 1,
          str(b["council_peers_up"]))
    check("cluster: mode is council", b["mode"] == "council")
    check("cluster: lane_resolver snapshot via getter",
          b["lane_resolver"] == {"engine": "heavy", "cooldown": 0})
    check("cluster: agents_up == 2", b["agents_up"] == 2, str(b["agents_up"]))
    _install_resolver(None)
    b2 = _body(asyncio.run(M.cluster_health_logic()))
    check("cluster: lane_resolver None when resolver unbuilt",
          b2["lane_resolver"] is None)

def t_scheduler_state():
    _install_resolver(_FakeResolver())
    _configure_common()
    b = _body(asyncio.run(M.scheduler_state_logic()))
    check("sched: object", b["object"] == "mios.scheduler")
    check("sched: lanes from _lane_sched_stats",
          b["lanes"][0]["lane"] == "gpu" and b["lanes"][0]["cap"] == 3)
    check("sched: global_cap", b["global_cap"] == 3)
    check("sched: admission posture present",
          b["admission"]["enabled"] is False and b["admission"]["load_ceil"] == 8.0)
    check("sched: recall reflects _PG_PRIMARY (pgvector)",
          "pgvector" in b["memory_manager_tiers"]["recall"])
    check("sched: priority_gate merges gate stats",
          b["priority_gate"]["enabled"] is False and b["priority_gate"]["in_flight"] == 1)
    check("sched: kv_fork resident_slots", b["kv_fork"]["resident_slots"] == 0)
    check("sched: kernel modes from dispatcher",
          b["kernel"]["modes"] == ["dag", "chat", "agent"])
    check("sched: kernel shadow_route", b["kernel"]["shadow_route"] is False)
    check("sched: slo classes wired", "classes" in b["slo"])
    check("sched: cost posture", b["cost"]["enabled"] is False)

def t_health():
    _configure_common()
    b = asyncio.run(M.health_logic())  # returns a plain dict, not JSONResponse
    check("health: status ok", b["status"] == "ok")
    check("health: version from injected app", b["version"] == "9.9.9-test")
    check("health: router block present", "enabled" in b["router"])
    check("health: dci act_count", isinstance(b["dci"]["act_count"], int))
    check("health: security allowlist sorted",
          b["security"]["allowlist_hosts"] == sorted({"localhost", "127.0.0.1"}))
    check("health: high_privilege provenance present",
          "total" in b["security"]["high_privilege_provenance"])
    check("health: passport disabled -> kid None",
          b["passport"]["enabled"] is False and b["passport"]["kid"] is None)
    check("health: passport private_key absent",
          b["passport"]["private_key_present"] is False)
    check("health: agents block lists all registry agents",
          set(b["agents"]) == {"hermes", "opencode", "disabled_peer"})
    check("health: broker_present False (nonexistent sock)",
          b["broker_present"] is False)
    check("health: db_url + port surfaced",
          b["db_url"] == "http://localhost:8000" and "port" in b)

def main():
    t_probe_one_endpoint()
    t_lane_sched_stats()
    t_kernel_managers_detail()
    t_resolve_failover_chain()
    t_cluster_health()
    t_scheduler_state()
    t_health()
    print(f"\n{_fails} FAILED" if _fails else "\nok")
    return 1 if _fails else 0



# ==============================================================================
# Consolidated from test_mios_drift_monitor.py (task_1006)
# ==============================================================================
# AI-hint: Stdlib offline unit tests for mios_pipe.observability.drift_monitor -- the Jensen-Shannon Goodhart alarm (CONS-02). No network / no DB / no ...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Stdlib offline unit tests for the Jensen-Shannon drift monitor (CONS-02)."""

import sys

from mios_pipe.observability import drift_monitor as M_drift_monitor

_fails_drift_monitor = 0

def _check_drift_monitor(name, cond):
    global _fails_drift_monitor
    if cond:
        print(f"ok   - {name}")
    else:
        _fails_drift_monitor += 1
        print(f"FAIL - {name}")

def t_histogram():
    h = M_drift_monitor.histogram(["yes", "yes", "no", "no"])
    _check_drift_monitor("histogram: even split", h == {"yes": 0.5, "no": 0.5})
    _check_drift_monitor("histogram: sums to 1.0", abs(sum(h.values()) - 1.0) < 1e-12)
    _check_drift_monitor("histogram: empty input -> {} (not a uniform window)",
          M_drift_monitor.histogram([]) == {})
    _check_drift_monitor("histogram: labels are stringified",
          M_drift_monitor.histogram([1, 1, 2]) == {"1": 2 / 3, "2": 1 / 3})

def t_jsd_bounds():
    p = {"yes": 0.7, "no": 0.3}
    _check_drift_monitor("jsd: identical -> 0.0", M_drift_monitor.jensen_shannon(p, p) == 0.0)
    _check_drift_monitor("jsd: disjoint support -> 1.0",
          M_drift_monitor.jensen_shannon({"a": 1.0}, {"b": 1.0}) == 1.0)
    d = M_drift_monitor.jensen_shannon(p, {"yes": 0.3, "no": 0.7})
    _check_drift_monitor("jsd: partial shift is strictly inside the bounds", 0.0 < d < 1.0)
    _check_drift_monitor("jsd: symmetric",
          abs(M_drift_monitor.jensen_shannon(p, {"yes": 0.1, "no": 0.9})
              - M_drift_monitor.jensen_shannon({"yes": 0.1, "no": 0.9}, p)) < 1e-12)

def t_jsd_monotone():
    base = {"yes": 0.5, "no": 0.5}
    near = M_drift_monitor.jensen_shannon(base, {"yes": 0.6, "no": 0.4})
    far = M_drift_monitor.jensen_shannon(base, {"yes": 0.95, "no": 0.05})
    _check_drift_monitor("jsd: a bigger shift scores higher", far > near)

def t_jsd_degenerate():
    _check_drift_monitor("jsd: empty baseline -> 0.0 (nothing to compare)",
          M_drift_monitor.jensen_shannon({}, {"a": 1.0}) == 0.0)
    _check_drift_monitor("jsd: empty live -> 0.0", M_drift_monitor.jensen_shannon({"a": 1.0}, {}) == 0.0)
    _check_drift_monitor("jsd: all-zero weights -> 0.0",
          M_drift_monitor.jensen_shannon({"a": 0.0}, {"a": 0.0}) == 0.0)
    _check_drift_monitor("jsd: negative and non-numeric weights are dropped",
          M_drift_monitor.jensen_shannon({"a": 1.0, "b": -5.0, "c": "junk"},
                           {"a": 1.0}) == 0.0)
    _check_drift_monitor("jsd: unnormalized input is normalized first",
          abs(M_drift_monitor.jensen_shannon({"a": 70, "b": 30}, {"a": 0.7, "b": 0.3})) < 1e-12)

def t_compare_alerting():
    base = {"verdict": {"satisfied": 0.9, "unsatisfied": 0.1}}
    same = {"verdict": {"satisfied": 0.9, "unsatisfied": 0.1}}
    r = M_drift_monitor.compare(base, same, threshold=0.2)
    _check_drift_monitor("compare: no shift -> not alerting", r["alerting"] is False)
    _check_drift_monitor("compare: no shift -> divergence 0.0", r["max_divergence"] == 0.0)

    flipped = {"verdict": {"satisfied": 0.1, "unsatisfied": 0.9}}
    r = M_drift_monitor.compare(base, flipped, threshold=0.2)
    _check_drift_monitor("compare: flipped verdicts -> alerting", r["alerting"] is True)
    _check_drift_monitor("compare: names the worst axis", r["max_axis"] == "verdict")
    _check_drift_monitor("compare: axis carries its own flag",
          r["axes"]["verdict"]["alerting"] is True)
    _check_drift_monitor("compare: is_alerting agrees", M_drift_monitor.is_alerting(r) is True)

    r = M_drift_monitor.compare(base, flipped, threshold=0.99)
    _check_drift_monitor("compare: a high threshold suppresses the alarm", r["alerting"] is False)
    _check_drift_monitor("compare: suppressed alarm still reports the divergence",
          r["max_divergence"] > 0.0)

def t_compare_incomparable():
    base = {"verdict": {"satisfied": 1.0}, "intent": {"chat": 1.0}}
    live = {"verdict": {"unsatisfied": 1.0}}
    r = M_drift_monitor.compare(base, live, threshold=0.1)
    _check_drift_monitor("compare: an axis missing from live is compared=False",
          r["axes"]["intent"]["compared"] is False)
    _check_drift_monitor("compare: a missing axis never alerts",
          r["axes"]["intent"]["alerting"] is False)
    _check_drift_monitor("compare: the present axis still alerts",
          r["axes"]["verdict"]["alerting"] is True)

def t_compare_thin_window():
    base = {"verdict": {"satisfied": 1.0}}
    live = {"verdict": {"unsatisfied": 1.0}}
    r = M_drift_monitor.compare(base, live, threshold=0.1, min_samples=50,
                  live_counts={"verdict": 3})
    _check_drift_monitor("compare: a thin live window is not evidence of drift",
          r["alerting"] is False)
    _check_drift_monitor("compare: thin window is marked uncompared",
          r["axes"]["verdict"]["compared"] is False)

    r = M_drift_monitor.compare(base, live, threshold=0.1, min_samples=50,
                  live_counts={"verdict": 500})
    _check_drift_monitor("compare: a full window alerts normally", r["alerting"] is True)

def t_is_alerting_tolerates_junk():
    _check_drift_monitor("is_alerting: empty report -> False", M_drift_monitor.is_alerting({}) is False)
    _check_drift_monitor("is_alerting: malformed report -> False", M_drift_monitor.is_alerting(None) is False)

def _server_or_skip():
    """Import server.py for the route-level cases, or None on a bare checkout
    without fastapi -- the pure-math cases above still run either way.

    Stubs only `websockets` (the portal terminal proxy imports it at module
    load and no route here touches it), exactly as test_mios_approutes does."""
    import types  # noqa: PLC0415
    ws = types.ModuleType("websockets")
    wse = types.ModuleType("websockets.exceptions")
    wse.ConnectionClosed = type("ConnectionClosed", (Exception,), {})
    ws.exceptions = wse
    sys.modules.setdefault("websockets", ws)
    sys.modules.setdefault("websockets.exceptions", wse)
    for sub in ("legacy", "legacy.client", "client", "sync", "sync.client",
                "asyncio", "asyncio.client"):
        sys.modules.setdefault("websockets." + sub,
                               types.ModuleType("websockets." + sub))
    try:
        import server  # noqa: PLC0415
        return server
    except Exception:  # noqa: BLE001
        return None

def t_route_axis_extractors():
    srv = _server_or_skip()
    if srv is None:
        print("skip - route cases (fastapi absent)")
        return
    rows = [
        {"kind": "user_query_satisfied", "payload": {"refine_intent": "chat"}},
        {"kind": "user_query_satisfied", "payload": '{"refine_intent": "agent"}'},
        {"kind": "user_query_unsatisfied", "payload": {"refine_intent": ""}},
    ]
    dist, n = srv._drift_live_window(rows, "verdict")
    _check_drift_monitor("route: verdict axis counts every row", n == 3)
    _check_drift_monitor("route: verdict axis splits 2/1",
          abs(dist["user_query_satisfied"] - 2 / 3) < 1e-12)

    dist, n = srv._drift_live_window(rows, "intent")
    _check_drift_monitor("route: intent axis skips the empty label", n == 2)
    _check_drift_monitor("route: intent axis parses a JSON-string payload",
          set(dist) == {"chat", "agent"})

    dist, n = srv._drift_live_window(rows, "no_such_axis")
    _check_drift_monitor("route: an axis with no extractor yields nothing", (dist, n) == ({}, 0))

def t_route_payload_normalization():
    srv = _server_or_skip()
    if srv is None:
        return
    _check_drift_monitor("route: dict payload passes through",
          srv._drift_payload({"payload": {"a": 1}}) == {"a": 1})
    _check_drift_monitor("route: JSON-string payload is parsed",
          srv._drift_payload({"payload": '{"a": 1}'}) == {"a": 1})
    _check_drift_monitor("route: unparseable payload -> {}",
          srv._drift_payload({"payload": "not json"}) == {})
    _check_drift_monitor("route: missing payload -> {}", srv._drift_payload({}) == {})

def t_route_gate_closed():
    srv = _server_or_skip()
    if srv is None:
        return
    _check_drift_monitor("route: the monitor ships disabled",
          srv.DRIFT_MONITOR_ENABLED is False)
    import asyncio as _a
    body = _a.run(srv.v1_drift()).body.decode()
    _check_drift_monitor("route: disabled -> enabled:false, no alert",
          '"enabled":false' in body.replace(" ", "")
          and '"alerting":false' in body.replace(" ", ""))

def _main_drift_monitor():
    t_histogram()
    t_jsd_bounds()
    t_jsd_monotone()
    t_jsd_degenerate()
    t_compare_alerting()
    t_compare_incomparable()
    t_compare_thin_window()
    t_is_alerting_tolerates_junk()
    t_route_axis_extractors()
    t_route_payload_normalization()
    t_route_gate_closed()
    print(f"\n{_fails_drift_monitor} FAILED" if _fails_drift_monitor else "\nok")
    return 1 if _fails_drift_monitor else 0


def _run_extra_drift_monitor():
    try:
        return _main_drift_monitor()
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0



# ==============================================================================
# Consolidated from test_mios_health.py (task_1006)
# ==============================================================================
# AI-hint: Unit test suite for mios_pipe.health module.
# AI-related: mios_pipe/health.py
"""Unit tests for mios_pipe.health."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from mios_pipe.health import build_health_response, get_system_version

class TestHealth(unittest.TestCase):
    """Test health response builder."""

    def test_build_health_response_defaults(self):
        resp = build_health_response()
        self.assertEqual(resp["status"], "ok")
        self.assertIsNotNone(resp["version"])
        self.assertIsNotNone(resp["backend"])
        self.assertIsInstance(resp["port"], int)

    def test_build_health_response_overrides(self):
        resp = build_health_response(status="healthy", version="0.3.0", backend="http://localhost:8642", port=8640)
        self.assertEqual(resp["status"], "healthy")
        self.assertEqual(resp["version"], "0.3.0")
        self.assertEqual(resp["backend"], "http://localhost:8642")
        self.assertEqual(resp["port"], 8640)

    def test_get_system_version(self):
        v = get_system_version()
        self.assertIsInstance(v, str)


def _run_extra_health():
    try:
        import unittest
        suite = unittest.TestSuite()
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestHealth))
        res = unittest.TextTestRunner().run(suite)
        return 0 if res.wasSuccessful() else 1
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0



def _run_all_folded_clusterhealth_suites():
    rc = _run_extra_drift_monitor()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")
    rc = _run_extra_health()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _run_all_folded_clusterhealth_suites()
    sys.exit(main())
