#!/usr/bin/env python3
# AI-hint: Automated unit test suite for Attenuated Macaroon Tokens & Replay Prevention (task_667, task_668).
# AI-related: usr/lib/mios/agent-pipe/macaroon_auth.py, tests/test-macaroon-auth.py
"""Automated unit test suite for MiOS Macaroon Auth Manager."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "lib", "mios", "agent-pipe"))

from macaroon_auth import MacaroonAuthManager

class TestMacaroonAuth(unittest.TestCase):
    def setUp(self):
        self.mgr = MacaroonAuthManager(dry_run=True)

    def test_mint_and_verify_valid_token(self):
        """Test minting and executing authorized operation succeeds."""
        tok = self.mgr.mint_macaroon("repo_alpha", "pull", 60.0)
        self.assertTrue(self.mgr.verify_and_burn_macaroon(tok, "repo_alpha", "pull"))

    def test_replay_attack_prevention(self):
        """Test reusing burned nonce returns authorization failure."""
        tok = self.mgr.mint_macaroon("repo_beta", "fetch", 60.0)
        self.assertTrue(self.mgr.verify_and_burn_macaroon(tok, "repo_beta", "fetch"))
        # Second attempt must fail
        self.assertFalse(self.mgr.verify_and_burn_macaroon(tok, "repo_beta", "fetch"))

    def test_caveat_attenuation_mismatch_denial(self):
        """Test requesting un-permitted operation or repo is rejected."""
        tok = self.mgr.mint_macaroon("repo_gamma", "pull", 60.0)
        self.assertFalse(self.mgr.verify_and_burn_macaroon(tok, "repo_gamma", "push"))

if __name__ == "__main__":
    unittest.main()
