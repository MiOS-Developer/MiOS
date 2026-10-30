#!/usr/bin/env python3
# AI-hint: LIVE opt-in test (MIOS_LIVE_NESTED=1) of the caller's Blender Lab MCP inside an owned nested MiOS PiP session: the installed Blender GUI runs as a Wayland client of the nested compositor with its add-on bridge on a private loopback port, and the actual mios-mcp-server federates [mcp.servers.blender] for MCP round trips (scene summary, in-process Wayland/session facts, window screenshot); the round-trip session lists from the cached catalog and starts blmcp on its first call. Negative controls: before Blender starts and after it exits the same tool returns a connection error. Primary compositor: read-only queries only.
# AI-related: tests/test-blender-mcp-real.py, usr/lib/mios/mios_mcp_federation.py, usr/libexec/mios/mios-mcp-server, usr/share/doc/mios/blender-mcp.md, tools/native/mios-gen/src/nested_session.rs

import asyncio
import base64
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("blender_mcp_real", HERE / "test-blender-mcp-real.py")
real = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(real)  # re-execs into the SDK interpreter when needed

OUT = Path(os.environ.get("MIOS_LIVE_OUT") or tempfile.mkdtemp(prefix="mios-blender-live-"))
OUT.mkdir(mode=0o700, parents=True, exist_ok=True)
LAUNCHER = os.environ.get("MIOS_GEN", "/usr/bin/mios-gen")
check, text, listening = real.check, real.text, real.listening

# Start the add-on's own bridge on the test port before its autostart timer
# fires (autostart then sees a running server and does nothing): the caller's
# saved preferences and configured port are never touched.
BRIDGE = """
import bpy, importlib
server = importlib.import_module('bl_ext.user_default.mcp.mcp_to_blender_server')
loop = importlib.import_module('bl_ext.user_default.mcp.execute_interactive')
server.start('127.0.0.1', {port})
bpy.app.timers.register(loop.run, first_interval=server.TIMER_INTERVAL_ACTIVE, persistent=True)
print('BLENDER_BRIDGE_STARTED', {port}, flush=True)
"""

FACTS = ("import bpy, os\nresult = {'background': bpy.app.background, 'version': bpy.app.version_string, "
         "'wayland_display': os.environ.get('WAYLAND_DISPLAY'), 'runtime': os.environ.get('XDG_RUNTIME_DIR'), "
         "'windows': len(bpy.context.window_manager.windows), 'pid': os.getpid()}")


def sh(args, env=None, timeout=30):
    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=timeout)


def main():
    spec, row = real.caller_blender()
    if spec is None:
        print(f"SKIP no caller-owned [mcp.servers.{real.SERVER_ID}] ({(row or {}).get('status', 'absent')})")
        return 0
    blender = spec.env.get("BLENDER_PATH")
    port = real.free_port()
    tier = OUT / "user.toml"
    real.write_tier(tier, spec, port)
    door_env = real.door_env(tier, OUT)
    tool = spec.namespace + "get_objects_summary"

    def door(calls):
        return asyncio.run(asyncio.wait_for(real.door(door_env, calls), 240))

    primary = sh(["pgrep", "-x", "Hyprland", "-o"]).stdout.strip()
    launcher_env = {k: v for k, v in os.environ.items() if k != "MIOS_TOML"}
    launcher_env["MIOS_VENDOR_TOML"] = str(real.SSOT)
    launcher = subprocess.Popen([LAUNCHER, "nested-session", "--layout", "pip"], env=launcher_env,
                                stdout=open(OUT / "launcher.log", "w"), stderr=subprocess.STDOUT)
    runtime = Path(os.environ["XDG_RUNTIME_DIR"]) / f"mios-nested-desktop-{launcher.pid}"
    nested_pid, blender_proc, env = None, None, None
    try:
        for _ in range(300):
            if (runtime / "session.json").is_file():
                session = json.loads((runtime / "session.json").read_text())
                nested_pid = session["instance"]["pid"]
                env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime),
                           HYPRLAND_INSTANCE_SIGNATURE=session["instance"]["instance"],
                           WAYLAND_DISPLAY=session["instance"]["wl_socket"])
                for key in ("DISPLAY", "MIOS_TOML", "TMUX", "TMUX_PANE"):
                    env.pop(key, None)
                break
            time.sleep(0.2)
        if not check("owned nested PiP session", env is not None, runtime.name):
            return 1

        _, (before,) = door([(tool, {})])
        check("NEGATIVE: before Blender starts the tool reports a connection error",
              before is not None and before.is_error and "Cannot connect" in text(before), text(before)[:90])

        blender_proc = subprocess.Popen([blender, "--online-mode", "--python-expr", BRIDGE.format(port=port)],
                                        env=env, stdout=open(OUT / "blender.log", "w"),
                                        stderr=subprocess.STDOUT, start_new_session=True)

        def nested_blender():
            try:
                clients = json.loads(sh(["hyprctl", "-j", "clients"], env=env).stdout or "[]")
            except ValueError:
                return None
            return next((c for c in clients if c.get("pid") == blender_proc.pid), None)

        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not (listening(port) and nested_blender()):
            if blender_proc.poll() is not None:
                break
            time.sleep(0.5)
        window = nested_blender()
        check("Blender GUI mapped as a client of the nested compositor", window,
              window and f"class={window.get('class')} size={window.get('size')}")
        check("Blender add-on bridge listening on the private port", listening(port), port)
        primary_clients = json.loads(sh(["hyprctl", "-j", "clients"]).stdout or "[]")
        check("Blender is not a client of the primary compositor",
              not any(c.get("pid") == blender_proc.pid for c in primary_clients))

        # Spawn budget: the first door cached the catalog, so this session lists
        # blender_* from the cache and starts blmcp on its first call only.
        cache = OUT / real.fed.load_config(real.tomllib.loads(real.SSOT.read_text()))["log_directory"] / f"{spec.id}.tools.json"
        check("spawn budget: catalog cached by the first session (0600)",
              cache.is_file() and cache.stat().st_mode & 0o777 == 0o600)
        names, (summary, facts, shot) = door([
            (tool, {}),
            (spec.namespace + "execute_blender_code", {"code": FACTS}),
            (spec.namespace + "get_screenshot_of_window_as_image", {})])
        check("MCP round trip: scene summary from the nested Blender",
              summary is not None and not summary.is_error and "Cube" in text(summary), text(summary)[:60])
        try:
            data = json.loads(text(facts))["result"]
        except (ValueError, KeyError, TypeError):
            data = {}
        check("MCP round trip: Blender runs interactive on the nested Wayland socket",
              data.get("background") is False and data.get("wayland_display") == env["WAYLAND_DISPLAY"]
              and data.get("runtime") == str(runtime) and data.get("pid") == blender_proc.pid, data)
        image = next((c for c in (shot.content if shot else []) if getattr(c, "type", "") == "image"), None)
        if image is not None:
            (OUT / "blender-window.png").write_bytes(base64.b64decode(image.data))
        check("MCP round trip: window screenshot returned an image", image is not None and len(image.data) > 1000,
              OUT / "blender-window.png")

        os.killpg(blender_proc.pid, signal.SIGTERM)
        blender_proc.wait(timeout=30)
        blender_proc = None
        _, (after,) = door([(tool, {})])
        check("NEGATIVE: after Blender exits the tool reports a connection error",
              after is not None and after.is_error and "Cannot connect" in text(after), text(after)[:90])
    except Exception as error:  # noqa: BLE001
        check("driver", False, repr(error))
    finally:
        if blender_proc is not None:
            try:
                os.killpg(blender_proc.pid, signal.SIGTERM)
                blender_proc.wait(timeout=30)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                os.killpg(blender_proc.pid, signal.SIGKILL)
        if nested_pid:
            try:
                os.kill(nested_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            launcher.wait(timeout=30)
        except subprocess.TimeoutExpired:
            launcher.kill()
        check("nested runtime cleaned up", not runtime.exists())
        check("primary compositor unchanged", sh(["pgrep", "-x", "Hyprland", "-o"]).stdout.strip() == primary)
    failed = [n for n, ok in real.results if not ok]
    print(f"{len(real.results) - len(failed)}/{len(real.results)} passed; evidence {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    if os.environ.get("MIOS_LIVE_NESTED") != "1" or not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        print("skip: live nested-session test (set MIOS_LIVE_NESTED=1 inside a MiOS desktop session)")
        sys.exit(0)
    sys.exit(main())
