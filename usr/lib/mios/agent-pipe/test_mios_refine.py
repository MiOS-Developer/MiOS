#!/usr/bin/env python3
# AI-hint: Offline refine regressions using canned completions and real HTTPX streaming transports; no server process, database, network or pytest.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Offline refine unit tests, including real HTTPX deadline and cleanup controls."""

import asyncio
import contextvars
import json

import httpx

import mios_refine as mr

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

class _Log:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    warn = warning

class _FakeResp:
    def __init__(self, body):
        self.status_code = 200
        self._body = body
        self.text = ""

    def json(self):
        return self._body

class _FakeClient:
    def __init__(self, body):
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        return _FakeResp(self._body)

class _FakeHTTPX:
    HTTPError = Exception

    def __init__(self):
        self.body = None

    def AsyncClient(self, timeout=None):
        return _FakeClient(self.body)

_FAKE = _FakeHTTPX()

_FASTPATH = frozenset(
    {"open_url", "launch_app", "launch_verified", "focus_window", "pc_type"})

class _CritResp:
    def __init__(self, body, status=200):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body

class _CritClient:
    def __init__(self, body, status=200):
        self._body = body
        self._status = status
        self.calls = 0

    async def post(self, url, content=None, headers=None):
        self.calls += 1
        return _CritResp(self._body, self._status)

def _configure():
    """Inject stub deps so import-clean module globals become exercise-ready."""
    mr.httpx = _FAKE  # swap the module's httpx for the canned-body fake
    mr._env_grounding = lambda: ""
    mr._deterministic_action_route = lambda _t: None

    async def _route_domain(_txt):
        return None

    mr.configure(
        logger=_Log(),
        agent_registry={},
        verb_catalog={
            "open_url": {"sig": "url, mode?"},
            "launch_app": {"sig": "name"},
            "launch_verified": {"sig": "name"},
            "pc_type": {"params": {"text": {"type": "str"}}},
            "remember": {"sig": "fact, scope?", "params": {"fact": {"type": "str"}}},
            "web_search": {"sig": "query", "params": {"query": {"type": "str"}}},
            "focus_window": {"sig": "name, position?"},
        },
        routed_domain_var=contextvars.ContextVar("routed_domain", default=None),
        over_global_ceiling=lambda: False,
        resolve_verb_key=lambda name: name,
        route_domain=_route_domain,
        db_fire=lambda *a, **k: None,
        db_post=lambda *a, **k: None,
        db_create=lambda *a, **k: {},
        refine_enabled=True,
        refine_model="test-refine",
        refine_endpoint="http://stub.local",
        refine_max_tokens=200,
        refine_timeout_s=5,
        refine_attempts=1,
        os_control_verbs_rendered="",
        browser_action_alt="",
        web_search_triggers=[],
        web_search_contexts=[],
        remember_triggers=[],
        fastpath_verbs=_FASTPATH,
        routing_enable=False,
        routing_domains={},
    )

def t_salvage_corpus():
    d = mr._salvage_refine_dispatch(
        'Sure, here is the plan: '
        '{"intent":"dispatch","tool":"open_url","args":{"url":"https://x.com"}}')
    check("salvage.embedded_json", isinstance(d, dict)
          and d.get("intent") == "dispatch" and d.get("tool") == "open_url",
          repr(d))

    d = mr._salvage_refine_dispatch(
        'To open Discord on your desktop, I will launch_app("Discord PTB").')
    check("salvage.verb_call_quoted", isinstance(d, dict)
          and d.get("tool") == "launch_app"
          and (d.get("args") or {}).get("name") == "Discord PTB"
          and d.get("_salvaged") is True, repr(d))

    d = mr._salvage_refine_dispatch(
        'I will now call open_url(url="https://wikipedia.org") for you.')
    check("salvage.kv_args", isinstance(d, dict) and d.get("tool") == "open_url"
          and (d.get("args") or {}).get("url") == "https://wikipedia.org", repr(d))

    d = mr._salvage_refine_dispatch("focus_window(Forza)")
    check("salvage.bare_positional", isinstance(d, dict)
          and d.get("tool") == "focus_window"
          and (d.get("args") or {}).get("name") == "Forza", repr(d))

    d = mr._salvage_refine_dispatch("open_url(https://news.ycombinator.com)")
    check("salvage.bare_positional_url_from_sig", isinstance(d, dict)
          and d.get("tool") == "open_url"
          and (d.get("args") or {}).get("url") == "https://news.ycombinator.com", repr(d))

    d = mr._salvage_refine_dispatch("pc_type('hello world')")
    check("salvage.bare_positional_text_from_params", isinstance(d, dict)
          and d.get("tool") == "pc_type"
          and (d.get("args") or {}).get("text") == "hello world", repr(d))

    d = mr._salvage_refine_dispatch("Running launch_verified(Steam) next.")
    check("salvage.longest_first", isinstance(d, dict)
          and d.get("tool") == "launch_verified", repr(d))

    d = mr._salvage_refine_dispatch("I'm sorry, I can't help with that request.")
    check("salvage.pure_prose_none", d is None, repr(d))

    check("salvage.empty_none", mr._salvage_refine_dispatch("") is None)

def _run(user_text, body):
    _FAKE.body = {"choices": [{"message": {"content": body}}]}
    return asyncio.run(mr.refine_intent(user_text, None))

def t_refine_envelope():
    p = _run("hey there", json.dumps(
        {"intent": "chat", "refined_text": "hey there", "reply": "Hi!",
         "web": False, "news": False, "local_state": False}))
    check("refine.chat_intent", isinstance(p, dict) and p.get("intent") == "chat",
          repr(p))
    check("refine.metadata_stamped",
          p.get("_model") == "test-refine" and "_elapsed_s" in p, repr(p))
    check("refine.bools_strict", p.get("web") is False and p.get("news") is False
          and p.get("local_state") is False, repr(p))

    p = _run("latest news on X", "```json\n" + json.dumps(
        {"intent": "agent", "refined_text": "latest news on X",
         "web": True, "news": "true", "local_state": False}) + "\n```")
    check("refine.fenced_agent", isinstance(p, dict) and p.get("intent") == "agent",
          repr(p))
    check("refine.web_true", p.get("web") is True, repr(p))
    check("refine.news_str_coerced", p.get("news") is True, repr(p))

    p = _run("open epiphany", "<think>the user wants a launch</think>" + json.dumps(
        {"intent": "dispatch", "refined_text": "open epiphany",
         "tool": "launch_app", "args": {"name": "epiphany"},
         "web": False, "news": False, "local_state": False}))
    check("refine.think_stripped", isinstance(p, dict)
          and p.get("intent") == "dispatch" and p.get("tool") == "launch_app",
          repr(p))

    p = _run("what's my cpu", json.dumps(
        {"intent": "agent", "refined_text": "report local cpu",
         "web": False, "news": False, "local_state": True}))
    check("refine.local_state_internal",
          p.get("local_state") is True and p.get("domain_type") == "internal",
          repr(p))

    p = _run("open epiphany",
             '{"intent": "dispatch", "refined_text": "open epiphany", '
             '"tool": "launch_app", "args": {"name": "epiphany"')
    check("refine.truncated_repaired", isinstance(p, dict)
          and p.get("intent") == "dispatch" and p.get("tool") == "launch_app",
          repr(p))

    p = _run("open discord",
             "To open Discord I will launch_app(\"Discord\").")
    check("refine.prose_none", p is None, repr(p))

    p = _run("hello", "")
    check("refine.empty_content_none", p is None, repr(p))

def t_cutoffs_ssot():
    mr.configure(promote_chars=5, chat_chars=7, dispatch_chars=11)
    check("cutoffs.char_globals",
          mr.REFINE_PROMOTE_CHARS == 5 and mr.REFINE_CHAT_CHARS == 7
          and mr.REFINE_DISPATCH_CHARS == 11)
    check("cutoffs.prompt_cue_rerendered",
          "<7 chars" in mr._REFINE_SYSTEM and "<11 chars" in mr._REFINE_SYSTEM
          and ">5 chars" in mr._REFINE_SYSTEM, "cue numbers not re-rendered")
    p = _run("hey there", json.dumps(
        {"intent": "chat", "refined_text": "hey there", "reply": "Hi!",
         "web": False, "news": False, "local_state": False}))
    check("cutoffs.promote_gate_follows",
          isinstance(p, dict) and p.get("intent") == "agent", repr(p))

    mr.configure(promote_chars=100, dispatch_arg_max_words=1)
    check("cutoffs.word_global", mr.REFINE_DISPATCH_ARG_MAX_WORDS == 1)
    p = _run("save this", json.dumps(
        {"intent": "dispatch", "tool": "remember", "args": {"text": "two words"},
         "web": False, "news": False, "local_state": False}))
    check("cutoffs.wordy_gate_follows",
          isinstance(p, dict) and p.get("intent") == "agent", repr(p))

    mr.configure(chat_chars=40, dispatch_chars=60, promote_chars=100,
                 dispatch_arg_max_words=3)
    check("cutoffs.restored_defaults",
          mr.REFINE_PROMOTE_CHARS == 100 and mr.REFINE_DISPATCH_ARG_MAX_WORDS == 3
          and "<40 chars" in mr._REFINE_SYSTEM and ">100 chars" in mr._REFINE_SYSTEM,
          "default cues not restored")

def t_critic_refine():
    base_body = {"messages": [{"role": "user", "content": "hi"}]}
    long_raw = "x" * 600          # >= the default MIN_CHARS (500)
    revised = {"choices": [{"message": {"content": "REVISED ANSWER"}}]}
    cli = _CritClient(revised)
    mr.configure(critic_refine_enabled=True, critic_refine_max=1,
                 critic_refine_min_chars=500)
    mr._emit_session_event = lambda *a, **k: None
    mr.DCI_ENABLED = True
    mr.DCI_FLOW_TRIGGER_CONF = 0.7

    def _call(raw, critic_ret):
        async def _crit(*a, **k):
            return critic_ret
        mr.dci_critic_pass = _crit
        return asyncio.run(mr._critic_refine_agent(
            raw, "user question", {"intent": "agent"}, "sess-1",
            client=cli, target_endpoint="http://stub.local",
            headers={}, base_body=base_body))

    mr.configure(critic_refine_enabled=False)
    check("critic.disabled_raw",
          _call(long_raw, {"act": "challenge", "confidence": 0.9}) == long_raw)
    mr.configure(critic_refine_enabled=True)

    check("critic.too_short_raw",
          _call("short answer", {"act": "challenge", "confidence": 0.9})
          == "short answer")

    check("critic.satisfied_affirm",
          _call(long_raw, {"act": "affirm", "confidence": 0.9}) == long_raw)
    check("critic.satisfied_none",
          _call(long_raw, None) == long_raw)
    check("critic.satisfied_lowconf",
          _call(long_raw, {"act": "challenge", "confidence": 0.3}) == long_raw)

    out = _call(long_raw, {"act": "challenge", "confidence": 0.9,
                           "content": "you omitted X"})
    check("critic.revised_on_challenge", out == "REVISED ANSWER", repr(out))
    check("critic.challenge_empty_concern",
          _call(long_raw, {"act": "ask", "confidence": 0.95, "content": "  "})
          == long_raw)

def t_deterministic_before_inference():
    import importlib
    from unittest.mock import patch
    target = importlib.import_module("mios_pipe.routing.refine")
    action = {"intent": "dispatch", "tool": "launch_app", "args": {"name": "OBS"},
              "_deterministic": True}
    with patch.object(target, "_deterministic_action_route", return_value=action), \
         patch.object(_FAKE, "AsyncClient", side_effect=AssertionError("inference must not run")):
        result = asyncio.run(mr.refine_intent("open OBS"))
    check("refine exact parser runs before any model transport", result == action, repr(result))
    factory = _FAKE.AsyncClient
    calls = []
    def counted(*a, **k):
        calls.append(True)
        return factory(*a, **k)
    unregistered = dict(action, tool="delete_file")
    with patch.object(target, "_deterministic_action_route", return_value=unregistered), \
         patch.object(_FAKE, "AsyncClient", side_effect=counted):
        _run("explain how to configure OBS", json.dumps({"intent": "chat", "reply": "normal path"}))
    check("unregistered/destructive parser output cannot bypass inference", bool(calls))


def t_stream_deadline_and_cancellation():
    """A real HTTPX transport stays readable forever; total time must still expire."""
    import httpx
    import types
    real_client = httpx.AsyncClient
    saved_httpx = mr.httpx
    saved_timeout, saved_attempts = mr.REFINE_TIMEOUT_S, mr.REFINE_ATTEMPTS

    async def exercise(mode):
        class Stream(httpx.AsyncByteStream):
            def __init__(self):
                self.closed = False
                self.chunks = 0
                self.started = asyncio.Event()
            async def __aiter__(self):
                if mode == "finite":
                    content = json.dumps({"intent": "chat", "reply": "ready"})
                    yield ("data: " + json.dumps({"choices": [{"delta": {"content": content}}]}) + "\n\n").encode()
                    yield b"data: [DONE]\n\n"
                    return
                while True:
                    self.chunks += 1
                    self.started.set()
                    yield b'data: {"choices":[{"delta":{"content":" "}}]}\n\n'
                    await asyncio.sleep(0.002)
            async def aclose(self):
                self.closed = True
        stream = Stream()
        transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
        clients = []
        def client(**kwargs):
            obj = real_client(transport=transport, **kwargs)
            clients.append(obj)
            return obj
        mr.httpx = types.SimpleNamespace(AsyncClient=client, HTTPError=httpx.HTTPError)
        mr.REFINE_TIMEOUT_S = 0.04 if mode == "deadline" else 1
        mr.REFINE_ATTEMPTS = 2
        async def token(*args): pass
        before = set(asyncio.all_tasks())
        started = asyncio.get_running_loop().time()
        task = asyncio.create_task(mr.refine_intent("hello", on_token=token))
        if mode == "cancel":
            await asyncio.wait_for(stream.started.wait(), 0.5)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            check("refine caller cancellation propagates", task.cancelled())
        else:
            result = await asyncio.wait_for(task, 0.5)
            if mode == "deadline":
                elapsed = asyncio.get_running_loop().time() - started
                check("live SSE obeys total deadline across retries", result is None
                      and 0.02 <= elapsed < 0.3 and stream.chunks > 2 and len(clients) == 1,
                      f"elapsed={elapsed:.3f}s chunks={stream.chunks} clients={len(clients)}")
            else:
                check("finite SSE still parses before deadline", result.get("reply") == "ready")
        await asyncio.sleep(0)
        check(f"refine {mode} closes response and client", stream.closed
              and clients and all(client.is_closed for client in clients))
        check(f"refine {mode} leaves no task", set(asyncio.all_tasks()) == before)
    try:
        for mode in ("finite", "deadline", "cancel"):
            asyncio.run(exercise(mode))
    finally:
        mr.httpx = saved_httpx
        mr.REFINE_TIMEOUT_S, mr.REFINE_ATTEMPTS = saved_timeout, saved_attempts


def t_selected_model_receipt_and_protected_stage():
    """Exercise real HTTPX requests, endpoint credentials, and the returned receipt."""
    import types
    from unittest import mock
    from mios_pipe.access import authn
    import importlib
    lanes = importlib.import_module('mios_pipe.routing.lanes_resolver')
    target_module = importlib.import_module('mios_pipe.routing.refine')
    real_client = httpx.AsyncClient
    requests = []
    def handle(request):
        requests.append(request)
        if request.headers.get("X-Node-Key") != "stage-secret":
            return httpx.Response(401, json={"error": {"message": "node key required"}})
        return httpx.Response(200, json={"choices": [{"message": {"content":
            json.dumps({"intent": "chat", "refined_text": "hello", "reply": "ready"})}}]})
    def client(**kwargs):
        return real_client(transport=httpx.MockTransport(handle), **kwargs)
    target = "http://protected.invalid/v1"
    selected = mock.AsyncMock(return_value=(target, "selected-gpu-model"))
    with mock.patch.object(target_module, "httpx", types.SimpleNamespace(
            AsyncClient=client, HTTPError=httpx.HTTPError)), \
         mock.patch.object(target_module, "_pick_inference_backend", selected), \
         mock.patch.multiple(authn, _BACKEND_KEY="cpu-secret", _AUTH_HOSTPORTS={"cpu.invalid"},
             _AGENT_AUTH_BY_HOSTPORT={"protected.invalid": "X-Node-Key: stage-secret"}):
        result = asyncio.run(mr.refine_intent("hello"))
    sent = json.loads(requests[0].content)
    check("protected refine stage uses selected model and endpoint", sent["model"] == "selected-gpu-model"
          and str(requests[0].url) == target + "/chat/completions")
    check("refine receipt reports actual selected model", result["_model"] == "selected-gpu-model"
          and result["_endpoint"] == target.removesuffix("/v1"))
    check("protected stage receives only its node credential", requests[0].headers.get("x-node-key") == "stage-secret"
          and "authorization" not in requests[0].headers)
    requests.clear()
    with mock.patch.object(target_module, "httpx", types.SimpleNamespace(
            AsyncClient=client, HTTPError=httpx.HTTPError)), \
         mock.patch.object(target_module, "REFINE_ENDPOINT", target), \
         mock.patch.object(target_module, "REFINE_MODEL", "explicit-stage-model"), \
         mock.patch.dict(__import__("os").environ, {"MIOS_REFINE_ENDPOINT": target}), \
         mock.patch.object(lanes, "_pick_tool_backend", side_effect=AssertionError("explicit endpoint moved")), \
         mock.patch.multiple(authn, _BACKEND_KEY="cpu-secret", _AUTH_HOSTPORTS={"cpu.invalid"},
             _AGENT_AUTH_BY_HOSTPORT={"protected.invalid": "X-Node-Key: stage-secret"}):
        result = asyncio.run(mr.refine_intent("hello"))
    check("explicit stage endpoint and model preserved", str(requests[0].url) == target + "/chat/completions"
          and json.loads(requests[0].content)["model"] == result["_model"] == "explicit-stage-model")


def main():
    _configure()
    t_salvage_corpus()
    t_refine_envelope()
    t_cutoffs_ssot()
    t_critic_refine()
    t_deterministic_before_inference()
    t_selected_model_receipt_and_protected_stage()
    t_stream_deadline_and_cancellation()
    print(f"\n{'ALL PASS' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0



# ==============================================================================
# Consolidated from test_mios_antifab.py (task_1006)
# ==============================================================================
# AI-hint: Standalone assert-script unit test for the anti-fabrication guard AI-related: ./mios_pipe/routing/chat.py, ./mios_chat.py, ./test_mios_chat.py AI-...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for the anti-fabrication tool-result predicate (task_119/task_120)."""

import os
import sys
import types
from unittest import mock

os.environ["MIOS_VERITY_ANTIFAB_ENABLE"] = "false"

_fails_antifab = 0

def _check_antifab(name, cond, detail=""):
    global _fails_antifab
    if not cond:
        _fails_antifab += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _install_stubs():
    """Minimal 3rd-party stand-ins so mios_chat (and the sibling graph it imports)
    loads on a bare checkout -- same recipe as test_mios_chat.py's _install_stubs."""
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

    class _FakeRequest:
        pass

    fastapi.FastAPI = lambda *a, **k: _App()
    fastapi.APIRouter = lambda *a, **k: _App()
    fastapi.Request = _FakeRequest
    fastapi.WebSocket = object
    responses = types.ModuleType("fastapi.responses")

    class _Resp:
        def __init__(self, *a, **k):
            pass

    responses.JSONResponse = type("JSONResponse", (_Resp,), {})
    responses.StreamingResponse = type("StreamingResponse", (_Resp,), {})
    for _c in ("HTMLResponse", "RedirectResponse", "Response", "PlainTextResponse"):
        setattr(responses, _c, type(_c, (_Resp,), {}))
    fastapi.responses = responses
    sys.modules.setdefault("fastapi", fastapi)
    sys.modules.setdefault("fastapi.responses", responses)

_predicate = None
_skip_reason = None
try:
    _install_stubs()
    import mios_chat
    _predicate = mios_chat._contains_tool_result_block
except Exception as exc:  # pragma: no cover -- offline-env fallback, not duplicated logic
    _skip_reason = f"{type(exc).__name__}: {exc}"

_NL = None
_nl_skip = None
try:
    _install_stubs()
    import mios_pipe.routing.native_loop as _NL
except Exception as exc:  # pragma: no cover -- offline-env fallback
    _nl_skip = f"{type(exc).__name__}: {exc}"

def t_tool_output_sentinel():
    out = _predicate("🤝 open_app output: {\"path\": \"/tmp\"}")
    _check_antifab("sentinel: '🤝 <verb> output:' -> True", out is True, out)

def t_success_json_tool_order():
    out = _predicate('{"success": true, "tool": "launch_app"}')
    _check_antifab("json(success,tool order): -> True", out is True, out)

def t_tool_json_success_order():
    out = _predicate('{"tool": "launch_app", "success": true}')
    _check_antifab("json(tool,success order): -> True", out is True, out)

def t_ordinary_prose():
    out = _predicate("Sure, I can help you open that application.")
    _check_antifab("ordinary prose -> False", out is False, out)

def t_empty_and_none():
    _check_antifab("empty string -> False", _predicate("") is False)
    _check_antifab("None -> False", _predicate(None) is False)

_FAB01_SYNTH = (
    "Here are the games installed on your system:\n\n"
    "🤝 apps output (truncated for brevity):\n"
    '{"success": true, "tool": "apps", "apps": ['
    '{"name": "example-app-1", "version": "unknown"}, '
    '{"name": "example-app-2", "version": "unknown"}, '
    '{"name": "example-app-3", "version": "unknown"}, '
    '{"name": "example-app-4", "version": "unknown"}]}'
)

def t_fab01_synth_strips_fired_verb_block():
    ans = _NL._guard_fabricated_execution(
        _FAB01_SYNTH, surfaced_raw_evidence=False, m2=[], enable=True)
    _check_antifab("FAB-01: fabricated '🤝 apps output' block stripped (fired-membership "
          "does NOT save it)", "🤝" not in ans and "example-app-1" not in ans, ans)
    _check_antifab("FAB-01: real synthesized prose survives the strip",
          ans.strip().startswith("Here are the games installed"), ans)

def t_fab01_raw_evidence_provenance():
    real_blk = '{"success": true, "tool": "open_app", "pid": 4242}'
    m2 = [{"role": "tool", "name": "open_app", "content": real_blk}]
    keep = _NL._guard_fabricated_execution(
        real_blk, surfaced_raw_evidence=True, m2=m2, enable=True)
    _check_antifab("FAB-01 raw path: success-JSON matching real _m2 output PRESERVED",
          keep.strip() == real_blk, keep)
    fake_blk = '{"success": true, "tool": "open_app", "pid": 9999}'
    drop = _NL._guard_fabricated_execution(
        fake_blk, surfaced_raw_evidence=True, m2=m2, enable=True)
    _check_antifab("FAB-01 raw path: NON-matching success-JSON stripped",
          "9999" not in drop, drop)

def t_fab01_skill_recipe_subsumed():
    ans = ("You can do that with a recipe.\n\n"
           "🤝 skill:foo output: {\"steps\": 3}")
    out = _NL._guard_fabricated_execution(
        ans, surfaced_raw_evidence=False, m2=[], enable=True)
    _check_antifab("FAB-01: skill/recipe sentinel in synthesized prose stripped "
          "(false-positive subsumed)", "🤝" not in out, out)

_FAB02_CORPUS = (
    "In 2024, notable releases included Prince of Persia: The Lost Crown, a "
    "Metroidvania from Ubisoft Montpellier, and Eiyuden Chronicle: Hundred Heroes, "
    "a spiritual successor to Suikoden. Source: Wikipedia, Polygon."
)
_FAB02_ANS = (
    "## Notable 2024 Games\n\n"
    "Prince of Persia: The Lost Crown launched in January 2024, and Eiyuden "
    "Chronicle: Hundred Heroes followed. Both were widely covered by Polygon.\n\n"
    "## Major 2025 Announcements\n\n"
    "Starfield Shattered Space DLC, FIFA 26, and a God of War Ragnarok expansion "
    "were unveiled, according to IGN, though not captured in the excerpt."
)

def t_fab02_strips_only_ungrounded_section():
    out = _NL._guard_entity_grounding(
        _FAB02_ANS, _FAB02_CORPUS, gate=True, enable=True,
        min_entities=3, ground_min=0.34, note="(unverified omitted)")
    _check_antifab("FAB-02: fabricated 2025 section stripped",
          "Starfield" not in out and "FIFA 26" not in out and "Ragnarok" not in out, out)
    _check_antifab("FAB-02: grounded 2024 section survives",
          "Prince of Persia" in out and "Eiyuden Chronicle" in out, out)
    _check_antifab("FAB-02: honest note appended", "(unverified omitted)" in out, out)

def t_fab02_all_grounded_untouched():
    grounded = ("## Notable 2024 Games\n\nPrince of Persia: The Lost Crown and "
                "Eiyuden Chronicle: Hundred Heroes released in 2024 per Polygon.")
    out = _NL._guard_entity_grounding(
        grounded, _FAB02_CORPUS, gate=True, enable=True,
        min_entities=3, ground_min=0.34, note="(unverified omitted)")
    _check_antifab("FAB-02: fully grounded answer untouched", out == grounded, out)

def t_fab02_degrade_open():
    out = _NL._guard_entity_grounding(
        _FAB02_ANS, "", gate=True, enable=True,
        min_entities=3, ground_min=0.34, note="(unverified omitted)")
    _check_antifab("FAB-02: empty corpus degrades-open (byte-identical)", out == _FAB02_ANS, out)
    cjk = "## 概要\n\nこれは日本語のテキストです。ゲームについて。\n\n## 続き\n\nもっと日本語。"
    out2 = _NL._guard_entity_grounding(
        cjk, _FAB02_CORPUS, gate=True, enable=True,
        min_entities=3, ground_min=0.34, note="(unverified omitted)")
    _check_antifab("FAB-02: caseless/CJK answer degrades-open (byte-identical)", out2 == cjk, out2)

def t_flag_off_passthrough():
    _check_antifab("gate: native-loop _ANTIFAB_ENABLE reflects env=false",
          _NL._ANTIFAB_ENABLE is False, _NL._ANTIFAB_ENABLE)
    ex = _NL._guard_fabricated_execution(
        _FAB01_SYNTH, surfaced_raw_evidence=False, m2=[], enable=_NL._ANTIFAB_ENABLE)
    _check_antifab("gate: FAB-01 guard passthrough when disabled (byte-identical)",
          ex == _FAB01_SYNTH, ex)
    gr = _NL._guard_entity_grounding(
        _FAB02_ANS, _FAB02_CORPUS, gate=True, enable=_NL._ANTIFAB_ENABLE,
        min_entities=3, ground_min=0.34, note="(unverified omitted)")
    _check_antifab("gate: FAB-02 guard passthrough when disabled (byte-identical)",
          gr == _FAB02_ANS, gr)

def _main_antifab():
    if _predicate is None:
        print(f"SKIP: mios_chat unimportable in this environment -- {_skip_reason}")
        return 0
    t_tool_output_sentinel()
    t_success_json_tool_order()
    t_tool_json_success_order()
    t_ordinary_prose()
    t_empty_and_none()
    if _NL is None:
        print(f"SKIP: native_loop unimportable in this environment -- {_nl_skip}")
    else:
        t_fab01_synth_strips_fired_verb_block()
        t_fab01_raw_evidence_provenance()
        t_fab01_skill_recipe_subsumed()
        t_fab02_strips_only_ungrounded_section()
        t_fab02_all_grounded_untouched()
        t_fab02_degrade_open()
        t_flag_off_passthrough()
    print(f"\n{'ok' if _fails_antifab == 0 else str(_fails_antifab) + ' FAILED'}")
    return 1 if _fails_antifab else 0


def _run_extra_antifab():
    import os
    _saved_env = dict(os.environ)
    try:
        return _main_antifab()
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



def _run_all_folded_refine_suites():
    rc = _run_extra_antifab()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _rc_main = main()
    _run_all_folded_refine_suites()
    raise SystemExit(_rc_main)
