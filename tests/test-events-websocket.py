#!/usr/bin/env python3
# AI-hint: Automated unit test suite for authenticated WebSocket real-time agent execution token stream (task_462).
# AI-doc: usr/share/doc/mios/manual/ch04-the-agentic-ai-stack.md
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_AGENT_PIPE_DIR = os.path.join(_ROOT, "usr", "lib", "mios", "agent-pipe")
if _AGENT_PIPE_DIR not in sys.path:
    sys.path.insert(0, _AGENT_PIPE_DIR)

from mios_events import AgentEventHub


class TestEventsWebSocket(unittest.TestCase):
    """Validates token authentication, JSON-RPC event chunk formatting, session filtering, and sub-50ms latency."""

    def setUp(self):
        self.api_key = "mios-secret-token-12345"
        self.hub = AgentEventHub(api_key=self.api_key)

    def test_authentication(self):
        # Correct token
        self.assertTrue(self.hub.authenticate("mios-secret-token-12345"))
        # Bearer prefix
        self.assertTrue(self.hub.authenticate("Bearer mios-secret-token-12345"))
        # Invalid token
        self.assertFalse(self.hub.authenticate("wrong-token"))
        # Missing token
        self.assertFalse(self.hub.authenticate(None))
        self.assertFalse(self.hub.authenticate(""))

    def test_format_event(self):
        data = {"token": " reasoning_step_1", "step": 3, "status": "thinking"}
        raw = self.hub.format_event("token", data, session_id="sess-abc")
        parsed = json.loads(raw)
        self.assertEqual(parsed.get("jsonrpc"), "2.0")
        self.assertEqual(parsed.get("method"), "agent/event")
        params = parsed.get("params", {})
        self.assertEqual(params.get("type"), "token")
        self.assertEqual(params.get("session_id"), "sess-abc")
        self.assertEqual(params.get("data"), data)
        self.assertIn("timestamp", params)

    def test_broadcast_and_session_filter(self):
        async def run_test():
            client1 = "ws_conn_1"
            client2 = "ws_conn_2"
            client3 = "ws_conn_3"

            # client1 subscribes to sess-1
            q1 = self.hub.register(client1, session_id="sess-1")
            # client2 subscribes to sess-2
            q2 = self.hub.register(client2, session_id="sess-2")
            # client3 subscribes to all (None)
            q3 = self.hub.register(client3, session_id=None)

            # Broadcast for sess-1
            delivered = await self.hub.broadcast("thought", {"thought": "analyzing bug"}, session_id="sess-1")
            self.assertEqual(delivered, 2)  # client1 and client3

            self.assertFalse(q1.empty())
            self.assertTrue(q2.empty())
            self.assertFalse(q3.empty())

            msg1 = json.loads(q1.get_nowait())
            self.assertEqual(msg1["params"]["session_id"], "sess-1")

            # Clean up
            self.hub.unregister(client1)
            self.hub.unregister(client2)
            self.hub.unregister(client3)

        asyncio.run(run_test())

    def test_sub_50ms_broadcast_latency(self):
        async def run_test():
            subscribers = [f"ws_bench_{i}" for i in range(100)]
            queues = [self.hub.register(c) for c in subscribers]

            t0 = time.perf_counter()
            delivered = await self.hub.broadcast("tool_call", {"tool": "bash", "args": {"cmd": "ls"}})
            elapsed_ms = (time.perf_counter() - t0) * 1000

            self.assertEqual(delivered, 100)
            self.assertLess(elapsed_ms, 50.0, f"Latency {elapsed_ms:.2f}ms exceeds 50ms SLA")

            for c in subscribers:
                self.hub.unregister(c)

        asyncio.run(run_test())

    def test_server_route_registered(self):
        server_path = os.path.join(_AGENT_PIPE_DIR, "server.py")
        content = open(server_path, "r", encoding="utf-8").read()
        self.assertIn('@app.websocket("/v1/events/ws")', content)
        self.assertIn("_event_hub.authenticate", content)


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestEventsWebSocket)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
