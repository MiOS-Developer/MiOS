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

Spawn budget (one front door per agent session must never become a process
storm): with ``catalog_cache`` a member's tool catalog is cached (0600, keyed
by a fingerprint of its declaration) so a new front door lists the tools
WITHOUT starting the server; the server starts on the first call only. A
started member stops after ``idle_stop_s`` without calls. Failed starts back
off exponentially from ``restart_backoff_s`` and stop for good (until the
front door restarts) after ``max_restarts`` consecutive failures. No timer,
watcher or loop runs per member: every start is caused by a list or a call.

The MCP SDK is imported lazily, so configuration parsing is usable (and
unit-testable) from any Python; running members needs the SDK 2.x the front
door already requires.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
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

_BOOL_KEYS = ("enabled", "serve_http", "catalog_cache")
# key -> (minimum, maximum); seconds or counts, all required in SSOT.
_NUMBER_KEYS = {
    "startup_timeout_s": (1, 600),
    "call_timeout_s": (1, 3600),
    "restart_backoff_s": (0, 3600),
    "max_tools_per_server": (1, 1024),
    "idle_stop_s": (0, 86400),
    "max_restarts": (1, 100),
}
_INTEGRAL = {"max_tools_per_server", "max_restarts"}
_BACKOFF_CAP_S = 3600


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
        integral = key in _INTEGRAL
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


def fingerprint(spec: Spec) -> str:
    """Catalog-cache key: the declaration plus the command's identity. Only
    the digest is stored, never the declared env values."""
    try:
        stat = os.stat(spec.command)
        identity = [stat.st_size, stat.st_mtime_ns]
    except OSError:
        identity = None
    material = json.dumps([spec.command, identity, spec.args, sorted(spec.env.items()), spec.cwd,
                           spec.namespace, sorted(spec.allowed_tools)], sort_keys=True)
    return hashlib.sha256(material.encode()).hexdigest()


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
        self.cache_path = os.path.join(log_dir, f"{spec.id}.tools.json")
        self.fingerprint = fingerprint(spec)
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
        self.failures = 0      # consecutive unexpected ends (failed start, crash, failed call)
        self.spawns = 0        # children started by this front door
        self.inflight = 0
        self.last_used = 0.0
        self.stopping = False  # halt()/idle stop: an expected end, not a failure
        self.cached = self._read_cache() if config["catalog_cache"] else None

    def _read_cache(self):
        try:
            with open(self.cache_path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return None
        if (not isinstance(data, dict) or data.get("fingerprint") != self.fingerprint
                or not isinstance(data.get("tools"), list)):
            return None
        tools = [t for t in data["tools"] if isinstance(t, dict) and isinstance(t.get("name"), str)]
        return {"tools": tools, "server_info": data.get("server_info")}

    def _write_cache(self) -> None:
        """Atomic 0600 write: a torn cache is never read back as a catalog."""
        rows = [tool.model_dump(by_alias=True, exclude_none=True) for tool in self.tools]
        data = {"fingerprint": self.fingerprint, "server_info": self.server_info, "tools": rows}
        tmp = f"{self.cache_path}.{os.getpid()}.tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            os.replace(tmp, self.cache_path)
            self.cached = {"tools": rows, "server_info": self.server_info}
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def catalog(self) -> list:
        """Tool rows: the live listing when running, else the valid cache."""
        if self.tools:
            return [tool.model_dump(by_alias=True, exclude_none=True) for tool in self.tools]
        return list(self.cached["tools"]) if self.cached else []

    def gave_up(self) -> bool:
        return self.failures >= self.config["max_restarts"]

    def backoff_s(self) -> float:
        if self.failures <= 0:
            return 0
        return min(self.config["restart_backoff_s"] * 2 ** (self.failures - 1), _BACKOFF_CAP_S)

    async def ensure(self) -> None:
        async with self.lock:
            if self.task is not None and not self.task.done():
                if not self.stopping:
                    if self.session is None:
                        await self._wait_ready()
                    return
                # An idle stop is tearing the child down: let it finish (bounded),
                # then start a fresh one instead of handing out a closing session.
                await self._reap()
            if self.gave_up():
                self.status = "gave-up"
                return
            if self.started is not None and time.monotonic() - self.started < self.backoff_s():
                return
            self.ready, self.stop = asyncio.Event(), asyncio.Event()
            self.started = self.last_used = time.monotonic()
            self.stopping = False
            self.spawns += 1
            self.status, self.error = "starting", None
            self.task = asyncio.create_task(self._owner(), name=f"mios-mcp-federation-{self.spec.id}")
            await self._wait_ready()

    async def _wait_ready(self) -> None:
        try:
            await asyncio.wait_for(asyncio.shield(self.ready.wait()), self.config["startup_timeout_s"])
        except asyncio.TimeoutError:
            await self.halt()
            self.failures += 1
            self.status = "startup-timeout"
            self.error = f"no initialize/tools/list within {self.config['startup_timeout_s']}s"

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
                        if self.config["catalog_cache"]:
                            self._write_cache()
                        self.ready.set()
                        await self._serve_until_idle()
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
            if self.status in {"failed", "startup-timeout"} and not self.stopping:
                self.failures += 1
            self.ready.set()

    async def _serve_until_idle(self) -> None:
        """Hold the session until halt() or idle_stop_s without a call. One
        bounded wait per member, re-armed only by its own timeout."""
        idle = self.config["idle_stop_s"]
        while not self.stop.is_set():
            if idle <= 0:
                await self.stop.wait()
                return
            remaining = idle if self.inflight else self.last_used + idle - time.monotonic()
            if remaining <= 0:
                # Unpublish the session before teardown awaits anything, so no
                # call can pick up a session that is about to close.
                self.stopping = True
                self.session = None
                self.status = "idle-stopped"
                return
            try:
                await asyncio.wait_for(self.stop.wait(), remaining)
            except asyncio.TimeoutError:
                pass

    async def halt(self) -> None:
        if self.task is None:
            return
        self.stopping = True
        self.stop.set()
        await self._reap()

    async def _reap(self) -> None:
        """Wait (5 s at most) for the owner task to exit, else cancel it."""
        task = self.task
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
               "namespace": self.spec.namespace, "tools_count": len(self.catalog()),
               "catalog": "live" if self.tools else ("cached" if self.cached else "none"),
               "spawns": self.spawns, "failures": self.failures}
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
        """Learn uncached catalogs without delaying the MCP initialize
        handshake; a member with a valid cached catalog is not started."""
        os.makedirs(self.log_dir, mode=0o700, exist_ok=True)
        if any(self._needs_start(member, live=False) for member in self.members):
            self.warm = asyncio.create_task(self._ensure_all(live=False), name="mios-mcp-federation-warm")

    @staticmethod
    def _needs_start(member: _Member, live: bool) -> bool:
        if member.session is not None:
            return False
        return live or member.cached is None

    async def _ensure_all(self, live: bool) -> None:
        os.makedirs(self.log_dir, mode=0o700, exist_ok=True)
        await asyncio.gather(*(member.ensure() for member in self.members
                               if self._needs_start(member, live)), return_exceptions=True)

    async def tool_rows(self, reserved: set, live: bool = False) -> list:
        """MCP tool rows for every member (live, or its cached catalog without
        starting it); native names always win. live=True starts every member."""
        await self._ensure_all(live)
        routes, rows, collisions = {}, [], []
        for member in self.members:
            spec = member.spec
            for tool in member.catalog()[:self.config["max_tools_per_server"]]:
                upstream = tool["name"]
                name = spec.namespace + upstream
                if not _TOOL_NAME.fullmatch(name) or name in reserved or name in routes:
                    collisions.append({"server": spec.id, "tool": upstream, "name": name})
                    continue
                row = dict(tool)
                row["name"] = name
                row["description"] = f"[{spec.label}] " + (row.get("description") or upstream)
                routes[name] = (member, upstream)
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
        os.makedirs(self.log_dir, mode=0o700, exist_ok=True)
        await member.ensure()
        session = member.session
        if session is None:
            return failure(f"{member.spec.label} MCP server is {member.status}: {member.error or 'not running'}")
        member.inflight += 1
        member.last_used = time.monotonic()
        try:
            result = await session.call_tool(upstream, arguments,
                                             read_timeout_seconds=self.config["call_timeout_s"])
        except BaseException as error:  # noqa: BLE001 -- the transport may have died
            if isinstance(error, asyncio.CancelledError):
                raise
            member.failures += 1
            await member.halt()
            member.status, member.error = "failed", f"{type(error).__name__}: {error}"
            return failure(f"{member.spec.label} MCP call failed: {member.error}")
        finally:
            member.inflight -= 1
            member.last_used = time.monotonic()
        member.failures = 0
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
