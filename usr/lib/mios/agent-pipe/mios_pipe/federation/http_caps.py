# AI-hint: ADVERTISED-SURFACE / capability + read-only admin route-handler LOGIC extracted VERBATIM from server.py (refactor R-CAPS wave).
# AI-doc: usr/share/doc/mios/manual/federation.md

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import mios_capreg
from mios_dci import (
    DCI_ENABLED,
    _DCI_ACTS,
    _DCI_ACT_NAMES,
    _DCI_ACT_SCHEMA,
)

log = logging.getLogger("mios-agent-pipe")

_VERB_CATALOG: dict = {}
_A2A_PEERS: dict = {}
_A2A_PEERS_LOCK = None
_KERNEL = None
_COST_LEDGER = None
_COST_MODEL = None
COST_ACCOUNTING_ENABLE = False
COST_BUDGET_USD = 0.0
_TRACER = None
BACKEND = ""

_verb_to_openai_tool = None
_recipe_to_openai_tool = None
_skill_to_openai_tool = None
_load_recipe_catalog = None
_skill_list = None
_skill_fetch = None
_user_rbac_filter = None
_match_user_cfg = None
_toml_section = None
_cap_skills = None
_get_client = None
kg_lookup = None
execute_skill = None
run_dci_flow = None
_offline_posture = None
_PROMPT_REGISTRY = None
_db_read = None
RUN_TEMPLATE_ENABLE = False
_MCP_CLIENT_TOOLS = None
_MCP_CLIENT_LOCK = None

def configure(*, verb_catalog=None, a2a_peers=None, a2a_peers_lock=None,
              kernel=None, cost_ledger=None, cost_model=None,
              cost_accounting_enable=None, cost_budget_usd=None, tracer=None,
              backend=None, verb_to_openai_tool=None, recipe_to_openai_tool=None,
              skill_to_openai_tool=None, load_recipe_catalog=None, skill_list=None,
              skill_fetch=None, user_rbac_filter=None, match_user_cfg=None,
              toml_section=None, cap_skills=None, get_client=None, kg_lookup=None,
              execute_skill=None, run_dci_flow=None, offline_posture=None,
              prompt_registry=None, db_read=None, run_template_enable=None,
              mcp_client_tools=None, mcp_client_lock=None) -> None:
    """Inject server.py's runtime deps under their EXACT original names. Objects
    (catalog/peers/ledger/tracer/kernel) are passed BY REFERENCE so server-side
    mutation stays visible; the moved logic is byte-identical."""
    g = globals()
    if verb_catalog is not None:
        g["_VERB_CATALOG"] = verb_catalog
    if a2a_peers is not None:
        g["_A2A_PEERS"] = a2a_peers
    if a2a_peers_lock is not None:
        g["_A2A_PEERS_LOCK"] = a2a_peers_lock
    if kernel is not None:
        g["_KERNEL"] = kernel
    if cost_ledger is not None:
        g["_COST_LEDGER"] = cost_ledger
    if cost_model is not None:
        g["_COST_MODEL"] = cost_model
    if cost_accounting_enable is not None:
        g["COST_ACCOUNTING_ENABLE"] = cost_accounting_enable
    if cost_budget_usd is not None:
        g["COST_BUDGET_USD"] = cost_budget_usd
    if tracer is not None:
        g["_TRACER"] = tracer
    if backend is not None:
        g["BACKEND"] = backend
    if verb_to_openai_tool is not None:
        g["_verb_to_openai_tool"] = verb_to_openai_tool
    if recipe_to_openai_tool is not None:
        g["_recipe_to_openai_tool"] = recipe_to_openai_tool
    if skill_to_openai_tool is not None:
        g["_skill_to_openai_tool"] = skill_to_openai_tool
    if load_recipe_catalog is not None:
        g["_load_recipe_catalog"] = load_recipe_catalog
    if skill_list is not None:
        g["_skill_list"] = skill_list
    if skill_fetch is not None:
        g["_skill_fetch"] = skill_fetch
    if user_rbac_filter is not None:
        g["_user_rbac_filter"] = user_rbac_filter
    if match_user_cfg is not None:
        g["_match_user_cfg"] = match_user_cfg
    if toml_section is not None:
        g["_toml_section"] = toml_section
    if cap_skills is not None:
        g["_cap_skills"] = cap_skills
    if get_client is not None:
        g["_get_client"] = get_client
    if kg_lookup is not None:
        g["kg_lookup"] = kg_lookup
    if execute_skill is not None:
        g["execute_skill"] = execute_skill
    if run_dci_flow is not None:
        g["run_dci_flow"] = run_dci_flow
    if offline_posture is not None:
        g["_offline_posture"] = offline_posture
    if prompt_registry is not None:
        g["_PROMPT_REGISTRY"] = prompt_registry
    if db_read is not None:
        g["_db_read"] = db_read
    if run_template_enable is not None:
        g["RUN_TEMPLATE_ENABLE"] = run_template_enable
    if mcp_client_tools is not None:
        g["_MCP_CLIENT_TOOLS"] = mcp_client_tools
    if mcp_client_lock is not None:
        g["_MCP_CLIENT_LOCK"] = mcp_client_lock

async def list_verbs_logic(include_rare: bool = True) -> JSONResponse:
    tools = []
    for vname, vcfg in _VERB_CATALOG.items():
        if not include_rare and vcfg.get("tier") == "rare":
            continue
        props: dict = {}
        required: list[str] = []
        for argname, argcfg in (vcfg.get("params") or {}).items():
            if not isinstance(argcfg, dict):
                continue
            spec: dict = {
                "type": argcfg.get("type", "string"),
                "description": argcfg.get("desc", ""),
            }
            if argcfg.get("enum"):
                spec["enum"] = list(argcfg["enum"])
            if "default" in argcfg:
                spec["default"] = argcfg["default"]
            else:
                required.append(argname)
            props[argname] = spec
        tools.append({
            "name": vname,
            "description": vcfg.get("desc", ""),
            "inputSchema": {
                "type": "object",
                "properties": props,
                "required": required,
                "additionalProperties": False,
            },
            "annotations": {
                "section": vcfg.get("section", ""),
                "tier": vcfg.get("tier", "common"),
                "readOnlyHint": vcfg.get("permission") == "read",
                "permission": vcfg.get("permission", "read"),
            },
        })
    return JSONResponse({"tools": tools})

async def list_verbs_openai_tools_logic(include_rare: bool = True) -> JSONResponse:
    tools = [
        _verb_to_openai_tool(vname, vcfg)
        for vname, vcfg in _VERB_CATALOG.items()
        if include_rare or vcfg.get("tier") != "rare"
    ]
    if globals().get("_MCP_CLIENT_TOOLS"):
        try:
            from mios_skills import _mcp_tool_to_openai_tool
            async with globals().get("_MCP_CLIENT_LOCK"):
                _mcp_items = list(globals()["_MCP_CLIENT_TOOLS"].items())
            for _k, _info in _mcp_items:
                tools.append(_mcp_tool_to_openai_tool(_k, _info))
        except Exception:  # noqa: BLE001
            pass
    return JSONResponse({"tools": tools, "count": len(tools)})

async def list_tools_logic(include_rare: bool = True) -> JSONResponse:
    tools = [
        _verb_to_openai_tool(vname, vcfg)
        for vname, vcfg in _VERB_CATALOG.items()
        if include_rare or vcfg.get("tier") != "rare"
    ]
    recipe_n = 0
    try:
        for rname, rcfg in (_load_recipe_catalog() or {}).items():
            tools.append(_recipe_to_openai_tool(rname, rcfg))
            recipe_n += 1
    except Exception:  # noqa: BLE001 -- best-effort section; degrade open
        pass
    skill_n = 0
    try:
        for srow in (await _skill_list(status="promoted")) or []:
            tools.append(_skill_to_openai_tool(srow))
            skill_n += 1
    except Exception:  # noqa: BLE001 -- best-effort section; degrade open
        pass
    tools = _user_rbac_filter(tools)
    _rn = sum(1 for t in tools
              if str((t.get("function") or {}).get("name") or "").startswith("mios_recipe__"))
    _sn = sum(1 for t in tools
              if str((t.get("function") or {}).get("name") or "").startswith("mios_skill__"))
    return JSONResponse({
        "tools": tools,
        "count": len(tools),
        "counts": {
            "verbs": len(tools) - _rn - _sn,
            "recipes": _rn,
            "skills": _sn,
        },
    })

def _skill_to_mcp_resource(srow: dict) -> dict:
    name = str(srow.get("name") or "")
    return {
        "uri": f"mios://skill/{name}",
        "name": name,
        "description": (str(srow.get("description") or ""))[:300],
        "mimeType": "text/markdown",
        "annotations": {"miosKind": "skill", "status": srow.get("status")},
    }

def _recipe_to_mcp_resource(rname: str, rcfg: dict) -> dict:
    desc = rcfg.get("description") or rcfg.get("desc") or rcfg.get("summary") or ""
    return {
        "uri": f"mios://recipe/{rname}",
        "name": rname,
        "description": str(desc)[:300],
        "mimeType": "application/json",
        "annotations": {"miosKind": "recipe"},
    }

def _verb_to_mcp_resource(vname: str, vcfg: dict) -> dict:
    desc = vcfg.get("description") or vcfg.get("desc") or vcfg.get("summary") or ""
    return {
        "uri": f"mios://verb/{vname}",
        "name": vname,
        "description": str(desc)[:300],
        "mimeType": "application/json",
        "annotations": {"miosKind": "verb", "tier": vcfg.get("tier")},
    }

async def v1_capabilities_logic(request: Request) -> JSONResponse:
    try:
        try:
            _, _ucfg = _match_user_cfg()
        except Exception:  # noqa: BLE001
            _ucfg = {}
        ceiling = str((_ucfg or {}).get("max_permission") or "interactive")
        man = mios_capreg.build_capability_manifest(
            _VERB_CATALOG, _toml_section("recipes") or {}, ceiling=ceiling,
            skills=_cap_skills())
        return JSONResponse({"object": "mios.capability.manifest",
                             "ceiling": ceiling,
                             "summary": mios_capreg.manifest_summary(man),
                             "data": man})
    except Exception as e:  # noqa: BLE001 -- never 500 the surface
        return JSONResponse({"object": "mios.capability.manifest",
                             "error": str(e), "data": []})

async def v1_capabilities_dag_logic() -> JSONResponse:
    try:
        dag = mios_capreg.build_capability_dag(
            _VERB_CATALOG, _toml_section("recipes") or {}, _cap_skills())
        return JSONResponse({"object": "mios.capability.dag",
                             "counts": {"nodes": len(dag["nodes"]),
                                        "edges": len(dag["edges"]),
                                        "cycles": len(dag["cycles"]),
                                        "dangling": len(dag["dangling"])},
                             **dag})
    except Exception as e:  # noqa: BLE001 -- never 500 the surface
        return JSONResponse({"object": "mios.capability.dag",
                             "error": str(e), "nodes": [], "edges": []})

async def v1_peers_logic() -> JSONResponse:
    try:
        async with _A2A_PEERS_LOCK:
            peers = [{"id": pid,
                      "endpoint": str(p.get("url") or ""),
                      "heartbeat": int(p.get("heartbeat", 1) or 1)}
                     for pid, p in _A2A_PEERS.items()]
        return JSONResponse({"object": "mios.peer.digest", "peers": peers})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"object": "mios.peer.digest", "error": str(e),
                             "peers": []})

async def list_resources_logic() -> JSONResponse:
    resources: list = [
        _verb_to_mcp_resource(vname, vcfg)
        for vname, vcfg in _VERB_CATALOG.items()
    ]
    try:
        for rname, rcfg in (_load_recipe_catalog() or {}).items():
            resources.append(_recipe_to_mcp_resource(rname, rcfg))
    except Exception:  # noqa: BLE001 -- best-effort section; degrade open
        pass
    try:
        for srow in (await _skill_list(status="all", limit=1000)) or []:
            resources.append(_skill_to_mcp_resource(srow))
    except Exception:  # noqa: BLE001 -- best-effort section; degrade open
        pass
    return JSONResponse({"resources": resources, "count": len(resources)})

async def read_resource_logic(uri: str = "") -> JSONResponse:
    uri = (uri or "").strip()
    try:
        if uri.startswith("mios://skill/"):
            nm = uri[len("mios://skill/"):]
            rows = await _skill_list(status="all", limit=1000)
            row = next((s for s in (rows or [])
                        if str(s.get("name")) == nm), None)
            if row is None:
                return JSONResponse({"error": f"no such skill: {nm}"},
                                    status_code=404)
            text = str(row.get("body") or row.get("description") or "")
            mime = "text/markdown"
        elif uri.startswith("mios://recipe/"):
            nm = uri[len("mios://recipe/"):]
            rcfg = (_load_recipe_catalog() or {}).get(nm)
            if rcfg is None:
                return JSONResponse({"error": f"no such recipe: {nm}"},
                                    status_code=404)
            text = json.dumps(rcfg, ensure_ascii=False, indent=2)
            mime = "application/json"
        elif uri.startswith("mios://verb/"):
            nm = uri[len("mios://verb/"):]
            vcfg = _VERB_CATALOG.get(nm)
            if vcfg is None:
                return JSONResponse({"error": f"no such verb: {nm}"},
                                    status_code=404)
            text = json.dumps(vcfg, ensure_ascii=False, indent=2)
            mime = "application/json"
        else:
            return JSONResponse({"error": f"unknown resource uri: {uri}"},
                                status_code=404)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse({"contents": [
        {"uri": uri, "mimeType": mime, "text": text}]})

async def v1_route_logic(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    refined = (body.get("refined") if isinstance(body, dict) and "refined" in body
               else body)
    dec = _KERNEL.router.route(refined if isinstance(refined, dict) else {})
    return JSONResponse({"object": "mios.route_decision", **dec.to_dict()})

async def cost_ledger_logic() -> JSONResponse:
    return JSONResponse({
        "object": "mios.cost",
        "enabled": COST_ACCOUNTING_ENABLE,
        "budget_usd": COST_BUDGET_USD,
        "over_budget": _COST_LEDGER.over_budget(COST_BUDGET_USD),
        "model": {"gpu_watts": _COST_MODEL.gpu_watts,
                  "usd_per_kwh": _COST_MODEL.usd_per_kwh,
                  "remote_usd_per_mtok": _COST_MODEL.remote_usd_per_mtok},
        **_COST_LEDGER.snapshot(),
    })

async def trace_read_logic(trace_id: str) -> JSONResponse:
    spans = _TRACER.get_trace(str(trace_id))
    return JSONResponse({
        "object": "mios.trace",
        "trace_id": str(trace_id),
        "enabled": _TRACER.enabled,
        "span_count": len(spans),
        "spans": spans,
    })

async def trace_recent_logic() -> JSONResponse:
    return JSONResponse({
        "object": "mios.trace.list",
        **_TRACER.stats(),
        "recent": _TRACER.recent(50),
    })

async def offline_status_logic() -> JSONResponse:
    return JSONResponse({"object": "mios.offline_status", **_offline_posture(),
                         "ts": int(time.time())})

async def prompt_registry_view_logic() -> JSONResponse:
    snap = _PROMPT_REGISTRY.snapshot()
    return JSONResponse({"object": "mios.prompt_registry",
                         "count": len(snap), "prompts": snap})

async def run_templates_list_logic() -> JSONResponse:
    rows: list = []
    try:
        resp = await _db_read(
            "SELECT class, summary, node_count, ts FROM run_template "
            "ORDER BY ts DESC LIMIT 50;",
            pg_sql="SELECT class, summary, node_count, ts FROM run_template "
                   "ORDER BY ts DESC LIMIT 50")
        for st in (resp or []):
            if isinstance(st, dict) and isinstance(st.get("result"), list):
                rows = st["result"]
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"object": "mios.run_templates", "error": str(e),
                             "templates": []})
    return JSONResponse({"object": "mios.run_templates",
                         "enabled": RUN_TEMPLATE_ENABLE,
                         "count": len(rows), "templates": rows})

async def list_models_logic(request: Request) -> JSONResponse:
    created = int(time.time())
    _agent_id = str((_toml_section("ai") or {}).get("agent_model") or "MiOS AI")
    _ctx = int(os.environ.get("MIOS_AGENT_PIPE_CTX", "65536"))

    model_ids = ["gpt-6.1-sol"]
    if _agent_id not in model_ids:
        model_ids.append(_agent_id)

    models_data = []
    for mid in model_ids:
        models_data.append({
            "id": mid,
            "object": "model",
            "created": created,
            "owned_by": "mios",
            "max_model_len": _ctx,
            "context_length": _ctx,
            "max_context_length": _ctx,
            "context_window": _ctx,
            "max_context_window": _ctx,
            "slug": mid,
            "display_name": mid,
            "supported_in_api": True,
            "visibility": "list",
            "supported_reasoning_levels": ["low", "medium", "high"],
            "default_reasoning_level": "medium",
            "shell_type": "unified_exec",
            "input_modalities": ["text", "image"],
        })

    return JSONResponse(content={
        "object": "list",
        "data": models_data,
        "models": models_data,
    })

async def embeddings_logic(request: Request) -> JSONResponse:
    body = await request.body()
    client = await _get_client()
    headers = {k: v for k, v in request.headers.items()
               if k.lower() in ("authorization", "content-type")}
    try:
        r = await client.post(
            f"{BACKEND}/embeddings", content=body, headers=headers,
        )
        return JSONResponse(content=r.json(), status_code=r.status_code)
    except httpx.HTTPError as e:
        log.warning("embeddings proxy failed: %s", e)
        return JSONResponse(
            content={"error": {"message": str(e), "type": "backend_error"}},
            status_code=502,
        )

async def kg_lookup_endpoint_logic(phrase: str = "") -> JSONResponse:
    if not phrase:
        return JSONResponse(
            content={"error": "phrase query param required"},
            status_code=400,
        )
    result = await kg_lookup(phrase)
    if result is None:
        return JSONResponse(
            content={"match": None, "phrase": phrase},
            status_code=404,
        )
    return JSONResponse(content={"match": result, "phrase": phrase})

async def skills_list_logic(status: str = "promoted",
                            source: str = "",
                            limit: int = 200) -> JSONResponse:
    rows = await _skill_list(
        status=status or "all",
        source=source or None,
        limit=max(1, min(int(limit or 200), 1000)),
    )
    return JSONResponse(content={"skills": rows, "count": len(rows)})

async def skills_show_logic(name: str = "") -> JSONResponse:
    if not name:
        return JSONResponse(
            content={"error": "name query param required"},
            status_code=400)
    row = await _skill_fetch(name)
    if not row:
        return JSONResponse(content={"skill": None, "name": name},
                            status_code=404)
    return JSONResponse(content={"skill": row})

async def skills_run_logic(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse(
            content={"error": "invalid JSON body"}, status_code=400)
    name = str(body.get("name", "")).strip()
    if not name:
        return JSONResponse(
            content={"error": "name required"}, status_code=400)
    params = body.get("params") or {}
    if not isinstance(params, dict):
        return JSONResponse(
            content={"error": "params must be an object"},
            status_code=400)
    session_id = body.get("session_id")
    result = await execute_skill(
        name, params, session_id=session_id)
    status_code = 200 if result.get("success") else 422
    return JSONResponse(content=result, status_code=status_code)

async def skills_openai_tools_logic() -> JSONResponse:
    rows = await _skill_list(status="promoted")
    tools = [_skill_to_openai_tool(r) for r in rows]
    return JSONResponse(content={"tools": tools, "count": len(tools)})

async def dci_deliberate_logic(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse(
            content={"error": "invalid JSON body"},
            status_code=400,
        )
    user_text = str(body.get("user_text", "")).strip()
    envelope = body.get("envelope") or {}
    if not user_text:
        return JSONResponse(
            content={"error": "user_text required"}, status_code=400,
        )
    if not isinstance(envelope, dict):
        return JSONResponse(
            content={"error": "envelope must be an object"},
            status_code=400,
        )
    r_max = body.get("r_max")
    if r_max is not None:
        try:
            r_max = max(1, min(int(r_max), 5))
        except (TypeError, ValueError):
            r_max = None
    result = await run_dci_flow(
        user_text, envelope,
        session_id=body.get("session_id"),
        r_max=r_max,
    )
    return JSONResponse(content=result)

async def dci_schema_logic() -> JSONResponse:
    return JSONResponse(content={
        "acts": _DCI_ACTS,
        "act_names": _DCI_ACT_NAMES,
        "response_schema": _DCI_ACT_SCHEMA,
        "enabled": DCI_ENABLED,
    })

http_caps_router = APIRouter()

@http_caps_router.get("/v1/peers")
async def v1_peers() -> JSONResponse:
    """WS-A18 gossip anti-entropy digest: this node's known A2A peers
    {id, endpoint, heartbeat}. Other nodes PULL this each gossip round and merge
    it (trust-gated) into their own peer set, so the federation discovers peers
    epidemically without a central registry. Additive + read-only. Calls
    v1_peers_logic (same module)."""
    return await v1_peers_logic()

@http_caps_router.get("/v1/resources")
async def list_resources() -> JSONResponse:
    return await list_resources_logic()

@http_caps_router.get("/v1/resources/read")
async def read_resource(uri: str = "") -> JSONResponse:
    """Fetch ONE mios:// resource (skill body / recipe def / verb doc) in MCP
    resources/read shape: {contents:[{uri,mimeType,text}]}. Unknown scheme ->
    404. Degrade-open on backend error. Calls read_resource_logic (same module)."""
    return await read_resource_logic(uri)

@http_caps_router.get("/v1/capabilities")
async def v1_capabilities(request: Request) -> JSONResponse:
    return await v1_capabilities_logic(request)

@http_caps_router.get("/v1/capabilities/dag")
async def v1_capabilities_dag() -> JSONResponse:
    return await v1_capabilities_dag_logic()

@http_caps_router.post("/v1/route")
async def v1_route(request: Request) -> JSONResponse:
    """WS-A11/WS-3 Router introspection: classify a refined plan WITHOUT executing
    it. POST a bare refined dict or {"refined": {...}} -> the typed RouteDecision
    {mode, intent, tool, fanout, reason}. Lets an operator confirm the decomposed
    Router matches the inline chat_completions cascade before the Stage-2b
    execution swap. Pure + read-only."""
    return await v1_route_logic(request)

@http_caps_router.get("/v1/cost")
async def cost_ledger() -> JSONResponse:
    """WS-RES-GOV cost/energy accounting (CLASSic Cost axis): the running ledger
    of dispatch energy (Wh) + $ + tokens, broken down per lane, since process
    start. Observe-only; populated when [cost].enable is on. The power envelope is
    the real constraint on a local-GPU OS, so this surfaces it as a first-class
    signal (complements the token-rate budget tripwire)."""
    return await cost_ledger_logic()

@http_caps_router.get("/v1/trace/{trace_id}")
async def trace_read(trace_id: str) -> JSONResponse:
    """WS-A8: return the recorded spans for one trace (zero DB hit -- served
    from the in-memory ring buffer). 404-shaped empty object when unknown or
    already evicted past the buffer cap."""
    return await trace_read_logic(trace_id)

@http_caps_router.get("/v1/trace")
async def trace_recent() -> JSONResponse:
    """WS-A8: list the most-recent traces still in the buffer (newest first)."""
    return await trace_recent_logic()

@http_caps_router.get("/v1/offline-status")
async def offline_status() -> JSONResponse:
    """Live offline-computation posture: every inference/embedding/agent
    endpoint classified local-vs-external. `offline: true` proves no MiOS
    compute path egresses to a cloud host ('maintain offline computation for all
    MiOS systems'). Calls offline_status_logic (same module)."""
    return await offline_status_logic()

@http_caps_router.get("/v1/prompts")
async def prompt_registry_view() -> JSONResponse:
    """WS-LIFECYCLE-VER versioned hop-prompt registry: each live system prompt's
    version + content-hash + length + history depth (content-FREE -- never leaks
    the prompt text). The substrate for self-improve rollback + prompt-drift
    detection. Empty until the startup registration runs. Calls
    prompt_registry_view_logic (same module)."""
    return await prompt_registry_view_logic()

@http_caps_router.get("/v1/run-templates")
async def run_templates_list() -> JSONResponse:
    """WS-6 determinism foundation: recent captured DAG run-templates (the
    replayable plan shapes). Replay-reuse is a follow-up; this is capture +
    observability. Calls run_templates_list_logic (same module)."""
    return await run_templates_list_logic()

@http_caps_router.get("/v1/verbs")
async def list_verbs(include_rare: bool = True) -> JSONResponse:
    """Render [verbs.*] as JSON-Schema tool specs. Same SSOT that
    drives the planner catalog. Consumed by mios-mcp-server (for
    MCP `tools/list`) and any external tooling that wants the
    canonical verb shape."""
    return await list_verbs_logic(include_rare)

@http_caps_router.get("/v1/verbs/openai-tools")
async def list_verbs_openai_tools(include_rare: bool = True) -> JSONResponse:
    return await list_verbs_openai_tools_logic(include_rare)

@http_caps_router.get("/v1/tools")
async def list_tools(include_rare: bool = True) -> JSONResponse:
    return await list_tools_logic(include_rare)

@http_caps_router.get("/kg/lookup")
async def kg_lookup_endpoint(phrase: str = "") -> JSONResponse:
    return await kg_lookup_endpoint_logic(phrase)

@http_caps_router.get("/skills/list")
async def skills_list(status: str = "promoted",
                      source: str = "",
                      limit: int = 200) -> JSONResponse:
    return await skills_list_logic(status, source, limit)

@http_caps_router.get("/skills/show")
async def skills_show(name: str = "") -> JSONResponse:
    return await skills_show_logic(name)

@http_caps_router.post("/skills/run")
async def skills_run(request: Request) -> JSONResponse:
    return await skills_run_logic(request)

@http_caps_router.get("/skills/openai-tools")
async def skills_openai_tools() -> JSONResponse:
    """Dump the OpenAI tool-schema array for every promoted skill.
    Hermes + OpenCode fetch this and append it to their static tool
    surface so promoted skills become first-class callable tools
    on every external gateway -- no client-side edits per skill."""
    return await skills_openai_tools_logic()

@http_caps_router.post("/dci/deliberate")
async def dci_deliberate(request: Request) -> JSONResponse:
    return await dci_deliberate_logic(request)

@http_caps_router.get("/dci/schema")
async def dci_schema() -> JSONResponse:
    return await dci_schema_logic()

@http_caps_router.get("/v1/models")
async def list_models(request: Request) -> JSONResponse:
    return await list_models_logic(request)

@http_caps_router.post("/v1/embeddings")
async def embeddings(request: Request) -> JSONResponse:
    return await embeddings_logic(request)
