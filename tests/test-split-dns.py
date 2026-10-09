#!/usr/bin/env python3
# AI-hint: Automated unit test suite for Split-DNS & Strict DoT Resolution (task_641, task_642).
# AI-related: usr/libexec/mios/net/split_dns.py, tests/test-split-dns.py
"""Automated unit test suite for MiOS Split-DNS Configurator."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "net"))

from split_dns import SplitDNSConfigurator

class TestSplitDNS(unittest.TestCase):
    def setUp(self):
        self.dns = SplitDNSConfigurator(dry_run=True)

    def test_internal_mios_domain_routes_locally(self):
        """Test .mios domain queries route strictly to local WireGuard mesh resolver."""
        res = self.dns.resolve_domain_query("cluster-coordinator.node.mios")
        self.assertEqual(res.protocol, "WireGuard_Local_DNS")
        self.assertIn("wg0", res.resolved_server)
        self.assertTrue(res.is_internal_leak_prevented)

    def test_public_domain_routes_over_strict_dot(self):
        """Test public internet queries route strictly over TLS port 853 with DNSSEC."""
        res = self.dns.resolve_domain_query("github.com")
        self.assertEqual(res.protocol, "Strict_DoT_TLS853")
        self.assertIn("quad9", res.resolved_server)
        self.assertTrue(res.dnssec_validated)

if __name__ == "__main__":
    unittest.main()
