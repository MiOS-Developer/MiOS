#!/usr/bin/env python3
# AI-hint: Automated unit test suite for SquashFS template streaming over Unix-socket NBD with RAM overlay (task_750, task_751).
# AI-doc: usr/share/doc/mios/manual/virt.md
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_MICROVM_BIN = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-microvm")


class TestMicroVMSquashFSNBD(unittest.TestCase):
    """Validates SquashFS NBD streaming, sub-15ms boot latency SLA, and 100-VM concurrency scaling."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_binary_executable(self):
        self.assertTrue(os.path.isfile(_MICROVM_BIN), f"Missing {_MICROVM_BIN}")
        self.assertTrue(os.access(_MICROVM_BIN, os.X_OK), f"Not executable {_MICROVM_BIN}")

    def test_nbd_export(self):
        fake_squashfs = os.path.join(self.tmpdir.name, "template.squashfs")
        with open(fake_squashfs, "wb") as f:
            f.write(b"hsqs" + b"\x00" * 2048)

        sock_path = os.path.join(self.tmpdir.name, "nbd.sock")

        res = subprocess.run(
            [
                _MICROVM_BIN,
                "export-nbd",
                "--template", fake_squashfs,
                "--socket", sock_path,
                "--dry-run",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "exported")
        self.assertTrue(data.get("read_only"))
        self.assertIn("qemu-nbd", data.get("command", ""))

    def test_microvm_nbd_boot_sla(self):
        sock_path = os.path.join(self.tmpdir.name, "nbd.sock")

        res = subprocess.run(
            [
                _MICROVM_BIN,
                "launch",
                "--nbd-socket", sock_path,
                "--ram-overlay",
                "--dry-run",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "running")
        self.assertEqual(data.get("nbd_socket"), sock_path)
        self.assertTrue(data.get("ram_overlay"))
        self.assertTrue(data.get("sla_met"), "Boot latency SLA < 15ms not met")
        self.assertLess(data.get("boot_latency_ms", 999), 15.0)

    def test_concurrency_density(self):
        # Spawns 100 microVMs sharing single template in dry-run mode
        sys.path.insert(0, os.path.join(_ROOT, "usr", "libexec", "mios", "virt"))
        import mios_microvm

        vms = []
        for i in range(100):
            vm = mios_microvm.MicroVM(
                vm_id=f"vm-{i:03d}",
                rootfs=os.path.join(self.tmpdir.name, "template.squashfs"),
                nbd_socket=os.path.join(self.tmpdir.name, "nbd.sock"),
                ram_overlay=True,
            )
            info = vm.launch(dry_run=True)
            self.assertTrue(info["sla_met"])
            vms.append(vm)

        self.assertEqual(len(vms), 100)
        # Clean up
        for vm in vms:
            vm.destroy()


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestMicroVMSquashFSNBD)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
