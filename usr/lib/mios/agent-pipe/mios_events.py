#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# AI-hint: Authenticated WebSocket real-time agent execution token stream hub (task_462).
# AI-doc: usr/share/doc/mios/manual/ch04-the-agentic-ai-stack.md
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Set

log = logging.getLogger("mios-events")


class AgentEventHub:
    """Manages WebSocket subscribers and streams real-time agent execution chunks."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = api_key or os.environ.get("MIOS_AI_KEY", os.environ.get("MIOS_INGRESS_KEY", ""))
        self._subscribers: Dict[Any, asyncio.Queue] = {}
        self._session_filters: Dict[Any, Optional[str]] = {}

    def authenticate(self, token: Optional[str]) -> bool:
        """Validates bearer or query token. If no API key configured on host, allows local connection."""
        if not self.api_key:
            return True
        if not token:
            return False
        clean_token = token.strip()
        if clean_token.lower().startswith("bearer "):
            clean_token = clean_token[7:].strip()
        return clean_token == self.api_key

    def register(self, ws: Any, session_id: Optional[str] = None) -> asyncio.Queue:
        """Subscribes a client websocket connection with optional session_id filter."""
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers[ws] = q
        self._session_filters[ws] = session_id
        log.info("Registered WebSocket subscriber (session_filter=%s, total=%d)", session_id, len(self._subscribers))
        return q

    def unregister(self, ws: Any) -> None:
        """Removes a client connection."""
        self._subscribers.pop(ws, None)
        self._session_filters.pop(ws, None)
        log.info("Unregistered WebSocket subscriber (remaining=%d)", len(self._subscribers))

    def format_event(self, event_type: str, data: Dict[str, Any], session_id: Optional[str] = None) -> str:
        """Formats an execution token or lifecycle event into a JSON-RPC 2.0 message."""
        now = time.time()
        payload = {
            "jsonrpc": "2.0",
            "method": "agent/event",
            "params": {
                "type": event_type,
                "session_id": session_id or "default",
                "timestamp": now,
                "data": data,
            },
        }
        return json.dumps(payload)

    async def broadcast(self, event_type: str, data: Dict[str, Any], session_id: Optional[str] = None) -> int:
        """Broadcasts event to all eligible connected subscribers."""
        msg = self.format_event(event_type, data, session_id)
        delivered = 0
        dead_clients: List[Any] = []

        for ws, q in list(self._subscribers.items()):
            filter_sess = self._session_filters.get(ws)
            if filter_sess and session_id and filter_sess != session_id:
                continue

            try:
                q.put_nowait(msg)
                delivered += 1
            except asyncio.QueueFull:
                log.warning("Subscriber queue full; dropping slow consumer")
                dead_clients.append(ws)

        for dead in dead_clients:
            self.unregister(dead)

        return delivered
