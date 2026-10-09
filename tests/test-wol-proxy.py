#!/usr/bin/env python3
# AI-hint: Automated unit test suite for signed proxy WoL with SecureON payload and peer wake daemon (task_467, task_2700).
# AI-doc: usr/share/doc/mios/manual/ch28-dynamic-network-and-firewall-management.md
from __future__ import annotations

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
_TOOL = os.path.join(_LIBEXEC_DIR, "mios-wol-proxy")
_SERVICE = os.path.join(_ROOT, "usr", "lib", "systemd", "system", "mios-wol-proxy.service")

# Dynamically import mios-wol-proxy
wol_proxy_mod = SourceFileLoader("mios_wol_proxy", _TOOL).load_module()
build_magic_packet = wol_proxy_mod.build_magic_packet
verify_packet = wol_proxy_mod.verify_packet
parse_mac = wol_proxy_mod.parse_mac
parse_secureon = wol_proxy_mod.parse_secureon
validate_interface = wol_proxy_mod.validate_interface
validate_destination_ip = wol_proxy_mod.validate_destination_ip
SecurityError = wol_proxy_mod.SecurityError
WolProxyServer = wol_proxy_mod.WolProxyServer
WolProxyRequestHandler = wol_proxy_mod.WolProxyRequestHandler


def setUpModule():
    """The daemon tests talk to an in-process server on 127.0.0.1; a sandbox
    that refuses loopback sockets skips them instead of failing them."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
    except OSError as exc:
        raise unittest.SkipTest(f"loopback sockets unavailable: {exc}") from exc


class TestWolProxy(unittest.TestCase):
    """Validates Signed Proxy WoL with SecureON payload, daemon, and security invariants."""

    def test_tool_and_service_files_exist_and_executable(self):
        """Validates that tool and systemd unit exist, have executable permissions and proper sandboxing."""
        self.assertTrue(os.path.isfile(_TOOL), f"Missing executable tool: {_TOOL}")
        self.assertTrue(os.access(_TOOL, os.X_OK), f"Tool is not executable: {_TOOL}")

        self.assertTrue(os.path.isfile(_SERVICE), f"Missing systemd service unit: {_SERVICE}")
        with open(_SERVICE, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("ExecStart=/usr/libexec/mios/mios-wol-proxy daemon", content)
        self.assertIn("Restart=on-failure", content)
        self.assertIn("ProtectSystem=strict", content)
        self.assertIn("ProtectHome=read-only", content)
        self.assertIn("NoNewPrivileges=yes", content)
        self.assertIn("PrivateTmp=yes", content)

    def test_magic_packet_byte_construction(self):
        """
        Validates magic packet byte construction:
          - assert length == 108 bytes
          - starts with 6x 0xFF (b'\\xff' * 6)
          - contains 16x MAC address (mac_bytes * 16)
          - ends with 6x SecureON password payload (secureon_bytes)
        """
        # Test 1: Hex SecureON
        mac_str = "12:34:56:78:9A:BC"
        sec_hex = "01:02:03:04:05:06"
        packet = build_magic_packet(mac_str, sec_hex)

        self.assertEqual(len(packet), 108, f"Magic packet must be 108 bytes, got {len(packet)}")
        self.assertEqual(packet[:6], b"\xff" * 6, "Header must be exactly 6 bytes of 0xFF")

        mac_expected = bytes.fromhex("123456789abc")
        self.assertEqual(packet[6:102], mac_expected * 16, "Body must contain exactly 16 repetitions of MAC")

        sec_expected = bytes.fromhex("010203040506")
        self.assertEqual(packet[102:108], sec_expected, "Tail must contain exactly 6 bytes of SecureON payload")

        # Verify using verify_packet function
        verif = verify_packet(packet, expected_mac=mac_expected, expected_sec=sec_expected)
        self.assertEqual(verif["status"], "valid")
        self.assertEqual(verif["packet_length"], 108)
        self.assertEqual(verif["target_mac"], "12:34:56:78:9a:bc")
        self.assertEqual(verif["secureon_hex"], "010203040506")

        # Test 2: ASCII SecureON
        sec_ascii = "secret"
        packet_ascii = build_magic_packet(mac_str, sec_ascii)
        self.assertEqual(len(packet_ascii), 108)
        self.assertEqual(packet_ascii[:6], b"\xff" * 6)
        self.assertEqual(packet_ascii[6:102], mac_expected * 16)
        self.assertEqual(packet_ascii[102:108], b"secret")

    def test_secureon_password_validation(self):
        """Enforces that SecureON password must be exactly 6 bytes (12 hex digits or 6 ASCII chars)."""
        # Valid cases
        self.assertEqual(parse_secureon("secret"), b"secret")
        self.assertEqual(parse_secureon("11:22:33:44:55:66"), bytes.fromhex("112233445566"))
        self.assertEqual(parse_secureon("112233445566"), bytes.fromhex("112233445566"))
        self.assertEqual(parse_secureon("0x112233445566"), bytes.fromhex("112233445566"))

        # Invalid cases (too short, too long, invalid hex)
        with self.assertRaises(ValueError):
            parse_secureon("short")  # 5 chars
        with self.assertRaises(ValueError):
            parse_secureon("toolong!")  # 8 chars
        with self.assertRaises(ValueError):
            parse_secureon("11:22:33:44:55")  # 10 hex digits

        # CLI validation of bad SecureON
        res = subprocess.run(
            [_TOOL, "verify-packet", "--target-mac", "00:11:22:33:44:55", "--secureon", "bad", "--json"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "error")
        self.assertIn("SecureON password must be exactly 6 bytes", data.get("error", ""))

    def test_authentication_check_cli(self):
        """Verifies that unauthenticated or invalidly authenticated CLI wake calls are rejected."""
        # 1. Unauthenticated call (no auth token provided, no env)
        clean_env = {k: v for k, v in os.environ.items() if k != "MIOS_WOL_AUTH_TOKEN"}
        res = subprocess.run(
            [_TOOL, "wake", "--target-mac", "00:11:22:33:44:55", "--secureon", "secret", "--dry-run", "--json"],
            capture_output=True,
            text=True,
            env=clean_env,
        )
        self.assertNotEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "error")
        self.assertIn("unauthenticated", data.get("error", "").lower())

        # 2. Invalid auth token when expected token is set
        auth_env = clean_env.copy()
        auth_env["MIOS_WOL_AUTH_TOKEN"] = "mesh-cluster-secret-key"
        res = subprocess.run(
            [
                _TOOL,
                "wake",
                "--target-mac",
                "00:11:22:33:44:55",
                "--secureon",
                "secret",
                "--auth-token",
                "wrong-token",
                "--dry-run",
                "--json",
            ],
            capture_output=True,
            text=True,
            env=auth_env,
        )
        self.assertNotEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "error")

        # 3. Valid auth token succeeds
        res = subprocess.run(
            [
                _TOOL,
                "wake",
                "--target-mac",
                "00:11:22:33:44:55",
                "--secureon",
                "secret",
                "--auth-token",
                "mesh-cluster-secret-key",
                "--dry-run",
                "--json",
            ],
            capture_output=True,
            text=True,
            env=auth_env,
        )
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "dry-run")
        self.assertFalse(data.get("sent"))
        self.assertEqual(data.get("packet_length"), 108)

    def test_daemon_authentication_and_rpc(self):
        """Verifies that the daemon requires Bearer token authentication and processes authenticated RPCs."""
        auth_token = "daemon-secret-test-token"
        server = WolProxyServer(("127.0.0.1", 0), WolProxyRequestHandler, auth_token=auth_token)
        port = server.server_address[1]

        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        time.sleep(0.05)

        base_url = f"http://127.0.0.1:{port}"

        try:
            # 1. Health check is unauthenticated
            req = urllib.request.Request(f"{base_url}/health")
            with urllib.request.urlopen(req) as response:
                self.assertEqual(response.status, 200)
                body = json.loads(response.read().decode("utf-8"))
                self.assertEqual(body.get("status"), "healthy")

            # 2. Wake request without auth header -> 401 Unauthorized
            payload = json.dumps({
                "target_mac": "AA:BB:CC:DD:EE:FF",
                "secureon": "123456",
                "dry_run": True,
            }).encode("utf-8")

            req = urllib.request.Request(f"{base_url}/wake", data=payload, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req)
            self.assertEqual(ctx.exception.code, 401)

            # 3. Wake request with invalid Bearer token -> 401 Unauthorized
            req = urllib.request.Request(f"{base_url}/wake", data=payload, method="POST")
            req.add_header("Authorization", "Bearer invalid-token")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req)
            self.assertEqual(ctx.exception.code, 401)

            # 4. Wake request with valid Bearer token -> 200 OK
            req = urllib.request.Request(f"{base_url}/wake", data=payload, method="POST")
            req.add_header("Authorization", f"Bearer {auth_token}")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req) as response:
                self.assertEqual(response.status, 200)
                res = json.loads(response.read().decode("utf-8"))
                self.assertEqual(res.get("status"), "dry-run")
                self.assertEqual(res.get("target_mac"), "AA:BB:CC:DD:EE:FF")
                self.assertEqual(res.get("packet_length"), 108)
                self.assertFalse(res.get("sent"))

            # 5. Untrusted interface over daemon RPC -> 403 Forbidden
            wan_payload = json.dumps({
                "target_mac": "AA:BB:CC:DD:EE:FF",
                "secureon": "123456",
                "interface": "wan0",
                "dry_run": True,
            }).encode("utf-8")
            req = urllib.request.Request(f"{base_url}/wake", data=wan_payload, method="POST")
            req.add_header("Authorization", f"Bearer {auth_token}")
            req.add_header("Content-Type", "application/json")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req)
            self.assertEqual(ctx.exception.code, 403)

        finally:
            server.shutdown()
            server.server_close()

    def test_security_invariant_untrusted_interface_and_ip(self):
        """Prohibits emission across public/untrusted interfaces and public internet IPs."""
        # Untrusted interface prefixes
        for bad_iface in ["wan0", "public", "untrusted-eth", "internet", "ppp0"]:
            with self.assertRaises(SecurityError):
                validate_interface(bad_iface)

        # Allowed interfaces
        for good_iface in ["eth0", "enp3s0", "lo", "mesh0", "wg0", "br0", "lan0"]:
            validate_interface(good_iface)  # Must not raise

        # Public internet IPs prohibited
        for public_ip in ["8.8.8.8", "1.1.1.1", "142.250.190.46"]:
            with self.assertRaises(SecurityError):
                validate_destination_ip(public_ip)

        # Private / loopback / LAN broadcast IPs permitted
        for valid_ip in ["255.255.255.255", "192.168.1.255", "10.0.0.255", "127.0.0.1", "172.16.0.255"]:
            validate_destination_ip(valid_ip)  # Must not raise

        # CLI rejection of untrusted interface
        res = subprocess.run(
            [
                _TOOL,
                "wake",
                "--target-mac",
                "00:11:22:33:44:55",
                "--secureon",
                "secret",
                "--interface",
                "wan0",
                "--auth-token",
                "any-token",
                "--dry-run",
                "--json",
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertIn("Security invariant violation", data.get("error", ""))

        # CLI rejection of public IP
        res = subprocess.run(
            [
                _TOOL,
                "wake",
                "--target-mac",
                "00:11:22:33:44:55",
                "--secureon",
                "secret",
                "--broadcast-ip",
                "8.8.8.8",
                "--auth-token",
                "any-token",
                "--dry-run",
                "--json",
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertIn("Security invariant violation", data.get("error", ""))

    def test_dry_run_and_json_output(self):
        """Validates dry-run execution and structured JSON output formatting."""
        # 1. verify-packet --json
        res = subprocess.run(
            [
                _TOOL,
                "verify-packet",
                "--target-mac",
                "AA:BB:CC:DD:EE:FF",
                "--secureon",
                "secret",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "valid")
        self.assertEqual(data.get("packet_length"), 108)
        self.assertEqual(data.get("target_mac"), "aa:bb:cc:dd:ee:ff")
        self.assertEqual(data.get("mac_hex"), "aabbccddeeff")
        self.assertEqual(data.get("secureon_hex"), "736563726574")
        self.assertTrue(data.get("packet_hex").startswith("ffffffffffff"))

        # 2. wake --dry-run --json
        res = subprocess.run(
            [
                _TOOL,
                "wake",
                "--target-mac",
                "AA:BB:CC:DD:EE:FF",
                "--secureon",
                "secret",
                "--broadcast-ip",
                "192.168.1.255",
                "--port",
                "9",
                "--auth-token",
                "mesh-secret",
                "--dry-run",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        wake_data = json.loads(res.stdout)
        self.assertEqual(wake_data.get("status"), "dry-run")
        self.assertFalse(wake_data.get("sent"))
        self.assertEqual(wake_data.get("target_mac"), "AA:BB:CC:DD:EE:FF")
        self.assertEqual(wake_data.get("broadcast_ip"), "192.168.1.255")
        self.assertEqual(wake_data.get("port"), 9)
        self.assertEqual(wake_data.get("packet_length"), 108)
        self.assertTrue(wake_data.get("packet_hex").startswith("ffffffffffff"))


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestWolProxy)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
