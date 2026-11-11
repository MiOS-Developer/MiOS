# AI-hint: Verifies that every open MiOS monitor dashboard shares one git/observer probe per interval instead of each spawning its own (CPU and battery regression guard).
# AI-related: /usr/libexec/mios/mios-mon.py, usr/share/mios/mios.toml [terminal.monitor].git_poll_s agent_poll_s

import fcntl
import importlib.machinery
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr/lib/mios"))

monitor = importlib.machinery.SourceFileLoader(
    "mios_mon_shared_probe", str(ROOT / "usr/libexec/mios/mios-mon.py")
).load_module()


class SharedProbeTests(unittest.TestCase):
    def setUp(self):
        self.runtime = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.runtime.name})
        self.env.start()
        self.calls = 0

    def tearDown(self):
        self.env.stop()
        self.runtime.cleanup()

    def produce(self):
        self.calls += 1
        return f"value-{self.calls}"

    def test_dashboards_share_one_probe_per_interval(self):
        # Positive: many dashboards inside one interval cause a single probe.
        for _ in range(12):
            self.assertEqual(monitor._shared_probe("probe", 60, self.produce), "value-1")
        self.assertEqual(self.calls, 1)
        cache = Path(self.runtime.name, "mios-mon", "probe.json")
        self.assertEqual(cache.stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(self.runtime.name, "mios-mon").stat().st_mode & 0o777, 0o700)

    def test_expired_results_are_probed_again(self):
        monitor._shared_probe("probe", 60, self.produce)
        cache = Path(self.runtime.name, "mios-mon", "probe.json")
        old = time.time() - 120
        os.utime(cache, (old, old))
        self.assertEqual(monitor._shared_probe("probe", 60, self.produce), "value-2")
        self.assertEqual(self.calls, 2)

    def test_a_busy_probe_is_reused_not_duplicated(self):
        # Negative control: while another dashboard holds the lock, an expired
        # cache is returned as-is and no second probe is spawned.
        monitor._shared_probe("probe", 60, self.produce)
        cache = Path(self.runtime.name, "mios-mon", "probe.json")
        old = time.time() - 120
        os.utime(cache, (old, old))
        with open(str(cache) + ".lock", "a") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX)
            self.assertEqual(monitor._shared_probe("probe", 60, self.produce), "value-1")
        self.assertEqual(self.calls, 1)

    def test_without_a_runtime_directory_each_call_probes_directly(self):
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": ""}):
            monitor._shared_probe("probe", 60, self.produce)
            monitor._shared_probe("probe", 60, self.produce)
        self.assertEqual(self.calls, 2)

    def test_poll_intervals_come_from_ssot_and_reject_non_positive(self):
        good = {"terminal": {"monitor": {"git_poll_s": 45, "agent_poll_s": 7}}}
        with patch.object(monitor, "monitor_config", return_value=good):
            self.assertEqual(monitor._monitor_interval("git_poll_s", 30), 45.0)
            self.assertEqual(monitor._monitor_interval("agent_poll_s", 5), 7.0)
        bad = {"terminal": {"monitor": {"git_poll_s": 0}}}
        with patch.object(monitor, "monitor_config", return_value=bad):
            with self.assertRaises(ValueError):
                monitor._monitor_interval("git_poll_s", 30)
        shipped = ROOT / "usr/share/mios/mios.toml"
        self.assertIn("git_poll_s = 30", shipped.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
