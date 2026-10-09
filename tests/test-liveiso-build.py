#!/usr/bin/env python3
# AI-hint: Automated unit test suite for Hybrid Live ISO and iPXE Netboot Pipeline (task_591, task_592).
# AI-related: usr/libexec/mios/build/liveiso.py, tests/test-liveiso-build.py
"""Automated unit test suite for MiOS Live ISO & iPXE Pipeline."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "build"))

from liveiso import LiveISOPipeline

class TestLiveISOPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="mios-iso-test-")
        self.pipe = LiveISOPipeline(output_dir=self.tmp_dir, dry_run=True)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_ipxe_script_generation(self):
        """Test iPXE script generates valid kernel/initramfs boot syntax."""
        p = self.pipe.generate_ipxe_script("http://boot.mios.local:8080")
        self.assertTrue(os.path.exists(p))
        with open(p, "r") as f:
            content = f.read()
        self.assertIn("#!ipxe", content)
        self.assertIn("bootc.install.to-disk", content)

    def test_hybrid_iso_synthesis(self):
        """Test hybrid ISO creation returns valid bootable artifact."""
        art = self.pipe.build_hybrid_iso("localhost/mios:latest")
        self.assertEqual(art.artifact_type, "iso")
        self.assertTrue(art.is_hybrid_bootable)
        self.assertTrue(os.path.exists(art.file_path))

if __name__ == "__main__":
    unittest.main()
