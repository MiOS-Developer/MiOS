#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_gateway_queue (CONV-03).
# AI-related: ./mios_gateway_queue.py

import asyncio
import sys
import unittest
from unittest.mock import MagicMock, patch

try:
    import mios_gateway_queue as mq
except (ImportError, Exception) as e:
    raise unittest.SkipTest(f"missing dependencies ({e})")

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

async def t_queue_basic():
    q = mq.GatewayQueue(maxsize=10)
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    req = mq.GatewayRequest(payload={"test": "data"}, fut=fut)

    await q.put(req)
    check("queue: put sets size to 1", q.qsize() == 1)

    ret = await q.get()
    check("queue: retrieved request matches", ret == req)
    check("queue: payload content is intact", ret.payload["test"] == "data")

    q.task_done()

async def t_future_resolution():
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    req = mq.GatewayRequest(payload={"test": "data"}, fut=fut)

    check("future: initially not done", not fut.done())
    fut.set_result({"choices": [{"message": {"content": "ok"}}]})
    check("future: becomes done", fut.done())
    res = await fut
    check("future: result content matches", res["choices"][0]["message"]["content"] == "ok")

async def t_worker_run_and_cancellation():
    with patch("mios_gateway_queue.ToolCallingAgent") as MockAgent, \
         patch("mios_gateway_queue.LiteLLMModel") as MockModel:

        mock_agent_instance = MagicMock()
        mock_agent_instance.run.return_value = "hello from agent"
        MockAgent.return_value = mock_agent_instance

        q = mq.GatewayQueue(maxsize=10)
        worker = mq.GatewayWorker(tools=[], endpoint="http://local:8080/v1", model_name="test-model")

        task = asyncio.create_task(worker.run(q, concurrency=2))

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        req = mq.GatewayRequest(payload={"messages": [{"role": "user", "content": "hi"}]}, fut=fut)

        await q.put(req)

        res = await fut
        check("worker: future resolved", fut.done())
        check("worker: returns correct assistant message", res["choices"][0]["message"]["content"] == "hello from agent")

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        check("worker: task cancelled cleanly", task.done())

async def t_worker_exception_handling():
    with patch("mios_gateway_queue.ToolCallingAgent") as MockAgent, \
         patch("mios_gateway_queue.LiteLLMModel") as MockModel:

        mock_agent_instance = MagicMock()
        mock_agent_instance.run.side_effect = RuntimeError("agent crash")
        MockAgent.return_value = mock_agent_instance

        q = mq.GatewayQueue(maxsize=10)
        worker = mq.GatewayWorker(tools=[], endpoint="http://local:8080/v1", model_name="test-model")

        task = asyncio.create_task(worker.run(q, concurrency=1))

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        req = mq.GatewayRequest(payload={"messages": [{"role": "user", "content": "hi"}]}, fut=fut)

        await q.put(req)

        try:
            await fut
            check("exception: failed to throw", False)
        except RuntimeError as e:
            check("exception: raised as future exception", str(e) == "agent crash")

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

def t_parse_sig():
    res1 = mq.parse_sig("limit?, force?")
    check("parse_sig fallback limit: type", res1["limit"]["type"] == "integer")
    check("parse_sig fallback limit: nullable", res1["limit"]["nullable"] is True)
    check("parse_sig fallback force: type", res1["force"]["type"] == "boolean")
    check("parse_sig fallback force: nullable", res1["force"]["nullable"] is True)

    vcfg = {
        "params": {
            "my_param": {"type": "number", "desc": "A custom float param"},
            "my_flag": {"type": "boolean", "desc": "A custom flag"}
        }
    }
    res2 = mq.parse_sig("my_param, my_flag=false", vcfg)
    check("parse_sig catalog my_param: type", res2["my_param"]["type"] == "number")
    check("parse_sig catalog my_param: desc", res2["my_param"]["description"] == "A custom float param")
    check("parse_sig catalog my_param: nullable", res2["my_param"]["nullable"] is False)

    check("parse_sig catalog my_flag: type", res2["my_flag"]["type"] == "boolean")
    check("parse_sig catalog my_flag: desc", res2["my_flag"]["description"] == "A custom flag")
    check("parse_sig catalog my_flag: nullable", res2["my_flag"]["nullable"] is True)

def main():
    t_parse_sig()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(t_queue_basic())
        loop.run_until_complete(t_future_resolution())
        loop.run_until_complete(t_worker_run_and_cancellation())
        loop.run_until_complete(t_worker_exception_handling())
    finally:
        loop.close()

    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0



# ==============================================================================
# Consolidated from test_mios_mcp_pool.py (task_1006)
# ==============================================================================
# AI-hint: Standalone assert-script unit test for MCPClientPool (CONV-13).
# AI-related: ./mios_gateway_queue.py

import os
import sys
import asyncio
from unittest import mock

import unittest

try:
    import mios_mcp_transport as transport
    from mios_gateway_queue import MCPClientPool
except ImportError as e:
    raise unittest.SkipTest(f"skipping mcp tests: {e}")

_fails_mcp_pool = 0

def _check_mcp_pool(name, cond, detail=""):
    global _fails_mcp_pool
    if not cond:
        _fails_mcp_pool += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

class MockTool:
    def __init__(self, name, description, inputSchema):
        self.name = name
        self.description = description
        self.inputSchema = inputSchema

class MockToolsResult:
    def __init__(self, tools):
        self.tools = tools

class MockSession:
    def __init__(self, tools):
        self.tools = tools
        self.inited = False
        self.closed = False

    async def list_tools(self):
        return MockToolsResult(self.tools)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.closed = True

async def _mcp_pool_lifecycle():
    server_configs = {
        "playwright": {
            "enabled": True,
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@playwright/mcp"],
            "env": {"TEST_VAR": "value"}
        },
        "disabled_srv": {
            "enabled": False,
            "transport": "stdio"
        }
    }

    pool = MCPClientPool(server_configs)

    _check_mcp_pool("pool: clients created", "playwright" in pool.clients)
    _check_mcp_pool("pool: disabled client ignored", "disabled_srv" not in pool.clients)

    async def mock_list_tools(self):
        return {"result": {"tools": [{
            "name": "navigate", "description": "Navigate to URL",
            "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
        }]}}

    async def mock_close(self):
        pass

    with mock.patch.object(transport._McpStdioClient, "list_tools", mock_list_tools), \
         mock.patch.object(transport._McpStdioClient, "close", mock_close):

        await pool.startup()

        tools = pool.get_tools()
        _check_mcp_pool("pool: fetched tool successfully", len(tools) == 1)
        _check_mcp_pool("pool: namespaced tool name", tools[0]["name"] == "mcp.playwright.navigate")
        _check_mcp_pool("pool: tool description matches", tools[0]["description"] == "Navigate to URL")
        _check_mcp_pool("pool: inputSchema is preserved", "properties" in tools[0]["inputSchema"])

        await pool.shutdown()
        _check_mcp_pool("pool: shutdown clears clients dict", len(pool.clients) == 0)
        _check_mcp_pool("pool: shutdown clears tools cache", len(pool.get_tools()) == 0)

async def _main_mcp_pool():
    await _mcp_pool_lifecycle()
    if _fails_mcp_pool > 0:
        sys.exit(1)
    sys.exit(0)


def test_mcp_pool_lifecycle():
    asyncio.run(_mcp_pool_lifecycle())
    assert _fails_mcp_pool == 0


def _run_extra_mcp_pool():
    import os
    _saved_env = dict(os.environ)
    try:
        import asyncio
        return asyncio.run(_main_mcp_pool())
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



def _run_all_folded_gateway_queue_suites():
    rc = _run_extra_mcp_pool()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _rc_main = main()
    _run_all_folded_gateway_queue_suites()
    sys.exit(_rc_main)
