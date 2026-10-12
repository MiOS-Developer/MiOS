#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_memory (WS-A15 MemoryProvider seam).
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_memory (WS-A15)."""

import asyncio
import sys

import mios_memory as mem

_fails = 0

def check(name: str, cond: bool, detail: str = "") -> None:
    global _fails
    tag = "PASS" if cond else "FAIL"
    if not cond:
        _fails += 1
    print(f"[{tag}] {name}" + (f" -- {detail}" if detail else ""))

class FakeBackend:
    """Stands in for mios_pg: records the exact (args, kwargs) of each call."""

    def __init__(self):
        self.recall_calls = []
        self.insert_calls = []

    async def recall(self, qvec, **kw):
        self.recall_calls.append((qvec, kw))
        return [{"score": 0.9, "fact": "x"}]

    async def insert(self, table, fields, **kw):
        self.insert_calls.append((table, fields, kw))
        return {"ok": True}

def t_factory():
    fb = FakeBackend()
    p = mem.get_memory_provider("pgvector", fb)
    check("factory: returns PgVectorMemoryProvider", isinstance(p, mem.PgVectorMemoryProvider))
    check("factory: default name -> pgvector", isinstance(mem.get_memory_provider("", fb),
          mem.PgVectorMemoryProvider))
    check("factory: provider isinstance MemoryProvider", isinstance(p, mem.MemoryProvider))
    raised = False
    try:
        mem.get_memory_provider("redis-vectors", fb)
    except ValueError:
        raised = True
    check("factory: FAIL-CLOSED on unknown name (ValueError)", raised)

def t_retrieve_parity():
    fb = FakeBackend()
    p = mem.get_memory_provider("pgvector", fb)
    rows = asyncio.run(p.retrieve([0.1, 0.2], table="knowledge", k=3, owner="alice"))
    check("retrieve: returns backend rows", rows == [{"score": 0.9, "fact": "x"}])
    check("retrieve: forwards ALL kwargs verbatim (golden parity)",
          fb.recall_calls == [([0.1, 0.2], {"table": "knowledge", "k": 3, "owner": "alice"})],
          f"{fb.recall_calls}")
    asyncio.run(p.retrieve([0.3], table="agent_memory", k=5))
    check("retrieve: no-owner call forwards exactly (no injected owner)",
          fb.recall_calls[-1] == ([0.3], {"table": "agent_memory", "k": 5}))

def t_add_parity():
    fb = FakeBackend()
    p = mem.get_memory_provider("pgvector", fb)
    r = asyncio.run(p.add("knowledge", {"q": "hi", "answer": "yo"}))
    check("add: returns backend result", r == {"ok": True})
    check("add: forwards table+fields verbatim",
          fb.insert_calls == [("knowledge", {"q": "hi", "answer": "yo"}, {})], f"{fb.insert_calls}")

def t_register():
    class MockProvider(mem.MemoryProvider):
        def __init__(self, backend): self.backend = backend
        async def retrieve(self, qvec, **kw): return ["mock"]
        async def add(self, table, fields, **kw): return None
    mem.register_provider("mock", MockProvider)
    p = mem.get_memory_provider("mock", FakeBackend())
    check("register: custom provider resolvable", isinstance(p, MockProvider))
    check("register: custom retrieve used", asyncio.run(p.retrieve([0.0])) == ["mock"])

def t_secret_token_redaction() -> None:
    import io
    import logging
    from mios_pipe.redact import redact, RedactingLogFilter

    sentinel_key = "sk-mios-sentinel-private-token-778899aabbcc"
    sentinel_pass = "SENTINEL_MIOS_SECRET_PASS_998877"
    sentinel_conn = f"postgresql://mios:{sentinel_pass}@127.0.0.1:8600/mios"

    # Positive control: redact() strips secret tokens from text
    redacted_text, is_redacted = redact(f"Stored memory with token {sentinel_key}")
    check("redact: sentinel token redacted", is_redacted and sentinel_key not in redacted_text)
    check("redact: API key placeholder present", "[REDACTED_API_KEY]" in redacted_text)

    # Positive control: redact() strips URI passwords
    redacted_uri, is_uri_redacted = redact(f"Connecting to {sentinel_conn}")
    check("redact: URI password redacted", is_uri_redacted and sentinel_pass not in redacted_uri)
    check("redact: URI secret placeholder present", "[REDACTED_SECRET]" in redacted_uri)

    # Logging sink verification: RedactingLogFilter intercepts sensitive tokens on stdout/log sinks
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingLogFilter())
    test_logger = logging.getLogger("test.secret.redaction")
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.DEBUG)

    try:
        raise ConnectionError(f"Failed to authenticate with {sentinel_key} to {sentinel_conn}")
    except ConnectionError as err:
        test_logger.error("Database connection failure: %s", err)

    log_output = stream.getvalue()
    check("log sink: sentinel key absent from log output", sentinel_key not in log_output)
    check("log sink: sentinel pass absent from log output", sentinel_pass not in log_output)
    check("log sink: API key placeholder in log output", "[REDACTED_API_KEY]" in log_output)
    check("log sink: secret placeholder in log output", "[REDACTED_SECRET]" in log_output)

def main() -> int:
    t_factory()
    t_retrieve_parity()
    t_add_parity()
    t_register()
    t_secret_token_redaction()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0



# ==============================================================================
# Consolidated from test_mios_letta.py (task_1006)
# ==============================================================================
# AI-hint: Standalone unit test suite for LettaMemoryClient and letta_dispatch_handler (task_55).

import asyncio
import json
import sys
from typing import Optional

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mios_memory

_fails_letta = 0

def _check_letta(name: str, cond: bool, detail: str = "") -> None:
    global _fails_letta
    tag = "PASS" if cond else "FAIL"
    if not cond:
        _fails_letta += 1
    print(f"[{tag}] {name}" + (f" -- {detail}" if detail else ""))

class MockResponse:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json = json_data

    def json(self):
        return self._json

class MockHTTPXClient:
    def __init__(self, **kwargs):
        self.history = []

    async def get(self, url, params=None):
        self.history.append(("GET", url, params))
        if url == "/v1/agents":
            return MockResponse(200, [{"name": "test-session", "id": "agent-123"}])
        elif url == "/v1/agents/agent-123/memory":
            return MockResponse(200, {"blocks": [{"label": "persona", "value": "prefers dark mode"}]})
        elif url == "/v1/agents/agent-123/archival-memory/search":
            return MockResponse(200, [{"id": "item-1", "text": "prefers dark mode", "scope": "global"}])
        return MockResponse(404, {})

    async def post(self, url, json=None):
        self.history.append(("POST", url, json))
        if url == "/v1/agents":
            return MockResponse(201, {"id": "agent-123", "name": json.get("name")})
        elif url == "/v1/agents/agent-123/memory/blocks":
            return MockResponse(200, {"ok": True})
        elif url == "/v1/agents/agent-123/messages":
            return MockResponse(200, {"ok": True})
        return MockResponse(404, {})

    async def delete(self, url):
        self.history.append(("DELETE", url, None))
        if url == "/v1/agents/agent-123/in-context-messages/oldest":
            return MockResponse(200, {"ok": True})
        return MockResponse(404, {})

async def test_letta_client_flow():
    client = mios_memory.LettaMemoryClient("http://localhost:8283")
    mock_http = MockHTTPXClient()
    client.client = mock_http

    agent_id = await client.get_or_create_agent("test-session")
    _check_letta("letta: resolves existing agent ID", agent_id == "agent-123")

    res = await client.append_memory("test-session", "persona", "prefers dark mode")
    _check_letta("letta: append memory succeeds", res.get("ok") is True)

    s_res = await client.search_memory("test-session", "dark mode")
    _check_letta("letta: search memory succeeds", s_res.get("ok") is True)
    _check_letta("letta: search returns formatted memories", len(s_res.get("memories", [])) == 1)

    await client.trigger_compaction("test-session")
    _check_letta("letta: trigger compaction post is recorded", mock_http.history[-1][0] == "POST")

    await client.flush_oldest("test-session")
    _check_letta("letta: flush oldest delete is recorded", mock_http.history[-1][0] == "DELETE")

async def test_dispatch_handler_remember():
    mios_memory.LETTA_MEMORY_BACKEND = True
    mios_memory._LETTA_CLIENT = mios_memory.LettaMemoryClient("http://localhost:8283")
    mock_http = MockHTTPXClient()
    mios_memory._LETTA_CLIENT.client = mock_http

    stored_db = []
    def mock_db_create(table, fields, **kw):
        stored_db.append((table, fields))
        return fields
    mios_memory._db_create = mock_db_create
    mios_memory._db_post = lambda x: x
    mios_memory._db_fire = lambda x: x

    args = {"fact": "I prefer dark mode", "scope": "global", "key": "persona"}
    res = await mios_memory.letta_dispatch_handler("remember", args, "test-session")
    _check_letta("dispatch: intercept remember returns dispatch result", res is not None)
    _check_letta("dispatch: remember dispatch succeeds", res.get("success") is True)
    _check_letta("dispatch: remember updates local pg snapshot", len(stored_db) > 0)
    _check_letta("dispatch: remember local pg scope correct", stored_db[0][1].get("scope") == "conversation:test-session")

    args_append = {"content": "I prefer dark mode", "label": "persona"}
    res_append = await mios_memory.letta_dispatch_handler("memory_append", args_append, "test-session")
    _check_letta("dispatch: memory_append with label/content succeeds", res_append.get("success") is True)

def _main_letta():
    asyncio.run(test_letta_client_flow())
    asyncio.run(test_dispatch_handler_remember())

    print(f"\n{'ok' if _fails_letta == 0 else str(_fails_letta) + ' FAILED'}")
    sys.exit(1 if _fails_letta else 0)


def _run_extra_letta():
    import os
    _saved_env = dict(os.environ)
    try:
        return _main_letta()
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



def _run_all_folded_memory_suites():
    rc = _run_extra_letta()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _rc_main = main()
    _run_all_folded_memory_suites()
    sys.exit(_rc_main)
