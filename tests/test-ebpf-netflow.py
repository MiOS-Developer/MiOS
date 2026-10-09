#!/usr/bin/env python3
# AI-hint: Automated unit test suite for in-kernel eBPF network flow probe and collector daemon (task_455).
# AI-doc: usr/share/doc/mios/manual/ch17-defense-in-depth-hardening.md
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_BPF_SRC = os.path.join(_ROOT, "usr", "src", "bpf", "mios_netflow.bpf.c")
_NETFLOWD_BIN = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-netflowd")


class TestEbpfNetflow(unittest.TestCase):
    """Validates eBPF flow probe and collector daemon."""

    def test_files_exist_and_executable(self):
        self.assertTrue(os.path.isfile(_BPF_SRC), f"Missing {_BPF_SRC}")
        self.assertTrue(os.path.isfile(_NETFLOWD_BIN), f"Missing {_NETFLOWD_BIN}")
        self.assertTrue(os.access(_NETFLOWD_BIN, os.X_OK), f"Not executable: {_NETFLOWD_BIN}")

    def test_bpf_c_source_structure(self):
        with open(_BPF_SRC, "r", encoding="utf-8") as f:
            code = f.read()

        self.assertIn("BPF_MAP_TYPE_PERCPU_HASH", code)
        self.assertIn("BPF_MAP_TYPE_RINGBUF", code)
        self.assertIn("struct flow_key_t", code)
        self.assertIn("struct flow_metrics_t", code)
        self.assertIn('SEC("tc")', code)
        self.assertIn("bpf_map_lookup_elem", code)

    def test_netflowd_flush_summary(self):
        res = subprocess.run(
            [_NETFLOWD_BIN, "--mock", "--flush-once", "--json", "--interval", "10.0"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"Netflowd failed: {res.stderr}")
        data = json.loads(res.stdout)

        self.assertEqual(data.get("window_sec"), 10.0)
        self.assertTrue(data.get("flow_count", 0) > 0)
        self.assertTrue(data.get("total_packets", 0) > 0)
        self.assertTrue(data.get("total_bytes", 0) > 0)
        self.assertIn("flows", data)
        self.assertIsInstance(data["flows"], list)


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestEbpfNetflow)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
