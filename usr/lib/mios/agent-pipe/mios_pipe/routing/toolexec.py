# AI-hint: Tool-call EXECUTION primitive extracted verbatim from server.py (refactor R4 wave).
# AI-doc: usr/share/doc/mios/manual/routing.md

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional

from mios_jsonsalvage import loads_lenient as _loads_lenient
from mios_aci import normalize_output as _aci_normalize
from mios_skills import execute_skill

log = logging.getLogger("mios-agent-pipe")

READ_TOOL_ENRICH_CHARS = 1500
READ_TOOL_ENRICH_TIMEOUT = 12.0
ACI_MAX_LINES = 160
ACI_HEAD_FRAC = 0.6
CODE_MODE_ENABLE = False
CODE_MODE_HEAVY_ONLY = False
MAX_DISPATCH_DEPTH = 2

_VERB_CATALOG: dict = {}
_RECIPE_CATALOG: dict = {}
_HIGH_PRIVILEGE_VERBS: set = set()
_WEB_ENRICH_VERBS = {"web_search", "web_extract", "crawl"}
_orch_ctx_var = None

dispatch_mios_verb = None
_mcp_call_tool = None
_record_mcp_tool_call = None
_plan_swarm = None
_live_agent_names = None
_agent_dag_from_tasks = None
_respond_agent_dag = None
_depth_exhausted = None
_dispatch_depth = None
_enter_dispatch_hop = None
_resolve_verb_key = None
_session_is_tainted = None
_db_fire = None
_db_post = None
_db_create = None
_src_record = None

def configure(*, read_tool_enrich_chars=None, read_tool_enrich_timeout=None,
              aci_max_lines=None, aci_head_frac=None, code_mode_enable=None,
              code_mode_heavy_only=None, max_dispatch_depth=None,
              verb_catalog=None, recipe_catalog=None, high_privilege_verbs=None,
              web_enrich_verbs=None, orch_ctx_var=None,
              dispatch_mios_verb=None, mcp_call_tool=None,
              record_mcp_tool_call=None, plan_swarm=None, live_agent_names=None,
              agent_dag_from_tasks=None, respond_agent_dag=None,
              depth_exhausted=None, dispatch_depth=None, enter_dispatch_hop=None,
              resolve_verb_key=None, session_is_tainted=None, db_fire=None,
              db_post=None, db_create=None, src_record=None,
              classify_verb_taint=None,
              sanitize_tool_text=None,
              otel_tracer=None) -> None:
    """Inject server.py's config scalars, catalogs, ContextVar and runtime
    helpers the executor + rescue corpus call back into."""
    global READ_TOOL_ENRICH_CHARS, READ_TOOL_ENRICH_TIMEOUT
    global ACI_MAX_LINES, ACI_HEAD_FRAC, CODE_MODE_ENABLE, CODE_MODE_HEAVY_ONLY
    global MAX_DISPATCH_DEPTH
    global _VERB_CATALOG, _RECIPE_CATALOG, _HIGH_PRIVILEGE_VERBS
    global _WEB_ENRICH_VERBS, _orch_ctx_var
    global _mcp_call_tool, _record_mcp_tool_call
    global _plan_swarm, _live_agent_names, _agent_dag_from_tasks
    global _respond_agent_dag, _depth_exhausted, _dispatch_depth
    global _enter_dispatch_hop, _resolve_verb_key, _session_is_tainted
    global _db_fire, _db_post, _db_create, _src_record
    global _classify_verb_taint, _sanitize_tool_text
    global _otel_tracer
    if otel_tracer is not None:
        _otel_tracer = otel_tracer
    if read_tool_enrich_chars is not None:
        READ_TOOL_ENRICH_CHARS = read_tool_enrich_chars
    if read_tool_enrich_timeout is not None:
        READ_TOOL_ENRICH_TIMEOUT = read_tool_enrich_timeout
    if aci_max_lines is not None:
        ACI_MAX_LINES = aci_max_lines
    if aci_head_frac is not None:
        ACI_HEAD_FRAC = aci_head_frac
    if code_mode_enable is not None:
        CODE_MODE_ENABLE = code_mode_enable
    if code_mode_heavy_only is not None:
        CODE_MODE_HEAVY_ONLY = code_mode_heavy_only
    if max_dispatch_depth is not None:
        MAX_DISPATCH_DEPTH = max_dispatch_depth
    if verb_catalog is not None:
        _VERB_CATALOG = verb_catalog
    if recipe_catalog is not None:
        _RECIPE_CATALOG = recipe_catalog
    if high_privilege_verbs is not None:
        _HIGH_PRIVILEGE_VERBS = high_privilege_verbs
    if web_enrich_verbs is not None:
        _WEB_ENRICH_VERBS = web_enrich_verbs
    if orch_ctx_var is not None:
        _orch_ctx_var = orch_ctx_var
    if dispatch_mios_verb is not None:
        globals()["dispatch_mios_verb"] = dispatch_mios_verb
    if mcp_call_tool is not None:
        _mcp_call_tool = mcp_call_tool
    if record_mcp_tool_call is not None:
        _record_mcp_tool_call = record_mcp_tool_call
    if classify_verb_taint is not None:
        _classify_verb_taint = classify_verb_taint
    if sanitize_tool_text is not None:
        _sanitize_tool_text = sanitize_tool_text
    if plan_swarm is not None:
        _plan_swarm = plan_swarm
    if live_agent_names is not None:
        _live_agent_names = live_agent_names
    if agent_dag_from_tasks is not None:
        _agent_dag_from_tasks = agent_dag_from_tasks
    if respond_agent_dag is not None:
        _respond_agent_dag = respond_agent_dag
    if depth_exhausted is not None:
        _depth_exhausted = depth_exhausted
    if dispatch_depth is not None:
        _dispatch_depth = dispatch_depth
    if enter_dispatch_hop is not None:
        _enter_dispatch_hop = enter_dispatch_hop
    if resolve_verb_key is not None:
        _resolve_verb_key = resolve_verb_key
    if session_is_tainted is not None:
        _session_is_tainted = session_is_tainted
    if db_fire is not None:
        _db_fire = db_fire
    if db_post is not None:
        _db_post = db_post
    if db_create is not None:
        _db_create = db_create
    if src_record is not None:
        _src_record = src_record

# Scan each markup token once. Nested lazy repetitions allowed exponential
# backtracking on malformed model output before this scanner.
_RESCUE_XML_TOKEN_RE = re.compile(
    r"<(/?)(function|parameter)(?:=([a-zA-Z0-9_.\-]+))?\s*>")


def _rescue_xml_calls(text: str):
    name = None
    args = {}
    parameter = None
    value_start = cursor = 0
    valid = False
    for token in _RESCUE_XML_TOKEN_RE.finditer(text):
        closing, kind, label = token.groups()
        if kind == "function" and not closing and label:
            name, args, parameter, valid = label, {}, None, True
            cursor = token.end()
            continue
        if name is None:
            continue
        if parameter is None and text[cursor:token.start()].strip():
            valid = False
        if kind == "parameter" and not closing and label:
            if parameter is not None:
                valid = False
            parameter, value_start = label, token.end()
        elif kind == "parameter" and closing and label is None:
            if parameter is None:
                valid = False
            else:
                args[parameter] = text[value_start:token.start()].strip()
                parameter = None
        elif kind == "function" and closing and label is None:
            if valid and parameter is None:
                yield name, args
            name = None
        else:
            valid = False
        cursor = token.end()


_RESCUE_FENCE_RE = re.compile(
    r"```(?:json|tool_call|tool)?\s*(\{.*?\}|\[.*?\])\s*```",
    re.DOTALL | re.IGNORECASE)
_RESCUE_TOOLCALL_RE = re.compile(
    r"<tool_call>\s*(\{.*?\}|\[.*?\])\s*</tool_call>", re.DOTALL | re.IGNORECASE)

def _norm_tool_call(name: str, args, idx: int) -> dict:
    """One OpenAI-spec tool_call dict; `arguments` canonicalised to a JSON
    STRING (the OpenAI egress contract -- Claude `input` / Gemini `args` arrive
    as objects, OpenAI `arguments` as a string; we normalise to the string)."""
    if isinstance(args, str):
        try:
            args = _loads_lenient(args)
        except Exception:  # noqa: BLE001 -- leave malformed for the executor
            args = {}
    if not isinstance(args, dict):
        args = {}
    return {"id": f"rescue{idx}",
            "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(args, ensure_ascii=False)}}

def _allowed_tool_names(tools: "Optional[list]") -> set:
    """Names the model may legitimately call: the OFFERED tools (OpenAI `tools=`
    shape) when present, else the full verb catalog. Gates the rescue parser so
    only REAL tool names are promoted out of narrated content."""
    names: set = set()
    for t in (tools or []):
        if isinstance(t, dict):
            fn = t.get("function") if isinstance(t.get("function"), dict) else t
            n = str((fn or {}).get("name") or "").strip()
            if n:
                names.add(n)

    import mios_verbcatalog
    model_names = getattr(mios_verbcatalog, "_MODEL_NAME_TO_VERB", {})
    return names | set(_VERB_CATALOG.keys()) | set(model_names.keys())

def _rescue_tool_calls(content: str, tools: "Optional[list]" = None) -> list:
    text = content or ""
    if "{" not in text and "<function=" not in text:
        return []
    allowed = _allowed_tool_names(tools)
    if not allowed:
        return []
    out: list = []
    for name, args in _rescue_xml_calls(text):
        if name in allowed or name.startswith(("mios_recipe__", "mios_skill__", "mcp.")):
            out.append(_norm_tool_call(name, args, len(out)))
    if out:
        return out
    candidates = list(_RESCUE_TOOLCALL_RE.findall(text))
    candidates += list(_RESCUE_FENCE_RE.findall(text))
    _stripped = text.strip()
    if _stripped[:1] in "{[":
        candidates.append(_stripped)
    for cand in candidates:
        try:
            obj = _loads_lenient(cand)
        except Exception:  # noqa: BLE001
            continue
        for item in (obj if isinstance(obj, list) else [obj]):
            if not isinstance(item, dict):
                continue
            fn = item.get("function") if isinstance(item.get("function"), dict) else None
            if fn:
                name = str(fn.get("name") or "").strip()
                args = fn.get("arguments")
            else:
                name = str(item.get("name") or item.get("tool")
                           or item.get("tool_name") or "").strip()
                args = next((item[k] for k in ("arguments", "args", "parameters", "input")
                             if item.get(k) is not None), None)
            if name and (name in allowed or name.startswith(("mios_recipe__", "mios_skill__", "mcp."))):
                out.append(_norm_tool_call(name, args, len(out)))
        if out:
            return out
    return out

def _verb_result_cap(verb: str) -> int:
    """Chars to keep from a verb's result before feeding it to the agent. A verb
    may declare a larger `max_result_chars` in mios.toml (inventory/discovery
    verbs return long lists) -- else the default READ_TOOL_ENRICH_CHARS. Data-
 driven SSOT, no per-verb literals in code."""
    cap = int((_VERB_CATALOG.get(verb) or {}).get("max_result_chars") or 0)
    return cap if cap > 0 else READ_TOOL_ENRICH_CHARS

def _cap_verb_result(verb: str, out: str) -> str:
    cap = _verb_result_cap(verb)
    return _aci_normalize(out, max_chars=cap, max_lines=ACI_MAX_LINES,
                          head_frac=ACI_HEAD_FRAC, label=verb)

def _format_tool_error(res: Any) -> Optional[dict]:
    if isinstance(res, dict):
        if "error" in res and isinstance(res["error"], dict) and "message" in res["error"]:
            return res
        has_error = False
        err_msg = ""
        if res.get("success") is False:
            has_error = True
            err_msg = res.get("error") or res.get("stderr") or "verb execution failed"
        elif res.get("ok") is False:
            has_error = True
            err_msg = res.get("error") or res.get("stderr") or "verb execution failed"
        elif "error" in res and res["error"]:
            has_error = True
            err_msg = str(res["error"])
        if has_error:
            return {
                "error": {
                    "message": err_msg,
                    "type": "invalid_request_error",
                    "code": "tool_execution_failed"
                }
            }
    return None

def _tool_span(vname: str, session_id: str):
    import contextlib
    @contextlib.contextmanager
    def _ctx():
        if _otel_tracer and vname:
            from opentelemetry.trace import SpanKind
            with _otel_tracer.start_as_current_span(
                "execute_tool",
                kind=SpanKind.INTERNAL,
                attributes={
                    "gen_ai.tool.name": vname,
                    "session_id": session_id or "",
                }
            ) as span:
                yield span
        else:
            yield None
    return _ctx()

async def _exec_tool_calls(tcs: list, push, allow_write: bool = False) -> tuple:
    from mios_pipe.routing.chat import _record_active, _replay_active, _replay_tool_queue, _conv_key_var, _in_exec_tool_calls

    _sess = (_orch_ctx_var.get() or {}).get("session_id")
    if not _sess and _conv_key_var is not None:
        try:
            _sess = _conv_key_var.get()
        except Exception:
            pass

    if _replay_active.get():
        tool_msgs: list = []
        q = _replay_tool_queue.get()
        for tc in tcs:
            fn = tc.get("function") or {}
            vname = str(fn.get("name") or "").strip()
            tmsg = {"role": "tool"}
            if tc.get("id"):
                tmsg["tool_call_id"] = tc["id"]
            if vname:
                tmsg["name"] = vname

            if q:
                row = q.pop(0)
                meta = row.get("meta") or {}
                tmsg["content"] = str(meta.get("output") or "")
                log.info("Replayed tool call: %s -> %s", vname, tmsg["content"][:200])
            else:
                tmsg["content"] = f"(error: no recorded tool call found in replay log for {vname})"
            tool_msgs.append(tmsg)
        return tool_msgs, True

    tool_msgs: list = []
    ran_read = False
    _iec_token = _in_exec_tool_calls.set(True)
    for tc in tcs:
        fn = tc.get("function") or {}
        vname = str(fn.get("name") or "").strip()
        tmsg = {"role": "tool"}
        if tc.get("id"):
            tmsg["tool_call_id"] = tc["id"]   # OpenAI-spec linkage
        if vname:
            tmsg["name"] = vname
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = _loads_lenient(args)
            except Exception:  # noqa: BLE001
                args = {}
        if not isinstance(args, dict):
            args = {}
        if vname.startswith("mios_skill__"):
            real = vname[len("mios_skill__"):]
            if not allow_write:
                tmsg["content"] = (
                    f"(skipped skill {real or '?'}: writes disabled this turn)")
                tool_msgs.append(tmsg)
                continue
            ran_read = True
            _args_str = f" {json.dumps(args, ensure_ascii=False)}" if args else ""
            push(f" 🔧 skill:{real}{_args_str}")
            try:
                with _tool_span(vname, _sess):
                    res = await asyncio.wait_for(
                        execute_skill(real, args),
                        timeout=READ_TOOL_ENRICH_TIMEOUT * 2)
            except Exception as e:  # noqa: BLE001
                res = {"error": str(e)}
            out = (json.dumps(res, ensure_ascii=False)
                   if isinstance(res, (dict, list)) else str(res))
            tmsg["content"] = out[:READ_TOOL_ENRICH_CHARS]
            push(f"\n\n🤝 skill:{real} output:\n{tmsg['content']}\n")
            tool_msgs.append(tmsg)
            continue
        if vname.startswith("mios_recipe__"):
            real = vname[len("mios_recipe__"):]
            rkey = real.replace("_", "-")
            rcfg = _RECIPE_CATALOG.get(rkey) or {}
            r_perm = str(rcfg.get("permission", "")).lower()
            if r_perm != "read" and not allow_write:
                tmsg["content"] = (
                    f"(skipped recipe {real or '?'}: not a read-only tool)")
                tool_msgs.append(tmsg)
                continue
            ran_read = True
            _args_str = f" {json.dumps(args, ensure_ascii=False)}" if args else ""
            push(f" 🔧 recipe:{real}{_args_str}")
            recipe_args = {"name": rkey, "params": {key: value for key, value in args.items() if key != "os"}}
            if args.get("os") is not None:
                recipe_args["os"] = args["os"]
            try:
                with _tool_span(vname, _sess):
                    res = await asyncio.wait_for(
                        dispatch_mios_verb("os_recipe", recipe_args),
                        timeout=READ_TOOL_ENRICH_TIMEOUT * 2)
            except Exception as e:  # noqa: BLE001
                res = {"error": str(e)}
            out = (json.dumps(res, ensure_ascii=False)
                   if isinstance(res, (dict, list)) else str(res))
            tmsg["content"] = _cap_verb_result("os_recipe", out)
            push(f"\n\n🤝 recipe:{real} output:\n{tmsg['content']}\n")
            tool_msgs.append(tmsg)
            continue
        if vname.startswith("mcp."):
            if not allow_write:
                tmsg["content"] = (
                    f"(skipped MCP tool {vname}: writes disabled this turn)")
                tool_msgs.append(tmsg)
                continue
            ran_read = True
            _args_str = f" {json.dumps(args, ensure_ascii=False)}" if args else ""
            push(f" 🔧 {vname}{_args_str}")
            try:
                with _tool_span(vname, _sess):
                    res = await asyncio.wait_for(
                        _mcp_call_tool(vname, args),
                        timeout=READ_TOOL_ENRICH_TIMEOUT * 2)
            except Exception as e:  # noqa: BLE001
                res = {"error": str(e)}
            out = (json.dumps(res, ensure_ascii=False)
                   if isinstance(res, (dict, list)) else str(res))
            tmsg["content"] = out[:READ_TOOL_ENRICH_CHARS]
            _ok = not (isinstance(res, dict) and res.get("error"))
            _record_mcp_tool_call(vname, args, _ok, out, _sess)
            push(f"\n\n🤝 {vname} output:\n{tmsg['content']}\n")
            tool_msgs.append(tmsg)
            continue

        if vname == "code_mode":
            if not allow_write:
                tmsg["content"] = (
                    "(skipped code_mode: writes disabled this turn)")
                tool_msgs.append(tmsg)
                continue
            if not CODE_MODE_ENABLE:
                tmsg["content"] = (
                    "(skipped code_mode: disabled -- set [code_mode].enable)")
                tool_msgs.append(tmsg)
                continue
            if CODE_MODE_HEAVY_ONLY and not (_orch_ctx_var.get() or {}):
                tmsg["content"] = (
                    "(skipped code_mode: heavy-lane-only -- a light worker may not author "
                    "code; the heavy orchestrator handles code_mode)")
                tool_msgs.append(tmsg)
                continue
            ran_read = True
            _args_str = f" {json.dumps(args, ensure_ascii=False)}" if args else ""
            push(f" 🧮 code_mode{_args_str}")
            try:
                with _tool_span(vname, _sess):
                    res = await asyncio.wait_for(
                        dispatch_mios_verb(vname, args),
                        timeout=READ_TOOL_ENRICH_TIMEOUT * 4)
            except Exception as e:  # noqa: BLE001
                res = {"error": str(e)}
            out = (json.dumps(res, ensure_ascii=False)
                   if isinstance(res, (dict, list)) else str(res))
            tmsg["content"] = _cap_verb_result("code_mode", out)
            push(f"\n\n🤝 code_mode output:\n{tmsg['content']}\n")
            tool_msgs.append(tmsg)
            continue
        if vname == "dispatch_to_nodes":
            if not allow_write:
                tmsg["content"] = "(skipped dispatch_to_nodes: writes disabled)"
                tool_msgs.append(tmsg)
                continue
            _octx = _orch_ctx_var.get() or {}
            if not _octx:
                tmsg["content"] = "(dispatch_to_nodes unavailable here)"
                tool_msgs.append(tmsg)
                continue
            ran_read = True
            _raw_tasks = args.get("tasks") if isinstance(args.get("tasks"), list) else []
            _norm = []
            for _t in _raw_tasks:
                if not isinstance(_t, dict):
                    continue
                _obj = str(_t.get("objective") or _t.get("refined_text")
                           or _t.get("task") or "").strip()
                if not _obj:
                    continue
                _brief = _obj
                if _t.get("output_format"):
                    _brief += "\n\nReturn: " + str(_t["output_format"])
                if _t.get("tool_guidance"):
                    _brief += "\nPrefer tools: " + str(_t["tool_guidance"])
                if _t.get("boundaries"):
                    _brief += "\nDo NOT: " + str(_t["boundaries"])
                _norm.append({
                    "target_agent": str(_t.get("node") or _t.get("target_agent") or ""),
                    "refined_text": _brief,
                    "title": (str(_t.get("title") or _obj))[:72],
                    "local_state": bool(_t.get("local_state")),
                    "web": bool(_t.get("web"))})
            if _depth_exhausted():
                log.info("dispatch_to_nodes: depth %d >= %d -> refusing nested "
                         "swarm (degrade-closed)", _dispatch_depth(), MAX_DISPATCH_DEPTH)
                tmsg["content"] = ("(dispatch_to_nodes: maximum fan-out depth "
                                   "reached; continue with the current agent)")
                tool_msgs.append(tmsg)
                continue
            _args_str = f" {json.dumps(args, ensure_ascii=False)}" if args else ""
            push(f" 🛰️ dispatch_to_nodes ({len(_norm)}){_args_str}")
            try:
                _enter_dispatch_hop()
                if not _norm:
                    _norm = await _plan_swarm(_octx.get("last_user_text") or "", None)
                _live = await _live_agent_names()
                _dag = _agent_dag_from_tasks(_norm, live_agents=_live,
                                             include_research=True)
                with _tool_span(vname, _sess):
                    _sresp = await _respond_agent_dag(
                        _dag, _octx.get("refined"), streaming=False,
                        chat_id=str(_octx.get("chat_id") or ""),
                        model=str(_octx.get("model") or ""),
                        session_id=_octx.get("session_id"),
                        last_user_text=str(_octx.get("last_user_text") or ""),
                        persona_system=str(_octx.get("persona_system") or ""),
                        request=_octx.get("request"))
                _stext = ""
                if _sresp is not None:
                    try:
                        _sb = _loads_lenient(bytes(_sresp.body).decode("utf-8"))
                        _stext = _sb["choices"][0]["message"]["content"]
                    except Exception:  # noqa: BLE001
                        _stext = ""
                tmsg["content"] = _stext or "(the node swarm returned no result)"
            except Exception as _e:  # noqa: BLE001
                tmsg["content"] = f"(dispatch_to_nodes failed: {str(_e)[:160]})"
            push(f"\n\n🤝 dispatch_to_nodes output:\n{tmsg['content']}\n")
            tool_msgs.append(tmsg)
            continue
        _key = _resolve_verb_key(vname)
        v = _VERB_CATALOG.get(_key)
        if not v or (str(v.get("permission", "")).lower() != "read"
                     and not allow_write):
            tmsg["content"] = f"(skipped {vname or '?'}: not a read-only tool)"
            tool_msgs.append(tmsg)
            continue
        if _key in _HIGH_PRIVILEGE_VERBS and _sess:
            try:
                _tainted, _chain = await _session_is_tainted(_sess)
            except Exception:  # noqa: BLE001
                _tainted, _chain = False, ""
            if _tainted:
                _db_fire(_db_post(_db_create("event", {
                    "source": "mios-agent-pipe", "kind": "firewall_block",
                    "severity": "high",
                    "summary": f"{_key} refused -- session tainted ({_chain[:120]})",
                    "payload": {"tool": _key, "taint_chain": _chain[:200]},
                }, now_fields=("ts",))))
                tmsg["content"] = (
                    f"(firewall_block: {vname} refused -- this session is tainted by "
                    f"untrusted content [{_chain[:120]}]; a high-privilege verb cannot "
                    f"run until the chain is cleared)")
                tool_msgs.append(tmsg)
                continue
        ran_read = True
        _args_str = f" {json.dumps(args, ensure_ascii=False)}" if args else ""
        push(f" 🔧 {vname}{_args_str}")
        try:
            with _tool_span(vname, _sess):
                res = await asyncio.wait_for(
                    dispatch_mios_verb(_key, args),
                    timeout=READ_TOOL_ENRICH_TIMEOUT * 2)
        except Exception as e:  # noqa: BLE001
            res = {"error": str(e)}
        if _key in _WEB_ENRICH_VERBS:
            try:
                _rj = res
                if isinstance(res, dict) and isinstance(res.get("output"), str):
                    _rj = _loads_lenient(res["output"])
                if isinstance(_rj, dict):
                    _src_record(_rj.get("results") or [])
            except Exception:  # noqa: BLE001 -- never break the tool loop
                pass
        _res_dict = None
        if isinstance(res, dict):
            _res_dict = res
        elif isinstance(res, str):
            try:
                _parsed = _loads_lenient(res)
                if isinstance(_parsed, dict):
                    _res_dict = _parsed
            except Exception:
                pass

        if _res_dict:
            _err = _format_tool_error(_res_dict)
            if _err:
                res = _err

        out = (json.dumps(res, ensure_ascii=False)
               if isinstance(res, (dict, list)) else str(res))
        tmsg["content"] = _cap_verb_result(_key, out)
        push(f"\n\n🤝 {vname} output:\n{tmsg['content']}\n")
        tool_msgs.append(tmsg)

    if _record_active.get() and _sess:
        try:
            import uuid
            for i, tc in enumerate(tcs):
                if i >= len(tool_msgs):
                    break
                fn = tc.get("function") or {}
                vname = str(fn.get("name") or "").strip()
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        args = _loads_lenient(args)
                    except Exception:
                        args = {}
                out_content = tool_msgs[i].get("content") or ""
                success = not out_content.startswith("(skipped") and not out_content.startswith("(firewall_block")

                row = {
                    "id": f"session:tool:{uuid.uuid4().hex[:24]}",
                    "kind": "tool_io",
                    "owui_chat_id": _sess,
                    "meta": {
                        "tool": vname,
                        "args": args if isinstance(args, dict) else {},
                        "output": out_content,
                        "success": success
                    }
                }
                if _db_create is not None:
                    sql = _db_create("session", row, now_fields=("ts",), passport_sign=False)
                    if _db_fire is not None and _db_post is not None:
                        _db_fire(_db_post(sql))
                log.info("Recorded tool call in session: %s", vname)
        except Exception as e:
            log.warning("Failed to record tool calls in session: %s", e)

    _in_exec_tool_calls.reset(_iec_token)
    return tool_msgs, ran_read

_classify_verb_taint = None
_sanitize_tool_text = None
_otel_tracer = None

def _record_mcp_tool_call(tool: str, args: dict, success: bool, output: str,
                          session_id: "Optional[str]") -> None:
    """P6: persist an MCP tool_call as a session-linked row so the Semantic Firewall sees
    its taint. MCP tools dispatch via _exec_tool_calls branch (b2) -> _mcp_call_tool,
    BYPASSING the broker (_dispatch_bounded) that records native-verb taint -- so without
    this an untrusted_web MCP server (Playwright) would never taint the session and the
    firewall would never gate the downstream exfil verbs (lethal trifecta left open)."""
    t_tainted, t_reason = _classify_verb_taint(
        tool, args if isinstance(args, dict) else {})
    if not t_tainted:
        return                                  # only record taint sources (cheap)
    _row = {
        "tool": tool,
        "args": args if isinstance(args, dict) else {},
        "result_preview": _sanitize_tool_text(output or "")[:500],
        "success": bool(success),
        "latency_ms": 0,
        "tainted": True,
        "taint_reason": (t_reason or "") or None,
    }
    sql = _db_create("tool_call", _row, now_fields=("ts",))
    if session_id:
        sql = sql.rstrip().rstrip(";") + f", session = {session_id};"
    _db_fire(_db_post(sql))
