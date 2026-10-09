#!/usr/bin/env python3
# AI-hint: Automated unit test suite for eBPF circular flight recorder and crash parser (task_460).
# AI-doc: usr/share/doc/mios/manual/ch45-diagnostic-tools-and-profilers.md
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_BPF_SOURCE = os.path.join(_ROOT, "usr", "src", "bpf", "mios_flightrec.bpf.c")
_PARSE_BIN = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-crash-parse")


class TestEbpfFlightrec(unittest.TestCase):
    """Validates in-kernel eBPF flight recorder definitions and crash parser functionality."""

    def test_bpf_source_exists_and_structures(self):
        self.assertTrue(os.path.isfile(_BPF_SOURCE), f"Missing {_BPF_SOURCE}")
        content = pathlib.Path(_BPF_SOURCE).read_text(encoding="utf-8")
        self.assertIn("BPF_MAP_TYPE_ARRAY", content)
        self.assertIn("BPF_MAP_TYPE_RINGBUF", content)
        self.assertIn("struct flight_record_t", content)
        self.assertIn("EVENT_TYPE_PANIC", content)
        self.assertIn("trace_panic", content)

    def test_crash_parser_executable(self):
        self.assertTrue(os.path.isfile(_PARSE_BIN), f"Missing {_PARSE_BIN}")
        self.assertTrue(os.access(_PARSE_BIN, os.X_OK), f"{_PARSE_BIN} not executable")

    def test_crash_parser_markdown_report(self):
        res = subprocess.run([_PARSE_BIN], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Error: {res.stderr}")
        self.assertIn("# MiOS Kernel Crash Diagnostic Report", res.stdout)
        self.assertIn("Diagnostic Summary", res.stdout)
        self.assertIn("Pre-Panic Timeline (Last 60 Seconds)", res.stdout)
        self.assertIn("Subsystem Event Distribution", res.stdout)

    def test_crash_parser_json_output(self):
        res = subprocess.run([_PARSE_BIN, "--json"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Error: {res.stderr}")
        data = json.loads(res.stdout)
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)
        first = data[0]
        self.assertIn("timestamp_ns", first)
        self.assertIn("pid", first)
        self.assertIn("comm", first)
        self.assertIn("event_type", first)

    def test_crash_parser_custom_input_file(self):
        custom_events = [
            {
                "timestamp_ns": 1000000000,
                "pid": 4242,
                "tid": 4242,
                "event_type": "SYSCALL",
                "latency_us": 12,
                "comm": "custom-agent",
                "detail": "test_agent_entry",
            },
            {
                "timestamp_ns": 1000050000,
                "pid": 4242,
                "tid": 4242,
                "event_type": "PANIC",
                "latency_us": 0,
                "comm": "custom-agent",
                "detail": "fatal_kernel_trap",
            },
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(custom_events, tf)
            tf_path = tf.name

        try:
            res = subprocess.run([_PARSE_BIN, tf_path], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"Error: {res.stderr}")
            self.assertIn("custom-agent", res.stdout)
            self.assertIn("fatal_kernel_trap", res.stdout)
            self.assertIn("4242", res.stdout)
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestEbpfFlightrec)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
