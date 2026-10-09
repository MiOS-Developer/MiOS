#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_hopbudget (WS-4 hop-budget guard + effort scaling). Pure stdlib, no server.py/...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_hopbudget (WS-4)."""

import sys

import mios_hopbudget as hb

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def t_depth():
    check("depth: below bound -> ok", hb.depth_exhausted(2, 4) is False)
    check("depth: at bound -> exhausted", hb.depth_exhausted(4, 4) is True)
    check("depth: above bound -> exhausted", hb.depth_exhausted(5, 4) is True)
    check("depth: max<=0 disables bound", hb.depth_exhausted(99, 0) is False)
    check("depth: bad input -> not exhausted", hb.depth_exhausted("x", 4) is False)

def t_via():
    check("via: append to empty", hb.append_via("", "a") == "a")
    check("via: append to chain", hb.append_via("a,b", "c") == "a,b,c")
    check("via: skip empty self", hb.append_via("a", "") == "a")
    check("loop: self in chain (case-insensitive)", hb.is_loop("A,b,C", "c") is True)
    check("loop: self not in chain", hb.is_loop("a,b", "z") is False)
    check("loop: empty chain", hb.is_loop("", "a") is False)
    check("loop: empty self -> no loop", hb.is_loop("a,b", "") is False)

def t_seed():
    check("seed: parses header", hb.seed_depth("3") == 3)
    check("seed: clamps negative to 0", hb.seed_depth("-2") == 0)
    check("seed: None -> default", hb.seed_depth(None, default=0) == 0)
    check("seed: garbage -> default", hb.seed_depth("abc", default=1) == 1)

def t_effort():
    check("effort: low -> 1", hb.effort_width("low", base=2, cap=6) == 1)
    check("effort: medium -> base", hb.effort_width("medium", base=2, cap=6) == 2)
    check("effort: high -> cap-1", hb.effort_width("high", base=2, cap=6) == 5)
    check("effort: max -> cap", hb.effort_width("max", base=2, cap=6) == 6)
    check("effort: unknown -> base", hb.effort_width("weird", base=3, cap=6) == 3)
    check("effort: float score 0.0 -> 1", hb.effort_width("0.0", cap=6) == 1)
    check("effort: float score 1.0 -> cap", hb.effort_width("1.0", cap=6) == 6)
    check("effort: float score 0.5 -> mid", hb.effort_width("0.5", cap=6) in (3, 4), f"{hb.effort_width('0.5', cap=6)}")
    check("effort: clamps to >=1", hb.effort_width("low", base=1, cap=1) == 1)

def main():
    t_depth()
    t_via()
    t_seed()
    t_effort()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0



# ==============================================================================
# Consolidated from test_mios_budget.py (task_1006)
# ==============================================================================
# AI-hint: stdlib unit test for mios_agent_call budget and depth limits.
import unittest
import asyncio
from unittest.mock import patch, MagicMock

import mios_agent_call
import mios_pipe.routing.agent_call as target_module

class AsyncContextMock:
    def __init__(self, *args, **kwargs):
        pass
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass
    def __call__(self, *args, **kwargs):
        return self

async def dummy_async(*args, **kwargs):
    pass

class TestMiosBudgetAndDepth(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        target_module._IN_FLIGHT_PROMPTS.clear()
        target_module._SESSION_TOKENS.clear()
        target_module._AUTONOMOUS_SOURCE_TOKENS.clear()
        target_module._dispatch_depth_var.set(0)
        target_module._opt_int_mb = lambda x: int(x or 0)
        target_module._lane_sem_key = lambda cfg: "test-lane"
        target_module._strip_agent_chrome = lambda text: text

        class MockSloShed(Exception):
            pass
        target_module._SloShed = MockSloShed

    @patch("mios_pipe.routing.agent_call._get_budget_ceil")
    @patch("mios_pipe.routing.agent_call._conv_key_var")
    async def test_conversation_token_budget_ceiling(self, mock_conv_key, mock_budget):
        mock_conv_key.get.return_value = "session-1"
        mock_budget.side_effect = lambda key, default: {
            "conversation_token_ceil": 2000000,
            "max_dispatch_depth": 5
        }.get(key, default)

        target_module._SESSION_TOKENS["session-1"] = 2500000

        cfg = {"vram_mb": 0}
        body = {
            "messages": [
                {"role": "system", "content": "sys1"},
                {"role": "system", "content": "sys2"},
                {"role": "user", "content": "usr1"},
                {"role": "user", "content": "usr2"},
                {"role": "user", "content": "usr3"},
                {"role": "user", "content": "usr4"},
                {"role": "user", "content": "usr5"},
                {"role": "user", "content": "usr6"},
            ]
        }

        class PassedCheck(Exception):
            pass
        target_module._agent_offload_engine = MagicMock(side_effect=PassedCheck)

        with self.assertRaises(PassedCheck):
            await target_module._call_agent_complete(
                "test-agent", cfg, body, {}, MagicMock(), priority=1.0
            )

        expected = [
            {"role": "system", "content": "sys1"},
            {"role": "system", "content": "sys2"},
            {"role": "user", "content": "usr3"},
            {"role": "user", "content": "usr4"},
            {"role": "user", "content": "usr5"},
            {"role": "user", "content": "usr6"},
        ]
        self.assertEqual(body["messages"], expected)

    @patch("mios_pipe.routing.agent_call._get_budget_ceil")
    @patch("mios_pipe.routing.agent_call._autonomous_var")
    @patch("mios_pipe.routing.agent_call._autonomous_source_var")
    async def test_autonomous_token_budget_ceiling(self, mock_source, mock_auto, mock_budget):
        mock_auto.get.return_value = True
        mock_source.get.return_value = "source-1"
        mock_budget.side_effect = lambda key, default: {
            "autonomous_token_ceil": 400000,
            "max_dispatch_depth": 5
        }.get(key, default)

        target_module._AUTONOMOUS_SOURCE_TOKENS["source-1"] = 450000

        cfg = {"vram_mb": 0}
        body = {
            "messages": [
                {"role": "system", "content": "sys1"},
                {"role": "system", "content": "sys2"},
                {"role": "user", "content": "usr1"},
                {"role": "user", "content": "usr2"},
                {"role": "user", "content": "usr3"},
                {"role": "user", "content": "usr4"},
                {"role": "user", "content": "usr5"},
                {"role": "user", "content": "usr6"},
            ]
        }

        class PassedCheck(Exception):
            pass
        target_module._agent_offload_engine = MagicMock(side_effect=PassedCheck)

        with self.assertRaises(PassedCheck):
            await target_module._call_agent_complete(
                "test-agent", cfg, body, {}, MagicMock(), priority=1.0
            )

        expected = [
            {"role": "system", "content": "sys1"},
            {"role": "system", "content": "sys2"},
            {"role": "user", "content": "usr3"},
            {"role": "user", "content": "usr4"},
            {"role": "user", "content": "usr5"},
            {"role": "user", "content": "usr6"},
        ]
        self.assertEqual(body["messages"], expected)

    @patch("mios_pipe.routing.agent_call._get_budget_ceil")
    async def test_max_dispatch_depth(self, mock_budget):
        mock_budget.side_effect = lambda key, default: {
            "max_dispatch_depth": 5
        }.get(key, default)

        target_module._dispatch_depth_var.set(5)

        cfg = {"vram_mb": 0}
        body = {"messages": [{"role": "user", "content": "hello"}]}

        with self.assertRaises(RecursionError) as ctx:
            await target_module._call_agent_complete(
                "test-agent", cfg, body, {}, MagicMock(), priority=1.0
            )
        self.assertIn("Max dispatch depth exceeded", str(ctx.exception))


def _run_extra_budget():
    import os
    _saved_env = dict(os.environ)
    try:
        import unittest
        suite = unittest.TestSuite()
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestMiosBudgetAndDepth))
        res = unittest.TextTestRunner().run(suite)
        return 0 if res.wasSuccessful() else 1
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



def _run_all_folded_hopbudget_suites():
    rc = _run_extra_budget()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _rc_main = main()
    _run_all_folded_hopbudget_suites()
    sys.exit(_rc_main)
