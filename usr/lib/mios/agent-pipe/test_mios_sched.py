# AI-hint: Standalone unit test for mios_sched -- PriorityGate concurrency logic (permit capping, priority reordering, anti-starvation) plus the lane/schedulin...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Standalone unit test for mios_sched.PriorityGate (WS-1).

Pure stdlib + the sibling module only -- no server.py import, so it runs on any
Python 3.10+ without the agent-pipe runtime deps (httpx/fastapi/...). Mirrors the
_execute_dag_saturated standalone-test pattern: a mock-free asyncio harness with
explicit asserts and a PASS/FAIL summary; exit code != 0 on any failure.

Run:  python test_mios_sched.py
"""

import asyncio
import contextlib
import os
import sys

import mios_sched as M
from mios_sched import PriorityGate

_RESULTS: list = []

def _check(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

@contextlib.contextmanager
def _sched_cfg(cfg: dict, mode_env=None):
    """Inject a [sched] table (M._SCHED_TOML) + optional MIOS_SCHED_PRIORITY_MODE for
    the duration of the block, restoring both afterward -- so a test exercises an exact
    config without touching mios.toml or leaking env between tests."""
    _saved_toml = M._SCHED_TOML
    _saved_env = os.environ.get("MIOS_SCHED_PRIORITY_MODE")
    M._SCHED_TOML = cfg
    if mode_env is None:
        os.environ.pop("MIOS_SCHED_PRIORITY_MODE", None)
    else:
        os.environ["MIOS_SCHED_PRIORITY_MODE"] = mode_env
    try:
        yield
    finally:
        M._SCHED_TOML = _saved_toml
        if _saved_env is None:
            os.environ.pop("MIOS_SCHED_PRIORITY_MODE", None)
        else:
            os.environ["MIOS_SCHED_PRIORITY_MODE"] = _saved_env

async def t_basic_bound() -> None:
    """cap permits issue immediately; the (cap+1)th blocks until a release."""
    g = PriorityGate(2)
    await g.acquire(5)
    await g.acquire(5)
    _check("basic: cap reached", g.available == 0 and g.in_flight == 2,
           f"avail={g.available} in_flight={g.in_flight}")
    got = []

    async def third():
        await g.acquire(5)
        got.append(1)
        g.release()

    t = asyncio.create_task(third())
    await asyncio.sleep(0.03)
    _check("basic: 3rd blocks", got == [] and g.queued == 1, f"queued={g.queued}")
    g.release()                       # frees one -> hands to the waiter
    await asyncio.gather(t)
    _check("basic: 3rd ran after release", got == [1])
    g.release()
    _check("basic: drained to full", g.available == g.cap, f"avail={g.available}")

async def t_priority_reorder() -> None:
    """A later HIGH-priority waiter is served before an earlier LOW one."""
    g = PriorityGate(1)
    await g.acquire(5)                # hold the only permit
    order = []

    async def worker(p, label):
        await g.acquire(p)
        order.append(label)
        g.release()

    t_low = asyncio.create_task(worker(2, "low"))
    await asyncio.sleep(0.01)         # low enqueues first
    t_high = asyncio.create_task(worker(8, "high"))
    await asyncio.sleep(0.01)         # high enqueues second
    _check("priority: both queued", g.queued == 2, f"queued={g.queued}")
    g.release()                       # next slot must go to HIGH despite arriving later
    await asyncio.gather(t_low, t_high)
    _check("priority: high before low", order == ["high", "low"], f"order={order}")

async def t_fifo_tiebreak() -> None:
    """Equal priority -> earliest arrival wins."""
    g = PriorityGate(1)
    await g.acquire(5)
    order = []

    async def worker(label):
        await g.acquire(5)
        order.append(label)
        g.release()

    a = asyncio.create_task(worker("a"))
    await asyncio.sleep(0.01)
    b = asyncio.create_task(worker("b"))
    await asyncio.sleep(0.01)
    g.release()
    await asyncio.gather(a, b)
    _check("fifo tie-break: a before b", order == ["a", "b"], f"order={order}")

async def t_anti_starvation() -> None:
    """An aged low-priority waiter is served ahead of a fresh high-priority one."""
    g = PriorityGate(1, starvation_s=0.1)
    await g.acquire(5)
    order = []

    async def worker(p, label):
        await g.acquire(p)
        order.append(label)
        g.release()

    t_low = asyncio.create_task(worker(2, "low"))
    await asyncio.sleep(0.15)         # low ages past the 0.1s starvation threshold
    t_high = asyncio.create_task(worker(9, "high"))
    await asyncio.sleep(0.01)         # high is fresh
    g.release()                       # aging must serve LOW first despite lower prio
    await asyncio.gather(t_low, t_high)
    _check("anti-starvation: aged low first", order == ["low", "high"],
           f"order={order}")

async def t_cancel_while_queued() -> None:
    """Cancelling a still-queued waiter (case a) leaks no permit."""
    g = PriorityGate(1)
    await g.acquire(5)

    async def worker():
        await g.acquire(5)

    t = asyncio.create_task(worker())
    await asyncio.sleep(0.02)
    _check("cancel-queued: enqueued", g.queued == 1, f"queued={g.queued}")
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass
    await asyncio.sleep(0.01)
    _check("cancel-queued: dequeued", g.queued == 0, f"queued={g.queued}")
    g.release()
    _check("cancel-queued: no leak", g.available == 1, f"avail={g.available}")

async def t_cancel_after_grant() -> None:
    """Cancelling a just-granted waiter (case b) hands the permit back."""
    g = PriorityGate(1)
    await g.acquire(5)

    async def worker():
        await g.acquire(5)
        g.release()

    t = asyncio.create_task(worker())
    await asyncio.sleep(0.02)
    g.release()
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass
    await asyncio.sleep(0.01)
    _check("cancel-granted: no leak",
           g.queued == 0 and g.available == 1,
           f"queued={g.queued} avail={g.available}")

async def t_cap_never_exceeded() -> None:
    """Under heavy mixed-priority load the cap is never exceeded."""
    cap = 3
    g = PriorityGate(cap, starvation_s=0.02)
    cur = 0
    peak = 0
    done = 0

    async def worker(p):
        nonlocal cur, peak, done
        await g.acquire(p)
        cur += 1
        peak = max(peak, cur)
        await asyncio.sleep(0.005)
        cur -= 1
        done += 1
        g.release()

    tasks = [asyncio.create_task(worker(float((i % 9) + 1))) for i in range(40)]
    await asyncio.gather(*tasks)
    _check("load: peak <= cap", peak <= cap, f"peak={peak} cap={cap}")
    _check("load: all completed", done == 40, f"done={done}")
    _check("load: drained to full", g.available == cap, f"avail={g.available}")

async def t_tenant_default_off() -> None:
    """tenant_cap=0 (the default) -> the tenant arg is inert: pick is PURE priority and
    nothing is tracked, byte-identical to the pre-V5 gate."""
    g = PriorityGate(1)                       # tenant_cap defaults to 0
    await g.acquire(5, tenant="A")            # tenant passed but never tracked
    order = []

    async def worker(p, label):
        await g.acquire(p, tenant="A")        # SAME tenant, both queued
        order.append(label)
        g.release(tenant="A")

    t_low = asyncio.create_task(worker(2, "low"))
    await asyncio.sleep(0.01)
    t_high = asyncio.create_task(worker(8, "high"))
    await asyncio.sleep(0.01)
    g.release(tenant="A")                     # cap=0 -> pure priority, tenant ignored
    await asyncio.gather(t_low, t_high)
    _check("tenant default-off: pure priority (tenant ignored)", order == ["high", "low"],
           f"order={order}")
    _check("tenant default-off: nothing tracked", g.tenant_inflight("A") == 0)

async def t_tenant_fair_share() -> None:
    """Under contention, a freed slot goes to a tenant UNDER its cap even over a
    HIGHER-priority waiter whose tenant is AT its cap -- one tenant can't starve another.
    Tenant A holds a slot for the whole test (A pinned AT cap); B1 holds its slot so the
    fairness moment (B1 served, A2 still queued) is observable, then A2 is served
    (degrade-open: it becomes the sole waiter)."""
    g = PriorityGate(2, tenant_cap=1)         # 2 global slots, 1 per tenant
    await g.acquire(5, tenant="A")            # A holds slot 1 (A in-flight=1, pinned AT cap)
    await g.acquire(5, tenant="C")            # C holds slot 2 -> gate FULL
    order = []
    hold_b = asyncio.Event()

    async def a2():                            # HIGH prio, but tenant A is AT cap
        await g.acquire(9, tenant="A")
        order.append("A2")
        g.release(tenant="A")

    async def b1():                            # LOW prio, tenant B is UNDER cap
        await g.acquire(2, tenant="B")
        order.append("B1")
        await hold_b.wait()                    # HOLD the slot so A2 stays queued
        g.release(tenant="B")

    tA2 = asyncio.create_task(a2())
    await asyncio.sleep(0.01)
    tB1 = asyncio.create_task(b1())
    await asyncio.sleep(0.01)
    _check("tenant fair-share: both queued", g.queued == 2, f"queued={g.queued}")
    g.release(tenant="C")                     # a DIFFERENT tenant frees a slot
    await asyncio.sleep(0.02)
    _check("tenant fair-share: under-cap B served before at-cap A despite lower prio",
           order == ["B1"], f"order={order}")
    _check("tenant fair-share: at-cap A2 still queued (throttled)", g.queued == 1,
           f"queued={g.queued}")
    hold_b.set()                              # B1 releases -> A2 is now the sole waiter
    await asyncio.gather(tA2, tB1)
    _check("tenant fair-share: at-cap tenant served once it is the sole waiter (no wedge)",
           order == ["B1", "A2"], f"order={order}")
    g.release(tenant="A")                     # release A's original pinned slot
    _check("tenant fair-share: drained + all tenant counts cleared",
           g.available == g.cap and g.tenant_inflight("A") == 0
           and g.tenant_inflight("B") == 0, f"avail={g.available}")

async def t_tenant_degrade_open() -> None:
    """When the ONLY live waiter's tenant is at its cap, the gate degrades OPEN and
    serves it anyway -- the per-tenant cap must NEVER wedge admission."""
    g = PriorityGate(2, tenant_cap=1)
    await g.acquire(5, tenant="A")            # A AT cap (slot 1)
    await g.acquire(5, tenant="X")            # gate full (slot 2)
    order = []

    async def worker():
        await g.acquire(9, tenant="A")        # tenant A is already at cap
        order.append("A2")
        g.release(tenant="A")

    t = asyncio.create_task(worker())
    await asyncio.sleep(0.01)
    g.release(tenant="X")                     # frees a slot; only an at-cap A waiter exists
    await asyncio.sleep(0.02)
    _check("tenant degrade-open: at-cap sole waiter still served (no wedge)",
           order == ["A2"], f"order={order}")
    g.release(tenant="A")
    _check("tenant degrade-open: drained to full", g.available == g.cap, f"avail={g.available}")

async def t_tenant_none_uncapped() -> None:
    """A None tenant (system/daemon, no forwarded owner) is NEVER capped -> pure priority
    and zero tracking, even with a cap configured."""
    g = PriorityGate(1, tenant_cap=1)
    await g.acquire(5, tenant=None)
    order = []

    async def worker(p, label):
        await g.acquire(p, tenant=None)
        order.append(label)
        g.release(tenant=None)

    t_low = asyncio.create_task(worker(2, "low"))
    await asyncio.sleep(0.01)
    t_high = asyncio.create_task(worker(8, "high"))
    await asyncio.sleep(0.01)
    g.release(tenant=None)
    await asyncio.gather(t_low, t_high)
    _check("tenant none-uncapped: pure priority for None tenant", order == ["high", "low"],
           f"order={order}")
    _check("tenant none-uncapped: None never tracked", g.tenant_inflight(None) == 0)

def _configure_helpers(*, offload_cpu: bool = False, slow_cap: int = 12,
                       default_cap: int = 24, agent_lane=None) -> None:
    M.configure(
        _AUTO_PRIO_WORDS={"low": 1.0, "normal": 5.0, "medium": 5.0, "high": 9.0},
        LANE_TOOL_CAP={"igpu": 15, "mobile": 15},
        SLOW_LANES={"cpu", "igpu", "mobile", "accelerator"},
        SLOW_LANE_TOOL_CAP=slow_cap,
        DEFAULT_TOOL_CAP=default_cap,
        DISPATCH_OFFLOAD_CPU=offload_cpu,
        _OFFLOAD_ENGINES=("cpu", "igpu", "accelerator"),
        _agent_lane=agent_lane if agent_lane is not None else (lambda cfg: "gpu"),
    )

async def t_lane_tool_cap() -> None:
    """Explicit per-lane entry > slow-lane fallback > DEFAULT_TOOL_CAP; slow_cap=0 opts out."""
    _configure_helpers(slow_cap=12, default_cap=24)
    _check("lane_tool_cap: explicit entry", M._lane_tool_cap("igpu") == 15,
           f"got={M._lane_tool_cap('igpu')}")
    _check("lane_tool_cap: slow-lane fallback", M._lane_tool_cap("cpu") == 12,
           f"got={M._lane_tool_cap('cpu')}")
    _check("lane_tool_cap: default for fast lane", M._lane_tool_cap("gpu") == 24,
           f"got={M._lane_tool_cap('gpu')}")
    _check("lane_tool_cap: case/space-insensitive", M._lane_tool_cap("  IGPU ") == 15,
           f"got={M._lane_tool_cap('  IGPU ')}")
    _configure_helpers(slow_cap=0, default_cap=24)   # slow_cap=0 => opt out -> default
    _check("lane_tool_cap: slow_cap=0 -> default", M._lane_tool_cap("cpu") == 24,
           f"got={M._lane_tool_cap('cpu')}")

async def t_agent_offload_engine() -> None:
    """Off -> None always; on -> first _OFFLOAD_ENGINES member present, else None."""
    _configure_helpers(offload_cpu=False)
    _check("offload_engine: off -> None",
           M._agent_offload_engine({"engines": {"cpu": {}}}) is None)
    _configure_helpers(offload_cpu=True)
    _check("offload_engine: first match wins",
           M._agent_offload_engine({"engines": {"igpu": {}, "accelerator": {}}}) == "igpu",
           f"got={M._agent_offload_engine({'engines': {'igpu': {}, 'accelerator': {}}})}")
    _check("offload_engine: cpu preferred when present",
           M._agent_offload_engine({"engines": {"cpu": {}, "igpu": {}}}) == "cpu")
    _check("offload_engine: no light engine -> None",
           M._agent_offload_engine({"engines": {"gpu": {}}}) is None)
    _check("offload_engine: missing engines key -> None",
           M._agent_offload_engine({}) is None)

async def t_resolve_autonomous_priority() -> None:
    """Numeric env wins; a word maps via _AUTO_PRIO_WORDS; unknown -> 1.0 floor."""
    _configure_helpers()
    _saved = os.environ.get("MIOS_AUTONOMOUS_PRIORITY")
    try:
        os.environ["MIOS_AUTONOMOUS_PRIORITY"] = "3.5"
        _check("auto_prio: numeric env", M._resolve_autonomous_priority() == 3.5,
               f"got={M._resolve_autonomous_priority()}")
        os.environ["MIOS_AUTONOMOUS_PRIORITY"] = "high"
        _check("auto_prio: word -> map", M._resolve_autonomous_priority() == 9.0,
               f"got={M._resolve_autonomous_priority()}")
        os.environ["MIOS_AUTONOMOUS_PRIORITY"] = "bogus"
        _check("auto_prio: unknown word -> 1.0", M._resolve_autonomous_priority() == 1.0,
               f"got={M._resolve_autonomous_priority()}")
    finally:
        if _saved is None:
            os.environ.pop("MIOS_AUTONOMOUS_PRIORITY", None)
        else:
            os.environ["MIOS_AUTONOMOUS_PRIORITY"] = _saved

async def t_sched_priority() -> None:
    """DEFAULT path (empty [sched]) reproduces the historical scoring byte-for-byte.
    The term inputs are pulled DYNAMICALLY from the SSOT fallback set, so no English
    example word is the test's source of truth (the asserted NUMBERS lock behaviour)."""
    with _sched_cfg({}):                       # force the degrade-open fallback path
        base = M._sched_priority(None)
        _check("sched_priority: defaults", base == {
            "score": 3.4, "complexity": 1, "urgency": 5, "intent": "agent"}, f"got={base}")
        _hi = next(iter(M._SCHED_FALLBACK["urgency_high_terms"]))
        _lo = next(iter(M._SCHED_FALLBACK["urgency_low_terms"]))
        urgent = M._sched_priority({"urgency": _hi})
        _check("sched_priority: urgency high", urgent["urgency"] == 9 and urgent["score"] == 5.8,
               f"got={urgent}")
        bg = M._sched_priority({"urgency": _lo})
        _check("sched_priority: urgency low", bg["urgency"] == 2 and bg["score"] == 1.6,
               f"got={bg}")
        disp = M._sched_priority({"intent": "dispatch"})
        _check("sched_priority: dispatch floors urgency", disp["urgency"] == 8 and disp["intent"] == "dispatch",
               f"got={disp}")
        cx = M._sched_priority({"tasks": [1, 2, 3], "hint_tools": [1, 2, 3, 4]})
        _check("sched_priority: complexity from steps+hints", cx["complexity"] == 6,
               f"got={cx}")

async def t_sched_priority_ssot_override() -> None:
    """An injected [sched] table changes the tiers + weights as configured (proves the
    numbers are SSOT, not baked). Synthetic non-dictionary tokens are the urgency terms."""
    cfg = {
        "urgency_high": 7, "urgency_low": 3, "urgency_default": 4,
        "urgency_dispatch_floor": 6,
        "urgency_high_terms": ["zzurg"], "urgency_low_terms": ["zzdefer"],
        "complexity_base": 2, "complexity_hints_divisor": 1, "complexity_cap": 5,
        "score_complexity_weight": 0.5, "score_urgency_weight": 0.5,
        "score_round_ndigits": 3,
    }
    with _sched_cfg(cfg):
        _check("sched_override: high tier from injected term",
               M._sched_priority({"urgency": "zzurg"})["urgency"] == 7,
               f"got={M._sched_priority({'urgency': 'zzurg'})}")
        _check("sched_override: low tier from injected term",
               M._sched_priority({"urgency": "zzdefer"})["urgency"] == 3,
               f"got={M._sched_priority({'urgency': 'zzdefer'})}")
        _check("sched_override: default urgency",
               M._sched_priority({"urgency": "nomatch"})["urgency"] == 4,
               f"got={M._sched_priority({'urgency': 'nomatch'})}")
        cx = M._sched_priority({"tasks": [1, 2, 3], "hint_tools": [1, 2]})
        _check("sched_override: complexity weights+cap", cx["complexity"] == 5, f"got={cx}")
        _check("sched_override: blended score honors weights+ndigits", cx["score"] == 4.5,
               f"got={cx}")
        _check("sched_override: dispatch floor from config",
               M._sched_priority({"intent": "dispatch"})["urgency"] == 6,
               f"got={M._sched_priority({'intent': 'dispatch'})}")

async def t_sched_priority_model_hook() -> None:
    """priority_mode='model' PREFERS an already-present numeric refined.urgency /
    refined.complexity over the lexical scan; an absent signal falls back to lexical.
    The DEFAULT 'ssot' mode must NOT consume a numeric urgency (byte-identical guard)."""
    with _sched_cfg({}, mode_env="model"):
        m = M._sched_priority({"urgency": 8, "complexity": 4})
        _check("sched_model: numeric urgency preferred", m["urgency"] == 8, f"got={m}")
        _check("sched_model: numeric complexity preferred", m["complexity"] == 4, f"got={m}")
        ms = M._sched_priority({"urgency": "9"})          # numeric STRING also honored
        _check("sched_model: numeric-string urgency", ms["urgency"] == 9, f"got={ms}")
        fb = M._sched_priority({"urgency": "nonnumeric"})  # no model number -> lexical
        _check("sched_model: absent signal -> lexical fallback", fb["urgency"] == 5, f"got={fb}")
    with _sched_cfg({}):                                   # default ssot mode
        ss = M._sched_priority({"urgency": 8})
        _check("sched_model: ssot mode ignores numeric urgency (byte-identical)",
               ss["urgency"] == 5, f"got={ss}")

async def t_sched_priority_unicode() -> None:
    """Urgency matching is Unicode-casefold, not ASCII-gated: a non-ASCII SSOT term
    matches a differently-cased input where a plain .lower() would NOT (casefold folds
    'ß'->'ss'). Synthetic invented token, set via injected config -- no baked word."""
    cfg = {"urgency_high_terms": ["zzstraße"], "urgency_low_terms": []}  # 'zzstraße'
    with _sched_cfg(cfg):
        hit = M._sched_priority({"urgency": "ZZSTRASSE"})  # casefold == 'zzstrasse' == term
        _check("sched_unicode: casefold matches non-ASCII SSOT term", hit["urgency"] == 9,
               f"got={hit}")
        miss = M._sched_priority({"urgency": "zzother"})
        _check("sched_unicode: non-member -> default urgency", miss["urgency"] == 5,
               f"got={miss}")

async def t_lane_sem_key() -> None:
    """sub_lane > custom lane > delegate to _agent_lane for a base category."""
    _configure_helpers(agent_lane=lambda cfg: "delegated")
    _check("lane_sem_key: sub_lane wins", M._lane_sem_key({"sub_lane": "gpu0", "lane": "gpu"}) == "gpu0")
    _check("lane_sem_key: custom lane wins",
           M._lane_sem_key({"lane": "potato-gpu"}) == "potato-gpu")
    _check("lane_sem_key: base category delegates to _agent_lane",
           M._lane_sem_key({"lane": "gpu"}) == "delegated",
           f"got={M._lane_sem_key({'lane': 'gpu'})}")
    _check("lane_sem_key: no lane delegates to _agent_lane",
           M._lane_sem_key({}) == "delegated")

async def main() -> int:
    for t in (t_basic_bound, t_priority_reorder, t_fifo_tiebreak,
              t_anti_starvation, t_cancel_while_queued, t_cancel_after_grant,
              t_cap_never_exceeded, t_tenant_default_off, t_tenant_fair_share,
              t_tenant_degrade_open, t_tenant_none_uncapped,
              t_lane_tool_cap, t_agent_offload_engine,
              t_resolve_autonomous_priority, t_sched_priority,
              t_sched_priority_ssot_override, t_sched_priority_model_hook,
              t_sched_priority_unicode, t_lane_sem_key):
        await t()
    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    total = len(_RESULTS)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1



# ==============================================================================
# Consolidated from test_mios_admission.py (task_1006)
# ==============================================================================
# AI-hint: Unit tests for the admission seam (mios_pipe.vram_scheduler semaphores + the scheduler.admission priority gate).
"""Unit tests for admission control and lane semaphore management."""

import unittest

from mios_pipe import vram_scheduler as _vs
from mios_pipe.scheduler import admission as _adm
from mios_pipe.vram_scheduler import (
    _SloShed,
    _endpoint_key,
    _lane_sem,
    configure as configure_vram_scheduler,
)

class TestAdmission(unittest.TestCase):

    def setUp(self):
        configure_vram_scheduler(
            AGENT_CONCURRENCY=4,
            ENDPOINT_CONCURRENCY=2,
            _dispatch_num=lambda _env, _key, default, cast=int: default,
        )

    def test_priority_gate_serializes_when_enabled(self):
        """With the queue enabled, _priority_gate holds a permit of the configured gate."""
        saved = (_adm.PRIORITY_QUEUE_ENABLE, _adm._GLOBAL_PRIORITY_GATE)
        gate = PriorityGate(1, 0.0)
        peak = {"now": 0, "max": 0}

        async def _hold():
            async with _adm._priority_gate(5.0):
                peak["now"] += 1
                peak["max"] = max(peak["max"], peak["now"])
                await asyncio.sleep(0.01)
                peak["now"] -= 1

        async def _burst():
            await asyncio.gather(_hold(), _hold(), _hold())

        try:
            _adm.configure(priority_queue_enable=True, global_priority_gate=gate)
            asyncio.run(_burst())
            self.assertEqual(peak["max"], 1)
            self.assertEqual(gate.stats()["in_flight"], 0)
            _adm.configure(priority_queue_enable=False)
            peak["max"] = 0
            asyncio.run(_burst())
            self.assertEqual(peak["max"], 3)
        finally:
            _adm.PRIORITY_QUEUE_ENABLE, _adm._GLOBAL_PRIORITY_GATE = saved

    def test_sloshed_exception_class(self):
        err = _SloShed("slo_test")
        self.assertIsInstance(err, Exception)

    def test_lane_sem_cache_identity(self):
        sem1 = _lane_sem("gpu_test")
        sem2 = _lane_sem("gpu_test")
        self.assertIs(sem1, sem2)

    def test_endpoint_key_parsing(self):
        self.assertEqual(_endpoint_key("http://localhost:11434/v1"), "localhost:11434")
        self.assertEqual(_endpoint_key("https://127.0.0.1:8000/api/chat"), "127.0.0.1:8000")


def _run_extra_admission():
    import os
    _saved_env = dict(os.environ)
    try:
        import unittest
        suite = unittest.TestSuite()
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestAdmission))
        res = unittest.TextTestRunner().run(suite)
        return 0 if res.wasSuccessful() else 1
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



# ==============================================================================
# Consolidated from test_mios_vram.py (task_1006)
# ==============================================================================
# AI-hint: Unit tests for mios_pipe.scheduler.vram.
"""Unit tests for VRAM and model residency manager."""

import asyncio
import unittest

from mios_pipe.scheduler.vram import (
    _model_active,
    _model_is_active,
    _norm_model_tag,
    configure as configure_vram,
)

class TestVram(unittest.TestCase):

    def setUp(self):
        configure_vram(
            vram_budget_mb=23000,
            vram_checkpoint_enable=True,
        )

    def test_norm_model_tag(self):
        self.assertEqual(_norm_model_tag("mios-hermes"), "mios-hermes:latest")
        self.assertEqual(_norm_model_tag("qwen3.5:4b"), "qwen3.5:4b")
        self.assertEqual(_norm_model_tag("llama3:latest"), "llama3:latest")

    def test_model_active_refcount_roundtrip(self):
        ep = "http://localhost:8450/v1"
        model = "mios-hermes"

        self.assertFalse(_model_is_active(ep, model))

        asyncio.run(_model_active(ep, model, +1))
        self.assertTrue(_model_is_active(ep, model))

        asyncio.run(_model_active(ep, model, -1))
        self.assertFalse(_model_is_active(ep, model))


def _run_extra_vram():
    import os
    _saved_env = dict(os.environ)
    try:
        import unittest
        suite = unittest.TestSuite()
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestVram))
        res = unittest.TextTestRunner().run(suite)
        return 0 if res.wasSuccessful() else 1
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



# ==============================================================================
# Consolidated from test_mios_vram_scheduler.py (task_1006)
# ==============================================================================
# AI-hint: [dispatch] values injected through vram_scheduler.configure take effect.
class TestVramSchedulerDispatch(unittest.TestCase):

    _ENV = ("MIOS_NODES_RESEARCH_ONLY", "MIOS_VRAM_RECLAIM_IDLE", "MIOS_LANE_PRIORITY")

    def test_configured_dispatch_table_recomputes_derived_values(self):
        saved = (_vs._DISPATCH_TOML, _vs.NODES_RESEARCH_ONLY, _vs.VRAM_RECLAIM_IDLE,
                 _vs._LANE_PRIORITY)
        for k in self._ENV:
            os.environ.pop(k, None)
        try:
            configure_vram_scheduler(_DISPATCH_TOML={
                "nodes_research_only": "true",
                "vram_reclaim_idle": "false",
                "lane_priority": "gpu:9,cpu:1,_default:4",
            })
            self.assertIs(_vs.NODES_RESEARCH_ONLY, True)
            self.assertIs(_vs.VRAM_RECLAIM_IDLE, False)
            self.assertEqual(_vs._LANE_PRIORITY["gpu"], 9.0)
            self.assertEqual(_vs._LANE_PRIORITY["cpu"], 1.0)
            self.assertEqual(_vs._LANE_PRIORITY["_default"], 4.0)
            os.environ["MIOS_NODES_RESEARCH_ONLY"] = "false"
            configure_vram_scheduler(_DISPATCH_TOML={"nodes_research_only": "true"})
            self.assertIs(_vs.NODES_RESEARCH_ONLY, False)
        finally:
            (_vs._DISPATCH_TOML, _vs.NODES_RESEARCH_ONLY, _vs.VRAM_RECLAIM_IDLE,
             _vs._LANE_PRIORITY) = saved


def _run_extra_vram_scheduler():
    import os
    _saved_env = dict(os.environ)
    try:
        import unittest
        suite = unittest.TestSuite()
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(
            TestVramSchedulerDispatch))
        res = unittest.TextTestRunner().run(suite)
        return 0 if res.wasSuccessful() else 1
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



def _run_all_folded_sched_suites():
    rc = _run_extra_admission()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")
    rc = _run_extra_vram()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")
    rc = _run_extra_vram_scheduler()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _run_all_folded_sched_suites()
    sys.exit(asyncio.run(main()))
