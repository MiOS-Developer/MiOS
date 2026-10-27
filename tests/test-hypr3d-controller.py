#!/usr/bin/env python3
# AI-hint: Two-sided regression test suite for Hypr3D universal controller support (L3+R3 toggle chord and dual-stick controls).
# AI-doc: usr/share/doc/mios/manual/ch89-glass-desktop-and-session-resilience.md
# AI-related: usr/share/mios/mios.toml [desktop.hyprland.hypr3d.controller], usr/libexec/mios/mios-gamepad-hypr3d
"""
Two-Sided Regression Test Suite for Milestone 4: Hypr3D Controller Support.

Verifies:
1. Positive controls:
   - Simultaneous L3 + R3 (BTN_THUMBL + BTN_THUMBR) chord within debounce window triggers Hypr3D toggle.
   - Left stick 3D translation when Hypr3D mode is active.
   - Right stick 3D camera look (yaw/pitch) when Hypr3D mode is active.
   - Fallback axes (ABS_Z / ABS_RZ) when primary axes (ABS_RX / ABS_RY) are absent.
   - Gamepad capability detection (EV_KEY + EV_ABS).
   - SSOT configuration parsing.

2. Negative controls:
   - Single button press (L3 alone or R3 alone) does NOT trigger toggle.
   - Unrelated buttons (BTN_SOUTH, BTN_EAST, etc.) do NOT trigger toggle.
   - Debounce hold suppression: holding chord down triggers exactly once.
   - Deadzone suppression: stick inputs below threshold (< 0.15) are ignored.
   - Inactive suppression: stick inputs while Hypr3D is inactive produce zero commands.
   - Device filter: non-gamepad devices (keyboards, mice) are rejected.
   - Invalid SSOT bounds rejection.
"""

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import os
import sys
import unittest
from typing import List, Tuple

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DAEMON_PATH = os.path.join(ROOT, "usr", "libexec", "mios", "mios-gamepad-hypr3d")
SSOT_PATH = os.path.join(ROOT, "usr", "share", "mios", "mios.toml")

# Dynamically import daemon module
loader = SourceFileLoader("mios_gamepad_hypr3d", DAEMON_PATH)
spec = importlib.util.spec_from_file_location("mios_gamepad_hypr3d", DAEMON_PATH, loader=loader)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Failed to load daemon module from {DAEMON_PATH}")
daemon_mod = importlib.util.module_from_spec(spec)
sys.modules["mios_gamepad_hypr3d"] = daemon_mod
spec.loader.exec_module(daemon_mod)

ControllerConfig = daemon_mod.ControllerConfig
ChordDetector = daemon_mod.ChordDetector
StickProcessor = daemon_mod.StickProcessor
GamepadHypr3dDaemon = daemon_mod.GamepadHypr3dDaemon
GamepadDevice = daemon_mod.GamepadDevice

EV_KEY = daemon_mod.EV_KEY
EV_ABS = daemon_mod.EV_ABS
EV_SYN = daemon_mod.EV_SYN
SYN_REPORT = daemon_mod.SYN_REPORT
BTN_THUMBL = daemon_mod.BTN_THUMBL
BTN_THUMBR = daemon_mod.BTN_THUMBR
BTN_SOUTH = daemon_mod.BTN_SOUTH
BTN_EAST = daemon_mod.BTN_EAST
BTN_NORTH = daemon_mod.BTN_NORTH
BTN_WEST = daemon_mod.BTN_WEST
BTN_TL = daemon_mod.BTN_TL
BTN_TR = daemon_mod.BTN_TR
BTN_SELECT = daemon_mod.BTN_SELECT
BTN_START = daemon_mod.BTN_START
BTN_TL2 = daemon_mod.BTN_TL2
BTN_TR2 = daemon_mod.BTN_TR2
ABS_X = daemon_mod.ABS_X
ABS_Y = daemon_mod.ABS_Y
ABS_Z = daemon_mod.ABS_Z
ABS_RX = daemon_mod.ABS_RX
ABS_RY = daemon_mod.ABS_RY
ABS_RZ = daemon_mod.ABS_RZ
ABS_HAT0X = daemon_mod.ABS_HAT0X
ABS_HAT0Y = daemon_mod.ABS_HAT0Y


class TestHypr3dChordPositive(unittest.TestCase):
    """Positive controls for L3 + R3 simultaneous chord detection."""

    def test_l3_r3_simultaneous_press_triggers_toggle(self):
        detector = ChordDetector(["BTN_THUMBL", "BTN_THUMBR"], debounce_ms=250)
        t0 = 100.0

        # Press L3
        r1 = detector.process_key(BTN_THUMBL, 1, now=t0)
        self.assertFalse(r1, "L3 alone should not trigger chord")

        # Press R3 50ms later (within 250ms debounce window)
        r2 = detector.process_key(BTN_THUMBR, 1, now=t0 + 0.05)
        self.assertTrue(r2, "Simultaneous L3 + R3 within debounce window MUST trigger chord")

    def test_r3_then_l3_reverse_order_triggers_toggle(self):
        detector = ChordDetector(["BTN_THUMBL", "BTN_THUMBR"], debounce_ms=250)
        t0 = 200.0

        # Press R3 first
        r1 = detector.process_key(BTN_THUMBR, 1, now=t0)
        self.assertFalse(r1, "R3 alone should not trigger chord")

        # Press L3 30ms later
        r2 = detector.process_key(BTN_THUMBL, 1, now=t0 + 0.03)
        self.assertTrue(r2, "Reverse order R3 then L3 within debounce window MUST trigger chord")

    def test_release_and_repress_triggers_second_toggle(self):
        detector = ChordDetector(["BTN_THUMBL", "BTN_THUMBR"], debounce_ms=250)
        t0 = 300.0

        # Chord 1
        detector.process_key(BTN_THUMBL, 1, now=t0)
        r1 = detector.process_key(BTN_THUMBR, 1, now=t0 + 0.02)
        self.assertTrue(r1)

        # Release both
        detector.process_key(BTN_THUMBL, 0, now=t0 + 0.3)
        detector.process_key(BTN_THUMBR, 0, now=t0 + 0.35)

        # Chord 2 after cooldown
        detector.process_key(BTN_THUMBL, 1, now=t0 + 0.7)
        r2 = detector.process_key(BTN_THUMBR, 1, now=t0 + 0.72)
        self.assertTrue(r2, "Second chord after release MUST trigger second toggle")


class TestHypr3dChordNegative(unittest.TestCase):
    """Negative controls for chord detection (single button, unrelated buttons, hold debounce)."""

    def test_single_l3_press_never_triggers(self):
        detector = ChordDetector(["BTN_THUMBL", "BTN_THUMBR"], debounce_ms=250)
        r = detector.process_key(BTN_THUMBL, 1, now=10.0)
        self.assertFalse(r, "NEGATIVE CONTROL FAILED: BTN_THUMBL alone triggered chord!")
        detector.process_key(BTN_THUMBL, 0, now=10.5)

    def test_single_r3_press_never_triggers(self):
        detector = ChordDetector(["BTN_THUMBL", "BTN_THUMBR"], debounce_ms=250)
        r = detector.process_key(BTN_THUMBR, 1, now=20.0)
        self.assertFalse(r, "NEGATIVE CONTROL FAILED: BTN_THUMBR alone triggered chord!")
        detector.process_key(BTN_THUMBR, 0, now=20.5)

    def test_unrelated_buttons_never_trigger(self):
        detector = ChordDetector(["BTN_THUMBL", "BTN_THUMBR"], debounce_ms=250)
        for btn in (BTN_SOUTH, BTN_EAST, BTN_START):
            r = detector.process_key(btn, 1, now=30.0)
            self.assertFalse(r, f"NEGATIVE CONTROL FAILED: Unrelated button {btn} triggered chord!")

    def test_debounce_hold_suppression(self):
        detector = ChordDetector(["BTN_THUMBL", "BTN_THUMBR"], debounce_ms=250)
        t0 = 50.0

        # Initial trigger
        detector.process_key(BTN_THUMBL, 1, now=t0)
        r1 = detector.process_key(BTN_THUMBR, 1, now=t0 + 0.01)
        self.assertTrue(r1)

        # Holding both buttons down continuously across multiple poll events
        for dt in [0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.0]:
            r_hold = detector.process_key(BTN_THUMBL, 1, now=t0 + dt)
            self.assertFalse(r_hold, "NEGATIVE CONTROL FAILED: Holding chord re-triggered without release!")
            r_hold2 = detector.process_key(BTN_THUMBR, 1, now=t0 + dt)
            self.assertFalse(r_hold2, "NEGATIVE CONTROL FAILED: Holding chord re-triggered without release!")

    def test_chord_exceeding_debounce_window_fails(self):
        detector = ChordDetector(["BTN_THUMBL", "BTN_THUMBR"], debounce_ms=250)
        t0 = 60.0

        # Press L3
        detector.process_key(BTN_THUMBL, 1, now=t0)
        # Press R3 400ms later (> 250ms debounce window)
        r = detector.process_key(BTN_THUMBR, 1, now=t0 + 0.40)
        self.assertFalse(r, "NEGATIVE CONTROL FAILED: Button presses outside debounce window triggered chord!")


class TestDualStickControlsPositive(unittest.TestCase):
    """Positive controls for Left Stick translation and Right Stick camera rotation."""

    def test_left_stick_translation_when_active(self):
        cfg = ControllerConfig(deadzone=0.15, sensitivity=1.0, move_sensitivity=1.0)
        proc = StickProcessor(cfg)

        # Move Left Stick forward-right (ABS_X=20000, ABS_Y=-20000)
        vecs = proc.process_abs(ABS_X, 20000)
        vecs = proc.process_abs(ABS_Y, -20000)

        lx, ly = vecs["left"]
        self.assertGreater(lx, 0.0, "Left stick X deflection must produce positive translation")
        self.assertLess(ly, 0.0, "Left stick Y deflection must produce negative translation")

    def test_right_stick_camera_look_when_active(self):
        cfg = ControllerConfig(deadzone=0.15, sensitivity=1.0, camera_sensitivity=1.0)
        proc = StickProcessor(cfg, available_axes={ABS_RX, ABS_RY})

        # Move Right Stick yaw right (ABS_RX=25000), pitch down (ABS_RY=25000)
        vecs = proc.process_abs(ABS_RX, 25000)
        vecs = proc.process_abs(ABS_RY, 25000)

        rx, ry = vecs["right"]
        self.assertGreater(rx, 0.0, "Right stick yaw must be positive")
        self.assertGreater(ry, 0.0, "Right stick pitch must be positive")

    def test_fallback_axes_for_right_stick(self):
        # Gamepad lacking ABS_RX/ABS_RY but exposing ABS_Z/ABS_RZ
        cfg = ControllerConfig(fallback_axes=["ABS_Z", "ABS_RZ"], deadzone=0.15)
        proc = StickProcessor(cfg, available_axes={ABS_X, ABS_Y, ABS_Z, ABS_RZ})

        self.assertEqual(proc.rx_axis, ABS_Z, "Right stick X should fallback to ABS_Z")
        self.assertEqual(proc.ry_axis, ABS_RZ, "Right stick Y should fallback to ABS_RZ")

        vecs = proc.process_abs(ABS_Z, 22000)
        vecs = proc.process_abs(ABS_RZ, -22000)

        rx, ry = vecs["right"]
        self.assertGreater(rx, 0.0, "Fallback axis ABS_Z must produce positive yaw")
        self.assertLess(ry, 0.0, "Fallback axis ABS_RZ must produce negative pitch")

    def test_dpad_translation_override(self):
        cfg = ControllerConfig(dpad_enabled=True, deadzone=0.15)
        proc = StickProcessor(cfg)

        # D-pad right (ABS_HAT0X=1)
        vecs = proc.process_abs(ABS_HAT0X, 1)
        lx, _ = vecs["left"]
        self.assertGreater(lx, 0.0, "D-pad right must drive positive left-stick translation")

    def test_wasd_mode_emits_sendshortcut_commands(self):
        """Positive control: left_stick_mode='wasd' generates discrete sendshortcut key commands."""
        cfg = ControllerConfig(left_stick_mode="wasd", deadzone=0.15)
        daemon = GamepadHypr3dDaemon(config=cfg, dry_run=True)
        daemon.hypr3d_active = True

        # Deflect forward -> W
        daemon.process_event(EV_ABS, ABS_Y, -25000, now=10.0)
        daemon.process_event(EV_SYN, SYN_REPORT, 0, now=10.0)
        self.assertIn("w", daemon.active_wasd_keys, "Forward stick must activate 'w' key in WASD mode")
        w_cmds = [cmd for cmd in daemon.dispatched_commands if any(",W," in part for part in cmd) and "sendshortcut" in cmd]
        self.assertGreater(len(w_cmds), 0, "sendshortcut for W must be dispatched with comma syntax")

        # Deflect right -> D (at 10.1 > 10.0 + 10ms poll interval)
        daemon.process_event(EV_ABS, ABS_X, 25000, now=10.1)
        daemon.process_event(EV_SYN, SYN_REPORT, 0, now=10.1)
        self.assertIn("d", daemon.active_wasd_keys, "Right stick must activate 'd' key in WASD mode")
        d_cmds = [cmd for cmd in daemon.dispatched_commands if any(",D," in part for part in cmd) and "sendshortcut" in cmd]
        self.assertGreater(len(d_cmds), 0, "sendshortcut for D must be dispatched with comma syntax")

        # Return to center -> keys released, NO spurious release taps dispatched
        cmd_count_before_center = len(daemon.dispatched_commands)
        daemon.process_event(EV_ABS, ABS_X, 0, now=10.2)
        daemon.process_event(EV_ABS, ABS_Y, 0, now=10.2)
        daemon.process_event(EV_SYN, SYN_REPORT, 0, now=10.2)
        self.assertEqual(len(daemon.active_wasd_keys), 0, "Centering stick must release all WASD keys")
        cmd_count_after_center = len(daemon.dispatched_commands)
        self.assertEqual(cmd_count_before_center, cmd_count_after_center, "Centering stick must NOT dispatch extra release keypresses")

    def test_continuous_mode_primary_dispatch(self):
        """Positive control: left_stick_mode='continuous' dispatches hypr3d:move when available."""
        cfg = ControllerConfig(left_stick_mode="continuous", deadzone=0.15)
        daemon = GamepadHypr3dDaemon(
            config=cfg,
            dry_run=True,
            dry_run_success_map={"hypr3d:move": True},
        )
        daemon.hypr3d_active = True

        daemon.process_event(EV_ABS, ABS_X, 25000, now=20.0)
        daemon.process_event(EV_SYN, SYN_REPORT, 0, now=20.0)
        move_cmds = [cmd for cmd in daemon.dispatched_commands if any("hypr3d:move" in part for part in cmd)]
        shortcut_cmds = [cmd for cmd in daemon.dispatched_commands if any("sendshortcut" in part for part in cmd)]
        self.assertGreater(len(move_cmds), 0, "Continuous mode must dispatch hypr3d:move")
        self.assertEqual(len(shortcut_cmds), 0, "Primary continuous dispatch must not trigger WASD sendshortcut")
        self.assertTrue(daemon.has_hypr3d_move, "has_hypr3d_move must remain True on success")

    def test_continuous_mode_fallback_to_wasd_on_failure(self):
        """Two-sided control: failure of hypr3d:move falls back to discrete WASD sendshortcut."""
        cfg = ControllerConfig(left_stick_mode="continuous", deadzone=0.15)
        daemon = GamepadHypr3dDaemon(
            config=cfg,
            dry_run=True,
            dry_run_success_map={"hypr3d:move": False},
        )
        daemon.hypr3d_active = True

        daemon.process_event(EV_ABS, ABS_Y, -25000, now=20.0)
        daemon.process_event(EV_SYN, SYN_REPORT, 0, now=20.0)
        move_cmds = [cmd for cmd in daemon.dispatched_commands if any("hypr3d:move" in part for part in cmd)]
        shortcut_cmds = [cmd for cmd in daemon.dispatched_commands if any(",W," in part for part in cmd)]
        self.assertGreater(len(move_cmds), 0, "Attempted hypr3d:move must have been recorded")
        self.assertGreater(len(shortcut_cmds), 0, "Failed hypr3d:move must immediately fallback to WASD ,W, sendshortcut")
        self.assertFalse(daemon.has_hypr3d_move, "has_hypr3d_move must be cached as False after failure")

    def test_continuous_mode_stick_centering_dispatches_stop(self):
        """Positive control: centering stick after continuous deflection dispatches hypr3d:move stop."""
        cfg = ControllerConfig(left_stick_mode="continuous", deadzone=0.15)
        daemon = GamepadHypr3dDaemon(
            config=cfg,
            dry_run=True,
            dry_run_success_map={"hypr3d:move": True},
        )
        daemon.hypr3d_active = True

        # 1. Deflect forward
        daemon.process_event(EV_ABS, ABS_Y, -25000, now=10.0)
        daemon.process_event(EV_SYN, SYN_REPORT, 0, now=10.0)
        self.assertTrue(daemon.active_continuous_move, "Continuous move must be active on deflection")
        move_cmds = [cmd for cmd in daemon.dispatched_commands if any("hypr3d:move" in part for part in cmd)]
        self.assertGreater(len(move_cmds), 0, "Continuous deflection must dispatch hypr3d:move")

        # 2. Return stick to center
        cmds_before_center = len(daemon.dispatched_commands)
        daemon.process_event(EV_ABS, ABS_Y, 0, now=10.1)
        daemon.process_event(EV_SYN, SYN_REPORT, 0, now=10.1)

        self.assertFalse(daemon.active_continuous_move, "Continuous move must be inactive after centering")
        new_cmds = daemon.dispatched_commands[cmds_before_center:]
        self.assertEqual(len(new_cmds), 1, "Centering stick in continuous mode must dispatch exactly one command")
        self.assertEqual(
            new_cmds[0],
            ["/usr/bin/hyprctl", "dispatch", "hypr3d:move", "stop"],
            "Centering stick must dispatch hypr3d:move stop",
        )

        # 3. Duplicate center/deadzone event should NOT emit additional stop commands
        cmds_before_dup = len(daemon.dispatched_commands)
        daemon.process_event(EV_ABS, ABS_Y, 0, now=10.2)
        daemon.process_event(EV_SYN, SYN_REPORT, 0, now=10.2)
        self.assertEqual(
            len(daemon.dispatched_commands),
            cmds_before_dup,
            "Duplicate centering events while stationary must not emit redundant stop commands",
        )

        # 4. Deflecting again then entering deadzone also dispatches stop
        daemon.process_event(EV_ABS, ABS_X, 25000, now=10.3)
        daemon.process_event(EV_SYN, SYN_REPORT, 0, now=10.3)
        self.assertTrue(daemon.active_continuous_move)

        cmds_before_dz = len(daemon.dispatched_commands)
        daemon.process_event(EV_ABS, ABS_X, 1000, now=10.4)  # inside deadzone
        daemon.process_event(EV_SYN, SYN_REPORT, 0, now=10.4)
        self.assertFalse(daemon.active_continuous_move)
        new_dz_cmds = daemon.dispatched_commands[cmds_before_dz:]
        self.assertEqual(
            new_dz_cmds,
            [["/usr/bin/hyprctl", "dispatch", "hypr3d:move", "stop"]],
            "Entering deadzone must dispatch hypr3d:move stop",
        )

    def test_camera_rotation_primary_dispatch(self):
        """Positive control: right stick dispatches hypr3d:camera when available."""
        cfg = ControllerConfig(deadzone=0.15, sensitivity=1.0)
        daemon = GamepadHypr3dDaemon(
            config=cfg,
            dry_run=True,
            dry_run_success_map={"hypr3d:camera": True},
        )
        daemon.hypr3d_active = True

        daemon.process_event(EV_ABS, ABS_RX, 20000, now=30.0)
        daemon.process_event(EV_SYN, SYN_REPORT, 0, now=30.0)
        cam_cmds = [cmd for cmd in daemon.dispatched_commands if any("hypr3d:camera" in part for part in cmd)]
        cursor_cmds = [cmd for cmd in daemon.dispatched_commands if any("movecursor" in part for part in cmd)]
        self.assertGreater(len(cam_cmds), 0, "Right stick must dispatch hypr3d:camera")
        self.assertEqual(len(cursor_cmds), 0, "Primary camera dispatch must not trigger movecursor")
        self.assertTrue(daemon.has_hypr3d_camera, "has_hypr3d_camera must remain True on success")

    def test_camera_rotation_fallback_to_movecursor_on_failure(self):
        """Two-sided control: failure of hypr3d:camera falls back to movecursor."""
        cfg = ControllerConfig(deadzone=0.15, sensitivity=1.0)
        daemon = GamepadHypr3dDaemon(
            config=cfg,
            dry_run=True,
            dry_run_success_map={"hypr3d:camera": False},
        )
        daemon.hypr3d_active = True

        daemon.process_event(EV_ABS, ABS_RX, 20000, now=30.0)
        daemon.process_event(EV_SYN, SYN_REPORT, 0, now=30.0)
        cam_cmds = [cmd for cmd in daemon.dispatched_commands if any("hypr3d:camera" in part for part in cmd)]
        cursor_cmds = [cmd for cmd in daemon.dispatched_commands if any("movecursor" in part for part in cmd)]
        self.assertGreater(len(cam_cmds), 0, "Attempted hypr3d:camera must have been recorded")
        self.assertGreater(len(cursor_cmds), 0, "Failed hypr3d:camera must fallback to movecursor")
        self.assertFalse(daemon.has_hypr3d_camera, "has_hypr3d_camera must be cached as False after failure")

    def test_multi_axis_packet_buffering_prevents_axis_drop(self):
        """Positive control: ABS_X and ABS_Y delivered sequentially before EV_SYN are buffered together."""
        cfg = ControllerConfig(left_stick_mode="continuous", deadzone=0.15)
        moves: List[Tuple[float, float]] = []
        daemon = GamepadHypr3dDaemon(
            config=cfg,
            dry_run=True,
            move_callback=lambda x, y: moves.append((x, y)),
            dry_run_success_map={"hypr3d:move": True},
        )
        daemon.hypr3d_active = True

        # Send ABS_X and ABS_Y sequentially within the same hardware packet
        daemon.process_event(EV_ABS, ABS_X, 22000, now=40.0)
        self.assertEqual(len(daemon.dispatched_commands), 0, "EV_ABS alone must not trigger premature dispatch")
        daemon.process_event(EV_ABS, ABS_Y, -22000, now=40.0)
        self.assertEqual(len(daemon.dispatched_commands), 0, "EV_ABS alone must not trigger premature dispatch")

        # Now send EV_SYN
        daemon.process_event(EV_SYN, SYN_REPORT, 0, now=40.0)
        self.assertEqual(len(daemon.dispatched_commands), 1, "Exactly one compound dispatch on EV_SYN")
        self.assertEqual(len(moves), 1)
        mx, my = moves[0]
        self.assertGreater(mx, 0.0, "Compound packet must contain positive X deflection")
        self.assertLess(my, 0.0, "Compound packet must contain negative Y deflection")

    def test_rate_limiting_throttles_rapid_dispatches(self):
        """Positive control: dispatches within poll_ms interval are throttled to prevent fork flooding."""
        cfg = ControllerConfig(poll_ms=50, left_stick_mode="wasd")
        daemon = GamepadHypr3dDaemon(config=cfg, dry_run=True)
        daemon.hypr3d_active = True

        t0 = 50.0
        daemon.process_event(EV_ABS, ABS_X, 20000, now=t0)
        daemon.process_event(EV_SYN, SYN_REPORT, 0, now=t0)
        initial_count = len(daemon.dispatched_commands)
        self.assertGreater(initial_count, 0)

        # Dispatch 10 events within 5ms (< 50ms poll_ms)
        for i in range(1, 10):
            daemon.process_event(EV_ABS, ABS_X, 20000 + i * 100, now=t0 + 0.0005 * i)
            daemon.process_event(EV_SYN, SYN_REPORT, 0, now=t0 + 0.0005 * i)

        # Count should remain unchanged because all subsequent events were throttled
        self.assertEqual(len(daemon.dispatched_commands), initial_count, "Rapid analog events must be rate-limited")

        # After poll_ms expires, next event executes
        daemon.process_event(EV_ABS, ABS_X, 25000, now=t0 + 0.060)
        daemon.process_event(EV_SYN, SYN_REPORT, 0, now=t0 + 0.060)
        self.assertGreater(len(daemon.dispatched_commands), initial_count, "Event after poll_ms must be dispatched")

    def test_held_stick_continuous_dispatch_on_timeout(self):
        """Positive control: held stick deflected maintains continuous dispatch across polling intervals."""
        cfg = ControllerConfig(poll_ms=10)
        daemon = GamepadHypr3dDaemon(config=cfg, dry_run=True)
        daemon.hypr3d_active = True

        daemon.process_event(EV_ABS, ABS_X, 25000, now=60.0)
        daemon.process_event(EV_SYN, SYN_REPORT, 0, now=60.0)
        initial_cmds = len(daemon.dispatched_commands)

        # Simulate polling interval timeout while stick is still held
        daemon.handle_stick_input(daemon.current_left_vector, daemon.current_right_vector, force=True, now=60.02)
        self.assertGreater(len(daemon.dispatched_commands), initial_cmds, "Held stick must dispatch across interval ticks")

    def test_device_disconnect_resets_chord_latch(self):
        """Positive control: device disconnect cleans up chord detector and held keys."""
        cfg = ControllerConfig()
        daemon = GamepadHypr3dDaemon(config=cfg, dry_run=True)
        daemon.chord_detector.button_states[BTN_THUMBL] = True
        daemon.chord_detector.button_states[BTN_THUMBR] = True
        daemon.chord_detector.chord_triggered = True
        daemon.active_wasd_keys = {"w", "d"}

        daemon.chord_detector.reset()
        daemon._release_wasd_keys()

        self.assertFalse(daemon.chord_detector.chord_triggered, "Chord triggered latch must be cleared on disconnect")
        self.assertFalse(any(daemon.chord_detector.button_states.values()), "Button states must be reset")
        self.assertEqual(len(daemon.active_wasd_keys), 0, "WASD keys must be released")


class TestDualStickControlsNegative(unittest.TestCase):
    """Negative controls for deadzone filtering and inactive suppression."""

    def test_deadzone_boundary_one_point_zero_prevents_zero_division(self):
        """Negative control: deadzone = 1.0 with diagonal deflection must not raise ZeroDivisionError."""
        cfg = ControllerConfig(deadzone=1.0)
        proc = StickProcessor(cfg)

        # Diagonal corner: x=1.0, y=1.0 -> mag = 1.4142 > 1.0
        vecs = proc.process_abs(ABS_X, 32767)
        vecs = proc.process_abs(ABS_Y, 32767)

        lx, ly = vecs["left"]
        self.assertEqual(lx, 0.0, "deadzone=1.0 must suppress all deflections to 0.0")
        self.assertEqual(ly, 0.0, "deadzone=1.0 must suppress all deflections to 0.0")

    def test_left_stick_deadzone_suppression(self):
        cfg = ControllerConfig(deadzone=0.15)
        proc = StickProcessor(cfg)

        # Small deflection: ~4.5% of max (1500 / 32767), inside 15% deadzone
        vecs = proc.process_abs(ABS_X, 1500)
        vecs = proc.process_abs(ABS_Y, 1500)

        lx, ly = vecs["left"]
        self.assertEqual(lx, 0.0, f"NEGATIVE CONTROL FAILED: Left stick X {lx} inside deadzone was not suppressed!")
        self.assertEqual(ly, 0.0, f"NEGATIVE CONTROL FAILED: Left stick Y {ly} inside deadzone was not suppressed!")

    def test_right_stick_deadzone_suppression(self):
        cfg = ControllerConfig(deadzone=0.15)
        proc = StickProcessor(cfg, available_axes={ABS_RX, ABS_RY})

        # Small deflection: ~6% of max (2000 / 32767), inside 15% deadzone
        vecs = proc.process_abs(ABS_RX, 2000)
        vecs = proc.process_abs(ABS_RY, 2000)

        rx, ry = vecs["right"]
        self.assertEqual(rx, 0.0, f"NEGATIVE CONTROL FAILED: Right stick RX {rx} inside deadzone was not suppressed!")
        self.assertEqual(ry, 0.0, f"NEGATIVE CONTROL FAILED: Right stick RY {ry} inside deadzone was not suppressed!")

    def test_inactive_suppression_blocks_all_movement_and_camera(self):
        # Daemon instantiated with hypr3d_active = False
        moves: List[Tuple[float, float]] = []
        cams: List[Tuple[float, float]] = []

        cfg = ControllerConfig(deadzone=0.15)
        daemon = GamepadHypr3dDaemon(
            config=cfg,
            dry_run=True,
            move_callback=lambda x, y: moves.append((x, y)),
            camera_callback=lambda x, y: cams.append((x, y)),
        )

        daemon.hypr3d_active = False  # Explicitly inactive

        # Large stick deflections (90% deflection)
        daemon.process_event(EV_ABS, ABS_X, 28000)
        daemon.process_event(EV_ABS, ABS_Y, -28000)
        daemon.process_event(EV_ABS, ABS_RX, 28000)
        daemon.process_event(EV_ABS, ABS_RY, 28000)
        daemon.process_event(EV_SYN, SYN_REPORT, 0)

        self.assertEqual(len(moves), 0, f"NEGATIVE CONTROL FAILED: Movement emitted while inactive! moves={moves}")
        self.assertEqual(len(cams), 0, f"NEGATIVE CONTROL FAILED: Camera emitted while inactive! cams={cams}")
        self.assertEqual(len(daemon.dispatched_commands), 0, f"NEGATIVE CONTROL FAILED: Commands dispatched while inactive! cmds={daemon.dispatched_commands}")


class TestSsotConfiguration(unittest.TestCase):
    """Verifies SSOT configuration schema parsing and validation."""

    def test_default_config_types_and_defaults(self):
        cfg = ControllerConfig()
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.toggle_chord, ["BTN_THUMBL", "BTN_THUMBR"])
        self.assertEqual(cfg.debounce_ms, 250)
        self.assertEqual(cfg.deadzone, 0.15)
        self.assertEqual(cfg.sensitivity, 1.0)
        self.assertEqual(cfg.left_stick_mode, "continuous")
        self.assertEqual(cfg.right_stick_mode, "camera")
        self.assertFalse(cfg.invert_y)
        self.assertTrue(cfg.dpad_enabled)
        self.assertEqual(cfg.fallback_axes, ["ABS_Z", "ABS_RZ"])

    def test_invalid_deadzone_raises_error(self):
        with self.assertRaises(ValueError):
            ControllerConfig.from_dict({
                "desktop": {"hyprland": {"hypr3d": {"controller": {"deadzone": 1.5}}}}
            })

        with self.assertRaises(ValueError):
            ControllerConfig.from_dict({
                "desktop": {"hyprland": {"hypr3d": {"controller": {"deadzone": -0.1}}}}
            })

    def test_invalid_debounce_raises_error(self):
        with self.assertRaises(ValueError):
            ControllerConfig.from_dict({
                "desktop": {"hyprland": {"hypr3d": {"controller": {"debounce_ms": 0}}}}
            })


class TestDeviceFilter(unittest.TestCase):
    """Positive and negative controls for gamepad capability discovery and device filtering."""

    @staticmethod
    def _create_bitmask(bits: List[int], size_bytes: int) -> bytearray:
        buf = bytearray(size_bytes)
        for b in bits:
            buf[b // 8] |= (1 << (b % 8))
        return buf

    def test_standard_gamepad_capabilities_accepted(self):
        """Positive control: gamepad with BTN_SOUTH and ABS_X/ABS_Y."""
        key_mask = self._create_bitmask([BTN_SOUTH, BTN_EAST], 96)
        abs_mask = self._create_bitmask([ABS_X, ABS_Y, ABS_RX, ABS_RY], 8)
        accepted = GamepadDevice.matches_capabilities(True, True, key_mask, abs_mask)
        self.assertTrue(accepted, "Standard gamepad capabilities must be accepted")

    def test_thumbl_thumbr_gamepad_capabilities_accepted(self):
        """Positive control: controller with BTN_THUMBL and BTN_THUMBR and ABS_X/ABS_Y."""
        key_mask = self._create_bitmask([BTN_THUMBL, BTN_THUMBR], 96)
        abs_mask = self._create_bitmask([ABS_X, ABS_Y], 8)
        accepted = GamepadDevice.matches_capabilities(True, True, key_mask, abs_mask)
        self.assertTrue(accepted, "Controller with L3+R3 and analog sticks must be accepted")

    def test_mouse_capabilities_rejected(self):
        """Negative control: mouse with BTN_LEFT and no ABS axes must be rejected."""
        btn_left = 0x110
        key_mask = self._create_bitmask([btn_left], 96)
        abs_mask = bytearray(8)
        accepted = GamepadDevice.matches_capabilities(True, False, key_mask, abs_mask)
        self.assertFalse(accepted, "NEGATIVE CONTROL FAILED: Mouse was accepted as a gamepad!")

    def test_keyboard_capabilities_rejected(self):
        """Negative control: standard keyboard (EV_KEY without gamepad buttons or ABS) must be rejected."""
        key_mask = self._create_bitmask([1, 2, 3, 28, 57], 96)  # Esc, 1, 2, Enter, Space
        abs_mask = bytearray(8)
        accepted = GamepadDevice.matches_capabilities(True, False, key_mask, abs_mask)
        self.assertFalse(accepted, "NEGATIVE CONTROL FAILED: Keyboard was accepted as a gamepad!")

    def test_touchscreen_tablet_rejected(self):
        """Negative control: touchscreen/tablet with ABS but no gamepad buttons must be rejected."""
        btn_touch = 0x14A
        key_mask = self._create_bitmask([btn_touch], 96)
        abs_mask = self._create_bitmask([ABS_X, ABS_Y], 8)
        accepted = GamepadDevice.matches_capabilities(True, True, key_mask, abs_mask)
        self.assertFalse(accepted, "NEGATIVE CONTROL FAILED: Touchscreen was accepted as a gamepad!")

    def test_missing_abs_y_rejected(self):
        """Negative control: single-axis throttle/pedal lacking ABS_Y must be rejected."""
        key_mask = self._create_bitmask([BTN_SOUTH], 96)
        abs_mask = self._create_bitmask([ABS_X], 8)  # missing ABS_Y
        accepted = GamepadDevice.matches_capabilities(True, True, key_mask, abs_mask)
        self.assertFalse(accepted, "NEGATIVE CONTROL FAILED: Single-axis device missing ABS_Y was accepted!")


class TestDualSenseAxisNormalizationAndTriggers(unittest.TestCase):
    """Two-sided controls for DualSense 0..255 axis ranges, RT grab, and LT resize."""

    def test_dualsense_unsigned_0_255_axis_normalization(self):
        """Positive & Negative control: DualSense (0, 255) EVIOCGABS range centers at 128 -> 0.0."""
        cfg = ControllerConfig(deadzone=0.15, sensitivity=1.0, move_sensitivity=1.0)
        abs_ranges = {
            ABS_X: (0, 255),
            ABS_Y: (0, 255),
            ABS_RX: (0, 255),
            ABS_RY: (0, 255),
            ABS_Z: (0, 255),
            ABS_RZ: (0, 255),
        }
        proc = StickProcessor(cfg, available_axes=set(abs_ranges.keys()), axis_ranges=abs_ranges)

        # Negative control: resting center 128 on DualSense must normalize inside deadzone -> 0.0
        vecs = proc.process_abs(ABS_X, 128)
        vecs = proc.process_abs(ABS_Y, 128)
        self.assertEqual(vecs["left"], (0.0, 0.0), "DualSense resting center 128 must yield (0.0, 0.0)")

        # Positive control: full right (255) and full forward (0)
        vecs = proc.process_abs(ABS_X, 255)
        vecs = proc.process_abs(ABS_Y, 0)
        lx, ly = vecs["left"]
        self.assertGreater(lx, 0.5, "DualSense ABS_X=255 must produce positive X deflection")
        self.assertLess(ly, -0.5, "DualSense ABS_Y=0 must produce negative Y deflection")

    def test_rt_grab_button_and_analog_trigger_positive_and_dedup(self):
        """Positive control: RT (BTN_TR2 and ABS_RZ) dispatches hypr3d:grab down/up without duplicate dispatches."""
        cfg = ControllerConfig(trigger_threshold=0.30)
        daemon = GamepadHypr3dDaemon(config=cfg, dry_run=True)
        daemon.hypr3d_active = True

        # Press analog RT (ABS_RZ=255 on default 0..255 trigger range) + digital BTN_TR2=1
        daemon.process_event(EV_ABS, ABS_RZ, 255, now=100.0)
        daemon.process_event(EV_KEY, BTN_TR2, 1, now=100.0)
        daemon.process_event(EV_SYN, SYN_REPORT, 0, now=100.0)

        self.assertTrue(daemon.rt_grab_active, "RT press must activate rt_grab_active")
        grab_down_cmds = [
            cmd for cmd in daemon.dispatched_commands
            if cmd == ["/usr/bin/hyprctl", "dispatch", "hypr3d:grab", "down"]
        ]
        self.assertEqual(len(grab_down_cmds), 1, "Analog + digital RT press must dispatch hypr3d:grab down once")

        # Release both analog RT and digital BTN_TR2
        daemon.process_event(EV_ABS, ABS_RZ, 0, now=100.5)
        daemon.process_event(EV_KEY, BTN_TR2, 0, now=100.5)
        daemon.process_event(EV_SYN, SYN_REPORT, 0, now=100.5)

        self.assertFalse(daemon.rt_grab_active, "RT release must clear rt_grab_active")
        grab_up_cmds = [
            cmd for cmd in daemon.dispatched_commands
            if cmd == ["/usr/bin/hyprctl", "dispatch", "hypr3d:grab", "up"]
        ]
        self.assertEqual(len(grab_up_cmds), 1, "RT release must dispatch hypr3d:grab up once")

    def test_lt_resize_button_and_analog_trigger_positive_and_dedup(self):
        """Positive control: LT (BTN_TL2 and ABS_Z) dispatches hypr3d:resize down/up without duplicate dispatches."""
        cfg = ControllerConfig(trigger_threshold=0.30)
        daemon = GamepadHypr3dDaemon(config=cfg, dry_run=True)
        daemon.hypr3d_active = True

        # Press analog LT (ABS_Z=255) + digital BTN_TL2=1
        daemon.process_event(EV_ABS, ABS_Z, 255, now=110.0)
        daemon.process_event(EV_KEY, BTN_TL2, 1, now=110.0)
        daemon.process_event(EV_SYN, SYN_REPORT, 0, now=110.0)

        self.assertTrue(daemon.lt_resize_active, "LT press must activate lt_resize_active")
        resize_down_cmds = [
            cmd for cmd in daemon.dispatched_commands
            if cmd == ["/usr/bin/hyprctl", "dispatch", "hypr3d:resize", "down"]
        ]
        self.assertEqual(len(resize_down_cmds), 1, "Analog + digital LT press must dispatch hypr3d:resize down once")

        # Release both analog LT and digital BTN_TL2
        daemon.process_event(EV_ABS, ABS_Z, 0, now=110.5)
        daemon.process_event(EV_KEY, BTN_TL2, 0, now=110.5)
        daemon.process_event(EV_SYN, SYN_REPORT, 0, now=110.5)

        self.assertFalse(daemon.lt_resize_active, "LT release must clear lt_resize_active")
        resize_up_cmds = [
            cmd for cmd in daemon.dispatched_commands
            if cmd == ["/usr/bin/hyprctl", "dispatch", "hypr3d:resize", "up"]
        ]
        self.assertEqual(len(resize_up_cmds), 1, "LT release must dispatch hypr3d:resize up once")

    def test_rt_grab_and_lt_resize_suppressed_when_inactive(self):
        """Negative control: RT grab and LT resize must NOT dispatch when Hypr3D is inactive."""
        cfg = ControllerConfig(trigger_threshold=0.30)
        daemon = GamepadHypr3dDaemon(config=cfg, dry_run=True)
        daemon.hypr3d_active = False

        daemon.process_event(EV_ABS, ABS_RZ, 255, now=120.0)
        daemon.process_event(EV_KEY, BTN_TR2, 1, now=120.0)
        daemon.process_event(EV_ABS, ABS_Z, 255, now=120.0)
        daemon.process_event(EV_KEY, BTN_TL2, 1, now=120.0)
        daemon.process_event(EV_SYN, SYN_REPORT, 0, now=120.0)

        self.assertFalse(daemon.rt_grab_active, "NEGATIVE CONTROL FAILED: rt_grab_active set while Hypr3D inactive!")
        self.assertFalse(daemon.lt_resize_active, "NEGATIVE CONTROL FAILED: lt_resize_active set while Hypr3D inactive!")
        self.assertEqual(
            len(daemon.dispatched_commands),
            0,
            f"NEGATIVE CONTROL FAILED: Trigger commands dispatched while inactive: {daemon.dispatched_commands}",
        )

    def test_continuous_stick_centering_within_poll_ms_dispatches_stop_immediately(self):
        """Positive control: stick centering within poll_ms (< 10ms) must immediately dispatch hypr3d:move stop."""
        cfg = ControllerConfig(poll_ms=50, left_stick_mode="continuous", deadzone=0.15)
        daemon = GamepadHypr3dDaemon(
            config=cfg,
            dry_run=True,
            dry_run_success_map={"hypr3d:move": True},
        )
        daemon.hypr3d_active = True

        # Deflect at t=200.0
        daemon.process_event(EV_ABS, ABS_X, 25000, now=200.0)
        daemon.process_event(EV_SYN, SYN_REPORT, 0, now=200.0)
        self.assertTrue(daemon.active_continuous_move)

        # Center 2ms later (well inside 50ms poll_ms window)
        daemon.process_event(EV_ABS, ABS_X, 0, now=200.002)
        daemon.process_event(EV_SYN, SYN_REPORT, 0, now=200.002)
        self.assertFalse(daemon.active_continuous_move, "Stick centering inside poll_ms must still clear active_continuous_move")
        self.assertEqual(
            daemon.dispatched_commands[-1],
            ["/usr/bin/hyprctl", "dispatch", "hypr3d:move", "stop"],
            "Stick centering inside poll_ms must immediately dispatch hypr3d:move stop",
        )

    def test_live_lua_translation_for_hyprctl_v0_56(self):
        """Positive control: _translate_live_hyprctl converts hypr3d:* dispatches into Lua eval expressions."""
        daemon = GamepadHypr3dDaemon(config=ControllerConfig(), dry_run=True)

        self.assertEqual(
            daemon._translate_live_hyprctl(["/usr/bin/hyprctl", "dispatch", "hypr3d:move", "stop"]),
            ["/usr/bin/hyprctl", "eval", "hl.plugin.hypr3d.move(0.0, 0.0)"],
        )
        self.assertEqual(
            daemon._translate_live_hyprctl(["/usr/bin/hyprctl", "dispatch", "hypr3d:move", "0.5000", "-0.7500"]),
            ["/usr/bin/hyprctl", "eval", "hl.plugin.hypr3d.move(0.5, -0.75)"],
        )
        self.assertEqual(
            daemon._translate_live_hyprctl(["/usr/bin/hyprctl", "dispatch", "hypr3d:camera", "12.50", "-4.25"]),
            ["/usr/bin/hyprctl", "eval", "hl.plugin.hypr3d.camera(12.500, -4.250)"],
        )
        self.assertEqual(
            daemon._translate_live_hyprctl(["/usr/bin/hyprctl", "dispatch", "hypr3d:grab", "down"]),
            ["/usr/bin/hyprctl", "eval", "hl.plugin.hypr3d.grab('down')"],
        )
        self.assertEqual(
            daemon._translate_live_hyprctl(["/usr/bin/hyprctl", "dispatch", "hypr3d:resize", "up"]),
            ["/usr/bin/hyprctl", "eval", "hl.plugin.hypr3d.resize('up')"],
        )
        self.assertEqual(
            daemon._translate_live_hyprctl(["/usr/bin/hyprctl", "dispatch", "hypr3d:view", "cycle"]),
            ["/usr/bin/hyprctl", "eval", "hl.plugin.hypr3d.view('cycle')"],
        )
        self.assertEqual(
            daemon._translate_live_hyprctl(["/usr/bin/hyprctl", "dispatch", "hypr3d:mode", "toggle"]),
            ["/usr/bin/hyprctl", "eval", "hl.plugin.hypr3d.mode('toggle')"],
        )

    def test_select_view_cycle_and_start_mode_toggle_positive_and_negative(self):
        """Two-sided controls for SELECT camera view cycle and START cursor/camera mode toggle."""
        views = []
        modes = []
        daemon = GamepadHypr3dDaemon(
            config=ControllerConfig(),
            dry_run=True,
            view_callback=lambda: views.append(True),
            mode_callback=lambda: modes.append(True),
        )

        # Negative control: suppressed when Hypr3D is inactive
        daemon.hypr3d_active = False
        daemon.process_event(EV_KEY, BTN_SELECT, 1, now=300.0)
        daemon.process_event(EV_KEY, BTN_SELECT, 0, now=300.1)
        daemon.process_event(EV_KEY, BTN_START, 1, now=300.2)
        daemon.process_event(EV_KEY, BTN_START, 0, now=300.3)
        self.assertEqual(len(views), 0, "SELECT must not cycle view when Hypr3D is inactive")
        self.assertEqual(len(modes), 0, "START must not toggle mode when Hypr3D is inactive")

        # Positive control: dispatches hypr3d:view cycle and hypr3d:mode toggle when active, with hold suppression
        daemon.hypr3d_active = True
        daemon.process_event(EV_KEY, BTN_SELECT, 1, now=301.0)
        daemon.process_event(EV_KEY, BTN_SELECT, 1, now=301.05)  # hold repeat must be ignored
        daemon.process_event(EV_KEY, BTN_SELECT, 0, now=301.1)
        self.assertEqual(len(views), 1, "SELECT press must trigger view cycle once")
        self.assertEqual(daemon.select_seq, 1)
        self.assertIn(
            ["/usr/bin/hyprctl", "dispatch", "hypr3d:view", "cycle"],
            daemon.dispatched_commands,
        )

        daemon.process_event(EV_KEY, BTN_START, 1, now=302.0)
        daemon.process_event(EV_KEY, BTN_START, 1, now=302.05)  # hold repeat must be ignored
        daemon.process_event(EV_KEY, BTN_START, 0, now=302.1)
        self.assertEqual(len(modes), 1, "START press must trigger cursor/camera mode toggle once")
        self.assertEqual(daemon.start_seq, 1)
        self.assertIn(
            ["/usr/bin/hyprctl", "dispatch", "hypr3d:mode", "toggle"],
            daemon.dispatched_commands,
        )

    def test_jump_double_press_fly_and_posture_positive_and_negative(self):
        """Two-sided controls for BTN_SOUTH jump / double-press fly mode and BTN_WEST posture cycle."""
        jumps = []
        flies = []
        postures = []
        daemon = GamepadHypr3dDaemon(
            config=ControllerConfig(double_press_ms=350),
            dry_run=True,
            jump_callback=lambda: jumps.append(True),
            fly_callback=lambda: flies.append(True),
            posture_callback=lambda: postures.append(True),
        )

        # Negative control: suppressed when Hypr3D is inactive
        daemon.hypr3d_active = False
        daemon.process_event(EV_KEY, BTN_SOUTH, 1, now=400.0)
        daemon.process_event(EV_KEY, BTN_SOUTH, 0, now=400.05)
        daemon.process_event(EV_KEY, BTN_WEST, 1, now=400.1)
        daemon.process_event(EV_KEY, BTN_WEST, 0, now=400.15)
        self.assertEqual(len(jumps), 0)
        self.assertEqual(len(flies), 0)
        self.assertEqual(len(postures), 0)

        # Positive control: single press of BTN_SOUTH triggers jump, not fly
        daemon.hypr3d_active = True
        daemon.process_event(EV_KEY, BTN_SOUTH, 1, now=401.0)
        self.assertTrue(daemon.south_btn_down, "south_btn_down must be True while held")
        daemon.process_event(EV_KEY, BTN_SOUTH, 0, now=401.08)
        self.assertFalse(daemon.south_btn_down, "south_btn_down must be False after release")
        self.assertEqual(len(jumps), 1)
        self.assertEqual(len(flies), 0)
        self.assertEqual(daemon.south_seq, 1)

        # Negative control: second press after 500ms (> 350ms double_press_ms) triggers second jump, NOT fly
        daemon.process_event(EV_KEY, BTN_SOUTH, 1, now=401.60)
        daemon.process_event(EV_KEY, BTN_SOUTH, 0, now=401.65)
        self.assertEqual(len(jumps), 2, "Second press outside double_press_ms must jump again")
        self.assertEqual(len(flies), 0, "Second press outside double_press_ms must not toggle fly")

        # Positive control: double press within 350ms (e.g. 120ms apart) toggles fly mode
        daemon.process_event(EV_KEY, BTN_SOUTH, 1, now=402.50)
        daemon.process_event(EV_KEY, BTN_SOUTH, 0, now=402.55)
        daemon.process_event(EV_KEY, BTN_SOUTH, 1, now=402.62)
        daemon.process_event(EV_KEY, BTN_SOUTH, 0, now=402.67)
        self.assertEqual(len(flies), 1, "Double press of BTN_SOUTH within 350ms must toggle fly mode")
        self.assertEqual(daemon.fly_seq, 1)
        self.assertIn(
            ["/usr/bin/hyprctl", "dispatch", "hypr3d:fly", "toggle"],
            daemon.dispatched_commands,
        )

        # Positive control: BTN_WEST cycles posture (Stand -> Crouch -> Sit)
        daemon.process_event(EV_KEY, BTN_WEST, 1, now=403.0)
        self.assertTrue(daemon.west_btn_down)
        daemon.process_event(EV_KEY, BTN_WEST, 0, now=403.05)
        self.assertFalse(daemon.west_btn_down)
        self.assertEqual(len(postures), 1)
        self.assertEqual(daemon.west_seq, 1)
        self.assertIn(
            ["/usr/bin/hyprctl", "dispatch", "hypr3d:posture", "cycle"],
            daemon.dispatched_commands,
        )

    def test_fullscreen_and_shaderglass_bumpers_and_unified_stick_during_grab(self):
        """Two-sided controls for BTN_NORTH fullscreen, BTN_TR/BTN_TL ShaderGlass bumpers, and Left Stick walking while grabbing."""
        fs_calls = []
        sg_toggles = []
        sg_cycles = []
        moves = []
        daemon = GamepadHypr3dDaemon(
            config=ControllerConfig(),
            dry_run=True,
            fullscreen_callback=lambda: fs_calls.append(True),
            shaderglass_toggle_callback=lambda: sg_toggles.append(True),
            shaderglass_cycle_callback=lambda: sg_cycles.append(True),
            move_callback=lambda x, y: moves.append((x, y)),
        )
        daemon.hypr3d_active = True

        # Positive control: BTN_NORTH triggers hypr3d:fullscreen
        daemon.process_event(EV_KEY, BTN_NORTH, 1, now=500.0)
        daemon.process_event(EV_KEY, BTN_NORTH, 0, now=500.05)
        self.assertEqual(len(fs_calls), 1)
        self.assertEqual(daemon.north_seq, 1)
        self.assertIn(
            ["/usr/bin/hyprctl", "dispatch", "hypr3d:fullscreen"],
            daemon.dispatched_commands,
        )

        # Positive control: BTN_TR (R1/RB) toggles ShaderGlass CRT, BTN_TL (L1/LB) cycles ShaderGlass preset
        daemon.process_event(EV_KEY, BTN_TR, 1, now=501.0)
        daemon.process_event(EV_KEY, BTN_TR, 0, now=501.05)
        self.assertEqual(len(sg_toggles), 1)
        self.assertEqual(daemon.rb_seq, 1)
        self.assertIn(
            ["/usr/bin/hyprctl", "dispatch", "hypr3d:crt", "toggle"],
            daemon.dispatched_commands,
        )

        daemon.process_event(EV_KEY, BTN_TL, 1, now=502.0)
        daemon.process_event(EV_KEY, BTN_TL, 0, now=502.05)
        self.assertEqual(len(sg_cycles), 1)
        self.assertEqual(daemon.lb_seq, 1)
        self.assertIn(
            ["/usr/bin/hyprctl", "dispatch", "hypr3d:crt", "cycle"],
            daemon.dispatched_commands,
        )

        # Positive control: Left Stick continues walking while holding RT grab
        daemon.process_event(EV_KEY, BTN_TR2, 1, now=503.0)
        self.assertTrue(daemon.rt_grab_active)
        daemon.process_event(EV_ABS, ABS_Y, -25000, now=503.1)
        daemon.process_event(EV_SYN, SYN_REPORT, 0, now=503.1)
        self.assertEqual(len(moves), 1, "Left stick must walk while RT grab is held")
        self.assertLess(moves[0][1], 0.0)

    def test_vr_walk_bob_disabled_and_mios_global_notifications_routing(self):
        """Two-sided controls for VR-native walk_bob=false and MiOS Global Notification glass island routing."""
        from pathlib import Path
        import tomllib

        repo_root = Path(__file__).resolve().parents[1]
        with open(repo_root / "usr/share/mios/mios.toml", "rb") as f:
            ssot = tomllib.load(f)

        # 1. Positive & Negative control: SSOT [desktop.hyprland.hypr3d.config.player] walk_bob == False
        player_cfg = ssot["desktop"]["hyprland"]["hypr3d"]["config"].get("player", {})
        self.assertIs(
            player_cfg.get("walk_bob"),
            False,
            "VR-native locomotion requires [desktop.hyprland.hypr3d.config.player] walk_bob = false",
        )

        # 2. Positive & Negative control: hypr3d-mios-crt.patch defaults g_cfgWalkBob = false and routes notify() to MiOS Global Notifications
        patch_text = (repo_root / "usr/share/mios/hyprland/patches/hypr3d-mios-crt.patch").read_text(encoding="utf-8")
        self.assertIn("g_cfgWalkBob       = false;", patch_text)
        self.assertNotIn("+static bool        g_cfgWalkBob       = true;", patch_text)
        self.assertIn("if (!g_cfgWalkBob)", patch_text)
        self.assertIn("/usr/bin/notify-send -p -a 'Hypr3D'", patch_text)
        self.assertIn("call notifications post", patch_text)

        # 3. Positive & Negative control: mios-pacing hooks CNotificationOverlay::addNotification & draw and dismisses default Hyprland corner overlay
        pacing_src = (repo_root / "usr/share/mios/hyprland/plugins/mios-pacing/main.cpp").read_text(encoding="utf-8")
        self.assertIn("CNotificationOverlay::addNotification(", pacing_src)
        self.assertIn("CNotificationOverlay::draw(", pacing_src)
        self.assertIn("self->dismissNotifications(-1);", pacing_src)
        self.assertIn("call notifications post", pacing_src)



@unittest.skipUnless(os.environ.get("MIOS_H3D_TEST_RUNTIME"), "isolated compositor not provided")
class TestNativeSessionTransitions(unittest.TestCase):
    """Exercise actual compositor transitions; never target the operator's session."""

    def test_controller_modes_and_fullscreen_return_keep_the_session_alive(self):
        import json
        import pathlib
        import struct
        import subprocess
        import time

        runtime = pathlib.Path(os.environ["MIOS_H3D_TEST_RUNTIME"]).resolve()
        signature = os.environ["MIOS_H3D_TEST_INSTANCE"]
        status = pathlib.Path(os.environ["MIOS_H3D_TEST_STATUS"]).resolve()
        packet = pathlib.Path(os.environ["MIOS_H3D_TEST_PACKET"]).resolve()
        self.assertNotEqual(str(runtime), os.environ.get("XDG_RUNTIME_DIR"))
        self.assertNotEqual(str(runtime), "/run/user/" + str(os.getuid()))
        self.assertEqual(packet.parent, status.parent)
        self.assertNotEqual(str(packet.parent), "/tmp")
        env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime),
                   WAYLAND_DISPLAY=os.environ["MIOS_H3D_TEST_WAYLAND"],
                   HYPRLAND_INSTANCE_SIGNATURE=signature,
                   DBUS_SESSION_BUS_ADDRESS="unix:path=" + str(runtime / "bus"))

        def hc(*args):
            return subprocess.check_output(["hyprctl", "-i", signature, *args], env=env,
                                           text=True, stderr=subprocess.STDOUT, timeout=10)

        self.assertEqual([i["instance"] for i in json.loads(hc("instances", "-j"))], [signature])
        monitors = json.loads(hc("monitors", "-j"))
        self.assertTrue(monitors)
        self.assertTrue(all(m["name"].startswith(("HEADLESS-", "WAYLAND-")) for m in monitors))
        self.assertFalse(json.loads(hc("clients", "-j")), "nest must contain no unrelated clients")
        seq = [0] * 8

        def send():
            value = struct.pack("<IIqffffBBBBIIIIIIII", 0x4D493344, 1, time.monotonic_ns(),
                                0., 0., 0., 0., 0, 0, 0, 0, *seq)
            tmp = packet.with_suffix(".test-tmp")
            tmp.write_bytes(value)
            tmp.replace(packet)

        def last(field):
            import re
            found = re.findall(r"\b" + field + r"=(\d+)", status.read_text()[-20000:])
            self.assertTrue(found, field)
            return int(found[-1])

        send()
        client = subprocess.Popen(["alacritty", "--class", "mios-session-regression",
                                   "--option", "window.opacity=0.75", "-e", "sleep", "120"],
                                  env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(1)
            windows = json.loads(hc("clients", "-j"))
            self.assertEqual(len(windows), 1)
            self.assertEqual(windows[0]["class"], "mios-session-regression")
            hc("eval", "hl.plugin.hypr3d.open()")
            time.sleep(1.5)
            for field, index, modulus in (("kbMode", 1, 2), ("viewMode", 0, 3)):
                before = last(field)
                seq[index] += 1
                send()
                time.sleep(.6)
                self.assertEqual(last(field), (before + 1) % modulus, field)
            hc("eval", 'hl.plugin.hypr3d.mode("camera")')
            for _ in range(3):
                hc("eval", "hl.plugin.hypr3d.fullscreen()")
                time.sleep(1)
                self.assertEqual(json.loads(hc("clients", "-j"))[0]["fullscreen"], 2)
                hc("eval", "hl.plugin.hypr3d.toggle()")
                time.sleep(1)
                hc("eval", "assert(hl.plugin.hypr3d.active())")
                self.assertEqual(json.loads(hc("clients", "-j"))[0]["fullscreen"], 0)
        finally:
            try:
                hc("eval", "hl.plugin.hypr3d.close()")
                time.sleep(1)
            finally:
                client.terminate()
                client.wait(timeout=5)

if __name__ == "__main__":
    # Regression test suite for Milestone 4 Hypr3D controller support
    unittest.main()
