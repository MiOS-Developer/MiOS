#!/usr/bin/env python3
# AI-hint: Unit test suite for MiOS mDNS peer discovery and WireGuard mesh daemon (task_532 / task_2765).
# AI-related: usr/libexec/mios/net/mdns_mesh.py
"""Unit and integration tests for MDNSMeshManager and WireGuard synthesizer."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_TARGET_PATH = os.path.join(_ROOT, "usr", "libexec", "mios", "net", "mdns_mesh.py")

spec = importlib.util.spec_from_file_location("mdns_mesh", _TARGET_PATH)
if spec and spec.loader:
    mdns_mesh = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mdns_mesh
    spec.loader.exec_module(mdns_mesh)
else:
    raise ImportError(f"Could not load module from {_TARGET_PATH}")

class TestMDNSMeshManager(unittest.TestCase):
    """Test suite for mDNS discovery, announcement records, and WireGuard configuration generation."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="mios-test-mdnsmesh-")
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_local_announcement_format(self):
        mgr = mdns_mesh.MDNSMeshManager(node_id="test-blade-01", mock=True)
        rec = mgr.get_local_announcement()
        self.assertEqual(rec["node_id"], "test-blade-01")
        self.assertEqual(rec["service"], "_mios-mesh._udp.local.")
        self.assertEqual(rec["port"], 51820)
        self.assertIn("mock_wg_pubkey", rec["wg_pubkey"])

    def test_discover_peers_mock(self):
        mgr = mdns_mesh.MDNSMeshManager(mock=True)
        peers = mgr.discover_peers()
        self.assertEqual(len(peers), 2)
        node_ids = [p.node_id for p in peers]
        self.assertIn("mios-node-02", node_ids)
        self.assertIn("mios-node-03", node_ids)

    def test_render_wireguard_conf(self):
        mgr = mdns_mesh.MDNSMeshManager(mock=True)
        mgr.discover_peers()

        out_file = self.root / "wg0.conf"
        conf = mgr.render_wireguard_conf(output_path=str(out_file))

        self.assertIn("[Interface]", conf)
        self.assertIn("ListenPort = 51820", conf)
        self.assertIn("[Peer]", conf)
        self.assertIn("AllowedIPs = 10.42.0.2/32", conf)
        self.assertIn("AllowedIPs = 10.42.0.3/32", conf)
        self.assertTrue(out_file.exists())

    def test_apply_peer_live_mock(self):
        mgr = mdns_mesh.MDNSMeshManager(mock=True)
        peers = mgr.discover_peers()
        peer = peers[0]

        ok, msg = mgr.apply_peer_live(peer)
        self.assertTrue(ok)
        self.assertIn("Mock: Executed wg set", msg)

    def test_cli_execution_announce_mock(self):
        test_args = ["mdns_mesh.py", "--announce", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = mdns_mesh.main()
            self.assertEqual(exit_code, 0)

    def test_cli_execution_render_config_mock(self):
        out_path = str(self.root / "cli_wg0.conf")
        test_args = ["mdns_mesh.py", "--render-config", "--output", out_path, "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = mdns_mesh.main()
            self.assertEqual(exit_code, 0)
            self.assertTrue(os.path.exists(out_path))

def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestMDNSMeshManager)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1

if __name__ == "__main__":
    sys.exit(main())
