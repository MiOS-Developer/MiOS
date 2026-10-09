#!/usr/bin/env python3
# AI-hint: Automated unit test suite for multi-GPU PCIe/NVLink topology discovery and NUMA affinity generator (task_463).
# AI-doc: usr/share/doc/mios/manual/ch22-cpu-topology-and-performance-pinning.md
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_LIBEXEC_DIR = os.path.join(_ROOT, "usr", "libexec", "mios")
if _LIBEXEC_DIR not in sys.path:
    sys.path.insert(0, _LIBEXEC_DIR)

from importlib.machinery import SourceFileLoader
mios_gpu_numa = SourceFileLoader("mios_gpu_numa", os.path.join(_LIBEXEC_DIR, "mios-gpu-numa")).load_module()
GpuNumaDiscovery = mios_gpu_numa.GpuNumaDiscovery


class TestGpuNumaDiscovery(unittest.TestCase):
    """Validates GPU PCI scanning, NUMA node binding, NVLink matrix parsing, and Quadlet integration."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios-test-numa-")
        self.sysfs_root = Path(self.tmpdir) / "sys"
        self.pci_dir = self.sysfs_root / "bus" / "pci" / "devices"
        self.pci_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _create_mock_pci_device(self, pci_id: str, pci_class: str, numa_node: int, vendor: str = "0x10de"):
        dev_dir = self.pci_dir / pci_id
        dev_dir.mkdir(parents=True, exist_ok=True)
        (dev_dir / "class").write_text(f"{pci_class}\n", encoding="utf-8")
        (dev_dir / "numa_node").write_text(f"{numa_node}\n", encoding="utf-8")
        (dev_dir / "vendor").write_text(f"{vendor}\n", encoding="utf-8")

    def test_scan_pci_gpus(self):
        # GPU 0 on NUMA 0 (3D controller)
        self._create_mock_pci_device("0000:01:00.0", "0x030200", 0)
        # GPU 1 on NUMA 1 (VGA controller)
        self._create_mock_pci_device("0000:02:00.0", "0x030000", 1)
        # Non-GPU device (Ethernet controller 0x020000)
        self._create_mock_pci_device("0000:03:00.0", "0x020000", 0)

        discovery = GpuNumaDiscovery(sysfs_root=str(self.sysfs_root))
        gpus = discovery.scan_pci_gpus()

        self.assertEqual(len(gpus), 2)
        self.assertEqual(gpus[0]["pci_id"], "0000:01:00.0")
        self.assertEqual(gpus[0]["numa_node"], 0)
        self.assertEqual(gpus[1]["pci_id"], "0000:02:00.0")
        self.assertEqual(gpus[1]["numa_node"], 1)

    def test_parse_topo_matrix(self):
        nvlink_output = """
        GPU0    GPU1    CPU Affinity    NUMA Affinity
GPU0     X      NV12    0-31            0
GPU1    NV12     X      32-63           1
        """
        level, dev = GpuNumaDiscovery.parse_topo_matrix(nvlink_output)
        self.assertEqual(level, "NVL")
        self.assertEqual(len(dev), 2)

        pcie_output = """
        GPU0    GPU1    CPU Affinity    NUMA Affinity
GPU0     X      PHB     0-31            0
GPU1    PHB      X      0-31            0
        """
        level_pcie, _ = GpuNumaDiscovery.parse_topo_matrix(pcie_output)
        self.assertEqual(level_pcie, "PHB")

        sys_output = """
        GPU0    GPU1    CPU Affinity    NUMA Affinity
GPU0     X      SYS     0-31            0
GPU1    SYS      X      32-63           1
        """
        level_sys, _ = GpuNumaDiscovery.parse_topo_matrix(sys_output)
        self.assertEqual(level_sys, "SYS")

    def test_generate_affinity_and_env_file(self):
        self._create_mock_pci_device("0000:01:00.0", "0x030000", 1)
        self._create_mock_pci_device("0000:02:00.0", "0x030000", 1)

        discovery = GpuNumaDiscovery(sysfs_root=str(self.sysfs_root))
        affinity = discovery.generate_affinity(gpu_indices=[0, 1], mock_topo="NVL")

        self.assertEqual(affinity["CUDA_VISIBLE_DEVICES"], "0,1")
        self.assertEqual(affinity["NCCL_P2P_LEVEL"], "NVL")
        self.assertEqual(affinity["NUMA_CPU_NODE"], "1")
        self.assertEqual(affinity["NUMACTL_ARGS"], "--cpunodebind=1 --membind=1")

        env_file = Path(self.tmpdir) / "gpu-numa.env"
        discovery.write_env_file(env_file, affinity)
        self.assertTrue(env_file.exists())

        content = env_file.read_text(encoding="utf-8")
        self.assertIn("CUDA_VISIBLE_DEVICES=0,1", content)
        self.assertIn("NCCL_P2P_LEVEL=NVL", content)
        self.assertIn("NUMA_CPU_NODE=1", content)
        self.assertIn("NUMACTL_ARGS=--cpunodebind=1 --membind=1", content)

    def test_container_quadlet_env_integration(self):
        container_path = os.path.join(_ROOT, "usr", "share", "containers", "systemd", "mios-llm-heavy.container")
        content = Path(container_path).read_text(encoding="utf-8")
        self.assertIn("EnvironmentFile=-/run/mios/gpu-numa.env", content)


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestGpuNumaDiscovery)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
