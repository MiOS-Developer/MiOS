#!/usr/bin/env python3
# AI-hint: Automated unit test suite for WS-AI speculative decoding multi-model lane configuration, and the light model map's invariants: unique served names, symmetric q8_0 KV on every chat entry, --jinja on every tool-calling entry.
# AI-related: usr/share/mios/llamacpp/mios-llm-light.yaml, usr/share/containers/systemd/mios-llm-heavy.container
"""Automated tests for WS-AI speculative model parameters and draft lane pairings."""

from __future__ import annotations

import os
import sys
import unittest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_CFG_PATH = os.path.join(_ROOT, "usr", "share", "mios", "llamacpp", "mios-llm-light.yaml")

class TestSpeculativeDecodingConfig(unittest.TestCase):
    """Validates llamacpp YAML model mappings and speculative parameters."""

    def test_yaml_structure_and_models(self):
        self.assertTrue(os.path.exists(_CFG_PATH))
        with open(_CFG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        self.assertIn("models", data)
        self.assertIn("granite4.1:8b", data["models"])
        self.assertIn("lfm2:700m", data["models"])

    def _models(self):
        with open(_CFG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)["models"]

    def test_served_names_are_unique_across_ids_and_aliases(self):
        seen = {}
        for model, entry in self._models().items():
            for name in [model] + list(entry.get("aliases") or []):
                self.assertNotIn(name, seen, f"{name} served by both {seen.get(name)} and {model}")
                seen[name] = model

    def test_chat_entries_run_symmetric_q8_0_kv(self):
        # A 4-bit KV cache flips tool-call decisions, and k != v falls back to
        # CPU attention upstream; embedding entries keep no KV ring to quantize.
        for model, entry in self._models().items():
            argv = entry["cmd"].split()
            if "--embedding" in argv:
                continue
            k = argv[argv.index("--cache-type-k") + 1]
            v = argv[argv.index("--cache-type-v") + 1]
            self.assertEqual((k, v), ("q8_0", "q8_0"), model)

    def test_tool_calling_entries_render_through_jinja(self):
        # Every chat entry except the inert vision-grounding lane parses tool calls.
        for model, entry in self._models().items():
            argv = entry["cmd"].split()
            if "--embedding" in argv or "--mmproj" in argv:
                continue
            self.assertIn("--jinja", argv, model)

def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestSpeculativeDecodingConfig)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1

if __name__ == "__main__":
    sys.exit(main())
