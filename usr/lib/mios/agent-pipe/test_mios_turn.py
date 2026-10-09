# AI-hint: Stdlib unit tests for mios_turn (per-turn message-prep + agent-selection
# AI-related: ./mios_turn.py
# AI-functions: (tests)
"""Stdlib tests for mios_turn -- no network, no DB (DI seam stubbed)."""

import asyncio
import re
import os
import unittest

import mios_turn

_THINK_TAGS = r"think|thinking|thought|reasoning|reflection|scratchpad"
_THINK_OPENERS = ("<think", "<thought", "<reason", "<reflect", "<scratch")
_THINK_CAP_RE = re.compile(
    rf"<({_THINK_TAGS})\b[^>]*>(.*?)</\1>", re.DOTALL | re.IGNORECASE)
_THINK_CAP_UNCLOSED_RE = re.compile(
    rf"<({_THINK_TAGS})\b[^>]*>(.*)$", re.DOTALL | re.IGNORECASE)
_THINK_ORPHAN_RE = re.compile(
    rf"</?({_THINK_TAGS})\b[^>]*>\s*", re.IGNORECASE)

def _configure(registry, node_live=None):
    """Inject a stub registry + (non-probing) health helpers + think regexes."""
    mios_turn.configure(
        _AGENT_REGISTRY=registry,
        _NODE_LIVE=dict(node_live or {}),
        _should_health_probe=lambda cfg: bool(cfg.get("health_gate")),
        _probe_auth_headers=lambda ep: {},
        NODE_LIVENESS_TTL_S=45.0,
        NODE_LIVENESS_CONNECT_S=6.0,
        _THINK_OPENERS=_THINK_OPENERS,
        _THINK_CAP_RE=_THINK_CAP_RE,
        _THINK_CAP_UNCLOSED_RE=_THINK_CAP_UNCLOSED_RE,
        _THINK_ORPHAN_RE=_THINK_ORPHAN_RE,
    )

class TestExtractLastUserText(unittest.TestCase):
    def test_plain_string(self):
        msgs = [{"role": "user", "content": "first"},
                {"role": "assistant", "content": "reply"},
                {"role": "user", "content": "second"}]
        self.assertEqual(mios_turn._extract_last_user_text(msgs), "second")

    def test_multimodal_parts(self):
        msgs = [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "x"}},
            {"type": "text", "text": "describe"}]}]
        self.assertEqual(mios_turn._extract_last_user_text(msgs), "describe")

    def test_no_user(self):
        msgs = [{"role": "assistant", "content": "hi"}, "not-a-dict"]
        self.assertEqual(mios_turn._extract_last_user_text(msgs), "")

class TestPickAgent(unittest.TestCase):
    def setUp(self):
        self.reg = {
            "alpha": {"role": "coder", "endpoint": "http://a"},
            "beta": {"role": "research", "default": True, "endpoint": "http://b"},
            "gamma": {"role": "vision", "endpoint": "http://c"},
        }

    def test_exact_role(self):
        _configure(self.reg)
        name, cfg = mios_turn._pick_agent("vision")
        self.assertEqual(name, "gamma")
        self.assertEqual(cfg["endpoint"], "http://c")

    def test_default_when_role_absent(self):
        _configure(self.reg)
        name, _cfg = mios_turn._pick_agent("nonexistent-role")
        self.assertEqual(name, "beta")  # the default flag wins

    def test_first_when_no_default(self):
        reg = {"only": {"role": "x", "endpoint": "http://o"}}
        _configure(reg)
        name, _cfg = mios_turn._pick_agent("")
        self.assertEqual(name, "only")

    def test_health_gate_degrade_open(self):
        reg = {"worker": {"role": "heavy", "endpoint": "http://w",
                          "model": "mios-heavy", "health_gate": True}}
        _configure(reg, node_live={})
        _name, cfg = mios_turn._pick_agent("heavy")
        self.assertEqual(cfg["endpoint"], "")

    def test_health_gate_live_kept(self):
        import time
        reg = {"worker": {"role": "heavy", "endpoint": "http://w",
                          "health_gate": True}}
        _configure(reg, node_live={"worker": (time.time(), True)})
        _name, cfg = mios_turn._pick_agent("heavy")
        self.assertEqual(cfg["endpoint"], "http://w")  # confirmed live -> untouched

class TestCasualAgentLabel(unittest.TestCase):
    def test_role_label(self):
        _configure({"alpha": {"role": "Coder"}})
        self.assertEqual(mios_turn._casual_agent_label("alpha"), "coder-agent")

    def test_generic_label(self):
        _configure({"alpha": {}})
        self.assertEqual(mios_turn._casual_agent_label("alpha"), "sub-agent")
        self.assertEqual(mios_turn._casual_agent_label("missing"), "sub-agent")

class TestThinkTags(unittest.TestCase):
    def test_split_captures_and_strips(self):
        _configure({"a": {}})
        txt = "<think>weighing options</think>The answer is 42."
        reasoning, answer = mios_turn._split_think_tags(txt)
        self.assertEqual(reasoning, "weighing options")
        self.assertEqual(answer, "The answer is 42.")

    def test_strip_only_answer(self):
        _configure({"a": {}})
        txt = "<reasoning>hmm</reasoning>Done."
        self.assertEqual(mios_turn._strip_think_tags(txt), "Done.")

    def test_no_tags_passthrough(self):
        _configure({"a": {}})
        self.assertEqual(mios_turn._strip_think_tags("plain text"), "plain text")

class TestLiveAgentNames(unittest.TestCase):
    def test_non_probed_lanes_all_live(self):
        reg = {"alpha": {"endpoint": "http://a"},
               "beta": {"endpoint": "http://b"}}
        _configure(reg)
        live = asyncio.run(mios_turn._live_agent_names())
        self.assertEqual(live, {"alpha", "beta"})

    def test_gated_node_cached_live(self):
        import time
        reg = {"local": {"endpoint": "http://l"},
               "gated": {"endpoint": "http://g", "health_gate": True}}
        _configure(reg, node_live={"gated": (time.time(), True)})
        live = asyncio.run(mios_turn._live_agent_names())
        self.assertEqual(live, {"local", "gated"})

    def test_gated_node_cached_dead(self):
        import time
        reg = {"local": {"endpoint": "http://l"},
               "gated": {"endpoint": "http://g", "health_gate": True}}
        _configure(reg, node_live={"gated": (time.time(), False)})
        live = asyncio.run(mios_turn._live_agent_names())
        self.assertEqual(live, {"local"})

class TestProbeVerifyTLS(unittest.TestCase):
    """task_254: probes verify TLS unless the SSOT opts out. See ADR-0016."""

    _SITES = (
        "mios_pipe/routing/turn.py",
        "mios_pipe/routing/portal.py",
        "mios_pipe/kernel/clusterhealth.py",
    )

    @staticmethod
    def _resolve(value):
        """The module-level expression, evaluated the way each site evaluates it."""
        return (value or "true").lower() not in {"false", "0", "no"}

    def test_default_verifies(self):
        self.assertIs(self._resolve(None), True)
        self.assertIs(self._resolve("true"), True)

    def test_opt_out_values(self):
        for v in ("false", "FALSE", "0", "no", "No"):
            self.assertIs(self._resolve(v), False, v)

    def test_unknown_value_stays_secure(self):
        for v in ("maybe", "", "yes", "1"):
            self.assertIs(self._resolve(v), True, v)

    def test_no_site_hardcodes_verify_false(self):
        import os
        base = os.path.dirname(os.path.abspath(__file__))
        for rel in self._SITES:
            body = open(os.path.join(base, rel), encoding="utf-8").read()
            self.assertNotIn("verify=False", body, rel)
            self.assertIn("verify=_PROBE_VERIFY_TLS", body, rel)

    def test_module_constant_matches_env(self):
        self.assertIs(mios_turn._PROBE_VERIFY_TLS,
                      self._resolve(os.environ.get("MIOS_SECURITY_PROBE_VERIFY_TLS")))

if __name__ == "__main__":
    unittest.main()
