# AI-hint: OpenAI streaming SSE chunk + status-emit primitives extracted from server.py (refactor WS R2 leaf wave).
# AI-doc: usr/share/doc/mios/manual/routing.md

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, Optional

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
