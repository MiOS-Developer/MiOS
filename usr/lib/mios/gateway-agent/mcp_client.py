# AI-hint: MiOS system and orchestration module providing mcp client capabilities.
# AI-related: /usr/libexec/mios/mios-mcp-server, mios-mcp-server, [ports].agent_pipe
# AI-functions: __init__, MiOSMCPClient

import asyncio
import logging
import os
import sys
from typing import Optional, Any

log = logging.getLogger("gateway-agent-mcp")

class MiOSMCPClient:
    def __init__(self, mcp_refresh_seconds: int = 300):
        self.mcp_refresh_seconds = mcp_refresh_seconds
        self.session = None
        self._ctx = None
        self.cached_tools = []
        self._refresh_task = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        async with self._lock:
            if self.session is not None:
                return

            from mcp import StdioServerParameters, ClientSession
            from mcp.client.stdio import stdio_client

            command = "/usr/libexec/mios/mios-mcp-server"
            env = dict(os.environ)
            # Law 5: the legacy orchestrator port is RETIRED -- the pipe answers on [ports].agent_pipe.
            _pipe_port = os.environ.get("MIOS_PORTS_AGENT_PIPE", "8700")
            env["MIOS_AGENT_PIPE_URL"] = os.environ.get(
                "MIOS_AGENT_PIPE_URL", "http://localhost:%s" % _pipe_port)

            server_params = StdioServerParameters(
                command=command,
                args=[],
                env=env
            )

            log.info("Starting stdio MCP client connection to: %s", command)
            try:
                self._ctx = stdio_client(server_params)
                read, write = await self._ctx.__aenter__()
                self.session = ClientSession(read, write)
                await self.session.__aenter__()
                await self.session.initialize()

                await self._fetch_tools()

                self._refresh_task = asyncio.create_task(self._refresh_loop())
                log.info("MCP client connected successfully and loaded %d tools", len(self.cached_tools))
            except Exception as e:
                log.error("Failed to connect stdio MCP client: %s", e)
                await self._cleanup()

    async def _fetch_tools(self) -> None:
        if not self.session:
            return
        try:
            res = await self.session.list_tools()
            self.cached_tools = getattr(res, "tools", []) or []
        except Exception as e:
            log.warning("Failed to fetch tools from MCP server: %s", e)

    async def _refresh_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.mcp_refresh_seconds)
                async with self._lock:
                    await self._fetch_tools()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("MCP client tools refresh failed: %s", e)

    async def call_tool(self, name: str, arguments: dict) -> str:
        async with self._lock:
            if not self.session:
                raise RuntimeError("MCP client is not connected")
            try:
                res = await self.session.call_tool(name, arguments)
                content_list = getattr(res, "content", []) or []
                text_outputs = []
                for content in content_list:
                    if getattr(content, "type", "text") == "text":
                        text_outputs.append(getattr(content, "text", ""))
                return "\n".join(text_outputs)
            except Exception as e:
                log.error("MCP call to tool %s failed: %s", name, e)
                raise

    async def _cleanup(self) -> None:
        if self._refresh_task:
            self._refresh_task.cancel()
            self._refresh_task = None
        if self.session:
            try:
                await self.session.__aexit__(None, None, None)
            except Exception:
                pass
            self.session = None
        if self._ctx:
            try:
                await self._ctx.__aexit__(None, None, None)
            except Exception:
                pass
            self._ctx = None

    async def close(self) -> None:
        async with self._lock:
            await self._cleanup()
            log.info("MCP client connection closed")
