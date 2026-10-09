#!/usr/bin/env python3
# AI-hint: Automated unit test suite for 3-Peer Council Swarm & Byzantine Consensus (task_599, task_600).
# AI-related: usr/lib/mios/agent-pipe/council.py, tests/test-council-consensus.py
"""Automated unit test suite for MiOS Council Consensus Engine."""

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "lib", "mios", "agent-pipe"))

from council import AgentCouncilEngine, CouncilVote

class TestCouncilConsensus(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.council = AgentCouncilEngine(dry_run=True)

    async def test_valid_patch_achieves_unanimous_consensus(self):
        """Test legitimate configuration patch achieves full 3/3 consensus."""
        res = await self.council.deliberate_proposal(
            "patch_valid", "sysctl_edit", "/etc/sysctl.d/99-network.conf", "net.core.rmem_max=16777216"
        )
        self.assertTrue(res.consensus_reached)
        self.assertEqual(res.consensus_score, 1.0)
        self.assertEqual(len(res.dissent_reasons), 0)

    async def test_malicious_proposal_rejected(self):
        """Test destructive command injection is rejected by council with detailed dissent."""
        res = await self.council.deliberate_proposal(
            "patch_malicious", "exec_cmd", "/tmp/bad.sh", "rm -rf / --no-preserve-root"
        )
        self.assertFalse(res.consensus_reached)
        self.assertLess(res.consensus_score, 0.66)
        self.assertGreater(len(res.dissent_reasons), 0)

    async def test_split_vote_boundary_conditions(self):
        """Test exact 2/3 threshold boundary condition."""
        votes = [
            CouncilVote("a1", "coder", True, 0.9, "Looks good"),
            CouncilVote("a2", "security_auditor", True, 0.8, "No CVEs"),
            CouncilVote("a3", "architect", False, 0.7, "Violates USR-OVER-ETC"),
        ]
        res = await self.council.deliberate_proposal(
            "patch_split", "edit", "/etc/foo", "data", mock_votes=votes
        )
        self.assertTrue(res.consensus_reached)
        self.assertAlmostEqual(res.consensus_score, 2.0 / 3.0, places=2)
        self.assertEqual(len(res.dissent_reasons), 1)

if __name__ == "__main__":
    unittest.main()
