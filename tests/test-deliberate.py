# AI-hint: Tests for task_285: mios_deliberate — DCI structured deliberation.
# AI-related: mios_deliberate
# AI-functions: test_decision_packet_has_required_fields, test_packet_json_serializable, test_consensus_reached

"""Tests for task_285: mios_deliberate — DCI structured deliberation."""
import sys
sys.path.insert(0, "usr/lib/mios/agent-pipe")
from mios_deliberate import DCISession, DecisionPacket, Act, Archetype

def test_decision_packet_has_required_fields():
    """DCI run produces a DecisionPacket with all required fields."""
    session = DCISession(topic="Deploy security patch")
    packet = session.run()
    assert isinstance(packet, DecisionPacket)
    assert len(packet.chosen_actions) > 0, "No chosen actions"
    assert isinstance(packet.residual_objections, list)
    assert isinstance(packet.reopen_conditions, list)
    assert packet.round_count >= 1
    assert 0.0 <= packet.consensus_score <= 1.0

def test_packet_json_serializable():
    """DecisionPacket.to_json() produces valid JSON."""
    import json
    session = DCISession(topic="Scale inference cluster")
    packet = session.run()
    d = json.loads(packet.to_json())
    assert "chosen_actions" in d
    assert "consensus_score" in d

def test_consensus_reached():
    """Default responder drives Integrator to synthesize → consensus."""
    session = DCISession(topic="Rollout new model weights")
    packet = session.run()
    # consensus_score > 0 means at least one archetype conceded
    assert packet.consensus_score > 0.0

if __name__ == "__main__":
    test_decision_packet_has_required_fields()
    test_packet_json_serializable()
    test_consensus_reached()
    print("All task_285 tests passed.")
