# AI-hint: MiOS system and orchestration module providing tool registry capabilities.
# AI-related: mios-searxng
# AI-functions: __init__, forward, get_tools, _create_tool_instance, MiOSToolRegistry, WebSearchTool, DynamicTool

import asyncio
import logging
import os
from typing import List, Callable, Any
from smolagents import Tool

log = logging.getLogger("gateway-agent-registry")

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

        from server import _toml_section
        gateway_cfg = _toml_section("gateway")
        # SSOT [ports].searxng_internal: the in-container listener (host maps 8800->8080).
        searxng_port = int(os.environ.get("MIOS_PORTS_SEARXNG_INTERNAL", "8080"))
        searxng_url = gateway_cfg.get("searxng_url", f"http://mios-searxng:{searxng_port}")
        tools.append(WebSearchTool(searxng_url))

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
