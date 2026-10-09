#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_slo (WS-SCHED-SLO deadline/SLO scheduling core). Pure stdlib, no server.py/pytest.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_slo (WS-SCHED-SLO)."""
import sys

import mios_slo as slo

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def t_classify():
    check("classify: foreground high-prio -> interactive",
          slo.classify(foreground=True, autonomous=False, priority=9.0) == slo.INTERACTIVE)
    check("classify: autonomous -> best_effort",
          slo.classify(foreground=True, autonomous=True, priority=9.0) == slo.BEST_EFFORT)
    check("classify: background (not foreground) -> best_effort",
          slo.classify(foreground=False) == slo.BEST_EFFORT)
    check("classify: clamped foreground priority -> best_effort",
          slo.classify(foreground=True, priority=3.0, interactive_priority=7.0) == slo.BEST_EFFORT)
    check("classify: default -> interactive (protect human)", slo.classify() == slo.INTERACTIVE)

def t_deadline_edf():
    check("deadline: interactive budget 8s", slo.deadline(slo.INTERACTIVE, 100.0) == 108.0)
    check("deadline: best_effort budget 120s", slo.deadline(slo.BEST_EFFORT, 100.0) == 220.0)
    check("deadline: unknown class -> best_effort budget",
          slo.deadline("weird", 100.0) == 220.0)
    k_be = slo.edf_key(slo.BEST_EFFORT, enqueue_t=0.0, now=0.0)     # deadline 120
    k_int = slo.edf_key(slo.INTERACTIVE, enqueue_t=10.0, now=10.0)  # deadline 18
    check("edf: interactive (later arrival) sorts before best_effort", k_int < k_be,
          f"{k_int} vs {k_be}")
    a = slo.edf_key(slo.INTERACTIVE, 0.0, 0.0, budgets={slo.INTERACTIVE: 5.0, slo.BEST_EFFORT: 5.0})
    b = slo.edf_key(slo.BEST_EFFORT, 0.0, 0.0, budgets={slo.INTERACTIVE: 5.0, slo.BEST_EFFORT: 5.0})
    check("edf: deadline tie -> interactive first", a < b, f"{a} vs {b}")

def t_shed():
    check("shed: interactive never shed (over+unhealthy)",
          slo.should_shed(slo.INTERACTIVE, over_ceiling=True, healthy=False) is False)
    check("shed: best_effort shed over-ceiling",
          slo.should_shed(slo.BEST_EFFORT, over_ceiling=True, healthy=True) is True)
    check("shed: best_effort kept with headroom",
          slo.should_shed(slo.BEST_EFFORT, over_ceiling=False, healthy=True) is False)
    check("shed: best_effort FAIL-CLOSED on unknown health",
          slo.should_shed(slo.BEST_EFFORT, over_ceiling=False, healthy=False) is True)

def t_admit_foreground_protection():
    LIVE_PRIO = 5.0  # a typical live foreground scheduling priority (< floor 7.0)
    buggy = slo.classify(priority=LIVE_PRIO)        # old _admit call site
    check("A5: (regression witness) priority-only classify -> best_effort",
          buggy == slo.BEST_EFFORT)
    check("A5: (regression witness) that turn WOULD be shed under contention",
          slo.should_shed(buggy, over_ceiling=True) is True)
    fg = slo.classify(foreground=True)              # new _admit call for a fg turn
    check("A5: foreground turn -> interactive", fg == slo.INTERACTIVE)
    check("A5: foreground turn NOT shed-eligible (over-ceiling + unhealthy)",
          slo.should_shed(fg, over_ceiling=True, healthy=False) is False)
    bg = slo.classify(foreground=False)             # new _admit call for fan-out
    check("A5: background fan-out -> best_effort", bg == slo.BEST_EFFORT)
    check("A5: background fan-out shed under contention",
          slo.should_shed(bg, over_ceiling=True) is True)
    check("A5: background NOT shed with headroom (healthy degrades open)",
          slo.should_shed(bg, over_ceiling=False) is False)

def t_ssot_configure():
    check("ssot: default floor 7.0 clamps priority 5.0 -> best_effort",
          slo.classify(priority=5.0) == slo.BEST_EFFORT)
    check("ssot: default interactive budget 8s", slo.deadline(slo.INTERACTIVE, 0.0) == 8.0)

    slo.configure(
        budgets={slo.INTERACTIVE: 3.0, slo.BEST_EFFORT: 60.0},
        default_priority=5.0,
        interactive_priority=4.0,
    )
    check("ssot: injected floor 4.0 now admits priority 5.0 -> interactive",
          slo.classify(priority=5.0) == slo.INTERACTIVE)
    check("ssot: bare classify() follows injected default_priority 5.0 vs floor 4.0",
          slo.classify() == slo.INTERACTIVE)
    check("ssot: injected interactive budget 3s drives deadline",
          slo.deadline(slo.INTERACTIVE, 0.0) == 3.0)
    check("ssot: injected best_effort budget 60s drives deadline",
          slo.deadline(slo.BEST_EFFORT, 0.0) == 60.0)
    check("ssot: unknown class falls back to injected best_effort budget",
          slo.deadline("weird", 0.0) == 60.0)

    slo.configure(
        budgets={slo.INTERACTIVE: 8.0, slo.BEST_EFFORT: 120.0},
        default_priority=7.0,
        interactive_priority=7.0,
    )
    check("ssot: defaults restored (floor 7.0 clamps priority 5.0)",
          slo.classify(priority=5.0) == slo.BEST_EFFORT)

def main():
    t_classify()
    t_deadline_edf()
    t_shed()
    t_admit_foreground_protection()
    t_ssot_configure()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0



# ==============================================================================
# Consolidated from test_mios_quality_gate.py (task_1006)
# ==============================================================================
# AI-hint: Unit test suite for quality_gate.py and smartroute escalation integration.
# AI-related: mios_pipe/routing/quality_gate.py, mios_pipe/routing/smartroute.py
"""Unit tests for mios_pipe.routing.quality_gate."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from mios_pipe.routing.quality_gate import evaluate_quality
from mios_pipe.routing.smartroute import should_escalate

class TestQualityGate(unittest.TestCase):
    """Test deterministic quality evaluation and escalation producer behavior."""

    def test_good_output_passes(self):
        good = "The capital of France is Paris. It is located on the Seine River."
        ok, reason = evaluate_quality(good)
        self.assertTrue(ok)
        self.assertEqual(reason, "quality_ok")
        self.assertFalse(should_escalate(ok, local_exhausted=False))

    def test_empty_output_fails_and_escalates(self):
        ok, reason = evaluate_quality("   \n")
        self.assertFalse(ok)
        self.assertEqual(reason, "empty_output")
        self.assertTrue(should_escalate(ok, local_exhausted=False))

    def test_below_min_length_fails(self):
        ok, reason = evaluate_quality("Hi", config={"min_length": 5})
        self.assertFalse(ok)
        self.assertEqual(reason, "below_min_length")
        self.assertTrue(should_escalate(ok, local_exhausted=False))

    def test_refusal_or_punt_fails(self):
        punt = "I do not have access to that information in the provided context."
        ok, reason = evaluate_quality(punt)
        self.assertFalse(ok)
        self.assertEqual(reason, "refusal_or_punt")
        self.assertTrue(should_escalate(ok, local_exhausted=False))

    def test_malformed_json_fails(self):
        bad_json = '{"key": "value", "unclosed": '
        ok, reason = evaluate_quality(bad_json)
        self.assertFalse(ok)
        self.assertEqual(reason, "malformed_json")
        self.assertTrue(should_escalate(ok, local_exhausted=False))

    def test_valid_json_passes(self):
        good_json = '{"status": "ok", "count": 42}'
        ok, reason = evaluate_quality(good_json)
        self.assertTrue(ok)
        self.assertEqual(reason, "quality_ok")
        self.assertFalse(should_escalate(ok, local_exhausted=False))


def _run_extra_quality_gate():
    import os
    _saved_env = dict(os.environ)
    try:
        import unittest
        suite = unittest.TestSuite()
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestQualityGate))
        res = unittest.TextTestRunner().run(suite)
        return 0 if res.wasSuccessful() else 1
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



def _run_all_folded_slo_suites():
    rc = _run_extra_quality_gate()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _rc_main = main()
    _run_all_folded_slo_suites()
    sys.exit(_rc_main)
