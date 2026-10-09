#!/usr/bin/env python3
# AI-hint: Automated unit test suite for Multi-modal WebSocket Streaming & Sub-100ms Latency (task_615, task_616).
# AI-related: usr/lib/mios/agent-pipe/multimodal_ws.py, tests/test-multimodal-ws.py
"""Automated unit test suite for MiOS Multi-modal WebSocket Pipeline."""

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "lib", "mios", "agent-pipe"))

import multimodal_ws  # noqa: E402
from multimodal_ws import MAX_VOICE_LATENCY_MS, MultiModalStreamingPipeline

class TestMultiModalWS(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pipe = MultiModalStreamingPipeline(dry_run=True)

    async def test_sub_100ms_conversational_voice_latency(self):
        """Test concurrent audio and vision processing maintains <100ms voice response latency."""
        turn = await self.pipe.process_multimodal_turn("turn_test_01", audio_frames=10, video_frames=3)
        self.assertIsNotNone(turn)
        self.assertEqual(turn.audio_frames_in, 10)
        self.assertEqual(turn.video_frames_in, 3)
        self.assertLess(turn.voice_latency_ms, MAX_VOICE_LATENCY_MS)

    async def test_heavy_vision_load_does_not_starve_audio(self):
        """A vision pass slower than the whole voice budget must not reach voice latency."""
        slow = 3 * MAX_VOICE_LATENCY_MS / 1000.0
        real_sleep = asyncio.sleep

        async def sleep(delay, *a, **kw):  # stretch only the 30ms vision embedding step
            return await real_sleep(slow if delay == 0.03 else delay, *a, **kw)

        multimodal_ws.asyncio.sleep = sleep
        try:
            turn = await self.pipe.process_multimodal_turn("turn_heavy_vis", audio_frames=5, video_frames=10)
        finally:
            multimodal_ws.asyncio.sleep = real_sleep
        self.assertEqual(turn.video_frames_in, 10)
        self.assertLess(turn.voice_latency_ms, slow * 1000.0,
                        "voice latency included the vision pass: audio waited on vision")

if __name__ == "__main__":
    unittest.main()
