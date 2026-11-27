# AI-hint: OpenAI streaming SSE chunk + status-emit primitives extracted from server.py (refactor WS R2 leaf wave).
# AI-doc: usr/share/doc/mios/manual/routing.md

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time
import uuid
from typing import Any, Optional


async def read_chat_request(request, *, loads, ingress_key, client_tools_passthrough,
                            has_client_tools):
    """Validate the body and route-local credentials before streaming headers."""
    from starlette.responses import JSONResponse

    try:
        body_bytes = await request.body()
        body = loads(body_bytes.decode("utf-8", errors="replace")) if body_bytes else {}
    except json.JSONDecodeError:
        return None, JSONResponse(
            content={"error": {"message": "invalid JSON body",
                               "type": "invalid_request_error"}},
            status_code=400,
        )

    if not isinstance(body, dict):
        return None, JSONResponse(status_code=400, content={"error": {
            "message": "body must be a JSON object", "type": "invalid_request_error"}})
    messages = body.get("messages")
    if not messages or not isinstance(messages, list):
        return None, JSONResponse(
            content={"error": {"message": "you must provide a 'messages' array",
                               "type": "invalid_request_error",
                               "param": "messages", "code": None}},
            status_code=400)
    if body.get("stream_options") is not None and not isinstance(body["stream_options"], dict):
        return None, JSONResponse(status_code=400, content={"error": {
            "message": "stream_options must be a JSON object", "type": "invalid_request_error",
            "param": "stream_options", "code": None}})

    plain = str(body.get("tool_choice") or "").strip().lower() == "none"
    if ingress_key and (plain or (client_tools_passthrough and has_client_tools(body))):
        credential = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
        allowed = credential == ingress_key
        if plain and not allowed:
            from mios_pipe.access.authn import _check_inbound_principal
            allowed = bool(_check_inbound_principal(credential))
        if not allowed:
            return None, JSONResponse(status_code=401, content={"error": {
                "message": "unauthorized", "type": "invalid_request_error"}})
    return body, None


def _completion_events(response, *, model: str, include_usage: bool):
    """Preserve a plain completion's choices when a routed reply is not SSE."""
    if isinstance(response, dict):
        payload = response
    else:
        payload = json.loads(response.body)
    if not isinstance(payload, dict):
        raise ValueError("invalid completion object")
    if "error" in payload or getattr(response, "status_code", 200) >= 400:
        error = payload.get("error") or {
            "message": "The chat backend rejected the request",
            "type": "server_error", "code": getattr(response, "status_code", None),
        }
        yield ("data: " + json.dumps({"error": error}) + "\n\n").encode("utf-8")
        yield _sse_done()
        return
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("completion has no choices")
    base = {
        "id": payload.get("id") or f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion.chunk",
        "created": payload.get("created", int(time.time())),
        "model": payload.get("model") or model,
    }
    for key in ("system_fingerprint", "service_tier"):
        if key in payload:
            base[key] = payload[key]
    for position, choice in enumerate(choices):
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ValueError("completion has no message")
        delta = dict(message)
        if isinstance(delta.get("tool_calls"), list):
            delta["tool_calls"] = [
                {**call, "index": index} for index, call in enumerate(delta["tool_calls"])
            ]
        index = choice.get("index", position)
        chunk = {**base, "choices": [{"index": index, "delta": delta,
                                      "finish_reason": None}]}
        if "logprobs" in choice:
            chunk["choices"][0]["logprobs"] = choice["logprobs"]
        yield ("data: " + json.dumps(chunk) + "\n\n").encode("utf-8")
        chunk["choices"] = [{"index": index, "delta": {},
                              "finish_reason": choice.get("finish_reason", "stop")}]
        yield ("data: " + json.dumps(chunk) + "\n\n").encode("utf-8")
    if include_usage and payload.get("usage") is not None:
        yield ("data: " + json.dumps({**base, "choices": [], "usage": payload["usage"]})
               + "\n\n").encode("utf-8")
    yield _sse_done()


def deferred_chat_response(response_factory, *, keepalive_s: float,
                           model: str, include_usage: bool = False):
    """Start SSE before routing, retaining one context through planning and output.

    The bounded queue provides backpressure. Only the consumer times out while
    waiting: a heartbeat never cancels model work. Disconnects cancel the owner
    task, which closes the returned stream and its background resources.
    """
    from starlette.responses import StreamingResponse

    if not math.isfinite(keepalive_s) or keepalive_s <= 0:
        raise ValueError("stream keepalive interval must be finite and positive")

    async def events():
        queue = asyncio.Queue(maxsize=1)

        async def produce():
            response = None
            try:
                response = await response_factory()
                if isinstance(response, StreamingResponse):
                    async for chunk in response.body_iterator:
                        await queue.put(chunk)
                else:
                    for chunk in _completion_events(response, model=model,
                                                    include_usage=include_usage):
                        await queue.put(chunk)
            finally:
                close = getattr(getattr(response, "body_iterator", None), "aclose", None)
                for cleanup in (close, getattr(response, "background", None)):
                    if cleanup is not None:
                        try:
                            await cleanup()
                        except Exception as exc:
                            # Cleanup cannot retroactively turn a completed stream
                            # into another completion/error or expose private data.
                            logging.getLogger(__name__).warning(
                                "stream cleanup failed (%s)", type(exc).__name__)

        producer = asyncio.create_task(produce())
        pending = None
        try:
            yield b": keepalive\n\n"
            while True:
                if pending is None:
                    pending = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait(
                    (pending, producer), timeout=keepalive_s,
                    return_when=asyncio.FIRST_COMPLETED)
                if pending.done():
                    chunk, pending = pending.result(), None
                    yield chunk
                elif producer in done:
                    # Drain any final queued chunk before observing completion.
                    if not queue.empty():
                        chunk, pending = await pending, None
                        yield chunk
                        continue
                    await producer
                    return
                elif not done:
                    yield b": keepalive\n\n"
        except Exception:
            # Headers have already been sent; failures stay on the SSE wire.
            # Exception strings may contain upstream credentials or private data.
            yield b'data: {"error":{"message":"The chat pipeline failed","type":"server_error","code":"pipeline_error"}}\n\n'
            yield _sse_done()
        finally:
            tasks = [producer] + ([pending] if pending is not None else [])
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    return disconnect_aware_response(StreamingResponse(
        events(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}))


def disconnect_aware_response(response):
    """Watch ASGI disconnects even while upstream awaits produce no body events."""
    from starlette.responses import StreamingResponse
    from starlette.requests import ClientDisconnect

    if not isinstance(response, StreamingResponse):
        return response

    class DisconnectAwareStreamingResponse(StreamingResponse):
        async def __call__(self, scope, receive, send):
            stream = asyncio.create_task(self.stream_response(send))
            disconnected = asyncio.create_task(self.listen_for_disconnect(receive))
            try:
                done, _ = await asyncio.wait(
                    (stream, disconnected), return_when=asyncio.FIRST_COMPLETED)
                if stream in done:
                    try:
                        await stream
                    except OSError as exc:
                        raise ClientDisconnect() from exc
                else:
                    await disconnected
            finally:
                for task in (stream, disconnected):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(stream, disconnected, return_exceptions=True)
                close = getattr(self.body_iterator, "aclose", None)
                if close is not None:
                    await close()
            if self.background is not None:
                await self.background()

    wrapped = DisconnectAwareStreamingResponse(
        response.body_iterator, status_code=response.status_code,
        media_type=response.media_type, background=response.background)
    wrapped.raw_headers = response.raw_headers
    return wrapped


STATUS_AS_REASONING = os.environ.get(
    "MIOS_STATUS_AS_REASONING", "true").lower() not in {"false", "0", "no"}

_DEBUG_ENABLE = False
_SURFACE_DEFAULT = "clean"

def _sse_chunk(content: Optional[str], *, chat_id: str, model: str,
               role: Optional[str] = None,
               finish_reason: Optional[str] = None,
               mios_status: Optional[dict] = None,
               reasoning: Optional[str] = None) -> bytes:
    delta: dict[str, Any] = {}
    if role:
        delta["role"] = role
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
        delta["reasoning"] = reasoning
    if content is not None:
        delta["content"] = content
    chunk = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "delta": delta,
            "finish_reason": finish_reason,
        }],
    }
    if mios_status:
        chunk["mios_status"] = mios_status
    return ("data: " + json.dumps(chunk) + "\n\n").encode("utf-8")

def _sse_reasoning(text: str, *, chat_id: str, model: str,
                   reasoning_ok: Optional[bool] = None) -> bytes:
    if reasoning_ok is True:
        return _sse_chunk(None, chat_id=chat_id, model=model, reasoning=text)
    if reasoning_ok is False:
        return _sse_chunk(text, chat_id=chat_id, model=model)
    if _SURFACE_DEFAULT == "inline":
        return _sse_chunk(text, chat_id=chat_id, model=model)
    return _sse_chunk(None, chat_id=chat_id, model=model, reasoning=text)

def _load_status_labels() -> dict:
    return {
        "prompt":         ("👂", ""),
        "refine":         ("✨", ""),
        "route":          ("🧭", ""),
        "plan":           ("🗺️", ""),
        "agent_target":   ("🤖", ""),
        "tool":           ("🛠️", ""),
        "tool_done":      ("✅", ""),
        "tool_done_warn": ("😅", ""),
        "chat":           ("💬", ""),
        "chat_done":      ("✅", ""),
        "dag_done":       ("✅", ""),
        "dag_done_warn":  ("😅", ""),
        "reflect":        ("🤔", ""),
        "subagent_done":  ("✅", ""),
    }

_HUMAN_LABELS = _load_status_labels()

def _sse_status_phase(*, chat_id: str, model: str, phase: str,
                      done: bool = False,
                      detail: Optional[str] = None) -> bytes:
    emoji, label = _HUMAN_LABELS.get(phase, ("·", phase))
    return _sse_status(chat_id=chat_id, model=model, emoji=emoji,
                       label=label, done=done, detail=detail)

def _sse_status(*, chat_id: str, model: str, emoji: str, label: str,
                done: bool = False, detail: Optional[str] = None) -> bytes:
    payload = {"emoji": emoji, "label": label, "done": done}
    _desc = f"{emoji} {label}".strip()
    if detail:
        d = str(detail).strip()
        if d:
            payload["detail"] = d[:80]
            payload["label"] = f"{label} · {d[:80]}" if label else d[:80]
            _desc = f"{_desc} · {d[:80]}".strip(" ·")
    _has_content = bool((label and str(label).strip())
                        or (detail and str(detail).strip()))
    _content = None
    _reason = None
    if STATUS_AS_REASONING and _has_content and not done:
        if _DEBUG_ENABLE:
            _content = (_desc + "\n")
        else:
            _reason = (_desc + "\n")
    return _sse_chunk(
        _content, chat_id=chat_id, model=model,
        mios_status=payload, reasoning=_reason,
    )

def _enrich_step_emits(refined: Optional[dict], *, chat_id: str, model: str):
    if not isinstance(refined, dict):
        return
    steps = ((refined.get("_web_steps") or [])
             + (refined.get("_readtool_steps") or [])
             + (refined.get("_verity_steps") or []))
    for s in steps:
        if not isinstance(s, dict):
            continue
        yield _sse_status(
            chat_id=chat_id, model=model,
            emoji=str(s.get("emoji", "·")), label=str(s.get("label", "")),
            detail=(str(s.get("detail", "")) or None))

def _node_context(node: dict) -> str:
    if not isinstance(node, dict):
        return ""
    if node.get("agent"):
        return str(node.get("title") or node.get("prompt")
                   or node.get("task") or "").strip()[:64]
    args = node.get("args") or {}
    if isinstance(args, dict):
        for _k in ("query", "id", "name", "path", "url", "title", "unit",
                   "text", "content", "script"):
            _v = args.get(_k)
            if _v:
                return f"{_k}={str(_v)[:48]}"
        for _v in args.values():
            if _v:
                return str(_v)[:48]
    return ""

def _node_status(*, chat_id: str, model: str, name: str, cfg: dict,
                 state: str, context: str = "") -> bytes:
    emoji = {"engage": "🤖", "ok": "✅", "down": "💤"}.get(state, "🤖")
    _ctx = str(context or "").strip()
    _role = str((cfg or {}).get("role") or "").strip()
    _label = _ctx or _role or "working"
    _detail = "" if _label == _ctx else _ctx
    return _sse_status(chat_id=chat_id, model=model, emoji=emoji,
                       label=_label[:80], detail=_detail[:80])

async def _stream_answer(text: str, *, chat_id: str, model: str):
    """Yield the final answer in small character-exact chunks so OWUI renders
    it progressively (live 'typing') instead of one end-of-turn burst -- the
    "thinking prints then switches to the refined copy" jolt (operator
). Pacing is bounded so long answers stream in ~1.2s, not slower.
    Char-slicing preserves the text byte-for-byte (markdown/code intact)."""
    if not text:
        return
    size = int(os.environ.get("MIOS_ANSWER_CHUNK_CHARS", "48"))
    chunks = [text[i:i + size] for i in range(0, len(text), max(1, size))]
    delay = min(0.03, 1.2 / max(1, len(chunks)))
    for ch in chunks:
        yield _sse_chunk(ch, chat_id=chat_id, model=model)
        if delay:
            await asyncio.sleep(delay)

def _sse_done() -> bytes:
    return b"data: [DONE]\n\n"

_TAIL_KIND_EMOJI = {
    "max_retries":    "❌",
    "invalid_tool":   "⚠️",
    "retry":          "↻",
    "delegate_spawn": "🚀",
    "synthesis":      "🔀",
    "subagent_done":  "✅",
    "tool_call":      "🛠️",
    "frontier":       "🛰️",
}
_HERMES_TAIL_PATH = os.environ.get(
    "MIOS_HERMES_TAIL_PATH", "/var/lib/mios/hermes-tail/latest.json")

def _frontier_stream_events(seen_ts: float) -> list:
    path = os.environ.get("MIOS_FRONTIER_STREAM_PATH") or os.path.join(
        os.path.dirname(_HERMES_TAIL_PATH), "frontier.jsonl")
    out: list = []
    try:
        with open(path) as f:
            lines = f.readlines()[-50:]   # only the tail matters (newest wins)
    except (OSError, ValueError):
        return out
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            ev = json.loads(ln)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(ev, dict) and ev.get("ts", 0) > seen_ts:
            out.append(ev)
    return out

def _tail_latest_status(seen_ts: float, *, chat_id: str,
                        model: str) -> tuple[Optional[bytes], float]:
    """If the hermes-tail holds an event newer than seen_ts, return its
    mios_status SSE chunk (emoji + generative detail) and the advanced
    ts; otherwise (None, seen_ts). Best-effort -- any read/parse error
    just yields no chunk."""
    try:
        with open(_HERMES_TAIL_PATH) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        data = {}
    newest = None
    new_ts = seen_ts
    for ev in data.get("events", []):
        ts = ev.get("ts", 0)
        if ts > new_ts:
            new_ts = ts
            newest = ev
    for ev in _frontier_stream_events(seen_ts):
        ts = ev.get("ts", 0)
        if ts > new_ts:
            new_ts = ts
            newest = ev
    if newest is None:
        return None, seen_ts
    emoji = _TAIL_KIND_EMOJI.get(str(newest.get("kind", "")), "·")
    detail = str(newest.get("detail", "")).strip()
    return (_sse_status(chat_id=chat_id, model=model, emoji=emoji,
                        label="", done=False, detail=detail), new_ts)

def _iter_answer_chunks(text: str, size: int):
    """Split `text` into ~size-char pieces at WORD boundaries so the final answer
 TYPES OUT smoothly in the front-ends (token-by-token).
    Whitespace is preserved (split keeps the separators). size<=0 -> one chunk."""
    if size <= 0 or len(text) <= size:
        yield text
        return
    buf = ""
    for tok in re.split(r"(\s+)", text):   # words + their trailing whitespace
        if buf and len(buf) + len(tok) > size:
            yield buf
            buf = ""
        buf += tok
    if buf:
        yield buf

def configure(*, debug_enable: bool = True, surface_default: str = "clean", **kwargs) -> None:
    global _DEBUG_ENABLE, _SURFACE_DEFAULT
    _DEBUG_ENABLE = bool(debug_enable)
    _SURFACE_DEFAULT = str(surface_default).strip().lower()
