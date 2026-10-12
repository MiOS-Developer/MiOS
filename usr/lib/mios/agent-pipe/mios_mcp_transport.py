# AI-hint: Upstream FOSS MCP SDK v2 client transports for stdio, Streamable HTTP and legacy SSE.
# AI-related: mios_mcp, mios_mcp_schema, /usr/libexec/mios/mcp-server-runner
"""MCP clients use the upstream SDK for protocol discovery, validation and transport.

Both stateless peers and legacy handshake peers are negotiated by
mcp.Client. MiOS only owns registry policy, sandbox policy and result mapping.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Optional

try:
    import httpx2
except ImportError:
    import httpx as httpx2
from mcp import Client, StdioServerParameters
from mcp import types
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

from mios_config import _toml_section

log = logging.getLogger("mios-agent-pipe")

MCP_PROTOCOL_VERSION = str(
    os.environ.get("MIOS_MCP_PROTOCOL_VERSION")
    or (_toml_section("mcp") or {}).get("protocol_version")
    or "2026-07-28"
).strip()

_MCP_SANDBOX_CFG = (_toml_section("security") or {}).get("mcp_sandbox") or {}
if isinstance(_MCP_SANDBOX_CFG, str):
    _MCP_SANDBOX_CFG = {}
MCP_SANDBOX_ENABLE = (
    str(os.environ.get("MIOS_MCP_SANDBOX") or _MCP_SANDBOX_CFG.get("enable", "false"))
    .strip().lower() not in {"false", "0", "no", "off", ""}
)
MCP_SANDBOX_GATEKEEPER = "/usr/libexec/mios/mcp-server-runner"
_MCP_ENV_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _mcp_render_headers(values: dict) -> dict:
    """Expand environment-backed values without persisting the expanded secret."""
    result = {}
    for key, value in (values or {}).items():
        rendered = str(value)
        for variable in _MCP_ENV_RE.findall(rendered):
            rendered = rendered.replace("${" + variable + "}", os.environ.get(variable, ""))
        result[key] = rendered
    return result


def _failure(error: Exception | str) -> dict:
    return {"error": {"code": -32000, "message": str(error)}}


def _connected_info(client: Client) -> dict:
    info = client.server_info
    return {
        "protocolVersion": client.protocol_version,
        "serverInfo": info.model_dump(by_alias=True) if info is not None else None,
    }


class _McpHttpClient:
    """Long-lived SDK client for Streamable HTTP or legacy SSE."""

    def __init__(self, sid: str, url: str, headers: Optional[dict] = None,
                 transport: str = "http"):
        self.sid = sid
        self.url = url.rstrip("/")
        self.headers = dict(headers or {})
        self.transport = transport
        self._lock = asyncio.Lock()
        self._sdk: Client | None = None
        self._http_client: httpx2.AsyncClient | None = None
        self._inited = False
        self._init_result: dict = {}

    async def initialize(self) -> dict:
        async with self._lock:
            if self._inited and self._sdk is not None:
                return self._init_result
            try:
                rendered = _mcp_render_headers(self.headers)
                if self.transport == "sse":
                    target = sse_client(self.url, headers=rendered)
                else:
                    self._http_client = httpx2.AsyncClient(
                        headers=rendered,
                        timeout=httpx2.Timeout(300.0, connect=30.0),
                    )
                    await self._http_client.__aenter__()
                    target = streamable_http_client(
                        self.url, http_client=self._http_client,
                    )
                self._sdk = Client(
                    target, client_info=types.Implementation(
                        name="mios-agent-pipe", version="2",
                    ),
                )
                await self._sdk.__aenter__()
                self._init_result = _connected_info(self._sdk)
                self._inited = True
                return self._init_result
            except Exception as error:
                await self._close_unlocked()
                return _failure(error)

    async def list_tools(self, cursor: Optional[str] = None) -> dict:
        init = await self.initialize()
        if not self._inited:
            return init
        try:
            result = await self._sdk.list_tools(cursor=cursor)
            return {"result": result.model_dump(by_alias=True, exclude_none=True)}
        except Exception as error:
            return _failure(error)

    async def call_tool(self, name: str, arguments: dict,
                        timeout_s: float = 120.0) -> dict:
        init = await self.initialize()
        if not self._inited:
            return init
        try:
            result = await self._sdk.call_tool(
                name, arguments or {}, read_timeout_seconds=timeout_s,
            )
            return {"result": result.model_dump(by_alias=True, exclude_none=True)}
        except Exception as error:
            return _failure(error)

    async def _close_unlocked(self) -> None:
        if self._sdk is not None:
            try:
                await self._sdk.__aexit__(None, None, None)
            except Exception:
                pass
        if self._http_client is not None:
            await self._http_client.aclose()
        self._sdk = None
        self._http_client = None
        self._inited = False

    async def close(self) -> None:
        async with self._lock:
            await self._close_unlocked()


class _McpStdioClient:
    """Long-lived SDK client with the MiOS sandbox gatekeeper retained."""

    def __init__(self, sid: str, command: str, args: Optional[list[str]] = None,
                 env: Optional[dict] = None, cwd: Optional[str] = None):
        self.sid = sid
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.cwd = cwd
        self._lock = asyncio.Lock()
        self._sdk: Client | None = None
        self._inited = False
        self._init_result: dict = {}

    async def initialize(self) -> dict:
        async with self._lock:
            if self._inited and self._sdk is not None:
                return self._init_result
            command, args = self.command, list(self.args)
            child_env = dict(os.environ)
            child_env.update(_mcp_render_headers(self.env))
            if MCP_SANDBOX_ENABLE and os.path.isfile(MCP_SANDBOX_GATEKEEPER):
                child_env["MIOS_MCP_SANDBOX"] = "true"
                allowed = _MCP_SANDBOX_CFG.get("write_allowed_paths") or []
                if isinstance(allowed, list):
                    child_env["MIOS_WRITE_ALLOWED_PATHS"] = ":".join(map(str, allowed))
                command, args = MCP_SANDBOX_GATEKEEPER, [command, *args]
            try:
                self._sdk = Client(
                    StdioServerParameters(
                        command=command, args=args, env=child_env, cwd=self.cwd,
                    ),
                    client_info=types.Implementation(
                        name="mios-agent-pipe", version="2",
                    ),
                )
                await self._sdk.__aenter__()
                self._init_result = _connected_info(self._sdk)
                self._inited = True
                return self._init_result
            except Exception as error:
                await self._close_unlocked()
                return _failure(error)

    async def _rpc(self, method: str, params: Optional[dict] = None,
                   timeout_s: float = 30.0) -> dict:
        if method == "tools/list":
            return await self.list_tools((params or {}).get("cursor"))
        if method == "tools/call":
            payload = params or {}
            return await self.call_tool(
                payload.get("name", ""), payload.get("arguments") or {}, timeout_s,
            )
        return {"error": {"code": -32601, "message": f"unsupported MCP method: {method}"}}

    async def list_tools(self, cursor: Optional[str] = None) -> dict:
        init = await self.initialize()
        if not self._inited:
            return init
        try:
            result = await self._sdk.list_tools(cursor=cursor)
            return {"result": result.model_dump(by_alias=True, exclude_none=True)}
        except Exception as error:
            return _failure(error)

    async def call_tool(self, name: str, arguments: dict,
                        timeout_s: float = 120.0) -> dict:
        init = await self.initialize()
        if not self._inited:
            return init
        try:
            result = await self._sdk.call_tool(
                name, arguments or {}, read_timeout_seconds=timeout_s,
            )
            return {"result": result.model_dump(by_alias=True, exclude_none=True)}
        except Exception as error:
            return _failure(error)

    async def _close_unlocked(self) -> None:
        if self._sdk is not None:
            try:
                await self._sdk.__aexit__(None, None, None)
            except Exception:
                pass
        self._sdk = None
        self._inited = False

    async def close(self) -> None:
        async with self._lock:
            await self._close_unlocked()


async def _mcp_http_rpc(url: str, headers: dict, method: str,
                        params: Optional[dict] = None, rid: int = 1,
                        timeout_s: float = 30.0) -> dict:
    """Compatibility entry for callers; SDK owns the wire and negotiation."""
    del rid
    client = _McpHttpClient("", url, headers)
    try:
        init = await client.initialize()
        if not client._inited:
            return init
        if method == "initialize":
            return {"result": init}
        if method == "notifications/initialized":
            return {}
        if method == "tools/list":
            return await client.list_tools((params or {}).get("cursor"))
        if method == "tools/call":
            payload = params or {}
            return await client.call_tool(
                payload.get("name", ""), payload.get("arguments") or {}, timeout_s,
            )
        if method == "ping":
            try:
                result = await client._sdk.ping()
                return {"result": result.model_dump(by_alias=True, exclude_none=True)}
            except Exception as error:
                return _failure(error)
        return {"error": {"code": -32601, "message": f"unsupported MCP method: {method}"}}
    finally:
        await client.close()
