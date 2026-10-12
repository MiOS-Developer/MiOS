#!/usr/bin/env python3
# AI-hint: Unit and integration tests for Windows Terminal settings.json profile injector.
# AI-related: usr/libexec/mios/win/wt_profile_inject.py, usr/share/mios/mios.toml, usr/libexec/mios/win/unattend_gen.py, usr/share/mios/wsl/terminal-profile.json, etc/wsl-distribution.conf
"""Unit and integration test suite for WindowsTerminalProfileInjector and CLI."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_TARGET_PATH = os.path.join(_ROOT, "usr", "libexec", "mios", "win", "wt_profile_inject.py")

spec = importlib.util.spec_from_file_location("wt_profile_inject", _TARGET_PATH)
if spec and spec.loader:
    wt_profile_inject = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = wt_profile_inject
    spec.loader.exec_module(wt_profile_inject)
else:
    raise ImportError(f"Could not load module from {_TARGET_PATH}")

_VENDOR = os.path.join(_ROOT, "usr", "share", "mios", "mios.toml")

def _env(user_toml=""):
    """The overlay pinned to this tree's vendor tier plus an optional user tier; no host tier."""
    return patch.dict(os.environ, {
        "MIOS_VENDOR_TOML": _VENDOR, "MIOS_VENDOR_TOML_D": os.path.join(_ROOT, "usr", "lib", "mios", "mios.d"),
        "MIOS_HOST_TOML": "/nonexistent/mios.toml", "MIOS_USER_TOML": user_toml or "/nonexistent/user/mios.toml"})

class TestWtProfileInject(unittest.TestCase):
    """Test suite for non-destructive Windows Terminal settings.json merging, palette injection, and CLI."""

    def setUp(self):
        env = _env()
        env.start()
        self.addCleanup(env.stop)
        self.temp_dir = tempfile.TemporaryDirectory(prefix="mios-test-wt-")
        self.settings_path = os.path.join(self.temp_dir.name, "settings.json")

    def test_scheme_uses_ssot_ansi_and_rejects_planted_missing_color(self):
        import copy
        data = copy.deepcopy(wt_profile_inject.mios_toml.load_merged())
        data["colors"]["ansi_0_black"] = "#123456"
        self.assertEqual(wt_profile_inject.color_scheme(data)["black"], "#123456")
        data["colors"]["ansi_0_black"] = "DEVLOOP-PLANTED-NOT-A-COLOR"
        with self.assertRaisesRegex(ValueError, "black is invalid"):
            wt_profile_inject.color_scheme(data)
    def tearDown(self):
        self.temp_dir.cleanup()

    def test_build_mios_profiles(self):
        injector = wt_profile_inject.WindowsTerminalProfileInjector(
            ssh_port=2222,
            ssh_user="mios",
            mock=True,
        )
        profiles = injector.build_mios_profiles()
        self.assertEqual(len(profiles), 3)

        guids = [p.guid for p in profiles]
        self.assertIn(wt_profile_inject.WSL_GUID, guids)
        self.assertIn(wt_profile_inject.SSH_GUID, guids)
        self.assertIn(wt_profile_inject.SERIAL_GUID, guids)

    def test_global_opacity_projects_user_tier_over_opaque_profile_overrides(self):
        user = self._write_user('[theme]\nopacity = 43\nunfocused_opacity = 37\nunfocused_acrylic = false\n')
        with _env(user):
            injector = wt_profile_inject.WindowsTerminalProfileInjector(mock=True)
            settings = {"profiles": {"defaults": {"opacity": 100}, "list": [
                {"name": "Command Prompt", "commandline": "cmd.exe", "opacity": 100,
                 "unfocusedAppearance": {"opacity": 100, "cursorShape": "bar"}}]}}
            injector.merge_profiles(settings, injector.build_mios_profiles())
        for appearance in [settings["profiles"]["defaults"], *settings["profiles"]["list"]]:
            self.assertEqual(appearance["opacity"], 43)
            self.assertEqual(appearance["unfocusedAppearance"]["opacity"], 37)
            self.assertIs(appearance["unfocusedAppearance"]["useAcrylic"], False)
        self.assertEqual(settings["profiles"]["list"][0]["commandline"], "cmd.exe")
        self.assertEqual(settings["profiles"]["list"][0]["unfocusedAppearance"]["cursorShape"], "bar")
        import copy
        data = copy.deepcopy(injector.data)
        data["theme"]["unfocused_opacity"] = 101
        with self.assertRaisesRegex(ValueError, "opacity"):
            wt_profile_inject.WindowsTerminalProfileInjector(data=data)

    def test_merge_profiles_and_schemes_into_existing(self):
        initial_settings = {
            "$schema": "https://aka.ms/terminal-profiles-schema",
            "defaultProfile": "{initial-guid}",
            "profiles": {
                "list": [
                    {
                        "guid": "{initial-guid}",
                        "name": "PowerShell",
                        "commandline": "powershell.exe",
                    }
                ]
            },
            "schemes": [],
        }
        with open(self.settings_path, "w", encoding="utf-8") as f:
            json.dump(initial_settings, f, indent=2)

        injector = wt_profile_inject.WindowsTerminalProfileInjector(
            settings_path=self.settings_path,
            mock=False,
        )
        res = injector.run()

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["profiles_added"], 3)
        self.assertTrue(os.path.exists(self.settings_path))

        with open(self.settings_path, "r", encoding="utf-8") as f:
            updated = json.load(f)

        # Original profile preserved
        self.assertEqual(len(updated["profiles"]["list"]), 4)
        names = [p["name"] for p in updated["profiles"]["list"]]
        self.assertIn("PowerShell", names)
        self.assertIn("MiOS WSL (Development)", names)
        self.assertIn("MiOS Host SSH", names)
        self.assertIn("MiOS Serial Console", names)

        # Scheme injected
        scheme_names = [s["name"] for s in updated["schemes"]]
        self.assertIn(wt_profile_inject.mios_toml.load_merged()["theme"]["terminal"]["scheme_name"], scheme_names)

    def _write_user(self, body):
        path = os.path.join(self.temp_dir.name, "user.toml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        return path

    def test_profiles_carry_padding_and_scrollbar_from_user_tier(self):
        user = self._write_user('[theme]\npadding = "3"\nscrollbar_state = "always"\n[theme.edge]\nwm_gaps_outer_px = 7\n')
        with _env(user):
            injector = wt_profile_inject.WindowsTerminalProfileInjector(settings_path=self.settings_path, mock=False)
            injector.run()
        with open(self.settings_path, "r", encoding="utf-8") as f:
            mios = [p for p in json.load(f)["profiles"]["list"] if p["name"].startswith("MiOS")]
        self.assertEqual(len(mios), 3)
        for p in mios:
            self.assertEqual(p["padding"], "3")
            self.assertEqual(p["scrollbarState"], "always")
            self.assertIs(p["useAcrylic"], True)
            self.assertEqual(p["opacity"], 50)
            self.assertEqual(p["systemBackdrop"], "acrylic")
            self.assertEqual(p["font"], {
                "face": "GeistMono Nerd Font Mono",
                "size": 12,
                "weight": "normal",
            })

    def test_padding_normalised_through_edge_insets(self):
        cases = {"1, 2, 1, 2": "1, 2", "4,4": "4", "1, 2, 3, 4": "1, 2, 3, 4"}
        for raw, want in cases.items():
            data = {"theme": {"padding": raw, "scrollbar_state": "visible"}}
            with self.subTest(raw=raw):
                self.assertEqual(wt_profile_inject.wt_edge(data), (want, "visible"))
        with self.assertRaisesRegex(ValueError, r"\[theme\]\.scrollbar_state"):
            wt_profile_inject.wt_edge({"theme": {"padding": "0", "scrollbar_state": "auto"}})

    def _cli(self, *args):
        with patch.object(sys, "argv", ["wt_profile_inject.py", *args]):
            return wt_profile_inject.main()

    def test_wsl_profile_template_both_sides(self):
        self.assertEqual(self._cli("--check-fixture", _ROOT), 0)
        with open(os.path.join(_ROOT, "etc", "wsl-distribution.conf"), encoding="utf-8") as f:
            self.assertIn(f"profileTemplate = /{wt_profile_inject.GOLDEN}\n", f.read())
        self.assertEqual(self._cli("--write-fixture", self.temp_dir.name), 0)
        copy = os.path.join(self.temp_dir.name, wt_profile_inject.GOLDEN)
        with open(copy, encoding="utf-8") as f:
            text = f.read()
        pad, bar = wt_profile_inject.wt_edge(wt_profile_inject.mios_toml.vendor_tree(_ROOT))
        self.assertEqual(json.loads(text)["profiles"], [{
            "colorScheme": wt_profile_inject.mios_toml.vendor_tree(_ROOT)["theme"]["terminal"]["scheme_name"],
            "font": {
                "face": "GeistMono Nerd Font Mono",
                "size": 12,
                "weight": "normal",
            },
            "padding": pad,
            "scrollbarState": bar,
            "useAcrylic": True,
            "opacity": 50,
            "unfocusedAppearance": {"opacity": 50, "useAcrylic": True},
            "systemBackdrop": "acrylic",
        }])
        with open(copy, "w", encoding="utf-8") as f:
            f.write(text.replace(f'"padding": "{pad}"', '"padding": "8"', 1))
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(self._cli("--check-fixture", self.temp_dir.name), 1)
        self.assertRegex(err.getvalue(), r"terminal-profile\.json:\d+: committed '\s*\"padding\": \"8\",'")

    def test_cli_execution_mock_json(self):
        test_args = [
            "wt_profile_inject.py",
            "--settings-json", self.settings_path,
            "--ssh-port", "2222",
            "--ssh-user", "mios",
            "--mock",
            "--json",
        ]
        with patch.object(sys, "argv", test_args):
            exit_code = wt_profile_inject.main()
            self.assertEqual(exit_code, 0)

def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestWtProfileInject)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1

if __name__ == "__main__":
    sys.exit(main())
