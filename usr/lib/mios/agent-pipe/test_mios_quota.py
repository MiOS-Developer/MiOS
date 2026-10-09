#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_quota (WS-6 per-user quota + rate limit). Pure stdlib, no server.py/DB/pytest.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_quota (WS-6)."""

import sys

import mios_quota as q

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def t_rpm():
    t = q.QuotaTracker(rpm_limit=3, window_s=60.0)
    base = 1000.0
    oks = [t.check("u", base + i).allowed for i in range(3)]
    check("rpm: first 3 allowed", all(oks))
    v = t.check("u", base + 3)
    check("rpm: 4th denied", v.allowed is False and "rate limit" in v.reason, v.reason)
    check("rpm: re-admit after window slides", t.check("u", base + 61).allowed is True)

def t_isolation():
    t = q.QuotaTracker(rpm_limit=1, window_s=60.0)
    check("isolation: user A admit", t.check("a", 0.0).allowed is True)
    check("isolation: user A 2nd denied", t.check("a", 1.0).allowed is False)
    check("isolation: user B unaffected", t.check("b", 1.0).allowed is True)

def t_budget():
    t = q.QuotaTracker(daily_budget=1.0, budget_window_s=86400.0)
    check("budget: within budget allowed", t.check("u", 0.0, cost=0.4).allowed is True)
    check("budget: still within allowed", t.check("u", 1.0, cost=0.4).allowed is True)
    v = t.check("u", 2.0, cost=0.4)   # 0.8 + 0.4 = 1.2 > 1.0
    check("budget: over budget denied", v.allowed is False and "budget" in v.reason, v.reason)
    check("budget: spent tracked", round(t.spent("u", 3.0), 2) == 0.8)
    check("budget: window rollover resets spend", t.spent("u", 90000.0) == 0.0)

def t_unlimited():
    t = q.QuotaTracker(rpm_limit=0, daily_budget=0.0)   # both disabled
    for i in range(50):
        if not t.check("u", float(i), cost=999.0).allowed:
            check("unlimited: never denies", False, f"denied at {i}")
            return
    check("unlimited: 50 calls + huge cost all allowed", True)
    check("no-principal: empty user always allowed", t.check("", 0.0).allowed is True)

def t_reset():
    t = q.QuotaTracker(rpm_limit=1)
    t.check("u", 0.0)
    check("reset: denied before reset", t.check("u", 0.5).allowed is False)
    t.reset("u")
    check("reset: allowed after reset", t.check("u", 0.6).allowed is True)

def t_snapshot_restore():
    """task_208: a principal's budget window survives a restart; a stale one does not."""
    t = q.QuotaTracker(rpm_limit=0, daily_budget=10.0)
    t.check("alice", 100.0, cost=3.0)
    t.check("alice", 101.0, cost=2.0)
    t.check("bob", 101.0, cost=1.0)
    a, b = t.snapshot("alice"), t.snapshot("bob")
    check("snapshot: carries the principal's own spend", a["spent"] == 5.0, str(a))
    check("snapshot: principals stay separate", b["spent"] == 1.0, str(b))
    check("snapshot: an unknown principal is zero, not an error",
          t.snapshot("nobody") == {"principal": "nobody", "window_start": 0.0, "spent": 0.0})
    check("snapshot: the RPM deque is deliberately NOT carried", "rpm" not in a and "reqs" not in a)

    fresh = q.QuotaTracker(rpm_limit=0, daily_budget=10.0)
    check("restore: a live window is re-seated",
          fresh.restore("alice", a["window_start"], a["spent"], 200.0) is True
          and fresh.spent("alice", 200.0) == 5.0)
    check("restore: the budget is still ENFORCED after the restore",
          fresh.check("alice", 200.0, cost=6.0).allowed is False)
    check("restore: what still fits is still allowed",
          fresh.check("alice", 200.0, cost=4.0).allowed is True)

    stale = q.QuotaTracker(rpm_limit=0, daily_budget=10.0)
    check("restore: a rolled-over window is refused, not resurrected",
          stale.restore("alice", 1.0, 9.0, 1.0 + 86400.0 + 1) is False
          and stale.spent("alice", 1.0 + 86400.0 + 1) == 0.0)
    check("restore: a zero/absent row restores nothing",
          stale.restore("x", 0, 0, 100.0) is False)
    check("restore: a non-numeric row is refused rather than raising",
          stale.restore("x", "bad", None, 100.0) is False)
    check("restore: an empty principal is refused",
          stale.restore("", 100.0, 1.0, 101.0) is False)

def main():
    t_rpm()
    t_isolation()
    t_budget()
    t_unlimited()
    t_reset()
    t_snapshot_restore()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0

if __name__ == "__main__":
    sys.exit(main())
