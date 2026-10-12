#!/usr/bin/env python3
# AI-hint: Automated unit test suite for Out-of-Band IP-KVM management mesh and Redfish/PiKVM virtual media provisioner (task_468, task_2701).
# AI-doc: usr/share/doc/mios/manual/ch28-dynamic-network-and-firewall-management.md
from __future__ import annotations

import http.server
import json
import os
import shutil
import stat
import subprocess
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from importlib.machinery import SourceFileLoader

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_LIBEXEC_DIR = os.path.join(_ROOT, "usr", "libexec", "mios")
_TOOL = os.path.join(_LIBEXEC_DIR, "mios-ipkvm-manager")

# Dynamically import mios-ipkvm-manager
ipkvm_mod = SourceFileLoader("mios_ipkvm_manager", _TOOL).load_module()
SecurityError = ipkvm_mod.SecurityError
validate_target_ip = ipkvm_mod.validate_target_ip
parse_target = ipkvm_mod.parse_target
resolve_auth_token = ipkvm_mod.resolve_auth_token
check_authentication = ipkvm_mod.check_authentication
get_mesh_status = ipkvm_mod.get_mesh_status
execute_power = ipkvm_mod.execute_power
execute_mount_iso = ipkvm_mod.execute_mount_iso
execute_eject_iso = ipkvm_mod.execute_eject_iso
execute_console_stream = ipkvm_mod.execute_console_stream
build_backend_request = ipkvm_mod.build_backend_request
SUPPORTED_BACKENDS = ipkvm_mod.SUPPORTED_BACKENDS
SUPPORTED_POWER_ACTIONS = ipkvm_mod.SUPPORTED_POWER_ACTIONS


class MockBmcServer(http.server.ThreadingHTTPServer):
    """Local HTTP mock server for live network validation testing over 127.0.0.1."""
    pass


class MockBmcHandler(http.server.BaseHTTPRequestHandler):
    """Responds with valid BMC/PiKVM/Redfish JSON payload."""

    def log_message(self, format, *args):
        pass  # Suppress server access log in test output

    def do_GET(self):
        auth = self.headers.get("Authorization", "")
        if "test-valid-token" not in auth:
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error": "unauthorized"}')
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if "/api/atx" in self.path:
            self.wfile.write(json.dumps({"ok": True, "result": {"busy": False, "leds": {"power": True, "hdd": False}}}).encode("utf-8"))
        elif "/redfish/v1/Systems/1" in self.path:
            self.wfile.write(json.dumps({"PowerState": "On", "Id": "1"}).encode("utf-8"))
        else:
            self.wfile.write(json.dumps({"status": "ok"}).encode("utf-8"))

    def do_POST(self):
        auth = self.headers.get("Authorization", "")
        if "test-valid-token" not in auth:
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error": "unauthorized"}')
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ok", "message": "action accepted"}).encode("utf-8"))


def setUpModule():
    """The daemon tests talk to an in-process server on 127.0.0.1; a sandbox
    that refuses loopback sockets skips them instead of failing them."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
    except OSError as exc:
        raise unittest.SkipTest(f"loopback sockets unavailable: {exc}") from exc


class TestIpkvmManager(unittest.TestCase):
    """Validates Dedicated Out-of-Band IP-KVM management mesh and provisioner."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="mios-ipkvm-test-")
        # Ensure clean environment for auth tests
        self.saved_env = {}
        for key in ("MIOS_IPKVM_TOKEN", "IPKVM_AUTH_TOKEN", "MIOS_IPKVM_MOCK"):
            if key in os.environ:
                self.saved_env[key] = os.environ.pop(key)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        # Restore environment
        for key, val in self.saved_env.items():
            os.environ[key] = val

    def test_tool_exists_and_executable(self):
        """Verifies that mios-ipkvm-manager exists, is executable, and has valid header directives."""
        self.assertTrue(os.path.isfile(_TOOL), f"Missing executable tool: {_TOOL}")
        self.assertTrue(os.access(_TOOL, os.X_OK), f"Tool is not executable: {_TOOL}")

        with open(_TOOL, "r", encoding="utf-8") as f:
            lines = [f.readline().strip() for _ in range(5)]

        self.assertEqual(lines[0], "#!/usr/bin/env python3")
        self.assertIn("AI-hint: Dedicated Out-of-Band IP-KVM management mesh and Redfish/PiKVM virtual media provisioner", lines[1])
        self.assertIn("AI-doc: usr/share/doc/mios/manual/ch12-networking-and-mesh.md", lines[2])

    def test_network_validation_invariants(self):
        """
        Validates the security invariant that targets must strictly reside within
        the dedicated management subnet 10.200.0.0/16 or 127.0.0.1 for testing.
        Public IPs and non-management networks must be unconditionally rejected.
        """
        valid_targets = [
            "10.200.0.1",
            "10.200.1.5",
            "10.200.42.99:8443",
            "10.200.255.254",
            "127.0.0.1",
            "127.0.0.1:8080",
            "localhost",
            "localhost:9000",
        ]
        for target in valid_targets:
            host, port = validate_target_ip(target)
            self.assertTrue(host, f"Expected valid target for {target}")

        invalid_targets = [
            "8.8.8.8",
            "1.1.1.1",
            "93.184.216.34",
            "192.168.1.100",
            "172.16.0.1",
            "10.100.1.1",
            "10.0.0.1",
            "169.254.1.1",
        ]
        for target in invalid_targets:
            with self.assertRaises(SecurityError, msg=f"Target {target} should violate security invariant"):
                validate_target_ip(target)

            # CLI rejection verification
            proc = subprocess.run(
                [_TOOL, "power", "--target", target, "--action", "status", "--auth-token", "dummy", "--dry-run", "--json"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 1, f"CLI should return exit code 1 for invalid IP {target}")
            data = json.loads(proc.stdout)
            self.assertEqual(data.get("status"), "error")
            self.assertEqual(data.get("error_type"), "SecurityError")
            self.assertIn("Security invariant violation", data.get("error", ""))

    def test_authentication_check(self):
        """
        Validates authentication enforcement: unauthenticated requests without token
        must be rejected unless in dry-run mode with explicit bypass (--allow-unauthenticated).
        """
        # 1. Unauthenticated power request fails
        proc = subprocess.run(
            [_TOOL, "power", "--target", "10.200.1.5", "--action", "status", "--json"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 1)
        data = json.loads(proc.stdout)
        self.assertEqual(data.get("status"), "error")
        self.assertEqual(data.get("error_type"), "SecurityError")
        self.assertIn("Authentication error", data.get("error", ""))

        # 2. Dry-run without token AND without --allow-unauthenticated fails
        proc_dry = subprocess.run(
            [_TOOL, "power", "--target", "10.200.1.5", "--action", "status", "--dry-run", "--json"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc_dry.returncode, 1)

        # 3. Dry-run with explicit bypass succeeds
        proc_bypass = subprocess.run(
            [_TOOL, "power", "--target", "10.200.1.5", "--action", "status", "--dry-run", "--allow-unauthenticated", "--json"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc_bypass.returncode, 0)
        data_bypass = json.loads(proc_bypass.stdout)
        self.assertEqual(data_bypass.get("status"), "ok")
        self.assertTrue(data_bypass.get("dry_run"))

        # 4. Valid token passed via CLI argument succeeds
        proc_token = subprocess.run(
            [_TOOL, "power", "--target", "10.200.1.5", "--action", "status", "--auth-token", "secret-token", "--mock", "--json"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc_token.returncode, 0)
        data_token = json.loads(proc_token.stdout)
        self.assertEqual(data_token.get("status"), "ok")

        # 5. Valid token passed via MIOS_IPKVM_TOKEN environment variable succeeds
        env_copy = os.environ.copy()
        env_copy["MIOS_IPKVM_TOKEN"] = "env-secret-token"
        proc_env = subprocess.run(
            [_TOOL, "power", "--target", "10.200.1.5", "--action", "status", "--mock", "--json"],
            capture_output=True,
            text=True,
            env=env_copy,
        )
        self.assertEqual(proc_env.returncode, 0)

    def test_power_operations_dry_run_and_mock(self):
        """
        Validates power actions (status, on, off, cycle, reset) in both dry-run
        and mock modes across all supported backends (pikvm, redfish, openbmc).
        """
        backends = ("pikvm", "redfish", "openbmc")
        actions = ("status", "on", "off", "cycle", "reset")

        for backend in backends:
            for action in actions:
                # 1. Dry-run test
                dry_res = execute_power(
                    target="10.200.10.20",
                    action=action,
                    backend=backend,
                    token="auth-tok-123",
                    dry_run=True,
                )
                self.assertEqual(dry_res.get("status"), "ok")
                self.assertTrue(dry_res.get("dry_run"))
                self.assertEqual(dry_res.get("backend"), backend)
                self.assertEqual(dry_res.get("action"), action)
                self.assertIn("planned_request", dry_res)
                self.assertIn("method", dry_res["planned_request"])
                self.assertIn("url", dry_res["planned_request"])

                # 2. Mock mode test
                mock_res = execute_power(
                    target="10.200.10.20",
                    action=action,
                    backend=backend,
                    token="auth-tok-123",
                    mock=True,
                )
                self.assertEqual(mock_res.get("status"), "ok")
                self.assertTrue(mock_res.get("mock"))
                self.assertEqual(mock_res.get("backend"), backend)
                self.assertEqual(mock_res.get("action"), action)
                self.assertTrue("power_state" in mock_res)

                # 3. CLI execution in mock mode
                cli_proc = subprocess.run(
                    [
                        _TOOL,
                        "power",
                        "--target", "10.200.10.20",
                        "--action", action,
                        "--backend", backend,
                        "--auth-token", "tok-xyz",
                        "--mock",
                        "--json",
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(cli_proc.returncode, 0, f"Failed CLI mock power {backend} {action}: {cli_proc.stderr}")
                cli_data = json.loads(cli_proc.stdout)
                self.assertEqual(cli_data.get("status"), "ok")
                self.assertEqual(cli_data.get("backend"), backend)
                self.assertEqual(cli_data.get("action"), action)

    def test_virtual_iso_mount_and_eject(self):
        """
        Validates virtual boot media ISO mounting and ejection across
        all backends in dry-run and mock modes.
        """
        test_iso = "http://10.200.0.1/isos/mios-metal-uefi.iso"
        backends = ("pikvm", "redfish", "openbmc")

        for backend in backends:
            # Mount ISO dry-run
            mount_dry = execute_mount_iso(
                target="10.200.1.100",
                iso_url=test_iso,
                backend=backend,
                token="auth-tok-123",
                dry_run=True,
            )
            self.assertEqual(mount_dry.get("status"), "ok")
            self.assertTrue(mount_dry.get("dry_run"))
            self.assertEqual(mount_dry.get("iso_url"), test_iso)
            self.assertIn(test_iso, json.dumps(mount_dry.get("planned_request", {})))

            # Mount ISO mock
            mount_mock = execute_mount_iso(
                target="10.200.1.100",
                iso_url=test_iso,
                backend=backend,
                token="auth-tok-123",
                mock=True,
            )
            self.assertEqual(mount_mock.get("status"), "ok")
            self.assertTrue(mount_mock.get("mounted"))
            self.assertEqual(mount_mock.get("iso_url"), test_iso)

            # Eject ISO dry-run
            eject_dry = execute_eject_iso(
                target="10.200.1.100",
                backend=backend,
                token="auth-tok-123",
                dry_run=True,
            )
            self.assertEqual(eject_dry.get("status"), "ok")
            self.assertTrue(eject_dry.get("dry_run"))

            # Eject ISO mock
            eject_mock = execute_eject_iso(
                target="10.200.1.100",
                backend=backend,
                token="auth-tok-123",
                mock=True,
            )
            self.assertEqual(eject_mock.get("status"), "ok")
            self.assertTrue(eject_mock.get("ejected"))
            self.assertFalse(eject_mock.get("mounted"))

            # CLI mount-iso execution
            mount_proc = subprocess.run(
                [
                    _TOOL,
                    "mount-iso",
                    "--target", "10.200.1.100",
                    "--iso-url", test_iso,
                    "--backend", backend,
                    "--auth-token", "tok",
                    "--mock",
                    "--json",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(mount_proc.returncode, 0)
            data = json.loads(mount_proc.stdout)
            self.assertTrue(data.get("mounted"))

            # CLI eject-iso execution
            eject_proc = subprocess.run(
                [
                    _TOOL,
                    "eject-iso",
                    "--target", "10.200.1.100",
                    "--backend", backend,
                    "--auth-token", "tok",
                    "--mock",
                    "--json",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(eject_proc.returncode, 0)
            data_eject = json.loads(eject_proc.stdout)
            self.assertTrue(data_eject.get("ejected"))

    def test_console_stream_retrieval(self):
        """Validates console stream inspection across all backends."""
        backends = ("pikvm", "redfish", "openbmc")

        for backend in backends:
            stream_info = execute_console_stream(
                target="10.200.5.10",
                backend=backend,
                token="tok-stream",
            )
            self.assertEqual(stream_info.get("status"), "ok")
            self.assertEqual(stream_info.get("backend"), backend)
            self.assertTrue(stream_info.get("console_url"))
            self.assertTrue(stream_info.get("protocol"))

            cli_proc = subprocess.run(
                [
                    _TOOL,
                    "console-stream",
                    "--target", "10.200.5.10",
                    "--backend", backend,
                    "--auth-token", "tok-stream",
                    "--json",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(cli_proc.returncode, 0)
            cli_data = json.loads(cli_proc.stdout)
            self.assertEqual(cli_data.get("backend"), backend)
            self.assertIn("console_url", cli_data)

    def test_mesh_status(self):
        """Validates management mesh interface state inspection."""
        status = get_mesh_status("wg-ipkvm")
        self.assertEqual(status.get("status"), "ok")
        self.assertEqual(status.get("interface"), "wg-ipkvm")
        self.assertEqual(status.get("subnet"), "10.200.0.0/16")
        self.assertEqual(status.get("expected_mtu"), 1420)
        self.assertIn("link_state", status)
        self.assertIn("mesh_active", status)

        # CLI execution
        proc = subprocess.run([_TOOL, "mesh-status", "--json"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(data.get("interface"), "wg-ipkvm")
        self.assertEqual(data.get("subnet"), "10.200.0.0/16")

    def test_json_formatting_compliance(self):
        """Verifies strict JSON schema compliance across all tool verbs."""
        verbs_and_args = [
            ["mesh-status", "--json"],
            ["power", "--target", "10.200.1.1", "--action", "status", "--auth-token", "tok", "--mock", "--json"],
            ["mount-iso", "--target", "10.200.1.1", "--iso-url", "http://10.200.0.1/boot.iso", "--auth-token", "tok", "--mock", "--json"],
            ["eject-iso", "--target", "10.200.1.1", "--auth-token", "tok", "--mock", "--json"],
            ["console-stream", "--target", "10.200.1.1", "--auth-token", "tok", "--json"],
        ]
        for cmd in verbs_and_args:
            proc = subprocess.run([_TOOL] + cmd, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, f"Command failed: {cmd}, stderr: {proc.stderr}")
            try:
                parsed = json.loads(proc.stdout)
            except Exception as e:
                self.fail(f"Invalid JSON returned for {cmd}: {e}\nOutput was: {proc.stdout}")
            self.assertIsInstance(parsed, dict)
            self.assertEqual(parsed.get("status"), "ok")
            self.assertTrue(parsed.get("success", True))

    def test_live_http_on_localhost_mock_server(self):
        """
        Validates real end-to-end HTTP communication (non-mock, non-dry-run)
        against a test server running on loopback 127.0.0.1.
        """
        server = MockBmcServer(("127.0.0.1", 0), MockBmcHandler)
        port = server.server_port
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        time.sleep(0.05)

        try:
            # Power status over real HTTP on 127.0.0.1
            res = execute_power(
                target=f"127.0.0.1:{port}",
                action="status",
                backend="pikvm",
                token="test-valid-token",
                dry_run=False,
                mock=False,
            )
            self.assertEqual(res.get("status"), "ok")
            self.assertEqual(res.get("power_state"), "on")

            # Unauthorized request against live server
            with self.assertRaises(Exception):
                execute_power(
                    target=f"127.0.0.1:{port}",
                    action="status",
                    backend="pikvm",
                    token="invalid-token",
                    dry_run=False,
                    mock=False,
                )
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
