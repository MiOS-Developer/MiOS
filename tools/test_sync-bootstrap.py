#!/usr/bin/env python3
# AI-hint: Real native bootstrap-sync CLI fixtures prove read-only checks, convergence, confinement and fail-closed dispatch.
# AI-related: tools/sync-bootstrap.py, tools/native/mios-gen/src/bootstrap_sync.rs, usr/share/mios/mios.toml
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BootstrapMirror(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mios-bootstrap-native-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "main"
        self.boot = Path(self.temp.name) / "boot"
        vendor = self.root / "usr/share/mios"
        vendor.mkdir(parents=True)
        self.boot.mkdir()
        self.authority = vendor / "mios.toml"
        self.authority.write_text('''[bootstrap.sync]
mirror_files = ["shared.txt"]
not_mirrored = ["mios.toml"]
mirror_toml_tables = ["ports"]
mirror_toml_keys = ["theme.padding"]
[ports]
alpha = 9
[theme]
padding = "0"
''', encoding="utf-8")
        (self.root / "shared.txt").write_text("authority\n", encoding="utf-8")
        (self.boot / "shared.txt").write_text("operator old\n", encoding="utf-8")
        (self.boot / "mios.toml").write_text('# keep header\n[ports]\nalpha = 1 # keep comment\n[theme]\npadding = "8"\nkeep = true\n', encoding="utf-8")
        for repo in (self.root, self.boot):
            subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("MIOS_")}
        binary = os.environ.get("MIOS_TEST_GEN") or os.environ.get("MIOS_GEN_BIN") or shutil.which("mios-gen")
        self.assertTrue(binary, "native mios-gen required; absence is not a skipped test")
        self.env["MIOS_GEN_BIN"] = binary

    def call(self, *args, boot=None):
        return subprocess.run([sys.executable, str(ROOT / "tools/sync-bootstrap.py"), "--root", str(self.root),
                               "--bootstrap", str(boot or self.boot), *args],
                              env=self.env, text=True, encoding="utf-8", capture_output=True, timeout=30)

    def snapshot(self):
        return {p.name: p.read_bytes() for p in self.boot.iterdir() if p.is_file()}

    def test_check_then_apply_then_idempotent_check(self):
        before = self.snapshot()
        result = self.call("--check")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("drifted", result.stderr)
        self.assertEqual(self.snapshot(), before)
        result = self.call("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = (self.boot / "mios.toml").read_text(encoding="utf-8")
        self.assertIn("# keep header", text)
        self.assertIn("# keep comment", text)
        data = tomllib.loads(text)
        self.assertEqual(data["ports"]["alpha"], 9)
        self.assertEqual(data["theme"]["padding"], "0")
        self.assertTrue(data["theme"]["keep"])
        after = self.snapshot()
        self.assertEqual(self.call("--check").returncode, 0)
        self.assertEqual(self.call("--apply").returncode, 0)
        self.assertEqual(self.snapshot(), after)

    def test_preflight_fails_before_copy_on_missing_authority(self):
        self.authority.write_text(self.authority.read_text(encoding="utf-8").replace('["shared.txt"]', '["shared.txt", "absent"]'), encoding="utf-8")
        before = self.snapshot()
        self.assertNotEqual(self.call("--apply").returncode, 0)
        self.assertEqual(self.snapshot(), before)

    def test_missing_native_has_no_python_fallback(self):
        self.env["MIOS_GEN_BIN"] = str(self.root / "missing-generator")
        before = self.snapshot()
        result = self.call("--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required native", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_missing_bootstrap_and_conflicting_flags_fail(self):
        self.assertIn("Law 15 NOT checked", self.call("--check", boot=self.root / "absent").stderr)
        before = self.snapshot()
        self.assertNotEqual(self.call("--check", "--apply").returncode, 0)
        self.assertEqual(self.snapshot(), before)

    def test_unclassified_shared_file_blocks_apply(self):
        for repo in (self.root, self.boot):
            (repo / "unknown").write_text("preserve", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "unknown"], check=True, capture_output=True)
        before = self.snapshot()
        result = self.call("--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unclassified", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_real_ssot_declares_install_mode_and_excludes_retired_monitor(self):
        with (ROOT / "usr/share/mios/mios.toml").open("rb") as stream:
            manifest = tomllib.load(stream)["bootstrap"]["sync"]
        self.assertTrue(manifest["mirror_files"])
        self.assertEqual(sorted(manifest["mirror_toml_keys"]),
                         ["bootstrap.windows_install_mode", "network.bind_all", "network.loopback",
                          "network.win_tailscale_ip", "theme.padding", "theme.scrollbar_state"])
        self.assertNotIn("installation/mios-mon.py", manifest["mirror_files"])
        self.assertTrue((ROOT / "usr/libexec/mios/mios-mon.py").is_file())


if __name__ == "__main__":
    unittest.main()
