# AI-hint: MiOS system and orchestration module providing mios reputation capabilities.
# AI-functions: _default_quality, __init__, evaluate_session, get_reputation, sorted_peers, _persist, PeerContribution, ReputationRecord, ReputationEngine

"""
mios_reputation.py — task_288 MAO-07
IntrospecLOO (Introspective Leave-One-Out) marginal contribution evaluation
for swarm/council agent sessions.

After a multi-agent council completes, each peer's marginal utility is
computed by evaluating the decision quality with and without that agent's
contributions.  The delta updates the `peer_reputation` PostgreSQL table.

Dry-run mode (no DB): reputation updates are accumulated in-memory.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)

@dataclass
class PeerContribution:
    """One peer's moves in a council session."""
    peer_id:  str
    moves:    list[dict[str, Any]] = field(default_factory=list)

@dataclass
class ReputationRecord:
    peer_id:     str
    score:       float = 0.5       # 0.0 (useless) .. 1.0 (invaluable)
    delta:       float = 0.0       # last LOO delta
    eval_count:  int   = 0
    updated_at:  float = field(default_factory=time.monotonic)

# Callable: (session result without peer) -> float quality score
QualityFn = Callable[[list[PeerContribution], str | None], float]

def _default_quality(contributions: list[PeerContribution],
                     exclude_peer: str | None) -> float:
    """
    Placeholder quality function for unit tests.
    Returns the count of non-excluded contributions / total as quality proxy.
    """
    included = [c for c in contributions
                if exclude_peer is None or c.peer_id != exclude_peer]
    total_moves = sum(len(c.moves) for c in contributions)
    if total_moves == 0:
        return 0.0
    included_moves = sum(len(c.moves) for c in included)
    return included_moves / total_moves

class ReputationEngine:
    """
    Compute IntrospecLOO marginal contribution scores and update peer reputation.
    """

    def __init__(self, quality_fn: QualityFn | None = None,
                 db_dsn: str = "",
                 dry_run: bool = True) -> None:
        self.quality_fn = quality_fn or _default_quality
        self.db_dsn     = db_dsn
        self.dry_run    = dry_run
        self._store:  dict[str, ReputationRecord] = {}

    # ------------------------------------------------------------------
    def evaluate_session(self,
                         session_id: str,
                         contributions: list[PeerContribution]) -> list[ReputationRecord]:
        """
        Run IntrospecLOO: for each peer compute quality_with_all minus
        quality_without_peer.  Update reputation store.
        """
        q_all = self.quality_fn(contributions, None)
        records: list[ReputationRecord] = []

        for peer in contributions:
            q_without = self.quality_fn(contributions, peer.peer_id)
            delta      = q_all - q_without        # positive = peer added value
            rec        = self._store.setdefault(
                peer.peer_id,
                ReputationRecord(peer_id=peer.peer_id))

            # Exponential moving average
            alpha    = 0.3
            rec.score = alpha * max(0.0, min(1.0, 0.5 + delta)) + (1 - alpha) * rec.score
            rec.delta = delta
            rec.eval_count += 1
            rec.updated_at = time.monotonic()
            records.append(rec)
            log.info("LOO: peer=%s delta=%.4f new_score=%.4f",
                     peer.peer_id, delta, rec.score)

        if not self.dry_run:
            self._persist(session_id, records)

        return records

    # ------------------------------------------------------------------
    def get_reputation(self, peer_id: str) -> ReputationRecord | None:
        return self._store.get(peer_id)

    def sorted_peers(self) -> list[ReputationRecord]:
        return sorted(self._store.values(), key=lambda r: -r.score)

    # ------------------------------------------------------------------
    def _persist(self, session_id: str,
                 records: list[ReputationRecord]) -> None:
        """Write reputation deltas to peer_reputation table."""
        try:
            import psycopg
            with psycopg.connect(self.db_dsn) as conn:
                for rec in records:
                    conn.execute(
                        """INSERT INTO peer_reputation
                           (peer_id, session_id, delta, score, eval_count)
                           VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT (peer_id) DO UPDATE
                           SET delta=EXCLUDED.delta,
                               score=EXCLUDED.score,
                               eval_count=peer_reputation.eval_count+1,
                               updated_at=now()""",
                        (rec.peer_id, session_id,
                         rec.delta, rec.score, rec.eval_count))
                conn.commit()
        except ImportError:
            log.debug("ReputationEngine: psycopg not installed, skip persist")
        except Exception as exc:
            log.warning("failed to persist reputation for session %s (%d record(s)): %s",
                        session_id, len(records), exc)


# PeerReputation is a DIFFERENT contract from ReputationEngine, not an alias for
# it: the A2A daemons call .score()/.restore()/.rows() on the object bound here,
# and ReputationEngine (session evaluation, task_288) has none of those. Aliasing the
# two silently removed that API from the federation path. Re-export the real
# implementation, which is what this module forwarded to before.
from mios_pipe.identity.reputation import (  # noqa: E402,F401 -- re-export
    NEUTRAL,
    PeerReputation,
)
