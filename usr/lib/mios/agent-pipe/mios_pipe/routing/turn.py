# AI-hint: PER-TURN message-prep + agent-selection helpers extracted VERBATIM from AI-related: ./server.py, ./mios_config.py, ./test_mios_turn.py AI-fun...
# AI-doc: usr/share/doc/mios/manual/routing.md

from __future__ import annotations

import asyncio
import os
import re
import time

import httpx
from mios_pipe.kernel.config import PROBE_VERIFY_TLS as _PROBE_VERIFY_TLS

_AGENT_REGISTRY = None
_NODE_LIVE = None
_should_health_probe = None
_probe_auth_headers = None
NODE_LIVENESS_TTL_S = 45.0
NODE_LIVENESS_CONNECT_S = 6.0
_THINK_TAGS = r"think|thinking|thought|reasoning|reflection|scratchpad"
_THINK_OPENERS = ("<think", "<thought", "<reason", "<reflect", "<scratch")
_THINK_CAP_RE = re.compile(
    rf"<({_THINK_TAGS})\b[^>]*>(.*?)</\1>", re.DOTALL | re.IGNORECASE)
_THINK_CAP_UNCLOSED_RE = re.compile(
    rf"<({_THINK_TAGS})\b[^>]*>(.*)$", re.DOTALL | re.IGNORECASE)
_THINK_ORPHAN_RE = re.compile(
    rf"</?({_THINK_TAGS})\b[^>]*>\s*", re.IGNORECASE)

_INJECTED = frozenset((
    "_AGENT_REGISTRY", "_NODE_LIVE", "_should_health_probe", "_probe_auth_headers",
    "NODE_LIVENESS_TTL_S", "NODE_LIVENESS_CONNECT_S", "_THINK_OPENERS", "_THINK_CAP_RE",
    "_THINK_CAP_UNCLOSED_RE", "_THINK_ORPHAN_RE",
))

def configure(**deps) -> None:
    g = globals()
    for _k, _v in deps.items():
        if _k in _INJECTED:
            g[_k] = _v

async def _live_agent_names() -> set:
    live: set = set()
    to_probe: list = []
    now = time.time()
    for name, cfg in _AGENT_REGISTRY.items():
        if (not isinstance(cfg, dict) or cfg.get("enabled") is False
                or not isinstance(cfg.get("endpoint"), str)
                or not cfg["endpoint"].strip()):
            continue
        if not _should_health_probe(cfg):
            live.add(name)
            continue
        cached = _NODE_LIVE.get(name)
        if cached and (now - cached[0]) < NODE_LIVENESS_TTL_S:
            if cached[1]:
                live.add(name)
        else:
            to_probe.append((name, cfg))
    if to_probe:
        _to = httpx.Timeout(connect=NODE_LIVENESS_CONNECT_S,
                            read=NODE_LIVENESS_CONNECT_S, write=2.0, pool=2.0)

        async def _probe1(client, ep: str) -> bool:
            ep = (ep or "").rstrip("/")
            if not ep:
                return False
            try:  # OpenAI /v1/models (llama.cpp + vLLM + every MiOS lane speak this)
                r = await client.get(f"{ep}/models", headers=_probe_auth_headers(ep))
                return 200 <= r.status_code < 300
            except Exception:
                return False

        try:
            async with httpx.AsyncClient(verify=_PROBE_VERIFY_TLS, timeout=_to,
                                         follow_redirects=False) as client:
                results = await asyncio.gather(
                    *[_probe1(client, c.get("endpoint")) for _n, c in to_probe],
                    return_exceptions=True)
        except Exception:  # noqa: BLE001 -- probe is best-effort; degrade open
            results = [False] * len(to_probe)
        for (name, _cfg), ok in zip(to_probe, results):
            ok = bool(ok) and not isinstance(ok, Exception)
            _NODE_LIVE[name] = (time.time(), ok)
            if ok:
                live.add(name)
    return live

def _pick_agent(role: str) -> tuple[str, dict]:
    role = (role or "").lower().strip()
    chosen = None
    if role:
        for name, cfg in _AGENT_REGISTRY.items():
            if cfg.get("role", "").lower() == role:
                chosen = (name, cfg)
                break
    if chosen is None:
        for name, cfg in _AGENT_REGISTRY.items():
            if cfg.get("default"):
                chosen = (name, cfg)
                break
    if chosen is None:
        _n = next(iter(_AGENT_REGISTRY))
        chosen = (_n, _AGENT_REGISTRY[_n])
    name, cfg = chosen
    if cfg.get("health_gate"):
        _c = _NODE_LIVE.get(name)
        if not (_c and _c[1]):  # not confirmed reachable -> fall back to BACKEND
            _fb_model = (os.environ.get("MIOS_AI_MODEL") or "").strip()
            cfg = {**cfg, "endpoint": "", **({"model": _fb_model} if _fb_model else {})}
    return name, cfg

def _split_think_tags(text: str) -> tuple[str, str]:
    if not text:
        return "", text
    low = text.lower()
    if not any(t in low for t in _THINK_OPENERS):
        return "", text
    thoughts: list[str] = []

    def _cap(m: "re.Match") -> str:
        thoughts.append((m.group(2) or "").strip())
        return ""
    answer = _THINK_CAP_RE.sub(_cap, text)
    m = _THINK_CAP_UNCLOSED_RE.search(answer)
    if m:
        thoughts.append((m.group(2) or "").strip())
        answer = _THINK_CAP_UNCLOSED_RE.sub("", answer)
    answer = _THINK_ORPHAN_RE.sub("", answer)
    answer = re.sub(r"[ \t]+", " ", answer)
    answer = re.sub(r"\n\s*\n", "\n", answer).strip()
    reasoning = "\n\n".join(t for t in thoughts if t).strip()
    return reasoning, answer

def _strip_think_tags(text: str) -> str:
    """Back-compat: return only the answer (reasoning discarded). Use
    _split_think_tags when the reasoning should be KEPT for a dropdown."""
    return _split_think_tags(text)[1]

def _casual_agent_label(target_name: str) -> str:
    cfg = _AGENT_REGISTRY.get(target_name) or {}
    role = str(cfg.get("role") or "").strip().lower()
    if role:
        return f"{role}-agent"
    return "sub-agent"

def _extract_last_user_text(messages: list) -> str:
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if not isinstance(m, dict):
            continue
        if m.get("role") != "user":
            continue
        c = m.get("content") or ""
        if isinstance(c, list):
            for part in c:
                if isinstance(part, dict) and part.get("type") == "text":
                    return part.get("text", "")
            return ""
        return c if isinstance(c, str) else ""
    return ""
