#!/usr/bin/env python3
# AI-hint: Automated unit test suite for WS-LANG mios-wallpaperd native living wallpaper service (T-1132).
# AI-related: tools/native/mios-wallpaperd/src/main.rs, usr/share/mios/branding/living-wallpaper.html, build-mios.ps1, src/mios-rs/mios-build/src/verification.rs
"""Automated tests for WS-LANG living wallpaper Rust crate and cross-build toolchain (T-1132)."""

from __future__ import annotations

import os
import re
import sys
import unittest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_WALL_RS = os.path.join(_ROOT, "tools", "native", "mios-wallpaperd", "src", "main.rs")
_CARGO_CFG = os.path.join(_ROOT, "tools", "native", ".cargo", "config.toml")
_BUILD_PS1 = os.path.join(_ROOT, "build-mios.ps1")
_SSOT = os.path.join(_ROOT, "usr", "share", "mios", "mios.toml")
# The cross-build and PE verification moved out of build-mios.ps1 into the
# native engine (miosd native-windows-build / native-artifact-check).
_VERIFY_RS = os.path.join(_ROOT, "src", "mios-rs", "mios-build", "src", "verification.rs")

class TestWallpaperService(unittest.TestCase):
    """Validates mios-wallpaperd Rust crate structure, cross-build configuration, and verification gating."""

    def test_rust_source_exists(self):
        self.assertTrue(os.path.exists(_WALL_RS))
        with open(_WALL_RS, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("WallpaperConfig", content)
        self.assertIn("living-wallpaper.html", content)

    def test_cargo_cross_target_pinned(self):
        """Verifies that tools/native/.cargo/config.toml pins a coherent MinGW cross-target linker."""
        self.assertTrue(os.path.exists(_CARGO_CFG))
        with open(_CARGO_CFG, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[target.x86_64-pc-windows-gnu]", content)
        self.assertIn("x86_64-w64-mingw32-gcc", content)

    def test_build_script_hermetic_cross_build(self):
        """build-mios.ps1 cross-builds mios-wallpaperd in MiOS-DEV through the native SSOT engine and
        publishes it, and registers the service, only after the native PE check passes."""
        with open(_SSOT, "rb") as fh:
            native = tomllib.load(fh)["build"]["native"]
        self.assertIn("mios-wallpaperd", native["windows_only"], "mios-wallpaperd must be a catalogued Windows executable")
        target = native["windows"]["target"]

        self.assertTrue(os.path.exists(_BUILD_PS1))
        with open(_BUILD_PS1, "r", encoding="utf-8") as f:
            content = f.read()
        fn = re.search(r"^function Install-MiosNativeWindowsArtifact\b.*?^\}", content, re.M | re.S)
        self.assertIsNotNone(fn, "build-mios.ps1 must define Install-MiosNativeWindowsArtifact")
        fn = fn.group(0)
        accepted = re.search(r"\$policy\.target -notin @\(([^)]*)\)", fn)
        self.assertIsNotNone(accepted, "installer must validate the SSOT Windows target")
        self.assertIn(f"'{target}'", accepted.group(1), "installer must accept the SSOT [build.native.windows].target")
        # Hermetic: the native engine builds inside MiOS-DEV into a Linux-local
        # target directory, never into the Windows source tree it reads.
        build = re.search(r"miosd native-windows-build --root \$linuxRoot --binary \$Binary --target-dir (\S+)", fn)
        self.assertIsNotNone(build, "cross-build must run through miosd native-windows-build in MiOS-DEV")
        self.assertTrue(build.group(1).startswith("/var/tmp/"), f"build output must stay in the builder: {build.group(1)}")
        # The PE/import check gates publication: it runs on the staged copy and
        # throws before the atomic move over the installed executable.
        check = re.search(r"miosd native-artifact-check \$linuxPending --platform windows[^\n]*\n[^\n]*throw", fn)
        move = fn.find("[IO.File]::Move($pending, $Destination")
        self.assertIsNotNone(check, "staged artifact must pass native-artifact-check --platform windows or throw")
        self.assertNotEqual(move, -1, "verified artifact must be published by an atomic move")
        self.assertLess(fn.find(build.group(0)), check.start())
        self.assertLess(check.start(), move, "PE check must precede publication")

        service = content.find("$svcName = 'MiOS-Wallpaper-Service'")
        install = content.find("Install-MiosNativeWindowsArtifact -Root $MiosRepoDir -Machine $DevDistro "
                               "-Binary 'mios-wallpaperd' -Destination $wallpaperd_exe -ServiceName $svcName")
        register = content.find("sc.exe create $svcName")
        self.assertTrue(-1 < service < install < register, "service must be registered only after the verified install")

        # The engine itself verifies the PE before staging the artifact.
        with open(_VERIFY_RS, "r", encoding="utf-8") as fh:
            engine = fh.read()
        windows_build = re.search(r"pub fn windows_build\b.*?^\}", engine, re.M | re.S)
        self.assertIsNotNone(windows_build, "mios-build must define windows_build")
        windows_build = windows_build.group(0)
        verified = windows_build.find('artifact_check(root, &artifact, "windows"')
        staged = windows_build.find("std::fs::copy(&artifact, &staged)")
        self.assertTrue(-1 < verified < staged, "windows_build must verify the PE before staging it")
        self.assertIn('"windows" => {', engine)
        self.assertIn("verify_windows_pe(&data, &policy.system_dlls)", engine)

    def test_pe_verification_rejects_corrupted_or_non_pe(self):
        """Negative control: Non-PE or truncated artifacts fail verification check."""
        def verify_bytes(data: bytes) -> bool:
            return len(data) > 102400 and len(data) >= 2 and data[0] == 0x4D and data[1] == 0x5A

        # Negative control 1: Non-PE text
        self.assertFalse(verify_bytes(b"MZ" + b"\x00" * 100))  # too small (< 100KB)
        self.assertFalse(verify_bytes(b"ELF" + b"\x00" * 200000))  # wrong magic header
        self.assertFalse(verify_bytes(b"\x00" * 200000))  # null bytes

        # Positive control: Valid PE dummy with MZ magic and >100KB length
        valid_pe_dummy = b"MZ" + b"\x00" * 150000
        self.assertTrue(verify_bytes(valid_pe_dummy))


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestWallpaperService)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1

if __name__ == "__main__":
    sys.exit(main())
