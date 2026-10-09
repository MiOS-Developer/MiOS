#!/usr/bin/env python3
# AI-hint: Consolidated unit test suite for MiOS Model Context Protocol (MCP) domain (task_3353 / GATECAT-01).
# AI-related: usr/lib/mios/agent-pipe/mios_mcp.py, usr/libexec/mios/mcp/sandbox.py, usr/lib/mios/agent-pipe/server.py
"""Automated unit test suite for MiOS MCP Domain.

Consolidates:
- Declarative MCP Server Lifecycle, JSON-RPC Handshakes, OpenAI Schemas & Tool Dispatch (test-mcp-gateway-handshake.py)
- Bubblewrap Namespace, Filesystem Mount Sandboxing & Capability Dropping (test-mcp-sandbox.py)
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import tempfile
import unittest
from typing import Any, Dict, List
from unittest import mock
from unittest.mock import MagicMock, patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))

# Resolve agent-pipe path
_AGENT_PIPE = os.path.join(_ROOT, "usr", "lib", "mios", "agent-pipe")
if _AGENT_PIPE not in sys.path:
    sys.path.insert(0, _AGENT_PIPE)

import mios_mcp
import mios_mcp_transport
import httpx2

# Resolve sandbox path
_LIBEXEC_MCP = os.path.join(_ROOT, "usr", "libexec", "mios", "mcp")
if _LIBEXEC_MCP not in sys.path:
    sys.path.insert(0, _LIBEXEC_MCP)

try:
    import sandbox
except ImportError:
    import importlib.util
    _spec = importlib.util.spec_from_file_location("sandbox", os.path.join(_LIBEXEC_MCP, "sandbox.py"))
    if _spec and _spec.loader:
        sandbox = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(sandbox)
    else:
        raise ImportError(f"Could not load sandbox module from {_LIBEXEC_MCP}")


def _run_async(coro):
    return asyncio.run(coro)


# ============================================================================
# Domain 3.1: MCP Gateway Lifecycle & Tool Dispatch
# (Migrated from tests/test-mcp-gateway-handshake.py)
# ============================================================================

class TestMcpGatewayHandshake(unittest.TestCase):
    """Validates Declarative MCP server discovery, stdio/SSE protocol handshakes,
    OpenAI function schema conversion, and dynamic tool call dispatch."""

    def setUp(self):
        """Reset internal registries for test isolation."""
        mios_mcp._MCP_CLIENT_SERVERS.clear()
        mios_mcp._MCP_STDIO_CLIENTS.clear()
        mios_mcp._MCP_HTTP_CLIENTS.clear()
        mios_mcp.configure(
            mcp_client_tools={},
            mcp_client_lock=asyncio.Lock(),
            mcp_embed_new_tools=None,
            invalidate_worker_cache=lambda: None,
        )

    # -----------------------------------------------------------------------
    # 1. Declarative TOML Server Discovery
    # -----------------------------------------------------------------------

    def test_load_servers_from_toml_dict(self):
        """Verify parsing [mcp.servers] from a dictionary source."""
        toml_dict = {
            "mcp": {
                "protocol_version": "2025-11-25",
                "servers": {
                    "playwright": {
                        "enabled": True,
                        "transport": "stdio",
                        "command": "npx",
                        "args": ["-y", "@playwright/mcp@0.0.76", "--headless"],
                        "cwd": "/var/lib/mios/ai",
                        "env": {"HOME": "/var/lib/mios/ai"},
                        "tier": "rare",
                        "namespace": "browser_",
                        "taint": "untrusted_web",
                        "allowed_tools": ["navigate", "click", "snapshot"],
                    },
                    "weather_api": {
                        "enabled": True,
                        "transport": "sse",
                        "url": "http://127.0.0.1:8000/sse",
                        "headers": {"Authorization": "Bearer ${WEATHER_API_KEY}"},
                        "tier": "common",
                    },
                    "disabled_svc": {
                        "enabled": False,
                        "transport": "stdio",
                        "command": "legacy_bin",
                    },
                }
            }
        }

        specs = mios_mcp.load_servers_from_toml(toml_dict)
        self.assertEqual(len(specs), 3)

        spec_map = {s.id: s for s in specs}
        self.assertIn("playwright", spec_map)
        self.assertIn("weather_api", spec_map)
        self.assertIn("disabled_svc", spec_map)

        pw = spec_map["playwright"]
        self.assertEqual(pw.transport, "stdio")
        self.assertEqual(pw.command, "npx")
        self.assertEqual(pw.args, ["-y", "@playwright/mcp@0.0.76", "--headless"])
        self.assertEqual(pw.cwd, "/var/lib/mios/ai")
        self.assertEqual(pw.env, {"HOME": "/var/lib/mios/ai"})
        self.assertEqual(pw.namespace, "browser_")
        self.assertEqual(pw.taint, "untrusted_web")
        self.assertEqual(pw.allowed_tools, ["navigate", "click", "snapshot"])
        self.assertTrue(pw.enabled)

        w = spec_map["weather_api"]
        self.assertEqual(w.transport, "sse")
        self.assertEqual(w.url, "http://127.0.0.1:8000/sse")
        self.assertEqual(w.headers, {"Authorization": "Bearer ${WEATHER_API_KEY}"})

        d = spec_map["disabled_svc"]
        self.assertFalse(d.enabled)

    def test_load_servers_from_toml_string(self):
        """Verify parsing raw TOML string syntax into McpServerSpec instances."""
        toml_text = """
[mcp]
protocol_version = "2025-11-25"

[mcp.servers.duckdb]
enabled = true
transport = "stdio"
command = "uvx"
args = ["mcp-server-motherduck", "--db-path", ":memory:"]
tier = "rare"
namespace = "duckdb_"

[mcp.servers.remote_hub]
enabled = true
transport = "http"
url = "http://localhost:9090/mcp"
"""
        specs = mios_mcp.load_servers_from_toml(toml_text)
        self.assertEqual(len(specs), 2)
        spec_map = {s.id: s for s in specs}

        self.assertIn("duckdb", spec_map)
        self.assertIn("remote_hub", spec_map)
        self.assertEqual(spec_map["duckdb"].command, "uvx")
        self.assertEqual(spec_map["remote_hub"].transport, "http")

    def test_load_servers_from_toml_file(self):
        """Verify loading MCP server definitions from a temporary TOML file on disk."""
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8") as f:
            f.write("""
[mcp.servers.disk_server]
enabled = true
transport = "stdio"
command = "python"
args = ["-m", "disk_mcp"]
""")
            temp_path = f.name

        try:
            specs = mios_mcp.load_servers_from_toml(temp_path)
            self.assertEqual(len(specs), 1)
            self.assertEqual(specs[0].id, "disk_server")
            self.assertEqual(specs[0].command, "python")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_spec_serialization_roundtrip(self):
        """Verify McpServerSpec to_dict() and from_dict() fidelity."""
        original = mios_mcp.McpServerSpec(
            id="test_spec",
            transport="stdio",
            command="custom_bin",
            args=["--flag", "val"],
            env={"KEY": "VAL"},
            cwd="/tmp",
            enabled=True,
            tier="core",
            namespace="custom_",
            taint="safe",
            allowed_tools=["tool_a", "tool_b"],
            label="Custom Test Spec",
            examples=["run tool_a"],
            note="unit test spec",
        )

        d = original.to_dict()
        reconstructed = mios_mcp.McpServerSpec.from_dict(d)

        self.assertEqual(reconstructed.id, original.id)
        self.assertEqual(reconstructed.transport, original.transport)
        self.assertEqual(reconstructed.command, original.command)
        self.assertEqual(reconstructed.args, original.args)
        self.assertEqual(reconstructed.env, original.env)
        self.assertEqual(reconstructed.cwd, original.cwd)
        self.assertEqual(reconstructed.tier, original.tier)
        self.assertEqual(reconstructed.namespace, original.namespace)
        self.assertEqual(reconstructed.taint, original.taint)
        self.assertEqual(reconstructed.allowed_tools, original.allowed_tools)
        self.assertEqual(reconstructed.label, original.label)
        self.assertEqual(reconstructed.examples, original.examples)
        self.assertEqual(reconstructed.note, original.note)

    # -----------------------------------------------------------------------
    # 2. Strict OpenAI Function Schema Conversion
    # -----------------------------------------------------------------------

    def test_convert_mcp_to_openai_schema_basic(self):
        """Verify standard MCP inputSchema conversion to strict OpenAI tools definition."""
        mcp_tool = {
            "name": "calculate",
            "description": "Perform math calculation",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Math expression to evaluate",
                    },
                    "precision": {
                        "type": "integer",
                        "description": "Decimal precision",
                    },
                },
                "required": ["expression"],
            },
        }

        openai_tool = mios_mcp.convert_mcp_to_openai_schema(mcp_tool, server_id="math_srv")

        self.assertEqual(openai_tool["type"], "function")
        self.assertEqual(openai_tool["x-mios-mcp-server"], "math_srv")

        fn = openai_tool["function"]
        self.assertEqual(fn["name"], "mcp.math_srv.calculate")
        self.assertEqual(fn["description"], "Perform math calculation")
        self.assertTrue(fn["strict"])

        params = fn["parameters"]
        self.assertEqual(params["type"], "object")
        self.assertFalse(params["additionalProperties"])
        self.assertEqual(set(params["required"]), {"expression", "precision"})

        # Required property retains clean type
        self.assertEqual(params["properties"]["expression"]["type"], "string")
        # Optional property widened to nullable
        self.assertIn("null", params["properties"]["precision"]["type"])

    def test_convert_mcp_to_openai_schema_nested_objects(self):
        """Verify strict schema transformation handles nested objects and arrays."""
        mcp_tool = {
            "name": "create_user",
            "description": "Create a user account",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "user": {
                        "type": "object",
                        "properties": {
                            "username": {"type": "string"},
                            "profile": {
                                "type": "object",
                                "properties": {
                                    "bio": {"type": "string"},
                                    "age": {"type": "integer"},
                                },
                                "required": ["bio"],
                            },
                        },
                        "required": ["username"],
                    },
                    "tags": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                            },
                            "required": ["name"],
                        },
                    },
                },
                "required": ["user"],
            },
        }

        openai_tool = mios_mcp.convert_mcp_to_openai_schema(mcp_tool, server_id="users")
        params = openai_tool["function"]["parameters"]

        # Check top-level strictness
        self.assertFalse(params["additionalProperties"])
        self.assertIn("tags", params["required"])

        # Check nested user object
        user_prop = params["properties"]["user"]
        self.assertFalse(user_prop["additionalProperties"])
        self.assertIn("profile", user_prop["required"])

        # Check deeply nested profile object
        profile_prop = user_prop["properties"]["profile"]
        self.assertFalse(profile_prop["additionalProperties"])
        self.assertEqual(set(profile_prop["required"]), {"bio", "age"})

        # Check array item strictness
        tag_items = params["properties"]["tags"]["items"]
        self.assertFalse(tag_items["additionalProperties"])
        self.assertEqual(tag_items["required"], ["name"])

    def test_convert_mcp_to_openai_schema_fallback_cases(self):
        """Verify graceful fallback on empty, missing, or malformed schema fields."""
        tool_empty = {"name": "ping"}
        out = mios_mcp.convert_mcp_to_openai_schema(tool_empty)
        self.assertEqual(out["function"]["name"], "ping")
        self.assertEqual(out["function"]["description"], "MCP tool ping")
        self.assertEqual(out["function"]["parameters"]["type"], "object")
        self.assertEqual(out["function"]["parameters"]["properties"], {})
        self.assertEqual(out["function"]["parameters"]["required"], [])
        self.assertFalse(out["function"]["parameters"]["additionalProperties"])

    # -----------------------------------------------------------------------
    # 3. Real Subprocess Stdio MCP Server Handshake & Execution
    # -----------------------------------------------------------------------

    def test_stdio_handshake_discovery_and_execution(self):
        """Spawn a genuine Python MCP server subprocess over stdio, verify JSON-RPC 2.0
        initialize handshake, notifications/initialized, tools/list discovery, and
        dynamic tool call execution dispatch."""

        # Self-contained mock stdio MCP server script
        mock_server_code = """import sys, json

for line in sys.stdin:
    if not line.strip():
        continue
    try:
        req = json.loads(line)
    except Exception:
        continue
    rid = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}

    if method == "initialize":
        resp = {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mock-calc-server", "version": "1.0.0"}
            }
        }
        sys.stdout.write(json.dumps(resp) + "\\n")
        sys.stdout.flush()
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        resp = {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "tools": [
                    {
                        "name": "add_numbers",
                        "description": "Add two numbers together",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "a": {"type": "number"},
                                "b": {"type": "number"}
                            },
                            "required": ["a", "b"]
                        }
                    },
                    {
                        "name": "format_greeting",
                        "description": "Format greeting string",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "shout": {"type": "boolean"}
                            },
                            "required": ["name"]
                        }
                    }
                ]
            }
        }
        sys.stdout.write(json.dumps(resp) + "\\n")
        sys.stdout.flush()
    elif method == "tools/call":
        tname = params.get("name")
        args = params.get("arguments") or {}
        if tname == "add_numbers":
            a = float(args.get("a", 0))
            b = float(args.get("b", 0))
            resp = {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {"content": [{"type": "text", "text": str(a + b)}], "isError": False}
            }
        elif tname == "format_greeting":
            name = str(args.get("name", "world"))
            msg = f"HELLO {name.upper()}!" if args.get("shout") else f"Hello {name}!"
            resp = {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {"content": [{"type": "text", "text": msg}], "isError": False}
            }
        else:
            resp = {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32601, "message": f"Tool '{tname}' not found"}
            }
        sys.stdout.write(json.dumps(resp) + "\\n")
        sys.stdout.flush()
"""

        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(mock_server_code)
            mock_script_path = f.name

        try:
            async def _run_test():
                # 1. Configure and probe server
                server_cfg = {
                    "id": "calc_srv",
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [mock_script_path],
                    "enabled": True,
                    "namespace": "calc_",
                    "tier": "core",
                }

                await mios_mcp._mcp_probe_server(server_cfg)

                # 2. Verify server state
                srv_state = mios_mcp._MCP_CLIENT_SERVERS.get("calc_srv")
                self.assertIsNotNone(srv_state)
                self.assertEqual(srv_state["status"], "ready")
                self.assertEqual(srv_state["protocolVersion"], "2025-11-25")
                self.assertEqual(srv_state["tools_count"], 2)

                # 3. Verify registered tools
                async with mios_mcp._MCP_CLIENT_LOCK:
                    tools = dict(mios_mcp._MCP_CLIENT_TOOLS)

                self.assertIn("mcp.calc_srv.add_numbers", tools)
                self.assertIn("mcp.calc_srv.format_greeting", tools)

                add_tool = tools["mcp.calc_srv.add_numbers"]
                self.assertEqual(add_tool["description"], "Add two numbers together")
                self.assertEqual(add_tool["namespace"], "calc_")

                # 4. Dispatch tool call: add_numbers
                res_add = await mios_mcp.dispatch_tool_call(
                    server_id="calc_srv",
                    tool_name="add_numbers",
                    arguments={"a": 15.5, "b": 24.5},
                )
                self.assertNotIn("error", res_add)
                self.assertEqual(res_add["content"][0]["text"], "40.0")

                # 5. Dispatch tool call via namespaced key: format_greeting
                res_greet = await mios_mcp._mcp_call_tool(
                    "mcp.calc_srv.format_greeting",
                    {"name": "Developer", "shout": True},
                )
                self.assertNotIn("error", res_greet)
                self.assertEqual(res_greet["content"][0]["text"], "HELLO DEVELOPER!")

                # 6. Dispatch tool call with default optional argument
                res_greet_default = await mios_mcp.dispatch_tool_call(
                    server_id="calc_srv",
                    tool_name="format_greeting",
                    arguments={"name": "Alice"},
                )
                self.assertNotIn("error", res_greet_default)
                self.assertEqual(res_greet_default["content"][0]["text"], "Hello Alice!")

                # 7. Clean shutdown
                cli = mios_mcp._MCP_STDIO_CLIENTS.get("calc_srv")
                if cli is not None:
                    await cli.close()
                    self.assertIsNone(cli._sdk)
                    self.assertFalse(cli._inited)

            _run_async(_run_test())

        finally:
            if os.path.exists(mock_script_path):
                os.remove(mock_script_path)

    # -----------------------------------------------------------------------
    # 4. SSE / HTTP Mock Transport Handshake & Execution
    # -----------------------------------------------------------------------

    def test_http_sse_handshake_and_dispatch(self):
        """Verify HTTP/SSE JSON-RPC transport handshake, tools/list, and execution."""
        def respond(request):
            if request.method != "POST":
                return httpx2.Response(405)
            body = json.loads(request.content)
            method, rid = body.get("method"), body.get("id")
            params = body.get("params") or {}
            if rid is None:
                return httpx2.Response(202)
            if method == "initialize":
                result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                          "serverInfo": {"name": "fixture-http-hub", "version": "1"}}
            elif method == "tools/list":
                result = {"tools": [{"name": "lookup_ip", "description": "Look up geolocation for IP address",
                                     "inputSchema": {"type": "object", "properties": {"ip": {"type": "string"}}, "required": ["ip"]}}]}
            elif method == "tools/call":
                ip = params.get("arguments", {}).get("ip", "127.0.0.1")
                result = {"content": [{"type": "text", "text": f"Loc: {ip} -> Localhost"}], "isError": False}
            else:
                return httpx2.Response(200, json={"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unknown method"}})
            return httpx2.Response(200, json={"jsonrpc": "2.0", "id": rid, "result": result})

        http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))

        async def _run_test():

            server_cfg = {
                "id": "geo_hub",
                "transport": "http",
                "url": "http://127.0.0.1:9191/mcp",
                "enabled": True,
            }

            await mios_mcp._mcp_probe_server(server_cfg)

            srv_state = mios_mcp._MCP_CLIENT_SERVERS.get("geo_hub")
            self.assertIsNotNone(srv_state)
            self.assertEqual(srv_state["status"], "ready")
            self.assertEqual(srv_state["tools_count"], 1)

            res = await mios_mcp.dispatch_tool_call(
                server_id="geo_hub",
                tool_name="lookup_ip",
                arguments={"ip": "192.168.1.1"},
            )
            self.assertNotIn("error", res)
            self.assertIn("192.168.1.1", res["content"][0]["text"])
            await mios_mcp._MCP_HTTP_CLIENTS["geo_hub"].close()

        with mock.patch.object(mios_mcp_transport.httpx2, "AsyncClient", return_value=http_client):
            _run_async(_run_test())

    # -----------------------------------------------------------------------
    # 5. Error Handling & Edge Cases
    # -----------------------------------------------------------------------

    def test_dispatch_unknown_server_or_tool(self):
        """Verify proper error structure when querying unknown servers or tools."""
        async def _run_test():
            res = await mios_mcp.dispatch_tool_call(
                server_id="nonexistent_srv",
                tool_name="missing_tool",
                arguments={},
            )
            self.assertIn("error", res)
            self.assertEqual(res["code"], -32601)
            self.assertIn("unknown MCP server", res["error"])

        _run_async(_run_test())

    def test_stdio_timeout_handling(self):
        """Verify stdio timeout handling returns structured timeout error."""
        cli = mios_mcp._McpStdioClient("hang_srv", "dummy_cmd")

        async def _run_test():
            cli._sdk = mock.AsyncMock()
            cli._sdk.call_tool.side_effect = TimeoutError("tool timeout")
            cli._inited = True
            res = await cli.call_tool("slow_tool", {}, timeout_s=0.05)
            self.assertIn("error", res)
            self.assertEqual(res["error"]["code"], -32000)
            self.assertIn("timeout", res["error"]["message"])
            cli._sdk.call_tool.assert_awaited_once_with("slow_tool", {}, read_timeout_seconds=0.05)
            await cli.close()

        _run_async(_run_test())

    # -----------------------------------------------------------------------
    # 6. Declarative McpGateway Full Lifecycle
    # -----------------------------------------------------------------------

    def test_mcp_gateway_lifecycle(self):
        """Verify McpGateway management, schema queries, and shutdown lifecycle."""
        gateway = mios_mcp.McpGateway()
        toml_content = """
[mcp.servers.local_tool]
enabled = true
transport = "stdio"
command = "echo"
args = ["test"]
"""
        gateway.load_from_toml(toml_content)
        self.assertIn("local_tool", gateway.specs)

        mios_mcp._MCP_CLIENT_TOOLS["mcp.local_tool.sample"] = {
            "server_id": "local_tool",
            "tool": "sample",
            "description": "Sample test tool",
            "inputSchema": {
                "type": "object",
                "properties": {"val": {"type": "string"}},
                "required": ["val"],
            },
        }

        openai_tools = gateway.get_openai_tools()
        self.assertTrue(len(openai_tools) >= 1)
        sample_tool = next(t for t in openai_tools if t["function"]["name"] == "mcp.local_tool.sample")
        self.assertEqual(sample_tool["function"]["strict"], True)
        self.assertEqual(sample_tool["function"]["description"], "Sample test tool")

        _run_async(gateway.shutdown())
        self.assertEqual(len(mios_mcp._MCP_STDIO_CLIENTS), 0)

    # -----------------------------------------------------------------------
    # 7. Allowed Tools Filter & Environment Rendering
    # -----------------------------------------------------------------------

    def test_stdio_allowed_tools_filtering(self):
        """Verify allowed_tools filters discovered tools correctly."""
        mock_server_code = """import sys, json

for line in sys.stdin:
    if not line.strip():
        continue
    req = json.loads(line)
    rid = req.get("id")
    method = req.get("method")
    if method == "initialize":
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}, "serverInfo": {"name": "filter-srv", "version": "1"}}}) + "\\n")
        sys.stdout.flush()
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": {"tools": [{"name": "allowed_one", "inputSchema": {"type": "object"}}, {"name": "blocked_two", "inputSchema": {"type": "object"}}]}}) + "\\n")
        sys.stdout.flush()
"""
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(mock_server_code)
            mock_path = f.name

        try:
            async def _run_test():
                cfg = {
                    "id": "filter_test",
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [mock_path],
                    "allowed_tools": ["allowed_one"],
                }
                await mios_mcp._mcp_probe_server(cfg)
                srv = mios_mcp._MCP_CLIENT_SERVERS.get("filter_test")
                self.assertEqual(srv["tools_count"], 1)

                async with mios_mcp._MCP_CLIENT_LOCK:
                    tools = dict(mios_mcp._MCP_CLIENT_TOOLS)
                self.assertIn("mcp.filter_test.allowed_one", tools)
                self.assertNotIn("mcp.filter_test.blocked_two", tools)

                cli = mios_mcp._MCP_STDIO_CLIENTS.get("filter_test")
                if cli:
                    await cli.close()

            _run_async(_run_test())
        finally:
            if os.path.exists(mock_path):
                os.remove(mock_path)

    def test_env_header_rendering(self):
        """Verify environment variable substitution in headers and configs."""
        os.environ["MIOS_TEST_ENV_VAR_1"] = "bearer_token_123"
        try:
            headers = {"Authorization": "Bearer ${MIOS_TEST_ENV_VAR_1}", "X-Custom": "static"}
            rendered = mios_mcp._mcp_render_headers(headers)
            self.assertEqual(rendered["Authorization"], "Bearer bearer_token_123")
            self.assertEqual(rendered["X-Custom"], "static")
        finally:
            os.environ.pop("MIOS_TEST_ENV_VAR_1", None)

    def test_fastapi_endpoints_logic(self):
        """Verify REST API response handlers for clients, tools, and dispatch."""
        mios_mcp._MCP_CLIENT_SERVERS["test_srv"] = {
            "id": "test_srv",
            "status": "ready",
            "protocolVersion": "2025-11-25",
            "tools_count": 1,
        }
        mios_mcp._MCP_CLIENT_TOOLS["mcp.test_srv.test_tool"] = {
            "server_id": "test_srv",
            "tool": "test_tool",
            "description": "Test tool desc",
            "inputSchema": {"type": "object"},
        }

        async def _run_test():
            clients_resp = await mios_mcp.mcp_clients_logic()
            clients_data = json.loads(clients_resp.body)
            self.assertEqual(clients_data["object"], "mios.mcp.clients")
            self.assertEqual(len(clients_data["servers"]), 1)

            tools_resp = await mios_mcp.mcp_tools_list_logic()
            tools_data = json.loads(tools_resp.body)
            self.assertEqual(tools_data["object"], "mios.mcp.tools")
            self.assertEqual(len(tools_data["tools"]), 1)
            self.assertEqual(tools_data["tools"][0]["name"], "mcp.test_srv.test_tool")

            class _FakeRequest:
                def __init__(self, data):
                    self._data = data
                async def json(self):
                    return self._data

            bad_resp = await mios_mcp.mcp_dispatch_logic(_FakeRequest({}))
            self.assertEqual(bad_resp.status_code, 400)

        _run_async(_run_test())


# ============================================================================
# Domain 3.2: MCP Bubblewrap Process & Filesystem Isolation
# (Migrated from tests/test-mcp-sandbox.py)
# ============================================================================

class TestMcpSandbox(unittest.TestCase):
    """Validates bubblewrap command construction, filesystem isolation, and policy enforcement."""

    def test_bwrap_argument_generation(self):
        """Verify bubblewrap arguments, default flags, and read-only mounts."""
        sb = sandbox.McpSandbox(server_name="test-server", allow_net=False)
        cmd = sb.build_command(["python3", "server.py"])

        self.assertEqual(cmd[0], "bwrap")
        self.assertIn("--die-with-parent", cmd)
        self.assertIn("--new-session", cmd)
        self.assertIn("--unshare-all", cmd)
        self.assertIn("--unshare-net", cmd)
        self.assertNotIn("--share-net", cmd)

        self.assertIn("--ro-bind", cmd)
        self.assertIn("/usr", cmd)
        self.assertIn("/etc", cmd)
        self.assertIn("/lib64", cmd)

        self.assertIn("--dev", cmd)
        self.assertIn("/dev", cmd)
        self.assertIn("--proc", cmd)
        self.assertIn("/proc", cmd)
        self.assertIn("--tmpfs", cmd)
        self.assertIn("/tmp", cmd)

        self.assertEqual(cmd[-2:], ["python3", "server.py"])

    def test_network_isolation_toggle(self):
        """Verify --unshare-net and --share-net toggle based on allow_net parameter."""
        sb_nonet = sandbox.McpSandbox(server_name="isolated", allow_net=False)
        cmd_nonet = sb_nonet.build_command(["run"])
        self.assertIn("--unshare-net", cmd_nonet)
        self.assertNotIn("--share-net", cmd_nonet)

        sb_net = sandbox.McpSandbox(server_name="networked", allow_net=True)
        cmd_net = sb_net.build_command(["run"])
        self.assertIn("--share-net", cmd_net)
        self.assertNotIn("--unshare-net", cmd_net)

    def test_disallowed_host_write_paths(self):
        """Verify that attempting to mount protected system directories as writable raises ValueError."""
        sb = sandbox.McpSandbox(server_name="test-server")

        disallowed = [
            "/etc",
            "/usr",
            "/boot",
            "/sys",
            "/root",
            "/bin",
            "/sbin",
            "/lib",
            "/lib64",
            "/dev",
            "/proc",
            "/etc/passwd",
            "/usr/local/bin",
            "/boot/efi",
            "/sys/fs/cgroup",
            "/var/lib/mios/../../../etc",
            "/var/../usr/bin",
        ]

        for path in disallowed:
            with self.assertRaises(ValueError, msg=f"Should reject writable mount for {path}"):
                sb.add_custom_bind(path, writable=True)

            with self.assertRaises(ValueError, msg=f"Should reject direct add_rw_bind for {path}"):
                sb.add_rw_bind(path)

            with self.assertRaises(ValueError, msg=f"Should reject workspace_dir for {path}"):
                sb.set_workspace_dir(path)

    def test_custom_ro_binds(self):
        """Verify custom read-only bind mounts can be added and rendered in bwrap command."""
        sb = sandbox.McpSandbox(
            server_name="test-ro",
            custom_ro_binds=[
                "/usr/share/custom-data",
                ("/opt/models", "/var/models"),
            ],
        )
        sb.add_ro_bind("/opt/extra-docs")
        sb.add_custom_bind("/opt/configs", "/etc/local-config", writable=False)

        cmd = sb.build_command(["run"])

        def find_subseq(seq, sub):
            for i in range(len(seq) - len(sub) + 1):
                if seq[i : i + len(sub)] == sub:
                    return True
            return False

        self.assertTrue(find_subseq(cmd, ["--ro-bind", "/usr/share/custom-data", "/usr/share/custom-data"]))
        self.assertTrue(find_subseq(cmd, ["--ro-bind", "/opt/models", "/var/models"]))
        self.assertTrue(find_subseq(cmd, ["--ro-bind", "/opt/extra-docs", "/opt/extra-docs"]))
        self.assertTrue(find_subseq(cmd, ["--ro-bind", "/opt/configs", "/etc/local-config"]))

    def test_custom_rw_binds(self):
        """Verify authorized writable bind mounts are accepted and added with --bind."""
        sb = sandbox.McpSandbox(
            server_name="test-rw",
            custom_rw_binds=[
                "/var/lib/mios/ai/workspace",
                ("/var/tmp/mcp-cache", "/cache"),
            ],
        )
        sb.add_rw_bind("/tmp/mcp-scratch")
        sb.add_custom_bind("/home/user/project", writable=True)

        cmd = sb.build_command(["node", "index.js"])

        def find_subseq(seq, sub):
            for i in range(len(seq) - len(sub) + 1):
                if seq[i : i + len(sub)] == sub:
                    return True
            return False

        self.assertTrue(find_subseq(cmd, ["--bind", "/var/lib/mios/ai/workspace", "/var/lib/mios/ai/workspace"]))
        self.assertTrue(find_subseq(cmd, ["--bind", "/var/tmp/mcp-cache", "/cache"]))
        self.assertTrue(find_subseq(cmd, ["--bind", "/tmp/mcp-scratch", "/tmp/mcp-scratch"]))
        self.assertTrue(find_subseq(cmd, ["--bind", "/home/user/project", "/home/user/project"]))

    def test_workspace_dir_configuration(self):
        """Verify workspace_dir configuration sets --bind and --chdir."""
        sb = sandbox.McpSandbox(
            server_name="test-ws",
            workspace_dir="/var/lib/mios/workspace",
        )
        cmd = sb.build_command(["cargo", "run"])

        self.assertIn("--bind", cmd)
        self.assertIn("/var/lib/mios/workspace", cmd)
        self.assertIn("--chdir", cmd)

        idx_chdir = cmd.index("--chdir")
        self.assertEqual(cmd[idx_chdir + 1], "/var/lib/mios/workspace")

    def test_initialization_edge_cases(self):
        """Verify input validation during McpSandbox initialization and command generation."""
        with self.assertRaises(ValueError):
            sandbox.McpSandbox(server_name="")

        with self.assertRaises(ValueError):
            sandbox.McpSandbox(server_name="   ")

        with self.assertRaises(TypeError):
            sandbox.McpSandbox(server_name="test", custom_ro_binds=[123])  # type: ignore

        with self.assertRaises(TypeError):
            sandbox.McpSandbox(server_name="test", custom_rw_binds=[{"bad": "type"}])  # type: ignore

        sb = sandbox.McpSandbox(server_name="test")
        with self.assertRaises(ValueError):
            sb.build_command([])

    def test_to_dict_serialization(self):
        """Verify dictionary export contains all configuration properties."""
        sb = sandbox.McpSandbox(
            server_name="export-test",
            allow_net=True,
            workspace_dir="/var/tmp/workspace",
            custom_ro_binds=["/usr/share/dict"],
            custom_rw_binds=["/var/tmp/scratch"],
        )
        d = sb.to_dict()

        self.assertEqual(d["server_name"], "export-test")
        self.assertTrue(d["allow_net"])
        self.assertEqual(d["workspace_dir"], "/var/tmp/workspace")
        self.assertEqual(d["ro_binds"], [("/usr/share/dict", "/usr/share/dict")])
        self.assertEqual(d["rw_binds"], [("/var/tmp/scratch", "/var/tmp/scratch")])

    def test_parse_bind_arg(self):
        """Verify parsing of command-line bind mount syntax."""
        self.assertEqual(sandbox.parse_bind_arg("/usr/share"), ("/usr/share", "/usr/share"))
        self.assertEqual(sandbox.parse_bind_arg("/host/path:/container/path"), ("/host/path", "/container/path"))

    def test_cli_dry_run_command(self):
        """Verify CLI --dry-run prints generated bwrap command as JSON."""
        captured_stdout = io.StringIO()
        with patch("sys.stdout", captured_stdout):
            exit_code = sandbox.main([
                "--server-name", "cli-test",
                "--allow-net",
                "--workspace-dir", "/var/lib/mios/ws",
                "--ro-bind", "/usr/share/locale",
                "--rw-bind", "/var/tmp/scratch:/scratch",
                "--dry-run",
                "--", "python3", "-m", "mcp_server",
            ])

        self.assertEqual(exit_code, 0)
        output = captured_stdout.getvalue()
        cmd_list = json.loads(output)

        self.assertIsInstance(cmd_list, list)
        self.assertEqual(cmd_list[0], "bwrap")
        self.assertIn("--share-net", cmd_list)
        self.assertIn("/var/lib/mios/ws", cmd_list)
        self.assertIn("--chdir", cmd_list)
        self.assertEqual(cmd_list[-3:], ["python3", "-m", "mcp_server"])

    def test_cli_dry_run_config_dump(self):
        """Verify CLI --dry-run without inner command prints config JSON."""
        captured_stdout = io.StringIO()
        with patch("sys.stdout", captured_stdout):
            exit_code = sandbox.main([
                "--server-name", "config-dump",
                "--allow-net",
                "--dry-run",
            ])

        self.assertEqual(exit_code, 0)
        output = captured_stdout.getvalue()
        cfg = json.loads(output)
        self.assertEqual(cfg["server_name"], "config-dump")
        self.assertTrue(cfg["allow_net"])

    def test_validate_rw_path_direct(self):
        """Verify direct calls to validate_rw_path with various path formats."""
        sb = sandbox.McpSandbox(server_name="validate-test")
        self.assertEqual(sb.validate_rw_path("/var/lib/mios/data"), "/var/lib/mios/data")
        self.assertEqual(sb.validate_rw_path("/home/mios/projects"), "/home/mios/projects")
        self.assertEqual(sb.validate_rw_path("/tmp/scratch"), "/tmp/scratch")

        with self.assertRaises(ValueError):
            sb.validate_rw_path("")
        with self.assertRaises(ValueError):
            sb.validate_rw_path(None)  # type: ignore

    def test_custom_bwrap_binary(self):
        """Verify custom bubblewrap binary path is used in command generation."""
        sb = sandbox.McpSandbox(server_name="custom-bin", bwrap_binary="/usr/local/bin/bwrap-hardened")
        cmd = sb.build_command(["echo", "test"])
        self.assertEqual(cmd[0], "/usr/local/bin/bwrap-hardened")

    def test_property_immutability(self):
        """Verify ro_binds and rw_binds property getters return copies."""
        sb = sandbox.McpSandbox(server_name="prop-test")
        sb.add_ro_bind("/usr/share/doc")
        ro_list = sb.ro_binds
        ro_list.append(("/injected", "/injected"))
        self.assertEqual(len(sb.ro_binds), 1)

    def test_cli_disallowed_write_returns_error(self):
        """Verify CLI execution with forbidden writable path exits with non-zero error."""
        captured_stderr = io.StringIO()
        with patch("sys.stderr", captured_stderr):
            exit_code = sandbox.main([
                "--server-name", "bad-cli",
                "--rw-bind", "/etc",
                "--dry-run",
                "--", "ls",
            ])

        self.assertEqual(exit_code, 1)
        self.assertIn("Disallowed writable bind path", captured_stderr.getvalue())

    def test_execute_invokes_subprocess(self):
        """Verify execute method constructs command and invokes subprocess.run."""
        sb = sandbox.McpSandbox(server_name="exec-test")
        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            res = sb.execute(["echo", "hello"], capture_output=True)
            self.assertEqual(res.returncode, 0)
            mock_run.assert_called_once()
            called_args = mock_run.call_args[0][0]
            self.assertEqual(called_args[0], "bwrap")
            self.assertEqual(called_args[-2:], ["echo", "hello"])


if __name__ == "__main__":
    unittest.main()
