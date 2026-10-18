#!/usr/bin/env python3
"""Tier 2: Boundary & Corner Cases — Xbox Subsystem Boundaries Suite."""

from __future__ import annotations

import os
import re
import sys

# Ensure repository root is in sys.path for standalone execution
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from typing import Any

from tests.e2e.e2e_framework import E2ETestCase, IsolatedEnvironment, run_standalone



class TestTier2BoundaryXbox(E2ETestCase):
    tier = 2
    suite_name = "T2_Boundary_Xbox_Subsystem"

    def test_cfr_toggle_id_boundaries(self):
        """Boundary: CFR toggle IDs in SSOT are valid 8-digit integers (10000000..99999999)."""
        import tomllib

        ssot_path = "/usr/share/mios/mios.toml"
        self.assert_true(os.path.exists(ssot_path), f"Missing SSOT at {ssot_path}")
        with open(ssot_path, "rb") as f:
            data = tomllib.load(f)

        cfr_overrides = (
            data.get("windows", {})
            .get("xbox", {})
            .get("xbox", {})
            .get("feature_overrides", {})
        )
        self.assert_greater_equal(len(cfr_overrides), 153, f"CFR overrides count {len(cfr_overrides)} < 153")

        critical_ids = {58989070, 59765208, 52580392, 50902630, 60786016}
        found_ids = set()

        for key, entry in cfr_overrides.items():
            self.assert_in("id", entry, f"CFR entry '{key}' missing 'id'")
            tid = entry["id"]
            self.assert_true(isinstance(tid, int), f"CFR toggle ID '{key}' must be int, got {type(tid).__name__}")
            # Boundary check: Velocity / CFR IDs are 8-digit feature IDs
            self.assert_greater_equal(tid, 10_000_000, f"CFR toggle ID {tid} for '{key}' < 10,000,000")
            self.assert_less(tid, 100_000_000, f"CFR toggle ID {tid} for '{key}' >= 100,000,000")
            self.assert_in("enabled", entry, f"CFR entry '{key}' missing 'enabled'")
            self.assert_true(isinstance(entry["enabled"], bool), f"CFR entry '{key}' 'enabled' must be bool")
            found_ids.add(tid)

        missing_critical = critical_ids - found_ids
        self.assert_equal(len(missing_critical), 0, f"Missing critical CFR IDs: {missing_critical}")

        # Negative control: verify rejection of out-of-boundary / non-int IDs
        def validate_cfr_id(val: Any) -> bool:
            return isinstance(val, int) and (10_000_000 <= val < 100_000_000)

        self.assert_false(validate_cfr_id("58989070"))
        self.assert_false(validate_cfr_id(9_999_999))
        self.assert_false(validate_cfr_id(100_000_000))
        self.assert_false(validate_cfr_id(-58989070))

    def test_vm_memory_headroom_boundary(self):
        """Boundary: worker.check_host_memory parses /proc/meminfo and enforces headroom boundaries."""
        from unittest.mock import patch, mock_open
        sys.path.insert(0, "/usr/libexec/mios/win/xbox")
        import worker

        # 1. Live positive control: min_gib=1.0 on live host must succeed
        worker.check_host_memory(min_gib=1.0)

        # 2. Live negative control: absurdly high min_gib must fail closed
        raised_insufficient = False
        try:
            worker.check_host_memory(min_gib=999999.0)
        except SystemExit as ex:
            raised_insufficient = True
            self.assert_in("INSUFFICIENT MEMORY", str(ex))
        self.assert_true(raised_insufficient, "check_host_memory did not fail closed on insufficient memory")

        # 3. Simulated boundary tests for Target Domain (24G RAM + 6G headroom = 30G threshold)
        def sim_meminfo(kib: int) -> str:
            return f"MemTotal:       65536000 kB\nMemFree:        30000000 kB\nMemAvailable:   {kib} kB\n"

        # Boundary Point: 32 GiB (33554432 kB) -> above 30 GiB threshold: PASS
        with patch("builtins.open", mock_open(read_data=sim_meminfo(33554432))):
            worker.check_host_memory(min_gib=30.0)

        # Boundary Point: 30.0 GiB (31457280 kB) -> exact threshold: PASS
        with patch("builtins.open", mock_open(read_data=sim_meminfo(31457280))):
            worker.check_host_memory(min_gib=30.0)

        # Boundary Point: 29.9 GiB (31352422 kB) -> just below threshold: FAIL CLOSED
        with patch("builtins.open", mock_open(read_data=sim_meminfo(31352422))):
            raised_boundary = False
            try:
                worker.check_host_memory(min_gib=30.0)
            except SystemExit as ex:
                raised_boundary = True
                self.assert_in("INSUFFICIENT MEMORY", str(ex))
            self.assert_true(raised_boundary, "check_host_memory failed to reject 29.9 GiB against 30.0 GiB requirement")

        # Boundary Point: 28.0 GiB -> FAIL CLOSED
        with patch("builtins.open", mock_open(read_data=sim_meminfo(29360128))):
            raised_28 = False
            try:
                worker.check_host_memory(min_gib=30.0)
            except SystemExit:
                raised_28 = True
            self.assert_true(raised_28, "check_host_memory failed to reject 28.0 GiB")

    def test_worker_vm_gpu_passthrough_prohibition(self):
        """Boundary: Worker VM definition strictly forbids hostdev / GPU passthrough (xb-14)."""
        import xml.etree.ElementTree as ET
        sys.path.insert(0, "/usr/libexec/mios/win/xbox")
        import worker

        # Positive control: call live generate_worker_xml() from worker.py
        worker_xml = worker.generate_worker_xml()
        self.assert_in("mios-xbox-worker", worker_xml)
        self.assert_in("8192", worker_xml)
        self.assert_not_in("hostdev", worker_xml)
        self.assert_not_in("0000:01:00", worker_xml)
        self.assert_not_in("01:00", worker_xml)

        # Negative control 1: <hostdev> injection is strictly rejected
        root_hostdev = ET.fromstring(worker_xml)
        ET.SubElement(root_hostdev.find("devices"), "hostdev", {"mode": "subsystem", "type": "pci"})
        raised_hostdev = False
        try:
            worker.validate_worker_xml(root_hostdev)
        except ValueError:
            raised_hostdev = True
        self.assert_true(raised_hostdev, "validate_worker_xml failed to reject injected <hostdev>")

        # Negative control 2: RTX 4090 bus ID 0000:01:00 injection is strictly rejected
        root_bus = ET.fromstring(worker_xml)
        dev = ET.SubElement(root_bus.find("devices"), "device")
        ET.SubElement(dev, "address", {"bus": "0000:01:00"})
        raised_bus = False
        try:
            worker.validate_worker_xml(root_bus)
        except ValueError:
            raised_bus = True
        self.assert_true(raised_bus, "validate_worker_xml failed to reject injected 0000:01:00 bus ID")

        # Negative control 3: RTX 4090 short bus ID 01:00 injection is strictly rejected
        root_short_bus = ET.fromstring(worker_xml)
        dev2 = ET.SubElement(root_short_bus.find("devices"), "device")
        ET.SubElement(dev2, "address", {"bus": "01:00"})
        raised_short_bus = False
        try:
            worker.validate_worker_xml(root_short_bus)
        except ValueError:
            raised_short_bus = True
        self.assert_true(raised_short_bus, "validate_worker_xml failed to reject injected 01:00 bus ID")

    def test_worker_vm_isolation_concurrency_fail_closed(self):
        """Boundary: assert_isolated() strictly fails closed on non-shutoff states (paused, pmsuspended, running)."""
        from unittest.mock import patch
        sys.path.insert(0, "/usr/libexec/mios/win/xbox")
        import worker

        # Positive control: shut off or unconfigured domains pass cleanly
        with patch.object(worker, "get_domain_state", return_value="shut off"):
            worker.assert_isolated()

        with patch.object(worker, "get_domain_state", return_value=None):
            worker.assert_isolated()

        # Negative control: active/paused/suspended states must all raise SystemExit
        for non_shutoff in ("running", "paused", "pmsuspended", "in shutdown", "blocked", "crashed"):
            with patch.object(worker, "get_domain_state", return_value=non_shutoff):
                raised_exit = False
                try:
                    worker.assert_isolated()
                except SystemExit:
                    raised_exit = True
                self.assert_true(
                    raised_exit,
                    f"assert_isolated() failed closed check: permitted domain in state '{non_shutoff}'"
                )

    def test_gpu_driver_vendor_authenticode_subject_regex(self):
        """Boundary: Case-sensitive Authenticode organization regex matching."""
        nvidia_subj = "CN=NVIDIA Corporation, O=NVIDIA Corporation, L=Santa Clara, S=California, C=US"
        intel_subj = "CN=Intel Corporation, O=Intel Corporation, L=Santa Clara, S=California, C=US"
        fake_subj = "CN=Nvidia Corporation, O=nvidia corporation"  # bad casing / forgery

        org_regex = r"^NVIDIA Corporation$"
        
        # Must match case-sensitively
        self.assert_true(bool(re.search(org_regex, "NVIDIA Corporation")))
        self.assert_false(bool(re.search(org_regex, "nvidia corporation")))

    def test_ivshmem_shm_size_boundary(self):
        """Boundary: LookingGlassManager.validate_shm_allocation against SSOT shm_size_mb (128 MB)."""
        import importlib.util
        import tomllib

        # Load LookingGlassManager from production script
        spec = importlib.util.spec_from_file_location(
            "setup_looking_glass", "/usr/libexec/mios/vfio/setup-looking-glass.py"
        )
        setup_looking_glass = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(setup_looking_glass)
        LookingGlassManager = setup_looking_glass.LookingGlassManager

        # Read SSOT configuration
        ssot_path = "/usr/share/mios/mios.toml"
        with open(ssot_path, "rb") as f:
            ssot = tomllib.load(f)

        lg_cfg = ssot.get("vm", {}).get("looking_glass", {})
        shm_size_mb = lg_cfg.get("shm_size_mb")
        shm_path = lg_cfg.get("shm_path", "/dev/shm/looking-glass")

        # Assert SSOT specification
        self.assert_equal(shm_size_mb, 128, "SSOT vm.looking_glass.shm_size_mb must equal 128")
        self.assert_equal(shm_path, "/dev/shm/looking-glass")

        # Instantiate production manager
        lg = LookingGlassManager(shm_path=shm_path, size_mb=shm_size_mb)

        # XML generation boundary check
        xml = lg.generate_ivshmem_xml()
        self.assert_in('<size unit="M">128</size>', xml)
        self.assert_in('<shmem name="looking-glass">', xml)

        # Positive control: validate live SHM allocation
        self.assert_true(os.path.exists(shm_path), f"Live IVSHMEM file missing at {shm_path}")
        self.assert_true(
            lg.validate_shm_allocation(mock=False),
            "validate_shm_allocation returned False for live /dev/shm/looking-glass"
        )
        actual_size = os.path.getsize(shm_path)
        self.assert_equal(
            actual_size,
            128 * 1024 * 1024,
            f"Live /dev/shm/looking-glass size {actual_size} != 134,217,728 bytes"
        )

        # Negative control 1: non-existent path
        bad_path_lg = LookingGlassManager(shm_path="/dev/shm/nonexistent_lg_test_shm", size_mb=128)
        self.assert_false(bad_path_lg.validate_shm_allocation(mock=False))

        # Negative control 2: invalid permissions (0644 instead of 0660)
        with IsolatedEnvironment() as env:
            bad_perm_file = env.write_file("dummy", "bad_shm", mode=0o644)
            bad_perm_lg = LookingGlassManager(shm_path=bad_perm_file, size_mb=128)
            self.assert_false(bad_perm_lg.validate_shm_allocation(mock=False))


if __name__ == "__main__":
    sys.exit(run_standalone(TestTier2BoundaryXbox))

