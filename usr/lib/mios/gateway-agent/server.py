# AI-hint: MiOS system and orchestration module providing server capabilities.
# AI-related: /usr/share/mios/mios.toml, /etc/mios/mios.toml, mios-gateway-agent, [ports].agent_pipe
# AI-functions: _toml_section, _xpand, openai_chunk, ChatCompletionRequest

import asyncio
import json
import logging
import os
import uuid
import time
from typing import Any, Generator, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
import httpx

from contextlib import asynccontextmanager
from mcp_client import MiOSMCPClient
from tool_registry import MiOSToolRegistry
from skill_catalog import SkillCatalogLoader

import session as session_db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mios-gateway-agent")

mcp_client = None
tool_registry = None
skill_catalog_loader = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global mcp_client, tool_registry, skill_catalog_loader
    gateway_cfg = _toml_section("gateway")
    mcp_refresh = int(gateway_cfg.get("mcp_refresh_seconds") or 300)
    skill_refresh = int(gateway_cfg.get("skill_refresh_seconds") or 300)
    catalog_path = gateway_cfg.get("skill_catalog_static_path") or "/var/lib/mios/skills/catalog.json"

    mcp_client = MiOSMCPClient(mcp_refresh_seconds=mcp_refresh)
    await mcp_client.connect()

    main_loop = asyncio.get_running_loop()
    tool_registry = MiOSToolRegistry(mcp_client, main_loop)

    skill_catalog_loader = SkillCatalogLoader(catalog_path=catalog_path, skill_refresh_seconds=skill_refresh)
    skill_catalog_loader.start()

    yield

    if skill_catalog_loader:
        skill_catalog_loader.stop()
    if mcp_client:
        await mcp_client.close()

app = FastAPI(title="MiOS Gateway Agent Service", lifespan=lifespan)

def _toml_section(section: str) -> dict:
    _layers = [
        os.environ.get("MIOS_TOML", "/usr/share/mios/mios.toml"),
        "/etc/mios/mios.toml",
        os.path.expanduser("~/.config/mios/mios.toml")
    ]
    out: dict = {}
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        for _p in _layers:
            try:
                with open(_p, "rb") as _f:
                    _layer = tomllib.load(_f).get(section) or {}
            except (OSError, tomllib.TOMLDecodeError):
                continue
            if isinstance(_layer, dict):
                out.update(_layer)
    except Exception as e:
        log.warning("Failed to load overlay config section %s: %s", section, e)

    def _xpand(v):
        if isinstance(v, str):
            return os.path.expandvars(v) if "$" in v else v
        if isinstance(v, dict):
            return {k: _xpand(x) for k, x in v.items()}
        if isinstance(v, list):
            return [_xpand(x) for x in v]
        return v
    return _xpand(out)

@app.get("/health")
@app.get("/v1/cluster/health")
async def health():
    return {"status": "ok", "service": "mios-gateway-agent"}

@app.get("/v1/models")
async def models():
    ai_cfg = _toml_section("ai")
    available_models = ai_cfg.get("available_models") or ["granite4.1:3b", "granite4.1:30b", "gpt-oss:20b", "nomic-embed-text"]

    data = []
    for model_id in available_models:
        data.append({
            "id": model_id,
            "object": "model",
            "created": int(time.time()),
            "owned_by": "system"
        })
    return {"object": "list", "data": data}

def openai_error(message: str, *, status: int, err_type: str,
                 code: Optional[str] = None,
                 param: Optional[str] = None) -> JSONResponse:
    """The OpenAI error envelope: `error` is an OBJECT, never a bare string.

    Every client on this endpoint (openai-python, OWUI, the Hermes gateway)
    reads body["error"]["message"], so a `{"error": "..."}` string raised a
    TypeError in the caller instead of surfacing the failure."""
    return JSONResponse(status_code=status, content={"error": {
        "message": message, "type": err_type,
        "param": param, "code": code or err_type}})

class ChatCompletionRequest(BaseModel):
    model: Optional[str] = None
    messages: list[dict]
    stream: Optional[bool] = False
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    tools: Optional[list] = None
    metadata: Optional[dict] = None

@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest):
    # `messages: list[dict]` admits []. That reaches history[-1] below (history is
    # REPLACED by new_incoming on the else branch, so a populated session does not
    # save it) and raises IndexError -> an opaque 500 on a plainly invalid request.
    if not req.messages:
        return openai_error("'messages' must contain at least one message.",
                            status=400, err_type="invalid_request_error",
                            param="messages", code="empty_messages")

    meta = req.metadata or {}
    session_id = str(meta.get("chat_id") or meta.get("session_id") or "default")

    history = await session_db.get_session(session_id)

    new_incoming = req.messages
    if len(history) < len(new_incoming):
        history = history + new_incoming[len(history):]
    else:
        history = new_incoming

    await session_db.save_session(session_id, history)

    gateway_cfg = _toml_section("gateway")
    ai_cfg = _toml_section("ai")

    model_id = req.model or gateway_cfg.get("model") or ai_cfg.get("agent_model") or "granite4.1:3b"
    max_steps = gateway_cfg.get("max_steps", 30)

    # Law 5: the legacy orchestrator port is RETIRED. [ai].endpoint is
    # "http://localhost:${MIOS_PORTS_AGENT_PIPE}/v1" -- resolve it the same way.
    _pipe_port = os.environ.get("MIOS_PORTS_AGENT_PIPE", "8700")
    ai_endpoint = os.environ.get(
        "MIOS_AI_ENDPOINT", "http://localhost:%s/v1" % _pipe_port)

    from smolagents import OpenAIServerModel, ToolCallingAgent

    try:
        model = OpenAIServerModel(
            model_id=model_id,
            api_base=ai_endpoint,
            api_key=os.environ.get("MIOS_AI_KEY", "fake")
        )
    except Exception as e:
        log.error("Failed to initialize OpenAIServerModel: %s", e)
        return openai_error("Model initialization failed", status=500,
                            err_type="api_error", code="model_init_failed")

    tools = []
    if tool_registry:
        tools.extend(tool_registry.get_tools())
    if skill_catalog_loader:
        tools.extend(skill_catalog_loader.get_tools())

    engine = gateway_cfg.get("tool_loop_engine", "smolagents")
    if engine == "native":
        import httpx
        async with httpx.AsyncClient() as client:
            try:
                passthrough_req = req.model_dump(exclude_none=True)
                passthrough_req["model"] = model_id
                resp = await client.post(
                    f"{ai_endpoint.rstrip('/')}/chat/completions",
                    json=passthrough_req,
                    timeout=120.0
                )
                if req.stream:
                    async def raw_stream():
                        async for chunk in resp.aiter_bytes():
                            yield chunk
                    return StreamingResponse(raw_stream(), media_type="text/event-stream")
                else:
                    data = resp.json()
                    if data.get("choices") and data["choices"][0].get("message"):
                        history.append(data["choices"][0]["message"])
                        await session_db.save_session(session_id, history)
                    return JSONResponse(status_code=resp.status_code, content=data)
            except Exception as e:
                log.error("Native pass-through error: %s", e)
                return openai_error("Upstream service unavailable", status=502,
                                    err_type="api_error",
                                    code="upstream_unavailable")

    try:
        agent = ToolCallingAgent(
            tools=tools,
            model=model,
            max_steps=max_steps
        )
    except Exception as e:
        log.error("Failed to initialize ToolCallingAgent: %s", e)
        return openai_error("Agent initialization failed", status=500,
                            err_type="api_error", code="agent_init_failed")

    context = ""
    for msg in history[:-1]:
        role = msg.get("role", "")
        content = msg.get("content", "")
        context += f"{role.upper()}: {content}\n"

    last_user_text = history[-1].get("content", "")
    task = (
        f"Conversation History:\n{context}\n\n"
        f"User request: {last_user_text}\n\n"
        "Guidelines for execution:\n"
        "- If a web search query returns no results (0 results) or irrelevant results, DO NOT give up immediately. "
        "Loop to refine/broaden your search query (e.g., remove specific date/time constraints, try alternative keywords, or search for related topics from the history) and try again.\n"
        "- Perform multiple searches if necessary to gather comprehensive details."
    )

    def openai_chunk(content: str, finish_reason: Optional[str] = None) -> str:
        chunk = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model_id,
            "choices": [{
                "index": 0,
                "delta": {"content": content} if content else {},
                "finish_reason": finish_reason
            }]
        }
        return f"data: {json.dumps(chunk)}\n\n"

    if req.stream:
        async def stream_generator():
            try:
                loop = asyncio.get_running_loop()
                steps = await loop.run_in_executor(None, lambda: list(agent.run(task, stream=True)))

                from smolagents.memory import ActionStep
                from smolagents.agents import FinalAnswerStep

                final_answer = ""
                # A stream MUST close with a chunk carrying a finish_reason. Every
                # path below that emits one flips this; the guard before [DONE]
                # covers the run that produced only ActionSteps and no
                # FinalAnswerStep, which used to end on nothing but null.
                _closed = False
                for step in steps:
                    if isinstance(step, ActionStep):
                        if step.model_output:
                            yield openai_chunk(step.model_output)
                        if step.tool_calls:
                            history.append({
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "id": f"call_{tc.name}",
                                        "type": "function",
                                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}
                                    } for tc in step.tool_calls
                                ]
                            })
                            for tc in step.tool_calls:
                                yield openai_chunk(f"\n[Calling Tool: {tc.name} with arguments: {tc.arguments}]\n")
                        if step.observations:
                            history.append({
                                "role": "tool",
                                "tool_call_id": f"call_{step.tool_calls[0].name}" if step.tool_calls else "unknown",
                                "content": str(step.observations)
                            })
                            yield openai_chunk(f"\n[Observation: {step.observations}]\n")
                    elif isinstance(step, FinalAnswerStep):
                        final_answer = step.output
                        yield openai_chunk(f"\nFinal Answer: {step.output}\n", finish_reason="stop")
                        _closed = True

                if final_answer:
                    history.append({"role": "assistant", "content": str(final_answer)})
                    await session_db.save_session(session_id, history)
            except Exception as stream_err:
                _closed = True
                if type(stream_err).__name__ == "AgentMaxStepsError":
                    yield openai_chunk(f"\n[Agent Max Steps Reached]\n", finish_reason="length")
                else:
                    log.error("Stream generation error: %s", stream_err)
                    # "error" is not an OpenAI finish_reason. The enum is closed --
                    # stop / length / tool_calls / content_filter / function_call --
                    # and a client switching on it drops the turn on anything else.
                    # The failure is already carried in the delta text.
                    yield openai_chunk("\n[Agent execution failed]\n", finish_reason="stop")
            if not _closed:
                yield openai_chunk("", finish_reason="stop")
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream_generator(), media_type="text/event-stream")
    else:
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, lambda: agent.run(task, stream=False))

            response = {
                "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model_id,
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": str(result)
                    },
                    "finish_reason": "stop"
                }],
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0
                }
            }

            history.append({"role": "assistant", "content": str(result)})
            await session_db.save_session(session_id, history)
            return response
        except Exception as run_err:
            if type(run_err).__name__ == "AgentMaxStepsError":
                return {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model_id,
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": "[Agent Max Steps Reached]"},
                        "finish_reason": "length"
                    }]
                }
            log.error("Agent execution error: %s", run_err)
            return openai_error("Agent execution failed", status=500,
                                err_type="api_error", code="agent_loop_failed")

if __name__ == "__main__":
    import uvicorn
    gateway_cfg = _toml_section("gateway")
    port = int(os.environ.get("MIOS_PORTS_HERMES", gateway_cfg.get("port") or 8720))
    uvicorn.run(app, host="0.0.0.0", port=port)
