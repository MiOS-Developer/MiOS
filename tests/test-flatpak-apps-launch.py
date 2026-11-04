#!/usr/bin/env python3
# AI-hint: Two-sided automated test suite verifying desktop application launch reliability, EPIPE/SIGPIPE immunity across pipe closures, 10,000 stdout/stderr writes, and mios-shell restart resilience.
# AI-related: usr/libexec/mios/mios-desktop-launch, usr/share/mios/quickshell/AppLaunch.qml, usr/share/mios/mios.toml, usr/libexec/mios/mios-flatpak-run
# AI-doc: usr/share/doc/mios/manual/ch31-desktop-applications-and-flatpaks.md
# AI-functions: FlatpakAppsLaunchSuite

from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

_HERE = Path(__file__).resolve().parent
_ROOT = Path(os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT") or _HERE.parent)
_LAUNCHER = _ROOT / "usr/libexec/mios/mios-desktop-launch"
if not _LAUNCHER.is_file():
    _LAUNCHER = Path("/usr/libexec/mios/mios-desktop-launch")
_FLATPAK_RUN = _ROOT / "usr/libexec/mios/mios-flatpak-run"
if not _FLATPAK_RUN.is_file():
    _FLATPAK_RUN = Path("/usr/libexec/mios/mios-flatpak-run")
_TOML_PATH = _ROOT / "usr/share/mios/mios.toml"
_QML_PATH = _ROOT / "usr/share/mios/quickshell/AppLaunch.qml"


class FlatpakAppsLaunchSuite(unittest.TestCase):
    """Rigorous two-sided test suite for Flatpaks and Desktop Apps launching."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="mios-flatpak-test-")
        self.test_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # =========================================================================
    # NEGATIVE CONTROLS: Proves that unisolated pipes cause EPIPE / SIGPIPE
    # =========================================================================

    def test_negative_control_closed_pipe_triggers_sigpipe_or_broken_pipe(self) -> None:
        """Negative Control: Proves that writes to a closed pipe trigger BrokenPipeError or SIGPIPE."""
        reader_fd, writer_fd = os.pipe()
        os.close(reader_fd)  # Close read end: pipe now has ZERO readers

        code = (
            "import sys\n"
            "try:\n"
            "    sys.stdout.write('test\\n')\n"
            "    sys.stdout.flush()\n"
            "    sys.exit(0)\n"
            "except BrokenPipeError:\n"
            "    sys.exit(32)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", code],
            stdout=writer_fd,
            stderr=subprocess.PIPE,
        )
        os.close(writer_fd)
        _, stderr = proc.communicate(timeout=5)

        # In Python, writes to closed pipes raise BrokenPipeError (exit 32),
        # unhandled flush on shutdown in Python 3.14 (exit 120),
        # or if default SIGPIPE handler is restored, termination by SIGPIPE (-13 / 141)
        self.assertTrue(
            proc.returncode in (32, 120, 141, -signal.SIGPIPE) or b"BrokenPipeError" in stderr,
            f"Expected broken pipe exit code (32 or 120 or 141 or -SIGPIPE), got: {proc.returncode}, stderr: {stderr}",
        )

    def test_negative_control_node_epipe_reproduction(self) -> None:
        """Negative Control: If Node.js is available, reproduces Discord's exact Error: write EPIPE."""
        node_bin = shutil.which("node")
        if not node_bin:
            self.skipTest("node binary not installed in test environment")

        r_fd, w_fd = os.pipe()
        os.close(r_fd)
        node_script = 'const net = require("net"); const s = new net.Socket({ fd: 1, readable: false, writable: true }); s.write("boom\\n");'
        res = subprocess.run([node_bin, "-e", node_script], stdout=w_fd, stderr=subprocess.PIPE, text=True)
        os.close(w_fd)
        has_epipe = "EPIPE" in res.stderr or "write EPIPE" in res.stdout or res.returncode != 0
        self.assertTrue(has_epipe, f"Negative control expected EPIPE failure, output: {res.stdout}\n{res.stderr}")

    # =========================================================================
    # POSITIVE CONTROLS: Desktop Launcher Decoupling, Stdio Isolation & 10,000 Writes
    # =========================================================================

    def test_positive_control_launcher_exists_and_executable(self) -> None:
        """Positive Control: Verifies mios-desktop-launch script exists and is executable."""
        self.assertTrue(_LAUNCHER.exists(), f"Missing launcher script: {_LAUNCHER}")
        self.assertTrue(os.access(_LAUNCHER, os.X_OK), f"Launcher not executable: {_LAUNCHER}")

    def test_positive_control_desktop_launcher_stdio_isolation(self) -> None:
        """Positive Control: Proves desktop apps launched via mios-desktop-launch survive

        parent pipe closure and 10,000 writes to stdout/stderr without EPIPE or SIGPIPE."""
        receipt_file = self.test_path / "receipt.txt"
        probe_script = self.test_path / "probe.py"
        probe_script.write_text(
            "import os, sys, time\n"
            "# Perform 10,000 sequential writes to stdout and stderr\n"
            "for i in range(10000):\n"
            "    sys.stdout.write(f'stdout line {i}\\n')\n"
            "    sys.stderr.write(f'stderr line {i}\\n')\n"
            "    if i % 2500 == 0:\n"
            "        sys.stdout.flush()\n"
            "        sys.stderr.flush()\n"
            "sys.stdout.flush()\n"
            "sys.stderr.flush()\n"
            f"with open({str(receipt_file)!r}, 'w') as f:\n"
            "    f.write('COMPLETED_10000_WRITES\\n')\n"
            "sys.exit(0)\n"
        )
        probe_script.chmod(0o755)

        desktop_file = self.test_path / "probe.desktop"
        desktop_file.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=ProbeApp\n"
            f"Exec={sys.executable} {probe_script}\n"
        )

        # Launch via mios-desktop-launch
        cmd = [str(_LAUNCHER), "launch", str(desktop_file)]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        self.assertEqual(res.returncode, 0, f"Launcher failed: {res.stdout}\n{res.stderr}")

        # Wait for receipt file from probe app
        deadline = time.time() + 10.0
        while time.time() < deadline and not receipt_file.exists():
            time.sleep(0.05)

        self.assertTrue(receipt_file.exists(), "Probe app did not complete 10,000 writes or crashed with EPIPE/SIGPIPE")
        self.assertIn("COMPLETED_10000_WRITES", receipt_file.read_text())

    def test_positive_control_shell_restart_simulation(self) -> None:
        """Positive Control: Simulates mios-shell.service restart while app is running.

        Verifies that when the launching shell process terminates, the launched application
        continues running in its detached session and finishes all 10,000 writes without error."""
        receipt_file = self.test_path / "restart_receipt.txt"
        probe_script = self.test_path / "restart_probe.py"
        probe_script.write_text(
            "import os, sys, time\n"
            "time.sleep(0.2)  # Wait for simulating parent shell termination\n"
            "for i in range(10000):\n"
            "    sys.stdout.write(f'restart stdout line {i}\\n')\n"
            "    sys.stderr.write(f'restart stderr line {i}\\n')\n"
            "sys.stdout.flush()\n"
            "sys.stderr.flush()\n"
            f"with open({str(receipt_file)!r}, 'w') as f:\n"
            "    f.write('SURVIVED_SHELL_RESTART\\n')\n"
            "sys.exit(0)\n"
        )
        probe_script.chmod(0o755)

        desktop_file = self.test_path / "restart_probe.desktop"
        desktop_file.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=RestartProbeApp\n"
            f"Exec={sys.executable} {probe_script}\n"
        )

        # Run launching process that simulates quickshell and then immediately terminates
        launch_runner = (
            f"import subprocess, sys\n"
            f"proc = subprocess.Popen([{str(_LAUNCHER)!r}, 'launch', {str(desktop_file)!r}])\n"
            f"proc.wait(timeout=5)\n"
            f"sys.exit(proc.returncode)\n"
        )
        parent_proc = subprocess.run([sys.executable, "-c", launch_runner], capture_output=True, text=True, timeout=10)
        self.assertEqual(parent_proc.returncode, 0, f"Parent launcher failed: {parent_proc.stderr}")

        # The parent process has now exited (simulating shell reload/restart).
        # Probe app must continue running and complete writes.
        deadline = time.time() + 10.0
        while time.time() < deadline and not receipt_file.exists():
            time.sleep(0.05)

        self.assertTrue(receipt_file.exists(), "App failed to survive parent shell termination / restart")
        self.assertIn("SURVIVED_SHELL_RESTART", receipt_file.read_text())

    def test_positive_control_mios_env_stripping(self) -> None:
        """Positive Control: Verifies that mios-desktop-launch strips MIOS_* variables

        while preserving non-MIOS variables to prevent bubblewrap argument overflow."""
        probe_script = self.test_path / "env_probe.py"
        probe_env_out = self.test_path / "env_out.txt"
        probe_script.write_text(
            "import os, sys\n"
            "leaked = [k for k in os.environ if k.startswith('MIOS_')]\n"
            "preserved = os.environ.get('KEEP_ME_NON_MIOS')\n"
            f"with open({str(probe_env_out)!r}, 'w') as f:\n"
            "    f.write(f'LEAKED:{len(leaked)}\\n')\n"
            "    f.write(f'PRESERVED:{preserved}\\n')\n"
            "sys.exit(0)\n"
        )
        probe_script.chmod(0o755)

        desktop_file = self.test_path / "env_probe.desktop"
        desktop_file.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=EnvProbe\n"
            f"Exec={sys.executable} {probe_script}\n"
        )

        # Inject fake MIOS_* variables into environment
        fake_env = dict(os.environ)
        for i in range(100):
            fake_env[f"MIOS_TEST_INJECT_{i}"] = f"val_{i}"
        fake_env["KEEP_ME_NON_MIOS"] = "intact_value"

        res = subprocess.run([str(_LAUNCHER), "launch", str(desktop_file)], env=fake_env, capture_output=True, text=True, timeout=10)
        self.assertEqual(res.returncode, 0, f"Launcher failed: {res.stderr}")

        deadline = time.time() + 10.0
        while time.time() < deadline and not probe_env_out.exists():
            time.sleep(0.05)

        self.assertTrue(probe_env_out.exists(), "Environment probe did not write output")
        content = probe_env_out.read_text()
        self.assertIn("LEAKED:0", content, f"Leaked MIOS_* variables detected: {content}")
        self.assertIn("PRESERVED:intact_value", content, "Non-MIOS variable was erroneously dropped")

    def test_positive_control_exit_code_propagation(self) -> None:
        """Positive Control: Verifies that mios-desktop-launch propagates non-zero exit codes

        when launching fails or desktop file does not exist."""
        non_existent = self.test_path / "does_not_exist.desktop"
        res = subprocess.run([str(_LAUNCHER), "launch", str(non_existent)], capture_output=True, text=True, timeout=10)
        self.assertNotEqual(res.returncode, 0, "Launcher returned 0 for non-existent desktop file")

    def test_positive_control_ssot_configuration(self) -> None:
        """Positive Control: Verifies mios.toml and AppLaunch.qml SSOT configuration."""
        self.assertTrue(_TOML_PATH.is_file(), f"Missing SSOT: {_TOML_PATH}")
        with open(_TOML_PATH, "rb") as f:
            data = tomllib.load(f)

        launch_cfg = data.get("desktop", {}).get("launch", {})
        self.assertEqual(
            launch_cfg.get("desktop_launcher_program"),
            "/usr/libexec/mios/mios-desktop-launch",
            "desktop_launcher_program in mios.toml must be /usr/libexec/mios/mios-desktop-launch",
        )

        # Verify AppLaunch.qml contains decoupled dispatchFactory
        self.assertTrue(_QML_PATH.is_file(), f"Missing QML: {_QML_PATH}")
        qml_text = _QML_PATH.read_text()
        self.assertIn("dispatchFactory", qml_text, "AppLaunch.qml must have dispatchFactory component")
        self.assertIn("dispatchJob.running = true", qml_text, "AppLaunch.qml must dispatch using decoupled job")

    def test_positive_control_installed_flatpaks_and_apps(self) -> None:
        """Positive Control: Validates the shipped desktop application catalog without assuming host-installed apps."""
        # Audit the actual shipped catalog in this checkout. The lean CI image
        # need not install a desktop or any particular third-party application.
        desktop_candidates = sorted((_ROOT / "usr/share/applications").glob("*.desktop"))
        checked = 0
        for cand in desktop_candidates:
            if cand.is_file():
                checked += 1
                text = cand.read_text()
                self.assertIn("[Desktop Entry]", text, f"Invalid desktop entry header in {cand}")
                self.assertIn("Exec=", text, f"Missing Exec in {cand}")

        # At least one system desktop file was verified
        self.assertGreater(checked, 0, "No shipped desktop files found to audit")


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(FlatpakAppsLaunchSuite)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
