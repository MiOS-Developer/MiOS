# AI-hint: MiOS system and orchestration module providing tool registry capabilities.
# AI-related: mios-searxng
# AI-functions: __init__, forward, get_tools, _create_tool_instance, MiOSToolRegistry, WebSearchTool, DynamicTool

import asyncio
import logging
import os
import sys
from typing import List, Callable, Any
from smolagents import Tool

log = logging.getLogger("gateway-agent-registry")


def searxng_url() -> str:
    """[gateway].searxng_url from the shared layered resolver.

    The vendor value derives from [ports].searxng (http://localhost:<port>):
    SearXNG is a host-network pod member bound to loopback, and this agent runs
    as a host systemd service, so localhost is its route. A non-empty host or
    user layer value is an explicit override and wins. Nothing is substituted
    when the setting is missing -- KeyError names it."""
    lib = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import mios_toml
    return mios_toml.resolved_export("MIOS_GATEWAY_SEARXNG_URL")

class MiOSToolRegistry:
    def __init__(self, mcp_client, main_loop: asyncio.AbstractEventLoop):
        self.mcp_client = mcp_client
        self.main_loop = main_loop
        self._tools = []

class WebSearchTool(Tool):
    def __init__(self, searxng_url: str):
        self.name = "web_search"
        self.description = "Perform a web search for the given query."
        self.inputs = {
            "query": {
                "type": "string",
                "description": "The search query."
            }
        }
        self.output_type = "string"
        self.searxng_url = searxng_url
        self.skip_forward_signature_validation = True
        super().__init__()

    def forward(self, query: str, **kwargs) -> str:
        import httpx
        try:
            resp = httpx.get(f"{self.searxng_url.rstrip('/')}/search", params={"q": query, "format": "json"}, timeout=15.0)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            if not results:
                return "0 results found."
            output = []
            for r in results[:5]:
                output.append(f"Title: {r.get('title')}\nURL: {r.get('url')}\nContent: {r.get('content')}\n")
            return "\n---\n".join(output)
        except Exception as e:
            return f"Error executing web_search: {e}"

    def get_tools(self) -> List[Tool]:
        tools = []
        for mcp_tool in self.mcp_client.cached_tools:
            try:
                tool_inst = self._create_tool_instance(mcp_tool)
                tools.append(tool_inst)
            except Exception as e:
                log.warning("Failed to map MCP tool %s to smolagents.Tool: %s", getattr(mcp_tool, "name", "unknown"), e)

        tools.append(WebSearchTool(searxng_url()))

        return tools

    def _create_tool_instance(self, mcp_tool) -> Tool:
        name = getattr(mcp_tool, "name", "")
        description = getattr(mcp_tool, "description", "")

        input_schema = getattr(mcp_tool, "inputSchema", {}) or {}
        properties = input_schema.get("properties", {}) or {}
        required = input_schema.get("required", []) or []

        inputs = {}
        for param_name, param_schema in properties.items():
            inputs[param_name] = {
                "type": param_schema.get("type", "any"),
                "description": param_schema.get("description", f"Parameter {param_name}")
            }
            if param_name not in required:
                inputs[param_name]["nullable"] = True

        client = self.mcp_client
        loop = self.main_loop

        class DynamicTool(Tool):
            def __init__(self, name_val, desc_val, inputs_val):
                self.name = name_val
                self.description = desc_val
                self.inputs = inputs_val
                self.output_type = "string"
                self.skip_forward_signature_validation = True
                super().__init__()

            def forward(self, **kwargs) -> str:
                coro = client.call_tool(self.name, kwargs)
                fut = asyncio.run_coroutine_threadsafe(coro, loop)
                try:
                    return fut.result(timeout=20.0)
                except Exception as e:
                    return f"Error executing tool {self.name}: {e}"

        return DynamicTool(name, description, inputs)
