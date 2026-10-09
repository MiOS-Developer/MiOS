#!/usr/bin/env python3
# AI-hint: Automated unit test suite for multi-domain storage segregater and inert snapshot export (task_465, task_2698).
# AI-doc: usr/share/doc/mios/manual/storage.md
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_TOOL = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-storage-segregate")


class TestStorageSegregate(unittest.TestCase):
    """Validates multi-domain cryptographic segregation and inert snapshot exporter."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="mios-test-crypto-")
        self.key_dir = os.path.join(self.test_dir, "keys")
        self.snap_dir = os.path.join(self.test_dir, "snapshots")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_tool_exists_and_executable(self):
        self.assertTrue(os.path.isfile(_TOOL), f"Missing tool: {_TOOL}")
        self.assertTrue(os.access(_TOOL, os.X_OK), f"Not executable: {_TOOL}")

    def test_init_domain_keys_distinct(self):
        res = subprocess.run(
            [_TOOL, "init-keys", "--key-dir", self.key_dir, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "success")
        keys_info = data.get("keys", {})
        self.assertIn("home", keys_info)
        self.assertIn("system_state", keys_info)
        self.assertIn("pgvector", keys_info)

        # Assert all fingerprints are unique
        fps = [k["fingerprint"] for k in keys_info.values()]
        self.assertEqual(len(fps), len(set(fps)), "Key fingerprints must be strictly pairwise unique!")

        # Assert key files exist with 0400 permissions
        for domain, info in keys_info.items():
            kpath = info["key_file"]
            self.assertTrue(os.path.isfile(kpath), f"Key file does not exist: {kpath}")
            mode = stat.S_IMODE(os.stat(kpath).st_mode)
            self.assertEqual(mode, stat.S_IRUSR, f"Key {kpath} must be mode 0400 (read-only owner)")

    def test_export_inert_snapshots(self):
        # 1. Initialize keys first
        subprocess.run(
            [_TOOL, "init-keys", "--key-dir", self.key_dir, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )

        # 2. Export snapshots for home and system_state
        for domain in ["home", "system_state"]:
            res = subprocess.run(
                [_TOOL, "export-snapshot", "--domain", domain, "--key-dir", self.key_dir, "--out-dir", self.snap_dir, "--json"],
                capture_output=True,
                text=True,
                check=True,
            )
            data = json.loads(res.stdout)
            self.assertEqual(data.get("status"), "success")
            self.assertEqual(data.get("domain"), domain)
            self.assertTrue(data.get("inert"))

            meta_file = data["meta_file"]
            payload_file = data["payload_file"]
            self.assertTrue(os.path.isfile(meta_file), f"Missing metadata file: {meta_file}")
            self.assertTrue(os.path.isfile(payload_file), f"Missing payload file: {payload_file}")

            with open(meta_file, "r") as f:
                meta = json.load(f)
            self.assertEqual(meta["domain"], domain)
            self.assertTrue(meta["inert"])
            self.assertTrue(meta["requires_key_for_restoration"])
            self.assertIn("auth_tag", meta)
            self.assertIn("key_fingerprint", meta)

    def test_cross_domain_cryptographic_isolation(self):
        # 1. Initialize keys
        subprocess.run(
            [_TOOL, "init-keys", "--key-dir", self.key_dir, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )

        # 2. Run verify-isolation
        res = subprocess.run(
            [_TOOL, "verify-isolation", "--key-dir", self.key_dir, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "success")
        self.assertTrue(data.get("isolation_verified"))
        self.assertEqual(data.get("checks_performed"), 9)  # 3x3 matrix

        # Assert no cross-domain authentication leaks
        for check in data.get("details", []):
            if check["source"] == check["target"]:
                self.assertEqual(check["result"], "authenticated")
            else:
                self.assertEqual(check["result"], "rejected")

    def test_status_inspection(self):
        # Uninitialized status
        res = subprocess.run(
            [_TOOL, "status", "--key-dir", self.key_dir, "--json"],
            capture_output=True,
            text=True,
        )
        data = json.loads(res.stdout)
        self.assertFalse(data.get("segregation_valid"))

        # Initialized status
        subprocess.run(
            [_TOOL, "init-keys", "--key-dir", self.key_dir, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        res = subprocess.run(
            [_TOOL, "status", "--key-dir", self.key_dir, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
        self.assertTrue(data.get("segregation_valid"))
        self.assertTrue(data["domains"]["home"]["key_exists"])
        self.assertTrue(data["domains"]["system_state"]["key_exists"])
        self.assertTrue(data["domains"]["pgvector"]["key_exists"])


if __name__ == "__main__":
    unittest.main()
