#!/usr/bin/env python3
# AI-hint: stdlib assert-script for mios_native_loop -- exercises the NATIVE
# AI-related: ./mios_native_loop.py
# AI-functions: _run
"""Offline test for mios_native_loop._respond_native_loop_direct loop control."""

import asyncio
import contextvars
import json
import types

import mios_native_loop as M

FINAL_ANSWER = "The verified answer is 42."

def _async_const(value):
    async def _f(*a, **k):
        return value
    return _f

class _FakeResp:
    def __init__(self, content):
        self._c = content

    def json(self):
        return {"choices": [{"message": {"content": self._c}, "finish_reason": "stop"}]}

class _FakeClient:
    """Stands in for httpx.AsyncClient: only `.post` (final completion) is used
    on the non-streaming / no-emit path; `.stream` must NOT be touched."""

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **k):
        return _FakeResp(FINAL_ANSWER)

    def stream(self, *a, **k):
        stream_content = getattr(self, "stream_content", FINAL_ANSWER)
        stream_reason = getattr(self, "stream_reason", "stop")
        class Stream:
            status_code = 200
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return False
            def raise_for_status(self):
                pass
            async def aiter_lines(self):
                yield "data: " + json.dumps({"choices": [{"delta": {"content": stream_content},
                    "finish_reason": None}]})
                yield "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": stream_reason}]})
                yield "data: [DONE]"
        return Stream()

_FAKE_HTTPX = types.SimpleNamespace(
    AsyncClient=_FakeClient,
    Timeout=lambda *a, **k: None,
)

def _base_configure(verb_catalog, dispatch_calls, loop_calls):
    async def _dispatch(verb, args, *, session_id=None):
        dispatch_calls.append((verb, args))
        return {"success": True}

    async def _secondary_loop(*a, **k):
        loop_calls.append(True)
        return [
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "web_search"}}]},
            {"role": "tool", "content": "tool output evidence"},
            {"role": "assistant", "content": ""},
        ]

    M.configure(
        _get_agent_registry=None, _live_agent_names=None, _agent_lane=None,
        _call_agent_complete=None, _get_client=None, _endpoint_key=None,
        BACKEND="http://x/v1", BACKEND_MODEL="m", _BACKEND_KEY=None,
        _BACKEND_HOSTPORT="x", REFINE_ENDPOINT="http://x/v1", REFINE_MODEL="m",
        STABLE_PREFIX=False, STABLE_PREFIX_HINT=False, STABLE_PREFIX_TAIL=0,
        NATIVE_LOOP_TOOL_CAP=0, NATIVE_LOOP_TIMEOUT_S=5.0,
        NATIVE_LOOP_CAPABILITY_GROUNDING=False, NATIVE_LOOP_PERSISTENCE=False,
        _NATIVE_LOOP_PERSISTENCE_PROSE="", NATIVE_LOOP_BREADTH_GUIDANCE=False,
        _NATIVE_LOOP_BREADTH_PROSE="", NATIVE_LOOP_REFLECTION=False,
        _NATIVE_LOOP_REFLECTION_PROSE="", NATIVE_LOOP_RECENCY_RANGE="day",
        NATIVE_LOOP_RECENCY_FANOUT=5, NATIVE_LOOP_RECENCY_DEFAULTS=False,
        NATIVE_LOOP_MATH_HINT=False, NATIVE_LOOP_DATE_ANCHOR=False,
        NATIVE_LOOP_QUERY_REFORMULATE=False, NATIVE_LOOP_STREAM_TOKENS=False,
        NATIVE_LOOP_STREAM_CHUNK=0, NATIVE_LOOP_STREAM_DELAY_MS=0,
        _ROUTING_DOMAINS={},
        _VERB_CATALOG=verb_catalog,
        _routed_domain_var=contextvars.ContextVar("rd", default=None),
        _orch_ctx_var=contextvars.ContextVar("orch", default=None),
        _recency_ctx_var=contextvars.ContextVar("rec", default=None),
        _worker_tools_core_cache=(lambda: None),
        dispatch_mios_verb=_dispatch,
        _usage_estimate=lambda p, c: {"total_tokens": 0},
        _identity_answer=lambda: "",
        _agent_contract=lambda: "",
        _capability_grounding=lambda vc: "",
        _env_grounding=lambda: "",
        _recall_agent_memory=_async_const(""),
        _recall_knowledge=_async_const(""),
        _rag_enrich=_async_const(""),
        _tool_pref_block=_async_const(""),
        _current_date_str=lambda: "2026-01-01",
        _worker_tools_surface_async=_async_const([]),
        _read_tool_enrich=_async_const(""),
        _needs_compute=_async_const(False),
        _src_record=lambda items: None,
        _src_collected=lambda: [],
        _src_record_from_text=lambda t: None,
        _endpoint_supports_parallel_tools=lambda ep: False,
        _filter_relevant_sources=lambda refs, *t: refs,
        _sources_markdown=lambda refs: "",
        _sources_annotations=lambda refs, t: [],
        _sources_metadata=lambda refs: [],
        _store_knowledge=lambda **k: None,
        _iter_answer_chunks=lambda text, size: [text],
        _write_skill_md_fire=lambda **k: None,
    )
    M.httpx = _FAKE_HTTPX
    M.polish_response = _async_const("")
    M._v1_secondary_tool_loop = _secondary_loop

def _body_text(resp):
    return json.loads(bytes(resp.body).decode("utf-8"))["choices"][0]["message"]["content"]

def _run():
    dcalls, lcalls = [], []
    _base_configure({"remember": {}}, dcalls, lcalls)
    resp = asyncio.run(M._respond_native_loop_direct(
        {"news": False, "web": False, "local_state": False},
        streaming=False, chat_id="c1", model="m", session_id="s1",
        last_user_text="remember that my editor is vim",
        persona_system="", messages=[{"role": "user", "content": "remember that my editor is vim"}],
        request=None, emit=None, tool_choice=None))
    txt = _body_text(resp)
    assert "vim" in txt, txt
    assert ("remember", {"fact": "my editor is vim"}) in dcalls, dcalls
    assert lcalls == [], "secondary tool loop must NOT run on the remember fast-path"
    print("branch-1 remember fast-path terminates early: OK ->", repr(txt))

    dcalls, lcalls = [], []
    _base_configure({}, dcalls, lcalls)  # no `remember`/`coderun` verbs -> no fast-path, no prefetch
    resp = asyncio.run(M._respond_native_loop_direct(
        {"news": False, "web": False, "local_state": False},
        streaming=False, chat_id="c2", model="m", session_id="s2",
        last_user_text="give me an overview of the project",
        persona_system="", messages=[{"role": "user", "content": "give me an overview of the project"}],
        request=None, emit=None, tool_choice=None))
    txt = _body_text(resp)
    assert lcalls == [True], "secondary tool loop must run exactly once"
    assert txt == FINAL_ANSWER, txt
    print("branch-2 tool loop runs then final answer terminates: OK ->", repr(txt))

    captured = []
    original = "Explain this topic in two sentences. Do not change files or execute commands."
    _base_configure({}, [], [])
    async def capture_constraints(*args, **kwargs):
        captured.extend(args[4])
        kwargs["terminal_response"]["content"] = FINAL_ANSWER
        return list(args[4]) + [{"role": "assistant", "content": FINAL_ANSWER}]
    M._v1_secondary_tool_loop = capture_constraints
    resp = asyncio.run(M._respond_native_loop_direct(
        {"intent": "agent", "refined_text": "A shortened topic", "intended_outcome": "A short explanation"},
        streaming=False, chat_id="constraints", model="m", session_id="s",
        last_user_text=original, persona_system="",
        messages=[{"role": "user", "content": original}],
        request=None, emit=None, tool_choice=None))
    content = "\n".join(str(message.get("content") or "") for message in captured)
    assert original in content, captured
    assert "original user messages" in content and "authoritative over this refinement" in content
    assert "do not anchor on the user's literal wording" not in content
    assert _body_text(resp) == FINAL_ANSWER
    print("native advisory refinement preserves original constraints and scope: OK")

    class EmptyClient(_FakeClient):
        stream_content = ""
        async def post(self, url, **kwargs):
            return _FakeResp("")

    for streaming in (False, True):
        _base_configure({}, [], [])
        M._v1_secondary_tool_loop = _async_const([])
        M.httpx = types.SimpleNamespace(AsyncClient=EmptyClient, Timeout=lambda *a, **k: None)
        resp = asyncio.run(M._respond_native_loop_direct(
            {"news": False, "web": False, "local_state": False},
            streaming=streaming, chat_id="unavailable", model="m", session_id="s",
            last_user_text="give an overview", persona_system="",
            messages=[{"role": "user", "content": "give an overview"}],
            request=None, emit=None, tool_choice=None))
        if streaming:
            async def collect():
                return b"".join([chunk async for chunk in resp.body_iterator])
            wire = asyncio.run(collect()).decode("utf-8")
            assert '"code": "inference_unavailable"' in wire, wire
            assert '"finish_reason": "stop"' not in wire, wire
        else:
            assert resp.status_code == 503
            assert json.loads(resp.body)["error"]["code"] == "inference_unavailable"
    print("empty backend is 503 for JSON and streaming clients: OK")

    for endpoint in ("http://fallback", "http://fallback/v1", "http://fallback/v1/"):
        urls = []
        class FallbackClient(_FakeClient):
            async def post(self, url, **kwargs):
                urls.append(url)
                return _FakeResp(FINAL_ANSWER if url.startswith("http://fallback/") else "")
        _base_configure({}, [], [])
        M.REFINE_ENDPOINT = endpoint
        M._v1_secondary_tool_loop = _async_const([])
        M.httpx = types.SimpleNamespace(AsyncClient=FallbackClient, Timeout=lambda *a, **k: None)
        resp = asyncio.run(M._respond_native_loop_direct(
            {"news": False, "web": False, "local_state": False},
            streaming=False, chat_id="fallback", model="m", session_id="s",
            last_user_text="give an overview", persona_system="",
            messages=[{"role": "user", "content": "give an overview"}],
            request=None, emit=None, tool_choice=None))
        assert _body_text(resp) == FINAL_ANSWER
        assert urls[-1] == "http://fallback/v1/chat/completions", urls
    print("light fallback normalizes /v1 exactly once: OK")

    for persist in (False, True):
        for streaming in (False, True):
            stored = []
            _base_configure({}, [], [])
            M._loads_lenient = json.loads
            M._store_knowledge = lambda **kwargs: stored.append(kwargs)
            resp = asyncio.run(M._respond_native_loop_direct(
                {"news": False, "web": False, "local_state": False},
                streaming=streaming, chat_id="storage", model="m", session_id="s",
                last_user_text="give an overview", persona_system="",
                messages=[{"role": "user", "content": "give an overview"}],
                request=None, emit=None, tool_choice=None, persist_response=persist))
            if streaming:
                async def drain():
                    return [chunk async for chunk in resp.body_iterator]
                asyncio.run(drain())
            assert bool(stored) == persist, (persist, streaming, stored)
    print("storage preference survives JSON and streaming native paths: OK")

    print("ALL OK")

class _FakeResp2:
    """status_code + .json() in the OpenAI (`choices`) shape -- MiOS is /v1-only --
    for the moved formulator/local-state tests. (`native` kept for signature
    compatibility; the retired `message` shape is no longer emitted.)"""
    def __init__(self, content, *, status=200, native=False):
        self._c = content
        self.status_code = status
        self.text = content
        self._native = native

    def json(self):
        if self._native:
            return {"message": {"content": self._c}}
        return {"choices": [{"message": {"content": self._c}, "finish_reason": "stop"}]}

def _fake_httpx_returning(content, **kw):
    class _C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **k):
            return _FakeResp2(content, **kw)

    return types.SimpleNamespace(AsyncClient=_C, Timeout=lambda *a, **k: None)

def _run_moved():
    """Cover the three functions extracted into mios_native_loop: the micro-LLM
    compute/web formulators + the local-state responder. All network is mocked;
    payload tokens are SYNTHETIC non-dictionary strings (no baked keywords)."""
    assert asyncio.run(M._formulate_compute_snippet("")) == ""
    M.httpx = _fake_httpx_returning("```python\nZZQX = 41 + 1\nprint(ZZQX)\n```")
    out = asyncio.run(M._formulate_compute_snippet("Vmbtok zzqx plff"))
    assert out == "ZZQX = 41 + 1\nprint(ZZQX)", repr(out)
    print("moved _formulate_compute_snippet fence-strip: OK ->", repr(out))

    M.httpx = _fake_httpx_returning("<think>scratch</think>QRZL Vmbtok 9000")
    out = asyncio.run(M._formulate_web_query("zzqx plff", "GPU: QRZL-9000"))
    assert out == "QRZL Vmbtok 9000", repr(out)
    assert asyncio.run(M._formulate_web_query("", "")) == ""
    print("moved _formulate_web_query think-strip + degrade: OK ->", repr(out))

    M._env_grounding = lambda: ""
    M._LOCAL_STATE_SYSTEM = "SSOT-LOCAL-STATE-PROMPT"
    M._polish_post = (lambda ep, model, msgs, mx, temperature=0.0:
                      ("http://x/v1/chat/completions",
                       {"model": model, "messages": msgs}))
    assert asyncio.run(M._format_local_state("zzqx", "")) is None
    M.httpx = _fake_httpx_returning("QRZL Vmbtok enumerated")
    out = asyncio.run(M._format_local_state("zzqx plff", "live: QRZL=1"))
    assert out == "QRZL Vmbtok enumerated", repr(out)
    M.httpx = _fake_httpx_returning("   ")
    assert asyncio.run(M._format_local_state("zzqx plff", "live: QRZL=1")) is None
    print("moved _format_local_state polish + native-shape extract: OK ->", repr(out))
    print("MOVED OK")



# ==============================================================================
# Consolidated from test_mios_pty.py (task_1006)
# ==============================================================================
# AI-hint: Stdlib offline tests for mios_pipe.routing.pty -- the persistent shell substrate's pure protocol (SHELL-01). No tmux, no subproc...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md

import sys

from mios_pipe.routing import pty as P

_fails_pty = 0

def _check_pty(name, cond, detail=""):
    global _fails_pty
    if cond:
        print(f"ok   - {name}")
    else:
        _fails_pty += 1
        print(f"FAIL - {name}" + (f" -- {detail}" if detail else ""))

def t_session_key_cannot_escape():
    k = P.session_key("../../etc/passwd")
    _check_pty("key: path traversal is neutralised",
          ".." not in k and "/" not in k, k)
    k = P.session_key("a; rm -rf /")
    _check_pty("key: shell metacharacters are neutralised",
          all(c.isalnum() or c in "-_" for c in k), k)
    _check_pty("key: always namespaced", P.session_key("x").startswith(P.SESSION_PREFIX))
    _check_pty("key: empty id still yields a unique name",
          P.session_key("") != P.SESSION_PREFIX and len(P.session_key("")) > 6,
          P.session_key(""))
    _check_pty("key: None is handled", P.session_key(None).startswith(P.SESSION_PREFIX))
    _check_pty("key: is stable for the same id",
          P.session_key("chat-9") == P.session_key("chat-9"))

def t_long_ids_do_not_collide():
    """Truncating alone would map two long ids to one shell -- and one chat
    reading another's cwd/env is the whole risk this substrate introduces."""
    a = "s" * 80 + "-alpha"
    b = "s" * 80 + "-beta"
    ka, kb = P.session_key(a), P.session_key(b)
    _check_pty("key: long ids are capped", len(ka) <= 48 + len(P.SESSION_PREFIX), ka)
    _check_pty("key: two long ids do NOT collide", ka != kb, f"{ka} vs {kb}")

def t_session_path_is_contained():
    p = P.session_path("../../../root", "/var/lib/mios/shell-sessions")
    _check_pty("path: cannot walk out of the root",
          p.startswith("/var/lib/mios/shell-sessions/") and ".." not in p, p)

def t_tmux_argv():
    _check_pty("argv: new", P.tmux_argv("new", "c1")[:4] == ["tmux", "-L", "mios", "new-session"])
    _check_pty("argv: send carries the command",
          "echo hi" in P.tmux_argv("send", "c1", command="echo hi"))
    _check_pty("argv: kill targets the namespaced key",
          P.session_key("c1") in P.tmux_argv("kill", "c1"))
    _check_pty("argv: an unknown action yields [] rather than a guess",
          P.tmux_argv("frobnicate", "c1") == [])

def t_wrap_requires_a_real_nonce():
    n = P.new_nonce()
    _check_pty("nonce: is long hex", len(n) >= 32 and all(c in "0123456789abcdef" for c in n))
    _check_pty("nonce: differs each call", P.new_nonce() != P.new_nonce())
    w = P.wrap_command("echo hi", n)
    _check_pty("wrap: the command is preserved", "\necho hi\n" in w, w)
    _check_pty("wrap: both sentinels are emitted",
          w.count(P.MARKER_PREFIX) == 2 and f"{n}-BEGIN" in w and f"{n} $? $PWD" in w, w)
    # The framing is printed in TWO pieces precisely so the terminal ECHO of
    # this line never contains an assembled marker -- otherwise the echo parses
    # as the result and the first command back reports a null exit code.
    _check_pty("wrap: no assembled marker appears in the command text",
          (P.MARKER_PREFIX + n) not in w, w)
    for bad in ("", None, "not-hex", "abc"):
        try:
            P.wrap_command("echo hi", bad)
            _check_pty(f"wrap: rejects a bad nonce {bad!r}", False)
        except ValueError:
            _check_pty(f"wrap: rejects a bad nonce {bad!r}", True)

def t_parse_happy_path():
    n = P.new_nonce()
    cap = (f"prompt$ stuff\n{P.MARKER_PREFIX}{n}-BEGIN\n"
           f"line one\nline two\n{P.MARKER_PREFIX}{n} 0 /tmp\n")
    r = P.parse_result(cap, n)
    _check_pty("parse: exit code", r and r["exit_code"] == 0, str(r))
    _check_pty("parse: cwd", r and r["cwd"] == "/tmp", str(r))
    _check_pty("parse: output excludes the marker line",
          r and P.MARKER_PREFIX not in r["output"], str(r))
    _check_pty("parse: output is otherwise verbatim",
          r and r["output"] == "line one\nline two", repr(r["output"]))

    cap = (f"{P.MARKER_PREFIX}{n}-BEGIN\nboom\n"
           f"{P.MARKER_PREFIX}{n} 127 /home/u\n")
    r = P.parse_result(cap, n)
    _check_pty("parse: a non-zero exit is carried", r and r["exit_code"] == 127, str(r))

def t_unfinished_is_not_success():
    n = P.new_nonce()
    _check_pty("parse: no marker yet -> None, NOT exit 0",
          P.parse_result("still running...\n", n) is None)
    _check_pty("parse: empty capture -> None", P.parse_result("", n) is None)

def t_output_cannot_forge_completion():
    """The security property: a command that PRINTS a marker-shaped line must
    not be read as having completed, because it cannot know this command's
    nonce."""
    mine = P.new_nonce()
    attacker = P.new_nonce()
    cap = (f"{P.MARKER_PREFIX}{mine}-BEGIN\n"
           f"pretending to finish\n{P.MARKER_PREFIX}{attacker} 0 /root\n"
           "still actually running\n")
    _check_pty("spoof: a marker with a DIFFERENT nonce is ignored",
          P.parse_result(cap, mine) is None, str(P.parse_result(cap, mine)))

    cap = f"here is the literal prefix {P.MARKER_PREFIX} and no nonce\n"
    _check_pty("spoof: a bare prefix with no nonce is ignored",
          P.parse_result(cap, mine) is None)

    # An old capture replayed into the pane must not end the CURRENT command
    # early: the LAST marker for this nonce is the authoritative one.
    cap = (f"{P.MARKER_PREFIX}{mine}-BEGIN\n"
           f"{P.MARKER_PREFIX}{mine} 1 /old\n"
           "more real output\n"
           f"{P.MARKER_PREFIX}{mine} 0 /new\n")
    r = P.parse_result(cap, mine)
    _check_pty("spoof: the LAST real marker wins", r and r["cwd"] == "/new", str(r))
    _check_pty("spoof: earlier output is retained",
          r and "more real output" in r["output"], str(r))

def t_idle_reaper_is_conservative():
    _check_pty("idle: past the window -> reap", P.is_idle(1000.0, now=5000.0, idle_s=100) is True)
    _check_pty("idle: inside the window -> keep", P.is_idle(4950.0, now=5000.0, idle_s=100) is False)
    for bad in (None, "", "not-a-number", 0, -1):
        _check_pty(f"idle: bad bookkeeping {bad!r} -> never reap",
              P.is_idle(bad, now=5000.0, idle_s=100) is False)

def _main_pty():
    t_session_key_cannot_escape()
    t_long_ids_do_not_collide()
    t_session_path_is_contained()
    t_tmux_argv()
    t_wrap_requires_a_real_nonce()
    t_parse_happy_path()
    t_unfinished_is_not_success()
    t_output_cannot_forge_completion()
    t_idle_reaper_is_conservative()
    print(f"\n{_fails_pty} FAILED" if _fails_pty else "\nok")
    return 1 if _fails_pty else 0


def _run_extra_pty():
    import os
    _saved_env = dict(os.environ)
    try:
        return _main_pty()
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



def _run_all_folded_native_loop_suites():
    rc = _run_extra_pty()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

def _run_stream_cleanup_and_local_grounding():
    async def cleanup():
        _base_configure({}, [], [])
        entered, closed = asyncio.Event(), asyncio.Event()
        preflight = []
        async def recall(*args): preflight.append(True); return ""
        async def blocked(*args, **kwargs):
            entered.set()
            try: await asyncio.Event().wait()
            finally: closed.set()
        M._v1_secondary_tool_loop = blocked
        M._recall_agent_memory = recall
        before = set(asyncio.all_tasks())
        response = await M._respond_native_loop_direct(None, streaming=True,
            chat_id="c", model="m", session_id=None, last_user_text="Opaque work request.",
            persona_system="", messages=[{"role": "user", "content": "Opaque work request."}], request=None)
        async def consume():
            async for _ in response.body_iterator: pass
        reader = asyncio.create_task(consume())
        await asyncio.wait_for(entered.wait(), 0.5)
        reader.cancel()
        try: await reader
        except asyncio.CancelledError: pass
        assert closed.is_set() and set(asyncio.all_tasks()) == before
        assert len(preflight) == 1, preflight
    asyncio.run(cleanup())
    print("production native stream cancellation drains worker; preflight exactly once: OK")
    async def grounding():
        for needs_state in (False, True):
            _base_configure({}, [], [])
            M._ROUTING_DOMAINS = {"opaque-capability": {"verbs": ["inspect_opaque"]}}
            M._routed_domain_var.set("opaque-capability")
            captured = []
            async def loop(client, endpoint, model, headers, messages, *args, **kwargs):
                captured.extend(messages)
                return messages
            M._v1_secondary_tool_loop = loop
            await M._respond_native_loop_direct({"intent": "agent", "local_state": needs_state},
                streaming=False, chat_id="c", model="m", session_id=None,
                last_user_text="An opaque capability request.", persona_system="",
                messages=[{"role": "user", "content": "An opaque capability request."}], request=None)
            system = captured[0]["content"]
            assert ("SOURCE = THIS MACHINE" in system) is needs_state
            assert "inspect_opaque" in system  # relevant tools remain available
    asyncio.run(grounding())
    print("capability domains preserve tool hints; local-state mandate only for actual local state: OK")


def _run_terminal_completion_reuse():
    import mios_secondary_loop as secondary
    for mode in ("buffered", "emit", "stream"):
        _base_configure({}, [], [])
        secondary.configure(apply_outbound_auth=lambda *a: None,
            endpoint_supports_parallel_tools=lambda *a: False,
            db_read=lambda *a, **k: None)
        secondary._rescue_tool_calls = lambda *a: []
        class Client(_FakeClient):
            calls = 0
            async def post(self, url, **kwargs):
                Client.calls += 1
                assert Client.calls == 1, "accepted tool-loop answer triggered repeated inference"
                response = _FakeResp("An opaque terminal assistant answer.")
                response.status_code = 200
                return response
        M.httpx = types.SimpleNamespace(AsyncClient=Client, Timeout=lambda *a, **k: None)
        M._v1_secondary_tool_loop = secondary._v1_secondary_tool_loop
        emitted = []
        async def run():
            response = await M._respond_native_loop_direct(
                {"local_state": False, "web": False, "news": False},
                streaming=mode == "stream", chat_id="terminal", model="m", session_id=None,
                last_user_text="An opaque request.", persona_system="",
                messages=[{"role": "user", "content": "An opaque request."}], request=None,
                emit=emitted.append if mode == "emit" else None)
            if mode == "stream":
                content = b"".join([chunk async for chunk in response.body_iterator]).decode()
            else: content = _body_text(response)
            assert "An opaque terminal assistant answer." in content, content
            assert Client.calls == 1
        asyncio.run(run())
    print("actual secondary/native terminal completion reused in all output modes: OK")


def _run_registered_cpu_fallback():
    import time
    import httpx
    import mios_turn as turn
    import mios_agentreg as registry_module
    import test_mios_agent_call as calls
    import mios_pipe.routing.agent_call as actual_calls
    from mios_pipe.vram_scheduler import _endpoint_key
    import logging
    nodes = {"opaque-capacity": {"endpoint": "https://capacity.invalid:9764/v1",
        "model": "configured-cpu-alias", "lane": "cpu", "health_gate": True}}
    registry = {}
    old_section = registry_module._toml_section
    try:
        registry_module._toml_section = lambda name: nodes if name == "nodes" else {}
        registry_module.configure(logger=logging.getLogger("offline"), opt_int_mb=lambda v: 0)
        registry_module._load_node_pool(registry)
    finally: registry_module._toml_section = old_section
    cpu_name = next(iter(registry))
    registry.update({
        "disabled": {**registry[cpu_name], "enabled": False},
        "research-only": {**registry[cpu_name], "research_only": True},
        "blank": {"lane": "cpu", "endpoint": "", "model": "unused"},
        "gpu-labelled": {**registry[cpu_name], "lane": "gpu"},
        "same-primary": {**registry[cpu_name], "endpoint": "http://x/"},
        "no-model": {**registry[cpu_name], "model": ""}})
    # Invalid candidates come first, so selection must apply each policy gate.
    registry = {**{k:v for k,v in registry.items() if k != cpu_name}, cpu_name: registry[cpu_name]}
    async def run(case):
        _base_configure({}, [], [])
        calls._configure()
        actual_calls.RR_ENABLE = False  # undo the imported fixture's RR-specific scenario
        import mios_secondary_loop as secondary
        secondary.configure(apply_outbound_auth=lambda *a: None,
            endpoint_supports_parallel_tools=lambda *a: False,
            secondary_tool_max_iters=1 if case == "gpu-limit" else 4,
            secondary_replan_max=1, daemon_diagnose_enable=False)
        secondary._rescue_tool_calls = lambda *a: []
        async def execute(*args, **kwargs):
            return [{"role": "tool", "tool_call_id": "opaque-call", "content": '{"success": false}'}], True
        secondary._exec_tool_calls = execute
        active, admitted, requests, readiness = [], [], [], []
        async def admit(*args, **kwargs): admitted.append(args)
        async def model_active(ep, model, delta, est): active.append(delta)
        actual_calls._admit = admit
        actual_calls._model_active = model_active
        actual_calls._apply_outbound_auth = lambda headers, ep: headers.update(Authorization="Bearer offline-cpu-key")
        live = case != "unready"
        current = {**registry, "duplicate-capacity": registry[cpu_name]} if case != "empty-registry" else {}
        turn.configure(_AGENT_REGISTRY=current,
            _NODE_LIVE={name: (time.time(), live) for name in current},
            _should_health_probe=lambda cfg: True, _probe_auth_headers=lambda ep: {})
        async def ready(): readiness.append(True); return await turn._live_agent_names()
        entered, closed = asyncio.Event(), asyncio.Event()
        class Pending(httpx.AsyncByteStream):
            async def __aiter__(self):
                entered.set()
                await asyncio.Event().wait()
                yield b"unreachable"
            async def aclose(self): closed.set()
        def transport(request):
            requests.append(request)
            payload = json.loads(request.content)
            assert actual_calls._dispatch_agent_var.get() == cpu_name
            assert str(request.url) == nodes["opaque-capacity"]["endpoint"] + "/chat/completions"
            assert payload["model"] == "configured-cpu-alias"
            assert request.headers["Authorization"] == "Bearer offline-cpu-key"
            assert payload["max_tokens"] > 0
            if case in ("cancel", "timeout"): return httpx.Response(200, stream=Pending())
            answer = "" if case == "empty-cpu" else "Configured CPU answer."
            return httpx.Response(200, json={"choices": [{"message": {"content": answer}}]})
        class Primary(_FakeClient):
            calls = 0
            async def post(self, url, **kwargs):
                Primary.calls += 1
                assert url == "http://x/v1/chat/completions"
                if case == "gpu-transport-failure": raise OSError("offline transport failure")
                response = _FakeResp("Primary answer." if case in ("primary-success", "gpu-success")
                    or (case in ("gpu-empty", "gpu-limit") and Primary.calls == 2)
                    or (case == "gpu-tool-recovery" and Primary.calls == 3) else "")
                response.status_code = 502 if case == "gpu-http-failure" else 200
                response.text = "offline primary failure"
                if case in ("gpu-limit", "gpu-tool-recovery") and Primary.calls == 1:
                    response.json = lambda: {"choices": [{"message": {"tool_calls": [
                        {"id": "opaque-call", "type": "function", "function": {"name": "opaque_tool", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]}
                return response
            def stream(self, method, url, **kwargs):
                Primary.calls += 1
                assert url == "http://x/v1/chat/completions"
                class Unavailable:
                    status_code = 503
                    async def __aenter__(self): return self
                    async def __aexit__(self, *args): return False
                    async def aread(self): return b"offline primary unavailable"
                return Unavailable()
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            M.httpx = types.SimpleNamespace(AsyncClient=Primary, Timeout=lambda *a, **k: None)
            M.configure(_get_agent_registry=lambda: current, _live_agent_names=ready,
                _agent_lane=registry_module._agent_lane,
                _call_agent_complete=actual_calls._call_agent_complete,
                _get_client=_async_const(client), _endpoint_key=_endpoint_key,
                REFINE_ENDPOINT="http://x", NATIVE_LOOP_TIMEOUT_S=0.03 if case == "timeout" else 1.0)
            async def real_secondary(*args, **kwargs):
                import mios_config
                original = mios_config._toml_section
                def section(name):
                    if name == "agent_pipe":
                        return {"tool_max_iters": 1 if case == "gpu-limit" else 4,
                                "replan_max": 1, "reflexion_enable": False}
                    return original(name)
                mios_config._toml_section = section
                try:
                    result = await secondary._v1_secondary_tool_loop(*args, **kwargs)
                    failed = kwargs["terminal_response"].get("_backend_failed", False)
                    assert bool(failed) is (case in ("gpu-http-failure", "gpu-transport-failure"))
                    return result
                finally: mios_config._toml_section = original
            M._v1_secondary_tool_loop = real_secondary if case.startswith("gpu-") else _async_const([])
            async def invoke():
                return await M._respond_native_loop_direct(None, streaming=case == "stream",
                    chat_id="fallback", model="public-alias", session_id=None,
                    last_user_text="An opaque request.", persona_system="",
                    messages=[{"role": "user", "content": "An opaque request."}], request=None,
                    force_delegate="explicit-agent" if case == "explicit" else None,
                    target_agents=["selected-agent"] if case == "selected" else None)
            if case == "cancel":
                task = asyncio.create_task(invoke())
                await asyncio.wait_for(entered.wait(), 0.5)
                task.cancel()
                try: await task
                except asyncio.CancelledError: pass
                else: raise AssertionError("CPU cancellation was swallowed")
                assert closed.is_set() and not actual_calls._IN_FLIGHT_PROMPTS
            else:
                response = await invoke()
                if case == "stream":
                    content = b"".join([chunk async for chunk in response.body_iterator]).decode()
                    assert "Configured CPU answer." in content and '"model": "public-alias"' in content
                    assert content.count("data: [DONE]") == 1
                elif case in ("cpu", "selected", "gpu-http-failure", "gpu-transport-failure"):
                    assert _body_text(response) == "Configured CPU answer."
                    assert json.loads(bytes(response.body))["model"] == "public-alias"
                elif case in ("primary-success", "gpu-success", "gpu-empty", "gpu-limit", "gpu-tool-recovery"):
                    assert _body_text(response) == "Primary answer.", (case, _body_text(response), Primary.calls)
                else: assert response.status_code == 503
            expected = 0 if case == "explicit" else 2 if case in ("gpu-empty", "gpu-limit") else 3 if case == "gpu-tool-recovery" else 1
            assert Primary.calls == expected, (case, Primary.calls, "failed primary retried or required synthesis skipped")
            if case == "timeout":
                assert closed.is_set() and not actual_calls._IN_FLIGHT_PROMPTS
            if case in ("cpu", "selected", "stream", "cancel", "timeout", "empty-cpu", "gpu-http-failure", "gpu-transport-failure"):
                assert len(requests) == 1 and len(admitted) == 1 and active == [1, -1]
            else:
                assert not requests and not admitted and not active
            if case in ("primary-success", "explicit", "gpu-success", "gpu-empty", "gpu-limit", "gpu-tool-recovery"): assert not readiness
    for case in ("cpu", "stream", "unready", "empty-registry", "primary-success", "explicit", "selected", "cancel", "timeout", "empty-cpu", "gpu-http-failure", "gpu-transport-failure", "gpu-success", "gpu-empty", "gpu-limit", "gpu-tool-recovery"):
        asyncio.run(run(case))
    print("registered CPU fallback: real registry/readiness/auth/admission/model/cancellation and policy controls: OK")



def _run_progress_and_completion_contract():
    raw = json.dumps({"url": "https://example.invalid/news", "items": ["evidence"]})
    for complete in (True, False):
        _base_configure({}, [], [])
        M._DEBUG_ENABLE = True
        M._loads_lenient = json.loads
        class NoRetry(_FakeClient):
            async def post(self, *args, **kwargs):
                raise AssertionError("accepted completion or failed backend must not repeat inference")
        M.httpx = types.SimpleNamespace(AsyncClient=NoRetry, Timeout=lambda *a, **k: None)
        async def tool_loop(*args, **kwargs):
            args[7](raw)
            terminal = kwargs["terminal_response"]
            if complete:
                terminal["content"] = FINAL_ANSWER
            else:
                terminal["_backend_failed"] = True
            return list(args[4]) + [{"role": "tool", "content": raw}]
        M._v1_secondary_tool_loop = tool_loop
        async def run():
            response = await M._respond_native_loop_direct(
                {"web": True, "news": True}, streaming=True, chat_id="progress",
                model="m", session_id=None, last_user_text="What is new today?",
                persona_system="", messages=[{"role": "user", "content": "What is new today?"}],
                request=None)
            return b"".join([chunk async for chunk in response.body_iterator]).decode()
        wire = asyncio.run(run())
        assert "example.invalid/news" in wire, wire
        if complete:
            assert FINAL_ANSWER in wire, wire
            assert '"finish_reason": "stop"' in wire, wire
            assert '"error":' not in wire, wire
        else:
            assert '"code": "inference_unavailable"' in wire, wire
            assert '"finish_reason": "stop"' not in wire, wire
        assert "data: [DONE]" in wire
    M._DEBUG_ENABLE = False
    _base_configure({}, [], [])
    M._v1_secondary_tool_loop = _async_const([])
    class SpacedClient(_FakeClient):
        stream_content = "  " + FINAL_ANSWER + "\n"
    M.httpx = types.SimpleNamespace(AsyncClient=SpacedClient, Timeout=lambda *a, **k: None)
    async def spaced():
        response = await M._respond_native_loop_direct(
            {}, streaming=True, chat_id="whitespace", model="m", session_id=None,
            last_user_text="An offline request", persona_system="",
            messages=[{"role": "user", "content": "An offline request"}], request=None)
        return b"".join([chunk async for chunk in response.body_iterator]).decode()
    assert asyncio.run(spaced()).count(FINAL_ANSWER) == 1
    for reason in (None, "length"):
        _base_configure({}, [], [])
        M._v1_secondary_tool_loop = _async_const([])
        class TruncatedClient(_FakeClient):
            stream_reason = reason
        M.httpx = types.SimpleNamespace(AsyncClient=TruncatedClient, Timeout=lambda *a, **k: None)
        async def incomplete():
            response = await M._respond_native_loop_direct(
                {}, streaming=True, chat_id="truncated", model="m", session_id=None,
                last_user_text="An offline request", persona_system="",
                messages=[{"role": "user", "content": "An offline request"}], request=None)
            return b"".join([chunk async for chunk in response.body_iterator]).decode()
        wire = asyncio.run(incomplete())
        assert '"code": "inference_unavailable"' in wire, wire
        assert '"finish_reason": "stop"' not in wire, wire
    print("web progress preserves final synthesis; tool-only and truncated streams fail explicitly: OK")


def _run_explicit_model_and_limit_contract():
    dispatches, loops = [], []
    _base_configure({}, dispatches, loops)
    seen = {}
    async def complete(client, endpoint, model, headers, messages, *args, **kwargs):
        seen.update(endpoint=endpoint, model=model, max_tokens=kwargs.get("max_tokens"))
        kwargs["terminal_response"]["content"] = FINAL_ANSWER
        return list(messages)
    async def polish(*args, **kwargs):
        seen["polish_max_tokens"] = kwargs.get("max_tokens")
        return ""
    M._v1_secondary_tool_loop = complete
    M.polish_response = polish
    response = asyncio.run(M._respond_native_loop_direct(
        {}, streaming=False, chat_id="bounded", model="explicit-brain", session_id=None,
        last_user_text="Hello", persona_system="", messages=[{"role": "user", "content": "Hello"}],
        requested_model="explicit-brain", max_tokens=8))
    assert _body_text(response) == FINAL_ANSWER
    assert seen == {"endpoint": "http://x/v1", "model": "explicit-brain",
                    "max_tokens": 8, "polish_max_tokens": 8}, seen
    print("explicit API model and generation limit reach native tool/final/polish: OK")



def _run_explicit_target_failure_does_not_substitute_another_model():
    import os
    from unittest import mock
    dispatches, loops = [], []
    _base_configure({}, dispatches, loops)
    async def failed(client, endpoint, model, headers, messages, *args, **kwargs):
        kwargs["terminal_response"]["_backend_failed"] = True
        return list(messages)
    async def forbidden(*args, **kwargs):
        raise AssertionError("explicit target silently retried on a different registered model")
    original_cpu = M._registered_cpu_completion
    original_refine = M.REFINE_ENDPOINT
    M._v1_secondary_tool_loop = failed
    M._registered_cpu_completion = forbidden
    M.REFINE_ENDPOINT = "http://different-fallback.invalid"
    try:
        for requested, environment in (
                ("explicit-brain", {}),
                (None, {"MIOS_AGENT_PIPE_BACKEND": "http://x/v1"})):
            with mock.patch.dict(os.environ, environment):
                response = asyncio.run(M._respond_native_loop_direct(
                    {}, streaming=False, chat_id="pinned", model="m", session_id=None,
                    last_user_text="Hello", persona_system="",
                    messages=[{"role": "user", "content": "Hello"}], requested_model=requested))
            assert response.status_code == 503, response.body
            assert b"inference_unavailable" in response.body, response.body
    finally:
        M._registered_cpu_completion = original_cpu
        M.REFINE_ENDPOINT = original_refine
    print("failed explicit model or endpoint is never substituted by CPU fallback: OK")



def _run_parallel_council_dispatch_contract():
    from mios_pipe.routing import native_loop as native
    from unittest import mock
    import time
    async def scenario(streaming=False, synthesis_failure=False):
        _base_configure({}, [], [])
        registry = {
            "cpu-fast": {"endpoint": "http://cpu-fast/v1", "model": "cpu-model", "lane": "cpu"},
            "cpu-slow": {"endpoint": "http://cpu-slow/v1", "model": "slow-model", "lane": "cpu"},
            "gpu-copy": {"endpoint": "http://gpu/v1", "model": "gpu-model", "lane": "gpu"},
            "disabled": {"endpoint": "http://off/v1", "model": "off", "enabled": False}}
        started, cancelled, calls, finals = asyncio.Event(), asyncio.Event(), [], []
        async def review(name, cfg, body, headers, client, **kwargs):
            calls.append((name, cfg["endpoint"], cfg["model"], body, kwargs))
            started.set()
            if name == "cpu-slow":
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            await asyncio.sleep(0)
            return name, "Verify the actual window, not merely the launcher exit code."
        async def main_loop(client, endpoint, model, headers, messages, *args, **kwargs):
            assert (endpoint, model) == ("http://gpu/v1", "gpu-model"), (endpoint, model)
            await asyncio.wait_for(started.wait(), 0.2)
            kwargs["terminal_response"]["content"] = "GPU draft"
            return list(messages)
        class FinalClient(_FakeClient):
            async def post(self, url, **kwargs):
                finals.append((url, json.loads(kwargs["content"])))
                if synthesis_failure:
                    raise OSError("optional final synthesis unavailable")
                return _FakeResp(FINAL_ANSWER)
            def stream(self, method, url, **kwargs):
                finals.append((url, json.loads(kwargs["content"])))
                return super().stream(method, url, **kwargs)
        M.configure(_get_agent_registry=lambda: registry,
                    _live_agent_names=_async_const(set(registry)),
                    _call_agent_complete=review, _get_client=_async_const(object()))
        M._v1_secondary_tool_loop = main_loop
        M.httpx = types.SimpleNamespace(AsyncClient=FinalClient, Timeout=lambda *a, **k: None)
        with mock.patch.object(native, "_pick_inference_backend",
                               _async_const(("http://gpu/v1", "gpu-model"))),              mock.patch.object(native, "COUNCIL_REVIEW_TIMEOUT_S", 0.04):
            began = time.monotonic()
            response = await M._respond_native_loop_direct(
                {}, streaming=streaming, chat_id="parallel", model="m", session_id=None,
                last_user_text="Launch an app and verify its window", persona_system="",
                messages=[{"role": "user", "content": "Launch an app and verify its window"}],
                target_agents=list(registry), max_tokens=8)
            if streaming:
                wire = b"".join([chunk async for chunk in response.body_iterator]).decode()
                assert wire.count(FINAL_ANSWER) == 1 and wire.count("data: [DONE]") == 1, wire
                assert "GPU draft" not in wire, wire
            else:
                assert _body_text(response) == ("GPU draft" if synthesis_failure else FINAL_ANSWER), response.body
            assert time.monotonic() - began < 0.5
        assert cancelled.is_set(), "unfinished CPU reviewer was not cancelled and awaited"
        assert {row[0] for row in calls} == {"cpu-fast", "cpu-slow"}, calls
        assert all(row[3]["max_tokens"] == 8 and "tools" not in row[3]
                   and row[4] == {"prefer_cpu": False} for row in calls), calls
        assert len(finals) == 1 and finals[0][0] == "http://gpu/v1/chat/completions", finals
        payload = finals[0][1]
        assert payload["model"] == "gpu-model" and payload["max_tokens"] == 8
        wire = json.dumps(payload["messages"])
        assert "GPU draft" in wire and "Verify the actual window" in wire, wire
        assert "cpu-slow" not in wire and "untrusted data" in wire, wire
    asyncio.run(scenario())
    asyncio.run(scenario(streaming=True))
    asyncio.run(scenario(synthesis_failure=True))
    print("selected endpoints run alongside GPU; useful CPU notes included; slow review cancelled: OK")


def _run_explicit_delegate_binding_contract():
    from mios_pipe.routing import native_loop as native
    from unittest import mock
    _base_configure({}, [], [])
    registry = {"chosen": {"endpoint": "http://chosen/v1", "model": "chosen-model"}}
    M.configure(_get_agent_registry=lambda: registry)
    seen = []
    async def loop(client, endpoint, model, headers, messages, *args, **kwargs):
        seen.append((endpoint, model, headers.get("Authorization")))
        kwargs["terminal_response"]["content"] = FINAL_ANSWER
        return list(messages)
    M._v1_secondary_tool_loop = loop
    async def scenario():
        for name in ("chosen", "missing"):
            response = await M._respond_native_loop_direct(
                {}, streaming=False, chat_id="delegate", model="m", session_id=None,
                last_user_text="Explain this", persona_system="", messages=[],
                force_delegate=name)
            if name == "chosen":
                assert _body_text(response) == FINAL_ANSWER
            else:
                assert response.status_code == 503 and b"agent_unavailable" in response.body
    with mock.patch.object(native, "_apply_outbound_auth",
                           lambda headers, endpoint: headers.update(Authorization="Bearer chosen-scope")):
        asyncio.run(scenario())
    assert seen == [("http://chosen/v1", "chosen-model", "Bearer chosen-scope")], seen
    print("explicit delegate uses registered endpoint/model; missing target fails honestly: OK")


def _run_council_request_cancellation_contract():
    async def scenario():
        _base_configure({}, [], [])
        started, finished = asyncio.Event(), asyncio.Event()
        registry = {"cpu": {"endpoint": "http://cpu/v1", "model": "cpu-model"}}
        async def review(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()
        async def main_loop(*args, **kwargs):
            await started.wait()
            await asyncio.Event().wait()
        M.configure(_get_agent_registry=lambda: registry,
                    _live_agent_names=_async_const({"cpu"}),
                    _call_agent_complete=review, _get_client=_async_const(object()))
        M._v1_secondary_tool_loop = main_loop
        task = asyncio.create_task(M._respond_native_loop_direct(
            {}, streaming=False, chat_id="cancel", model="m", session_id=None,
            last_user_text="Explain this", persona_system="", messages=[],
            target_agents=["cpu"]))
        await asyncio.wait_for(started.wait(), 0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("request cancellation was swallowed")
        assert finished.is_set(), "CPU dispatch outlived cancelled caller"
    asyncio.run(scenario())
    print("caller cancellation drains CPU review tasks before returning: OK")



def _run_council_httpx_dispatch_contract():
    import httpx
    import test_mios_agent_call as fixture
    from mios_pipe.routing import agent_call, native_loop as native
    from unittest import mock
    async def scenario():
        _base_configure({}, [], [])
        fixture._configure()
        agent_call.RR_ENABLE = False
        seen, closed = [], asyncio.Event()
        registry = {
            "cpu": {"endpoint": "https://cpu.invalid:9764/v1", "model": "served-cpu"},
            "slow": {"endpoint": "https://slow.invalid:9765/v1", "model": "served-slow"}}
        agent_call._apply_outbound_auth = lambda headers, endpoint: headers.update(
            Authorization="Bearer cpu-scope" if "cpu.invalid" in endpoint else "Bearer slow-scope")
        class Pending(httpx.AsyncByteStream):
            async def __aiter__(self):
                await asyncio.Event().wait()
                yield b"unreachable"
            async def aclose(self):
                closed.set()
        def transport(request):
            body = json.loads(request.content)
            seen.append((str(request.url), body, request.headers["Authorization"]))
            if "slow.invalid" in str(request.url):
                return httpx.Response(200, stream=Pending())
            return httpx.Response(200, json={"choices": [{"message": {
                "content": "Verify the mapped app window."}, "finish_reason": "stop"}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            M.configure(_get_agent_registry=lambda: registry,
                        _live_agent_names=_async_const(set(registry)),
                        _call_agent_complete=agent_call._call_agent_complete,
                        _get_client=_async_const(client))
            with mock.patch.object(native, "COUNCIL_REVIEW_TIMEOUT_S", 0.04):
                notes = await M._selected_council_reviews(
                    ["cpu", "slow"], "Check it", "http://gpu/v1", 8,
                    messages=[{"role": "user", "content": "The app is OBS."},
                              {"role": "assistant", "content": "I will verify its window."},
                              {"role": "user", "content": "Check it"}])
        assert notes == [("cpu", "Verify the mapped app window.")], notes
        assert closed.is_set() and not agent_call._IN_FLIGHT_PROMPTS
        assert {row[0] for row in seen} == {
            "https://cpu.invalid:9764/v1/chat/completions",
            "https://slow.invalid:9765/v1/chat/completions"}, seen
        for url, body, auth in seen:
            assert body["model"] == ("served-cpu" if "cpu.invalid" in url else "served-slow")
            assert auth == ("Bearer cpu-scope" if "cpu.invalid" in url else "Bearer slow-scope")
            assert body["max_tokens"] == 8 and body["tool_choice"] == "none" and body["store"] is False
            assert body["messages"][1]["content"] == "The app is OBS.", body
    asyncio.run(scenario())
    print("canonical dispatch sends exact CPU URLs/models/auth/context and cancels actual pending HTTPX response: OK")


if __name__ == "__main__":
    _run_progress_and_completion_contract()
    _run_all_folded_native_loop_suites()
    _run()
    _run_moved()
    _run_stream_cleanup_and_local_grounding()
    _run_terminal_completion_reuse()
    _run_registered_cpu_fallback()
    _run_explicit_model_and_limit_contract()
    _run_explicit_target_failure_does_not_substitute_another_model()
    _run_parallel_council_dispatch_contract()
    _run_explicit_delegate_binding_contract()
    _run_council_request_cancellation_contract()
    _run_council_httpx_dispatch_contract()
