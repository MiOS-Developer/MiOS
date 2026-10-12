# AI-hint: Standalone unit test for mios_kvfork to verify KV-cache fork primitives, ensuring filename sanitization, length capping, and fork validation logic ...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Standalone unit test for mios_kvfork (WS-8 KV-cache fork primitives).

Pure stdlib + the sibling module only -- no server.py import, so it runs on any
Python 3.10+ without the agent-pipe runtime deps. Mirrors the mios_sched /
mios_evict standalone-test pattern: explicit asserts, PASS/FAIL summary, exit
code != 0 on any failure.

Run:  python test_mios_kvfork.py
"""

import re
import sys

import mios_kvfork

# The primitives are consolidated in mios_kvfork per task_3585 (ADR-0030)
_IMPLEMENTATIONS = (("mios_kvfork", mios_kvfork),)

_RESULTS: list = []
_LABEL = ""

def _check(name: str, ok: bool, detail: str = "") -> None:
    name = f"{_LABEL}: {name}"
    _RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _reference_kv_filename(conv) -> str:
    """The server.py _kv_filename body, copied here so the test PINS the contract
    (a child file must be the file _kv_paging later restores)."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(conv or "default"))[:120]
    return f"mios-kv-{safe or 'default'}.bin"

def t_filename_matches_server(m) -> None:
    cases = ["abc", "chat/with slashes", "weird:chars*here", "", None,
             "x" * 300, "default", "a.b-c_d", "한국어unicode", "  spaces  "]
    bad = [c for c in cases if m.kv_filename(c) != _reference_kv_filename(c)]
    _check("filename: identical to server _kv_filename", not bad,
           f"diverged on={bad}")
    _check("filename: shape", m.kv_filename("abc") == "mios-kv-abc.bin",
           m.kv_filename("abc"))
    _check("filename: empty -> default", m.kv_filename("") == "mios-kv-default.bin",
           m.kv_filename(""))
    _check("filename: sanitised", m.kv_filename("a/b c") == "mios-kv-a_b_c.bin",
           m.kv_filename("a/b c"))
    _check("filename: capped <= 120 token", len(m.conv_token("z" * 400)) == 120,
           f"len={len(m.conv_token('z' * 400))}")

def t_validate(m) -> None:
    ok, _ = m.validate_fork("parent", "child")
    _check("validate: distinct ok", ok)
    ok, reason = m.validate_fork("", "child")
    _check("validate: empty src rejected", not ok and "source" in reason, reason)
    ok, reason = m.validate_fork("parent", "")
    _check("validate: empty dst rejected", not ok and "destination" in reason, reason)
    ok, reason = m.validate_fork(None, "child")
    _check("validate: None src rejected", not ok, reason)
    ok, reason = m.validate_fork("same", "same")
    _check("validate: self-fork rejected", not ok and "same KV file" in reason, reason)
    ok, reason = m.validate_fork("a/b", "a_b")
    _check("validate: collision after sanitise rejected", not ok, reason)
    ok, _ = m.validate_fork("   ", "child")
    _check("validate: whitespace src rejected", not ok)

def t_plan(m) -> None:
    plan = m.plan_fork("parent", "child")
    _check("plan: two steps", len(plan) == 2, f"len={len(plan)}")
    _check("plan: restore parent first",
           plan[0] == ("restore", "parent", "mios-kv-parent.bin"), str(plan[0]))
    _check("plan: save child second",
           plan[1] == ("save", "child", "mios-kv-child.bin"), str(plan[1]))
    actions = [step[0] for step in plan]
    _check("plan: order restore->save", actions == ["restore", "save"], str(actions))
    _check("plan: filenames consistent",
           plan[0][2] == m.kv_filename("parent") and plan[1][2] == m.kv_filename("child"))
    p2 = m.plan_fork("a/b", "c d")
    _check("plan: sanitised tokens",
           p2[0][1] == "a_b" and p2[1][1] == "c_d", str(p2))

def t_outcome(m) -> None:
    forked, reason = m.fork_outcome(restore_ok=True, save_ok=True)
    _check("outcome: both ok -> forked", forked and "from parent prefix" in reason, reason)
    forked, reason = m.fork_outcome(restore_ok=False, save_ok=True)
    _check("outcome: restore-fail still forked (degraded)",
           forked and "WARNING" in reason, reason)
    forked, reason = m.fork_outcome(restore_ok=True, save_ok=False)
    _check("outcome: save-fail -> not forked", not forked and "could not save" in reason, reason)
    forked, reason = m.fork_outcome(restore_ok=False, save_ok=False)
    _check("outcome: both fail -> not forked + notes restore",
           not forked and "parent restore also failed" in reason, reason)

def t_parse_bool(m) -> None:
    _check("bool: default off", m.parse_bool(None) is False)
    _check("bool: default honoured", m.parse_bool(None, default=True) is True)
    truthy = all(m.parse_bool(v) for v in ("true", "1", "YES", "On", True))
    _check("bool: truthy set", truthy)
    falsy = not any(m.parse_bool(v) for v in ("false", "0", "no", "OFF", "", False))
    _check("bool: falsy set", falsy)
    _check("bool: garbage -> default", m.parse_bool("maybe", default=False) is False)

def t_clamp(m) -> None:
    _check("clamp: within cap", m.clamp_branches(3, hard_cap=8) == 3)
    _check("clamp: over cap clamped", m.clamp_branches(50, hard_cap=8) == 8)
    _check("clamp: negative -> 0", m.clamp_branches(-5, hard_cap=8) == 0)
    _check("clamp: non-numeric -> default", m.clamp_branches("x", hard_cap=8, default=2) == 2)
    _check("clamp: None -> default", m.clamp_branches(None, hard_cap=8, default=1) == 1)
    _check("clamp: default also clamped", m.clamp_branches("x", hard_cap=3, default=99) == 3)
    _check("clamp: zero cap floors all", m.clamp_branches(5, hard_cap=0) == 0)

def main() -> int:
    global _LABEL
    for _LABEL, impl in _IMPLEMENTATIONS:
        for t in (t_filename_matches_server, t_validate, t_plan, t_outcome,
                  t_parse_bool, t_clamp):
            t(impl)
    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    total = len(_RESULTS)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1

if __name__ == "__main__":
    sys.exit(main())
