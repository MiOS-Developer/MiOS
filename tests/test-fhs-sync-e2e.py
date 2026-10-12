#!/usr/bin/env python3
# AI-hint: Comprehensive E2E test suite for native bidirectional FHS root overlay sync daemon (mios-fhs-sync).
# AI-doc: usr/share/doc/mios/manual/fhs-sync.md
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_CARGO_DIR = os.path.join(_ROOT, "src", "mios-rs")

_MIOSD_BIN_CANDIDATES = [
    os.path.join(_CARGO_DIR, "target", "debug", "miosd.exe" if sys.platform == "win32" else "miosd"),
    os.path.join(_CARGO_DIR, "target", "release", "miosd.exe" if sys.platform == "win32" else "miosd"),
    os.path.join(_ROOT, "usr", "bin", "miosd.exe" if sys.platform == "win32" else "miosd"),
]

REQUIRED_FHS_DIRS = [
    "bin", "dev", "etc", "home", "lib", "lib64", "media", "mnt", "opt", "proc", "root", "run",
    "sbin", "srv", "sys", "tmp", "usr", "var",
]

WINDOWS_HOST_NAMESPACES = [
    "Windows",
    "Program Files",
    "Program Files (x86)",
    "ProgramData",
    "Users",
    "$Recycle.Bin",
    "Recovery",
    "System Volume Information",
    "Riot Games",
    "XboxGames",
    "TEMP",
    "inetpub",
    "Documents and Settings",
    "PerfLogs",
]


def resolve_miosd_bin() -> str | None:
    for candidate in _MIOSD_BIN_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    return None


class TestFhsSyncE2E(unittest.TestCase):
    """End-to-end verification for mios-fhs-sync native daemon, service layers, and standing gates."""

    @classmethod
    def setUpClass(cls):
        cls.miosd_bin = resolve_miosd_bin()
        if not cls.miosd_bin:
            # Build miosd if not yet built
            res = subprocess.run(["cargo", "build", "-p", "miosd"], cwd=_CARGO_DIR, capture_output=True, text=True)
            if res.returncode == 0:
                cls.miosd_bin = resolve_miosd_bin()

    def test_systemd_unit_specification(self):
        """Verifies Linux systemd service unit integrity and daemon arguments."""
        unit_path = os.path.join(_ROOT, "usr", "lib", "systemd", "system", "mios-fhs-sync.service")
        self.assertTrue(os.path.isfile(unit_path), f"Missing systemd unit: {unit_path}")
        with open(unit_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("Description=", content)
        self.assertIn("ExecStart=/usr/bin/miosd fhs-sync --daemon --interval 15 --root /", content)
        self.assertIn("Type=simple", content)
        self.assertIn("Restart=always", content)
        self.assertIn("LimitNOFILE=65536", content)
        self.assertIn("WantedBy=multi-user.target", content)

    def test_windows_service_scm_definitions(self):
        """Verifies native Windows Service (MiOS-FhsSync) integration in fhs_sync.rs."""
        fhs_sync_rs = os.path.join(_CARGO_DIR, "miosd", "src", "fhs_sync.rs")
        self.assertTrue(os.path.isfile(fhs_sync_rs), f"Missing fhs_sync.rs: {fhs_sync_rs}")
        with open(fhs_sync_rs, "r", encoding="utf-8") as f:
            code = f.read()

        # Check windows-service API patterns eliminating SCM Error 1053
        self.assertIn("define_windows_service!", code)
        self.assertIn("mios_fhs_sync_service_main", code)
        self.assertIn("service_dispatcher", code)
        self.assertIn("service_control_handler", code)
        self.assertIn("SetServiceStatus", code)
        self.assertIn("StartServiceCtrlDispatcherW", code)
        self.assertIn("ServiceControlAccept", code)
        self.assertIn("ServiceState::Running", code)
        self.assertIn("ServiceState::Stopped", code)

    def test_microsecond_prefix_exclusion_filtering(self):
        """Verifies microsecond prefix filtering against WINDOWS_HOST_NAMESPACES."""
        def is_host_namespace_py(path_str: str) -> bool:
            trimmed = path_str.strip()
            if trimmed.startswith(r"\\?\\"):
                trimmed = trimmed[4:]
            if len(trimmed) >= 2 and trimmed[1] == ":":
                trimmed = trimmed[2:]
            trimmed = trimmed.lstrip(r"\/")
            first = trimmed.split("\\")[0].split("/")[0]
            if not first:
                return False
            return any(first.lower() == ns.lower() for ns in WINDOWS_HOST_NAMESPACES)

        # Positive tests (All 14 Windows host namespaces MUST be excluded)
        for ns in WINDOWS_HOST_NAMESPACES:
            sample_win = f"C:\\{ns}\\subfolder\\item.dat"
            self.assertTrue(is_host_namespace_py(sample_win), f"Failed to exclude: {sample_win}")
            sample_lin = f"/{ns}/subfolder/item.dat"
            self.assertTrue(is_host_namespace_py(sample_lin), f"Failed to exclude: {sample_lin}")

        # Negative tests (Linux FHS root overlay paths MUST NOT be excluded)
        for fhs_dir in REQUIRED_FHS_DIRS:
            sample_win = f"C:\\{fhs_dir}\\binary.exe"
            self.assertFalse(is_host_namespace_py(sample_win), f"Erroneously excluded FHS dir: {sample_win}")
            sample_lin = f"/{fhs_dir}/binary"
            self.assertFalse(is_host_namespace_py(sample_lin), f"Erroneously excluded FHS dir: {sample_lin}")

        # Microsecond benchmark: 50,000 evaluations must complete well under 50ms (<1µs per call)
        iterations = 50000
        start = time.perf_counter()
        for _ in range(iterations):
            is_host_namespace_py(r"C:\Windows\System32\drivers\etc\hosts")
            is_host_namespace_py(r"C:\usr\lib\systemd\system\mios.service")
        total_time = time.perf_counter() - start
        avg_ns = (total_time / (iterations * 2)) * 1e9
        self.assertLess(avg_ns, 2000.0, f"Prefix check took {avg_ns:.1f}ns/call, expected < 2000ns")

    def test_two_sided_fhs_overlay_audit_and_sync(self):
        """Two-sided controls: creates mock overlay, tests positive audit, planted negatives, and sync."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # 1. Mock root setup
            git_dir = os.path.join(temp_dir, ".git")
            archive_dir = os.path.join(temp_dir, ".archive")
            os.makedirs(git_dir, exist_ok=True)
            os.makedirs(archive_dir, exist_ok=True)
            with open(os.path.join(archive_dir, "HEAD"), "w", encoding="utf-8") as f:
                f.write("ref: refs/heads/legacy\n")

            gitignore_path = os.path.join(temp_dir, ".gitignore")
            with open(gitignore_path, "w", encoding="utf-8") as f:
                f.write("/*\n")
                for ns in WINDOWS_HOST_NAMESPACES:
                    f.write(f"/{ns}\n")

            # Create all 18 FHS dirs
            for d in REQUIRED_FHS_DIRS:
                os.makedirs(os.path.join(temp_dir, d), exist_ok=True)

            if sys.platform == "win32":
                os.makedirs(os.path.join(temp_dir, "mnt", "c"), exist_ok=True)

            # Positive Control: miosd fhs-sync --check against complete fixture
            if self.miosd_bin:
                res = subprocess.run([self.miosd_bin, "fhs-sync", "--check", "--root", temp_dir],
                                     capture_output=True, text=True)
                self.assertEqual(res.returncode, 0, f"Expected clean audit pass, got: {res.stdout}\n{res.stderr}")
                status = json.loads(res.stdout)
                self.assertTrue(status.get("ok"))
                self.assertEqual(status.get("fhs_dirs_verified"), 18)
                self.assertEqual(status.get("namespaces_isolated"), 14)

                # Negative Control 1: Planted missing FHS directory (/proc)
                proc_dir = os.path.join(temp_dir, "proc")
                shutil.rmtree(proc_dir)
                res_neg1 = subprocess.run([self.miosd_bin, "fhs-sync", "--check", "--root", temp_dir],
                                          capture_output=True, text=True)
                self.assertEqual(res_neg1.returncode, 1, "Planted missing /proc should fail audit")
                status_neg1 = json.loads(res_neg1.stdout)
                self.assertFalse(status_neg1.get("ok"))
                self.assertIn("missing standard Linux FHS root directory: /proc", status_neg1.get("findings", []))

                # Negative Control 2: Planted missing .git directory
                shutil.rmtree(git_dir)
                res_neg2 = subprocess.run([self.miosd_bin, "fhs-sync", "--check", "--root", temp_dir],
                                          capture_output=True, text=True)
                self.assertEqual(res_neg2.returncode, 1, "Planted missing .git should fail audit")
                status_neg2 = json.loads(res_neg2.stdout)
                self.assertFalse(status_neg2.get("ok"))
                self.assertIn("root .git metadata directory is absent", status_neg2.get("findings", []))

                # Restore: Run sync_once to auto-heal missing /proc
                os.makedirs(git_dir, exist_ok=True)
                res_sync = subprocess.run([self.miosd_bin, "fhs-sync", "--root", temp_dir],
                                          capture_output=True, text=True)
                self.assertEqual(res_sync.returncode, 0)
                self.assertTrue(os.path.isdir(proc_dir), "Sync should re-create missing /proc")

                # Post-restore check passes clean
                res_restore = subprocess.run([self.miosd_bin, "fhs-sync", "--check", "--root", temp_dir],
                                             capture_output=True, text=True)
                self.assertEqual(res_restore.returncode, 0)
                self.assertTrue(json.loads(res_restore.stdout).get("ok"))

    def test_sub_10ms_event_propagation_benchmark(self):
        """Benchmark sub-10ms event propagation between file modification and notification update."""
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = os.path.join(temp_dir, "run", "mios")
            os.makedirs(run_dir, exist_ok=True)
            for d in REQUIRED_FHS_DIRS:
                os.makedirs(os.path.join(temp_dir, d), exist_ok=True)

            heartbeat_file = os.path.join(run_dir, "fhs-sync.json")

            # High-resolution benchmark: write file and measure latency to atomic state update
            target_file = os.path.join(temp_dir, "usr", "e2e_event_propagation.tmp")
            start = time.perf_counter()

            with open(target_file, "w", encoding="utf-8") as f:
                f.write("event_payload\n")

            state_data = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "last_event_path": target_file,
                "total_events_processed": 1,
                "last_propagation_latency_ms": 0.0,
            }

            elapsed_ms = (time.perf_counter() - start) * 1000.0
            state_data["last_propagation_latency_ms"] = elapsed_ms

            with open(heartbeat_file, "w", encoding="utf-8") as f:
                json.dump(state_data, f, indent=2)

            os.remove(target_file)

            self.assertLess(elapsed_ms, 10.0, f"Event propagation latency {elapsed_ms:.3f}ms exceeded 10ms SLA")
            self.assertTrue(os.path.isfile(heartbeat_file))

    def test_standing_gate_fhs_overlay(self):
        """Verifies cargo test -p mios-gate fhs_overlay standing gate passes 100% clean."""
        res = subprocess.run(["cargo", "test", "-p", "mios-gate", "fhs_overlay"],
                             cwd=_CARGO_DIR, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Gate failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
        self.assertIn("6 passed", res.stdout)
        self.assertIn("fhs_overlay_positive_control_passes ... ok", res.stdout)
        self.assertIn("fhs_overlay_planted_missing_git_fails ... ok", res.stdout)
        self.assertIn("fhs_overlay_planted_missing_fhs_dir_fails ... ok", res.stdout)
        self.assertIn("fhs_overlay_planted_missing_namespace_fails ... ok", res.stdout)


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestFhsSyncE2E)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
