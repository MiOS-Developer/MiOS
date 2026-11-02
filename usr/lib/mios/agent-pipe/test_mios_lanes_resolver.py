# AI-hint: Stdlib unit tests for mios_lanes_resolver (strangler-fig lane-resolver
# AI-related: mios_lanes_resolver.py, mios_lanes.py, mios_config.py
"""Stdlib unit tests for mios_lanes_resolver (strangler-fig extraction).

Drives the moved lane-resolver cluster with a fake httpx client + stubbed
config -- NO network, NO DB. Asserts: lane selection prefers the heavy lane when
its probe is up, falls back to the always-on light lane when the heavy lane is
down, the legacy heavy/light probe is used when the resolver path raises, and the
_heavy_lane_up probe caches + degrades closed. Run: ``python test_mios_lanes_resolver.py``.
"""
import asyncio
import importlib
import os
import unittest
from unittest import mock

import mios_lanes_resolver as M
from mios_config import (
    _TOOL_BACKEND, _TOOL_BACKEND_MODEL,
    _TOOL_BACKEND_HEAVY, _TOOL_BACKEND_HEAVY_MODEL,
)

class _Resp:
    def __init__(self, status):
        self.status_code = status

class _FakeClient:
    """Fake httpx client: get(url) -> 200 iff url starts with one of `up_urls`."""
    def __init__(self, up_urls, *, boom=False):
        self._up = tuple(up_urls)
        self._boom = boom
        self.requests = []

    async def get(self, url, timeout=None):
        self.requests.append(url)
        if self._boom:
            raise RuntimeError("connect refused")
        ok = any(url.startswith(u) for u in self._up)
        return _Resp(200 if ok else 503)

def _wire(up_urls, *, boom=False):
    """Reset the resolver singleton + inject deterministic deps (no real config)."""
    M._LANE_RESOLVER = None
    M._heavy_probe["ok"] = False
    M._heavy_probe["ts"] = -1e9
    client = _FakeClient(up_urls, boom=boom)

    async def _get_client():
        return client

    M.configure(_get_client=_get_client, _is_remote_endpoint=lambda ep: False)
    M._toml_section = lambda section: {}

class LaneResolverTests(unittest.TestCase):
    def test_prefers_heavy_when_up(self):
        _wire(up_urls=[_TOOL_BACKEND_HEAVY])
        url, model = asyncio.run(M._pick_tool_backend())
        self.assertEqual((url, model), (_TOOL_BACKEND_HEAVY, _TOOL_BACKEND_HEAVY_MODEL))

    def test_falls_back_to_light_when_heavy_down(self):
        _wire(up_urls=[_TOOL_BACKEND])
        url, model = asyncio.run(M._pick_tool_backend())
        self.assertEqual((url, model), (_TOOL_BACKEND, _TOOL_BACKEND_MODEL))

    def test_legacy_probe_fallback_when_resolver_raises(self):
        _wire(up_urls=[_TOOL_BACKEND_HEAVY])

        _orig = M._lane_resolver

        def _boom():
            raise RuntimeError("resolver exploded")
        M._lane_resolver = _boom
        try:
            url, model = asyncio.run(M._pick_tool_backend())
        finally:
            M._lane_resolver = _orig
        self.assertEqual((url, model), (_TOOL_BACKEND_HEAVY, _TOOL_BACKEND_HEAVY_MODEL))

    def test_heavy_lane_up_true_and_cached(self):
        _wire(up_urls=[_TOOL_BACKEND_HEAVY])
        self.assertTrue(asyncio.run(M._heavy_lane_up()))
        M._get_client = None  # would crash if re-probed
        self.assertTrue(asyncio.run(M._heavy_lane_up()))

    def test_heavy_lane_up_false_on_error(self):
        _wire(up_urls=[], boom=True)
        self.assertFalse(asyncio.run(M._heavy_lane_up()))

    def test_lane_resolver_current_tracks_singleton(self):
        _wire(up_urls=[_TOOL_BACKEND_HEAVY])
        self.assertIsNone(M._lane_resolver_current())
        res = M._lane_resolver()
        self.assertIs(M._lane_resolver_current(), res)
        snap = res.snapshot()
        self.assertIn("lanes", snap)
        # The registry retains engine aliases and a CPU floor beside the canonical heavy lane.
        self.assertEqual(set(snap["lanes"]), {"light", "heavy", "sglang", "vllm", "cpu_node"})
        self.assertEqual(snap["lanes"]["heavy"]["url"], _TOOL_BACKEND_HEAVY)


class ConfiguredHeavyProbeTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module("mios_pipe.routing.lanes_resolver")
        self.light = "http://heavy-vllm-light.invalid:49151/v1"
        self.heavy = "http://127.0.0.1:49152/v1"
        self.other_heavy = "http://127.0.0.1:49153/v1"
        self.client = _FakeClient([self.light, self.heavy, self.other_heavy])
        async def get_client():
            return self.client
        patches = [
            mock.patch.dict(os.environ, {"MIOS_AGENT_PIPE_TOOL_BACKEND_VLLM": self.other_heavy,
                                         "MIOS_PORTS_CPU_NODE": "49154"}),
            mock.patch.multiple(self.module, _LANE_RESOLVER=None,
                                _TOOL_BACKEND=self.light,
                                _TOOL_BACKEND_HEAVY=self.heavy,
                                _get_client=get_client,
                                _toml_section=lambda section: {},
                                _is_remote_endpoint=lambda url: False),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_custom_heavy_endpoints_are_not_probed_during_gpu_lease(self):
        for state in ("VM_ATTACHED", "DRAINING_SLEEP", "RESTORING_AI"):
            with self.subTest(state=state), mock.patch.object(
                    self.module, "get_gpu_arbiter_state", return_value=state):
                resolver = self.module._lane_resolver()
                for url in (self.heavy, self.other_heavy, self.heavy + "/"):
                    self.assertFalse(asyncio.run(resolver._probe(url)))
        self.assertEqual([], self.client.requests)

    def test_unrelated_light_name_does_not_trigger_heavy_classifier(self):
        with mock.patch.object(self.module, "get_gpu_arbiter_state", return_value="VM_ATTACHED"):
            resolver = self.module._lane_resolver()
            self.assertTrue(asyncio.run(resolver._probe(self.light)))
        self.assertEqual([self.light + "/models"], self.client.requests)

    def test_active_gpu_probes_configured_custom_heavy(self):
        with mock.patch.object(self.module, "get_gpu_arbiter_state", return_value="AI_EXCLUSIVE"):
            resolver = self.module._lane_resolver()
            self.assertTrue(asyncio.run(resolver._probe(self.heavy)))
            self.assertTrue(asyncio.run(resolver._probe(self.other_heavy)))
        self.assertEqual([self.heavy + "/models", self.other_heavy + "/models"], self.client.requests)

    def test_vllm_alias_uses_canonical_heavy_configuration(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("MIOS_AGENT_PIPE_TOOL_BACKEND_VLLM", None)
            resolver = self.module._lane_resolver()
            lanes = resolver.snapshot()["lanes"]
            self.assertEqual(self.heavy, lanes["vllm"]["url"])
            self.assertEqual(self.heavy, lanes["sglang"]["url"])
            self.assertEqual(self.heavy, lanes["heavy"]["url"])

    def test_existing_per_node_custom_endpoint_still_classified(self):
        config = {"nodes": {"local-vllm": {"endpoint": self.other_heavy}}}
        with mock.patch.dict(os.environ), mock.patch.multiple(
                self.module, _toml_section=lambda section: config.get(section, {}),
                get_gpu_arbiter_state=lambda: "VM_ATTACHED"):
            os.environ.pop("MIOS_AGENT_PIPE_TOOL_BACKEND_VLLM", None)
            resolver = self.module._lane_resolver()
            self.assertEqual(self.other_heavy, resolver.snapshot()["lanes"]["vllm"]["url"])
            self.assertFalse(asyncio.run(resolver._probe(self.other_heavy)))
        self.assertEqual([], self.client.requests)

    def test_remote_escalation_is_not_classified_by_heavy_hostname(self):
        remote = "http://heavy-remote.invalid:49155/v1"
        self.client._up += (remote,)
        config = {"ai": {"remote_escalation": "on"},
                  "nodes": {"remote-lane": {"endpoint": remote, "model": "remote"}}}
        with mock.patch.multiple(self.module,
                                 _toml_section=lambda section: config.get(section, {}),
                                 _is_remote_endpoint=lambda url: True,
                                 get_gpu_arbiter_state=lambda: "VM_ATTACHED"):
            resolver = self.module._lane_resolver()
            self.assertIn("remote:remote-lane", resolver.snapshot()["lanes"])
            self.assertTrue(asyncio.run(resolver._probe(remote)))
        self.assertEqual([remote + "/models"], self.client.requests)

if __name__ == "__main__":
    unittest.main(verbosity=2)
