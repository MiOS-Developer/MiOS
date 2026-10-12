# AI-hint: In-process asyncio.Queue gateway producer-consumer seam (CONV-02).
# AI-related: ./server.py, ./mios_dispatcher.py, ./mios_capreg.py, ./test_mios_gateway_queue.py

import asyncio
import os
import time
import uuid
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, List

from smolagents import Tool, ToolCallingAgent, LiteLLMModel
import mios_scratchpad
from mios_config import _toml_section

log = logging.getLogger("mios-agent-pipe")

_VERB_CATALOG: dict = {}
_recipes: dict = {}
_skills: dict = {}
_trace_span: Any = None

def configure(verb_catalog: dict = None, recipes: dict = None, skills: dict = None, trace_span: Any = None):
    global _VERB_CATALOG, _recipes, _skills, _trace_span
    if verb_catalog is not None:
        _VERB_CATALOG = verb_catalog
    if recipes is not None:
        _recipes = recipes
    if skills is not None:
        _skills = skills
    if trace_span is not None:
        _trace_span = trace_span

@dataclass
class GatewayRequest:
    payload: dict
    fut: asyncio.Future

class GatewayQueue:
    def __init__(self, maxsize: int = 64):
        self.queue = asyncio.Queue(maxsize=maxsize)

    async def put(self, req: GatewayRequest) -> None:
        await self.queue.put(req)

    async def get(self) -> GatewayRequest:
        return await self.queue.get()

    def task_done(self) -> None:
        self.queue.task_done()

    def qsize(self) -> int:
        return self.queue.qsize()

class DispatchTool(Tool):
    def __init__(self, name: str, description: str, inputs: dict, kind: str, main_loop: asyncio.AbstractEventLoop):
        self.name = name
        self.description = description
        self.inputs = inputs
        self.output_type = "string"
        self.kind = kind
        self.main_loop = main_loop
        super().__init__()

    def forward(self, **kwargs) -> str:
        if self.kind == "verb":
            from mios_dispatch import dispatch_mios_verb
            coro = dispatch_mios_verb(self.name, kwargs)
        elif self.kind == "recipe":
            from mios_dispatch import dispatch_mios_verb
            rkey = self.name[len("mios_recipe__"):].replace("_", "-")
            recipe_args = {"name": rkey, "params": {key: value for key, value in kwargs.items() if key != "os"}}
            if kwargs.get("os") is not None:
                recipe_args["os"] = kwargs["os"]
            coro = dispatch_mios_verb("os_recipe", recipe_args)
        elif self.kind == "skill":
            from mios_skills import execute_skill
            real = self.name[len("mios_skill__"):]
            coro = execute_skill(real, kwargs)
        else:
            return f"Error: unknown capability kind '{self.kind}'"

        fut = asyncio.run_coroutine_threadsafe(coro, self.main_loop)
        res = fut.result()
        if isinstance(res, dict):
            return str(res.get("output") or res.get("result") or res)
        return str(res)

def parse_sig(sig: str, vcfg: dict = None) -> dict:
    if not sig:
        return {}
    inputs = {}
    params_cfg = {}
    if vcfg and isinstance(vcfg, dict):
        params_cfg = vcfg.get("params") or {}
    for part in sig.split(","):
        part = part.strip()
        if not part:
            continue
        optional = False
        if part.endswith("?"):
            part = part[:-1].strip()
            optional = True

        if "=" in part:
            name_part, val_part = part.split("=", 1)
            name = name_part.strip()
            optional = True
        else:
            name = part

        name = name.strip('"').strip("'")

        pcfg = params_cfg.get(name)
        if pcfg and isinstance(pcfg, dict) and pcfg.get("type"):
            param_type = pcfg.get("type")
        else:
            param_type = "string"
            _routing_cfg = _toml_section("routing") or {}
            _int_kw = (os.environ.get("MIOS_ROUTING_INTEGER_PARAM_KEYWORDS")
                       or _routing_cfg.get("integer_param_keywords")
                       or ["limit", "count", "timeout", "port", "every", "concurrency", "maxsize"])
            if isinstance(_int_kw, str):
                _int_kw = [x.strip() for x in _int_kw.split(",") if x.strip()]
            _bool_kw = (os.environ.get("MIOS_ROUTING_BOOLEAN_PARAM_KEYWORDS")
                        or _routing_cfg.get("boolean_param_keywords")
                        or ["enable", "force", "success", "active", "dryrun"])
            if isinstance(_bool_kw, str):
                _bool_kw = [x.strip() for x in _bool_kw.split(",") if x.strip()]
            lower_name = name.lower()
            if any(x in lower_name for x in _int_kw):
                param_type = "integer"
            elif any(x in lower_name for x in _bool_kw):
                param_type = "boolean"

        inputs[name] = {
            "type": param_type,
            "description": pcfg.get("desc", f"Parameter {name}") if pcfg and isinstance(pcfg, dict) else f"Parameter {name}",
            "nullable": optional
        }
    return inputs

def get_tools(ceiling: str = "interactive") -> list:
    from mios_capreg import build_capability_manifest
    manifest = build_capability_manifest(
        _VERB_CATALOG, _recipes, ceiling=ceiling, skills=_skills
    )

    tools = []
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()

    for cap in manifest:
        name = cap["name"]
        kind = cap["kind"]
        description = cap.get("description", "")

        sig = ""
        vcfg = {}
        if kind == "verb":
            vcfg = _VERB_CATALOG.get(name) or {}
            sig = vcfg.get("sig", "")
        elif kind == "recipe":
            rcfg = _recipes.get(name) or {}
            sig = rcfg.get("sig", "")
            vcfg = rcfg
            name = f"mios_recipe__{name}"
        elif kind == "skill":
            scfg = _skills.get(name) or {}
            sig = scfg.get("sig", "")
            vcfg = scfg
            name = f"mios_skill__{name}"

        inputs = parse_sig(sig, vcfg)
        tools.append(DispatchTool(
            name=name,
            description=description,
            inputs=inputs,
            kind=kind,
            main_loop=loop
        ))
    return tools

def extract_prompt_from_payload(payload: dict) -> str:
    messages = payload.get("messages") or []
    prompt = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            prompt = msg.get("content") or ""
            break
    if not prompt and messages:
        prompt = messages[-1].get("content") or ""
    return prompt

def extract_system_prompt_from_payload(payload: dict) -> Optional[str]:
    messages = payload.get("messages") or []
    for msg in messages:
        if msg.get("role") in ("system", "developer"):
            return msg.get("content")
    return None

class GatewayWorker:
    def __init__(self, tools: list, endpoint: str, model_name: str, mcp_pool=None):
        self.tools = tools
        self.endpoint = endpoint
        self.model_name = model_name
        self.mcp_pool = mcp_pool
        self._tasks: List[asyncio.Task] = []

    async def run(self, queue: GatewayQueue, concurrency: int = 4) -> None:
        self._tasks = []
        for i in range(concurrency):
            t = asyncio.create_task(self._worker_loop(queue, f"worker-{i}"))
            self._tasks.append(t)
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            log.info("GatewayWorker loop cancelled")
            for t in self._tasks:
                t.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _worker_loop(self, queue: GatewayQueue, worker_id: str) -> None:
        while True:
            try:
                req = await queue.get()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"{worker_id} error getting request: {e}")
                continue

            try:
                meta = req.payload.get("metadata") if isinstance(req.payload.get("metadata"), dict) else {}
                session_id = str(meta.get("chat_id") or meta.get("session_id") or req.payload.get("chat_id") or uuid.uuid4().hex[:12])
                scratchpad_dir = os.environ.get("MIOS_CONVERGE_MEMORY_SCRATCHPAD_DIR", "/tmp")

                conn, path = await asyncio.to_thread(mios_scratchpad.create_scratchpad, session_id, scratchpad_dir)
                try:
                    loop = asyncio.get_running_loop()
                    if _trace_span:
                        async with _trace_span("tool_loop", kind="tool_loop"):
                            res = await asyncio.to_thread(self._run_agent, req.payload, conn, session_id, loop)
                    else:
                        res = await asyncio.to_thread(self._run_agent, req.payload, conn, session_id, loop)
                finally:
                    await asyncio.to_thread(mios_scratchpad.destroy_scratchpad, conn, path)

                if not req.fut.done():
                    req.fut.set_result(res)
            except Exception as e:
                log.exception(f"{worker_id} agent execution failed")
                if not req.fut.done():
                    req.fut.set_exception(e)
            finally:
                queue.task_done()

    def _run_agent(self, payload: dict, conn=None, session_id: str = None, main_loop: asyncio.AbstractEventLoop = None) -> dict:
        system_prompt = extract_system_prompt_from_payload(payload)
        prompt = extract_prompt_from_payload(payload)

        os.environ["LITELLM_TELEMETRY"] = "False"

        model = LiteLLMModel(
            model_id="openai/" + self.model_name,
            api_base=self.endpoint,
            api_key="none"
        )

        agent_tools = list(self.tools)
        if self.mcp_pool is not None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.get_event_loop()
            for sid, client in self.mcp_pool.clients.items():
                for t in self.mcp_pool.get_tools():
                    if t["name"].startswith(f"mcp.{sid}."):
                        agent_tools.append(MCPDispatchTool(
                            name=t["name"],
                            description=t.get("description") or "",
                            inputs=t.get("inputSchema") or {},
                            client=client,
                            main_loop=loop
                        ))

        agent = ToolCallingAgent(
            tools=agent_tools,
            model=model,
            system_prompt=system_prompt
        )

        if conn is not None:
            original_execute = agent.execute_tool_call

            def hooked_execute_tool_call(tool_name: str, arguments) -> Any:
                res = original_execute(tool_name, arguments)
                try:
                    output_str = str(res)
                    _pipe_port = os.environ.get("MIOS_PORTS_AGENT_PIPE", "8700")
                    endpoint = os.environ.get(
                        "MIOS_AI_ENDPOINT", f"http://localhost:{_pipe_port}")
                    url = f"{endpoint}/v1/embeddings"
                    import httpx
                    r = httpx.post(url, json={"input": output_str, "model": "google/embedding-gemma-300m"}, timeout=10.0)
                    if r.status_code == 200:
                        emb = r.json()["data"][0]["embedding"]

                        from mios_firewall import _classify_verb_taint, _session_is_tainted
                        args_dict = arguments if isinstance(arguments, dict) else {}
                        t_tainted, _ = _classify_verb_taint(tool_name, args_dict)

                        if not t_tainted and session_id and main_loop:
                            try:
                                coro = _session_is_tainted(session_id)
                                t_tainted, _ = asyncio.run_coroutine_threadsafe(coro, main_loop).result()
                            except Exception as e:
                                log.warning(f"Failed to check session taint state: {e}")

                        mios_scratchpad.vec_insert(conn, output_str, emb, tainted=t_tainted)
                except Exception as e:
                    log.warning(f"Failed to record scratchpad embedding for tool {tool_name}: {e}")
                return res

            agent.execute_tool_call = hooked_execute_tool_call

        result = agent.run(prompt)

        return {
            "id": "chatcmpl-" + uuid.uuid4().hex[:12],
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self.model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": str(result)
                    },
                    "finish_reason": "stop"
                }
            ]
        }

from mios_mcp_transport import _McpHttpClient, _McpStdioClient

class MCPDispatchTool(Tool):
    def __init__(self, name: str, description: str, inputs: dict, client, main_loop: asyncio.AbstractEventLoop):
        self.name = name
        self.raw_name = name.split(".")[-1]
        self.description = description

        properties = inputs.get("properties") or {}
        required = inputs.get("required") or []
        self.inputs = {}
        for p_name, p_info in properties.items():
            self.inputs[p_name] = {
                "type": p_info.get("type", "string"),
                "description": p_info.get("description", f"Parameter {p_name}"),
                "nullable": p_name not in required
            }
        self.output_type = "string"
        self.client = client
        self.main_loop = main_loop
        super().__init__()

    def forward(self, **kwargs) -> str:
        async def call_mcp():
            res = await self.client.call_tool(self.raw_name, kwargs)
            if res.get("error"):
                raise RuntimeError(res["error"].get("message", "MCP tool call failed"))
            out_texts = []
            for content in (res.get("result") or {}).get("content") or []:
                if content.get("type") == "text":
                    out_texts.append(content.get("text", ""))
            return "\n".join(out_texts)

        fut = asyncio.run_coroutine_threadsafe(call_mcp(), self.main_loop)
        return str(fut.result())

class MCPClientPool:
    def __init__(self, server_configs: dict):
        self.clients = {}
        self.tool_cache = []
        for sid, cfg in (server_configs or {}).items():
            if not cfg.get("enabled", True):
                continue
            transport = cfg.get("transport", "stdio")
            if transport == "stdio":
                self.clients[sid] = _McpStdioClient(
                    sid, cfg.get("command", ""), cfg.get("args") or [],
                    cfg.get("env") or {}, cfg.get("cwd"),
                )
            else:
                self.clients[sid] = _McpHttpClient(
                    sid, cfg.get("url") or cfg.get("server_url") or "",
                    headers=cfg.get("headers") or {}, transport=transport,
                )

    async def startup(self):
        self.tool_cache = []
        for sid, client in self.clients.items():
            try:
                tools_result = await client.list_tools()
                if tools_result.get("error"):
                    raise RuntimeError(tools_result["error"].get("message", "MCP discovery failed"))
                for tool in (tools_result.get("result") or {}).get("tools") or []:
                    self.tool_cache.append({
                        "name": f"mcp.{sid}.{tool['name']}",
                        "description": tool.get("description"),
                        "inputSchema": tool.get("inputSchema") or {},
                      })
            except Exception as e:
                log.error(f"Failed to connect MCP client {sid}: {e}")

    async def shutdown(self):
        for sid, client in self.clients.items():
            try:
                await client.close()
            except Exception:
                pass
        self.clients = {}
        self.tool_cache = []

    def get_tools(self) -> list:
        return self.tool_cache
