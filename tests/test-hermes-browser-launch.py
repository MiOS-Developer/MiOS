#!/usr/bin/env python3
# AI-hint: Runs mios-hermes-browser start against a stub session-bus wrapper and asserts the argv it execs: flatpak run with --no-documents-portal (the portal's FUSE mount at $XDG_RUNTIME_DIR/doc cannot succeed under NoNewPrivileges), the profile and runtime dirs shared, the app id, and headless CDP on 127.0.0.1.
# AI-related: usr/libexec/mios/mios-hermes-browser, usr/lib/systemd/system/mios-hermes-browser.service, usr/lib/systemd/system/mios-hermes-browser-worker.service
# AI-doc: usr/share/doc/mios/manual/tests.md
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "usr/libexec/mios/mios-hermes-browser"
# The session-bus wrapper the launcher execs flatpak under, read from the launcher
# itself; the test replaces it with a stub that records argv and starts no bus.
WRAPPER = re.search(r"^exec (\S+) -- flatpak run", LAUNCHER.read_text(), re.M).group(1)


class HermesBrowserLaunch(unittest.TestCase):
    def run_launcher(self, subcmd: str, markers=()):
        """(completed process, recorded argv or None) for one launcher run on a
        host whose blade.d holds `markers` (role-apply's capability files)."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            blade_d = d / "blade.d"
            blade_d.mkdir()
            for m in markers:
                (blade_d / m).touch()
            bin_dir = d / "bin"
            bin_dir.mkdir()
            record = d / "argv"
            # WRAPPER -- CMD...: record CMD's argv, one per line, and exit.
            (bin_dir / WRAPPER).write_text(
                '#!/bin/sh\n[ "$1" = "--" ] && shift\nprintf \'%s\\n\' "$@" > "$RECORD"\n'
            )
            (bin_dir / WRAPPER).chmod(0o755)
            env = {
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "RECORD": str(record),
                "XDG_RUNTIME_DIR": str(d / "run"),
                "HERMES_BROWSER_PROFILE_DIR": str(d / "profile"),
                "HERMES_BROWSER_LOG": str(d / "launch.log"),
                "HERMES_BROWSER_HEADLESS": "1",
                "HERMES_BROWSER_APP_ID": "com.example.Browser",
                "HERMES_BROWSER_CDP_PORT": "9333",
                "BLADE_D": str(blade_d),
            }
            p = subprocess.run(
                ["bash", str(LAUNCHER), subcmd], env=env, timeout=30, capture_output=True, text=True
            )
            return p, (record.read_text().splitlines() if record.exists() else None)

    def launch(self) -> list[str]:
        p, argv = self.run_launcher("start")
        self.assertEqual(p.returncode, 0, p.stderr)
        return argv

    BLADE = ("service-plane",)

    def test_a_blade_without_the_capability_launches_nothing(self):
        for subcmd in ("start", "ensure"):
            p, argv = self.run_launcher(subcmd, self.BLADE)
            self.assertEqual(p.returncode, 3, f"{subcmd}: {p.stdout}{p.stderr}")
            self.assertIn("agent browser disabled on this host", p.stderr)
            self.assertIn("FEATURES=agent-browser", p.stderr)
            self.assertIsNone(argv, f"{subcmd} started the browser on a Blade")

    def test_allowed_is_the_units_exec_condition(self):
        # 0 runs the unit, 1 skips it: a seat and a Blade with the capability
        # run it, a Blade without it does not.
        for markers, rc in (((), 0), (self.BLADE, 1), (self.BLADE + ("agent-browser",), 0)):
            p, argv = self.run_launcher("allowed", markers)
            self.assertEqual(p.returncode, rc, f"{markers}: {p.stdout}{p.stderr}")
            self.assertIsNone(argv)

    def test_a_blade_with_the_capability_still_starts(self):
        p, argv = self.run_launcher("start", self.BLADE + ("agent-browser",))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(argv[:2], ["flatpak", "run"])

    def test_start_runs_flatpak_without_the_documents_portal(self):
        argv = self.launch()
        self.assertEqual(argv[:2], ["flatpak", "run"])
        app = argv.index("com.example.Browser")
        flatpak_opts = argv[2:app]
        self.assertIn("--no-documents-portal", flatpak_opts)

    def test_start_keeps_profile_runtime_and_headless_cdp(self):
        argv = self.launch()
        app = argv.index("com.example.Browser")
        flatpak_opts, chrome = argv[2:app], argv[app + 1 :]
        self.assertTrue(
            any(o.startswith("--filesystem=") and o.endswith("/profile") for o in flatpak_opts)
        )
        self.assertTrue(
            any(o.startswith("--filesystem=") and o.endswith("/run") for o in flatpak_opts)
        )
        self.assertIn("--headless=new", chrome)
        self.assertIn("--remote-debugging-port=9333", chrome)
        self.assertIn("--remote-debugging-address=127.0.0.1", chrome)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)
