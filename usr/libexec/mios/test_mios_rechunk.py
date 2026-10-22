#!/usr/bin/env python3
# AI-hint: Unit and regression test suite for mios_rechunk functionality.
# AI-related: mios-rechunk, mios-oci-delta-apply
# AI-functions: setUp, tearDown, test_rechunk_roundtrip, test_signed_rechunk, TestRechunkDelta

import os
import sys
import unittest
import tempfile
import tarfile
import subprocess
import shutil

import subprocess
import shutil

class TestRechunkDelta(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.old_dir = os.path.join(self.tmpdir.name, "old")
        self.new_dir = os.path.join(self.tmpdir.name, "new")
        self.out_dir = os.path.join(self.tmpdir.name, "out")
        self.delta_tar = os.path.join(self.tmpdir.name, "delta.tar")

        self.rechunk_bin = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mios-rechunk")
        self.apply_bin = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mios-oci-delta-apply")

        os.makedirs(self.old_dir)
        os.makedirs(self.new_dir)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_rechunk_roundtrip(self):
        with open(os.path.join(self.old_dir, "file1.txt"), "w") as f:
            f.write("A" * 2_000_000)  # > 1MB
        with open(os.path.join(self.old_dir, "file2.txt"), "w") as f:
            f.write("B" * 100_000)

        os.makedirs(os.path.join(self.new_dir, "subdir"))
        with open(os.path.join(self.new_dir, "file1.txt"), "w") as f:
            f.write("A" * 2_000_000) # Unchanged
        with open(os.path.join(self.new_dir, "file2.txt"), "w") as f:
            f.write("B" * 50_000)    # Changed (truncated)
        with open(os.path.join(self.new_dir, "subdir", "file3.txt"), "w") as f:
            f.write("C" * 50_000)    # New file

        subprocess.run(["python3", self.rechunk_bin, self.old_dir, self.new_dir, self.delta_tar], check=True)

        self.assertTrue(os.path.exists(self.delta_tar))

        subprocess.run(["python3", self.apply_bin, self.old_dir, self.out_dir, self.delta_tar], check=True)

        with open(os.path.join(self.new_dir, "file1.txt"), "r") as f1, open(os.path.join(self.out_dir, "file1.txt"), "r") as f2:
            self.assertEqual(f1.read(), f2.read())

        with open(os.path.join(self.new_dir, "file2.txt"), "r") as f1, open(os.path.join(self.out_dir, "file2.txt"), "r") as f2:
            self.assertEqual(f1.read(), f2.read())

        with open(os.path.join(self.new_dir, "subdir", "file3.txt"), "r") as f1, open(os.path.join(self.out_dir, "subdir", "file3.txt"), "r") as f2:
            self.assertEqual(f1.read(), f2.read())

    def test_signed_rechunk(self):
        priv_key = "dummy_private_key_content_123"
        with open(os.path.join(self.old_dir, "file1.txt"), "w") as f:
            f.write("Test content")
        with open(os.path.join(self.new_dir, "file1.txt"), "w") as f:
            f.write("Test content modified")

        subprocess.run(["python3", self.rechunk_bin, self.old_dir, self.new_dir, self.delta_tar, "--key", priv_key], check=True)

        subprocess.run(["python3", self.apply_bin, self.old_dir, self.out_dir, self.delta_tar, "--key", priv_key], check=True)

        res = subprocess.run(["python3", self.apply_bin, self.old_dir, self.out_dir, self.delta_tar, "--key", "wrong_key"])
        self.assertNotEqual(res.returncode, 0)

if __name__ == "__main__":
    unittest.main()

