#!/usr/bin/env python3
# AI-hint: Clock offset jitter and monotonic timestamp ordering validation test suite.
# AI-related: usr/libexec/mios/net/ptp_time_sync.py, usr/share/mios/mios.toml
"""Unit test suite for PTP IEEE 1588 time sync, Chrony smooth slewing, and clock monotonicity (task_510)."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_TARGET_PATH = os.path.join(_ROOT, "usr", "libexec", "mios", "net", "ptp_time_sync.py")

spec = importlib.util.spec_from_file_location("ptp_time_sync", _TARGET_PATH)
if spec and spec.loader:
    ptp_time_sync = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = ptp_time_sync
    spec.loader.exec_module(ptp_time_sync)
else:
    raise ImportError(f"Could not load module from {_TARGET_PATH}")

class TestPTPTimeSync(unittest.TestCase):
    """Test suite for PTP time sync capabilities, smooth slewing config generation, and monotonic clock guarantees."""

    def setUp(self) -> None:
        self.daemon = ptp_time_sync.PTPTimeSyncDaemon(mock=True)

    def test_ethtool_probing_hw_and_sw(self) -> None:
        probe = ptp_time_sync.PTPCapabilityProbe(mock=True)
        # HW PTP interface
        eth0 = probe.probe_interface("enp1s0f0")
        self.assertTrue(eth0.supports_hardware_ptp())
        self.assertTrue(eth0.hw_tx_timestamping)
        self.assertTrue(eth0.hw_rx_timestamping)
        self.assertEqual(eth0.phc_index, 0)

        # SW only interface (e.g. WiFi)
        wlan = probe.probe_interface("wlan0")
        self.assertFalse(wlan.supports_hardware_ptp())
        self.assertFalse(wlan.hw_tx_timestamping)
        self.assertTrue(wlan.sw_tx_timestamping)
        self.assertIsNone(wlan.phc_index)

    def test_parse_raw_ethtool_output(self) -> None:
        raw_ethtool_output = """Time stamping parameters for enp3s0:
Capabilities:
\tSOF_TIMESTAMPING_TX_HARDWARE
\tSOF_TIMESTAMPING_TX_SOFTWARE
\tSOF_TIMESTAMPING_RX_HARDWARE
\tSOF_TIMESTAMPING_RX_SOFTWARE
\tSOF_TIMESTAMPING_RAW_HARDWARE
PTP Hardware Clock: 2
Hardware Transmit Timestamp Modes:
\tHWTSTAMP_TX_OFF
\tHWTSTAMP_TX_ON
Hardware Receive Filter Modes:
\tHWTSTAMP_FILTER_NONE
\tHWTSTAMP_FILTER_ALL
"""
        probe = ptp_time_sync.PTPCapabilityProbe(mock=False)
        parsed = probe.parse_ethtool_output("enp3s0", raw_ethtool_output)
        self.assertTrue(parsed.supports_hardware_ptp())
        self.assertEqual(parsed.phc_index, 2)
        self.assertTrue(parsed.hw_tx_timestamping)
        self.assertTrue(parsed.hw_rx_timestamping)

    def test_ptp4l_config_generation(self) -> None:
        gen = ptp_time_sync.PTPConfigGenerator()
        conf = gen.generate_ptp4l_conf(interface="eth0", domain=0, time_stamping="hardware")
        self.assertIn("[global]", conf)
        self.assertIn("time_stamping               hardware", conf)
        self.assertIn("slaveOnly                   1", conf)
        self.assertIn("[eth0]", conf)

    def test_chrony_dropin_smooth_slewing(self) -> None:
        gen = ptp_time_sync.PTPConfigGenerator()
        conf = gen.generate_chrony_conf(phc_device="/dev/ptp0")
        # Invariant checks:
        # 1. makestep 0 0 ensures no backwards clock steps after boot (protects PostgreSQL and Raft transactions)
        self.assertIn("makestep 0 0", conf)
        # 2. PHC refclock
        self.assertIn("refclock PHC /dev/ptp0", conf)
        # 3. NTS support
        self.assertIn("nts", conf)

    def test_clock_jitter_and_monotonicity_multithread(self) -> None:
        monitor = ptp_time_sync.PTPStatusMonitor(mock=True)

        # Single thread test
        monotonic, jitter_ns, count = monitor.sample_monotonic_timestamps(iterations=2000)
        self.assertTrue(monotonic)
        self.assertEqual(count, 2000)
        self.assertGreaterEqual(jitter_ns, 0.0)

        # Multi-thread concurrent monotonicity test
        errors: list[str] = []

        def worker_sample() -> None:
            mono, _, _ = monitor.sample_monotonic_timestamps(iterations=500)
            if not mono:
                errors.append("Monotonicity violation detected in worker thread")

        threads = [threading.Thread(target=worker_sample) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)

    def test_telemetry_status(self) -> None:
        status = self.daemon.monitor.get_status()
        self.assertTrue(status.ptp_locked)
        self.assertTrue(status.chrony_locked)
        # Offset must be under 1ms (1,000,000 ns) SLA
        self.assertLess(status.offset_ns, 1_000_000.0)
        self.assertTrue(status.nts_authenticated)

    def test_cli_execution(self) -> None:
        # CLI --status --json
        stdout_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf):
            ret = ptp_time_sync.main(["--status", "--json", "--mock"])
            self.assertEqual(ret, 0)
            data = json.loads(stdout_buf.getvalue())
            self.assertTrue(data["ptp_locked"])
            self.assertIn("offset_ns", data)

        # CLI --probe-interfaces --json
        stdout_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf):
            ret = ptp_time_sync.main(["--probe-interfaces", "--json", "--mock"])
            self.assertEqual(ret, 0)
            data = json.loads(stdout_buf.getvalue())
            self.assertIsInstance(data, list)
            self.assertGreaterEqual(len(data), 1)

        # CLI --generate-ptp4l-conf --json
        stdout_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf):
            ret = ptp_time_sync.main(["--generate-ptp4l-conf", "--interface", "eth0", "--json", "--mock"])
            self.assertEqual(ret, 0)
            data = json.loads(stdout_buf.getvalue())
            self.assertIn("ptp4l_conf", data)

        # CLI --check-jitter --json
        stdout_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf):
            ret = ptp_time_sync.main(["--check-jitter", "--json", "--mock"])
            self.assertEqual(ret, 0)
            data = json.loads(stdout_buf.getvalue())
            self.assertTrue(data["strictly_monotonic"])
            self.assertEqual(data["jitter_status"], "PASS")

if __name__ == "__main__":
    unittest.main()
