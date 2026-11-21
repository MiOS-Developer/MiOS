#!/usr/bin/env python3
# AI-hint: SSE primitive and real-ASGI deferred-stream tests; no live model or database.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_sse (refactor R2)."""

import asyncio
import json
import os
import sys
import tempfile

import mios_sse as e

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _decode(b):
    """`data: {json}\\n\\n` -> the parsed chunk dict."""
    s = b.decode("utf-8")
    assert s.startswith("data: ") and s.endswith("\n\n"), s
    return json.loads(s[len("data: "):].strip())

def t_chunk():
    c = _decode(e._sse_chunk("hello", chat_id="cid", model="m", role="assistant"))
    check("chunk: object type", c["object"] == "chat.completion.chunk")
    check("chunk: id+model", c["id"] == "cid" and c["model"] == "m")
    check("chunk: content delta", c["choices"][0]["delta"]["content"] == "hello")
    check("chunk: role", c["choices"][0]["delta"]["role"] == "assistant")
    r = _decode(e._sse_chunk(None, chat_id="cid", model="m", reasoning="thinking"))
    d = r["choices"][0]["delta"]
    check("chunk: dual reasoning fields", d.get("reasoning_content") == "thinking" and d.get("reasoning") == "thinking")
    check("chunk: mios_status passthrough",
          _decode(e._sse_chunk("", chat_id="c", model="m", mios_status={"emoji": "x"})).get("mios_status") == {"emoji": "x"})

def t_done():
    check("done: [DONE] sentinel", e._sse_done() == b"data: [DONE]\n\n")

def t_status():
    b = e._sse_status(chat_id="c", model="m", emoji="🔎", label="search", detail="cats")
    c = _decode(b)
    st = c["mios_status"]
    check("status: payload emoji/label/done", st["emoji"] == "🔎" and st["done"] is False)
    check("status: detail appended to label", "cats" in st["label"] and st.get("detail") == "cats")
    if e.STATUS_AS_REASONING:
        check("status: persists reasoning when content", c["choices"][0]["delta"].get("reasoning_content"))
    bare = _decode(e._sse_status(chat_id="c", model="m", emoji="👂", label="", detail=None))
    check("status: bare marker no reasoning", not bare["choices"][0]["delta"].get("reasoning_content"))
    check("status: bare marker still a pill", bare.get("mios_status", {}).get("emoji") == "👂")

def t_status_phase():
    c = _decode(e._sse_status_phase(chat_id="c", model="m", phase="tool"))
    check("phase: known phase emoji from _HUMAN_LABELS", c["mios_status"]["emoji"] == e._HUMAN_LABELS["tool"][0])
    c2 = _decode(e._sse_status_phase(chat_id="c", model="m", phase="nope"))
    check("phase: unknown -> fallback glyph", c2["mios_status"]["emoji"] == "·")

def t_stream_answer():
    os.environ["MIOS_ANSWER_CHUNK_CHARS"] = "4"
    out = []
    async def run():
        async for b in e._stream_answer("abcdefgh", chat_id="c", model="m"):
            out.append(_decode(b)["choices"][0]["delta"]["content"])
    asyncio.run(run())
    check("stream: char-exact reassembly", "".join(out) == "abcdefgh", "".join(out))
    check("stream: chunked by size", out == ["abcd", "efgh"], str(out))
    empty = []
    async def run2():
        async for b in e._stream_answer("", chat_id="c", model="m"):
            empty.append(b)
    asyncio.run(run2())
    check("stream: empty -> nothing", empty == [])

def t_tail():
    fd, path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"events": [{"ts": 10, "kind": "tool_call", "detail": "ran X"},
                                  {"ts": 20, "kind": "subagent_done", "detail": "done Y"}]}, fh)
        e._HERMES_TAIL_PATH = path  # point at the fixture
        chunk, new_ts = e._tail_latest_status(0.0, chat_id="c", model="m")
        check("tail: advances to newest ts", new_ts == 20)
        st = _decode(chunk)["mios_status"]
        check("tail: emoji from kind map", st["emoji"] == e._TAIL_KIND_EMOJI["subagent_done"])
        check("tail: detail carried", st.get("detail") == "done Y")
        none_chunk, ts2 = e._tail_latest_status(20.0, chat_id="c", model="m")
        check("tail: nothing newer -> (None, ts)", none_chunk is None and ts2 == 20.0)
    finally:
        os.unlink(path)

def t_iter_chunks():
    check("iter: size<=0 -> one chunk", list(e._iter_answer_chunks("hello world", 0)) == ["hello world"])
    check("iter: text<=size -> one chunk", list(e._iter_answer_chunks("hi", 8)) == ["hi"])
    out = list(e._iter_answer_chunks("alpha beta gamma delta", 8))
    check("iter: lossless reassembly", "".join(out) == "alpha beta gamma delta", str(out))
    check("iter: word-boundary chunks (whitespace kept)",
          out == ["alpha ", "beta ", "gamma ", "delta"], str(out))
    big = list(e._iter_answer_chunks("supercalifragilistic", 5))
    check("iter: oversize token emitted whole", big == ["supercalifragilistic"], str(big))
    check("iter: empty text -> ['']", list(e._iter_answer_chunks("", 4)) == [""])

def main():
    t_chunk()
    t_done()
    t_status()
    t_status_phase()
    t_stream_answer()
    t_tail()
    t_iter_chunks()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0



# ==============================================================================
# mios_pipe.streaming via configure() fakes; task_1006 had folded only a placeholder here.
# ==============================================================================
from mios_pipe import streaming as _streaming


def _streaming_wire(calls, *, shed=False, inner_error=None):
    class Shed(Exception):
        pass

    def gate(kind):
        class _Gate:
            def __init__(self, key):
                self.key = key

            async def __aenter__(self):
                calls.append(("enter", kind, self.key))

            async def __aexit__(self, *exc):
                calls.append(("exit", kind, self.key))
                return False
        return _Gate

    async def admit(ep, model, lane, prio, est, foreground):
        calls.append(("admit", ep, model, lane, prio, est, foreground))
        if shed:
            raise Shed()

    async def model_active(ep, model, delta, est):
        calls.append(("active", delta))

    async def inner(name, cfg, body, headers, client, q, prefer_cpu):
        calls.append(("inner", name, prefer_cpu))
        if inner_error is not None:
            raise inner_error
        return name, "<chrome>answer</chrome>"

    _streaming.configure(
        _agent_offload_engine=lambda cfg: "cpu-engine",
        _agent_binding=lambda cfg, engine: ("http://ep", f"model@{engine}"),
        _dispatch_priority=lambda cfg: 5,
        _opt_int_mb=lambda v: int(v) if v is not None else None,
        _admit=admit, _SloShed=Shed,
        _priority_gate=gate("priority"), _endpoint_sem=gate("endpoint"),
        _lane_sem=gate("lane"), _lane_sem_key=lambda cfg: "lane-key",
        _model_active=model_active, _call_agent_stream_inner=inner,
        _strip_agent_chrome=lambda t: t.replace("<chrome>", "").replace("</chrome>", ""),
    )


def t_streaming_admits_gates_and_accounts_in_order():
    calls = []
    _streaming_wire(calls)
    got = asyncio.run(_streaming.call_agent_stream("peer", {"vram_mb": "512"}, {}, {}, None, None))
    check("streaming: returns (name, chrome-stripped text)", got == ("peer", "answer"), str(got))
    check("streaming: admission precedes every gate, on the offload engine's lane",
          calls[0] == ("admit", "http://ep", "model@cpu-engine", "cpu-engine", 5, 512, False), str(calls[0]))
    order = [c[:2] for c in calls[1:]]
    check("streaming: gates nest priority > endpoint > lane, the call runs inside all three",
          order == [("enter", "priority"), ("enter", "endpoint"), ("enter", "lane"), ("active", 1),
                    ("inner", "peer"), ("active", -1), ("exit", "lane"), ("exit", "endpoint"),
                    ("exit", "priority")], str(order))


def t_streaming_priority_and_lane_selection():
    calls = []
    _streaming_wire(calls)
    asyncio.run(_streaming.call_agent_stream("p", {}, {}, {}, None, None, priority=9))
    check("streaming: an explicit priority overrides the dispatch default",
          calls[0][4] == 9 and ("enter", "priority", 9) in calls, str(calls[:2]))
    calls.clear()
    asyncio.run(_streaming.call_agent_stream("p", {}, {}, {}, None, None, prefer_cpu=False))
    check("streaming: prefer_cpu=False binds no engine and admits on the lane key",
          calls[0][2] == "model@None" and calls[0][3] == "lane-key"
          and ("enter", "lane", "lane-key") in calls and ("inner", "p", False) in calls, str(calls))


def t_streaming_slo_shed_runs_nothing():
    calls = []
    _streaming_wire(calls, shed=True)
    got = asyncio.run(_streaming.call_agent_stream("p", {}, {}, {}, None, None))
    check("streaming: an SLO shed returns (name, '')", got == ("p", ""), str(got))
    check("streaming: a shed request enters no gate and never calls the model",
          [c[0] for c in calls] == ["admit"], str(calls))


def t_streaming_failure_still_releases_accounting():
    calls = []
    _streaming_wire(calls, inner_error=RuntimeError("upstream reset"))
    try:
        asyncio.run(_streaming.call_agent_stream("p", {}, {}, {}, None, None))
        raised = False
    except RuntimeError:
        raised = True
    check("streaming: an upstream failure propagates", raised)
    check("streaming: the active-model count is released on failure",
          ("active", 1) in calls and ("active", -1) in calls, str(calls))
    check("streaming: every gate is exited on failure",
          [c[1] for c in calls if c[0] == "exit"] == ["lane", "endpoint", "priority"], str(calls))


def _run_extra_streaming():
    before = _fails
    for t in (t_streaming_admits_gates_and_accounts_in_order,
              t_streaming_priority_and_lane_selection,
              t_streaming_slo_shed_runs_nothing,
              t_streaming_failure_still_releases_accounting):
        t()
    return 1 if _fails > before else 0


def t_deferred_chat_response_asgi():
    """Real ASGI events prove header timing, context propagation and cleanup."""
    import subprocess
    child = r'''import asyncio, contextvars, json
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse, StreamingResponse
from mios_pipe.routing.sse import deferred_chat_response

SCOPE = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
         "http_version": "1.1", "method": "POST", "scheme": "http",
         "path": "/v1/chat/completions", "raw_path": b"/v1/chat/completions",
         "query_string": b"", "headers": [], "server": ("asgi.invalid", 80),
         "client": ("127.0.0.1", 12345)}
KEEPALIVE = b": keepalive\n\n"

class Exchange:
    def __init__(self, factory, *, include_usage=False):
        self.received = asyncio.Queue()
        self.events = asyncio.Queue()
        self.sent = []
        response = deferred_chat_response(factory, keepalive_s=0.01,
                                          model="requested", include_usage=include_usage)
        self.task = asyncio.create_task(response(SCOPE, self.received.get, self.send))

    async def send(self, event):
        self.sent.append(event)
        await self.events.put(event)

    async def next(self):
        return await asyncio.wait_for(self.events.get(), 5)

    async def body(self):
        while True:
            event = await self.next()
            if event["type"] == "http.response.body" and event.get("body"):
                return event["body"]

    async def finish(self):
        await asyncio.wait_for(self.task, 5)
        return b"".join(e.get("body", b"") for e in self.sent)

    async def disconnect(self):
        await self.received.put({"type": "http.disconnect"})
        return await self.finish()

def events(raw):
    return [json.loads(line[6:]) for line in raw.decode().splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"]

def no_orphans():
    assert asyncio.all_tasks() == {asyncio.current_task()}, asyncio.all_tasks()

async def timing_context_and_passthrough():
    context = contextvars.ContextVar("request-context", default="outer")
    entered, release_factory, body_waiting, release_body = (asyncio.Event() for _ in range(4))
    closed, background = [], []
    first, last = b'data: {"delta":"first"}\n\n', b'data: [DONE]\n\n'

    async def factory():
        context.set("inside-request")
        entered.set()
        await release_factory.wait()
        async def chunks():
            try:
                assert context.get() == "inside-request", context.get()
                yield first
                body_waiting.set()
                await release_body.wait()
                assert context.get() == "inside-request"
                yield last
            finally:
                closed.append(context.get())
        async def cleanup(): background.append(context.get())
        return StreamingResponse(chunks(), background=BackgroundTask(cleanup),
                                 media_type="text/event-stream")

    exchange = Exchange(factory)
    start = await exchange.next()
    assert start["type"] == "http.response.start" and start["status"] == 200, start
    assert dict(start["headers"])[b"content-type"].startswith(b"text/event-stream")
    await asyncio.wait_for(entered.wait(), 5)
    assert not release_factory.is_set()
    assert await exchange.body() == KEEPALIVE
    assert await exchange.body() == KEEPALIVE
    release_factory.set()
    while await exchange.body() != first:
        pass
    await asyncio.wait_for(body_waiting.wait(), 5)
    assert await exchange.body() == KEEPALIVE
    assert await exchange.body() == KEEPALIVE
    release_body.set()
    raw = await exchange.finish()
    assert raw.replace(KEEPALIVE, b"") == first + last, raw
    assert closed == ["inside-request"] and background == ["inside-request"]
    assert context.get() == "outer"
    no_orphans()

async def cancellation_during_planning():
    entered, cancelled = asyncio.Event(), asyncio.Event()
    async def factory():
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    exchange = Exchange(factory)
    await asyncio.wait_for(entered.wait(), 5)
    assert await exchange.body() == KEEPALIVE
    raw = await exchange.disconnect()
    assert cancelled.is_set()
    assert b"data:" not in raw, raw
    no_orphans()

async def cancellation_during_iteration():
    waiting = asyncio.Event()
    closed, background = [], []
    async def chunks():
        try:
            yield b'data: {"delta":"one"}\n\n'
            waiting.set()
            await asyncio.Event().wait()
        finally:
            closed.append(True)
    async def cleanup(): background.append(True)
    async def factory():
        return StreamingResponse(chunks(), background=BackgroundTask(cleanup))
    exchange = Exchange(factory)
    await asyncio.wait_for(waiting.wait(), 5)
    raw = await exchange.disconnect()
    assert closed == [True] and background == [True], (closed, background)
    assert b'"error"' not in raw and b"[DONE]" not in raw, raw
    no_orphans()

async def cleanup_failure_preserves_completed_wire():
    cleaned = []
    content = b'data: {"delta":"complete"}\n\ndata: [DONE]\n\n'
    async def chunks(): yield content
    async def cleanup():
        cleaned.append(True)
        raise RuntimeError("private cleanup diagnostic")
    async def factory():
        return StreamingResponse(chunks(), background=BackgroundTask(cleanup))
    exchange = Exchange(factory)
    raw = await exchange.finish()
    assert raw.replace(KEEPALIVE, b"") == content, raw
    assert cleaned == [True]
    no_orphans()

async def conversion_and_errors():
    message = {"role": "assistant", "content": None,
               "tool_calls": [{"id": "call-a", "type": "function",
                               "function": {"name": "lookup", "arguments": "{}"}}],
               "function_call": {"name": "legacy", "arguments": "{}"},
               "refusal": "not available", "reasoning_content": "trace", "reasoning": "trace"}
    completion = {"id": "completion-a", "object": "chat.completion", "created": 123,
                  "model": "selected", "system_fingerprint": "fp-local",
                  "choices": [{"index": 2, "message": message, "finish_reason": "tool_calls"},
                              {"index": 4, "message": {"role": "assistant", "content": "second"},
                               "finish_reason": "length"}],
                  "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}}
    for as_response in (False, True):
        for include_usage in (False, True):
            async def factory():
                return JSONResponse(completion) if as_response else completion
            exchange = Exchange(factory, include_usage=include_usage)
            raw = await exchange.finish()
            parsed = events(raw)
            assert raw.count(b"data: [DONE]") == 1, raw
            choices = [choice for event in parsed for choice in event.get("choices", [])]
            for expected in completion["choices"]:
                matching = [c for c in choices if c["index"] == expected["index"]]
                assert matching, (expected, parsed)
                combined = {}
                for choice in matching: combined.update(choice.get("delta", {}))
                for key, value in expected["message"].items():
                    if key == "tool_calls":
                        actual = combined[key]
                        assert len(actual) == len(value)
                        for index, (got, want) in enumerate(zip(actual, value)):
                            assert got.get("index") == index, got
                            assert all(got[k] == v for k, v in want.items()), got
                    else:
                        assert combined.get(key) == value, (key, combined)
                assert matching[-1]["finish_reason"] == expected["finish_reason"], matching
            for event in parsed:
                assert event["id"] == "completion-a" and event["model"] == "selected"
                assert event["created"] == 123 and event["object"] == "chat.completion.chunk"
            usage_events = [event for event in parsed if event.get("usage")]
            if include_usage:
                assert len(usage_events) == 1 and usage_events[0]["usage"] == completion["usage"]
            else:
                assert not usage_events, usage_events
            no_orphans()

    error = {"message": "capacity reached", "type": "rate_limit_error",
             "param": None, "code": "budget_exhausted"}
    async def rejected(): return JSONResponse({"error": error}, status_code=429)
    exchange = Exchange(rejected)
    raw = await exchange.finish()
    assert events(raw) == [{"error": error}] and raw.count(b"data: [DONE]") == 1, raw
    no_orphans()
    async def failed(): raise RuntimeError("private upstream diagnostic")
    exchange = Exchange(failed)
    raw = await exchange.finish()
    assert len(events(raw)) == 1 and isinstance(events(raw)[0].get("error"), dict), raw
    assert b"private upstream diagnostic" not in raw and raw.count(b"data: [DONE]") == 1
    no_orphans()

async def main():
    await timing_context_and_passthrough()
    await cancellation_during_planning()
    await cancellation_during_iteration()
    await cleanup_failure_preserves_completed_wire()
    await conversion_and_errors()
    print("DEFERRED_CHAT_ASGI_PASS")
asyncio.run(main())
'''
    result = subprocess.run([sys.executable, "-c", child], capture_output=True, text=True,
                            timeout=45, cwd=os.path.dirname(os.path.abspath(__file__)))
    check("deferred chat: early ASGI headers, keepalives, context, conversion and cancellation",
          result.returncode == 0 and "DEFERRED_CHAT_ASGI_PASS" in result.stdout,
          result.stderr if result.returncode else "real ASGI 2.4, blocked planner and iterator, no orphan tasks")



def _run_all_folded_sse_suites():
    t_deferred_chat_response_asgi()
    rc = _run_extra_streaming()
    if rc not in (None, 0) or _fails:
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _rc_main = main()
    _run_all_folded_sse_suites()
    sys.exit(_rc_main)
