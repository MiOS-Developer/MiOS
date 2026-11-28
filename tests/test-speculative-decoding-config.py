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

    def test_every_served_gguf_and_tier_row_meets_the_tool_safe_floor(self):
        # Independent of mios-gen tool-safe (check_tool_safe_floor): the same SSOT rule,
        # so a sub-floor quant fails here even where no native build is available.
        import tomllib
        with open(os.path.join(_ROOT, "usr", "share", "mios", "mios.toml"), "rb") as f:
            ssot = tomllib.load(f)
        llama = ssot["llamacpp"]
        floor, facts = llama["tool_safe"], llama["gguf"]

        def meets(file):
            f = facts[file]
            if f["quant"] in floor["native"]:
                return True
            if f["kind"] == "embedding":
                need = floor["embed_floor_rank"]
            else:
                active = f.get("active_b", f["params_b"])
                small = (f["params_b"] < floor["large_min_params_b"]
                         or (active < f["params_b"] and active <= floor["small_max_active_b"]))
                need = floor["small_floor_rank"] if small and not f["reasoning"] else floor["large_floor_rank"]
            return floor["quant_rank"].get(f["quant"], 0) >= need

        for file, f in facts.items():
            # The cpu-node mixes model families against the heavy lane's pick.
            self.assertTrue(isinstance(f.get("family"), str) and f["family"], f"{file}: no family")
        for model, entry in self._models().items():
            file = entry["cmd"].split("--model", 1)[1].split()[0].rsplit("/", 1)[-1]
            self.assertIn(file, facts, f"{model}: no [llamacpp.gguf] row for {file}")
            self.assertTrue(meets(file), f"{model}: {file} is below the tool-safe floor")
        fetched = ({r["name"] for r in ssot["ai"]["firstboot_models"]}
                   | {e.split("=", 1)[0].strip() for e in llama["bake_models"].split(",")})
        for lane, rows in llama["tiers"].items():
            kv = floor["embed_kv"] if lane == "embed" else floor["chat_kv"]
            for row in rows:
                where = f"llamacpp.tiers.{lane}.{row['name']}"
                self.assertIn(row["cache_type"], kv, where)
                self.assertTrue(meets(row["model_file"]), where)
                self.assertIn(row["model_file"], fetched, f"{where}: no host can fetch it")

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
