#!/usr/bin/env python3
# AI-hint: Consolidated virt test suite: IOMMU parser, VFIO bind, hugepages, virtiofs, vTPM, PipeWire bridge, microVM bridge/sandbox, plus adversarial virt/display (Looking Glass, multimonitor) cases.
"""Consolidated MiOS virtualization and display test suite."""

from __future__ import annotations


# ======================================================================
# from tests/test-virt.py
# ======================================================================
import configparser
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

avd__HERE = os.path.dirname(os.path.abspath(__file__))
avd__ROOT = os.path.normpath(os.path.join(avd__HERE, ".."))
avd__VIRT_DIR = os.path.join(avd__ROOT, "usr", "libexec", "mios", "virt")
avd__DISP_DIR = os.path.join(avd__ROOT, "usr", "libexec", "mios", "display")

def avd__import_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    raise ImportError(f"Could not load module {name} from {path}")

avd_iommu_parser_mod = avd__import_module("iommu_parser", os.path.join(avd__VIRT_DIR, "iommu_parser.py"))
avd_vfio_bind_mod = avd__import_module("vfio_bind", os.path.join(avd__VIRT_DIR, "vfio_bind.py"))
avd_looking_glass_mod = avd__import_module("looking_glass", os.path.join(avd__DISP_DIR, "looking_glass.py"))
avd_pipewire_bridge_mod = avd__import_module("pipewire_bridge", os.path.join(avd__VIRT_DIR, "pipewire_bridge.py"))
avd_vtpm_provision_mod = avd__import_module("vtpm_provision", os.path.join(avd__VIRT_DIR, "vtpm_provision.py"))
avd_hugepages_mgr_mod = avd__import_module("hugepages_mgr", os.path.join(avd__VIRT_DIR, "hugepages_mgr.py"))
avd_virtiofs_mount_mod = avd__import_module("virtiofs_mount", os.path.join(avd__VIRT_DIR, "virtiofs_mount.py"))
avd_multimonitor_sync_mod = avd__import_module("multimonitor_sync", os.path.join(avd__DISP_DIR, "multimonitor_sync.py"))

class avd_TestAdversarialIOMMUParser(unittest.TestCase):
    """Adversarial stress-testing for task_357 IOMMU Group Parser & ACS Override Topology Auditor."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="adv_test_iommu_")
        self.sysfs_root = self.tmp_dir

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _setup_device(
        self,
        bdf: str,
        group_id: int,
        vendor: str = "0x10de",
        device: str = "0x2484",
        pci_class: str = "0x030000",
        boot_vga: str = "0",
        driver: str | None = "nvidia",
    ) -> str:
        safe_bdf = bdf.replace(":", "_")
        dev_dir = os.path.join(self.sysfs_root, "bus", "pci", "devices", safe_bdf)
        os.makedirs(dev_dir, exist_ok=True)
        with open(os.path.join(dev_dir, "vendor"), "w") as f:
            f.write(f"{vendor}\n")
        with open(os.path.join(dev_dir, "device"), "w") as f:
            f.write(f"{device}\n")
        with open(os.path.join(dev_dir, "class"), "w") as f:
            f.write(f"{pci_class}\n")
        with open(os.path.join(dev_dir, "boot_vga"), "w") as f:
            f.write(f"{boot_vga}\n")

        # Group directory
        grp_dev_dir = os.path.join(self.sysfs_root, "kernel", "iommu_groups", str(group_id), "devices", safe_bdf)
        os.makedirs(grp_dev_dir, exist_ok=True)
        with open(os.path.join(grp_dev_dir, "vendor"), "w") as f:
            f.write(f"{vendor}\n")
        with open(os.path.join(grp_dev_dir, "device"), "w") as f:
            f.write(f"{device}\n")
        with open(os.path.join(grp_dev_dir, "class"), "w") as f:
            f.write(f"{pci_class}\n")
        with open(os.path.join(grp_dev_dir, "boot_vga"), "w") as f:
            f.write(f"{boot_vga}\n")

        if driver:
            drv_dir = os.path.join(self.sysfs_root, "bus", "pci", "drivers", driver)
            os.makedirs(drv_dir, exist_ok=True)
            with open(os.path.join(dev_dir, "driver_name"), "w") as f:
                f.write(f"{driver}\n")
            with open(os.path.join(grp_dev_dir, "driver_name"), "w") as f:
                f.write(f"{driver}\n")

        return dev_dir

    def test_malformed_bdf_inputs(self) -> None:
        """Adversarial Test: Path traversal, out-of-range functions, invalid formats."""
        parser = avd_iommu_parser_mod.IOMMUParser(sysfs_root=self.sysfs_root)

        malformed_bdfs = [
            "",
            "   ",
            "invalid",
            "0000:01:00",  # missing function
            "0000:01:00.8",  # function > 7
            "0000:01:00.f",  # hex function > 7
            "00000:01:00.0",  # domain > 4 hex
            "0000:01:00.0/../../../etc/passwd",  # path traversal
            "0000:01:00.0; rm -rf /",  # command injection string
            "00:00",  # incomplete short form
            "GGGG:01:00.0",  # non-hex
        ]

        for bdf in malformed_bdfs:
            with self.assertRaises(ValueError, msg=f"Should raise ValueError on malformed BDF: {bdf}"):
                avd_iommu_parser_mod.IOMMUParser.parse_bdf(bdf)

            # audit_isolation should catch gracefully without throwing unhandled exceptions
            res = parser.audit_isolation(bdf)
            self.assertEqual(res["status"], "error")
            self.assertFalse(res["isolated"])
            self.assertIn("error", res)

            # find_device should return None gracefully
            dev = parser.find_device(bdf)
            self.assertIsNone(dev)

    def test_nested_quad_function_gpu_clean_isolation(self) -> None:
        """Adversarial Test: 4 functions on same slot (VGA, Audio, USB xHCI, Type-C UCSI) in group 15."""
        # 0000:01:00.0 VGA
        self._setup_device("0000:01:00.0", group_id=15, vendor="0x10de", device="0x2484", pci_class="0x030000")
        # 0000:01:00.1 Audio
        self._setup_device("0000:01:00.1", group_id=15, vendor="0x10de", device="0x228b", pci_class="0x040300")
        # 0000:01:00.2 USB Controller
        self._setup_device("0000:01:00.2", group_id=15, vendor="0x10de", device="0x1ad8", pci_class="0x0c0330")
        # 0000:01:00.3 Type-C UCSI
        self._setup_device("0000:01:00.3", group_id=15, vendor="0x10de", device="0x1ad9", pci_class="0x0c8000")

        parser = avd_iommu_parser_mod.IOMMUParser(sysfs_root=self.sysfs_root)

        # Audit function 0 (VGA)
        res0 = parser.audit_isolation("0000:01:00.0")
        self.assertEqual(res0["status"], "pass")
        self.assertTrue(res0["isolated"])
        self.assertEqual(res0["iommu_group"], 15)
        self.assertEqual(len(res0["companions"]), 4)
        self.assertEqual(len(res0["conflicts"]), 0)
        self.assertIsNone(res0["uki_kargs"])

        # Audit function 1 (Audio)
        res1 = parser.audit_isolation("0000:01:00.1")
        self.assertEqual(res1["status"], "pass")
        self.assertTrue(res1["isolated"])
        self.assertEqual(len(res1["companions"]), 4)
        self.assertEqual(len(res1["conflicts"]), 0)

        # Invariant checks
        self.assertIn("uki_vs_mok", res0["invariants"])
        self.assertIn("venus_vs_cuda", res0["invariants"])

    def test_shared_root_port_conflict_requires_acs_override(self) -> None:
        """Adversarial Test: GPU in same group as PCIe Root Port and SATA Controller -> Conflict detected."""
        # 0000:01:00.0 GPU VGA
        self._setup_device("0000:01:00.0", group_id=2, vendor="0x10de", device="0x2484", pci_class="0x030000")
        # 0000:01:00.1 GPU Audio
        self._setup_device("0000:01:00.1", group_id=2, vendor="0x10de", device="0x228b", pci_class="0x040300")
        # 0000:00:01.0 PCIe Root Port (Conflict!)
        self._setup_device("0000:00:01.0", group_id=2, vendor="0x8086", device="0x460d", pci_class="0x060400")
        # 0000:00:17.0 SATA Controller (Conflict!)
        self._setup_device("0000:00:17.0", group_id=2, vendor="0x8086", device="0x7a62", pci_class="0x010601")

        parser = avd_iommu_parser_mod.IOMMUParser(sysfs_root=self.sysfs_root)
        res = parser.audit_isolation("0000:01:00.0")

        self.assertEqual(res["status"], "conflict")
        self.assertFalse(res["isolated"])
        self.assertEqual(res["iommu_group"], 2)
        self.assertEqual(len(res["companions"]), 2)
        self.assertEqual(len(res["conflicts"]), 2)
        self.assertEqual(res["uki_kargs"], "pcie_acs_override=downstream,multifunction")
        self.assertIn("Unified Kernel Image (UKI)", res["recommendation"])
        self.assertIsNotNone(res["security_warning"])

    def test_device_not_found_in_sysfs(self) -> None:
        """Adversarial Test: Querying non-existent BDF."""
        parser = avd_iommu_parser_mod.IOMMUParser(sysfs_root=self.sysfs_root)
        res = parser.audit_isolation("0000:99:00.0")
        self.assertEqual(res["status"], "not_found")
        self.assertFalse(res["isolated"])

class avd_TestAdversarialVFIOBinder(unittest.TestCase):
    """Adversarial stress-testing for task_358 Dynamic Runtime VFIO Device Unbind and Rebind Utility."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="adv_test_vfio_")
        self.sysfs_root = self.tmp_dir

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _setup_pci_device(
        self,
        bdf: str,
        vendor: str = "0x10de",
        device: str = "0x2484",
        driver: str = "nvidia",
        boot_vga: bool = False,
    ) -> str:
        safe_bdf = bdf.replace(":", "_")
        dev_dir = os.path.join(self.sysfs_root, "bus", "pci", "devices", safe_bdf)
        os.makedirs(dev_dir, exist_ok=True)
        with open(os.path.join(dev_dir, "vendor"), "w") as f:
            f.write(f"{vendor}\n")
        with open(os.path.join(dev_dir, "device"), "w") as f:
            f.write(f"{device}\n")
        with open(os.path.join(dev_dir, "boot_vga"), "w") as f:
            f.write("1\n" if boot_vga else "0\n")
        with open(os.path.join(dev_dir, "current_driver"), "w") as f:
            f.write(f"{driver}\n")
        with open(os.path.join(dev_dir, "driver_override"), "w") as f:
            f.write("(null)\n")

        # Driver control dirs
        for drv in [driver, "vfio-pci", "snd_hda_intel", "amdgpu", "i915"]:
            drv_dir = os.path.join(self.sysfs_root, "bus", "pci", "drivers", drv)
            os.makedirs(drv_dir, exist_ok=True)
            with open(os.path.join(drv_dir, "bind"), "w") as f:
                f.write("")
            with open(os.path.join(drv_dir, "unbind"), "w") as f:
                f.write("")
            with open(os.path.join(drv_dir, "new_id"), "w") as f:
                f.write("")

        return dev_dir

    def test_primary_gpu_unbind_protection_boot_vga(self) -> None:
        """Adversarial Test: Primary host display GPU (boot_vga=1) unbind is refused unless forced."""
        # Setup primary host GPU on 0000:00:02.0 with boot_vga=1
        self._setup_pci_device("0000:00:02.0", vendor="0x8086", device="0x4680", driver="i915", boot_vga=True)

        binder = avd_vfio_bind_mod.VFIOBinder(sysfs_root=self.sysfs_root)
        self.assertTrue(binder.is_primary_gpu("0000:00:02.0"))

        # 1. Unbind attempt without force -> MUST BE REFUSED
        res_refused = binder.bind_to_vfio("0000:00:02.0", force=False)
        self.assertEqual(res_refused["status"], "refused")
        self.assertFalse(res_refused["bound"])
        self.assertIn("primary host display", res_refused["error"])
        self.assertIn("gpu_fractioning_limit", res_refused["invariants"])
        self.assertIn("venus_vs_cuda", res_refused["invariants"])

        # Verify device remained bound to i915
        state = binder.get_device_state("0000:00:02.0")
        self.assertEqual(state.current_driver, "i915")

        # 2. Unbind with force=True -> Succeeded
        res_forced = binder.bind_to_vfio("0000:00:02.0", force=True)
        self.assertEqual(res_forced["status"], "success")
        self.assertTrue(res_forced["bound"])

    def test_companion_functions_bound_and_rebound_atomically(self) -> None:
        """Adversarial Test: Multi-function slot (VGA + Audio) bound to vfio-pci and rebound to host."""
        # Function 0: NVIDIA GPU
        self._setup_pci_device("0000:01:00.0", vendor="0x10de", device="0x2484", driver="nvidia", boot_vga=False)
        # Function 1: Audio Controller
        self._setup_pci_device("0000:01:00.1", vendor="0x10de", device="0x228b", driver="snd_hda_intel", boot_vga=False)

        binder = avd_vfio_bind_mod.VFIOBinder(sysfs_root=self.sysfs_root)
        siblings = binder.get_slot_siblings("0000:01:00.0")
        self.assertEqual(len(siblings), 2)
        self.assertIn("0000:01:00.0", siblings)
        self.assertIn("0000:01:00.1", siblings)

        # 1. Bind to VFIO
        bind_res = binder.bind_to_vfio("0000:01:00.0")
        self.assertEqual(bind_res["status"], "success")
        self.assertEqual(len(bind_res["siblings"]), 2)

        # Check driver overrides and state
        state0 = binder.get_device_state("0000:01:00.0")
        state1 = binder.get_device_state("0000:01:00.1")
        self.assertEqual(state0.current_driver, "vfio-pci")
        self.assertEqual(state1.current_driver, "vfio-pci")
        self.assertEqual(state0.driver_override, "vfio-pci")
        self.assertEqual(state1.driver_override, "vfio-pci")

        # 2. Rebind back to host drivers
        rebind_res = binder.rebind_to_host("0000:01:00.0")
        self.assertEqual(rebind_res["status"], "success")

        # Check state after rebind
        state0_after = binder.get_device_state("0000:01:00.0")
        state1_after = binder.get_device_state("0000:01:00.1")
        self.assertEqual(state0_after.current_driver, "nvidia")
        self.assertEqual(state1_after.current_driver, "snd_hda_intel")
        self.assertIsNone(state0_after.driver_override)
        self.assertIsNone(state1_after.driver_override)

class avd_TestAdversarialLookingGlass(unittest.TestCase):
    """Adversarial stress-testing for task_359 Looking Glass B6 Direct SPICE Host Input Manager."""

    def test_corrupted_and_edge_ini_parsing(self) -> None:
        """Adversarial Test: Corrupted INI formatting, missing sections, custom options, case-preservation."""
        manager = avd_looking_glass_mod.LookingGlassConfigManager(
            vm_name="win11-pro",
            shm_file="/dev/kvmfr0",
            escape_key="KEY_F12",
            full_screen=True,
        )

        ini_str = manager.generate_ini(overrides={"input": {"customOption": "value123", "mouseSens": 5}})
        parsed = manager.parse_ini(ini_str)

        self.assertEqual(parsed["app"]["shmFile"], "/dev/kvmfr0")
        self.assertEqual(parsed["input"]["escapeKey"], "KEY_F12")
        self.assertTrue(parsed["win"]["fullScreen"])
        self.assertEqual(parsed["input"]["mouseSens"], 5)
        self.assertEqual(parsed["input"]["customOption"], "value123")

    def test_custom_keybindings_syntax_hyprland_and_gnome(self) -> None:
        """Adversarial Test: Validate Hyprland and GNOME rules syntax."""
        manager = avd_looking_glass_mod.LookingGlassConfigManager(
            vm_name="gaming-vm",
            shm_file="/dev/kvmfr1",
            escape_key="KEY_RIGHTCTRL",
        )

        hypr_rules = manager.generate_hyprland_rules(app_class="custom-lg", title_pattern="Custom.*")
        self.assertIn("windowrulev2 = fullscreen, class:^(custom-lg)$, title:^(Custom.*)$", hypr_rules)
        self.assertIn("windowrulev2 = idleinhibit always, class:^(custom-lg)$", hypr_rules)
        self.assertIn("windowrulev2 = immediate, class:^(custom-lg)$", hypr_rules)
        self.assertIn("bind = $mainMod, Scroll_Lock, exec, /usr/bin/looking-glass-client -f /dev/kvmfr1", hypr_rules)

        gnome_rules = manager.generate_gnome_rules()
        self.assertIn("gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings", gnome_rules)
        self.assertIn('command "/usr/bin/looking-glass-client -f /dev/kvmfr1"', gnome_rules)

    def test_launch_args_synthesis(self) -> None:
        """Adversarial Test: Verify CLI argument synthesis for Looking Glass."""
        manager = avd_looking_glass_mod.LookingGlassConfigManager(
            vm_name="test-vm",
            shm_file="/dev/kvmfr0",
            spice_socket="/run/libvirt/qemu/test-vm-spice.sock",
            escape_key="KEY_SCROLLLOCK",
            full_screen=True,
            allow_dma=True,
        )

        args = manager.build_client_launch_args(extra_args=["--extra-flag"])
        self.assertEqual(args[0], "looking-glass-client")
        self.assertIn("-f", args)
        self.assertIn("/dev/kvmfr0", args)
        self.assertIn("spice:host=/run/libvirt/qemu/test-vm-spice.sock", args)
        self.assertIn("spice:port=0", args)
        self.assertIn("input:escapeKey=KEY_SCROLLLOCK", args)
        self.assertIn("win:fullScreen=true", args)
        self.assertIn("app:allowDMA=true", args)
        self.assertIn("--extra-flag", args)

class avd_TestAdversarialPipeWireBridge(unittest.TestCase):
    """Adversarial stress-testing for task_360 PipeWire Low-Latency Audio Bridge."""

    def test_latency_sla_calculations_and_extreme_inputs(self) -> None:
        """Adversarial Test: Extreme sample rates and quantums with strict sub-5ms SLA validation."""
        calc = avd_pipewire_bridge_mod.PipeWireBridgeManager.calculate_latency_ms

        # Extreme high frequency: 16 samples @ 44100 Hz -> 0.363 ms (PASS)
        lat_16_44 = calc(16, 44100)
        self.assertEqual(lat_16_44, 0.363)
        self.assertTrue(lat_16_44 <= 5.0)

        # Extreme high frequency: 1024 samples @ 192000 Hz -> 5.333 ms (FAIL > 5ms)
        lat_1024_192 = calc(1024, 192000)
        self.assertEqual(lat_1024_192, 5.333)
        self.assertFalse(lat_1024_192 <= 5.0)

        # Standard low latency: 64 samples @ 48000 Hz -> 1.333 ms (PASS)
        lat_64_48 = calc(64, 48000)
        self.assertEqual(lat_64_48, 1.333)
        self.assertTrue(lat_64_48 <= 5.0)

        # High quantum standard rate: 512 samples @ 48000 Hz -> 10.667 ms (FAIL)
        lat_512_48 = calc(512, 48000)
        self.assertEqual(lat_512_48, 10.667)
        self.assertFalse(lat_512_48 <= 5.0)

        # Boundary test at exactly 5.0ms: 240 / 48000 = 5.0ms (PASS)
        lat_exact_5 = calc(240, 48000)
        self.assertEqual(lat_exact_5, 5.0)
        self.assertTrue(lat_exact_5 <= 5.0)

        # Boundary test slightly exceeding 5.0ms: 241 / 48000 = 5.021ms (FAIL)
        lat_exceed_5 = calc(241, 48000)
        self.assertEqual(lat_exceed_5, 5.021)
        self.assertFalse(lat_exceed_5 <= 5.0)

        # Invalid zero and negative inputs
        with self.assertRaises(ValueError):
            calc(0, 48000)
        with self.assertRaises(ValueError):
            calc(-64, 48000)
        with self.assertRaises(ValueError):
            calc(64, 0)
        with self.assertRaises(ValueError):
            calc(64, -48000)

    def test_sla_validation_method(self) -> None:
        """Adversarial Test: validate_latency_sla method returns pass/fail and formula metadata."""
        mgr = avd_pipewire_bridge_mod.PipeWireBridgeManager(quantum=64, sample_rate=48000)
        res_pass = mgr.validate_latency_sla()
        self.assertEqual(res_pass["status"], "pass")
        self.assertTrue(res_pass["passed"])
        self.assertEqual(res_pass["latency_ms"], 1.333)

        res_fail = mgr.validate_latency_sla(quantum=1024, sample_rate=48000)
        self.assertEqual(res_fail["status"], "fail")
        self.assertFalse(res_fail["passed"])
        self.assertEqual(res_fail["latency_ms"], 21.333)

    def test_ivshmem_xml_and_systemd_service_synthesis(self) -> None:
        """Adversarial Test: Synthesized IVSHMEM XML and systemd units."""
        mgr = avd_pipewire_bridge_mod.PipeWireBridgeManager(
            shm_path="/dev/shm/scream-gaming",
            size_mb=4,
            sample_rate=96000,
            quantum=128,
            backend="jack",
            node_name="scream-gaming-bridge",
        )

        xml = mgr.generate_ivshmem_xml(shmem_name="scream-gaming")
        self.assertIn('<shmem name="scream-gaming">', xml)
        self.assertIn('<model type="ivshmem-plain"/>', xml)
        self.assertIn('<size unit="M">4</size>', xml)

        service = mgr.generate_systemd_service()
        self.assertIn('Environment="PIPEWIRE_LATENCY=128/96000"', service)
        self.assertIn('Environment="JACK_PROMISCUOUS_SERVER=1"', service)
        self.assertIn("ExecStart=/usr/bin/scream -m /dev/shm/scream-gaming -o jack -t 128", service)
        self.assertIn("LimitRTPRIO=95", service)
        self.assertIn("LimitMEMLOCK=infinity", service)

class avd_TestAdversarialVTPMProvision(unittest.TestCase):
    """Adversarial stress-testing for task_361 Virtual TPM2 Provisioning."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="adv_test_vtpm_")
        self.state_root = os.path.join(self.tmp_dir, "var", "lib", "libvirt", "swtpm")
        self.sock_root = os.path.join(self.tmp_dir, "run", "libvirt", "swtpm")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_invalid_and_malicious_vm_ids(self) -> None:
        """Adversarial Test: Path traversal, command injection in VM ID."""
        prov = avd_vtpm_provision_mod.VTPMProvisioner(state_root=self.state_root, sock_root=self.sock_root)

        malicious_ids = [
            "",
            "   ",
            "../../etc/shadow",
            "win11; rm -rf /",
            "win11/subdomain",
            "win11\x00nullbyte",
            "win11$PATH",
            "win11`id`",
            "*",
        ]

        for vm_id in malicious_ids:
            with self.assertRaises(ValueError, msg=f"Should reject malicious VM ID: {vm_id}"):
                prov.get_state_dir(vm_id)

    def test_multi_vm_state_isolation_and_scoped_cleanup(self) -> None:
        """Adversarial Test: Multiple VMs provisioned; purging one does NOT touch the other."""
        prov = avd_vtpm_provision_mod.VTPMProvisioner(state_root=self.state_root, sock_root=self.sock_root)

        # Provision VM 1
        res1 = prov.provision("win11-prod")
        self.assertEqual(res1["status"], "provisioned")
        self.assertTrue(os.path.exists(os.path.join(res1["state_dir"], "tpm2-00.permall")))

        # Provision VM 2
        res2 = prov.provision("win11-dev")
        self.assertEqual(res2["status"], "provisioned")
        self.assertTrue(os.path.exists(os.path.join(res2["state_dir"], "tpm2-00.permall")))

        # Verify state directories are strictly separate
        self.assertNotEqual(res1["state_dir"], res2["state_dir"])
        self.assertNotEqual(res1["socket_path"], res2["socket_path"])

        # Purge VM 1 only
        clean1 = prov.cleanup("win11-prod", purge_state=True)
        self.assertTrue(clean1["state_purged"])
        self.assertFalse(os.path.exists(res1["state_dir"]))

        # Verify VM 2 state is completely intact
        self.assertTrue(os.path.exists(res2["state_dir"]))
        st2 = prov.get_status("win11-dev")
        self.assertTrue(st2["provisioned"])
        self.assertTrue(st2["has_nvram"])

    def test_domain_xml_schema_and_model(self) -> None:
        """Adversarial Test: libvirt domain XML structure matching Windows 11 CRB TPM 2.0."""
        prov = avd_vtpm_provision_mod.VTPMProvisioner(state_root=self.state_root, sock_root=self.sock_root)
        xml = prov.generate_domain_xml("win11", tpm_version="2.0", model="tpm-crb")

        self.assertIn('<tpm model="tpm-crb">', xml)
        self.assertIn('<backend type="emulator" version="2.0">', xml)
        self.assertIn('<source type="unix" path="', xml)
        self.assertIn('win11-swtpm.sock"/>', xml)

class avd_TestAdversarialHugepagesManager(unittest.TestCase):
    """Adversarial stress-testing for task_362 Hugepages Automatic Allocation & Compaction Manager."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="adv_test_hp_")
        self.sysfs_root = self.tmp_dir
        self.proc_root = self.tmp_dir

        # Setup synthetic sysfs hugepages pools
        self.p2m_dir = os.path.join(self.sysfs_root, "kernel", "mm", "hugepages", "hugepages-2048kB")
        self.p1g_dir = os.path.join(self.sysfs_root, "kernel", "mm", "hugepages", "hugepages-1048576kB")
        os.makedirs(self.p2m_dir, exist_ok=True)
        os.makedirs(self.p1g_dir, exist_ok=True)

        with open(os.path.join(self.p2m_dir, "nr_hugepages"), "w") as f:
            f.write("0\n")
        with open(os.path.join(self.p2m_dir, "free_hugepages"), "w") as f:
            f.write("0\n")
        with open(os.path.join(self.p1g_dir, "nr_hugepages"), "w") as f:
            f.write("0\n")
        with open(os.path.join(self.p1g_dir, "free_hugepages"), "w") as f:
            f.write("0\n")

        # Setup proc compaction node
        proc_vm = os.path.join(self.proc_root, "sys", "vm")
        os.makedirs(proc_vm, exist_ok=True)
        with open(os.path.join(proc_vm, "compact_memory"), "w") as f:
            f.write("0\n")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_2m_and_1g_page_count_math_and_validation(self) -> None:
        """Adversarial Test: Exact page count calculations, invalid sizes, odd MB values."""
        mgr = avd_hugepages_mgr_mod.HugepagesManager(sysfs_root=self.sysfs_root, proc_root=self.proc_root)

        # 2MB calculations
        self.assertEqual(mgr.calculate_page_count(2, "2M"), 1)
        self.assertEqual(mgr.calculate_page_count(2048, "2M"), 1024)
        self.assertEqual(mgr.calculate_page_count(8192, "2M"), 4096)
        self.assertEqual(mgr.calculate_page_count(16384, "2M"), 8192)

        # Odd MB with 2M page size must be rejected
        with self.assertRaises(ValueError):
            mgr.calculate_page_count(2049, "2M")

        # 1GB calculations
        self.assertEqual(mgr.calculate_page_count(1024, "1G"), 1)
        self.assertEqual(mgr.calculate_page_count(8192, "1G"), 8)
        self.assertEqual(mgr.calculate_page_count(16384, "1G"), 16)
        self.assertEqual(mgr.calculate_page_count(32768, "1G"), 32)

        # Non-1024 multiple with 1G page size must be rejected
        self.assertEqual(mgr.calculate_page_count(2048, "1G"), 2)

        with self.assertRaises(ValueError):
            mgr.calculate_page_count(1500, "1G")

        # Zero or negative sizes
        with self.assertRaises(ValueError):
            mgr.calculate_page_count(0, "2M")
        with self.assertRaises(ValueError):
            mgr.calculate_page_count(-8192, "2M")

        # Unsupported page size
        with self.assertRaises(ValueError):
            mgr.calculate_page_count(8192, "4K")

    def test_allocation_compaction_and_release_lifecycle(self) -> None:
        """Adversarial Test: Allocation triggers compaction, updates pool, and release decrements cleanly."""
        mgr = avd_hugepages_mgr_mod.HugepagesManager(sysfs_root=self.sysfs_root, proc_root=self.proc_root)

        # 1. Allocate 8192 MB of 2M hugepages (4096 pages)
        alloc_res = mgr.allocate(8192, page_size="2M", compact=True)
        self.assertEqual(alloc_res["status"], "allocated")
        self.assertEqual(alloc_res["requested_pages"], 4096)
        self.assertEqual(alloc_res["target_pages"], 4096)
        self.assertTrue(alloc_res["compaction"]["compaction_triggered"])

        # Check compaction node was written
        with open(os.path.join(self.proc_root, "sys", "vm", "compact_memory"), "r") as f:
            self.assertEqual(f.read().strip(), "1")

        # Check sysfs pool status
        pool_st = mgr.get_pool_status("2M")
        self.assertEqual(pool_st["nr_hugepages"], 4096)
        self.assertEqual(pool_st["allocated_mb"], 8192)

        # 2. Allocate an additional 4096 MB (2048 pages)
        alloc_res2 = mgr.allocate(4096, page_size="2M", compact=False)
        self.assertEqual(alloc_res2["target_pages"], 6144)

        # 3. Release 8192 MB (4096 pages)
        rel_res = mgr.release(8192, page_size="2M")
        self.assertEqual(rel_res["status"], "released")
        self.assertEqual(rel_res["pages_freed"], 4096)
        self.assertEqual(rel_res["remaining_pages"], 2048)

        # 4. Release remaining and ensure no negative underflow
        rel_res2 = mgr.release(8192, page_size="2M")
        self.assertEqual(rel_res2["remaining_pages"], 0)

class avd_TestAdversarialVirtIOFS(unittest.TestCase):
    """Adversarial stress-testing for task_363 VirtIO-FS Shared Directory Mount Daemon."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="adv_test_vfs_")
        self.run_root = os.path.join(self.tmp_dir, "run", "libvirt")
        self.shared_dir = os.path.join(self.tmp_dir, "var", "home", "mios", "Shared")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_malformed_mount_tags_and_vm_ids(self) -> None:
        """Adversarial Test: Injection strings in mount tag."""
        vfs = avd_virtiofs_mount_mod.VirtioFSManager(run_root=self.run_root, default_shared_dir=self.shared_dir)

        malformed_tags = [
            "",
            "   ",
            "../escape",
            "shared; rm -rf /",
            "tag with spaces",
            "tag/slash",
            "tag`whoami`",
        ]

        for tag in malformed_tags:
            with self.assertRaises(ValueError, msg=f"Should reject malformed tag: {tag}"):
                vfs.get_socket_path("win11", mount_tag=tag)

    def test_directory_creation_and_persistent_var_check(self) -> None:
        """Adversarial Test: Directory creation and Invariant 1 verification."""
        vfs = avd_virtiofs_mount_mod.VirtioFSManager(run_root=self.run_root, default_shared_dir=self.shared_dir)

        # Before create
        info1 = vfs.verify_source_directory(create=False)
        self.assertFalse(info1["exists"])

        # Create
        info2 = vfs.verify_source_directory(create=True)
        self.assertTrue(info2["exists"])
        self.assertTrue(info2["created"])

        # Invariant 1 verification on default canonical path
        vfs_default = avd_virtiofs_mount_mod.VirtioFSManager(mock=True)
        def_info = vfs_default.verify_source_directory()
        self.assertTrue(def_info["persistent_var_path"])
        self.assertEqual(def_info["source_dir"], "/var/home/mios/Shared")

    def test_dax_cache_window_and_domain_xml_matrix(self) -> None:
        """Adversarial Test: DAX window boundary options in daemon command and libvirt XML."""
        vfs = avd_virtiofs_mount_mod.VirtioFSManager(run_root=self.run_root, default_shared_dir=self.shared_dir)

        # 1. Without DAX (dax_size_mb=0)
        cmd_no_dax = vfs.build_daemon_cmd("win11", mount_tag="hostshare", dax_size_mb=0)
        self.assertFalse(any(arg.startswith("--dax-size") for arg in cmd_no_dax))
        xml_no_dax = vfs.generate_domain_xml(mount_tag="hostshare", dax_size_mb=0)
        self.assertNotIn("<dax", xml_no_dax)

        # 2. With 2048MB DAX cache window
        cmd_dax = vfs.build_daemon_cmd("win11", mount_tag="hostshare", dax_size_mb=2048)
        self.assertIn("--dax-size=2048M", cmd_dax)
        self.assertIn("--posix-acl", cmd_dax)
        self.assertIn("--xattr", cmd_dax)

        xml_dax = vfs.generate_domain_xml(mount_tag="hostshare", dax_size_mb=2048)
        self.assertIn('<filesystem type="mount" accessmode="passthrough">', xml_dax)
        self.assertIn('<driver type="virtiofs" queue="1024"/>', xml_dax)
        self.assertIn('<target dir="hostshare"/>', xml_dax)
        self.assertIn('<dax unit="KiB">2097152</dax>', xml_dax)
        self.assertIn('<source type="memfd"/>', xml_dax)
        self.assertIn('<access mode="shared"/>', xml_dax)

class avd_TestAdversarialMultiMonitorSync(unittest.TestCase):
    """Adversarial stress-testing for task_367 Multi-Monitor Looking Glass Display Geometry & Synchronizer."""

    def test_shm_buffer_sizing_across_resolutions(self) -> None:
        """Adversarial Test: Power-of-2 IVSHMEM sizing across standard, ultrawide, and extreme 8K displays."""
        calc = avd_multimonitor_sync_mod.MultiMonitorSyncManager.compute_shm_size_mb

        # 1080p: 1920x1080 -> 32 MB
        self.assertEqual(calc(1920, 1080), 32)

        # 1440p: 2560x1440 -> 64 MB
        self.assertEqual(calc(2560, 1440), 64)

        # Ultrawide 1440p: 3440x1440 -> 64 MB
        self.assertEqual(calc(3440, 1440), 64)

        # Super Ultrawide: 5120x1440 -> 128 MB
        self.assertEqual(calc(5120, 1440), 128)

        # 4K: 3840x2160 -> 128 MB
        self.assertEqual(calc(3840, 2160), 128)

        # 8K: 7680x4320 -> 512 MB
        self.assertEqual(calc(7680, 4320), 512)

        # Invalid zero or negative dimensions
        with self.assertRaises(ValueError):
            calc(0, 1080)
        with self.assertRaises(ValueError):
            calc(1920, -1080)

    def test_cursor_warp_transitions_horizontal_topology(self) -> None:
        """Adversarial Test: Cross-monitor cursor warp transitions in dual 1440p side-by-side."""
        monitors = [
            {"id": 0, "name": "DP-1", "width": 2560, "height": 1440, "x": 0, "y": 0},
            {"id": 1, "name": "DP-2", "width": 2560, "height": 1440, "x": 2560, "y": 0},
        ]
        mgr = avd_multimonitor_sync_mod.MultiMonitorSyncManager(monitors=monitors)

        # 1. Cursor inside Head 0 -> No transition
        w1 = mgr.calculate_cursor_warp(0, 1000.0, 500.0)
        self.assertFalse(w1["transition"])
        self.assertEqual(w1["target_head"], 0)

        # 2. Cursor crossing right of Head 0 into Head 1
        w2 = mgr.calculate_cursor_warp(0, 2570.0, 600.0)
        self.assertTrue(w2["transition"])
        self.assertEqual(w2["direction"], "right")
        self.assertEqual(w2["target_head"], 1)
        self.assertEqual(w2["target_coords"], [10.0, 600.0])

        # 3. Cursor crossing left of Head 1 into Head 0
        w3 = mgr.calculate_cursor_warp(1, -25.0, 450.0)
        self.assertTrue(w3["transition"])
        self.assertEqual(w3["direction"], "left")
        self.assertEqual(w3["target_head"], 0)
        self.assertEqual(w3["target_coords"], [2535.0, 450.0])

    def test_cursor_warp_transitions_vertical_and_negative_offsets(self) -> None:
        """Adversarial Test: Vertical stack and negative monitor coordinates (secondary to the left)."""
        # Topology with secondary display positioned to the left (negative x)
        monitors = [
            {"id": 0, "name": "DP-1", "width": 1920, "height": 1080, "x": -1920, "y": 0},
            {"id": 1, "name": "DP-2", "width": 2560, "height": 1440, "x": 0, "y": 0},
        ]
        mgr = avd_multimonitor_sync_mod.MultiMonitorSyncManager(monitors=monitors)

        # On Head 0 (-1920..0), crossing right (x >= 1920) into Head 1 (0..2560)
        w_right = mgr.calculate_cursor_warp(0, 1930.0, 500.0)
        self.assertTrue(w_right["transition"])
        self.assertEqual(w_right["direction"], "right")
        self.assertEqual(w_right["target_head"], 1)
        self.assertEqual(w_right["target_coords"], [10.0, 500.0])

        # On Head 1 (0..2560), crossing left (x < 0) into Head 0 (-1920..0)
        w_left = mgr.calculate_cursor_warp(1, -50.0, 500.0)
        self.assertTrue(w_left["transition"])
        self.assertEqual(w_left["direction"], "left")
        self.assertEqual(w_left["target_head"], 0)
        self.assertEqual(w_left["target_coords"], [1870.0, 500.0])


# ======================================================================
# from tests/test-virt.py
# ======================================================================
"""
Automated unit tests for dynamic hugepages allocation, kernel memory compaction triggering,
pool status calculation, release teardown, and libvirt XML generation.
"""


import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

hm__HERE = os.path.dirname(os.path.abspath(__file__))
hm__ROOT = os.path.normpath(os.path.join(hm__HERE, ".."))
hm__TARGET_PATH = os.path.join(hm__ROOT, "usr", "libexec", "mios", "virt", "hugepages_mgr.py")

hm_spec = importlib.util.spec_from_file_location("hugepages_mgr", hm__TARGET_PATH)
if hm_spec and hm_spec.loader:
    hugepages_mgr = importlib.util.module_from_spec(hm_spec)
    sys.modules[hm_spec.name] = hugepages_mgr
    hm_spec.loader.exec_module(hugepages_mgr)
else:
    raise ImportError(f"Could not load hugepages_mgr module from {hm__TARGET_PATH}")

class hm_TestHugepagesManager(unittest.TestCase):
    """Tests hugepages allocation, compaction trigger, teardown, and XML generation."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="mios-test-hugepages-")
        self.sysfs_root = os.path.join(self.temp_dir, "sys")
        self.proc_root = os.path.join(self.temp_dir, "proc")

        # Setup synthetic 2M and 1G sysfs directories
        for kb in [2048, 1048576]:
            pool_dir = os.path.join(self.sysfs_root, "kernel", "mm", "hugepages", f"hugepages-{kb}kB")
            os.makedirs(pool_dir, exist_ok=True)
            with open(os.path.join(pool_dir, "nr_hugepages"), "w", encoding="utf-8") as f:
                f.write("0\n")
            with open(os.path.join(pool_dir, "free_hugepages"), "w", encoding="utf-8") as f:
                f.write("0\n")

        # Setup synthetic /proc/sys/vm/compact_memory
        vm_dir = os.path.join(self.proc_root, "sys", "vm")
        os.makedirs(vm_dir, exist_ok=True)
        with open(os.path.join(vm_dir, "compact_memory"), "w", encoding="utf-8") as f:
            f.write("0\n")

        # Setup synthetic /proc/meminfo
        with open(os.path.join(self.proc_root, "meminfo"), "w", encoding="utf-8") as f:
            f.write("MemTotal:       32768000 kB\nMemFree:        24000000 kB\nMemAvailable:   26000000 kB\n")

    def tearDown(self) -> None:
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_page_count_calculation_2m(self) -> None:
        mgr = hugepages_mgr.HugepagesManager(mock=True)
        self.assertEqual(mgr.calculate_page_count(8192, page_size="2M"), 4096)
        self.assertEqual(mgr.calculate_page_count(16384, page_size="2M"), 8192)
        with self.assertRaises(ValueError):
            mgr.calculate_page_count(8193, page_size="2M")  # Not multiple of 2

    def test_page_count_calculation_1g(self) -> None:
        mgr = hugepages_mgr.HugepagesManager(mock=True)
        self.assertEqual(mgr.calculate_page_count(8192, page_size="1G"), 8)
        self.assertEqual(mgr.calculate_page_count(16384, page_size="1G"), 16)
        with self.assertRaises(ValueError):
            mgr.calculate_page_count(8190, page_size="1G")  # Not multiple of 1024

    def test_memory_compaction_trigger(self) -> None:
        mgr = hugepages_mgr.HugepagesManager(
            sysfs_root=self.sysfs_root,
            proc_root=self.proc_root,
            mock=False,
        )
        res = mgr.trigger_compaction()
        self.assertTrue(res["compaction_triggered"])
        compact_file = os.path.join(self.proc_root, "sys", "vm", "compact_memory")
        with open(compact_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "1")

    def test_allocate_and_release_lifecycle(self) -> None:
        mgr = hugepages_mgr.HugepagesManager(
            sysfs_root=self.sysfs_root,
            proc_root=self.proc_root,
            mock=False,
        )
        # Allocate 4096 MB (2048 pages of 2M) with compaction
        alloc_res = mgr.allocate(4096, page_size="2M", compact=True)
        self.assertEqual(alloc_res["status"], "allocated")
        self.assertEqual(alloc_res["requested_pages"], 2048)
        self.assertEqual(alloc_res["target_pages"], 2048)
        self.assertTrue(alloc_res["compaction"]["compaction_triggered"])

        # Verify on synthetic sysfs
        pool_2m = mgr.get_pool_status("2M")
        self.assertEqual(pool_2m["nr_hugepages"], 2048)
        self.assertEqual(pool_2m["allocated_mb"], 4096)

        # Release 4096 MB
        rel_res = mgr.release(4096, page_size="2M")
        self.assertEqual(rel_res["status"], "released")
        self.assertEqual(rel_res["remaining_pages"], 0)

        pool_2m_after = mgr.get_pool_status("2M")
        self.assertEqual(pool_2m_after["nr_hugepages"], 0)

    def test_domain_xml_generation(self) -> None:
        mgr = hugepages_mgr.HugepagesManager(mock=True)
        xml_2m = mgr.generate_domain_xml(8192, page_size="2M")
        self.assertIn('<page size="2048" unit="KiB"/>', xml_2m)
        self.assertIn('<locked/>', xml_2m)

        xml_1g = mgr.generate_domain_xml(8192, page_size="1G")
        self.assertIn('<page size="1048576" unit="KiB"/>', xml_1g)
        self.assertIn('<locked/>', xml_1g)

    def test_mock_status_and_meminfo(self) -> None:
        mgr = hugepages_mgr.HugepagesManager(mock=True)
        mem = mgr.get_meminfo()
        self.assertIn("MemTotal", mem)
        self.assertIn("Hugepagesize", mem)

        st = mgr.get_pool_status("2M")
        self.assertEqual(st["page_size"], "2M")
        self.assertEqual(st["page_size_kb"], 2048)


# ======================================================================
# from tests/test-virt.py
# ======================================================================
"""
Automated unit tests for IOMMU group parser, multifunction device detection,
isolation conflict auditing, and UKI-baked ACS override recommendation.
"""


import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

ip__HERE = os.path.dirname(os.path.abspath(__file__))
ip__ROOT = os.path.normpath(os.path.join(ip__HERE, ".."))
ip__TARGET_PATH = os.path.join(ip__ROOT, "usr", "libexec", "mios", "virt", "iommu_parser.py")

ip_spec = importlib.util.spec_from_file_location("iommu_parser", ip__TARGET_PATH)
if ip_spec and ip_spec.loader:
    iommu_parser = importlib.util.module_from_spec(ip_spec)
    sys.modules[ip_spec.name] = iommu_parser
    ip_spec.loader.exec_module(iommu_parser)
else:
    raise ImportError(f"Could not load iommu_parser module from {ip__TARGET_PATH}")

class ip_TestIOMMUParser(unittest.TestCase):
    """Tests IOMMU group parsing and isolation auditing using mock and synthetic sysfs trees."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="mios-test-iommu-")

    def tearDown(self) -> None:
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_synthetic_pci_device(
        self,
        sysfs_root: str,
        group_id: int,
        bdf: str,
        vendor: str,
        device: str,
        class_code: str,
        driver: str | None = None,
        boot_vga: bool = False,
    ) -> None:
        fs_bdf = iommu_parser.IOMMUParser.sanitize_bdf_for_fs(bdf)
        dev_dir = os.path.join(sysfs_root, "kernel", "iommu_groups", str(group_id), "devices", fs_bdf)
        os.makedirs(dev_dir, exist_ok=True)
        with open(os.path.join(dev_dir, "vendor"), "w", encoding="utf-8") as f:
            f.write(f"{vendor}\n")
        with open(os.path.join(dev_dir, "device"), "w", encoding="utf-8") as f:
            f.write(f"{device}\n")
        with open(os.path.join(dev_dir, "class"), "w", encoding="utf-8") as f:
            f.write(f"{class_code}\n")
        with open(os.path.join(dev_dir, "boot_vga"), "w", encoding="utf-8") as f:
            f.write(f"{'1' if boot_vga else '0'}\n")

        # Also create sysfs bus/pci/devices entry
        bus_dev_dir = os.path.join(sysfs_root, "bus", "pci", "devices", fs_bdf)
        os.makedirs(bus_dev_dir, exist_ok=True)
        for fname in ["vendor", "device", "class", "boot_vga"]:
            src = os.path.join(dev_dir, fname)
            dst = os.path.join(bus_dev_dir, fname)
            if os.path.exists(src) and not os.path.exists(dst):
                with open(src, "r", encoding="utf-8") as rf, open(dst, "w", encoding="utf-8") as wf:
                    wf.write(rf.read())

    def test_bdf_normalization(self) -> None:
        dom, bus, slot, func = iommu_parser.IOMMUParser.parse_bdf("0000:01:00.0")
        self.assertEqual((dom, bus, slot, func), ("0000", "01", "00", "0"))

        dom, bus, slot, func = iommu_parser.IOMMUParser.parse_bdf("01:00.1")
        self.assertEqual((dom, bus, slot, func), ("0000", "01", "00", "1"))

        dom, bus, slot, func = iommu_parser.IOMMUParser.parse_bdf("0000_01_00.0")
        self.assertEqual((dom, bus, slot, func), ("0000", "01", "00", "0"))

        with self.assertRaises(ValueError):
            iommu_parser.IOMMUParser.parse_bdf("invalid-bdf")

    def test_decode_pci_class(self) -> None:
        self.assertEqual(iommu_parser.decode_pci_class("0x030000"), "VGA compatible controller")
        self.assertEqual(iommu_parser.decode_pci_class("0x040300"), "Audio device (HD Audio / Soundwire)")
        self.assertEqual(iommu_parser.decode_pci_class("0x060400"), "PCI bridge (Root Port / Switch)")
        self.assertEqual(iommu_parser.decode_pci_class("0x010802"), "Non-Volatile memory controller (NVMe)")

    def test_mock_isolation_pass(self) -> None:
        parser = iommu_parser.IOMMUParser(mock=True)
        report = parser.audit_isolation("0000:01:00.0")

        self.assertEqual(report["status"], "pass")
        self.assertTrue(report["isolated"])
        self.assertEqual(report["iommu_group"], 13)
        self.assertEqual(len(report["companions"]), 2)  # VGA (0000:01:00.0) + Audio (0000:01:00.1)
        self.assertEqual(len(report["conflicts"]), 0)
        self.assertIn("Clean hardware IOMMU isolation", report["recommendation"])
        self.assertIsNone(report["uki_kargs"])

    def test_synthetic_isolated_group(self) -> None:
        # Create group 5 with isolated GPU (0000:02:00.0 VGA and 0000:02:00.1 Audio)
        self._create_synthetic_pci_device(
            self.temp_dir, 5, "0000:02:00.0", "0x10de", "0x2484", "0x030000", boot_vga=False
        )
        self._create_synthetic_pci_device(
            self.temp_dir, 5, "0000:02:00.1", "0x10de", "0x228b", "0x040300", boot_vga=False
        )

        parser = iommu_parser.IOMMUParser(sysfs_root=self.temp_dir, mock=False)
        report = parser.audit_isolation("0000:02:00.0")

        self.assertEqual(report["status"], "pass")
        self.assertTrue(report["isolated"])
        self.assertEqual(report["iommu_group"], 5)
        self.assertEqual(len(report["conflicts"]), 0)
        self.assertEqual(len(report["companions"]), 2)

    def test_synthetic_conflicting_group_requires_acs_override(self) -> None:
        # Create group 7 with GPU (0000:03:00.0) sharing group with PCIe Root Port (0000:00:01.0) and SATA (0000:00:17.0)
        self._create_synthetic_pci_device(
            self.temp_dir, 7, "0000:03:00.0", "0x10de", "0x2484", "0x030000", boot_vga=False
        )
        self._create_synthetic_pci_device(
            self.temp_dir, 7, "0000:00:01.0", "0x8086", "0x460d", "0x060400", boot_vga=False
        )
        self._create_synthetic_pci_device(
            self.temp_dir, 7, "0000:00:17.0", "0x8086", "0x7a62", "0x010601", boot_vga=False
        )

        parser = iommu_parser.IOMMUParser(sysfs_root=self.temp_dir, mock=False)
        report = parser.audit_isolation("0000:03:00.0")

        self.assertEqual(report["status"], "conflict")
        self.assertFalse(report["isolated"])
        self.assertEqual(report["iommu_group"], 7)
        self.assertEqual(len(report["companions"]), 1)  # Target itself
        self.assertEqual(len(report["conflicts"]), 2)   # Root Port + SATA
        self.assertEqual(report["uki_kargs"], "pcie_acs_override=downstream,multifunction")
        self.assertIn("Unified Kernel Image (UKI)", report["recommendation"])
        self.assertIn("not injected via runtime MOK", report["recommendation"])
        self.assertIsNotNone(report["security_warning"])

    def test_device_not_found(self) -> None:
        parser = iommu_parser.IOMMUParser(mock=True)
        report = parser.audit_isolation("0000:99:00.0")
        self.assertEqual(report["status"], "not_found")
        self.assertFalse(report["isolated"])

    def test_invalid_bdf_error(self) -> None:
        parser = iommu_parser.IOMMUParser(mock=True)
        report = parser.audit_isolation("bad-bdf-str")
        self.assertEqual(report["status"], "error")
        self.assertFalse(report["isolated"])

    def test_list_groups_mock(self) -> None:
        parser = iommu_parser.IOMMUParser(mock=True)
        groups = parser.parse_groups()
        self.assertIn(0, groups)
        self.assertIn(1, groups)
        self.assertIn(13, groups)
        self.assertEqual(len(groups[13]), 2)


# ======================================================================
# from tests/test-virt.py
# ======================================================================
"""Unit and benchmark test suite for Ephemeral Cloud-Hypervisor microVM orchestrator and Virtio-VSOCK bridge (task_514)."""


import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

mb__HERE = os.path.dirname(os.path.abspath(__file__))
mb__ROOT = os.path.normpath(os.path.join(mb__HERE, ".."))
mb__TARGET_PATH = os.path.join(mb__ROOT, "usr", "libexec", "mios", "virt", "microvm_bridge.py")

mb_spec = importlib.util.spec_from_file_location("microvm_bridge", mb__TARGET_PATH)
if mb_spec and mb_spec.loader:
    microvm_bridge = importlib.util.module_from_spec(mb_spec)
    sys.modules[mb_spec.name] = microvm_bridge
    mb_spec.loader.exec_module(microvm_bridge)
else:
    raise ImportError(f"Could not load module from {mb__TARGET_PATH}")

class mb_TestMicroVMBridge(unittest.TestCase):
    """Test suite for microVM direct-boot configuration, ephemeral lifecycle, VSOCK IPC, and boot latency SLA."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="mios-vm-test-")
        self.orchestrator = microvm_bridge.CloudHypervisorOrchestrator(
            mock=True,
            runtime_dir=self.tmpdir.name,
        )

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_config_generation_and_cmd_builder(self) -> None:
        cfg = microvm_bridge.MicroVMConfig(
            vm_id="vm-test-01",
            vcpus=4,
            memory_mb=1024,
            kernel_path="/boot/vmlinuz-custom",
            initramfs_path="/boot/initramfs-custom.img",
            cmdline="console=ttyS0 quiet panic=1",
            vsock_cid=5,
            vsock_port=5200,
            vsock_socket_path="/run/mios/vm.vsock",
            virtiofs_socket="/run/mios/virtiofs.sock",
            dax=True,
        )

        cmd = self.orchestrator.build_launch_cmd(cfg)
        self.assertIn("cloud-hypervisor", cmd)
        self.assertIn("--cpus", cmd)
        self.assertIn("boot=4", cmd)
        self.assertIn("--memory", cmd)
        self.assertIn("size=1024M,shared=on", cmd)
        self.assertIn("--kernel", cmd)
        self.assertIn("/boot/vmlinuz-custom", cmd)
        self.assertIn("--vsock", cmd)
        self.assertIn("cid=5,socket=/run/mios/vm.vsock", cmd)
        self.assertIn("--fs", cmd)
        self.assertIn("tag=workspace,socket=/run/mios/virtiofs.sock,num_queues=1,queue_size=1024", cmd)

    def test_spawn_and_sub_50ms_boot_latency(self) -> None:
        cfg = microvm_bridge.MicroVMConfig(vm_id="vm-perf-01", memory_mb=512)
        ok, boot_ms, msg = self.orchestrator.spawn_microvm(cfg)
        self.assertTrue(ok)
        # SLA: Ephemeral microVM must boot in <50ms
        self.assertLess(boot_ms, 50.0)
        self.assertIn("vm-perf-01", self.orchestrator.active_vms)

        # Cleanup
        self.orchestrator.cleanup_microvm("vm-perf-01")
        self.assertNotIn("vm-perf-01", self.orchestrator.active_vms)

    def test_exec_tool_lifecycle(self) -> None:
        res = self.orchestrator.exec_tool(
            command="python3 -c 'print(\"hello microvm\")'",
            memory_mb=512,
            vcpus=2,
        )
        self.assertEqual(res.exit_code, 0)
        self.assertIn("hello microvm", res.stdout)
        self.assertLess(res.boot_latency_ms, 50.0)
        self.assertTrue(res.cleaned_up)
        # Verify active VMs are cleaned up
        self.assertEqual(len(self.orchestrator.active_vms), 0)

    def test_benchmark_performance_sla(self) -> None:
        bench = self.orchestrator.benchmark_performance(iterations=5)
        self.assertEqual(bench["status"], "PASS")
        self.assertTrue(bench["boot_sla_passed"])
        self.assertTrue(bench["throughput_sla_passed"])
        self.assertLess(bench["avg_boot_latency_ms"], 50.0)
        self.assertGreaterEqual(bench["vsock_throughput_gbps"], 1.0)

    def test_vsock_bridge_mock_rpc(self) -> None:
        bridge = microvm_bridge.VSOCKBridge(mock=True)
        resp = bridge.send_rpc(
            cid=3,
            port=5200,
            payload={"id": "test-req", "action": "exec", "command": "uname -r"},
        )
        self.assertEqual(resp["status"], "success")
        self.assertEqual(resp["exit_code"], 0)
        self.assertIn("uname -r", resp["stdout"])

    def test_cli_execution(self) -> None:
        # CLI --benchmark --json
        stdout_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf):
            ret = microvm_bridge.main(["--benchmark", "--json", "--mock"])
            self.assertEqual(ret, 0)
            data = json.loads(stdout_buf.getvalue())
            self.assertEqual(data["status"], "PASS")
            self.assertTrue(data["boot_sla_passed"])

        # CLI --exec --command "echo test" --json
        stdout_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf):
            ret = microvm_bridge.main(["--exec", "--command", "echo test", "--json", "--mock"])
            self.assertEqual(ret, 0)
            data = json.loads(stdout_buf.getvalue())
            self.assertEqual(data["exit_code"], 0)
            self.assertIn("echo test", data["stdout"])

        # CLI --spawn and --cleanup
        stdout_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf):
            ret = microvm_bridge.main(["--spawn", "--vm-id", "vm-cli-01", "--json", "--mock"])
            self.assertEqual(ret, 0)
            data = json.loads(stdout_buf.getvalue())
            self.assertTrue(data["success"])

        stdout_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf):
            ret = microvm_bridge.main(["--cleanup", "--vm-id", "vm-cli-01", "--json", "--mock"])
            self.assertEqual(ret, 0)
            data = json.loads(stdout_buf.getvalue())
            self.assertTrue(data["success"])


# ======================================================================
# from tests/test-virt.py
# ======================================================================
"""Automated unit test suite for MiOS MicroVM Sandbox Manager."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "virt"))

from microvm_sandbox import MAX_BOOT_LATENCY_MS, MicroVMSandboxManager

class ms_TestMicroVMSandbox(unittest.TestCase):
    def setUp(self):
        self.mgr = MicroVMSandboxManager(dry_run=True)

    def test_sub_50ms_microvm_boot_latency(self):
        """Test microVM boots and runs task in <50ms."""
        res = self.mgr.launch_ephemeral_microvm("echo 'safe code'")
        self.assertIsNotNone(res)
        self.assertEqual(res.exit_code, 0)
        self.assertLess(res.boot_latency_ms, MAX_BOOT_LATENCY_MS)

    def test_synthetic_exploit_breakout_contained(self):
        """Test container breakout exploits are safely contained inside guest KVM boundary."""
        res = self.mgr.launch_ephemeral_microvm("cat ../../../etc/shadow # dirty_cow")
        self.assertTrue(res.is_contained)
        self.assertEqual(res.exit_code, 1)


# ======================================================================
# from tests/test-virt.py
# ======================================================================
"""Unit tests for low-latency PipeWire JACK inter-VM audio bridge and Scream IVSHMEM sink."""


import importlib.util
import json
import os
import sys
import unittest

pb__HERE = os.path.dirname(os.path.abspath(__file__))
pb__ROOT = os.path.normpath(os.path.join(pb__HERE, ".."))
pb__PB_PATH = os.path.join(pb__ROOT, "usr", "libexec", "mios", "virt", "pipewire_bridge.py")

pb_spec = importlib.util.spec_from_file_location("pipewire_bridge", pb__PB_PATH)
if pb_spec and pb_spec.loader:
    pipewire_bridge = importlib.util.module_from_spec(pb_spec)
    sys.modules[pb_spec.name] = pipewire_bridge
    pb_spec.loader.exec_module(pipewire_bridge)
else:
    raise ImportError(f"Could not load pipewire_bridge module from {pb__PB_PATH}")

class pb_TestPipeWireBridge(unittest.TestCase):
    """Validates low-latency buffer math, SLA thresholds, IVSHMEM XML, and systemd units."""

    def setUp(self) -> None:
        self.manager = pipewire_bridge.PipeWireBridgeManager(
            shm_path="/dev/shm/scream",
            size_mb=2,
            sample_rate=48000,
            quantum=64,
            backend="jack",
            node_name="scream-ivshmem-bridge",
        )

    def test_latency_math_exactness(self) -> None:
        # 64 / 48000 = 1.333 ms
        lat_64_48k = pipewire_bridge.PipeWireBridgeManager.calculate_latency_ms(64, 48000)
        self.assertEqual(lat_64_48k, 1.333)

        # 32 / 48000 = 0.667 ms
        lat_32_48k = pipewire_bridge.PipeWireBridgeManager.calculate_latency_ms(32, 48000)
        self.assertEqual(lat_32_48k, 0.667)

        # 128 / 96000 = 1.333 ms
        lat_128_96k = pipewire_bridge.PipeWireBridgeManager.calculate_latency_ms(128, 96000)
        self.assertEqual(lat_128_96k, 1.333)

        # 128 / 192000 = 0.667 ms
        lat_128_192k = pipewire_bridge.PipeWireBridgeManager.calculate_latency_ms(128, 192000)
        self.assertEqual(lat_128_192k, 0.667)

        # 128 / 44100 = 2.902 ms
        lat_128_44k = pipewire_bridge.PipeWireBridgeManager.calculate_latency_ms(128, 44100)
        self.assertEqual(lat_128_44k, 2.902)

    def test_latency_math_error_handling(self) -> None:
        with self.assertRaises(ValueError):
            pipewire_bridge.PipeWireBridgeManager.calculate_latency_ms(0, 48000)
        with self.assertRaises(ValueError):
            pipewire_bridge.PipeWireBridgeManager.calculate_latency_ms(64, 0)
        with self.assertRaises(ValueError):
            pipewire_bridge.PipeWireBridgeManager.calculate_latency_ms(-1, 48000)

    def test_sla_enforcement(self) -> None:
        # 64 / 48000 = 1.333 ms -> <= 5.0ms PASS
        res_pass = self.manager.validate_latency_sla(quantum=64, sample_rate=48000)
        self.assertTrue(res_pass["passed"])
        self.assertEqual(res_pass["status"], "pass")
        self.assertLessEqual(res_pass["latency_ms"], 5.0)

        # 256 / 48000 = 5.333 ms -> > 5.0ms FAIL
        res_fail = self.manager.validate_latency_sla(quantum=256, sample_rate=48000)
        self.assertFalse(res_fail["passed"])
        self.assertEqual(res_fail["status"], "fail")
        self.assertGreater(res_fail["latency_ms"], 5.0)

    def test_pipewire_env_generation(self) -> None:
        env = self.manager.generate_pipewire_env()
        self.assertEqual(env["PIPEWIRE_LATENCY"], "64/48000")
        self.assertEqual(env["PIPEWIRE_QUANTUM"], "64/48000")
        self.assertEqual(env["PIPEWIRE_RATE"], "1/48000")
        self.assertEqual(env["JACK_PROMISCUOUS_SERVER"], "1")
        self.assertEqual(env["PIPEWIRE_NODE_NAME"], "scream-ivshmem-bridge")

    def test_ivshmem_xml_generation(self) -> None:
        xml = self.manager.generate_ivshmem_xml()
        self.assertIn('<shmem name="scream">', xml)
        self.assertIn('<model type="ivshmem-plain"/>', xml)
        self.assertIn('<size unit="M">2</size>', xml)

    def test_systemd_service_generation(self) -> None:
        # System unit
        svc_sys = self.manager.generate_systemd_service(user_unit=False)
        self.assertIn("[Unit]", svc_sys)
        self.assertIn("Description=MiOS Scream IVSHMEM to PipeWire JACK Low-Latency Audio Bridge", svc_sys)
        self.assertIn("After=pipewire.service", svc_sys)
        self.assertIn('Environment="PIPEWIRE_LATENCY=64/48000"', svc_sys)
        self.assertIn("ExecStart=/usr/bin/scream -m /dev/shm/scream -o jack -t 64", svc_sys)
        self.assertIn("WantedBy=multi-user.target", svc_sys)

        # User unit
        svc_user = self.manager.generate_systemd_service(user_unit=True)
        self.assertIn("WantedBy=default.target", svc_user)

    def test_audio_node_mock_validation(self) -> None:
        node_res = self.manager.validate_audio_nodes(mock=True)
        self.assertEqual(node_res["status"], "pass")
        self.assertTrue(node_res["accessible"])

    def test_verify_all(self) -> None:
        res = self.manager.verify_all(mock=True)
        self.assertEqual(res["status"], "pass")
        self.assertTrue(res["sla_passed"])
        self.assertEqual(res["checks"]["latency_sla"], "pass")
        self.assertEqual(res["checks"]["xml_generation"], "pass")
        self.assertEqual(res["checks"]["env_generation"], "pass")
        self.assertEqual(res["checks"]["service_generation"], "pass")


# ======================================================================
# from tests/test-virt.py
# ======================================================================
"""
Automated unit tests for VFIO dynamic runtime unbind/rebind, primary display guard,
slot-sibling coordination, and driver_override management.
"""


import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

vb__HERE = os.path.dirname(os.path.abspath(__file__))
vb__ROOT = os.path.normpath(os.path.join(vb__HERE, ".."))
vb__TARGET_PATH = os.path.join(vb__ROOT, "usr", "libexec", "mios", "virt", "vfio_bind.py")

vb_spec = importlib.util.spec_from_file_location("vfio_bind", vb__TARGET_PATH)
if vb_spec and vb_spec.loader:
    vfio_bind = importlib.util.module_from_spec(vb_spec)
    sys.modules[vb_spec.name] = vfio_bind
    vb_spec.loader.exec_module(vfio_bind)
else:
    raise ImportError(f"Could not load vfio_bind module from {vb__TARGET_PATH}")

class vb_TestVFIOBind(unittest.TestCase):
    """Tests VFIO dynamic binding, primary display protection, and sysfs state transitions."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="mios-test-vfio-")

    def tearDown(self) -> None:
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _setup_synthetic_gpu(
        self,
        sysfs_root: str,
        bdf_vga: str = "0000:01:00.0",
        bdf_audio: str = "0000:01:00.1",
        primary_vga: bool = False,
        initial_driver: str = "nvidia",
    ) -> None:
        # Create driver dirs
        for drv in ["nvidia", "snd_hda_intel", "vfio-pci", "amdgpu", "i915"]:
            os.makedirs(os.path.join(sysfs_root, "bus", "pci", "drivers", drv), exist_ok=True)
            with open(os.path.join(sysfs_root, "bus", "pci", "drivers", drv, "bind"), "w", encoding="utf-8") as f:
                f.write("")
            with open(os.path.join(sysfs_root, "bus", "pci", "drivers", drv, "unbind"), "w", encoding="utf-8") as f:
                f.write("")

        # Create device dirs
        for bdf, dev_id, class_code, drv, is_boot in [
            (bdf_vga, "0x2484", "0x030000", initial_driver, primary_vga),
            (bdf_audio, "0x228b", "0x040300", "snd_hda_intel", False),
        ]:
            fs_bdf = vfio_bind.sanitize_bdf_for_fs(bdf)
            dev_dir = os.path.join(sysfs_root, "bus", "pci", "devices", fs_bdf)
            os.makedirs(dev_dir, exist_ok=True)
            with open(os.path.join(dev_dir, "vendor"), "w", encoding="utf-8") as f:
                f.write("0x10de\n")
            with open(os.path.join(dev_dir, "device"), "w", encoding="utf-8") as f:
                f.write(f"{dev_id}\n")
            with open(os.path.join(dev_dir, "class"), "w", encoding="utf-8") as f:
                f.write(f"{class_code}\n")
            with open(os.path.join(dev_dir, "boot_vga"), "w", encoding="utf-8") as f:
                f.write(f"{'1' if is_boot else '0'}\n")
            with open(os.path.join(dev_dir, "driver_override"), "w", encoding="utf-8") as f:
                f.write("(null)\n")
            with open(os.path.join(dev_dir, "current_driver"), "w", encoding="utf-8") as f:
                f.write(f"{drv}\n")

    def test_bdf_normalization(self) -> None:
        self.assertEqual(vfio_bind.normalize_bdf("0000:01:00.0"), "0000:01:00.0")
        self.assertEqual(vfio_bind.normalize_bdf("01:00.1"), "0000:01:00.1")
        self.assertEqual(vfio_bind.normalize_bdf("0000_02_00.0"), "0000:02:00.0")
        with self.assertRaises(ValueError):
            vfio_bind.normalize_bdf("invalid-format")

    def test_mock_bind_and_rebind(self) -> None:
        binder = vfio_bind.VFIOBinder(mock=True)
        res_vfio = binder.bind_to_vfio("0000:01:00.0")
        self.assertEqual(res_vfio["status"], "success")
        self.assertTrue(res_vfio["bound"])
        self.assertEqual(res_vfio["target_driver"], "vfio-pci")

        res_host = binder.rebind_to_host("0000:01:00.0", host_driver="nvidia")
        self.assertEqual(res_host["status"], "success")
        self.assertTrue(res_host["bound"])

    def test_primary_gpu_unbind_refused_without_force(self) -> None:
        self._setup_synthetic_gpu(self.temp_dir, primary_vga=True)
        binder = vfio_bind.VFIOBinder(sysfs_root=self.temp_dir, mock=False)

        res = binder.bind_to_vfio("0000:01:00.0", force=False)
        self.assertEqual(res["status"], "refused")
        self.assertFalse(res["bound"])
        self.assertIn("primary host display", res["error"])
        self.assertIn("Wayland", res["error"])

    def test_primary_gpu_unbind_succeeds_with_force(self) -> None:
        self._setup_synthetic_gpu(self.temp_dir, primary_vga=True)
        binder = vfio_bind.VFIOBinder(sysfs_root=self.temp_dir, mock=False)

        res = binder.bind_to_vfio("0000:01:00.0", force=True)
        self.assertEqual(res["status"], "success")
        self.assertTrue(res["bound"])

    def test_synthetic_full_bind_unbind_cycle(self) -> None:
        # Set up secondary GPU (VGA + Audio companion)
        self._setup_synthetic_gpu(self.temp_dir, primary_vga=False, initial_driver="nvidia")
        binder = vfio_bind.VFIOBinder(sysfs_root=self.temp_dir, mock=False)

        # Check initial status
        st = binder.get_status("0000:01:00.0")
        self.assertFalse(st["is_primary_gpu"])
        self.assertEqual(len(st["slot_devices"]), 2)

        # 1. Bind to VFIO
        res_vfio = binder.bind_to_vfio("0000:01:00.0")
        self.assertEqual(res_vfio["status"], "success")
        self.assertEqual(len(res_vfio["siblings"]), 2)

        # Verify driver_override file on disk
        vga_fs = vfio_bind.sanitize_bdf_for_fs("0000:01:00.0")
        override_file = os.path.join(self.temp_dir, "bus", "pci", "devices", vga_fs, "driver_override")
        with open(override_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "vfio-pci")

        # 2. Rebind back to host drivers
        res_host = binder.rebind_to_host("0000:01:00.0", host_driver="nvidia")
        self.assertEqual(res_host["status"], "success")
        self.assertEqual(len(res_host["siblings"]), 2)

        # Verify driver_override cleared on disk
        with open(override_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "")

    def test_status_json_structure(self) -> None:
        binder = vfio_bind.VFIOBinder(mock=True)
        st = binder.get_status("0000:01:00.0")
        self.assertIn("target_bdf", st)
        self.assertIn("is_primary_gpu", st)
        self.assertIn("slot_devices", st)
        self.assertIsInstance(st["slot_devices"], list)


# ======================================================================
# from tests/test-virt.py
# ======================================================================
"""
Automated unit tests for VirtIO-FS daemon command construction, POSIX ACL / xattr configuration,
persistent /var/home/mios/Shared verification, and libvirt domain XML generation.
"""


import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

vm__HERE = os.path.dirname(os.path.abspath(__file__))
vm__ROOT = os.path.normpath(os.path.join(vm__HERE, ".."))
vm__TARGET_PATH = os.path.join(vm__ROOT, "usr", "libexec", "mios", "virt", "virtiofs_mount.py")

vm_spec = importlib.util.spec_from_file_location("virtiofs_mount", vm__TARGET_PATH)
if vm_spec and vm_spec.loader:
    virtiofs_mount = importlib.util.module_from_spec(vm_spec)
    sys.modules[vm_spec.name] = virtiofs_mount
    vm_spec.loader.exec_module(virtiofs_mount)
else:
    raise ImportError(f"Could not load virtiofs_mount module from {vm__TARGET_PATH}")

class vm_TestVirtioFSMount(unittest.TestCase):
    """Tests VirtIO-FS mount configuration, daemon commands, and libvirt XML."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="mios-test-virtiofs-")
        self.run_root = os.path.join(self.temp_dir, "run")
        self.share_root = os.path.join(self.temp_dir, "var", "home", "mios", "Shared")

    def tearDown(self) -> None:
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_tag_validation(self) -> None:
        self.assertEqual(virtiofs_mount.validate_tag("shared"), "shared")
        self.assertEqual(virtiofs_mount.validate_tag("host_share_01"), "host_share_01")
        with self.assertRaises(ValueError):
            virtiofs_mount.validate_tag("invalid tag spaces")
        with self.assertRaises(ValueError):
            virtiofs_mount.validate_tag("../escape")

    def test_socket_path_generation(self) -> None:
        vfs = virtiofs_mount.VirtioFSManager(run_root="/run/libvirt")
        sock = vfs.get_socket_path("win11", "shared")
        self.assertEqual(sock, "/run/libvirt/virtiofsd-win11-shared.sock")

    def test_daemon_command_building(self) -> None:
        vfs = virtiofs_mount.VirtioFSManager(run_root="/run/libvirt")
        cmd = vfs.build_daemon_cmd(
            "win11",
            source_dir="/var/home/mios/Shared",
            mount_tag="shared",
            dax_size_mb=2048,
            posix_acl=True,
            xattr=True,
        )
        self.assertEqual(cmd[0], "virtiofsd")
        self.assertIn("--socket-path=/run/libvirt/virtiofsd-win11-shared.sock", cmd)
        self.assertIn("--shared-dir=/var/home/mios/Shared", cmd)
        self.assertIn("--posix-acl", cmd)
        self.assertIn("--xattr", cmd)
        self.assertIn("--dax-size=2048M", cmd)

    def test_domain_xml_generation(self) -> None:
        vfs = virtiofs_mount.VirtioFSManager()
        xml = vfs.generate_domain_xml(
            source_dir="/var/home/mios/Shared",
            mount_tag="shared",
            dax_size_mb=1024,
        )
        self.assertIn('<filesystem type="mount" accessmode="passthrough">', xml)
        self.assertIn('<driver type="virtiofs" queue="1024"/>', xml)
        self.assertIn('<source dir="/var/home/mios/Shared"/>', xml)
        self.assertIn('<target dir="shared"/>', xml)
        self.assertIn('<dax unit="KiB">1048576</dax>', xml)
        self.assertIn('<memoryBacking>', xml)
        self.assertIn('<access mode="shared"/>', xml)

    def test_guest_mount_commands(self) -> None:
        vfs = virtiofs_mount.VirtioFSManager()
        cmds = vfs.generate_guest_mount_command(mount_tag="shared", guest_mount_point="/mnt/shared")
        self.assertEqual(cmds["mount_tag"], "shared")
        self.assertEqual(cmds["shell_command"], "sudo mount -t virtiofs shared /mnt/shared")
        self.assertIn("virtiofs", cmds["fstab_entry"])

    def test_source_directory_verification_and_creation(self) -> None:
        vfs = virtiofs_mount.VirtioFSManager(default_shared_dir=self.share_root, mock=False)
        res = vfs.verify_source_directory(create=True)
        self.assertTrue(res["exists"])
        self.assertTrue(os.path.isdir(self.share_root))

    def test_status_report(self) -> None:
        vfs = virtiofs_mount.VirtioFSManager(
            run_root=self.run_root,
            default_shared_dir=self.share_root,
            mock=False,
        )
        os.makedirs(self.run_root, exist_ok=True)
        sock_path = vfs.get_socket_path("win11", "shared")
        with open(sock_path, "w") as f:
            f.write("mock-sock")

        st = vfs.get_status("win11", "shared")
        self.assertEqual(st["vm_id"], "win11")
        self.assertTrue(st["socket_active"])
        self.assertEqual(st["protocol"], "virtiofs")
        self.assertTrue(st["legacy_9p_avoided"])


# ======================================================================
# from tests/test-virt.py
# ======================================================================
"""
Automated unit tests for vTPM2 swtpm provisioning, per-VM state isolation,
ephemeral UNIX socket path management, and libvirt CRB domain XML generation.
"""


import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

vp__HERE = os.path.dirname(os.path.abspath(__file__))
vp__ROOT = os.path.normpath(os.path.join(vp__HERE, ".."))
vp__TARGET_PATH = os.path.join(vp__ROOT, "usr", "libexec", "mios", "virt", "vtpm_provision.py")

vp_spec = importlib.util.spec_from_file_location("vtpm_provision", vp__TARGET_PATH)
if vp_spec and vp_spec.loader:
    vtpm_provision = importlib.util.module_from_spec(vp_spec)
    sys.modules[vp_spec.name] = vtpm_provision
    vp_spec.loader.exec_module(vtpm_provision)
else:
    raise ImportError(f"Could not load vtpm_provision module from {vp__TARGET_PATH}")

class vp_TestVTPMProvision(unittest.TestCase):
    """Tests vTPM swtpm isolation, socket lifecycle, and domain XML generation."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="mios-test-vtpm-")
        self.state_root = os.path.join(self.temp_dir, "libvirt", "swtpm")
        self.sock_root = os.path.join(self.temp_dir, "run", "swtpm")

    def tearDown(self) -> None:
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_vm_id_validation(self) -> None:
        self.assertEqual(vtpm_provision.validate_vm_id("win11"), "win11")
        self.assertEqual(vtpm_provision.validate_vm_id("gaming-vm_01"), "gaming-vm_01")
        with self.assertRaises(ValueError):
            vtpm_provision.validate_vm_id("bad vm id with spaces")
        with self.assertRaises(ValueError):
            vtpm_provision.validate_vm_id("../escape")
        with self.assertRaises(ValueError):
            vtpm_provision.validate_vm_id("")

    def test_per_vm_state_directory_isolation(self) -> None:
        prov = vtpm_provision.VTPMProvisioner(
            state_root=self.state_root,
            sock_root=self.sock_root,
        )
        state_vm1 = prov.get_state_dir("vm-win11")
        state_vm2 = prov.get_state_dir("vm-linux-dev")

        # Invariant 1 check: Never share state directories between VMs
        self.assertNotEqual(state_vm1, state_vm2)
        self.assertTrue(state_vm1.endswith("vm-win11"))
        self.assertTrue(state_vm2.endswith("vm-linux-dev"))

    def test_domain_xml_generation(self) -> None:
        prov = vtpm_provision.VTPMProvisioner(
            state_root="/var/lib/libvirt/swtpm",
            sock_root="/run/libvirt/swtpm",
        )
        xml = prov.generate_domain_xml("win11-prod")
        self.assertIn('<tpm model="tpm-crb">', xml)
        self.assertIn('<backend type="emulator" version="2.0">', xml)
        self.assertIn('/run/libvirt/swtpm/win11-prod-swtpm.sock', xml)

    def test_build_setup_and_daemon_cmds(self) -> None:
        prov = vtpm_provision.VTPMProvisioner(
            state_root="/var/lib/libvirt/swtpm",
            sock_root="/run/libvirt/swtpm",
        )
        setup_cmd = prov.build_setup_cmd("win11")
        self.assertEqual(setup_cmd[0], "swtpm_setup")
        self.assertIn("--tpm2", setup_cmd)
        self.assertIn("--createek", setup_cmd)

        daemon_cmd = prov.build_daemon_cmd("win11")
        self.assertEqual(daemon_cmd[0], "swtpm")
        self.assertEqual(daemon_cmd[1], "socket")
        self.assertIn("--tpm2", daemon_cmd)
        self.assertIn("type=unixio,path=/run/libvirt/swtpm/win11-swtpm.sock", daemon_cmd)

    def test_provision_and_status_lifecycle(self) -> None:
        prov = vtpm_provision.VTPMProvisioner(
            state_root=self.state_root,
            sock_root=self.sock_root,
        )
        # Initial status: not provisioned
        st_before = prov.get_status("win11-test")
        self.assertFalse(st_before["provisioned"])

        # Provision
        res = prov.provision("win11-test")
        self.assertEqual(res["status"], "provisioned")
        self.assertTrue(os.path.isdir(res["state_dir"]))
        self.assertTrue(os.path.isfile(os.path.join(res["state_dir"], "tpm2-00.permall")))

        # Check status after
        st_after = prov.get_status("win11-test")
        self.assertTrue(st_after["provisioned"])
        self.assertTrue(st_after["has_nvram"])

    def test_cleanup_sockets_and_purge_state(self) -> None:
        prov = vtpm_provision.VTPMProvisioner(
            state_root=self.state_root,
            sock_root=self.sock_root,
        )
        prov.provision("win11-cleanup")
        sock_path = prov.get_socket_path("win11-cleanup")
        os.makedirs(os.path.dirname(sock_path), exist_ok=True)
        with open(sock_path, "w") as f:
            f.write("mock-sock")

        # Cleanup without purge
        res1 = prov.cleanup("win11-cleanup", purge_state=False)
        self.assertFalse(os.path.exists(sock_path))
        self.assertTrue(os.path.isdir(res1["state_dir"]))

        # Cleanup with purge
        res2 = prov.cleanup("win11-cleanup", purge_state=True)
        self.assertTrue(res2["state_purged"])
        self.assertFalse(os.path.exists(res2["state_dir"]))




class OVMFContentProofTests(unittest.TestCase):
    """Fixtures never touch installed firmware, NVRAM, or VM state."""

    GLOBAL = '8be4df61-93ca-11d2-aa0d-00e098032b8c'
    DB = 'd719b2cb-3d3a-4596-a3bc-dad00e67656f'
    ENABLE = 'f0a30bc7-af08-4556-99c4-001009c93a44'
    RSA = '3c5766e8-269c-4e34-aa14-ed776e85b3b6'
    SHA256 = 'c1c41626-504c-4092-aca9-41f936934328'

    def setUp(self):
        from pathlib import Path
        self.temp = tempfile.TemporaryDirectory(prefix='ovmf-content-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.desc = self.base / 'descriptors'
        self.desc.mkdir()
        self.lib = os.path.join(avd__ROOT, 'tools', 'find-ovmf-firmware.sh')
        self.env = dict(os.environ, OVMF_SHARE_ROOT=str(self.base), OVMF_FWDESC_DIR=str(self.desc))

    @staticmethod
    def guid(value):
        import uuid
        return uuid.UUID(value).bytes_le

    def signatures(self, kind, payload):
        import struct
        owner = self.guid(self.GLOBAL)
        signature = owner + payload
        return self.guid(kind) + struct.pack('<III', 28+len(signature), 0, len(signature)) + signature

    def keys(self):
        rsa = self.signatures(self.RSA, b'\x80' + b'\x11'*254 + b'\x01')
        db = self.signatures(self.SHA256, b'\x22'*32)
        return [('PK', self.GLOBAL, rsa, 0x3f, 0x27), ('KEK', self.GLOBAL, rsa, 0x3f, 0x27),
                ('db', self.DB, db, 0x3f, 0x27), ('SecureBootEnable', self.ENABLE, b'\x01', 0x3f, 3)]

    def store(self, records=(), filename='OVMF_VARS.secboot.fd', normal=False):
        import struct
        data = bytearray(b'\xff' * 4096)
        data[:72] = b'\x00' * 72
        data[16:32] = self.guid('fff12b8d-7696-4c8b-a985-2747075b4f50')
        struct.pack_into('<Q4sIHHHBBIIII', data, 32, 4096, b'_FVH', 0x800, 72, 0, 0, 0, 2, 1, 4096, 0, 0)
        checksum = (-sum(struct.unpack_from('<36H', data))) & 0xffff
        struct.pack_into('<H', data, 50, checksum)
        signature = 'ddcf3616-3275-4164-98b6-fe85707ffe7d' if normal else 'aaf32c78-947b-439a-a180-2e144ec37792'
        data[72:100] = self.guid(signature) + struct.pack('<IBBHI', len(data)-72, 0x5a, 0xfe, 0, 0)
        offset = 100
        for name, vendor, payload, state, attrs in records:
            encoded = (name+'\x00').encode('utf-16-le')
            if normal:
                header = struct.pack('<HBBIII16s', 0x55aa, state, 0, attrs, len(encoded), len(payload), self.guid(vendor))
            else:
                header = struct.pack('<HBBIQ16sIII16s', 0x55aa, state, 0, attrs, 0, b'\x00'*16, 0, len(encoded), len(payload), self.guid(vendor))
            entry = header + encoded + payload
            data[offset:offset+len(entry)] = entry
            offset = (offset + len(entry) + 3) & ~3
        path = self.base / filename
        path.write_bytes(data)
        return path

    def run_function(self, name, *args):
        import subprocess
        result = subprocess.run(['bash', '-c', 'source "$1"; shift; function_name=$1; shift; "$function_name" "$@"',
                                 'ovmf-fixture', self.lib, name, *map(str,args)], env=self.env, capture_output=True, text=True, timeout=10)
        return result.returncode, result.stdout.strip()

    def descriptor(self, code, vars_, features=('secure-boot','enrolled-keys'), code_format='raw', vars_format='raw', mode=None):
        mapping = {'device':'flash','executable':{'filename':str(code),'format':code_format},
                   'nvram-template':{'filename':str(vars_),'format':vars_format}}
        if mode is not None:
            mapping['mode'] = mode
        path = self.desc / '10-fixture.json'
        path.write_text(json.dumps({'description':'fixture only','features':list(features),'mapping':mapping}), encoding='utf-8')
        return path

    def assert_not_enrolled(self, records):
        rc, out = self.run_function('ovmf_vars_enrollment', self.store(records))
        self.assertFalse(out.startswith('ENROLLED'), out)
        return rc, out

    def test_positive_authenticated_keys_and_blank_template(self):
        rc, out = self.run_function('ovmf_vars_enrollment', self.store(self.keys()))
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.startswith('ENROLLED'), out)
        rc, out = self.run_function('ovmf_vars_enrollment', self.store())
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.startswith('BLANK'), out)

    def test_standard_guid_store_layout_is_supported(self):
        rc, out = self.run_function('ovmf_vars_enrollment', self.store(self.keys(), normal=True))
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.startswith('ENROLLED'), out)

    def test_empty_platform_key_cannot_prove_enrollment(self):
        keys = self.keys(); keys[0] = ('PK', self.GLOBAL, b'', 0x3f, 0x27)
        self.assertEqual(self.assert_not_enrolled(keys)[0], 1)

    def test_deleted_transition_and_header_only_keys_are_not_live(self):
        for state in (0x3c, 0x3d, 0x3e, 0x7f):
            with self.subTest(state=state):
                keys = self.keys(); keys[0] = (*keys[0][:3], state, 0x27)
                self.assertEqual(self.assert_not_enrolled(keys)[0], 1)

    def test_foreign_namespace_and_platform_key_alone_fail_closed(self):
        keys = self.keys(); keys[0] = ('PK', self.DB, keys[0][2], 0x3f, 0x27)
        self.assertEqual(self.assert_not_enrolled(keys)[0], 1)
        self.assertEqual(self.assert_not_enrolled(self.keys()[:1])[0], 1)

    def test_truncated_variable_and_store_lengths_fail_closed(self):
        import struct
        path = self.store(self.keys()); data=bytearray(path.read_bytes())
        struct.pack_into('<I', data, 100+40, 4096)
        path.write_bytes(data)
        rc,out=self.run_function('ovmf_vars_enrollment',path)
        self.assertEqual(rc,1,out); self.assertIn('UNKNOWN',out)
        path=self.store(self.keys()); path.write_bytes(path.read_bytes()[:300])
        rc,out=self.run_function('ovmf_vars_enrollment',path)
        self.assertEqual(rc,1,out); self.assertIn('truncated',out)

    def test_missing_authenticated_attributes_and_disabled_intent_fail(self):
        keys=self.keys(); keys[0]=(*keys[0][:4],7)
        self.assertEqual(self.assert_not_enrolled(keys)[0],1)
        keys=self.keys(); keys[-1]=('SecureBootEnable',self.ENABLE,b'\x00',0x3f,3)
        self.assertEqual(self.assert_not_enrolled(keys)[0],1)
        self.assertEqual(self.assert_not_enrolled(self.keys()[:-1])[0],1)

    def test_malformed_signature_list_and_duplicate_live_pk_fail(self):
        keys=self.keys(); keys[0]=('PK',self.GLOBAL,b'not a signature list',0x3f,0x27)
        self.assertEqual(self.assert_not_enrolled(keys)[0],1)
        self.assertEqual(self.assert_not_enrolled(self.keys()+self.keys()[:1])[0],1)

    def test_printing_pk_with_external_tool_does_not_bypass_content(self):
        tool_dir=self.base/'bin';tool_dir.mkdir()
        tool=tool_dir/'virt-fw-vars';tool.write_text('#!/bin/sh\nprintf "PK: fake\\nKEK: fake\\ndb: fake\\n"\n',encoding='utf-8');tool.chmod(0o755)
        self.env['PATH']=str(tool_dir)+os.pathsep+self.env['PATH']
        rc,out=self.run_function('ovmf_vars_enrollment',self.store())
        self.assertEqual(rc,0,out);self.assertTrue(out.startswith('BLANK'),out)

    def test_lying_secboot_names_never_prove_capability(self):
        for name in ('OVMF_CODE.secboot.fd','OVMF_CODE.secboot.qcow2'):
            with self.subTest(name=name):
                code=self.store(filename=name)
                rc,out=self.run_function('ovmf_sb_capability',code)
                self.assertEqual(rc,1,out);self.assertTrue(out.startswith('unknown'),out)

    def test_valid_descriptor_and_content_prove_pair_and_capability(self):
        code=self.store(filename='OVMF_CODE.fd');vars_=self.store(self.keys())
        self.descriptor(code,vars_)  # omitted mode defaults to split upstream.
        for fn,args in [('ovmf_pair_status',(code,vars_)),('ovmf_sb_capability',(code,)),('find_vars_for_code',(code,))]:
            rc,out=self.run_function(fn,*args)
            self.assertEqual(rc,0,out)
        rc,out=self.run_function('ovmf_sb_capability',code)
        self.assertTrue(out.startswith('yes'),out)

    def test_descriptor_cannot_bless_invalid_image_or_wrong_format(self):
        code=self.store(filename='OVMF_CODE.fd');vars_=self.store(self.keys())
        self.descriptor(code,vars_);code.write_bytes(b'empty or truncated')
        for fn,args in [('ovmf_pair_status',(code,vars_)),('ovmf_sb_capability',(code,))]:
            rc,out=self.run_function(fn,*args);self.assertEqual(rc,1,out)
        code=self.store(filename='OVMF_CODE.fd')
        self.descriptor(code,vars_,code_format='qcow2')
        rc,out=self.run_function('ovmf_pair_status',code,vars_)
        self.assertEqual(rc,1,out);self.assertIn('FORMAT_MISMATCH',out)

    def test_descriptor_enrolled_feature_requires_live_keys(self):
        code=self.store(filename='OVMF_CODE.fd');vars_=self.store()
        self.descriptor(code,vars_)
        rc,out=self.run_function('ovmf_pair_status',code,vars_)
        self.assertEqual(rc,1,out);self.assertIn('ENROLLMENT_MISMATCH',out)

    def test_same_directory_and_size_names_do_not_prove_common_build(self):
        code=self.store(filename='OVMF_CODE_4M.fd');vars_=self.store(self.keys(),filename='OVMF_VARS_4M.fd')
        rc,out=self.run_function('ovmf_pair_status',code,vars_)
        self.assertEqual(rc,1,out);self.assertIn('UNPROVEN_PAIR',out)



    def test_blank_descriptor_without_features_is_readable(self):
        code=self.store(filename='OVMF_CODE.fd');vars_=self.store()
        self.descriptor(code,vars_,features=())
        rc,out=self.run_function('ovmf_pair_status',code,vars_)
        self.assertEqual(rc,0,out)
        rc,out=self.run_function('ovmf_sb_capability',code)
        self.assertEqual(rc,0,out);self.assertTrue(out.startswith('no '),out)

    def test_invalid_checksum_is_not_blessed_by_descriptor(self):
        code=self.store(filename='OVMF_CODE.fd');vars_=self.store(self.keys())
        self.descriptor(code,vars_)
        data=bytearray(code.read_bytes());data[50]^=1;code.write_bytes(data)
        rc,out=self.run_function('ovmf_pair_status',code,vars_)
        self.assertEqual(rc,1,out);self.assertIn('checksum',out)

    def test_install_refuses_existing_file_and_missing_code_hint(self):
        source=self.store(self.keys());target=self.base/'destination';target.mkdir()
        output=target/'existing.fd';output.write_bytes(b'keep this existing firmware')
        rc,out=self.run_function('ovmf_install_verified_vars',source,target,output.name,'')
        self.assertEqual(rc,1,out);self.assertEqual(output.read_bytes(),b'keep this existing firmware')
        rc,out=self.run_function('ovmf_install_verified_vars',source,target,'new.fd',self.base/'missing_CODE.fd')
        self.assertEqual(rc,1,out);self.assertFalse((target/'new.fd').exists())
        rc,out=self.run_function('ovmf_install_verified_vars',source,target,'published.fd','')
        self.assertEqual(rc,0,out);self.assertEqual((target/'published.fd').read_bytes(),source.read_bytes())
        tool_dir=self.base/'copy-tool';tool_dir.mkdir()
        tool=tool_dir/'cp';tool.write_text('#!/bin/sh\nfor target do :; done\nprintf corrupt > "$target"\n',encoding='utf-8');tool.chmod(0o755)
        self.env['PATH']=str(tool_dir)+os.pathsep+self.env['PATH']
        rc,out=self.run_function('ovmf_install_verified_vars',source,target,'corrupt.fd','')
        self.assertEqual(rc,1,out);self.assertFalse((target/'corrupt.fd').exists())


    def test_actual_qcow2_mixed_format_pair(self):
        import subprocess
        if not shutil.which('qemu-img'):
            self.skipTest('qemu-img unavailable; raw proof tests still run')
        code=self.store(filename='OVMF_CODE.fd');raw=self.store(self.keys());vars_=self.base/'OVMF_VARS.qcow2'
        subprocess.run(['qemu-img','convert','-f','raw','-O','qcow2',str(raw),str(vars_)],check=True,capture_output=True)
        self.descriptor(code,vars_,vars_format='qcow2')
        rc,out=self.run_function('ovmf_pair_status',code,vars_)
        self.assertEqual(rc,0,out)

    def test_qcow2_backing_file_is_not_trusted_firmware(self):
        import subprocess
        if not shutil.which('qemu-img'):
            self.skipTest('qemu-img unavailable; backing-chain live control not run')
        backing=self.store(self.keys());vars_=self.base/'OVMF_VARS.qcow2'
        subprocess.run(['qemu-img','create','-f','qcow2','-F','raw','-b',str(backing),str(vars_)],check=True,capture_output=True)
        rc,out=self.run_function('ovmf_vars_enrollment',vars_)
        self.assertEqual(rc,1,out);self.assertTrue(out.startswith('UNKNOWN'),out)


def main() -> int:
    rc = 0 if unittest.main(argv=[sys.argv[0]], exit=False).result.wasSuccessful() else 1
    return rc


if __name__ == '__main__':
    sys.exit(main())
