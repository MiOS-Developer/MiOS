#!/usr/bin/env python3
# AI-hint: Asserts journald keeps the kernel log and syncs within seconds on bare-metal and VM hosts, and that only a WSL2 kernel gets ReadKMsg=no (through the mios-wsl-journald generator's runtime drop-in), by merging the shipped and generated journald.conf.d drop-ins in systemd's order.
# AI-related: usr/lib/systemd/journald.conf.d/10-mios-noaudit.conf, usr/lib/systemd/system-generators/mios-wsl-journald
# AI-doc: usr/share/doc/mios/manual/tests.md
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "usr/lib/systemd/journald.conf.d"
GENERATOR = ROOT / "usr/lib/systemd/system-generators/mios-wsl-journald"


def seconds(value: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(ms|s|min|m)?", value.strip())
    if not m:
        raise AssertionError(f"unparsed time span {value!r}")
    n, unit = float(m.group(1)), m.group(2) or "s"
    return n * {"ms": 0.001, "s": 1, "min": 60, "m": 60}[unit]


def effective(runtime: Path) -> dict:
    """journald.conf.d semantics: one file per name (/run beats /usr/lib),
    files applied in name order, the last assignment of a key wins."""
    chosen = {p.name: p for p in VENDOR.glob("*.conf")}
    chosen.update({p.name: p for p in runtime.glob("*.conf")})
    out = {}
    for name in sorted(chosen):
        for line in chosen[name].read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith(("#", ";", "[")) and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def generate(release: str, runtime: Path) -> None:
    osr = runtime.parent / "osrelease"
    osr.write_text(release + "\n", encoding="utf-8")
    env = dict(os.environ, OSRELEASE_FILE=str(osr), JOURNALD_RUNTIME_DIR=str(runtime))
    subprocess.run([str(GENERATOR), "/tmp/n", "/tmp/e", "/tmp/l"], env=env, check=True)


class JournaldKmsg(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = Path(self.tmp.name) / "run"
        self.runtime.mkdir()

    def test_generator_is_executable(self):
        self.assertTrue(os.access(GENERATOR, os.X_OK))

    def test_bare_metal_keeps_the_kernel_log_and_syncs_fast(self):
        generate("7.3.3-ps5", self.runtime)
        conf = effective(self.runtime)
        self.assertNotEqual(conf.get("ReadKMsg", "yes").lower(), "no")
        self.assertLessEqual(seconds(conf.get("SyncIntervalSec", "5min")), 30)
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_wsl2_kernel_turns_kmsg_off_at_runtime(self):
        generate("6.6.87.2-microsoft-standard-WSL2", self.runtime)
        self.assertEqual(effective(self.runtime).get("ReadKMsg"), "no")

    def test_unreadable_osrelease_never_fails_boot(self):
        env = dict(os.environ, OSRELEASE_FILE=str(self.runtime / "absent"),
                   JOURNALD_RUNTIME_DIR=str(self.runtime))
        self.assertEqual(subprocess.run([str(GENERATOR)], env=env).returncode, 0)
        self.assertNotEqual(effective(self.runtime).get("ReadKMsg", "yes").lower(), "no")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)
