#!/usr/bin/env python3
# AI-hint: Automated unit test suite for task_329 Bounded Reflection Loop Convergence.
# AI-related: usr/lib/mios/agent-pipe/mios_deliberate.py, usr/lib/mios/agent-pipe/server.py
"""
Automated unit tests for semantic delta calculation, diminishing-returns exit criteria,
max iteration ceiling, and deliberation loop convergence.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_DELIB_PATH = os.path.join(_ROOT, "usr", "lib", "mios", "agent-pipe", "mios_deliberate.py")

spec = importlib.util.spec_from_file_location("mios_deliberate", _DELIB_PATH)
if spec and spec.loader:
    mios_deliberate = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mios_deliberate
    spec.loader.exec_module(mios_deliberate)
else:
    raise ImportError(f"Could not load mios_deliberate module from {_DELIB_PATH}")

class TestBoundedReflection(unittest.TestCase):
    """Validates semantic delta scoring, diminishing returns exit, and iteration limits."""

    def setUp(self):
        self.calculator = mios_deliberate.SemanticDeltaCalculator()
        self.config = mios_deliberate.DeliberationConfig(
            max_iterations=3,
            convergence_threshold=0.05,
        )
        self.engine = mios_deliberate.BoundedDeliberationEngine(
            config=self.config,
            calculator=self.calculator,
        )

    def test_semantic_delta_calculation(self):
        # Identical text -> 0.0 delta
        text_a = "The pgvector service persists data to /var/lib/mios/pgvector on port 5432."
        self.assertEqual(self.calculator.calculate_delta(text_a, text_a), 0.0)

        # Minor punctuation or casing -> < 0.05 delta
        text_b = "The pgvector service persists data to /var/lib/mios/pgvector on port 5432!"
        delta_minor = self.calculator.calculate_delta(text_a, text_b)
        self.assertLess(delta_minor, 0.05)

        # Significant structural change -> > 0.20 delta
        text_c = "Tokio asynchronous TCP frame reader routes 16-byte binary wire frames."
        delta_major = self.calculator.calculate_delta(text_a, text_c)
        self.assertGreaterEqual(delta_major, 0.70)

    def test_exit_on_diminishing_returns(self):
        state = mios_deliberate.DeliberationState(
            initial_prompt="Configure systemd service",
            current_draft="[Service]\nExecStart=/usr/bin/daemon\nRestart=always",
        )

        # Turn 1: Substantial change (delta > 0.05)
        rev1 = "[Service]\nExecStart=/usr/bin/daemon\nRestart=on-failure\nRestartSec=10s"
        converged1 = self.engine.step(
            state,
            critique="Add restart delay",
            revision=rev1,
        )
        self.assertFalse(converged1)
        self.assertEqual(len(state.turns), 1)
        self.assertGreaterEqual(state.turns[0].semantic_delta, 0.05)

        # Turn 2: Trivial change (delta < 0.05)
        rev2 = "[Service]\nExecStart=/usr/bin/daemon\nRestart=on-failure\nRestartSec=10s\n"
        converged2 = self.engine.step(
            state,
            critique="Looks mostly good",
            revision=rev2,
        )
        self.assertTrue(converged2)
        self.assertEqual(state.exit_reason, "converged_diminishing_returns")
        self.assertTrue(state.is_converged)
        self.assertEqual(len(state.turns), 2)

    def test_exit_on_max_iteration_ceiling(self):
        state = mios_deliberate.DeliberationState(
            initial_prompt="Write a distributed algorithm",
            current_draft="Draft 1: Basic gossip protocol.",
        )

        # Turn 1: Major change
        self.assertFalse(self.engine.step(state, "Add CRDT clocks", "Draft 2: CRDT state synchronization vector clocks."))
        # Turn 2: Major change
        self.assertFalse(self.engine.step(state, "Add Ed25519 auth", "Draft 3: CRDT sync with Ed25519 signature validation."))
        # Turn 3: Major change (reaches max_iterations = 3)
        converged = self.engine.step(state, "Add wire AEAD", "Draft 4: CRDT sync with Ed25519 auth and ChaCha20 AEAD wire encryption.")

        self.assertTrue(converged)
        self.assertEqual(state.exit_reason, "max_iterations")
        self.assertEqual(len(state.turns), 3)

    def test_exit_on_critique_approval(self):
        state = mios_deliberate.DeliberationState(
            initial_prompt="Verify Architectural Law 1",
            current_draft="/usr is immutable; use /etc overrides.",
        )
        converged = self.engine.step(
            state,
            critique="APPROVED: satisfies all requirements perfectly.",
            revision="/usr is immutable; use /etc overrides.",
        )
        self.assertTrue(converged)
        self.assertEqual(state.exit_reason, "converged_critique_passed")

    def test_run_bounded_deliberation_convenience(self):
        def mock_critic(prompt: str, draft: str) -> str:
            if "port" not in draft:
                return "Please specify port 5432"
            return "No further changes needed."

        def mock_reviser(prompt: str, draft: str, critique: str) -> str:
            if "port 5432" in critique:
                return draft + " on port 5432"
            return draft

        result = mios_deliberate.run_bounded_deliberation(
            initial_prompt="Configure database connection",
            initial_draft="Connect to pgvector",
            critique_fn=mock_critic,
            revision_fn=mock_reviser,
            max_iterations=3,
        )

        self.assertTrue(result.is_converged)
        self.assertIn(result.exit_reason, ("converged_critique_passed", "converged_diminishing_returns"))
        self.assertIn("port 5432", result.final_output)

def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestBoundedReflection)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1

if __name__ == "__main__":
    sys.exit(main())
