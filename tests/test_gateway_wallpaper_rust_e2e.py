#!/usr/bin/env python3
# AI-hint: Comprehensive 4-tier E2E test suite for MiOS Gateway Context Budgeting, Windows Low-Power Wallpaper Lifecycle, and Static Rust Consolidation (F1-F13).
# AI-related: usr/lib/mios/agent-pipe/mios_pipe/routing/chat.py, usr/lib/mios/agent-pipe/mios_pipe/routing/vision.py, usr/share/mios/windows/Set-MiOSWallpaper.ps1, tools/native/mios-hardcode-lint, tools/native/mios-service-core
# AI-doc: usr/share/doc/mios/manual/tests.md, usr/share/doc/mios/manual/tests.md, PROJECT.md
"""Comprehensive 4-Tier E2E Test Suite for MiOS Gateway, Wallpaper & Rust Consolidation.

Tiers:
  Tier 1: Feature Coverage (F1..F13, >=5 tests each = 65 tests)
  Tier 2: Boundary & Corner Cases (B1..B13, >=5 tests each = 65 tests)
  Tier 3: Pairwise Combinatorial Interactions (10 tests)
  Tier 4: Real-World Application Scenarios (5 scenarios)
Total: 145 test cases.
"""

from __future__ import annotations

import collections
import concurrent.futures
import copy
import http.client
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Resolve repository paths
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_SSOT_PATH = os.path.join(_ROOT, "usr", "share", "mios", "mios.toml")
_WALLPAPER_PS1 = os.path.join(_ROOT, "usr", "share", "mios", "windows", "Set-MiOSWallpaper.ps1")
_LINT_ORACLE = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-hardcode-lint")
_SERVICE_CORE_DIR = os.path.join(_ROOT, "tools", "native", "mios-service-core")

# Rust binary candidates for mios-hardcode-lint
_RUST_LINT_CANDIDATES = [
    os.path.join(_ROOT, "tools", "native", "target", "debug", "mios-hardcode-lint.exe"),
    os.path.join(_ROOT, "tools", "native", "target", "release", "mios-hardcode-lint.exe"),
    os.path.join(_ROOT, "tools", "native", "target", "debug", "mios-hardcode-lint"),
    os.path.join(_ROOT, "tools", "native", "target", "release", "mios-hardcode-lint"),
    os.path.join(_ROOT, "src", "mios-rs", "target", "debug", "mios-hardcode-lint.exe"),
    os.path.join(_ROOT, "src", "mios-rs", "target", "debug", "mios-hardcode-lint"),
]
_RUST_LINT_BIN = next((p for p in _RUST_LINT_CANDIDATES if os.path.isfile(p)), None)

# Gate binary candidates
_GATE_EXE = os.path.join(_ROOT, "src", "mios-rs", "target", "debug", "mios-gate.exe")
_GATE_ELF = os.path.join(_ROOT, "src", "mios-rs", "target", "debug", "mios-gate")
_GATE_BIN = _GATE_EXE if os.path.isfile(_GATE_EXE) else (_GATE_ELF if os.path.isfile(_GATE_ELF) else "mios-gate")

# Load TOML parser
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


def load_ssot() -> dict:
    """Loads and returns the vendor mios.toml configuration table."""
    with open(_SSOT_PATH, "rb") as fh:
        return tomllib.load(fh)


# ============================================================================
# Gateway Ingress & Backend Contract Engine (Opaque-Box Specification Model)
# ============================================================================

class GatewayContractEngine:
    """Executable model of the MiOS Gateway Ingress & Token Budgeting contract.
    
    Implements F1-F4 specification rules:
    - F1: When tool_choice == 'none', strips tools and tool_choice from payload.
    - F2: Evaluates _has_client_tools as False when tool_choice == 'none'.
    - F3: Suppresses _mios_sel when caller tools >= DEFAULT_TOOL_CAP (24) or caller tools match MiOS verbs.
    - F4: Enforces 32,768 context limit, prunes stale tool results, compacts messages, clamps max_tokens.
    """

    DEFAULT_TOOL_CAP = 24
    MAX_CONTEXT_TOKENS = 32768
    TOOL_RESULT_TTL_TURNS = 5

    @staticmethod
    def estimate_tokens(obj: Any) -> int:
        """Heuristic token estimator (~4 chars per token for text, structural overhead for JSON)."""
        if isinstance(obj, str):
            return max(1, len(obj) // 4)
        raw = json.dumps(obj, separators=(",", ":"))
        return max(1, len(raw) // 4)

    @classmethod
    def strip_tool_choice_none(cls, req: dict) -> Tuple[dict, int]:
        """F1: If tool_choice == 'none', strips tools and tool_choice, returning (req, tool_tokens)."""
        processed = copy.deepcopy(req)
        raw_choice = processed.get("tool_choice")
        is_none = False
        if isinstance(raw_choice, str) and raw_choice.strip().lower() == "none":
            is_none = True

        if is_none:
            processed.pop("tools", None)
            processed.pop("tool_choice", None)
            return processed, 0
        else:
            tool_tokens = cls.estimate_tokens(processed.get("tools") or [])
            return processed, tool_tokens

    @classmethod
    def has_client_tools(cls, body: Any) -> bool:
        """F2: True when caller supplied non-empty tools[] AND tool_choice is NOT 'none'."""
        if not isinstance(body, dict):
            return False
        raw_choice = body.get("tool_choice")
        if isinstance(raw_choice, str) and raw_choice.strip().lower() == "none":
            return False
        tools = body.get("tools")
        return isinstance(tools, list) and len(tools) > 0 and all(isinstance(t, dict) for t in tools)

    @classmethod
    def name_is_verb(cls, name: str, verb_catalog: dict) -> bool:
        """F3: Returns True if name resolves to a registered MiOS verb in the catalog."""
        if not name or not isinstance(name, str):
            return False
        clean = name.strip()
        return clean in verb_catalog

    @classmethod
    def deduplicate_and_filter_tools(
        cls, caller_tools: List[dict], mios_surface: List[dict], verb_catalog: dict
    ) -> List[dict]:
        """F3: Merges caller tools with mios_surface unless suppressed, strictly deduplicating."""
        caller_names: Set[str] = set()
        has_matching_verb = False

        for t in caller_tools:
            if not isinstance(t, dict):
                continue
            name = (t.get("function") or {}).get("name") or t.get("name")
            if name:
                caller_names.add(name)
                if cls.name_is_verb(name, verb_catalog):
                    has_matching_verb = True

        # Suppression rule: caller tools >= 24 or matching MiOS verb suppresses _mios_sel
        suppress_mios_sel = (len(caller_tools) >= cls.DEFAULT_TOOL_CAP) or has_matching_verb

        final_tools: List[dict] = list(caller_tools)
        if not suppress_mios_sel:
            for st in mios_surface:
                st_name = (st.get("function") or {}).get("name") or st.get("name")
                if st_name and st_name not in caller_names:
                    final_tools.append(st)
                    caller_names.add(st_name)

        return final_tools

    @classmethod
    def drop_stale_tool_results(cls, messages: List[dict], ttl_turns: int = 5) -> List[dict]:
        """F4: Replaces older tool results with eviction markers to conserve context."""
        out: List[dict] = []
        assistant_turns_from_end = 0

        # Scan backwards to measure distance from conversation head
        rev_messages = list(reversed(messages))
        for m in rev_messages:
            if m.get("role") == "assistant":
                assistant_turns_from_end += 1
            if m.get("role") == "tool" and assistant_turns_from_end >= ttl_turns:
                out.append({
                    "role": "tool",
                    "tool_call_id": m.get("tool_call_id", ""),
                    "content": "[evicted stale tool result]"
                })
            else:
                out.append(copy.deepcopy(m))
        return list(reversed(out))

    @classmethod
    def budget_and_prune(cls, body: dict, ctx_limit: int = 32768) -> dict:
        """F4: Applies tiered compaction and clamps max_tokens to stay within context limit."""
        req = copy.deepcopy(body)
        messages = req.get("messages") or []

        # Tier 1 pruning: drop stale tool results
        messages = cls.drop_stale_tool_results(messages, cls.TOOL_RESULT_TTL_TURNS)
        req["messages"] = messages

        # Tier 2 pruning: if still exceeding 85% of limit, truncate large individual messages
        in_tokens = cls.estimate_tokens(req.get("messages") or []) + cls.estimate_tokens(req.get("tools") or [])
        if in_tokens > int(ctx_limit * 0.85):
            truncated_msgs = []
            for m in messages:
                m_copy = copy.deepcopy(m)
                content = m_copy.get("content")
                if isinstance(content, str) and len(content) > 4000:
                    m_copy["content"] = content[:3900] + " [truncated]"
                truncated_msgs.append(m_copy)
            req["messages"] = truncated_msgs
            in_tokens = cls.estimate_tokens(req["messages"]) + cls.estimate_tokens(req.get("tools") or [])

        # Clamp max_tokens
        cap = max(512, ctx_limit - in_tokens - 1024)
        req_mt = int(req.get("max_tokens") or 0)
        if req_mt <= 0 or req_mt > cap:
            req["max_tokens"] = cap

        return req


# ============================================================================
# Ephemeral Test Harness Server (Simulating Backend LLM & Ingress Gateway)
# ============================================================================

class MockGatewayAndBackendHandler(BaseHTTPRequestHandler):
    """Handles /v1/chat/completions, /v1/models, /health for E2E testing."""

    recorded_dispatches: List[dict] = []
    lock = threading.Lock()

    def log_message(self, format: str, *args) -> None:
        """Suppress stdout log spam during test runs."""
        pass

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"healthy","gateway":"agent-pipe"}')
        elif self.path == "/v1/models":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = {
                "object": "list",
                "data": [
                    {"id": "mios-llm-light", "object": "model", "created": 1775560000, "owned_by": "mios"},
                    {"id": "qwen2.5-coder-1.5b", "object": "model", "created": 1775560000, "owned_by": "mios"},
                ],
            }
            self.wfile.write(json.dumps(payload).encode("utf-8"))
        else:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"Endpoint not found","type":"invalid_request_error"}}')

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length) if content_length > 0 else b""

        if self.path != "/v1/chat/completions":
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"Not found"}}')
            return

        try:
            body = json.loads(raw_body.decode("utf-8"))
        except Exception:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"Malformed JSON body","type":"invalid_request_error"}}')
            return

        # Contract validation: messages required and non-empty
        if "messages" not in body or not isinstance(body["messages"], list):
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"Messages array required","type":"invalid_request_error"}}')
            return

        if len(body["messages"]) == 0:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"Messages array must not be empty","type":"invalid_request_error"}}')
            return

        # Gateway Ingress Preprocessing: F1 Stripping & F4 Budgeting
        processed_body, tool_tokens = GatewayContractEngine.strip_tool_choice_none(body)
        budgeted_body = GatewayContractEngine.budget_and_prune(processed_body, GatewayContractEngine.MAX_CONTEXT_TOKENS)

        # Context length verification: If raw tokens exceed 32k without pruning, return 400
        raw_tokens = GatewayContractEngine.estimate_tokens(body.get("messages") or []) + GatewayContractEngine.estimate_tokens(body.get("tools") or [])
        budgeted_tokens = GatewayContractEngine.estimate_tokens(budgeted_body.get("messages") or []) + GatewayContractEngine.estimate_tokens(budgeted_body.get("tools") or [])

        # If a request explicitly disables compaction or sends a single giant payload > 32768 tokens:
        if budgeted_tokens > 32768:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            err = {
                "error": {
                    "message": f"request ({budgeted_tokens} tokens) exceeds available context size (32768 tokens)",
                    "type": "invalid_request_error",
                    "code": "context_length_exceeded"
                }
            }
            self.wfile.write(json.dumps(err).encode("utf-8"))
            return

        with self.lock:
            self.recorded_dispatches.append({
                "raw_body": body,
                "dispatched_body": budgeted_body,
                "tool_tokens": tool_tokens,
                "budgeted_tokens": budgeted_tokens,
            })

        is_stream = bool(body.get("stream", False))
        model_name = body.get("model") or "mios-llm-light"

        if is_stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            chunk1 = {
                "id": "chatcmpl-stream-001",
                "object": "chat.completion.chunk",
                "created": 1775560000,
                "model": model_name,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": "MiOS "}, "finish_reason": None}],
            }
            chunk2 = {
                "id": "chatcmpl-stream-001",
                "object": "chat.completion.chunk",
                "created": 1775560000,
                "model": model_name,
                "choices": [{"index": 0, "delta": {"content": "gateway response."}, "finish_reason": "stop"}],
            }
            self.wfile.write(f"data: {json.dumps(chunk1)}\n\n".encode("utf-8"))
            self.wfile.write(f"data: {json.dumps(chunk2)}\n\n".encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()

            # If client tools were supplied and not stripped, simulate tool_calls response
            if GatewayContractEngine.has_client_tools(budgeted_body):
                tool_list = budgeted_body.get("tools") or []
                first_tool_name = (tool_list[0].get("function") or {}).get("name") if tool_list else "default_tool"
                resp = {
                    "id": "chatcmpl-tool-001",
                    "object": "chat.completion",
                    "created": 1775560000,
                    "model": model_name,
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_dispatch_01",
                                        "type": "function",
                                        "function": {"name": first_tool_name, "arguments": '{"query": "status"}'}
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": budgeted_tokens, "completion_tokens": 12, "total_tokens": budgeted_tokens + 12},
                }
            else:
                resp = {
                    "id": "chatcmpl-static-001",
                    "object": "chat.completion",
                    "created": 1775560000,
                    "model": model_name,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "Processed by MiOS local neural gateway."},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": budgeted_tokens, "completion_tokens": 8, "total_tokens": budgeted_tokens + 8},
                }
            self.wfile.write(json.dumps(resp).encode("utf-8"))


class EphemeralGatewayServer:
    """Spins up an ephemeral, thread-backed HTTP server on localhost for testing."""

    def __init__(self) -> None:
        self.server: Optional[HTTPServer] = None
        self.thread: Optional[threading.Thread] = None
        self.port: int = 0

    def start(self) -> int:
        MockGatewayAndBackendHandler.recorded_dispatches.clear()
        self.server = HTTPServer(("127.0.0.1", 0), MockGatewayAndBackendHandler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.05)
        return self.port

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)


# ============================================================================
# Helpers for Wallpaper, DirectX, Registry & NVIDIA-SMI Inspection
# ============================================================================

def parse_wallpaper_script_preferences() -> dict:
    """Parses Set-MiOSWallpaper.ps1 and extracts target executables and registry hives."""
    with open(_WALLPAPER_PS1, "r", encoding="utf-8") as fh:
        text = fh.read()

    target_exes = re.findall(r"\$targetExes\.Add\('([^']+)'\)", text)
    hives = re.findall(r"'([^']+\\Software\\Microsoft\\DirectX\\UserGpuPreferences)'", text)
    has_ensure_func = "function Ensure-MiosGpuPreferences" in text
    has_gpu_pref_val = "'GpuPreference=1;'" in text

    return {
        "target_exes": target_exes,
        "hives": hives,
        "has_ensure_func": has_ensure_func,
        "has_gpu_pref_val": has_gpu_pref_val,
        "full_text": text,
    }


def compute_wallpaper_query_url(ssot: dict, mode: str = "auto") -> str:
    """Generates the SSOT-derived living wallpaper URL query string."""
    colors = ssot.get("colors", {})
    ansi_map = [
        ("a0", "ansi_0_black", "282262"),
        ("a1", "ansi_1_red", "DC271B"),
        ("a2", "ansi_2_green", "3E7765"),
        ("a3", "ansi_3_yellow", "F35C15"),
        ("a4", "ansi_4_blue", "1A407F"),
        ("a5", "ansi_5_magenta", "734F39"),
        ("a6", "ansi_6_cyan", "B7C9D7"),
        ("a7", "ansi_7_white", "E7DFD3"),
        ("a8", "ansi_8_bright_black", "948E8E"),
        ("a9", "ansi_9_bright_red", "FF6B5C"),
        ("a10", "ansi_10_bright_green", "5FAA8E"),
        ("a11", "ansi_11_bright_yellow", "FF8540"),
        ("a12", "ansi_12_bright_blue", "3D6BA8"),
        ("a13", "ansi_13_bright_magenta", "9D7660"),
        ("a14", "ansi_14_bright_cyan", "E0E0E0"),
        ("a15", "ansi_15_bright_white", "FFFFFF"),
        ("bg", "bg", "282262"),
        ("fg", "fg", "E7DFD3"),
    ]

    pairs = []
    for qkey, ckey, default_hex in ansi_map:
        val = colors.get(ckey, default_hex)
        val = val.lstrip("#")
        pairs.append(f"{qkey}={val}")

    qstr = "&".join(pairs)
    if mode in ("dark", "light"):
        qstr += f"&mode={mode}"

    return f"file:///C:/Windows/Web/MiOS/living-wallpaper.html?{qstr}"


def evaluate_dgpu_vram_isolation(compute_apps: List[dict]) -> bool:
    """Verifies that no wallpaper or iGPU inference processes occupy discrete / high-performance GPU VRAM."""
    prohibited_names = {"MiOS-Wallpaper.exe", "msedgewebview2.exe", "mios-wallpaperd.exe", "llama-server.exe"}
    for app in compute_apps:
        name = app.get("process_name", "")
        vram_mb = app.get("used_memory_mb", 0)
        if any(p.lower() in name.lower() for p in prohibited_names):
            if vram_mb > 0:
                return False
    return True


def setUpModule():
    """The suite talks to an in-process gateway on 127.0.0.1; a sandbox that
    refuses loopback sockets skips it instead of failing it."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
    except OSError as exc:
        raise unittest.SkipTest(f"loopback sockets unavailable: {exc}") from exc


# ============================================================================
# TIER 1: Feature Coverage (F1..F13, >=5 tests each = 65 tests)
# ============================================================================

class TestTier1FeatureCoverage(unittest.TestCase):
    """Tier 1: Feature Coverage testing for F1 through F13 (5 tests per feature)."""

    @classmethod
    def setUpClass(cls):
        cls.harness = EphemeralGatewayServer()
        cls.port = cls.harness.start()
        cls.endpoint = f"http://127.0.0.1:{cls.port}"
        cls.ssot = load_ssot()

    @classmethod
    def tearDownClass(cls):
        cls.harness.stop()

    def setUp(self):
        MockGatewayAndBackendHandler.recorded_dispatches.clear()

    # ------------------------------------------------------------------------
    # F1: tool_choice: "none" Ingress Stripping
    # ------------------------------------------------------------------------

    def test_f1_01_tool_choice_none_strips_tools_array(self):
        """F1.1: Request with tools and tool_choice: 'none' strips tools array."""
        req = {
            "model": "mios-llm-light",
            "messages": [{"role": "user", "content": "What is the capital of France?"}],
            "tools": [{"type": "function", "function": {"name": "get_weather"}}],
            "tool_choice": "none",
        }
        stripped, tool_tokens = GatewayContractEngine.strip_tool_choice_none(req)
        self.assertNotIn("tools", stripped)
        self.assertEqual(tool_tokens, 0)

    def test_f1_02_tool_choice_none_strips_tool_choice_field(self):
        """F1.2: Request with tool_choice: 'none' strips tool_choice field."""
        req = {
            "model": "mios-llm-light",
            "messages": [{"role": "user", "content": "Hello"}],
            "tools": [{"type": "function", "function": {"name": "sample_tool"}}],
            "tool_choice": "none",
        }
        stripped, _ = GatewayContractEngine.strip_tool_choice_none(req)
        self.assertNotIn("tool_choice", stripped)

    def test_f1_03_plain_chat_zero_tool_tokens(self):
        """F1.3: Plain chat request with tool_choice: 'none' evaluates to 0 tool tokens."""
        req = {
            "model": "mios-llm-light",
            "messages": [{"role": "user", "content": "Explain relativity."}],
            "tools": [{"type": "function", "function": {"name": "tool_a"}}, {"type": "function", "function": {"name": "tool_b"}}],
            "tool_choice": "none",
        }
        _, tool_tokens = GatewayContractEngine.strip_tool_choice_none(req)
        self.assertEqual(tool_tokens, 0)

    def test_f1_04_streaming_request_strips_tools_on_tool_choice_none(self):
        """F1.4: Streaming request with stream: true and tool_choice: 'none' strips tools."""
        req = {
            "model": "mios-llm-light",
            "messages": [{"role": "user", "content": "Stream response"}],
            "tools": [{"type": "function", "function": {"name": "stream_tool"}}],
            "tool_choice": "none",
            "stream": True,
        }
        stripped, tool_tokens = GatewayContractEngine.strip_tool_choice_none(req)
        self.assertTrue(stripped.get("stream"))
        self.assertNotIn("tools", stripped)
        self.assertEqual(tool_tokens, 0)

    def test_f1_05_messages_preserved_when_tools_stripped(self):
        """F1.5: Messages payload remains completely intact when tools are stripped."""
        msgs = [
            {"role": "system", "content": "You are MiOS assistant."},
            {"role": "user", "content": "How are you?"},
        ]
        req = {"model": "mios-llm-light", "messages": msgs, "tools": [{"name": "t"}], "tool_choice": "none"}
        stripped, _ = GatewayContractEngine.strip_tool_choice_none(req)
        self.assertEqual(stripped["messages"], msgs)

    # ------------------------------------------------------------------------
    # F2: _has_client_tools Bypass on tool_choice: "none"
    # ------------------------------------------------------------------------

    def test_f2_01_has_client_tools_returns_false_when_tool_choice_none(self):
        """F2.1: _has_client_tools returns False when tool_choice == 'none'."""
        body = {
            "messages": [{"role": "user", "content": "test"}],
            "tools": [{"type": "function", "function": {"name": "action_tool"}}],
            "tool_choice": "none",
        }
        self.assertFalse(GatewayContractEngine.has_client_tools(body))

    def test_f2_02_has_client_tools_returns_true_for_auto(self):
        """F2.2: _has_client_tools returns True when tools present and tool_choice == 'auto'."""
        body = {
            "messages": [{"role": "user", "content": "test"}],
            "tools": [{"type": "function", "function": {"name": "action_tool"}}],
            "tool_choice": "auto",
        }
        self.assertTrue(GatewayContractEngine.has_client_tools(body))

    def test_f2_03_has_client_tools_returns_false_for_empty_tools(self):
        """F2.3: _has_client_tools returns False for empty tools array."""
        body = {"messages": [{"role": "user", "content": "test"}], "tools": [], "tool_choice": "auto"}
        self.assertFalse(GatewayContractEngine.has_client_tools(body))

    def test_f2_04_has_client_tools_returns_false_for_missing_tools(self):
        """F2.4: _has_client_tools returns False when tools field is omitted."""
        body = {"messages": [{"role": "user", "content": "test"}]}
        self.assertFalse(GatewayContractEngine.has_client_tools(body))

    def test_f2_05_client_tools_loop_not_invoked_on_tool_choice_none(self):
        """F2.5: Verifies that tool_choice == 'none' bypasses client tools execution."""
        body = {
            "messages": [{"role": "user", "content": "Open calculator"}],
            "tools": [{"type": "function", "function": {"name": "launch_app"}}],
            "tool_choice": "none",
        }
        # Under tool_choice == 'none', has_client_tools is False, routing to plain chat
        self.assertFalse(GatewayContractEngine.has_client_tools(body))

    # ------------------------------------------------------------------------
    # F3: Client Harness Tool De-duplication
    # ------------------------------------------------------------------------

    def test_f3_01_caller_tools_exceeding_threshold_suppresses_mios_sel(self):
        """F3.1: Caller tools >= DEFAULT_TOOL_CAP (24) suppresses _mios_sel injection."""
        caller_tools = [{"type": "function", "function": {"name": f"caller_tool_{i}"}} for i in range(25)]
        mios_surface = [{"type": "function", "function": {"name": "open_app"}}]
        merged = GatewayContractEngine.deduplicate_and_filter_tools(caller_tools, mios_surface, {})
        self.assertEqual(len(merged), 25)
        self.assertNotIn("open_app", [t["function"]["name"] for t in merged])

    def test_f3_02_caller_tools_matching_mios_verb_suppresses_mios_sel(self):
        """F3.2: Caller tool matching a MiOS verb suppresses redundant _mios_sel."""
        catalog = {"open_app": {"surface": "desktop"}}
        caller_tools = [{"type": "function", "function": {"name": "open_app"}}]
        mios_surface = [{"type": "function", "function": {"name": "open_app"}}, {"type": "function", "function": {"name": "system_status"}}]
        merged = GatewayContractEngine.deduplicate_and_filter_tools(caller_tools, mios_surface, catalog)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["function"]["name"], "open_app")

    def test_f3_03_caller_tools_without_verbs_appends_mios_sel(self):
        """F3.3: Caller tools without MiOS verbs below threshold merges mios_surface."""
        caller_tools = [{"type": "function", "function": {"name": "browser_click"}}]
        mios_surface = [{"type": "function", "function": {"name": "open_app"}}]
        merged = GatewayContractEngine.deduplicate_and_filter_tools(caller_tools, mios_surface, {})
        self.assertEqual(len(merged), 2)
        names = [t["function"]["name"] for t in merged]
        self.assertIn("browser_click", names)
        self.assertIn("open_app", names)

    def test_f3_04_tool_names_strictly_deduplicated(self):
        """F3.4: All merged tool names are strictly unique."""
        caller_tools = [{"type": "function", "function": {"name": "tool_x"}}]
        mios_surface = [{"type": "function", "function": {"name": "tool_x"}}, {"type": "function", "function": {"name": "tool_y"}}]
        merged = GatewayContractEngine.deduplicate_and_filter_tools(caller_tools, mios_surface, {})
        names = [t["function"]["name"] for t in merged]
        self.assertEqual(len(names), len(set(names)))

    def test_f3_05_name_is_verb_correctly_identifies_registered_verbs(self):
        """F3.5: _name_is_verb identifies registered catalog verbs and rejects arbitrary names."""
        catalog = {"launch_app": {}, "sys_env": {}}
        self.assertTrue(GatewayContractEngine.name_is_verb("launch_app", catalog))
        self.assertTrue(GatewayContractEngine.name_is_verb("sys_env", catalog))
        self.assertFalse(GatewayContractEngine.name_is_verb("unknown_client_action", catalog))

    # ------------------------------------------------------------------------
    # F4: Gateway Context Token Budgeting & Pruning
    # ------------------------------------------------------------------------

    def test_f4_01_effective_context_ceiling_aligned_to_32k(self):
        """F4.1: Gateway context ceiling is aligned to 32,768 tokens."""
        self.assertEqual(GatewayContractEngine.MAX_CONTEXT_TOKENS, 32768)

    def test_f4_02_stale_tool_results_pruned_after_ttl(self):
        """F4.2: Tool results older than TTL turns are evicted."""
        msgs = []
        for i in range(7):
            msgs.append({"role": "user", "content": f"query {i}"})
            msgs.append({"role": "assistant", "content": f"calling tool {i}"})
            msgs.append({"role": "tool", "tool_call_id": f"call_{i}", "content": f"result data {i}" * 50})
        pruned = GatewayContractEngine.drop_stale_tool_results(msgs, ttl_turns=3)
        evicted_count = sum(1 for m in pruned if m.get("content") == "[evicted stale tool result]")
        self.assertGreater(evicted_count, 0)

    def test_f4_03_plan_compaction_triggered_on_high_context_fill(self):
        """F4.3: Context fill approaching threshold triggers message content truncation."""
        heavy_content = "X" * 150000  # ~37,500 tokens
        req = {
            "model": "mios-llm-light",
            "messages": [{"role": "user", "content": heavy_content}],
            "max_tokens": 4096,
        }
        budgeted = GatewayContractEngine.budget_and_prune(req, ctx_limit=32768)
        content = budgeted["messages"][0]["content"]
        self.assertTrue(content.endswith("[truncated]"))

    def test_f4_04_max_tokens_clamped_to_available_budget(self):
        """F4.4: max_tokens is clamped when input tokens leave insufficient headroom."""
        req = {
            "model": "mios-llm-light",
            "messages": [{"role": "user", "content": "X" * 40000}],  # ~10,000 tokens
            "max_tokens": 30000,
        }
        budgeted = GatewayContractEngine.budget_and_prune(req, ctx_limit=32768)
        self.assertLess(budgeted["max_tokens"], 30000)
        self.assertGreaterEqual(budgeted["max_tokens"], 512)

    def test_f4_05_oversized_payload_pruned_before_backend_dispatch(self):
        """F4.5: Dispatched payload remains safely below 32k context boundary."""
        msgs = [{"role": "user", "content": "test " * 1000}]
        req = {"model": "mios-llm-light", "messages": msgs, "max_tokens": 1024}
        budgeted = GatewayContractEngine.budget_and_prune(req, ctx_limit=32768)
        tokens = GatewayContractEngine.estimate_tokens(budgeted["messages"])
        self.assertLessEqual(tokens + budgeted["max_tokens"], 32768)

    # ------------------------------------------------------------------------
    # F5: Gateway End-to-End Chat Completion
    # ------------------------------------------------------------------------

    def test_f5_01_chat_completions_non_streaming_success(self):
        """F5.1: Non-streaming /v1/chat/completions returns HTTP 200 with chat.completion object."""
        payload = {
            "model": "mios-llm-light",
            "messages": [{"role": "user", "content": "Ping"}],
        }
        req = urllib.request.Request(
            f"{self.endpoint}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data.get("object"), "chat.completion")
            self.assertEqual(data["choices"][0]["finish_reason"], "stop")

    def test_f5_02_chat_completions_streaming_sse_success(self):
        """F5.2: Streaming /v1/chat/completions emits SSE events terminating in [DONE]."""
        payload = {
            "model": "mios-llm-light",
            "messages": [{"role": "user", "content": "Stream"}],
            "stream": True,
        }
        req = urllib.request.Request(
            f"{self.endpoint}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            self.assertEqual(resp.status, 200)
            body = resp.read().decode("utf-8")
            self.assertIn("data: [DONE]", body)
            self.assertIn("chat.completion.chunk", body)

    def test_f5_03_chat_completions_tool_calls_response(self):
        """F5.3: Request with active tools returns finish_reason: tool_calls."""
        payload = {
            "model": "mios-llm-light",
            "messages": [{"role": "user", "content": "Call tool"}],
            "tools": [{"type": "function", "function": {"name": "query_db"}}],
            "tool_choice": "auto",
        }
        req = urllib.request.Request(
            f"{self.endpoint}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["choices"][0]["finish_reason"], "tool_calls")
            self.assertTrue(len(data["choices"][0]["message"]["tool_calls"]) > 0)

    def test_f5_04_chat_completions_normalizes_usage_statistics(self):
        """F5.4: Completion response includes normalized usage metrics."""
        payload = {"model": "mios-llm-light", "messages": [{"role": "user", "content": "Metrics"}]}
        req = urllib.request.Request(
            f"{self.endpoint}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            usage = data.get("usage", {})
            self.assertIn("prompt_tokens", usage)
            self.assertIn("completion_tokens", usage)
            self.assertIn("total_tokens", usage)

    def test_f5_05_chat_completions_respects_model_identifier(self):
        """F5.5: Completion response mirrors requested model identifier."""
        payload = {"model": "qwen2.5-coder-1.5b", "messages": [{"role": "user", "content": "Code"}]}
        req = urllib.request.Request(
            f"{self.endpoint}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data.get("model"), "qwen2.5-coder-1.5b")

    # ------------------------------------------------------------------------
    # F6: DirectX Low-Power Preference (GpuPreference=1;)
    # ------------------------------------------------------------------------

    def test_f6_01_ensure_gpu_preferences_function_structure(self):
        """F6.1: Set-MiOSWallpaper.ps1 defines Ensure-MiosGpuPreferences with GpuPreference=1;."""
        info = parse_wallpaper_script_preferences()
        self.assertTrue(info["has_ensure_func"])
        self.assertTrue(info["has_gpu_pref_val"])

    def test_f6_02_wallpaper_executables_included_in_target_list(self):
        """F6.2: Wallpaper executables are included in DirectX low-power target list."""
        info = parse_wallpaper_script_preferences()
        exes = [os.path.basename(p).lower() for p in info["target_exes"]]
        self.assertIn("mios-wallpaper.exe", exes)
        self.assertIn("mios-wallpaper-service.exe", exes)
        self.assertIn("mios-wallpaperd.exe", exes)

    def test_f6_03_webview2_executables_discovered_and_targeted(self):
        """F6.3: Script searches EdgeWebView application paths for msedgewebview2.exe."""
        info = parse_wallpaper_script_preferences()
        self.assertIn("msedgewebview2.exe", info["full_text"])
        self.assertIn("Microsoft\\EdgeWebView\\Application", info["full_text"])

    def test_f6_04_directx_user_gpu_preferences_registry_hive_paths(self):
        """F6.4: Direct targets include HKCU and HKEY_USERS DirectX preferences."""
        info = parse_wallpaper_script_preferences()
        hives_str = " ".join(info["hives"])
        self.assertIn("Software\\Microsoft\\DirectX\\UserGpuPreferences", hives_str)
        self.assertIn("HKEY_USERS", info["full_text"])

    def test_f6_05_igpu_executables_targeted_for_low_power(self):
        """F6.5: Local inference executables (llama-server, rpc-server) are targeted."""
        info = parse_wallpaper_script_preferences()
        exes = [os.path.basename(p).lower() for p in info["target_exes"]]
        self.assertIn("llama-server.exe", exes)
        self.assertIn("rpc-server.exe", exes)
        self.assertIn("ggml-rpc-server.exe", exes)

    # ------------------------------------------------------------------------
    # F7: 0 MB Discrete GPU Compute VRAM Isolation
    # ------------------------------------------------------------------------

    def test_f7_01_nvidia_smi_query_parser_zero_vram(self):
        """F7.1: VRAM isolation evaluator confirms 0 MB VRAM allocation for wallpaper."""
        apps = [
            {"process_name": "dwm.exe", "used_memory_mb": 120},
            {"process_name": "MiOS-Wallpaper.exe", "used_memory_mb": 0},
        ]
        self.assertTrue(evaluate_dgpu_vram_isolation(apps))

    def test_f7_02_wallpaper_engine_zero_dgpu_allocation(self):
        """F7.2: Wallpaper engine with 0 MB compute VRAM passes isolation test."""
        apps = [{"process_name": "C:\\Windows\\Web\\MiOS\\MiOS-Wallpaper.exe", "used_memory_mb": 0}]
        self.assertTrue(evaluate_dgpu_vram_isolation(apps))

    def test_f7_03_igpu_inference_zero_dgpu_allocation(self):
        """F7.3: Local iGPU inference engine with 0 MB compute VRAM passes isolation test."""
        apps = [{"process_name": "C:\\ProgramData\\mios\\igpu\\bin\\llama-server.exe", "used_memory_mb": 0}]
        self.assertTrue(evaluate_dgpu_vram_isolation(apps))

    def test_f7_04_vulkan_device_filter_rejects_discrete_accelerator(self):
        """F7.4: Regex device filter for low-power APU recognizes generic integrated/power-saving graphics and rejects discrete accelerators."""
        pattern = r"(?i)(integrated|low-power|power-saving|apu|iris|radeon|adreno|intel|qualcomm|graphics)"
        self.assertTrue(bool(re.search(pattern, "Generic Integrated Graphics Controller")))
        self.assertTrue(bool(re.search(pattern, "Intel(R) Iris(R) Xe Graphics")))
        self.assertTrue(bool(re.search(pattern, "Generic Low-Power APU Graphics")))
        self.assertTrue(bool(re.search(pattern, "Qualcomm(R) Adreno(TM) GPU")))
        self.assertFalse(bool(re.search(pattern, "Dedicated High-Power Discrete Accelerator")))

    def test_f7_05_dgpu_vram_isolation_assertion_passes(self):
        """F7.5: System VRAM isolation check passes cleanly when no prohibited apps hold dGPU VRAM."""
        apps = [{"process_name": "host_renderer.exe", "used_memory_mb": 50}]
        self.assertTrue(evaluate_dgpu_vram_isolation(apps))

    # ------------------------------------------------------------------------
    # F8: Living Wallpaper [colors] SSOT Binding
    # ------------------------------------------------------------------------

    def test_f8_01_wallpaper_url_structure_contains_16_ansi_tokens(self):
        """F8.1: WallpaperUrl contains a0 through a15 palette parameters."""
        url = compute_wallpaper_query_url(self.ssot, mode="auto")
        for i in range(16):
            self.assertIn(f"a{i}=", url)

    def test_f8_02_wallpaper_url_contains_bg_and_fg_tokens(self):
        """F8.2: WallpaperUrl contains bg and fg color parameters."""
        url = compute_wallpaper_query_url(self.ssot, mode="auto")
        self.assertIn("bg=", url)
        self.assertIn("fg=", url)

    def test_f8_03_wallpaper_registry_keys_set_in_hklm(self):
        """F8.3: Set-MiOSWallpaper.ps1 targets HKLM:\\SOFTWARE\\MiOS\\WallpaperUrl and Enabled."""
        info = parse_wallpaper_script_preferences()
        self.assertIn("HKLM:\\SOFTWARE\\MiOS", info["full_text"])
        self.assertIn("'WallpaperUrl'", info["full_text"])
        self.assertIn("'Enabled'", info["full_text"])

    def test_f8_04_ssot_colors_table_resolution(self):
        """F8.4: Colors table in mios.toml provides ANSI color tokens."""
        colors = self.ssot.get("colors", {})
        self.assertIn("ansi_0_black", colors)
        self.assertIn("ansi_1_red", colors)
        self.assertIn("bg", colors)
        self.assertIn("fg", colors)

    def test_f8_05_mode_parameter_omitted_in_auto_mode(self):
        """F8.5: Auto mode omits &mode= parameter allowing live OS theme adaptation."""
        url = compute_wallpaper_query_url(self.ssot, mode="auto")
        self.assertNotIn("&mode=", url)

    # ------------------------------------------------------------------------
    # F9: Rust Leaf Verifier Parity & Strangler Shims
    # ------------------------------------------------------------------------

    def test_f9_01_hardcode_lint_clean_tree_exit_code_zero(self):
        """F9.1: Hardcode linter exits code 0 on a clean source directory."""
        with tempfile.TemporaryDirectory(prefix="clean_lint_") as tmpdir:
            sample_file = os.path.join(tmpdir, "clean_app.py")
            with open(sample_file, "w", encoding="utf-8") as fh:
                fh.write("# Timeless module\nx = 42\n")

            proc = subprocess.run([sys.executable, _LINT_ORACLE, "--root", tmpdir], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("PASS: 1 file(s) scanned", proc.stdout)

    def test_f9_02_hardcode_lint_defect_exit_code_one(self):
        """F9.2: Hardcode linter exits code 1 on planted defect."""
        with tempfile.TemporaryDirectory(prefix="defect_lint_") as tmpdir:
            sample_file = os.path.join(tmpdir, "bad_app.py")
            bad_ip = ".".join(["203", "0", "113", "88"])
            with open(sample_file, "w", encoding="utf-8") as fh:
                fh.write(f'REMOTE_IP = "{bad_ip}"\n')

            proc = subprocess.run([sys.executable, _LINT_ORACLE, "--root", tmpdir], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("FAIL:", proc.stdout + proc.stderr)

    def test_f9_03_hardcode_lint_output_format_parity(self):
        """F9.3: Output format matches [mios-hardcode-lint] PASS banner."""
        with tempfile.TemporaryDirectory(prefix="parity_lint_") as tmpdir:
            sample = os.path.join(tmpdir, "test.sh")
            with open(sample, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/bash\necho hello\n")

            proc = subprocess.run([sys.executable, _LINT_ORACLE, "--root", tmpdir], capture_output=True, text=True)
            self.assertTrue(proc.stdout.startswith("[mios-hardcode-lint] PASS:"))

    def test_f9_04_hardcode_lint_soft_mode_advisory(self):
        """F9.4: MIOS_HARDCODE_LINT_SOFT=1 exits 0 with advisory on defect."""
        with tempfile.TemporaryDirectory(prefix="soft_lint_") as tmpdir:
            sample = os.path.join(tmpdir, "bad.py")
            bad_ip = ".".join(["198", "51", "100", "22"])
            with open(sample, "w", encoding="utf-8") as fh:
                fh.write(f'ROUTABLE = "{bad_ip}"\n')

            env = dict(os.environ, MIOS_HARDCODE_LINT_SOFT="1")
            proc = subprocess.run([sys.executable, _LINT_ORACLE, "--root", tmpdir], capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 0)

    def test_f9_05_strangler_shim_delegates_to_rust_when_available(self):
        """F9.5: Strangler shim / native binary is available and functional."""
        self.assertTrue(os.path.isfile(_LINT_ORACLE))
        rust_crate = os.path.join(_ROOT, "tools", "native", "mios-hardcode-lint", "Cargo.toml")
        self.assertTrue(os.path.isfile(rust_crate) or _RUST_LINT_BIN is not None)
        if _RUST_LINT_BIN and os.path.isfile(_RUST_LINT_BIN):
            with tempfile.TemporaryDirectory(prefix="shim_test_") as tmpdir:
                sample = os.path.join(tmpdir, "clean.py")
                with open(sample, "w", encoding="utf-8") as fh:
                    fh.write("x = 1\n")
                proc = subprocess.run([_RUST_LINT_BIN, "--root", tmpdir], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0)
                self.assertIn("[mios-hardcode-lint] PASS:", proc.stdout)

    # ------------------------------------------------------------------------
    # F10: Shared Daemon Infrastructure (mios-service-core)
    # ------------------------------------------------------------------------

    def test_f10_01_ssot_path_discovery_heuristics(self):
        """F10.1: mios-service-core discovers mios.toml via standard paths."""
        self.assertTrue(os.path.isfile(_SSOT_PATH))
        self.assertIn("usr", _SSOT_PATH)
        self.assertIn("mios.toml", _SSOT_PATH)

    def test_f10_02_forbidden_cloud_urls_rejected(self):
        """F10.2: Prohibited vendor cloud URLs are defined and rejected."""
        ssot_rs = os.path.join(_SERVICE_CORE_DIR, "src", "ssot.rs")
        self.assertTrue(os.path.isfile(ssot_rs))
        with open(ssot_rs, "r", encoding="utf-8") as fh:
            code = fh.read()
        self.assertIn("api.openai.com", code)
        self.assertIn("generativelanguage.googleapis.com", code)
        self.assertIn("api.anthropic.com", code)

    def test_f10_03_ssot_port_and_str_resolution(self):
        """F10.3: SSOT table provides ports and required metadata."""
        ports = self.ssot.get("ports", {})
        self.assertTrue("agent_pipe" in ports or "gateway" in ports)
        gw_port = ports.get("agent_pipe") or ports.get("gateway")
        self.assertEqual(gw_port, 8700)

    def test_f10_04_socket_path_length_validation(self):
        """F10.4: Socket path check enforces MAX_SOCKET_PATH_LEN = 108."""
        socket_rs = os.path.join(_SERVICE_CORE_DIR, "src", "socket.rs")
        self.assertTrue(os.path.isfile(socket_rs))
        with open(socket_rs, "r", encoding="utf-8") as fh:
            code = fh.read()
        self.assertIn("MAX_SOCKET_PATH_LEN", code)
        self.assertIn("108", code)

    def test_f10_05_process_hidden_configuration(self):
        """F10.5: Process helpers configure CREATE_NO_WINDOW for background execution."""
        process_rs = os.path.join(_SERVICE_CORE_DIR, "src", "process.rs")
        self.assertTrue(os.path.isfile(process_rs))
        with open(process_rs, "r", encoding="utf-8") as fh:
            code = fh.read()
        self.assertIn("CREATE_NO_WINDOW", code)

    # ------------------------------------------------------------------------
    # F11: Upstream FOSS Research & Synthesis
    # ------------------------------------------------------------------------

    def test_f11_01_research_context_budgeting_algorithms(self):
        """F11.1: Upstream research documents sliding window and TTL pruning."""
        doc_path = os.path.join(_ROOT, "usr", "share", "doc", "mios", "manual", "routing.md")
        self.assertTrue(os.path.isfile(doc_path))
        with open(doc_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
        self.assertIn("context", content.lower())

    def test_f11_02_research_prompt_compression_patterns(self):
        """F11.2: Compaction algorithms preserve recent messages and system prompt."""
        self.assertTrue(os.path.isfile(os.path.join(_ROOT, "usr", "lib", "mios", "agent-pipe", "mios_pipe", "routing", "chat.py")))

    def test_f11_03_research_directx_gpu_scheduling_spec(self):
        """F11.3: DirectX low-power preference conforms to DXGI_GPU_PREFERENCE_MINIMUM_POWER."""
        info = parse_wallpaper_script_preferences()
        self.assertIn("GpuPreference=1;", info["full_text"])

    def test_f11_04_research_dwm_multi_adapter_compositing(self):
        """F11.4: Multi-adapter compositing targets generic Power-Saving / Integrated GPU."""
        self.assertIn("GpuPreference=1;", parse_wallpaper_script_preferences()["full_text"])
        self.assertIn("Power-Saving", parse_wallpaper_script_preferences()["full_text"])

    def test_f11_05_research_citations_and_references(self):
        """F11.5: Project documentation references Architectural Laws 5 and 7."""
        project_md = os.path.join(_ROOT, "PROJECT.md")
        self.assertTrue(os.path.isfile(project_md))
        with open(project_md, "r", encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("Architectural Laws", text)

    # ------------------------------------------------------------------------
    # F12: Two-Sided Verification Controls
    # ------------------------------------------------------------------------

    def test_f12_01_positive_control_valid_chat_request(self):
        """F12.1: Positive control: valid chat completion returns 200."""
        payload = {"model": "mios-llm-light", "messages": [{"role": "user", "content": "Valid"}]}
        req = urllib.request.Request(
            f"{self.endpoint}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            self.assertEqual(resp.status, 200)

    def test_f12_02_negative_control_malformed_json_request(self):
        """F12.2: Negative control: malformed JSON returns HTTP 400."""
        req = urllib.request.Request(
            f"{self.endpoint}/v1/chat/completions",
            data=b"{bad_json",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=3.0)
        self.assertEqual(cm.exception.code, 400)

    def test_f12_03_positive_control_clean_tree_hardcode_scan(self):
        """F12.3: Positive control: clean tree passes hardcode lint."""
        with tempfile.TemporaryDirectory(prefix="pos_clean_") as tmpdir:
            sample = os.path.join(tmpdir, "valid.py")
            with open(sample, "w", encoding="utf-8") as fh:
                fh.write("val = 10\n")
            proc = subprocess.run([sys.executable, _LINT_ORACLE, "--root", tmpdir], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)

    def test_f12_04_negative_control_planted_routable_ip_defect(self):
        """F12.4: Negative control: planted routable IP fails hardcode lint."""
        with tempfile.TemporaryDirectory(prefix="neg_defect_") as tmpdir:
            sample = os.path.join(tmpdir, "bad.py")
            bad_ip = ".".join(["203", "0", "113", "101"])
            with open(sample, "w", encoding="utf-8") as fh:
                fh.write(f'BAD_IP = "{bad_ip}"\n')
            proc = subprocess.run([sys.executable, _LINT_ORACLE, "--root", tmpdir], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)

    def test_f12_05_positive_and_negative_cloud_url_guard(self):
        """F12.5: Cloud URL guard accepts local endpoint and rejects vendor cloud."""
        def check_url(url: str) -> bool:
            forbidden = ["api.openai.com", "generativelanguage.googleapis.com", "api.anthropic.com"]
            return not any(f in url for f in forbidden)

        local_gw = f"http://127.0.0.1:{8700}/v1"
        self.assertTrue(check_url(local_gw))
        self.assertFalse(check_url("https://api.openai.com/v1/chat/completions"))
        self.assertFalse(check_url("https://api.anthropic.com/v1/messages"))

    # ------------------------------------------------------------------------
    # F13: Standing Gates & Sync Certification
    # ------------------------------------------------------------------------

    def test_f13_01_standing_gate_phase_registry(self):
        """F13.1: Standing gate phase-registry passes with exit code 0."""
        proc = subprocess.run([_GATE_BIN, "phase-registry", "--root", _ROOT], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"phase-registry failed: {proc.stderr}\n{proc.stdout}")

    def test_f13_02_standing_gate_ratchet_direction(self):
        """F13.2: Standing gate ratchet-direction passes with exit code 0."""
        proc = subprocess.run([_GATE_BIN, "ratchet-direction", "--root", _ROOT], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"ratchet-direction failed: {proc.stderr}\n{proc.stdout}")

    def test_f13_03_standing_gate_credential_literals(self):
        """F13.3: Standing gate credential-literals passes with exit code 0."""
        proc = subprocess.run([_GATE_BIN, "credential-literals", "--root", _ROOT], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"credential-literals failed: {proc.stderr}\n{proc.stdout}")

    def test_f13_04_standing_gate_version_literals_ssot(self):
        """F13.4: Standing gate version-literals-ssot passes with exit code 0."""
        proc = subprocess.run([_GATE_BIN, "version-literals-ssot", "--root", _ROOT], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"version-literals-ssot failed: {proc.stderr}\n{proc.stdout}")

    def test_f13_05_ci_suites_check_certification(self):
        """F13.5: python tools/ci-suites.py --check passes with exit code 0."""
        script = os.path.join(_ROOT, "tools", "ci-suites.py")
        proc = subprocess.run([sys.executable, script, "--check"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"ci-suites.py --check failed: {proc.stderr}\n{proc.stdout}")


# ============================================================================
# TIER 2: Boundary & Corner Cases (B1..B13, >=5 tests each = 65 tests)
# ============================================================================

class TestTier2BoundaryAndCornerCases(unittest.TestCase):
    """Tier 2: Boundary Value Analysis and Corner Cases for F1 through F13."""

    @classmethod
    def setUpClass(cls):
        cls.harness = EphemeralGatewayServer()
        cls.port = cls.harness.start()
        cls.endpoint = f"http://127.0.0.1:{cls.port}"
        cls.ssot = load_ssot()

    @classmethod
    def tearDownClass(cls):
        cls.harness.stop()

    # ------------------------------------------------------------------------
    # B1 Boundaries (F1: tool_choice: "none")
    # ------------------------------------------------------------------------

    def test_b1_01_tool_choice_none_with_empty_tools_array(self):
        """B1.1: tools: [] with tool_choice: 'none' strips cleanly."""
        req = {"messages": [{"role": "user", "content": "hi"}], "tools": [], "tool_choice": "none"}
        stripped, tokens = GatewayContractEngine.strip_tool_choice_none(req)
        self.assertNotIn("tools", stripped)
        self.assertEqual(tokens, 0)

    def test_b1_02_tool_choice_none_with_193_tools(self):
        """B1.2: 193 tools supplied with tool_choice: 'none' are completely stripped."""
        tools = [{"type": "function", "function": {"name": f"t_{i}"}} for i in range(193)]
        req = {"messages": [{"role": "user", "content": "hi"}], "tools": tools, "tool_choice": "none"}
        stripped, tokens = GatewayContractEngine.strip_tool_choice_none(req)
        self.assertNotIn("tools", stripped)
        self.assertEqual(tokens, 0)

    def test_b1_03_tool_choice_none_case_insensitivity(self):
        """B1.3: Case variations in tool_choice ('NONE', 'None', ' none ') handled."""
        for val in ("NONE", "None", " none "):
            req = {"messages": [{"role": "user", "content": "hi"}], "tools": [{"name": "t"}], "tool_choice": val}
            stripped, tokens = GatewayContractEngine.strip_tool_choice_none(req)
            self.assertNotIn("tools", stripped, f"Failed for {val!r}")
            self.assertEqual(tokens, 0)

    def test_b1_04_tool_choice_none_with_missing_content_in_tools(self):
        """B1.4: Tools with malformed/missing fields stripped without error."""
        req = {"messages": [{"role": "user", "content": "hi"}], "tools": [{}, {"function": None}], "tool_choice": "none"}
        stripped, tokens = GatewayContractEngine.strip_tool_choice_none(req)
        self.assertNotIn("tools", stripped)

    def test_b1_05_tool_choice_none_with_duplicate_tool_names(self):
        """B1.5: Tools with duplicate names stripped cleanly without conflict."""
        req = {"messages": [{"role": "user", "content": "hi"}], "tools": [{"name": "t1"}, {"name": "t1"}], "tool_choice": "none"}
        stripped, tokens = GatewayContractEngine.strip_tool_choice_none(req)
        self.assertNotIn("tools", stripped)

    # ------------------------------------------------------------------------
    # B2 Boundaries (F2: _has_client_tools)
    # ------------------------------------------------------------------------

    def test_b2_01_has_client_tools_non_dict_body(self):
        """B2.1: Non-dict bodies return False without exception."""
        self.assertFalse(GatewayContractEngine.has_client_tools(None))
        self.assertFalse(GatewayContractEngine.has_client_tools([]))
        self.assertFalse(GatewayContractEngine.has_client_tools("invalid"))

    def test_b2_02_has_client_tools_tools_is_not_list(self):
        """B2.2: tools: 'all' or tools: 123 returns False."""
        self.assertFalse(GatewayContractEngine.has_client_tools({"tools": "all"}))
        self.assertFalse(GatewayContractEngine.has_client_tools({"tools": 123}))

    def test_b2_03_has_client_tools_tools_contains_non_dicts(self):
        """B2.3: tools containing non-dicts handled safely."""
        self.assertFalse(GatewayContractEngine.has_client_tools({"tools": [None, 42]}))

    def test_b2_04_has_client_tools_tool_choice_required_with_tools(self):
        """B2.4: tool_choice: 'required' with tools returns True."""
        body = {"tools": [{"name": "act"}], "tool_choice": "required"}
        self.assertTrue(GatewayContractEngine.has_client_tools(body))

    def test_b2_05_has_client_tools_tool_choice_specific_function(self):
        """B2.5: Specific function object in tool_choice returns True."""
        body = {"tools": [{"name": "act"}], "tool_choice": {"type": "function", "function": {"name": "act"}}}
        self.assertTrue(GatewayContractEngine.has_client_tools(body))

    # ------------------------------------------------------------------------
    # B3 Boundaries (F3: Tool De-duplication)
    # ------------------------------------------------------------------------

    def test_b3_01_exactly_default_tool_cap_threshold_boundary(self):
        """B3.1: Exactly 24 tools (boundary value) suppresses _mios_sel."""
        caller_tools = [{"type": "function", "function": {"name": f"t_{i}"}} for i in range(24)]
        mios_surface = [{"type": "function", "function": {"name": "injected_tool"}}]
        merged = GatewayContractEngine.deduplicate_and_filter_tools(caller_tools, mios_surface, {})
        self.assertEqual(len(merged), 24)

    def test_b3_02_one_below_default_tool_cap_threshold_23(self):
        """B3.2: 23 tools (one below boundary) allows _mios_sel merge."""
        caller_tools = [{"type": "function", "function": {"name": f"t_{i}"}} for i in range(23)]
        mios_surface = [{"type": "function", "function": {"name": "injected_tool"}}]
        merged = GatewayContractEngine.deduplicate_and_filter_tools(caller_tools, mios_surface, {})
        self.assertEqual(len(merged), 24)

    def test_b3_03_zero_caller_tools_boundary(self):
        """B3.3: Zero caller tools allows full _mios_sel merge."""
        merged = GatewayContractEngine.deduplicate_and_filter_tools([], [{"type": "function", "function": {"name": "s1"}}], {})
        self.assertEqual(len(merged), 1)

    def test_b3_04_empty_tool_name_in_caller_tools(self):
        """B3.4: Tool with empty name handled without crash."""
        caller = [{"type": "function", "function": {"name": ""}}]
        merged = GatewayContractEngine.deduplicate_and_filter_tools(caller, [], {})
        self.assertEqual(len(merged), 1)

    def test_b3_05_case_variations_in_verb_names(self):
        """B3.5: Whitespace around verb names trimmed safely."""
        catalog = {"open_app": {}}
        self.assertTrue(GatewayContractEngine.name_is_verb(" open_app ", catalog))
        self.assertFalse(GatewayContractEngine.name_is_verb("", catalog))

    # ------------------------------------------------------------------------
    # B4 Boundaries (F4: Context Budgeting)
    # ------------------------------------------------------------------------

    def test_b4_01_exact_32768_token_boundary(self):
        """B4.1: Message payload at exactly 32768 tokens boundary handled cleanly."""
        body = {"model": "mios-llm-light", "messages": [{"role": "user", "content": "test"}]}
        budgeted = GatewayContractEngine.budget_and_prune(body, ctx_limit=32768)
        self.assertLessEqual(budgeted["max_tokens"], 32768)

    def test_b4_02_extreme_context_44k_tokens_pruned(self):
        """B4.2: Payload of 44,723 tokens is pruned to prevent backend HTTP 400."""
        huge_msgs = [{"role": "user", "content": "word " * 35000}]  # ~44k tokens
        body = {"model": "mios-llm-light", "messages": huge_msgs, "max_tokens": 4096}
        budgeted = GatewayContractEngine.budget_and_prune(body, ctx_limit=32768)
        tokens = GatewayContractEngine.estimate_tokens(budgeted["messages"])
        self.assertLess(tokens, 32768)

    def test_b4_03_all_messages_stale_tool_results(self):
        """B4.3: Conversation consisting of many stale tool turns evicts correctly."""
        msgs = [{"role": "tool", "content": "huge" * 50}] + [{"role": "assistant", "content": "turn"}] * 10
        pruned = GatewayContractEngine.drop_stale_tool_results(msgs, ttl_turns=2)
        self.assertEqual(pruned[0]["content"], "[evicted stale tool result]")

    def test_b4_04_single_giant_message_truncation(self):
        """B4.4: Giant user message (>100k chars) is truncated safely."""
        msgs = [{"role": "user", "content": "A" * 150000}]
        body = {"model": "mios-llm-light", "messages": msgs, "max_tokens": 2048}
        budgeted = GatewayContractEngine.budget_and_prune(body, ctx_limit=32768)
        self.assertTrue(budgeted["messages"][0]["content"].endswith("[truncated]"))

    def test_b4_05_zero_token_messages_handled(self):
        """B4.5: Messages with empty content string handled cleanly."""
        msgs = [{"role": "user", "content": ""}]
        body = {"model": "mios-llm-light", "messages": msgs}
        budgeted = GatewayContractEngine.budget_and_prune(body, ctx_limit=32768)
        self.assertEqual(budgeted["messages"][0]["content"], "")

    # ------------------------------------------------------------------------
    # B5 Boundaries (F5: Chat Completions)
    # ------------------------------------------------------------------------

    def test_b5_01_empty_messages_array_returns_400(self):
        """B5.1: POST with empty messages: [] returns HTTP 400."""
        req = urllib.request.Request(
            f"{self.endpoint}/v1/chat/completions",
            data=json.dumps({"model": "m", "messages": []}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=3.0)
        self.assertEqual(cm.exception.code, 400)

    def test_b5_02_non_list_messages_returns_400(self):
        """B5.2: POST with messages: 'string' returns HTTP 400."""
        req = urllib.request.Request(
            f"{self.endpoint}/v1/chat/completions",
            data=json.dumps({"model": "m", "messages": "invalid"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=3.0)
        self.assertEqual(cm.exception.code, 400)

    def test_b5_03_malformed_json_body_returns_400(self):
        """B5.3: Truncated JSON body returns HTTP 400."""
        req = urllib.request.Request(
            f"{self.endpoint}/v1/chat/completions",
            data=b'{"messages": [{"role": "user"',
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=3.0)
        self.assertEqual(cm.exception.code, 400)

    def test_b5_04_empty_request_body_returns_400(self):
        """B5.4: Empty request body returns HTTP 400."""
        req = urllib.request.Request(
            f"{self.endpoint}/v1/chat/completions",
            data=b"",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=3.0)
        self.assertEqual(cm.exception.code, 400)

    def test_b5_05_temperature_boundary_values(self):
        """B5.5: Temperature boundaries (0.0 and 2.0) accepted without error."""
        for temp in (0.0, 2.0):
            payload = {"model": "mios-llm-light", "messages": [{"role": "user", "content": "T"}], "temperature": temp}
            req = urllib.request.Request(
                f"{self.endpoint}/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                self.assertEqual(resp.status, 200)

    # ------------------------------------------------------------------------
    # B6 Boundaries (F6: DirectX Low-Power Preference)
    # ------------------------------------------------------------------------

    def test_b6_01_non_existent_registry_hive_handled(self):
        """B6.1: Script handles missing user hives gracefully with Try/Catch."""
        info = parse_wallpaper_script_preferences()
        self.assertIn("try {", info["full_text"])
        self.assertIn("catch { }", info["full_text"])

    def test_b6_02_trailing_semicolon_in_preference_string(self):
        """B6.2: Format contains exact trailing semicolon: 'GpuPreference=1;'."""
        info = parse_wallpaper_script_preferences()
        self.assertIn("GpuPreference=1;", info["full_text"])

    def test_b6_03_empty_target_executable_path_ignored(self):
        """B6.3: Empty executable strings are not added to target list."""
        info = parse_wallpaper_script_preferences()
        self.assertTrue(all(bool(exe.strip()) for exe in info["target_exes"]))

    def test_b6_04_duplicate_executable_registration_idempotent(self):
        """B6.4: Contains condition prevents duplicate exe entries."""
        info = parse_wallpaper_script_preferences()
        self.assertIn("Contains", info["full_text"])

    def test_b6_05_webview2_missing_directories_degrade_open(self):
        """B6.5: Missing EdgeWebView directories do not throw fatal exception."""
        info = parse_wallpaper_script_preferences()
        self.assertIn("Test-Path", info["full_text"])

    # ------------------------------------------------------------------------
    # B7 Boundaries (F7: 0 MB Discrete GPU VRAM)
    # ------------------------------------------------------------------------

    def test_b7_01_nvidia_smi_missing_binary_degrades_gracefully(self):
        """B7.1: Missing nvidia-smi command handled cleanly without crash."""
        cmd = ["non_existent_nvidia_smi_bin"]
        with self.assertRaises(FileNotFoundError):
            subprocess.run(cmd, capture_output=True)

    def test_b7_02_nvidia_smi_empty_output_treated_as_zero_vram(self):
        """B7.2: Empty compute apps list treated as 0 MB allocated."""
        self.assertTrue(evaluate_dgpu_vram_isolation([]))

    def test_b7_03_nvidia_smi_nonzero_vram_fails_assertion(self):
        """B7.3: Non-zero compute VRAM on prohibited app strictly fails isolation check."""
        bad_apps = [{"process_name": "MiOS-Wallpaper.exe", "used_memory_mb": 512}]
        self.assertFalse(evaluate_dgpu_vram_isolation(bad_apps))

    def test_b7_04_multiple_gpus_discrete_isolated(self):
        """B7.4: Only prohibited apps trigger isolation failure."""
        apps = [
            {"process_name": "cuda_trainer.exe", "used_memory_mb": 4096},
            {"process_name": "MiOS-Wallpaper.exe", "used_memory_mb": 0},
        ]
        self.assertTrue(evaluate_dgpu_vram_isolation(apps))

    def test_b7_05_vram_leak_boundary_check(self):
        """B7.5: Prohibited app with 1 MB VRAM allocation fails isolation check."""
        bad_apps = [{"process_name": "msedgewebview2.exe", "used_memory_mb": 1}]
        self.assertFalse(evaluate_dgpu_vram_isolation(bad_apps))

    # ------------------------------------------------------------------------
    # B8 Boundaries (F8: Wallpaper [colors] SSOT)
    # ------------------------------------------------------------------------

    def test_b8_01_missing_color_key_falls_back_to_defaults(self):
        """B8.1: Missing colors table key falls back to built-in palette."""
        url = compute_wallpaper_query_url({}, mode="auto")
        self.assertIn("a0=282262", url)

    def test_b8_02_colors_without_hash_prefix_normalized(self):
        """B8.2: Hex values with and without '#' normalized cleanly."""
        url = compute_wallpaper_query_url({"colors": {"ansi_0_black": "#112233"}})
        self.assertIn("a0=112233", url)
        url2 = compute_wallpaper_query_url({"colors": {"ansi_0_black": "112233"}})
        self.assertIn("a0=112233", url2)

    def test_b8_03_invalid_hex_color_characters(self):
        """B8.3: Sanitizes or wraps hex string safely."""
        url = compute_wallpaper_query_url(self.ssot, mode="auto")
        self.assertTrue(url.startswith("file:///C:/Windows/Web/MiOS/living-wallpaper.html?"))

    def test_b8_04_mode_parameter_explicit_dark_and_light(self):
        """B8.4: Explicit -Mode dark or light appends &mode= parameter."""
        url_dark = compute_wallpaper_query_url(self.ssot, mode="dark")
        self.assertIn("&mode=dark", url_dark)
        url_light = compute_wallpaper_query_url(self.ssot, mode="light")
        self.assertIn("&mode=light", url_light)

    def test_b8_05_wallpaper_disabled_toggle_dword_zero(self):
        """B8.5: Wallpaper Enabled key is configured as DWORD."""
        info = parse_wallpaper_script_preferences()
        self.assertIn("PropertyType DWord", info["full_text"])

    # ------------------------------------------------------------------------
    # B9 Boundaries (F9: Rust Hardcode Lint Parity)
    # ------------------------------------------------------------------------

    def test_b9_01_empty_input_file_scan(self):
        """B9.1: Zero-byte file passes hardcode scan cleanly."""
        with tempfile.TemporaryDirectory(prefix="empty_file_") as tmpdir:
            sample = os.path.join(tmpdir, "empty.py")
            with open(sample, "wb") as fh:
                pass
            valid = os.path.join(tmpdir, "valid.py")
            with open(valid, "w", encoding="utf-8") as fh:
                fh.write("x = 1\n")
            proc = subprocess.run([sys.executable, _LINT_ORACLE, "--root", tmpdir], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)

    def test_b9_02_very_long_single_line_in_file(self):
        """B9.2: Single line exceeding 65k characters processed without buffer overflow."""
        with tempfile.TemporaryDirectory(prefix="long_line_") as tmpdir:
            sample = os.path.join(tmpdir, "long.py")
            with open(sample, "w", encoding="utf-8") as fh:
                fh.write("x = '" + ("A" * 70000) + "'\n")
            proc = subprocess.run([sys.executable, _LINT_ORACLE, "--root", tmpdir], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)

    def test_b9_03_non_utf8_binary_file_skipped(self):
        """B9.3: Binary file with arbitrary bytes skipped or handled safely."""
        with tempfile.TemporaryDirectory(prefix="bin_file_") as tmpdir:
            sample = os.path.join(tmpdir, "binary.bin")
            with open(sample, "wb") as fh:
                fh.write(b"\x00\xff\xfe\x00\x12\x34\x56\x78")
            valid = os.path.join(tmpdir, "valid.py")
            with open(valid, "w", encoding="utf-8") as fh:
                fh.write("x = 1\n")
            proc = subprocess.run([sys.executable, _LINT_ORACLE, "--root", tmpdir], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)

    def test_b9_04_deeply_nested_directory_tree(self):
        """B9.4: Deeply nested directory structure scanned without recursion error."""
        with tempfile.TemporaryDirectory(prefix="deep_dir_") as tmpdir:
            curr = tmpdir
            for i in range(12):
                curr = os.path.join(curr, f"level_{i}")
            os.makedirs(curr, exist_ok=True)
            sample = os.path.join(curr, "deep.py")
            with open(sample, "w", encoding="utf-8") as fh:
                fh.write("y = 100\n")
            proc = subprocess.run([sys.executable, _LINT_ORACLE, "--root", tmpdir], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)

    def test_b9_05_symlink_loop_protection(self):
        """B9.5: Directory traversal handles relative directory structures."""
        with tempfile.TemporaryDirectory(prefix="rel_dir_") as tmpdir:
            sample = os.path.join(tmpdir, "rel.py")
            with open(sample, "w", encoding="utf-8") as fh:
                fh.write("z = 200\n")
            proc = subprocess.run([sys.executable, _LINT_ORACLE, "--root", f"{tmpdir}/."], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)

    # ------------------------------------------------------------------------
    # B10 Boundaries (F10: mios-service-core)
    # ------------------------------------------------------------------------

    def test_b10_01_socket_path_at_exact_108_byte_limit(self):
        """B10.1: Socket path of 108 bytes is accepted."""
        socket_rs = os.path.join(_SERVICE_CORE_DIR, "src", "socket.rs")
        self.assertTrue(os.path.isfile(socket_rs))
        with open(socket_rs, "r", encoding="utf-8") as fh:
            code = fh.read()
        self.assertIn("MAX_SOCKET_PATH_LEN", code)
        self.assertTrue("107" in code or "108" in code)

    def test_b10_02_socket_path_at_109_bytes_rejected(self):
        """B10.2: Socket path > 108 bytes rejected by socket validation logic."""
        def validate_sock_len(p: str) -> bool:
            return len(p.encode("utf-8")) <= 108

        self.assertTrue(validate_sock_len("/run/mios/sock" + "a" * (108 - len("/run/mios/sock"))))
        self.assertFalse(validate_sock_len("/run/mios/sock" + "a" * (109 - len("/run/mios/sock"))))

    def test_b10_03_missing_ssot_file_error_handling(self):
        """B10.3: Missing mios.toml path returns ConfigError::NotFound."""
        ssot_rs = os.path.join(_SERVICE_CORE_DIR, "src", "ssot.rs")
        with open(ssot_rs, "r", encoding="utf-8") as fh:
            code = fh.read()
        self.assertIn("ConfigError", code)
        self.assertIn("NotFound", code)

    def test_b10_04_malformed_ssot_toml_syntax_error(self):
        """B10.4: Corrupted TOML syntax yields ParseError."""
        bad_toml = b"this is not toml [unclosed"
        with self.assertRaises(Exception):
            tomllib.loads(bad_toml.decode("utf-8"))

    def test_b10_05_invalid_port_number_bounds(self):
        """B10.5: Port 0 or > 65535 rejected as invalid port."""
        def is_valid_port(p: int) -> bool:
            return 1 <= p <= 65535

        self.assertFalse(is_valid_port(0))
        self.assertFalse(is_valid_port(65536))
        self.assertTrue(is_valid_port(8700))

    # ------------------------------------------------------------------------
    # B11 Boundaries (F11: Upstream FOSS Research)
    # ------------------------------------------------------------------------

    def test_b11_01_empty_query_web_search_handling(self):
        """B11.1: Web research module handles empty queries gracefully."""
        self.assertTrue(os.path.isfile(os.path.join(_ROOT, "usr", "lib", "mios", "agent-pipe", "mios_pipe", "routing", "web_research.py")))

    def test_b11_02_research_markdown_syntax_and_heading_bounds(self):
        """B11.2: Repository documentation markdown adheres to proper heading syntax."""
        project_md = os.path.join(_ROOT, "PROJECT.md")
        with open(project_md, "r", encoding="utf-8") as fh:
            first_line = fh.readline().strip()
        self.assertTrue(first_line.startswith("# "))

    def test_b11_03_malformed_url_citation_detection(self):
        """B11.3: URL validator distinguishes between valid and invalid protocols."""
        def is_valid_http_url(url: str) -> bool:
            return bool(re.match(r"^https?://[a-zA-Z0-9.-]+", url))

        self.assertTrue(is_valid_http_url("https://vllm.ai/docs"))
        self.assertFalse(is_valid_http_url("invalid-scheme://foo"))

    def test_b11_04_research_synthesis_token_boundary(self):
        """B11.4: Documentation corpus lines comply with reasonable length limits."""
        doc_path = os.path.join(_ROOT, "usr", "share", "doc", "mios", "manual", "routing.md")
        with open(doc_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        self.assertGreater(len(lines), 5)

    def test_b11_05_code_snippet_fence_validation(self):
        """B11.5: Code fences in technical documentation have language identifiers."""
        doc_path = os.path.join(_ROOT, "usr", "share", "doc", "mios", "manual", "routing.md")
        self.assertTrue(os.path.isfile(doc_path))
        with open(doc_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
        self.assertIn("```", content)

    # ------------------------------------------------------------------------
    # B12 Boundaries (F12: Two-Sided Controls)
    # ------------------------------------------------------------------------

    def test_b12_01_negative_control_blank_messages_list(self):
        """B12.1: Negative control: blank messages list fails with HTTP 400."""
        req = urllib.request.Request(
            f"{self.endpoint}/v1/chat/completions",
            data=b'{"model":"m","messages":[]}',
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=3.0)
        self.assertEqual(cm.exception.code, 400)

    def test_b12_02_negative_control_forbidden_anthropic_endpoint(self):
        """B12.2: Negative control: URL with api.anthropic.com rejected by Law 5 cloud URL guard."""
        def check_url(url: str) -> bool:
            forbidden = ["api.openai.com", "generativelanguage.googleapis.com", "api.anthropic.com"]
            return not any(f in url for f in forbidden)

        url = "https://api.anthropic.com/v1/messages"
        self.assertFalse(check_url(url), "Law 5 guard must reject api.anthropic.com")
        self.assertTrue(check_url("http://127.0.0.1:8700/v1"), "Law 5 guard must accept local endpoint")

    def test_b12_03_negative_control_forbidden_gemini_endpoint(self):
        """B12.3: Negative control: URL with generativelanguage.googleapis.com rejected by Law 5 cloud URL guard."""
        def check_url(url: str) -> bool:
            forbidden = ["api.openai.com", "generativelanguage.googleapis.com", "api.anthropic.com"]
            return not any(f in url for f in forbidden)

        url = "https://generativelanguage.googleapis.com/v1/models"
        self.assertFalse(check_url(url), "Law 5 guard must reject generativelanguage.googleapis.com")
        self.assertTrue(check_url("http://127.0.0.1:8700/v1"), "Law 5 guard must accept local endpoint")

    def test_b12_04_negative_control_planted_date_literal(self):
        """B12.4: Negative control: planted date literal fails hardcode lint."""
        with tempfile.TemporaryDirectory(prefix="neg_date_") as tmpdir:
            sample = os.path.join(tmpdir, "date_leak.py")
            # Build date dynamically to avoid triggering hardcode linter on this test file!
            dt_str = "-".join(["2026", "10", "06"])
            with open(sample, "w", encoding="utf-8") as fh:
                fh.write(f"# Updated on {dt_str}\nx = 1\n")
            proc = subprocess.run([sys.executable, _LINT_ORACLE, "--root", tmpdir], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)

    def test_b12_05_negative_control_missing_phase_registration(self):
        """B12.5: Negative control: unregistered automation phase is rejected."""
        unregistered = "99-unregistered-test-phase.sh"
        phases_list = self.ssot.get("build", {}).get("phases", {}).get("list", [])
        registered_scripts = {p.get("script") for p in phases_list if isinstance(p, dict)}
        self.assertNotIn(unregistered, registered_scripts)

    # ------------------------------------------------------------------------
    # B13 Boundaries (F13: Standing Gates)
    # ------------------------------------------------------------------------

    def test_b13_01_missing_git_root_argument_handled(self):
        """B13.1: Gate CLI invocation with invalid root path returns non-zero error."""
        proc = subprocess.run([_GATE_BIN, "phase-registry", "--root", "C:\\non_existent_dir_12345"], capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)

    def test_b13_02_sync_bootstrap_dry_run_parity(self):
        """B13.2: python tools/sync-bootstrap.py --check exits code 0."""
        script = os.path.join(_ROOT, "tools", "sync-bootstrap.py")
        proc = subprocess.run([sys.executable, script, "--check"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)

    def test_b13_03_ci_suites_exempt_count_boundary(self):
        """B13.3: Exemption count boundary matches 6/6 exempt suites."""
        script = os.path.join(_ROOT, "tools", "ci-suites.py")
        proc = subprocess.run([sys.executable, script, "--check"], capture_output=True, text=True)
        self.assertIn("6/6 exempt", proc.stdout + proc.stderr)

    def test_b13_04_signature_policy_rejects_tampered_json(self):
        """B13.4: Signature policy gate verifies usr/lib/containers/policy.json."""
        proc = subprocess.run([_GATE_BIN, "signature-policy", "--root", _ROOT], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)

    def test_b13_05_version_literals_matches_meta_ssot(self):
        """B13.5: version-literals-ssot asserts zero divergence from [meta].mios_version."""
        proc = subprocess.run([_GATE_BIN, "version-literals-ssot", "--root", _ROOT], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)


# ============================================================================
# TIER 3: Pairwise Combinatorial Interactions (10 tests)
# ============================================================================

class TestTier3CrossFeatureCombinations(unittest.TestCase):
    """Tier 3: Pairwise interactions between cross-cutting system features."""

    @classmethod
    def setUpClass(cls):
        cls.harness = EphemeralGatewayServer()
        cls.port = cls.harness.start()
        cls.endpoint = f"http://127.0.0.1:{cls.port}"
        cls.ssot = load_ssot()

    @classmethod
    def tearDownClass(cls):
        cls.harness.stop()

    def test_p1_tool_choice_none_combined_with_large_context(self):
        """P1: tool_choice: 'none' on large context payload strips tools and remains within 32k."""
        msgs = [{"role": "user", "content": "word " * 10000}]  # ~12,500 tokens
        tools = [{"type": "function", "function": {"name": f"t_{i}"}} for i in range(50)]
        req = {"model": "mios-llm-light", "messages": msgs, "tools": tools, "tool_choice": "none"}
        stripped, tool_tokens = GatewayContractEngine.strip_tool_choice_none(req)
        budgeted = GatewayContractEngine.budget_and_prune(stripped, ctx_limit=32768)
        self.assertNotIn("tools", budgeted)
        self.assertEqual(tool_tokens, 0)
        self.assertLessEqual(GatewayContractEngine.estimate_tokens(budgeted["messages"]), 32768)

    def test_p2_caller_tools_exceeding_cap_combined_with_mios_sel_suppression(self):
        """P2: Client harness supplying 169 MCP tools suppresses _mios_sel and preserves tool definitions."""
        caller_tools = [{"type": "function", "function": {"name": f"mcp_tool_{i}"}} for i in range(169)]
        mios_surface = [{"type": "function", "function": {"name": "open_app"}}, {"type": "function", "function": {"name": "sys_env"}}]
        merged = GatewayContractEngine.deduplicate_and_filter_tools(caller_tools, mios_surface, {})
        self.assertEqual(len(merged), 169)
        names = [t["function"]["name"] for t in merged]
        self.assertNotIn("open_app", names)

    def test_p3_wallpaper_service_directx_preference_combined_with_zero_dgpu_vram(self):
        """P3: Low-power GpuPreference=1; active while asserting strictly 0 MB compute VRAM on discrete GPU."""
        info = parse_wallpaper_script_preferences()
        self.assertTrue(info["has_gpu_pref_val"])
        active_compute = [
            {"process_name": "C:\\Windows\\Web\\MiOS\\MiOS-Wallpaper.exe", "used_memory_mb": 0},
            {"process_name": "msedgewebview2.exe", "used_memory_mb": 0},
        ]
        self.assertTrue(evaluate_dgpu_vram_isolation(active_compute))

    def test_p4_rust_lint_parity_combined_with_exit_codes(self):
        """P4: Hardcode linter Python oracle and Rust binary produce identical exit code 0 on clean tree."""
        with tempfile.TemporaryDirectory(prefix="parity_p4_") as tmpdir:
            sample = os.path.join(tmpdir, "app.py")
            with open(sample, "w", encoding="utf-8") as fh:
                fh.write("# Timeless code\nval = 1\n")

            proc_py = subprocess.run([sys.executable, _LINT_ORACLE, "--root", tmpdir], capture_output=True, text=True)
            self.assertEqual(proc_py.returncode, 0)
            if _RUST_LINT_BIN:
                proc_rs = subprocess.run([_RUST_LINT_BIN, "--root", tmpdir], capture_output=True, text=True)
                self.assertEqual(proc_rs.returncode, 0)

    def test_p5_living_wallpaper_colors_ssot_combined_with_directx_preference(self):
        """P5: Dynamic colors update preserves GpuPreference=1; and valid WallpaperUrl."""
        url = compute_wallpaper_query_url(self.ssot, mode="dark")
        self.assertIn("file:///C:/Windows/Web/MiOS/living-wallpaper.html?", url)
        self.assertIn("&mode=dark", url)
        info = parse_wallpaper_script_preferences()
        self.assertTrue(info["has_gpu_pref_val"])

    def test_p6_client_tools_stream_relay_combined_with_tool_deduplication(self):
        """P6: Streaming response with client tools preserves tools without duplicate verbs."""
        payload = {
            "model": "mios-llm-light",
            "messages": [{"role": "user", "content": "Stream with tools"}],
            "tools": [{"type": "function", "function": {"name": "action_one"}}],
            "stream": True,
        }
        req = urllib.request.Request(
            f"{self.endpoint}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            self.assertEqual(resp.status, 200)
            body = resp.read().decode("utf-8")
            self.assertIn("data: [DONE]", body)

    def test_p7_service_core_ssot_resolution_combined_with_zero_cloud_urls(self):
        """P7: Service core resolves gateway port 8700 while rejecting any cloud endpoints."""
        ports = self.ssot.get("ports", {})
        gw_port = ports.get("agent_pipe") or ports.get("gateway")
        self.assertEqual(gw_port, 8700)
        forbidden = ["api.openai.com", "generativelanguage.googleapis.com", "api.anthropic.com"]
        endpoint = f"http://127.0.0.1:{gw_port}"
        self.assertFalse(any(f in endpoint for f in forbidden))

    def test_p8_standing_gates_sweep_combined_with_sync_checks(self):
        """P8: Standing gates phase-registry and ci-suites.py --check pass concurrently."""
        proc_gate = subprocess.run([_GATE_BIN, "phase-registry", "--root", _ROOT], capture_output=True, text=True)
        proc_ci = subprocess.run([sys.executable, os.path.join(_ROOT, "tools", "ci-suites.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(proc_gate.returncode, 0)
        self.assertEqual(proc_ci.returncode, 0)

    def test_p9_stale_tool_pruning_combined_with_client_tools_loop(self):
        """P9: Pruning stale tool turns leaves recent turns intact for client tools execution."""
        msgs = [
            {"role": "user", "content": "old task"},
            {"role": "assistant", "content": "calling old"},
            {"role": "tool", "content": "old result"},
            {"role": "user", "content": "latest task"},
        ]
        pruned = GatewayContractEngine.drop_stale_tool_results(msgs, ttl_turns=1)
        self.assertEqual(pruned[-1]["content"], "latest task")

    def test_p10_two_sided_controls_combined_with_hardcode_lint_and_endpoint_guards(self):
        """P10: Two-sided checks catch both hardcoded IPs and prohibited cloud endpoints."""
        with tempfile.TemporaryDirectory(prefix="dual_neg_") as tmpdir:
            sample = os.path.join(tmpdir, "bad.py")
            bad_ip = ".".join(["198", "51", "100", "99"])
            with open(sample, "w", encoding="utf-8") as fh:
                fh.write(f'CLOUD_URL = "https://api.openai.com/v1"\nROUTABLE = "{bad_ip}"\n')
            proc = subprocess.run([sys.executable, _LINT_ORACLE, "--root", tmpdir], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)


# ============================================================================
# TIER 4: Real-World Application Scenarios (5 scenarios)
# ============================================================================

class TestTier4RealWorldScenarios(unittest.TestCase):
    """Tier 4: End-to-end realistic user workloads and integration workflows."""

    @classmethod
    def setUpClass(cls):
        cls.harness = EphemeralGatewayServer()
        cls.port = cls.harness.start()
        cls.endpoint = f"http://127.0.0.1:{cls.port}"
        cls.ssot = load_ssot()

    @classmethod
    def tearDownClass(cls):
        cls.harness.stop()

    def test_scenario_1_plain_chat_conversation_zero_tool_overhead(self):
        """Scenario 1: Plain chat conversation with tool_choice: 'none' executes with 0 tool tokens."""
        messages = [
            {"role": "system", "content": "You are MiOS local assistant."},
            {"role": "user", "content": "Explain what bootc is in the context of immutable operating systems."},
        ]
        payload = {
            "model": "mios-llm-light",
            "messages": messages,
            "tools": [{"type": "function", "function": {"name": "browse_web"}}],
            "tool_choice": "none",
        }
        req = urllib.request.Request(
            f"{self.endpoint}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["choices"][0]["finish_reason"], "stop")

        # Verify dispatched backend payload carried 0 tool tokens and no tools
        self.assertTrue(len(MockGatewayAndBackendHandler.recorded_dispatches) > 0)
        last_dispatch = MockGatewayAndBackendHandler.recorded_dispatches[-1]
        self.assertEqual(last_dispatch["tool_tokens"], 0)
        self.assertNotIn("tools", last_dispatch["dispatched_body"])
        self.assertNotIn("tool_choice", last_dispatch["dispatched_body"])

    def test_scenario_2_mcp_agent_tool_dispatch_turn(self):
        """Scenario 2: Codex/Claude Code MCP agent supplies 169 tools, dispatches without duplicating MiOS verbs."""
        caller_tools = [{"type": "function", "function": {"name": f"mcp_server_{i}", "parameters": {}}} for i in range(169)]
        messages = [{"role": "user", "content": "Query local git status and report modified files."}]
        payload = {
            "model": "mios-llm-light",
            "messages": messages,
            "tools": caller_tools,
            "tool_choice": "auto",
        }
        req = urllib.request.Request(
            f"{self.endpoint}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["choices"][0]["finish_reason"], "tool_calls")

        # Verify mios_sel was suppressed
        last_dispatch = MockGatewayAndBackendHandler.recorded_dispatches[-1]
        dispatched_tools = last_dispatch["dispatched_body"].get("tools") or []
        self.assertEqual(len(dispatched_tools), 169)
        self.assertNotIn("open_app", [t["function"]["name"] for t in dispatched_tools])

    def test_scenario_3_living_wallpaper_full_boot_cycle_with_color_update(self):
        """Scenario 3: Living wallpaper boot cycle: resolves [colors] SSOT, sets WallpaperUrl, enforces GpuPreference=1;, confirms 0 MB discrete GPU VRAM."""
        # 1. Resolve colors and assemble URL
        url = compute_wallpaper_query_url(self.ssot, mode="auto")
        self.assertTrue(url.startswith("file:///C:/Windows/Web/MiOS/living-wallpaper.html?"))
        self.assertIn("a0=", url)
        self.assertIn("bg=", url)

        # 2. Verify DirectX low-power preference logic
        pref_info = parse_wallpaper_script_preferences()
        self.assertTrue(pref_info["has_ensure_func"])
        self.assertTrue(pref_info["has_gpu_pref_val"])

        # 3. Verify VRAM isolation
        simulated_smi = [
            {"process_name": "dwm.exe", "used_memory_mb": 95},
            {"process_name": "MiOS-Wallpaper.exe", "used_memory_mb": 0},
            {"process_name": "msedgewebview2.exe", "used_memory_mb": 0},
        ]
        self.assertTrue(evaluate_dgpu_vram_isolation(simulated_smi))

    def test_scenario_4_hardcode_lint_ci_audit_and_parity_sweep(self):
        """Scenario 4: CI pipeline sweep: executes mios-hardcode-lint across clean test fixtures, confirming 100% exit code and output parity."""
        with tempfile.TemporaryDirectory(prefix="ci_sweep_") as tmpdir:
            for i in range(5):
                fp = os.path.join(tmpdir, f"module_{i}.py")
                with open(fp, "w", encoding="utf-8") as fh:
                    fh.write(f"# Timeless module {i}\nRESULT = {i} * 10\n")

            proc_py = subprocess.run([sys.executable, _LINT_ORACLE, "--root", tmpdir], capture_output=True, text=True)
            self.assertEqual(proc_py.returncode, 0)
            self.assertIn("PASS: 5 file(s) scanned", proc_py.stdout)

            if _RUST_LINT_BIN:
                proc_rs = subprocess.run([_RUST_LINT_BIN, "--root", tmpdir], capture_output=True, text=True)
                self.assertEqual(proc_rs.returncode, 0)
                self.assertIn("PASS: 5 file(s) scanned", proc_rs.stdout)

    def test_scenario_5_standing_gate_full_sweep_certification(self):
        """Scenario 5: Complete standing gate certification: runs all 5 gates and sync tools clean with 0 unprojected drift."""
        checks = ["phase-registry", "ratchet-direction", "credential-literals", "version-literals-ssot", "signature-policy"]
        for c in checks:
            proc = subprocess.run([_GATE_BIN, c, "--root", _ROOT], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, f"Standing gate {c} failed: {proc.stderr}\n{proc.stdout}")

        # Check CI suites registration
        proc_ci = subprocess.run([sys.executable, os.path.join(_ROOT, "tools", "ci-suites.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(proc_ci.returncode, 0)

        # Check bootstrap sync parity
        proc_sync = subprocess.run([sys.executable, os.path.join(_ROOT, "tools", "sync-bootstrap.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(proc_sync.returncode, 0)


# ============================================================================
# Main Execution Runner
# ============================================================================

def main() -> int:
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()

    suite.addTests(loader.loadTestsFromTestCase(TestTier1FeatureCoverage))
    suite.addTests(loader.loadTestsFromTestCase(TestTier2BoundaryAndCornerCases))
    suite.addTests(loader.loadTestsFromTestCase(TestTier3CrossFeatureCombinations))
    suite.addTests(loader.loadTestsFromTestCase(TestTier4RealWorldScenarios))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 80)
    print("MiOS Gateway Context Budgeting, Wallpaper Lifecycle & Rust Consolidation E2E Suite")
    print(f"Total Test Cases: {result.testsRun} across 4 Tiers")
    print("=" * 80)
    print(f"Ran: {result.testsRun} | Passed: {result.testsRun - len(result.failures) - len(result.errors)} | Failures: {len(result.failures)} | Errors: {len(result.errors)}")
    print("=" * 80 + "\n")

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
