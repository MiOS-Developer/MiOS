# AI-hint: DAG EXECUTION entrypoints extracted VERBATIM from server.py (refactor R8 wave).
# AI-doc: usr/share/doc/mios/manual/routing.md

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from typing import Optional

import httpx

import mios_tokenize
from mios_config import _toml_section
from mios_agent_call import _call_agent_complete
from mios_web_research import _web_research_enrich
from mios_planner import _dag_levels
from mios_sse import _sse_reasoning, _node_status, _node_context
from mios_grounding import _env_grounding
from mios_hitlflow import _action_hash
from mios_jsonsalvage import loads_lenient as _loads_lenient
from mios_policy import _agent_rbac_filter, _user_rbac_filter

log = logging.getLogger("mios-agent-pipe")

DEEPEN_FETCH = False
DEEPEN_DEADLINE_S = 45.0
DEEPEN_MAX_ITERS = 12
DEEPEN_WEB_TIMEOUT_S = 20.0
DEEPEN_EARLY_EXIT = False
DEEPEN_JUDGE_TIMEOUT_S = 6.0
DAG_NODE_MAX_TOKENS = 800
DAG_NODE_SLOW_MAX_TOKENS = 400
DAG_NODE_RETRY = 1
DAG_NODE_DEADLINE_S = 75.0
DAG_NODE_DEADLINE_SLOW_S = 150.0
SLOW_LANES: set = set()
KV_FORK_ENABLE = False
WORKER_TOOLS_ENABLE = False
WORKER_TOOL_CTX = 16384
WORKER_TOOL_CTX_SLOW = 6144
PLANNER_REFLEXION_CAP = 2
SWARM_SATURATE = False
REQUEST_CANCEL_ENABLE = True
REQUEST_CANCEL_POLL_S = 1.0
TURN_DEADLINE_S = 600.0
_PG_PRIMARY = False
CTX_FIT = False
WORKER_TOOL_CTX_MAX = 24576
DEEPEN_LANES: set = set()
RUNAWAY_REAP_ENABLE = False
_LIGHT_LANE = ""

_AGENT_REGISTRY: dict = {}
_CHAT_CANCEL: dict = {}
_kv_fork_parent_var = None
_conv_key_var = None

dispatch_mios_verb = None
_call_agent_stream = None
reflect_on_step_failure = None
_judge_answer_satisfied = None
_sanitize_tool_text = None
_scratchpad_note = None
_scratchpad_render = None
_agent_contract = None
_role_system = None
_agent_lane = None
_worker_tools_surface_async = None
_lane_tool_cap = None
_a2a_send_message_to_peer = None
_a2a_extract_text = None
_get_client = None
_db_fire = None
_db_post = None
_db_create = None
_db_read = None
_pg_mirror = None

def configure(*, deepen_fetch=None, deepen_deadline_s=None, deepen_max_iters=None,
              deepen_web_timeout_s=None, deepen_early_exit=None,
              deepen_judge_timeout_s=None, judge_answer_satisfied=None,
              dag_node_max_tokens=None,
              dag_node_slow_max_tokens=None, dag_node_retry=None,
              dag_node_deadline_s=None, dag_node_deadline_slow_s=None,
              slow_lanes=None, kv_fork_enable=None, worker_tools_enable=None,
              worker_tool_ctx=None, worker_tool_ctx_slow=None,
              planner_reflexion_cap=None, swarm_saturate=None,
              request_cancel_enable=None, request_cancel_poll_s=None,
              turn_deadline_s=None, pg_primary=None,
              ctx_fit=None, worker_tool_ctx_max=None, deepen_lanes=None,
              runaway_reap_enable=None, light_lane=None,
              agent_registry=None, chat_cancel=None, kv_fork_parent_var=None,
              conv_key_var=None,
              dispatch_mios_verb=None, call_agent_stream=None,
              reflect_on_step_failure=None,
              sanitize_tool_text=None, scratchpad_note=None,
              scratchpad_render=None, agent_contract=None, role_system=None,
              agent_lane=None, worker_tools_surface_async=None, lane_tool_cap=None,
              a2a_send_message_to_peer=None,
              a2a_extract_text=None, get_client=None,
              db_fire=None, db_post=None, db_create=None,
              db_read=None,
              pg_mirror=None) -> None:
    """Inject server.py's config scalars, the live registry / ContextVars and
    the runtime helpers the DAG executors call back into."""
    global DEEPEN_FETCH, DEEPEN_DEADLINE_S, DEEPEN_MAX_ITERS, DEEPEN_WEB_TIMEOUT_S
    global DEEPEN_EARLY_EXIT, DEEPEN_JUDGE_TIMEOUT_S, _judge_answer_satisfied
    global DAG_NODE_MAX_TOKENS, DAG_NODE_SLOW_MAX_TOKENS, DAG_NODE_RETRY
    global DAG_NODE_DEADLINE_S, DAG_NODE_DEADLINE_SLOW_S, SLOW_LANES
    global KV_FORK_ENABLE, WORKER_TOOLS_ENABLE, WORKER_TOOL_CTX, WORKER_TOOL_CTX_SLOW
    global PLANNER_REFLEXION_CAP, SWARM_SATURATE
    global REQUEST_CANCEL_ENABLE, REQUEST_CANCEL_POLL_S, TURN_DEADLINE_S, _PG_PRIMARY
    global CTX_FIT, WORKER_TOOL_CTX_MAX, DEEPEN_LANES, RUNAWAY_REAP_ENABLE, _LIGHT_LANE
    global _AGENT_REGISTRY, _CHAT_CANCEL, _kv_fork_parent_var, _conv_key_var
    global _call_agent_stream
    global _sanitize_tool_text, _scratchpad_note, _scratchpad_render
    global _agent_contract, _role_system, _agent_lane
    global _worker_tools_surface_async, _lane_tool_cap
    global _a2a_send_message_to_peer, _a2a_extract_text
    global _get_client, _db_fire, _db_post, _db_create, _pg_mirror, _db_read
    if db_read is not None:
        _db_read = db_read
    if deepen_fetch is not None:
        DEEPEN_FETCH = deepen_fetch
    if deepen_deadline_s is not None:
        DEEPEN_DEADLINE_S = deepen_deadline_s
    if deepen_max_iters is not None:
        DEEPEN_MAX_ITERS = deepen_max_iters
    if deepen_web_timeout_s is not None:
        DEEPEN_WEB_TIMEOUT_S = deepen_web_timeout_s
    if deepen_early_exit is not None:
        DEEPEN_EARLY_EXIT = deepen_early_exit
    if deepen_judge_timeout_s is not None:
        DEEPEN_JUDGE_TIMEOUT_S = deepen_judge_timeout_s
    if judge_answer_satisfied is not None:
        _judge_answer_satisfied = judge_answer_satisfied
    if dag_node_max_tokens is not None:
        DAG_NODE_MAX_TOKENS = dag_node_max_tokens
    if dag_node_slow_max_tokens is not None:
        DAG_NODE_SLOW_MAX_TOKENS = dag_node_slow_max_tokens
    if dag_node_retry is not None:
        DAG_NODE_RETRY = dag_node_retry
    if dag_node_deadline_s is not None:
        DAG_NODE_DEADLINE_S = dag_node_deadline_s
    if dag_node_deadline_slow_s is not None:
        DAG_NODE_DEADLINE_SLOW_S = dag_node_deadline_slow_s
    if slow_lanes is not None:
        SLOW_LANES = slow_lanes
    if kv_fork_enable is not None:
        KV_FORK_ENABLE = kv_fork_enable
    if worker_tools_enable is not None:
        WORKER_TOOLS_ENABLE = worker_tools_enable
    if worker_tool_ctx is not None:
        WORKER_TOOL_CTX = worker_tool_ctx
    if worker_tool_ctx_slow is not None:
        WORKER_TOOL_CTX_SLOW = worker_tool_ctx_slow
    if planner_reflexion_cap is not None:
        PLANNER_REFLEXION_CAP = planner_reflexion_cap
    if swarm_saturate is not None:
        SWARM_SATURATE = swarm_saturate
    if request_cancel_enable is not None:
        REQUEST_CANCEL_ENABLE = request_cancel_enable
    if request_cancel_poll_s is not None:
        REQUEST_CANCEL_POLL_S = request_cancel_poll_s
    if turn_deadline_s is not None:
        TURN_DEADLINE_S = turn_deadline_s
    if pg_primary is not None:
        _PG_PRIMARY = pg_primary
    if ctx_fit is not None:
        CTX_FIT = ctx_fit
    if worker_tool_ctx_max is not None:
        WORKER_TOOL_CTX_MAX = worker_tool_ctx_max
    if deepen_lanes is not None:
        DEEPEN_LANES = deepen_lanes
    if runaway_reap_enable is not None:
        RUNAWAY_REAP_ENABLE = runaway_reap_enable
    if light_lane is not None:
        _LIGHT_LANE = light_lane
    if agent_registry is not None:
        _AGENT_REGISTRY = agent_registry
    if chat_cancel is not None:
        _CHAT_CANCEL = chat_cancel
    if kv_fork_parent_var is not None:
        _kv_fork_parent_var = kv_fork_parent_var
    if conv_key_var is not None:
        _conv_key_var = conv_key_var
    if dispatch_mios_verb is not None:
        globals()["dispatch_mios_verb"] = dispatch_mios_verb
    if call_agent_stream is not None:
        _call_agent_stream = call_agent_stream
    if reflect_on_step_failure is not None:
        globals()["reflect_on_step_failure"] = reflect_on_step_failure
    if sanitize_tool_text is not None:
        _sanitize_tool_text = sanitize_tool_text
    if scratchpad_note is not None:
        _scratchpad_note = scratchpad_note
    if scratchpad_render is not None:
        _scratchpad_render = scratchpad_render
    if agent_contract is not None:
        _agent_contract = agent_contract
    if role_system is not None:
        _role_system = role_system
    if agent_lane is not None:
        _agent_lane = agent_lane
    if worker_tools_surface_async is not None:
        _worker_tools_surface_async = worker_tools_surface_async
    if lane_tool_cap is not None:
        _lane_tool_cap = lane_tool_cap
    if a2a_send_message_to_peer is not None:
        _a2a_send_message_to_peer = a2a_send_message_to_peer
    if a2a_extract_text is not None:
        _a2a_extract_text = a2a_extract_text
    if get_client is not None:
        _get_client = get_client
    if db_fire is not None:
        _db_fire = db_fire
    if db_post is not None:
        _db_post = db_post
    if db_create is not None:
        _db_create = db_create
    if pg_mirror is not None:
        _pg_mirror = pg_mirror
    _configure_run_template(
        run_template_enable=RUN_TEMPLATE_ENABLE, pg_primary=_PG_PRIMARY,
        db_read=_db_read, db_create=_db_create, db_post=_db_post,
        db_fire=_db_fire, pg_mirror=_pg_mirror)

async def _deepen_until_barrier(node: dict, res: dict, barrier: "asyncio.Event",
                                session_id: Optional[str], client) -> dict:
    aname = str(node.get("agent") or "")
    acfg = _AGENT_REGISTRY.get(aname) or {}
    base_q = str(node.get("_base_query") or node.get("title")
                 or node.get("prompt") or "")[:200]
    grounding = str(node.get("_grounding") or "")
    _refined = node.get("_refined") if isinstance(node.get("_refined"), dict) else None
    _fetch = bool(DEEPEN_FETCH and _refined and base_q)
    out = (res.get("output") or "").strip()
    iters = 0
    fetched = 0
    _deadline = time.monotonic() + DEEPEN_DEADLINE_S
    while (iters < DEEPEN_MAX_ITERS and time.monotonic() < _deadline
           and not barrier.is_set()):
        if (DEEPEN_EARLY_EXIT and _judge_answer_satisfied is not None
                and base_q and out.strip()):
            _jbudget = _deadline - time.monotonic()
            if _jbudget > 0:
                try:
                    if await asyncio.wait_for(
                            _judge_answer_satisfied(base_q, out),
                            timeout=max(1.0, min(_jbudget, DEEPEN_JUDGE_TIMEOUT_S))):
                        log.info("deepen: %s satisfied -> early exit after %d pass(es)",
                                 aname, iters)
                        break
                except Exception:  # noqa: BLE001 -- judge hiccup -> deadline-bound loop
                    pass
        iters += 1
        _budget = _deadline - time.monotonic()
        if _budget <= 0:
            break
        if _fetch and not barrier.is_set():
            try:
                _new = await asyncio.wait_for(
                    _web_research_enrich(base_q, _refined, quick=True),
                    timeout=max(1.0, min(_budget, DEEPEN_WEB_TIMEOUT_S)))
            except Exception:  # noqa: BLE001
                _new = ""
            if _new and _new[:160] not in grounding:
                grounding = (grounding + "\n\n" + _new).strip()[:24000]
                fetched += 1
            _budget = _deadline - time.monotonic()
            if _budget <= 0 or barrier.is_set():
                break
        _msgs = [{"role": "user", "content":
                  "Task: " + base_q + "\n\nExpand COVERAGE: provide ADDITIONAL, "
                  "DISTINCT, specific points -- new angles / items / facets -- "
                  "that are NOT already listed below. Ground them in the research; "
                  "be concrete; do NOT repeat anything already covered and do NOT "
                  "punt. If there is genuinely nothing new to add, reply with a "
                  "single blank line.\n\nAlready covered:\n" + (out or "(none)")[:5000]
                  + (("\n\nResearch:\n" + grounding[:5000]) if grounding else "")}]
        body = {"model": acfg.get("model") or aname, "messages": _msgs,
                "max_tokens": DAG_NODE_MAX_TOKENS}
        try:
            _, ans = await asyncio.wait_for(
                _call_agent_complete(
                    aname, acfg, body, {"Content-Type": "application/json"},
                    client, prefer_cpu=False),
                timeout=max(1.0, _budget))
            ans = (ans or "").strip()
            if ans and ans[:120].lower() not in out.lower():
                out = (out + "\n\n" + ans).strip()
        except Exception:  # noqa: BLE001  (incl. asyncio.TimeoutError)
            pass
    if iters:
        log.info("deepen: %s did %d coverage pass(es) (+%d detail-fill fetch) "
                 "until barrier", aname, iters, fetched)
        res = dict(res)
        res["output"] = out
        res["deepened"] = iters
        res["fetched"] = fetched
        res["success"] = bool(out)
        node["_grounding"] = grounding   # ENRICHED grounding -> final synthesis
    return res

async def _execute_dag_node(node: dict, results_by_id: dict,
                            seen_actions: dict, dag_summary: str,
                            session_id: Optional[str], client,
                            frag_q: "Optional[asyncio.Queue]" = None) -> dict:
    nid = str(node.get("id", "?"))
    tool = str(node.get("tool", "")).strip()
    aname = str(node.get("agent", ""))
    agent_label = f"agent:{aname}" if aname else f"tool:{tool}"
    task_desc = str(node.get("prompt") or tool)

    if session_id and _db_create and _db_fire and _db_post:
        try:
            sql = _db_create("progress_ledger", {
                "session_id": session_id,
                "agent": agent_label,
                "task": task_desc,
                "state": "assigned"
            }, now_fields=("assigned_at",))
            _db_fire(_db_post(sql))
        except Exception as _pl_err:
            log.warning("Failed to log progress_ledger assignment: %s", _pl_err)

    is_research = bool(node.get("web") or node.get("news"))
    is_action = bool(node.get("local_state") or not is_research)

    if is_action and session_id and _db_read:
        try:
            fact_sql = f"SELECT claim, source FROM fact_ledger WHERE session_id = '{session_id}'"
            fact_rows = await _db_read(fact_sql, pg_sql=fact_sql)
            if fact_rows:
                fact_lines = []
                for row in fact_rows:
                    claim = row.get("claim")
                    source = row.get("source") or "unknown"
                    fact_lines.append(f"- Claim: {claim} (Source: {source})")
                fact_context = "\n[Grounded Facts from Research]:\n" + "\n".join(fact_lines) + "\n"

                if node.get("prompt"):
                    node["prompt"] = str(node.get("prompt")) + "\n" + fact_context
                if node.get("args"):
                    args = node.get("args")
                    for key in ("prompt", "text", "query"):
                        if key in args and isinstance(args[key], str):
                            args[key] = args[key] + "\n" + fact_context
        except Exception as _fl_err:
            log.warning("Failed to read fact_ledger: %s", _fl_err)

    res = await _execute_dag_node_core(node, results_by_id, seen_actions,
                                       dag_summary, session_id, client, frag_q)

    success = bool(res.get("success"))
    state_val = "completed" if success else "stalled"
    if session_id and _db_create and _db_fire and _db_post:
        try:
            sql = _db_create("progress_ledger", {
                "session_id": session_id,
                "agent": agent_label,
                "task": task_desc,
                "state": state_val
            }, now_fields=("completed_at",))
            _db_fire(_db_post(sql))
        except Exception as _pl_err:
            log.warning("Failed to log progress_ledger completion: %s", _pl_err)

    if is_research and success and session_id and _db_create and _db_fire and _db_post:
        output_txt = str(res.get("output") or "")
        claims = parse_research_claims(output_txt)
        for c in claims:
            try:
                sql = _db_create("fact_ledger", {
                    "session_id": session_id,
                    "claim": c["claim"],
                    "source": c["source"] or "web_search"
                }, now_fields=("ts",))
                _db_fire(_db_post(sql))
            except Exception as _fact_err:
                log.warning("Failed to log fact_ledger: %s", _fact_err)

    return res

def parse_research_claims(output_str: str) -> list[dict]:
    output_str = (output_str or "").strip()
    if not output_str:
        return []

    import json
    try:
        start_idx = min(output_str.find('['), output_str.find('{'))
        end_idx = max(output_str.rfind(']'), output_str.rfind('}'))
        if start_idx != -1 and end_idx != -1 and start_idx < end_idx:
            json_candidate = output_str[start_idx:end_idx+1]
            data = json.loads(json_candidate)
            claims = []
            if isinstance(data, dict):
                if "claim" in data:
                    claims.append({"claim": data.get("claim"), "source": data.get("source")})
                elif "claims" in data and isinstance(data["claims"], list):
                    for c in data["claims"]:
                        if isinstance(c, dict) and "claim" in c:
                            claims.append({"claim": c.get("claim"), "source": c.get("source")})
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "claim" in item:
                        claims.append({"claim": item.get("claim"), "source": item.get("source")})
            if claims:
                return [c for c in claims if c.get("claim")]
    except Exception:
        pass

    claims = []
    lines = output_str.split("\n")
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if "claim" in line.lower() or "fact" in line.lower() or "source" in line.lower():
            parts = line.split("Source:")
            if len(parts) == 2:
                c = parts[0].replace("Claim:", "").replace("claim:", "").strip()
                s = parts[1].strip()
                claims.append({"claim": c, "source": s})
                continue
        if "[" in line and "]" in line:
            cites = re.findall(r"\[([^\]]+)\]", line)
            if cites:
                clean_claim = re.sub(r"\[[^\]]+\]", "", line).strip()
                claims.append({"claim": clean_claim, "source": ", ".join(cites)})

    if not claims:
        for line in lines[:5]:
            if len(line) > 20 and re.search(r"\b\d{3,}\b|http", line):
                claims.append({"claim": line, "source": "web_search"})

    return [c for c in claims if c.get("claim")]

async def _execute_dag_node_core(node: dict, results_by_id: dict,
                                 seen_actions: dict, dag_summary: str,
                                 session_id: Optional[str], client,
                                 frag_q: "Optional[asyncio.Queue]" = None) -> dict:
    nid = str(node.get("id", "?"))
    if node.get("agent"):
        aname = str(node.get("agent"))
        acfg = _AGENT_REGISTRY.get(aname) or {}
        prompt = _substitute_ek_refs(
            {"_p": str(node.get("prompt") or "")}, results_by_id).get("_p", "")
        _act = _action_hash(f"agent:{aname}", {"prompt": prompt})
        _prior = seen_actions.get(_act)
        if _prior is not None:
            d = dict(_prior)
            d["node_id"] = nid
            d["repeat_of"] = _prior.get("node_id")
            return d
        _peer = (node.get("a2a_peer_id")
                 or (acfg.get("a2a_peer_id") if isinstance(acfg, dict) else None))
        if _peer:
            _t0 = time.time()
            _env = await _a2a_send_message_to_peer(
                str(_peer), prompt, context_id=session_id)
            _txt = _a2a_extract_text(_env)
            return {
                "success": bool(_txt),
                "output": _txt,
                "latency_ms": int((time.time() - _t0) * 1000),
                "tool": f"agent:{aname}",
                "args": {},
                "node_id": nid,
                "retries": 0,
                "_act": _act,
            }
        t0 = time.time()
        _node_msgs: list = []
        _contract = _agent_contract()
        if _contract:
            _node_msgs.append({"role": "system",
                               "content": _contract + "\n\n" + _env_grounding()})
        _overlay = _role_system(aname)   # thin per-role DEVELOPER overlay (OpenAI pattern)
        if _overlay:
            _node_msgs.append({"role": "system", "content": _overlay})
        _sp_block = _scratchpad_render()
        if _sp_block:
            _node_msgs.append({"role": "system", "content": _sp_block})
        _node_msgs.append({"role": "user", "content": prompt})
        _lane = _agent_lane(acfg)
        _maxtok = (DAG_NODE_SLOW_MAX_TOKENS if _lane in SLOW_LANES
                   else DAG_NODE_MAX_TOKENS)
        body = {"model": acfg.get("model") or aname,
                "messages": _node_msgs,
                "max_tokens": _maxtok}
        if KV_FORK_ENABLE:
            _kv_fork_parent_var.set(_conv_key_var.get() or "")
        _slow_node = _lane in SLOW_LANES
        _grounded_node = bool(node.get("_grounding"))
        _reason_only = node.get("_no_tools") or (_slow_node and _grounded_node)
        if WORKER_TOOLS_ENABLE and not _reason_only:
            _wtools = await _worker_tools_surface_async(cap=_lane_tool_cap(_lane), intent=prompt)
            _wtools = _agent_rbac_filter(aname, _wtools)  # WS-2 per-agent RBAC
            _wtools = _user_rbac_filter(_wtools)          # #60 WS-6 per-user authz
            if _wtools:
                body["tools"] = _wtools
                body["num_ctx"] = _fit_context(
                    body.get("messages") or [], _wtools, _lane,
                    WORKER_TOOL_CTX_SLOW if _slow_node else WORKER_TOOL_CTX)
                body["_allow_write"] = True
        elif _slow_node:
            body["num_ctx"] = WORKER_TOOL_CTX_SLOW
        if str(node.get("format") or "").lower() == "json":
            body["response_format"] = {"type": "json_object"}
        hdrs = {"Content-Type": "application/json"}

        async def _run_node(prefer_cpu: bool) -> tuple:
            if frag_q is not None:
                return await _call_agent_stream(
                    aname, acfg, body, hdrs, client, frag_q,
                    prefer_cpu=prefer_cpu)
            return await _call_agent_complete(
                aname, acfg, body, hdrs, client, prefer_cpu=prefer_cpu)

        async def _dispatch_with_retries() -> tuple:
            _, _txt = await _run_node(prefer_cpu=False)
            _txt = (_txt or "").strip()
            if not _txt and acfg.get("cpu_endpoint") and acfg.get("cpu_model"):
                _, _txt = await _run_node(prefer_cpu=True)
                _txt = (_txt or "").strip()
            _n = 0
            while not _txt and _n < DAG_NODE_RETRY:
                _n += 1
                await asyncio.sleep(0.4)
                _, _txt = await _run_node(prefer_cpu=False)
                _txt = (_txt or "").strip()
            return _txt, _n
        _node_deadline = DAG_NODE_DEADLINE_SLOW_S if _slow_node else DAG_NODE_DEADLINE_S
        try:
            text, _ntry = await asyncio.wait_for(_dispatch_with_retries(),
                                                 timeout=_node_deadline)
        except asyncio.TimeoutError:
            text, _ntry = "", 0
            log.warning("DAG node %s (agent:%s lane=%s) exceeded %.0fs -> abandoned",
                        nid, aname, _lane, _node_deadline)
        return {
            "success": bool(text),
            "output": text,
            "latency_ms": int((time.time() - t0) * 1000),
            "tool": f"agent:{aname}",
            "args": {},
            "node_id": nid,
            "retries": _ntry,
            "_act": _act,
        }
    tool = str(node.get("tool", "")).strip()
    args = _substitute_ek_refs(node.get("args") or {}, results_by_id)
    _act = _action_hash(tool, args)
    _prior = seen_actions.get(_act)
    if _prior is not None:
        d = dict(_prior)
        d["node_id"] = nid
        d["repeat_of"] = _prior.get("node_id")
        d["_act"] = _act
        return d
    attempt = 0
    last_result = await dispatch_mios_verb(tool, args, session_id=session_id)
    if not last_result.get("success"):
        correction = await reflect_on_step_failure(
            {"id": nid, "tool": tool, "args": args}, last_result,
            {"summary": dag_summary}, session_id=session_id)
        if correction and correction.get("tool"):
            tool = str(correction.get("tool", tool))
            args = _substitute_ek_refs(
                correction.get("args") or {}, results_by_id)
            last_result = await dispatch_mios_verb(
                tool, args, session_id=session_id)
    while not last_result.get("success") and attempt < PLANNER_REFLEXION_CAP:
        attempt += 1
        await asyncio.sleep(0.5)
        last_result = await dispatch_mios_verb(tool, args, session_id=session_id)
    res = dict(last_result)
    res["node_id"] = nid
    res["tool"] = tool
    res["args"] = args if isinstance(args, dict) else {}
    res["attempts"] = attempt
    res["_act"] = _act
    return res

def _record_dag_node_row(res: dict, session_id: Optional[str]) -> None:
    """Persist a DAG node's dispatch as a session-linked tool_call row so
    the confirmation engine + critics see the propagation/taint chain.
    Logs an action_repeat_dedup event when the node reused a prior result."""
    if res.get("repeat_of"):
        _db_fire(_db_post(_db_create("event", {
            "source": "mios-agent-pipe",
            "kind": "action_repeat_dedup",
            "severity": "info",
            "summary": f"node {res.get('node_id')} == {res.get('repeat_of')} "
                       f"({res.get('tool')})",
            "payload": {"tool": res.get("tool"), "node_id": res.get("node_id"),
                        "repeat_of": res.get("repeat_of")},
        }, now_fields=("ts",))))
        return
    _row = {
        "tool": res.get("tool", ""),
        "args": res.get("args") if isinstance(res.get("args"), dict) else {},
        "result_preview": _sanitize_tool_text(res.get("output") or "")[:500],
        "success": bool(res.get("success")),
        "latency_ms": int(res.get("latency_ms", 0)),
        "tainted": bool(res.get("tainted")),
        "taint_reason": (res.get("taint_reason") or "") or None,
    }
    sql = _db_create("tool_call", _row, now_fields=("ts",))
    if session_id:
        sql = sql.rstrip().rstrip(";") + f", session = {session_id};"
    _db_fire(_db_post(sql))

async def _execute_dag_saturated(dag: dict, *, session_id: Optional[str],
                                 event_q: "Optional[asyncio.Queue]" = None,
                                 deepen_barrier: bool = False) -> dict:
    nodes = [n for n in (dag.get("nodes") or [])
             if isinstance(n, dict) and "id" in n]
    summary = dag.get("summary", "")
    by_id = {str(n["id"]): n for n in nodes}
    deps = {nid: {str(d) for d in (n.get("deps") or []) if str(d) in by_id}
            for nid, n in by_id.items()}
    results: list[dict] = []
    results_by_id: dict[str, dict] = {}
    seen_actions: dict[str, dict] = {}
    succeeded: set = set()
    failed: set = set()          # failed OR skipped -> poisons dependents
    client = await _get_client()
    _barrier = asyncio.Event()
    _primary_done = {"n": 0}
    _primary_expected = {"n": len(nodes)}
    _do_deepen = bool(deepen_barrier) and \
        sum(1 for n in nodes if n.get("agent")) > 1

    def _check_barrier() -> None:
        if _primary_done["n"] >= _primary_expected["n"]:
            _barrier.set()

    def _node_tool(node: dict) -> str:
        return str(node.get("tool") or
                   (f"agent:{node.get('agent')}" if node.get("agent") else ""))

    def _record(node: dict, res: dict) -> None:
        results.append(res)
        _record_dag_node_row(res, session_id)
        _scratchpad_note(res.get("tool") or f"agent:{node.get('agent') or '?'}",
                         str(res.get("output") or ""), phase="dag")
        if event_q is not None:
            event_q.put_nowait(("done", node, res))

    async def _run_node(node: dict):
        _r = await _execute_dag_node(node, results_by_id, seen_actions, summary,
                                     session_id, client, frag_q=event_q)
        _primary_done["n"] += 1            # atomic: no await between read+set
        _check_barrier()
        if _do_deepen and _node_deepens(node) and not _barrier.is_set():
            _r = await _deepen_until_barrier(node, _r, _barrier,
                                             session_id, client)
        return node, _r

    pending: set = set(by_id.keys())
    running: dict = {}   # asyncio.Task -> node_id

    def _cascade_skips() -> None:
        changed = True
        while changed:
            changed = False
            for nid in list(pending):
                if deps[nid] & failed:
                    pending.discard(nid)
                    failed.add(nid)
                    _primary_expected["n"] -= 1
                    node = by_id[nid]
                    _record(node, {"success": False, "node_id": nid,
                                   "tool": _node_tool(node), "args": {},
                                   "output": f"node {nid} skipped: dependency failed"})
                    changed = True
        _check_barrier()

    _cascade_skips()
    while pending or running:
        for nid in [x for x in pending if deps[x] <= succeeded]:
            pending.discard(nid)
            node = by_id[nid]
            if event_q is not None:
                event_q.put_nowait(("engage", node, None))
            running[asyncio.create_task(_run_node(node))] = nid
        if not running:
            if pending:
                nid = next(iter(pending))
                pending.discard(nid)
                node = by_id[nid]
                if event_q is not None:
                    event_q.put_nowait(("engage", node, None))
                running[asyncio.create_task(_run_node(node))] = nid
            else:
                break
        try:
            completed, _ = await asyncio.wait(
                set(running.keys()), return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            for _t in list(running.keys()):
                if not _t.done():
                    _t.cancel()
            raise
        for t in completed:
            nid = running.pop(t)
            node = by_id[nid]
            try:
                node, res = t.result()
            except BaseException as e:  # noqa: BLE001
                res = {"success": False, "node_id": nid, "tool": _node_tool(node),
                       "args": {}, "output": f"node {nid} raised: {e}"}
            _record(node, res)
            if res.get("success"):
                succeeded.add(nid)
                results_by_id[nid] = res
                if res.get("_act"):
                    seen_actions[res["_act"]] = res
            else:
                failed.add(nid)
        _cascade_skips()
    if event_q is not None:
        event_q.put_nowait(None)  # sentinel: DAG complete, drainer can stop
    return {
        "success": not failed,
        "summary": summary,
        "nodes_total": len(nodes),
        "nodes_executed": len(results),
        "node_results": results,
    }

RUN_TEMPLATE_ENABLE = str(os.environ.get("MIOS_RUN_TEMPLATE")
                          or _toml_section("run_template").get("enable", "true")
                          ).strip().lower() in {"1", "true", "yes"}

from mios_pipe.routing.run_template import (   # task_205: capture+replay source
    _run_template_class, _capture_run_template, load_run_templates,
    configure as _configure_run_template)

async def execute_dag(dag: dict, *, session_id: Optional[str],
                      event_q: "Optional[asyncio.Queue]" = None,
                      deepen_barrier: bool = False) -> dict:
    _capture_run_template(dag, session_id)   # WS-6: additive, fire-and-forget
    try:
        from mios_pipe.routing.dag_validate import validate_dag
        verdict = validate_dag(dag)
        if not verdict.is_valid:
            log.warning("DAG validation failed (status=%s); remediating to linear order", verdict.status)
            dag = dict(dag)
            dag["nodes"] = verdict.remediation_order
    except Exception as _val_err:
        log.warning("DAG pre-execution validation error: %s", _val_err)

    if SWARM_SATURATE:
        return await _execute_dag_saturated(
            dag, session_id=session_id, event_q=event_q,
            deepen_barrier=deepen_barrier)
    levels = _dag_levels(dag.get("nodes") or [])
    summary = dag.get("summary", "")
    results: list[dict] = []
    results_by_id: dict[str, dict] = {}
    seen_actions: dict[str, dict] = {}
    all_ok = True
    client = await _get_client()
    for level_idx, level in enumerate(levels, start=1):
        superstep_id = f"superstep_{level_idx}"
        checkpoint_key = f"{session_id}:{superstep_id}"
        loaded_from_checkpoint = False
        if session_id and _db_read:
            try:
                ckpt_sql = f"SELECT meta FROM session WHERE id = '{checkpoint_key}' AND kind = 'checkpoint'"
                ckpt_rows = await _db_read(ckpt_sql, pg_sql=ckpt_sql)
                if ckpt_rows and ckpt_rows[0].get("meta"):
                    meta = ckpt_rows[0]["meta"]
                    if isinstance(meta, str):
                        meta = json.loads(meta)
                    log.info("Resuming DAG level %d from checkpoint %s", level_idx, checkpoint_key)
                    level_res = meta.get("level_res") or []
                    for res in level_res:
                        results.append(res)
                        nid = res.get("node_id")
                        if nid:
                            results_by_id[nid] = res
                        _act = res.get("_act")
                        if _act:
                            seen_actions[_act] = res
                        _scratchpad_note(
                            res.get("tool") or "agent",
                            str(res.get("output") or ""), phase="dag")
                    loaded_from_checkpoint = True
            except Exception as _ckpt_err:
                log.warning("Failed to load checkpoint %s: %s", checkpoint_key, _ckpt_err)

        if loaded_from_checkpoint:
            continue

        if event_q is not None:
            for n in level:
                event_q.put_nowait(("engage", n, None))
        _agent_level = [n for n in level if n.get("agent")]
        if deepen_barrier and len(_agent_level) > 1:
            _barrier = asyncio.Event()
            _bstate = {"done": 0}
            _btotal = len(level)

            async def _node_then_deepen(n: dict) -> dict:
                _r = await _execute_dag_node(n, results_by_id, seen_actions,
                                             summary, session_id, client,
                                             frag_q=event_q)
                _bstate["done"] += 1            # atomic: no await since the read
                if _bstate["done"] >= _btotal:  # last primary -> release barrier
                    _barrier.set()
                if _node_deepens(n) and not _barrier.is_set():
                    _r = await _deepen_until_barrier(
                        n, _r, _barrier, session_id, client)
                return _r

            level_res = await asyncio.gather(
                *[_node_then_deepen(n) for n in level], return_exceptions=True)
        else:
            level_res = await asyncio.gather(*[
                _execute_dag_node(n, results_by_id, seen_actions, summary,
                                  session_id, client, frag_q=event_q)
                for n in level
            ], return_exceptions=True)
        for node, res in zip(level, level_res):
            nid = str(node.get("id", "?"))
            if isinstance(res, BaseException):
                res = {"success": False, "node_id": nid,
                       "tool": str(node.get("tool") or
                                   (f"agent:{node.get('agent')}"
                                    if node.get("agent") else "")),
                       "args": {}, "output": f"node {nid} raised: {res}"}
            results.append(res)
            _record_dag_node_row(res, session_id)
            _scratchpad_note(
                res.get("tool") or f"agent:{node.get('agent') or '?'}",
                str(res.get("output") or ""), phase="dag")
            if event_q is not None:
                event_q.put_nowait(("done", node, res))
            if res.get("success"):
                results_by_id[nid] = res
                if res.get("_act"):
                    seen_actions[res["_act"]] = res
            else:
                all_ok = False

        if session_id and _db_create and _db_fire and _db_post:
            try:
                ckpt_meta = {
                    "level_res": level_res,
                }
                del_sql = f"DELETE FROM session WHERE id = '{checkpoint_key}'"
                _db_fire(_db_post(del_sql))
                ins_sql = _db_create("session", {
                    "id": checkpoint_key,
                    "kind": "checkpoint",
                    "owui_chat_id": session_id,
                    "meta": ckpt_meta
                }, now_fields=("ts",))
                _db_fire(_db_post(ins_sql))
                log.info("Saved superstep checkpoint %s to database", checkpoint_key)
            except Exception as _ckpt_save_err:
                log.warning("Failed to save checkpoint %s: %s", checkpoint_key, _ckpt_save_err)

        if not all_ok:
            break
    if event_q is not None:
        event_q.put_nowait(None)  # sentinel: DAG complete, drainer can stop
    return {
        "success": all_ok,
        "summary": summary,
        "nodes_total": len(dag.get("nodes") or []),
        "nodes_executed": len(results),
        "node_results": results,
    }

async def _execute_dag_bounded(dag: dict, *, session_id: Optional[str],
                               deepen_barrier: bool = False,
                               request=None) -> dict:
    _task = asyncio.create_task(
        execute_dag(dag, session_id=session_id, deepen_barrier=deepen_barrier))
    _disconnected = False
    _watch = None
    if request is not None and REQUEST_CANCEL_ENABLE:
        async def _watch_disconnect():
            nonlocal _disconnected
            while not _task.done():
                try:
                    if await request.is_disconnected():
                        _disconnected = True
                        _task.cancel()
                        return
                except Exception:  # never let the watcher break the turn
                    return
                await asyncio.sleep(REQUEST_CANCEL_POLL_S)
        _watch = asyncio.create_task(_watch_disconnect())
    try:
        return await asyncio.wait_for(_task, timeout=TURN_DEADLINE_S)
    except asyncio.TimeoutError:
        log.warning("non-streaming turn deadline %.0fs exceeded -> partial result",
                    TURN_DEADLINE_S)
        await _reap_cpu_lane("non-streaming deadline")
        return {"success": False, "summary": dag.get("summary", ""),
                "nodes_total": len(dag.get("nodes") or []), "nodes_executed": 0,
                "node_results": [], "timed_out": True}
    except asyncio.CancelledError:
        if _disconnected:
            log.info("non-streaming turn CANCELLED: client disconnected -> swarm stopped")
            await _reap_cpu_lane("client disconnect")
            return {"success": False, "summary": dag.get("summary", ""),
                    "nodes_total": len(dag.get("nodes") or []), "nodes_executed": 0,
                    "node_results": [], "disconnected": True}
        raise
    finally:
        if _watch is not None:
            _watch.cancel()

async def _execute_dag_emitting(dag: dict, *, session_id: Optional[str],
                                chat_id: str, model: str,
                                deepen_barrier: bool = False):
    _my_cancel = asyncio.Event()
    _sup = bool(chat_id) and chat_id != "default"
    if _sup:
        _prev = _CHAT_CANCEL.get(chat_id)
        if _prev is not None:
            _prev.set()
        _CHAT_CANCEL[chat_id] = _my_cancel
    q: "asyncio.Queue" = asyncio.Queue()
    task = asyncio.create_task(
        execute_dag(dag, session_id=session_id, event_q=q,
                    deepen_barrier=deepen_barrier))
    _sec_bufs: dict = {}
    _sec_hdr: set = set()
    _func_by_name: dict = {}

    def _disp(_nm: str) -> str:
        lbl = str(_func_by_name.get(_nm) or "").strip()
        if lbl and not lbl.startswith(("node:", "a2a:")):
            return lbl
        c = _AGENT_REGISTRY.get(_nm) or {}
        for cand in (c.get("role"), c.get("job"), c.get("lane")):
            s = str(cand or "").strip()
            if s:
                return s[:48]
        return (str(_nm).split(":")[-1] or "agent")[:48]

    _turn_deadline = time.monotonic() + TURN_DEADLINE_S
    try:
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=0.25)
            except asyncio.TimeoutError:
                if task.done():
                    break
                if _my_cancel.is_set():
                    log.info("turn superseded for chat %s -> cancelling DAG", chat_id)
                    break
                if time.monotonic() > _turn_deadline:
                    log.warning("turn deadline %.0fs exceeded -> cancelling DAG",
                                TURN_DEADLINE_S)
                    await _reap_cpu_lane("streaming idle deadline")
                    break
                continue
            if item is None:  # sentinel
                break
            if _my_cancel.is_set() or time.monotonic() > _turn_deadline:
                log.warning("turn stop (deadline/supersede) chat %s -> cancel DAG",
                            chat_id)
                if time.monotonic() > _turn_deadline and not _my_cancel.is_set():
                    await _reap_cpu_lane("streaming busy deadline")
                break
            if item and item[0] == "SF":
                _sec_bufs[item[1]] = _sec_bufs.get(item[1], "") + (item[2] or "")
                continue
            kind, node, res = item
            aname = node.get("agent")
            if aname:
                name = str(aname)
                cfg = _AGENT_REGISTRY.get(aname) or {}
            else:
                name = str(node.get("tool") or "node")
                cfg = {"lane": "verb", "model": str(node.get("tool") or "")}
            if kind == "engage":
                _ctx = _node_context(node)
                _func_by_name[name] = _ctx or str((cfg or {}).get("role") or "")
                yield ("event", _node_status(chat_id=chat_id, model=model,
                                             name=name, cfg=cfg, state="engage",
                                             context=_ctx))
            else:
                ok = bool(isinstance(res, dict) and res.get("success"))
                if name not in _sec_hdr:
                    _nout = (_sec_bufs.get(name) or "").strip()
                    if not _nout:
                        _nout = (str(res.get("output") or "").strip()
                                 if isinstance(res, dict) else "")
                    if _nout:
                        _sec_hdr.add(name)
                        if name not in _func_by_name:
                            _func_by_name[name] = (_node_context(node)
                                                   or str((cfg or {}).get("role") or ""))
                        yield ("event", _sse_reasoning(
                            _sanitize_tool_text(f"\n\n🤝 {_disp(name)}:\n{_nout}\n"),
                            chat_id=chat_id, model=model))
                yield ("event", _node_status(chat_id=chat_id, model=model,
                                             name=name, cfg=cfg,
                                             state="ok" if ok else "down",
                                             context=_node_context(node)))
        dag_result = await task
        yield ("result", dag_result)
    finally:
        if not task.done():
            task.cancel()
        if _sup and _CHAT_CANCEL.get(chat_id) is _my_cancel:
            _CHAT_CANCEL.pop(chat_id, None)

_EK_REF_RE = re.compile(r"#E([A-Za-z0-9_]+)")

_EK_FIELD_REF_RE = re.compile(r"#E([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)")

def _smart_extract_from_jsonish(payload: str) -> str:
    s = _sanitize_tool_text((payload or "").strip())
    if not s:
        return ""
    try:
        obj = _loads_lenient(s)
        if isinstance(obj, dict):
            for k in ("name", "launch", "title", "id", "path"):
                v = obj.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()[:1024]
            for v in obj.values():
                if isinstance(v, str) and v.strip():
                    return v.strip()[:1024]
        elif isinstance(obj, list) and obj:
            first = obj[0]
            if isinstance(first, str):
                return first.strip()[:1024]
            if isinstance(first, dict):
                for k in ("name", "launch", "title", "id", "path"):
                    v = first.get(k)
                    if isinstance(v, str) and v.strip():
                        return v.strip()[:1024]
    except (json.JSONDecodeError, ValueError):
        pass
    first_line = s.splitlines()[0].strip()
    if first_line.startswith("{") and first_line.endswith("}"):
        try:
            obj = _loads_lenient(first_line)
            if isinstance(obj, dict):
                for k in ("name", "launch", "title", "id", "path"):
                    v = obj.get(k)
                    if isinstance(v, str) and v.strip():
                        return v.strip()[:1024]
        except (json.JSONDecodeError, ValueError):
            pass
    return first_line[:1024]

def _substitute_ek_refs(args: dict, results_by_id: dict) -> dict:
    if not args:
        return args
    out: dict = {}
    for k, v in args.items():
        if isinstance(v, str) and "#E" in v:
            def _sub_field(m: re.Match) -> str:
                ref, field = m.group(1), m.group(2)
                r = results_by_id.get(ref)
                if not r:
                    return m.group(0)
                payload = r.get("output") or ""
                try:
                    obj = _loads_lenient(payload)
                except (json.JSONDecodeError, ValueError):
                    first = (payload.strip().splitlines() or [""])[0]
                    try:
                        obj = _loads_lenient(first)
                    except (json.JSONDecodeError, ValueError):
                        return m.group(0)
                if isinstance(obj, list) and obj:
                    obj = obj[0]
                if isinstance(obj, dict):
                    val = obj.get(field)
                    if isinstance(val, str):
                        return val[:1024]
                return m.group(0)
            v = _EK_FIELD_REF_RE.sub(_sub_field, v)
            def _sub_bare(m: re.Match) -> str:
                ref = m.group(1)
                r = results_by_id.get(ref)
                if not r:
                    return m.group(0)
                payload = r.get("output") or ""
                return _smart_extract_from_jsonish(payload)
            out[k] = _EK_REF_RE.sub(_sub_bare, v)
        else:
            out[k] = v
    return out

def _fit_context(messages: list, tools: list, lane: str, want_ctx: int) -> int:
    if not CTX_FIT:
        return want_ctx
    try:
        if lane in SLOW_LANES:
            return want_ctx
        est = mios_tokenize.count_messages(messages, tools)  # WS-A5 tokenizer seam (was //4)
        return max(want_ctx, min(WORKER_TOOL_CTX_MAX, est + 512))
    except Exception:  # noqa: BLE001
        return want_ctx

def _node_deepens(node: dict) -> bool:
    if not node.get("agent"):
        return False
    lane = _agent_lane(_AGENT_REGISTRY.get(str(node.get("agent"))) or {})
    return lane in DEEPEN_LANES

async def _reap_cpu_lane(reason: str) -> None:
    if not RUNAWAY_REAP_ENABLE:
        return
    log.debug("runaway reaper (%s): /v1 lane self-releases on cancel -- no-op", reason)
