# AI-hint: Tests for task_288: mios_reputation — IntrospecLOO marginal contribution.
# AI-related: mios_reputation
# AI-functions: _make_contributions, test_records_returned_for_all_peers, test_low_contributor_gets_low_delta, test_eval_count_increments, test_sorted_peers_descending

"""Tests for task_288: mios_reputation — IntrospecLOO marginal contribution."""
import sys
sys.path.insert(0, "usr/lib/mios/agent-pipe")
from mios_reputation import ReputationEngine, PeerContribution

def _make_contributions():
    return [
        PeerContribution(peer_id="agent-a",
                         moves=[{"act": "propose"}, {"act": "evidence"}]),
        PeerContribution(peer_id="agent-b",
                         moves=[{"act": "challenge"}]),
        PeerContribution(peer_id="agent-c",
                         moves=[]),   # contributed nothing
    ]

def test_records_returned_for_all_peers():
    """evaluate_session() returns one ReputationRecord per peer."""
    engine = ReputationEngine(dry_run=True)
    records = engine.evaluate_session("sess-1", _make_contributions())
    peer_ids = {r.peer_id for r in records}
    assert peer_ids == {"agent-a", "agent-b", "agent-c"}

def test_low_contributor_gets_low_delta():
    """Peer with no moves gets a lower delta than peers with moves."""
    engine = ReputationEngine(dry_run=True)
    records = engine.evaluate_session("sess-2", _make_contributions())
    by_id = {r.peer_id: r for r in records}
    # agent-c contributed nothing — delta should be ≤ agent-a's delta
    assert by_id["agent-c"].delta <= by_id["agent-a"].delta, (
        "Empty contributor should have lower marginal value")

def test_eval_count_increments():
    """eval_count increments on each session evaluation for a peer."""
    engine = ReputationEngine(dry_run=True)
    engine.evaluate_session("sess-3", _make_contributions())
    engine.evaluate_session("sess-4", _make_contributions())
    rec = engine.get_reputation("agent-a")
    assert rec.eval_count == 2, f"Expected eval_count=2, got {rec.eval_count}"

def test_sorted_peers_descending():
    """sorted_peers() returns highest-score peers first."""
    engine = ReputationEngine(dry_run=True)
    engine.evaluate_session("sess-5", _make_contributions())
    peers = engine.sorted_peers()
    scores = [p.score for p in peers]
    assert scores == sorted(scores, reverse=True), "Peers not in descending score order"

if __name__ == "__main__":
    test_records_returned_for_all_peers()
    test_low_contributor_gets_low_delta()
    test_eval_count_increments()
    test_sorted_peers_descending()
    print("All task_288 tests passed.")
