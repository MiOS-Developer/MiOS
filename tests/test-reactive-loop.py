#!/usr/bin/env python3
# AI-hint: Automated unit test suite for Reactive Event Loop & Subagent Wakeups (task_597, task_598).
# AI-related: usr/lib/mios/agent-pipe/reactive_loop.py, tests/test-reactive-loop.py
"""Automated unit test suite for MiOS Reactive Event Dispatcher."""

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "lib", "mios", "agent-pipe"))

from reactive_loop import MAX_WAKEUP_LATENCY_MS, ReactiveEventDispatcher

class TestReactiveLoop(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dispatcher = ReactiveEventDispatcher(dry_run=True)

    async def test_sub_5ms_agent_wakeup_latency(self):
        """Test event emission wakes sleeping coroutine in <5ms."""
        q = self.dispatcher.subscribe("agent_inbox_test")
        emit_task = asyncio.create_task(
            self.dispatcher.emit_notify("agent_inbox_test", {"task_id": "task_100", "action": "wake"})
        )
        ev = await self.dispatcher.wait_for_wakeup(q, timeout=1.0)
        await emit_task

        self.assertIsNotNone(ev)
        self.assertEqual(ev.payload["task_id"], "task_100")
        self.assertLess(ev.latency_ms, MAX_WAKEUP_LATENCY_MS)

    async def test_multi_subscriber_broadcast(self):
        """Test broadcast event wakes 10 concurrent subscribers without loss."""
        queues = [self.dispatcher.subscribe("broadcast_chan") for _ in range(10)]
        count = await self.dispatcher.emit_notify("broadcast_chan", {"event": "sync"})
        self.assertEqual(count, 10)

        for q in queues:
            ev = await self.dispatcher.wait_for_wakeup(q, timeout=1.0)
            self.assertIsNotNone(ev)
            self.assertEqual(ev.payload["event"], "sync")

if __name__ == "__main__":
    unittest.main()
