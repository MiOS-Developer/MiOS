#!/usr/bin/env python3
# AI-hint: Real-binary test of the caller's Blender Lab MCP through the MiOS MCP front door: the installed Blender launches headless, the add-on bridge serves a private loopback port (background mode), and the actual mios-mcp-server federates the caller's [mcp.servers.blender] stdio server (blender_* tools, one scene round trip). Negative controls: no bridge -> tool error; federation off -> no blender_* tools. Opt-in (MIOS_TEST_LIVE=1); skips when the caller declares no Blender server.
# AI-related: usr/lib/mios/mios_mcp_federation.py, usr/libexec/mios/mios-mcp-server, usr/share/doc/mios/blender-mcp.md, tests/test-mcp-federation.py, tests/test-blender-mcp-live.py

import asyncio
import importlib.util
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SSOT = ROOT / "usr/share/mios/mios.toml"
SERVER = ROOT / "usr/libexec/mios/mios-mcp-server"
SERVER_ID = "blender"  # the caller-tier id this receipt verifies

if importlib.util.find_spec("mcp") is None:
    PY = tomllib.loads(SSOT.read_text())["mcp"]["python"]
    if os.path.realpath(PY) != os.path.realpath(sys.executable) and os.access(PY, os.X_OK):
        os.execv(PY, [PY, *sys.argv])
    raise SystemExit("the MCP Python SDK 2.x ([mcp].python) is required")

sys.path.insert(0, str(ROOT / "usr/lib/mios"))
import mios_mcp_federation as fed  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}".rstrip(), flush=True)
    return ok


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def listening(port):
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def caller_blender():
    """The caller's own Blender declaration (user tier only), validated."""
    for key in ("MIOS_TOML", "MIOS_VENDOR_TOML"):
        os.environ.pop(key, None)
    os.environ["MIOS_VENDOR_TOML"] = str(SSOT)
    specs, rows = fed.caller_servers()
    spec = next((s for s in specs if s.id == SERVER_ID), None)
    row = next((r for r in rows if r["id"] == SERVER_ID), None)
    return spec, row


def write_tier(path, spec, port, federation=True):
    """A private caller tier: only the Blender stdio declaration, on a test port."""
    env = {**spec.env, "BLENDER_MCP_HOST": "127.0.0.1", "BLENDER_MCP_PORT": str(port)}
    lines = ["[mcp.tmux]", "enabled = false", "[mcp.federation]", f"enabled = {json.dumps(federation)}",
             f"[mcp.servers.{spec.id}]", 'transport = "stdio"', f"command = {json.dumps(spec.command)}",
             f"args = {json.dumps(spec.args)}", f"namespace = {json.dumps(spec.namespace)}",
             f"label = {json.dumps(spec.label)}"]
    if spec.cwd:
        lines.append(f"cwd = {json.dumps(spec.cwd)}")
    lines.append(f"[mcp.servers.{spec.id}.env]")
    lines += [f"{k} = {json.dumps(v)}" for k, v in env.items()]
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write("\n".join(lines) + "\n")


def door_env(tier, state):
    env = {k: v for k, v in os.environ.items() if k not in {"MIOS_TOML", "TMUX", "TMUX_PANE"}}
    env.update(MIOS_VENDOR_TOML=str(SSOT), MIOS_USER_TOML=str(tier),
               MIOS_USER_TOML_D=str(Path(tier).parent / "no-user.d"), XDG_STATE_HOME=str(state))
    return env


async def door(env, calls):
    """List tools and run calls through the actual front door over stdio."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)], env=env)
    with open(Path(env["XDG_STATE_HOME"]) / "front-door.log", "w") as err:
        async with stdio_client(params, errlog=err) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                names = sorted(t.name for t in (await client.list_tools()).tools)
                out = []
                for name, args in calls:
                    out.append(await client.call_tool(name, args) if name in names else None)
                return names, out


def text(result):
    return "" if result is None else " ".join(getattr(c, "text", "") for c in result.content)


def main():
    spec, row = caller_blender()
    if spec is None:
        print(f"SKIP no caller-owned [mcp.servers.{SERVER_ID}] ({(row or {}).get('status', 'absent')})")
        return 0
    blender = spec.env.get("BLENDER_PATH") or shutil.which("blender")
    check("receipt: Blender binary present and executable", blender and os.access(blender, os.X_OK), blender)
    check("receipt: MCP server interpreter present", os.access(spec.command, os.X_OK), spec.command)
    out = Path(tempfile.mkdtemp(prefix="mios-blender-real-"))
    os.chmod(out, 0o700)
    headless = subprocess.run([blender, "-b", "--factory-startup", "--python-expr",
                               "import bpy;print('BLENDER_VERSION_MARK', bpy.app.version_string)"],
                              capture_output=True, text=True, timeout=180)
    version = next((l.split(" ", 1)[1] for l in headless.stdout.splitlines() if l.startswith("BLENDER_VERSION_MARK")), "")
    check("Blender launches headless", headless.returncode == 0 and version, version)

    port = free_port()
    tier = out / "user.toml"
    write_tier(tier, spec, port)
    env = door_env(tier, out)

    # Negative control first: no bridge listening on the test port.
    names, (dead,) = asyncio.run(asyncio.wait_for(door(env, [(spec.namespace + "get_objects_summary", {})]), 240))
    tools = [n for n in names if n.startswith(spec.namespace)]
    check("front door lists the caller's Blender tools", len(tools) >= 10, f"{len(tools)} tools")
    check("NEGATIVE: no Blender bridge -> tool error, not a fake result",
          dead is not None and dead.is_error and "Cannot connect" in text(dead), text(dead)[:120])

    bridge = subprocess.Popen([blender, "-b", "--online-mode", "-c", "blender_mcp", "--host", "127.0.0.1",
                               "--port", str(port)], stdout=open(out / "bridge.log", "w"),
                              stderr=subprocess.STDOUT, start_new_session=True)
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if listening(port):
                break
            if bridge.poll() is not None:
                break
            time.sleep(0.5)
        check("Blender add-on bridge listening on the private loopback port", bridge.poll() is None, port)
        names, (summary, code) = asyncio.run(asyncio.wait_for(door(env, [
            (spec.namespace + "get_objects_summary", {}),
            (spec.namespace + "execute_blender_code",
             {"code": "import bpy\nresult = {'version': bpy.app.version_string}"})]), 240))
        check("round trip: blender_get_objects_summary through the front door",
              summary is not None and not summary.is_error and "Cube" in text(summary), text(summary)[:80])
        check("round trip: blender_execute_blender_code reports the running version",
              code is not None and not code.is_error and version.split()[0] in text(code), text(code)[:120])
        write_tier(tier, spec, port, federation=False)
        names, _ = asyncio.run(asyncio.wait_for(door(env, []), 240))
        check("NEGATIVE: federation disabled -> no Blender tools exported",
              not any(n.startswith(spec.namespace) for n in names))
    finally:
        os.killpg(bridge.pid, signal.SIGTERM)
        try:
            bridge.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(bridge.pid, signal.SIGKILL)
    failed = [n for n, ok in results if not ok]
    if not failed:
        shutil.rmtree(out, ignore_errors=True)
    print(f"{len(results) - len(failed)}/{len(results)} passed" + (f"; evidence {out}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    if os.environ.get("MIOS_TEST_LIVE") != "1":
        print("skip: real-binary Blender MCP test (set MIOS_TEST_LIVE=1; starts the caller's Blender)")
        sys.exit(0)
    sys.exit(main())
