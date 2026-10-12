#!/usr/bin/env python3
# AI-hint: Automated unit test suite for subagent Bubblewrap sandboxing, cgroup quotas, and escape resistance.
# AI-related: usr/lib/mios/agent-pipe/mios_subagent_sandbox.py, usr/share/mios/mios.toml
"""Unit and integration test suite for SubagentSandbox and mios_subagent_sandbox CLI (task_496)."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_TARGET_PATH = os.path.join(_ROOT, "usr", "lib", "mios", "agent-pipe", "mios_subagent_sandbox.py")

spec = importlib.util.spec_from_file_location("mios_subagent_sandbox", _TARGET_PATH)
if spec and spec.loader:
    mios_subagent_sandbox = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mios_subagent_sandbox
    spec.loader.exec_module(mios_subagent_sandbox)
else:
    raise ImportError(f"Could not load module from {_TARGET_PATH}")

class TestSubagentCgroups(unittest.TestCase):
    """Test suite for subagent Bubblewrap isolation, cgroup enforcement, and path escape detection."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="mios-test-sandbox-")
        self.workspace = os.path.join(self.tmpdir.name, "workspace")
        os.makedirs(self.workspace, exist_ok=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_bwrap_args_construction(self):
        sandbox = mios_subagent_sandbox.SubagentSandbox(
            workspace_dir=self.workspace,
            mock=True,
        )
        cmd = ["python", "-c", "print('hello')"]
        args = sandbox.build_bwrap_args(cmd)

        self.assertIn("--unshare-all", args)
        self.assertIn("--die-with-parent", args)
        self.assertIn("--tmpfs", args)
        self.assertIn("/tmp", args)
        self.assertIn("--ro-bind", args)
        self.assertIn("/usr", args)
        self.assertIn("/etc", args)
        self.assertIn("--bind", args)
        self.assertIn(self.workspace, args)
        self.assertIn("python", args)

    def test_systemd_run_cgroup_args_construction(self):
        sandbox = mios_subagent_sandbox.SubagentSandbox(
            workspace_dir=self.workspace,
            memory_max="2G",
            cpu_quota="150%",
            tasks_max=128,
            mock=True,
        )
        bwrap_cmd = ["bwrap", "--unshare-all"]
        full_cmd = sandbox.build_systemd_run_args(bwrap_cmd, unit_name="test-unit-01")

        self.assertIn("--user", full_cmd)
        self.assertIn("--scope", full_cmd)
        self.assertIn("--unit=test-unit-01", full_cmd)
        self.assertIn("-pMemoryMax=2G", full_cmd)
        self.assertIn("-pCPUQuota=150%", full_cmd)
        self.assertIn("-pTasksMax=128", full_cmd)
        self.assertIn("-pIOWeight=100", full_cmd)

    def test_validate_workspace_path_within_boundary(self):
        valid_file = os.path.join(self.workspace, "output.txt")
        valid, msg = mios_subagent_sandbox.validate_workspace_path(valid_file, self.workspace)
        self.assertTrue(valid)
        self.assertIn("within permitted", msg)

        sub_dir_file = os.path.join(self.workspace, "subdir", "nested.json")
        valid, msg = mios_subagent_sandbox.validate_workspace_path(sub_dir_file, self.workspace)
        self.assertTrue(valid)

    def test_validate_workspace_path_escape_attempt(self):
        # Traversal attempt
        escape_path = os.path.join(self.workspace, "..", "..", "etc", "shadow")
        valid, msg = mios_subagent_sandbox.validate_workspace_path(escape_path, self.workspace)
        self.assertFalse(valid)
        self.assertIn("escape detected", msg)

    def test_validate_workspace_path_direct_system_target(self):
        sys_target = "/etc/sudoers"
        valid, msg = mios_subagent_sandbox.validate_workspace_path(sys_target, self.workspace)
        self.assertFalse(valid)

    def test_sandbox_execute_mock_success(self):
        sandbox = mios_subagent_sandbox.SubagentSandbox(workspace_dir=self.workspace, mock=True)
        res = sandbox.execute(["echo", "Running agent unit"])
        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["exit_code"], 0)
        self.assertIn("Running agent unit", res["stdout"])

    def test_sandbox_execute_mock_failure(self):
        sandbox = mios_subagent_sandbox.SubagentSandbox(workspace_dir=self.workspace, mock=True)
        res = sandbox.execute(["run_tool_error"])
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "execution_failed")
        self.assertEqual(res["exit_code"], 1)
        self.assertIn("Mock subagent intentional execution failure", res["stderr"])

    def test_sandbox_execute_mock_oom_killed(self):
        sandbox = mios_subagent_sandbox.SubagentSandbox(workspace_dir=self.workspace, mock=True)
        res = sandbox.execute(["allocate_huge_tensor_oom"])
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "oom_killed")
        self.assertEqual(res["exit_code"], 137)
        self.assertIn("OOM killed", res["stderr"])

    def test_sandbox_dry_run(self):
        sandbox = mios_subagent_sandbox.SubagentSandbox(workspace_dir=self.workspace, dry_run=True)
        res = sandbox.execute(["cargo", "check"])
        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "dry_run")
        self.assertIn("command", res)
        self.assertEqual(res["cgroups"]["MemoryMax"], "4G")

    def test_cli_execution_mock(self):
        with patch.object(sys, "argv", ["mios_subagent_sandbox.py", "--mock", "--json", "--run", "echo", "123"]):
            code = mios_subagent_sandbox.main()
            self.assertEqual(code, 0)

        with patch.object(sys, "argv", ["mios_subagent_sandbox.py", "--dry-run", "--json"]):
            code = mios_subagent_sandbox.main()
            self.assertEqual(code, 0)

if __name__ == "__main__":
    unittest.main()
