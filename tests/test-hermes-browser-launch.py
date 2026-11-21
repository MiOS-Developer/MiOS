#!/usr/bin/env python3
# AI-hint: Runs mios-hermes-browser start against fake dbus-run-session and flatpak binaries and asserts the argv it execs: flatpak run with --no-documents-portal (the portal's FUSE mount at $XDG_RUNTIME_DIR/doc cannot succeed under NoNewPrivileges), the profile and runtime dirs shared, the app id, and headless CDP on 127.0.0.1.
# AI-related: usr/libexec/mios/mios-hermes-browser, usr/lib/systemd/system/mios-hermes-browser.service, usr/lib/systemd/system/mios-hermes-browser-worker.service
# AI-doc: usr/share/doc/mios/manual/tests.md
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "usr/libexec/mios/mios-hermes-browser"


class HermesBrowserLaunch(unittest.TestCase):
    def launch(self) -> list[str]:
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            bin_dir = d / "bin"
            bin_dir.mkdir()
            record = d / "argv"
            # dbus-run-session -- CMD...: record CMD's argv, one per line, and exit.
            (bin_dir / "dbus-run-session").write_text(
                '#!/bin/sh\n[ "$1" = "--" ] && shift\nprintf \'%s\\n\' "$@" > "$RECORD"\n'
            )
            (bin_dir / "dbus-run-session").chmod(0o755)
            env = {
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "RECORD": str(record),
                "XDG_RUNTIME_DIR": str(d / "run"),
                "HERMES_BROWSER_PROFILE_DIR": str(d / "profile"),
                "HERMES_BROWSER_LOG": str(d / "launch.log"),
                "HERMES_BROWSER_HEADLESS": "1",
                "HERMES_BROWSER_APP_ID": "com.example.Browser",
                "HERMES_BROWSER_CDP_PORT": "9333",
            }
            subprocess.run(["bash", str(LAUNCHER), "start"], env=env, check=True, timeout=30)
            return record.read_text().splitlines()

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
