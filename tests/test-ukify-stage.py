#!/usr/bin/env python3
# AI-hint: Automated unit test suite for A/B UKI staging and systemd-ukify compilation pipeline (task_451).
# AI-doc: usr/share/doc/mios/manual/ch08-bootloader-and-unified-kernel-images-uki.md
from __future__ import annotations

import glob
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_UKIFY_STAGE_BIN = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-ukify-stage")
_UKIFY_STAGE_CMD = [sys.executable, _UKIFY_STAGE_BIN] if sys.platform == "win32" else [_UKIFY_STAGE_BIN]


class TestUkifyStage(unittest.TestCase):
    """Validates A/B UKI staging and systemd-boot entry generation."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_binary_exists(self):
        self.assertTrue(os.path.isfile(_UKIFY_STAGE_BIN), f"Missing {_UKIFY_STAGE_BIN}")
        self.assertTrue(os.access(_UKIFY_STAGE_BIN, os.X_OK) or sys.platform == "win32", f"Not executable: {_UKIFY_STAGE_BIN}")

    def test_dry_run_json(self):
        res = subprocess.run(
            [*_UKIFY_STAGE_CMD, "--dry-run", "--json", "--root", _ROOT],  # the repo tree, not the host
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "success")
        self.assertTrue(data.get("dry_run"))
        self.assertIn("baked_kargs", data)
        with open(os.path.join(_ROOT, "usr", "lib", "kernel", "cmdline"), encoding="utf-8") as fh:
            self.assertEqual(data["baked_kargs"], fh.read().strip())
        self.assertIn("console=tty0", data["baked_kargs"])

    # The tool finds `ukify` on PATH only (no env override), so a test steers it the
    # way an operator would: by what is on PATH.
    def _fake_ukify_dir(self) -> str:
        """An explicit FAKE compiler: writes a marker to --output and exits 0."""
        bindir = os.path.join(self.tmpdir.name, "fakebin")
        os.makedirs(bindir, exist_ok=True)
        script = os.path.join(bindir, "ukify")
        with open(script, "w", encoding="utf-8") as f:
            f.write(
                f"#!{sys.executable}\n"
                "import sys\n"
                "assert sys.argv[1] == 'build', sys.argv\n"
                "for arg in sys.argv[2:]:\n"
                "    if arg.startswith('--output='):\n"
                "        with open(arg.split('=', 1)[1], 'w', encoding='utf-8') as fh:\n"
                "            fh.write('MZ-FAKE-UKIFY\\n')\n"
                "sys.exit(0)\n"
            )
        os.chmod(script, 0o755)
        if sys.platform == "win32":
            with open(os.path.join(bindir, "ukify.cmd"), "w", encoding="utf-8") as f:
                f.write(f'@"{sys.executable}" "%~dp0ukify" %*\n')
        return bindir

    def _run_stage(self, path: str, out_efi: str, entry: str, kernel: str, initrd: str, cmdline: str,
                   *extra: str, want_rc: int = 0):
        env = os.environ.copy()
        env["PATH"] = path
        res = subprocess.run(
            [
                sys.executable, _UKIFY_STAGE_BIN,
                "--root", _ROOT,
                "--loader-entry", entry,
                "--output", out_efi,
                "--kernel", kernel,
                "--initrd", initrd,
                "--cmdline", cmdline,
                "--json",
                *extra,
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(res.returncode, want_rc, f"stage exit {res.returncode}: {res.stdout}{res.stderr}")
        return json.loads(res.stdout)

    def _mock_inputs(self, tag: str):
        kernel = os.path.join(self.tmpdir.name, f"vmlinuz-{tag}")
        initrd = os.path.join(self.tmpdir.name, f"initrd-{tag}.img")
        with open(kernel, "w") as f:
            f.write("mock-vmlinuz\n")
        with open(initrd, "w") as f:
            f.write("mock-initrd\n")
        return kernel, initrd

    def test_stage_execution(self):
        out_efi = os.path.join(self.tmpdir.name, "boot", "EFI", "Linux", "mios-next.efi")
        mock_kernel, mock_initrd = self._mock_inputs("test")
        fake_path = self._fake_ukify_dir() + os.pathsep + os.environ.get("PATH", "")

        entry = os.path.join(self.tmpdir.name, "loader", "entries", "mios-next.conf")
        host_entry = "/boot/loader/entries/mios-next.conf"
        host_before = os.stat(host_entry).st_mtime_ns if os.path.exists(host_entry) else None
        data = self._run_stage(fake_path, out_efi, entry, mock_kernel, mock_initrd,
                               "console=tty0 root=UUID=123 rw")
        self.assertEqual(data.get("status"), "success")
        self.assertTrue(data.get("ukify_executed"))
        self.assertTrue(os.path.isfile(out_efi), f"Missing staged EFI at {out_efi}")
        self.assertTrue(os.path.isfile(entry), f"Missing loader entry at {entry}")
        host_after = os.stat(host_entry).st_mtime_ns if os.path.exists(host_entry) else None
        self.assertEqual(host_before, host_after, "the stage test wrote the HOST loader entry")

        with open(out_efi, "rb") as f:
            hdr = f.read(32)
        self.assertIn(b"MZ-FAKE-UKIFY", hdr)

    def _no_ukify_path(self) -> str:
        no_ukify = os.path.join(self.tmpdir.name, "empty-bin")
        os.makedirs(no_ukify, exist_ok=True)
        return no_ukify

    def test_stage_execution_simulate_is_explicit(self):
        out_efi = os.path.join(self.tmpdir.name, "boot", "EFI", "Linux", "mios-sim.efi")
        mock_kernel, mock_initrd = self._mock_inputs("sim")
        entry = os.path.join(self.tmpdir.name, "loader", "entries", "mios-sim.conf")
        data = self._run_stage(self._no_ukify_path(), out_efi, entry, mock_kernel, mock_initrd,
                               "console=tty0 rw", "--simulate")
        self.assertEqual(data.get("status"), "success")
        self.assertTrue(data.get("simulated"))
        self.assertFalse(data.get("ukify_executed"))
        with open(out_efi, "rb") as f:
            hdr = f.read(32)
        self.assertIn(b"MZ-SIMULATED-UKI", hdr)

    def test_negative_missing_ukify_is_an_error(self):
        """Pre-fix, no ukify on PATH staged a SIMULATED UKI as success, with a loader entry."""
        out_efi = os.path.join(self.tmpdir.name, "boot", "EFI", "Linux", "mios-none.efi")
        mock_kernel, mock_initrd = self._mock_inputs("none")
        entry = os.path.join(self.tmpdir.name, "loader", "entries", "mios-none.conf")
        data = self._run_stage(self._no_ukify_path(), out_efi, entry, mock_kernel, mock_initrd,
                               "console=tty0 rw", want_rc=1)
        self.assertEqual(data.get("status"), "error")
        self.assertIn("ukify not found", data.get("error", ""))
        self.assertFalse(os.path.exists(out_efi), "a placeholder EFI was staged")
        self.assertFalse(os.path.exists(entry), "a loader entry points at no real UKI")

    def test_negative_unrunnable_ukify_is_an_error(self):
        """A ukify that is on PATH but cannot execute is an error result, not a traceback."""
        bad = os.path.join(self.tmpdir.name, "badbin")
        os.makedirs(bad)
        with open(os.path.join(bad, "ukify"), "wb") as fh:
            fh.write(b"\x00\x01not-an-executable")
        os.chmod(os.path.join(bad, "ukify"), 0o755)
        out_efi = os.path.join(self.tmpdir.name, "boot", "EFI", "Linux", "mios-bad.efi")
        mock_kernel, mock_initrd = self._mock_inputs("bad")
        entry = os.path.join(self.tmpdir.name, "loader", "entries", "mios-bad.conf")
        data = self._run_stage(bad + os.pathsep + self._no_ukify_path(), out_efi, entry, mock_kernel,
                               mock_initrd, "console=tty0 rw", want_rc=1)
        self.assertEqual(data.get("status"), "error")
        self.assertIn("ukify could not run", data.get("error", ""))
        self.assertFalse(os.path.exists(out_efi), "a placeholder EFI was staged")

    def test_negative_missing_kernel_is_an_error(self):
        out_efi = os.path.join(self.tmpdir.name, "boot", "EFI", "Linux", "mios-nok.efi")
        _, mock_initrd = self._mock_inputs("nok")
        entry = os.path.join(self.tmpdir.name, "loader", "entries", "mios-nok.conf")
        data = self._run_stage(self._fake_ukify_dir(), out_efi, entry,
                               os.path.join(self.tmpdir.name, "no-such-vmlinuz"), mock_initrd,
                               "console=tty0 rw", want_rc=1)
        self.assertIn("kernel/initrd not found", data.get("error", ""))
        self.assertFalse(os.path.exists(entry))

    def test_stage_ignores_removed_env_override(self):
        """No environment variable may force the simulated UKI when ukify is on PATH."""
        out_efi = os.path.join(self.tmpdir.name, "boot", "EFI", "Linux", "mios-env.efi")
        mock_kernel, mock_initrd = self._mock_inputs("env")
        entry = os.path.join(self.tmpdir.name, "loader", "entries", "mios-env.conf")
        fake_path = self._fake_ukify_dir() + os.pathsep + os.environ.get("PATH", "")
        os.environ["MIOS_UKIFY_BIN"] = ""
        try:
            data = self._run_stage(fake_path, out_efi, entry, mock_kernel, mock_initrd, "console=tty0 rw")
        finally:
            del os.environ["MIOS_UKIFY_BIN"]
        self.assertTrue(data.get("ukify_executed"), "an env override forced the simulated UKI")
        with open(out_efi, "rb") as f:
            self.assertIn(b"MZ-FAKE-UKIFY", f.read(32))

    @staticmethod
    def _host_kernel():
        cands = sorted(glob.glob("/usr/lib/modules/*/vmlinuz"), reverse=True)
        cands += sorted(glob.glob("/lib/modules/*/vmlinuz"), reverse=True)
        cands += sorted(glob.glob("/boot/vmlinuz-*"), reverse=True)
        for cand in cands:
            if os.path.isfile(cand) and os.access(cand, os.R_OK):
                return cand
        return None

    @staticmethod
    def _pe_sections(blob: bytes):
        """Parse a PE/COFF section table -> {name: raw bytes}."""
        if len(blob) < 0x40 or blob[:2] != b"MZ":
            raise AssertionError(f"no DOS/MZ header ({len(blob)} bytes): {blob[:16]!r}")
        pe = struct.unpack_from("<I", blob, 0x3C)[0]
        if len(blob) < pe + 24 or blob[pe:pe + 4] != b"PE\0\0":
            raise AssertionError("no PE signature")
        nsect = struct.unpack_from("<H", blob, pe + 6)[0]
        opt_size = struct.unpack_from("<H", blob, pe + 20)[0]
        table = pe + 24 + opt_size
        out = {}
        for i in range(nsect):
            off = table + 40 * i
            name = blob[off:off + 8].rstrip(b"\0").decode("ascii", "replace")
            raw_size, raw_ptr = struct.unpack_from("<II", blob, off + 16)
            out[name] = blob[raw_ptr:raw_ptr + raw_size]
        return out

    def test_real_ukify_validation_on_suitable_inputs(self):
        """The REAL compiler, on a real kernel, through the stage tool."""
        real_ukify = shutil.which("ukify")
        if not real_ukify:
            self.skipTest("ukify not installed on host")
        kernel = self._host_kernel()
        if not kernel:
            self.skipTest("no readable kernel image (vmlinuz) on host")
        if not glob.glob("/usr/lib/systemd/boot/efi/linux*.efi.stub"):
            self.skipTest("ukify present but no systemd-boot EFI stub installed")

        out_efi = os.path.join(self.tmpdir.name, "real", "mios-real.efi")
        initrd = os.path.join(self.tmpdir.name, "initrd-real.img")
        with open(initrd, "wb") as f:
            f.write(b"\0" * 512)
        entry = os.path.join(self.tmpdir.name, "loader", "entries", "mios-real.conf")
        cmdline = "console=tty0 mios.uki.test=1 rw"
        data = self._run_stage(os.environ.get("PATH", ""), out_efi, entry, kernel, initrd, cmdline)
        self.assertEqual(data.get("status"), "success", data)
        self.assertTrue(data.get("ukify_executed"), data)

        with open(out_efi, "rb") as f:
            blob = f.read()
        sections = self._pe_sections(blob)
        self.assertIn(".linux", sections, f"sections: {sorted(sections)}")
        self.assertGreater(len(sections[".linux"]), 0)
        self.assertIn(".cmdline", sections, f"sections: {sorted(sections)}")
        self.assertIn(cmdline.encode(), sections[".cmdline"])

    def test_pe_parser_rejects_non_pe(self):
        """The PE check above must be able to fail: a simulated UKI is not a PE."""
        with self.assertRaises(AssertionError):
            self._pe_sections(b"MZ-SIMULATED-UKI\nKERNEL=x\n" + b"\0" * 64)
        with self.assertRaises(AssertionError):
            self._pe_sections(b"\x7fELF" + b"\0" * 64)


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestUkifyStage)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        return 1
    # A skipped real-compiler tier is exit 77, never a pass: run-suites.sh fails
    # it unless [ci.tool_skips] registers it ([ci.fedora] provides ukify instead).
    return 77 if result.skipped else 0


if __name__ == "__main__":
    sys.exit(main())
