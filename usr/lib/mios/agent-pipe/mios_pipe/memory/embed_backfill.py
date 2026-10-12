# AI-hint: WS-A2 embedding-version hygiene -- the pure decision core for an off-hot-path re-embed (backfill) job.
# AI-doc: usr/share/doc/mios/manual/memory.md

from __future__ import annotations

from typing import List, Optional, Tuple

def needs_reembed(has_emb: bool, row_version: Optional[str],
                  current_version: str) -> bool:
    """True if a row should be re-embedded: it HAS a vector but its emb_version
    differs from (or predates -- NULL) the current identity. A row with no
    vector is left for the normal embed-on-write path, not the backfill."""
    if not has_emb:
        return False
    rv = (row_version or "").strip()
    cur = (current_version or "").strip()
    return rv != cur

def select_candidates_sql(table: str, current_version: str,
                          limit: int = 500) -> Tuple[str, dict]:
    """Parameterized SELECT for rows whose vector is stale under the current
    emb_version (emb present AND emb_version IS DISTINCT FROM current). Returns
    (sql, params). `table` is validated against a small allow-list by the caller;
    here it is interpolated as an identifier ONLY after that check (never user
    input)."""
    lim = max(1, int(limit))
    sql = (
        f"SELECT id FROM {table} "
        f"WHERE emb IS NOT NULL "
        f"AND (emb_version IS DISTINCT FROM %(ver)s) "
        f"ORDER BY id "
        f"LIMIT %(lim)s"
    )
    return sql, {"ver": str(current_version), "lim": lim}

def stamp_version_sql(table: str) -> str:
    """Parameterized UPDATE that writes a freshly-computed vector + the current
    identity onto one row. Caller binds %(emb)s/%(model)s/%(ver)s/%(id)s."""
    return (
        f"UPDATE {table} "
        f"SET emb = %(emb)s, emb_model = %(model)s, emb_version = %(ver)s "
        f"WHERE id = %(id)s"
    )

def plan_batches(ids: List, batch_size: int = 50) -> List[list]:
    """Split a candidate id list into bounded batches so the backfill embeds +
    writes in chunks (never one giant transaction, never one-at-a-time)."""
    bs = max(1, int(batch_size))
    return [list(ids[i:i + bs]) for i in range(0, len(ids), bs)]

def summarize(candidate_count: int, batch_size: int = 50) -> dict:
    """A cheap plan summary for logging / the /v1/scheduler diagnostics."""
    bs = max(1, int(batch_size))
    n = max(0, int(candidate_count))
    return {
        "candidates": n,
        "batch_size": bs,
        "batches": (n + bs - 1) // bs,
    }

import json
import logging
import os
import asyncio
import httpx
import mios_pg as _mios_pg

log = logging.getLogger("mios-agent-pipe")

PK_MAP = {
    "skill": "id",
    "verb": "name",
    "tool_call": "id",
    "directory_entry": "id",
    "event": "id",
    "session": "id",
    "config_kv": "id",
    "account_preference": ("account_id", "layer", "key"),
    "feature_set": "id",
    # task_979. system_logs is embeddable (emb vector(768) + an HNSW index) and has
    # `id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY`. It was in neither list,
    # which check_backfill_coverage has been failing on at every bake.
    "system_logs": "id",
    "threat_events": "id"
}

_BACKFILL_EXEMPT = [
    "knowledge",
    "agent_memory",
    "package_set",
    "build_recipe",
    "build_phase",
    "xbox_feature",
    "debloat_policy",
    "debloat_profile",
    "preset",
    "appx_removal",
    "feature_removal",
    "capability_removal",
    "component_removal",
    "person_pref",
    "mios_rag"
]

def get_embed_config() -> Tuple[str, str]:
    light_port = os.environ.get("MIOS_PORTS_LLM_LIGHT") or "8500"
    light_base = f"http://localhost:{light_port}"
    url = os.environ.get("MIOS_VERB_EMBED_URL") or f"{light_base}/v1/embeddings"
    model = os.environ.get("MIOS_AI_EMBED_MODEL") or "nomic-embed-text"
    return url, model

def get_text_projection(table: str, row: dict) -> Optional[str]:
    if table == "skill":
        name = row.get("name") or ""
        desc = row.get("description") or ""
        return f"Skill: {name}\nDescription: {desc}".strip() or None
    elif table == "verb":
        facing = row.get("model_name") or row.get("name") or ""
        desc = row.get("desc_default") or ""
        txt = f"{facing}: {desc}"
        examples = row.get("examples")
        if examples:
            if isinstance(examples, str):
                try:
                    examples = json.loads(examples)
                except Exception:
                    pass
            if isinstance(examples, list):
                txt += "\nExample requests: " + " | ".join(str(e) for e in examples if str(e).strip())
        return txt.strip() or None
    elif table == "tool_call":
        tool = row.get("tool") or ""
        args = row.get("args") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                pass
        result = row.get("result_preview") or row.get("output") or ""
        return f"Tool Call: {tool}\nArguments: {json.dumps(args)}\nResult: {result}".strip() or None
    elif table == "directory_entry":
        path = row.get("path") or ""
        kind = row.get("kind") or ""
        size = row.get("size") or 0
        summary = row.get("summary") or ""
        return f"File: {path}\nKind: {kind}\nSize: {size} bytes\nSummary: {summary}".strip() or None
    elif table == "event":
        act_type = row.get("act_type") or ""
        summary = row.get("summary") or ""
        return f"Event: {act_type}\nSummary: {summary}".strip() or None
    elif table == "session":
        meta = row.get("meta") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        title = row.get("title") or meta.get("title") or ""
        first_prompt = row.get("first_prompt") or meta.get("first_prompt") or meta.get("first_user_text") or ""
        return f"Session Title: {title}\nPrompt: {first_prompt}".strip() or None
    elif table == "config_kv":
        scope = row.get("scope") or ""
        key = row.get("key") or ""
        desc = row.get("description") or ""
        val = row.get("value")
        val_str = json.dumps(val) if val is not None else ""
        return f"Config [{scope}.{key}]: {desc}\nValue: {val_str}".strip() or None
    elif table == "account_preference":
        key = row.get("key") or ""
        val = row.get("value")
        val_str = json.dumps(val) if val is not None else ""
        return f"Preference {key}: {val_str}".strip() or None
    elif table == "feature_set":
        name = row.get("name") or ""
        desc = row.get("description") or ""
        return f"Feature: {name}\nDescription: {desc}".strip() or None
    elif table == "threat_events":
        return (row.get("description") or "").strip() or None
    elif table == "system_logs":
        # Byte-identical to what usr/libexec/mios/log/mios-log-streamer embeds at
        # insert time: `f"[{unit}] {message}"`. A backfilled row and a streamed
        # row must land in the same place in the vector space, or the HNSW index
        # answers differently depending on which path wrote the row (task_979).
        unit = row.get("unit") or ""
        message = row.get("message") or ""
        return f"[{unit}] {message}".strip() or None
    return None

async def embed_text_with_retry(client: httpx.AsyncClient, text: str, url: str, model: str, prefix: str = "search_document: ") -> Optional[list[float]]:
    if prefix and not text.startswith(prefix):
        text = prefix + text
    backoff = 1.0
    for attempt in range(3):
        try:
            r = await client.post(
                url,
                json={"model": model, "input": text},
                headers={"Content-Type": "application/json"},
                timeout=10.0
            )
            if r.status_code == 200:
                data = r.json()
                v = data.get("embedding")
                if v is None:
                    _d = data.get("data")
                    if isinstance(_d, list) and _d:
                        v = _d[0].get("embedding")
                if isinstance(v, list) and v:
                    return [float(x) for x in v]
            log.warning("Embedding attempt %d failed with status %d", attempt + 1, r.status_code)
        except Exception as e:
            log.warning("Embedding attempt %d raised exception: %s", attempt + 1, e)

        if attempt < 2:
            await asyncio.sleep(backoff)
            backoff *= 2.0
    return None

async def run_backfill(current_version: str = "nomic-768-v1") -> dict:
    url, model = get_embed_config()
    results = {}

    for table, pk in PK_MAP.items():
        cols_map = {
            "skill": "id, name, description",
            "verb": "name, desc_default, examples, model_name",
            "tool_call": "id, tool, args, result_preview, output",
            "directory_entry": "id, path, kind, size, summary",
            "event": "id, act_type, summary",
            "session": "id, meta",
            "config_kv": "id, scope, key, description, value",
            "account_preference": "account_id, layer, key, value",
            "feature_set": "id, name, description",
            "system_logs": "id, unit, message",
            "threat_events": "id, description"
        }
        # A bare cols_map[table] raised KeyError deep inside the loop the moment
        # system_logs joined PK_MAP, taking the whole backfill down. A table can
        # be registered without its projection; say which one and keep going for
        # the rest rather than aborting every table (task_979).
        cols = cols_map.get(table)
        if cols is None:
            log.error("PK_MAP has %s but cols_map does not -- skipping, and this is a bug", table)
            results[table] = 0
            continue

        order_col = pk if isinstance(pk, str) else ", ".join(pk)

        table_ver = current_version
        if table == "verb":
            try:
                from mios_pipe.routing.toolsearch import _verb_embed_fingerprint, configure as ts_configure
                from mios_pipe.routing.verbcatalog import _load_verb_catalog
                cat = _load_verb_catalog()
                if cat:
                    ts_configure(verb_catalog=cat)
                table_ver = _verb_embed_fingerprint()
            except Exception as e_fp:
                log.debug("Failed to get verb catalog fingerprint: %s", e_fp)

        select_query = (
            f"SELECT {cols} FROM {table} "
            f"WHERE emb IS NULL OR (emb_version IS DISTINCT FROM %(ver)s) "
            f"ORDER BY {order_col} LIMIT 500"
        )
        try:
            rows = await _mios_pg.execute(select_query, {"ver": table_ver}, fetch=True)
        except Exception as e:
            log.warning("Failed to fetch backfill candidates for table %s: %s", table, e)
            continue

        if not rows:
            results[table] = 0
            continue

        embedded_count = 0
        async with httpx.AsyncClient() as client:
            for row in rows:
                pk_val = row[pk] if isinstance(pk, str) else tuple(row[k] for k in pk)
                txt = get_text_projection(table, row)
                if not txt:
                    continue

                vec = await embed_text_with_retry(client, txt, url, model)
                if vec is None:
                    log.warning("Failed to embed text for %s row %s", table, pk_val)
                    continue

                where_clause = f"WHERE {pk} = %(id)s" if isinstance(pk, str) else f"WHERE " + " AND ".join(f"{k} = %({k})s" for k in pk)
                update_query = (
                    f"UPDATE {table} "
                    f"SET emb = %(emb)s::vector, emb_model = %(model)s, emb_version = %(ver)s "
                    f"{where_clause}"
                )

                params = {
                    "emb": vec,
                    "model": model,
                    "ver": table_ver,
                }
                if isinstance(pk, str):
                    params["id"] = pk_val
                else:
                    for k in pk:
                        params[k] = row[k]

                try:
                    res = await _mios_pg.execute(update_query, params, fetch=False)
                    if res is not None:
                        embedded_count += 1
                except Exception as e:
                    log.warning("Failed to write embedding for %s row %s: %s", table, pk_val, e)

        results[table] = embedded_count
        log.info("Completed embedding backfill for table %s: %d rows updated", table, embedded_count)

    return results

async def main():
    log.info("Starting embedding backfill worker...")
    res = await run_backfill("nomic-768-v1")
    log.info("Backfill completed. Results: %s", res)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
