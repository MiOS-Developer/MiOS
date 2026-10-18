#!/usr/bin/env python3
# AI-hint: Mobile window-controls tabs = the desktop controls tab (hyprbars overlay, MiOS patch) split around the status
# pill: the SSOT table [desktop.hyprland.hyprbars.mobile] (touch target >= 44 logical px, palette references, the close
# button index really closes, the menu action toggles the shell's window menu), the plugin patch (hyprbars:mobile rule,
# the mobile_* plugin keys, tabs always raised, strip = touch height, whole tab = hit area, same glass path as the
# desktop tab), and a geometry model of the plugin/Glass.qml rules on a phone (tabs touch-sized, narrow, clear of the
# pill, inset like the desktop tab). Positive and negative controls in every class; nothing touches the live desktop.
# Opt-in: MIOS_HYPRBARS_SO=<built hyprbars.so> checks the build exports the mobile rule and keys.
# AI-related: usr/share/mios/mios.toml [desktop.hyprland.hyprbars.mobile], usr/share/mios/hyprland/patches/hyprbars-overlay.patch,
# usr/share/mios/quickshell/Glass.qml, usr/share/mios/quickshell/ControlsTab.qml, usr/share/mios/quickshell/PanelWindow.qml
"""Mobile controls tab tests."""

from __future__ import annotations

import copy
import os
import re
import subprocess
import tomllib
import unittest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SSOT = os.environ.get("MIOS_SSOT") or os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
PATCH = os.environ.get("MIOS_HYPRBARS_PATCH") or os.path.join(ROOT, "usr", "share", "mios", "hyprland", "patches", "hyprbars-overlay.patch")
TOUCH_MIN = 44  # finger-sized target, logical px (HIG 44 pt / Material 48 dp)


def ssot() -> dict:
    with open(SSOT, "rb") as f:
        return tomllib.load(f)


def mobile_errors(d: dict) -> list[str]:
    bars = d["desktop"]["hyprland"]["hyprbars"]
    m = bars.get("mobile")
    if not isinstance(m, dict):
        return ["[desktop.hyprland.hyprbars.mobile] missing"]
    errs = []
    t = m.get("touch_target")
    if not isinstance(t, int) or t < TOUCH_MIN:
        errs.append(f"touch_target {t!r} < {TOUCH_MIN}")
    for k in ("menu_palette", "menu_icon_palette"):
        if m.get(k) not in d.get("colors", {}):
            errs.append(f"{k} {m.get(k)!r} is not a [colors] key")
    buttons = bars.get("buttons", [])
    i = m.get("close_button")
    if not isinstance(i, int) or not 0 <= i < len(buttons) or not re.search(r"window\.close\b|killactive", buttons[i].get("action", "")):
        errs.append(f"close_button {i!r} does not name the closing button")
    if not re.search(r"\bcall mobile toggleControls$", m.get("menu_action", "")):
        errs.append("menu_action does not toggle the shell's window menu")
    if not isinstance(m.get("menu_icon"), str) or not m["menu_icon"]:
        errs.append("menu_icon empty")
    return errs


class Ssot(unittest.TestCase):
    def test_positive_vendor_table(self):
        self.assertEqual(mobile_errors(ssot()), [])

    def test_negative_bad_values_rejected(self):
        for key, val, frag in (("touch_target", 30, "touch_target"), ("menu_palette", "#B7C9D7", "menu_palette"),
                               ("close_button", 1, "close_button"), ("menu_action", "true", "menu_action")):
            d = copy.deepcopy(ssot())
            d["desktop"]["hyprland"]["hyprbars"]["mobile"][key] = val
            errs = mobile_errors(d)
            self.assertTrue(any(frag in e for e in errs), (key, errs))
        d = copy.deepcopy(ssot())
        del d["desktop"]["hyprland"]["hyprbars"]["mobile"]
        self.assertTrue(mobile_errors(d))


# What the patch must carry for the mobile tabs to be the desktop tab split in two.
PATCH_RULES = {
    "rule": r'registerEffect\("hyprbars:mobile"\)',
    "keys": r'plugin:hyprbars:mobile_touch_target.*\n(?:.*\n)*.*plugin:hyprbars:mobile_menu_action',
    "always_raised": r"revealEnabled\(\) \{\n.*mobileMode\(\)\)\n\s+return false;",
    "touch_height": r"std::max\(H, \(double\)std::max<Config::INTEGER>\(0, g_pGlobalState->config\.mobileTouchTarget->value\(\)\)\)",
    "touch_width": r"std::max\(\(double\)g_pGlobalState->config\.mobileTouchTarget->value\(\), mobileButtonSize\(\) \+ 2\.0 \* PAD\)",
    "same_glass": r"drawTab\(STRIP\.x \+ TABS\[i\]\.x, TABS\[i\]\.w, texBox\)",
    "whole_tab_hit": r"coords\.x >= TABS\[1\]\.x && coords\.x <= TABS\[1\]\.x \+ TABS\[1\]\.w",
    "strip_height": r"const double H  = tabHeight\(\);",
    "no_yield": r"if \(mobileMode\(\)\) \{\n\s+// mobile: the status pill",
}


def patch_errors(text: str) -> list[str]:
    added = "\n".join(l[1:] for l in text.splitlines() if l.startswith("+") and not l.startswith("+++"))
    return [name for name, rx in PATCH_RULES.items() if not re.search(rx, added)]


class Patch(unittest.TestCase):
    def read(self) -> str:
        with open(PATCH, encoding="utf-8") as f:
            return f.read()

    def test_positive_patch_has_mobile_tabs(self):
        self.assertEqual(patch_errors(self.read()), [])

    def test_negative_mutations_caught(self):
        text = self.read()
        for name, old, new in (("rule", 'registerEffect("hyprbars:mobile")', 'registerEffect("hyprbars:phone")'),
                               ("always_raised", "|| mobileMode())\n", ")\n"),
                               ("touch_width", "std::max((double)g_pGlobalState->config.mobileTouchTarget->value(), mobileButtonSize() + 2.0 * PAD)",
                                "mobileButtonSize() + 2.0 * PAD")):
            self.assertIn(old, text, name)
            self.assertIn(name, patch_errors(text.replace(old, new)), name)

    @unittest.skipUnless(os.environ.get("MIOS_HYPRBARS_SO"), "built plugin check (MIOS_HYPRBARS_SO=<hyprbars.so>)")
    def test_built_plugin_exports_mobile(self):
        so = os.environ["MIOS_HYPRBARS_SO"]
        with open(so, "rb") as f:
            blob = f.read()
        for s in (b"hyprbars:mobile", b"plugin:hyprbars:mobile_touch_target", b"plugin:hyprbars:mobile_menu_action"):
            self.assertIn(s, blob)
        self.assertNotIn(b"hyprbars:phone", blob)  # negative control: an unregistered name is absent


def phone_geometry(d: dict, out_w: float = 440, gaps_out: float = 6, pill_w: float = 92) -> dict:
    """The plugin's (and Glass.qml's) mobile tab rules for a window on a phone-sized output."""
    bars = d["desktop"]["hyprland"]["hyprbars"]
    cfg, m = bars["config"], bars["mobile"]
    r = d["desktop"]["hyprland"]["config"]["decoration"]["rounding"]
    h = max(cfg["bar_height"], m["touch_target"])
    fillet = min(r / 2, h / 2)
    inset = r + fillet
    w = max(m["touch_target"], bars["buttons"][m["close_button"]]["size"] + 2 * cfg["pill_padding"])
    win_l, win_r = gaps_out, out_w - gaps_out
    left = (win_l + inset, win_l + inset + w)
    right = (win_r - inset - w, win_r - inset)
    pill = ((out_w - pill_w) / 2, (out_w + pill_w) / 2)
    return {"h": h, "w": w, "left": left, "right": right, "pill": pill, "fillet": fillet,
            "desktop_inset": cfg["bar_padding"] - cfg["pill_padding"], "inset": inset}


def geometry_errors(g: dict) -> list[str]:
    errs = []
    if g["w"] < TOUCH_MIN or g["h"] < TOUCH_MIN:
        errs.append(f"tab {g['w']}x{g['h']} under the {TOUCH_MIN} px touch target")
    if g["w"] > 2 * TOUCH_MIN:
        errs.append(f"tab {g['w']} px wide: not narrow")
    if g["left"][1] + g["fillet"] > g["pill"][0] or g["right"][0] - g["fillet"] < g["pill"][1]:
        errs.append("a tab runs under the status pill")
    if abs(g["inset"] - g["desktop_inset"]) > 2:
        errs.append(f"tab inset {g['inset']} differs from the desktop tab's {g['desktop_inset']}")
    return errs


class Geometry(unittest.TestCase):
    def test_positive_phone_tabs(self):
        g = phone_geometry(ssot())
        self.assertEqual(geometry_errors(g), [], g)
        self.assertEqual((g["w"], g["h"]), (44, 44))

    def test_negative_small_or_wide_tabs(self):
        d = copy.deepcopy(ssot())
        d["desktop"]["hyprland"]["hyprbars"]["mobile"]["touch_target"] = 30
        self.assertTrue(any("touch target" in e for e in geometry_errors(phone_geometry(d))))
        d = copy.deepcopy(ssot())
        d["desktop"]["hyprland"]["hyprbars"]["mobile"]["touch_target"] = 160
        errs = geometry_errors(phone_geometry(d))
        self.assertTrue(any("not narrow" in e for e in errs) and any("pill" in e for e in errs), errs)


if __name__ == "__main__":
    unittest.main()
