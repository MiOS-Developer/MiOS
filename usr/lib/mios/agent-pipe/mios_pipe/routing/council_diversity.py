# AI-hint: Council input-diversity gate + confidence-aware aggregation bypass (task_47 RouteMoA GAP-1 / task_48 MOSAIC GAP-2).
# AI-doc: usr/share/doc/mios/manual/routing.md

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from mios_toolsearch import _cosine

log = logging.getLogger("mios-agent-pipe")

_STATS = {"aggregator_total": 0, "aggregator_bypassed": 0}

def note_aggregator(bypassed: bool) -> None:
    """Record one aggregation opportunity and whether it was bypassed."""
    _STATS["aggregator_total"] += 1
    if bypassed:
        _STATS["aggregator_bypassed"] += 1

def bypassed_pct() -> float:
    """Percentage of aggregation opportunities that skipped the aggregator LLM."""
    tot = _STATS["aggregator_total"]
    if tot <= 0:
        return 0.0
    return round(100.0 * _STATS["aggregator_bypassed"] / tot, 2)

def reset_stats() -> None:
    _STATS["aggregator_total"] = 0
    _STATS["aggregator_bypassed"] = 0

def _sim_matrix(vectors: list, cosine: Callable = _cosine) -> list:
    """Full symmetric pairwise cosine matrix (diagonal = 1.0). O(k^2) cosine over
    the CACHED vectors -- NOT O(k^2) model calls (the vectors are embedded once)."""
    n = len(vectors)
    m = [[1.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            s = float(cosine(vectors[i], vectors[j]))
            m[i][j] = s
            m[j][i] = s
    return m

def select_diverse(vectors: list, threshold: float,
                   cosine: Callable = _cosine) -> list:
    n = len(vectors)
    if n <= 1:
        return list(range(n))
    S = _sim_matrix(vectors, cosine)
    mean_sim = [sum(S[i][j] for j in range(n) if j != i) / (n - 1)
                for i in range(n)]
    seed = min(range(n), key=lambda i: mean_sim[i])
    selected = [seed]
    remaining = [i for i in range(n) if i != seed]
    while remaining:
        cand = min(remaining, key=lambda i: max(S[i][q] for q in selected))
        if max(S[cand][q] for q in selected) > threshold:
            break  # most-orthogonal candidate is still redundant -> so is the rest
        selected.append(cand)
        remaining.remove(cand)
    return selected

def should_bypass(vectors: list, threshold: float,
                  cosine: Callable = _cosine) -> tuple:
    """task_48 MOSAIC bypass predicate. Returns ``(bypass, mean_similarity)`` where
    ``bypass`` is True iff there are >=2 council responses and EVERY pairwise
    cosine similarity exceeds ``threshold`` (the council converged). Fewer than 2
    responses cannot converge -> ``(False, 0.0)``. ``mean_similarity`` is the mean
    over the unique pairs."""
    n = len(vectors)
    if n < 2:
        return (False, 0.0)
    sims = []
    all_exceed = True
    for i in range(n):
        for j in range(i + 1, n):
            s = float(cosine(vectors[i], vectors[j]))
            sims.append(s)
            if not (s > threshold):
                all_exceed = False
    mean_s = sum(sims) / len(sims) if sims else 0.0
    return (all_exceed, mean_s)

def medoid_index(vectors: list, cosine: Callable = _cosine) -> int:
    n = len(vectors)
    if n <= 1:
        return 0
    S = _sim_matrix(vectors, cosine)
    return max(range(n),
               key=lambda i: sum(S[i][j] for j in range(n) if j != i) / (n - 1))

async def apply_council_gates(
    nodes: list, *, embed_one: Optional[Callable],
    cosine: Callable = _cosine,
    diversity_gate: bool = False, diversity_threshold: float = 0.92,
    aggregator_bypass: bool = False, aggregator_bypass_threshold: float = 0.95,
    output_key: str = "output",
    log_event: Optional[Callable] = None,
) -> tuple:
    if not (diversity_gate or aggregator_bypass):
        return (nodes, None)
    if not nodes or len(nodes) < 2 or embed_one is None:
        return (nodes, None)
    texts = [str((n or {}).get(output_key) or "") for n in nodes]
    try:
        vecs = list(await asyncio.gather(*[embed_one(t) for t in texts]))
    except Exception as e:  # noqa: BLE001 -- degrade-open, never break synthesis
        log.debug("council gate: embed failed -> gates skipped: %s", e)
        return (nodes, None)
    if any(not v for v in vecs):
        return (nodes, None)

    if aggregator_bypass:
        bypass_ok, mean_s = should_bypass(vecs, aggregator_bypass_threshold, cosine)
        if bypass_ok:
            mi = medoid_index(vecs, cosine)
            if log_event is not None:
                try:
                    log_event(kind="aggregator_bypass",
                              council_size=len(nodes), mean_similarity=mean_s)
                except Exception:  # noqa: BLE001 -- telemetry never breaks synthesis
                    pass
            return (nodes, {"node": nodes[mi], "mean_similarity": mean_s,
                            "council_size": len(nodes)})

    if diversity_gate:
        keep = set(select_diverse(vecs, diversity_threshold, cosine))
        if keep and len(keep) < len(nodes):
            return ([n for i, n in enumerate(nodes) if i in keep], None)

    return (nodes, None)
