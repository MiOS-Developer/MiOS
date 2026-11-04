# AI-hint: Layer-1 micro-LLM CLASSIFIER cluster, extracted verbatim from server.py
# AI-related: server.py, mios_config.py, mios_jsonsalvage.py, mios_chat.py, mios_refine.py
# AI-functions: classify_intent, _route_domain, configure
"""Layer-1 micro-LLM classifiers (classify_intent + _route_domain), from server.py."""

from __future__ import annotations

from mios_lanes_resolver import _pick_inference_backend

import asyncio
import json
import os
import re
import logging
from typing import Optional

import httpx

from mios_config import (
    ROUTER_ENABLED,
    ROUTER_MODEL,
    ROUTER_ENDPOINT,
    ROUTER_TIMEOUT_S,
    ROUTER_MAX_TOKENS,
    _ROUTER_SYSTEM,
    PLANNER_ENDPOINT,
    PLANNER_TIMEOUT_S,
)
from mios_jsonsalvage import loads_lenient as _loads_lenient

log = logging.getLogger("mios-agent-pipe")

_VERB_CATALOG: dict = {}
_ROUTING_DOMAINS: dict = {}
_ROUTING_ENABLE: bool = False
_db_create = None
_db_post = None
_db_fire = None

def configure(*, verb_catalog=None, routing_domains=None, routing_enable=None,
              db_create=None, db_post=None, db_fire=None) -> None:
    """Inject the server-owned hot globals + event-DB helpers. One-way boundary:
    mios_classify never imports server. routing_enable may legitimately be False, so
    each field is gated on ``is not None`` (not truthiness)."""
    global _VERB_CATALOG, _ROUTING_DOMAINS, _ROUTING_ENABLE
    global _db_create, _db_post, _db_fire
    if verb_catalog is not None:
        _VERB_CATALOG = verb_catalog
    if routing_domains is not None:
        _ROUTING_DOMAINS = routing_domains
    if routing_enable is not None:
        _ROUTING_ENABLE = routing_enable
    if db_create is not None:
        _db_create = db_create
    if db_post is not None:
        _db_post = db_post
    if db_fire is not None:
        _db_fire = db_fire

async def classify_intent(user_text: str) -> Optional[dict]:
    """Call the micro-LLM router. Returns the parsed verdict dict
    or None to fall through to backend proxy. Best-effort: any error
    falls through cleanly."""
    if not ROUTER_ENABLED or not user_text or not user_text.strip():
        return None
    payload = {
        "model": ROUTER_MODEL,
        "messages": [
            {"role": "system", "content": _ROUTER_SYSTEM},
            {"role": "user",   "content": user_text[:2000]},
        ],
        "temperature": 0.0,
        "max_tokens": ROUTER_MAX_TOKENS,
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    if os.environ.get("MIOS_ROUTER_STRUCTURED", "true").strip().lower() not in {
            "0", "false", "no", "off"}:
        payload["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "mios_route", "strict": True, "schema": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "action": {"type": "string",
                               "enum": ["dispatch", "chat", "agent"]},
                    "tool": {"type": ["string", "null"],
                             "enum": sorted(_VERB_CATALOG.keys()) + [None]},
                    "args": {"type": ["object", "null"], "additionalProperties": True},
                    "reason": {"type": ["string", "null"]},
                    "reply": {"type": ["string", "null"]}},
                "required": ["action", "tool", "args", "reason", "reply"]}}}
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    endpoint, selected_model = await _pick_inference_backend(
        ROUTER_ENDPOINT, ROUTER_MODEL,
        endpoint_env=("MIOS_AGENT_PIPE_ROUTER_ENDPOINT", "MIOS_MICRO_ENDPOINT"),
        model_env=("MIOS_AGENT_PIPE_ROUTER_MODEL", "MIOS_MICRO_MODEL"))
    payload["model"] = selected_model
    url = f"{endpoint}/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=ROUTER_TIMEOUT_S) as s:
            r = await s.post(url, json=payload,
                             headers={"Content-Type": "application/json"})
            if r.status_code != 200:
                return None
            body = r.json()
    except (httpx.HTTPError, asyncio.TimeoutError):
        return None
    except Exception as e:
        log.warning("router unexpected error: %s", e)
        return None
    choices = body.get("choices") or []
    msg = (choices[0].get("message") if choices else {}) or {}
    content = (msg.get("content") or "").strip()
    if not content:
        return None
    content = re.sub(r"^\s*```(?:json)?\s*\n?", "", content)
    content = re.sub(r"\n?```\s*$", "", content)
    try:
        parsed = _loads_lenient(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict) or "action" not in parsed:
        return None
    _db_fire(_db_post(_db_create("event", {
        "source": "mios-agent-pipe",
        "kind": "classify",
        "severity": "info",
        "summary": str(parsed.get("action", "?"))[:120],
        "payload": parsed,
    }, now_fields=("ts",))))
    return parsed

async def _route_domain(user_text: str) -> Optional[str]:
    if not _ROUTING_ENABLE or not _ROUTING_DOMAINS or not (user_text or "").strip():
        return None
    names = list(_ROUTING_DOMAINS.keys())
    sys = ("Classify the user request into a capability domain only when one is needed (the kind of "
           "capability it needs). Return domain=null for conversation or a reply that needs no listed capability. "
           "Do not force conversation into an action or machine-state domain. Domains:\n"
           + "\n".join(f"{n}: {_ROUTING_DOMAINS[n]['desc']}" for n in names))
    payload = {
        "model": ROUTER_MODEL,
        "messages": [{"role": "system", "content": sys},
                     {"role": "user", "content": user_text[:2000]}],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "route", "strict": True, "schema": {
                "type": "object",
                "properties": {"domain": {"type": ["string", "null"], "enum": names + [None]}},
                "required": ["domain"], "additionalProperties": False}}},
        "chat_template_kwargs": {"enable_thinking": False},
        "temperature": 0.0, "max_tokens": 30, "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=PLANNER_TIMEOUT_S) as s:
            endpoint, payload["model"] = await _pick_inference_backend(
                PLANNER_ENDPOINT, ROUTER_MODEL,
                endpoint_env=("MIOS_AGENT_PIPE_PLANNER_ENDPOINT",),
                model_env=("MIOS_AGENT_PIPE_ROUTER_MODEL", "MIOS_MICRO_MODEL"))
            r = await s.post(f"{endpoint}/chat/completions", json=payload,
                             headers={"Content-Type": "application/json"})
        if r.status_code != 200:
            return None
        content = ((r.json().get("choices") or [{}])[0].get("message", {})
                   .get("content") or "")
        dom = (_loads_lenient(content) or {}).get("domain")
        if dom in _ROUTING_DOMAINS:
            log.info("router: domain=%s <- %s", dom, user_text[:48].replace(chr(10), " "))
            return dom
        log.info("router: out-of-enum %r -> full surface", dom)
    except Exception as e:
        log.info("router classify failed (-> full surface): %s", e)
    return None
