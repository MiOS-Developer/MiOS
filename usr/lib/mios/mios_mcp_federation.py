# AI-hint: Caller-owned MCP federation. An [mcp.servers.<id>] declared ONLY in the caller's own SSOT tier (~/.config/mios/mios.toml + its mios.d/) runs as the caller and is re-exported by the per-user MiOS MCP front door (mios-control) as <namespace><tool>. The system agent-pipe (User=mios-ai, ProtectHome=yes) can neither read nor execute such declarations; vendor/host declarations remain its responsibility. Policy: [mcp.federation].
# AI-related: usr/libexec/mios/mios-mcp-server, usr/lib/mios/mios_toml.py, usr/share/mios/mios.toml, usr/share/mios/configurator/mios.html, tests/test-mcp-federation.py, usr/share/doc/mios/blender-mcp.md
# AI-functions: load_config, tier_paths, caller_servers, Federation
"""Caller-owned MCP federation for the per-user MiOS MCP front door.

Ownership rule (the whole design):

* A server id declared by a SYSTEM tier (vendor ``/usr/share/mios/mios.toml``,
  ``/usr/lib/mios/mios.d``, host ``/etc/mios/mios.toml`` and its ``mios.d``)
  belongs to the shared agent-pipe service. The front door never spawns it,
  even when the user tier tweaks one of its keys.
* A server id declared ONLY by the caller's own tier belongs to the caller.
  The shared service cannot see it (it runs as another user under
  ``ProtectHome=yes``), so the caller's front door runs it as the caller and
  re-exports its tools under ``<namespace><tool>``.

The child receives the MCP SDK's minimal default environment plus the
declared ``env`` table: never the front door's own environment (relay
pointers, endpoints, tokens). Its stderr goes to a private per-server log
under ``$XDG_STATE_HOME/<[mcp.federation].log_directory>``. Federation is
never offered over the HTTP front door unless ``serve_http`` says so.

The MCP SDK is imported lazily, so configuration parsing is usable (and
unit-testable) from any Python; running members needs the SDK 2.x the front
door already requires.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field

import mios_toml

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}")
_NAMESPACE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}")
# The strictest common client rule (OpenAI/Anthropic function names).
_TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")

_BOOL_KEYS = ("enabled", "serve_http")
# key -> (minimum, maximum); seconds or counts, all required in SSOT.
_NUMBER_KEYS = {
    "startup_timeout_s": (1, 600),
    "call_timeout_s": (1, 3600),
    "restart_backoff_s": (0, 3600),
    "max_tools_per_server": (1, 1024),
}


def load_config(merged: dict) -> dict:
    """Validated [mcp.federation] policy. Every key is required: no literals."""
    table = (merged.get("mcp") or {}).get("federation")
    if not isinstance(table, dict):
        raise ValueError("[mcp.federation] is missing from the SSOT")
    out = {}
    for key in _BOOL_KEYS:
        if type(table.get(key)) is not bool:
            raise ValueError(f"[mcp.federation].{key} must be true or false")
        out[key] = table[key]
    for key, (low, high) in _NUMBER_KEYS.items():
        value = table.get(key)
        integral = key == "max_tools_per_server"
        if type(value) is bool or not isinstance(value, int if integral else (int, float)):
            raise ValueError(f"[mcp.federation].{key} must be a number")
        if not low <= value <= high:
            raise ValueError(f"[mcp.federation].{key} must be within {low}..{high}")
        out[key] = value
    log_directory = table.get("log_directory")
    if (not isinstance(log_directory, str) or not log_directory or os.path.isabs(log_directory)
            or ".." in log_directory.split("/")):
        raise ValueError("[mcp.federation].log_directory must be a relative path under $XDG_STATE_HOME")
    out["log_directory"] = log_directory
    return out


def _servers(paths) -> dict:
    """The [mcp.servers] declarations of the given layer files, merged in order."""
    merged: dict = {}
    for path in paths:
        mios_toml.deep_merge(merged, mios_toml._load_one(path))
    servers = (merged.get("mcp") or {}).get("servers") or {}
    return servers if isinstance(servers, dict) else {}


def tier_paths() -> tuple[list, list]:
    """(system layers, caller layers) from the ONE shared resolver's tier table."""
    vendor, vendor_d, host, host_d, user, user_d = mios_toml._tier_dirs()
    system = [vendor, *mios_toml._frags(vendor_d), host, *mios_toml._frags(host_d)]
    return system, [user, *mios_toml._frags(user_d)]


@dataclass
class Spec:
    id: str
    command: str
    args: list
    env: dict
    cwd: str | None
    namespace: str
    label: str
    allowed_tools: list = field(default_factory=list)


def _spec(sid: str, cfg: dict) -> Spec:
    """Validate one caller declaration; raise ValueError with the reason."""
    if not _ID.fullmatch(sid):
        raise ValueError("server id must be 1-32 letters, digits, '_' or '-'")
    transport = cfg.get("transport", "stdio")
    if transport != "stdio":
        raise ValueError(f"transport {transport!r} is not federated; only stdio servers run as the caller")
    command = cfg.get("command")
    if not isinstance(command, str) or not command:
        raise ValueError("command is required")
    resolved = command if os.path.isabs(command) else shutil.which(command)
    if not resolved or not os.path.isfile(resolved) or not os.access(resolved, os.X_OK):
        raise ValueError(f"command is not an executable file: {command}")
    args = cfg.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ValueError("args must be a list of strings")
    env = cfg.get("env", {})
    if not isinstance(env, dict) or not all(
            isinstance(k, str) and type(v) in (str, int, float, bool) for k, v in env.items()):
        raise ValueError("env must be a table of scalar values")
    env = {k: (str(v).lower() if type(v) is bool else str(v)) for k, v in env.items()}
    cwd = cfg.get("cwd")
    if cwd is not None and (not isinstance(cwd, str) or not os.path.isabs(cwd) or not os.path.isdir(cwd)):
        raise ValueError(f"cwd must be an existing absolute directory: {cwd}")
    namespace = cfg.get("namespace") or f"{sid.replace('-', '_')}_"
    if not isinstance(namespace, str) or not _NAMESPACE.fullmatch(namespace):
        raise ValueError("namespace must start with a letter and use letters, digits or '_'")
    allowed = cfg.get("allowed_tools", [])
    if not isinstance(allowed, list) or not all(isinstance(a, str) for a in allowed):
        raise ValueError("allowed_tools must be a list of strings")
    label = cfg.get("label") or sid
    return Spec(sid, resolved, list(args), env, cwd, namespace, str(label), list(allowed))


def caller_servers(system=None, caller=None) -> tuple[list, list]:
    """(specs, rows): enabled caller-owned stdio specs, plus a status row for
    every caller declaration that is skipped, disabled or invalid."""
    if system is None or caller is None:
        default_system, default_caller = tier_paths()
        system = default_system if system is None else system
        caller = default_caller if caller is None else caller
    system_ids = set(_servers(system))
    specs, rows = [], []
    for sid, cfg in sorted(_servers(caller).items()):
        if sid in system_ids:
            rows.append({"id": sid, "status": "system-owned",
                         "error": "declared by a system tier; the shared agent-pipe owns it"})
            continue
        if not isinstance(cfg, dict):
            rows.append({"id": sid, "status": "config-error", "error": "declaration must be a table"})
            continue
        if cfg.get("enabled", True) is not True:
            rows.append({"id": sid, "status": "disabled"})
            continue
        try:
            specs.append(_spec(sid, cfg))
        except ValueError as error:
            rows.append({"id": sid, "status": "config-error", "error": str(error)})
    return specs, rows


class _Member:
    """One caller-owned server: a single owner task enters and exits the SDK
    session (anyio cancel scopes must not cross tasks); handlers only call."""

    def __init__(self, spec: Spec, config: dict, log_dir: str):
        self.spec = spec
        self.config = config
        self.log_path = os.path.join(log_dir, f"{spec.id}.log")
        self.status = "idle"
        self.error = None
        self.server_info = None
        self.tools: list = []
        self.session = None
        self.task = None
        self.ready = asyncio.Event()
        self.stop = asyncio.Event()
        self.started = None
        self.lock = asyncio.Lock()

    async def ensure(self) -> None:
        async with self.lock:
            if self.task is not None and not self.task.done():
                if self.session is None:
                    await self._wait_ready()
                return
            if self.started is not None and time.monotonic() - self.started < self.config["restart_backoff_s"]:
                return
            self.ready, self.stop = asyncio.Event(), asyncio.Event()
            self.started = time.monotonic()
            self.status, self.error = "starting", None
            self.task = asyncio.create_task(self._owner(), name=f"mios-mcp-federation-{self.spec.id}")
            await self._wait_ready()

    async def _wait_ready(self) -> None:
        try:
            await asyncio.wait_for(asyncio.shield(self.ready.wait()), self.config["startup_timeout_s"])
        except asyncio.TimeoutError:
            self.status = "startup-timeout"
            self.error = f"no initialize/tools/list within {self.config['startup_timeout_s']}s"
            await self.halt()

    async def _owner(self) -> None:
        from mcp import ClientSession, StdioServerParameters, types
        from mcp.client.stdio import get_default_environment, stdio_client
        spec = self.spec
        try:
            params = StdioServerParameters(command=spec.command, args=spec.args, cwd=spec.cwd,
                                           env={**get_default_environment(), **spec.env})
            fd = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as errlog:
                async with stdio_client(params, errlog=errlog) as (read, write):
                    async with ClientSession(read, write, client_info=types.Implementation(
                            name="mios-mcp-federation", version="1")) as session:
                        initialized = await session.initialize()
                        tools, cursor = [], None
                        while True:
                            page = await session.list_tools(
                                params=types.PaginatedRequestParams(cursor=cursor) if cursor else None)
                            tools.extend(page.tools)
                            cursor = page.next_cursor
                            if not cursor or len(tools) > self.config["max_tools_per_server"]:
                                break
                        if spec.allowed_tools:
                            tools = [t for t in tools if t.name in spec.allowed_tools]
                        self.server_info = initialized.server_info.model_dump(exclude_none=True)
                        self.tools = tools
                        self.session = session
                        self.status = "ready"
                        self.ready.set()
                        await self.stop.wait()
        except asyncio.CancelledError:
            raise
        except BaseException as error:  # noqa: BLE001 -- ExceptionGroup from anyio included
            leaf = error
            while isinstance(leaf, BaseExceptionGroup) and leaf.exceptions:
                leaf = leaf.exceptions[0]
            self.status, self.error = "failed", f"{type(leaf).__name__}: {leaf}"
        finally:
            self.session = None
            self.tools = []
            if self.status == "ready":
                self.status = "stopped"
            self.ready.set()

    async def halt(self) -> None:
        task = self.task
        if task is None:
            return
        self.stop.set()
        try:
            await asyncio.wait_for(asyncio.shield(task), 5)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
            task.cancel()
            try:
                await task
            except BaseException:  # noqa: BLE001
                pass

    def row(self) -> dict:
        out = {"id": self.spec.id, "label": self.spec.label, "status": self.status,
               "namespace": self.spec.namespace, "tools_count": len(self.tools)}
        if self.error:
            out["error"] = self.error
        if self.server_info:
            out["serverInfo"] = self.server_info
        return out


class Federation:
    """The caller's federated servers, their exported names and dispatch."""

    def __init__(self, config: dict, specs: list, rows: list, state_home: str | None = None):
        self.config = config
        self.skipped = rows
        state = state_home or os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
        self.log_dir = os.path.join(state, config["log_directory"])
        self.members = [_Member(spec, config, self.log_dir) for spec in specs]
        self.routes: dict = {}
        self.collisions: list = []
        self.warm = None

    @classmethod
    def from_ssot(cls, merged: dict):
        """The caller's federation, or None when the policy disables it or the
        caller declares no runnable server. Invalid declarations are reported
        on stderr and in status(); they never stop the front door."""
        config = load_config(merged)
        if not config["enabled"]:
            return None
        specs, rows = caller_servers()
        for row in rows:
            if row["status"] == "config-error":
                sys.stderr.write(f"[mios-mcp] federation skipped {row['id']}: {row['error']}\n")
        return cls(config, specs, rows) if specs else None

    def serves(self, http: bool) -> bool:
        """Stdio front doors always serve; HTTP only when [mcp.federation].serve_http."""
        return not http or self.config["serve_http"]

    def may_own(self, name: str) -> bool:
        """True when name carries a member namespace (routes may still be cold)."""
        return any(name.startswith(member.spec.namespace) for member in self.members)

    def start_background(self) -> None:
        """Warm every member without delaying the MCP initialize handshake."""
        os.makedirs(self.log_dir, mode=0o700, exist_ok=True)
        self.warm = asyncio.create_task(self._ensure_all(), name="mios-mcp-federation-warm")

    async def _ensure_all(self) -> None:
        os.makedirs(self.log_dir, mode=0o700, exist_ok=True)
        await asyncio.gather(*(member.ensure() for member in self.members), return_exceptions=True)

    async def tool_rows(self, reserved: set) -> list:
        """MCP tool rows for every ready member; native names always win."""
        await self._ensure_all()
        routes, rows, collisions = {}, [], []
        for member in self.members:
            spec = member.spec
            for tool in member.tools[:self.config["max_tools_per_server"]]:
                name = spec.namespace + tool.name
                if not _TOOL_NAME.fullmatch(name) or name in reserved or name in routes:
                    collisions.append({"server": spec.id, "tool": tool.name, "name": name})
                    continue
                row = tool.model_dump(by_alias=True, exclude_none=True)
                row["name"] = name
                row["description"] = f"[{spec.label}] " + (row.get("description") or tool.name)
                routes[name] = (member, tool.name)
                rows.append(row)
        self.routes, self.collisions = routes, collisions
        return rows

    def owns(self, name: str) -> bool:
        return name in self.routes

    async def call(self, name: str, arguments: dict):
        from mcp import types

        def failure(text):
            return types.CallToolResult(content=[types.TextContent(type="text", text=text)], isError=True)

        member, upstream = self.routes[name]
        await member.ensure()
        session = member.session
        if session is None:
            return failure(f"{member.spec.label} MCP server is {member.status}: {member.error or 'not running'}")
        try:
            result = await session.call_tool(upstream, arguments,
                                             read_timeout_seconds=self.config["call_timeout_s"])
        except BaseException as error:  # noqa: BLE001 -- the transport may have died
            if isinstance(error, asyncio.CancelledError):
                raise
            member.status, member.error = "failed", f"{type(error).__name__}: {error}"
            await member.halt()
            return failure(f"{member.spec.label} MCP call failed: {member.error}")
        if not isinstance(result, types.CallToolResult):
            return failure(f"{member.spec.label} returned an unsupported {type(result).__name__}")
        return result

    def status(self) -> dict:
        return {"servers": [m.row() for m in self.members] + list(self.skipped),
                "collisions": list(self.collisions), "log_directory": self.log_dir}

    async def close(self) -> None:
        if self.warm is not None and not self.warm.done():
            self.warm.cancel()
        await asyncio.gather(*(member.halt() for member in self.members), return_exceptions=True)
