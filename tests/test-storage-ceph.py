#!/usr/bin/env python3
# AI-hint: Consolidated unit test suite for MiOS Storage & Ceph domain (Bcachefs tiering, CephFS provisioning, active-active MDS, and RADOS Gateway).
# AI-related: usr/libexec/mios/storage/, usr/libexec/mios/mios-cephfs-provision, usr/share/containers/systemd/mios-radosgw.container
"""Consolidated Storage & Ceph Domain Test Suite."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))

sys.path.insert(0, os.path.join(_ROOT, "usr", "lib", "mios"))
sys.path.insert(0, os.path.join(_ROOT, "lib", "mios"))
sys.path.insert(0, os.path.join(_ROOT, "usr", "libexec", "mios", "storage"))

from bcachefs_tier import BcachefsTierManager
from ceph_mds import CephMDSOperator

# Dynamic loader for mios-cephfs-provision (extensionless executable script)
_PROV_PATH = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-cephfs-provision")
loader = importlib.machinery.SourceFileLoader("cephfs_provision", _PROV_PATH)
spec = importlib.util.spec_from_loader("cephfs_provision", loader)
if spec and spec.loader:
    cephfs_provision = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = cephfs_provision
    spec.loader.exec_module(cephfs_provision)
else:
    raise ImportError(f"Could not load mios-cephfs-provision module from {_PROV_PATH}")


# ============================================================================
# Domain 1.1: Bcachefs Multi-Device Storage Tiering Configurator
# (Migrated from tests/test-bcachefs-tiering.py)
# ============================================================================

class TestBcachefsTierManager(unittest.TestCase):
    """Unit tests for MiOS Bcachefs multi-device storage tiering configurator."""

    def test_multi_device_format_command_rendering(self):
        mgr = BcachefsTierManager(
            nvme_devices=["/dev/nvme0n1"],
            hdd_devices=["/dev/sda", "/dev/sdb"],
            mount_point="/srv/storage",
            compression="zstd:3",
            replicas=1,
            dry_run=True,
        )
        cmd = mgr.render_format_command()
        cmd_str = " ".join(cmd)

        self.assertIn("bcachefs format", cmd_str)
        self.assertIn("--foreground_target=nvme.hot", cmd_str)
        self.assertIn("--promote_target=nvme.hot", cmd_str)
        self.assertIn("--background_target=hdd.bulk", cmd_str)
        self.assertIn("--label=nvme.hot /dev/nvme0n1", cmd_str)
        self.assertIn("--label=hdd.bulk /dev/sda", cmd_str)
        self.assertIn("--label=hdd.bulk /dev/sdb", cmd_str)

    def test_single_nvme_volume_format(self):
        mgr = BcachefsTierManager(
            nvme_devices=["/dev/nvme0n1"],
            hdd_devices=[],
            dry_run=True,
        )
        cmd = mgr.render_format_command()
        cmd_str = " ".join(cmd)

        self.assertIn("--foreground_target=nvme.hot", cmd_str)
        self.assertNotIn("hdd.bulk", cmd_str)

    def test_fstab_entry_generation(self):
        mgr = BcachefsTierManager(
            nvme_devices=["/dev/nvme0n1"],
            hdd_devices=["/dev/sda"],
            mount_point="/var/lib/mios/models",
            dry_run=True,
        )
        fstab = mgr.render_fstab_entry(uuid="12345678-abcd-ef01-2345-6789abcdef01")
        self.assertIn("UUID=12345678-abcd-ef01-2345-6789abcdef01", fstab)
        self.assertIn("/var/lib/mios/models", fstab)
        self.assertIn("bcachefs", fstab)
        self.assertIn("promote_target=nvme.hot", fstab)
        self.assertIn("background_target=hdd.bulk", fstab)

    def test_empty_devices_raises_error(self):
        mgr = BcachefsTierManager(nvme_devices=[], hdd_devices=[], dry_run=True)
        with self.assertRaises(ValueError):
            mgr.render_format_command()


# ============================================================================
# Domain 1.2: Bcachefs Burst Write (>10 GB/s) and Background Migration
# (Migrated from tests/test-bcachefs-tiered-storage.py)
# ============================================================================

class TestBcachefsTieredStorage(unittest.TestCase):
    """Tests for task_705 & task_706: Bcachefs tiering burst write (>10 GB/s) and migration."""

    def test_bcachefs_burst_write_throughput(self):
        """Verify Bcachefs absorbs burst writes at >10 GB/s with SHA-256 integrity."""
        mgr = BcachefsTierManager()
        data = b"MIOS_TIERED_STORAGE_BLOCK_PAYLOAD" * 1024
        res = mgr.burst_write("blk-001", data)

        self.assertGreater(res["throughput_gbs"], 10.0, f"Throughput {res['throughput_gbs']} <= 10.0 GB/s SLA")
        self.assertEqual(mgr.blocks["blk-001"].tier, "foreground_nvme")

    def test_bcachefs_migration_integrity(self):
        """Verify background migration preserves block hashes without corruption."""
        mgr = BcachefsTierManager()
        mgr.burst_write("blk-002", b"PAYLOAD_TO_MIGRATE")
        initial_hash = mgr.blocks["blk-002"].data_hash

        migrated = mgr.rebalance_to_background()
        self.assertEqual(migrated, 1)
        self.assertEqual(mgr.blocks["blk-002"].tier, "background_hdd")
        self.assertEqual(mgr.blocks["blk-002"].data_hash, initial_hash, "Hash mismatch after migration")


# ============================================================================
# Domain 1.3: Active-Active CephFS MDS Clustering & Failover
# (Migrated from tests/test-ceph-mds-active-active.py)
# ============================================================================

class TestCephMDSActiveActive(unittest.TestCase):
    """Tests for task_683 & task_684: active-active CephFS MDS throughput and failover."""

    def test_active_active_mds_throughput(self):
        """Verify aggregated metadata throughput exceeds 50,000 ops/s across 2 active ranks."""
        op = CephMDSOperator(max_mds=2)
        op.pin_subtree("/workspaces/agent-1", target_rank=0)
        op.pin_subtree("/workspaces/agent-2", target_rank=1)

        total_ops = op.simulate_mdtest_ops()
        self.assertGreater(total_ops, 50_000.0, f"Aggregate MDS ops/sec {total_ops} <= 50,000 SLA")

    def test_standby_failover_latency(self):
        """Verify standby MDS transitions to active in <2.0s upon rank failure."""
        op = CephMDSOperator(max_mds=2)
        duration = op.trigger_failover(failed_rank=0)
        self.assertLess(duration, 2.0, f"Failover duration {duration:.3f}s >= 2.0s SLA")
        active_count = sum(1 for r in op.ranks.values() if r.state == "active")
        self.assertEqual(active_count, 2, "Active ranks must recover to 2")


# ============================================================================
# Domain 1.4: CephFS Multi-Tenant User & Keyring Provisioning
# (Migrated from tests/test-cephfs-provision.py)
# ============================================================================

class TestCephFSProvision(unittest.TestCase):
    """Validates CephFS configuration defaults, user info parsing, and keyring paths."""

    def test_cephfs_config_defaults(self):
        cfg = cephfs_provision.load_cephfs_config()
        self.assertIn("cluster_name", cfg)
        self.assertIn("fs_name", cfg)
        self.assertIn("tenant_id", cfg)
        self.assertEqual(cfg["subvolume_mode"], "0700")

    def test_user_info_lookup(self):
        username, gid = cephfs_provision.get_user_info("1000")
        self.assertTrue(len(username) > 0)
        self.assertIsInstance(gid, int)

    def test_user_info_lookup_string_username(self):
        username, gid = cephfs_provision.get_user_info("mios")
        self.assertTrue(len(username) > 0)
        self.assertIsInstance(gid, int)

    def test_resolve_uid_number(self):
        uid_num = cephfs_provision.resolve_uid_number("1000")
        self.assertEqual(uid_num, 1000)
        uid_str = cephfs_provision.resolve_uid_number("mios")
        self.assertIsInstance(uid_str, int)

    def test_pam_auth_file_exists(self):
        pam_path = os.path.join(_ROOT, "usr", "lib", "pam.d", "mios-cephfs-auth")
        self.assertTrue(os.path.exists(pam_path), f"PAM file missing at {pam_path}")
        with open(pam_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("pam_exec.so", content)
        self.assertIn("mios-cephfs-provision", content)
        self.assertIn("validate %u %g", content)


# ============================================================================
# Domain 1.5: Ceph RADOS Gateway Quadlet Container Configuration
# (Migrated from tests/test-radosgw-gateway.py)
# ============================================================================

class TestRADOSGWGateway(unittest.TestCase):
    """Validates Quadlet container unit definition, port binding, S3 configuration, and localhost isolation."""

    def setUp(self):
        self.quadlet_path = os.path.join(
            _ROOT, "usr", "share", "containers", "systemd", "mios-radosgw.container"
        )

    def test_quadlet_file_exists(self):
        self.assertTrue(
            os.path.exists(self.quadlet_path),
            f"Quadlet file missing at {self.quadlet_path}",
        )

    def test_quadlet_sections_and_syntax(self):
        with open(self.quadlet_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Check raw lines
        self.assertIn("[Unit]", content)
        self.assertIn("[Container]", content)
        self.assertIn("[Service]", content)
        self.assertIn("[Install]", content)

    def test_localhost_port_binding_and_no_wan_exposure(self):
        """Verify the S3 gateway port is strictly bound to 127.0.0.1 or mesh, not 0.0.0.0."""
        with open(self.quadlet_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]

        publish_lines = [l for l in lines if l.startswith("PublishPort=")]
        self.assertTrue(len(publish_lines) > 0, "Missing PublishPort in mios-radosgw.container")

        for pl in publish_lines:
            val = pl.split("=", 1)[1].strip()
            # Must be bound to 127.0.0.1
            self.assertTrue(
                val.startswith("127.0.0.1:"),
                f"PublishPort '{val}' must explicitly bind to 127.0.0.1 to prevent WAN exposure",
            )
            self.assertIn("8470", val, f"Port 8470 must be referenced in '{val}'")

    def test_container_name_and_image(self):
        with open(self.quadlet_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("ContainerName=mios-radosgw", content)
        self.assertIn("Image=quay.io/ceph/ceph", content)
        self.assertIn("Restart=always", content)

    def test_volume_mounts(self):
        with open(self.quadlet_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]

        vol_lines = [l.split("=", 1)[1].strip() for l in lines if l.startswith("Volume=")]
        self.assertTrue(any("/etc/ceph" in v for v in vol_lines), "Missing /etc/ceph volume mount")
        self.assertTrue(any("/var/lib/ceph" in v for v in vol_lines), "Missing /var/lib/ceph volume mount")
        self.assertTrue(any("radosgw" in v for v in vol_lines), "Missing radosgw data volume mount")

    def test_healthcheck_configuration(self):
        with open(self.quadlet_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("HealthCmd=", content)
        self.assertIn("curl", content)
        self.assertIn("8470", content)


if __name__ == "__main__":
    unittest.main()
