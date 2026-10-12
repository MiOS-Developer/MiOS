# AI-hint: Chat CONVERSATION HISTORY -- the gateway session-replay store (Postgres gateway_sessions), the stale tool-result TTL drop and the evicted-turn summarizer, split out of chat.py.
# AI-related: usr/lib/mios/agent-pipe/mios_pipe/routing/chat.py, usr/lib/mios/agent-pipe/test_mios_compact.py, usr/lib/mios/gateway-agent/session.py
# AI-doc: usr/share/doc/mios/manual/routing.md

from __future__ import annotations

import json
import logging

import httpx

import mios_pg as _mios_pg   # WS-9 Postgres client
from mios_config import ROUTER_MODEL, PLANNER_ENDPOINT, PLANNER_TIMEOUT_S

log = logging.getLogger("mios-agent-pipe")

def _drop_stale_tool_results(messages: list, ttl_turns: int) -> list:
    """Drop tool result messages older than ttl_turns turns ago."""
    new_msgs = []
    assistant_count = 0
    for msg in reversed(messages):
        role = msg.get("role")
        if role == "assistant":
            assistant_count += 1
        if role in ("tool", "function"):
            if assistant_count > ttl_turns:
                continue
        new_msgs.append(msg)
    return list(reversed(new_msgs))

async def _summarize_evicted_messages(evicted_messages: list) -> str:
    """Precise summarization helper using the planner/model endpoint."""
    history_str = ""
    for m in evicted_messages:
        role = str(m.get("role") or "").upper()
        content = str(m.get("content") or "").strip()
        history_str += f"{role}: {content}\n"

    payload = {
        "model": ROUTER_MODEL,
        "messages": [
            {"role": "system", "content": "You are a precise summarization assistant. Summarize the key facts, tasks, preferences, and details from the following conversation history in a concise, bulleted format. Keep the summary under 200 words. Focus strictly on facts and decisions made, omitting conversational filler."},
            {"role": "user", "content": history_str}
        ],
        "temperature": 0.0,
        "max_tokens": 300,
        "stream": False
    }
    try:
        async with httpx.AsyncClient(timeout=PLANNER_TIMEOUT_S) as s:
            r = await s.post(f"{PLANNER_ENDPOINT}/v1/chat/completions", json=payload,
                             headers={"Content-Type": "application/json"})
            if r.status_code == 200:
                res = r.json()
                summary = (res.get("choices") or [{}])[0].get("message", {}).get("content") or ""
                return summary.strip()
    except Exception as e:
        log.warning("Failed to summarize evicted messages: %s", e)
    return "Archive of oldest conversation history turns."

async def _get_gateway_session(session_id: str) -> list[dict]:
    try:
        sql = "SELECT messages FROM gateway_sessions WHERE session_id = %(session_id)s"
        rows = await _mios_pg.execute(sql, {"session_id": session_id}, fetch=True)
        if rows:
            messages = rows[0].get("messages")
            if isinstance(messages, str):
                return json.loads(messages)
            return messages or []
    except Exception as e:
        log.warning("Database error fetching gateway session %s: %s", session_id, e)
    return []

async def _save_gateway_session(session_id: str, messages: list[dict]) -> None:
    try:
        sql = """
            INSERT INTO gateway_sessions (session_id, messages, updated_at)
            VALUES (%(session_id)s, %(messages)s, CURRENT_TIMESTAMP)
            ON CONFLICT (session_id)
            DO UPDATE SET messages = EXCLUDED.messages, updated_at = CURRENT_TIMESTAMP
        """
        await _mios_pg.execute(sql, {"session_id": session_id, "messages": json.dumps(messages)})
    except Exception as e:
        log.warning("Database error saving gateway session %s: %s", session_id, e)
