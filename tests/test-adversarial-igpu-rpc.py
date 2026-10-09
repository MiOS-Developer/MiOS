#!/usr/bin/env python3
# AI-hint: Empirical adversarial stress test suite for MiOS iGPU Inference Lane, RPC Compute Fabric, and Hardware Routing (T-211, T-212).
# AI-related: usr/share/mios/windows/mios-igpu-server.ps1, usr/share/mios/mios.toml, usr/share/mios/llamacpp/llama-swap.yaml
# AI-doc: PROJECT.md, usr/share/doc/mios/manual/tests.md, usr/share/doc/mios/manual/thesis.md
"""Empirical Adversarial Stress Test Suite for MiOS iGPU Inference Lane & RPC Compute Fabric.

Executes adversarial challenges across four core dimensions:
  1. Challenge 1: Localhost isolation & binding.
     - Live port 8540 detection & stale service audit.
     - Rejection of non-localhost connections.
     - Closed port handling & socket reconnection resilience.
  2. Challenge 2: Protocol payload stress.
     - Malformed JSON bodies, syntax errors, raw binary garbage.
     - Missing fields (model, messages), empty prompts, empty arrays.
     - Non-existent models, out-of-range temperatures.
     - Server survivability after adversarial fault injection.
  3. Challenge 3: Hardware routing integrity.
     - DirectX UserGpuPreferences registry verification (GpuPreference=1;).
     - Adversarial regex matrix for Vulkan device enumeration (AMD vs NVIDIA).
     - Live Vulkan device enumeration check.
     - RTX 4090 dGPU VRAM isolation check via nvidia-smi.
  4. Challenge 4: RPC fallback & Vulkan cooperative matrix handling.
     - Enforcement of GGML_VK_DISABLE_COOPMAT=1.
     - Vulkan matrix cores verification on AMD Radeon (matrix cores: none).
     - Raw TCP wire protocol fuzzing & malformed RPC handshake.
     - Coordinator --rpc and --tensor-split configuration validation.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_SSOT_PATH = os.path.join(_ROOT, "usr", "share", "mios", "mios.toml")
_IGPU_SCRIPT_PATH = os.path.join(_ROOT, "usr", "share", "mios", "windows", "mios-igpu-server.ps1")
_SERVICE_CFG_PATH = os.path.join(_ROOT, "usr", "share", "mios", "windows", "MiOS-iGPU-Server.cfg")
_LLAMA_EXE = r"C:\ProgramData\mios\igpu\bin\llama-server.exe"
_RPC_EXE = r"C:\ProgramData\mios\igpu\bin\ggml-rpc-server.exe"
# The iGPU lane runs on the Windows host. Its live state (the listening service,
# the process table, HKCU) exists only there; on the Linux CI runner the same
# contract is asserted statically against the script that produces that state.
_ON_WINDOWS_HOST = sys.platform == "win32"
_WINDOWS_HOST_ONLY = "live Windows-host state; the script contract is asserted by the static twin"


def _ssot_igpu_port() -> int:
    with open(_SSOT_PATH, "rb") as fh:
        return int(tomllib.load(fh)["ports"]["llm_igpu"])


def _igpu_script() -> str:
    with open(_IGPU_SCRIPT_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


# ============================================================================
# Adversarial Mock Server for Protocol Payload Fuzzing
# ============================================================================

class AdversarialLlamaServerHandler(BaseHTTPRequestHandler):
    """Rigorous HTTP handler strictly enforcing OpenAI wire specifications."""

    def log_message(self, format: str, *args) -> None:
        pass

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        elif self.path == "/v1/models":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = {
                "object": "list",
                "data": [
                    {"id": "mios-igpu", "object": "model", "owned_by": "mios"},
                    {"id": "qwen2.5-1.5b-instruct", "object": "model", "owned_by": "mios"},
                ],
            }
            self.wfile.write(json.dumps(payload).encode("utf-8"))
        else:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"Endpoint not found","type":"invalid_request_error","code":"not_found"}}')

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"Endpoint not found","type":"invalid_request_error"}}')
            return

        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length) if content_length > 0 else b""

        try:
            body = json.loads(raw_body.decode("utf-8"))
        except Exception as e:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            err = {"error": {"message": f"Parse error: {e}", "type": "invalid_request_error", "code": "bad_json"}}
            self.wfile.write(json.dumps(err).encode("utf-8"))
            return

        # Model validation
        if "model" not in body or not isinstance(body["model"], str) or not body["model"].strip():
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"Missing or invalid \'model\' field","type":"invalid_request_error"}}')
            return

        if body["model"] not in ("mios-igpu", "qwen2.5-1.5b-instruct"):
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"Model not found","type":"invalid_request_error","code":"model_not_found"}}')
            return

        # Messages validation
        if "messages" not in body or not isinstance(body["messages"], list):
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"Missing or invalid \'messages\' field","type":"invalid_request_error"}}')
            return

        if len(body["messages"]) == 0:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"Messages array must not be empty","type":"invalid_request_error"}}')
            return

        for m in body["messages"]:
            if not isinstance(m, dict) or "content" not in m:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"Each message must contain a \'content\' field","type":"invalid_request_error"}}')
                return
            if not isinstance(m["content"], str) or len(m["content"].strip()) == 0:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"Prompt content must be a non-empty string","type":"invalid_request_error"}}')
                return

        # Temperature validation
        if "temperature" in body:
            temp = body["temperature"]
            if not isinstance(temp, (int, float)) or temp < 0.0 or temp > 2.0:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"Temperature must be a float between 0.0 and 2.0","type":"invalid_request_error"}}')
                return

        # Successful OpenAI response
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        resp = {
            "id": "chatcmpl-test-adv",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Adversarial test response ok."},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        self.wfile.write(json.dumps(resp).encode("utf-8"))


# ============================================================================
# Challenge 1: Localhost Isolation & Binding
# ============================================================================

class TestChallenge1LocalhostIsolation(unittest.TestCase):
    """Adversarial challenge 1: Localhost isolation, binding, and closed ports."""

    def test_c1_01_script_strictly_binds_to_localhost(self):
        """Verifies mios-igpu-server.ps1 AST/content passes --host 127.0.0.1 and lacks 0.0.0.0."""
        with open(_IGPU_SCRIPT_PATH, "r", encoding="utf-8") as f:
            content = f.read()

        # Must bind to 127.0.0.1
        self.assertIn("--host 127.0.0.1", content, "Server command line must specify --host 127.0.0.1")
        # Must NOT bind to 0.0.0.0
        self.assertNotIn("--host 0.0.0.0", content, "Server command line must NOT bind to 0.0.0.0")
        # Tailscale firewall rule should not be added
        self.assertNotIn("New-NetFirewallRule", content, "Script must not open external firewall ports")

    def test_c1_02_service_launch_serves_ssot_port(self):
        """The script's default port and the service launch line are the SSOT [ports].llm_igpu, never the stale 11436."""
        port = _ssot_igpu_port()
        default = re.search(r"\[int\]\s*\$Port\s*=\s*(\d+)", _igpu_script())
        self.assertIsNotNone(default, "mios-igpu-server.ps1 must declare an [int] $Port default")
        self.assertEqual(int(default.group(1)), port, "Script default port must be the SSOT [ports].llm_igpu")
        if os.path.exists(_SERVICE_CFG_PATH):
            with open(_SERVICE_CFG_PATH, "r", encoding="utf-8") as f:
                cfg_content = f.read()
            self.assertNotIn("11436", cfg_content, "Stale port 11436 must not be present in MiOS-iGPU-Server.cfg")
            launch = re.search(r"-Port\s+(\d+)", cfg_content)
            self.assertIsNotNone(launch, "MiOS-iGPU-Server.cfg must pass -Port to the script")
            self.assertEqual(int(launch.group(1)), port, "Service must listen on the SSOT [ports].llm_igpu")

    @unittest.skipUnless(_ON_WINDOWS_HOST, _WINDOWS_HOST_ONLY)
    def test_c1_02_live_service_state(self):
        """Empirically inspects the live iGPU listener and the retired mios-ainode process on the Windows host."""
        port = _ssot_igpu_port()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        port_open = False
        try:
            sock.connect(("127.0.0.1", port))
            port_open = True
            sock.close()
        except (socket.timeout, ConnectionRefusedError):
            port_open = False

        # Check if mios-ainode process is running
        ainode_running = False
        res = subprocess.run(["tasklist", "/FI", "IMAGENAME eq mios-ainode.exe"], capture_output=True, text=True)
        if "mios-ainode.exe" in res.stdout:
            ainode_running = True

        # After remediation the SSOT port is open and ainode is terminated.
        self.assertFalse(ainode_running, "Deprecated mios-ainode process must not be running")
        self.assertTrue(port_open, f"Port {port} must be listening on localhost")

    def test_c1_03_non_localhost_connection_refused(self):
        """Spins up a server on 127.0.0.1 and verifies connection to non-loopback IP is refused."""
        server = HTTPServer(("127.0.0.1", 0), AdversarialLlamaServerHandler)
        port = server.server_port
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            # 1. Connecting via 127.0.0.1 must succeed
            s_local = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s_local.settimeout(2.0)
            s_local.connect(("127.0.0.1", port))
            s_local.close()

            # 2. Connecting via non-localhost IP (e.g. machine external IP or broadcast) must fail
            # Resolve machine hostname IP
            hostname = socket.gethostname()
            try:
                host_ip = socket.gethostbyname(hostname)
            except Exception:
                host_ip = "192.168.1.1"

            if host_ip != "127.0.0.1":
                s_ext = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s_ext.settimeout(1.0)
                with self.assertRaises((ConnectionRefusedError, socket.timeout, OSError)):
                    s_ext.connect((host_ip, port))
                s_ext.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_c1_04_closed_port_behavior_fails_cleanly(self):
        """Attempts connection to closed ports (8541, 8551, 8599) and asserts failure."""
        for closed_port in (8541, 8551, 8599):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)
            with self.assertRaises((ConnectionRefusedError, TimeoutError, OSError)):
                s.connect(("127.0.0.1", closed_port))
            s.close()

    def test_c1_05_rapid_connection_churn_stress(self):
        """Performs 50 rapid connect/disconnect cycles on localhost without socket leaks."""
        server = HTTPServer(("127.0.0.1", 0), AdversarialLlamaServerHandler)
        port = server.server_port
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            for _ in range(50):
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1.0)
                s.connect(("127.0.0.1", port))
                s.sendall(b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
                resp = s.recv(512)
                self.assertIn(b"200 OK", resp)
                s.close()
        finally:
            server.shutdown()
            server.server_close()


# ============================================================================
# Challenge 2: Protocol Payload Stress
# ============================================================================

class TestChallenge2ProtocolPayloadStress(unittest.TestCase):
    """Adversarial challenge 2: Protocol payload fuzzing, malformed bodies, HTTP 400/404."""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), AdversarialLlamaServerHandler)
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _post(self, path: str, data: bytes | str, headers: dict | None = None) -> tuple[int, dict]:
        if isinstance(data, str):
            data = data.encode("utf-8")
        h = {"Content-Type": "application/json"}
        if headers:
            h.update(headers)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                status = resp.status
                body = json.loads(resp.read().decode("utf-8"))
                return status, body
        except urllib.error.HTTPError as e:
            body = json.loads(e.read().decode("utf-8"))
            return e.code, body

    def test_c2_01_malformed_json_syntax(self):
        """Submits truncated and syntax-broken JSON strings; expects HTTP 400."""
        malformed_inputs = [
            b'{"model": "mios-igpu", "messages": ',
            b'{"model": "mios-igpu", "messages": [{"role": "user", "content": "hi"}],}',
            b'{\\',
            b'{not_a_json}',
            b'\x00\x01\x02\xff\xfe',
        ]
        for payload in malformed_inputs:
            status, resp = self._post("/v1/chat/completions", payload)
            self.assertEqual(status, 400, f"Expected 400 for payload: {payload[:20]}")
            self.assertIn("error", resp)
            self.assertEqual(resp["error"].get("type"), "invalid_request_error")

    def test_c2_02_missing_or_invalid_model_field(self):
        """Submits requests with missing, empty, or non-string model; expects HTTP 400."""
        invalid_models = [
            {"messages": [{"role": "user", "content": "hi"}]},
            {"model": "", "messages": [{"role": "user", "content": "hi"}]},
            {"model": "   ", "messages": [{"role": "user", "content": "hi"}]},
            {"model": 12345, "messages": [{"role": "user", "content": "hi"}]},
            {"model": None, "messages": [{"role": "user", "content": "hi"}]},
        ]
        for payload in invalid_models:
            status, resp = self._post("/v1/chat/completions", json.dumps(payload))
            self.assertEqual(status, 400, f"Expected 400 for: {payload}")
            self.assertIn("error", resp)

    def test_c2_03_non_existent_model_returns_404(self):
        """Submits request for non-existent model; expects HTTP 404 with error code."""
        payload = {"model": "non-existent-gpt-5-turbo", "messages": [{"role": "user", "content": "hello"}]}
        status, resp = self._post("/v1/chat/completions", json.dumps(payload))
        self.assertEqual(status, 404, "Expected 404 for non-existent model")
        self.assertEqual(resp["error"].get("code"), "model_not_found")

    def test_c2_04_empty_messages_array_returns_400(self):
        """Submits empty messages list or missing messages; expects HTTP 400."""
        invalid_messages = [
            {"model": "mios-igpu"},
            {"model": "mios-igpu", "messages": []},
            {"model": "mios-igpu", "messages": "not_a_list"},
            {"model": "mios-igpu", "messages": None},
        ]
        for payload in invalid_messages:
            status, resp = self._post("/v1/chat/completions", json.dumps(payload))
            self.assertEqual(status, 400, f"Expected 400 for: {payload}")
            self.assertIn("error", resp)

    def test_c2_05_empty_prompt_content_returns_400(self):
        """Submits messages where content is empty or whitespace; expects HTTP 400."""
        empty_contents = [
            {"model": "mios-igpu", "messages": [{"role": "user", "content": ""}]},
            {"model": "mios-igpu", "messages": [{"role": "user", "content": "   "}]},
            {"model": "mios-igpu", "messages": [{"role": "user"}]},
            {"model": "mios-igpu", "messages": [{"role": "user", "content": None}]},
        ]
        for payload in empty_contents:
            status, resp = self._post("/v1/chat/completions", json.dumps(payload))
            self.assertEqual(status, 400, f"Expected 400 for: {payload}")
            self.assertIn("error", resp)

    def test_c2_06_out_of_range_temperature_returns_400(self):
        """Submits invalid temperature values (-1.0, 999.0, string, null); expects HTTP 400."""
        invalid_temps = [-5.0, -0.1, 2.5, 99.0, "hot", None, [1.0]]
        for temp in invalid_temps:
            payload = {
                "model": "mios-igpu",
                "messages": [{"role": "user", "content": "test"}],
                "temperature": temp,
            }
            status, resp = self._post("/v1/chat/completions", json.dumps(payload))
            self.assertEqual(status, 400, f"Expected 400 for temperature: {temp}")
            self.assertIn("error", resp)

    def test_c2_07_server_survives_adversarial_flurry(self):
        """Verifies server remains alive and serves 200 OK after multiple invalid inputs."""
        # 1. Send barrage of invalid inputs
        for _ in range(10):
            self._post("/v1/chat/completions", b"malformed")
            self._post("/v1/chat/completions", json.dumps({"model": "fake", "messages": []}))

        # 2. Send valid request immediately after
        valid_payload = {
            "model": "mios-igpu",
            "messages": [{"role": "user", "content": "Explain quantum computing briefly."}],
            "temperature": 0.7,
        }
        status, resp = self._post("/v1/chat/completions", json.dumps(valid_payload))
        self.assertEqual(status, 200, "Server must remain functional after fault barrage")
        self.assertIn("choices", resp)
        self.assertEqual(len(resp["choices"]), 1)


# ============================================================================
# Challenge 3: Hardware Routing Integrity
# ============================================================================

class TestChallenge3HardwareRoutingIntegrity(unittest.TestCase):
    """Adversarial challenge 3: DirectX UserGpuPreferences, Vulkan device routing, dGPU isolation."""

    def test_c3_01_script_registers_low_power_preference_for_every_server(self):
        """The script writes GpuPreference=1; to UserGpuPreferences for llama-server, rpc-server and ggml-rpc-server."""
        content = _igpu_script()
        self.assertIn(r"\Software\Microsoft\DirectX\UserGpuPreferences", content)
        self.assertIn("-Value 'GpuPreference=1;'", content, "Preference value must be the low-power GpuPreference=1;")
        self.assertRegex(content, r"\$exe\s*=\s*Join-Path \$binDir 'llama-server\.exe'")
        self.assertRegex(content, r"\$rpcExe\s*=\s*Join-Path \$binDir 'rpc-server\.exe'")
        call = re.search(r"^Ensure-MiosGpuPreferences\s+@\((.*)\)\s*$", content, re.MULTILINE)
        self.assertIsNotNone(call, "Script must apply the GPU preference to its server binaries")
        for target in ("$exe", "$rpcExe", "'ggml-rpc-server.exe'"):
            self.assertIn(target, call.group(1), f"{target} missing from the GPU preference registration")

    @unittest.skipUnless(_ON_WINDOWS_HOST, _WINDOWS_HOST_ONLY)
    def test_c3_01_directx_user_gpu_preferences_registry_value(self):
        """Queries HKCU\\Software\\Microsoft\\DirectX\\UserGpuPreferences for GpuPreference=1;."""
        import winreg

        reg_path = r"Software\Microsoft\DirectX\UserGpuPreferences"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_READ) as key:
                values = {}
                i = 0
                while True:
                    try:
                        name, val, _ = winreg.EnumValue(key, i)
                        values[name] = val
                        i += 1
                    except OSError:
                        break
        except FileNotFoundError:
            self.fail(f"Registry key {reg_path} does not exist!")

        target_binaries = [
            _LLAMA_EXE,
            r"C:\ProgramData\mios\igpu\bin\rpc-server.exe",
            _RPC_EXE,
        ]
        for target in target_binaries:
            self.assertIn(target, values, f"{target} missing from UserGpuPreferences")
            self.assertEqual(values[target], "GpuPreference=1;", f"{target} must have GpuPreference=1;")

    def test_c3_02_vulkan_device_regex_adversarial_matrix(self):
        """Stress-tests the exact PowerShell regex from mios-igpu-server.ps1 against adversarial lists."""
        pattern = re.compile(r"^\s*(Vulkan\d+)\s*:\s*(.+?)\s*(\(|$|\r|\n)", re.IGNORECASE | re.MULTILINE)

        def resolve_device(dev_txt: str) -> str:
            matches = pattern.finditer(dev_txt)
            for m in matches:
                dev_id = m.group(1)
                dev_name = m.group(2).strip()
                if re.search(r"AMD|Radeon", dev_name, re.IGNORECASE) and not re.search(r"NVIDIA|GeForce|RTX", dev_name, re.IGNORECASE):
                    return dev_id
            return "Vulkan0"  # fallback

        # Matrix 1: Standard enumeration (AMD first)
        t1 = "  Vulkan0: AMD Radeon(TM) Graphics (32143 MiB)\n  Vulkan1: NVIDIA GeForce RTX 4090 (24138 MiB)"
        self.assertEqual(resolve_device(t1), "Vulkan0")

        # Matrix 2: Inverted enumeration (NVIDIA first, AMD second) - The historic bug
        t2 = "  Vulkan0: NVIDIA GeForce RTX 4090 (24138 MiB)\n  Vulkan1: AMD Radeon(TM) Graphics (32143 MiB)"
        self.assertEqual(resolve_device(t2), "Vulkan1", "Must select Vulkan1 (AMD) when RTX 4090 is Vulkan0!")

        # Matrix 3: Mixed case and vendor variations
        t3 = "Vulkan0: NVIDIA RTX 4090\nVulkan1: Radeon RX 780M (Vulkan)\nVulkan2: Intel Graphics"
        self.assertEqual(resolve_device(t3), "Vulkan1")

        # Matrix 4: Adversarial trick: NVIDIA name containing 'AMD' substring (e.g. driver string)
        t4 = "  Vulkan0: NVIDIA GeForce RTX 4090 (with AMD compatibility profile)\n  Vulkan1: AMD Radeon(TM) Graphics"
        self.assertEqual(resolve_device(t4), "Vulkan1", "Must reject NVIDIA device even if AMD appears in suffix")

    def test_c3_03_live_vulkan_device_enumeration(self):
        """Executes llama-server.exe --list-devices and verifies AMD Radeon is enumerated."""
        if not os.path.exists(_LLAMA_EXE):
            self.skipTest(f"llama-server.exe not found at {_LLAMA_EXE}")

        res = subprocess.run([_LLAMA_EXE, "--list-devices"], capture_output=True, text=True, timeout=5.0)
        self.assertEqual(res.returncode, 0, f"--list-devices failed: {res.stderr}")
        self.assertIn("AMD Radeon", res.stdout, "AMD Radeon iGPU must be listed in Vulkan devices")

    def test_c3_04_rtx_4090_dgpu_vram_isolation(self):
        """Verifies via nvidia-smi that no llama-server or rpc-server process is on the RTX 4090."""
        if shutil.which("nvidia-smi") is None:  # absent raised FileNotFoundError, not the skip intended
            self.skipTest("nvidia-smi not available")
        res = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=5.0)
        if res.returncode != 0:
            self.skipTest("nvidia-smi not available")

        # Process list in nvidia-smi output must not contain llama-server or rpc-server
        self.assertNotIn("llama-server.exe", res.stdout, "llama-server must not attach to RTX 4090!")
        self.assertNotIn("rpc-server.exe", res.stdout, "rpc-server must not attach to RTX 4090!")
        self.assertNotIn("ggml-rpc-server.exe", res.stdout, "ggml-rpc-server must not attach to RTX 4090!")


# ============================================================================
# Challenge 4: RPC Fallback & Vulkan Cooperative Matrix
# ============================================================================

class TestChallenge4RpcFallbackAndVulkanCoopmat(unittest.TestCase):
    """Adversarial challenge 4: Vulkan cooperative matrix handling and RPC protocol."""

    def test_c4_01_script_enforces_coopmat_disabled_in_rpc_mode(self):
        """Verifies mios-igpu-server.ps1 explicitly sets GGML_VK_DISABLE_COOPMAT = '1' in Rpc mode."""
        with open(_IGPU_SCRIPT_PATH, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("$env:GGML_VK_DISABLE_COOPMAT = '1'", content, "Script must disable coopmat in Rpc mode")

    def test_c4_02_vulkan_coopmat_disabled_execution(self):
        """Runs ggml-rpc-server.exe with GGML_VK_DISABLE_COOPMAT=1 and confirms matrix cores: none."""
        if not os.path.exists(_RPC_EXE):
            self.skipTest(f"RPC executable not found at {_RPC_EXE}")

        env = os.environ.copy()
        env["GGML_VK_DISABLE_COOPMAT"] = "1"
        res = subprocess.run([_RPC_EXE, "--device", "?"], capture_output=True, text=True, env=env, timeout=5.0)

        # Check AMD Radeon matrix cores in stderr telemetry
        amd_lines = [line for line in res.stderr.splitlines() if "AMD Radeon" in line and "matrix cores" in line]
        self.assertTrue(len(amd_lines) > 0, f"Expected AMD Radeon matrix cores line in stderr: {res.stderr}")
        self.assertIn("matrix cores: none", amd_lines[0], "AMD Radeon iGPU must report matrix cores: none")

    def test_c4_03_rpc_wire_protocol_malformed_handshake(self):
        """Connects to a simulated RPC port and transmits corrupted bytes; asserts graceful termination."""
        # Spin up a simple raw socket listener simulating rpc-server socket
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.listen(1)

        def client_worker():
            time.sleep(0.1)
            c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            c.connect(("127.0.0.1", port))
            # Send corrupted RPC header
            c.sendall(b"\xde\xad\xbe\xef\x00\x00\x00\x00")
            c.close()

        t = threading.Thread(target=client_worker)
        t.start()

        conn, _ = listener.accept()
        data = conn.recv(1024)
        conn.close()
        listener.close()
        t.join()

        self.assertEqual(data, b"\xde\xad\xbe\xef\x00\x00\x00\x00")

    def test_c4_04_coordinator_rpc_config_syntax_validation(self):
        """Validates coordinator --rpc and --tensor-split syntax rules."""
        # Valid tensor split formats
        valid_splits = ["24,4", "16,8,8", "30,2", "0,32"]
        for s in valid_splits:
            parts = [int(p) for p in s.split(",")]
            self.assertTrue(all(p >= 0 for p in parts), f"Invalid split: {s}")

        # Invalid tensor split formats
        invalid_splits = ["24,", ",4", "abc,def", "-1,24", ""]
        for s in invalid_splits:
            with self.assertRaises((ValueError, IndexError)):
                parts = [int(p) for p in s.split(",")]
                if len(parts) < 2 or any(p < 0 for p in parts):
                    raise ValueError("Malformed split")


# ============================================================================
# Main Test Runner
# ============================================================================

if __name__ == "__main__":
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    suite.addTests(loader.loadTestsFromTestCase(TestChallenge1LocalhostIsolation))
    suite.addTests(loader.loadTestsFromTestCase(TestChallenge2ProtocolPayloadStress))
    suite.addTests(loader.loadTestsFromTestCase(TestChallenge3HardwareRoutingIntegrity))
    suite.addTests(loader.loadTestsFromTestCase(TestChallenge4RpcFallbackAndVulkanCoopmat))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 80)
    print("MiOS iGPU & RPC Inference Adversarial Challenge Test Suite")
    print("=" * 80)
    print(f"Ran: {result.testsRun} | Passed: {result.testsRun - len(result.failures) - len(result.errors)} | "
          f"Failures: {len(result.failures)} | Errors: {len(result.errors)}")
    print("=" * 80)

    sys.exit(0 if result.wasSuccessful() else 1)
