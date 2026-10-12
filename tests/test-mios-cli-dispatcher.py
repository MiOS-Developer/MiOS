#!/usr/bin/env python3
# AI-hint: Test suite for task_343: Compiled binary CLI dispatcher (/usr/bin/mios).
# AI-related: usr/bin/mios, src/mios-rs/miosd/src/cli.rs

import pathlib
import subprocess
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKSPACE = ROOT / "src" / "mios-rs"

def _cargo(*args):
    """Run a cargo command in the workspace, on whatever platform we are on.

    This suite used to shell into a WSL distro by name ("podman-MiOS-DEV") and
    cd to /mnt/c/MiOS, so it could only pass on one developer's Windows box and
    failed outright on any CI runner. Skip -- loudly -- when there is no
    toolchain, so a missing cargo can never read as a passing dispatcher test.
    """
    import shutil
    if shutil.which("cargo") is None:
        raise unittest.SkipTest("cargo is not on PATH; cannot exercise the miosd CLI")
    return subprocess.run(list(args), cwd=str(WORKSPACE), capture_output=True, text=True)

class TestMiosCliDispatcher(unittest.TestCase):
    def test_cli_help_and_verbs_coverage(self):
        """Verify that CLI prints help and includes all expected verbs."""
        res = _cargo("cargo", "run", "-q", "-p", "miosd", "--", "cli", "--help")
        self.assertEqual(res.returncode, 0, f"Help failed: {res.stdout}\n{res.stderr}")

        output = res.stdout + res.stderr
        expected_verbs = [
            "build", "dash", "mini", "mon", "config", "code", "ai",
            "xbox", "virt", "vfio", "tune", "summary", "profile",
            "assess", "theme", "dotfiles", "new", "iommu", "env",
            "sync-env", "blade", "flatpaks", "user", "flight", "models",
            "update", "check", "status", "logs", "backup"
        ]
        for v in expected_verbs:
            self.assertIn(v, output, f"Verb '{v}' missing from CLI dispatcher help output")

    def test_completion_generation(self):
        """Verify that shell completions are correctly generated for bash, zsh, fish, pwsh."""
        for shell in ["bash", "zsh", "fish", "pwsh"]:
            res = _cargo("cargo", "run", "-q", "-p", "miosd", "--",
                         "cli", "--generate-completion", shell)
            self.assertEqual(res.returncode, 0, f"Completion generation failed for {shell}")
            out = res.stdout
            self.assertTrue(len(out) > 50, f"Completion output too short for {shell}")
            self.assertIn("build", out)
            self.assertIn("dash", out)

    def test_rust_unit_tests_pass(self):
        """Execute the Rust unit tests for the CLI dispatcher module."""
        res = _cargo("cargo", "test", "-p", "miosd", "--lib", "--", "cli")
        self.assertEqual(
            res.returncode, 0,
            f"Cargo test for miosd cli failed:\nstdout: {res.stdout}\nstderr: {res.stderr}"
        )
        self.assertIn("test cli::dispatcher::tests::test_known_verbs_table_count ... ok", res.stdout)
        self.assertIn("test cli::completions::tests::test_completion_generation ... ok", res.stdout)
        self.assertIn("test cli::tests::test_dispatch_help ... ok", res.stdout)

if __name__ == "__main__":
    unittest.main()
