#!/usr/bin/env python3
# AI-hint: Native provider-mock unit controls for the actual Windows installer login resolver and stdin hash handoff; never run the installer or a Windows provisioning command.
# AI-related: build-mios.ps1, tests/test-credential-ssot.py
"""Extract two actual functions with the PowerShell AST and test their boundaries.

These are Linux MiOS-DEV unit fixtures, not Windows installation acceptance.
The only executables named wsl.exe and podman resolve to private fixture files.
No real account, distro, machine, OpenSSL process or credential is accessed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(os.environ.get("MIOS_CRED_TEST_ROOT") or Path(__file__).resolve().parents[1])
PWSH = shutil.which("pwsh")
FIXTURE_HASH = "$6$fixturesalt$" + "A" * 86  # Format fixture, never a real password hash.
FIXTURE_PASSWORD = "fixture-stdin-\u0394\u03a9-'-$-`-password"

PS_FIXTURE = r"""
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$tree = [System.Management.Automation.Language.Parser]::ParseFile($env:MIOS_LOGIN_UNIT_SOURCE, [ref]$tokens, [ref]$errors)
if ($errors.Count -ne 0) { throw 'installer-source-parse-error' }
foreach ($name in @('Resolve-MiosLoginCredential', 'Get-PasswordHash')) {
    $found = @($tree.FindAll({ param($n)
        $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name
    }, $true))
    if ($found.Count -ne 1) { throw 'installer-function-not-unique' }
    . ([ScriptBlock]::Create($found[0].Extent.Text))
}
$script:Fixture = ConvertFrom-Json ([IO.File]::ReadAllText($env:MIOS_LOGIN_UNIT_INPUT))
function Resolve-MiosTomlLayers {
    foreach ($layer in $script:Fixture.layers) { [pscustomobject]@{ Path = $layer.path } }
}
function Get-MiosTomlValue {
    param($Section, $Key, $Default, $SourcePath)
    foreach ($layer in $script:Fixture.layers) {
        if ($layer.path -eq $SourcePath) {
            $property = $layer.values.PSObject.Properties[($Section + '.' + $Key)]
            if ($null -ne $property -and $property.Value) { return [string]$property.Value }
            return $Default
        }
    }
    return $Default
}
$script:BuilderDistro = 'MiOS-fixture-primary'
$script:LegacyDistro = 'MiOS-fixture-legacy'
$OutputEncoding = [Text.Encoding]::ASCII
try {
    if ($script:Fixture.action -eq 'resolve') {
        $value = Resolve-MiosLoginCredential
    } else {
        $value = Get-PasswordHash -Plain $script:Fixture.plain
    }
    @{ ok = $true; value = $value; encoding_after = $OutputEncoding.CodePage } | ConvertTo-Json -Depth 8 -Compress
} catch {
    @{ ok = $false; error = $_.Exception.Message; encoding_after = $OutputEncoding.CodePage } | ConvertTo-Json -Depth 8 -Compress
}
"""

PROVIDER_FIXTURE = r"""
import json
import os
from pathlib import Path
import sys

provider = Path(sys.argv[0]).name
incoming = sys.stdin.buffer.read()
row = {"provider": provider, "argv": sys.argv[1:], "stdin": incoming.decode("utf-8")}
with open(os.environ["MIOS_LOGIN_UNIT_LOG"], "a", encoding="utf-8") as output:
    output.write(json.dumps(row, ensure_ascii=False) + "\n")
mode = os.environ["MIOS_LOGIN_UNIT_MODE"]
if mode == "fail" or (mode == "wsl-fail" and provider == "wsl.exe"):
    raise SystemExit(17)
print(os.environ["MIOS_LOGIN_UNIT_HASH"])
"""


@unittest.skipUnless(os.name == "posix", "Linux MiOS-DEV is required for executable provider fixtures")
@unittest.skipUnless(PWSH, "pwsh is required for Native installer-function unit controls")
class TestWindowsLoginCredential(unittest.TestCase):
    def _run(self, action: str, *, layers=(), plain=None, mode="success"):
        with tempfile.TemporaryDirectory(prefix="mios-login-unit-") as directory:
            work = Path(directory)
            commands = work / "bin"
            commands.mkdir(mode=0o700)
            provider = "#!" + sys.executable + "\n" + textwrap.dedent(PROVIDER_FIXTURE)
            for name in ("wsl.exe", "podman"):
                path = commands / name
                path.write_text(provider, encoding="utf-8")
                path.chmod(0o700)
            request = work / "request.json"
            request.write_text(json.dumps({"action": action, "layers": layers, "plain": plain}), encoding="utf-8")
            script = work / "unit.ps1"
            script.write_text(PS_FIXTURE, encoding="utf-8")
            log = work / "provider.jsonl"
            env = {k: v for k, v in os.environ.items() if not k.startswith("MIOS_")}
            env.update({
                "PATH": str(commands) + os.pathsep + env.get("PATH", ""),
                "MIOS_LOGIN_UNIT_SOURCE": str(ROOT / "build-mios.ps1"),
                "MIOS_LOGIN_UNIT_INPUT": str(request),
                "MIOS_LOGIN_UNIT_LOG": str(log),
                "MIOS_LOGIN_UNIT_MODE": mode,
                "MIOS_LOGIN_UNIT_HASH": FIXTURE_HASH,
            })
            result = subprocess.run(
                [PWSH, "-NoProfile", "-NonInteractive", "-File", str(script)],
                capture_output=True, text=True, env=env, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-600:])
            reply = json.loads(result.stdout)
            calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
            return reply, calls

    def _assert_private_stdin(self, calls):
        self.assertTrue(calls)
        for call in calls:
            self.assertEqual(call["stdin"].rstrip("\r\n"), FIXTURE_PASSWORD)
            self.assertIn("-stdin", call["argv"])
            self.assertNotIn(FIXTURE_PASSWORD, " ".join(call["argv"]))
            self.assertNotIn("run", call["argv"])
            self.assertNotIn("apk", call["argv"])

    def test_layered_resolution_keeps_operator_priority_and_explicit_hash(self):
        for values, expected in (
            ({"identity.default_password": FIXTURE_PASSWORD}, {"Hash": "", "Plain": FIXTURE_PASSWORD}),
            ({"auth.password_hash": FIXTURE_HASH}, {"Hash": FIXTURE_HASH, "Plain": ""}),
        ):
            with self.subTest(keys=tuple(values)):
                reply, calls = self._run("resolve", layers=[
                    {"path": "operator", "values": values},
                    {"path": "vendor", "values": {"auth.password_hash": FIXTURE_HASH}},
                ])
                self.assertTrue(reply["ok"], reply)
                self.assertEqual(reply["value"], expected)
                self.assertEqual(calls, [])

    def test_missing_invalid_and_control_credentials_never_fall_back(self):
        cases = [
            ([], "no login credential in any mios.toml layer"),
            ([{"path": "operator", "values": {}}], "no login credential in any mios.toml layer"),
            ([{"path": "operator", "values": {"auth.password_hash": "not-a-hash"}}], "auth.password_hash"),
            ([{"path": "operator", "values": {"auth.password_hash": "$6$truncated"}}], "auth.password_hash"),
            ([{"path": "operator", "values": {"identity.default_password": "fixture-refused\ncredential"}}], "control characters"),
        ]
        for layers, error in cases:
            with self.subTest(error=error, layers=len(layers)):
                if layers and layers[0]["values"]:
                    layers = layers + [{"path": "vendor", "values": {"identity.default_password": FIXTURE_PASSWORD}}]
                reply, calls = self._run("resolve", layers=layers)
                self.assertFalse(reply["ok"], reply)
                self.assertIn(error, reply["error"])
                self.assertNotIn(FIXTURE_PASSWORD, reply["error"])
                self.assertNotIn("fixture-refused", reply["error"])
                self.assertEqual(calls, [])

    def test_wsl_hashing_uses_unicode_stdin_and_restores_encoding(self):
        reply, calls = self._run("hash", plain=FIXTURE_PASSWORD)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["value"], FIXTURE_HASH)
        self.assertEqual(reply["encoding_after"], 20127)
        self.assertEqual([c["provider"] for c in calls], ["wsl.exe"])
        self._assert_private_stdin(calls)

    def test_existing_machine_fallback_uses_the_same_private_stdin(self):
        reply, calls = self._run("hash", plain=FIXTURE_PASSWORD, mode="wsl-fail")
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["value"], FIXTURE_HASH)
        self.assertEqual(reply["encoding_after"], 20127)
        self.assertEqual([c["provider"] for c in calls], ["wsl.exe", "wsl.exe", "podman"])
        self.assertEqual(calls[-1]["argv"][:3], ["machine", "ssh", "MiOS-fixture-primary"])
        self._assert_private_stdin(calls)

    def test_invalid_plaintext_never_invokes_hash_providers(self):
        for plain in (None, "", "fixture-refused\ncredential", "fixture-refused\x00credential"):
            with self.subTest(kind="empty" if not plain else "control"):
                reply, calls = self._run("hash", plain=plain)
                self.assertFalse(reply["ok"], reply)
                self.assertIn("identity.default_password", reply["error"])
                self.assertNotIn("fixture-refused", reply["error"])
                self.assertEqual(calls, [])

    def test_provider_failure_has_no_default_or_unrelated_image_fallback(self):
        reply, calls = self._run("hash", plain=FIXTURE_PASSWORD, mode="fail")
        self.assertFalse(reply["ok"], reply)
        self.assertIn("no credential was substituted", reply["error"])
        self.assertNotIn(FIXTURE_PASSWORD, reply["error"])
        self.assertEqual(reply["encoding_after"], 20127)
        self.assertEqual([c["provider"] for c in calls], ["wsl.exe", "wsl.exe", "podman"])
        self._assert_private_stdin(calls)


if __name__ == "__main__":
    unittest.main()
