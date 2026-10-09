#!/usr/bin/env python3
# AI-hint: Automated unit test suite for native eBPF XDP network fastpath and WireGuard router (task_746, task_747).
# AI-doc: usr/share/doc/mios/manual/ch28-dynamic-network-and-firewall-management.md
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_EBPF_SRC = os.path.join(_ROOT, "usr", "lib", "mios", "ebpf", "xdp_router.c")
_XDP_BIN = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-xdp")


class TestXDPFastpath(unittest.TestCase):
    """Validates eBPF XDP router source and user-space management utility."""

    def test_ebpf_source_structure(self):
        self.assertTrue(os.path.isfile(_EBPF_SRC), f"Missing {_EBPF_SRC}")
        with open(_EBPF_SRC, "r", encoding="utf-8") as f:
            code = f.read()

        self.assertIn('#include <linux/bpf.h>', code)
        self.assertIn('#include <linux/udp.h>', code)
        self.assertIn('SEC("xdp")', code)
        self.assertIn('WIREGUARD_PORT', code)
        self.assertIn('XDP_PASS', code)
        self.assertIn('XDP_DROP', code)
        self.assertIn('BPF_MAP_TYPE_PERCPU_ARRAY', code)

    def test_xdp_cli_check(self):
        self.assertTrue(os.path.isfile(_XDP_BIN), f"Missing {_XDP_BIN}")
        self.assertTrue(os.access(_XDP_BIN, os.X_OK), f"Not executable {_XDP_BIN}")

        res = subprocess.run(
            [sys.executable, _XDP_BIN, "--check", "--source", _EBPF_SRC, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
        val = data.get("validation", {})
        self.assertEqual(val.get("status"), "valid")
        self.assertTrue(val.get("has_xdp_sec"))
        self.assertTrue(val.get("has_wireguard_filter"))
        self.assertTrue(val.get("has_bpf_maps"))
        self.assertEqual(val.get("target_pps"), 10_000_000)

    def test_xdp_attach_dry_run(self):
        res = subprocess.run(
            [sys.executable, _XDP_BIN, "--attach", "eth0", "--dry-run", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
        action = data.get("action", {})
        self.assertEqual(action.get("action"), "attach")
        self.assertEqual(action.get("interface"), "eth0")
        self.assertEqual(action.get("status"), "simulated")


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestXDPFastpath)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
