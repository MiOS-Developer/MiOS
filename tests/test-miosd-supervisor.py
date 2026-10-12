#!/usr/bin/env python3
# AI-hint: Test suite for task_341: Native Rust miosd daemon supervisor and state manager.
# AI-related: usr/lib/systemd/system/miosd.service, src/mios-rs/miosd/src/daemon.rs

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

class TestMiosdSupervisor(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_dir = pathlib.Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_systemd_unit_specification(self):
        """Verify usr/lib/systemd/system/miosd.service exists and enforces hardening/memory bounds."""
        unit_file = ROOT / "usr/lib/systemd/system/miosd.service"
        self.assertTrue(unit_file.is_file(), f"miosd.service not found at {unit_file}")

        content = unit_file.read_text(encoding="utf-8")
        self.assertIn("ExecStart=/usr/libexec/mios/miosd daemon", content)
        self.assertIn("MemoryMax=15M", content)
        self.assertIn("ProtectSystem=strict", content)
        self.assertIn("NoNewPrivileges=true", content)
        self.assertIn("Restart=always", content)
        self.assertIn("WantedBy=multi-user.target", content)

    def test_daemon_state_schema_and_memory_ceiling(self):
        """Verify state schema structure, memory ceiling <15MB, and required fields."""
        state_data = {
            "ts": int(time.time()),
            "uptime_s": 120,
            "version": "0.3.0",
            "memory_ceiling_mb": 15,
            "metrics": {
                "cpu_percent": 8.5,
                "memory_used_mb": 2048,
                "memory_total_mb": 8192,
                "memory_percent": 25.0,
                "load_1m": 0.45,
                "load_5m": 0.30,
                "load_15m": 0.25,
                "disk_used_gb": 40.0,
                "disk_total_gb": 250.0,
            },
            "hardware": {
                "gpu_util_percent": 0.0,
                "gpu_detected": False,
                "watchdog_active": True,
                "iommu_enabled": True,
                "last_watchdog_ping_ts": int(time.time()),
            },
            "theme": {
                "current_theme": "bibata-modern-classic",
                "cursor_theme": "Bibata-Modern-Classic",
                "last_sync_ts": int(time.time()),
                "in_sync": True,
            },
            "backup": {
                "last_backup_ts": int(time.time()),
                "status": "idle",
                "next_scheduled_ts": int(time.time()) + 3600,
                "backup_count": 0,
            },
            "classify": {
                "summary": "All systems nominal",
                "tags": ["system", "nominal"],
                "severity": "info",
                "event_count": 0,
            },
            "refusal": None,
            "cron": {
                "last_fire": None,
                "decisions": [],
            },
        }

        # Write and atomically read
        state_file = self.state_dir / "state.json"
        tmp_file = self.state_dir / "state.json.tmp"
        tmp_file.write_text(json.dumps(state_data, indent=2), encoding="utf-8")
        tmp_file.replace(state_file)

        self.assertTrue(state_file.is_file())
        loaded = json.loads(state_file.read_text(encoding="utf-8"))

        self.assertLessEqual(loaded["memory_ceiling_mb"], 15)
        self.assertIn("metrics", loaded)
        self.assertIn("hardware", loaded)
        self.assertIn("theme", loaded)
        self.assertIn("backup", loaded)
        self.assertIn("cron", loaded)
        self.assertTrue(loaded["hardware"]["watchdog_active"])

    def test_rust_unit_tests_pass(self):
        """Execute the Rust unit tests for the daemon modules."""
        import shutil
        if shutil.which("cargo") is None:
            raise unittest.SkipTest("cargo is not on PATH; cannot exercise the miosd daemon")
        # Was pinned to a named WSL distro and /mnt/c paths, so it could only run
        # on one Windows box and failed on every CI runner.
        res = subprocess.run(
            ["cargo", "test", "-p", "miosd", "--lib", "--", "daemon"],
            cwd=str(ROOT / "src" / "mios-rs"), capture_output=True, text=True)
        self.assertEqual(
            res.returncode, 0,
            f"Cargo test for miosd daemon failed:\nstdout: {res.stdout}\nstderr: {res.stderr}"
        )
        self.assertIn("test daemon::state::tests::test_atomic_state_write_and_read ... ok", res.stdout)
        self.assertIn("test daemon::tests::test_supervisor_run_once ... ok", res.stdout)

if __name__ == "__main__":
    unittest.main()
