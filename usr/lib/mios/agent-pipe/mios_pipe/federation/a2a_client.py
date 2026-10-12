# AI-hint: A2A PEER-CLIENT consumer half extracted VERBATIM from server.py (refactor R11 federation follow-up).
# AI-doc: usr/share/doc/mios/manual/federation.md

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
import logging
from typing import Optional

import httpx

from mios_a2a import _a2a_principal_metadata
from mios_mcp import _mcp_render_headers
from mios_jsonsalvage import loads_lenient as _loads_lenient
from mios_config import _toml_section

log = logging.getLogger("mios-agent-pipe")

_A2A_PEERS: dict = {}
_A2A_PEER_SKILLS: dict = {}
_A2A_PEERS_LOCK = None
_A2A_REPUTATION = None
_AGENT_REGISTRY: dict = {}
_A2A_PEER_REGISTRY_PATHS: list = []
A2A_COUNCIL = False
A2A_SELF_ID = "local-mios"
ROUTE_ON_CARD_SKILLS = False
_get_client = None

def _invalidate_worker_cache() -> None:
    """Default no-op until server injects its _WORKER_TOOLS_FULL_CACHE invalidator."""
    return None

def configure(*, a2a_peers=None, a2a_peer_skills=None, a2a_peers_lock=None,
              a2a_reputation=None, agent_registry=None,
              a2a_peer_registry_paths=None, a2a_council=None, a2a_self_id=None,
              get_client=None, route_on_card_skills=None,
              invalidate_worker_cache=None) -> None:
    """Inject server.py's runtime deps. Mutable registries (_A2A_PEERS/
    _A2A_PEER_SKILLS/_AGENT_REGISTRY) are injected BY REFERENCE so server-side
    mutation stays visible to the probe + dispatch paths."""
    g = globals()
    if a2a_peers is not None:
        g["_A2A_PEERS"] = a2a_peers
    if a2a_peer_skills is not None:
        g["_A2A_PEER_SKILLS"] = a2a_peer_skills
    if a2a_peers_lock is not None:
        g["_A2A_PEERS_LOCK"] = a2a_peers_lock
    if a2a_reputation is not None:
        g["_A2A_REPUTATION"] = a2a_reputation
    if agent_registry is not None:
        g["_AGENT_REGISTRY"] = agent_registry
    if a2a_peer_registry_paths is not None:
        g["_A2A_PEER_REGISTRY_PATHS"] = a2a_peer_registry_paths
    if a2a_council is not None:
        g["A2A_COUNCIL"] = a2a_council
    if a2a_self_id is not None:
        g["A2A_SELF_ID"] = a2a_self_id
    if get_client is not None:
        g["_get_client"] = get_client
    if route_on_card_skills is not None:
        g["ROUTE_ON_CARD_SKILLS"] = route_on_card_skills
    if invalidate_worker_cache is not None:
        g["_invalidate_worker_cache"] = invalidate_worker_cache

def _a2a_self_peer_url(url: str) -> bool:
    _self_port = str(os.environ.get("MIOS_PORTS_AGENT_PIPE", "8700")).strip()
    u = (url or "").lower()
    return (f":{_self_port}" in u) and (
        "127.0.0.1" in u or "localhost" in u or "://[::1]" in u or "0.0.0.0" in u)

async def _a2a_fetch_card(url: str, headers: dict,
                          timeout_s: float = 10.0) -> dict:
    """Try the spec's 0.3 well-known path, fall back to the legacy and the
    /v1 alias so we discover MiOS-flavoured peers AND clean A2A 0.3 peers.
    Returns the parsed card dict, or {"error": …}."""
    candidates = [
        url.rstrip("/") + "/.well-known/agent-card.json",
        url.rstrip("/") + "/.well-known/agent.json",
        url.rstrip("/") + "/v1/agent-card",
    ]
    h = _mcp_render_headers(headers or {})
    h.setdefault("Accept", "application/json")
    last_err: Optional[str] = None
    client = await _get_client()
    for candidate in candidates:
        try:
            r = await client.get(candidate, headers=h, timeout=timeout_s)
        except httpx.HTTPError as e:
            last_err = f"http error at {candidate}: {e}"
            continue
        if r.status_code != 200:
            last_err = f"{r.status_code} at {candidate}"
            continue
        try:
            card = r.json()
        except (json.JSONDecodeError, ValueError):
            last_err = f"non-JSON card at {candidate}"
            continue
        if isinstance(card, dict):
            card["_fetched_from"] = candidate
            return card
        last_err = f"card not an object at {candidate}"
    return {"error": last_err or "no card endpoint responded"}

async def _a2a_fetch_models_card(url: str, headers: dict, timeout_s: float = 10.0) -> dict:
    """Probe /v1/models as a fallback for cardless agents (Claude, Gemini, vLLM).
    Infers capabilities from model names and returns a synthetic agent card."""
    h = _mcp_render_headers(headers or {})
    h.setdefault("Accept", "application/json")
    client = await _get_client()
    models_url = url.rstrip("/") + "/v1/models"
    try:
        r = await client.get(models_url, headers=h, timeout=timeout_s)
        if r.status_code == 200:
            res = r.json()
            if isinstance(res, dict) and "data" in res:
                data = res["data"]
                model_ids = []
                if isinstance(data, list):
                    for m in data:
                        if isinstance(m, dict) and m.get("id"):
                            model_ids.append(str(m["id"]))

                skills = []
                has_text = False
                has_embed = False
                has_image = False
                _routing_cfg = _toml_section("routing") or {}
                _embed_kw = (os.environ.get("MIOS_ROUTING_MODEL_MODALITIES_EMBEDDINGS")
                             or _routing_cfg.get("model_modalities_embeddings")
                             or ["embed", "bert", "text-embedding", "bge"])
                if isinstance(_embed_kw, str):
                    _embed_kw = [x.strip() for x in _embed_kw.split(",") if x.strip()]
                _image_kw = (os.environ.get("MIOS_ROUTING_MODEL_MODALITIES_IMAGE")
                             or _routing_cfg.get("model_modalities_image")
                             or ["diffuse", "flux", "dall", "midjourney", "sd"])
                if isinstance(_image_kw, str):
                    _image_kw = [x.strip() for x in _image_kw.split(",") if x.strip()]
                for mid in model_ids:
                    mid_lower = mid.lower()
                    if any(x in mid_lower for x in _embed_kw):
                        has_embed = True
                    elif any(x in mid_lower for x in _image_kw):
                        has_image = True
                    else:
                        has_text = True

                if has_text or not model_ids:
                    skills.append({
                        "id": "text-generation",
                        "name": "Text Generation",
                        "description": "Generative text generation and reasoning capabilities",
                    })
                if has_embed:
                    skills.append({
                        "id": "embeddings",
                        "name": "Embeddings",
                        "description": "Vector embedding generation",
                    })
                if has_image:
                    skills.append({
                        "id": "image-generation",
                        "name": "Image Generation",
                        "description": "Image generation and styling capabilities",
                    })

                return {
                    "name": url.split("//", 1)[-1].split(":", 1)[0],
                    "provider": {"organization": "Cardless"},
                    "version": "0.3.0",
                    "protocolVersion": "0.3.0",
                    "skills": skills,
                    "_cardless": True,
                    "_fetched_from": models_url
                }
    except Exception as e:
        return {"error": f"models probe failed: {e}"}
    return {"error": "no models endpoint responded or invalid response"}

async def _a2a_tailnet_candidates() -> list:
    try:
        port = int(os.environ.get("MIOS_A2A_DISCOVER_PORT") or (_toml_section("a2a") or {}).get("discover_port") or 8700)
    except ValueError:
        port = 8700
    urls: list = []
    for u in (os.environ.get("MIOS_A2A_DISCOVER_URLS", "") or "").split(","):
        u = u.strip().rstrip("/")
        if u:
            urls.append(u)
    try:
        p = await asyncio.create_subprocess_exec(
            "tailscale", "status", "--json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(p.communicate(), timeout=6)
        data = _loads_lenient((out or b"").decode("utf-8", "replace") or "{}")
        for peer in (data.get("Peer") or {}).values():    # Peer = OTHERS, not Self
            if not isinstance(peer, dict) or not peer.get("Online"):
                continue
            for ip in (peer.get("TailscaleIPs") or [])[:1]:   # first (v4) IP
                if ip:
                    urls.append(f"http://{ip}:{port}")
    except Exception as e:  # noqa: BLE001
        log.debug("a2a discover: tailscale status unavailable: %s", e)
    seen: set = set(); out_urls: list = []
    for u in urls:
        if u not in seen:
            seen.add(u); out_urls.append(u)
    return out_urls

def _a2a_load_peers() -> list:
    """Layered peer registry read: vendor < /etc < user. Later overlays
    REPLACE earlier entries with the same id (matches MCP client semantics)
    so an operator can disable a vendor peer by re-declaring it disabled.
    The LOCAL self-peer (loopback :8700) is EXCLUDED -- it is a self-loop vector
    (see _a2a_self_peer_url); delegation to oneself is a no-op on a single node."""
    by_id: dict = {}
    for p in _A2A_PEER_REGISTRY_PATHS:
        try:
            with open(p) as f:
                d = json.load(f) or {}
        except (OSError, json.JSONDecodeError):
            continue
        for s in (d.get("peers") or []):
            if not isinstance(s, dict):
                continue
            pid = str(s.get("id") or s.get("peer_id") or "").strip()
            if not pid:
                continue
            if _a2a_self_peer_url(str(s.get("url") or "")):
                log.info("a2a: excluding self-peer %r (%s) -- local orchestrator, "
                         "would self-loop", pid, s.get("url"))
                continue
            by_id[pid] = s
    return list(by_id.values())

async def _a2a_probe_peer(cfg: dict) -> None:
    """Fetch ONE peer's agent card, index its declared skills. Errors land in
    the per-peer state dict (never raise) so a single bad peer doesn't break
    startup -- mirrors _mcp_probe_server's contract."""
    pid = str(cfg.get("id") or cfg.get("peer_id") or "").strip()
    if not pid:
        return
    url = (cfg.get("url") or cfg.get("base_url") or "").rstrip("/") or ""
    state: dict = {"id": pid, "url": url, "status": "connecting",
                   "label": cfg.get("label") or pid,
                   "card": None, "skills": [],
                   "headers_template": cfg.get("headers") or {}}
    async with _A2A_PEERS_LOCK:
        _A2A_PEERS[pid] = state

    if not cfg.get("enabled", True):
        state["status"] = "disabled"
        return
    if not url:
        state["status"] = "config-error"
        state["error"] = "missing url"
        return

    card = await _a2a_fetch_card(url, cfg.get("headers") or {})
    if card.get("error"):
        cardless_card = await _a2a_fetch_models_card(url, cfg.get("headers") or {})
        if cardless_card.get("error"):
            state["status"] = "card-fetch-failed"
            state["error"] = card["error"]
            log.warning("a2a client: card fetch and models probe failed for %s: %s",
                        pid, state["error"])
            return
        card = cardless_card
    state["card"] = card
    state["protocolVersion"] = card.get("protocolVersion") or next(
        (i.get("protocolVersion")
         for i in (card.get("supportedInterfaces") or [])
         if isinstance(i, dict) and i.get("protocolVersion")), None)
    state["agent_name"] = card.get("name")
    skills = []
    if isinstance(card.get("skills"), list):
        for s in card["skills"]:
            if isinstance(s, dict) and s.get("id"):
                skills.append({
                    "id": str(s.get("id")),
                    "name": s.get("name"),
                    "description": s.get("description"),
                    "tags": s.get("tags") or [],
                })
    state["skills"] = skills

    async with _A2A_PEERS_LOCK:
        for sid in list(_A2A_PEER_SKILLS.keys()):
            _A2A_PEER_SKILLS[sid] = [
                p for p in _A2A_PEER_SKILLS[sid] if p != pid]
            if not _A2A_PEER_SKILLS[sid]:
                _A2A_PEER_SKILLS.pop(sid, None)
        for s in skills:
            sid = s["id"]
            _A2A_PEER_SKILLS.setdefault(sid, []).append(pid)
    state["status"] = "ready"
    try:
        _a2a_fanout = bool(A2A_COUNCIL and (pid or "").strip().lower() != A2A_SELF_ID)
        _peer_cfg = {
            "endpoint": "", "model": pid, "role": "general",
            "default": False, "lane": "remote", "fanout": _a2a_fanout,
            "a2a_peer_id": pid, "research_only": False, "engines": {},
            "strengths": [str(s.get("id") or "") for s in (skills or [])],
        }
        if ROUTE_ON_CARD_SKILLS and skills:
            _peer_cfg["card_skills"] = skills
        _AGENT_REGISTRY[f"a2a:{pid}"] = _peer_cfg
        _invalidate_worker_cache()
    except Exception:  # noqa: BLE001
        pass
    log.info("a2a client: %s ready (%d skills, protocol %s)",
             pid, len(skills), state.get("protocolVersion"))

async def _a2a_autodiscover_peers(known_urls: set) -> list:
    if os.environ.get("MIOS_A2A_TAILNET_DISCOVER", "").strip().lower() \
            not in {"1", "true", "yes"}:
        return []
    cands = [u for u in await _a2a_tailnet_candidates()
             if u.rstrip("/") not in known_urls]
    if not cands:
        return []
    log.info("a2a autodiscover: probing %d tailnet candidate(s)", len(cands))

    async def _probe(url: str) -> Optional[dict]:
        card = await _a2a_fetch_card(url, {}, timeout_s=5.0)
        if not isinstance(card, dict) or card.get("error"):
            return None
        name = str(card.get("name") or card.get("agent_name") or "").strip()
        pid = (str(card.get("id") or "").strip()
               or "auto-" + url.split("//", 1)[-1].replace(":", "-").replace("/", ""))
        return {"id": pid, "url": url, "label": name or pid,
                "_autodiscovered": True}

    found = await asyncio.gather(*(_probe(u) for u in cands),
                                 return_exceptions=True)
    return [c for c in found if isinstance(c, dict)]

async def _a2a_client_startup() -> None:
    """Read the peer registry (+ optional tailnet auto-discovery), probe every
    enabled peer concurrently. Errors per peer are captured in state; total
    startup never blocks on a slow peer."""
    if os.environ.get("MIOS_A2A_CLIENT_DISABLED",
                      "").strip().lower() in {"1", "true", "yes"}:
        log.info("a2a client: disabled by env (MIOS_A2A_CLIENT_DISABLED)")
        return
    peers = _a2a_load_peers()
    _known = {str((p.get("url") or "")).rstrip("/") for p in peers}
    _disc = await _a2a_autodiscover_peers(_known)
    if _disc:
        log.info("a2a autodiscover: +%d tailnet peer(s)", len(_disc))
        peers = peers + _disc
    if not peers:
        log.info("a2a client: registry empty + no tailnet peers discovered")
        return
    log.info("a2a client: probing %d peer(s) (%d registry + %d discovered)",
             len(peers), len(peers) - len(_disc), len(_disc))
    await asyncio.gather(*(_a2a_probe_peer(s) for s in peers),
                         return_exceptions=True)

async def _a2a_send_message_to_peer(peer_id: str, text: str,
                                    context_id: Optional[str] = None,
                                    timeout_s: float = 120.0) -> dict:
    """POST a JSON-RPC message/send to one A2A peer's /a2a endpoint and return
    the Task envelope (or {"error": …}). Used by /v1/a2a/dispatch + the
    upcoming P2.2 live agent-to-agent delegation path."""
    async with _A2A_PEERS_LOCK:
        peer = _A2A_PEERS.get(peer_id)
    if not peer:
        return {"error": f"unknown A2A peer: {peer_id}"}
    if peer.get("status") != "ready":
        return {"error": f"peer {peer_id} not ready ({peer.get('status')})"}
    url = (peer.get("url") or "").rstrip("/") + "/a2a"
    headers = _mcp_render_headers(peer.get("headers_template") or {})
    headers.setdefault("Content-Type", "application/json")
    headers.setdefault("Accept", "application/json")
    msg = {
        "role": "ROLE_USER",
        "messageId": uuid.uuid4().hex,
        "parts": [{"text": str(text or ""), "mediaType": "text/plain"}],
    }
    if context_id:
        msg["contextId"] = context_id
    _pp = _a2a_principal_metadata(text, peer_id, context_id)
    if _pp:
        msg["metadata"] = {**(msg.get("metadata") or {}), "mios_principal": _pp}
    body = {
        "jsonrpc": "2.0",
        "id": int(time.time() * 1000) & 0x7FFFFFFF,
        "method": "message/send",
        "params": {"message": msg},
    }
    result: dict
    try:
        client = await _get_client()
        r = await client.post(url, json=body, headers=headers,
                              timeout=timeout_s)
    except httpx.HTTPError as e:
        result = {"error": f"http error: {e}", "peer_id": peer_id}
    else:
        if r.status_code != 200:
            result = {"error": f"status {r.status_code}: {(r.text or '')[:200]}",
                      "peer_id": peer_id}
        else:
            try:
                resp = r.json()
            except (json.JSONDecodeError, ValueError):
                result = {"error": "non-JSON response", "peer_id": peer_id}
            else:
                if resp.get("error"):
                    err = resp["error"]
                    result = {"error": err.get("message") or "rpc error",
                              "code": err.get("code"), "peer_id": peer_id}
                else:
                    res = resp.get("result")
                    if isinstance(res, dict):
                        result = res.get("task") or res.get("message") or res
                    else:
                        result = {}
    _A2A_REPUTATION.record(
        peer_id, not (isinstance(result, dict) and result.get("error")))
    return result

def _a2a_extract_text(env: dict) -> str:
    """Pull the assistant text out of an A2A Task envelope (artifacts[].parts[]
    or status.message.parts[]) -- _a2a_send_message_to_peer returns the raw Task
 object, not plain text (P0)."""
    if not isinstance(env, dict) or env.get("error"):
        return ""
    def _parts(parts):
        return "".join(str(p.get("text") or "") for p in (parts or [])
                       if isinstance(p, dict))
    for art in (env.get("artifacts") or []):
        t = _parts(art.get("parts"))
        if t.strip():
            return t.strip()
    msg = ((env.get("status") or {}).get("message")) or env.get("message") or {}
    return _parts(msg.get("parts")).strip()
