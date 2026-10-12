#!/usr/bin/env python3
# AI-hint: Standalone unit test for the task_49 (GAP-3) hard pass^k skill-promotion gate embedded in the hyphenated `mios-skills` CLI.
# AI-doc: usr/share/doc/mios/manual/mios.md
"""Unit test: mios-skills pass^k promotion gate (task_49) -- all-k reliability, degrade-open."""

import importlib.machinery
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _load(fname):
    loader = importlib.machinery.SourceFileLoader(
        "tool_" + fname.replace("-", "_"), os.path.join(_HERE, fname))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod

def _ok_run():
    return {"success": True, "steps": [{"step": 0, "verb": "x", "success": True}]}

class _Counter:
    """A stub _post_skill_run: returns a scripted sequence of run envelopes and
    records how many times it was called (to assert the replay count k)."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, name, params, session):
        self.calls += 1
        out = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if isinstance(out, Exception):
            raise out
        return out

def t_run_ok(m):
    check("run_ok: success + clean steps -> True", m._passk_run_ok(_ok_run()) is True)
    check("run_ok: success false -> False",
          m._passk_run_ok({"success": False, "steps": []}) is False)
    check("run_ok: non-dict -> False", m._passk_run_ok(None) is False)
    check("run_ok: firewall_blocked step -> False",
          m._passk_run_ok({"success": True,
                           "steps": [{"success": True, "firewall_blocked": True}]}) is False)
    check("run_ok: hitl_blocked step -> False",
          m._passk_run_ok({"success": True,
                           "steps": [{"success": True, "hitl_blocked": True}]}) is False)

def t_gate(m):
    passed, n_ok, msg = m._passk_gate(lambda: _ok_run(), 3)
    check("gate 3/3 -> pass", passed is True and n_ok == 3, msg)
    check("gate 3/3 message", "PASS" in msg and "3/3 succeeded, required 3/3" in msg, msg)

    seq = _Counter([_ok_run(), _ok_run(), {"success": False, "steps": []}])
    passed, n_ok, msg = m._passk_gate(lambda: seq(None, None, None), 3)
    check("gate 2/3 -> veto", passed is False and n_ok == 2, msg)
    check("gate 2/3 message", "FAIL" in msg and "2/3 succeeded, required 3/3" in msg, msg)

    def _boom():
        raise RuntimeError("agent-pipe unreachable")
    passed, n_ok, msg = m._passk_gate(_boom, 3)
    check("gate unreachable -> fail-closed", passed is False and n_ok == 0, msg)

def _promote_args(m, *extra):
    return m.build_parser().parse_args(["promote", "myskill", *extra])

def t_promote_gate_off(m):
    m.PASS_AND_K_GATE_ENABLED = False
    calls = {"status": 0, "run": 0}

    def _upd(name, status):
        calls["status"] += 1
        return 0
    def _run(name, params, session):
        calls["run"] += 1
        return _ok_run()
    m._update_status = _upd
    m._post_skill_run = _run
    rc = m.cmd_promote(_promote_args(m))
    check("gate off: promotes (status flipped)", rc == 0 and calls["status"] == 1)
    check("gate off: NO replay (byte-identical legacy path)", calls["run"] == 0)

def t_promote_gate_on_pass(m):
    m.PASS_AND_K_GATE_ENABLED = True
    m.PASS_AND_K_COUNT = 3
    seq = _Counter([_ok_run()])            # always succeeds
    flipped = {"n": 0}
    m._post_skill_run = seq
    m._update_status = lambda name, status: flipped.__setitem__("n", flipped["n"] + 1) or 0
    rc = m.cmd_promote(_promote_args(m))
    check("gate on + 3/3: promoted", rc == 0 and flipped["n"] == 1)
    check("gate on: replayed exactly pass_and_k_count times", seq.calls == 3)

def t_promote_gate_on_veto(m):
    m.PASS_AND_K_GATE_ENABLED = True
    m.PASS_AND_K_COUNT = 3
    seq = _Counter([_ok_run(), {"success": False, "steps": []}, _ok_run()])
    flipped = {"n": 0}
    m._post_skill_run = seq
    m._update_status = lambda name, status: flipped.__setitem__("n", flipped["n"] + 1) or 0
    rc = m.cmd_promote(_promote_args(m))
    check("gate on + 2/3: NOT promoted (nonzero rc)", rc == 1)
    check("gate on + veto: status never flipped", flipped["n"] == 0)

def t_promote_dgm_count(m):
    m.PASS_AND_K_GATE_ENABLED = True
    m.PASS_AND_K_COUNT = 3
    m.PASS_AND_K_DGM_COUNT = 5
    seq = _Counter([_ok_run()])
    m._post_skill_run = seq
    m._update_status = lambda name, status: 0
    rc = m.cmd_promote(_promote_args(m, "--dgm"))
    check("gate on + --dgm: uses pass_and_k_dgm_count replays",
          rc == 0 and seq.calls == 5, f"calls={seq.calls}")

def main():
    m = _load("mios-skills")
    t_run_ok(m)
    t_gate(m)
    t_promote_gate_off(m)
    t_promote_gate_on_pass(m)
    t_promote_gate_on_veto(m)
    t_promote_dgm_count(m)
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0

if __name__ == "__main__":
    sys.exit(main())
