#!/usr/bin/env python3
# AI-hint: Automated unit test suite for Bit-Perfect PipeWire Audio & Dynamic Sample Rates (task_647, task_648).
# AI-related: usr/libexec/mios/audio/bitperfect_mgr.py, tests/test-bitperfect-audio.py
"""Automated unit test suite for MiOS Bit-Perfect Audio Adapter."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "audio"))

from bitperfect_mgr import ALLOWED_SAMPLE_RATES, MAX_CLOCK_SWITCH_MS, BitPerfectAudioAdapter

class TestBitPerfectAudio(unittest.TestCase):
    def setUp(self):
        self.adapter = BitPerfectAudioAdapter(dry_run=True)

    def test_192khz_bit_perfect_passthrough(self):
        """Test 192kHz audio stream relocks DAC clock in <50ms with 0 XRuns."""
        state = self.adapter.adapt_sample_rate(192000)
        self.assertEqual(state.dac_hardware_rate_hz, 192000)
        self.assertTrue(state.is_bit_perfect)
        self.assertLess(state.switch_latency_ms, MAX_CLOCK_SWITCH_MS)
        self.assertEqual(state.buffer_xruns_detected, 0)

    def test_all_supported_sample_rates_adapt_cleanly(self):
        """Test all 6 standard audiophile rates achieve bit-perfect lock."""
        for rate in ALLOWED_SAMPLE_RATES:
            state = self.adapter.adapt_sample_rate(rate)
            self.assertEqual(state.dac_hardware_rate_hz, rate)
            self.assertTrue(state.is_bit_perfect)

if __name__ == "__main__":
    unittest.main()
