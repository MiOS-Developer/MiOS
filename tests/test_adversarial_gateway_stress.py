#!/usr/bin/env python3
# AI-hint: Adversarial suite for agent-pipe gateway context budgeting and tool de-duplication under stress.
"""
Adversarial Empirical Challenge Suite for MiOS Gateway Context Budgeting & Tool Deduplication.
Empirically stress tests:
- 50,000 token massive prompts (single message, 100 turns, giant system, unicode)
- 193 tools request handling, tool cap suppression, and schema resilience
- tool_choice: "none" stripping with 169 tools (0 tool tokens in backend dispatch)
- Client tools matching MiOS verbs or exceeding DEFAULT_TOOL_CAP suppressing _mios_sel
- Empty and malformed request bodies (HTTP 400 verification and crash resilience)
- Context pruning reducing >32k payloads safely below 32,768 without HTTP 400
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import unittest
from pathlib import Path
from typing import Any, List, Optional

# Set up paths to load agent-pipe modules
REPO_ROOT = Path(__file__).resolve().parent.parent
PIPE_DIR = REPO_ROOT / "usr" / "lib" / "mios" / "agent-pipe"
if str(PIPE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPE_DIR))

# Ensure MIOS_TOML points to vendor SSOT
SSOT_TOML = REPO_ROOT / "usr" / "share" / "mios" / "mios.toml"
if "MIOS_TOML" not in os.environ and SSOT_TOML.is_file():
    os.environ["MIOS_TOML"] = str(SSOT_TOML)

# Import test_mios_chat which installs all required environment stubs
import test_mios_chat
import mios_compact
import mios_tokenize
import mios_vision
import mios_chat


class FakeRequest:
    """Mock FastAPI Request object."""
    def __init__(self, body_bytes: bytes, headers: Optional[dict] = None):
        self._body = body_bytes
        self.headers = test_mios_chat._Headers(headers or {})

    async def body(self) -> bytes:
        return self._body


def mock_llama_server_evaluate(payload: dict, max_ctx: int = 32768) -> tuple[int, str]:
    """Strict mock of backend llama-server context limit enforcement."""
    prompt_tokens = mios_tokenize.count_messages(payload.get("messages"), tools=payload.get("tools"))
    if prompt_tokens > max_ctx:
        return 400, f"request ({prompt_tokens} tokens) exceeds the available context size ({max_ctx} tokens)"
    return 200, "OK"


def setUpModule():
    mios_vision.configure(
        agent_contract=lambda: "",
        resolve_verb_key=lambda name: name,
        default_tool_cap=24,
    )


class TestAdversarialMassivePromptPruning(unittest.TestCase):
    """Stress testing massive prompts (>50,000 tokens) and context budgeting."""

    def test_single_message_50k_tokens_pruned_below_32k(self):
        """Massive 50,000 token single user prompt is pruned safely below 32,768 without HTTP 400."""
        massive_text = "Adversarial stress test user input with high entropy: " + ("abcdefghij " * 18000)
        initial_tokens = mios_tokenize.count_text(massive_text)
        self.assertGreater(initial_tokens, 45000, f"Initial tokens {initial_tokens} should be > 45000")

        req = {
            "messages": [
                {"role": "user", "content": massive_text}
            ],
            "max_tokens": 4096
        }

        # Negative control: unpruned payload fails backend context ceiling
        neg_status, neg_err = mock_llama_server_evaluate(req, max_ctx=32768)
        self.assertEqual(neg_status, 400)
        self.assertIn("exceeds the available context size", neg_err)

        # Apply gateway pruning
        pruned = mios_vision._prune_request_to_context_budget(req, max_ctx=32768)
        pruned_tokens = mios_tokenize.count_messages(pruned["messages"], tools=pruned.get("tools"))

        # Assert pruned token count is strictly <= 31,744 (32,768 - 1,024 target_budget)
        self.assertLessEqual(pruned_tokens, 32768 - 1024,
                             f"Pruned tokens {pruned_tokens} must be <= {32768 - 1024}")

        # Assert backend receives HTTP 200
        pos_status, pos_msg = mock_llama_server_evaluate(pruned, max_ctx=32768)
        self.assertEqual(pos_status, 200, f"Backend returned {pos_status}: {pos_msg}")

        # Assert total prompt + generation budget strictly fits within 32,768
        self.assertLessEqual(pruned_tokens + int(pruned.get("max_tokens", 0)), 32768)

        # Assert truncation marker is present
        self.assertIn("[...truncated to fit context budget...]", pruned["messages"][0]["content"])

    def test_100_turns_conversation_50k_tokens_compaction(self):
        """100-turn conversation totaling 50,000 tokens preserves system prompt and recent turn."""
        sys_msg = {"role": "system", "content": "You are the canonical MiOS agent."}
        msgs = [sys_msg]
        for i in range(50):
            msgs.append({"role": "user", "content": f"Turn {i} question: " + ("data " * 200)})
            msgs.append({"role": "assistant", "content": f"Turn {i} answer: " + ("response " * 200)})
        # Add massive middle turn
        msgs.append({"role": "user", "content": "Giant artifact dump: " + ("x" * 160000)})
        msgs.append({"role": "assistant", "content": "Acknowledged artifact dump."})
        # Final urgent turn
        recent_turn = {"role": "user", "content": "Urgent final instructions: solve problem now."}
        msgs.append(recent_turn)

        req = {"messages": msgs, "max_tokens": 2048}
        raw_tokens = mios_tokenize.count_messages(req["messages"])
        self.assertGreater(raw_tokens, 45000)

        pruned = mios_vision._prune_request_to_context_budget(req, max_ctx=32768)
        pruned_tokens = mios_tokenize.count_messages(pruned["messages"])

        self.assertLessEqual(pruned_tokens, 32768 - 1024)
        pos_status, _ = mock_llama_server_evaluate(pruned, max_ctx=32768)
        self.assertEqual(pos_status, 200)

        # Verify system prompt preserved
        self.assertTrue(any(m.get("role") == "system" and "canonical MiOS agent" in m.get("content", "")
                            for m in pruned["messages"]))
        # Verify recent user turn preserved
        self.assertTrue(any(m.get("role") == "user" and "Urgent final instructions" in m.get("content", "")
                            for m in pruned["messages"]))

    def test_giant_system_prompt_50k_tokens_alone(self):
        """Giant system prompt (50k tokens) without user turns is pruned safely."""
        sys_msg = {"role": "system", "content": "System directive: " + ("rules and constraints " * 10000)}
        req = {"messages": [sys_msg], "max_tokens": 1024}
        raw_tokens = mios_tokenize.count_messages(req["messages"])
        self.assertGreater(raw_tokens, 45000)

        pruned = mios_vision._prune_request_to_context_budget(req, max_ctx=32768)
        pruned_tokens = mios_tokenize.count_messages(pruned["messages"])

        self.assertLessEqual(pruned_tokens, 32768 - 1024)
        pos_status, _ = mock_llama_server_evaluate(pruned, max_ctx=32768)
        self.assertEqual(pos_status, 200)

    def test_unicode_and_multibyte_massive_payload(self):
        """Massive prompt with multi-byte CJK, emojis, and symbols is pruned cleanly without decode errors."""
        emoji_cjk = ("🚀 操作系统 MiOS 深度测试 🎯 " * 10000)
        req = {
            "messages": [
                {"role": "user", "content": emoji_cjk}
            ],
            "max_tokens": 2048
        }
        pruned = mios_vision._prune_request_to_context_budget(req, max_ctx=32768)
        pruned_tokens = mios_tokenize.count_messages(pruned["messages"])
        self.assertLessEqual(pruned_tokens, 32768 - 1024)
        pos_status, _ = mock_llama_server_evaluate(pruned, max_ctx=32768)
        self.assertEqual(pos_status, 200)


class TestAdversarial193ToolsHandling(unittest.TestCase):
    """Stress testing 193 tools request handling, deduplication, and capping."""

    def setUp(self):
        self.tools_193 = [
            {
                "type": "function",
                "function": {
                    "name": f"custom_mcp_tool_{i:03d}",
                    "description": f"Extended MCP tool capability number {i}",
                    "parameters": {
                        "type": "object",
                        "properties": {"arg": {"type": "string"}},
                        "required": ["arg"]
                    }
                }
            }
            for i in range(193)
        ]

    def test_has_client_tools_detects_193_tools(self):
        """_has_client_tools returns True for 193 tools."""
        body = {
            "messages": [{"role": "user", "content": "execute"}],
            "tools": self.tools_193
        }
        self.assertTrue(mios_vision._has_client_tools(body))

    def test_193_tools_suppresses_mios_sel(self):
        """193 tools exceeds DEFAULT_TOOL_CAP (24) -> _mios_sel is completely suppressed."""
        async def _select_child_tools(surface, intent, cap):
            return [{"type": "function", "function": {"name": "unexpected_mios_tool"}}]

        mios_vision.configure(
            verb_catalog={"app_search": {}},
            resolve_verb_key=lambda name: name,
            select_child_tools=_select_child_tools,
            default_tool_cap=24,
        )

        captured_reqs = []
        async def _mock_backend(req):
            captured_reqs.append(dict(req))
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}]}

        orig_backend = mios_vision._client_tools_backend
        mios_vision._client_tools_backend = _mock_backend
        try:
            body = {
                "messages": [{"role": "user", "content": "list capabilities"}],
                "tools": self.tools_193
            }
            client_names = {t["function"]["name"] for t in self.tools_193}
            res = asyncio.run(mios_vision._client_tools_loop(body, client_names, "cid-193"))
            self.assertEqual(res.get("content"), "done")

            dispatched_tools = captured_reqs[0].get("tools", [])
            tool_names = [t["function"]["name"] for t in dispatched_tools]

            # Assert unexpected_mios_tool is NOT injected
            self.assertNotIn("unexpected_mios_tool", tool_names)
            # Assert all 193 tools are preserved
            self.assertEqual(len(dispatched_tools), 193)
        finally:
            mios_vision._client_tools_backend = orig_backend

    def test_193_tools_deduplication(self):
        """193 tools containing 50 duplicates are deduplicated to unique names."""
        dup_tools = copy.deepcopy(self.tools_193[:143])
        # Add 50 duplicate tools
        for i in range(50):
            dup_tools.append(copy.deepcopy(self.tools_193[i]))
        self.assertEqual(len(dup_tools), 193)

        captured_reqs = []
        async def _mock_backend(req):
            captured_reqs.append(dict(req))
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}]}

        orig_backend = mios_vision._client_tools_backend
        mios_vision._client_tools_backend = _mock_backend
        try:
            body = {
                "messages": [{"role": "user", "content": "dedup test"}],
                "tools": dup_tools
            }
            client_names = {t["function"]["name"] for t in dup_tools}
            asyncio.run(mios_vision._client_tools_loop(body, client_names, "cid-dedup"))

            dispatched_tools = captured_reqs[0].get("tools", [])
            dispatched_names = [t["function"]["name"] for t in dispatched_tools]
            self.assertEqual(len(dispatched_names), 143, f"Expected 143 unique tools, got {len(dispatched_names)}")
            self.assertEqual(len(dispatched_names), len(set(dispatched_names)))
        finally:
            mios_vision._client_tools_backend = orig_backend

    def test_193_tools_with_extreme_schemas_capped_on_context_overflow(self):
        """If 193 tools alone exceed context budget, pruning caps tools to DEFAULT_TOOL_CAP (24)."""
        huge_tools = []
        for i in range(193):
            huge_tools.append({
                "type": "function",
                "function": {
                    "name": f"huge_tool_{i:03d}",
                    "description": "Massive schema parameter specification: " + ("prop_desc " * 200),
                    "parameters": {"type": "object", "properties": {f"param_{j}": {"type": "string", "description": "desc"} for j in range(10)}}
                }
            })

        req = {
            "messages": [{"role": "user", "content": "run task"}],
            "tools": huge_tools,
            "max_tokens": 1024
        }
        raw_tokens = mios_tokenize.count_messages(req["messages"], tools=req["tools"])
        self.assertGreater(raw_tokens, 35000)

        pruned = mios_vision._prune_request_to_context_budget(req, max_ctx=32768)
        pruned_tokens = mios_tokenize.count_messages(pruned["messages"], tools=pruned.get("tools"))

        self.assertLessEqual(pruned_tokens, 32768 - 1024)
        self.assertLessEqual(pruned_tokens + pruned["max_tokens"], 32768)
        kept = pruned.get("tools", [])
        self.assertTrue(kept, "pruning must not drop every caller tool")
        # Schemas are compacted, not rewritten: every kept tool keeps its name
        # and parameter names, and caller order is preserved.
        names = [t["function"]["name"] for t in kept]
        self.assertEqual(names, sorted(names))
        for t in kept:
            self.assertEqual(set(t["function"]["parameters"]["properties"]),
                             {f"param_{j}" for j in range(10)})
        pos_status, _ = mock_llama_server_evaluate(pruned, max_ctx=32768)
        self.assertEqual(pos_status, 200)

    def test_malformed_tools_do_not_crash_gateway(self):
        """Tools with missing function object, missing name, or empty dicts do not crash."""
        weird_tools = [
            {},
            {"type": "function"},
            {"type": "function", "function": {}},
            {"type": "custom", "name": "custom_name"},
            {"type": "function", "function": {"name": None}},
        ]
        body = {
            "messages": [{"role": "user", "content": "test"}],
            "tools": weird_tools
        }
        has_tools = mios_vision._has_client_tools(body)
        self.assertTrue(has_tools)
        pruned = mios_vision._prune_request_to_context_budget(body, max_ctx=32768)
        self.assertIsNotNone(pruned)


class TestAdversarialToolChoiceNoneStripping(unittest.TestCase):
    """Stress testing tool_choice: 'none' stripping with 169 tools."""

    def setUp(self):
        self.tools_169 = [
            {"type": "function", "function": {"name": f"mcp_tool_{i:03d}", "description": "mcp function"}}
            for i in range(169)
        ]

    def test_tool_choice_none_with_169_tools_returns_false_in_has_client_tools(self):
        """tool_choice: 'none' forces _has_client_tools to False despite 169 tools."""
        body = {
            "messages": [{"role": "user", "content": "plain chat turn"}],
            "tools": self.tools_169,
            "tool_choice": "none"
        }
        self.assertFalse(mios_vision._has_client_tools(body))

    def test_tool_choice_none_with_169_tools_stripped_in_pruning(self):
        """_prune_request_to_context_budget strips tools and tool_choice completely."""
        req = {
            "messages": [{"role": "user", "content": "plain chat"}],
            "tools": self.tools_169,
            "tool_choice": "none"
        }
        pruned = mios_vision._prune_request_to_context_budget(req, max_ctx=32768)
        self.assertNotIn("tools", pruned)
        self.assertNotIn("tool_choice", pruned)
        tool_tokens = mios_tokenize.count_messages([], tools=pruned.get("tools"))
        self.assertEqual(tool_tokens, 0)

    def test_tool_choice_none_with_169_tools_in_client_tools_loop(self):
        """_client_tools_loop sets tools=[] and _mios_sel=[] when tool_choice == 'none'."""
        captured_reqs = []
        async def _mock_backend(req):
            captured_reqs.append(dict(req))
            return {"choices": [{"message": {"role": "assistant", "content": "plain response"}}]}

        orig_backend = mios_vision._client_tools_backend
        mios_vision._client_tools_backend = _mock_backend
        try:
            body = {
                "messages": [{"role": "user", "content": "plain chat"}],
                "tools": self.tools_169,
                "tool_choice": "none"
            }
            client_names = {t["function"]["name"] for t in self.tools_169}
            res = asyncio.run(mios_vision._client_tools_loop(body, client_names, "cid-none-169"))
            self.assertEqual(res.get("content"), "plain response")

            dispatched = captured_reqs[0]
            self.assertNotIn("tools", dispatched)
            self.assertNotIn("tool_choice", dispatched)
        finally:
            mios_vision._client_tools_backend = orig_backend

    def test_two_sided_control_tool_choice_auto_retains_169_tools(self):
        """Two-sided control: tool_choice: 'auto' retains all 169 tools and tool_choice."""
        captured_reqs = []
        async def _mock_backend(req):
            captured_reqs.append(dict(req))
            return {"choices": [{"message": {"role": "assistant", "content": "res"}}]}

        orig_backend = mios_vision._client_tools_backend
        mios_vision._client_tools_backend = _mock_backend
        try:
            body = {
                "messages": [{"role": "user", "content": "use tools"}],
                "tools": self.tools_169,
                "tool_choice": "auto"
            }
            client_names = {t["function"]["name"] for t in self.tools_169}
            asyncio.run(mios_vision._client_tools_loop(body, client_names, "cid-auto-169"))

            dispatched = captured_reqs[0]
            self.assertEqual(len(dispatched.get("tools", [])), 169)
            self.assertEqual(dispatched.get("tool_choice"), "auto")
        finally:
            mios_vision._client_tools_backend = orig_backend

    def test_tool_choice_case_sensitivity_empirical_probe(self):
        """Empirically probe case-sensitivity behavior: lowercase 'none' vs uppercase 'None'/'NONE'."""
        body_lower = {"tools": [{"type": "function"}], "tool_choice": "none"}
        body_capital = {"tools": [{"type": "function"}], "tool_choice": "None"}
        body_upper = {"tools": [{"type": "function"}], "tool_choice": "NONE"}

        self.assertFalse(mios_vision._has_client_tools(body_lower))
        capital_result = mios_vision._has_client_tools(body_capital)
        upper_result = mios_vision._has_client_tools(body_upper)
        self.assertFalse(capital_result, "Case-insensitive: 'None' matches 'none'")
        self.assertFalse(upper_result, "Case-insensitive: 'NONE' matches 'none'")


    def test_chat_completions_logic_strips_tools_on_tool_choice_none(self):
        """In chat_completions_logic, tool_choice: 'none' strips tools and tool_choice from body."""
        captured_dispatches = []
        test_mios_chat._wire_common(
            _has_client_tools=mios_vision._has_client_tools,
            refine_intent=test_mios_chat._ahandler("refine", {"intent": "chat", "reply": "chat reply"}),
            _scratchpad_key=lambda b, cid: (captured_dispatches.append(dict(b)), cid)[1],
        )

        req_body = {
            "model": "m",
            "messages": [{"role": "user", "content": "plain chat"}],
            "tools": self.tools_169,
            "tool_choice": "none"
        }
        req = FakeRequest(json.dumps(req_body).encode("utf-8"))
        res = asyncio.run(mios_chat.chat_completions_logic(req))

        self.assertTrue(len(captured_dispatches) >= 1)
        for d in captured_dispatches:
            self.assertNotIn("tools", d, f"Dispatched body should not contain 'tools': {d}")
            self.assertNotIn("tool_choice", d, f"Dispatched body should not contain 'tool_choice': {d}")


class TestAdversarialVerbSuppressionAndThresholds(unittest.TestCase):
    """Stress testing client tool suppression thresholds (DEFAULT_TOOL_CAP = 24) and MiOS verb detection."""

    def test_client_tool_matches_mios_verb_suppresses_mios_sel(self):
        """Single client tool matching a registered MiOS verb suppresses _mios_sel."""
        async def _select_child_tools(surface, intent, cap):
            return [{"type": "function", "function": {"name": "should_be_suppressed"}}]

        mios_vision.configure(
            verb_catalog={"app_search": {}, "launch_app": {}, "open_url": {}},
            resolve_verb_key=lambda name: name,
            select_child_tools=_select_child_tools,
            default_tool_cap=24,
        )

        captured_reqs = []
        async def _mock_backend(req):
            captured_reqs.append(dict(req))
            return {"choices": [{"message": {"role": "assistant", "content": "res"}}]}

        orig_backend = mios_vision._client_tools_backend
        mios_vision._client_tools_backend = _mock_backend
        try:
            body = {
                "messages": [{"role": "user", "content": "open browser"}],
                "tools": [
                    {"type": "function", "function": {"name": "app_search"}}  # matches verb!
                ]
            }
            asyncio.run(mios_vision._client_tools_loop(body, {"app_search"}, "cid-verb-match"))
            tools = [t["function"]["name"] for t in captured_reqs[0].get("tools", [])]
            self.assertNotIn("should_be_suppressed", tools)
            self.assertEqual(tools, ["app_search"])
        finally:
            mios_vision._client_tools_backend = orig_backend

    def test_threshold_exact_boundaries_23_vs_24_vs_25(self):
        """Exact boundary tests: 23 tools appends _mios_sel, 24 tools suppresses, 25 tools suppresses."""
        async def _select_child_tools(surface, intent, cap):
            return [{"type": "function", "function": {"name": "appended_mios_sel"}}]

        mios_vision.configure(
            verb_catalog={"unrelated_verb": {}},
            resolve_verb_key=lambda name: name,
            select_child_tools=_select_child_tools,
            default_tool_cap=24,
        )

        captured_reqs = []
        async def _mock_backend(req):
            captured_reqs.append(dict(req))
            return {"choices": [{"message": {"role": "assistant", "content": "res"}}]}

        orig_backend = mios_vision._client_tools_backend
        mios_vision._client_tools_backend = _mock_backend
        try:
            # 1. Boundary 23 tools: NOT suppressed -> appended_mios_sel IS injected
            tools_23 = [{"type": "function", "function": {"name": f"tool_{i}"}} for i in range(23)]
            body_23 = {"messages": [{"role": "user", "content": "test"}], "tools": tools_23}
            asyncio.run(mios_vision._client_tools_loop(body_23, {f"tool_{i}" for i in range(23)}, "cid-23"))
            t_names_23 = [t["function"]["name"] for t in captured_reqs[-1].get("tools", [])]
            self.assertIn("appended_mios_sel", t_names_23, "23 tools should NOT suppress _mios_sel")
            self.assertEqual(len(t_names_23), 24)

            # 2. Boundary 24 tools: SUPPRESSED -> appended_mios_sel is NOT injected
            tools_24 = [{"type": "function", "function": {"name": f"tool_{i}"}} for i in range(24)]
            body_24 = {"messages": [{"role": "user", "content": "test"}], "tools": tools_24}
            asyncio.run(mios_vision._client_tools_loop(body_24, {f"tool_{i}" for i in range(24)}, "cid-24"))
            t_names_24 = [t["function"]["name"] for t in captured_reqs[-1].get("tools", [])]
            self.assertNotIn("appended_mios_sel", t_names_24, "24 tools MUST suppress _mios_sel")
            self.assertEqual(len(t_names_24), 24)

            # 3. Boundary 25 tools: SUPPRESSED -> appended_mios_sel is NOT injected
            tools_25 = [{"type": "function", "function": {"name": f"tool_{i}"}} for i in range(25)]
            body_25 = {"messages": [{"role": "user", "content": "test"}], "tools": tools_25}
            asyncio.run(mios_vision._client_tools_loop(body_25, {f"tool_{i}" for i in range(25)}, "cid-25"))
            t_names_25 = [t["function"]["name"] for t in captured_reqs[-1].get("tools", [])]
            self.assertNotIn("appended_mios_sel", t_names_25, "25 tools MUST suppress _mios_sel")
            self.assertEqual(len(t_names_25), 25)
        finally:
            mios_vision._client_tools_backend = orig_backend


class TestAdversarialEmptyAndMalformedBodies(unittest.TestCase):
    """Stress testing empty, missing, and malformed request bodies."""

    def test_empty_json_body_returns_400(self):
        """POST with empty JSON {} returns HTTP 400 with invalid_request_error."""
        test_mios_chat._wire_common()
        req = FakeRequest(b"{}")
        resp = asyncio.run(mios_chat.chat_completions_logic(req))
        self.assertEqual(getattr(resp, "status_code", None), 400)
        err = getattr(resp, "content", {}).get("error", {})
        self.assertIn("messages", err.get("message", ""))

    def test_empty_bytes_body_returns_400(self):
        """POST with 0-byte body b'' returns HTTP 400."""
        test_mios_chat._wire_common()
        req = FakeRequest(b"")
        resp = asyncio.run(mios_chat.chat_completions_logic(req))
        self.assertEqual(getattr(resp, "status_code", None), 400)

    def test_malformed_json_syntax_returns_400(self):
        """POST with broken JSON syntax returns HTTP 400."""
        test_mios_chat._wire_common()
        req = FakeRequest(b'{"messages": [{"role": "user"')
        resp = asyncio.run(mios_chat.chat_completions_logic(req))
        self.assertEqual(getattr(resp, "status_code", None), 400)

    def test_empty_messages_list_returns_400(self):
        """POST with messages: [] returns HTTP 400."""
        test_mios_chat._wire_common()
        req = FakeRequest(b'{"messages": []}')
        resp = asyncio.run(mios_chat.chat_completions_logic(req))
        self.assertEqual(getattr(resp, "status_code", None), 400)

    def test_messages_is_string_returns_400(self):
        """POST with messages: 'not a list' returns HTTP 400."""
        test_mios_chat._wire_common()
        req = FakeRequest(b'{"messages": "invalid string"}')
        resp = asyncio.run(mios_chat.chat_completions_logic(req))
        self.assertEqual(getattr(resp, "status_code", None), 400)

    def test_non_dict_message_elements_handled_safely(self):
        """Messages containing non-dict items (None, numbers, strings) do not crash pruning."""
        body = {
            "messages": [
                None,
                123,
                "raw string",
                {"role": "user", "content": "valid turn"}
            ]
        }
        pruned = mios_vision._prune_request_to_context_budget(body, max_ctx=32768)
        self.assertIsNotNone(pruned)

    def test_message_with_null_content_handled_safely(self):
        """Messages with null content do not raise TypeError during token counting or pruning."""
        body = {
            "messages": [
                {"role": "user", "content": None},
                {"role": "assistant", "content": ""},
                {"role": "user", "content": "hello"}
            ]
        }
        toks = mios_tokenize.count_messages(body["messages"])
        self.assertGreaterEqual(toks, 0)
        pruned = mios_vision._prune_request_to_context_budget(body, max_ctx=32768)
        self.assertIsNotNone(pruned)

    def test_tools_is_not_a_list_handled_safely(self):
        """Body with tools: 'invalid' or tools: 123 is handled safely without unhandled exception."""
        body = {
            "messages": [{"role": "user", "content": "hi"}],
            "tools": "not a list"
        }
        self.assertFalse(mios_vision._has_client_tools(body))
        pruned = mios_vision._prune_request_to_context_budget(body, max_ctx=32768)
        self.assertIsNotNone(pruned)


def _shape_violations(messages):
    """OpenAI chat shape problems a strict backend/template rejects."""
    problems = []
    open_ids = set()
    body = [m for m in messages if m.get("role") not in ("system", "developer")]
    if body and body[0].get("role") != "user":
        problems.append("first non-system turn is %r" % body[0].get("role"))
    prev = None
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            if open_ids:
                problems.append("unanswered tool_calls %s" % sorted(open_ids))
            open_ids = {c["id"] for c in m["tool_calls"]}
        elif role == "tool":
            if m.get("tool_call_id") not in open_ids:
                problems.append("orphan tool result %s" % m.get("tool_call_id"))
            open_ids.discard(m.get("tool_call_id"))
        else:
            if open_ids:
                problems.append("unanswered tool_calls %s" % sorted(open_ids))
                open_ids = set()
            if prev is not None and role in ("user", "assistant") and role == prev \
                    and not m.get("tool_calls"):
                problems.append("consecutive %s turns" % role)
        if not isinstance(m.get("content", ""), (str, list, type(None))):
            problems.append("content type %s" % type(m.get("content")).__name__)
        prev = role
    if open_ids:
        problems.append("unanswered tool_calls %s" % sorted(open_ids))
    return problems


def _agentic_history(rounds=6, result_chars=40000):
    msgs = [{"role": "system", "content": "You are MiOS."}]
    for i in range(rounds):
        msgs.append({"role": "user", "content": f"step {i}: " + "u" * 2000})
        msgs.append({"role": "assistant", "content": "",
                     "tool_calls": [{"id": f"c{i}", "type": "function",
                                     "function": {"name": "read", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "r" * result_chars})
    msgs.append({"role": "user", "content": "final question"})
    return msgs


class TestM3GateRegressions(unittest.TestCase):
    """Regressions for the M3 gate findings (reviewer, challenger, auditor)."""

    def test_shape_validator_is_two_sided(self):
        """Control: the validator flags each broken shape and passes a valid one."""
        good = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "a"}]},
                {"role": "tool", "tool_call_id": "a", "content": "r"},
                {"role": "assistant", "content": "done"}]
        self.assertEqual(_shape_violations(good), [])
        self.assertTrue(_shape_violations(good[:3]))                       # unanswered call
        self.assertTrue(_shape_violations([good[0], good[1], good[3]]))    # orphan result
        self.assertTrue(_shape_violations([good[0], good[4], good[1]]))    # assistant first
        self.assertTrue(_shape_violations([good[0], good[1], good[1]]))    # user, user

    def test_long_agentic_history_keeps_tool_pairs_and_alternation(self):
        """Pruned agentic history stays a valid OpenAI sequence and fits."""
        req = {"messages": _agentic_history(), "tools": [], "max_tokens": 1024}
        self.assertGreater(mios_tokenize.count_messages(req["messages"]), 32768)
        out = mios_vision._prune_request_to_context_budget(req, max_ctx=32768)
        self.assertEqual(_shape_violations(out["messages"]), [])
        self.assertEqual(out["messages"][0]["role"], "system")
        self.assertEqual(out["messages"][-1]["content"], "final question")
        toks = mios_tokenize.count_messages(out["messages"])
        self.assertLessEqual(toks + out["max_tokens"], 32768)

    def test_list_content_is_pruned_part_by_part(self):
        """Content-part arrays stay arrays; images become a text placeholder."""
        img = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 200000}}
        req = {"messages": [{"role": "system", "content": "s"},
                            {"role": "user", "content": [{"type": "text", "text": "t" * 150000}, img]}]}
        out = mios_vision._prune_request_to_context_budget(req, max_ctx=32768)
        content = out["messages"][-1]["content"]
        self.assertIsInstance(content, list)
        self.assertTrue(all(isinstance(p, dict) and "type" in p for p in content))
        self.assertFalse(any(p.get("type") == "image_url" for p in content))
        self.assertTrue(any(mios_vision._ELIDED_IMAGE in str(p.get("text")) for p in content))
        self.assertNotIn("{'type'", json.dumps(content))
        self.assertLessEqual(mios_tokenize.count_messages(out["messages"]) + out["max_tokens"], 32768)

    def _fat_tools(self, n, desc_words):
        return [{"type": "function", "function": {
            "name": f"t{i}", "description": "d " * desc_words,
            "parameters": {"type": "object", "properties": {
                "q": {"type": "string", "description": "x " * desc_words}}}}} for i in range(n)]

    def test_tool_dominated_overflow_fits_context(self):
        """23 tools (under the cap) whose schemas alone overflow are budgeted to fit."""
        req = {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "hello"}],
               "tools": self._fat_tools(23, 3800), "tool_choice": "auto"}
        self.assertGreater(mios_tokenize.count_messages(req["messages"], tools=req["tools"]), 32768)
        out = mios_vision._prune_request_to_context_budget(req, max_ctx=32768)
        total = mios_tokenize.count_messages(out["messages"], tools=out.get("tools")) + out["max_tokens"]
        self.assertLessEqual(total, 32768)
        self.assertTrue(out.get("tools"))
        self.assertTrue(all(set(t["function"]["parameters"]["properties"]) == {"q"} for t in out["tools"]))

    def test_forced_tool_survives_budgeting(self):
        """A tool named by tool_choice is never the one dropped."""
        req = {"messages": [{"role": "user", "content": "go"}], "tools": self._fat_tools(193, 3800),
               "tool_choice": {"type": "function", "function": {"name": "t150"}}}
        out = mios_vision._prune_request_to_context_budget(req, max_ctx=32768)
        self.assertIn("t150", [t["function"]["name"] for t in out["tools"]])
        total = mios_tokenize.count_messages(out["messages"], tools=out["tools"]) + out["max_tokens"]
        self.assertLessEqual(total, 32768)

    def test_loop_drops_tool_choice_variants_and_malformed_tools(self):
        """The loop never forwards a 'none' variant and skips non-dict tools."""
        captured = []

        async def fake_backend(req):
            captured.append(req)
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

        orig = mios_vision._client_tools_backend
        mios_vision._client_tools_backend = fake_backend
        try:
            for variant in ("NONE", " None ", "none"):
                body = {"messages": [{"role": "user", "content": "hi"}],
                        "tools": [{"type": "function", "function": {"name": "f1"}}],
                        "tool_choice": variant}
                asyncio.run(mios_vision._client_tools_loop(body, {"f1"}, "cid"))
                self.assertNotIn("tool_choice", captured[-1], variant)
                self.assertNotIn("tools", captured[-1], variant)
            body = {"messages": [{"role": "user", "content": "hi"}],
                    "tools": ["not-a-dict", None, {"type": "function", "function": {"name": "f1"}}]}
            asyncio.run(mios_vision._client_tools_loop(body, set(), "cid"))
            names = [mios_vision._tool_name(t) for t in captured[-1].get("tools", [])]
            self.assertIn("f1", names)
        finally:
            mios_vision._client_tools_backend = orig

    def test_invalid_ctx_env_falls_back(self):
        """An unparsable MIOS_AGENT_PIPE_TOOL_CTX falls back instead of raising."""
        old = os.environ.get("MIOS_AGENT_PIPE_TOOL_CTX")
        try:
            os.environ["MIOS_AGENT_PIPE_TOOL_CTX"] = "abc"
            self.assertEqual(mios_vision._tool_ctx(), mios_vision._DEFAULT_TOOL_CTX)
            os.environ["MIOS_AGENT_PIPE_TOOL_CTX"] = "8192"
            self.assertEqual(mios_vision._tool_ctx(), 8192)
        finally:
            if old is None:
                os.environ.pop("MIOS_AGENT_PIPE_TOOL_CTX", None)
            else:
                os.environ["MIOS_AGENT_PIPE_TOOL_CTX"] = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
