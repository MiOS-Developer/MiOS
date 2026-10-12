# AI-hint: Routing-PRECEDENCE gate for the extracted chat-completions router-brain
# AI-related: ./mios_chat.py, ./server.py, ./test_server_import.py
# AI-functions: _install_stubs, _resolve_toml, _apply, _run, main
"""Routing-precedence gate for mios_chat.chat_completions_logic (refactor R12)."""

import asyncio
import contextvars
import json
import os
import sys
import types
from unittest import mock

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _resolve_toml():
    """Point MIOS_TOML at the repo's vendor mios.toml if present so the sibling
    config parse exercises the REAL file; harmless if absent (readers degrade)."""
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.abspath(os.path.join(here, "..", "..", "..", ".."))
    toml = os.path.join(repo, "usr", "share", "mios", "mios.toml")
    if "MIOS_TOML" not in os.environ and os.path.isfile(toml):
        os.environ["MIOS_TOML"] = toml

class _Resp:
    """Recording stand-in for a fastapi response: captures content/status so the
    test can assert on the Tier-0 400 + the trivial-chat reply body."""
    def __init__(self, *a, **k):
        self.content = k.get("content", a[0] if a else None)
        self.status_code = k.get("status_code", 200)
        self.media_type = k.get("media_type")

class _Stream(_Resp):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.body_iterator = a[0] if a else k.get("content")

class _Headers:
    """Case-insensitive .get returning None for any missing header."""
    def __init__(self, d):
        self._d = {str(k).lower(): v for k, v in (d or {}).items()}

    def get(self, key, default=None):
        return self._d.get(str(key).lower(), default)

class FakeRequest:
    def __init__(self, body_obj, headers=None):
        self._body = json.dumps(body_obj).encode("utf-8")
        self.headers = _Headers(headers)

    async def body(self):
        return self._body

def _install_stubs():
    """Minimal 3rd-party stand-ins so mios_chat (and the sibling graph it imports)
    loads on a bare checkout. fastapi gets a recording responses module + Request."""
    for name in ("httpx", "websockets", "uvicorn"):
        sys.modules.setdefault(name, mock.MagicMock(name=name))
    fastapi = types.ModuleType("fastapi")

    class _App:
        def __getattr__(self, _attr):
            def _factory(*_a, **_k):
                def _wrap(fn=None):
                    return fn if fn is not None else (lambda f: f)
                return _wrap
            return _factory

    fastapi.FastAPI = lambda *a, **k: _App()
    fastapi.APIRouter = lambda *a, **k: _App()
    fastapi.Request = FakeRequest
    fastapi.WebSocket = object
    responses = types.ModuleType("fastapi.responses")
    responses.JSONResponse = type("JSONResponse", (_Resp,), {})
    responses.StreamingResponse = type("StreamingResponse", (_Stream,), {})
    for _c in ("HTMLResponse", "RedirectResponse", "Response", "PlainTextResponse"):
        setattr(responses, _c, type(_c, (_Resp,), {}))
    fastapi.responses = responses
    sys.modules.setdefault("fastapi", fastapi)
    sys.modules.setdefault("fastapi.responses", responses)

_resolve_toml()
_install_stubs()
import mios_chat  # noqa: E402 -- after stubs so the import succeeds on a bare checkout

_REAL_BUDGET_ADMIT = mios_chat._budget_admit
_REAL_BUDGET_RELEASE = mios_chat._budget_release_inflight

CALLS: list = []

def _ahandler(tag, ret):
    """An async dispatched-responder stub that records it fired + returns a sentinel."""
    async def _h(*a, **k):
        CALLS.append(tag)
        return ret
    return _h

async def _anoop(*a, **k):
    return None

def _apply(**deps):
    """Route each dep to the right seam: configure() for injected names, setattr for
    the directly-imported sibling responders -- auto-split by mios_chat._INJECTED."""
    inj = {k: v for k, v in deps.items() if k in mios_chat._INJECTED}
    mios_chat.configure(**inj)
    for k, v in deps.items():
        if k not in mios_chat._INJECTED:
            setattr(mios_chat, k, v)

class FakeKernel:
    def __init__(self):
        import mios_router
        import mios_dispatcher
        self.router = mios_router.Router()
        self.dispatcher = mios_dispatcher.Dispatcher({
            "chat": mios_chat._kernel_chat_handler,
            "dispatch": mios_chat._kernel_dispatch_handler,
            "multi_task": mios_chat._kernel_multi_task_handler,
            "agent": mios_chat._kernel_agent_handler,
        })
    def managers(self):
        return {"scheduler": True, "memory": True}

S_VISION, S_CLIENT, S_OS, S_NATIVE, S_LOCAL, S_DAG = (object() for _ in range(6))

def _wire_common(**over):
    """Fresh ContextVars + safe stubs for every dep the precedence paths touch.
    Per-case predicate/flag overrides come in via **over (applied last)."""
    base = dict(
        BACKEND_MODEL="m", VISION_ENABLE=True, VISION_MODEL="vlm",
        CLIENT_TOOLS_PASSTHROUGH=True, _INGRESS_KEY="",
        _HOP_HEADER="x-mios-hop", _VIA_HEADER="x-mios-via",
        _SRC_TURN_HEADER="x-mios-src-turn",
        _TOOL_BACKEND="tb", _TOOL_BACKEND_MODEL="tbm",
        _FASTPATH_VERBS={"open_app"}, _VERB_CATALOG={},
        KERNEL_ROUTE=False, COUNCIL_DEFAULT=False, _KERNEL=FakeKernel(),
        SWARM_DECOMPOSE_MIN_WORDS=6, AUTONOMOUS_PRIORITY=1.0,
        LOCAL_STATE_FASTPATH=True, NATIVE_LOOP_ENABLE=True, NATIVE_LOOP_MATH_HINT=False,
        _conv_key_var=contextvars.ContextVar("conv", default=None),
        _sources_var=contextvars.ContextVar("src", default=None),
        _trace_id_var=contextvars.ContextVar("trace", default=None),
        _span_id_var=contextvars.ContextVar("span", default=None),
        _src_turn_var=contextvars.ContextVar("srcturn", default=None),
        _client_env_var=contextvars.ContextVar("cenv", default=None),
        _routed_domain_var=contextvars.ContextVar("routed", default=None),
        _turn_volatile_var=contextvars.ContextVar("vol", default=None),
        _loads_lenient=json.loads,
        _extract_last_user_text=lambda msgs: next(
            (m.get("content") for m in reversed(msgs)
             if isinstance(m, dict) and m.get("role") == "user"), ""),
        _strip_owui_scaffold=lambda s: s,
        _scratchpad_key=lambda body, cid: cid,
        _scratchpad_rehydrate=_anoop,
        _maybe_run_pending_approval=_anoop,
        _seed_hop_from_headers=lambda *a, **k: None,
        _src_turn_key=lambda: "t", _src_turn_init=lambda *a, **k: None,
        _client_env=lambda body, headers: {},
        _route_domain=_anoop,
        _vram_checkpoint=_anoop,
        _sched_priority=lambda refined: {"score": 5.0},
        _budget_admit=_ahandler("budget_admit", (True, "")),  # returns (ok, reason)
        _recall_agent_memory=_ahandler("recall", ""),
        _is_memory_question=_ahandler("memq", False),
        _quick_chat_reply=_ahandler("quick_chat", ""),
        _write_skill_md_fire=lambda *a, **k: None,
        _messages_have_image=lambda msgs: False,
        _has_client_tools=lambda body: False,
        _deterministic_action_route=lambda text: None,
        _vision_complete=_ahandler("vision", S_VISION),
        _client_tools_complete=_ahandler("client_tools", S_CLIENT),
        _respond_os_control=_ahandler("os_control", S_OS),
        _respond_native_loop_direct=_ahandler("native", S_NATIVE),
        _respond_local_state=_ahandler("local_state", S_LOCAL),
        _respond_agent_dag=_ahandler("agent_dag", S_DAG),
        _store_knowledge=lambda *a, **k: None,
        refine_intent=_ahandler("refine", None),
    )
    base.update(over)
    _apply(**base)

def _run(body, headers=None):
    CALLS.clear()
    return asyncio.run(mios_chat.chat_completions_logic(FakeRequest(body, headers)))

def main():
    _wire_common()
    r = _run({"model": "m"})
    check("no-messages -> 400 short-circuit",
          getattr(r, "status_code", None) == 400 and not CALLS,
          f"status={getattr(r,'status_code',None)} calls={CALLS}")

    _wire_common(_messages_have_image=lambda msgs: True,
                 _has_client_tools=lambda body: True)
    r = _run({"model": "m", "messages": [{"role": "user", "content": "look"}],
              "tools": [{"type": "function"}]})
    check("image turn -> vision wins over client-tools",
          r is S_VISION and CALLS == ["vision"], f"calls={CALLS}")

    _wire_common(_has_client_tools=lambda body: True)
    r = _run({"model": "m", "messages": [{"role": "user", "content": "use my tools"}],
              "tools": [{"type": "function"}]})
    check("client-tools turn -> client-tools passthrough (vision skipped)",
          r is S_CLIENT and "client_tools" in CALLS and "vision" not in CALLS,
          f"calls={CALLS}")

    _wire_common(_has_client_tools=lambda body: True,
                 _deterministic_action_route=lambda text: {"tool": "open_app", "args": {}})
    r = _run({"model": "m", "messages": [{"role": "user", "content": "open notepad"}],
              "tools": [{"type": "function"}]})
    check("client-tools + OS action -> OS fast-path wins over hybrid",
          r is S_OS and CALLS == ["os_control"], f"calls={CALLS}")

    _wire_common(refine_intent=_ahandler("refine", {"intent": "chat", "reply": "hi there"}))
    r = _run({"model": "m", "messages": [{"role": "user", "content": "hey"}]})
    body = getattr(r, "content", {}) or {}
    reply = (((body.get("choices") or [{}])[0].get("message") or {}).get("content"))
    heavy = {"vision", "client_tools", "os_control", "native", "local_state", "agent_dag"}
    check("trivial chat -> short-circuit reply, no heavy handler",
          reply == "hi there" and not (heavy & set(CALLS)),
          f"reply={reply!r} calls={CALLS}")

    _test_responses_api()

    _test_budget()

    _test_microreply()

    _test_refine_orchestration()

    _test_developer_role()

    _test_tool_choice_none_stripping()

    _test_memory_compaction_tools()

    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0

class _RespHttpxResp:
    """A stand-in chat/completions response for the responses_api self-proxy POST."""
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

class _RespHttpxClient:
    """Async-context-manager httpx.AsyncClient stand-in returning a canned answer."""
    _payload = {"choices": [{"message": {"content": "PROXIED ANSWER"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2}}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        return _RespHttpxResp(_RespHttpxClient._payload)

class _RespHttpx:
    AsyncClient = _RespHttpxClient

def _test_responses_api():
    mios_chat.configure(BACKEND_MODEL="m", _loads_lenient=json.loads)
    orig_httpx = mios_chat.httpx
    mios_chat.httpx = _RespHttpx
    try:
        r = asyncio.run(mios_chat.responses_api_logic(
            FakeRequest({"model": "m", "input": "hello",
                         "instructions": "be terse"})))
        body = getattr(r, "content", {}) or {}
        out_text = body.get("output_text")
        item = (body.get("output") or [{}])[0]
        part = ((item.get("content") or [{}])[0]) if isinstance(item, dict) else {}
        check("responses_api -> Responses items shape (proxied answer)",
              body.get("object") == "response" and out_text == "PROXIED ANSWER"
              and part.get("type") == "output_text" and part.get("text") == "PROXIED ANSWER"
              and body.get("usage", {}).get("prompt_tokens") == 1,
              f"body={body}")
        r2 = asyncio.run(mios_chat.responses_api_logic(FakeRequest({"model": "m"})))
        err = (getattr(r2, "content", {}) or {}).get("error") or {}
        check("responses_api -> missing input is a 400 invalid_request error",
              getattr(r2, "status_code", None) == 400
              and err.get("type") == "invalid_request_error" and err.get("param") == "input",
              f"status={getattr(r2,'status_code',None)} err={err}")
    finally:
        mios_chat.httpx = orig_httpx

def _test_budget():
    """The aggregate-budget admission cluster, now owned by mios_chat. Drives the
    real functions with SYNTHETIC keys + tiny deterministic ceilings (the window
    is large, so every debit stays in-window -> no wall-clock dependence). No
    English example words: keys are opaque identifiers."""
    mc = mios_chat
    saved = {k: getattr(mc, k) for k in (
        "BUDGET_ENABLE", "BUDGET_CONV_TOKEN_CEIL", "BUDGET_AUTO_TOKEN_CEIL",
        "BUDGET_AUTO_MAX_INFLIGHT", "BUDGET_PER_TURN_ESTIMATE", "BUDGET_WINDOW_S")}
    mc._budget_admit = _REAL_BUDGET_ADMIT
    mc._budget_release_inflight = _REAL_BUDGET_RELEASE
    mc._BUDGET_LEDGER.clear()
    mc._BUDGET_AUTO_INFLIGHT.clear()
    try:
        mc.BUDGET_ENABLE = True
        mc.BUDGET_WINDOW_S = 3600.0
        mc.BUDGET_PER_TURN_ESTIMATE = 100
        mc.BUDGET_CONV_TOKEN_CEIL = 100
        mc.BUDGET_AUTO_TOKEN_CEIL = 10_000_000
        mc.BUDGET_AUTO_MAX_INFLIGHT = 1

        ck = "conv-synthetic-xq7"
        ok1, _ = asyncio.run(mc._budget_admit(ck, None))
        ok2, reason2 = asyncio.run(mc._budget_admit(ck, None))
        check("budget: conversation ceiling trips on the 2nd turn",
              ok1 is True and ok2 is False and "budget exhausted" in reason2,
              f"ok1={ok1} ok2={ok2} reason={reason2!r}")

        mc._BUDGET_LEDGER.clear()
        okA, _ = asyncio.run(mc._budget_admit("c1", "src-alpha", "tok-1"))
        okB, reasonB = asyncio.run(mc._budget_admit("c2", "src-beta", "tok-2"))
        asyncio.run(mc._budget_release_inflight("tok-1"))
        okC, _ = asyncio.run(mc._budget_admit("c3", "src-gamma", "tok-3"))
        check("budget: autonomous in-flight cap halts then releases",
              okA is True and okB is False and "concurrency" in reasonB
              and okC is True,
              f"okA={okA} okB={okB} reason={reasonB!r} okC={okC}")

        mc.BUDGET_ENABLE = False
        okD, _ = asyncio.run(mc._budget_admit(ck, None))
        check("budget: disabled degrades open (admits regardless of ledger)",
              okD is True, f"okD={okD}")
    finally:
        for k, v in saved.items():
            setattr(mc, k, v)
        mc._BUDGET_LEDGER.clear()
        mc._BUDGET_AUTO_INFLIGHT.clear()

class _BoomHttpxClient:
    """httpx.AsyncClient stand-in whose POST always raises -- exercises the
    degrade-open except path of the micro-LLM early-reply helpers (no network)."""
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        raise RuntimeError("no network in unit test")

class _BoomHttpx:
    AsyncClient = _BoomHttpxClient

def _test_microreply():
    """The micro-LLM early-reply helpers (intent=chat reply, memory-hit judge,
    location-ask), now owned by mios_chat (the injection was reversed). Asserts
    (1) the no-network GUARDS short-circuit and (2) the degrade-open except path.
    Inputs are SYNTHETIC opaque tokens (no English example words); the REFINE lane
    is never actually called -- httpx is swapped for a client that raises and
    _env_grounding is stubbed so the system-prompt assembly stays local."""
    mc = mios_chat
    check("micro: _quick_chat_reply('') short-circuits to ''",
          asyncio.run(mc._quick_chat_reply("")) == "")
    check("micro: _is_memory_question with empty facts is False (no judge call)",
          asyncio.run(mc._is_memory_question("zxq-1", "")) is False)
    check("micro: _is_memory_question with blank facts is False",
          asyncio.run(mc._is_memory_question("zxq-1", "   ")) is False)

    orig_httpx, orig_env = mc.httpx, mc._env_grounding
    mc.httpx = _BoomHttpx
    mc._env_grounding = lambda: ""
    try:
        check("micro: _quick_chat_reply degrades to '' on a REFINE-lane error",
              asyncio.run(mc._quick_chat_reply("zxq-token", None)) == "")
        check("micro: _is_memory_question degrades to False on a REFINE-lane error",
              asyncio.run(mc._is_memory_question("zxq-token", "zxq-fact")) is False)
        loc = asyncio.run(mc._ask_for_location("zxq-token"))
        check("micro: _ask_for_location degrades to a non-empty plain fallback",
              isinstance(loc, str) and bool(loc.strip()) and "zxq" not in loc,
              f"loc={loc!r}")
    finally:
        mc.httpx, mc._env_grounding = orig_httpx, orig_env

def _test_refine_orchestration():
    """The refine-driven orchestration helpers, now owned by mios_chat (the
    injection was reversed): the action-hint gate (_hints_write_action), the
    micro-LLM knowledge-gap judge (_needs_external_knowledge) and the multi-task
    queue writer (_shadow_queue_tasks). SYNTHETIC opaque verb tokens + permissions
    (no English example words); no network (the judge degrades open via the
    raising httpx stub) and no DB (the queue writer's guard paths return early)."""
    mc = mios_chat
    mc.configure(_VERB_CATALOG={
        "zxq_mutate": {"permission": "write"},
        "zxq_inspect": {"permission": "read"},
    })
    check("hints: non-dict refined -> False",
          mc._hints_write_action(None) is False)
    check("hints: chat-class intent never forces an action",
          mc._hints_write_action({"intent": "chat",
                                  "hint_tools": ["zxq_mutate"]}) is False)
    check("hints: agent intent + non-read verb -> True",
          mc._hints_write_action({"intent": "agent",
                                  "hint_tools": ["zxq_mutate"]}) is True)
    check("hints: agent intent + read-only verb -> False",
          mc._hints_write_action({"intent": "agent",
                                  "hint_tools": ["zxq_inspect"]}) is False)

    check("queue: empty task list -> [] (no DB)",
          mc._shadow_queue_tasks([], None) == [])
    check("queue: non-list input -> [] (no DB)",
          mc._shadow_queue_tasks("not-a-list", None) == [])

    check("kgap: empty input short-circuits to False",
          asyncio.run(mc._needs_external_knowledge("   ")) is False)
    orig_httpx = mc.httpx
    mc.httpx = _BoomHttpx
    try:
        check("kgap: degrades to False on a micro-lane error",
              asyncio.run(mc._needs_external_knowledge("zxq-token")) is False)
    finally:
        mc.httpx = orig_httpx

def _test_developer_role():
    from mios_pipe.routing.provider_translate import oai_msgs_to_gemini, oai_msgs_to_anthropic
    msgs = [
        {"role": "developer", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello"}
    ]
    system_str, anthropic_messages = oai_msgs_to_anthropic(msgs)
    check("developer role -> anthropic system text matches", system_str == "You are a helpful assistant.", f"sys={system_str}")
    check("developer role -> anthropic content ignores developer role", len(anthropic_messages) == 1 and anthropic_messages[0]["role"] == "user", f"msgs={anthropic_messages}")

    system_parts, gemini_contents = oai_msgs_to_gemini(msgs)
    check("developer role -> gemini system parts match", system_parts == {"parts": [{"text": "You are a helpful assistant."}]}, f"sys_parts={system_parts}")
    check("developer role -> gemini contents ignore developer role", len(gemini_contents) == 1, f"gemini={gemini_contents}")

def _test_tool_choice_none_stripping():
    import mios_vision
    captured_bodies = []
    _wire_common(
        _has_client_tools=mios_vision._has_client_tools,
        _client_tools_complete=_ahandler("client_tools", S_CLIENT),
        refine_intent=_ahandler("refine", {"intent": "chat", "reply": "plain chat reply"}),
        _scratchpad_key=lambda b, cid: (captured_bodies.append(dict(b)), cid)[1],
    )
    # Positive control: tool_choice == "none" strips tools and routes to plain chat (0 tool tokens)
    r_pos = _run({
        "model": "m",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"type": "function", "function": {"name": "test_tool"}}],
        "tool_choice": "none"
    })
    check("tool_choice: 'none' -> client_tools skipped (plain chat)",
          "client_tools" not in CALLS and "refine" in CALLS,
          f"calls={CALLS}")
    check("tool_choice: 'none' -> tools stripped from body",
          len(captured_bodies) >= 1 and all("tools" not in b for b in captured_bodies),
          f"body={captured_bodies}")
    check("tool_choice: 'none' -> tool_choice stripped from body",
          len(captured_bodies) >= 1 and all("tool_choice" not in b for b in captured_bodies),
          f"body={captured_bodies}")

    # Positive control (case-insensitive stripped): ' None '
    captured_ci_bodies = []
    _wire_common(
        _has_client_tools=mios_vision._has_client_tools,
        _client_tools_complete=_ahandler("client_tools", S_CLIENT),
        refine_intent=_ahandler("refine", {"intent": "chat", "reply": "plain chat reply"}),
        _scratchpad_key=lambda b, cid: (captured_ci_bodies.append(dict(b)), cid)[1],
    )
    r_pos_ci = _run({
        "model": "m",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"type": "function", "function": {"name": "test_tool"}}],
        "tool_choice": " None "
    })
    check("tool_choice: ' None ' (case-insensitive) -> client_tools skipped",
          "client_tools" not in CALLS and "refine" in CALLS,
          f"calls={CALLS}")
    check("tool_choice: ' None ' -> tools stripped from body",
          len(captured_ci_bodies) >= 1 and all("tools" not in b for b in captured_ci_bodies),
          f"body={captured_ci_bodies}")

    # Negative control: tool_choice == "auto" retains tools and routes to client_tools
    auto_bodies = []
    async def _async_client_tools(b, *a, **k):
        auto_bodies.append(dict(b))
        CALLS.append("client_tools")
        return S_CLIENT

    _wire_common(
        _has_client_tools=mios_vision._has_client_tools,
        _client_tools_complete=_async_client_tools,
    )
    r_neg = _run({
        "model": "m",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"type": "function", "function": {"name": "test_tool"}}],
        "tool_choice": "auto"
    })
    check("tool_choice: 'auto' -> client_tools passthrough",
          CALLS == ["client_tools"],
          f"calls={CALLS}")
    check("tool_choice: 'auto' -> tools and tool_choice retained in body",
          len(auto_bodies) == 1 and "tools" in auto_bodies[0] and auto_bodies[0].get("tool_choice") == "auto",
          f"body={auto_bodies}")

def _test_memory_compaction_tools():
    import mios_tokenize
    msgs = [{"role": "user", "content": "hello"}]
    tools = [{"type": "function", "function": {"name": "t1", "description": "tool " * 50}}]
    tok_no_tools = mios_tokenize.count_messages(msgs)
    tok_with_tools = mios_tokenize.count_messages(msgs, tools=tools)
    check("compaction tokens: tools included in token count",
          tok_with_tools > tok_no_tools,
          f"with={tok_with_tools} without={tok_no_tools}")
    check("compaction tokens: empty tools has 0 overhead",
          mios_tokenize.count_messages(msgs, tools=[]) == tok_no_tools)

if __name__ == "__main__":
    sys.exit(main())
