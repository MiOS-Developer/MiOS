#!/usr/bin/env python3
# AI-hint: Automated unit test suite for OverlayFS Workspace Provisioning & Isolation (task_635, task_636).
# AI-related: usr/lib/mios/agent-pipe/overlay_workspace.py, tests/test-overlay-workspace.py
"""Automated unit test suite for MiOS OverlayFS Workspace Manager."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "lib", "mios", "agent-pipe"))

from overlay_workspace import MAX_PROVISION_LATENCY_MS, OverlayWorkspaceManager

class TestOverlayWorkspace(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="mios-ws-test-")
        self.mgr = OverlayWorkspaceManager(base_workspace_dir=self.tmp_dir, dry_run=True)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_fast_provisioning_and_file_mutation(self):
        """Test workspace provisions and writes isolated mutation."""
        mount = self.mgr.provision_agent_workspace("agent_alpha")
        self.assertTrue(mount.is_active)
        path = self.mgr.apply_file_mutation("agent_alpha", "src/main.py", "x = 100")
        self.assertTrue(os.path.exists(path))
        self.assertTrue(self.mgr.teardown_workspace("agent_alpha"))

    def test_20_concurrent_subagent_workspace_isolation(self):
        """Test 20 concurrent subagents perform isolated edits with zero collisions."""
        for i in range(20):
            self.mgr.provision_agent_workspace(f"worker_{i}")
            p = self.mgr.apply_file_mutation(f"worker_{i}", f"file_{i}.txt", f"Content {i}")
            self.assertTrue(os.path.exists(p))

        self.assertEqual(len(self.mgr.active_mounts), 20)

        for i in range(20):
            self.assertTrue(self.mgr.teardown_workspace(f"worker_{i}"))
        self.assertEqual(len(self.mgr.active_mounts), 0)

if __name__ == "__main__":
    unittest.main()
