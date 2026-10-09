# AI-hint: Two-sided native AIO MCP tests: real upstream process, private tmux sockets, protocol negotiation, exit receipts and negative controls.
# AI-related: /usr/libexec/mios/mios-mcp-server, /usr/share/mios/mios.toml [mcp.tmux], /usr/share/doc/mios/mcp-tmux.md
# AI-functions: TestMcpAio, load_relay, payload
import asyncio
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.request
import socket
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import tomllib
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
RELAY = ROOT / "usr/libexec/mios/mios-mcp-server"
os.environ["MIOS_TOML"] = str(ROOT / "usr/share/mios/mios.toml")
os.environ["MIOS_RESOLVER_NATIVE"] = "0"

def load_relay():
    loader = importlib.machinery.SourceFileLoader("mios_mcp_aio_relay", str(RELAY))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module

relay = load_relay()
CONFIG = tomllib.loads((ROOT / "usr/share/mios/mios.toml").read_text())["mcp"]["tmux"]

# CI runs against the same verified release asset as the image installer. It
# does not depend on a previously installed binary or an upstream download.
_PAYLOAD = tempfile.TemporaryDirectory(prefix="mios-mcp-test-binary-")
asset = CONFIG["assets"][relay.platform.machine()]
archive = ROOT / asset["path"]
if hashlib.sha256(archive.read_bytes()).hexdigest() != asset["sha256"]:
    raise RuntimeError("tmux-mcp test asset checksum mismatch")
with tarfile.open(archive) as package:
    binary = Path(_PAYLOAD.name) / "tmux-mcp"
    binary.write_bytes(package.extractfile("tmux-mcp").read())
    binary.chmod(0o755)
os.environ["MIOS_TMUX_MCP_BINARY"] = str(binary)

def setUpModule():
    if shutil.which("tmux") is None:
        raise unittest.SkipTest("native tmux dependency absent")
    # Network use in this suite is limited to its private loopback HTTP
    # fixtures. Reject an external URL before a connection can be opened.
    original = urllib.request.urlopen
    def fixture_urlopen(url, *args, **kwargs):
        from urllib.parse import urlparse
        address = url.full_url if isinstance(url, urllib.request.Request) else url
        if urlparse(address).hostname != "127.0.0.1":
            raise AssertionError("network outside the loopback fixture")
        return original(url, *args, **kwargs)
    guard = patch.object(urllib.request, "urlopen", fixture_urlopen)
    guard.start()
    unittest.addModuleCleanup(guard.stop)

def payload(result):
    wire = result.model_dump(by_alias=True, exclude_none=True)
    if wire.get("structuredContent"):
        return wire["structuredContent"]
    for block in result.content:
        if block.type == "text":
            try:
                return json.loads(block.text)
            except ValueError:
                continue
    raise AssertionError("missing structured command receipt")

class TestMcpAio(unittest.IsolatedAsyncioTestCase):
    async def test_network_guard_rejects_external_destination(self):
        with self.assertRaisesRegex(AssertionError, "outside the loopback"):
            urllib.request.urlopen("https://example.invalid/DEVLOOP-PLANTED-NETWORK")

    async def asyncSetUp(self):
        self.bridge = relay._TmuxBridge(CONFIG)
        await self.bridge.start()

    async def asyncTearDown(self):
        await self.bridge.close()

    async def execute(self, command, **args):
        return await self.bridge.call("mios_tmux_execute_command", {"command": command, **args})

    def mock_agent_commands(self, directory, codex_script=None):
        """Require the native dispatch contract without contacting a provider."""
        dispatcher = Path(directory) / "mios"
        dispatcher.write_text('#!/bin/sh\n[ "$1" = agent ] || exit 24\n'
                              'agent="$2"; shift 2\nexport MIOS_MOCK_DISPATCHED=1\n'
                              'exec "$(dirname "$0")/$agent" "$@"\n')
        dispatcher.chmod(0o755)
        for agent in ("codex", "agy"):
            shim = Path(directory) / agent
            script = codex_script if agent == "codex" and codex_script else f"echo '{agent} 1.0.0 (mock)'\n"
            shim.write_text('#!/bin/sh\n[ "$MIOS_MOCK_DISPATCHED" = 1 ] || exit 25\n' + script)
            shim.chmod(0o755)
        return dict(os.environ, PATH=f"{directory}:{os.environ.get('PATH', '')}",
                    MIOS_AGENT_PIPE_URL="http://127.0.0.1:1", MIOS_MCP_LIST_TIMEOUT="1",
                    MIOS_MCP_TOOLS_CACHE=os.devnull)

    async def test_positive_and_planted_nonzero_receipt(self):
        good = await self.execute("printf 'POSITIVE-CONTROL\\n'")
        self.assertFalse(good.model_dump(by_alias=True).get("isError"), good)
        self.assertEqual(payload(good)["exitCode"], 0)
        self.assertIn("POSITIVE-CONTROL", payload(good)["output"])
        bad = await self.execute("printf 'DEVLOOP-PLANTED-EXIT\\n'; (exit 23)")
        self.assertTrue(bad.model_dump(by_alias=True).get("isError"), bad)
        self.assertEqual(payload(bad)["exitCode"], 23)
        self.assertIn("DEVLOOP-PLANTED-EXIT", payload(bad)["output"])

    async def test_private_client_socket_and_teardown(self):
        other = relay._TmuxBridge(CONFIG)
        try:
            await other.start()
            self.assertNotEqual(self.bridge.directory.name, other.directory.name)
            self.assertEqual(os.stat(self.bridge.directory.name).st_mode & 0o777, 0o700)
            first = await self.execute('printf FIRST > "$HOME/client-marker"; printf "%s" "$HOME"')
            self.assertEqual(payload(first)["exitCode"], 0, first)
            self.assertEqual(payload(first)["output"], self.bridge.directory.name)
            again = await self.execute('cat "$HOME/client-marker"')
            self.assertEqual(payload(again)["output"], "FIRST")
            result = await other.call("mios_tmux_execute_command", {
                "command": 'test ! -e "$HOME/client-marker" && printf second:unset'})
            self.assertFalse(result.model_dump(by_alias=True).get("isError"), result)
            self.assertIn("second:unset", payload(result)["output"])
            directory = self.bridge.directory.name
            await self.bridge.close()
            self.assertFalse(os.path.exists(directory))
            survivor = await other.call("mios_tmux_execute_command", {"command": "printf SURVIVOR"})
            self.assertFalse(survivor.model_dump(by_alias=True).get("isError"), survivor)
            self.assertIn("SURVIVOR", payload(survivor)["output"])
        finally:
            await other.close()

    async def test_timeout_kills_busy_slot_and_is_error(self):
        result = await self.execute("printf DEVLOOP-PLANTED-TIMEOUT; sleep 30", timeoutSeconds=1)
        self.assertTrue(result.model_dump(by_alias=True).get("isError"), result)
        self.assertTrue(payload(result)["timedOut"])
        slots = await self.bridge.call("mios_tmux_list_slots", {})
        self.assertEqual(payload(slots), [])
        recovered = await self.execute("printf RECOVERED")
        self.assertFalse(recovered.model_dump(by_alias=True).get("isError"), recovered)
        self.assertIn("RECOVERED", payload(recovered)["output"])

    async def test_negative_policy_no_visible_pane_or_unbounded_slot(self):
        for args, needle in (({"slot": 0}, "slot"), ({"slot": CONFIG["max_slots"]+1}, "slot"),
                             ({"slot": True}, "slot"), ({"isolated": False}, "isolated"),
                             ({"paneId": "%0"}, "paneId"), ({"timeoutSeconds": 0}, "timeoutSeconds")):
            result = await self.execute("printf SHOULD-NOT-RUN", **args)
            self.assertTrue(result.model_dump(by_alias=True).get("isError"), result)
            self.assertIn(needle, result.content[0].text)
        disallowed = await self.bridge.call("mios_tmux_notify", {"message": "SHOULD-NOT-RUN"})
        self.assertTrue(disallowed.model_dump(by_alias=True).get("isError"))

    async def test_ambient_secrets_and_shell_hooks_are_absent(self):
        other = relay._TmuxBridge(CONFIG)
        with patch.dict(os.environ, {"MIOS_TEST_SECRET": "DEVLOOP-PLANTED-SECRET", "BASH_ENV": "/invalid"}):
            try:
                await other.start()
                result = await other.call("mios_tmux_execute_command", {
                    "command": "printf '%s:%s' \"${MIOS_TEST_SECRET-unset}\" \"${BASH_ENV-unset}\""})
                self.assertFalse(result.model_dump(by_alias=True).get("isError"), result)
                self.assertIn("unset:unset", payload(result)["output"])
            finally:
                await other.close()

    async def test_parallel_slots_are_independent(self):
        results = await asyncio.gather(self.execute("sleep 1; printf SLOT1", slot=1),
                                       self.execute("printf SLOT2", slot=2))
        for slot, result in enumerate(results, 1):
            self.assertFalse(result.model_dump(by_alias=True).get("isError"), result)
            self.assertIn(f"SLOT{slot}", payload(result)["output"])

    async def test_caller_theme_survives_private_worker_home(self):
        with tempfile.TemporaryDirectory() as directory:
            override = Path(directory) / "theme.toml"
            override.write_text('[colors]\nbg = "#123456"\n')
            with patch.dict(os.environ, {"MIOS_USER_TOML": str(override)}):
                other = relay._TmuxBridge(CONFIG)
                try:
                    await other.start()
                    before = (Path(other.directory.name) / "tmux.conf").read_bytes()
                    self.assertIn(b"#123456", before)
                    result = await other.call("mios_tmux_execute_command", {"command": "printf CALLER-THEME"})
                    self.assertFalse(result.model_dump(by_alias=True).get("isError"), result)
                    self.assertEqual((Path(other.directory.name) / "tmux.conf").read_bytes(), before)
                    self.assertEqual(other.env["MIOS_THEME_PROJECTED"], "1")
                finally:
                    await other.close()

    async def test_one_stdio_endpoint_lists_system_and_tmux_tools(self):
        from mcp import Client, StdioServerParameters
        env = dict(os.environ, MIOS_AGENT_PIPE_URL="http://127.0.0.1:1",
                   MIOS_MCP_LIST_TIMEOUT="1", MIOS_MCP_TOOLS_CACHE=os.devnull)
        async with Client(StdioServerParameters(command=sys.executable, args=[str(RELAY)], env=env)) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            self.assertIn("mios_tmux_execute_command", names)
            self.assertTrue(any(not n.startswith("mios_tmux_") for n in names), names)
            result = await client.call_tool("mios_tmux_execute_command", {"command": "printf AIO-ENDPOINT"})
            self.assertFalse(result.model_dump(by_alias=True).get("isError"), result)
            self.assertIn("AIO-ENDPOINT", payload(result)["output"])

    async def test_packaging_positive_and_corruption_negative(self):
        for asset in CONFIG["assets"].values():
            content = (ROOT / asset["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(), asset["sha256"])
            self.assertNotEqual(hashlib.sha256(content + b"DEVLOOP-PLANTED-CORRUPTION").hexdigest(), asset["sha256"])
        with patch.dict(os.environ, {"MIOS_TMUX_MCP_BINARY": "/nonexistent/DEVLOOP-PLANTED-BINARY"}):
            broken = relay._TmuxBridge(CONFIG)
            try:
                with self.assertRaisesRegex(RuntimeError, "DEVLOOP-PLANTED-BINARY"):
                    await broken.start()
            finally:
                await broken.close()

    async def test_cross_process_agent_messages_require_recipient_receipts(self):
        from mcp import Client, StdioServerParameters
        with tempfile.TemporaryDirectory(prefix="mios-agent-relay-proof-") as state:
            env = dict(os.environ, MIOS_AGENT_RELAY_STATE=state,
                       MIOS_AGENT_PIPE_URL="http://127.0.0.1:1",
                       MIOS_MCP_LIST_TIMEOUT="1", MIOS_MCP_TOOLS_CACHE=os.devnull)
            params = StdioServerParameters(command=sys.executable, args=[str(RELAY)], env=env)
            async with Client(params) as head, Client(params) as worker:
                registered = payload(await head.call_tool("mios_agent_register", {
                    "agent_id": "proof-head", "kind": "codex"}))
                recipient = payload(await worker.call_tool("mios_agent_register", {
                    "agent_id": "proof-worker", "kind": "agy"}))
                identity = {"agent_id": "proof-head", "token": registered["token"]}
                worker_identity = {"agent_id": "proof-worker", "token": recipient["token"]}
                message = {**identity, "to": "proof-worker", "message_id": "proof-1",
                           "message": "Bounded transport test; no provider invocation."}
                self.assertEqual(payload(await head.call_tool("mios_agent_send", message))["status"], "queued")
                names = {row.name for row in (await worker.list_tools()).tools}
                self.assertTrue({"mios_agent_receive", "mios_tmux_execute_command", "system_status"} <= names)
                inbox = payload(await worker.call_tool("mios_agent_receive", worker_identity))
                self.assertEqual([row["message"] for row in inbox["messages"]], [message["message"]])
                misrouted = await head.call_tool("mios_agent_ack", {**identity, "message_id": "proof-1"})
                self.assertTrue(misrouted.model_dump(by_alias=True).get("isError"))
                self.assertIn("does not belong", misrouted.content[0].text)
                stolen = await worker.call_tool("mios_agent_receive", {
                    **worker_identity, "token": "DEVLOOP-PLANTED-INVALID-LEASE-TOKEN"})
                self.assertTrue(stolen.model_dump(by_alias=True).get("isError"))
                self.assertIn("invalid lease", stolen.content[0].text)
                acknowledged = payload(await worker.call_tool("mios_agent_ack", {
                    **worker_identity, "message_id": "proof-1"}))
                self.assertEqual(acknowledged["status"], "received")
                retry = payload(await head.call_tool("mios_agent_send", message))
                self.assertEqual(retry["status"], "received")
                self.assertTrue(retry["duplicate"])
                self.assertEqual(payload(await worker.call_tool("mios_agent_receive", worker_identity))["messages"], [])
                offline = await head.call_tool("mios_agent_send", {
                    **message, "to": "DEVLOOP-PLANTED-ABSENT", "message_id": "missing-1"})
                self.assertTrue(offline.model_dump(by_alias=True).get("isError"))
                self.assertIn("offline", offline.content[0].text)
                public = payload(await worker.call_tool("mios_agent_list", {}))
                self.assertNotIn(registered["token"], json.dumps(public))
                await worker.call_tool("mios_agent_unregister", worker_identity)
                await head.call_tool("mios_agent_unregister", identity)

    async def test_cancelled_command_retires_occupied_slot(self):
        running = asyncio.create_task(self.execute("sleep 30", slot=3))
        await asyncio.sleep(0.3)
        running.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await running
        recovered = await self.execute("printf CANCEL-RECOVERED", slot=3)
        self.assertFalse(recovered.model_dump(by_alias=True).get("isError"), recovered)
        self.assertIn("CANCEL-RECOVERED", payload(recovered)["output"])

    async def test_existing_tools_dispatch_and_resources_over_stdio(self):
        from mcp import Client, StdioServerParameters
        catalog = relay._build_catalog_from_ssot()
        catalog += [{"name": name, "description": "MiOS API preservation probe",
                     "inputSchema": {"type": "object", "properties": {}}}
                    for name in ("mios_test_verb", "mios_skill__test", "mios_recipe__test")]
        resource = {"uri": "mios://recipe/test", "name": "test", "mimeType": "text/plain"}

        class Backend(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, body):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def do_GET(self):
                if self.path == "/v1/tools":
                    self.reply({"tools": catalog})
                elif self.path == "/v1/resources":
                    self.reply({"resources": [resource]})
                elif "DEVLOOP-PLANTED-MISSING" in self.path:
                    self.reply({"error": "DEVLOOP-PLANTED-MISSING"})
                else:
                    self.reply({"contents": [{"uri": resource["uri"], "mimeType": "text/plain", "text": "RESOURCE-PRESERVED"}]})

            def do_POST(self):
                args = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.reply({"success": not args.get("args", {}).get("fail"), "path": self.path, "body": args})

        backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
        thread = threading.Thread(target=backend.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as cache:
                env = dict(os.environ, MIOS_AGENT_PIPE_URL=f"http://127.0.0.1:{backend.server_port}",
                           MIOS_MCP_TOOLS_CACHE=str(Path(cache) / "tools.json"))
                async with Client(StdioServerParameters(command=sys.executable, args=[str(RELAY)], env=env)) as client:
                    names = {t.name for t in (await client.list_tools()).tools}
                    self.assertEqual({n for n in names if not n.startswith(("mios_tmux_", "mios_agent_")) and n != "translate_frames"}, {t["name"] for t in catalog})
                    for name, path, body in (
                        ("mios_test_verb", "/v1/dispatch", {"tool": "mios_test_verb", "args": {"x": 1}}),
                        ("mios_skill__test", "/skills/run", {"name": "test", "params": {"x": 1}}),
                        ("mios_recipe__test", "/v1/dispatch", {"tool": "os_recipe", "args": {"name": "test", "params": {"x": 1}}}),
                    ):
                        result = await client.call_tool(name, {"x": 1})
                        self.assertFalse(result.model_dump(by_alias=True).get("isError"), result)
                        self.assertEqual(payload(result)["path"], path)
                        self.assertEqual(payload(result)["body"], body)
                    targeted = await client.call_tool("mios_recipe__test", {"x": 1, "os": "windows"})
                    self.assertEqual(payload(targeted)["body"], {
                        "tool": "os_recipe", "args": {"name": "test", "params": {"x": 1}, "os": "windows"}})
                    bad = await client.call_tool("mios_test_verb", {"fail": True})
                    self.assertTrue(bad.model_dump(by_alias=True).get("isError"), bad)
                    resources = (await client.list_resources()).resources
                    self.assertEqual([str(r.uri) for r in resources], [resource["uri"]])
                    read = await client.read_resource(resource["uri"])
                    self.assertEqual(read.contents[0].text, "RESOURCE-PRESERVED")
                    with self.assertRaisesRegex(Exception, "DEVLOOP-PLANTED-MISSING"):
                        await client.read_resource("mios://recipe/DEVLOOP-PLANTED-MISSING")
        finally:
            await asyncio.to_thread(backend.shutdown)
            backend.server_close()

    async def test_http_sessions_and_legacy_protocol_are_isolated(self):
        from mcp import Client
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
        with tempfile.TemporaryDirectory(prefix="mios-http-test-") as directory, tempfile.TemporaryFile() as log:
            env = dict(os.environ, MIOS_PORT_MCP=str(port), RUNTIME_DIRECTORY=directory)
            process = subprocess.Popen([sys.executable, str(RELAY), "--http", "--tmux-only"],
                                       env=env, stdout=log, stderr=log)
            try:
                url = f"http://127.0.0.1:{port}"
                for _attempt in range(100):
                    try:
                        with urllib.request.urlopen(url + "/health", timeout=0.2) as response:
                            self.assertEqual(json.load(response)["status"], "ok")
                        break
                    except OSError:
                        if process.poll() is not None:
                            log.seek(0)
                            self.fail(log.read().decode())
                        await asyncio.sleep(0.05)
                else:
                    self.fail("HTTP server did not become healthy")
                async with Client(url + "/mcp") as other:
                    async with Client(url + "/mcp") as first:
                        tools = (await first.list_tools()).tools
                        self.assertIn("mios_tmux_execute_command", {t.name for t in tools})
                        denied = await first.call_tool("mios_tmux_execute_command", {"command": "printf DEVLOOP-PLANTED-SHARED"})
                        self.assertTrue(denied.model_dump(by_alias=True).get("isError"), denied)
                        a = payload(await first.call_tool("mios_tmux_session_open", {}))["session"]
                        b = payload(await other.call_tool("mios_tmux_session_open", {}))["session"]
                        self.assertNotEqual(a, b)
                        result = await first.call_tool("mios_tmux_execute_command", {"session": a, "command": 'printf FIRST > "$HOME/client-marker"'})
                        self.assertEqual(payload(result)["exitCode"], 0, result)
                        result = await first.call_tool("mios_tmux_execute_command", {"session": a, "command": 'cat "$HOME/client-marker"'})
                        self.assertEqual(payload(result)["output"], "FIRST")
                        result = await other.call_tool("mios_tmux_execute_command", {"session": b, "command": 'test ! -e "$HOME/client-marker" && printf PRIVATE'})
                        self.assertEqual(payload(result)["output"], "PRIVATE")
                        await first.call_tool("mios_tmux_session_close", {"session": a})
                        denied = await first.call_tool("mios_tmux_execute_command", {"session": a, "command": "printf DEVLOOP-PLANTED-REUSE"})
                        self.assertTrue(denied.model_dump(by_alias=True).get("isError"), denied)
                    result = await other.call_tool("mios_tmux_execute_command", {"session": b, "command": "printf HTTP-SURVIVOR"})
                    self.assertIn("HTTP-SURVIVOR", payload(result)["output"])
                    await other.call_tool("mios_tmux_session_close", {"session": b})
                async with Client(url + "/mcp", mode="legacy") as legacy:
                    token = payload(await legacy.call_tool("mios_tmux_session_open", {}))["session"]
                    result = await legacy.call_tool("mios_tmux_execute_command", {"session": token, "command": "printf LEGACY-PRESERVED"})
                    self.assertEqual(payload(result)["output"], "LEGACY-PRESERVED")
            finally:
                process.terminate()
                await asyncio.to_thread(process.wait, 10)
            self.assertEqual(list(Path(directory).iterdir()), [], "HTTP shutdown leaked tmux sessions")

    async def test_installer_refuses_corrupt_archive_before_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            target = Path(directory) / "target"
            config_path = source / "usr/share/mios/mios.toml"
            config_path.parent.mkdir(parents=True)
            config_path.write_bytes((ROOT / "usr/share/mios/mios.toml").read_bytes())
            broken = source / asset["path"]
            broken.parent.mkdir(parents=True)
            broken.write_bytes(b"DEVLOOP-PLANTED-CORRUPTION")
            existing = target / CONFIG["binary"].lstrip("/")
            existing.parent.mkdir(parents=True)
            existing.write_bytes(b"PREVIOUS-BINARY")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                relay._install_native(source, target)
            self.assertEqual(existing.read_bytes(), b"PREVIOUS-BINARY")

    async def test_translate_frames_tool_positive_and_negative_controls(self):
        from mcp import Client, StdioServerParameters
        import mios_translate
        env = dict(os.environ, MIOS_AGENT_PIPE_URL="http://127.0.0.1:1",
                   MIOS_MCP_LIST_TIMEOUT="1", MIOS_MCP_TOOLS_CACHE=os.devnull)
        async with Client(StdioServerParameters(command=sys.executable, args=[str(RELAY)], env=env)) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            self.assertIn("translate_frames", names)
            self.assertIn("mios_tmux_nested_workflow", names)

            # Positive control: auto-detect AGY frames
            res = await client.call_tool("translate_frames", {
                "source": "auto",
                "frames": [
                    {"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "POSITIVE-TRANSLATION"}},
                    {"event": "result", "result": {"status": "SUCCESS", "response": "COMPLETE"}}
                ],
                "evidence": {
                    "diff_bytes": 100, "positive": True, "negative": True, "tree_restored": True, "exit_code": 0
                }
            })
            self.assertFalse(res.model_dump(by_alias=True).get("isError"), res)
            data = payload(res)
            self.assertEqual(data["schema"], "loop.v1")
            self.assertEqual(data["events"][0]["text"], "POSITIVE-TRANSLATION")
            self.assertEqual(data["events"][1]["status"], "delivered")
            self.assertEqual(data["responses_items"][0]["type"], "message")

            # Positive control: Chat Completions streaming accumulation
            res = await client.call_tool("translate_frames", {
                "source": "chat_completions",
                "frames": [
                    {"object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "test_fn", "arguments": "{\"x\":"}}]}}]},
                    {"object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "42}"}}]}}]},
                    {"object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
                ]
            })
            self.assertFalse(res.model_dump(by_alias=True).get("isError"), res)
            data = payload(res)
            self.assertEqual(data["events"][0]["arguments"], {"x": 42})
            self.assertEqual(data["events"][1]["status"], "unverified")

            # Negative control: credential field refusal
            bad_cred = await client.call_tool("translate_frames", {
                "source": "openai_chat",
                "frames": [{"object": "chat.completion", "choices": [{"message": {"role": "assistant", "api_key": "DEVLOOP-PLANTED-SECRET"}}]}]
            })
            self.assertTrue(bad_cred.model_dump(by_alias=True).get("isError"), bad_cred)
            self.assertIn("CREDENTIAL FIELD REFUSED: api_key", bad_cred.content[0].text)

            # Negative control: unknown source
            bad_source = await client.call_tool("translate_frames", {
                "source": "DEVLOOP-PLANTED-UNKNOWN-SOURCE",
                "frames": [{}]
            })
            self.assertTrue(bad_source.model_dump(by_alias=True).get("isError"), bad_source)
            self.assertIn("UNKNOWN SOURCE", bad_source.content[0].text)

            # Negative control: vacuous demotion
            vacuous = await client.call_tool("translate_frames", {
                "source": "openai_chat",
                "frames": [{"object": "chat.completion", "choices": [{"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}]}],
                "evidence": {
                    "diff_bytes": 0, "positive": True, "negative": True, "tree_restored": True, "exit_code": 0
                }
            })
            self.assertFalse(vacuous.model_dump(by_alias=True).get("isError"), vacuous)
            self.assertEqual(payload(vacuous)["events"][1]["status"], "vacuous")

    async def test_nested_workflow_and_parallel_slots_isolation(self):
        from mcp import Client, StdioServerParameters
        with tempfile.TemporaryDirectory(prefix="mios-mock-agent-bin-") as mock_bin:
            env = self.mock_agent_commands(mock_bin)
            async with Client(StdioServerParameters(command=sys.executable, args=[str(RELAY)], env=env)) as client:
                # Positive control: execute nested workflow in slot 1 and slot 2 in parallel
                results = await asyncio.gather(
                    client.call_tool("mios_tmux_nested_workflow", {"agent": "codex", "task": "--version", "slot": 1}),
                    client.call_tool("mios_tmux_nested_workflow", {"agent": "agy", "task": "--version", "slot": 2}),
                )
                for res in results:
                    self.assertFalse(res.model_dump(by_alias=True).get("isError"), res)
                    p = payload(res)
                    self.assertEqual(p["status"], "completed")
                    self.assertEqual(p["exitCode"], 0)
                    self.assertTrue(len(p["output"]) > 0)
                    self.assertTrue(p["receiptVerified"])
                    self.assertEqual(p["communication"]["status"], "not_requested")
                    self.assertFalse(p["communication"]["acknowledged"])
                    self.assertFalse(p["communication"]["replyReceived"])
                self.assertEqual(payload(results[0])["agent"], "codex")
                self.assertEqual(payload(results[0])["slot"], 1)
                self.assertEqual(payload(results[1])["agent"], "agy")
                self.assertEqual(payload(results[1])["slot"], 2)

                # Negative control: unknown agent rejected
                unknown = await client.call_tool("mios_tmux_nested_workflow", {"agent": "DEVLOOP-PLANTED-AGENT", "task": "--version"})
                self.assertTrue(unknown.model_dump(by_alias=True).get("isError"), unknown)
                self.assertIn("not in the SSOT CLI catalog", unknown.content[0].text)

                # Negative control: invalid slot number rejected
                bad_slot = await client.call_tool("mios_tmux_nested_workflow", {"agent": "codex", "task": "--version", "slot": 9999})
                self.assertTrue(bad_slot.model_dump(by_alias=True).get("isError"), bad_slot)
                self.assertIn("tmux slot must be an integer", bad_slot.content[0].text)

    async def test_nested_receipts_failures_and_fabricated_delivery(self):
        from mcp import Client, StdioServerParameters
        script = ("case \"$*\" in\n"
                  "  *PLANTED-EXIT*) printf 'DEVLOOP-PLANTED-EXIT'; exit 23;;\n"
                  "  *PLANTED-TIMEOUT*) printf 'DEVLOOP-PLANTED-TIMEOUT'; sleep 30;;\n"
                  "  *) printf '%s\\n' '{\"event\":\"result\",\"result\":{\"status\":\"SUCCESS\",\"response\":\"DEVLOOP-PLANTED-DELIVERED\"}}';;\n"
                  "esac\n")
        with tempfile.TemporaryDirectory(prefix="mios-nested-failure-bin-") as directory:
            env = self.mock_agent_commands(directory, script)
            async with Client(StdioServerParameters(command=sys.executable, args=[str(RELAY)], env=env)) as client:
                failed = await client.call_tool("mios_tmux_nested_workflow", {
                    "agent": "codex", "task": "DEVLOOP-PLANTED-EXIT", "slot": 6})
                self.assertTrue(failed.model_dump(by_alias=True).get("isError"))
                self.assertEqual(payload(failed)["exitCode"], 23)
                self.assertEqual(payload(failed)["status"], "failed")
                self.assertIn("DEVLOOP-PLANTED-EXIT", payload(failed)["output"])
                timed = await client.call_tool("mios_tmux_nested_workflow", {
                    "agent": "codex", "task": "DEVLOOP-PLANTED-TIMEOUT", "slot": 7, "timeoutSeconds": 1})
                self.assertTrue(timed.model_dump(by_alias=True).get("isError"))
                self.assertTrue(payload(timed)["timedOut"])
                self.assertEqual(payload(timed)["status"], "failed")
                slots = payload(await client.call_tool("mios_tmux_list_slots", {}))
                self.assertNotIn(7, [row["slot"] for row in slots])
                fabricated = await client.call_tool("mios_tmux_nested_workflow", {
                    "agent": "codex", "task": "bounded transport test", "slot": 8})
                self.assertFalse(fabricated.model_dump(by_alias=True).get("isError"))
                data = payload(fabricated)
                self.assertEqual(data["status"], "completed")
                self.assertEqual(data["communication"]["status"], "not_requested")
                self.assertFalse(data["communication"]["acknowledged"])
                statuses = [event.get("status") for event in data["translation"]["events"]]
                self.assertNotIn("delivered", statuses)
                self.assertIn("unverified", statuses)

    async def test_nested_busy_pane_is_refused_and_auto_reservation_is_distinct(self):
        with tempfile.TemporaryDirectory(prefix="mios-nested-busy-bin-") as directory:
            env = self.mock_agent_commands(directory)
            with patch.dict(os.environ, env):
                other = relay._TmuxBridge(CONFIG)
                try:
                    await other.start()
                    busy = await other.call("mios_tmux_start_and_watch", {
                        "slot": 1, "command": "printf 'DEVLOOP-PLANTED-BUSY\\n'; sleep 30",
                        "pattern": "DEVLOOP-PLANTED-BUSY", "timeout": 5})
                    self.assertFalse(busy.model_dump(by_alias=True).get("isError"), busy)
                    with self.assertRaisesRegex(ValueError, "busy"):
                        await other.nested_workflow({"agent": "codex", "task": "--version", "slot": 1})
                    results = await asyncio.gather(
                        other.nested_workflow({"agent": "codex", "task": "--version"}),
                        other.nested_workflow({"agent": "agy", "task": "--version"}))
                    self.assertNotEqual(results[0]["slot"], results[1]["slot"])
                    self.assertNotIn(1, [row["slot"] for row in results])
                    self.assertTrue(all(row["status"] == "completed" for row in results))
                    state = payload(await other.call("mios_tmux_pane_state", {"slot": 1}))
                    self.assertNotEqual(state["foregroundCmd"], "bash")
                finally:
                    await other.close()

    async def test_nested_endpoint_is_resolved_without_ambient_endpoint(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MIOS_AI_ENDPOINT", None)
            other = relay._TmuxBridge(CONFIG)
            try:
                await other.start()
                self.assertEqual(other.env["MIOS_AI_ENDPOINT"], relay.mios_toml.emit_exports()["MIOS_AI_ENDPOINT"])
                self.assertNotIn("${", other.env["MIOS_AI_ENDPOINT"])
                self.assertNotIn("MIOS_AI_KEY", other.env)
            finally:
                await other.close()

    async def test_nested_missing_or_fabricated_exit_receipt_is_failed(self):
        from mcp import types
        async def fake_receipt(*_args):
            data = {"status": "delivered", "exitCode": "0", "output": "DEVLOOP-PLANTED-FAKE-RECEIPT"}
            return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(data))], structuredContent=data)
        with patch.object(self.bridge, "_call_locked", fake_receipt):
            data = await self.bridge.nested_workflow({"agent": "codex", "task": "--version", "slot": 9})
        self.assertEqual(data["status"], "failed")
        self.assertIsNone(data["exitCode"])
        self.assertFalse(data["receiptVerified"])
        self.assertFalse(data["communication"]["acknowledged"])
        self.assertIn("no verified process exit receipt", data["error"])

    def test_terminal_ansi_cleaning_and_receipt_extraction(self):
        import mios_translate
        raw_terminal = "\x1b[32m[PASS]\x1b[0m \x1b[1mCommand completed\x1b[0m\r\n{\"exitCode\": 0, \"output\": \"OK\"}\n"
        clean = mios_translate.strip_ansi(raw_terminal)
        self.assertNotIn("\x1b", clean)
        self.assertIn("[PASS] Command completed", clean)
        receipt = mios_translate.extract_embedded_receipt(raw_terminal)
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt["exitCode"], 0)
        self.assertEqual(receipt["output"], "OK")

if __name__ == "__main__":
    if "--negative-keybindings" in sys.argv:
        data = relay.mios_toml.load_merged()["keybindings"]
        data["actions"][1]["key"] = data["actions"][0]["key"]
        with tempfile.NamedTemporaryFile(mode="w") as source:
            json.dump({"keybindings":data},source)
            source.flush()
            result = subprocess.run(["/usr/libexec/mios/mios-unit-gen","keybindings","--from-json",source.name,"--emit-json"],capture_output=True,text=True)
        print("DEVLOOP-PLANTED-KEYBIND", result.stderr)
        sys.exit(result.returncode)
    if "--negative-receipt" in sys.argv:
        async def negative():
            bridge = relay._TmuxBridge(CONFIG)
            try:
                result = await bridge.call("mios_tmux_execute_command", {
                    "command": "printf DEVLOOP-PLANTED-EXIT; (exit 23)"})
                print(payload(result)["output"])
                return payload(result)["exitCode"] if result.model_dump(by_alias=True).get("isError") else 0
            finally:
                await bridge.close()
        sys.exit(asyncio.run(negative()))
    unittest.main(verbosity=2)
