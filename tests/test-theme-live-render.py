#!/usr/bin/env python3
# AI-hint: Automated unit test suite for multi-surface live theme rendering and DBus/socket broadcast (task_443, task_444).
# AI-doc: usr/share/doc/mios/manual/ch68-living-wallpaper-shaders-and-ssot-theme-engine.md
from __future__ import annotations

import configparser
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_RENDER_BIN = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-theme-render")
_BROADCAST_BIN = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-theme-broadcast")
_loader = importlib.machinery.SourceFileLoader("mios_theme_live_render", _RENDER_BIN)
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
_theme = importlib.util.module_from_spec(_spec)
_loader.exec_module(_theme)


class TestThemeLiveRender(unittest.TestCase):
    """Validates mios-theme-render and mios-theme-broadcast."""

    def test_library_path_and_layered_palette(self):
        self.assertEqual(_theme._LIB, os.path.join(_ROOT, "usr", "lib", "mios"))
        with mock.patch.object(_theme.mios_toml, "load_merged", return_value={"colors": {"bg": "#123456"}}):
            self.assertEqual(_theme.resolve_colors()["bg"], "#123456")

    def test_modern_gtk_palette_and_negative_control(self):
        palette = _theme.mios_toml.colors({"colors": {"bg": "#123456", "accent": "#654321"}})
        css = _theme.render_gtk_css(palette)
        needle = "--window-bg-color: #123456;"
        self.assertIn(needle, css)
        self.assertIn("--accent-bg-color: #654321;", css)
        with self.assertRaises(AssertionError):
            self.assertIn(needle, css.replace(needle, "--window-bg-color: #000000;"))
        self.assertNotIn(":root", _theme.render_gtk_css(palette, edge_import=False))

    def test_flatpak_projection_preserves_user_css_and_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            host = home / ".config"
            app = home / ".var/app/org.gnome.Epiphany/config"
            gtk = app / "gtk-4.0"
            gtk.mkdir(parents=True)
            (gtk / "gtk.css").write_text("label { font-weight: bold; }\n")
            (gtk / "settings.ini").write_text("[Settings]\ngtk-enable-animations=false\n")
            data = {"appearance": {"gtk_theme": "operator-dark"}, "theme": {
                "font": {"family": "Operator Font", "size": 14},
                "cursor_linux": {"theme": "Operator-Cursor", "size": 32}}}
            expand = lambda value: str(home) + value[1:] if value.startswith("~") else value
            with mock.patch.object(_theme.os.path, "expanduser", side_effect=expand), \
                 mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(host)}), \
                 mock.patch.object(_theme.mios_toml, "load_merged", return_value=data):
                roots = _theme.config_dirs("org.gnome.Epiphany")
                _theme.apply_gtk(_theme.mios_toml.colors(data), roots=roots)
                _theme.apply_gtk(_theme.mios_toml.colors(data), roots=roots)
            css = (gtk / "gtk.css").read_text()
            self.assertEqual(css.count('@import url("mios-colors.css");'), 1)
            self.assertIn("label { font-weight: bold; }", css)
            settings = configparser.ConfigParser()
            settings.read(gtk / "settings.ini")
            self.assertEqual(settings["Settings"]["gtk-font-name"], "Operator Font 14")
            self.assertEqual(settings["Settings"]["gtk-cursor-theme-size"], "32")
            self.assertEqual(settings["Settings"]["gtk-enable-animations"], "false")
            self.assertTrue((host / "gtk-3.0/mios-colors.css").is_file())

    def test_flatpak_id_rejects_path_escape(self):
        with self.assertRaisesRegex(ValueError, "Invalid Flatpak"):
            _theme.config_dirs("../outside/target")

    def test_binaries_exist_and_executable(self):
        self.assertTrue(os.path.isfile(_RENDER_BIN), f"Missing {_RENDER_BIN}")
        self.assertTrue(os.access(_RENDER_BIN, os.X_OK), f"Not executable {_RENDER_BIN}")
        self.assertTrue(os.path.isfile(_BROADCAST_BIN), f"Missing {_BROADCAST_BIN}")
        self.assertTrue(os.access(_BROADCAST_BIN, os.X_OK), f"Not executable {_BROADCAST_BIN}")

    def test_theme_render_dry_run_json(self):
        res = subprocess.run(
            [sys.executable, _RENDER_BIN, "--dry-run", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "success")
        self.assertTrue(data.get("dry_run"))

        colors = data.get("colors", {})
        self.assertIn("bg", colors)
        self.assertIn("fg", colors)
        self.assertIn("accent", colors)

        surfaces = data.get("surfaces", {})
        self.assertIn("gtk", surfaces)
        self.assertIn("qt", surfaces)
        self.assertIn("pty", surfaces)

        # Check GTK targets
        gtk_targets = surfaces["gtk"]
        self.assertTrue(any("gtk-4.0" in p for p in gtk_targets))
        self.assertTrue(any("gtk-3.0" in p for p in gtk_targets))

    def test_theme_broadcast_dry_run_json(self):
        res = subprocess.run(
            [sys.executable, _BROADCAST_BIN, "--dry-run", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "success")
        self.assertIn(data.get("color_scheme"), ["prefer-dark", "default"])
        self.assertTrue(data.get("is_dark"))

        dbus_res = data.get("dbus", {})
        self.assertEqual(dbus_res.get("color_scheme"), "prefer-dark")
        self.assertTrue(dbus_res.get("dry_run"))

        sock_res = data.get("wallpaper_socket", {})
        self.assertIn("mios-wallpaper", sock_res.get("socket_path", ""))
        self.assertTrue(sock_res.get("dry_run"))

        self.assertTrue(data.get("theme_render_triggered"))


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestThemeLiveRender)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
