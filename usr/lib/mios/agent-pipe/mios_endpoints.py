# AI-hint: Pure endpoint capability detection extracted verbatim from server.py (strangler-fig refactor R-wave).
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md

from __future__ import annotations

import os
from typing import Optional

from mios_config import _DISPATCH_TOML

def _binding_api(cfg: dict, engine: Optional[str]) -> str:
    """The feature-family an endpoint declares, from CONFIG: the engine binding's
    `api` field, else the agent's top-level `api`, else '' (auto-detect). Lets ANY
    agent/model on ANY node declare its engine (e.g. 'llamacpp'/'vllm'/'openai')
    explicitly so the capability probes below don't have to guess from the port."""
    if engine:
        b = (cfg.get("engines") or {}).get(str(engine).lower().strip())
        if isinstance(b, dict) and b.get("api"):
            return str(b["api"]).strip().lower()
    return str(cfg.get("api") or "").strip().lower()

_NO_TOOL_CHOICE_API = {"llamacpp", "llama.cpp", "llama-server", "vulkan"}
_NO_TOOL_CHOICE_HINTS = tuple(
    h.strip() for h in str(os.environ.get("MIOS_NO_TOOL_CHOICE_HINTS")
                           or _DISPATCH_TOML.get("no_tool_choice_hints", "8450,8458,11436")).split(",")
    if h.strip())

def _endpoint_supports_tool_choice(ep: str, cfg: dict,
                                   engine: Optional[str] = None) -> bool:
    """False when the endpoint rejects tool_choice='required' (llama.cpp). The
    agent/engine can opt out via api=llamacpp or an explicit tool_choice=false;
    else fall back to the env-SSOT host:port hint list."""
    api = _binding_api(cfg, engine)
    if api in _NO_TOOL_CHOICE_API:
        return False
    if cfg.get("tool_choice") is False or \
            (engine and isinstance((cfg.get("engines") or {}).get(engine), dict)
             and (cfg["engines"][engine].get("tool_choice") is False)):
        return False
    return not any(h and h in (ep or "") for h in _NO_TOOL_CHOICE_HINTS)

_PARALLEL_TOOLS_HINTS = tuple(
    h.strip() for h in str(os.environ.get("MIOS_PARALLEL_TOOLS_HINTS")
                           or _DISPATCH_TOML.get("parallel_tools_hints", "8520")).split(",")
    if h.strip())

def _endpoint_supports_parallel_tools(ep: str) -> bool:
    """True when the endpoint's model reliably emits well-formed PARALLEL tool calls
    (the capable heavy lane) -> OpenAI-default parallel_tool_calls. Default False
    (sequential, robust for small local models); opt IN via the SSOT hint list."""
    return any(h and h in (ep or "") for h in _PARALLEL_TOOLS_HINTS)

_LLAMACPP_API = {"llamacpp", "llama.cpp", "llama-server", "vulkan"}
_KV_PAGING_HINTS = tuple(
    h.strip() for h in str(os.environ.get("MIOS_KV_PAGING_HINTS")
                           or _DISPATCH_TOML.get("kv_paging_hints", "8450,8458,11436")).split(",")
    if h.strip())

def _endpoint_is_llamacpp(ep: str, cfg: dict, engine: Optional[str] = None) -> bool:
    """True when `ep` is a llama.cpp llama-server that can do /slots KV paging.
    CONFIG-FIRST (api='llamacpp'/'vulkan' on the agent/engine), else the
    env/SSOT host:port hint list -- no bare port literal in the routing code."""
    if _binding_api(cfg, engine) in _LLAMACPP_API:
        return True
    return any(h and h in (ep or "") for h in _KV_PAGING_HINTS)
