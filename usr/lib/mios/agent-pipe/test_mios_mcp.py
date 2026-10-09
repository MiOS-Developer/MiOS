# AI-hint: Stdlib unit test for mios_mcp -- the external-MCP CONSUME client extracted from server.py (refactor R-MCP).
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Offline unit tests for mios_mcp (no network, no DB, no real subprocess)."""

import asyncio
import json as _json
import os
import sys
import tempfile
from unittest.mock import patch
import types

try:
    import httpx
except ImportError:
    _httpx = types.ModuleType("httpx")
    class _HTTPError(Exception):
        pass
    _httpx.HTTPError = _HTTPError
    sys.modules["httpx"] = _httpx

if "fastapi" in sys.modules and hasattr(sys.modules["fastapi"], "responses"):
    pass
else:
    try:
        import fastapi
        import fastapi.responses
    except ImportError:
        _resp_mod = types.ModuleType("fastapi.responses")

        class _JSONResponse:
            def __init__(self, content=None, status_code=200, **_kw):
                self.status_code = status_code
                self._content = content
                self.body = _json.dumps(content).encode("utf-8")

        _resp_mod.JSONResponse = _JSONResponse
        sys.modules["fastapi.responses"] = _resp_mod
        _fa = types.ModuleType("fastapi")
        _fa.responses = _resp_mod
        _fa.Request = object

        class _StubRouter:
            def __getattr__(self, _name):
                def _decorator_factory(*_a, **_k):
                    def _wrap(fn=None):
                        return fn if fn is not None else (lambda f: f)
                    return _wrap
                return _decorator_factory

        _fa.APIRouter = lambda *a, **k: _StubRouter()
        sys.modules.setdefault("fastapi", _fa)

import mios_mcp as mc  # noqa: E402

def _run(coro):
    return asyncio.run(coro)

class _Resp:
    def __init__(self, status=200, ct="application/json", payload=None, text=""):
        self.status_code = status
        self.headers = {"content-type": ct}
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload

def _client_returning(resp):
    class _Client:
        async def post(self, *_a, **_k):
            return resp
    client = _Client()

    async def _get():
        return client
    return _get

def _embed_calls(box):
    async def _embed():
        box.append(1)
    return _embed

def _reset_registry(servers_dict=None, tools_dict=None, embed_box=None,
                    invalidated_box=None):
    """Inject fresh server-resident deps so each test is isolated."""
    inval = invalidated_box if invalidated_box is not None else []
    mc._MCP_CLIENT_SERVERS.clear()
    mc.configure(
        mcp_client_tools=tools_dict if tools_dict is not None else {},
        mcp_client_lock=asyncio.Lock(),
        mcp_embed_new_tools=_embed_calls(embed_box if embed_box is not None else []),
        invalidate_worker_cache=(lambda: inval.append(1)),
    )

def test_render_headers():
    os.environ["MIOS_TEST_MCP_TOKEN"] = "sekret"
    out = mc._mcp_render_headers({"Authorization": "Bearer ${MIOS_TEST_MCP_TOKEN}",
                                  "X-Plain": "v"})
    assert out["Authorization"] == "Bearer sekret", out
    assert out["X-Plain"] == "v", out
    out2 = mc._mcp_render_headers({"H": "${MIOS_NO_SUCH_VAR_XYZ}"})
    assert out2["H"] == "", out2

def test_load_registry_layered():
    with tempfile.TemporaryDirectory() as d:
        p_vendor = os.path.join(d, "vendor.json")
        p_user = os.path.join(d, "user.json")
        with open(p_vendor, "w", encoding="utf-8") as f:
            _json.dump({"servers": [{"id": "a", "url": "http://v"},
                                    {"id": "b", "url": "http://b"}]}, f)
        with open(p_user, "w", encoding="utf-8") as f:
            _json.dump({"servers": [{"id": "a", "enabled": False}]}, f)
        orig = mc._MCP_REGISTRY_PATHS
        mc._MCP_REGISTRY_PATHS = [p_vendor, p_user]
        try:
            with patch.object(mc, "_toml_section", return_value={}):
                reg = mc._mcp_load_registry()
        finally:
            mc._MCP_REGISTRY_PATHS = orig
        by_id = {s["id"]: s for s in reg}
        assert set(by_id) == {"a", "b"}, by_id
        assert by_id["a"].get("enabled") is False, by_id["a"]
        assert "url" not in by_id["a"], by_id["a"]      # fully replaced, not merged

def test_probe_server_projection():
    embed_box, inval_box, tools = [], [], {}
    _reset_registry(tools_dict=tools, embed_box=embed_box, invalidated_box=inval_box)
    mc._MCP_HTTP_CLIENTS.clear()

    class _FakeHTTP:
        def __init__(self, sid, url, headers=None, transport="http"):
            self.url = url
        async def initialize(self):
            return {"protocolVersion": "2026-07-28", "serverInfo": {"name": "srv"}}
        async def list_tools(self):
            return {"result": {"tools": [
                {"name": "query", "description": "run SQL", "inputSchema": {"type": "object"}},
                {"name": "ping", "description": "ping"},
            ]}}
        async def close(self):
            pass

    original = mc._McpHttpClient
    mc._McpHttpClient = _FakeHTTP
    try:
        _run(mc._mcp_probe_server({
            "id": "duck", "url": "http://duck", "transport": "http",
            "namespace": "duckdb_", "tier": "common", "taint": "ro",
            "examples": ["select 1"]}))
    finally:
        mc._McpHttpClient = original
        mc._MCP_HTTP_CLIENTS.clear()
    assert "mcp.duck.query" in tools and "mcp.duck.ping" in tools, tools
    ent = tools["mcp.duck.query"]
    assert ent["server_id"] == "duck" and ent["tool"] == "query", ent
    assert ent["namespace"] == "duckdb_" and ent["tier"] == "common", ent
    assert ent["taint"] == "ro" and ent["examples"] == ["select 1"], ent
    assert ent["url"] == "http://duck", ent
    st = mc._MCP_CLIENT_SERVERS["duck"]
    assert st["status"] == "ready" and st["tools_count"] == 2, st
    assert st["protocolVersion"] == "2026-07-28", st
    assert embed_box and inval_box, (embed_box, inval_box)

def test_route_logic_shapes():
    tools = {"mcp.s.t": {"server_id": "s", "tool": "t", "description": "d",
                         "inputSchema": {"type": "object"}, "url": "http://s"}}
    _reset_registry(tools_dict=tools)
    mc._MCP_CLIENT_SERVERS["s"] = {"id": "s", "status": "ready", "tools_count": 1}

    cl = _json.loads(_run(mc.mcp_clients_logic()).body)
    assert cl["object"] == "mios.mcp.clients" and cl["tools_total"] == 1, cl
    assert cl["servers"][0]["id"] == "s", cl

    tl = _json.loads(_run(mc.mcp_tools_list_logic()).body)
    assert tl["object"] == "mios.mcp.tools", tl
    assert tl["tools"][0]["name"] == "mcp.s.t", tl

    class _Req:
        def __init__(self, body):
            self._b = body

        async def json(self):
            return self._b

    bad = _run(mc.mcp_dispatch_logic(_Req({})))
    assert bad.status_code == 400, bad.status_code

    async def _fake_call(key, args):
        return {"called": key, "args": args}
    orig = mc._mcp_call_tool
    mc._mcp_call_tool = _fake_call
    try:
        ok = _json.loads(_run(mc.mcp_dispatch_logic(_Req({"tool": "mcp.s.t",
                                                          "args": {"x": 1}}))).body)
    finally:
        mc._mcp_call_tool = orig
    assert ok["called"] == "mcp.s.t" and ok["args"] == {"x": 1}, ok

def test_call_tool_unknown():
    _reset_registry(tools_dict={})
    out = _run(mc._mcp_call_tool("mcp.nope.x", {}))
    assert "error" in out and "unknown" in out["error"], out

def test_declared_protocol_version_is_current():
    assert mc.MCP_PROTOCOL_VERSION == "2026-07-28", mc.MCP_PROTOCOL_VERSION

def test_back_compat_negotiation_accepts_older_revision():
    tools = {}
    _reset_registry(tools_dict=tools)
    mc._MCP_HTTP_CLIENTS.clear()

    class _LegacyHTTP:
        def __init__(self, *_args, **_kwargs):
            pass
        async def initialize(self):
            return {"protocolVersion": "2025-06-18", "serverInfo": {"name": "old"}}
        async def list_tools(self):
            return {"result": {"tools": [{"name": "t", "description": "d"}]}}
        async def close(self):
            pass

    original = mc._McpHttpClient
    mc._McpHttpClient = _LegacyHTTP
    try:
        _run(mc._mcp_probe_server({"id": "old", "url": "http://old", "transport": "http"}))
    finally:
        mc._McpHttpClient = original
        mc._MCP_HTTP_CLIENTS.clear()
    st = mc._MCP_CLIENT_SERVERS["old"]
    assert st["status"] == "ready", st
    assert st["protocolVersion"] == "2025-06-18", st
    assert "mcp.old.t" in tools, tools


# ==============================================================================
# Consolidated from test_mios_mcp_dispatch.py (task_1006)
# ==============================================================================
# AI-hint: Unit test suite for mios_pipe.mcp_dispatch module.
# AI-related: mios_pipe/mcp_dispatch.py
"""Unit tests for mios_pipe.mcp_dispatch."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from mios_pipe.mcp_dispatch import build_mcp_tool_envelope

class TestMcpDispatch(unittest.TestCase):
    """Test MCP tool envelope builder."""

    def test_build_mcp_tool_envelope(self):
        env = build_mcp_tool_envelope("query_db", {"query": "hello"}, call_id="call_123")
        self.assertEqual(env["type"], "function")
        self.assertEqual(env["id"], "call_123")
        self.assertEqual(env["function"]["name"], "query_db")
        self.assertEqual(env["function"]["arguments"], {"query": "hello"})


def _run_extra_mcp_dispatch():
    import os
    _saved_env = dict(os.environ)
    try:
        import unittest
        suite = unittest.TestSuite()
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestMcpDispatch))
        res = unittest.TextTestRunner().run(suite)
        return 0 if res.wasSuccessful() else 1
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



def _run_all_folded_mcp_suites():
    rc = _run_extra_mcp_dispatch()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _run_all_folded_mcp_suites()
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok", fn.__name__)
    print(f"all {len(fns)} tests passed")
