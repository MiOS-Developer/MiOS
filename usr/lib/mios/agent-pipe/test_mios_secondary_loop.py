#!/usr/bin/env python3
# AI-hint: Stdlib assert-script for mios_secondary_loop (the /v1 sub-agent tool-loop + its
# AI-related: ./mios_secondary_loop.py, ./mios_toolexec.py
# AI-functions: (test script)
"""Offline unit test for mios_secondary_loop. Run: python test_mios_secondary_loop.py"""

import asyncio
import json
import sys

import mios_toolexec
import mios_secondary_loop as M

def _apply_outbound_auth(hdrs, ep):
    return None

def _endpoint_supports_parallel_tools(ep):
    return False

class _Resp:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

class _FakeClient:
    """Returns one scripted assistant message per POST, in order."""
    def __init__(self, scripted_messages):
        self._q = list(scripted_messages)
        self.calls = 0

    async def post(self, url, content=None, headers=None, timeout=None):
        self.calls += 1
        msg = self._q.pop(0) if self._q else {"content": "done."}
        return _Resp({"choices": [{"message": msg,
            "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop"}]})

def _msg(content="", tool_calls=None):
    m = {"content": content}
    if tool_calls is not None:
        m["tool_calls"] = tool_calls
    return m

_orig_toolexec_exec = getattr(mios_toolexec, "_exec_tool_calls", None)
_orig_m_exec = getattr(M, "_exec_tool_calls", None)
_orig_rescue = getattr(M, "_rescue_tool_calls", None)

def tearDownModule():
    if _orig_toolexec_exec is not None:
        mios_toolexec._exec_tool_calls = _orig_toolexec_exec
    if _orig_m_exec is not None:
        M._exec_tool_calls = _orig_m_exec
    if _orig_rescue is not None:
        M._rescue_tool_calls = _orig_rescue
    if _orig_mios_config is not None:
        sys.modules["mios_config"] = _orig_mios_config
    else:
        sys.modules.pop("mios_config", None)

def _wire(*, exec_result):
    """Inject stubs. exec_result is the (tmsgs, ran_read) the executor returns."""
    async def _fake_exec(tcs, push, allow_write=False):
        return exec_result
    mios_toolexec._exec_tool_calls = _fake_exec  # noqa: SLF001
    M._exec_tool_calls = _fake_exec             # the loop binds the name at import
    M._rescue_tool_calls = lambda content, tools=None: []
    M.configure(
        secondary_tool_max_iters=4,
        secondary_replan_max=1,
        daemon_diagnose_model="stub",
        daemon_diagnose_endpoint="http://stub/v1",
        daemon_diagnose_enable=False,   # degrade-open -> generic nudge, no extra net call
        apply_outbound_auth=_apply_outbound_auth,
        endpoint_supports_parallel_tools=_endpoint_supports_parallel_tools,
    )

def _run(scripted, exec_result=([], False)):
    _wire(exec_result=exec_result)
    nudges = []
    client = _FakeClient(scripted)
    tools = [{"function": {"name": "web_search"}}]
    msgs = [{"role": "user", "content": "what's the latest news?"}]
    out = asyncio.run(M._v1_secondary_tool_loop(
        client, "http://ep/v1", "m", {}, msgs, tools, None,
        nudges.append))
    return out, nudges, client

out, nudges, client = _run([
    _msg(content="I cannot do that, use my tools."),   # disclaim -> nudge
    _msg(content="The latest news is X."),              # clean final after nudge
])
assert any(m.get("role") == "user" and m.get("content") == M._TOOL_NUDGE
           for m in out), "TOOL_NUDGE not injected on a no-tool-call disclaimer"
assert client.calls == 2, f"expected re-loop after nudge, got {client.calls} calls"
print("TEST 1 PASS: disclaimer triggers _TOOL_NUDGE re-loop")

fail_tmsgs = [{"role": "tool", "content": json.dumps({"success": False})}]
_wire(exec_result=(fail_tmsgs, True))
M._rescue_tool_calls = lambda content, tools=None: []
nudges = []
client = _FakeClient([
    _msg(tool_calls=[{"id": "1", "function": {"name": "web_search",
                                              "arguments": "{}"}}]),
    _msg(content="I'll stop here."),   # give-up, not a disclaimer
    _msg(content="Re-attempted; here is the answer."),
])
out = asyncio.run(M._v1_secondary_tool_loop(
    client, "http://ep/v1", "m", {}, [{"role": "user", "content": "go"}],
    [{"function": {"name": "web_search"}}], None, nudges.append))
assert any(m.get("role") == "user"
           and str(m.get("content") or "").startswith(M._REPLAN_NUDGE)
           for m in out), "REPLAN_NUDGE not injected after a failed tool batch + give-up"
print("TEST 2 PASS: failed tool batch + give-up triggers _REPLAN_NUDGE closed loop")

out, nudges, client = _run([
    _msg(content="Here is a plain final answer."),
])
assert client.calls == 1, f"clean answer should be one pass, got {client.calls}"
assert not any(m.get("content") in (M._TOOL_NUDGE,) for m in out), \
    "no nudge should be injected on a clean answer"
assert not any(str(m.get("content") or "").startswith(M._REPLAN_NUDGE)
               for m in out), "no replan on a clean answer"
print("TEST 3 PASS: clean final answer terminates in one pass")

for reason in (None, "length", "content_filter", "tool_calls"):
    _wire(exec_result=([], False))
    class IncompleteClient(_FakeClient):
        async def post(self, *args, **kwargs):
            self.calls += 1
            return _Resp({"choices": [{"message": {"content": "Partial answer"},
                "finish_reason": reason}]})
    client = IncompleteClient([])
    terminal = {}
    asyncio.run(M._v1_secondary_tool_loop(
        client, "http://ep/v1", "m", {}, [{"role": "user", "content": "go"}],
        [], None, lambda *args: None, terminal_response=terminal))
    assert client.calls == 1
    assert terminal.get("_backend_failed") and terminal.get("_incomplete"), terminal
    assert not terminal.get("content"), terminal
print("Incomplete or mismatched completion acknowledgements fail without repeated inference: OK")

M.configure(daemon_diagnose_enable=False)
diag = asyncio.run(M._daemon_diagnose(_FakeClient([]), "it failed", "the goal"))
assert diag == "", f"disabled daemon-diagnose must return '', got {diag!r}"
print("TEST 4 PASS: _daemon_diagnose degrade-open when disabled")

sig_a = M._tool_call_sig({"function": {"name": "web_search",
                                       "arguments": {"q": "x", "n": 3}}})
sig_b = M._tool_call_sig({"function": {"name": "web_search",
                                       "arguments": {"n": 3, "q": "x"}}})
sig_c = M._tool_call_sig({"function": {"name": "web_search",
                                       "arguments": '{"q": "x", "n": 3}'}})  # JSON str
assert sig_a == sig_b == sig_c, "tool_call_sig must be stable + arg-order independent"
assert M._tool_call_sig({"function": {"name": "open_url"}}) != sig_a, \
    "different verbs must produce different signatures"
assert M._looks_like_disclaimer("I cannot find that, no data available.")
assert not M._looks_like_disclaimer("Here is the concrete answer you asked for.")
assert not M._looks_like_disclaimer("")
assert M._tmsgs_indicate_failure(
    [{"role": "tool", "content": json.dumps({"success": False})}])
assert M._tmsgs_indicate_failure(
    [{"role": "tool", "content": "dispatch error: text_not_delivered"}])
assert not M._tmsgs_indicate_failure(
    [{"role": "tool", "content": json.dumps({"success": True, "data": []})}])
assert not M._tmsgs_indicate_failure([{"role": "tool", "content": ""}])
print("TEST 6 PASS: _tool_call_sig / _looks_like_disclaimer / _tmsgs_indicate_failure")

_wire(exec_result=([], True))
nudges = []
client = _FakeClient([
    _msg(tool_calls=[{"id": "t1", "function": {"name": "web_search", "arguments": '{"q": "1"}'}}]),
    _msg(tool_calls=[{"id": "t2", "function": {"name": "web_search", "arguments": '{"q": "2"}'}}]),
    _msg(tool_calls=[{"id": "t3", "function": {"name": "web_search", "arguments": '{"q": "3"}'}}]),
])
sys_agent_cfg = {"no_progress_window": 2}
_orig_mios_config = sys.modules.get("mios_config")
class FakeConfig:
    @staticmethod
    def _toml_section(sec):
        return sys_agent_cfg if sec == "agent_pipe" else {}
    def __getattr__(self, name):
        if _orig_mios_config is not None:
            return getattr(_orig_mios_config, name)
        raise AttributeError(name)
sys.modules["mios_config"] = FakeConfig()

out = asyncio.run(M._v1_secondary_tool_loop(
    client, "http://ep/v1", "m", {}, [{"role": "user", "content": "go"}],
    [{"function": {"name": "web_search"}}], None, nudges.append))
print("DEBUG: out =", out)
assert any("Runaway loop detected" in str(m.get("content")) for m in out), "runaway loop should terminate"
print("TEST 7 PASS: same tool signatures trigger runaway loop termination")

from types import ModuleType
fake_reflect_mod = ModuleType("mios_reflect")
async def dummy_reflect(*args, **kwargs):
    return {"tool": "web_search", "args": {"q": "new_query"}, "rationale": "try another query"}
fake_reflect_mod.reflect_on_step_failure = dummy_reflect
sys.modules["mios_reflect"] = fake_reflect_mod

fail_tmsgs = [{"role": "tool", "tool_call_id": "t1", "content": json.dumps({"success": False})}]
_wire(exec_result=(fail_tmsgs, True))
client = _FakeClient([
    _msg(tool_calls=[{"id": "t1", "function": {"name": "web_search", "arguments": '{"q": "fail"}'}}]),
    _msg(tool_calls=[
        {"id": "t2", "function": {"name": "web_search", "arguments": '{"q": "fail"}'}},
        {"id": "t3", "function": {"name": "web_search", "arguments": '{"q": "new_query"}'}}
    ]),
    _msg(content="stopping"),
])
sys_agent_cfg = {"no_progress_window": 5, "max_consecutive_failures": 5}
execution_calls = []
async def mock_exec(tcs, push, allow_write=False):
    execution_calls.append(tcs)
    return fail_tmsgs, True
_orig_exec = M._exec_tool_calls
M._exec_tool_calls = mock_exec

out = asyncio.run(M._v1_secondary_tool_loop(
    client, "http://ep/v1", "m", {}, [{"role": "user", "content": "go"}],
    [{"function": {"name": "web_search"}}], None, nudges.append))
M._exec_tool_calls = _orig_exec
assert len(execution_calls) == 2, f"expected 2 execution calls, got {len(execution_calls)}"
assert any(tc.get("id") == "t1" for tc in execution_calls[0]), "first call should execute t1"
assert any(tc.get("id") == "t3" for tc in execution_calls[1]), "second call should execute t3"
assert not any(tc.get("id") == "t2" for tc in execution_calls[1]), "t2 should be blacklisted and not executed"
assert "tool_execution_failed: This exact tool call previously failed" in str(out), "blacklist intercept prompt not found"
print("TEST 8 PASS: identical failed tool call is blacklisted and intercepted")

print("ALL TESTS PASSED")
tearDownModule()


def _test_literal_tool_examples_do_not_execute():
    from mios_pipe.routing import secondary_loop as actual
    from mios_pipe.routing import toolexec as executor
    from unittest import mock
    raw = '<tool_call>{"name":"web_search","arguments":{"query":"facts"}}</tool_call>'
    async def scenario(literal):
        _wire(exec_result=([], True))
        calls, terminal = [], {}
        async def execute(tcs, push, allow_write=False):
            calls.extend(tcs)
            return [{"role": "tool", "content": "verified evidence"}], True
        text = "Literal syntax: " + chr(96) + raw + chr(96) if literal else raw
        client = _FakeClient([_msg(text), _msg("Verified answer.")])
        with mock.patch.object(actual, "_rescue_tool_calls", executor._rescue_tool_calls),              mock.patch.object(actual, "_exec_tool_calls", execute),              mock.patch.object(executor, "_VERB_CATALOG", {"web_search": {"permission": "read"}}):
            await actual._v1_secondary_tool_loop(
                client, "http://ep/v1", "m", {}, [{"role": "user", "content": "A request"}],
                [{"function": {"name": "web_search"}}], None, lambda text: None,
                terminal_response=terminal)
        assert len(calls) == (0 if literal else 1), calls
        assert terminal["content"] == (text if literal else "Verified answer."), terminal
    asyncio.run(scenario(True))
    asyncio.run(scenario(False))
    tearDownModule()
    print("actual tool loop preserves inline examples without executing them; bare request still rescues once: OK")

_test_literal_tool_examples_do_not_execute()
