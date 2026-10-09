#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# AI-hint: Comprehensive unit test suite for mios_psi (task_429 PSI monitor).
# AI-doc: usr/share/doc/mios/manual/ch04-the-agentic-ai-stack.md
"""Two-sided unit tests for Linux PSI monitoring, threshold detection, and throttling triggers.

Verifies:
1. Parsing: positive controls on standard procfs lines; negative controls on malformed lines.
2. Fallback/Mock: degradation when /proc/pressure is absent.
3. Thresholds: positive controls (NORMAL, WARNING > 40.0, CRITICAL > 70.0); boundary controls.
4. Negative controls: full-pressure isolation, negative numbers, missing fields.
5. Throttling triggers & events: callback dispatch, event payload structure.
6. Async background lifecycle: start, sample loop, and clean cancellation.
7. Server integration: is_inference_throttled and _signal_inference_throttling.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import get_type_hints

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mios_psi
from mios_psi import (
    PsiMetric,
    PsiResourcePressure,
    PsiSample,
    PsiSampler,
    PSIMonitor,
    get_psi_monitor,
    is_psi_available,
    parse_psi_content,
    parse_psi_line,
    read_psi_file,
)

_fails = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _fails
    if not cond:
        _fails += 1
    status = "PASS" if cond else "FAIL"
    msg = f"[{status}] {name}"
    if detail:
        msg += f" -- {detail}"
    print(msg)


class TestPsiParsing(unittest.TestCase):
    """Positive and negative unit tests for PSI procfs line and content parsing."""

    def test_parse_valid_cpu_line(self) -> None:
        """Positive control: parse standard single-line CPU PSI."""
        raw = "some avg10=2.50 avg60=1.20 avg300=0.80 total=123456"
        parsed = parse_psi_line(raw)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        line_type, metric = parsed
        self.assertEqual(line_type, "some")
        self.assertAlmostEqual(metric.avg10, 2.50)
        self.assertAlmostEqual(metric.avg60, 1.20)
        self.assertAlmostEqual(metric.avg300, 0.80)
        self.assertEqual(metric.total, 123456)
        check("parsing: valid CPU some line", metric.avg10 == 2.50 and metric.total == 123456)

    def test_parse_valid_multiline_memory_content(self) -> None:
        """Positive control: parse multi-line memory PSI containing both some and full."""
        content = (
            "some avg10=15.40 avg60=8.20 avg300=2.10 total=987654\n"
            "full avg10=5.10 avg60=2.00 avg300=0.50 total=12345\n"
        )
        rp = parse_psi_content(content, resource="memory")
        self.assertEqual(rp.resource, "memory")
        self.assertIsNotNone(rp.some)
        self.assertIsNotNone(rp.full)
        assert rp.some is not None and rp.full is not None
        self.assertAlmostEqual(rp.some.avg10, 15.40)
        self.assertAlmostEqual(rp.full.avg10, 5.10)
        check("parsing: multi-line memory some and full", rp.some.avg10 == 15.40 and rp.full.avg10 == 5.10)

    def test_parse_empty_or_comment_lines(self) -> None:
        """Negative control: empty, whitespace-only, and comment lines return (None, None)."""
        self.assertEqual(parse_psi_line(""), (None, None))
        self.assertEqual(parse_psi_line("   \n"), (None, None))
        self.assertEqual(parse_psi_line("# comment line"), (None, None))
        check("parsing: negative control empty/comment lines return None", True)

    def test_parse_unknown_line_type(self) -> None:
        """Negative control: lines starting with unknown tokens return (None, None)."""
        self.assertEqual(parse_psi_line("unknown avg10=10.0 avg60=5.0 avg300=1.0 total=0"), (None, None))
        self.assertEqual(parse_psi_line("total avg10=10.0"), (None, None))
        check("parsing: negative control unknown line type", True)

    def test_parse_malformed_numbers_resilience(self) -> None:
        """Negative control: non-numeric tokens do not cause unhandled crashes."""
        line = "some avg10=corrupted avg60=12.5 avg300=NaN_val total=notanint"
        parsed = parse_psi_line(line)
        self.assertEqual(parsed, (None, None))
        check("parsing: negative control malformed numbers handled safely", True)

    def test_parse_missing_avg10_field(self) -> None:
        """Negative control: lines missing avg10 are rejected."""
        line = "some avg60=5.0 avg300=2.0 total=1000"
        self.assertEqual(parse_psi_line(line), (None, None))
        check("parsing: negative control missing avg10 rejected", True)

    def test_read_nonexistent_file(self) -> None:
        """Negative control: read_psi_file on missing path returns None safely."""
        res = read_psi_file("/tmp/nonexistent_psi_file_9999", "cpu")
        self.assertIsNone(res)
        check("parsing: negative control nonexistent file returns None", True)


class TestPsiFallbackAndMock(unittest.TestCase):
    """Tests for non-Linux host degradation, fallback metrics, and mock overrides."""

    def test_fallback_when_procfs_missing(self) -> None:
        """Positive control: missing procfs degrades open with NORMAL zero-pressure metrics."""
        sampler = PsiSampler(proc_dir="/nonexistent/proc/pressure/dir")
        self.assertFalse(sampler.available)
        sample = sampler.sample_once()
        self.assertEqual(sample.level, "NORMAL")
        self.assertFalse(sample.throttled)
        self.assertEqual(len(sample.triggers), 0)
        self.assertEqual(sample.resources["cpu"].some.avg10, 0.0)
        check("fallback: non-Linux host degrades open with NORMAL state", sample.level == "NORMAL")

    def test_mock_pressure_injection(self) -> None:
        """Positive control: mock pressure injection overrides file reads correctly."""
        sampler = PsiSampler(proc_dir="/nonexistent/proc/pressure/dir")
        sampler.set_mock_pressure("cpu", some_avg10=45.2, some_avg60=20.0, some_avg300=10.0, total=5000)
        self.assertTrue(sampler.available)  # mock data makes monitor available
        rp = sampler.read_resource("cpu")
        self.assertAlmostEqual(rp.some.avg10, 45.2)
        self.assertEqual(rp.some.total, 5000)
        check("mock: mock metrics injected and read successfully", rp.some.avg10 == 45.2)

    def test_mock_provider_callback(self) -> None:
        """Positive control: custom mock_provider function provides metrics."""
        def custom_provider(resource: str) -> PsiResourcePressure | None:
            if resource == "memory":
                return PsiResourcePressure(
                    resource="memory",
                    some=PsiMetric(avg10=62.5, avg60=30.0, avg300=15.0, total=100),
                )
            return None

        self.assertEqual(get_type_hints(custom_provider), {"resource": str, "return": PsiResourcePressure | None})
        sampler = PsiSampler(proc_dir="/nonexistent", mock_provider=custom_provider)
        rp = sampler.read_resource("memory")
        self.assertAlmostEqual(rp.some.avg10, 62.5)
        check("mock: custom provider callback respected", rp.some.avg10 == 62.5)

    def test_mock_provider_exception_handled(self) -> None:
        """Negative control: crashing mock provider degrades safely to fallback."""
        def bad_provider(resource: str) -> PsiResourcePressure | None:
            raise RuntimeError("Simulated provider crash")

        self.assertEqual(get_type_hints(bad_provider), {"resource": str, "return": PsiResourcePressure | None})
        sampler = PsiSampler(proc_dir="/nonexistent", mock_provider=bad_provider)
        rp = sampler.read_resource("io")
        self.assertEqual(rp.resource, "io")
        self.assertEqual(rp.some.avg10, 0.0)
        check("mock: provider exception degrades safely without crash", True)


class TestPsiThresholdsAndTriggers(unittest.TestCase):
    """Two-sided controls for pressure thresholds: NORMAL, WARNING (>40), CRITICAL (>70)."""

    def setUp(self) -> None:
        self.sampler = PsiSampler(
            proc_dir="/nonexistent",
            warning_threshold=40.0,
            critical_threshold=70.0,
            hysteresis_clear_pct=35.0,
            hysteresis_crit_clear_pct=65.0,
        )

    def test_normal_pressure_positive_control(self) -> None:
        """Positive control: all resources under warning threshold remain NORMAL."""
        self.sampler.set_mock_pressure("cpu", some_avg10=12.5)
        self.sampler.set_mock_pressure("memory", some_avg10=25.0)
        self.sampler.set_mock_pressure("io", some_avg10=5.0)
        sample = self.sampler.sample_once()
        self.assertEqual(sample.level, "NORMAL")
        self.assertFalse(sample.throttled)
        self.assertFalse(self.sampler.is_critical())
        self.assertFalse(self.sampler.is_throttled())
        check("threshold: normal low pressure evaluates to NORMAL", sample.level == "NORMAL")

    def test_boundary_exact_40_is_normal(self) -> None:
        """Boundary control: exactly 40.0% is not > 40.0%, remains NORMAL."""
        self.sampler.set_mock_pressure("cpu", some_avg10=40.0)
        self.sampler.set_mock_pressure("memory", some_avg10=20.0)
        self.sampler.set_mock_pressure("io", some_avg10=10.0)
        sample = self.sampler.sample_once()
        self.assertEqual(sample.level, "NORMAL")
        self.assertFalse(sample.throttled)
        check("threshold: boundary 40.0% remains NORMAL", sample.level == "NORMAL")

    def test_warning_above_40_cpu(self) -> None:
        """Positive control: CPU some.avg10 = 40.1% triggers WARNING event."""
        self.sampler.set_mock_pressure("cpu", some_avg10=40.1)
        self.sampler.set_mock_pressure("memory", some_avg10=10.0)
        self.sampler.set_mock_pressure("io", some_avg10=10.0)
        sample = self.sampler.sample_once()
        self.assertEqual(sample.level, "WARNING")
        self.assertFalse(sample.throttled)
        self.assertTrue(self.sampler.is_warning())
        self.assertFalse(self.sampler.is_critical())
        check("threshold: CPU some avg10 > 40.0 triggers WARNING", sample.level == "WARNING")

    def test_warning_above_40_memory(self) -> None:
        """Positive control: Memory some.avg10 = 55.0% triggers WARNING event."""
        self.sampler.set_mock_pressure("cpu", some_avg10=10.0)
        self.sampler.set_mock_pressure("memory", some_avg10=55.0)
        self.sampler.set_mock_pressure("io", some_avg10=10.0)
        sample = self.sampler.sample_once()
        self.assertEqual(sample.level, "WARNING")
        self.assertFalse(sample.throttled)
        self.assertEqual(sample.highest_resource, "memory")
        self.assertAlmostEqual(sample.highest_avg10, 55.0)
        check("threshold: Memory some avg10 > 40.0 triggers WARNING", sample.level == "WARNING")

    def test_warning_above_40_io(self) -> None:
        """Positive control: IO some.avg10 = 68.0% triggers WARNING event."""
        self.sampler.set_mock_pressure("cpu", some_avg10=10.0)
        self.sampler.set_mock_pressure("memory", some_avg10=10.0)
        self.sampler.set_mock_pressure("io", some_avg10=68.0)
        sample = self.sampler.sample_once()
        self.assertEqual(sample.level, "WARNING")
        self.assertFalse(sample.throttled)
        self.assertEqual(sample.highest_resource, "io")
        check("threshold: IO some avg10 > 40.0 triggers WARNING", sample.level == "WARNING")

    def test_boundary_exact_70_is_warning(self) -> None:
        """Boundary control: exactly 70.0% is not > 70.0%, remains WARNING (not critical)."""
        self.sampler.set_mock_pressure("cpu", some_avg10=70.0)
        sample = self.sampler.sample_once()
        self.assertEqual(sample.level, "WARNING")
        self.assertFalse(sample.throttled)
        self.assertFalse(self.sampler.is_critical())
        check("threshold: boundary 70.0% remains WARNING", sample.level == "WARNING")

    def test_critical_above_70_triggers_throttling_cpu(self) -> None:
        """Positive control: CPU some.avg10 = 70.1% triggers CRITICAL throttling."""
        self.sampler.set_mock_pressure("cpu", some_avg10=70.1)
        sample = self.sampler.sample_once()
        self.assertEqual(sample.level, "CRITICAL")
        self.assertTrue(sample.throttled)
        self.assertTrue(self.sampler.is_critical())
        self.assertTrue(self.sampler.is_throttled())
        shed_reason = self.sampler.get_shed_reason()
        self.assertIsNotNone(shed_reason)
        assert shed_reason is not None
        self.assertIn("cpu", shed_reason.lower())
        check("threshold: CPU some avg10 > 70.0 triggers CRITICAL throttling", sample.throttled is True)

    def test_critical_above_70_memory(self) -> None:
        """Positive control: Memory some.avg10 = 85.0% triggers CRITICAL throttling."""
        self.sampler.set_mock_pressure("memory", some_avg10=85.0)
        sample = self.sampler.sample_once()
        self.assertEqual(sample.level, "CRITICAL")
        self.assertTrue(sample.throttled)
        self.assertEqual(sample.highest_resource, "memory")
        check("threshold: Memory some avg10 > 70.0 triggers CRITICAL throttling", sample.throttled is True)

    def test_critical_above_70_io(self) -> None:
        """Positive control: IO some.avg10 = 92.0% triggers CRITICAL throttling."""
        self.sampler.set_mock_pressure("io", some_avg10=92.0)
        sample = self.sampler.sample_once()
        self.assertEqual(sample.level, "CRITICAL")
        self.assertTrue(sample.throttled)
        self.assertEqual(sample.highest_resource, "io")
        check("threshold: IO some avg10 > 70.0 triggers CRITICAL throttling", sample.throttled is True)

    def test_negative_control_full_pressure_isolation(self) -> None:
        """Negative control: high full pressure with low some pressure does NOT trigger WARNING or CRITICAL."""
        # Gating contract specifies `some avg10 > 40.0` / `some avg10 > 70.0`
        self.sampler.set_mock_pressure("memory", some_avg10=20.0, full_avg10=85.0)
        sample = self.sampler.sample_once()
        self.assertEqual(sample.level, "NORMAL")
        self.assertFalse(sample.throttled)
        check("threshold: negative control high full pressure alone does not trigger", sample.level == "NORMAL")

    def test_negative_pressure_values_safe(self) -> None:
        """Negative control: negative values do not trigger alarms."""
        self.sampler.set_mock_pressure("cpu", some_avg10=-10.0)
        sample = self.sampler.sample_once()
        self.assertEqual(sample.level, "NORMAL")
        self.assertFalse(sample.throttled)
        check("threshold: negative control negative values stay NORMAL", sample.level == "NORMAL")


class TestPsiEventCallbacksAndSignaling(unittest.TestCase):
    """Tests for listener dispatch, throttling state transition signaling, and event structure."""

    def setUp(self) -> None:
        self.sampler = PsiSampler(
            proc_dir="/nonexistent",
            warning_threshold=40.0,
            critical_threshold=70.0,
            hysteresis_clear_pct=35.0,
            hysteresis_crit_clear_pct=65.0,
        )

    def test_warning_event_dispatched(self) -> None:
        """Positive control: listener receives event payload when warning triggers."""
        received_events: list = []
        self.sampler.add_listener(lambda evt, *args: received_events.append(evt))

        self.sampler.set_mock_pressure("cpu", some_avg10=45.0)
        self.sampler.sample_once()

        self.assertGreaterEqual(len(received_events), 1)
        last_evt = received_events[-1]
        self.assertEqual(last_evt["level"], "WARNING")
        self.assertFalse(last_evt["throttled"])
        self.assertIn("triggers", last_evt)
        self.assertEqual(last_evt["triggers"][0]["resource"], "cpu")
        check("event: warning event correctly formatted and dispatched", last_evt["level"] == "WARNING")

    def test_critical_throttling_event_and_callback(self) -> None:
        """Positive control: throttle callback receives throttled=True on critical pressure."""
        throttle_signals: list = []
        self.sampler.register_throttle_callback(lambda throttled, evt: throttle_signals.append((throttled, evt)))

        self.sampler.set_mock_pressure("memory", some_avg10=78.5)
        self.sampler.sample_once()

        self.assertGreaterEqual(len(throttle_signals), 1)
        throttled_flag, event = throttle_signals[-1]
        self.assertTrue(throttled_flag)
        self.assertEqual(event["level"], "CRITICAL")
        self.assertTrue(event["throttled"])
        self.assertEqual(event["type"], "psi_throttling")
        check("event: critical throttling signaled to throttle callbacks", throttled_flag is True)

    def test_recovery_transition_signaled(self) -> None:
        """Positive control: pressure recovery signals throttled=False."""
        throttle_signals: list = []
        self.sampler.register_throttle_callback(lambda throttled, evt: throttle_signals.append((throttled, evt)))

        # Trigger CRITICAL
        self.sampler.set_mock_pressure("cpu", some_avg10=80.0)
        self.sampler.sample_once()
        self.assertTrue(self.sampler.is_throttled())

        # Recover to low pressure (<= 35% hysteresis clear)
        self.sampler.set_mock_pressure("cpu", some_avg10=10.0)
        # 2 consecutive samples to clear hysteresis
        self.sampler.sample_once()
        self.sampler.sample_once()

        self.assertFalse(self.sampler.is_throttled())
        # Latest throttle signal should be False
        self.assertFalse(throttle_signals[-1][0])
        check("event: recovery signals throttled=False to callbacks", throttle_signals[-1][0] is False)


class TestPsiAsyncLifecycle(unittest.TestCase):
    """Test continuous async background loop, periodic sampling, and clean cancellation."""

    def test_async_sampler_lifecycle(self) -> None:
        """Positive control: sampler starts, collects periodic samples, and stops cleanly."""
        async def _run_test() -> None:
            sampler = PsiSampler(
                proc_dir="/nonexistent",
                sample_interval_s=0.02,  # fast test interval
                warning_threshold=40.0,
                critical_threshold=70.0,
            )
            sampler.set_mock_pressure("cpu", some_avg10=15.0)

            self.assertFalse(sampler.is_running)
            await sampler.start()
            self.assertTrue(sampler.is_running)

            # Wait for at least 3 samples
            await asyncio.sleep(0.08)
            self.assertGreaterEqual(sampler._sample_count, 2)

            await sampler.stop()
            self.assertFalse(sampler.is_running)
            check("async: background sampler loop starts, samples, and stops cleanly", True)

        asyncio.run(_run_test())


class TestServerPsiIntegration(unittest.TestCase):
    """Test integration hook in server.py: inference throttling signal and load shedding."""

    def test_server_is_inference_throttled_reflects_psi_state(self) -> None:
        """Positive control: server.is_inference_throttled() dynamically responds to PSI pressure."""
        import server
        mon = get_psi_monitor()

        # Normal state
        mon.set_mock_pressure("cpu", some_avg10=10.0)
        mon.set_mock_pressure("memory", some_avg10=10.0)
        mon.set_mock_pressure("io", some_avg10=10.0)
        # Sample twice to ensure hysteresis clear
        mon.sample_once()
        mon.sample_once()
        self.assertFalse(server.is_inference_throttled())
        check("server: inference throttling is False under normal conditions", server.is_inference_throttled() is False)

        # Critical pressure state
        mon.set_mock_pressure("cpu", some_avg10=76.0)
        mon.sample_once()
        self.assertTrue(mon.is_critical())
        self.assertTrue(server.is_inference_throttled())
        check("server: inference throttling is True under critical PSI pressure", server.is_inference_throttled() is True)

        # Over global ceiling also evaluates to True during critical pressure
        self.assertTrue(server._over_global_ceiling())
        check("server: _over_global_ceiling is True when inference is throttled", server._over_global_ceiling() is True)

        # Clean up
        mon.set_mock_pressure("cpu", some_avg10=10.0)
        mon.sample_once()
        mon.sample_once()

    def test_signal_inference_throttling_dispatches(self) -> None:
        """Positive control: _signal_inference_throttling invokes registered callbacks."""
        import server

        cb_calls: list = []
        server.register_inference_throttle_callback(lambda throttled, evt: cb_calls.append((throttled, evt)))

        evt = {"level": "CRITICAL", "message": "Test throttle event"}
        asyncio.run(server._signal_inference_throttling(True, evt))

        self.assertTrue(server._PSI_THROTTLED)
        self.assertGreaterEqual(len(cb_calls), 1)
        self.assertTrue(cb_calls[-1][0])
        self.assertEqual(cb_calls[-1][1]["level"], "CRITICAL")
        check("server: _signal_inference_throttling dispatches to registered callbacks", cb_calls[-1][0] is True)

        # Reset
        asyncio.run(server._signal_inference_throttling(False, {"level": "NORMAL"}))
        self.assertFalse(server._PSI_THROTTLED)


def main() -> int:
    print("=== Running task_429 Linux PSI Unit Test Suite ===")
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    overall_ok = result.wasSuccessful() and (_fails == 0)
    print(f"\nResult: {'ALL CHECKS PASSED' if overall_ok else 'FAILED'} (Test failures: {len(result.failures)}, Errors: {len(result.errors)}, Check failures: {_fails})")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
