#!/usr/bin/env python3
# AI-hint: Asserts the mios-cpu-node inference unit carries cgroup memory bounds from SSOT [llamacpp].cpu_node_memory_high/_max (percent of RAM, High below Max, Max below the whole box), so one CPU inference node cannot starve a small Blade into a freeze.
# AI-related: usr/share/mios/mios.toml, usr/share/containers/systemd/mios-cpu-node.container
# AI-doc: usr/share/doc/mios/manual/tests.md
import re
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNIT = ROOT / "usr/share/containers/systemd/mios-cpu-node.container"


def service_value(text: str, key: str) -> list[str]:
    section, out = None, []
    for line in text.splitlines():
        m = re.match(r"^\[(\w+)\]$", line.strip())
        if m:
            section = m.group(1)
        elif section == "Service" and line.startswith(f"{key}="):
            out.append(line.split("=", 1)[1].strip())
    return out


def pct(value: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)%", value)
    if not m:
        raise AssertionError(f"{value!r} is not a percentage of physical RAM")
    return float(m.group(1))


class CpuNodeMemory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(ROOT / "usr/share/mios/mios.toml", "rb") as f:
            cls.cfg = tomllib.load(f)["llamacpp"]
        cls.text = UNIT.read_text(encoding="utf-8")

    def test_rendered_bounds_are_the_ssot_values(self):
        self.assertEqual(service_value(self.text, "MemoryHigh"), [self.cfg["cpu_node_memory_high"]])
        self.assertEqual(service_value(self.text, "MemoryMax"), [self.cfg["cpu_node_memory_max"]])

    def test_bounds_leave_the_host_headroom(self):
        high = pct(self.cfg["cpu_node_memory_high"])
        top = pct(self.cfg["cpu_node_memory_max"])
        self.assertLess(high, top, "MemoryHigh must throttle before MemoryMax kills")
        self.assertLessEqual(top, 75, "the node may not claim most of the box")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)
