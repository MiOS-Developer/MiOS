#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_batch (WS-A6 batch coalescing). Stdlib + asyncio, no DB/pytest.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_batch (WS-A6)."""

import asyncio
import sys
import time

import mios_batch as mb

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def t_key():
    check("key: strips scheme + /v1", mb.batch_key("http://localhost:8442/v1", "mios-heavy") == "localhost:8442|mios-heavy")
    check("key: bare endpoint", mb.batch_key("localhost:8450", "x") == "localhost:8450|x")
    check("key: distinct models differ", mb.batch_key("e", "a") != mb.batch_key("e", "b"))

def t_native_bypass():
    hints = ["8442", "8441", "8450"]  # SGLang / vLLM / llama-swap local lanes
    check("native: SGLang lane bypassed", mb.is_native_batch("http://localhost:8442/v1", hints) is True)
    check("native: vLLM lane bypassed", mb.is_native_batch("http://localhost:8441/v1", hints) is True)
    check("native: llama.cpp lane bypassed", mb.is_native_batch("http://localhost:8450/v1", hints) is True)
    check("non-native: remote API NOT bypassed", mb.is_native_batch("https://api.example.com/v1", hints) is False)
    check("non-native: empty hints -> nothing bypassed", mb.is_native_batch("http://localhost:8442", []) is False)

def t_window_size():
    w = mb.CoalesceWindow(interval_s=10.0, max_size=3)
    check("window: empty -> no flush", w.should_flush(0.0) is False)
    w.add(0.0); w.add(0.1)
    check("window: below size + within interval -> no flush", w.should_flush(0.2) is False)
    w.add(0.2)
    check("window: at max_size -> flush", w.should_flush(0.3) is True)
    check("window: flush returns count + resets", w.flush() == 3 and w.pending == 0)

def t_window_interval():
    w = mb.CoalesceWindow(interval_s=0.05, max_size=100)
    w.add(1000.0)
    check("window: within interval -> hold", w.should_flush(1000.02) is False)
    check("window: interval elapsed -> flush", w.should_flush(1000.10) is True)
    w0 = mb.CoalesceWindow(interval_s=0.0, max_size=100)
    w0.add(0.0)
    check("window: zero interval -> immediate flush", w0.should_flush(0.0) is True)

async def _coalescer_cases():
    C = mb.Coalescer

    c = C(enabled=False, interval_s=5.0)
    t0 = time.perf_counter()
    r = await c.hold("http://remote:9999", "m")
    check("coalescer: disabled never holds",
          r["held"] is False and (time.perf_counter() - t0) < 0.05, str(r))

    c = C(enabled=True, interval_s=5.0, native_hints=["8500"])
    t0 = time.perf_counter()
    r = await c.hold("http://localhost:8500/v1", "m")
    check("coalescer: a native lane bypasses the window",
          r["held"] is False and r["reason"] == "native"
          and (time.perf_counter() - t0) < 0.05, str(r))

    c = C(enabled=True, interval_s=0.12, max_size=8)
    t0 = time.perf_counter()
    outs = await asyncio.gather(*[c.hold("http://remote:9999", "m") for _ in range(4)])
    held = (time.perf_counter() - t0)
    check("coalescer: concurrent same-key callers form ONE group",
          {o["group_size"] for o in outs} == {4}, str(outs))
    check("coalescer: exactly one leader", sum(1 for o in outs if o["leader"]) == 1)
    check("coalescer: the group waits out the interval", held >= 0.10, f"{held:.3f}s")
    check("coalescer: the group is not held past the interval", held < 0.6, f"{held:.3f}s")
    check("coalescer: no window is left behind", c.open_groups == 0)

    c = C(enabled=True, interval_s=30.0, max_size=3)
    t0 = time.perf_counter()
    outs = await asyncio.gather(*[c.hold("http://remote:9999", "m") for _ in range(3)])
    check("coalescer: max_size flushes without waiting out the interval",
          all(o["reason"] == "full" for o in outs) and (time.perf_counter() - t0) < 1.0,
          str(outs))
    check("coalescer: a full group leaves nothing behind", c.open_groups == 0)

    c = C(enabled=True, interval_s=0.05, max_size=8)
    outs = await asyncio.gather(c.hold("http://a:1", "m"), c.hold("http://b:2", "m"))
    check("coalescer: distinct endpoints never share a window",
          [o["group_size"] for o in outs] == [1, 1], str(outs))
    outs = await asyncio.gather(c.hold("http://a:1", "m1"), c.hold("http://a:1", "m2"))
    check("coalescer: distinct models never share a window",
          [o["group_size"] for o in outs] == [1, 1], str(outs))

    c = C(enabled=True, interval_s=0.02, max_size=8)
    a = await c.hold("http://remote:9999", "m")
    b = await c.hold("http://remote:9999", "m")
    check("coalescer: a flushed group is sealed, the next caller opens a fresh one",
          a["group_size"] == 1 and b["group_size"] == 1, f"{a} {b}")
    check("coalescer: sequential holds leave nothing behind", c.open_groups == 0)

def t_coalescer():
    asyncio.run(_coalescer_cases())

def main():
    t_key()
    t_native_bypass()
    t_window_size()
    t_window_interval()
    t_coalescer()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0



# ==============================================================================
# Consolidated from test_mios_compound.py (task_1006)
# ==============================================================================
# AI-hint: Standalone unit test for the #49 read-tool-enrich domain-filter fix: a compound that spans domains must keep verbs refine EXPLICITLY hinted (and,...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Standalone unit test for the #49 enrich domain-filter contract.

server.py `_read_tool_enrich` restricts AUTO-added enrich verbs to the routed
domain, but must NOT drop (a) verbs refine explicitly hinted -- a compound can
span domains -- nor (b) the deterministic local_state core verbs when the turn is
a state query mis-routed to e.g. apps_windows. This pins that set-logic with a
reference impl (pure stdlib; mirrors the server.py keep computation), the same
pattern as test_mios_launch. Live behaviour is verified on MiOS-DEV.

Run:  python test_mios_compound.py
"""

import sys

_RESULTS_compound: list = []

def _check_compound(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS_compound.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _enrich_keep(hints, explicit, dvset, core, local_state):
    keep = set(dvset) | set(explicit)
    if local_state:
        keep |= set(core)
    return [h for h in hints if h in keep]

APPS = {"list_windows", "focus_window", "close_window", "maximize_window"}
SYS = {"system_status", "sys_env", "process_list", "container_status"}
FILES = {"fs_search", "text_view", "directory_lookup"}
CORE = {"system_status", "mios_apps", "process_list", "container_status", "list_windows"}

def t_compound_cross_domain() -> None:
    out = _enrich_keep(
        hints=["list_windows", "system_status"],
        explicit={"list_windows", "system_status"},
        dvset=APPS, core=CORE, local_state=True)
    _check_compound("compound: explicit cross-domain verb kept", "system_status" in out, str(out))
    _check_compound("compound: domain verb kept", "list_windows" in out, str(out))

def t_local_state_core() -> None:
    out = _enrich_keep(
        hints=["list_windows", "system_status", "process_list", "container_status"],
        explicit={"list_windows"},
        dvset=APPS, core=CORE, local_state=True)
    _check_compound("local_state: core system_status survives mis-route",
           "system_status" in out, str(out))
    _check_compound("local_state: core process_list survives", "process_list" in out, str(out))

def t_no_overground() -> None:
    out = _enrich_keep(
        hints=["fs_search", "system_status"],
        explicit={"fs_search"},          # system_status was auto-added, NOT asked
        dvset=FILES, core=CORE, local_state=False)
    _check_compound("no-overground: auto cross-domain verb dropped",
           "system_status" not in out, str(out))
    _check_compound("no-overground: domain verb kept", "fs_search" in out, str(out))

def t_no_domain() -> None:
    out = _enrich_keep(
        hints=["list_windows", "system_status"],
        explicit={"list_windows", "system_status"},
        dvset=set(), core=set(), local_state=False)
    _check_compound("subset: only explicit kept when dvset empty",
           set(out) == {"list_windows", "system_status"}, str(out))

def _main_compound() -> int:
    for t in (t_compound_cross_domain, t_local_state_core, t_no_overground, t_no_domain):
        t()
    passed = sum(1 for _, ok in _RESULTS_compound if ok)
    total = len(_RESULTS_compound)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


def _run_extra_compound():
    try:
        return _main_compound()
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0



def _run_all_folded_batch_suites():
    rc = _run_extra_compound()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _run_all_folded_batch_suites()
    sys.exit(main())
