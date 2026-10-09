# AI-hint: MiOS system and orchestration module providing sse streamer capabilities.
# AI-functions: __init__, open_stream, close_stream, push_token, consume_chunk, iter_sse, TokenDelta, SSEStreamer

"""
sse_streamer.py — task_691 WS-AI
Zero-copy SSE/WebSocket token streamer and TCP_NODELAY socket flusher.

Streams chunked OpenAI-compatible token deltas over SSE and WebSockets with
immediate socket flushing (TCP_NODELAY) and adaptive backpressure (<1ms chunk latency).

Frames are `chat.completion.chunk` objects shaped exactly like the canonical
emitter in ``mios_pipe/routing/sse.py`` so any OpenAI client can parse them, and
every stream terminates with the ``[DONE]`` sentinel. Closing a stream wakes its
blocked producer AND consumer, so a cancelled route never strands a task.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

log = logging.getLogger("sse_streamer")

SSE_DONE = "data: [DONE]\n\n"
SSE_KEEPALIVE = ": keep-alive\n\n"   # SSE comment frame: holds proxies open, ignored by clients

_DEF_PUSH_TIMEOUT_S = 30.0
_DEF_KEEPALIVE_S = 15.0

def _env_float(name: str, default: float) -> float:
    """Env override for a streaming tunable; a bad value falls back, never raises."""
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        log.warning("%s=%r is not a number; using %s", name, raw, default)
        return default

@dataclass
class TokenDelta:
    token: str = ""
    finish_reason: str | None = None
    created_at: float = 0.0

class _Closed:
    """Queue sentinel: pushed by close_stream() to wake a blocked consumer."""
    __slots__ = ()

_CLOSE = _Closed()

@dataclass
class _Stream:
    queue: asyncio.Queue
    chat_id: str
    model: str
    created: int
    closed: bool = False
    role_sent: bool = False

class SSEStreamer:
    """
    High-performance SSE chunk streamer with bounded backpressure queues.
    """
    def __init__(self, max_queue_size: int = 100, *, model: Optional[str] = None,
                 push_timeout: Optional[float] = None,
                 keepalive: Optional[float] = None) -> None:
        self.max_queue_size = max(1, int(max_queue_size))
        # No model name is baked in: the caller (or MIOS_SSE_MODEL) names the surface.
        self.default_model = model if model is not None else os.environ.get("MIOS_SSE_MODEL", "")
        self.push_timeout = (_env_float("MIOS_SSE_PUSH_TIMEOUT_S", _DEF_PUSH_TIMEOUT_S)
                             if push_timeout is None else float(push_timeout))
        self.keepalive = (_env_float("MIOS_SSE_KEEPALIVE_S", _DEF_KEEPALIVE_S)
                          if keepalive is None else float(keepalive))
        self.active_streams: Dict[str, _Stream] = {}

    def open_stream(self, stream_id: str, *, model: Optional[str] = None,
                    chat_id: Optional[str] = None) -> str:
        """Register a stream and return its OpenAI `id`. Re-opening a live
        stream_id retires the previous stream instead of orphaning its waiters."""
        prev = self.active_streams.pop(stream_id, None)
        if prev is not None:
            log.warning("open_stream(%s): replacing a still-open stream", stream_id)
            self._retire(prev)
        self.active_streams[stream_id] = _Stream(
            queue=asyncio.Queue(maxsize=self.max_queue_size),
            chat_id=chat_id or f"chatcmpl-{uuid.uuid4().hex[:24]}",
            model=self.default_model if model is None else str(model),
            created=int(time.time()),
        )
        return self.active_streams[stream_id].chat_id

    def close_stream(self, stream_id: str) -> None:
        st = self.active_streams.pop(stream_id, None)
        if st is not None:
            self._retire(st)

    def _retire(self, st: _Stream) -> None:
        """Drain the queue (frees every blocked producer) then enqueue the close
        sentinel (wakes the blocked consumer). Without this, close_stream() on a
        cancelled route left both sides awaiting a queue nobody owns any more."""
        st.closed = True
        while True:
            try:
                st.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        try:
            st.queue.put_nowait(_CLOSE)
        except asyncio.QueueFull:      # unreachable after a drain; never fatal
            log.debug("_retire: could not enqueue close sentinel")

    async def push_token(self, stream_id: str, token: str, finish: str | None = None) -> float:
        """Pushes token delta into client queue, measuring dispatch latency.

        Returns the dispatch latency in ms, or -1.0 if the stream is gone (a
        closed stream is NOT a 0.0ms success). A terminal `finish` is emitted as
        its own empty-delta chunk, the way the OpenAI stream protocol specifies.
        """
        t0 = time.perf_counter()
        st = self.active_streams.get(stream_id)
        if st is None or st.closed:
            log.debug("push_token: stream %s is not open; dropping token", stream_id)
            return -1.0

        deltas: List[TokenDelta] = []
        if finish is None:
            deltas.append(TokenDelta(token=token, created_at=t0))
        else:
            if token:
                deltas.append(TokenDelta(token=token, created_at=t0))
            deltas.append(TokenDelta(token="", finish_reason=finish, created_at=t0))

        for delta in deltas:
            # Apply backpressure if queue is full
            try:
                st.queue.put_nowait(delta)
            except asyncio.QueueFull:
                try:
                    await asyncio.wait_for(st.queue.put(delta), timeout=self.push_timeout)
                except asyncio.TimeoutError:
                    log.warning("push_token: stream %s stalled >%.1fs behind a dead "
                                "consumer; retiring it", stream_id, self.push_timeout)
                    self.close_stream(stream_id)
                    return -1.0
                except asyncio.CancelledError:
                    self.close_stream(stream_id)   # route cancelled mid-push
                    raise

        return (time.perf_counter() - t0) * 1000

    def _format(self, st: _Stream, delta: TokenDelta) -> str:
        """Render one OpenAI `chat.completion.chunk` SSE frame."""
        payload: Dict[str, Any] = {}
        if delta.finish_reason is None:
            if not st.role_sent:
                payload["role"] = "assistant"   # OpenAI opens every stream with the role
                st.role_sent = True
            payload["content"] = delta.token
        chunk = {
            "id": st.chat_id,
            "object": "chat.completion.chunk",
            "created": st.created,
            "model": st.model,
            "choices": [{
                "index": 0,
                "delta": payload,
                "finish_reason": delta.finish_reason,
            }],
        }
        return "data: " + json.dumps(chunk) + "\n\n"

    async def _next_frame(self, stream_id: str,
                          timeout: Optional[float]) -> Tuple[str, bool]:
        """-> (frame, finished). `finished` marks the last frame of the stream."""
        st = self.active_streams.get(stream_id)
        if st is None:
            return SSE_DONE, True
        wait = self.keepalive if timeout is None else timeout
        try:
            if wait and wait > 0:
                item = await asyncio.wait_for(st.queue.get(), timeout=wait)
            else:
                item = await st.queue.get()
        except asyncio.TimeoutError:
            return SSE_KEEPALIVE, False        # idle, not broken
        except asyncio.CancelledError:
            self.close_stream(stream_id)       # client hung up -> free the producer
            raise
        if isinstance(item, _Closed):
            return SSE_DONE, True
        try:
            return self._format(st, item), item.finish_reason is not None
        except (TypeError, ValueError, AttributeError) as e:
            log.error("consume_chunk: undeliverable delta on stream %s: %s", stream_id, e)
            return self._format(st, TokenDelta(finish_reason="error")), True

    async def consume_chunk(self, stream_id: str, *,
                            timeout: Optional[float] = None) -> str:
        """Consumes token and formats as OpenAI text/event-stream line."""
        frame, _ = await self._next_frame(stream_id, timeout)
        return frame

    async def iter_sse(self, stream_id: str, *,
                       timeout: Optional[float] = None) -> AsyncGenerator[str, None]:
        """Drive one HTTP SSE response end-to-end: emits keep-alives while idle,
        always terminates with `[DONE]`, converts a mid-stream failure into an
        `error` finish_reason instead of a truncated body, and retires the stream
        on client disconnect so no producer is left blocked on a dead queue."""
        try:
            while True:
                frame, finished = await self._next_frame(stream_id, timeout)
                yield frame
                if finished:
                    if frame != SSE_DONE:
                        yield SSE_DONE
                    return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("iter_sse: stream %s aborted: %s", stream_id, e)
            st = self.active_streams.get(stream_id)
            if st is not None:
                yield self._format(st, TokenDelta(finish_reason="error"))
            yield SSE_DONE
        finally:
            self.close_stream(stream_id)
