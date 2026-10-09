# AI-hint: OAI-04/task_205 run-template REPLAY matcher -- the reuse half of the WS-6 capture path.
# AI-doc: usr/share/doc/mios/manual/routing.md
"""Intent-keyed run-template matching (task_205)."""

from __future__ import annotations

import hashlib
import re
from typing import Iterable, Optional, Sequence, Tuple

_WORD_RE = re.compile(r"[A-Za-z0-9_]+")

# Function words carry no intent and vary freely with phrasing. Kept small and
# explicit: an aggressive list would collapse genuinely different requests onto
# one key, which is the failure this feature must not have.
_STOPWORDS = frozenset("""
a an the and or but if then than of to in on at by for with from into over
is are was were be been being do does did done can could will would shall
should may might must i me my we our you your it its this that these those
please just now also there here what which who whom how why when where
""".split())

def normalize_tokens(text: str) -> tuple:
    """Sorted unique significant tokens; order-insensitive by construction."""
    words = _WORD_RE.findall(str(text or "").lower())
    return tuple(sorted({w for w in words if w and w not in _STOPWORDS}))

def intent_key(text: str) -> str:
    """Stable short hash of a turn's normalized tokens. Empty for an empty or
    entirely-stopword turn, which must never match anything."""
    toks = normalize_tokens(text)
    if not toks:
        return ""
    return hashlib.sha256(" ".join(toks).encode("utf-8", "replace")).hexdigest()[:16]

def similarity(a: Iterable[str], b: Iterable[str]) -> float:
    """Jaccard overlap of two token sets, 0.0..1.0. Two empty sets score 0.0,
    not 1.0 -- 'nothing matches nothing' must not read as perfect confidence."""
    sa, sb = set(a or ()), set(b or ())
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    if not inter:
        return 0.0
    return inter / float(len(sa | sb))

def match_template(text: str, templates: Sequence[dict], threshold: float = 0.8,
                   ) -> Tuple[Optional[dict], float, str]:
    """Best stored template for `text`, or (None, best_score, reason).

    Exact key wins; otherwise Jaccard must reach `threshold`. Manual ch61."""
    key = intent_key(text)
    if not key:
        return None, 0.0, "empty intent"
    if not templates:
        return None, 0.0, "no templates"
    toks = normalize_tokens(text)

    best, best_score = None, 0.0
    for t in templates:
        if not isinstance(t, dict) or not (t.get("dag") or {}).get("nodes"):
            continue
        t_key = str(t.get("intent_key") or "")
        if not t_key and t.get("intent"):
            t_key = intent_key(t.get("intent"))
        if t_key and t_key == key:
            return t, 1.0, "exact intent key"
        score = similarity(toks, normalize_tokens(t.get("intent") or ""))
        if score > best_score:
            best, best_score = t, score

    try:
        thr = float(threshold)
    except (TypeError, ValueError):
        thr = 0.8
    if best is not None and best_score >= thr:
        return best, best_score, f"overlap {best_score:.2f} >= {thr:.2f}"
    return None, best_score, f"below threshold ({best_score:.2f} < {thr:.2f})"
