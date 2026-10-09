#!/usr/bin/env python3
# AI-hint: Test suite for task_342: Rust SSOT mios.toml validator (mios check / miosd check).
# AI-related: usr/share/mios/mios.toml, src/mios-rs/mios-config/src/lib.rs

import pathlib
import subprocess
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKSPACE = ROOT / "src" / "mios-rs"

def _cargo(*args):
    """Run cargo in the workspace on any platform.

    These tests used to shell into a WSL distro by name and cd to /mnt/c/MiOS,
    so they only ever ran on one machine. Skip when there is no toolchain
    rather than letting its absence look like a pass.
    """
    import shutil
    if shutil.which("cargo") is None:
        raise unittest.SkipTest("cargo is not on PATH; cannot exercise the validator")
    return subprocess.run(list(args), cwd=str(WORKSPACE), capture_output=True, text=True)

class TestMiosCheckSSOT(unittest.TestCase):
    def test_validator_unit_tests_pass(self):
        """Run the validator's own unit tests over its synthetic fixtures.

        Named for what it does: these are the crate's fixtures, NOT the live
        mios.toml. The old name claimed the shipped SSOT was being validated
        while asserting only on cargo output, which is the kind of gap this
        repo's gates exist to catch.
        """
        ssot_file = ROOT / "usr/share/mios/mios.toml"
        self.assertTrue(ssot_file.is_file(), f"SSOT file missing at {ssot_file}")

        res = _cargo("cargo", "test", "-p", "mios-config", "--lib", "--", "validator")
        self.assertEqual(
            res.returncode, 0,
            f"Cargo test for mios-config validator failed:\nstdout: {res.stdout}\nstderr: {res.stderr}"
        )
        self.assertIn("test validator::tests::test_valid_ssot_toml ... ok", res.stdout)
        self.assertIn("test validator::tests::test_port_collision_detection ... ok", res.stdout)
        self.assertIn("test validator::tests::test_law7_empty_string_rejection ... ok", res.stdout)
        self.assertIn("test validator::tests::test_ratchet_violation ... ok", res.stdout)

    def test_negative_port_collision_detection(self):
        """Verify that duplicate port assignments in [ports] are detected and reported."""
        sample_toml = """
[meta]
mios_version = "0.3.0"
fedora_version = "44"

[identity]
username = "mios"
fullname = "MiOS Operator"
hostname = "mios"
shell = "/bin/bash"

[ports]
service_alpha = 9090
service_beta = 9090
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
            f.write(sample_toml)
            temp_path = f.name

        try:
            # Run miosd check on the invalid temp file
            res = _cargo("cargo", "run", "-q", "-p", "miosd", "--", "check", temp_path)
            self.assertNotEqual(res.returncode, 0, "Validator should fail on port collision")
            self.assertIn("Port Collision", res.stdout + res.stderr)
        finally:
            pathlib.Path(temp_path).unlink(missing_ok=True)

    def test_negative_law7_empty_string_rejection(self):
        """Verify that empty string literals in critical configuration sections are rejected under Law 7."""
        sample_toml = """
[meta]
mios_version = ""
fedora_version = "44"

[identity]
username = "   "
fullname = "MiOS Operator"
hostname = "mios"
shell = "/bin/bash"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
            f.write(sample_toml)
            temp_path = f.name

        try:
            res = _cargo("cargo", "run", "-q", "-p", "miosd", "--", "check", temp_path)
            self.assertNotEqual(res.returncode, 0, "Validator should fail on empty string literal")
            self.assertIn("Law 7 Violation", res.stdout + res.stderr)
        finally:
            pathlib.Path(temp_path).unlink(missing_ok=True)

if __name__ == "__main__":
    unittest.main()
