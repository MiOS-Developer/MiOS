#!/usr/bin/env python3
"""Native PowerShell unit proof for shared credential/render boundaries.

Runs only against private synthetic files. This is not Windows installation,
ACL, registry, device, image, or USB acceptance. No operating system is spoofed.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

BOOT = Path(os.environ.get("MIOS_BOOTSTRAP_DIR") or
            os.environ.get("MIOS_BOOTSTRAP_ROOT") or
            "/var/lib/mios-build/repos/mios-bootstrap-build")
LIB = BOOT / "field/autounattend/MiOS-Provision.lib.ps1"
PWSH = shutil.which("pwsh")


class TestPrivateCredentialRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH or not LIB.is_file() or os.name != "posix":
            raise RuntimeError("Native POSIX PowerShell and the selected Bootstrap source are required")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mios-private-render-")
        self.root = Path(self.temp.name)
        for name in ("vendor.d", "host.d", "user.d"):
            (self.root / name).mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("MIOS_")}
        self.env.update({"MIOS_BOOTSTRAP_BASE_TOML": "-", "MIOS_PRIVATE_TEST_LIB": str(LIB),
                         "MIOS_PRIVATE_TEST_DIR": str(self.root),
                         "MIOS_VENDOR_TOML": str(self.root / "vendor.toml"),
                         "MIOS_VENDOR_TOML_D": str(self.root / "vendor.d"),
                         "MIOS_HOST_TOML": str(self.root / "host.toml"),
                         "MIOS_HOST_TOML_D": str(self.root / "host.d"),
                         "MIOS_USER_TOML": str(self.root / "user.toml"),
                         "MIOS_USER_TOML_D": str(self.root / "user.d")})
        self.addCleanup(self.temp.cleanup)

    def ps(self, source):
        return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command",
                               "$ErrorActionPreference='Stop'; . $env:MIOS_PRIVATE_TEST_LIB;\n" + source],
                              env=self.env, capture_output=True, text=True, timeout=60)

    def test_public_placeholder_then_private_rerender(self):
        target = self.root / "output.env"
        target.write_text("__MIOS_PLACEHOLDER__\n", encoding="utf-8")
        target.chmod(0o644)
        seal = hashlib.sha256(target.read_bytes()).hexdigest()
        self.env["MIOS_PRIVATE_TEST_TEMPLATE_SHA"] = seal
        result = self.ps(r'''
$p=Join-Path $env:MIOS_PRIVATE_TEST_DIR 'output.env'
Write-MiOSPrivateText -Path $p -Text 'fixture-first-value' -PublicTemplateSha256 $env:MIOS_PRIVATE_TEST_TEMPLATE_SHA
Write-MiOSPrivateText -Path $p -Text 'fixture-revised-value' -PublicTemplateSha256 $env:MIOS_PRIVATE_TEST_TEMPLATE_SHA
''')
        self.assertEqual(result.returncode, 0, result.stderr[-700:])
        self.assertEqual(target.read_text(encoding="utf-8"), "fixture-revised-value")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_unknown_public_file_refuses_without_replacement(self):
        target = self.root / "output.env"
        target.write_text("fixture-existing-public", encoding="utf-8")
        target.chmod(0o644)
        before = target.read_bytes(), target.stat().st_ino, stat.S_IMODE(target.stat().st_mode)
        self.env["MIOS_PRIVATE_TEST_TEMPLATE_SHA"] = hashlib.sha256(b"__MIOS_PLACEHOLDER__\n").hexdigest()
        result = self.ps(r'''
Write-MiOSPrivateText -Path (Join-Path $env:MIOS_PRIVATE_TEST_DIR 'output.env') -Text 'fixture-new-value' -PublicTemplateSha256 $env:MIOS_PRIVATE_TEST_TEMPLATE_SHA
''')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((target.read_bytes(), target.stat().st_ino, stat.S_IMODE(target.stat().st_mode)), before)
        self.assertEqual(list(self.root.glob(".mios-private-*")), [])

    def test_complete_private_projection_replaces_complex_fork_plane(self):
        (self.root / "vendor.toml").write_text(
            '[identity]\nusername="fixture-fork-user"\ndefault_password="""fixture-multiline\nvalue"""\n'
            '[autounattend]\nautologon_enable=false\naccounts=[{name="fixture-fork-account", password="fixture-fork-value"}]\n'
            '[autounattend.service]\nenable=false\n'
            '[branding]\noem_manufacturer="fixture-base-brand"\n'
            '[editions.fixture.identity]\nusername="fixture-edition-user"\ndefault_password="fixture-edition-value"\n'
            '[editions.fixture.autounattend]\nautologon_enable=false\nautologon_user="fixture-edition-user"\nautologon_persistent=false\nautologon_count=2\n'
            '[editions.fixture.autounattend.service]\nenable=true\nsvc_user="fixture-edition-service"\nsvc_password="fixture-edition-service-value"\n'
            '[editions.fixture.field.sysrescue]\nusername="fixture-edition-rescue"\npassword="fixture-edition-rescue-value"\n'
            '[editions.fixture.auth]\npassword_hash="$6$fixturesalt$fixturehashvalue"\n'
            '[editions.fixture.branding]\noem_manufacturer="fixture-edition-brand"\n', encoding="utf-8")
        projection = {"schema": "mios-resolved-credentials/1", "scalars": {
            "identity.username": "fixture-projected-user", "identity.default_password": "fixture-π-quoted-'#-value",
            "autounattend.service.enable": "false", "field.sysrescue.username": "fixture-projected-rescue",
            "field.sysrescue.password": "fixture-projected-rescue-value"}, "accounts": [], "has_accounts": False}
        path = self.root / "resolved.json"
        path.write_text(json.dumps(projection, ensure_ascii=False), encoding="utf-8")
        path.chmod(0o600)
        # A private projection owns complete credential planes across every
        # later edition call. An unrelated edition setting must still change.
        for edition in ("", "fixture"):
            with self.subTest(edition=edition or "base"):
                self.env["MIOS_PRIVATE_TEST_EDITION"] = edition
                result = self.ps(r'''
$t=Read-MiosToml -Path $env:MIOS_VENDOR_TOML -ResolvedJson (Join-Path $env:MIOS_PRIVATE_TEST_DIR 'resolved.json')
$t=Apply-MiosEdition -T $t -Edition $env:MIOS_PRIVATE_TEST_EDITION 6>$null
$c=Resolve-MiOSCredentials -Toml $t
@{ user_ok=($c.username -ceq 'fixture-projected-user'); count=$c.accounts.Count; account_ok=($c.accounts[0].name -ceq $c.username); pass_ok=($c.autologon.password -ceq $c.default_password); pass_exact=($c.default_password -ceq 'fixture-π-quoted-''#-value'); enabled=$c.autologon.enable; persistent=$c.autologon.persistent; logon_count=$c.autologon.count; service_disabled=(-not $c.service.enable); rescue_user_ok=((Get-Toml $t 'field.sysrescue.username') -ceq 'fixture-projected-rescue'); rescue_pass_ok=((Get-Toml $t 'field.sysrescue.password') -ceq 'fixture-projected-rescue-value'); hash_absent=(-not $t.scalars.ContainsKey('auth.password_hash')); brand=(Get-Toml $t 'branding.oem_manufacturer') } | ConvertTo-Json -Compress
''')
                self.assertEqual(result.returncode, 0, result.stderr[-700:])
                self.assertEqual(json.loads(result.stdout), {
                    "user_ok": True, "count": 1, "account_ok": True, "pass_ok": True,
                    "pass_exact": True, "enabled": True, "persistent": True,
                    "logon_count": 9999999, "service_disabled": True, "rescue_user_ok": True,
                    "rescue_pass_ok": True, "hash_absent": True,
                    "brand": "fixture-edition-brand" if edition else "fixture-base-brand"})
                self.assertNotIn(projection["scalars"]["identity.default_password"],
                                 result.stdout + result.stderr)
        without = self.ps("Read-MiosToml -Path $env:MIOS_VENDOR_TOML | Out-Null")
        self.assertNotEqual(without.returncode, 0, "complex credential syntax must refuse without a valid projection")
        self.assertNotIn(projection["scalars"]["identity.default_password"], result.stdout + result.stderr)

    def test_branding_renderer_never_runs_host_install_operations(self):
        (self.root / "vendor.toml").write_text('[branding.windows]\nenable=true\n[branding]\nliving_wallpaper=true\nliving_wallpaper_mode="shader"\n', encoding="utf-8")
        result = self.ps(r'''
function global:New-Item { throw 'forbidden-host-write' }
function global:Invoke-WebRequest { throw 'forbidden-host-download' }
function global:Expand-Archive { throw 'forbidden-host-extract' }
function global:Copy-Item { throw 'forbidden-host-copy' }
function global:Start-Process { throw 'forbidden-host-process' }
$t=Read-MiosToml -Path $env:MIOS_VENDOR_TOML -SingleFile
$c=@(New-MiOSBrandingCommands -Toml $t)
@{ count=$c.Count; commands_only=(@($c | Where-Object { $_ -isnot [string] }).Count -eq 0) } | ConvertTo-Json -Compress
''')
        self.assertEqual(result.returncode, 0, result.stderr[-700:])
        observed = json.loads(result.stdout)
        self.assertTrue(observed["commands_only"])
        self.assertGreater(observed["count"], 0)
        self.assertNotIn("forbidden-host-", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
