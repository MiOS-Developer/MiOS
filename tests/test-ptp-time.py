#!/usr/bin/env python3
# AI-hint: Clock offset jitter and monotonic timestamp ordering validation test suite.
# AI-related: usr/libexec/mios/net/ptp_time_sync.py, usr/libexec/mios/mios-chrony-ptp-dropin, etc/chrony.conf, usr/share/mios/mios.toml
"""Unit test suite for PTP IEEE 1588 time sync, Chrony smooth slewing, and clock monotonicity (task_510)."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_TARGET_PATH = os.path.join(_ROOT, "usr", "libexec", "mios", "net", "ptp_time_sync.py")
_DROPIN = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-chrony-ptp-dropin")
_BAKED_CHRONY = os.path.join(_ROOT, "etc", "chrony.conf")
_REFCLOCK_TAIL = "poll 3 dpoll -2 offset 0 minsamples 4 prefer trust"

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

    def test_chrony_conf_without_a_reported_phc_has_no_refclock(self) -> None:
        # No interface reports a PHC (ethtool -T): the generator must not invent
        # /dev/ptp0, which on bare metal can be an undisciplined NIC clock.
        no_phc = ptp_time_sync.NetworkInterfacePTP(
            interface="eno2", hw_tx_timestamping=False, hw_rx_timestamping=False,
            hw_raw_timestamping=False, sw_tx_timestamping=True, sw_rx_timestamping=True,
            phc_index=None,
        )
        self.assertNotIn("refclock", ptp_time_sync.PTPConfigGenerator.generate_chrony_conf())
        stdout_buf = io.StringIO()
        with patch.object(ptp_time_sync.PTPCapabilityProbe, "list_candidate_interfaces", return_value=[no_phc]), \
                patch("sys.stdout", stdout_buf):
            self.assertEqual(ptp_time_sync.main(["--generate-chrony-conf", "--json"]), 0)
        data = json.loads(stdout_buf.getvalue())
        self.assertIsNone(data["phc_device"])
        self.assertNotIn("refclock PHC", data["chrony_conf"])

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

class TestChronyPtpDropin(unittest.TestCase):
    """mios-chrony-ptp-dropin trusts a PHC only when the kernel names it Hyper-V's clock."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="mios-chrony-ptp-")
        self.sysfs = os.path.join(self.tmp, "sys", "class", "ptp")
        self.dev = os.path.join(self.tmp, "dev")
        self.chrony_d = os.path.join(self.tmp, "chrony.d")
        self.dropin = os.path.join(self.chrony_d, "10-ptp.conf")
        os.makedirs(self.sysfs)
        os.makedirs(self.dev)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def clock(self, index: int, name: str, node: bool = True) -> str:
        os.makedirs(os.path.join(self.sysfs, f"ptp{index}"))
        with open(os.path.join(self.sysfs, f"ptp{index}", "clock_name"), "w", encoding="utf-8") as fh:
            fh.write(name + "\n")
        dev = os.path.join(self.dev, f"ptp{index}")
        if node:
            open(dev, "w", encoding="utf-8").close()
        return dev

    def run_dropin(self, sysfs: str | None = None) -> subprocess.CompletedProcess[str]:
        # A scrubbed env: the host's ambient variables must not steer the fixture.
        env = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "PTP_SYSFS_DIR": sysfs or self.sysfs,
            "PTP_DEV_DIR": self.dev,
            "CHRONY_D": self.chrony_d,
        }
        res = subprocess.run(["bash", _DROPIN], env=env, capture_output=True, text=True, check=False)
        self.assertEqual(res.returncode, 0, res.stderr)
        return res

    def refclocks(self) -> list[str]:
        with open(self.dropin, encoding="utf-8") as fh:
            return [line for line in fh.read().splitlines() if line.startswith("refclock")]

    def test_nic_phc_on_ptp0_yields_no_refclock(self) -> None:
        # The bare-metal host: ptp0 is the Aquantia NIC's free-running clock.
        self.clock(0, "atlantic ptp")
        self.run_dropin()
        self.assertFalse(os.path.exists(self.dropin))

    def test_hyperv_clock_on_ptp0_yields_its_refclock(self) -> None:
        dev = self.clock(0, "hyperv")
        self.run_dropin()
        self.assertEqual(self.refclocks(), [f"refclock PHC {dev} {_REFCLOCK_TAIL}"])

    def test_hyperv_clock_on_ptp1_behind_a_nic_on_ptp0(self) -> None:
        self.clock(0, "atlantic ptp")
        dev = self.clock(1, "hyperv")
        self.run_dropin()
        self.assertEqual(self.refclocks(), [f"refclock PHC {dev} {_REFCLOCK_TAIL}"])

    def test_wsl2_refclock_text_is_the_pre_fix_line_for_that_device(self) -> None:
        # Hyper-V hosts keep the exact refclock the image used to bake, device aside.
        legacy = "refclock PHC /dev/ptp0 poll 3 dpoll -2 offset 0 minsamples 4 prefer trust"
        dev = self.clock(0, "hyperv")
        self.run_dropin()
        self.assertEqual(self.refclocks(), [legacy.replace("/dev/ptp0", dev)])

    def test_hyperv_clock_without_a_device_node_yields_no_refclock(self) -> None:
        # chronyd fails fatally on an unopenable refclock; name only a present node.
        self.clock(0, "hyperv", node=False)
        self.run_dropin()
        self.assertFalse(os.path.exists(self.dropin))

    def test_no_ptp_class_yields_no_refclock(self) -> None:
        self.run_dropin(sysfs=os.path.join(self.tmp, "absent"))
        self.assertFalse(os.path.exists(self.dropin))

    def test_rerun_is_a_no_op(self) -> None:
        self.clock(0, "hyperv")
        self.run_dropin()
        before = os.stat(self.dropin)
        self.run_dropin()
        after = os.stat(self.dropin)
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))

    def test_stale_pre_fix_dropin_is_retired(self) -> None:
        # The pre-fix helper wrote exactly this line on any host with /dev/ptp0.
        self.clock(0, "atlantic ptp")
        os.makedirs(self.chrony_d)
        with open(self.dropin, "w", encoding="utf-8") as fh:
            fh.write("refclock PHC /dev/ptp0 poll 3 dpoll -2 offset 0 minsamples 4 prefer trust\n")
        self.run_dropin()
        self.assertFalse(os.path.exists(self.dropin))

    def test_own_dropin_is_retired_when_the_hyperv_clock_goes_away(self) -> None:
        self.clock(0, "hyperv")
        self.run_dropin()
        self.assertTrue(os.path.exists(self.dropin))
        shutil.rmtree(os.path.join(self.sysfs, "ptp0"))
        self.clock(0, "atlantic ptp")
        self.run_dropin()
        self.assertFalse(os.path.exists(self.dropin))

    def test_foreign_dropin_is_left_alone(self) -> None:
        self.clock(0, "atlantic ptp")
        os.makedirs(self.chrony_d)
        operator = "# operator-owned\nrefclock PHC /dev/ptp0 poll 2 prefer\n"
        with open(self.dropin, "w", encoding="utf-8") as fh:
            fh.write(operator)
        self.run_dropin()
        with open(self.dropin, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), operator)

    def test_baked_chrony_conf_has_no_refclock_and_loads_dropins(self) -> None:
        with open(_BAKED_CHRONY, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual([line for line in lines if line.lstrip().startswith("refclock")], [])
        self.assertEqual(lines.count("confdir /etc/chrony.d"), 1)

    @unittest.skipUnless(shutil.which("chronyd", path="/usr/sbin:/usr/bin:/sbin:/bin"), "chronyd not installed")
    def test_chronyd_loads_the_dropin_through_the_baked_confdir(self) -> None:
        # chronyd -p parses and prints the merged configuration, then exits.
        dev = self.clock(1, "hyperv")
        self.run_dropin()
        with open(_BAKED_CHRONY, encoding="utf-8") as fh:
            baked = fh.read()
        conf = os.path.join(self.tmp, "chrony.conf")
        with open(conf, "w", encoding="utf-8") as fh:
            fh.write(baked.replace("confdir /etc/chrony.d", f"confdir {self.chrony_d}"))
        chronyd = shutil.which("chronyd", path="/usr/sbin:/usr/bin:/sbin:/bin")
        res = subprocess.run([chronyd, "-p", "-f", conf], capture_output=True, text=True, check=False,
                             env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual([line for line in res.stdout.splitlines() if line.startswith("refclock")],
                         [f"refclock PHC {dev} {_REFCLOCK_TAIL}"])

if __name__ == "__main__":
    unittest.main()
