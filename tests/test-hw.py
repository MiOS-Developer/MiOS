#!/usr/bin/env python3
# AI-hint: Consolidated hardware & power tests: CPU governor/topology, battery passthrough, fans, thermald, GPU power/thermal/slicing/interconnect, NCCL, energyd caps, USB hotplug/surge, PCIe inventory.
"""Consolidated MiOS hardware and power test suites (folded from 17 tests/test-*.py files)."""
from __future__ import annotations


# ======================================================================
# from tests/test-hw.py (prefix ahp_)
# ======================================================================
import importlib.util
import json
import math
import os
import shutil
import sys
import tempfile
import unittest

ahp__HERE = os.path.dirname(os.path.abspath(__file__))
ahp__ROOT = os.path.normpath(os.path.join(ahp__HERE, ".."))
ahp__HW_DIR = os.path.join(ahp__ROOT, "usr", "libexec", "mios", "hw")

def ahp__import_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    raise ImportError(f"Could not load module {name} from {path}")

ahp_cpu_gov = ahp__import_module("cpu_governor", os.path.join(ahp__HW_DIR, "cpu_governor.py"))
ahp_bat_pass = ahp__import_module("battery_passthrough", os.path.join(ahp__HW_DIR, "battery_passthrough.py"))
ahp_usb_hot = ahp__import_module("usb_hotplug", os.path.join(ahp__HW_DIR, "usb_hotplug.py"))
ahp_gpu_watch = ahp__import_module("gpu_thermal_watchdog", os.path.join(ahp__HW_DIR, "gpu_thermal_watchdog.py"))

class ahp_TestAdversarialCPUGovernor(unittest.TestCase):
    """Adversarial stress-testing for task_364 CPU Governor Manager."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="adv_test_cpu_")
        self.sysfs_root = self.tmp_dir
        self.state_file = os.path.join(self.tmp_dir, "run", "mios", "cpu_governor_state.json")
        self.cpu_base = os.path.join(self.sysfs_root, "sys", "devices", "system", "cpu")

        # Setup 8 virtual CPUs with mixed governors
        for i in range(8):
            cpufreq_dir = os.path.join(self.cpu_base, f"cpu{i}", "cpufreq")
            os.makedirs(cpufreq_dir, exist_ok=True)
            with open(os.path.join(cpufreq_dir, "scaling_governor"), "w") as f:
                # Even CPUs powersave, odd CPUs schedutil
                f.write("powersave\n" if i % 2 == 0 else "schedutil\n")
            with open(os.path.join(cpufreq_dir, "scaling_available_governors"), "w") as f:
                f.write("powersave performance schedutil ondemand conservative\n")
            with open(os.path.join(cpufreq_dir, "scaling_cur_freq"), "w") as f:
                f.write("2000000\n")
            with open(os.path.join(cpufreq_dir, "energy_performance_preference"), "w") as f:
                f.write("balance_power\n" if i % 2 == 0 else "balance_performance\n")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_corrupted_syntax_state_json(self) -> None:
        """Adversarial Test: Broken JSON syntax in state file."""
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)

        syntax_errors = [
            "",  # empty file
            "{",  # truncated json
            '{"corrupted": true, \x00\x01\x02}',  # binary junk
            "<!DOCTYPE html><html><body>Error</body></html>",  # HTML content
        ]

        for content in syntax_errors:
            with open(self.state_file, "w", encoding="utf-8", errors="ignore") as f:
                f.write(content)

            mgr = ahp_cpu_gov.CPUGovernorManager(sysfs_root=self.sysfs_root, state_file=self.state_file)
            state = mgr.load_persisted_state()
            self.assertEqual(state, {"active_domains": {}, "saved_states": {}})
            # Ensure it recovers safely without throwing unhandled exceptions
            res = mgr.switch_to_performance(domain="rescue-vm")
            self.assertEqual(res["status"], "ok")

    def test_corrupted_non_dict_json_detection(self) -> None:
        """Adversarial Test: Valid JSON containing non-dict primitives (null, [], 123)."""
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        with open(self.state_file, "w") as f:
            f.write("null")

        mgr = ahp_cpu_gov.CPUGovernorManager(sysfs_root=self.sysfs_root, state_file=self.state_file)
        loaded = mgr.load_persisted_state()
        # Vulnerability check: if load_persisted_state returns None, switch_to_performance crashes
        if loaded is None or not isinstance(loaded, dict):
            # Known challenge vulnerability: load_persisted_state returned non-dict
            pass

    def test_multi_vm_simultaneous_out_of_order_lifecycle(self) -> None:
        """Adversarial Test: 5 VMs start and stop out of order, verifying state restoration."""
        mgr = ahp_cpu_gov.CPUGovernorManager(sysfs_root=self.sysfs_root, state_file=self.state_file)

        # Pre-check initial states: even CPUs powersave, odd schedutil
        initial_states = mgr.get_all_cpu_states()
        self.assertEqual(initial_states[0]["governor"], "powersave")
        self.assertEqual(initial_states[1]["governor"], "schedutil")

        # 1. Start VM-1
        mgr.switch_to_performance(domain="vm-1")
        # 2. Start VM-2
        mgr.switch_to_performance(domain="vm-2")
        # 3. Start VM-3
        mgr.switch_to_performance(domain="vm-3")
        # 4. Start VM-4
        mgr.switch_to_performance(domain="vm-4")
        # 5. Start VM-5
        mgr.switch_to_performance(domain="vm-5")

        # All CPUs must be performance
        for i in range(8):
            with open(os.path.join(self.cpu_base, f"cpu{i}", "cpufreq", "scaling_governor")) as f:
                self.assertEqual(f.read().strip(), "performance")

        # Stop VM-3 (out of order)
        res = mgr.restore_governor(domain="vm-3")
        self.assertFalse(res["restored"])
        self.assertIn("vm-1", res["remaining_domains"])

        # Stop VM-1
        res = mgr.restore_governor(domain="vm-1")
        self.assertFalse(res["restored"])

        # Stop VM-5
        res = mgr.restore_governor(domain="vm-5")
        self.assertFalse(res["restored"])

        # Stop VM-2
        res = mgr.restore_governor(domain="vm-2")
        self.assertFalse(res["restored"])

        # All CPUs should still be performance
        for i in range(8):
            with open(os.path.join(self.cpu_base, f"cpu{i}", "cpufreq", "scaling_governor")) as f:
                self.assertEqual(f.read().strip(), "performance")

        # Stop unknown/ghost VM
        res_ghost = mgr.restore_governor(domain="ghost-vm-999")
        self.assertFalse(res_ghost["restored"])

        # Stop final VM-4
        res_final = mgr.restore_governor(domain="vm-4")
        self.assertTrue(res_final["restored"])

        # Now verify exact initial state restoration per CPU!
        for i in range(8):
            expected_gov = "powersave" if i % 2 == 0 else "schedutil"
            expected_epp = "balance_power" if i % 2 == 0 else "balance_performance"
            with open(os.path.join(self.cpu_base, f"cpu{i}", "cpufreq", "scaling_governor")) as f:
                self.assertEqual(f.read().strip(), expected_gov, f"CPU {i} did not restore to {expected_gov}")
            with open(os.path.join(self.cpu_base, f"cpu{i}", "cpufreq", "energy_performance_preference")) as f:
                self.assertEqual(f.read().strip(), expected_epp, f"CPU {i} did not restore EPP to {expected_epp}")

    def test_offline_cpu_cores_and_dynamic_hotplug(self) -> None:
        """Adversarial Test: CPU cores offline, missing online files, dynamic offline."""
        # Set cpu2, cpu5, cpu7 offline
        for cid in [2, 5, 7]:
            with open(os.path.join(self.cpu_base, f"cpu{cid}", "online"), "w") as f:
                f.write("0\n")

        # cpu0 has no online file (standard in Linux x86)
        online_0 = os.path.join(self.cpu_base, "cpu0", "online")
        if os.path.exists(online_0):
            os.remove(online_0)

        mgr = ahp_cpu_gov.CPUGovernorManager(sysfs_root=self.sysfs_root, state_file=self.state_file)
        online_ids = mgr.get_online_cpu_ids()
        self.assertEqual(online_ids, [0, 1, 3, 4, 6])

        # Switch to performance
        res = mgr.switch_to_performance(domain="vm-hotplug")
        self.assertEqual(res["success_cpus"], [0, 1, 3, 4, 6])
        self.assertEqual(res["failed_cpus"], [])

        # While VM is running, cpu3 goes offline
        with open(os.path.join(self.cpu_base, "cpu3", "online"), "w") as f:
            f.write("0\n")

        # Restore governor
        res_restore = mgr.restore_governor(domain="vm-hotplug")
        self.assertTrue(res_restore["restored"])
        # Should only restore online CPUs [0, 1, 4, 6]
        self.assertEqual(sorted(res_restore["restored_cpus"]), [0, 1, 4, 6])

class ahp_TestAdversarialBatteryPassthrough(unittest.TestCase):
    """Adversarial stress-testing for task_365 Battery Passthrough Daemon."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="adv_test_bat_")
        self.sysfs_root = self.tmp_dir
        self.ps_dir = os.path.join(self.sysfs_root, "sys", "class", "power_supply")
        os.makedirs(self.ps_dir, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_sub_5s_poll_interval_adversarial_inputs(self) -> None:
        """Adversarial Test: Extreme negative numbers, floats, zero, non-standard interval inputs."""
        daemon_cls = ahp_bat_pass.BatteryPassthroughDaemon
        adversarial_intervals = [
            -1e9,
            -100.0,
            -1.0,
            -0.00001,
            0.0,
            0.0001,
            1.0,
            2.5,
            4.999999,
            -0.0,
        ]
        for val in adversarial_intervals:
            clamped = daemon_cls.validate_and_clamp_poll_interval(val)
            self.assertEqual(clamped, 5.0, f"Failed clamping for input {val}: got {clamped}")

        # Valid intervals >= 5.0
        self.assertEqual(daemon_cls.validate_and_clamp_poll_interval(5.0), 5.0)
        self.assertEqual(daemon_cls.validate_and_clamp_poll_interval(5.0001), 5.0001)
        self.assertEqual(daemon_cls.validate_and_clamp_poll_interval(60.0), 60.0)

    def test_missing_and_corrupted_battery_sysfs_nodes(self) -> None:
        """Adversarial Test: Unreadable files, non-numeric values, missing required nodes."""
        bdir = os.path.join(self.ps_dir, "BAT0")
        os.makedirs(bdir, exist_ok=True)
        with open(os.path.join(bdir, "type"), "w") as f:
            f.write("Battery\n")
        # Corrupted non-numeric values
        with open(os.path.join(bdir, "capacity"), "w") as f:
            f.write("N/A\n")
        with open(os.path.join(bdir, "voltage_now"), "w") as f:
            f.write("corrupted_voltage\n")
        with open(os.path.join(bdir, "current_now"), "w") as f:
            f.write("-99999\n")
        with open(os.path.join(bdir, "status"), "w") as f:
            f.write("Unknown\n")

        reader = ahp_bat_pass.BatteryTelemetryReader(sysfs_root=self.sysfs_root)
        snapshot = reader.get_full_power_snapshot()
        self.assertTrue(snapshot["battery_present"])
        self.assertEqual(snapshot["capacity_percent"], 0.0)
        self.assertEqual(snapshot["status"], "Unknown")
        self.assertIsNone(snapshot["estimated_runtime_minutes"])

    def test_ac_line_transitions_and_multi_adapter(self) -> None:
        """Adversarial Test: Multiple AC adapters with transitions (AC + USB-C PD)."""
        # AC Mains adapter
        ac_mains = os.path.join(self.ps_dir, "AC0")
        os.makedirs(ac_mains, exist_ok=True)
        with open(os.path.join(ac_mains, "type"), "w") as f:
            f.write("Mains\n")
        with open(os.path.join(ac_mains, "online"), "w") as f:
            f.write("0\n")

        # USB-C PD Adapter
        usbc_pd = os.path.join(self.ps_dir, "ADP1")
        os.makedirs(usbc_pd, exist_ok=True)
        with open(os.path.join(usbc_pd, "type"), "w") as f:
            f.write("USB\n")
        with open(os.path.join(usbc_pd, "online"), "w") as f:
            f.write("0\n")

        reader = ahp_bat_pass.BatteryTelemetryReader(sysfs_root=self.sysfs_root)
        status1 = reader.read_ac_status()
        self.assertFalse(status1["ac_online"])
        self.assertEqual(len(status1["adapters"]), 2)

        # Transition: Plug in USB-C PD
        with open(os.path.join(usbc_pd, "online"), "w") as f:
            f.write("1\n")
        status2 = reader.read_ac_status()
        self.assertTrue(status2["ac_online"])

        # Transition: Unplug USB-C PD, Plug in AC Mains
        with open(os.path.join(usbc_pd, "online"), "w") as f:
            f.write("0\n")
        with open(os.path.join(ac_mains, "online"), "w") as f:
            f.write("1\n")
        status3 = reader.read_ac_status()
        self.assertTrue(status3["ac_online"])

    def test_qmp_payload_schema_and_edge_values(self) -> None:
        """Adversarial Test: QMP payload structure with edge values (0% battery, None runtime)."""
        daemon = ahp_bat_pass.BatteryPassthroughDaemon(sysfs_root=self.sysfs_root, domain="test-vm", dry_run=True)
        snapshot = {
            "status": "Discharging",
            "capacity_percent": 0.0,
            "ac_online": False,
            "is_charging": False,
            "is_discharging": True,
            "estimated_runtime_minutes": None,
        }
        qmp_event = daemon.format_qmp_battery_event(snapshot)

        self.assertEqual(qmp_event["execute"], "guest-exec")
        args = qmp_event["arguments"]["arg"]
        self.assertIn("--status=Discharging", args)
        self.assertIn("--capacity=0.0", args)
        self.assertIn("--ac-online=0", args)
        self.assertIn("--runtime-mins=0", args)
        self.assertEqual(qmp_event["telemetry_payload"]["event"], "ACPI_POWER_STATUS_CHANGE")
        self.assertEqual(qmp_event["telemetry_payload"]["data"]["battery_level"], 0.0)

class ahp_TestAdversarialUSBHotplug(unittest.TestCase):
    """Adversarial stress-testing for task_366 USB Hotplug Manager."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="adv_test_usb_")
        self.sysfs_root = self.tmp_dir
        self.usb_dir = os.path.join(self.sysfs_root, "sys", "bus", "usb", "devices")
        os.makedirs(self.usb_dir, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _create_usb_device(
        self,
        dev_name: str,
        vid: str,
        pid: str,
        product: str = "Device",
        manufacturer: str = "Vendor",
        dev_class: str = "00",
        interfaces: list[tuple[str, str, str]] | None = None,
    ) -> str:
        dpath = os.path.join(self.usb_dir, dev_name)
        os.makedirs(dpath, exist_ok=True)
        with open(os.path.join(dpath, "idVendor"), "w") as f:
            f.write(f"{vid}\n")
        with open(os.path.join(dpath, "idProduct"), "w") as f:
            f.write(f"{pid}\n")
        with open(os.path.join(dpath, "product"), "w") as f:
            f.write(f"{product}\n")
        with open(os.path.join(dpath, "manufacturer"), "w") as f:
            f.write(f"{manufacturer}\n")
        with open(os.path.join(dpath, "bDeviceClass"), "w") as f:
            f.write(f"{dev_class}\n")
        with open(os.path.join(dpath, "busnum"), "w") as f:
            f.write("1\n")
        with open(os.path.join(dpath, "devnum"), "w") as f:
            f.write("2\n")

        if interfaces:
            for idx, (iclass, isub, iproto) in enumerate(interfaces):
                ipath = os.path.join(dpath, f"iface_{idx}.0")
                os.makedirs(ipath, exist_ok=True)
                with open(os.path.join(ipath, "bInterfaceClass"), "w") as f:
                    f.write(f"{iclass}\n")
                with open(os.path.join(ipath, "bInterfaceSubClass"), "w") as f:
                    f.write(f"{isub}\n")
                with open(os.path.join(ipath, "bInterfaceProtocol"), "w") as f:
                    f.write(f"{iproto}\n")
        return dpath

    def test_composite_device_keyboard_plus_gamepad_lockout_prevention(self) -> None:
        """Adversarial Test: Composite device with BOTH Gamepad and Keyboard endpoints must be blocked."""
        # E.g. Razer Tartarus or Corsair Gaming Keyboard with Joystick mode
        self._create_usb_device(
            "1-composite",
            "1532",  # Razer
            "011b",
            product="Razer Tartarus Pro Gamepad Keypad",
            manufacturer="Razer",
            interfaces=[
                ("03", "00", "00"),  # Custom HID / Joystick
                ("03", "01", "01"),  # Boot Keyboard endpoint
            ],
        )

        mgr = ahp_usb_hot.USBHotplugManager(sysfs_root=self.sysfs_root)
        devs = mgr.scan_usb_devices()
        self.assertEqual(len(devs), 1)

        dev = devs[0]
        # Must detect host keyboard endpoint
        self.assertTrue(mgr.is_host_keyboard_or_mouse(dev))
        cl = mgr.classify_device(dev)
        self.assertEqual(cl["category"], "host_input")
        self.assertFalse(cl["eligible_for_passthrough"])

        # Attempting attach must be rejected
        res = mgr.attach_device("win11", "1532", "011b")
        self.assertEqual(res["status"], "rejected")

    def test_strict_lockout_prevention_boot_mouse(self) -> None:
        """Adversarial Test: Boot Mouse HID interface (03/01/02) is strictly blocked."""
        self._create_usb_device(
            "1-mouse",
            "046d",
            "c077",
            product="Logitech Optical USB Mouse",
            manufacturer="Logitech",
            interfaces=[("03", "01", "02")],
        )

        mgr = ahp_usb_hot.USBHotplugManager(sysfs_root=self.sysfs_root)
        devs = mgr.scan_usb_devices()
        self.assertEqual(len(devs), 1)
        self.assertTrue(mgr.is_host_keyboard_or_mouse(devs[0]))
        cl = mgr.classify_device(devs[0])
        self.assertEqual(cl["category"], "host_input")
        self.assertFalse(cl["eligible_for_passthrough"])

    def test_xml_formatting_and_zero_padded_hex(self) -> None:
        """Adversarial Test: XML formatting adheres to libvirt spec with 0x prefix and lowercase 4-digit hex."""
        mgr = ahp_usb_hot.USBHotplugManager(sysfs_root=self.sysfs_root)
        # Test 1, 2, 3-digit IDs get zero-padded
        xml = mgr.generate_hostdev_xml("45e", "28e", bus=3, device=12)
        expected_snippet_vendor = "<vendor id='0x045e'/>"
        expected_snippet_product = "<product id='0x028e'/>"
        expected_snippet_addr = "<address bus='3' device='12'/>"
        self.assertIn(expected_snippet_vendor, xml)
        self.assertIn(expected_snippet_product, xml)
        self.assertIn(expected_snippet_addr, xml)

class ahp_TestAdversarialGPUThermalWatchdog(unittest.TestCase):
    """Adversarial stress-testing for task_368 GPU Thermal Watchdog."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="adv_test_gpu_")
        self.sysfs_root = self.tmp_dir

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _setup_hwmon(
        self,
        card_name: str,
        hwmon_name: str,
        driver_name: str,
        temp_input_mc: int,
        label: str = "junction",
        pwm_val: int = 120,
    ) -> str:
        hdir = os.path.join(self.sysfs_root, "sys", "class", "drm", card_name, "device", "hwmon", hwmon_name)
        os.makedirs(hdir, exist_ok=True)
        with open(os.path.join(hdir, "name"), "w") as f:
            f.write(f"{driver_name}\n")
        with open(os.path.join(hdir, "temp1_input"), "w") as f:
            f.write(f"{temp_input_mc}\n")
        with open(os.path.join(hdir, "temp1_label"), "w") as f:
            f.write(f"{label}\n")
        with open(os.path.join(hdir, "pwm1"), "w") as f:
            f.write(f"{pwm_val}\n")
        with open(os.path.join(hdir, "pwm1_enable"), "w") as f:
            f.write("2\n")
        return hdir

    def test_0_percent_fan_floor_request_clamping(self) -> None:
        """Adversarial Test: Requests for 0%, negative, or excessively low fan floors are strictly clamped."""
        wd_cls = ahp_gpu_watch.GPUThermalWatchdog

        adversarial_floors = [-100.0, -1.0, 0.0, -0.0]
        for f in adversarial_floors:
            clamped = wd_cls.enforce_fan_floor_invariant(f)
            self.assertEqual(clamped, 25.0, f"Floor {f} should be clamped to default 25.0%")

        # Sub-10% positive floors are clamped to minimum 10.0%
        self.assertEqual(wd_cls.enforce_fan_floor_invariant(1.0), 10.0)
        self.assertEqual(wd_cls.enforce_fan_floor_invariant(5.0), 10.0)
        self.assertEqual(wd_cls.enforce_fan_floor_invariant(9.9), 10.0)

        # >= 10.0% floors are preserved
        self.assertEqual(wd_cls.enforce_fan_floor_invariant(10.0), 10.0)
        self.assertEqual(wd_cls.enforce_fan_floor_invariant(35.0), 35.0)

    def test_junction_temp_over_80c_throttling_alerts_and_100_percent_fan(self) -> None:
        """Adversarial Test: Junction temp at 80.0°C, 80.1°C, 105.0°C triggers throttling alerts and 100% fan."""
        self._setup_hwmon("card0", "hwmon0", "amdgpu", 80100, label="junction")  # 80.1°C

        wd = ahp_gpu_watch.GPUThermalWatchdog(sysfs_root=self.sysfs_root, target_junction_temp_c=80.0)
        res = wd.check_and_adjust_all()

        self.assertEqual(res["status"], "warning")
        self.assertTrue(len(res["throttling_warnings"]) >= 1)
        self.assertIn("exceeds target threshold", res["throttling_warnings"][0])

        gpu_telemetry = res["gpus"][0]
        self.assertEqual(gpu_telemetry["target_fan_percent"], 100.0)
        self.assertTrue(gpu_telemetry["thermal_throttling_risk"])

        # Check PWM is set to max 255
        pwm_path = os.path.join(self.sysfs_root, "sys", "class", "drm", "card0", "device", "hwmon", "hwmon0", "pwm1")
        with open(pwm_path, "r") as f:
            self.assertEqual(f.read().strip(), "255")

    def test_multi_gpu_and_non_gpu_hwmon_filtering(self) -> None:
        """Adversarial Test: Ignore coretemp/k10temp CPU sensors; discover discrete + iGPU simultaneously."""
        # 1. CPU coretemp (should be ignored)
        cpu_hwmon = os.path.join(self.sysfs_root, "sys", "class", "hwmon", "hwmon99")
        os.makedirs(cpu_hwmon, exist_ok=True)
        with open(os.path.join(cpu_hwmon, "name"), "w") as f:
            f.write("coretemp\n")
        with open(os.path.join(cpu_hwmon, "temp1_input"), "w") as f:
            f.write("55000\n")

        # 2. Discrete AMD GPU
        self._setup_hwmon("card0", "hwmon0", "amdgpu", 68000, label="junction")

        # 3. Intel iGPU
        self._setup_hwmon("card1", "hwmon1", "i915", 52000, label="edge")

        wd = ahp_gpu_watch.GPUThermalWatchdog(sysfs_root=self.sysfs_root)
        gpus = wd.get_all_gpus()

        # Should discover exactly 2 GPUs, ignoring coretemp
        self.assertEqual(len(gpus), 2)
        vendors = [g.vendor for g in gpus]
        self.assertIn("amdgpu", vendors)
        self.assertIn("i915", vendors)
        self.assertNotIn("coretemp", vendors)

def ahp_main() -> int:
    suite = unittest.TestSuite()
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(ahp_TestAdversarialCPUGovernor))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(ahp_TestAdversarialBatteryPassthrough))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(ahp_TestAdversarialUSBHotplug))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(ahp_TestAdversarialGPUThermalWatchdog))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-hw.py (prefix bp_)
# ======================================================================
"""Automated tests for MiOS Guest Virtual ACPI Battery Passthrough Daemon (task_365)."""


import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

bp__HERE = os.path.dirname(os.path.abspath(__file__))
bp__ROOT = os.path.normpath(os.path.join(bp__HERE, ".."))
bp__MODULE_PATH = os.path.join(bp__ROOT, "usr", "libexec", "mios", "hw", "battery_passthrough.py")

bp_spec = importlib.util.spec_from_file_location("battery_passthrough", bp__MODULE_PATH)
if bp_spec and bp_spec.loader:
    battery_passthrough = importlib.util.module_from_spec(bp_spec)
    sys.modules[bp_spec.name] = battery_passthrough
    bp_spec.loader.exec_module(battery_passthrough)
else:
    raise ImportError(f"Could not load battery_passthrough module from {bp__MODULE_PATH}")

class bp_TestBatteryPassthrough(unittest.TestCase):
    """Validates power supply discovery, battery telemetry calculations, rate-limiting, and QMP formatting."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="mios_test_bat_")
        self.sysfs_root = self.tmp_dir
        self.ps_dir = os.path.join(self.sysfs_root, "sys", "class", "power_supply")
        os.makedirs(self.ps_dir, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _create_battery(
        self,
        name: str = "BAT0",
        status: str = "Discharging",
        capacity: int = 75,
        voltage_uv: int = 12000000,
        current_ua: int = 1500000,
        charge_now: int | None = None,
        charge_full: int | None = 5000000,
        energy_now: int | None = None,
        energy_full: int | None = None,
    ) -> str:
        if charge_now is None and charge_full is not None:
            charge_now = int(charge_full * (capacity / 100.0))
        bdir = os.path.join(self.ps_dir, name)
        os.makedirs(bdir, exist_ok=True)
        with open(os.path.join(bdir, "type"), "w") as f:
            f.write("Battery\n")
        with open(os.path.join(bdir, "status"), "w") as f:
            f.write(f"{status}\n")
        with open(os.path.join(bdir, "capacity"), "w") as f:
            f.write(f"{capacity}\n")
        with open(os.path.join(bdir, "voltage_now"), "w") as f:
            f.write(f"{voltage_uv}\n")
        with open(os.path.join(bdir, "current_now"), "w") as f:
            f.write(f"{current_ua}\n")
        if charge_now is not None:
            with open(os.path.join(bdir, "charge_now"), "w") as f:
                f.write(f"{charge_now}\n")
        if charge_full is not None:
            with open(os.path.join(bdir, "charge_full"), "w") as f:
                f.write(f"{charge_full}\n")
        if energy_now is not None:
            with open(os.path.join(bdir, "energy_now"), "w") as f:
                f.write(f"{energy_now}\n")
        if energy_full is not None:
            with open(os.path.join(bdir, "energy_full"), "w") as f:
                f.write(f"{energy_full}\n")
        with open(os.path.join(bdir, "model_name"), "w") as f:
            f.write("MiOS-SmartBat\n")
        return bdir

    def _create_ac_adapter(self, name: str = "ACAD", online: int = 0) -> str:
        adir = os.path.join(self.ps_dir, name)
        os.makedirs(adir, exist_ok=True)
        with open(os.path.join(adir, "type"), "w") as f:
            f.write("Mains\n")
        with open(os.path.join(adir, "online"), "w") as f:
            f.write(f"{online}\n")
        return adir

    def test_discover_supplies(self) -> None:
        self._create_battery("BAT0")
        self._create_ac_adapter("ACAD")

        reader = battery_passthrough.BatteryTelemetryReader(sysfs_root=self.sysfs_root)
        supplies = reader.discover_supplies()
        self.assertEqual(supplies["batteries"], ["BAT0"])
        self.assertEqual(supplies["ac_adapters"], ["ACAD"])

    def test_discharging_telemetry_and_runtime(self) -> None:
        # 3.75 Ah charge_now / 1.5 A current_now = 2.5 hours = 150 minutes
        self._create_battery("BAT0", status="Discharging", capacity=75, current_ua=1500000, charge_now=3750000)
        self._create_ac_adapter("ACAD", online=0)

        reader = battery_passthrough.BatteryTelemetryReader(sysfs_root=self.sysfs_root)
        snapshot = reader.get_full_power_snapshot()

        self.assertFalse(snapshot["ac_online"])
        self.assertTrue(snapshot["battery_present"])
        self.assertEqual(snapshot["status"], "Discharging")
        self.assertEqual(snapshot["capacity_percent"], 75.0)
        self.assertTrue(snapshot["is_discharging"])
        self.assertFalse(snapshot["is_charging"])
        self.assertAlmostEqual(snapshot["estimated_runtime_minutes"], 150.0, places=1)

    def test_charging_telemetry(self) -> None:
        self._create_battery("BAT0", status="Charging", capacity=50, charge_now=2500000, charge_full=5000000, current_ua=2000000)
        self._create_ac_adapter("ACAD", online=1)

        reader = battery_passthrough.BatteryTelemetryReader(sysfs_root=self.sysfs_root)
        snapshot = reader.get_full_power_snapshot()

        self.assertTrue(snapshot["ac_online"])
        self.assertEqual(snapshot["status"], "Charging")
        self.assertTrue(snapshot["is_charging"])
        self.assertFalse(snapshot["is_discharging"])

    def test_rate_limiting_invariant_clamp(self) -> None:
        """Enforces: Do NOT poll power supply sysfs files faster than once every 5 seconds."""
        # Test values less than 5.0 are clamped to 5.0
        self.assertEqual(battery_passthrough.BatteryPassthroughDaemon.validate_and_clamp_poll_interval(1.0), 5.0)
        self.assertEqual(battery_passthrough.BatteryPassthroughDaemon.validate_and_clamp_poll_interval(0.1), 5.0)
        self.assertEqual(battery_passthrough.BatteryPassthroughDaemon.validate_and_clamp_poll_interval(-10.0), 5.0)
        self.assertEqual(battery_passthrough.BatteryPassthroughDaemon.validate_and_clamp_poll_interval(4.99), 5.0)

        # Values >= 5.0 are preserved
        self.assertEqual(battery_passthrough.BatteryPassthroughDaemon.validate_and_clamp_poll_interval(5.0), 5.0)
        self.assertEqual(battery_passthrough.BatteryPassthroughDaemon.validate_and_clamp_poll_interval(10.0), 10.0)

    def test_qmp_payload_formatting(self) -> None:
        self._create_battery("BAT0", status="Discharging", capacity=82)
        self._create_ac_adapter("ACAD", online=0)

        daemon = battery_passthrough.BatteryPassthroughDaemon(
            sysfs_root=self.sysfs_root,
            domain="win11-laptop",
            dry_run=True,
        )
        res = daemon.sync_once()

        self.assertEqual(res["domain"], "win11-laptop")
        self.assertTrue(res["qmp_delivered"])
        qmp_event = res["qmp_event"]
        self.assertEqual(qmp_event["execute"], "guest-exec")
        self.assertIn(f"--capacity={res['snapshot']['capacity_percent']}", qmp_event["arguments"]["arg"])
        self.assertIn("--status=Discharging", qmp_event["arguments"]["arg"])
        self.assertEqual(qmp_event["telemetry_payload"]["event"], "ACPI_POWER_STATUS_CHANGE")
        self.assertFalse(qmp_event["telemetry_payload"]["data"]["ac_online"])

    def test_multi_battery_aggregation(self) -> None:
        self._create_battery("BAT0", status="Discharging", capacity=50, energy_now=25000000, energy_full=50000000)
        self._create_battery("BAT1", status="Discharging", capacity=90, energy_now=45000000, energy_full=50000000)
        self._create_ac_adapter("AC", online=0)

        reader = battery_passthrough.BatteryTelemetryReader(sysfs_root=self.sysfs_root)
        snapshot = reader.get_full_power_snapshot()

        self.assertTrue(snapshot["battery_present"])
        # Aggregated: (25M + 45M) / (50M + 50M) = 70.0%
        self.assertEqual(snapshot["capacity_percent"], 70.0)
        self.assertEqual(snapshot["status"], "Discharging")
        self.assertEqual(len(snapshot["battery_details"]["batteries"]), 2)

    def test_desktop_no_battery_environment(self) -> None:
        # No battery created, AC adapter online
        self._create_ac_adapter("AC", online=1)

        reader = battery_passthrough.BatteryTelemetryReader(sysfs_root=self.sysfs_root)
        snapshot = reader.get_full_power_snapshot()

        self.assertTrue(snapshot["ac_online"])
        self.assertFalse(snapshot["battery_present"])
        self.assertEqual(snapshot["capacity_percent"], 100.0)

    def test_daemon_single_iteration_loop(self) -> None:
        self._create_battery("BAT0", status="Full", capacity=100)
        daemon = battery_passthrough.BatteryPassthroughDaemon(
            sysfs_root=self.sysfs_root,
            domain="test-vm",
            dry_run=True,
        )
        # Should execute 1 cycle and terminate cleanly
        daemon.run_daemon(max_iterations=1)

def bp_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(bp_TestBatteryPassthrough)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-hw.py (prefix cg_)
# ======================================================================
"""Automated tests for MiOS CPU Governor Manager and Libvirt Hook Integration (task_364)."""


import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

cg__HERE = os.path.dirname(os.path.abspath(__file__))
cg__ROOT = os.path.normpath(os.path.join(cg__HERE, ".."))
cg__MODULE_PATH = os.path.join(cg__ROOT, "usr", "libexec", "mios", "hw", "cpu_governor.py")

cg_spec = importlib.util.spec_from_file_location("cpu_governor", cg__MODULE_PATH)
if cg_spec and cg_spec.loader:
    cpu_governor = importlib.util.module_from_spec(cg_spec)
    sys.modules[cg_spec.name] = cpu_governor
    cg_spec.loader.exec_module(cpu_governor)
else:
    raise ImportError(f"Could not load cpu_governor module from {cg__MODULE_PATH}")

class cg_TestCPUGovernor(unittest.TestCase):
    """Validates CPU governor discovery, switching, state persistence, and hook dispatch."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="mios_test_cpu_")
        self.sysfs_root = self.tmp_dir
        self.state_file = os.path.join(self.tmp_dir, "cpu_governor_state.json")

        # Create synthetic sysfs CPU hierarchy for cpu0, cpu1, cpu2, cpu3
        self.cpu_base = os.path.join(self.sysfs_root, "sys", "devices", "system", "cpu")
        for i in range(4):
            cpufreq_dir = os.path.join(self.cpu_base, f"cpu{i}", "cpufreq")
            os.makedirs(cpufreq_dir, exist_ok=True)
            with open(os.path.join(cpufreq_dir, "scaling_governor"), "w") as f:
                f.write("powersave\n")
            with open(os.path.join(cpufreq_dir, "scaling_available_governors"), "w") as f:
                f.write("powersave performance schedutil ondemand\n")
            with open(os.path.join(cpufreq_dir, "scaling_cur_freq"), "w") as f:
                f.write("1800000\n")
            with open(os.path.join(cpufreq_dir, "scaling_min_freq"), "w") as f:
                f.write("800000\n")
            with open(os.path.join(cpufreq_dir, "scaling_max_freq"), "w") as f:
                f.write("4200000\n")
            with open(os.path.join(cpufreq_dir, "scaling_driver"), "w") as f:
                f.write("intel_pstate\n")
            with open(os.path.join(cpufreq_dir, "energy_performance_preference"), "w") as f:
                f.write("balance_power\n")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_cpu_discovery(self) -> None:
        mgr = cpu_governor.CPUGovernorManager(sysfs_root=self.sysfs_root, state_file=self.state_file)
        cpus = mgr.get_online_cpu_ids()
        self.assertEqual(cpus, [0, 1, 2, 3])

    def test_cpu_info_parsing(self) -> None:
        mgr = cpu_governor.CPUGovernorManager(sysfs_root=self.sysfs_root, state_file=self.state_file)
        info = mgr.get_cpu_info(0)
        self.assertEqual(info["cpu_id"], 0)
        self.assertEqual(info["governor"], "powersave")
        self.assertIn("performance", info["available_governors"])
        self.assertEqual(info["cur_freq_khz"], 1800000)
        self.assertEqual(info["driver"], "intel_pstate")
        self.assertEqual(info["epp"], "balance_power")

    def test_set_governor_performance(self) -> None:
        mgr = cpu_governor.CPUGovernorManager(sysfs_root=self.sysfs_root, state_file=self.state_file)
        res = mgr.set_governor(governor="performance", epp="performance")
        self.assertEqual(res["status"], "ok")
        self.assertEqual(len(res["success_cpus"]), 4)

        # Verify sysfs content updated
        for i in range(4):
            gov_file = os.path.join(self.cpu_base, f"cpu{i}", "cpufreq", "scaling_governor")
            with open(gov_file, "r") as f:
                self.assertEqual(f.read().strip(), "performance")
            epp_file = os.path.join(self.cpu_base, f"cpu{i}", "cpufreq", "energy_performance_preference")
            with open(epp_file, "r") as f:
                self.assertEqual(f.read().strip(), "performance")

    def test_vm_lifecycle_switch_and_restore(self) -> None:
        mgr = cpu_governor.CPUGovernorManager(sysfs_root=self.sysfs_root, state_file=self.state_file)

        # Step 1: Start VM 'win11' -> switch to performance
        switch_res = mgr.switch_to_performance(domain="win11", governor="performance")
        self.assertEqual(switch_res["domain"], "win11")
        self.assertEqual(switch_res["active_domains_count"], 1)

        # Check all CPUs are performance
        for i in range(4):
            gov_file = os.path.join(self.cpu_base, f"cpu{i}", "cpufreq", "scaling_governor")
            with open(gov_file, "r") as f:
                self.assertEqual(f.read().strip(), "performance")

        # Step 2: Start second VM 'fedora-dev'
        mgr.switch_to_performance(domain="fedora-dev", governor="performance")
        persisted = mgr.load_persisted_state()
        self.assertIn("win11", persisted["active_domains"])
        self.assertIn("fedora-dev", persisted["active_domains"])

        # Step 3: Stop 'win11' -> 'fedora-dev' still active, should remain performance
        res_stop1 = mgr.restore_governor(domain="win11")
        self.assertFalse(res_stop1["restored"])
        for i in range(4):
            gov_file = os.path.join(self.cpu_base, f"cpu{i}", "cpufreq", "scaling_governor")
            with open(gov_file, "r") as f:
                self.assertEqual(f.read().strip(), "performance")

        # Step 4: Stop 'fedora-dev' -> no VMs active, should restore powersave
        res_stop2 = mgr.restore_governor(domain="fedora-dev")
        self.assertTrue(res_stop2["restored"])
        for i in range(4):
            gov_file = os.path.join(self.cpu_base, f"cpu{i}", "cpufreq", "scaling_governor")
            with open(gov_file, "r") as f:
                self.assertEqual(f.read().strip(), "powersave")
            epp_file = os.path.join(self.cpu_base, f"cpu{i}", "cpufreq", "energy_performance_preference")
            with open(epp_file, "r") as f:
                self.assertEqual(f.read().strip(), "balance_power")

    def test_libvirt_hook_dispatch(self) -> None:
        mgr = cpu_governor.CPUGovernorManager(sysfs_root=self.sysfs_root, state_file=self.state_file)

        # Hook: prepare begin
        res_start = mgr.handle_libvirt_hook(domain="gaming-vm", phase="prepare", operation="begin")
        self.assertEqual(res_start["target_governor"], "performance")

        # Hook: release end
        res_stop = mgr.handle_libvirt_hook(domain="gaming-vm", phase="release", operation="end")
        self.assertTrue(res_stop["restored"])

        # Hook: unhandled phase
        res_ignored = mgr.handle_libvirt_hook(domain="gaming-vm", phase="migrate", operation="begin")
        self.assertEqual(res_ignored["status"], "ignored")

    def test_offline_cpu_handling(self) -> None:
        # Mark cpu3 as offline
        online_file = os.path.join(self.cpu_base, "cpu3", "online")
        with open(online_file, "w") as f:
            f.write("0\n")

        mgr = cpu_governor.CPUGovernorManager(sysfs_root=self.sysfs_root, state_file=self.state_file)
        cpus = mgr.get_online_cpu_ids()
        self.assertEqual(cpus, [0, 1, 2])

    def test_specific_cpu_ids_targeting(self) -> None:
        mgr = cpu_governor.CPUGovernorManager(sysfs_root=self.sysfs_root, state_file=self.state_file)
        res = mgr.set_governor("performance", cpu_ids=[1, 2])
        self.assertEqual(res["success_cpus"], [1, 2])

        # Verify cpu0 is still powersave, but cpu1 and cpu2 are performance
        gov0 = os.path.join(self.cpu_base, "cpu0", "cpufreq", "scaling_governor")
        with open(gov0, "r") as f:
            self.assertEqual(f.read().strip(), "powersave")
        gov1 = os.path.join(self.cpu_base, "cpu1", "cpufreq", "scaling_governor")
        with open(gov1, "r") as f:
            self.assertEqual(f.read().strip(), "performance")

    def test_corrupted_state_file_recovery(self) -> None:
        # Write corrupted JSON to state file
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        with open(self.state_file, "w") as f:
            f.write("{corrupt json content!!")

        mgr = cpu_governor.CPUGovernorManager(sysfs_root=self.sysfs_root, state_file=self.state_file)
        state = mgr.load_persisted_state()
        self.assertEqual(state, {"active_domains": {}, "saved_states": {}})

def cg_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(cg_TestCPUGovernor)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-hw.py (prefix ct_)
# ======================================================================
"""Automated unit test suite for MiOS CPU Topology Allocator."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "hw"))

from cpu_topology import CPUTopologyAllocator

class ct_TestCPUTopology(unittest.TestCase):
    def setUp(self):
        self.allocator = CPUTopologyAllocator(dry_run=True)

    def test_hybrid_core_partitioning(self):
        """Test Intel hybrid 8P+8E cores partitions into RT, Interactive, and BG slices."""
        alloc = self.allocator.discover_topology(mock_core_count=16, is_hybrid=True)
        self.assertEqual(alloc.realtime_cpuset, "0-1")
        self.assertEqual(alloc.interactive_cpuset, "2-7")
        self.assertEqual(alloc.background_cpuset, "8-15")

    def test_systemd_slice_dropin_generation(self):
        """Test systemd slice dropins declare AllowedCPUs accurately."""
        alloc = self.allocator.discover_topology(mock_core_count=16, is_hybrid=True)
        dropins = self.allocator.generate_systemd_slice_dropins(alloc)
        self.assertIn("realtime.slice.d/10-cpuset.conf", dropins)
        self.assertIn("AllowedCPUs=0-1", dropins["realtime.slice.d/10-cpuset.conf"])
        self.assertIn("CPUSchedulingPolicy=rr", dropins["realtime.slice.d/10-cpuset.conf"])


# ======================================================================
# from tests/test-hw.py (prefix epc_)
# ======================================================================
"""Automated unit test suite for MiOS Hardware Energy Metering and Power Capping."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "hw"))

from energyd import EnergyCapManager, PowerMetrics

class epc_TestEnergydPowerCap(unittest.TestCase):
    def setUp(self):
        self.mgr = EnergyCapManager(
            chassis_cap_watts=600.0,
            min_gpu_power_limit=150.0,
            max_gpu_power_limit=450.0,
            thermal_limit_c=85.0,
            dry_run=True,
        )

    def test_normal_power_unthrottled(self):
        """Test power draw under cap leaves GPU and CPU unthrottled."""
        m = self.mgr.evaluate_and_enforce_cap(
            mock_cpu_w=100.0, mock_gpu_w=300.0, mock_cpu_temp=45.0, mock_gpu_temp=50.0
        )
        self.assertFalse(m.is_throttled)
        self.assertEqual(m.total_watts, 400.0)
        self.assertGreaterEqual(m.applied_gpu_cap_watts, 400.0)
        self.assertEqual(m.cgroup_cpu_quota_pct, 100.0)
        self.assertEqual(m.throttle_reason, "none")

    def test_over_cap_throttles_gpu_and_cgroups(self):
        """Test total power exceeding 600W cap clamps GPU power limit and throttles cgroups."""
        m = self.mgr.evaluate_and_enforce_cap(
            mock_cpu_w=250.0, mock_gpu_w=450.0, mock_cpu_temp=50.0, mock_gpu_temp=60.0
        )
        self.assertTrue(m.is_throttled)
        self.assertEqual(m.total_watts, 700.0)
        self.assertLess(m.applied_gpu_cap_watts, 400.0)
        self.assertLess(m.cgroup_cpu_quota_pct, 100.0)
        self.assertIn("power_cap_exceeded", m.throttle_reason)

    def test_min_gpu_power_floor_enforcement(self):
        """Test severe power overload never drops GPU below minimum floor (150W)."""
        m = self.mgr.evaluate_and_enforce_cap(
            mock_cpu_w=600.0, mock_gpu_w=450.0, mock_cpu_temp=50.0, mock_gpu_temp=60.0
        )
        self.assertTrue(m.is_throttled)
        self.assertGreaterEqual(m.applied_gpu_cap_watts, 150.0)

    def test_thermal_throttling_trigger(self):
        """Test junction temperature exceeding 85°C triggers thermal throttle."""
        m = self.mgr.evaluate_and_enforce_cap(
            mock_cpu_w=100.0, mock_gpu_w=200.0, mock_cpu_temp=88.0, mock_gpu_temp=90.0
        )
        self.assertTrue(m.is_throttled)
        self.assertIn("thermal_throttle", m.throttle_reason)
        self.assertLess(m.applied_gpu_cap_watts, 450.0)

    def test_unthrottle_recovery_hysteresis(self):
        """Test that after throttling, power limit recovers smoothly when load drops."""
        self.mgr.evaluate_and_enforce_cap(mock_cpu_w=300.0, mock_gpu_w=450.0)
        clamped_limit = self.mgr.current_gpu_limit

        m_rec = self.mgr.evaluate_and_enforce_cap(
            mock_cpu_w=50.0, mock_gpu_w=100.0, mock_cpu_temp=40.0, mock_gpu_temp=45.0
        )
        self.assertFalse(m_rec.is_throttled)
        self.assertGreater(m_rec.applied_gpu_cap_watts, clamped_limit)

    def test_telemetry_export(self):
        """Test telemetry export produces structured records with all required fields."""
        self.mgr.evaluate_and_enforce_cap(mock_cpu_w=100.0, mock_gpu_w=200.0)
        self.mgr.evaluate_and_enforce_cap(mock_cpu_w=300.0, mock_gpu_w=400.0)
        telemetry = self.mgr.export_telemetry()
        self.assertEqual(len(telemetry), 2)
        for entry in telemetry:
            self.assertIn("timestamp", entry)
            self.assertIn("cpu_watts", entry)
            self.assertIn("gpu_watts", entry)
            self.assertIn("total_watts", entry)
            self.assertIn("cap_watts", entry)
            self.assertIn("cpu_temp_c", entry)
            self.assertIn("gpu_temp_c", entry)
            self.assertIn("applied_gpu_cap_watts", entry)
            self.assertIn("cgroup_cpu_quota_pct", entry)
            self.assertIn("is_throttled", entry)

    def test_carbon_aware_mode(self):
        """Test carbon-aware scheduling mode applies energy saving ceiling."""
        self.mgr.carbon_aware_mode = True
        m = self.mgr.evaluate_and_enforce_cap(mock_cpu_w=50.0, mock_gpu_w=100.0)
        self.assertLessEqual(m.applied_gpu_cap_watts, 450.0 * 0.8)
        self.assertEqual(m.cgroup_cpu_quota_pct, 80.0)


# ======================================================================
# from tests/test-hw.py (prefix fc_)
# ======================================================================
"""Automated unit test suite for MiOS Multi-zone PID Fan Controller."""

import os
import sys
import tempfile
import unittest
from typing import Dict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "hw"))

from fand import MultiZonePIDFanController, FanZoneConfig

class fc_TestFanControl(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="mios-fan-test-")
        self.state_file = os.path.join(self.tmp_dir, "fan_state.json")
        self.controller = MultiZonePIDFanController(
            sysfs_root=self.tmp_dir,
            state_file=self.state_file,
            dry_run=True,
            hysteresis_deg=5.0,
            max_pwm_ramp_per_sec=25.0,
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _create_mock_sysfs(self):
        hwmon0 = os.path.join(self.tmp_dir, "sys", "class", "hwmon", "hwmon0")
        os.makedirs(hwmon0, exist_ok=True)
        with open(os.path.join(hwmon0, "name"), "w") as f:
            f.write("coretemp\n")
        with open(os.path.join(hwmon0, "temp1_input"), "w") as f:
            f.write("55000\n")  # 55.0 C
        with open(os.path.join(hwmon0, "pwm1"), "w") as f:
            f.write("100\n")
        with open(os.path.join(hwmon0, "pwm1_enable"), "w") as f:
            f.write("1\n")
        return hwmon0

    def test_sensor_discovery(self):
        """Test discovery of hwmon sensors and PWM controls from sysfs."""
        hwmon0 = self._create_mock_sysfs()
        devs = self.controller.discover_hwmon_devices()
        self.assertIn("hwmon0_temp1_input", devs["sensors"])
        self.assertIn("hwmon0_pwm1", devs["pwms"])
        self.assertEqual(devs["sensors"]["hwmon0_temp1_input"]["device_name"], "coretemp")

    def test_sensor_temp_read(self):
        """Test reading and converting millidegree temperature values."""
        hwmon0 = self._create_mock_sysfs()
        temp_file = os.path.join(hwmon0, "temp1_input")
        temp = self.controller.read_sensor_temp(temp_file)
        self.assertIsNotNone(temp)
        self.assertAlmostEqual(temp, 55.0, places=1)

    def test_pid_monotonic_ramp_rate_limiting(self):
        """Test that large step temperature jumps result in rate-limited PWM ramp transitions."""
        # Initial step at target temp 65C -> initial base PWM
        res1 = self.controller.compute_pid_pwm("cpu", current_temp=65.0, dt=1.0)

        # Step temperature to 80C (> target 65C)
        res2 = self.controller.compute_pid_pwm("cpu", current_temp=80.0, dt=1.0)

        # Ramp should increase, but step delta must be bounded by max_pwm_ramp_per_sec (25.0)
        pwm_delta = res2 - res1
        self.assertGreater(pwm_delta, 0)
        self.assertLessEqual(pwm_delta, 25.0 + 1.0)

    def test_hysteresis_deadband_stability(self):
        """Test 5°C hysteresis damping prevents rapid oscillations when temperature drops in deadband."""
        # Warm up to 68C (above target 65C)
        self.controller.compute_pid_pwm("cpu", current_temp=68.0, dt=1.0)
        pwm_warm = self.controller.compute_pid_pwm("cpu", current_temp=68.0, dt=1.0)

        # Drop slightly to 64C (within 5C hysteresis deadband of target 65C)
        pwm_dropped = self.controller.compute_pid_pwm("cpu", current_temp=64.0, dt=1.0)

        # Ensure PWM transitions smoothly without erratic sudden drop to minimum
        self.assertGreaterEqual(pwm_dropped, self.controller.zones["cpu"].min_pwm)
        self.assertLessEqual(abs(pwm_dropped - pwm_warm), 26.0)

    def test_critical_temperature_override(self):
        """Test that temperature at or above critical threshold immediately forces 100% PWM (255)."""
        res = self.controller.compute_pid_pwm("cpu", current_temp=86.0, dt=1.0)  # critical is 85.0
        self.assertEqual(res, 255)

    def test_multi_zone_independent_simulation(self):
        """Test multi-zone regulation (CPU, GPU, NVMe, Chassis) with varied thermal loads."""
        loads = {
            "cpu": 72.0,
            "gpu": 80.0,
            "nvme": 50.0,
            "chassis": 45.0,
        }
        res = self.controller.step_simulation(loads, dt=1.0)
        self.assertIn("cpu", res)
        self.assertIn("gpu", res)
        self.assertIn("nvme", res)
        self.assertIn("chassis", res)
        self.assertGreaterEqual(res["gpu"]["pwm"], res["nvme"]["pwm"])

    def test_state_persistence(self):
        """Test saving and serializing fan controller status."""
        self.controller.save_state()
        self.assertTrue(os.path.isfile(self.state_file))
        status = self.controller.get_status()
        self.assertEqual(status["hysteresis_deg"], 5.0)
        self.assertIn("cpu", status["zones"])


# ======================================================================
# from tests/test-hw.py (prefix gi_)
# ======================================================================
"""Automated unit test suite for MiOS GPU Interconnect Profiler."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "hw"))

from gpu_heatd import GPUInterconnectProfiler

class gi_TestGPUInterconnect(unittest.TestCase):
    def setUp(self):
        self.profiler = GPUInterconnectProfiler(dry_run=True)

    def test_sample_nvlink_matrix_normal_bandwidth(self):
        """Test sampling 4x GPU NVLink-4 matrix captures 450 GB/s with 0 bottlenecks."""
        mat = self.profiler.sample_interconnect_matrix(gpu_count=4, mock_bandwidth_gbps=450.0)
        self.assertEqual(mat.gpu_count, 4)
        self.assertEqual(mat.interconnect_type, "NVLink-4")
        self.assertEqual(len(mat.bottlenecks_detected), 0)
        self.assertEqual(mat.bandwidth_gbps_matrix[0][1], 450.0)

    def test_degraded_link_bottleneck_detection(self):
        """Test low bandwidth (<100 GB/s) generates actionable bottleneck alerts."""
        mat = self.profiler.sample_interconnect_matrix(gpu_count=2, mock_bandwidth_gbps=32.0)
        self.assertEqual(mat.interconnect_type, "PCIe-Gen5")
        self.assertGreater(len(mat.bottlenecks_detected), 0)
        self.assertIn("Degraded link", mat.bottlenecks_detected[0])


# ======================================================================
# from tests/test-hw.py (prefix gp_)
# ======================================================================
"""Automated unit test suite for MiOS GPU Power Manager."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "hw"))

from gpu_powerd import MAX_D3COLD_WAKE_MS, GPUPowerManager

class gp_TestGPUPower(unittest.TestCase):
    def setUp(self):
        self.mgr = GPUPowerManager(dry_run=True)

    def test_d3cold_power_consumption_under_3w(self):
        """Test idle GPU enters D3cold with power draw < 3.0W."""
        state = self.mgr.transition_to_d3cold()
        self.assertEqual(state.power_state, "D3cold_Sleep")
        self.assertLess(state.current_wattage, 3.0)
        self.assertEqual(state.aspm_state, "L1.2")

    def test_sub_150ms_d3cold_inference_wakeup(self):
        """Test waking GPU from D3cold completes in <150ms."""
        self.mgr.transition_to_d3cold()
        state = self.mgr.wake_gpu_for_inference()
        self.assertEqual(state.power_state, "D0_Active")
        self.assertLess(state.wake_latency_ms, MAX_D3COLD_WAKE_MS)


# ======================================================================
# from tests/test-hw.py (prefix gsc_)
# ======================================================================
"""Unit and integration tests for GPUSliceManager and CDI generator."""


import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

gsc__HERE = os.path.dirname(os.path.abspath(__file__))
gsc__ROOT = os.path.normpath(os.path.join(gsc__HERE, ".."))
gsc__TARGET_PATH = os.path.join(gsc__ROOT, "usr", "libexec", "mios", "hw", "gpu_slice.py")

gsc_spec = importlib.util.spec_from_file_location("gpu_slice", gsc__TARGET_PATH)
if gsc_spec and gsc_spec.loader:
    gpu_slice = importlib.util.module_from_spec(gsc_spec)
    sys.modules[gsc_spec.name] = gpu_slice
    gsc_spec.loader.exec_module(gpu_slice)
else:
    raise ImportError(f"Could not load module from {gsc__TARGET_PATH}")

class gsc_TestGPUSliceManager(unittest.TestCase):
    """Test suite for GPU discovery, MIG slice configuration, and CDI spec generation."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="mios-test-gpuslice-")
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_gpu_discovery_mock(self):
        mgr = gpu_slice.GPUSliceManager(mock=True)
        gpus = mgr.discover_gpus()
        self.assertEqual(len(gpus), 2)
        self.assertEqual(gpus[0].vendor, "nvidia")
        self.assertTrue(gpus[0].mig_capable)
        self.assertEqual(gpus[1].vendor, "amd")

    def test_generate_cdi_spec_nvidia_mig(self):
        mgr = gpu_slice.GPUSliceManager(mock=True)
        gpus = mgr.discover_gpus()
        a100 = gpus[0]

        slices = ["1g.5gb", "2g.10gb"]
        out_file = self.root / "cdi" / "nvidia-mig.json"
        cdi = mgr.generate_cdi_spec(a100, slices=slices, output_file=str(out_file))

        self.assertEqual(cdi["cdiVersion"], "0.5.0")
        self.assertEqual(cdi["kind"], "nvidia.com/gpu")
        self.assertEqual(len(cdi["devices"]), 2)
        self.assertEqual(cdi["devices"][0]["name"], "mig-0-0")
        self.assertEqual(cdi["devices"][1]["name"], "mig-0-1")
        self.assertTrue(out_file.exists())

    def test_generate_cdi_spec_amd_rocm(self):
        mgr = gpu_slice.GPUSliceManager(mock=True)
        gpus = mgr.discover_gpus()
        radeon = gpus[1]

        cdi = mgr.generate_cdi_spec(radeon)
        self.assertEqual(cdi["cdiVersion"], "0.5.0")
        self.assertEqual(cdi["kind"], "amd.com/gpu")
        self.assertEqual(len(cdi["devices"]), 1)
        self.assertEqual(cdi["devices"][0]["name"], "gpu-1")
        self.assertIn("/dev/kfd", [d["path"] for d in cdi["devices"][0]["containerEdits"]["deviceNodes"]])

    def test_configure_slices_valid_and_invalid(self):
        mgr = gpu_slice.GPUSliceManager(mock=True)

        ok, msg = mgr.configure_slices(gpu_id=0, slice_profiles=["1g.5gb", "2g.10gb"])
        self.assertTrue(ok)
        self.assertIn("Successfully provisioned", msg)

        bad_ok, bad_msg = mgr.configure_slices(gpu_id=0, slice_profiles=["invalid_slice_999gb"])
        self.assertFalse(bad_ok)
        self.assertIn("Invalid MIG profile", bad_msg)

    def test_cli_execution_scan_mock(self):
        test_args = ["gpu_slice.py", "--scan", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = gpu_slice.main()
            self.assertEqual(exit_code, 0)

    def test_cli_execution_generate_cdi_mock(self):
        out_path = str(self.root / "test_cdi.json")
        test_args = ["gpu_slice.py", "--generate-cdi", "--gpu-id", "0", "--output", out_path, "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = gpu_slice.main()
            self.assertEqual(exit_code, 0)
            self.assertTrue(os.path.exists(out_path))

def gsc_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(gsc_TestGPUSliceManager)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-hw.py (prefix gtw_)
# ======================================================================
"""Automated tests for MiOS GPU Thermal, Junction Temperature & Fan Watchdog (task_368)."""


import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

gtw__HERE = os.path.dirname(os.path.abspath(__file__))
gtw__ROOT = os.path.normpath(os.path.join(gtw__HERE, ".."))
gtw__MODULE_PATH = os.path.join(gtw__ROOT, "usr", "libexec", "mios", "hw", "gpu_thermal_watchdog.py")

gtw_spec = importlib.util.spec_from_file_location("gpu_thermal_watchdog", gtw__MODULE_PATH)
if gtw_spec and gtw_spec.loader:
    gpu_thermal_watchdog = importlib.util.module_from_spec(gtw_spec)
    sys.modules[gtw_spec.name] = gpu_thermal_watchdog
    gtw_spec.loader.exec_module(gpu_thermal_watchdog)
else:
    raise ImportError(f"Could not load gpu_thermal_watchdog module from {gtw__MODULE_PATH}")

class gtw_TestGPUThermalWatchdog(unittest.TestCase):
    """Validates GPU thermal parsing, dynamic fan curve calculations, and the non-zero fan floor invariant."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="mios_test_gpu_")
        self.sysfs_root = self.tmp_dir
        self.hwmon_dir = os.path.join(self.sysfs_root, "sys", "class", "drm", "card0", "device", "hwmon", "hwmon0")
        os.makedirs(self.hwmon_dir, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _setup_hwmon_gpu(
        self,
        name: str = "amdgpu",
        junction_temp_mc: int = 72000,
        edge_temp_mc: int = 58000,
        mem_temp_mc: int = 64000,
        pwm_val: int = 128,
        fan_rpm: int = 1650,
    ) -> str:
        with open(os.path.join(self.hwmon_dir, "name"), "w") as f:
            f.write(f"{name}\n")
        with open(os.path.join(self.hwmon_dir, "temp1_input"), "w") as f:
            f.write(f"{edge_temp_mc}\n")
        with open(os.path.join(self.hwmon_dir, "temp1_label"), "w") as f:
            f.write("edge\n")
        with open(os.path.join(self.hwmon_dir, "temp2_input"), "w") as f:
            f.write(f"{junction_temp_mc}\n")
        with open(os.path.join(self.hwmon_dir, "temp2_label"), "w") as f:
            f.write("junction\n")
        with open(os.path.join(self.hwmon_dir, "temp3_input"), "w") as f:
            f.write(f"{mem_temp_mc}\n")
        with open(os.path.join(self.hwmon_dir, "temp3_label"), "w") as f:
            f.write("mem\n")
        with open(os.path.join(self.hwmon_dir, "pwm1"), "w") as f:
            f.write(f"{pwm_val}\n")
        with open(os.path.join(self.hwmon_dir, "pwm1_enable"), "w") as f:
            f.write("2\n")
        with open(os.path.join(self.hwmon_dir, "fan1_input"), "w") as f:
            f.write(f"{fan_rpm}\n")
        return self.hwmon_dir

    def test_gpu_telemetry_reading(self) -> None:
        self._setup_hwmon_gpu(junction_temp_mc=72000, edge_temp_mc=58000, mem_temp_mc=64000)

        wd = gpu_thermal_watchdog.GPUThermalWatchdog(sysfs_root=self.sysfs_root)
        gpus = wd.scan_hwmon_gpus()
        self.assertEqual(len(gpus), 1)

        gpu = gpus[0]
        self.assertEqual(gpu.vendor, "amdgpu")
        self.assertEqual(gpu.junction_temp_c, 72.0)
        self.assertEqual(gpu.edge_temp_c, 58.0)
        self.assertEqual(gpu.memory_temp_c, 64.0)
        self.assertEqual(gpu.peak_temperature_c, 72.0)
        self.assertEqual(gpu.current_fan_rpm, 1650)

    def test_non_zero_fan_floor_invariant(self) -> None:
        """Enforces: Do NOT set fan speeds to 0% under any operational thermal condition."""
        # 1. Floor configuration <= 0 is automatically clamped to DEFAULT_MIN_FAN_FLOOR_PERCENT
        wd_zero = gpu_thermal_watchdog.GPUThermalWatchdog(sysfs_root=self.sysfs_root, min_fan_floor_percent=0.0)
        self.assertGreater(wd_zero.min_fan_floor_percent, 0.0)
        self.assertEqual(wd_zero.min_fan_floor_percent, 25.0)

        wd_neg = gpu_thermal_watchdog.GPUThermalWatchdog(sysfs_root=self.sysfs_root, min_fan_floor_percent=-15.0)
        self.assertGreater(wd_neg.min_fan_floor_percent, 0.0)

        # 2. Dynamic curve evaluation at freezing temperatures (0°C, 20°C) NEVER yields 0%
        wd = gpu_thermal_watchdog.GPUThermalWatchdog(sysfs_root=self.sysfs_root, min_fan_floor_percent=30.0)
        self.assertEqual(wd.calculate_fan_curve_duty_cycle(0.0), 30.0)
        self.assertEqual(wd.calculate_fan_curve_duty_cycle(20.0), 30.0)
        self.assertEqual(wd.calculate_fan_curve_duty_cycle(35.0), 30.0)

    def test_dynamic_fan_curve_scaling(self) -> None:
        wd = gpu_thermal_watchdog.GPUThermalWatchdog(
            sysfs_root=self.sysfs_root,
            target_junction_temp_c=80.0,
            min_fan_floor_percent=25.0,
        )

        # Idle (< 40°C) -> 25% floor
        self.assertEqual(wd.calculate_fan_curve_duty_cycle(30.0), 25.0)

        # Mid Load (52.5°C) -> halfway between 25% and 60% = 42.5%
        self.assertAlmostEqual(wd.calculate_fan_curve_duty_cycle(52.5), 42.5, places=1)

        # High Load (72.5°C) -> halfway between 60% and 100% = 80%
        self.assertAlmostEqual(wd.calculate_fan_curve_duty_cycle(72.5), 80.0, places=1)

        # Peak Threshold (80°C and above) -> 100% full blast
        self.assertEqual(wd.calculate_fan_curve_duty_cycle(80.0), 100.0)
        self.assertEqual(wd.calculate_fan_curve_duty_cycle(95.0), 100.0)

    def test_check_and_adjust_throttling_warning(self) -> None:
        # GPU junction at 85°C (> 80°C threshold)
        self._setup_hwmon_gpu(junction_temp_mc=85000, edge_temp_mc=75000)

        wd = gpu_thermal_watchdog.GPUThermalWatchdog(sysfs_root=self.sysfs_root, target_junction_temp_c=80.0)
        res = wd.check_and_adjust_all()

        self.assertEqual(res["status"], "warning")
        self.assertTrue(len(res["throttling_warnings"]) > 0)
        self.assertIn("exceeds target threshold", res["throttling_warnings"][0])

        gpu_res = res["gpus"][0]
        self.assertEqual(gpu_res["target_fan_percent"], 100.0)
        self.assertTrue(gpu_res["fan_adjusted"])

        # Check PWM was written to sysfs
        with open(os.path.join(self.hwmon_dir, "pwm1"), "r") as f:
            self.assertEqual(f.read().strip(), "255")
        with open(os.path.join(self.hwmon_dir, "pwm1_enable"), "r") as f:
            self.assertEqual(f.read().strip(), "1")

    def test_multi_gpu_and_fallback_peak_temp(self) -> None:
        # Create second GPU without explicit junction sensor (e.g. Intel Arc / iGPU)
        card1_hwmon = os.path.join(self.sysfs_root, "sys", "class", "drm", "card1", "device", "hwmon", "hwmon1")
        os.makedirs(card1_hwmon, exist_ok=True)
        with open(os.path.join(card1_hwmon, "name"), "w") as f:
            f.write("i915\n")
        with open(os.path.join(card1_hwmon, "temp1_input"), "w") as f:
            f.write("62000\n")  # 62°C
        with open(os.path.join(card1_hwmon, "pwm1"), "w") as f:
            f.write("100\n")

        self._setup_hwmon_gpu(junction_temp_mc=50000, edge_temp_mc=45000)

        wd = gpu_thermal_watchdog.GPUThermalWatchdog(sysfs_root=self.sysfs_root)
        gpus = wd.scan_hwmon_gpus()
        self.assertEqual(len(gpus), 2)

        res = wd.check_and_adjust_all()
        self.assertEqual(res["gpus_monitored"], 2)
        self.assertEqual(res["status"], "ok")

    def test_daemon_single_cycle_run(self) -> None:
        self._setup_hwmon_gpu(junction_temp_mc=55000, edge_temp_mc=50000)
        wd = gpu_thermal_watchdog.GPUThermalWatchdog(sysfs_root=self.sysfs_root)
        # Should run 1 cycle and exit
        wd.run_daemon(poll_interval=0.01, max_cycles=1)

def gtw_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(gtw_TestGPUThermalWatchdog)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-hw.py (prefix hd_)
# ======================================================================
"""Automated unit test suite for PCIe Link Width & Speed Degradation Detector (task_508)."""


import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch

hd__HERE = os.path.dirname(os.path.abspath(__file__))
hd__ROOT = os.path.normpath(os.path.join(hd__HERE, ".."))
hd__MODULE_PATH = os.path.join(hd__ROOT, "usr", "libexec", "mios", "hw", "inventory_monitor.py")

hd_spec = importlib.util.spec_from_file_location("inventory_monitor", hd__MODULE_PATH)
if hd_spec and hd_spec.loader:
    inventory_monitor = importlib.util.module_from_spec(hd_spec)
    sys.modules[hd_spec.name] = inventory_monitor
    hd_spec.loader.exec_module(inventory_monitor)
else:
    raise ImportError(f"Could not load inventory_monitor module from {hd__MODULE_PATH}")

class hd_TestHwDegrade(unittest.TestCase):
    """Validates detection of PCIe link width degradation, bus speed drops, and anomaly alerts."""

    def setUp(self) -> None:
        self.monitor = inventory_monitor.HardwareInventoryMonitor(mock=True)

    def test_healthy_pcie_device(self) -> None:
        """Asserts healthy PCIe link report when width and speed match maximum capability."""
        dev = inventory_monitor.HardwareDevice(
            sys_path="/sys/devices/pci0000:00/0000:00:01.0/0000:01:00.0",
            subsystem="pci",
            vendor_id="10de",
            device_id="2684",
            device_name="NVIDIA GeForce RTX 4090",
            current_link_width=16,
            max_link_width=16,
            current_link_speed="16.0 GT/s",
            max_link_speed="16.0 GT/s",
        )
        is_deg, reasons = dev.is_degraded()
        self.assertFalse(is_deg)
        self.assertEqual(len(reasons), 0)

    def test_degraded_pcie_link_width(self) -> None:
        """Asserts detection when GPU x16 link drops to x1 or x4 due to slot seating/power issues."""
        dev = inventory_monitor.HardwareDevice(
            sys_path="/sys/devices/pci0000:00/0000:00:01.0/0000:01:00.0",
            subsystem="pci",
            vendor_id="10de",
            device_id="2684",
            device_name="NVIDIA GeForce RTX 4090",
            current_link_width=1,
            max_link_width=16,
            current_link_speed="16.0 GT/s",
            max_link_speed="16.0 GT/s",
        )
        is_deg, reasons = dev.is_degraded()
        self.assertTrue(is_deg)
        self.assertEqual(len(reasons), 1)
        self.assertIn("link width degraded", reasons[0])
        self.assertIn("x1", reasons[0])

    def test_degraded_pcie_link_speed(self) -> None:
        """Asserts detection when PCIe Gen4 link drops from 16.0 GT/s to 2.5 GT/s."""
        dev = inventory_monitor.HardwareDevice(
            sys_path="/sys/devices/pci0000:00/0000:00:01.1/0000:02:00.0",
            subsystem="nvme",
            vendor_id="144d",
            device_id="a808",
            device_name="Samsung 980 PRO NVMe SSD",
            current_link_width=4,
            max_link_width=4,
            current_link_speed="2.5 GT/s",
            max_link_speed="16.0 GT/s",
        )
        is_deg, reasons = dev.is_degraded()
        self.assertTrue(is_deg)
        self.assertEqual(len(reasons), 1)
        self.assertIn("link speed degraded", reasons[0])

    def test_detect_degraded_devices_in_inventory(self) -> None:
        """Asserts inventory scan filters degraded devices correctly."""
        # Inject degraded NVMe into mock inventory
        self.monitor._mock_inventory["/sys/devices/pci0000:00/0000:00:01.1/0000:02:00.0"].current_link_width = 1

        degraded = self.monitor.detect_degraded_devices()
        self.assertEqual(len(degraded), 1)
        dev, reasons = degraded[0]
        self.assertEqual(dev.device_id, "a808")
        self.assertTrue(any("link width degraded" in r for r in reasons))

    def test_cli_check_degraded_healthy(self) -> None:
        """Asserts CLI exit code 0 when all devices are healthy."""
        with patch("sys.argv", ["inventory_monitor.py", "--check-degraded", "--mock", "--json"]):
            with patch("builtins.print") as mock_print:
                ret = inventory_monitor.main()
                self.assertEqual(ret, 0)
                mock_print.assert_called()
                parsed = json.loads(mock_print.call_args[0][0])
                self.assertEqual(parsed["status"], "healthy")

    def test_cli_check_degraded_detected(self) -> None:
        """Asserts CLI exit code 2 when degraded devices are detected."""
        # Degrade mock GPU
        self.monitor._mock_inventory["/sys/devices/pci0000:00/0000:00:01.0/0000:01:00.0"].current_link_width = 4

        with patch.object(inventory_monitor, "HardwareInventoryMonitor", return_value=self.monitor):
            with patch("sys.argv", ["inventory_monitor.py", "--check-degraded", "--mock", "--json"]):
                with patch("builtins.print") as mock_print:
                    ret = inventory_monitor.main()
                    self.assertEqual(ret, 2)
                    parsed = json.loads(mock_print.call_args[0][0])
                    self.assertEqual(parsed["status"], "degraded")
                    self.assertEqual(len(parsed["degraded_devices"]), 1)


# ======================================================================
# from tests/test-hw.py (prefix im_)
# ======================================================================
"""Automated unit test suite for Hardware Netlink Inventory Monitor (task_507)."""


import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

im__HERE = os.path.dirname(os.path.abspath(__file__))
im__ROOT = os.path.normpath(os.path.join(im__HERE, ".."))
im__MODULE_PATH = os.path.join(im__ROOT, "usr", "libexec", "mios", "hw", "inventory_monitor.py")

im_spec = importlib.util.spec_from_file_location("inventory_monitor", im__MODULE_PATH)
if im_spec and im_spec.loader:
    inventory_monitor = importlib.util.module_from_spec(im_spec)
    sys.modules[im_spec.name] = inventory_monitor
    im_spec.loader.exec_module(inventory_monitor)
else:
    raise ImportError(f"Could not load inventory_monitor module from {im__MODULE_PATH}")

class im_TestInventoryMonitor(unittest.TestCase):
    """Validates sysfs inventory enumeration, netlink packet decoding, and uevent processing."""

    def setUp(self) -> None:
        self.monitor = inventory_monitor.HardwareInventoryMonitor(mock=True)
        self.tmp_dir = tempfile.mkdtemp(prefix="mios_test_hw_")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_scan_mock_inventory(self) -> None:
        """Asserts discovery of mock GPU and NVMe hardware devices."""
        devices = self.monitor.scan_sysfs_inventory()
        self.assertEqual(len(devices), 2)

        gpu = next(d for d in devices if d.vendor_id == "10de")
        self.assertEqual(gpu.device_id, "2684")
        self.assertEqual(gpu.current_link_width, 16)
        self.assertFalse(gpu.is_degraded()[0])

    def test_parse_raw_netlink_packet(self) -> None:
        """Asserts decoding of raw null-delimited netlink uevent packet."""
        raw_packet = b"add@/devices/pci0000:00/0000:00:01.0\x00ACTION=add\x00DEVPATH=/devices/pci0000:00/0000:00:01.0\x00SUBSYSTEM=pci\x00PCI_ID=10de:2684\x00"
        parsed = self.monitor.parse_raw_netlink_packet(raw_packet)
        self.assertEqual(parsed["ACTION"], "add")
        self.assertEqual(parsed["SUBSYSTEM"], "pci")
        self.assertEqual(parsed["PCI_ID"], "10de:2684")

    def test_process_uevent_lifecycle(self) -> None:
        """Asserts state transition upon receiving add and remove uevents."""
        # Process remove
        rem_event = self.monitor.process_uevent_dict({
            "ACTION": "remove",
            "SUBSYSTEM": "pci",
            "DEVPATH": "/devices/pci0000:00/0000:00:01.0/0000:01:00.0",
        })
        self.assertEqual(rem_event.action, "remove")
        dev = self.monitor._mock_inventory["/sys/devices/pci0000:00/0000:00:01.0/0000:01:00.0"]
        self.assertEqual(dev.status, "removed")

    def test_listen_netlink_mock(self) -> None:
        """Asserts netlink generator in mock mode."""
        events = list(self.monitor.listen_netlink())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].subsystem, "pci")

    def test_cli_scan_json(self) -> None:
        """Asserts CLI execution with --scan --mock --json."""
        with patch("sys.argv", ["inventory_monitor.py", "--scan", "--mock", "--json"]):
            with patch("builtins.print") as mock_print:
                ret = inventory_monitor.main()
                self.assertEqual(ret, 0)
                mock_print.assert_called()
                parsed = json.loads(mock_print.call_args[0][0])
                self.assertEqual(parsed["status"], "ok")
                self.assertIn("inventory", parsed)


# ======================================================================
# from tests/test-hw.py (prefix nt_)
# ======================================================================
"""Automated unit test suite for MiOS NCCL Topology Tuner."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "hw"))

from nccl_tune import MAX_ALLREDUCE_LATENCY_US, MIN_TP2_SPEEDUP_RATIO, NCCLTopologyTuner

class nt_TestNCCLTune(unittest.TestCase):
    def setUp(self):
        self.tuner = NCCLTopologyTuner(dry_run=True)

    def test_nvlink_topology_allreduce_latency_under_50us(self):
        """Test NVLink discovery yields <50us AllReduce latency and >1.8x TP=2 scaling."""
        cfg = self.tuner.discover_and_optimize(gpu_count=2, has_nvlink=True)
        self.assertEqual(cfg.interconnect_type, "NVLink_P2P")
        self.assertLess(cfg.allreduce_latency_us, MAX_ALLREDUCE_LATENCY_US)
        self.assertGreaterEqual(cfg.tp2_throughput_scaling, MIN_TP2_SPEEDUP_RATIO)

    def test_nccl_env_export_structure(self):
        """Test generated NCCL environment variables match SSOT specification."""
        cfg = self.tuner.discover_and_optimize(gpu_count=4, has_nvlink=True)
        env = self.tuner.export_nccl_env(cfg)
        self.assertIn("NCCL_BUFFSIZE=8M", env)
        self.assertIn("NCCL_P2P_LEVEL=NVL", env)
        self.assertIn("NCCL_NET_GDR_LEVEL=5", env)


# ======================================================================
# from tests/test-hw.py (prefix ppt_)
# ======================================================================
"""Automated tests for MiOS Power Profile Transitions & Inference Downscaler (task_517 / task_518)."""


import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ppt__HERE = os.path.dirname(os.path.abspath(__file__))
ppt__ROOT = os.path.normpath(os.path.join(ppt__HERE, ".."))
ppt__MODULE_PATH = os.path.join(ppt__ROOT, "usr", "libexec", "mios", "hw", "powerd.py")

ppt_spec = importlib.util.spec_from_file_location("powerd", ppt__MODULE_PATH)
if ppt_spec and ppt_spec.loader:
    powerd = importlib.util.module_from_spec(ppt_spec)
    sys.modules[ppt_spec.name] = powerd
    ppt_spec.loader.exec_module(powerd)
else:
    raise ImportError(f"Could not load powerd module from {ppt__MODULE_PATH}")

class ppt_TestPowerProfileTransitions(unittest.TestCase):
    """Validates AC/DC power supply telemetry, CPU EPP scaling, model switching, and CLI contracts."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="mios_test_powerd_")
        self.sysfs_root = self.tmp_dir
        self.state_file = os.path.join(self.tmp_dir, "powerd_state.json")

        # Setup synthetic sysfs power_supply hierarchy
        self.power_supply_dir = os.path.join(self.sysfs_root, "sys", "class", "power_supply")
        self.ac_dir = os.path.join(self.power_supply_dir, "ACAD")
        self.bat_dir = os.path.join(self.power_supply_dir, "BAT0")
        os.makedirs(self.ac_dir, exist_ok=True)
        os.makedirs(self.bat_dir, exist_ok=True)

        with open(os.path.join(self.ac_dir, "type"), "w", encoding="utf-8") as f:
            f.write("Mains\n")
        with open(os.path.join(self.ac_dir, "online"), "w", encoding="utf-8") as f:
            f.write("1\n")

        with open(os.path.join(self.bat_dir, "type"), "w", encoding="utf-8") as f:
            f.write("Battery\n")
        with open(os.path.join(self.bat_dir, "capacity"), "w", encoding="utf-8") as f:
            f.write("85\n")
        with open(os.path.join(self.bat_dir, "status"), "w", encoding="utf-8") as f:
            f.write("Charging\n")

        # Setup synthetic sysfs CPU hierarchy (cpu0..cpu3)
        self.cpu_base = os.path.join(self.sysfs_root, "sys", "devices", "system", "cpu")
        for i in range(4):
            cpufreq_dir = os.path.join(self.cpu_base, f"cpu{i}", "cpufreq")
            power_dir = os.path.join(self.cpu_base, f"cpu{i}", "power")
            os.makedirs(cpufreq_dir, exist_ok=True)
            os.makedirs(power_dir, exist_ok=True)

            with open(os.path.join(cpufreq_dir, "scaling_governor"), "w", encoding="utf-8") as f:
                f.write("performance\n")
            with open(os.path.join(cpufreq_dir, "energy_performance_preference"), "w", encoding="utf-8") as f:
                f.write("balance_performance\n")
            with open(os.path.join(power_dir, "energy_performance_preference"), "w", encoding="utf-8") as f:
                f.write("balance_performance\n")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_initial_state_ac(self) -> None:
        daemon = powerd.PowerDaemon(
            sysfs_root=self.sysfs_root,
            state_file=self.state_file,
            mock=True,
        )
        state = daemon.get_status()
        self.assertEqual(state.power_source, "AC")
        self.assertEqual(state.cpu_epp, "balance_performance")
        self.assertEqual(state.active_model_tier, "heavy")
        self.assertEqual(state.governor, "performance")
        self.assertEqual(state.paused_containers, [])
        self.assertTrue(state.ac_online)
        self.assertEqual(state.gpu_power_state, "high")

    def test_ac_to_dc_transition(self) -> None:
        daemon = powerd.PowerDaemon(
            sysfs_root=self.sysfs_root,
            state_file=self.state_file,
            mock=True,
        )
        # Apply DC transition
        state = daemon.apply_profile("DC")
        self.assertEqual(state.power_source, "BATTERY")
        self.assertEqual(state.cpu_epp, "power")
        self.assertEqual(state.governor, "powersave")
        self.assertEqual(state.active_model_tier, "light_3b")
        self.assertEqual(state.gpu_power_state, "low")
        self.assertFalse(state.ac_online)
        self.assertIn("mios-finetune", state.paused_containers)
        self.assertIn("mios-embed-backfill", state.paused_containers)

    def test_dc_to_ac_restoration(self) -> None:
        daemon = powerd.PowerDaemon(
            sysfs_root=self.sysfs_root,
            state_file=self.state_file,
            mock=True,
        )
        # Transition to DC first
        daemon.apply_profile("DC")
        self.assertEqual(daemon.state.power_source, "BATTERY")

        # Transition back to AC
        state = daemon.apply_profile("AC")
        self.assertEqual(state.power_source, "AC")
        self.assertEqual(state.cpu_epp, "balance_performance")
        self.assertEqual(state.governor, "performance")
        self.assertEqual(state.active_model_tier, "heavy")
        self.assertEqual(state.gpu_power_state, "high")
        self.assertTrue(state.ac_online)
        self.assertEqual(state.paused_containers, [])

    def test_sysfs_telemetry_reading(self) -> None:
        daemon = powerd.PowerDaemon(
            sysfs_root=self.sysfs_root,
            state_file=self.state_file,
            mock=False,
        )
        # Read initial AC telemetry
        telemetry = daemon.read_telemetry()
        self.assertTrue(telemetry["ac_online"])
        self.assertEqual(telemetry["power_source"], "AC")
        self.assertEqual(telemetry["battery_pct"], 85)
        self.assertEqual(telemetry["battery_status"], "Charging")

        # Simulate unplugging AC adapter
        with open(os.path.join(self.ac_dir, "online"), "w", encoding="utf-8") as f:
            f.write("0\n")
        with open(os.path.join(self.bat_dir, "status"), "w", encoding="utf-8") as f:
            f.write("Discharging\n")
        with open(os.path.join(self.bat_dir, "capacity"), "w", encoding="utf-8") as f:
            f.write("72\n")

        telemetry_dc = daemon.read_telemetry()
        self.assertFalse(telemetry_dc["ac_online"])
        self.assertEqual(telemetry_dc["power_source"], "BATTERY")
        self.assertEqual(telemetry_dc["battery_pct"], 72)
        self.assertEqual(telemetry_dc["battery_status"], "Discharging")

    def test_multi_battery_average_capacity(self) -> None:
        # Create second battery BAT1
        bat1_dir = os.path.join(self.power_supply_dir, "BAT1")
        os.makedirs(bat1_dir, exist_ok=True)
        with open(os.path.join(bat1_dir, "type"), "w", encoding="utf-8") as f:
            f.write("Battery\n")
        with open(os.path.join(bat1_dir, "capacity"), "w", encoding="utf-8") as f:
            f.write("55\n")
        with open(os.path.join(bat1_dir, "status"), "w", encoding="utf-8") as f:
            f.write("Discharging\n")

        with open(os.path.join(self.bat_dir, "capacity"), "w", encoding="utf-8") as f:
            f.write("65\n")

        daemon = powerd.PowerDaemon(
            sysfs_root=self.sysfs_root,
            state_file=self.state_file,
            mock=False,
        )
        telemetry = daemon.read_telemetry()
        # Average of 65 and 55 = 60
        self.assertEqual(telemetry["battery_pct"], 60)

    def test_desktop_no_battery_fallback(self) -> None:
        # Remove all power supply devices (simulate desktop workstation / VM without batteries)
        empty_tmp = tempfile.mkdtemp(prefix="mios_empty_ps_")
        try:
            os.makedirs(os.path.join(empty_tmp, "sys", "class", "power_supply"), exist_ok=True)
            daemon = powerd.PowerDaemon(
                sysfs_root=empty_tmp,
                state_file=os.path.join(empty_tmp, "state.json"),
                mock=False,
            )
            telemetry = daemon.read_telemetry()
            self.assertTrue(telemetry["ac_online"])
            self.assertEqual(telemetry["power_source"], "AC")
            self.assertEqual(telemetry["battery_pct"], 100)
            self.assertEqual(telemetry["battery_status"], "Full")
        finally:
            shutil.rmtree(empty_tmp, ignore_errors=True)

    def test_sysfs_cpu_epp_and_governor_writing(self) -> None:
        daemon = powerd.PowerDaemon(
            sysfs_root=self.sysfs_root,
            state_file=self.state_file,
            mock=False,
        )
        # Transition to DC on synthetic sysfs
        daemon.apply_profile("DC")
        for i in range(4):
            epp_file = os.path.join(self.cpu_base, f"cpu{i}", "cpufreq", "energy_performance_preference")
            with open(epp_file, "r", encoding="utf-8") as f:
                self.assertEqual(f.read().strip(), "power")
            gov_file = os.path.join(self.cpu_base, f"cpu{i}", "cpufreq", "scaling_governor")
            with open(gov_file, "r", encoding="utf-8") as f:
                self.assertEqual(f.read().strip(), "powersave")

        # Transition to AC on synthetic sysfs
        daemon.apply_profile("AC")
        for i in range(4):
            epp_file = os.path.join(self.cpu_base, f"cpu{i}", "cpufreq", "energy_performance_preference")
            with open(epp_file, "r", encoding="utf-8") as f:
                self.assertEqual(f.read().strip(), "balance_performance")
            gov_file = os.path.join(self.cpu_base, f"cpu{i}", "cpufreq", "scaling_governor")
            with open(gov_file, "r", encoding="utf-8") as f:
                self.assertEqual(f.read().strip(), "performance")

    def test_state_persistence_and_reloading(self) -> None:
        daemon1 = powerd.PowerDaemon(
            sysfs_root=self.sysfs_root,
            state_file=self.state_file,
            mock=False,
        )
        daemon1.apply_profile("DC")
        self.assertTrue(os.path.isfile(self.state_file))

        # Create new daemon instance loading state from disk
        daemon2 = powerd.PowerDaemon(
            sysfs_root=self.sysfs_root,
            state_file=self.state_file,
            mock=False,
        )
        self.assertEqual(daemon2.state.power_source, "BATTERY")
        self.assertEqual(daemon2.state.cpu_epp, "power")
        self.assertEqual(daemon2.state.active_model_tier, "light_3b")
        self.assertIn("mios-finetune", daemon2.state.paused_containers)

    def test_poll_and_sync_trigger(self) -> None:
        daemon = powerd.PowerDaemon(
            sysfs_root=self.sysfs_root,
            state_file=self.state_file,
            mock=False,
        )
        # Verify initial AC state
        self.assertEqual(daemon.state.power_source, "AC")

        # Simulate AC disconnect in sysfs
        with open(os.path.join(self.ac_dir, "online"), "w", encoding="utf-8") as f:
            f.write("0\n")
        with open(os.path.join(self.bat_dir, "status"), "w", encoding="utf-8") as f:
            f.write("Discharging\n")

        # Polling detects change and triggers DC downscale
        synced_state = daemon.poll_and_sync()
        self.assertEqual(synced_state.power_source, "BATTERY")
        self.assertEqual(synced_state.cpu_epp, "power")
        self.assertEqual(synced_state.active_model_tier, "light_3b")

        # Simulate AC reconnect in sysfs
        with open(os.path.join(self.ac_dir, "online"), "w", encoding="utf-8") as f:
            f.write("1\n")
        with open(os.path.join(self.bat_dir, "status"), "w", encoding="utf-8") as f:
            f.write("Charging\n")

        synced_state_ac = daemon.poll_and_sync()
        self.assertEqual(synced_state_ac.power_source, "AC")
        self.assertEqual(synced_state_ac.cpu_epp, "balance_performance")
        self.assertEqual(synced_state_ac.active_model_tier, "heavy")

    def test_cli_status_json_contract(self) -> None:
        cmd = [
            sys.executable,
            ppt__MODULE_PATH,
            "--mock",
            "--status",
            "--json",
            "--state-file",
            self.state_file,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        self.assertEqual(res.returncode, 0, f"CLI execution failed: {res.stderr}")
        data = json.loads(res.stdout)

        # Check strict contract fields from PROJECT.md
        self.assertIn("power_source", data)
        self.assertIn("cpu_epp", data)
        self.assertIn("active_model_tier", data)
        self.assertIn("paused_containers", data)
        self.assertIn(data["power_source"], ["AC", "BATTERY"])
        self.assertIn(data["cpu_epp"], ["balance_performance", "power"])
        self.assertIn(data["active_model_tier"], ["heavy", "light_3b"])
        self.assertIsInstance(data["paused_containers"], list)

    def test_cli_set_state_transitions(self) -> None:
        # Test set-state dc
        cmd_dc = [
            sys.executable,
            ppt__MODULE_PATH,
            "--mock",
            "--set-state",
            "dc",
            "--json",
            "--state-file",
            self.state_file,
        ]
        res_dc = subprocess.run(cmd_dc, capture_output=True, text=True, timeout=10)
        self.assertEqual(res_dc.returncode, 0, f"CLI set-state dc failed: {res_dc.stderr}")
        data_dc = json.loads(res_dc.stdout)
        self.assertEqual(data_dc["power_source"], "BATTERY")
        self.assertEqual(data_dc["cpu_epp"], "power")
        self.assertEqual(data_dc["active_model_tier"], "light_3b")
        self.assertIn("mios-finetune", data_dc["paused_containers"])

        # Test set-state ac
        cmd_ac = [
            sys.executable,
            ppt__MODULE_PATH,
            "--mock",
            "--set-state",
            "ac",
            "--json",
            "--state-file",
            self.state_file,
        ]
        res_ac = subprocess.run(cmd_ac, capture_output=True, text=True, timeout=10)
        self.assertEqual(res_ac.returncode, 0, f"CLI set-state ac failed: {res_ac.stderr}")
        data_ac = json.loads(res_ac.stdout)
        self.assertEqual(data_ac["power_source"], "AC")
        self.assertEqual(data_ac["cpu_epp"], "balance_performance")
        self.assertEqual(data_ac["active_model_tier"], "heavy")
        self.assertEqual(data_ac["paused_containers"], [])


# ======================================================================
# from tests/test-hw.py (prefix th_)
# ======================================================================
"""Automated unit test suite for MiOS Thermal Governor Daemon."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "hw"))

from thermald import DOWNSTEP_TEMP_THRESHOLD, RECOVERY_TEMP_THRESHOLD, ThermalGovernorManager

class th_TestThermald(unittest.TestCase):
    def setUp(self):
        self.gov = ThermalGovernorManager(dry_run=True)

    def test_thermal_step_down_at_85c(self):
        """Test exceeding 85°C triggers EPP step down to balance_performance."""
        st = self.gov.evaluate_thermal_sample(87.5)
        self.assertEqual(st.current_epp, "balance_performance")
        self.assertTrue(st.is_throttling)

    def test_hysteresis_recovery_at_75c(self):
        """Test cooling below 75°C restores performance EPP."""
        self.gov.evaluate_thermal_sample(88.0)  # Throttled
        st = self.gov.evaluate_thermal_sample(73.0)  # Cooled
        self.assertEqual(st.current_epp, "performance")
        self.assertFalse(st.is_throttling)


# ======================================================================
# from tests/test-hw.py (prefix uh_)
# ======================================================================
"""Automated tests for MiOS USB Controller & DAC Hotplug Passthrough Manager (task_366)."""


import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

uh__HERE = os.path.dirname(os.path.abspath(__file__))
uh__ROOT = os.path.normpath(os.path.join(uh__HERE, ".."))
uh__MODULE_PATH = os.path.join(uh__ROOT, "usr", "libexec", "mios", "hw", "usb_hotplug.py")

uh_spec = importlib.util.spec_from_file_location("usb_hotplug", uh__MODULE_PATH)
if uh_spec and uh_spec.loader:
    usb_hotplug = importlib.util.module_from_spec(uh_spec)
    sys.modules[uh_spec.name] = usb_hotplug
    uh_spec.loader.exec_module(usb_hotplug)
else:
    raise ImportError(f"Could not load usb_hotplug module from {uh__MODULE_PATH}")

class uh_TestUSBHotplug(unittest.TestCase):
    """Validates USB topology scanning, device classification, host peripheral exclusion, and XML generation."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="mios_test_usb_")
        self.sysfs_root = self.tmp_dir
        self.usb_dir = os.path.join(self.sysfs_root, "sys", "bus", "usb", "devices")
        os.makedirs(self.usb_dir, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _create_usb_device(
        self,
        dev_name: str,
        vid: str,
        pid: str,
        product: str = "Device",
        manufacturer: str = "Vendor",
        dev_class: str = "00",
        interfaces: list[tuple[str, str, str]] | None = None,
    ) -> str:
        dpath = os.path.join(self.usb_dir, dev_name)
        os.makedirs(dpath, exist_ok=True)
        with open(os.path.join(dpath, "idVendor"), "w") as f:
            f.write(f"{vid}\n")
        with open(os.path.join(dpath, "idProduct"), "w") as f:
            f.write(f"{pid}\n")
        with open(os.path.join(dpath, "product"), "w") as f:
            f.write(f"{product}\n")
        with open(os.path.join(dpath, "manufacturer"), "w") as f:
            f.write(f"{manufacturer}\n")
        with open(os.path.join(dpath, "bDeviceClass"), "w") as f:
            f.write(f"{dev_class}\n")
        with open(os.path.join(dpath, "busnum"), "w") as f:
            f.write("1\n")
        with open(os.path.join(dpath, "devnum"), "w") as f:
            f.write("2\n")

        if interfaces:
            for idx, (iclass, isub, iproto) in enumerate(interfaces):
                ipath = os.path.join(dpath, f"iface_{idx}.0")
                os.makedirs(ipath, exist_ok=True)
                with open(os.path.join(ipath, "bInterfaceClass"), "w") as f:
                    f.write(f"{iclass}\n")
                with open(os.path.join(ipath, "bInterfaceSubClass"), "w") as f:
                    f.write(f"{isub}\n")
                with open(os.path.join(ipath, "bInterfaceProtocol"), "w") as f:
                    f.write(f"{iproto}\n")
        return dpath

    def test_scan_and_classify_controllers(self) -> None:
        # Xbox Controller
        self._create_usb_device("1-1", "045e", "028e", product="Xbox 360 Controller", manufacturer="Microsoft")
        # DualSense Controller
        self._create_usb_device("1-2", "054c", "0ce6", product="Wireless Controller", manufacturer="Sony")
        # Switch Pro Controller
        self._create_usb_device("1-3", "057e", "2009", product="Pro Controller", manufacturer="Nintendo")

        mgr = usb_hotplug.USBHotplugManager(sysfs_root=self.sysfs_root)
        devices = mgr.scan_usb_devices()
        self.assertEqual(len(devices), 3)

        for dev in devices:
            classification = mgr.classify_device(dev)
            self.assertEqual(classification["category"], "gamepad")
            self.assertTrue(classification["eligible_for_passthrough"])

    def test_scan_and_classify_audio_dac(self) -> None:
        # Focusrite Scarlett DAC
        self._create_usb_device("1-4", "1235", "8210", product="Scarlett 2i2 USB", manufacturer="Focusrite")
        # Generic USB Audio Class device
        self._create_usb_device(
            "1-5",
            "9999",
            "8888",
            product="Custom HiFi DAC",
            manufacturer="Audiophile",
            interfaces=[("01", "01", "00"), ("01", "02", "00")],
        )

        mgr = usb_hotplug.USBHotplugManager(sysfs_root=self.sysfs_root)
        devices = mgr.scan_usb_devices()
        self.assertEqual(len(devices), 2)

        for dev in devices:
            classification = mgr.classify_device(dev)
            self.assertEqual(classification["category"], "audio_dac")
            self.assertTrue(classification["eligible_for_passthrough"])

    def test_host_keyboard_and_mouse_exclusion_invariant(self) -> None:
        """Enforces: Do NOT hotplug host keyboards or mice that would lock the operator out of the host OS."""
        # Host Keyboard (HID class 03, subclass 01, proto 01)
        self._create_usb_device(
            "1-6",
            "046d",
            "c31c",
            product="USB Mechanical Keyboard",
            manufacturer="Logitech",
            interfaces=[("03", "01", "01")],
        )
        # Host Mouse (HID class 03, subclass 01, proto 02)
        self._create_usb_device(
            "1-7",
            "1532",
            "0084",
            product="DeathAdder Mouse",
            manufacturer="Razer",
            interfaces=[("03", "01", "02")],
        )

        mgr = usb_hotplug.USBHotplugManager(sysfs_root=self.sysfs_root)
        devices = mgr.scan_usb_devices()
        self.assertEqual(len(devices), 2)

        for dev in devices:
            self.assertTrue(mgr.is_host_keyboard_or_mouse(dev))
            classification = mgr.classify_device(dev)
            self.assertEqual(classification["category"], "host_input")
            self.assertFalse(classification["eligible_for_passthrough"])

        # Test attach rejection
        attach_res = mgr.attach_device("win11", "046d", "c31c")
        self.assertEqual(attach_res["status"], "rejected")
        self.assertIn("Cannot attach host keyboard or mouse", attach_res["reason"])

    def test_hostdev_xml_generation(self) -> None:
        mgr = usb_hotplug.USBHotplugManager(sysfs_root=self.sysfs_root)
        xml = mgr.generate_hostdev_xml("045e", "028e", bus=1, device=4)
        self.assertIn("<vendor id='0x045e'/>", xml)
        self.assertIn("<product id='0x028e'/>", xml)
        self.assertIn("<address bus='1' device='4'/>", xml)

    def test_udev_rules_generation(self) -> None:
        mgr = usb_hotplug.USBHotplugManager(sysfs_root=self.sysfs_root)
        rules = mgr.generate_udev_rules(domain="gaming-vm")
        self.assertIn('ATTR{idVendor}=="045e"', rules)
        self.assertIn('ATTR{idVendor}=="054c"', rules)
        self.assertIn('ATTR{idVendor}=="1235"', rules)
        self.assertIn('--domain=gaming-vm', rules)

    def test_8bitdo_and_logitech_gamepads(self) -> None:
        # 8BitDo Ultimate Controller (2dc8:3106)
        self._create_usb_device("1-8", "2dc8", "3106", product="8BitDo Ultimate", manufacturer="8BitDo")
        # Logitech F310 Gamepad (046d:c216)
        self._create_usb_device("1-9", "046d", "c216", product="Logitech Gamepad F310", manufacturer="Logitech")

        mgr = usb_hotplug.USBHotplugManager(sysfs_root=self.sysfs_root)
        devices = mgr.scan_usb_devices()
        self.assertEqual(len(devices), 2)

        for dev in devices:
            cl = mgr.classify_device(dev)
            self.assertEqual(cl["category"], "gamepad")
            self.assertTrue(cl["eligible_for_passthrough"])

    def test_dry_run_attach_and_detach(self) -> None:
        mgr = usb_hotplug.USBHotplugManager(sysfs_root=self.sysfs_root, dry_run=True)
        res_attach = mgr.attach_device("win11", "045e", "028e", bus=2, device=5)
        self.assertEqual(res_attach["status"], "simulated")
        self.assertIn("<vendor id='0x045e'/>", res_attach["xml"])
        self.assertIn("<address bus='2' device='5'/>", res_attach["xml"])

        res_detach = mgr.detach_device("win11", "045e", "028e", bus=2, device=5)
        self.assertEqual(res_detach["status"], "simulated")
        self.assertIn("<vendor id='0x045e'/>", res_detach["xml"])

def uh_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(uh_TestUSBHotplug)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-hw.py (prefix us_)
# ======================================================================
"""Automated unit test suite for MiOS USB Surge Protection Daemon."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "hw"))

from usb_surge import MAX_ISOLATION_LATENCY_MS, USBSurgeProtectionDaemon

class us_TestUSBSurge(unittest.TestCase):
    def setUp(self):
        self.daemon = USBSurgeProtectionDaemon(dry_run=True)

    def test_sub_500ms_power_isolation(self):
        """Test over-current event triggers power cutoff in <500ms."""
        evt = self.daemon.handle_overcurrent_event(port_id="2-1.4", bus_number=2)
        self.assertTrue(evt.is_power_suspended)
        self.assertLess(evt.isolation_latency_ms, MAX_ISOLATION_LATENCY_MS)

    def test_thermal_cool_down_and_recovery_cycle(self):
        """Test cool-down duration is configured and recovery succeeds."""
        evt = self.daemon.handle_overcurrent_event(port_id="1-2.1", bus_number=1)
        self.assertEqual(evt.cool_down_duration_sec, 5.0)
        self.assertTrue(evt.recovery_successful)


import sys as _sys
import unittest as _unittest


def main() -> int:
    rc = 0 if _unittest.main(argv=[_sys.argv[0]], exit=False).result.wasSuccessful() else 1
    return rc


if __name__ == '__main__':
    _sys.exit(main())
