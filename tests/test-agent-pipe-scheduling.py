#!/usr/bin/env python3
# AI-hint: Consolidated unit test suite for MiOS Agent Pipe Scheduling domain: continuous batch preemption, quantum slicing, token-bucket quotas, and engine-level priority routing (task_3353 / GATECAT-01).
# AI-related: usr/lib/mios/agent-pipe/server.py, usr/lib/mios/agent-pipe/mios_sched.py, usr/lib/mios/agent-pipe/mios_quota.py, usr/lib/mios/agent-pipe/mios_priority_sched.py
"""Consolidated Agent Pipe Scheduling Domain Test Suite.

Consolidates:
- WS-AI continuous batch preemption and turn scheduling (test-agent-pipe-preempt.py)
- WS-SCHED agent-pipe token-bucket rate limiter and quotas (test-agent-pipe-quota.py)
- Engine-level priority scheduling and gate drain ordering (test-priority-sched.py)
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_AGENT_PIPE = os.path.join(_ROOT, "usr", "lib", "mios", "agent-pipe")
if _AGENT_PIPE not in sys.path:
    sys.path.insert(0, _AGENT_PIPE)

from mios_pipe.scheduler.preempt import (
    PreemptScheduler, Snapshot, Quantum, decide, TokenSliceQueue,
    CONTINUE, PREEMPT, COMPLETE,
)
from mios_pipe.access.quota import QuotaTracker, QuotaVerdict
from mios_priority_sched import (
    PriorityGate, PriorityRequest,
    PRIORITY_FOREGROUND, PRIORITY_BACKGROUND, PRIORITY_INTERACTIVE,
)
from test_server_import import check_ast_use_before_def


# ============================================================================
# Domain 2.1: Preemption Scheduling, Quantum Expiration & Turn Boundary Slicing
# (Migrated from tests/test-agent-pipe-preempt.py)
# ============================================================================

class TestAgentPipePreempt(unittest.TestCase):
    """Validates preemption slots, quantum expiration, priority ordering, and cascade decisions."""

    def test_preempt_scheduler_slots_and_resume(self):
        sched = PreemptScheduler(max_suspended=2)
        self.assertTrue(sched.can_admit())

        slot1 = sched.acquire_slot()
        self.assertIsNotNone(slot1)
        snap1 = Snapshot("task_low", priority=1.0, position=10, partial="abc", slot=slot1)
        self.assertTrue(sched.suspend(snap1))

        slot2 = sched.acquire_slot()
        self.assertIsNotNone(slot2)
        snap2 = Snapshot("task_high", priority=10.0, position=5, partial="xyz", slot=slot2)
        self.assertTrue(sched.suspend(snap2))

        # Full slots -> cannot admit another
        self.assertFalse(sched.can_admit())

        # Resumes highest priority first
        resumed = sched.resume()
        self.assertIsNotNone(resumed)
        self.assertEqual(resumed.task_id, "task_high")

        # Now can admit again
        self.assertTrue(sched.can_admit())

    def test_quantum_expiration(self):
        q = Quantum(t0=100.0, limit_s=5.0)
        self.assertFalse(q.expired(now=104.0))
        self.assertTrue(q.expired(now=106.0))

    def test_preemption_cascade_decisions(self):
        # When finished -> COMPLETE
        self.assertEqual(decide(finished=True, quantum_expired=False, higher_priority_waiting=False, can_suspend=False), COMPLETE)
        # When quantum expired and higher priority waiting and can suspend -> PREEMPT
        self.assertEqual(decide(finished=False, quantum_expired=True, higher_priority_waiting=True, can_suspend=True), PREEMPT)
        # Otherwise -> CONTINUE
        self.assertEqual(decide(finished=False, quantum_expired=True, higher_priority_waiting=False, can_suspend=True), CONTINUE)

    def test_token_slice_starvation_prevention(self):
        q = TokenSliceQueue(default_slice_tokens=100)
        q.enqueue("turn_1", priority=5.0, slice_tokens=50)
        q.enqueue("turn_2", priority=5.0, slice_tokens=50)
        q.dispatch()  # turn_1 running
        # Account tokens crossing budget: priority of turn_1 degrades to prevent starvation
        tripped = q.account("turn_1", 60)
        self.assertTrue(tripped)
        row = q._turns.get("turn_1")
        self.assertEqual(row[0], 4.0)  # Priority degraded from 5.0 to 4.0


# ============================================================================
# Domain 2.2: Token & Request Quota Enforcement (Sliding Windows & Cost Budgets)
# (Migrated from tests/test-agent-pipe-quota.py)
# ============================================================================

class TestAgentPipeQuota(unittest.TestCase):
    """Validates token-bucket sliding windows, budget enforcement, and tenant isolation."""

    def test_rpm_sliding_window(self):
        qt = QuotaTracker(rpm_limit=2, window_s=60.0)
        v1 = qt.check("tenant_a", now=10.0)
        self.assertTrue(v1.allowed)

        v2 = qt.check("tenant_a", now=15.0)
        self.assertTrue(v2.allowed)

        # 3rd request in same 60s window is rejected
        v3 = qt.check("tenant_a", now=20.0)
        self.assertFalse(v3.allowed)
        self.assertIn("rate limit", v3.reason)

        # Window rolls over after 60s
        v4 = qt.check("tenant_a", now=75.0)
        self.assertTrue(v4.allowed)

    def test_cost_budget(self):
        qt = QuotaTracker(daily_budget=5.0, budget_window_s=86400.0)
        v1 = qt.check("tenant_b", now=10.0, cost=3.0)
        self.assertTrue(v1.allowed)

        # Exceeds remaining budget (3.0 + 3.0 > 5.0)
        v2 = qt.check("tenant_b", now=15.0, cost=3.0)
        self.assertFalse(v2.allowed)
        self.assertIn("budget exceeded", v2.reason)


# ============================================================================
# Domain 2.3: Engine-Level Priority Scheduling, Queue Draining & Turn Classification
# (Migrated from tests/test-priority-sched.py)
# ============================================================================

class TestPrioritySched(unittest.TestCase):
    """Validates engine-level priority queue sorting, header injection, turn classification, and drain order."""

    def test_foreground_preempts_background(self):
        """Foreground user requests sort ahead of background batches."""
        gate = PriorityGate()
        bg = gate.wrap({"model": "x", "messages": []}, priority=PRIORITY_BACKGROUND)
        fg = gate.wrap({"model": "x", "messages": []}, priority=PRIORITY_FOREGROUND)

        queue = gate.sorted_queue()
        self.assertEqual(queue[0].priority, PRIORITY_FOREGROUND)
        self.assertEqual(queue[1].priority, PRIORITY_BACKGROUND)

    def test_headers_injected(self):
        """x-priority header is set to the request priority level."""
        gate = PriorityGate()
        req = gate.wrap({}, priority=PRIORITY_FOREGROUND)
        hdrs = gate.augment_headers(req, {"content-type": "application/json"})
        self.assertEqual(hdrs["x-priority"], "1")
        self.assertEqual(hdrs["x-mios-priority-hint"], "foreground")

    def test_classify_user_turn_foreground(self):
        """User streaming turn is classified as foreground priority."""
        gate = PriorityGate()
        msgs = [{"role": "user", "content": "hello"}]
        p = gate.classify_turn(msgs, is_streaming=True)
        self.assertEqual(p, PRIORITY_FOREGROUND)

    def test_drain_order(self):
        """drain() returns highest-priority items first."""
        gate = PriorityGate()
        gate.wrap({}, priority=9)
        gate.wrap({}, priority=1)
        gate.wrap({}, priority=5)
        drained = gate.drain(2)
        self.assertEqual(drained[0].priority, 1)
        self.assertEqual(drained[1].priority, 5)
        self.assertEqual(len(gate._submitted), 1)


# ============================================================================
# Domain 2.4: AST Sequential Use-Before-Def Boundary Verification (Task 2494)
# (Catches symbol relocation and import ordering errors like 4c7bcfa7)
# ============================================================================

class TestAgentPipeASTBoundary(unittest.TestCase):
    """Validates top-level symbol resolution order and catches symbol relocation defects."""

    def test_server_ast_use_before_definition(self):
        """server.py executes sequentially with 0 use-before-definition violations in <50ms."""
        import time
        server_path = os.path.join(_AGENT_PIPE, "server.py")
        t0 = time.perf_counter()
        violations = check_ast_use_before_def(file_path=server_path)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        self.assertEqual(violations, [], f"Use-before-def violations in server.py: {violations}")
        self.assertLess(elapsed_ms, 50.0, f"Execution time {elapsed_ms:.2f}ms exceeded 50ms budget")

    def test_relocation_negative_control_fails_red(self):
        """Planted call relocated above its definition fails RED with exact diagnostic."""
        planted = (
            "import os\n"
            "# Defect: call before definition\n"
            "_configure_scratchpad(_conv_key_var)\n\n"
            "def _configure_scratchpad(conv_key):\n"
            "    return conv_key\n\n"
            "_conv_key_var = 'default'\n"
        )
        violations = check_ast_use_before_def(source=planted)
        symbols = [v[0] for v in violations]
        lines = [v[1] for v in violations]
        self.assertIn("_configure_scratchpad", symbols)
        self.assertIn("_conv_key_var", symbols)
        self.assertIn(3, lines)

    def test_unbound_rhs_assignment_negative_control(self):
        """Planted assignment using undefined global fails RED with symbol diagnostic."""
        planted = "x = _unbound_variable + 42\n"
        violations = check_ast_use_before_def(source=planted)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0][0], "_unbound_variable")
        self.assertEqual(violations[0][1], 1)

    def test_core_agent_pipe_modules_clean(self):
        """Core agent-pipe modules exhibit 0 use-before-def violations."""
        for mod in ["context_compactor.py", "council.py", "macaroon_auth.py", "pss_regulator.py"]:
            mod_path = os.path.join(_AGENT_PIPE, mod)
            if os.path.isfile(mod_path):
                violations = check_ast_use_before_def(file_path=mod_path)
                self.assertEqual(violations, [], f"Violations in {mod}: {violations}")


if __name__ == "__main__":
    unittest.main()

