# AI-hint: Offline stdlib-assert test for the F2/task_33 Rule-of-Two architectural prompt-injection gate.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Stdlib assert-script gates for mios_ruleof2 + its dispatch wiring -- run: python test_mios_ruleof2.py"""
import asyncio
import contextvars
import sys
import time

import mios_ruleof2 as R
import mios_hitl
import mios_dispatch

_RESULTS: list = []

def _check(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def t_is_state_change() -> None:
    _check("C: read -> not state-change", R.is_state_change("read") is False)
    _check("C: write -> state-change", R.is_state_change("write") is True)
    _check("C: interactive -> state-change", R.is_state_change("interactive") is True)
    _check("C: unknown tier -> state-change (fail-closed)", R.is_state_change("zzz") is True)
    _check("C: empty tier -> state-change (fail-closed)", R.is_state_change("") is True)
    _check("C: None tier -> state-change (fail-closed)", R.is_state_change(None) is True)
    _check("C: non-string tier -> state-change (degrade-safe)", R.is_state_change({"x": 1}) is True)

def t_normalize_mode() -> None:
    _check("mode: off", R.normalize_mode("off") == R.MODE_OFF)
    _check("mode: audit", R.normalize_mode("audit") == R.MODE_AUDIT)
    _check("mode: enforce", R.normalize_mode("enforce") == R.MODE_ENFORCE)
    _check("mode: case-insensitive", R.normalize_mode("ENFORCE") == R.MODE_ENFORCE)
    _check("mode: unknown -> off (degrade-open)", R.normalize_mode("bogus") == R.MODE_OFF)
    _check("mode: None -> off", R.normalize_mode(None) == R.MODE_OFF)

def t_evaluate_counts() -> None:
    v = R.evaluate(session_tainted=True, permission_tier="write", sensitive=True, mode=R.MODE_ENFORCE)
    _check("eval: all-3 -> all_three", v.all_three is True and v.count == 3, str(v.to_dict()))
    _check("eval: all-3 properties keyed", v.properties == {
        R.PROP_UNTRUSTED: True, R.PROP_SENSITIVE: True, R.PROP_STATECHANGE: True})
    _check("eval: A+B only (read) -> 2of3",
           R.evaluate(session_tainted=True, permission_tier="read", sensitive=True).count == 2)
    _check("eval: A+C only -> 2of3",
           R.evaluate(session_tainted=True, permission_tier="write", sensitive=False).count == 2)
    _check("eval: B+C only (untainted) -> 2of3",
           R.evaluate(session_tainted=False, permission_tier="write", sensitive=True).count == 2)
    _check("eval: none -> 0",
           R.evaluate(session_tainted=False, permission_tier="read", sensitive=False).count == 0)

def t_evaluate_action_matrix() -> None:
    def _act(mode):
        return R.evaluate(session_tainted=True, permission_tier="write",
                          sensitive=True, mode=mode).action
    _check("action: all-3 + off -> proceed (not consulted-equivalent)", _act("off") == R.ACT_PROCEED)
    _check("action: all-3 + audit -> audit", _act("audit") == R.ACT_AUDIT)
    _check("action: all-3 + enforce -> gate", _act("enforce") == R.ACT_GATE)
    _check("action: all-3 + unknown mode -> proceed (off-like)", _act("weird") == R.ACT_PROCEED)
    for m in ("off", "audit", "enforce"):
        v = R.evaluate(session_tainted=True, permission_tier="read", sensitive=True, mode=m)
        _check(f"action: 2of3 + {m} -> proceed", v.action == R.ACT_PROCEED, str(v.to_dict()))

def t_evaluate_total() -> None:
    try:
        v = R.evaluate(session_tainted=True, permission_tier=12345, sensitive=True, mode="enforce")
        _check("eval: malformed tier -> no raise + state-change", v.all_three is True, str(v.to_dict()))
    except Exception as e:  # noqa: BLE001
        _check("eval: malformed tier -> no raise", False, repr(e))

def t_decide_ro2() -> None:
    _check("decide: ro2_block -> BLOCK", mios_hitl.decide(ro2_block=True) == mios_hitl.BLOCK)
    _check("decide: ro2_block + approved -> OBSERVE",
           mios_hitl.decide(ro2_block=True, approved=True) == mios_hitl.OBSERVE)
    _check("decide: no ro2_block -> PROCEED (inert default)", mios_hitl.decide() == mios_hitl.PROCEED)
    _check("decide: ro2_block + ai audit -> BLOCK still",
           mios_hitl.decide(ro2_block=True, in_tier_scope=True, ai_mode="audit") == mios_hitl.BLOCK)

_CAT = {
    "zq_senwrite": {"permission": "write", "sensitive": True},   # B + C
    "zq_senread":  {"permission": "read",  "sensitive": True},   # B only
    "zq_plainwr":  {"permission": "write"},                       # C only
}
_conv = contextvars.ContextVar("conv", default="conv-ro2")
_appr = contextvars.ContextVar("appr", default=None)
_agent = contextvars.ContextVar("agent", default="")
_evspy = {"n": 0}
_ORIG_EVALUATE = R.evaluate   # captured BEFORE any spy patch (so the spy can delegate)

def _spy_evaluate(*a, **k):
    _evspy["n"] += 1
    return _ORIG_EVALUATE(*a, **k)

def _configure(mode, *, tainted):
    """Wire mios_dispatch for the ro2 gate in isolation: synthetic catalog, the chosen
    mode, the taint signal stubbed, and every OTHER gate stubbed to pass-through."""
    _created: list = []
    mios_dispatch.configure(
        verb_catalog=_CAT,
        high_privilege_verbs=frozenset(),     # keep the firewall block out of the way
        launch_verbs=frozenset(),
        rule_of_two_mode=mode,
        launcher_sock="/nonexistent/launcher.sock",
        dispatch_inflight={},
        conv_key_var=_conv,
        proposal_var=contextvars.ContextVar("p", default=None),
        recency_ctx_var=contextvars.ContextVar("r", default=None),
        dispatch_agent_var=_agent,
        hitl_approved_var=_appr,
        resolve_verb_key=lambda n: n,
        current_date_str=lambda: time.strftime("%Y-%m-%d"),
        emit_dispatch_dedup_event=lambda *a, **k: None,
        db_create=lambda table, row=None, **k: _created.append((table, row)) or (table, row),
        db_post=lambda sql: sql,
        db_fire=lambda x: None,
    )
    mios_dispatch._dispatch_pdp_reason = lambda tool: None
    mios_dispatch._dispatch_quota_reason = lambda tool: None
    mios_dispatch._validate_enum_args = lambda t, a: None
    mios_dispatch._hitl_block_reason = lambda tool, args: None
    mios_dispatch._HITL_ARBITER_URL = ""

    async def _gate_none(*a, **k):
        return None
    mios_dispatch._hitl_gate = _gate_none

    async def _taint(session_id):
        return (bool(tainted), "zq_src->external" if tainted else "")
    mios_dispatch._session_is_tainted = _taint
    R.evaluate = _spy_evaluate
    return _created

def _run(tool, args, session_id="s-ro2"):
    return asyncio.run(mios_dispatch._dispatch_mios_verb_inner(tool, args, session_id=session_id))

def t_enforce_blocks_all3() -> None:
    _evspy["n"] = 0
    _appr.set(None)
    _configure("enforce", tainted=True)
    res = _run("zq_senwrite", {"k": "v"})
    _check("enforce: all-3 -> rule_of_two_blocked", res.get("rule_of_two_blocked") is True, str(res))
    _check("enforce: all-3 -> not success", res.get("success") is False)
    _check("enforce: all-3 -> hitl_pending shape", res.get("hitl_pending") is True, str(res))
    _check("enforce: evaluator WAS consulted", _evspy["n"] >= 1)

def t_enforce_proceeds_2of3() -> None:
    _configure("enforce", tainted=True)
    res = _run("zq_senread", {"k": "v"})
    _check("enforce: 2of3 (sensitive read) -> NOT blocked", not res.get("rule_of_two_blocked"), str(res))
    res2 = _run("zq_plainwr", {"k": "v"})
    _check("enforce: 2of3 (plain write) -> NOT blocked", not res2.get("rule_of_two_blocked"), str(res2))
    _configure("enforce", tainted=False)
    res3 = _run("zq_senwrite", {"k": "v"})
    _check("enforce: all-3 verb but untainted -> NOT blocked", not res3.get("rule_of_two_blocked"), str(res3))

def t_audit_non_blocking() -> None:
    _evspy["n"] = 0
    created = _configure("audit", tainted=True)
    res = _run("zq_senwrite", {"k": "v"})
    _check("audit: all-3 -> NOT blocked (non-blocking)", not res.get("rule_of_two_blocked"), str(res))
    _check("audit: evaluator consulted", _evspy["n"] >= 1)
    kinds = [(r or {}).get("kind") for (t, r) in created if t == "event"]
    _check("audit: emitted a rule_of_two_audit event", "rule_of_two_audit" in kinds, str(kinds))
    _check("audit: did NOT emit a block event", "rule_of_two_block" not in kinds, str(kinds))

def t_off_byte_identical() -> None:
    _evspy["n"] = 0
    _configure("off", tainted=True)
    res = _run("zq_senwrite", {"k": "v"})
    _check("off: all-3 verb -> NOT blocked (gate inert)", not res.get("rule_of_two_blocked"), str(res))
    _check("off: evaluator NEVER consulted (byte-identical)", _evspy["n"] == 0,
           f"evaluate called {_evspy['n']}x in off mode")

def t_enforce_approval_downgrade() -> None:
    _configure("enforce", tainted=True)
    _ah = mios_dispatch._pending_hash("zq_senwrite", {"k": "v"})
    _appr.set(_ah)
    try:
        res = _run("zq_senwrite", {"k": "v"})
        _check("enforce: approved all-3 -> downgraded (NOT blocked)",
               not res.get("rule_of_two_blocked"), str(res))
    finally:
        _appr.set(None)

def t_degrade_open() -> None:
    _configure("enforce", tainted=True)

    async def _boom(session_id):
        raise RuntimeError("taint store down")
    mios_dispatch._session_is_tainted = _boom
    res = _run("zq_senwrite", {"k": "v"})
    _check("degrade-open: taint error -> NOT rule_of_two_blocked", not res.get("rule_of_two_blocked"), str(res))
    _check("degrade-open: dispatch still returns a dict", isinstance(res, dict))

def main() -> int:
    for t in (t_is_state_change, t_normalize_mode, t_evaluate_counts,
              t_evaluate_action_matrix, t_evaluate_total, t_decide_ro2,
              t_enforce_blocks_all3, t_enforce_proceeds_2of3, t_audit_non_blocking,
              t_off_byte_identical, t_enforce_approval_downgrade, t_degrade_open):
        t()
    passed = sum(1 for _, ok in _RESULTS if ok)
    total = len(_RESULTS)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1

if __name__ == "__main__":
    sys.exit(main())
