# AI-hint: Pure self-improvement ACT-half decision core (task_64 ACT + task_66 proof-of-utility).
# AI-doc: usr/share/doc/mios/manual/lifecycle.md

from __future__ import annotations

from typing import Iterable, Optional, Sequence, Tuple

import mios_bench

def proposal_target_allowed(target_kind: str, *, improvable, protected) -> bool:
    kind = str(target_kind or "").strip()
    if not kind:
        return False
    prot = {str(p).strip() for p in (protected or ())}
    impr = {str(p).strip() for p in (improvable or ())}
    if kind in prot:
        return False
    return kind in impr

def validate_proposal(proposal: dict, *, improvable, protected) -> "Tuple[bool, str]":
    if not isinstance(proposal, dict):
        return False, "not_a_proposal"
    kind = str(proposal.get("target_kind") or "").strip()
    tid = str(proposal.get("target_id") or "").strip()
    if not kind:
        return False, "missing_target_kind"
    if not tid:
        return False, "missing_target_id"
    if not proposal_target_allowed(kind, improvable=improvable, protected=protected):
        return False, "target_protected_or_unimprovable"
    return True, "ok"

def solver_gap(weak_score: float, strong_score: float) -> float:
    """The discriminative signal: ``strong - weak``. A purely numeric verifier
    output (good per NO-HARDCODE -- not an English/keyword gate). Both args are
    pass-rates in [0,1]; the sign is preserved (a negative gap = the weak lane beat
    the strong one, which carries no curation signal)."""
    return float(strong_score) - float(weak_score)

def is_discriminative(weak_score: float, strong_score: float, *, gap_min: float) -> bool:
    """True iff a task SEPARATES a weak from a strong solver by at least ``gap_min``
    (Autodata's sweet spot). Tasks both lanes pass (trivial) or both fail
    (impossible) have a gap below the threshold and carry no eval/training signal."""
    return solver_gap(weak_score, strong_score) >= float(gap_min)

def curate_eval(candidates: "Iterable[dict]", *, gap_min: float) -> "list[dict]":
    kept: list[dict] = []
    for c in candidates or []:
        if not isinstance(c, dict):
            continue
        w, s = c.get("weak"), c.get("strong")
        if not isinstance(w, (int, float)) or not isinstance(s, (int, float)):
            continue
        if is_discriminative(w, s, gap_min=gap_min):
            kept.append(c)
    return kept

def pass_hat_k_score(tasks: "Sequence[Tuple[int, int]]", *, k: int) -> float:
    return mios_bench.aggregate_pass_hat_k(tasks, int(k))

def proof_of_utility(baseline_score: float, proposed_score: float, *,
                     margin: float = 0.0,
                     require_improvement: bool = False) -> "Tuple[bool, float]":
    delta = float(proposed_score) - float(baseline_score)
    accept = delta >= -abs(float(margin))
    if require_improvement:
        accept = accept and (delta > 0.0)
    return accept, delta

def decide_proposal(proposal: dict, *, baseline_score: float, proposed_score: float,
                    improvable, protected, margin: float = 0.0,
                    require_improvement: bool = False) -> dict:
    kind = str((proposal or {}).get("target_kind") or "").strip()
    tid = str((proposal or {}).get("target_id") or "").strip()
    ok, why = validate_proposal(proposal, improvable=improvable, protected=protected)
    if not ok:
        return {"accept": False, "reason": "isolation_rejected", "detail": why,
                "delta": None, "target_kind": kind, "target_id": tid}
    accept, delta = proof_of_utility(
        baseline_score, proposed_score,
        margin=margin, require_improvement=require_improvement)
    return {"accept": bool(accept),
            "reason": "accepted" if accept else "regression",
            "delta": delta, "target_kind": kind, "target_id": tid}
