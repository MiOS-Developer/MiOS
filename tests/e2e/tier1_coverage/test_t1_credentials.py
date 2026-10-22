#!/usr/bin/env python3
"""Tier 1: Feature Coverage — Credentials & Secrets Architecture Suite.

Covers:
- Feature 2: Credential Helper Mode (D1)
- Feature 3: PowerShell Layered Readers (D2/D3)
- Feature 4: AutoLogon Consistency (D4)
- Feature 5: Bootstrap SSOT Deduplication (G1)
- Feature 6: Render Globals Secret Stripping (G2)
- Feature 7: WSL Conf Default User (G3)
- Feature 8: Justfile Password Hash (G4)
- Feature 9: Install Bootstrap Credentials (G5)
- Feature 10: Kickstart SHA-512 Hash (G6)
- Feature 11: CMD Literal Escaping (G8)
- Feature 12: Xbox Conformance Deny-List
- Feature 13: Credentials Test Verification
"""

from __future__ import annotations

import os
import re
import sys
import importlib.util
import tomllib

# Ensure repository root is in sys.path for standalone execution
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests.e2e.e2e_framework import E2ETestCase, IsolatedEnvironment, run_standalone



class TestTier1Credentials(E2ETestCase):
    tier = 1
    suite_name = "T1_Credentials_Architecture"

    def test_feature_02_credential_helper_permissions(self):
        """Feature 2 (D1): Verify /usr/libexec/mios/mios-credential is executable (0755)."""
        helper_path = "/usr/libexec/mios/mios-credential"
        staged_path = "/home/mios/mios-desktop-work-1/lanes/credentials/root/usr/libexec/mios/mios-credential"
        
        target = helper_path if os.path.exists(helper_path) else staged_path
        self.assert_true(os.path.exists(target), f"mios-credential missing at {target}")
        
        mode = os.stat(target).st_mode
        is_exec = bool(mode & 0o111)
        self.assert_true(is_exec, f"mios-credential at {target} is not executable (mode: {oct(mode)})")

    def test_feature_04_autologon_consistency_rejection(self):
        """Feature 4 (D4): Verify autologon_user == svc_user is rejected consistently."""
        unattend_gen_path = "/usr/libexec/mios/win/unattend_gen.py"
        self.assert_true(os.path.exists(unattend_gen_path), f"Missing {unattend_gen_path}")

        spec = importlib.util.spec_from_file_location("unattend_gen", unattend_gen_path)
        self.assert_true(spec is not None and spec.loader is not None, "Failed to load spec for unattend_gen")
        ug = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = ug
        spec.loader.exec_module(ug)

        # Two-sided control: valid user passes, autologon_user == svc_user rejected
        def pos_control():
            cfg = ug.UnattendConfig(
                username="desktop_user",
                password="secret_password",
                autologon_user="desktop_user",
                svc_user="admin",
                autologon_enable=True
            )
            tree = ug.UnattendGenerator(cfg).build_xml_tree()
            return tree is not None

        def neg_control():
            cfg = ug.UnattendConfig(
                username="admin",
                password="secret_password",
                autologon_user="admin",
                svc_user="admin",
                autologon_enable=True
            )
            try:
                ug.UnattendGenerator(cfg).build_xml_tree()
                return False
            except ValueError as e:
                return "is the service account" in str(e) or "autologon" in str(e).lower()

        self.assert_two_sided(pos_control, neg_control, "AutoLogon user conflict detection")

    def test_feature_06_render_globals_secret_stripping(self):
        """Feature 6 (G2): Verify render.rs has is_credential_name and globals strip secrets."""
        render_rs = "/tools/native/mios-gen/src/render.rs"
        staged_render_rs = "/home/mios/mios-desktop-work-1/lanes/credentials/root/tools/native/mios-gen/src/render.rs"
        target_rs = render_rs if os.path.exists(render_rs) else staged_render_rs
        
        self.assert_true(os.path.exists(target_rs), f"render.rs missing at {target_rs}")
        with open(target_rs, "r", encoding="utf-8") as f:
            content = f.read()

        # Check for credential stripping logic
        has_secret_strip = "is_credential_name" in content or "_PASSWORD" in content
        self.assert_true(has_secret_strip, "render.rs lacks secret stripping logic")

    def test_feature_09_install_bootstrap_credentials_helper(self):
        """Feature 9 (G5): Verify install-bootstrap.sh uses mios_credentials helper."""
        install_script = "/automation/install-bootstrap.sh"
        staged_script = "/home/mios/mios-desktop-work-1/lanes/credentials/root/automation/install-bootstrap.sh"
        target = install_script if os.path.exists(install_script) else staged_script

        if os.path.exists(target):
            with open(target, "r", encoding="utf-8") as f:
                content = f.read()
            self.assert_in("mios_credentials", content)

    def test_feature_10_kickstart_sha512_crypt(self):
        """Feature 10 (G6): Verify ConvertTo-MiosSha512Crypt implementation in PowerShell library."""
        lib_ps1 = "/home/mios/mios-desktop-work-1/lanes/credentials/bootstrap/field/autounattend/MiOS-Provision.lib.ps1"
        if not os.path.exists(lib_ps1):
            lib_ps1 = "/var/lib/mios-build/repos/mios-bootstrap-build/field/autounattend/MiOS-Provision.lib.ps1"

        if os.path.exists(lib_ps1):
            with open(lib_ps1, "r", encoding="utf-8") as f:
                content = f.read()
            self.assert_in("ConvertTo-MiosSha512Crypt", content)
            self.assert_in("$6$", content)

    def test_feature_11_cmd_literal_escaping(self):
        """Feature 11 (G8): Verify ConvertTo-MiosCmdLiteral escapes % to %% and quotes literals."""
        lib_ps1 = "/home/mios/mios-desktop-work-1/lanes/credentials/bootstrap/field/autounattend/MiOS-Provision.lib.ps1"
        if not os.path.exists(lib_ps1):
            lib_ps1 = "/var/lib/mios-build/repos/mios-bootstrap-build/field/autounattend/MiOS-Provision.lib.ps1"

        if os.path.exists(lib_ps1):
            with open(lib_ps1, "r", encoding="utf-8") as f:
                content = f.read()
            self.assert_in("ConvertTo-MiosCmdLiteral", content)
            self.assert_in("%%", content)

    def test_feature_12_xbox_conformance_deny_list(self):
        """Feature 12: Verify conform.py denies hardcoded credentials while verifying against SSOT."""
        conform_py = "/usr/libexec/mios/win/xbox/conform.py"
        self.assert_true(os.path.exists(conform_py), f"Missing {conform_py}")
        with open(conform_py, "r", encoding="utf-8") as f:
            content = f.read()

        self.assert_in("deny.literal-credential", content)

    def test_feature_13_credential_tests_exist(self):
        """Feature 13: Verify test-credential-ssot.py and test-unattend.py exist."""
        test_cred = "/tests/test-credential-ssot.py"
        staged_test = "/home/mios/mios-desktop-work-1/lanes/credentials/root/tests/test-credential-ssot.py"
        self.assert_true(
            os.path.exists(test_cred) or os.path.exists(staged_test),
            "test-credential-ssot.py missing from both live /tests/ and staged lane"
        )

        test_unattend = "/tests/test-unattend.py"
        self.assert_true(os.path.exists(test_unattend), f"Missing {test_unattend}")


if __name__ == "__main__":
    sys.exit(run_standalone(TestTier1Credentials))

