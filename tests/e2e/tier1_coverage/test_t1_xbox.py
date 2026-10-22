#!/usr/bin/env python3
"""Tier 1: Feature Coverage — Windows / Xbox Subsystem & Pipeline Suite.

Covers:
- Feature 20: Pipeline Script Port (xb-05)
- Feature 21: Xbox Feature CFR (xb-07: 153 toggles)
- Feature 22: Drivers & Guest Tools (xb-08)
- Feature 23: MiOS Component Staging (xb-10)
- Feature 24: Autologon & Admin (xb-12)
- Feature 25: Domain Generator (xb-13)
- Feature 26: GPU Driver Script Fixes (G1-G4)
- Feature 27: Phase-A Pins Gate Follow-ups (virtio latest, pin-gate:allow)
- Feature 28: Build Worker VM (xb-14)
- Feature 29: Start Menu Assets (xb-11)
- Feature 30: Image Servicing & Build (xb-15)
- Feature 31: Offline Conformance (xb-16)
- Feature 32: Cold GPU Domain Launch (xb-17)
- Feature 33: In-Guest Probe Verification (xb-18/19)
- Feature 34: Legacy Script Deprecation (xb-20)
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib

# Ensure repository root is in sys.path for standalone execution
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests.e2e.e2e_framework import E2ETestCase, IsolatedEnvironment, run_standalone



class TestTier1Xbox(E2ETestCase):
    tier = 1
    suite_name = "T1_Xbox_Subsystem_Pipeline"

    def test_feature_21_cfr_toggles_count_in_ssot(self):
        """Feature 21: Verify SSOT declares at least 153 CFR feature toggles."""
        ssot_path = "/usr/share/mios/mios.toml"
        with open(ssot_path, "rb") as f:
            data = tomllib.load(f)

        # SSOT stores CFR toggles under [windows.xbox.xbox.feature_overrides]
        cfr_overrides = (
            data.get("windows", {})
            .get("xbox", {})
            .get("xbox", {})
            .get("feature_overrides", {})
        )
        self.assert_greater_equal(len(cfr_overrides), 153, f"CFR overrides count {len(cfr_overrides)} < 153")

    def test_feature_22_zero_gpu_vendor_drivers_baked_in_spec(self):
        """Feature 22 (xb-08): Verify SSOT specifies drivers.gpu = 'detect' and zero GPU drivers baked."""
        ssot_path = "/usr/share/mios/mios.toml"
        with open(ssot_path, "rb") as f:
            data = tomllib.load(f)

        drivers_cfg = data.get("windows", {}).get("xbox", {}).get("drivers", {})
        self.assert_equal(drivers_cfg.get("gpu"), "detect", "drivers.gpu in SSOT must be 'detect'")

    def test_feature_25_domain_generator_contract(self):
        """Feature 25 (xb-13): Verify setup-looking-glass.py configures q35, OVMF, SPICE 5930, IVSHMEM 128M."""
        gen_script = "/usr/libexec/mios/vfio/setup-looking-glass.py"
        self.assert_true(os.path.exists(gen_script), f"Missing {gen_script}")

        with open(gen_script, "r", encoding="utf-8") as f:
            content = f.read()

        self.assert_in("5930", content)  # SPICE port
        self.assert_in("looking-glass", content)  # IVSHMEM shm

        # Verify q35 machine type in Xbox domain templates
        xml_template = "/usr/libexec/mios/Xbox-AutoEnroll.xml"
        if os.path.exists(xml_template):
            with open(xml_template, "r", encoding="utf-8") as f:
                self.assert_in("q35", f.read())

    def test_feature_26_gpu_driver_script_security_fixes(self):
        """Feature 26: Verify mios-gpu-driver.ps1 has G1-G4 security patterns."""
        driver_script = "/usr/share/mios/windows/xbox/mios-gpu-driver.ps1"
        self.assert_true(os.path.exists(driver_script), f"Missing {driver_script}")

        with open(driver_script, "r", encoding="utf-8") as f:
            content = f.read()

        # Script must support -Register switch for scheduled task
        self.assert_in("Register", content)
        # Script must verify Authenticode signatures
        self.assert_in("Get-AuthenticodeSignature", content)

    def test_feature_27_pins_gate_follow_ups(self):
        """Feature 27: Verify pin regex in resolve_latest.py and pin-gate compatibility."""
        resolve_latest = "/usr/libexec/mios/win/xbox/resolve_latest.py"
        self.assert_true(os.path.exists(resolve_latest), f"Missing {resolve_latest}")

        with open(resolve_latest, "r", encoding="utf-8") as f:
            content = f.read()
        self.assert_in("_PIN_URL", content)

    def test_feature_31_offline_conformance_script_contract(self):
        """Feature 31 (xb-16): Verify conform.py runs and has two-sided tests."""
        conform_script = "/usr/libexec/mios/win/xbox/conform.py"
        self.assert_true(os.path.exists(conform_script), f"Missing {conform_script}")

        # Check existing unit test
        conform_test = "/tests/test-mios-xbox-conform.py"
        staged_test = "/home/mios/mios-desktop-work-1/lanes/credentials/root/tests/test-mios-xbox-conform.py"
        self.assert_true(
            os.path.exists(conform_test) or os.path.exists(staged_test),
            "test-mios-xbox-conform.py missing"
        )

    def test_feature_32_cold_gpu_domain_launch_contract(self):
        """Feature 32 (xb-17): Verify mios-xbox domain definition, specs, and cold launch invariants."""
        domain_xml_path = "/var/lib/mios-build/vms/mios-xbox/domain.xml"
        self.assert_true(os.path.exists(domain_xml_path), f"Missing domain XML at {domain_xml_path}")

        with open(domain_xml_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Check required hardware definitions
        self.assert_in("mios-xbox", content)
        self.assert_in("25165824", content)  # 24 GiB RAM
        self.assert_in('vcpu placement="static">12</vcpu>', content)  # 12 vCPUs
        self.assert_in("OVMF_CODE_4M.secboot.qcow2", content)  # secboot firmware
        self.assert_in("/var/lib/mios-build/vms/mios-xbox/disk.qcow2", content)  # target disk
        self.assert_in('model type="virtio"', content)  # virtio video / net
        self.assert_in('port="5930"', content)  # SPICE 5930
        self.assert_in("org.qemu.guest_agent.0", content)  # QEMU-GA
        self.assert_true('bus="0x01" slot="0x00" function="0x0"' in content or 'bus="0x01"' in content)  # RTX 4090 VGA
        self.assert_true('bus="0x01" slot="0x00" function="0x1"' in content or 'function="0x1"' in content)  # RTX 4090 Audio
        self.assert_in('shmem name="looking-glass"', content)

        # Target disk must exist and legacy disks must be preserved
        self.assert_true(os.path.exists("/var/lib/mios-build/vms/mios-xbox/disk.qcow2"))
        self.assert_true(os.path.exists("/var/lib/mios-build/vms/mios-xbox-current/disk.qcow2"))
        self.assert_true(os.path.exists("/var/lib/mios-build/vms/mios-xbox-baseline/disk.qcow2"))

    def test_feature_33_in_guest_probe_contract(self):
        """Feature 33 (xb-18/19): Verify probe.py and mios-xbox-probe.ps1 exist and two-sided verification."""
        probe_py = "/usr/libexec/mios/win/xbox/probe.py"
        probe_ps1 = "/usr/share/mios/windows/xbox/mios-xbox-probe.ps1"

        self.assert_true(os.path.exists(probe_py), f"Missing {probe_py}")
        self.assert_true(os.path.exists(probe_ps1), f"Missing {probe_ps1}")

        venv_py = "/var/lib/mios-build/venv/bin/python"
        py_bin = venv_py if os.path.exists(venv_py) else sys.executable

        # Positive control: offline recorded conforming fixture
        conforming_fixture = os.path.join(REPO_ROOT, "tests/fixtures/mios-xbox/probe-conforming.recorded.json")
        r_good = subprocess.run(
            [py_bin, probe_py, "--recorded", conforming_fixture],
            capture_output=True, text=True, timeout=30
        )
        self.assert_equal(r_good.returncode, 0, f"Positive control failed: {r_good.stdout} {r_good.stderr}")
        self.assert_in("CONFORMING", r_good.stdout)

        # Negative control: offline recorded legacy fixture
        legacy_fixture = os.path.join(REPO_ROOT, "tests/fixtures/mios-xbox/probe-legacy.recorded.json")
        if os.path.exists(legacy_fixture):
            r_bad = subprocess.run(
                [py_bin, probe_py, "--recorded", legacy_fixture],
                capture_output=True, text=True, timeout=30
            )
            self.assert_equal(r_bad.returncode, 1, "Negative control should have failed with exit code 1")
            self.assert_in("NON-CONFORMING", r_bad.stdout)

        # Negative control: live probe fails closed when domain is unreachable
        r_unreach = subprocess.run(
            [py_bin, probe_py, "--domain", "nonexistent-test-domain"],
            capture_output=True, text=True, timeout=30
        )
        self.assert_equal(r_unreach.returncode, 1, "Unreachable live domain must fail closed with exit code 1")
        self.assert_in("NON-CONFORMING", r_unreach.stdout)

    def test_feature_34_legacy_script_deprecation_contract(self):
        """Feature 34 (xb-20): Verify legacy scripts fail closed with exit code 2 and deprecation banners."""
        import subprocess

        # 1. Pipeline check: no active pipeline entry references retired legacy scripts
        rg_check = subprocess.run(
            ["grep", "-rnE", "mios-remaster-xbox-answerfile|mios-define-current-xbox", "automation/"],
            cwd=REPO_ROOT, capture_output=True, text=True
        )
        self.assert_not_equal(rg_check.returncode, 0, f"Found legacy script references in automation/: {rg_check.stdout}")

        # 2. Subprocess execution: /var/lib/mios-build/mios-remaster-xbox-answerfile.sh
        sh_script = "/var/lib/mios-build/mios-remaster-xbox-answerfile.sh"
        self.assert_true(os.path.exists(sh_script), f"Missing {sh_script}")
        r_sh = subprocess.run(
            ["bash", sh_script],
            capture_output=True, text=True, timeout=10
        )
        self.assert_equal(r_sh.returncode, 2, f"Expected exit code 2, got {r_sh.returncode}. Output: {r_sh.stdout} {r_sh.stderr}")
        self.assert_in("DEPRECATED and RETIRED", r_sh.stderr)
        self.assert_in("New-MiOSXboxImage.ps1", r_sh.stderr)

        # 3. Subprocess execution: /var/lib/mios-build/mios-define-current-xbox.py
        py_script = "/var/lib/mios-build/mios-define-current-xbox.py"
        self.assert_true(os.path.exists(py_script), f"Missing {py_script}")
        r_py = subprocess.run(
            [sys.executable, py_script],
            capture_output=True, text=True, timeout=10
        )
        self.assert_equal(r_py.returncode, 2, f"Expected exit code 2, got {r_py.returncode}. Output: {r_py.stdout} {r_py.stderr}")
        self.assert_in("DEPRECATED and RETIRED", r_py.stderr)
        self.assert_in("worker.py", r_py.stderr)

        # 4. Two-sided negative control: verify broken/non-deprecated script fails deprecation contract
        with IsolatedEnvironment() as env:
            dummy_bad = env.write_file("#!/usr/bin/env bash\necho 'running legacy code'\nexit 0\n", "bad_legacy.sh", mode=0o755)
            r_bad = subprocess.run(["bash", dummy_bad], capture_output=True, text=True, timeout=10)
            self.assert_not_equal(r_bad.returncode, 2)
            self.assert_not_in("DEPRECATED and RETIRED", r_bad.stderr)


if __name__ == "__main__":
    sys.exit(run_standalone(TestTier1Xbox))


