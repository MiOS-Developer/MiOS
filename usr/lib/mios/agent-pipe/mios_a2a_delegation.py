# AI-hint: MiOS system and orchestration module providing mios a2a delegation capabilities.
# AI-functions: supports, to_dict, to_wire, from_wire, __init__, register, unregister, get_card, negotiate_mode, build_frame, best_peer, PayloadMode

"""
mios_a2a_delegation.py — task_289 MAO-06
Identity-Aware Delegation & Progressive Payload Negotiation for A2A federation.

Extends AgentCard schema with:
  supportedInterfaces[]  — ["text", "semantic_frame", "embedding_hints"]
  reasoning_profile      — "fast" | "deliberate" | "reflexive"
  cost_hint              — float (relative token cost estimate)

Payload negotiation selects the most compact mutually-supported format,
targeting ~35% token reduction when semantic_frame is available on both sides.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger(__name__)

class PayloadMode(str, Enum):
    TEXT           = "text"
    SEMANTIC_FRAME = "semantic_frame"
    EMBEDDING_HINTS = "embedding_hints"

# Priority order: most compact first
_MODE_PRIORITY = [
    PayloadMode.EMBEDDING_HINTS,
    PayloadMode.SEMANTIC_FRAME,
    PayloadMode.TEXT,
]

@dataclass
class AgentCard:
    """Extended AgentCard with capability metadata for A2A negotiation."""
    agent_id:             str
    endpoint:             str
    supported_interfaces: list[str] = field(
        default_factory=lambda: [PayloadMode.TEXT])
    reasoning_profile:    str   = "fast"
    cost_hint:            float = 1.0
    capabilities:         dict[str, Any] = field(default_factory=dict)

    def supports(self, mode: PayloadMode) -> bool:
        return mode.value in self.supported_interfaces

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id":             self.agent_id,
            "endpoint":             self.endpoint,
            "supportedInterfaces":  self.supported_interfaces,
            "reasoningProfile":     self.reasoning_profile,
            "costHint":             self.cost_hint,
            "capabilities":         self.capabilities,
        }

@dataclass
class DelegationFrame:
    """Negotiated delegation payload between two A2A peers."""
    mode:    PayloadMode
    content: Any        # str (text) | dict (semantic_frame) | list (embedding_hints)
    source_agent: str   = ""
    target_agent: str   = ""

    def to_wire(self) -> dict[str, Any]:
        return {
            "mode":         self.mode.value,
            "content":      self.content,
            "source_agent": self.source_agent,
            "target_agent": self.target_agent,
        }

    @classmethod
    def from_wire(cls, d: dict[str, Any]) -> "DelegationFrame":
        return cls(
            mode=PayloadMode(d["mode"]),
            content=d["content"],
            source_agent=d.get("source_agent", ""),
            target_agent=d.get("target_agent", ""),
        )

class DelegationRouter:
    """
    Routes A2A delegation to the best-matching peer and negotiates payload mode.
    """

    def __init__(self) -> None:
        self._registry: dict[str, AgentCard] = {}

    # ------------------------------------------------------------------
    def register(self, card: AgentCard) -> None:
        self._registry[card.agent_id] = card
        log.debug("A2A: registered agent %s @ %s", card.agent_id, card.endpoint)

    def unregister(self, agent_id: str) -> None:
        self._registry.pop(agent_id, None)

    def get_card(self, agent_id: str) -> AgentCard | None:
        return self._registry.get(agent_id)

    # ------------------------------------------------------------------
    def negotiate_mode(self, source: AgentCard,
                       target: AgentCard) -> PayloadMode:
        """
        Select the most compact payload mode both agents support.
        Falls back to text gracefully.
        """
        for mode in _MODE_PRIORITY:
            if source.supports(mode) and target.supports(mode):
                log.debug("A2A: negotiated mode=%s between %s→%s",
                          mode, source.agent_id, target.agent_id)
                return mode
        return PayloadMode.TEXT

    def build_frame(self, source_id: str, target_id: str,
                    content_text: str,
                    semantic_frame: dict[str, Any] | None = None,
                    embedding_hints: list[float] | None = None,
                    ) -> DelegationFrame:
        """
        Build a DelegationFrame using the negotiated payload mode.
        """
        src = self._registry.get(source_id) or AgentCard(
            agent_id=source_id, endpoint="")
        tgt = self._registry.get(target_id) or AgentCard(
            agent_id=target_id, endpoint="")
        mode = self.negotiate_mode(src, tgt)

        if mode == PayloadMode.EMBEDDING_HINTS and embedding_hints is not None:
            content: Any = embedding_hints
        elif mode == PayloadMode.SEMANTIC_FRAME and semantic_frame is not None:
            content = semantic_frame
        else:
            content = content_text
            mode    = PayloadMode.TEXT   # downgrade if no structured content

        return DelegationFrame(
            mode=mode,
            content=content,
            source_agent=source_id,
            target_agent=target_id,
        )

    def best_peer(self, capability: str,
                  exclude: str = "") -> AgentCard | None:
        """
        Select the cheapest (lowest cost_hint) peer that supports a capability.
        """
        candidates = [
            c for c in self._registry.values()
            if capability in c.capabilities
            and c.agent_id != exclude
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda c: c.cost_hint)
