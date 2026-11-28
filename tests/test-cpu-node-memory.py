#!/usr/bin/env python3
# AI-hint: Asserts the mios-cpu-node inference unit carries cgroup memory bounds from SSOT [llamacpp].cpu_node_memory_high/_max (percent of RAM, High below Max, Max below the whole box), so one CPU inference node cannot starve a small Blade into a freeze; its model file, context window and symmetric K/V type are expanded at start from the lane's model tier (read after install.env), and every tier row's KV meets the tool-safe floor.
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
            cls.ssot = tomllib.load(f)
        cls.cfg = cls.ssot["llamacpp"]
        cls.text = UNIT.read_text(encoding="utf-8")

    def test_rendered_bounds_are_the_ssot_values(self):
        self.assertEqual(service_value(self.text, "MemoryHigh"), [self.cfg["cpu_node_memory_high"]])
        self.assertEqual(service_value(self.text, "MemoryMax"), [self.cfg["cpu_node_memory_max"]])

    def _exec(self):
        execs = [l.split("=", 1)[1] for l in self.text.splitlines() if l.startswith("Exec=")]
        self.assertEqual(len(execs), 1)
        return execs[0].split()

    def test_model_context_and_kv_are_expanded_at_start_from_the_model_tier(self):
        # The renderer leaves them for systemd ([generator].placeholders): each host's
        # [[llamacpp.tiers.cpu_node]] pick is written to the lane's env file at start.
        argv = self._exec()
        self.assertEqual(argv[argv.index("--model") + 1], "/models/${MIOS_LLAMACPP_CPU_NODE_MODEL_FILE}")
        self.assertEqual(argv[argv.index("--ctx-size") + 1], "${MIOS_LLAMACPP_CPU_NODE_CONTEXT}")
        for name in ("MIOS_LLAMACPP_CPU_NODE_MODEL_FILE", "MIOS_LLAMACPP_CPU_NODE_CONTEXT",
                     "MIOS_LLAMACPP_CPU_NODE_CACHE_TYPE"):
            self.assertIn(name, self.ssot["generator"]["placeholders"])
        cond = [l.split("=", 1)[1] for l in self.text.splitlines() if l.startswith("ConditionPathExists=")]
        self.assertEqual(cond, [f"{self.cfg['models_dir']}/.ready"])

    def test_the_tier_env_file_is_read_after_install_env_and_written_before_start(self):
        envs = service_value(self.text, "EnvironmentFile")
        tier = f"-{self.cfg['tier_policy']['env_dir']}/cpu-node.env"
        self.assertEqual(envs, ["/etc/mios/install.env", tier], "the tier must be read last to win")
        self.assertEqual(service_value(self.text, "ExecStartPre"),
                         ["-/usr/bin/mios-gen model-tier --root / --lane cpu-node --apply"],
                         "an always-on lane must start on its static keys if the pick fails")

    def test_kv_cache_type_is_one_tool_safe_value_for_both_k_and_v(self):
        # One placeholder drives both flags, so the lane can never go asymmetric
        # (k != v falls back to CPU attention upstream); the static value meets the floor.
        argv = self._exec()
        self.assertEqual(argv[argv.index("--cache-type-k") + 1], "${MIOS_LLAMACPP_CPU_NODE_CACHE_TYPE}")
        self.assertEqual(argv[argv.index("--cache-type-v") + 1], "${MIOS_LLAMACPP_CPU_NODE_CACHE_TYPE}")
        self.assertIn(self.cfg["cpu_node_cache_type"], self.cfg["tool_safe"]["chat_kv"])
        for row in self.cfg["tiers"]["cpu_node"]:
            self.assertIn(row["cache_type"], self.cfg["tool_safe"]["chat_kv"], row["name"])

    def test_bounds_leave_the_host_headroom(self):
        high = pct(self.cfg["cpu_node_memory_high"])
        top = pct(self.cfg["cpu_node_memory_max"])
        self.assertLess(high, top, "MemoryHigh must throttle before MemoryMax kills")
        self.assertLessEqual(top, 75, "the node may not claim most of the box")

    def test_bounds_fit_the_node_on_the_smallest_blade(self):
        # Measured on the 14G PS5 Blade: 8B GGUF at 32k context = 8.6G, 62% of
        # RAM. A High at or under that throttles the node permanently; a Max
        # under it OOM-loops it. Re-measure before shrinking the model or ctx.
        smallest_blade_footprint_pct = 62
        self.assertGreater(pct(self.cfg["cpu_node_memory_high"]), smallest_blade_footprint_pct)
        self.assertGreater(pct(self.cfg["cpu_node_memory_max"]), smallest_blade_footprint_pct)

    def test_swap_spill_is_bounded_from_ssot(self):
        self.assertEqual(service_value(self.text, "MemorySwapMax"), [self.cfg["cpu_node_memory_swap_max"]])


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)
