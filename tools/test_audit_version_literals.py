#!/usr/bin/env python3
# AI-hint: Black-box controls for the native version gate replacing the retired Python scanner.
# AI-related: src/mios-rs/mios-gate/src/version_literals.rs
# AI-doc: usr/share/doc/mios/manual/tools.md

import json
import os
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

class TestAuditVersionLiterals(unittest.TestCase):
    def test_native_scan_rejects_divergence_and_unavailable_corpus(self):
        binary = os.environ.get("MIOS_GATE_BIN") or shutil.which("mios-gate")
        self.assertTrue(binary, "native mios-gate is required")
        vendor = Path(__file__).resolve().parents[1] / "usr/share/mios/mios.toml"
        version = tomllib.loads(vendor.read_text())["meta"]["mios_version"]
        env = {key: value for key, value in os.environ.items() if not key.startswith("MIOS_")}
        with tempfile.TemporaryDirectory(prefix="mios-version-control-") as directory:
            root = Path(directory)
            ssot = root / "usr/share/mios/mios.toml"
            ssot.parent.mkdir(parents=True)
            ssot.write_text(f"[meta]\nmios_version={json.dumps(version)}\n")
            subject = root / "tools/consumer.sh"
            subject.parent.mkdir()
            subject.write_text(f"echo {version}\n")
            subprocess.run(["git", "init", "-q", directory], check=True)
            subprocess.run(["git", "-C", directory, "add", "."], check=True)
            command = [binary, "version-literals-ssot", "--root", directory, "--format", "json"]
            def check(expected, environment=env):
                result = subprocess.run(command, env=environment, capture_output=True, text=True)
                report = json.loads(result.stdout)
                self.assertEqual(result.returncode, expected, report)
                return report
            check(0)
            divergent = "0." + "987.654"
            subject.write_text(f"echo {divergent}\n")
            report = check(1)
            self.assertTrue(any("tools/consumer.sh:1" in finding for finding in report["findings"]))
            subject.write_text(f"echo {version}\n")
            check(0)
            check(2, dict(env, PATH=""))

    def test_native_scan_refuses_non_checkout(self):
        binary = os.environ.get("MIOS_GATE_BIN") or shutil.which("mios-gate")
        self.assertTrue(binary, "native mios-gate is required")
        vendor = Path(__file__).resolve().parents[1] / "usr/share/mios/mios.toml"
        version = tomllib.loads(vendor.read_text())["meta"]["mios_version"]
        with tempfile.TemporaryDirectory(prefix="mios-version-nogit-") as directory:
            root = Path(directory)
            ssot = root / "usr/share/mios/mios.toml"
            ssot.parent.mkdir(parents=True)
            ssot.write_text(f"[meta]\nmios_version={json.dumps(version)}\n")
            command = [binary, "version-literals-ssot", "--root", directory, "--format", "json"]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(report["status"], "could_not_run")
            self.assertIn("not a git checkout", report.get("summary", ""))

if __name__ == "__main__":
    unittest.main()
