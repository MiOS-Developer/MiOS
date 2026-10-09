# AI-hint: MiOS system and orchestration module providing mios priority sched capabilities.
# AI-related: usr/lib/mios/agent-pipe/test_mios_priority_sched.py
# AI-functions: age_s, inject_headers, to_request_body_extra, _priority_name, __init__, wrap, augment_headers, sorted_queue, drain, classify_turn, PriorityRequest, PriorityGate

"""
mios_priority_sched.py — task_283 task_3217
Engine-level priority scheduling: forward x-priority header to heavy inference
lanes (vLLM, SGLang, llama-swap) so foreground user turns preempt background
autonomous-agent batches at the engine level.

Priority scale: 1 (highest) .. 10 (lowest). Foreground user = 1. Autonomous
agent batches = 5..10.  Values map to vLLM/SGLang priority and llama-swap
priority_boost.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# Priority constants per mios.toml [agents.scheduling]
PRIORITY_FOREGROUND  = 1   # live user turn
PRIORITY_INTERACTIVE = 3   # tool-call reply expected <5 s
PRIORITY_BACKGROUND  = 5   # autonomous daemon batch
PRIORITY_SCRUB       = 9   # low-importance background sweep
PRIORITY_IDLE        = 10  # sleep-mode housekeeping

_DEFAULT_TIMEOUT_S = 30.0  # max wait in priority queue

@dataclass
class PriorityRequest:
    """Wrapper that attaches a priority level to an inference payload."""
    payload: dict[str, Any]
    priority: int = PRIORITY_BACKGROUND
    enqueued_at: float = field(default_factory=time.monotonic)

    @property
    def age_s(self) -> float:
        return time.monotonic() - self.enqueued_at

    def inject_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """Return headers dict with x-priority and x-mios-priority-hint set."""
        out = dict(headers)
        out["x-priority"] = str(self.priority)
        out["x-mios-priority-hint"] = _priority_name(self.priority)
        return out

    def to_request_body_extra(self) -> dict[str, Any]:
        """Extra fields for engines that accept priority in request body."""
        return {
            "priority": self.priority,
            "priority_hint": _priority_name(self.priority),
        }

def _priority_name(p: int) -> str:
    names = {
        1: "foreground",
        3: "interactive",
        5: "background",
        9: "scrub",
        10: "idle",
    }
    return names.get(p, f"level-{p}")

class PriorityGate:
    """
    Simple priority gate — wraps outbound HTTP calls to inference lanes and
    injects the priority signal.  A real production implementation would use
    an asyncio PriorityQueue; this in-process implementation provides the
    correct interface for unit testing and progressive enhancement.
    """

    def __init__(self, default_priority: int = PRIORITY_BACKGROUND,
                 timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
        self.default_priority = default_priority
        self.timeout_s = timeout_s
        self._submitted: list[PriorityRequest] = []

    # ------------------------------------------------------------------
    def wrap(self, payload: dict[str, Any],
             priority: int | None = None) -> PriorityRequest:
        """Wrap a raw inference payload with a priority level."""
        p = priority if priority is not None else self.default_priority
        req = PriorityRequest(payload=payload, priority=p)
        self._submitted.append(req)
        log.debug("PriorityGate: queued request priority=%d", p)
        return req

    def augment_headers(self, req: PriorityRequest,
                        base_headers: dict[str, str] | None = None
                        ) -> dict[str, str]:
        """Augment HTTP headers for the inference backend call."""
        return req.inject_headers(base_headers or {})

    def sorted_queue(self) -> list[PriorityRequest]:
        """Return pending requests sorted lowest-number (highest priority) first."""
        return sorted(self._submitted, key=lambda r: r.priority)

    def drain(self, count: int = 1) -> list[PriorityRequest]:
        """Pop up to *count* highest-priority requests."""
        self._submitted.sort(key=lambda r: r.priority)
        out, self._submitted = self._submitted[:count], self._submitted[count:]
        return out

    # ------------------------------------------------------------------
    def classify_turn(self, messages: list[dict[str, Any]],
                      is_streaming: bool = True) -> int:
        """
        Heuristic: classify turn priority from conversation context.
        Returns a priority integer.
        """
        if not messages:
            return self.default_priority
        last_role = messages[-1].get("role", "user")
        if last_role == "user" and is_streaming:
            return PRIORITY_FOREGROUND
        if last_role == "tool":
            return PRIORITY_INTERACTIVE
        return PRIORITY_BACKGROUND
