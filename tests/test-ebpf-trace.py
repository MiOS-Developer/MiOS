#!/usr/bin/env python3
# AI-hint: Automated unit test suite for eBPF Probe Attachment & Tracing Overhead (task_663, task_664).
# AI-related: usr/lib/mios/agent-pipe/mios_trace.py, tests/test-ebpf-trace.py
"""Automated unit test suite for MiOS eBPF Tracer Manager."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "diag"))

from ebpf_trace import MAX_CPU_OVERHEAD_PCT, MAX_PROBE_ATTACH_MS, EBPFTracerManager

class TestEBPFTrace(unittest.TestCase):
    def setUp(self):
        self.tracer = EBPFTracerManager(dry_run=True)

    def test_probe_attach_latency_under_10ms(self):
        """Test eBPF probe compilation and load completes in <10ms."""
        res = self.tracer.attach_probe("tcpretrans")
        self.assertTrue(res.is_attached)
        self.assertLess(res.attach_latency_ms, MAX_PROBE_ATTACH_MS)
        self.assertLess(res.cpu_overhead_pct, MAX_CPU_OVERHEAD_PCT)

if __name__ == "__main__":
    unittest.main()
