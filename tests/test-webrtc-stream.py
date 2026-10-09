#!/usr/bin/env python3
# AI-hint: Automated unit test suite for PipeWire DMA-BUF WebRTC Streamer (task_569, task_570).
# AI-related: usr/libexec/mios/ui/webrtc_stream.py, tests/test-webrtc-stream.py
"""Automated unit test suite for MiOS PipeWire DMA-BUF WebRTC Streamer."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "ui"))

from webrtc_stream import (
    LATENCY_TARGET_MS,
    PipeWireDMABUFStreamer,
    ScreenCastPortalBridge,
    StreamConfig,
)

class TestWebRTCStream(unittest.TestCase):
    def setUp(self):
        self.portal = ScreenCastPortalBridge(dry_run=True)
        self.config = StreamConfig(
            resolution="3840x2160",
            framerate=60,
            codec="H264",
            hw_encoder="auto",
            zero_copy_dmabuf=True,
            portal_auth_required=True,
        )
        self.streamer = PipeWireDMABUFStreamer(
            config=self.config,
            portal_bridge=self.portal,
            dry_run=True,
        )

    def test_portal_authorization_success(self):
        """Test successful portal handshake and session handle generation."""
        ok, handle = self.streamer.start_stream()
        self.assertTrue(ok)
        self.assertIsNotNone(handle)
        self.assertTrue(self.streamer.is_streaming())
        self.streamer.stop_stream()

    def test_portal_authorization_rejection(self):
        """Test stream is rejected when portal authorization is declined."""
        ok, msg = self.streamer.start_stream(force_auth_result=False)
        self.assertFalse(ok)
        self.assertIn("rejected", msg)
        self.assertFalse(self.streamer.is_streaming())

    def test_instant_session_revocation(self):
        """Test instant revocation invalidates active stream immediately."""
        ok, handle = self.streamer.start_stream()
        self.assertTrue(ok)
        self.assertTrue(self.streamer.is_streaming())

        # Revoke session
        revoked = self.streamer.stop_stream()
        self.assertTrue(revoked)
        self.assertFalse(self.streamer.is_streaming())

        # Frame processing must now fail
        frame = self.streamer.process_frame(dmabuf_fd=42)
        self.assertIsNone(frame)

    def test_zero_copy_latency_target_compliance(self):
        """Test that zero-copy DMA-BUF hardware encoding achieves <15ms end-to-end latency."""
        self.streamer.start_stream()
        for _ in range(30):
            # In simulation, encode + transmit takes ~8-9ms with zero-copy
            metric = self.streamer.process_frame(
                dmabuf_fd=100,
                simulated_encode_overhead_ms=4.0,
                simulated_transmit_overhead_ms=3.5,
            )
            self.assertIsNotNone(metric)
            self.assertLess(metric.total_latency_ms, LATENCY_TARGET_MS)

        avg_lat = self.streamer.get_average_latency_ms()
        self.assertLess(avg_lat, LATENCY_TARGET_MS)
        status = self.streamer.get_status()
        self.assertTrue(status["target_met"])
        self.streamer.stop_stream()

    def test_sdp_offer_generation(self):
        """Test SDP offer generation with correct video codec and parameters."""
        self.streamer.start_stream()
        sdp = self.streamer.generate_sdp_offer()
        self.assertEqual(sdp["type"], "offer")
        self.assertIn("H264", sdp["sdp"])
        self.assertIn("sendonly", sdp["sdp"])
        self.streamer.stop_stream()

if __name__ == "__main__":
    unittest.main()
