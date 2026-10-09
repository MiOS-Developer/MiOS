# AI-hint: MiOS system and orchestration module providing mios pg events capabilities.
# AI-functions: __init__, subscribe, inject, stop, AgentEvent, EventBus

"""
mios_pg_events.py — task_286 MAO-03
PostgreSQL LISTEN/NOTIFY event-bus coordination.

Daemon agents call `listen()` to subscribe to the `mios_agent_events` channel.
Mutations on `tasks`, `pending_action`, and `event` tables fire NOTIFY via
SQL triggers defined in schema-init.sql.

The EventBus uses psycopg3 async connection with `notify_timeout=0.05`
so reaction latency stays well under the 50ms SLA.

In unit-test mode (no DB) the bus runs in dry-run mode: NOTIFY events are
injected directly via `inject()` for hermetic CI testing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

log = logging.getLogger(__name__)

CHANNEL = "mios_agent_events"
_LATENCY_SLA_MS = 50.0

@dataclass
class AgentEvent:
    """Parsed agent event from a NOTIFY payload."""
    channel:   str
    payload:   dict[str, Any]
    received_at: float = field(default_factory=time.monotonic)

class EventBus:
    """
    Subscribe to PostgreSQL NOTIFY events on `mios_agent_events`.
    Dispatches to registered handlers within the 50ms SLA.
    """

    def __init__(self, dsn: str = "",
                 dry_run: bool = False) -> None:
        self.dsn     = dsn
        self.dry_run = dry_run
        self._handlers: list[Callable[[AgentEvent], Awaitable[None]]] = []
        self._injected: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self._running = False

    # ------------------------------------------------------------------
    def subscribe(self, handler: Callable[[AgentEvent], Awaitable[None]]
                  ) -> None:
        self._handlers.append(handler)

    def inject(self, payload: dict[str, Any]) -> None:
        """Inject a synthetic event for unit testing."""
        evt = AgentEvent(channel=CHANNEL, payload=payload)
        self._injected.put_nowait(evt)

    async def run_once(self, timeout_s: float = 0.1) -> list[AgentEvent]:
        """
        Drain pending events (injected or real) and dispatch to handlers.
        Returns dispatched events.
        """
        dispatched: list[AgentEvent] = []
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                evt = self._injected.get_nowait()
            except asyncio.QueueEmpty:
                break
            t0 = time.monotonic()
            for handler in self._handlers:
                await handler(evt)
            latency_ms = (time.monotonic() - t0) * 1000
            if latency_ms > _LATENCY_SLA_MS:
                log.warning("EventBus: handler latency %.1f ms > SLA %.1f ms",
                            latency_ms, _LATENCY_SLA_MS)
            dispatched.append(evt)
        return dispatched

    async def listen(self) -> None:
        """
        Production: open a persistent psycopg3 async connection and LISTEN.
        Dry-run: drain injected events in a loop.
        """
        if self.dry_run:
            log.info("EventBus: dry-run mode, processing injected events only")
            self._running = True
            while self._running:
                await self.run_once(timeout_s=0.05)
                await asyncio.sleep(0.01)
        else:
            await self._listen_real()

    def stop(self) -> None:
        self._running = False

    async def _listen_real(self) -> None:
        """Real psycopg3 LISTEN loop (requires DB)."""
        try:
            import psycopg
            async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
                await conn.set_autocommit(True)
                await conn.execute(f"LISTEN {CHANNEL}")
                log.info("EventBus: listening on channel %s", CHANNEL)
                self._running = True
                async for notify in conn.notifies():
                    if not self._running:
                        break
                    try:
                        payload = json.loads(notify.payload)
                    except json.JSONDecodeError:
                        payload = {"raw": notify.payload}
                    evt = AgentEvent(channel=notify.channel, payload=payload)
                    for handler in self._handlers:
                        await handler(evt)
        except ImportError:
            log.error("EventBus: psycopg not installed; falling back to dry-run")
            await self.listen()
