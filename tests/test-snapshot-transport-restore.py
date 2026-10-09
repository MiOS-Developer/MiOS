#!/usr/bin/env python3
# AI-hint: Automated unit test suite for zero-knowledge remote snapshot transport and offline restore (task_466, task_2699).
# AI-doc: usr/share/doc/mios/manual/storage.md
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
_SEGREGATE = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-storage-segregate")
_TRANSPORT = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-snapshot-transport")
_RESTORE = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-snapshot-restore")


class TestSnapshotTransportRestore(unittest.TestCase):
    """Validates zero-knowledge untrusted remote snapshot transport and offline recovery."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="mios-test-zk-")
        self.key_dir = os.path.join(self.test_dir, "keys")
        self.local_snap_dir = os.path.join(self.test_dir, "local_snaps")
        self.remote_dir = os.path.join(self.test_dir, "untrusted_remote")
        self.restore_dir = os.path.join(self.test_dir, "restored")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_tools_exist_and_executable(self):
        for tool in [_SEGREGATE, _TRANSPORT, _RESTORE]:
            self.assertTrue(os.path.isfile(tool), f"Missing tool: {tool}")
            self.assertTrue(os.access(tool, os.X_OK), f"Not executable: {tool}")

    def test_zero_knowledge_transport_and_restore_cycle(self):
        # 1. Initialize domain keys
        subprocess.run(
            [_SEGREGATE, "init-keys", "--key-dir", self.key_dir, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )

        # 2. Export inert snapshot for domain home
        res = subprocess.run(
            [_SEGREGATE, "export-snapshot", "--domain", "home", "--key-dir", self.key_dir, "--out-dir", self.local_snap_dir, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        snap_meta = json.loads(res.stdout)
        meta_file = snap_meta["meta_file"]
        payload_file = snap_meta["payload_file"]

        # 3. Transport to untrusted remote destination
        res_transport = subprocess.run(
            [_TRANSPORT, "--meta", meta_file, "--payload", payload_file, "--remote-dest", self.remote_dir, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        t_data = json.loads(res_transport.stdout)
        self.assertEqual(t_data.get("status"), "success")
        self.assertTrue(t_data.get("zero_knowledge_guarantee"))
        self.assertFalse(t_data.get("keys_transmitted"))

        # Verify remote storage contains NO key files or plaintext secrets
        remote_files = os.listdir(self.remote_dir)
        self.assertFalse(any(f.endswith(".key") for f in remote_files), "Key leaked to remote storage!")
        with open(t_data["dest_meta"], "r") as f:
            stored_meta = json.load(f)
        self.assertNotIn("key", stored_meta)
        self.assertNotIn("secret", stored_meta)
        self.assertTrue(stored_meta.get("inert"))

        # 4. Restore on clean target host using local key
        home_key = os.path.join(self.key_dir, "home.key")
        res_restore = subprocess.run(
            [
                _RESTORE,
                "--meta", t_data["dest_meta"],
                "--payload", t_data["dest_payload"],
                "--key-file", home_key,
                "--target-dir", self.restore_dir,
                "--json",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        r_data = json.loads(res_restore.stdout)
        self.assertEqual(r_data.get("status"), "success")
        self.assertTrue(r_data.get("authenticated"))
        self.assertTrue(os.path.isfile(r_data["restored_file"]))
        with open(r_data["restored_file"], "rb") as f:
            restored_bytes = f.read()
        self.assertTrue(b"MiOS storage domain snapshot for home" in restored_bytes)

    def test_negative_control_wrong_domain_key_fails(self):
        # 1. Initialize domain keys
        subprocess.run(
            [_SEGREGATE, "init-keys", "--key-dir", self.key_dir, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )

        # 2. Export home snapshot
        res = subprocess.run(
            [_SEGREGATE, "export-snapshot", "--domain", "home", "--key-dir", self.key_dir, "--out-dir", self.local_snap_dir, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        snap_meta = json.loads(res.stdout)
        meta_file = snap_meta["meta_file"]
        payload_file = snap_meta["payload_file"]

        # 3. Transport
        subprocess.run(
            [_TRANSPORT, "--meta", meta_file, "--payload", payload_file, "--remote-dest", self.remote_dir, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        remote_meta = os.path.join(self.remote_dir, os.path.basename(meta_file))
        remote_payload = os.path.join(self.remote_dir, os.path.basename(payload_file))

        # 4. Attempt restoration using pgvector.key instead of home.key
        pg_key = os.path.join(self.key_dir, "pgvector.key")
        res_fail = subprocess.run(
            [
                _RESTORE,
                "--meta", remote_meta,
                "--payload", remote_payload,
                "--key-file", pg_key,
                "--target-dir", self.restore_dir,
                "--json",
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_fail.returncode, 0, "Restoration must fail when wrong key is provided!")
        fail_data = json.loads(res_fail.stdout)
        self.assertEqual(fail_data.get("status"), "error")
        self.assertIn("Key fingerprint mismatch", fail_data.get("error"))

    def test_negative_control_tampered_payload_fails(self):
        # 1. Initialize domain keys
        subprocess.run(
            [_SEGREGATE, "init-keys", "--key-dir", self.key_dir, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )

        # 2. Export system_state snapshot
        res = subprocess.run(
            [_SEGREGATE, "export-snapshot", "--domain", "system_state", "--key-dir", self.key_dir, "--out-dir", self.local_snap_dir, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        snap_meta = json.loads(res.stdout)
        meta_file = snap_meta["meta_file"]
        payload_file = snap_meta["payload_file"]

        # 3. Transport
        subprocess.run(
            [_TRANSPORT, "--meta", meta_file, "--payload", payload_file, "--remote-dest", self.remote_dir, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        remote_meta = os.path.join(self.remote_dir, os.path.basename(meta_file))
        remote_payload = os.path.join(self.remote_dir, os.path.basename(payload_file))

        # Tamper with 1 byte in payload
        with open(remote_payload, "r+b") as f:
            b = f.read(1)
            f.seek(0)
            f.write(bytes([b[0] ^ 0xFF]))

        # Attempt restore with valid key
        sys_key = os.path.join(self.key_dir, "system_state.key")
        res_tamper = subprocess.run(
            [
                _RESTORE,
                "--meta", remote_meta,
                "--payload", remote_payload,
                "--key-file", sys_key,
                "--target-dir", self.restore_dir,
                "--json",
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_tamper.returncode, 0, "Restoration must fail on tampered payload!")
        data = json.loads(res_tamper.stdout)
        self.assertEqual(data.get("status"), "error")
        self.assertIn("Corrupted payload", data.get("error"))


if __name__ == "__main__":
    unittest.main()
