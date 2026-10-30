#!/usr/bin/env python3
# AI-hint: Unit + protocol tests of caller-owned MCP federation: [mcp.federation] policy validation, tier ownership (user-only ids run as the caller; system ids never), a real stdio fixture server through Federation and through the actual mios-mcp-server front door, env isolation, native-name precedence, the spawn budget (cached catalog lists without spawning, idle stop, bounded exponential restarts) and the --tmux-only / disabled / HTTP / cache-off negative controls.
# AI-related: usr/lib/mios/mios_mcp_federation.py, usr/libexec/mios/mios-mcp-server, usr/share/mios/mios.toml [mcp.federation], usr/share/doc/mios/blender-mcp.md

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SSOT = ROOT / "usr/share/mios/mios.toml"
SERVER = ROOT / "usr/libexec/mios/mios-mcp-server"


def _sdk_python():
    """The front door's SDK 2.x interpreter from the SSOT ([mcp].python)."""
    return tomllib.loads(SSOT.read_text())["mcp"]["python"]


if importlib.util.find_spec("mcp") is None:
    PY = _sdk_python()
    if os.path.realpath(PY) != os.path.realpath(sys.executable) and os.access(PY, os.X_OK):
        os.execv(PY, [PY, *sys.argv])
    raise SystemExit("the MCP Python SDK 2.x ([mcp].python) is required")

sys.path.insert(0, str(ROOT / "usr/lib/mios"))
import mios_mcp_federation as fed  # noqa: E402

POLICY = {"enabled": True, "serve_http": False, "startup_timeout_s": 30, "call_timeout_s": 30,
          "restart_backoff_s": 0, "max_tools_per_server": 16, "log_directory": "mios/mcp-federation",
          "max_restarts": 5, "catalog_cache": True, "idle_stop_s": 0}

FIXTURE = textwrap.dedent('''
    import os, sys
    from mcp.server.mcpserver import MCPServer
    server = MCPServer("mios-fixture")

    @server.tool()
    def echo(text: str) -> str:
        """Echo text back."""
        return "echo:" + text

    @server.tool()
    def env_keys() -> str:
        """Report the environment names this child received."""
        return ",".join(sorted(os.environ))

    @server.tool()
    def pid() -> str:
        """This child's process id."""
        return str(os.getpid())

    @server.tool()
    def translate_frames() -> str:
        """Collides with a native name once namespaced as ''."""
        return "fixture"

    if os.environ.get("FIXTURE_MARK"):
        with open(os.environ["FIXTURE_MARK"], "a") as mark:
            mark.write(f"{os.getpid()}\\n")
    if os.environ.get("FIXTURE_CRASH") == "1":
        sys.exit(3)
    server.run()
''')


def toml_table(path, data):
    """Tiny TOML writer for the flat test tiers used here."""
    lines = []

    def emit(prefix, table):
        scalars = {k: v for k, v in table.items() if not isinstance(v, dict)}
        if prefix:
            lines.append(f"[{prefix}]")
        for key, value in scalars.items():
            lines.append(f"{key} = {json.dumps(value)}")
        for key, value in table.items():
            if isinstance(value, dict):
                emit(f"{prefix}.{key}" if prefix else key, value)

    emit("", data)
    Path(path).write_text("\n".join(lines) + "\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mios-fed-test-")
        self.dir = Path(self.tmp.name)
        self.fixture = self.dir / "fixture.py"
        self.fixture.write_text(FIXTURE)
        self.system = self.dir / "system.toml"
        self.user = self.dir / "user.toml"
        toml_table(self.system, {"mcp": {"servers": {"vendor_only": {
            "transport": "stdio", "command": sys.executable, "args": ["-c", "pass"]}}}})

    def tearDown(self):
        self.tmp.cleanup()

    def declare(self, **servers):
        toml_table(self.user, {"mcp": {"servers": servers}})
        return fed.caller_servers([str(self.system)], [str(self.user)])

    def fixture_decl(self, **extra):
        return {"transport": "stdio", "command": sys.executable, "args": ["-I", str(self.fixture)],
                "namespace": "fx_", "label": "Fixture", **extra}


class PolicyTests(Base):
    def test_vendor_ssot_policy_is_valid(self):
        config = fed.load_config(tomllib.loads(SSOT.read_text()))
        self.assertTrue(config["enabled"])
        self.assertFalse(config["serve_http"], "HTTP federation must be opt-in")
        # The spawn budget is on by default: no unbounded per-session children.
        self.assertTrue(config["catalog_cache"])
        self.assertGreater(config["idle_stop_s"], 0)
        self.assertGreaterEqual(config["restart_backoff_s"], 1)

    def test_missing_and_invalid_policy_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            fed.load_config({"mcp": {}})
        for key, bad in (("enabled", 1), ("startup_timeout_s", 0), ("max_tools_per_server", 1.5),
                         ("log_directory", "/abs"), ("log_directory", "a/../b"), ("catalog_cache", "yes"),
                         ("max_restarts", 0), ("max_restarts", 2.5), ("idle_stop_s", -1)):
            with self.subTest(key=key, bad=bad), self.assertRaises(ValueError):
                fed.load_config({"mcp": {"federation": {**POLICY, key: bad}}})


class OwnershipTests(Base):
    def test_user_only_declaration_runs_as_caller(self):
        specs, rows = self.declare(fixture=self.fixture_decl(env={"FX_FLAG": True, "FX_PORT": 1}))
        self.assertEqual([s.id for s in specs], ["fixture"])
        self.assertEqual(specs[0].env, {"FX_FLAG": "true", "FX_PORT": "1"})
        self.assertEqual(rows, [])

    def test_system_owned_id_is_never_spawned_by_the_front_door(self):
        specs, rows = self.declare(vendor_only={"enabled": False})
        self.assertEqual(specs, [])
        self.assertEqual(rows[0]["status"], "system-owned")

    def test_invalid_declarations_are_reported_not_run(self):
        specs, rows = self.declare(
            http_one={"transport": "streamable-http", "url": "http://127.0.0.1:1/mcp"},
            missing={"command": str(self.dir / "absent")},
            badns=self.fixture_decl(namespace="1bad"),
            off=self.fixture_decl(enabled=False))
        self.assertEqual(specs, [])
        status = {row["id"]: row["status"] for row in rows}
        self.assertEqual(status, {"http_one": "config-error", "missing": "config-error",
                                  "badns": "config-error", "off": "disabled"})


class MemberBase(Base):
    def run_async(self, coroutine):
        return asyncio.run(asyncio.wait_for(coroutine, 120))

    def federation(self, policy=None, **decl):
        specs, rows = self.declare(fixture=self.fixture_decl(**decl))
        return fed.Federation({**POLICY, **(policy or {})}, specs, rows, state_home=str(self.dir / "state"))

    def spawned(self, mark):
        return len(mark.read_text().split()) if mark.exists() else 0


class MemberTests(MemberBase):

    def test_round_trip_namespace_and_native_precedence(self):
        async def body():
            federation = self.federation(env={"FX_DECLARED": "yes"})
            os.environ["FRONT_DOOR_SECRET_PROBE"] = "must-not-leak"
            try:
                federation.start_background()
                rows = await federation.tool_rows({"fx_translate_frames"})
                names = sorted(row["name"] for row in rows)
                self.assertEqual(names, ["fx_echo", "fx_env_keys", "fx_pid"])
                self.assertEqual(federation.collisions[0]["name"], "fx_translate_frames")
                self.assertTrue(rows[0]["description"].startswith("[Fixture] "))
                result = await federation.call("fx_echo", {"text": "hi"})
                self.assertFalse(result.is_error)
                self.assertEqual(result.content[0].text, "echo:hi")
                self.assertEqual(federation.members[0].failures, 0)
                keys = (await federation.call("fx_env_keys", {})).content[0].text.split(",")
                self.assertIn("FX_DECLARED", keys)
                self.assertNotIn("FRONT_DOOR_SECRET_PROBE", keys)
                self.assertTrue(federation.may_own("fx_anything"))
                self.assertFalse(federation.may_own("mios_tmux_list_slots"))
                log = Path(federation.log_dir) / "fixture.log"
                self.assertEqual(log.stat().st_mode & 0o777, 0o600)
                self.assertEqual(Path(federation.log_dir).stat().st_mode & 0o777, 0o700)
            finally:
                os.environ.pop("FRONT_DOOR_SECRET_PROBE", None)
                await federation.close()
        self.run_async(body())

    def test_allowed_tools_filter(self):
        async def body():
            federation = self.federation(allowed_tools=["echo"])
            try:
                rows = await federation.tool_rows(set())
                self.assertEqual([row["name"] for row in rows], ["fx_echo"])
            finally:
                await federation.close()
        self.run_async(body())

    def test_crashing_server_reports_failure_and_exports_nothing(self):
        async def body():
            federation = self.federation(env={"FIXTURE_CRASH": "1"})
            try:
                rows = await federation.tool_rows(set())
                self.assertEqual(rows, [])
                status = federation.status()["servers"][0]
                self.assertEqual(status["status"], "failed", status)
            finally:
                await federation.close()
        self.run_async(body())


class SpawnBudgetTests(MemberBase):
    """One front door per agent session must never become a process storm."""

    def test_cached_catalog_lists_without_spawning(self):
        mark = self.dir / "spawns"

        async def body():
            first = self.federation(env={"FIXTURE_MARK": str(mark), "FX_SECRET": "s3cr3t-value"})
            try:
                await first.tool_rows(set())
            finally:
                await first.close()
            self.assertEqual(self.spawned(mark), 1)
            cache = Path(first.log_dir) / "fixture.tools.json"
            self.assertEqual(cache.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("s3cr3t-value", cache.read_text(), "declared env values never reach the cache")
            # A NEW front door (the next agent session): lists from the cache, spawns nothing.
            second = self.federation(env={"FIXTURE_MARK": str(mark), "FX_SECRET": "s3cr3t-value"})
            try:
                second.start_background()
                self.assertIsNone(second.warm, "a cached member is not warmed")
                rows = await second.tool_rows(set())
                self.assertIn("fx_echo", [row["name"] for row in rows])
                self.assertEqual(self.spawned(mark), 1, "listing a cached catalog must not spawn")
                self.assertEqual(second.status()["servers"][0]["catalog"], "cached")
                result = await second.call("fx_echo", {"text": "cold"})
                self.assertEqual(result.content[0].text, "echo:cold")
                self.assertEqual(self.spawned(mark), 2, "the first call starts it, once")
                await second.call("fx_echo", {"text": "warm"})
                self.assertEqual(self.spawned(mark), 2)
            finally:
                await second.close()
        self.run_async(body())

    def test_negative_cache_off_or_changed_declaration_spawns(self):
        mark = self.dir / "spawns"

        async def body():
            for policy, env in (({}, {}), ({"catalog_cache": False}, {}), ({}, {"FX_CHANGED": "1"})):
                federation = self.federation(policy, env={"FIXTURE_MARK": str(mark), **env})
                try:
                    await federation.tool_rows(set())
                finally:
                    await federation.close()
            # 1 (fills the cache) + 1 (cache off) + 1 (fingerprint changed)
            self.assertEqual(self.spawned(mark), 3)
        self.run_async(body())

    def test_idle_member_stops_and_restarts_on_demand(self):
        async def body():
            federation = self.federation({"idle_stop_s": 1})
            try:
                await federation.tool_rows(set())
                pid = int((await federation.call("fx_pid", {})).content[0].text)
                await asyncio.sleep(3)
                self.assertEqual(federation.status()["servers"][0]["status"], "idle-stopped")
                await asyncio.sleep(0.5)
                with self.assertRaises(ProcessLookupError, msg="the idle child must have exited"):
                    os.kill(pid, 0)
                again = int((await federation.call("fx_pid", {})).content[0].text)
                self.assertNotEqual(again, pid)
                self.assertEqual(federation.members[0].failures, 0, "an idle stop is not a failure")
            finally:
                await federation.close()
        self.run_async(body())

    def test_call_during_idle_teardown_gets_a_fresh_server(self):
        """A call that lands while an idle stop is still closing the child must
        not be handed the closing session (and must not count as a failure)."""
        async def body():
            federation = self.federation({"idle_stop_s": 1})
            try:
                await federation.tool_rows(set())
                first = int((await federation.call("fx_pid", {})).content[0].text)
                member = federation.members[0]
                caught = False
                for _ in range(600):  # bounded poll, 6 s at most
                    if member.status == "idle-stopped" and member.task is not None and not member.task.done():
                        caught = True
                        break
                    await asyncio.sleep(0.01)
                result = await federation.call("fx_pid", {})
                self.assertFalse(result.is_error, result.content[0].text)
                self.assertNotEqual(int(result.content[0].text), first)
                self.assertEqual(member.failures, 0)
                if caught:
                    self.assertEqual(member.spawns, 2)
            finally:
                await federation.close()
        self.run_async(body())

    def test_negative_idle_stop_disabled_keeps_member(self):
        async def body():
            federation = self.federation({"idle_stop_s": 0})
            try:
                await federation.tool_rows(set())
                pid = int((await federation.call("fx_pid", {})).content[0].text)
                await asyncio.sleep(3)
                os.kill(pid, 0)  # still alive
                self.assertEqual(federation.status()["servers"][0]["status"], "ready")
            finally:
                await federation.close()
        self.run_async(body())

    def test_crash_loop_is_bounded_by_max_restarts(self):
        mark = self.dir / "spawns"

        async def body():
            federation = self.federation({"max_restarts": 3, "catalog_cache": False},
                                         env={"FIXTURE_CRASH": "1", "FIXTURE_MARK": str(mark)})
            try:
                for _ in range(10):
                    await federation.tool_rows(set())
                    result = await federation.call("fx_echo", {"text": "x"}) if federation.owns("fx_echo") else None
                    self.assertIsNone(result)
                self.assertEqual(self.spawned(mark), 3, "exactly max_restarts spawns, then it gives up")
                self.assertEqual(federation.status()["servers"][0]["status"], "gave-up")
            finally:
                await federation.close()
        self.run_async(body())

    def test_negative_backoff_gates_immediate_restart(self):
        mark = self.dir / "spawns"

        async def body():
            federation = self.federation({"restart_backoff_s": 60, "catalog_cache": False},
                                         env={"FIXTURE_CRASH": "1", "FIXTURE_MARK": str(mark)})
            try:
                for _ in range(5):
                    await federation.tool_rows(set())
                self.assertEqual(self.spawned(mark), 1, "a failed server is not retried inside its backoff")
                member = federation.members[0]
                self.assertEqual(member.backoff_s(), 60)
                member.failures = 3
                self.assertEqual(member.backoff_s(), 240, "backoff doubles per consecutive failure")
                member.failures = 40
                self.assertEqual(member.backoff_s(), 3600, "backoff is capped")
            finally:
                await federation.close()
        self.run_async(body())


class FrontDoorTests(Base):
    """The actual mios-mcp-server over stdio, with a private caller tier."""

    def tier(self, federation=True, **servers):
        toml_table(self.user, {"mcp": {"tmux": {"enabled": False},
                                       "federation": {**POLICY, "enabled": federation},
                                       "servers": servers}})

    def env(self):
        env = {k: v for k, v in os.environ.items()
               if k not in {"MIOS_TOML", "TMUX", "TMUX_PANE", "MIOS_USER_TOML_D"}}
        # Hermetic: no live agent-pipe; the front door serves its SSOT floor catalog.
        env.update(MIOS_AGENT_PIPE_URL="http://127.0.0.1:9", MIOS_VENDOR_TOML=str(SSOT), MIOS_USER_TOML=str(self.user),
                   MIOS_USER_TOML_D=str(self.dir / "no-user.d"), XDG_STATE_HOME=str(self.dir / "state"))
        return env

    def session(self, *args, call=True):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def body():
            params = StdioServerParameters(command=sys.executable, args=[str(SERVER), *args], env=self.env())
            with open(self.dir / "front-door.log", "w") as err:
                async with stdio_client(params, errlog=err) as (read, write):
                    async with ClientSession(read, write) as client:
                        await client.initialize()
                        names = {tool.name for tool in (await client.list_tools()).tools}
                        echo = (await client.call_tool("fx_echo", {"text": "door"})
                                if call and "fx_echo" in names else None)
                        return names, echo
        return asyncio.run(asyncio.wait_for(body(), 180))

    def test_front_door_exports_and_dispatches_caller_server(self):
        self.tier(fixture=self.fixture_decl())
        names, echo = self.session()
        self.assertIn("fx_echo", names)
        self.assertIn("translate_frames", names, "native catalog must remain")
        self.assertEqual(echo.content[0].text, "echo:door")

    def test_second_front_door_lists_cached_tools_without_spawning(self):
        mark = self.dir / "spawns"
        self.tier(fixture=self.fixture_decl(env={"FIXTURE_MARK": str(mark)}))
        self.session()
        self.assertEqual(len(mark.read_text().split()), 1)
        mark.unlink()
        names, _ = self.session(call=False)
        self.assertIn("fx_echo", names)
        self.assertFalse(mark.exists(), "a new session that only lists must not spawn the server")

    def test_negative_disabled_policy_exports_nothing(self):
        self.tier(federation=False, fixture=self.fixture_decl())
        names, _ = self.session()
        self.assertFalse(any(name.startswith("fx_") for name in names))

    def test_negative_terminal_only_child_never_federates(self):
        self.tier(fixture=self.fixture_decl())
        names, _ = self.session("--tmux-only")
        self.assertFalse(any(name.startswith("fx_") for name in names))

    def http_started(self, serve_http):
        """Did an HTTP front door start the caller's server at all?"""
        import time
        mark = self.dir / "started"
        self.tier(fixture=self.fixture_decl(env={"FIXTURE_MARK": str(mark)}))
        text = self.user.read_text().replace("serve_http = false", f"serve_http = {json.dumps(serve_http)}")
        self.user.write_text(text)
        env = {**self.env(), "MIOS_PORTS_MCP": "0"}  # any free loopback port
        log = open(self.dir / "http.log", "w")
        door = subprocess.Popen([sys.executable, str(SERVER), "--http"], env=env,
                                stdout=subprocess.DEVNULL, stderr=log)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not mark.exists():
                time.sleep(0.5)
                if door.poll() is not None:
                    break
            return mark.exists()
        finally:
            door.terminate()
            door.wait(timeout=30)
            log.close()

    def test_negative_http_front_door_never_starts_caller_servers(self):
        self.assertFalse(self.http_started(False))

    def test_http_front_door_starts_them_only_when_serve_http(self):
        self.assertTrue(self.http_started(True), (self.dir / "http.log").read_text()[-2000:])

    def test_federation_status_receipt(self):
        self.tier(fixture=self.fixture_decl(), broken={"command": str(self.dir / "absent")})
        run = subprocess.run([sys.executable, str(SERVER), "--federation-status"], env=self.env(),
                             capture_output=True, text=True, timeout=120)
        status = json.loads(run.stdout)
        self.assertEqual(run.returncode, 1, "a config-error row must fail the receipt")
        self.assertIn("fx_echo", status["tools"])
        rows = {row["id"]: row["status"] for row in status["servers"]}
        self.assertEqual(rows, {"fixture": "ready", "broken": "config-error"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
