# AI-hint: CaMeL dual-context QUARANTINE boundary -- the deeper half of the F2/task_33 prompt-injection defense (Debenedetti et al., "Defeating Promp...
# AI-doc: usr/share/doc/mios/manual/access.md

from __future__ import annotations

import mios_ruleof2

MODE_OFF = mios_ruleof2.MODE_OFF
MODE_AUDIT = mios_ruleof2.MODE_AUDIT
MODE_ENFORCE = mios_ruleof2.MODE_ENFORCE

PROP_UNTRUSTED = mios_ruleof2.PROP_UNTRUSTED       # A -- attacker-controllable content present
PROP_SENSITIVE = mios_ruleof2.PROP_SENSITIVE       # B -- reads sensitive / private data
PROP_STATECHANGE = mios_ruleof2.PROP_STATECHANGE   # C -- mutates state / side effects

ACT_PROCEED, ACT_AUDIT, ACT_GATE = "proceed", "audit", "gate"
_BITE_ACTION = {MODE_OFF: ACT_PROCEED, MODE_AUDIT: ACT_AUDIT, MODE_ENFORCE: ACT_GATE}

def normalize_mode(mode) -> str:
    """Resolve the SSOT ``[security].quarantine_mode`` value to a known enum; an
    empty/unknown token -> off (degrade-open: an unrecognised mode never silently
    enforces or audits). Delegates to the SHARED task_33 normaliser so the two
    architectural-gate modes can never drift in their parsing."""
    return mios_ruleof2.normalize_mode(mode)

class QuarantineVerdict:
    """The deterministic verdict for one ``(session_tainted, verb)`` quarantine
    evaluation: which axes are present (A / B / C), whether the verb is privileged
    (B OR C), whether the boundary BITES (tainted AND privileged), the SSOT mode in
    force, and the resulting action (proceed | audit | gate). Pure data -- the caller
    maps the action onto the dispatch outcome (proceed / audit-log / HITL-block)."""

    __slots__ = ("untrusted", "sensitive", "state_change", "privileged",
                 "bites", "mode", "action")

    def __init__(self, *, untrusted, sensitive, state_change, mode) -> None:
        self.untrusted = bool(untrusted)
        self.sensitive = bool(sensitive)
        self.state_change = bool(state_change)
        self.privileged = self.sensitive or self.state_change
        self.bites = self.untrusted and self.privileged
        self.mode = normalize_mode(mode)
        self.action = _BITE_ACTION[self.mode] if self.bites else ACT_PROCEED

    def to_dict(self) -> dict:
        return {
            "properties": {
                PROP_UNTRUSTED: self.untrusted,
                PROP_SENSITIVE: self.sensitive,
                PROP_STATECHANGE: self.state_change,
            },
            "privileged": self.privileged,
            "bites": self.bites,
            "mode": self.mode,
            "action": self.action,
        }

def evaluate(*, session_tainted, permission_tier, sensitive,
             mode: str = MODE_OFF) -> QuarantineVerdict:
    return QuarantineVerdict(
        untrusted=session_tainted,
        sensitive=sensitive,
        state_change=mios_ruleof2.is_state_change(permission_tier),
        mode=mode)

def quarantined_extract(untrusted_content, *, schema=None):
    return None
