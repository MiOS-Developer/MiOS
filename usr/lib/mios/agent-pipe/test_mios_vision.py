# AI-hint: Stdlib assert-script for mios_vision (refactor R9). Covers the two
# AI-related: mios_vision.py, mios_native_loop.py

import asyncio
import json
import sys
import types
from unittest import mock

def _install_stubs():
    for name in ("websockets", "uvicorn"):
        sys.modules.setdefault(name, mock.MagicMock(name=name))

    class _Resp:
        def __init__(self, content=None, status_code=200, media_type=None, *a, **k):
            self.content = content if content is not None else (a[0] if a else None)
            self.status_code = k.get("status_code", status_code)
            self.media_type = k.get("media_type", media_type)
            if isinstance(self.content, (dict, list)):
                self.body = json.dumps(self.content).encode("utf-8")
            elif isinstance(self.content, (bytes, bytearray)):
                self.body = bytes(self.content)
            elif isinstance(self.content, str):
                self.body = self.content.encode("utf-8")
            else:
                self.body = b""

    class _Stream(_Resp):
        def __init__(self, content=None, *a, **k):
            super().__init__(content, *a, **k)
            self.body_iterator = content if content is not None else (a[0] if a else k.get("content"))

    class _App:
        def __getattr__(self, _attr):
            def _factory(*_a, **_k):
                def _wrap(fn=None):
                    return fn if fn is not None else (lambda f: f)
                return _wrap
            return _factory

    fastapi = types.ModuleType("fastapi")
    fastapi.__path__ = []
    fastapi.FastAPI = lambda *a, **k: _App()
    fastapi.APIRouter = lambda *a, **k: _App()
    fastapi.Request = object
    fastapi.WebSocket = object
    fastapi.BackgroundTasks = object

    responses = types.ModuleType("fastapi.responses")
    responses.JSONResponse = type("JSONResponse", (_Resp,), {})
    responses.StreamingResponse = type("StreamingResponse", (_Stream,), {})
    for _c in ("HTMLResponse", "RedirectResponse", "Response", "PlainTextResponse"):
        setattr(responses, _c, type(_c, (_Resp,), {}))
    fastapi.responses = responses
    sys.modules["fastapi"] = fastapi
    sys.modules["fastapi.responses"] = responses

_install_stubs()
import mios_vision

def test_vision_unavailable_no_fabrication() -> None:
    mios_vision.configure(vision_model="")
    resp = asyncio.run(mios_vision._vision_complete(
        {"messages": [{"role": "user", "content": "what's in this image?"}]},
        False, "chatcmpl-test", "mios-vision"))
    body = json.loads(bytes(resp.body).decode("utf-8"))
    content = body["choices"][0]["message"]["content"]
    assert content == mios_vision._VISION_UNAVAILABLE_MSG, content
    assert "can't read images" in content, content
    assert body["choices"][0]["finish_reason"] == "stop"
    print("ok: vision unavailable -> honest assistant turn, no fabrication")

def test_vision_backend_failed_classifier() -> None:
    assert mios_vision._vision_backend_failed(503, "") is True
    assert mios_vision._vision_backend_failed(
        200, "exited prematurely") is True
    assert mios_vision._vision_backend_failed(
        200, "image inputs are not supported") is True
    assert mios_vision._vision_backend_failed(200, "a cat on a mat") is False
    print("ok: _vision_backend_failed classifies degraded backends")

def test_messages_have_image() -> None:
    assert mios_vision._messages_have_image(
        [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:..."}}]}]) is True
    assert mios_vision._messages_have_image(
        [{"role": "user", "content": "plain text"}]) is False
    print("ok: _messages_have_image detects vision content")

def test_client_tools_handback_shape() -> None:
    async def _select_child_tools(surface, intent, cap):
        return []

    mios_vision.configure(
        agent_contract=lambda: "",            # no SOUL contract in the test
        verb_catalog={},                      # empty -> no MiOS surface merged
        resolve_verb_key=lambda name: name,   # identity resolver
        select_child_tools=_select_child_tools,
        default_tool_cap=8,
    )

    handback = {"role": "assistant", "content": "",
                "tool_calls": [{"id": "call_x", "type": "function",
                                "function": {"name": "browser_back",
                                             "arguments": "{}"}}]}

    async def _stub_backend(req):
        return {"choices": [{"message": handback}]}

    orig = mios_vision._client_tools_backend
    mios_vision._client_tools_backend = _stub_backend
    try:
        body = {"messages": [{"role": "user", "content": "go back"}],
                "tools": [{"type": "function",
                           "function": {"name": "browser_back"}}]}
        msg = asyncio.run(mios_vision._client_tools_loop(
            body, {"browser_back"}, "chatcmpl-ct"))
    finally:
        mios_vision._client_tools_backend = orig

    assert msg.get("tool_calls"), msg
    assert msg["tool_calls"][0]["function"]["name"] == "browser_back", msg

    wrapped = mios_vision._client_tools_wrap(msg, "chatcmpl-ct", "mios")
    assert wrapped["object"] == "chat.completion"
    assert wrapped["choices"][0]["finish_reason"] == "tool_calls", wrapped
    assert wrapped["choices"][0]["message"] is msg
    print("ok: client-tools client tool_call handed back, wrap shape correct")

def test_client_tools_is_mios_gate() -> None:
    mios_vision.configure(verb_catalog={"open_app": {}},
                          resolve_verb_key=lambda name: name)
    assert mios_vision._client_tools_is_mios("open_app", set()) is True
    assert mios_vision._client_tools_is_mios("browser_back", set()) is False
    assert mios_vision._client_tools_is_mios("", set()) is False
    print("ok: _client_tools_is_mios gates server-side vs handback")

def test_client_tools_sse_relays_tool_calls() -> None:
    msg = {"role": "assistant", "content": "",
           "tool_calls": [{"id": "c1", "type": "function",
                           "function": {"name": "browser_back",
                                        "arguments": "{}"}}]}

    async def _collect():
        out = []
        async for chunk in mios_vision._client_tools_sse(msg, "cid", "mios"):
            out.append(chunk.decode("utf-8"))
        return "".join(out)

    blob = asyncio.run(_collect())
    assert "browser_back" in blob, blob
    assert "tool_calls" in blob and '"finish_reason": "tool_calls"' in blob, blob
    assert blob.rstrip().endswith("[DONE]"), blob
    print("ok: _client_tools_sse relays tool_calls + [DONE]")

def test_has_client_tools_tool_choice_none() -> None:
    # Positive control: tool_choice "none" returns False even if tools present
    assert mios_vision._has_client_tools({
        "tools": [{"type": "function", "function": {"name": "f"}}],
        "tool_choice": "none"
    }) is False
    assert mios_vision._has_client_tools({
        "tools": [{"type": "function", "function": {"name": "f"}}],
        "tool_choice": "None"
    }) is False
    assert mios_vision._has_client_tools({
        "tools": [{"type": "function", "function": {"name": "f"}}],
        "tool_choice": " none "
    }) is False
    # Negative control: tool_choice "auto" returns True when tools present
    assert mios_vision._has_client_tools({
        "tools": [{"type": "function", "function": {"name": "f"}}],
        "tool_choice": "auto"
    }) is True
    # Positive control: missing tool_choice defaults to True when tools present
    assert mios_vision._has_client_tools({
        "tools": [{"type": "function", "function": {"name": "f"}}]
    }) is True
    # Positive control: empty tools returns False
    assert mios_vision._has_client_tools({"tools": []}) is False
    print("ok: _has_client_tools respects tool_choice: none (two-sided control)")

def test_client_tools_loop_tool_choice_none() -> None:
    captured_reqs = []
    async def _stub_backend(req):
        captured_reqs.append(dict(req))
        return {"choices": [{"message": {"role": "assistant", "content": "plain answer"}}]}

    orig_backend = mios_vision._client_tools_backend
    mios_vision._client_tools_backend = _stub_backend
    try:
        # Positive control: tool_choice == "none" strips tools and tool_choice before backend
        body_pos = {
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": {"name": "f1"}}],
            "tool_choice": "none"
        }
        res_pos = asyncio.run(mios_vision._client_tools_loop(body_pos, {"f1"}, "cid-none"))
        assert res_pos.get("content") == "plain answer", res_pos
        assert len(captured_reqs) == 1, captured_reqs
        assert "tools" not in captured_reqs[0] or not captured_reqs[0]["tools"], captured_reqs[0]
        assert "tool_choice" not in captured_reqs[0], captured_reqs[0]

        # Negative control: tool_choice == "auto" retains tools and tool_choice
        body_neg = {
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": {"name": "f1"}}],
            "tool_choice": "auto"
        }
        res_neg = asyncio.run(mios_vision._client_tools_loop(body_neg, {"f1"}, "cid-auto"))
        assert len(captured_reqs) == 2, captured_reqs
        assert "tools" in captured_reqs[1] and len(captured_reqs[1]["tools"]) >= 1, captured_reqs[1]
        assert captured_reqs[1].get("tool_choice") == "auto", captured_reqs[1]
    finally:
        mios_vision._client_tools_backend = orig_backend
    print("ok: _client_tools_loop strips tools when tool_choice: none (two-sided control)")

def test_client_tools_deduplication_and_suppression() -> None:
    orig_catalog = getattr(mios_vision, "_VERB_CATALOG", None)
    orig_resolve = getattr(mios_vision, "_resolve_verb_key", None)
    orig_select = getattr(mios_vision, "_select_child_tools", None)
    orig_cap = getattr(mios_vision, "DEFAULT_TOOL_CAP", 24)

    async def _select_child_tools(surface, intent, cap):
        return [{"type": "function", "function": {"name": "dummy_verb"}}]

    mios_vision.configure(
        verb_catalog={"app_search": {}},
        resolve_verb_key=lambda name: name,
        select_child_tools=_select_child_tools,
        default_tool_cap=24,
    )

    captured = []
    async def _stub_backend(req):
        captured.append(dict(req))
        return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    orig_backend = mios_vision._client_tools_backend
    mios_vision._client_tools_backend = _stub_backend
    try:
        # Positive control 1: Client tools contain a MiOS verb -> _mios_sel suppressed
        body_verbs = {
            "messages": [{"role": "user", "content": "search"}],
            "tools": [
                {"type": "function", "function": {"name": "app_search"}},
                {"type": "function", "function": {"name": "client_extra"}}
            ]
        }
        asyncio.run(mios_vision._client_tools_loop(body_verbs, {"app_search", "client_extra"}, "cid-1"))
        req_tools_1 = [((t.get("function") or {}).get("name") or t.get("name")) for t in captured[-1].get("tools", [])]
        assert "dummy_verb" not in req_tools_1, f"_mios_sel should be suppressed: {req_tools_1}"
        assert req_tools_1 == ["app_search", "client_extra"], req_tools_1

        # Positive control 2: Client tool count >= DEFAULT_TOOL_CAP (24) -> _mios_sel suppressed
        harness_tools = [{"type": "function", "function": {"name": f"harness_tool_{i}"}} for i in range(25)]
        harness_names = {f"harness_tool_{i}" for i in range(25)}
        body_cap = {
            "messages": [{"role": "user", "content": "run task"}],
            "tools": harness_tools
        }
        asyncio.run(mios_vision._client_tools_loop(body_cap, harness_names, "cid-2"))
        req_tools_2 = [((t.get("function") or {}).get("name") or t.get("name")) for t in captured[-1].get("tools", [])]
        assert "dummy_verb" not in req_tools_2, f"_mios_sel should be suppressed: {req_tools_2}"
        assert len(req_tools_2) == 25, len(req_tools_2)

        # Positive control 3: Tool deduplication across duplicates in input
        dup_tools = [
            {"type": "function", "function": {"name": "dup_fn"}},
            {"type": "function", "function": {"name": "dup_fn"}},
            {"type": "function", "function": {"name": "other_fn"}}
        ]
        body_dup = {"messages": [{"role": "user", "content": "dup"}], "tools": dup_tools}
        asyncio.run(mios_vision._client_tools_loop(body_dup, {"dup_fn", "other_fn"}, "cid-3"))
        req_tools_3 = [((t.get("function") or {}).get("name") or t.get("name")) for t in captured[-1].get("tools", [])]
        assert req_tools_3.count("dup_fn") == 1, f"dup_fn was not deduplicated: {req_tools_3}"

        # Negative control: Small client tool set without MiOS verbs -> _mios_sel IS appended
        body_small = {
            "messages": [{"role": "user", "content": "navigate"}],
            "tools": [{"type": "function", "function": {"name": "browser_back"}}]
        }
        asyncio.run(mios_vision._client_tools_loop(body_small, {"browser_back"}, "cid-4"))
        req_tools_4 = [((t.get("function") or {}).get("name") or t.get("name")) for t in captured[-1].get("tools", [])]
        assert "dummy_verb" in req_tools_4, f"_mios_sel should be appended: {req_tools_4}"
        assert "browser_back" in req_tools_4, req_tools_4
    finally:
        mios_vision._client_tools_backend = orig_backend
        mios_vision.configure(
            verb_catalog=orig_catalog,
            resolve_verb_key=orig_resolve,
            select_child_tools=orig_select,
            default_tool_cap=orig_cap,
        )
    print("ok: client harness tool deduplication and _mios_sel suppression (two-sided control)")

def test_context_budget_pruning_overflow() -> None:
    import mios_tokenize
    from mios_pipe.routing import vision_context

    # The pruner lives in vision_context; mios_vision re-exports it because the
    # client-tools responders resolve it there. Every check below reaches it
    # through that re-export, so pin that it is the very same code.
    for name in ("_ELIDED_IMAGE", "_DEFAULT_TOOL_CTX", "_tool_ctx", "_prune_request_to_context_budget"):
        assert getattr(mios_vision, name) is getattr(vision_context, name), \
            f"mios_vision.{name} is not vision_context.{name}"

    # Build a 44,723+ token request
    # Under heuristic tokenizer (4 chars/token), ~180,000 chars is ~45,000 tokens
    sys_msg = {"role": "system", "content": "You are MiOS agent system prompt."}
    msgs = [sys_msg]
    for i in range(10):
        msgs.append({"role": "user", "content": f"User question {i}: " + ("x" * 15000)})
        msgs.append({"role": "assistant", "content": f"Assistant response {i}: " + ("y" * 15000)})
    msgs.append({"role": "tool", "content": "tool result: " + ("z" * 10000)})
    msgs.append({"role": "assistant", "content": "Assistant final: " + ("w" * 5000)})
    recent_user = {"role": "user", "content": "Current urgent user question"}
    msgs.append(recent_user)

    tools = [{"type": "function", "function": {"name": "f1", "description": "func 1"}}]
    req = {"messages": msgs, "tools": tools, "max_tokens": 4096}

    raw_tokens = mios_tokenize.count_messages(req["messages"], tools=req["tools"])
    assert raw_tokens > 40000, f"raw_tokens={raw_tokens} should exceed 40000"

    # Mock llama-server context limit enforcement
    def mock_llama_server(payload, max_ctx=32768):
        prompt_tokens = mios_tokenize.count_messages(payload.get("messages"), tools=payload.get("tools"))
        if prompt_tokens > max_ctx:
            return 400, f"request ({prompt_tokens} tokens) exceeds the available context size ({max_ctx} tokens)"
        return 200, "OK"

    # Negative control: Unpruned request fails with HTTP 400
    neg_status, neg_err = mock_llama_server(req, max_ctx=32768)
    assert neg_status == 400, f"Expected 400, got {neg_status}"
    assert "exceeds the available context size" in neg_err, neg_err

    # Positive control: Pruned request fits within 32,768 and backend succeeds with HTTP 200
    pruned = mios_vision._prune_request_to_context_budget(req, max_ctx=32768)
    pruned_tokens = mios_tokenize.count_messages(pruned["messages"], tools=pruned.get("tools"))
    assert pruned_tokens <= 32768 - 1024, f"pruned_tokens={pruned_tokens} exceeds target budget"
    assert any(m.get("role") == "system" and "You are MiOS agent system prompt." in m.get("content", "") for m in pruned["messages"]), "system prompt lost"
    assert any(m.get("role") == "user" and "Current urgent user question" in m.get("content", "") for m in pruned["messages"]), "recent user turn lost"

    pos_status, pos_msg = mock_llama_server(pruned, max_ctx=32768)
    assert pos_status == 200, f"Expected 200 after pruning, got {pos_status}: {pos_msg}"
    print(f"ok: context overflow pruning {raw_tokens} -> {pruned_tokens} tokens, HTTP 200 pass (two-sided control)")

def test_client_tools_relays_tool_choice_none_and_pruning() -> None:
    posted_payloads = []
    stream_payloads = []

    class MockStreamResp:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def aiter_bytes(self):
            yield b"data: {}\n\n"

    class MockResp:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"role": "assistant", "content": "mocked"}}]}

    class MockClient:
        async def post(self, url, content=b"", headers=None):
            posted_payloads.append(json.loads(content.decode("utf-8")))
            return MockResp()

        def stream(self, method, url, content=b"", headers=None):
            stream_payloads.append(json.loads(content.decode("utf-8")))
            return MockStreamResp()

    orig_get_client = mios_vision._get_client
    orig_pick = mios_vision._pick_tool_backend
    async def _mock_get_client():
        return MockClient()
    mios_vision._get_client = _mock_get_client
    async def _mock_pick():
        return ("http://mock-tool-backend", "mock-model")
    mios_vision._pick_tool_backend = _mock_pick

    try:
        # 1. _client_tools_relay (non-streaming)
        # Positive control: tool_choice "none" strips tools and tool_choice
        body_pos = {
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [{"type": "function", "function": {"name": "browser_forward"}}],
            "tool_choice": "none"
        }
        resp = asyncio.run(mios_vision._client_tools_relay(body_pos, streaming=False))
        assert len(posted_payloads) == 1, posted_payloads
        assert "tools" not in posted_payloads[0], posted_payloads[0]
        assert "tool_choice" not in posted_payloads[0], posted_payloads[0]

        # Negative control: tool_choice "auto" retains tools and tool_choice
        body_neg = {
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [{"type": "function", "function": {"name": "browser_forward"}}],
            "tool_choice": "auto"
        }
        resp_neg = asyncio.run(mios_vision._client_tools_relay(body_neg, streaming=False))
        assert len(posted_payloads) == 2, posted_payloads
        assert "tools" in posted_payloads[1] and len(posted_payloads[1]["tools"]) == 1, posted_payloads[1]
        assert posted_payloads[1].get("tool_choice") == "auto", posted_payloads[1]

        # 2. _client_tools_stream_relay
        # Positive control: tool_choice "none" strips tools and tool_choice
        async def _consume_stream(s_resp):
            async for _ in s_resp.body_iterator:
                pass

        resp_stream_pos = asyncio.run(mios_vision._client_tools_stream_relay(body_pos, "cid-s1", "m"))
        asyncio.run(_consume_stream(resp_stream_pos))
        assert len(stream_payloads) == 1, stream_payloads
        assert "tools" not in stream_payloads[0], stream_payloads[0]
        assert "tool_choice" not in stream_payloads[0], stream_payloads[0]

        # Negative control: tool_choice "auto" retains tools and tool_choice
        resp_stream_neg = asyncio.run(mios_vision._client_tools_stream_relay(body_neg, "cid-s2", "m"))
        asyncio.run(_consume_stream(resp_stream_neg))
        assert len(stream_payloads) == 2, stream_payloads
        assert "tools" in stream_payloads[1] and len(stream_payloads[1]["tools"]) == 1, stream_payloads[1]
        assert stream_payloads[1].get("tool_choice") == "auto", stream_payloads[1]

        # 3. Pruning under context overflow in _client_tools_relay
        large_body = {
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "u" * 150000}
            ],
            "tools": [{"type": "function", "function": {"name": "t1"}}],
            "tool_choice": "auto"
        }
        asyncio.run(mios_vision._client_tools_relay(large_body, streaming=False))
        assert len(posted_payloads) == 3
        pruned_p = posted_payloads[2]
        import mios_tokenize
        tok_pruned = mios_tokenize.count_messages(pruned_p["messages"], tools=pruned_p.get("tools"))
        assert tok_pruned <= 32768 - 1024, f"Relay payload {tok_pruned} exceeds budget"

    finally:
        mios_vision._get_client = orig_get_client
        mios_vision._pick_tool_backend = orig_pick
    print("ok: _client_tools_relay and _client_tools_stream_relay strip tools & prune overflow (two-sided control)")

if __name__ == "__main__":
    test_vision_unavailable_no_fabrication()
    test_vision_backend_failed_classifier()
    test_messages_have_image()
    test_client_tools_handback_shape()
    test_client_tools_is_mios_gate()
    test_client_tools_sse_relays_tool_calls()
    test_has_client_tools_tool_choice_none()
    test_client_tools_loop_tool_choice_none()
    test_client_tools_deduplication_and_suppression()
    test_context_budget_pruning_overflow()
    test_client_tools_relays_tool_choice_none_and_pruning()
    print("\nALL mios_vision tests passed")
