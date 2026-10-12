#!/usr/bin/env python3
# AI-hint: Stdlib assert-script for mios_toolexec. Stubs every injected dep (no
# AI-related: ./mios_toolexec.py
# AI-functions: (test)
"""Offline regression for mios_toolexec -- rescue corpus + executor shaping."""

import ast
import asyncio
import contextvars
import json
import subprocess
import sys
from pathlib import Path

import mios_toolexec as T

# Execute the gateway's actual import declarations against the real shim and
# executor, so a retired symbol cannot hide behind a permissive import stub.
_server_path = Path(__file__).with_name("server.py")
_server_imports = [node for node in ast.parse(_server_path.read_text(encoding="utf-8")).body
                   if isinstance(node, ast.ImportFrom) and node.module == "mios_toolexec"]
assert _server_imports, "gateway must exercise the executor import contract"
_exports = {}
exec(compile(ast.Module(body=_server_imports, type_ignores=[]), str(_server_path), "exec"), _exports)
for _node in _server_imports:
    for _alias in _node.names:
        assert _exports[_alias.asname or _alias.name] is getattr(T, _alias.name)

_VERB_CATALOG = {
    "web_search": {"permission": "read"},
    "web_extract": {"permission": "read", "max_result_chars": 4000},
    "delete_file": {"permission": "write"},
    "os_recipe": {"permission": "read"},
}
_DISPATCHED = []

async def _fake_dispatch(verb, args):
    _DISPATCHED.append((verb, args))
    return {"ok": True, "output": json.dumps({"results": [
        {"title": "T", "url": "http://x"}]})}

_octx = contextvars.ContextVar("orch", default={})

T.configure(
    read_tool_enrich_chars=1500,
    read_tool_enrich_timeout=5.0,
    aci_max_lines=160,
    aci_head_frac=0.6,
    code_mode_enable=False,
    code_mode_heavy_only=False,
    max_dispatch_depth=2,
    verb_catalog=_VERB_CATALOG,
    recipe_catalog={},
    high_privilege_verbs=set(),
    web_enrich_verbs={"web_search", "web_extract", "crawl"},
    orch_ctx_var=_octx,
    dispatch_mios_verb=_fake_dispatch,
    mcp_call_tool=None,
    record_mcp_tool_call=lambda *a, **k: None,
    plan_swarm=None,
    live_agent_names=None,
    agent_dag_from_tasks=None,
    respond_agent_dag=None,
    depth_exhausted=lambda: False,
    dispatch_depth=lambda: 0,
    enter_dispatch_hop=lambda: 1,
    resolve_verb_key=lambda n: n,
    session_is_tainted=None,
    db_fire=lambda coro: None,
    db_post=lambda *a, **k: None,
    db_create=lambda *a, **k: None,
    src_record=lambda items: None,
)

PASS = 0

def ok(cond, label):
    global PASS
    assert cond, "FAIL: " + label
    PASS += 1
    print("  ok:", label)

print("[_norm_tool_call]")
n = T._norm_tool_call("web_search", {"query": "hi"}, 0)
ok(n["type"] == "function" and n["function"]["name"] == "web_search",
   "norm shapes an OpenAI tool_call")
ok(json.loads(n["function"]["arguments"]) == {"query": "hi"},
   "norm canonicalises dict args to a JSON string")
n2 = T._norm_tool_call("web_search", '{"query": "s"}', 1)
ok(json.loads(n2["function"]["arguments"]) == {"query": "s"},
   "norm parses a JSON-STRING arg")
n3 = T._norm_tool_call("web_search", "not json at all", 2)
ok(json.loads(n3["function"]["arguments"]) == {},
   "norm degrades malformed args to {}")

print("[_rescue_tool_calls]")
TOOLS = [{"type": "function", "function": {"name": "web_search"}},
         {"type": "function", "function": {"name": "web_extract"}}]

def _one(rescued):
    assert len(rescued) == 1, "expected exactly one rescued call, got %r" % rescued
    fn = rescued[0]["function"]
    return fn["name"], json.loads(fn["arguments"])

xml = ("Sure, let me look that up.\n"
       "<function=web_search>\n<parameter=query>mios refactor</parameter>\n</function>")
name, args = _one(T._rescue_tool_calls(xml, TOOLS))
ok(name == "web_search" and args == {"query": "mios refactor"},
   "rescues Qwen <function=> XML markup")

fenced = ("Here you go:\n```json\n"
          '{"name": "web_search", "arguments": {"query": "fenced"}}\n```')
name, args = _one(T._rescue_tool_calls(fenced, TOOLS))
ok(name == "web_search" and args == {"query": "fenced"},
   "rescues a ```json fenced {name,arguments} block")

tc = ('thinking...<tool_call>{"name": "web_extract", "arguments": '
      '{"url": "http://e"}}</tool_call>')
name, args = _one(T._rescue_tool_calls(tc, TOOLS))
ok(name == "web_extract" and args == {"url": "http://e"},
   "rescues <tool_call>{json}</tool_call> markup")

openai_blob = '{"function": {"name": "web_search", "arguments": "{\\"query\\": \\"blob\\"}"}}'
name, args = _one(T._rescue_tool_calls(openai_blob, TOOLS))
ok(name == "web_search" and args == {"query": "blob"},
   "rescues a bare OpenAI {function:{name,arguments}} object")

opencode = ("I'll call the web_search tool to find that.\n\n"
            "```json\n{\"tool\": \"web_search\", \"args\": {\"query\": \"opencode\"}}\n```")
name, args = _one(T._rescue_tool_calls(opencode, TOOLS))
ok(name == "web_search" and args == {"query": "opencode"},
   "rescues the opencode narrated/fenced {tool,args} lie")

guard = '```json\n{"name": "rm_rf_everything", "arguments": {}}\n```'
ok(T._rescue_tool_calls(guard, TOOLS) == [],
   "guard: an un-offered tool name is never promoted")

ok(T._rescue_tool_calls("just a normal answer, nothing to call here.", TOOLS) == [],
   "plain prose yields no rescued calls")

# Execute the real parser in a bounded child: the former nested regex wedges
# on repeated completed parameters without a function closer.
rescue_probe = """
import mios_toolexec as t
bad = '<function=web_search>' + '<parameter=query>x</parameter>' * 10000
assert t._rescue_tool_calls(bad, [{'function': {'name': 'web_search'}}]) == []
assert t._rescue_tool_calls('<function=-><parameter=->' * 10000, [{'function': {'name': 'web_search'}}]) == []
"""
subprocess.run([sys.executable, "-c", rescue_probe], cwd=Path(__file__).resolve().parent, check=True, timeout=10)
ok(True, "malformed XML rescue completes within bounded process time")
ok(T._rescue_tool_calls('<function=web_search>unexpected text</function>', TOOLS) == [],
   "unexpected function text is not promoted to a tool call")

print("[_cap_verb_result]")
ok(T._verb_result_cap("web_search") == 1500,
   "verb_result_cap falls back to READ_TOOL_ENRICH_CHARS")
ok(T._verb_result_cap("web_extract") == 4000,
   "verb_result_cap honours a verb's max_result_chars")
short = "small result"
ok(T._cap_verb_result("web_search", short) == short,
   "cap_verb_result returns a within-budget string unchanged")
big = "x" * 5000
capped = T._cap_verb_result("web_search", big)
ok(isinstance(capped, str) and len(capped) < len(big),
   "cap_verb_result truncates an over-budget string")

print("[_format_tool_error]")
ok(T._format_tool_error({"results": []}) is None,
   "no error on a normal result dict")
e = T._format_tool_error({"success": False, "error": "boom"})
ok(e and e["error"]["message"] == "boom" and e["error"]["code"] == "tool_execution_failed",
   "shapes success=False into an error envelope")
e2 = T._format_tool_error({"ok": False, "stderr": "bad"})
ok(e2 and e2["error"]["message"] == "bad",
   "shapes ok=False (stderr fallback) into an error envelope")
ok(T._format_tool_error("not a dict") is None,
   "non-dict result is not an error")

print("[_exec_tool_calls]")
_pushed = []

def _push(s):
    _pushed.append(s)

async def _run():
    _DISPATCHED.clear()
    _octx.set({})
    T.configure(
        read_tool_enrich_chars=1500,
        read_tool_enrich_timeout=5.0,
        aci_max_lines=160,
        aci_head_frac=0.6,
        code_mode_enable=False,
        code_mode_heavy_only=False,
        max_dispatch_depth=2,
        verb_catalog=_VERB_CATALOG,
        recipe_catalog={},
        high_privilege_verbs=set(),
        web_enrich_verbs={"web_search", "web_extract", "crawl"},
        orch_ctx_var=_octx,
        dispatch_mios_verb=_fake_dispatch,
        mcp_call_tool=None,
        record_mcp_tool_call=lambda *a, **k: None,
        plan_swarm=None,
        live_agent_names=None,
        agent_dag_from_tasks=None,
        respond_agent_dag=None,
        depth_exhausted=lambda: False,
        dispatch_depth=lambda: 0,
        enter_dispatch_hop=lambda: 1,
        resolve_verb_key=lambda n: n,
        session_is_tainted=None,
        db_fire=lambda coro: None,
        db_post=lambda *a, **k: None,
        db_create=lambda *a, **k: None,
        src_record=lambda items: None,
    )
    tcs_read = [{"id": "c1", "function": {
        "name": "web_search", "arguments": '{"query": "unique_toolexec_query"}'}}]
    msgs, ran = await T._exec_tool_calls(tcs_read, _push, allow_write=False)
    if not (ran is True and len(msgs) == 1 and msgs[0]["role"] == "tool"):
        print("DEBUG test_toolexec:", "ran=", ran, "msgs=", msgs)
    ok(ran is True and len(msgs) == 1 and msgs[0]["role"] == "tool",
       "read verb auto-executes (allow_write=False)")
    ok(msgs[0].get("tool_call_id") == "c1" and msgs[0].get("name") == "web_search",
       "tool message carries id + name linkage")
    ok(("web_search", {"query": "unique_toolexec_query"}) in _DISPATCHED,
       "read verb dispatched with canonicalised args")
    tcs_write = [{"id": "c2", "function": {"name": "delete_file", "arguments": "{}"}}]
    msgs2, ran2 = await T._exec_tool_calls(tcs_write, _push, allow_write=False)
    ok(ran2 is False and "skipped" in msgs2[0]["content"],
       "write verb skipped when writes disabled")

    T.configure(
        verb_catalog=_VERB_CATALOG,
        recipe_catalog={
            "disk-usage": {"permission": "read"},
            "shutdown": {"permission": "write"}})
    _DISPATCHED.clear()
    tcs_recipe = [{"id": "r1", "function": {
        "name": "mios_recipe__disk_usage", "arguments": "{}"}}]
    msgs3, ran3 = await T._exec_tool_calls(tcs_recipe, _push, allow_write=False)
    ok(ran3 is True and "skipped" not in msgs3[0]["content"],
       "A2: hyphenated read recipe runs on a read-only turn (not dropped)")
    ok(("os_recipe", {"name": "disk-usage", "params": {}}) in _DISPATCHED,
       "A2: read recipe dispatched via os_recipe with the canonical hyphenated name")
    await T._exec_tool_calls([{"id": "r-os", "function": {
        "name": "mios_recipe__disk_usage", "arguments": '{"os":"windows","path":"C:\\\\MiOS"}'}}], _push, allow_write=False)
    ok(("os_recipe", {"name": "disk-usage", "os": "windows", "params": {"path": "C:\\MiOS"}}) in _DISPATCHED,
       "Recipe OS selector reaches dispatch while path remains a recipe parameter")
    tcs_wrecipe = [{"id": "r2", "function": {
        "name": "mios_recipe__shutdown", "arguments": "{}"}}]
    msgs4, ran4 = await T._exec_tool_calls(tcs_wrecipe, _push, allow_write=False)
    ok(ran4 is False and "skipped" in msgs4[0]["content"],
       "A2: write recipe still skipped when writes disabled")

import unittest

class TestMiosToolexec(unittest.TestCase):
    def test_run(self):
        asyncio.run(_run())

if __name__ == "__main__":
    asyncio.run(_run())
    print("\nALL %d ASSERTIONS PASSED" % PASS)
