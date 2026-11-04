# AI-hint: SKILLS execution cluster extracted verbatim from server.py (refactor R7/mios_skills wave).
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from typing import Optional

import mios_pg as _mios_pg
from mios_a2a import _a2a_now
from mios_a2a_principal import _passport_sign

log = logging.getLogger("mios-agent-pipe")

_db_read = None
_db_post = None
_db_update = None
_db_write = None
_pg_mirror = None
dispatch_mios_verb = None
SKILLS_ENABLED = True
_SKILLS_EPISODIC_DIR = None
_SKILLS_EPISODIC_ENABLED = None
_turn_volatile_var = None

def configure(*, db_read=None, db_post=None, db_update=None, db_write=None,
              pg_mirror=None, dispatch_verb=None, skills_enabled=None,
              skills_episodic_dir=None, skills_episodic_enabled=None,
              turn_volatile_var=None) -> None:
    global _db_read, _db_post, _db_update, _db_write, _pg_mirror
    global dispatch_mios_verb, SKILLS_ENABLED
    global _SKILLS_EPISODIC_DIR, _SKILLS_EPISODIC_ENABLED, _turn_volatile_var
    if turn_volatile_var is not None:
        _turn_volatile_var = turn_volatile_var
    if db_read is not None:
        _db_read = db_read
    if db_post is not None:
        _db_post = db_post
    if db_update is not None:
        _db_update = db_update
    if db_write is not None:
        _db_write = db_write
    if pg_mirror is not None:
        _pg_mirror = pg_mirror
    if dispatch_verb is not None:
        dispatch_mios_verb = dispatch_verb
    if skills_enabled is not None:
        SKILLS_ENABLED = skills_enabled
    if skills_episodic_dir is not None:
        _SKILLS_EPISODIC_DIR = skills_episodic_dir
    if skills_episodic_enabled is not None:
        _SKILLS_EPISODIC_ENABLED = skills_episodic_enabled

async def _skill_fetch(name: str) -> Optional[dict]:
    """Read one skill row by name. Returns the row dict (with body
    + status fields) or None if not found."""
    if not name:
        return None
    sql = (
        f"SELECT id, name, body, status, source, version, "
        f"description, support, confidence "
        f"FROM skill WHERE name = {json.dumps(name)} LIMIT 1;"
    )
    r = await _db_read(sql, pg_sql=(
        "SELECT id, name, body, status, source, version, "
        "description, support, confidence FROM skill "
        "WHERE name = %(n)s LIMIT 1"), pg_params={"n": name})
    if not r:
        return None
    rows = (r[-1] or {}).get("result") or []
    return rows[0] if rows else None

async def _skill_list(*, status: str = "promoted",
                      source: Optional[str] = None,
                      limit: int = 200) -> list[dict]:
    where = []
    if status and status != "all":
        where.append(f"status = {json.dumps(status)}")
    if source and source != "all":
        where.append(f"source = {json.dumps(source)}")
    clause = " AND ".join(where) if where else "true"
    sql = (
        f"SELECT name, description, body, source, status, "
        f"support, confidence, version "
        f"FROM skill WHERE {clause} "
        f"ORDER BY name LIMIT {int(limit)};"
    )
    pg_where, pg_params = [], {}
    if status and status != "all":
        pg_where.append("status = %(status)s"); pg_params["status"] = status
    if source and source != "all":
        pg_where.append("source = %(source)s"); pg_params["source"] = source
    pg_clause = " AND ".join(pg_where) if pg_where else "true"
    r = await _db_read(sql, pg_sql=(
        "SELECT name, description, body, source, status, support, "
        f"confidence, version FROM skill WHERE {pg_clause} "
        f"ORDER BY name LIMIT {int(limit)}"), pg_params=pg_params)
    if not r:
        return []
    return (r[-1] or {}).get("result") or []

async def execute_skill(name: str, params: dict, *,
                        session_id: Optional[str] = None) -> dict:
    if not SKILLS_ENABLED:
        return {"success": False,
                "skill": name,
                "error": "skills_disabled",
                "steps": [],
                "failures": ["skills disabled via MIOS_SKILLS_ENABLE"]}
    row = await _skill_fetch(name)
    if not row:
        return {"success": False, "skill": name,
                "error": "not_found", "steps": [], "failures": []}
    if row.get("status") != "promoted":
        return {"success": False, "skill": name,
                "error": "not_promoted",
                "status": row.get("status"),
                "steps": [], "failures": []}
    body = row.get("body") or {}
    steps = body.get("steps") or []
    if not steps:
        return {"success": False, "skill": name,
                "error": "empty_body", "steps": [], "failures": []}
    mode = str(body.get("mode") or "sequence").lower()
    inv_id = await _skill_invocation_open(
        row.get("id"), params or {}, session_id)
    expanded: list[dict] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        ef = step.get("expand_from")
        if not ef:
            expanded.append(step)
            continue
        ba = step.get("bind_as") or "item"
        seq = (params or {}).get(ef)
        if not isinstance(seq, list) or not seq:
            continue
        for v in seq:
            inst = {k: w for k, w in step.items()
                    if k not in ("expand_from", "bind_as")}
            inst_params = {**(params or {}), ba: v}
            inst["args"] = _skill_render_args(
                inst.get("args") or {}, inst_params)
            inst["_expanded_from"] = ef
            inst["_bound_value"] = v
            expanded.append(inst)
    steps = expanded
    results: list[dict] = []
    failures: list[str] = []
    for idx, step in enumerate(steps):
        verb = (step or {}).get("verb") or ""
        raw_args = (step or {}).get("args") or {}
        if step.get("_expanded_from"):
            rendered = raw_args
        else:
            rendered = _skill_render_args(raw_args, params or {})
        leftover = [
            v for v in rendered.values()
            if isinstance(v, str) and _PARAM_TOKEN_RE.search(v)
        ]
        if leftover:
            failures.append(
                f"step {idx} ({verb}): missing params {leftover}")
            results.append({"step": idx, "verb": verb,
                            "success": False,
                            "error": "missing_params",
                            "leftover": leftover})
            await _skill_invocation_close(inv_id, success=False)
            return {"success": False, "skill": name, "steps": results,
                    "failures": failures, "aborted": True}
        r = await dispatch_mios_verb(
            verb, rendered, session_id=session_id)
        results.append({
            "step": idx, "verb": verb, "args": rendered,
            "success": bool(r.get("success", False)),
            "exit_code": r.get("exit_code"),
            "output": r.get("output", "")[:400],
            "stderr": r.get("stderr", "")[:400],
            "tainted": r.get("tainted", False),
            "taint_reason": r.get("taint_reason", ""),
            "firewall_blocked": bool(r.get("firewall_blocked", False)),
            "hitl_blocked": bool(
                r.get("hitl_blocked", False) or r.get("hitl_pending", False)),
        })
        if session_id:
            q = (
                f"SELECT id, ts FROM tool_call "
                f"WHERE session = {session_id} "
                f"  AND tool = {json.dumps(verb)} "
                f"ORDER BY ts DESC LIMIT 1;"
            )
            qr = await _db_post(q)
            if qr:
                tc_rows = (qr[-1] or {}).get("result") or []
                if tc_rows:
                    await _skill_attribute_tool_call(
                        inv_id, tc_rows[0].get("id"), idx)
        step_ok = bool(r.get("success", False))
        if mode == "try-each":
            if step_ok:
                await _skill_invocation_close(inv_id, success=True)
                await _db_update(
                    f"UPDATE {row.get('id')} SET last_used_at = time::now();",
                    pg_sql="UPDATE skill SET last_used_at = now() WHERE id = %(id)s",
                    pg_params={"id": _mios_pg.rid_to_pg_id(row.get('id'))})
                _db_write("event", {
                    "source": "agent-pipe",
                    "kind": "skill_run",
                    "severity": "info",
                    "summary": f"{name} ok at step {idx} (try-each)",
                    "payload": {"skill": name, "winning_step": idx,
                                "mode": "try-each",
                                "steps_attempted": idx + 1},
                }, now_fields=("ts",))
                return {"success": True, "skill": name, "steps": results,
                        "failures": failures, "aborted": False,
                        "winning_step": idx, "mode": "try-each"}
            failures.append(
                f"step {idx} ({verb}): "
                f"exit={r.get('exit_code')} "
                f"stderr={r.get('stderr','')[:200]}")
            continue
        if not step_ok:
            failures.append(
                f"step {idx} ({verb}): "
                f"exit={r.get('exit_code')} "
                f"stderr={r.get('stderr','')[:200]}")
            await _skill_invocation_close(inv_id, success=False)
            _db_write("event", {
                "source": "agent-pipe",
                "kind": "skill_run",
                "severity": "warn",
                "summary": f"{name} failed at step {idx}",
                "payload": {"skill": name, "step": idx,
                            "verb": verb,
                            "stderr": r.get("stderr", "")[:300]},
            }, now_fields=("ts",))
            return {"success": False, "skill": name, "steps": results,
                    "failures": failures, "aborted": True}
    if mode == "try-each":
        await _skill_invocation_close(inv_id, success=False)
        _db_write("event", {
            "source": "agent-pipe",
            "kind": "skill_run",
            "severity": "warn",
            "summary": f"{name} exhausted (try-each)",
            "payload": {"skill": name, "mode": "try-each",
                        "steps_attempted": len(steps)},
        }, now_fields=("ts",))
        return {"success": False, "skill": name, "steps": results,
                "failures": failures, "aborted": True,
                "mode": "try-each"}
    await _skill_invocation_close(inv_id, success=True)
    await _db_update(
        f"UPDATE {row.get('id')} SET last_used_at = time::now();",
        pg_sql="UPDATE skill SET last_used_at = now() WHERE id = %(id)s",
        pg_params={"id": _mios_pg.rid_to_pg_id(row.get('id'))})
    _db_write("event", {
        "source": "agent-pipe",
        "kind": "skill_run",
        "severity": "info",
        "summary": f"{name} ok ({len(steps)} steps)",
        "payload": {"skill": name, "steps_run": len(steps)},
    }, now_fields=("ts",))
    return {"success": True, "skill": name, "steps": results,
            "failures": [], "aborted": False}

def _skill_to_openai_tool(row: dict) -> dict:
    """Render one skill row as an OpenAI function-tool schema.
    Hermes + OpenCode consume this dump verbatim so their tool
    surface auto-extends every time the operator promotes a skill --
    no code changes per skill on either client."""
    name = row.get("name") or ""
    description = row.get("description") or f"MiOS skill {name}"
    body = row.get("body") or {}
    params_raw = body.get("params") or []

    if isinstance(params_raw, dict):
        properties = {}
        required = []
        for p, pcfg in params_raw.items():
            if isinstance(pcfg, dict):
                ptype = pcfg.get("type", "string")
                pdesc = pcfg.get("desc") or pcfg.get("description") or f"value for ${p}"
                spec = {"type": ptype, "description": pdesc}
                if "enum" in pcfg:
                    spec["enum"] = list(pcfg["enum"])
                if "items" in pcfg:
                    spec["items"] = pcfg["items"]
                properties[p] = spec
            else:
                properties[p] = {
                    "type": "string",
                    "description": f"value for ${p}"
                }
            required.append(p)
    else:
        params = list(params_raw)
        properties = {
            p: {"type": "string",
                "description": f"value for ${p}"} for p in params
        }
        required = params

    return {
        "type": "function",
        "function": {
            "name": f"mios_skill__{re.sub(r'[^A-Za-z0-9_]', '_', name)}",
            "description": description,
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
        "x-mios-skill": name,
    }

def _make_schema_strict(schema: dict) -> dict:
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    s = dict(schema)
    if s.get("type") == "object":
        properties = s.get("properties") or {}
        if not isinstance(properties, dict):
            properties = {}
        s["properties"] = dict(properties)

        required = s.get("required") or []
        if not isinstance(required, list):
            required = []

        new_required = list(required)
        for prop_name, prop_val in s["properties"].items():
            if isinstance(prop_val, dict):
                prop_val = _make_schema_strict(prop_val)
                s["properties"][prop_name] = prop_val
                if prop_name not in required:
                    new_required.append(prop_name)
                    t = prop_val.get("type")
                    if isinstance(t, str):
                        prop_val["type"] = [t, "null"]
                    elif isinstance(t, list):
                        if "null" not in t:
                            prop_val["type"] = list(t) + ["null"]
                    else:
                        prop_val["type"] = ["object", "null"]
            else:
                prop_val = {"type": ["string", "null"]}
                s["properties"][prop_name] = prop_val
                if prop_name not in required:
                    new_required.append(prop_name)
        s["required"] = new_required
        s["additionalProperties"] = False
    elif s.get("type") == "array":
        items = s.get("items")
        if isinstance(items, dict):
            s["items"] = _make_schema_strict(items)
    return s

def _mcp_tool_to_openai_tool(key: str, info: dict) -> dict:
    """Project a registered external MCP tool (key 'mcp.<server>.<tool>', raw
    MCP inputSchema) into OpenAI function-tool shape so it joins the worker tool
 surface (P0: wire the MCP CLIENT into the agent loop).
    MCP inputSchema IS JSON-Schema -> drops straight into function.parameters.
    Strictified for OpenAI compliance."""
    schema = info.get("inputSchema")
    strict_schema = _make_schema_strict(schema)
    return {
        "type": "function",
        "function": {
            "name": key,
            "description": info.get("description") or f"MCP tool {key}",
            "strict": True,
            "parameters": strict_schema,
        },
        "x-mios-mcp-server": info.get("server_id"),
    }

_PARAM_TOKEN_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")

def _skill_render_args(args: dict, params: dict) -> dict:
    out: dict = {}
    for k, v in (args or {}).items():
        if isinstance(v, str):
            def _sub(m: re.Match) -> str:
                key = m.group(1)
                if key in params and params[key] is not None:
                    return str(params[key])
                return m.group(0)
            out[k] = _PARAM_TOKEN_RE.sub(_sub, v)
        else:
            out[k] = v
    return out

_SKILL_INV_META: dict = {}

_PG_ID_PREFIX = "skill_invocation:pg#"

def _pg_row_id(inv_id):
    """The postgres row id behind an invocation handle, or None when the handle
    came from the legacy backend or from the synthetic no-DB fallback."""
    if not isinstance(inv_id, str) or not inv_id.startswith(_PG_ID_PREFIX):
        return None
    try:
        return int(inv_id[len(_PG_ID_PREFIX):])
    except ValueError:
        return None

async def _pg_invocation_open(skill_id, params, session_id, envelope):
    """INSERT the open-time invocation row and return its handle.

    Under pg-primary the legacy CREATE above is discarded, so without this the
    row only ever appeared at CLOSE and started_at/params were lost. Returns
    None when postgres is absent, so the synthetic fallback still applies."""
    try:
        rows = await _mios_pg.execute(
            "INSERT INTO skill_invocation "
            "(skill, session_id, started_at, params, passport) "
            "VALUES (%(skill)s, %(session)s, now(), %(params)s::jsonb, "
            "        %(passport)s::jsonb) RETURNING id",
            {"skill": str(skill_id), "session": session_id,
             "params": json.dumps(params or {}),
             "passport": json.dumps(envelope) if envelope is not None else None},
            fetch=True)
    except Exception:  # noqa: BLE001 -- an invocation must never fail on the DB
        return None
    if not rows:
        return None
    row = rows[0] if isinstance(rows[0], dict) else {}
    rid = row.get("id")
    return f"{_PG_ID_PREFIX}{int(rid)}" if rid is not None else None

async def _pg_invocation_close(row_id: int, success: bool) -> None:
    """Stamp ended_at + success on the open-time row. Best-effort."""
    try:
        await _mios_pg.execute(
            "UPDATE skill_invocation SET ended_at = now(), success = %(ok)s "
            "WHERE id = %(id)s",
            {"ok": bool(success), "id": int(row_id)}, fetch=False)
    except Exception:  # noqa: BLE001
        log.debug("skills: could not close invocation %s", row_id)

async def _pg_attribute_tool_call(row_id: int, tool_call_id, step_index) -> None:
    """Record the skill -> tool_call edge the RELATE used to express.
    Idempotent: a retried step updates its index instead of erroring."""
    try:
        await _mios_pg.execute(
            "INSERT INTO skill_tool_call (invocation_id, tool_call_id, step_index) "
            "VALUES (%(inv)s, %(tc)s, %(step)s) "
            "ON CONFLICT (invocation_id, tool_call_id) "
            "DO UPDATE SET step_index = EXCLUDED.step_index",
            {"inv": int(row_id), "tc": str(tool_call_id),
             "step": int(step_index)}, fetch=False)
    except Exception:  # noqa: BLE001
        log.debug("skills: could not attribute tool_call %s", tool_call_id)

async def _skill_invocation_open(skill_id: str,
                                 params: dict,
                                 session_id: Optional[str]) -> Optional[str]:
    parts = [
        "started_at = time::now()",
        f"skill = {skill_id}",
        f"params = {json.dumps(params or {})}",
    ]
    if session_id:
        parts.append(f"session = {session_id}")
    hash_fields = {
        "started_at": "time::now()",
        "skill": skill_id,
        "params": params or {},
    }
    if session_id:
        hash_fields["session"] = session_id
    envelope = _passport_sign("skill_invocation", hash_fields)
    if envelope is not None:
        parts.append(f"passport = {json.dumps(envelope)}")
    sql = "CREATE skill_invocation SET " + ", ".join(parts) + " RETURN AFTER;"
    r = await _db_post(sql)
    inv_id = None
    if r:
        last = r[-1] or {}
        if last.get("status") == "OK":
            rows = last.get("result") or []
            if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                inv_id = rows[0].get("id")
    if not inv_id:
        inv_id = await _pg_invocation_open(skill_id, params, session_id, envelope)
    if not inv_id:
        inv_id = "skill_invocation:pg-" + uuid.uuid4().hex
    _SKILL_INV_META[inv_id] = {"skill": skill_id, "session": session_id}
    return inv_id

async def _skill_invocation_close(inv_id: Optional[str],
                                  success: bool) -> None:
    if not inv_id:
        return
    meta = _SKILL_INV_META.pop(inv_id, None)
    row_id = _pg_row_id(inv_id)
    if row_id is not None:
        # The open-time row already exists; mirroring here would duplicate it.
        await _pg_invocation_close(row_id, success)
    elif meta:
        try:
            _pg_mirror("skill_invocation", {
                "skill": meta.get("skill"),
                "success": bool(success),
                "session_id": meta.get("session"),
            })
        except Exception:  # noqa: BLE001 -- never break the close
            pass
    sql = (
        f"UPDATE {inv_id} SET ended_at = time::now(), "
        f"success = {str(bool(success)).lower()};"
    )
    await _db_post(sql)

async def _skill_attribute_tool_call(inv_id: Optional[str],
                                     tool_call_id: Optional[str],
                                     step_index: int) -> None:
    """RELATE the tool_call back to the skill_invocation so the
    miner subtracts skill-emitted runs from future candidate
    populations (Phase C.2 closes the loop on its own output)."""
    if not inv_id or not tool_call_id:
        return
    row_id = _pg_row_id(inv_id)
    if row_id is not None:
        await _pg_attribute_tool_call(row_id, tool_call_id, step_index)
    sql = (
        f"RELATE {inv_id}->emitted->{tool_call_id} "
        f"SET step_index = {int(step_index)};"
    )
    await _db_post(sql)

def _slug_for_skill(query: str) -> str:
    """Stable, filesystem-safe slug from the user query. Length-capped so a
    long prompt doesn't blow past max-filename on tmpfs."""
    s = (query or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s[:60] or "skill"

def _render_skill_md(query: str, answer: str,
                     tool_history: Optional[list],
                     session_id: Optional[str]) -> str:
    """Render a self-contained SKILL.md (operator brief L6 'closed-loop self-
    learning'): YAML frontmatter (re-usable by OpenViking-style L0/L1/L2 +
    Obsidian) + Goal + Workflow (per-tool-call line) + Outcome. Kept compact
    so the file fits a single tokenizer window when the next similar query
    recalls it as exemplar context."""
    th = [t for t in (tool_history or []) if isinstance(t, dict)]
    verbs = sorted({str(t.get("tool", "")) for t in th if t.get("tool")})
    front = [
        "---",
        f"name: skill-{int(time.time())}-{_slug_for_skill(query)}",
        f"ts: {_a2a_now()}",
        "source: episodic",
        f"session: {session_id or 'unknown'}",
        f"verbs_used: [{', '.join(verbs)}]",
        f"goal: {(query or '').strip()[:200]!r}",
        "---",
        "",
    ]
    body = [
        "# Goal",
        (query or "").strip(),
        "",
        "# Workflow",
    ]
    if not th:
        body.append("(answer produced without explicit tool calls)")
    else:
        for i, t in enumerate(th, 1):
            tool = str(t.get("tool", "?"))
            args = t.get("args") or {}
            try:
                arg_repr = json.dumps(args, default=str)[:200]
            except Exception:  # noqa: BLE001
                arg_repr = str(args)[:200]
            body.append(f"{i}. `{tool}` with args {arg_repr}")
    body += [
        "",
        "# Outcome",
        (answer or "").strip()[:4000],
        "",
        "# Re-use",
        ("This run is recorded as episodic memory; semantic-recall via "
         "the knowledge table surfaces it when a similar query lands. "
         "Treat as prior work, NOT fresh ground truth."),
    ]
    return "\n".join(front) + "\n".join(body) + "\n"

def _write_skill_md_fire(*, query: str, answer: str,
                         tool_history: Optional[list] = None,
                         session_id: Optional[str] = None) -> None:
    """Fire-and-forget SKILL.md write to _SKILLS_EPISODIC_DIR. Never raises --
    a write failure is logged + ignored; the answer is already returned."""
    try:
        if _turn_volatile_var is not None and _turn_volatile_var.get(False):
            log.info("skill md: skipped volatile/point-in-time facts; no durable learned skill")
            return
    except Exception:
        log.warning("skill md: volatility classification unavailable; skipping durable write")
        return
    if not _SKILLS_EPISODIC_ENABLED:
        log.info("skill md: disabled by env")
        return
    q = (query or "").strip()
    a = (answer or "").strip()
    if not q or not a:
        log.info("skill md: empty q=%s a=%s -> skip",
                 bool(q), bool(a))
        return
    try:
        os.makedirs(_SKILLS_EPISODIC_DIR, exist_ok=True)
        slug = _slug_for_skill(q)
        ts = time.strftime("%Y-%m-%dT%H-%M-%S")
        fname = f"{ts}-{slug}.md"
        path = os.path.join(_SKILLS_EPISODIC_DIR, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(_render_skill_md(q, a, tool_history, session_id))
        log.info("skill md: wrote %s", path)
    except Exception as e:  # noqa: BLE001
        log.warning("skill md write skipped: %s", e)
