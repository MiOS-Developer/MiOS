# AI-hint: WS-9 out-of-process policy-arbiter DECISION core. Pure-stdlib verdict logic the mios-policy-arbiter service uses to answer the agent-pipe's...
# AI-doc: usr/share/doc/mios/manual/access.md

from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence


class Verdict:
    __slots__ = ("allow", "reason", "rule")

    def __init__(self, allow: bool, reason: str = "", rule: str = "ok") -> None:
        self.allow = bool(allow)
        self.reason = str(reason)
        self.rule = str(rule)

    def to_dict(self) -> dict[str, Any]:
        return {"allow": self.allow, "reason": self.reason, "rule": self.rule}

def _rank(tier: str, tiers: Sequence[str]) -> int:
    t = [str(x).strip().lower() for x in tiers]
    p = str(tier or "").strip().lower()
    try:
        return t.index(p)
    except ValueError:
        return len(t)   # unknown tier -> most restrictive (fail-closed)

def decide(verb: str, tier: str, *,
           deny: Iterable[str] = (), allow: Optional[Iterable[str]] = None,
           block_tier: str = "", tiers: Sequence[str] = ("read", "write", "interactive")) -> Verdict:
    """Return an allow/deny Verdict for one (verb, tier). See module docstring
    for the rule order. `allow=None` means no allow-list (rule 2/3 skipped);
    `allow=[]` (empty list) is an exclusive allow-list that denies everything."""
    v = str(verb or "")
    deny_s = {str(x).strip() for x in (deny or []) if str(x).strip()}
    if v in deny_s:
        return Verdict(False, f"'{v}' is on the arbiter deny-list", "deny_list")
    if allow is not None:
        allow_s = {str(x).strip() for x in allow if str(x).strip()}
        if v in allow_s:
            return Verdict(True, "", "allow_list")
        return Verdict(False, f"'{v}' is not on the arbiter allow-list", "allow_list")
    bt = str(block_tier or "").strip().lower()
    if bt:
        if _rank(tier, tiers) >= _rank(bt, tiers):
            return Verdict(False,
                           f"'{v}' ({tier}-tier) is at/above the arbiter block tier '{bt}'",
                           "block_tier")
    return Verdict(True, "", "ok")
