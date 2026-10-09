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
import unittest

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

    async def get(self, url, timeout=None):
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
        # ONE heavy lane beside the light floor, whichever engine serves it.
        self.assertEqual(set(snap["lanes"]), {"light", "heavy"})
        self.assertEqual(snap["lanes"]["heavy"]["url"], _TOOL_BACKEND_HEAVY)

if __name__ == "__main__":
    unittest.main(verbosity=2)
