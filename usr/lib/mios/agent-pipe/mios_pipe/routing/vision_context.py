# AI-hint: Client-tools CONTEXT BUDGET -- tiered pruning that fits a caller's messages + tools[] into the tool backend's window (stale results, compaction, schema budget, truncation), split out of vision.py.
# AI-related: usr/lib/mios/agent-pipe/mios_pipe/routing/vision.py, usr/lib/mios/agent-pipe/mios_pipe/context/compact.py, usr/lib/mios/agent-pipe/test_mios_vision.py, tests/test_adversarial_gateway_stress.py
# AI-doc: usr/share/doc/mios/manual/routing.md

from __future__ import annotations

import json
import logging
import os

import mios_tokenize  # WS-A5 tokenizer seam -- token estimate for the ctx clamp

log = logging.getLogger("mios-agent-pipe")

_ELIDED_TOOL_RESULT = "[tool result elided to fit context budget]"
_ELIDED_IMAGE = "[image omitted to fit context budget]"
_TRUNC_MARKER = "\n[...truncated to fit context budget...]"
_DEFAULT_TOOL_CTX = 32768


def _tool_ctx() -> int:
    """Context window for the client-tools lane (env MIOS_AGENT_PIPE_TOOL_CTX).
    An unparsable or non-positive value falls back to the default instead of
    raising out of the degrade path."""
    try:
        val = int(os.environ.get("MIOS_AGENT_PIPE_TOOL_CTX", "") or _DEFAULT_TOOL_CTX)
        return val if val > 0 else _DEFAULT_TOOL_CTX
    except (TypeError, ValueError):
        return _DEFAULT_TOOL_CTX


def _call_ids(msg) -> list:
    if not isinstance(msg, dict) or msg.get("role") != "assistant":
        return []
    return [c.get("id") for c in (msg.get("tool_calls") or [])
            if isinstance(c, dict) and c.get("id")]


def _repair_message_shape(messages: list) -> list:
    """Restore an OpenAI-valid sequence after messages were dropped.

    * every assistant tool_call keeps a matching tool result (unanswered calls
      are removed from the assistant message);
    * every tool result answers a tool_call of the nearest preceding assistant
      turn (orphans are dropped);
    * the first non-system turn is a user turn;
    * consecutive plain user/assistant turns are merged so roles alternate.
    """
    msgs = [m for m in messages if isinstance(m, dict)]
    answered = {m.get("tool_call_id") for m in msgs if m.get("role") == "tool"}
    fixed: list = []
    open_ids: set = set()
    for m in msgs:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            calls = [c for c in m["tool_calls"]
                     if isinstance(c, dict) and c.get("id") in answered]
            m = {**m}
            if calls:
                m["tool_calls"] = calls
            else:
                m.pop("tool_calls", None)
                if not m.get("content"):
                    open_ids = set()
                    continue
            open_ids = {c.get("id") for c in calls}
        elif role == "tool":
            if m.get("tool_call_id") not in open_ids:
                continue
        elif role in ("user", "system", "developer"):
            open_ids = set()
        fixed.append(m)
    # First non-system turn must be a user turn.
    head = [m for m in fixed if m.get("role") in ("system", "developer")]
    body = [m for m in fixed if m.get("role") not in ("system", "developer")]
    while body and body[0].get("role") != "user":
        body.pop(0)
    # Merge consecutive plain turns of the same role.
    merged: list = []
    for m in body:
        prev = merged[-1] if merged else None
        plain = m.get("role") in ("user", "assistant") and not m.get("tool_calls")
        if (prev is not None and plain and prev.get("role") == m.get("role")
                and not prev.get("tool_calls")):
            a, b = prev.get("content"), m.get("content")
            if isinstance(a, list) or isinstance(b, list):
                la = a if isinstance(a, list) else ([{"type": "text", "text": a}] if a else [])
                lb = b if isinstance(b, list) else ([{"type": "text", "text": b}] if b else [])
                merged[-1] = {**prev, "content": la + lb}
            else:
                merged[-1] = {**prev, "content": "\n\n".join(x for x in (a, b) if x)}
            continue
        merged.append(m)
    return head + merged


def _drop_stale_tool_results(messages: list, ttl_turns: int = 1) -> list:
    """Elide tool results older than ttl_turns assistant turns.

    The result message is kept with placeholder content so every assistant
    tool_call still has its answer (dropping it would make the request invalid).
    """
    out = []
    assistant_count = 0
    for msg in reversed(messages):
        if isinstance(msg, dict):
            role = msg.get("role")
            if role == "assistant":
                assistant_count += 1
            if role in ("tool", "function") and assistant_count > ttl_turns:
                msg = {**msg, "content": _ELIDED_TOOL_RESULT}
        out.append(msg)
    return list(reversed(out))


def _content_tokens(content) -> int:
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                total += mios_tokenize.count_text(str(part.get("text") or ""))
            elif isinstance(part, dict):
                total += mios_tokenize.count_text(json.dumps(part))
        return total
    return mios_tokenize.count_text(str(content or ""))


def _truncate_text(text: str, max_tok: int) -> str:
    if mios_tokenize.count_text(text) <= max_tok:
        return text
    keep = max(10, max_tok - mios_tokenize.count_text(_TRUNC_MARKER))
    return mios_tokenize.truncate_to_tokens(text, keep) + _TRUNC_MARKER


def _truncate_content(content, max_tok: int):
    """Truncate content to about max_tok tokens, preserving its type.

    Strings are cut with a marker. OpenAI content-part lists stay lists: image
    parts become a text placeholder, then the largest text parts are cut.
    """
    if not isinstance(content, list):
        return _truncate_text(str(content or ""), max_tok)
    parts = []
    for part in content:
        if isinstance(part, dict) and part.get("type") not in (None, "text"):
            parts.append({"type": "text", "text": _ELIDED_IMAGE})
        elif isinstance(part, dict):
            parts.append(dict(part))
    while _content_tokens(parts) > max_tok:
        texts = [(mios_tokenize.count_text(str(p.get("text") or "")), i)
                 for i, p in enumerate(parts) if p.get("type") == "text"]
        if not texts:
            break
        tok, idx = max(texts)
        excess = _content_tokens(parts) - max_tok
        if tok <= 20:
            break
        parts[idx] = {**parts[idx],
                      "text": _truncate_text(str(parts[idx].get("text") or ""), max(10, tok - excess))}
        if mios_tokenize.count_text(str(parts[idx]["text"])) >= tok:
            break
    return parts


def _compact_tool_schema(tool: dict) -> dict:
    """Shrink a tool definition without changing its call signature: trim the
    function description and drop nested property descriptions/examples."""
    if not isinstance(tool, dict):
        return tool

    def _strip(node):
        if isinstance(node, dict):
            return {k: _strip(v) for k, v in node.items()
                    if k not in ("description", "examples", "example", "title")}
        if isinstance(node, list):
            return [_strip(v) for v in node]
        return node

    out = dict(tool)
    fn = dict(out.get("function") or {})
    if fn:
        if fn.get("description"):
            fn["description"] = _truncate_text(str(fn["description"]), 48)
        if isinstance(fn.get("parameters"), dict):
            fn["parameters"] = _strip(fn["parameters"])
        out["function"] = fn
    return out


def _tool_name(tool) -> str:
    if not isinstance(tool, dict):
        return ""
    return str((tool.get("function") or {}).get("name") or tool.get("name") or "")


def _forced_tool_name(tool_choice) -> str:
    if isinstance(tool_choice, dict):
        return str((tool_choice.get("function") or {}).get("name") or tool_choice.get("name") or "")
    return ""


def _budget_tools(tools: list, budget: int, forced: str) -> list:
    """Keep tools in caller order (forced tool first) while they fit `budget`."""
    ordered = sorted(tools, key=lambda t: 0 if forced and _tool_name(t) == forced else 1)
    kept, used = [], 0
    for t in ordered:
        cost = mios_tokenize.count_messages([], tools=[t])
        if kept and used + cost > budget and _tool_name(t) != forced:
            continue
        kept.append(t)
        used += cost
    return kept


def _prune_request_to_context_budget(req: dict, max_ctx: int = _DEFAULT_TOOL_CTX) -> dict:
    """Principled multi-tier request pruning before backend dispatch so the
    payload fits the backend context instead of failing with HTTP 400.

    Tiers (each runs only while still over budget):
      1. tool_choice 'none' -> drop tools and tool_choice.
      2. Elide stale tool results (placeholder keeps tool_call pairing).
      3. Compact middle turns via plan_compaction, then repair the sequence.
      4. Budget tool definitions: compact schemas, then keep tools in caller
         order (forced tool always kept) within half the budget.
      5. Truncate each oversized message (content-part aware).
      6. Iteratively truncate the largest message.
    Finally repair the sequence and clamp max_tokens to what actually fits.
    """
    req = dict(req)
    messages = [m for m in list(req.get("messages") or []) if isinstance(m, dict)]
    tools = [t for t in (req.get("tools") or []) if isinstance(t, dict)] \
        if isinstance(req.get("tools"), list) else None

    if str(req.get("tool_choice") or "").strip().lower() == "none":
        req.pop("tools", None)
        req.pop("tool_choice", None)
        tools = None

    def _count(msgs, tls):
        return mios_tokenize.count_messages(msgs, tools=tls or None)

    target = max(1024, max_ctx - 1024)
    in_tokens = _count(messages, tools)

    if in_tokens > target:
        messages = _drop_stale_tool_results(messages, ttl_turns=1)
        in_tokens = _count(messages, tools)

    if in_tokens > target:
        from mios_pipe.context.compact import plan_compaction
        tools_tok = _count([], tools) if tools else 0
        plan = plan_compaction(messages, budget=max(512, target - tools_tok),
                               keep_recent=4, keep_system=True)
        if plan.needed and plan.to_keep:
            messages = _repair_message_shape(plan.to_keep)
            in_tokens = _count(messages, tools)

    if in_tokens > target and tools:
        tool_budget = target // 2
        if _count([], tools) > tool_budget:
            tools = [_compact_tool_schema(t) for t in tools]
        if _count([], tools) > tool_budget:
            before = len(tools)
            tools = _budget_tools(tools, tool_budget, _forced_tool_name(req.get("tool_choice")))
            log.warning("context budget: kept %d of %d caller tools", len(tools), before)
        in_tokens = _count(messages, tools)

    if in_tokens > target:
        avail = max(256, target - (_count([], tools) if tools else 0))
        messages = [{**m, "content": _truncate_content(m.get("content"), avail)}
                    if m.get("content") and _content_tokens(m.get("content")) > avail else m
                    for m in messages]
        in_tokens = _count(messages, tools)

    for _ in range(64):
        if in_tokens <= target or not messages:
            break
        cands = [(_content_tokens(m.get("content")), i) for i, m in enumerate(messages)
                 if m.get("role") not in ("system", "developer") and m.get("content")]
        if not cands or max(cands)[0] < 50:
            cands = [(_content_tokens(m.get("content")), i)
                     for i, m in enumerate(messages) if m.get("content")]
        if not cands:
            break
        tok, idx = max(cands)
        if tok <= 25:
            break
        excess = in_tokens - target + 64
        messages[idx] = {**messages[idx],
                         "content": _truncate_content(messages[idx]["content"], max(25, tok - excess))}
        new_tokens = _count(messages, tools)
        if new_tokens >= in_tokens:
            break
        in_tokens = new_tokens

    messages = _repair_message_shape(messages)
    req["messages"] = messages
    if tools is not None:
        if tools:
            req["tools"] = tools
        else:
            req.pop("tools", None)
            req.pop("tool_choice", None)
    in_tokens = _count(messages, tools)

    cap = max_ctx - in_tokens - 256
    if cap < 64:
        log.warning("context budget: prompt %d leaves %d tokens of %d", in_tokens, cap, max_ctx)
        cap = 64
    req_mt = int(req.get("max_tokens") or 0)
    if req_mt <= 0 or req_mt > cap:
        req["max_tokens"] = cap
    return req
