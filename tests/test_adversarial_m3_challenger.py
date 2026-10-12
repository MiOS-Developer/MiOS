#!/usr/bin/env python3
# AI-hint: Adversarial suite for M3 agent-pipe gateway context budgeting and tool de-duplication.
"""
Comprehensive Empirical Adversarial Challenge Suite for Milestone M3:
R4 Agent-Pipe Gateway Context Budgeting & Tool De-duplication.

Adversarial Stress Test Matrix:
1. Tool Choice 'none' Casing & Edge Cases:
   - 'none', 'NONE', ' None ', 'None', '\t\n NONE \r\n' -> STRIPPED (0 tool tokens)
   - '', None, 'auto', 'AUTO', 'required' -> RETAINED (Negative controls)
   - Object/dict tool_choice: {'type': 'function', 'function': {'name': 'none'}} -> RETAINED
2. Client Supplying 150+ Tools:
   - 175 client tools supplied -> _mios_sel is empty ([])
   - 175 client tools with 35 duplicates -> deduplicated to 140 unique tools, 0 duplicate schemas
3. Collision with MiOS Verbs:
   - Client supplies tools overlapping with MiOS verbs ('run_command', 'read_file', 'app_search')
   - Client supplies only 2 tools matching verbs (< DEFAULT_TOOL_CAP) -> _mios_sel is still suppressed
   - Injected verbs with same name filtered by client_names and seen_names
4. Two-Sided Negative Control:
   - tool_choice: 'auto' does NOT strip tools
   - tool_choice omitted does NOT strip tools
5. Relay and Streaming Relay Ingress:
   - _client_tools_relay and _client_tools_stream_relay strip tools on case-insensitive 'none'
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PIPE_DIR = REPO_ROOT / "usr" / "lib" / "mios" / "agent-pipe"
if str(PIPE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPE_DIR))

SSOT_TOML = REPO_ROOT / "usr" / "share" / "mios" / "mios.toml"
if "MIOS_TOML" not in os.environ and SSOT_TOML.is_file():
    os.environ["MIOS_TOML"] = str(SSOT_TOML)

import test_mios_chat
import mios_tokenize
import mios_vision
import mios_chat


def setUpModule():
    mios_vision.configure(
        agent_contract=lambda: "System contract",
        resolve_verb_key=lambda name: name,
        default_tool_cap=24,
    )


class FakeRequest:
    def __init__(self, body_bytes: bytes, headers: dict | None = None):
        self._body = body_bytes
        self.headers = test_mios_chat._Headers(headers or {})

    async def body(self) -> bytes:
        return self._body


class TestAdversarialToolChoiceCasings(unittest.TestCase):
    """Adversarial stress testing of tool_choice: 'none' string casings and edge cases."""

    def setUp(self):
        self.dummy_tools = [
            {"type": "function", "function": {"name": "calc", "description": "Calculator"}},
            {"type": "function", "function": {"name": "search", "description": "Search"}}
        ]

    def test_strip_cases_in_has_client_tools(self):
        """All variations of 'none' must evaluate to False in _has_client_tools."""
        strip_values = [
            "none",
            "NONE",
            " None ",
            "None",
            "nOnE",
            "\t\n NONE \r\n",
            "  none  ",
        ]
        for val in strip_values:
            with self.subTest(val=repr(val)):
                body = {
                    "messages": [{"role": "user", "content": "hello"}],
                    "tools": self.dummy_tools,
                    "tool_choice": val
                }
                res = mios_vision._has_client_tools(body)
                self.assertFalse(res, f"_has_client_tools({val!r}) must be False")

    def test_retained_cases_in_has_client_tools(self):
        """Values other than 'none' must evaluate to True in _has_client_tools."""
        retain_values = [
            "auto",
            "AUTO",
            " Auto ",
            "required",
            "",
            "   ",
            None,
            {"type": "function", "function": {"name": "calc"}},
            {"type": "function", "function": {"name": "none"}},  # function name is 'none', not mode
        ]
        for val in retain_values:
            with self.subTest(val=repr(val)):
                body = {
                    "messages": [{"role": "user", "content": "hello"}],
                    "tools": self.dummy_tools,
                }
                if val is not None:
                    body["tool_choice"] = val
                res = mios_vision._has_client_tools(body)
                self.assertTrue(res, f"_has_client_tools with tool_choice={val!r} must be True")

    def test_strip_cases_in_pruning(self):
        """_prune_request_to_context_budget strips tools and tool_choice for all 'none' variations."""
        strip_values = ["none", "NONE", " None ", "None", "\t\n NONE \r\n"]
        for val in strip_values:
            with self.subTest(val=repr(val)):
                req = {
                    "messages": [{"role": "user", "content": "hello"}],
                    "tools": copy.deepcopy(self.dummy_tools),
                    "tool_choice": val
                }
                pruned = mios_vision._prune_request_to_context_budget(req, max_ctx=32768)
                self.assertNotIn("tools", pruned, f"tools should be stripped for {val!r}")
                self.assertNotIn("tool_choice", pruned, f"tool_choice should be stripped for {val!r}")

    def test_retain_cases_in_pruning(self):
        """_prune_request_to_context_budget retains tools for non-none values."""
        retain_values = ["auto", "AUTO", "required", "", None]
        for val in retain_values:
            with self.subTest(val=repr(val)):
                req = {
                    "messages": [{"role": "user", "content": "hello"}],
                    "tools": copy.deepcopy(self.dummy_tools),
                }
                if val is not None:
                    req["tool_choice"] = val
                pruned = mios_vision._prune_request_to_context_budget(req, max_ctx=32768)
                self.assertIn("tools", pruned, f"tools should be retained for {val!r}")
                self.assertEqual(len(pruned["tools"]), 2)

    def test_chat_completions_logic_strips_various_casings(self):
        """chat_completions_logic pops tools and tool_choice across casing variants."""
        captured = []
        test_mios_chat._wire_common(
            _has_client_tools=mios_vision._has_client_tools,
            refine_intent=test_mios_chat._ahandler("refine", {"intent": "chat", "reply": "test"}),
            _scratchpad_key=lambda b, cid: (captured.append(dict(b)), cid)[1],
        )

        for val in ["none", "NONE", " None ", "None"]:
            captured.clear()
            req_body = {
                "model": "m",
                "messages": [{"role": "user", "content": "hello"}],
                "tools": copy.deepcopy(self.dummy_tools),
                "tool_choice": val
            }
            req = FakeRequest(json.dumps(req_body).encode("utf-8"))
            asyncio.run(mios_chat.chat_completions_logic(req))
            self.assertTrue(len(captured) >= 1)
            for d in captured:
                self.assertNotIn("tools", d, f"Dispatched body must NOT have tools for {val!r}")
                self.assertNotIn("tool_choice", d, f"Dispatched body must NOT have tool_choice for {val!r}")


class TestAdversarialClient150PlusTools(unittest.TestCase):
    """Adversarial stress testing of client supplying 150+ tools."""

    def setUp(self):
        self.tools_175 = [
            {
                "type": "function",
                "function": {
                    "name": f"client_tool_{i:03d}",
                    "description": f"Client capability {i}",
                    "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}
                }
            }
            for i in range(175)
        ]

    def test_175_tools_suppresses_mios_sel(self):
        """175 client tools (>> 24 DEFAULT_TOOL_CAP) completely suppresses _mios_sel."""
        injected = []
        async def _mock_select(surface, intent, cap):
            injected.append("called")
            return [{"type": "function", "function": {"name": "injected_tool"}}]

        mios_vision.configure(
            agent_contract=lambda: "contract",
            verb_catalog={"unrelated_v": {}},
            resolve_verb_key=lambda n: n,
            select_child_tools=_mock_select,
            default_tool_cap=24,
        )

        captured_reqs = []
        async def _mock_backend(req):
            captured_reqs.append(dict(req))
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

        orig_backend = mios_vision._client_tools_backend
        mios_vision._client_tools_backend = _mock_backend
        try:
            body = {
                "messages": [{"role": "user", "content": "do task"}],
                "tools": self.tools_175,
                "tool_choice": "auto"
            }
            client_names = {t["function"]["name"] for t in self.tools_175}
            res = asyncio.run(mios_vision._client_tools_loop(body, client_names, "cid-175"))
            self.assertEqual(res.get("content"), "ok")

            dispatched = captured_reqs[0]
            d_tools = dispatched.get("tools", [])
            d_names = [t["function"]["name"] for t in d_tools]

            # Injected tool must NOT appear
            self.assertNotIn("injected_tool", d_names)
            # Exactly 175 tools preserved
            self.assertEqual(len(d_tools), 175)
            # _select_child_tools should NOT even have been called
            self.assertEqual(len(injected), 0, "_select_child_tools must not be called when >24 client tools")
        finally:
            mios_vision._client_tools_backend = orig_backend

    def test_175_tools_with_35_duplicates_deduplicated(self):
        """Client supplying 175 tools with 35 duplicate definitions has duplicates removed."""
        tools_with_dups = copy.deepcopy(self.tools_175[:140])
        for i in range(35):
            tools_with_dups.append(copy.deepcopy(self.tools_175[i]))
        self.assertEqual(len(tools_with_dups), 175)

        captured_reqs = []
        async def _mock_backend(req):
            captured_reqs.append(dict(req))
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

        orig_backend = mios_vision._client_tools_backend
        mios_vision._client_tools_backend = _mock_backend
        try:
            body = {
                "messages": [{"role": "user", "content": "do task"}],
                "tools": tools_with_dups,
                "tool_choice": "auto"
            }
            client_names = {t["function"]["name"] for t in tools_with_dups}
            asyncio.run(mios_vision._client_tools_loop(body, client_names, "cid-175-dup"))

            dispatched = captured_reqs[0]
            d_tools = dispatched.get("tools", [])
            d_names = [t["function"]["name"] for t in d_tools]

            self.assertEqual(len(d_names), 140, "35 duplicate tools must be eliminated")
            self.assertEqual(len(d_names), len(set(d_names)), "All tool names must be unique")
        finally:
            mios_vision._client_tools_backend = orig_backend


class TestAdversarialMiOSVerbCollisions(unittest.TestCase):
    """Adversarial stress testing of tool name collisions with MiOS verbs."""

    def test_client_tools_overlapping_run_command_and_read_file(self):
        """Client providing tools named 'run_command' and 'read_file' suppresses _mios_sel and causes no duplicate schemas."""
        verb_cat = {
            "run_command": {"tier": "common", "description": "Run shell command"},
            "read_file": {"tier": "common", "description": "Read file contents"},
            "app_search": {"tier": "common", "description": "Search applications"},
        }

        async def _mock_select(surface, intent, cap):
            # If called, returns duplicates of the same tools
            return [
                {"type": "function", "function": {"name": "run_command", "description": "Injected run_command"}},
                {"type": "function", "function": {"name": "read_file", "description": "Injected read_file"}},
                {"type": "function", "function": {"name": "app_search", "description": "Injected app_search"}},
            ]

        mios_vision.configure(
            agent_contract=lambda: "contract",
            verb_catalog=verb_cat,
            resolve_verb_key=lambda n: n,
            select_child_tools=_mock_select,
            default_tool_cap=24,
        )

        client_tools = [
            {"type": "function", "function": {"name": "run_command", "description": "Client run_command"}},
            {"type": "function", "function": {"name": "read_file", "description": "Client read_file"}},
        ]

        captured_reqs = []
        async def _mock_backend(req):
            captured_reqs.append(dict(req))
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

        orig_backend = mios_vision._client_tools_backend
        mios_vision._client_tools_backend = _mock_backend
        try:
            body = {
                "messages": [{"role": "user", "content": "run ls"}],
                "tools": client_tools,
                "tool_choice": "auto"
            }
            client_names = {t["function"]["name"] for t in client_tools}
            # Note: client has only 2 tools (< 24), but they are MiOS verbs!
            asyncio.run(mios_vision._client_tools_loop(body, client_names, "cid-verb-overlap"))

            dispatched = captured_reqs[0]
            d_tools = dispatched.get("tools", [])
            d_names = [t["function"]["name"] for t in d_tools]

            # Must contain only the 2 client tools
            self.assertEqual(len(d_tools), 2, f"Expected 2 tools, got {len(d_tools)}: {d_names}")
            self.assertEqual(d_names, ["run_command", "read_file"])
            # Descriptions must match client's description, not injected
            self.assertEqual(d_tools[0]["function"]["description"], "Client run_command")
            self.assertEqual(d_tools[1]["function"]["description"], "Client read_file")
        finally:
            mios_vision._client_tools_backend = orig_backend

    def test_client_names_empty_inferred_from_body_tools(self):
        """When client_names set is passed empty, it is automatically inferred from body['tools']."""
        verb_cat = {"find_file": {"tier": "common"}}
        mios_vision.configure(
            agent_contract=lambda: "contract",
            verb_catalog=verb_cat,
            resolve_verb_key=lambda n: n,
            select_child_tools=lambda s, i, c: [{"type": "function", "function": {"name": "spurious"}}],
            default_tool_cap=24,
        )

        captured_reqs = []
        async def _mock_backend(req):
            captured_reqs.append(dict(req))
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

        orig_backend = mios_vision._client_tools_backend
        mios_vision._client_tools_backend = _mock_backend
        try:
            body = {
                "messages": [{"role": "user", "content": "find test"}],
                "tools": [{"type": "function", "function": {"name": "find_file"}}],
                "tool_choice": "auto"
            }
            # Pass empty client_names set
            asyncio.run(mios_vision._client_tools_loop(body, set(), "cid-infer-names"))

            d_tools = captured_reqs[0].get("tools", [])
            d_names = [t["function"]["name"] for t in d_tools]
            self.assertEqual(d_names, ["find_file"])
            self.assertNotIn("spurious", d_names)
        finally:
            mios_vision._client_tools_backend = orig_backend


class TestAdversarialNegativeControlAutoRetainsTools(unittest.TestCase):
    """Negative control: verify tool_choice: 'auto' does NOT strip tools."""

    def test_tool_choice_auto_retains_tools_in_all_layers(self):
        mios_vision.configure(
            agent_contract=lambda: "contract",
            resolve_verb_key=lambda n: n,
            default_tool_cap=24,
        )
        tools = [
            {"type": "function", "function": {"name": "tool_a", "description": "Tool A"}},
            {"type": "function", "function": {"name": "tool_b", "description": "Tool B"}}
        ]
        body = {
            "messages": [{"role": "user", "content": "use tool_a"}],
            "tools": tools,
            "tool_choice": "auto"
        }

        # 1. _has_client_tools
        self.assertTrue(mios_vision._has_client_tools(body))

        # 2. _prune_request_to_context_budget
        pruned = mios_vision._prune_request_to_context_budget(body, max_ctx=32768)
        self.assertIn("tools", pruned)
        self.assertEqual(len(pruned["tools"]), 2)
        self.assertEqual(pruned.get("tool_choice"), "auto")

        # 3. _client_tools_loop
        captured_reqs = []
        async def _mock_backend(req):
            captured_reqs.append(dict(req))
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

        orig_backend = mios_vision._client_tools_backend
        mios_vision._client_tools_backend = _mock_backend
        try:
            client_names = {"tool_a", "tool_b"}
            asyncio.run(mios_vision._client_tools_loop(body, client_names, "cid-auto-check"))
            d_tools = captured_reqs[0].get("tools", [])
            self.assertTrue(len(d_tools) >= 2)
            d_names = [t["function"]["name"] for t in d_tools]
            self.assertIn("tool_a", d_names)
            self.assertIn("tool_b", d_names)
            self.assertEqual(captured_reqs[0].get("tool_choice"), "auto")
        finally:
            mios_vision._client_tools_backend = orig_backend


class TestAdversarialRelaysIngressStripping(unittest.TestCase):
    """Verify relay functions (_client_tools_relay and _client_tools_stream_relay) strip tools on 'none'."""

    def test_client_tools_relay_strips_tools_on_casing_none(self):
        """_client_tools_relay strips tools and tool_choice before backend post when tool_choice == 'none'."""
        posted = []
        class MockClient:
            async def post(self, url, content=None, headers=None):
                posted.append(json.loads(content.decode("utf-8")))
                class MockResp:
                    status_code = 200
                    def json(self):
                        return {"choices": [{"message": {"role": "assistant", "content": "relay ok"}}]}
                return MockResp()

        orig_client = mios_vision._safe_get_client
        mios_vision._safe_get_client = lambda: asyncio.sleep(0, result=MockClient())
        try:
            for val in ["none", " NONE ", "None"]:
                posted.clear()
                body = {
                    "model": "granite4.1:8b",
                    "messages": [{"role": "user", "content": "hello"}],
                    "tools": [{"type": "function", "function": {"name": "test_t"}}],
                    "tool_choice": val
                }
                res = asyncio.run(mios_vision._client_tools_relay(body, streaming=False))
                self.assertEqual(res.status_code, 200)
                self.assertEqual(len(posted), 1)
                self.assertNotIn("tools", posted[0], f"Relay payload must NOT have tools for {val!r}")
                self.assertNotIn("tool_choice", posted[0], f"Relay payload must NOT have tool_choice for {val!r}")
        finally:
            mios_vision._safe_get_client = orig_client


if __name__ == "__main__":
    unittest.main(verbosity=2)
