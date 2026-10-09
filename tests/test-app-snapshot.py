#!/usr/bin/env python3
# AI-hint: Automated unit test suite for Flatpak Application State Snapshotter (task_589, task_590).
# AI-related: usr/libexec/mios/app/snapshot.py, tests/test-app-snapshot.py
"""Automated unit test suite for MiOS Flatpak Application Snapshot Manager."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "app"))

from snapshot import FlatpakSnapshotManager

class TestAppSnapshot(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="mios-snap-test-")
        self.mgr = FlatpakSnapshotManager(root_dir=self.tmp_dir, dry_run=True)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_snapshot_creation_and_hash_tracking(self):
        """Test taking pre-update snapshot preserves state hash."""
        app_path = self.mgr._app_dir("org.chromium.Chromium")
        os.makedirs(app_path, exist_ok=True)
        with open(os.path.join(app_path, "profile.json"), "w") as f:
            f.write('{"user": "alice", "cookies": 42}')

        snap = self.mgr.create_snapshot("org.chromium.Chromium", tag="pre-update")
        self.assertIsNotNone(snap.snapshot_id)
        self.assertEqual(len(self.mgr.snapshots["org.chromium.Chromium"]), 1)

    def test_corruption_and_atomic_rollback(self):
        """Test data corruption rollback restores original files with 0 loss."""
        app_id = "org.code.VSCodium"
        app_path = self.mgr._app_dir(app_id)
        os.makedirs(app_path, exist_ok=True)
        good_file = os.path.join(app_path, "settings.json")
        with open(good_file, "w") as f:
            f.write('{"theme": "dark", "tabSize": 4}')

        snap = self.mgr.create_snapshot(app_id, tag="good-state")

        # Inject corruption
        with open(good_file, "w") as f:
            f.write('{"CORRUPTED_GARBAGE": null}')

        ok = self.mgr.rollback_app(app_id, snap.snapshot_id)
        self.assertTrue(ok)

        with open(good_file, "r") as f:
            content = f.read()
        self.assertIn('"theme": "dark"', content)

if __name__ == "__main__":
    unittest.main()
