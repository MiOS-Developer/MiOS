#!/usr/bin/env python3
# AI-hint: Two-sided verification test suite for MiOS Decentralized RDE Platform Engine (DevPod + OpenTofu + MIT IDE).
# AI-doc: usr/share/doc/mios/manual/rde.md
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_SSOT_PATH = os.path.join(_ROOT, "usr", "share", "mios", "mios.toml")
_DEVCONTAINER_PATH = os.path.join(_ROOT, ".devcontainer", "devcontainer.json")
_USR_BIN_MIOS = os.path.join(_ROOT, "usr", "bin", "mios")
_MIOS_RS_DIR = os.path.join(_ROOT, "src", "mios-rs")


def _find_miosd_binary() -> str:
    """Finds or builds the miosd executable for testing."""
    candidates = [
        os.path.join(_MIOS_RS_DIR, "target", "debug", "miosd.exe"),
        os.path.join(_MIOS_RS_DIR, "target", "debug", "miosd"),
        os.path.join(_ROOT, "usr", "libexec", "mios", "miosd"),
        shutil.which("miosd") or "",
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    # If not found, try building with cargo
    try:
        subprocess.run(["cargo", "build", "-p", "miosd"], cwd=_MIOS_RS_DIR, check=True, capture_output=True)
    except Exception:
        pass
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return ""


class TestRdeEngine(unittest.TestCase):
    """Two-sided test suite for MiOS RDE Orchestration Engine, Copyleft Taint Gating, and SSOT Conformance."""

    @classmethod
    def setUpClass(cls):
        cls.miosd_bin = _find_miosd_binary()
        if not cls.miosd_bin:
            raise unittest.SkipTest("miosd binary not found and cargo build unavailable")

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="mios_rde_test_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # POSITIVE CONTROLS
    # -------------------------------------------------------------------------

    def test_ssot_rde_configuration(self):
        """Positive Control: Verify SSOT defaults and ports in mios.toml."""
        self.assertTrue(os.path.isfile(_SSOT_PATH), f"SSOT missing at {_SSOT_PATH}")
        with open(_SSOT_PATH, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("code_server", content)
        self.assertIn("8900", content)

        # Verify miosd status loads SSOT-derived defaults
        cmd = [self.miosd_bin, "rde", "--root", _ROOT, "status", "--json"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"rde status failed: {res.stderr}")
        status = json.loads(res.stdout)
        self.assertEqual(status.get("orchestrator"), "devpod")
        self.assertEqual(status.get("engine"), "podman")
        self.assertEqual(status.get("ide"), "code-server")
        self.assertEqual(status.get("devpod_agent_port"), 8910)

    def test_devcontainer_port_contract(self):
        """Positive Control: Verify devcontainer.json forwards DevPod agent port 8910."""
        self.assertTrue(os.path.isfile(_DEVCONTAINER_PATH))
        with open(_DEVCONTAINER_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        forward_ports = data.get("forwardPorts", [])
        self.assertIn(8910, forward_ports, "Port 8910 (devpod_agent) must be in forwardPorts")
        self.assertIn(8900, forward_ports, "Port 8900 (code_server) must be in forwardPorts")

    def test_rde_audit_zero_taint(self):
        """Positive Control: Verify automated license audit confirms 0 copyleft taint."""
        cmd = [self.miosd_bin, "rde", "--root", _ROOT, "audit", "--json"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"rde audit failed: {res.stderr}")

        report = json.loads(res.stdout)
        self.assertTrue(report.get("compliant"), "Audit report must be compliant")
        self.assertEqual(report.get("copyleft_violations"), 0, "Violations must be 0")

        # Verify orchestrator is MPL-2.0 DevPod
        orch = report.get("orchestrator", {})
        self.assertEqual(orch.get("name"), "devpod")
        self.assertEqual(orch.get("license"), "MPL-2.0")
        self.assertFalse(orch.get("copyleft_taint"))

        # Verify infrastructure is MPL-2.0 OpenTofu
        infra = report.get("infrastructure", {})
        self.assertEqual(infra.get("name"), "OpenTofu")
        self.assertEqual(infra.get("license"), "MPL-2.0")
        self.assertFalse(infra.get("copyleft_taint"))

        # Verify IDE is MIT code-server
        ide = report.get("ide", {})
        self.assertEqual(ide.get("name"), "code-server")
        self.assertEqual(ide.get("license"), "MIT")
        self.assertFalse(ide.get("copyleft_taint"))

    def test_rde_up_dry_run_plan(self):
        """Positive Control: Verify miosd rde up generates valid execution plan and arguments."""
        # Create a sample workspace with a mock devcontainer
        dc_dir = os.path.join(self.temp_dir, ".devcontainer")
        os.makedirs(dc_dir, exist_ok=True)
        with open(os.path.join(dc_dir, "devcontainer.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "Test Workspace"}, f)

        cmd = [
            self.miosd_bin,
            "rde",
            "--root",
            _ROOT,
            "up",
            self.temp_dir,
            "--provider",
            "docker",
            "--ide",
            "code-server",
            "--dry-run",
            "--json",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"rde up failed: {res.stderr}")

        plan = json.loads(res.stdout)
        self.assertEqual(plan.get("orchestrator"), "devpod")
        self.assertEqual(plan.get("engine"), "podman")
        self.assertEqual(plan.get("provider"), "docker")
        self.assertEqual(plan.get("ide"), "code-server")

        command = plan.get("command", [])
        self.assertTrue(len(command) >= 3)
        self.assertEqual(command[0], "devpod")
        self.assertEqual(command[1], "up")
        self.assertIn("--provider", command)
        self.assertIn("docker", command)
        self.assertIn("--ide", command)
        self.assertIn("code-server", command)

        # Check port forwarding in devpod command
        ports = plan.get("forward_ports", [])
        self.assertIn(8910, ports)
        self.assertIn(8900, ports)

        env = plan.get("env", {})
        self.assertEqual(env.get("MIOS_RDE_ORCHESTRATOR"), "devpod")
        self.assertEqual(env.get("MIOS_RDE_IDE"), "code-server")

    def test_rde_down_dry_run(self):
        """Positive Control: Verify miosd rde down generates valid stop/delete command."""
        cmd = [self.miosd_bin, "rde", "--root", _ROOT, "down", self.temp_dir, "--dry-run", "--json"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"rde down failed: {res.stderr}")

        data = json.loads(res.stdout)
        self.assertEqual(data.get("action"), "stop")
        command = data.get("command", [])
        self.assertEqual(command[0], "devpod")
        self.assertEqual(command[1], "stop")

    def test_rde_down_destroy_dry_run(self):
        """Positive Control: Verify miosd rde down --destroy generates delete command."""
        cmd = [self.miosd_bin, "rde", "--root", _ROOT, "down", self.temp_dir, "--destroy", "--dry-run", "--json"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"rde down --destroy failed: {res.stderr}")

        data = json.loads(res.stdout)
        self.assertEqual(data.get("action"), "delete")
        self.assertTrue(data.get("destroy"))
        command = data.get("command", [])
        self.assertEqual(command[0], "devpod")
        self.assertEqual(command[1], "delete")

    def test_tofu_plan_dry_run(self):
        """Positive Control: Verify OpenTofu wrapper accepts valid tofu arguments under dry-run."""
        cmd = [self.miosd_bin, "rde", "--root", _ROOT, "tofu", "plan", "-var", "env=dev", "--dry-run"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"rde tofu failed: {res.stderr}")
        self.assertIn("tofu plan", res.stdout)
        self.assertIn("--dry-run", res.stdout)

    def test_cli_verb_dispatch_registration(self):
        """Positive Control: Verify usr/bin/mios registers 'rde' in KNOWN_VERBS."""
        self.assertTrue(os.path.isfile(_USR_BIN_MIOS))
        with open(_USR_BIN_MIOS, "r", encoding="utf-8") as f:
            script_text = f.read()

        self.assertIn('"rde"', script_text)
        self.assertIn('["/usr/libexec/mios/miosd", "rde"]', script_text)
        self.assertIn("mios rde", script_text)

    # -------------------------------------------------------------------------
    # NEGATIVE CONTROLS (Gating & Taint Prevention)
    # -------------------------------------------------------------------------

    def test_coder_agpl_taint_rejection(self):
        """Negative Control: Verify provider 'coder' is rejected with AGPL taint error."""
        cmd = [self.miosd_bin, "rde", "--root", _ROOT, "up", self.temp_dir, "--provider", "coder"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0, "Provider 'coder' must be rejected")
        combined_err = res.stdout + res.stderr
        self.assertTrue(
            "AGPL" in combined_err or "copyleft" in combined_err,
            f"Expected AGPL copyleft rejection error, got: {combined_err}",
        )

    def test_coder_ide_agpl_taint_rejection(self):
        """Negative Control: Verify IDE 'coder' is rejected with AGPL taint error."""
        cmd = [self.miosd_bin, "rde", "--root", _ROOT, "up", self.temp_dir, "--ide", "coder"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0, "IDE 'coder' must be rejected")
        combined_err = res.stdout + res.stderr
        self.assertTrue(
            "AGPL" in combined_err or "copyleft" in combined_err,
            f"Expected AGPL copyleft rejection error, got: {combined_err}",
        )

    def test_terraform_bsl_taint_rejection(self):
        """Negative Control: Verify direct 'terraform' invocations are rejected under BSL 1.1 taint policy."""
        cmd = [self.miosd_bin, "rde", "--root", _ROOT, "tofu", "terraform", "apply"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0, "Direct 'terraform' must be rejected")
        combined_err = res.stdout + res.stderr
        self.assertTrue(
            "BSL" in combined_err or "terraform" in combined_err,
            f"Expected BSL 1.1 rejection error, got: {combined_err}",
        )

    def test_invalid_workspace_rejection(self):
        """Negative Control: Verify non-existent workspace path is rejected with diagnostic."""
        non_existent = os.path.join(self.temp_dir, "non_existent_subdir_404")
        cmd = [self.miosd_bin, "rde", "--root", _ROOT, "up", non_existent]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0, "Non-existent workspace must fail")
        combined_err = res.stdout + res.stderr
        self.assertTrue(
            "does not exist" in combined_err or "Invalid workspace" in combined_err,
            f"Expected workspace existence error, got: {combined_err}",
        )


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestRdeEngine)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
