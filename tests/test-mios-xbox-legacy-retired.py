#!/usr/bin/env python3
# AI-hint: Two-sided tests that the legacy MiOS-Xbox paths are gone or refuse (XB-1): every main-repo pipeline file and the bootstrap autounattend tree carry no legacy shell literal (TaskbarAl=0, SearchboxTaskbarMode=1, Start_ShowDownloads/Documents=1, static jpg wallpaper, stable-virtio, -ForceUnsigned, Set-MiOSCustomDefaults/UserOnce) while a planted one is caught; each bootstrap image entry (New-MiOSISO, Build-MiOSXbox[ISO], Deploy-MiOSXbox, Merge-MiOSPresets, ConvertTo-MiOSPreset, New-MiOSAutounattend -SourceIso/-OutIso, mios-build-iso.sh) refuses before doing anything; MiOS-Provision.lib.ps1's global preferences read the [windows.xbox] spec (centered taskbar, hidden search, the 4 Start folders) and refuse when the spec key is missing, and follow a changed spec value (no hardcode).
# AI-related: usr/libexec/mios/win/xbox/spec_items.py, usr/libexec/mios/win/xbox/conform.py, /var/lib/mios-build/repos/mios-bootstrap-build/field/autounattend
"""Run: python3 -I tests/test-mios-xbox-legacy-retired.py -v   (pwsh optional: its tests skip without it)"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "usr", "libexec", "mios", "win", "xbox"))
import conform as C  # noqa: E402
import spec_items as S  # noqa: E402

BS = os.environ.get("MIOS_BOOTSTRAP_TREE", "/var/lib/mios-build/repos/mios-bootstrap-build")
AU = os.path.join(BS, "field", "autounattend")
VENDOR = os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
PWSH = shutil.which("pwsh")
LEGACY_TEXT = re.compile(r"\bSet-MiOSCustomDefaults\b(?!\s+(?:\(|was removed|call removed))|UserOnce\.ps1", re.I)
ENTRIES = ("New-MiOSISO.ps1", "Build-MiOSXboxISO.ps1", "Build-MiOSXbox.ps1", "Deploy-MiOSXbox.ps1",
           "Merge-MiOSPresets.ps1", "ConvertTo-MiOSPreset.ps1")


def pipeline_files():
    out = []
    for d, exts in ((os.path.join(ROOT, "usr", "libexec", "mios", "win", "xbox"), (".py",)),
                    (os.path.join(ROOT, "usr", "share", "mios", "windows", "build"), (".ps1",)),
                    (os.path.join(ROOT, "usr", "share", "mios", "windows", "xbox"), (".ps1",)),
                    (os.path.join(ROOT, "usr", "share", "mios", "windows"), (".ps1", ".cfg"))):
        for n in sorted(os.listdir(d)):
            if n.endswith(exts) and os.path.isfile(os.path.join(d, n)):
                out.append(os.path.join(d, n))
    return out


RETIRED_MARK = "RETIRED for MiOS-Xbox images (xb-20"
DEAD_CODE_OK = {"deny.stable-virtio", "deny.ForceUnsigned", "deny.static-wallpaper"}


def scan(paths, retired_dead_code_ok=False):
    """Legacy registry values (parsed like the gate) + legacy text literals; deny-list definitions are skipped.
    With retired_dead_code_ok, a script whose FIRST statement is the xb-20 refusal (TestBootstrapRefuses proves
    it) may keep unreachable stable-virtio/ForceUnsigned/jpg text; the shell-layout literals must still be gone."""
    retired = set()
    if retired_dead_code_ok:
        for p in paths:
            with open(p, encoding="utf-8", errors="replace") as fh:
                if RETIRED_MARK in fh.read():
                    retired.add(p)
    obs = C.Observations()
    hits = []
    for p in paths:
        with open(p, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        if os.path.basename(p) in ("spec_items.py", "conform.py"):
            continue   # the gate's own deny-list patterns
        text = "\n".join("" if "pin-gate:allow" in ln else ln for ln in text.splitlines())
        if p.endswith(".xml") and C._is_answer_file(p):
            C.parse_answer_file(p, p, obs)
        else:
            C.parse_script(text, p, obs)
        for n, line in enumerate(text.splitlines(), 1):
            if "pin-gate:allow" in line:
                continue
            if LEGACY_TEXT.search(line):
                hits.append(("legacy.UserOnce/Set-MiOSCustomDefaults", f"{p}:{n}"))
    for o in obs.reg:
        for fid in S.reg_deny_hits(o.scope, o.name, o.vtype, o.value):
            hits.append((fid, o.source))
    hits += [(f.id, f.source) for f in obs.findings]
    return [h for h in hits if not (h[0] in DEAD_CODE_OK and h[1].rsplit(":", 1)[0] in retired)]


class TestNoLegacyLiterals(unittest.TestCase):
    def test_main_repo_pipeline_is_clean(self):
        self.assertEqual([], scan(pipeline_files()))

    @unittest.skipUnless(os.path.isdir(AU), "bootstrap tree not present")
    def test_bootstrap_autounattend_is_clean(self):
        files = [os.path.join(AU, n) for n in sorted(os.listdir(AU))
                 if n.endswith((".ps1", ".cmd", ".xml", ".sh")) and os.path.isfile(os.path.join(AU, n))]
        self.assertEqual([], scan(files, retired_dead_code_ok=True))

    def test_negative_planted_literals_are_caught(self):
        with tempfile.TemporaryDirectory() as t:
            planted = {
                "a.ps1": "Set-ItemProperty -LiteralPath 'Registry::HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Advanced' -Name 'TaskbarAl' -Type 'DWord' -Value 0",
                "b.cmd": 'reg add "HKU\\DefaultUser\\Software\\Microsoft\\Windows\\CurrentVersion\\Search" /v SearchboxTaskbarMode /t REG_DWORD /d 1 /f',
                "c.ps1": "function Set-MiOSCustomDefaults { param($Mount) }",
                "d.ps1": "Copy-Item mios-wallpaper.jpg C:\\Windows\\Web",
            }
            for n, body in planted.items():
                with open(os.path.join(t, n), "w") as fh:
                    fh.write(body + "\n")
            ids = {h[0] for h in scan([os.path.join(t, n) for n in planted])}
            for fid in ("deny.TaskbarAl=0", "deny.SearchboxTaskbarMode=1", "legacy.UserOnce/Set-MiOSCustomDefaults",
                        "deny.static-wallpaper"):
                self.assertIn(fid, ids)


@unittest.skipUnless(os.path.isdir(AU), "bootstrap tree not present")
class TestBootstrapRefuses(unittest.TestCase):
    def test_entry_scripts_refuse_first(self):
        if not PWSH:
            self.skipTest("pwsh not installed")
        files = json.dumps([os.path.join(AU, f) for f in ENTRIES + ("New-MiOSAutounattend.ps1",)])
        ps = r'''
$out = @{}
foreach ($f in (ConvertFrom-Json $env:FILES)) {
  $t=$null;$e=$null; $ast=[System.Management.Automation.Language.Parser]::ParseFile($f,[ref]$t,[ref]$e)
  $first = $ast.EndBlock.Statements | Select-Object -First 1
  $out[[IO.Path]::GetFileName($f)] = @{ errors = $e.Count; first = $first.Extent.Text }
}
$out | ConvertTo-Json -Depth 3'''
        r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
                           timeout=300, env={**os.environ, "FILES": files})
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        res = json.loads(r.stdout)
        for f in ENTRIES:
            self.assertEqual(res[f]["errors"], 0)
            self.assertRegex(res[f]["first"], r"^throw 'MiOS-Xbox: .*retired.*build\.py", f)
        au = res["New-MiOSAutounattend.ps1"]["first"]
        self.assertRegex(au, r"^if \(\$SourceIso -or \$OutIso\) \{ throw 'MiOS-Xbox: .*retired")

    def test_entry_script_execution_refuses(self):
        if not PWSH:
            self.skipTest("pwsh not installed")
        r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", os.path.join(AU, "Merge-MiOSPresets.ps1")],
                           capture_output=True, text=True, timeout=120)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("retired", r.stdout + r.stderr)

    def test_linux_iso_builder_refuses(self):
        r = subprocess.run(["bash", os.path.join(AU, "mios-build-iso.sh")], capture_output=True, text=True, timeout=60,
                           env={"PATH": "/usr/bin:/bin", "MIOS_ISO_WORKDIR": tempfile.gettempdir() + "/nonexistent-mios-iso"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("retired", r.stderr)


@unittest.skipUnless(os.path.isdir(AU) and PWSH, "bootstrap tree or pwsh not present")
class TestBootstrapLibReadsSpec(unittest.TestCase):
    PS = r'''
$ErrorActionPreference = 'Stop'
. (Join-Path $env:AU 'MiOS-Provision.lib.ps1')
$toml = Read-MiosToml -Path $env:VENDOR -SingleFile
if ($env:EXTRA) { Merge-MiosTomlLayer -Dst $toml -Src (ConvertFrom-MiosTomlFile -Path $env:EXTRA) }
if ($env:DROP) { foreach ($k in ($env:DROP -split ',')) { [void]$toml.scalars.Remove($k) } }
try { (New-MiOSGlobalPrefCommands -Toml $toml) -join "`n" } catch { "ERROR: $($_.Exception.Message)" }
'''

    def run_lib(self, extra=None, drop=None):
        env = {**os.environ, "AU": AU, "VENDOR": VENDOR, "EXTRA": extra or "", "DROP": drop or "",
               "MIOS_BOOTSTRAP_BASE_TOML": "-"}
        r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", self.PS], capture_output=True, text=True,
                           timeout=300, env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        return r.stdout

    def test_positive_spec_values(self):
        out = self.run_lib()
        self.assertNotIn("ERROR:", out)
        self.assertRegex(out, r'reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Advanced" /v TaskbarAl /t REG_DWORD /d 1 /f')
        self.assertRegex(out, r"/v SearchboxTaskbarMode /t REG_DWORD /d 0 /f")
        self.assertRegex(out, r"/v AllowPinnedFolderPersonalFolder /t REG_DWORD /d 1 /f")
        self.assertRegex(out, r"/v AllowPinnedFolderDownloads /t REG_DWORD /d 0 /f")
        self.assertRegex(out, r"/v AllowPinnedFolderDocuments /t REG_DWORD /d 0 /f")
        self.assertNotIn("Start_ShowDownloads", out)
        self.assertNotIn("/v TaskbarAl /t REG_DWORD /d 0", out)

    def test_negative_missing_spec_key_refuses(self):
        out = self.run_lib(drop="windows.xbox.preferences.taskbar_align")
        self.assertIn("ERROR: MiOS SSOT: spec key 'windows.xbox.preferences.taskbar_align' is missing", out)

    def test_follows_the_spec_not_a_hardcode(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as fh:
            fh.write("[windows.xbox.preferences]\nsearch_box_mode = 2\n")
        try:
            out = self.run_lib(extra=fh.name)
        finally:
            os.unlink(fh.name)
        self.assertRegex(out, r"/v SearchboxTaskbarMode /t REG_DWORD /d 2 /f")


if __name__ == "__main__":
    unittest.main()
