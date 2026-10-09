#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_compact (WS-A5 rolling-summary compaction planner). Pure stdlib, no server.py/DB/pytest.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_compact (WS-A5)."""

import sys

import mios_compact as cmp

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def m(role, content):
    return {"role": role, "content": content}

def t_noop():
    msgs = [m("user", "a" * 20), m("assistant", "b" * 20)]  # 5+5 tokens
    plan = cmp.plan_compaction(msgs, budget=1000)
    check("noop: fits -> needed False", plan.needed is False)
    check("noop: keeps everything", len(plan.to_keep) == 2 and not plan.to_summarize)

def t_summarize_oldest():
    msgs = [m("user", f"turn{i} " + "x" * 33) for i in range(10)]
    plan = cmp.plan_compaction(msgs, budget=40, keep_recent=2)
    check("compact: needed True (over budget)", plan.needed is True)
    check("compact: keeps within budget", plan.kept_tokens <= 40, f"{plan.to_dict()}")
    kept_texts = [x["content"] for x in plan.to_keep]
    check("compact: last keep_recent kept", msgs[-1]["content"] in kept_texts and msgs[-2]["content"] in kept_texts)
    check("compact: oldest folded into summary", msgs[0] in plan.to_summarize)
    check("compact: split is a partition", len(plan.to_keep) + len(plan.to_summarize) == 10)

def t_keep_system():
    msgs = [m("system", "S" * 200)] + [m("user", "u" * 40) for _ in range(6)]
    plan = cmp.plan_compaction(msgs, budget=30, keep_recent=1)
    check("system: system message kept verbatim",
          any(x["role"] == "system" for x in plan.to_keep))
    check("system: never summarized",
          not any(x["role"] == "system" for x in plan.to_summarize))

def t_order_preserved():
    msgs = [m("user", "u" * 40) for _ in range(8)]
    plan = cmp.plan_compaction(msgs, budget=25, keep_recent=2)
    idx = [msgs.index(x) for x in plan.to_keep]
    check("order: kept messages in original order", idx == sorted(idx), f"{idx}")

def t_drop_stale_tool_results():
    from mios_pipe.routing.chat_history import _drop_stale_tool_results
    msgs = [
        m("user", "turn 1"),
        m("assistant", "response 1"),
        m("user", "turn 2"),
        m("assistant", "call tool"),
        m("tool", "result 2"),
        m("assistant", "response 2"),
        m("user", "turn 3"),
        m("assistant", "response 3")
    ]
    res = _drop_stale_tool_results(msgs, ttl_turns=1)
    check("drop_tool: drops old tool message", not any(x.get("role") == "tool" for x in res))

    res2 = _drop_stale_tool_results(msgs, ttl_turns=2)
    check("drop_tool: keeps recent tool message", any(x.get("role") == "tool" for x in res2))

def t_chat_history():
    """mios_pipe.routing.chat_history: chat re-exports the very same objects;
    the session store degrades to an empty history, never raising into a turn."""
    import asyncio
    import json
    from mios_pipe.routing import chat, chat_history as ch
    for name in ("_drop_stale_tool_results", "_summarize_evicted_messages",
                 "_get_gateway_session", "_save_gateway_session"):
        check(f"history: chat re-exports chat_history.{name}",
              getattr(chat, name, None) is getattr(ch, name))

    class _Pg:
        def __init__(self, rows=None, error=None):
            self.rows, self.error, self.calls = rows, error, []

        async def execute(self, sql, params, fetch=False):
            self.calls.append((" ".join(sql.split()), params, fetch))
            if self.error is not None:
                raise self.error
            return self.rows

    turn = [{"role": "user", "content": "hi"}]
    saved = ch._mios_pg
    try:
        ch._mios_pg = _Pg(rows=[{"messages": json.dumps(turn)}])
        check("history: a stored JSON string is decoded",
              asyncio.run(ch._get_gateway_session("s1")) == turn)
        ch._mios_pg = _Pg(rows=[{"messages": turn}])
        check("history: a stored jsonb list is returned as is",
              asyncio.run(ch._get_gateway_session("s1")) == turn)
        ch._mios_pg = _Pg(rows=[])
        check("history: an unknown session -> []", asyncio.run(ch._get_gateway_session("nope")) == [])
        ch._mios_pg = _Pg(error=RuntimeError("db down"))
        check("history: a database error on read degrades to []",
              asyncio.run(ch._get_gateway_session("s1")) == [])

        pg = _Pg()
        ch._mios_pg = pg
        asyncio.run(ch._save_gateway_session("s1", turn))
        sql, params, _ = pg.calls[0]
        check("history: save upserts on session_id",
              "ON CONFLICT (session_id)" in sql and params["session_id"] == "s1", sql)
        check("history: save stores the messages as JSON", json.loads(params["messages"]) == turn)
        ch._mios_pg = _Pg(error=RuntimeError("db down"))
        try:
            asyncio.run(ch._save_gateway_session("s1", turn))
            swallowed = True
        except Exception:
            swallowed = False
        check("history: a database error on save never raises into the turn", swallowed)
    finally:
        ch._mios_pg = saved

def main():
    t_noop()
    t_summarize_oldest()
    t_keep_system()
    t_order_preserved()
    t_drop_stale_tool_results()
    t_chat_history()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0



# ==============================================================================
# Consolidated from test_mios_tiered_memory.py (task_1006)
# ==============================================================================
# AI-hint: Standalone assert-script unit test for MEM-02 (tiered memory / context warning and eviction logic). Pure stdlib + asyncio, no live Letta server required. Runs as `python3 test_mios_tiered_memory.py` (exit 0 = pass).

import sys
import os
import asyncio
import json
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mios_pipe.routing.chat as chat
import mios_compact
import mios_tokenize

_fails_tiered_memory = 0

def _check_tiered_memory(name, cond, detail=""):
    global _fails_tiered_memory
    tag = "PASS" if cond else "FAIL"
    if not cond:
        _fails_tiered_memory += 1
    print(f"[{tag}] {name}" + (f" -- {detail}" if detail else ""))

class MockRequest:
    def __init__(self, body_dict, headers=None):
        self.body_dict = body_dict
        self.headers = headers or {}

    async def body(self):
        return json.dumps(self.body_dict).encode("utf-8")

def _m_tiered_memory(role, content):
    return {"role": role, "content": content}

events_written = []
memories_written = []
scratch_written = []
scratchpads = {}

def mock_db_write(table, fields, **kwargs):
    if table == "event":
        events_written.append(fields)
    elif table == "agent_memory":
        memories_written.append(fields)
    elif table == "scratch":
        scratch_written.append(fields)

def mock_scratchpad_for(session_id):
    if session_id not in scratchpads:
        scratchpads[session_id] = deque()
    return scratchpads[session_id]

async def mock_embed_one(text):
    return [0.1] * 768

def mock_turn_tenant():
    return "test-tenant"

async def mock_summarize(evicted):
    return "Evicted turns summary"

async def test_warning_and_eviction():
    chat.LETTA_MEMORY_BACKEND = False
    chat._db_write = mock_db_write
    chat._embed_one = mock_embed_one
    chat._scratchpad_for = mock_scratchpad_for
    chat._turn_tenant = mock_turn_tenant
    chat._summarize_evicted_messages = mock_summarize
    chat.SCRATCHPAD_PERSIST = True
    chat.EMB_MODEL = "test-model"
    chat.EMB_VERSION = "test-version"
    import contextvars
    chat._conv_key_var = contextvars.ContextVar("conv_key", default="test-session")
    chat._turn_volatile_var = contextvars.ContextVar("volatile", default=False)
    chat._trace_id_var = contextvars.ContextVar("trace_id", default="")
    chat._span_id_var = contextvars.ContextVar("span_id", default="")
    chat._client_env_var = contextvars.ContextVar("client_env", default=None)
    chat._src_turn_var = contextvars.ContextVar("src_turn", default="")
    chat._sources_var = contextvars.ContextVar("sources", default=[])
    chat._routed_domain_var = contextvars.ContextVar("routed_domain", default=None)

    import json
    chat._loads_lenient = lambda text: json.loads(text) if text else {}
    chat._extract_last_user_text = lambda msgs: next((m["content"] for m in reversed(msgs) if isinstance(m, dict) and m.get("role") == "user"), "")
    chat._strip_owui_scaffold = lambda x: x
    chat._scratchpad_key = lambda b, f: "test-session"
    chat._src_turn_key = lambda: "test-turn-key"

    chat._toml_section = lambda sect: {"n_ctx": 100, "compaction_threshold_pct": 100} if sect == "memory" else {}

    chat._conv_key_var.set("test-session")

    async def _async_noop(*a, **k): return None
    chat._scratchpad_rehydrate = _async_noop
    chat._maybe_run_pending_approval = _async_noop
    chat._route_domain = _async_noop

    async def _async_admit(*a, **k): return (True, "")
    chat._budget_admit = _async_admit

    chat._seed_hop_from_headers = lambda *a, **k: None
    chat._src_turn_init = lambda *a, **k: None
    chat._client_env = lambda *a, **k: None

    async def _async_void(*a, **k): pass
    chat._vram_checkpoint = _async_void

    msgs = [
        _m_tiered_memory("system", "You are an assistant."),
        _m_tiered_memory("user", "Hello " * 50) # ~75 tokens
    ]

    class MockKernel:
        def __init__(self):
            self.router = self
            self.dispatcher = self
            self.mode = "test"
        def route(self, refined):
            return self
        async def run(self, dec, **ctx):
            _check_tiered_memory("warning: event emitted", len(events_written) > 0)
            if events_written:
                _check_tiered_memory("warning: kind is context_warning", events_written[-1].get("kind") == "context_warning")

            last_msg = ctx.get("messages")[-1]
            _check_tiered_memory("warning: warning message appended", "WARNING" in last_msg.get("content"))
            raise RuntimeError("success_warning_test")

    chat._KERNEL = MockKernel()

    req = MockRequest({"model": "test", "messages": msgs})
    try:
        await chat.chat_completions_logic(req)
    except RuntimeError as e:
        if str(e) != "success_warning_test":
            raise

    chat._toml_section = lambda sect: {"n_ctx": 40, "compaction_threshold_pct": 100} if sect == "memory" else {}

    msgs_over = [
        _m_tiered_memory("system", "You are an assistant."),
        _m_tiered_memory("user", "Hello " * 10),  # ~15 tokens
        _m_tiered_memory("assistant", "Hi " * 10), # ~15 tokens
        _m_tiered_memory("user", "Hello " * 10),
        _m_tiered_memory("assistant", "Hi " * 10),
        _m_tiered_memory("user", "Hello " * 10),
        _m_tiered_memory("assistant", "Hi " * 10),
        _m_tiered_memory("user", "Hello " * 10),
    ]

    class MockKernelEvict:
        def __init__(self):
            self.router = self
            self.dispatcher = self
            self.mode = "test"
        def route(self, refined):
            return self
        async def run(self, dec, **ctx):
            scratch = mock_scratchpad_for("test-session")
            _check_tiered_memory("evict: scratchpad has summary prepended", len(scratch) > 0)
            if scratch:
                _check_tiered_memory("evict: system-summary is agent", scratch[0].get("agent") == "system-summary")
                _check_tiered_memory("evict: summary text in note", "Evicted turns summary" in scratch[0].get("note"))

            _check_tiered_memory("evict: scratch persistent write", len(scratch_written) > 0)
            _check_tiered_memory("evict: pgvector memory archival write", len(memories_written) > 0)
            if memories_written:
                _check_tiered_memory("evict: memory fact is summary", "Evicted turns summary" in memories_written[-1].get("fact"))
                _check_tiered_memory("evict: memory embedding added", memories_written[-1].get("emb") == [0.1] * 768)

            final_msgs = ctx.get("messages")
            _check_tiered_memory("evict: messages list rebuilt", len(final_msgs) > 0)
            _check_tiered_memory("evict: messages has summary system prompt", any("recursive summary" in str(m.get("content")).lower() for m in final_msgs))

            raise RuntimeError("success_evict_test")

    chat._KERNEL = MockKernelEvict()
    events_written.clear()
    memories_written.clear()
    scratch_written.clear()
    scratchpads.clear()

    req_evict = MockRequest({"model": "test", "messages": msgs_over})
    try:
        await chat.chat_completions_logic(req_evict)
    except RuntimeError as e:
        if str(e) != "success_evict_test":
            raise

def _main_tiered_memory():
    asyncio.run(test_warning_and_eviction())
    print(f"\n{'ok' if _fails_tiered_memory == 0 else str(_fails_tiered_memory) + ' FAILED'}")
    sys.exit(1 if _fails_tiered_memory else 0)


def _run_extra_tiered_memory():
    try:
        import os
        for k in list(os.environ.keys()):
            if k.startswith('MIOS_MEMORY_'): os.environ.pop(k)
        return _main_tiered_memory()
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0



def _run_all_folded_compact_suites():
    rc = _run_extra_tiered_memory()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    # The module's own checks first, as in the other folded suites: the folded
    # tiered-memory suite patches chat's globals and leaves them patched, so
    # anything run after it reads the mocks rather than the module.
    _rc_main = main()
    _run_all_folded_compact_suites()
    sys.exit(_rc_main)
