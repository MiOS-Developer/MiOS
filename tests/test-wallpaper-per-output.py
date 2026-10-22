#!/usr/bin/env python3
# AI-hint: Wallpaper host contract: one mios-wallpaper layer per output, including outputs added later
# (headless RDP). Static checks always run; MIOS_LIVE_WALLPAPER_TEST=1 also adds a temporary headless
# output to the running Hyprland session, counts layers per monitor, and removes it.
# AI-related: usr/share/mios/wallpaper/Host.qml, usr/lib/systemd/user/mios-wallpaper.service
"""Per-output wallpaper layer tests (positive and negative controls)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import unittest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
HOST = os.path.join(_ROOT, "usr", "share", "mios", "wallpaper", "Host.qml")


def contract_errors(qml: str) -> list[str]:
    """Why a Host.qml would stack its copies on one output (empty = per-output)."""
    errors = []
    if "Qt.application.screens" not in qml or "Instantiator" not in qml:
        errors.append("one delegate per Qt screen (Instantiator over Qt.application.screens)")
    # Qt re-derives a shown window's screen from its position: (0,0) is always the first output.
    if not re.search(r"^\s*x:\s*modelData\.virtualX\s*$", qml, re.M):
        errors.append("window x must be its output's virtualX")
    if not re.search(r"^\s*y:\s*modelData\.virtualY\s*$", qml, re.M):
        errors.append("window y must be its output's virtualY")
    if re.search(r"^\s*LayerShell\.Window\.screen\s*:", qml, re.M):
        errors.append("LayerShell.Window.screen takes a QScreen; QML only has QQuickScreenInfo (stays null)")
    if re.search(r"^\s*LayerShell\.Window\.screenConfiguration\s*:", qml, re.M):
        errors.append("deprecated screenConfiguration")
    if not re.search(r"LayerShell\.Window\.wantsToBeOnActiveScreen:\s*false", qml):
        errors.append("wantsToBeOnActiveScreen must be false (else the focused output gets every copy)")
    if not re.search(r"^\s*visible:\s*false\s*$", qml, re.M):
        errors.append("windows must be shown only after the screen is assigned")
    return errors


# The pre-fix hosts: the original (screenConfiguration, shown at once) and the untested coordinator
# edit (LayerShell.Window.screen bound to a QQuickScreenInfo, no position).
OLD_HOST = """Instantiator { model: Qt.application.screens
 delegate: Window {
            visible: true
            screen: modelData
            LayerShell.Window.screenConfiguration: LayerShell.Window.ScreenFromQWindow
 } }"""
EDIT_HOST = """Instantiator { model: Qt.application.screens
 delegate: Window {
            visible: false
            screen: modelData
            LayerShell.Window.wantsToBeOnActiveScreen: false
            LayerShell.Window.screen: win.modelData
 } }"""


class StaticContract(unittest.TestCase):
    def test_positive_shipped_host_is_per_output(self):
        with open(HOST, encoding="utf-8") as f:
            self.assertEqual(contract_errors(f.read()), [])

    def test_negative_original_host_is_rejected(self):
        errs = contract_errors(OLD_HOST)
        self.assertTrue(any("virtualX" in e for e in errs), errs)
        self.assertTrue(any("screenConfiguration" in e for e in errs), errs)

    def test_negative_unpositioned_screen_binding_is_rejected(self):
        errs = contract_errors(EDIT_HOST)
        self.assertTrue(any("QQuickScreenInfo" in e for e in errs), errs)
        self.assertTrue(any("virtualY" in e for e in errs), errs)


def _hc(*args: str) -> str:
    return subprocess.run(["hyprctl", *args], capture_output=True, text=True, timeout=10).stdout


def _wallpaper_layers() -> dict[str, int]:
    out: dict[str, int] = {}
    for mon, v in json.loads(_hc("-j", "layers")).items():
        for level in v["levels"].values():
            for layer in level:
                if layer["namespace"] == "mios-wallpaper":
                    out[mon] = out.get(mon, 0) + 1
    return out


def _monitors() -> list[str]:
    return [m["name"] for m in json.loads(_hc("-j", "monitors"))]


@unittest.skipUnless(os.environ.get("MIOS_LIVE_WALLPAPER_TEST") == "1", "live session test (MIOS_LIVE_WALLPAPER_TEST=1)")
class LiveSession(unittest.TestCase):
    OUT = "MIOS-WPTEST"

    def test_hot_added_output_gets_exactly_one_layer(self):
        before = _monitors()
        self.assertNotIn(self.OUT, before)
        # Baseline: one layer per existing output, none for the output not yet added.
        self.assertEqual(_wallpaper_layers(), {m: 1 for m in before})
        _hc("output", "create", "headless", self.OUT)
        try:
            for _ in range(40):
                time.sleep(0.25)
                if _wallpaper_layers().get(self.OUT):
                    break
            time.sleep(1)
            self.assertEqual(_wallpaper_layers(), {m: 1 for m in before + [self.OUT]})
        finally:
            _hc("output", "remove", self.OUT)
            for _ in range(40):
                time.sleep(0.25)
                if self.OUT not in _monitors():
                    break
        time.sleep(1)
        self.assertEqual(_wallpaper_layers(), {m: 1 for m in before})


if __name__ == "__main__":
    unittest.main()
