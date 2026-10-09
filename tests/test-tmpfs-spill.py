#!/usr/bin/env python3
# AI-hint: Automated unit test suite for WS-STRG automated tmpfs spillover to NVMe storage under memory pressure.
# AI-related: usr/libexec/mios/mem/mios-tmpfs-spill, usr/lib/systemd/system/mios-tmpfs-spill.service
"""Automated tests for WS-STRG tmpfs spill-to-NVMe manager (task_354 / task_2587)."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_SPILL_PATH = os.path.join(_ROOT, "usr", "libexec", "mios", "mem", "mios-tmpfs-spill")

loader = importlib.machinery.SourceFileLoader("tmpfs_spill", _SPILL_PATH)
spec = importlib.util.spec_from_loader("tmpfs_spill", loader)
if spec and spec.loader:
    tmpfs_spill = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = tmpfs_spill
    spec.loader.exec_module(tmpfs_spill)
else:
    raise ImportError(f"Could not load tmpfs_spill module from {_SPILL_PATH}")

class TestTmpfsSpill(unittest.TestCase):
    """Validates memory pressure detection, security exclusions, symlink migration, LRU quota eviction, and restoration."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="mios_test_tmpfs_spill_")
        self.src_dir = os.path.join(self.test_dir, "mock_tmp")
        self.target_dir = os.path.join(self.test_dir, "mock_nvme_spill")
        os.makedirs(self.src_dir, exist_ok=True)
        os.makedirs(self.target_dir, exist_ok=True)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_psi_pressure_and_ram_ratio_detection(self):
        # Mock PSI
        psi = tmpfs_spill.read_memory_pressure_psi(mock_psi=75.5)
        self.assertEqual(psi["some_avg10"], 75.5)
        self.assertTrue(psi["available"])

        # Mock RAM
        ram_ratio = tmpfs_spill.get_available_memory_ratio(mock_ratio=0.08)
        self.assertEqual(ram_ratio, 0.08)

    def test_security_sensitive_exclusions(self):
        """Do NOT migrate security-sensitive RAM keys, tokens, or credentials."""
        self.assertTrue(tmpfs_spill.is_file_security_sensitive("id_rsa", "/tmp/id_rsa"))
        self.assertTrue(tmpfs_spill.is_file_security_sensitive("token.jwt", "/tmp/token.jwt"))
        self.assertTrue(tmpfs_spill.is_file_security_sensitive("agent.key", "/tmp/agent.key"))
        self.assertTrue(tmpfs_spill.is_file_security_sensitive("db_password.txt", "/tmp/db_password.txt"))
        self.assertTrue(tmpfs_spill.is_file_security_sensitive("daemon.sock", "/tmp/daemon.sock"))

        # Legitimate build cache / data files should not be excluded
        self.assertFalse(tmpfs_spill.is_file_security_sensitive("model_weights.bin", "/tmp/model_weights.bin"))
        self.assertFalse(tmpfs_spill.is_file_security_sensitive("libtorch.so.tmp", "/tmp/libtorch.so.tmp"))
        self.assertFalse(tmpfs_spill.is_file_security_sensitive("rustc_build_artifact.o", "/tmp/rustc_build_artifact.o"))

    def test_scan_spillable_files_and_lru_ordering(self):
        # Create 3 files with different access times and sizes
        file_old = os.path.join(self.src_dir, "build_old.o")
        with open(file_old, "wb") as f:
            f.write(b"O" * 5000)
        os.utime(file_old, (time.time() - 500, time.time() - 500))

        file_new = os.path.join(self.src_dir, "build_new.o")
        with open(file_new, "wb") as f:
            f.write(b"N" * 6000)
        os.utime(file_new, (time.time() - 10, time.time() - 10))

        file_small = os.path.join(self.src_dir, "small.txt")
        with open(file_small, "wb") as f:
            f.write(b"S" * 100)

        # Sensitive file (should be ignored)
        file_secret = os.path.join(self.src_dir, "auth_token.secret")
        with open(file_secret, "wb") as f:
            f.write(b"SECRET_KEY_DATA" * 500)

        candidates = tmpfs_spill.scan_spillable_files(source_dir=self.src_dir, min_size_bytes=1000)

        # Expect exactly 2 candidates (old and new), excluding small.txt and auth_token.secret
        self.assertEqual(len(candidates), 2)
        # Oldest accessed file must be first in LRU order
        self.assertEqual(candidates[0]["filename"], "build_old.o")
        self.assertEqual(candidates[1]["filename"], "build_new.o")

    def test_execute_spill_and_symlink_transparency(self):
        """Verify spilling moves file to NVMe and creates a transparent symlink at original path."""
        orig_file = os.path.join(self.src_dir, "tensor_cache.bin")
        orig_content = b"TENSOR_ARRAY_DATA_PAYLOAD_12345" * 100
        with open(orig_file, "wb") as f:
            f.write(orig_content)

        ledger = tmpfs_spill.load_spill_ledger(self.target_dir)
        ok, dest_path, b_spilled = tmpfs_spill.execute_spill_file(
            src_path=orig_file,
            target_dir=self.target_dir,
            ledger=ledger,
        )

        self.assertTrue(ok)
        self.assertTrue(os.path.isfile(dest_path))
        self.assertTrue(os.path.islink(orig_file))

        # Reading from the symlinked original path must transparently yield the original content
        with open(orig_file, "rb") as f:
            read_back = f.read()
        self.assertEqual(read_back, orig_content)
        self.assertEqual(b_spilled, len(orig_content))

    def test_spill_quota_lru_eviction(self):
        ledger = tmpfs_spill.load_spill_ledger(self.target_dir)

        # Create 3 spilled files in target
        for i in range(3):
            src_f = os.path.join(self.src_dir, f"file_{i}.bin")
            with open(src_f, "wb") as f:
                f.write(b"DATA" * 1000)
            tmpfs_spill.execute_spill_file(src_f, self.target_dir, ledger)
            time.sleep(0.01)

        tmpfs_spill.save_spill_ledger(self.target_dir, ledger)
        self.assertEqual(len(ledger["spilled_files"]), 3)

        # Enforce quota of 9000 bytes (total 12000 bytes, so 1 evicted, 2 remaining = 8000 bytes)
        evicted = tmpfs_spill.enforce_spill_quota_lru(self.target_dir, max_spill_bytes=9000, ledger=ledger)
        self.assertEqual(len(evicted), 1)
        self.assertEqual(len(ledger["spilled_files"]), 2)

    def test_unspill_restoration(self):
        orig_file = os.path.join(self.src_dir, "unspill_me.dat")
        with open(orig_file, "wb") as f:
            f.write(b"RESTORE_CONTENT_DATA")

        ledger = tmpfs_spill.load_spill_ledger(self.target_dir)
        ok, dest_path, _ = tmpfs_spill.execute_spill_file(orig_file, self.target_dir, ledger)
        self.assertTrue(ok)
        self.assertTrue(os.path.islink(orig_file))

        # Unspill
        restored_cnt, restored_bytes = tmpfs_spill.unspill_files(self.target_dir, ledger)
        self.assertEqual(restored_cnt, 1)
        self.assertGreater(restored_bytes, 0)
        self.assertFalse(os.path.islink(orig_file))
        self.assertTrue(os.path.isfile(orig_file))

    def test_evaluate_and_spill_under_high_psi(self):
        # Create a 2MB temporary compiler file
        compile_file = os.path.join(self.src_dir, "rust_codegen.o")
        with open(compile_file, "wb") as f:
            f.write(b"BYTECODE" * (256 * 1024))

        # High PSI pressure (75% > 60% threshold)
        res = tmpfs_spill.evaluate_and_spill(
            source_dir=self.src_dir,
            target_dir=self.target_dir,
            psi_threshold=60.0,
            min_file_size=1024,
            mock_psi=75.0,
        )

        self.assertTrue(res["is_under_pressure"])
        self.assertTrue(res["spill_action_taken"])
        self.assertEqual(res["files_spilled"], 1)
        self.assertTrue(os.path.islink(compile_file))

def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestTmpfsSpill)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1

if __name__ == "__main__":
    sys.exit(main())
