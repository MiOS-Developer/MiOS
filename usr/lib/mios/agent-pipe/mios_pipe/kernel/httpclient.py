# AI-hint: The ONE shared outbound httpx.AsyncClient for the whole pipe, extracted verbatim from server.py, plus the WS-A6/task_206 batch-coalescing c...
# AI-doc: usr/share/doc/mios/manual/kernel.md
"""The shared outbound HTTP client + the batch-coalescing request hook."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx

from mios_pipe.scheduler import batch as _batch

log = logging.getLogger("mios-agent-pipe")

_client: httpx.AsyncClient | None = None
_coalescer: "_batch.Coalescer | None" = None

_BATCH_ENABLE = False
_BATCH_INTERVAL_S = 0.05
_BATCH_MAX_SIZE = 8
_BATCH_NATIVE_HINTS: list = []
_CONNECT_TIMEOUT_S = 10.0

def configure(*, batch_enable=False, batch_interval_s=0.05, batch_max_size=8,
              batch_native_hints=(), connect_timeout_s=10.0) -> None:
    """One-way injection of the [dispatch] batch knobs from server.py."""
    global _BATCH_ENABLE, _BATCH_INTERVAL_S, _BATCH_MAX_SIZE
    global _BATCH_NATIVE_HINTS, _CONNECT_TIMEOUT_S
    _BATCH_ENABLE = bool(batch_enable)
    _BATCH_INTERVAL_S = float(batch_interval_s)
    _BATCH_MAX_SIZE = int(batch_max_size)
    _BATCH_NATIVE_HINTS = list(batch_native_hints or [])
    _CONNECT_TIMEOUT_S = float(connect_timeout_s)

def get_coalescer():
    """The live Coalescer, or None when the feature is off."""
    return _coalescer

async def reset() -> None:
    """Drop the memoised client so the next _get_client() rebuilds it."""
    global _client, _coalescer
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
    _client = None
    _coalescer = None

async def _batch_request_hook(request) -> None:
    """Hold a non-native upstream call in its (endpoint, model) window.

    Degrades open: every failure path sends the request unheld. Manual ch59."""
    try:
        if _coalescer is None or request.method != "POST":
            return
        model = ""
        try:
            body = request.content
        except Exception:
            body = b""          # streaming body -- not readable here, not batchable
        if body:
            try:
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    model = str(parsed.get("model") or "")
            except (ValueError, TypeError):
                model = ""
        host = request.url.host or ""
        port = request.url.port
        await _coalescer.hold(f"{host}:{port}" if port else host, model)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.debug("batch coalescing skipped: %s", e)

async def _get_client() -> httpx.AsyncClient:
    global _client, _coalescer
    if _client is None:
        kwargs = {
            "timeout": httpx.Timeout(connect=_CONNECT_TIMEOUT_S,
                                     read=None, write=None, pool=None),
        }
        # Registered only when the flag is on: at the default the client is
        # built exactly as before. Manual ch59.
        if _BATCH_ENABLE:
            _coalescer = _batch.Coalescer(
                enabled=True,
                interval_s=_BATCH_INTERVAL_S,
                max_size=_BATCH_MAX_SIZE,
                native_hints=_BATCH_NATIVE_HINTS,
            )
            kwargs["event_hooks"] = {"request": [_batch_request_hook]}
        _client = httpx.AsyncClient(**kwargs)
    return _client
