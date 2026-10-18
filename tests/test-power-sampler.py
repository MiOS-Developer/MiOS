#!/usr/bin/env python3
# AI-hint: Tests for usr/libexec/mios/mios-power-sampler, the root CPU-package power sampler.
# Positive cases: the coarse average from fixture RAPL counters, counter wraparound, the 0.1 W quantum, and an atomic
# 0644 output. Negative cases: no package zone, a subzone only, an interval below the 1 s floor, and the live system
# keeping energy_uj root-only, with no world-readable udev/tmpfiles rule.
# AI-related: usr/libexec/mios/mios-power-sampler, usr/lib/systemd/system/mios-power-sampler.service,
# usr/share/mios/quickshell/Sysmon.qml, usr/libexec/mios/mios-sysmon-line
import os
import stat
import subprocess
import tempfile
import threading
import time
import unittest

SAMPLER = "/usr/libexec/mios/mios-power-sampler"


def zone(root, name, idx="intel-rapl:0", energy=1_000_000, rng=262_143_328_850):
    z = os.path.join(root, "sys/class/powercap", idx)
    os.makedirs(z, exist_ok=True)
    for f, v in (("name", name), ("energy_uj", str(energy)), ("max_energy_range_uj", str(rng))):
        with open(os.path.join(z, f), "w") as fh:
            fh.write(v + "\n")
    return os.path.join(z, "energy_uj")


def bump_later(path, value, delay=0.3):
    def go():
        time.sleep(delay)
        with open(path + ".t", "w") as fh:
            fh.write("%d\n" % value)
        os.replace(path + ".t", path)
    t = threading.Thread(target=go)
    t.start()
    return t


def run(root, out, interval="1", timeout=10):
    env = dict(os.environ, MIOS_POWER_ROOT=root, MIOS_POWER_DIR=out, MIOS_POWER_INTERVAL_S=interval)
    t0 = time.monotonic()
    p = subprocess.run([SAMPLER, "--once"], env=env, capture_output=True, text=True, timeout=timeout)
    return p, time.monotonic() - t0


class Positive(unittest.TestCase):
    def test_average_quantised_and_atomic(self):
        with tempfile.TemporaryDirectory() as d:
            e = zone(d, "package-0", energy=1_000_000)
            out = os.path.join(d, "out")
            th = bump_later(e, 1_000_000 + 25_000_000)          # 25 J over the ~1 s step: about 25 W
            p, _ = run(d, out)
            th.join()
            self.assertEqual(p.returncode, 0, p.stderr)
            f = os.path.join(out, "cpu_package_uw")
            uw = int(open(f).read())
            self.assertEqual(uw % 100_000, 0, "quantised to 0.1 W")
            self.assertTrue(20_000_000 <= uw <= 26_000_000, uw)
            self.assertEqual(stat.S_IMODE(os.stat(f).st_mode), 0o644)
            self.assertFalse(os.path.exists(f + ".tmp"), "no temp file left behind")

    def test_counter_wraparound(self):
        with tempfile.TemporaryDirectory() as d:
            rng = 100_000_000
            e = zone(d, "package-0", energy=rng - 2_000_000, rng=rng)
            out = os.path.join(d, "out")
            th = bump_later(e, 3_000_000)                        # 2 J before the wrap + 3 J after = 5 J: about 5 W
            p, _ = run(d, out)
            th.join()
            self.assertEqual(p.returncode, 0, p.stderr)
            uw = int(open(os.path.join(out, "cpu_package_uw")).read())
            self.assertTrue(4_000_000 <= uw <= 5_200_000, uw)


class Negative(unittest.TestCase):
    def test_interval_floor_is_one_second(self):
        with tempfile.TemporaryDirectory() as d:
            e = zone(d, "package-0")
            th = bump_later(e, 2_000_000, delay=0.05)
            p, took = run(d, os.path.join(d, "out"), interval="0.01")
            th.join()
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertGreaterEqual(took, 0.95, "a sub-second SSOT interval must be clamped to the 1 s floor")

    def test_no_package_zone_publishes_nothing_and_withdraws_stale(self):
        with tempfile.TemporaryDirectory() as d:
            zone(d, "core", idx="intel-rapl:0:0")               # a subzone only, no package
            out = os.path.join(d, "out")
            os.makedirs(out)
            open(os.path.join(out, "cpu_package_uw"), "w").write("9999999\n")
            p, _ = run(d, out)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertFalse(os.path.exists(os.path.join(out, "cpu_package_uw")), "a stale value must be withdrawn")

    def test_static_counter_publishes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            zone(d, "package-0")
            out = os.path.join(d, "out")
            p, _ = run(d, out)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertFalse(os.path.exists(os.path.join(out, "cpu_package_uw")), "0 W is not a reading")


class LiveSystem(unittest.TestCase):
    """The PLATYPUS mitigation stays in place: nothing makes RAPL energy counters world-readable."""

    def test_no_world_readable_rules(self):
        for p in ("/usr/lib/udev/rules.d/60-mios-rapl.rules", "/usr/lib/tmpfiles.d/mios-rapl.conf",
                  "/etc/udev/rules.d/60-mios-rapl.rules", "/etc/tmpfiles.d/mios-rapl.conf"):
            self.assertFalse(os.path.exists(p), p + " re-exposes energy_uj to every user")

    def test_energy_uj_not_world_readable(self):
        import glob
        files = glob.glob("/sys/class/powercap/intel-rapl:*/energy_uj")
        if not files:
            self.skipTest("no RAPL on this machine")
        for f in files:
            self.assertEqual(os.stat(f).st_mode & 0o044, 0, f + " is readable by group/other")

    def test_consumers_use_the_sampler(self):
        qml = open("/usr/share/mios/quickshell/Sysmon.qml").read()
        line = open("/usr/libexec/mios/mios-sysmon-line").read()
        self.assertIn("/run/mios/power/cpu_package_uw", qml)
        self.assertIn("/run/mios/power/cpu_package_uw", line)
        self.assertNotIn("powercap/intel-rapl:0/energy_uj", line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
