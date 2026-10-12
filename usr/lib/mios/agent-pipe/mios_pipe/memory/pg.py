# AI-hint: Provides a PostgreSQL and pgvector client for the agent plane (WS-9), offering a standalone, SQL-injection-safe datastore client using parameter...
# AI-doc: usr/share/doc/mios/manual/memory.md

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Mapping, Optional

log = logging.getLogger("mios-agent-pipe")

_pg_down_until = 0.0
_PG_BACKOFF_S = 30.0

_REDACT_CFG: dict[str, Any] = {}

def _redact_cfg() -> dict[str, Any]:
    """[security.redact] from the mios.toml cascade, read once and cached."""
    global _REDACT_CFG
    if not _REDACT_CFG:
        try:
            from mios_pipe.kernel.config import _toml_section
            _REDACT_CFG = (_toml_section("security") or {}).get("redact", {}) or {}
        except Exception:  # noqa: BLE001 -- degrade to the built-in floor below
            _REDACT_CFG = {}
        if not _REDACT_CFG.get("tables"):
            # Floor so an unreadable SSOT never turns redaction off.
            _REDACT_CFG = {"enable": True, "fail_closed": True,
                           "tables": ["knowledge", "agent_memory", "event", "tool_call"]}
    return _REDACT_CFG

def _redact_targets(sql: str) -> list[str]:
    """Tables named in this statement that [security.redact].tables covers."""
    cfg = _redact_cfg()
    if not cfg.get("enable", True):
        return []
    low = (sql or "").lower()
    return [t for t in cfg.get("tables", []) if t in low]

def _redact_fail_closed() -> bool:
    return bool(_redact_cfg().get("fail_closed", True))

def _pg_skip() -> bool:
    return time.monotonic() < _pg_down_until

def _pg_mark_down() -> None:
    global _pg_down_until
    _pg_down_until = time.monotonic() + _PG_BACKOFF_S

def rid_to_pg_id(rid: Any) -> "Optional[int]":
    if rid is None:
        return None
    try:
        tail = str(rid).split(":")[-1].strip()
        return int(tail)
    except (TypeError, ValueError):
        return None

def pg_config(env: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
    """Resolve connection settings from the environment (already layered from
    mios.toml by userenv.sh). Local-only defaults match the quadlet."""
    e = env if env is not None else os.environ
    return {
        "host": e.get("MIOS_PGVECTOR_HOST", "localhost"),
        "port": int(e.get("MIOS_PORTS_PGVECTOR", "8600") or 8600),
        "user": e.get("MIOS_PGVECTOR_USER", "mios"),
        "password": e.get("MIOS_PGVECTOR_PASS", "mios"),
        "dbname": e.get("MIOS_PGVECTOR_DB", "mios"),
    }

def dsn(cfg: Optional[dict[str, Any]] = None) -> str:
    """Build a libpq connection URI from a config dict (or the env)."""
    c = cfg or pg_config()
    return (f"postgresql://{c['user']}:{c['password']}"
            f"@{c['host']}:{c['port']}/{c['dbname']}")

def vector_literal(vec: Any) -> str:
    """Format a float sequence as a pgvector text literal: '[0.1,0.2,...]'.
    (psycopg binds this to a `vector` column via the `::vector` cast.)"""
    return "[" + ",".join(repr(float(x)) for x in (vec or [])) + "]"

def build_insert(table: str, fields: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """`INSERT INTO <table> (cols) VALUES (%(col)s, ...)` -- never interpolates
    values. `emb` (if a list) is bound as a pgvector via the ::vector cast."""
    cols = list(fields.keys())
    params: dict[str, Any] = {}
    placeholders = []
    for c in cols:
        v = fields[c]
        if c == "emb" and isinstance(v, (list, tuple)):
            params[c] = vector_literal(v)
            placeholders.append(f"%({c})s::vector")
        elif isinstance(v, (dict, list, tuple)):
            params[c] = json.dumps(v, default=str)
            placeholders.append(f"%({c})s::jsonb")
        else:
            params[c] = v
            placeholders.append(f"%({c})s")
    sql = (f"INSERT INTO {table} (" + ", ".join(cols) + ") VALUES ("
           + ", ".join(placeholders) + ") RETURNING id;")
    return sql, params

def build_recall(table: str = "knowledge", k: int = 3,
                 owner: Optional[str] = None,
                 emb_version: Optional[str] = None) -> tuple[str, dict[str, Any]]:
    if table == "agent_memory":
        proj = "mem_key AS id, fact, scope, source, ts"
    elif table == "mios_rag":
        proj = "id, source, content"
    else:  # knowledge (default) -- + ts/last_access for the recall recency decay
        proj = "id, q, answer, tier, satisfied, access_count, ts, last_access"
    where = "emb IS NOT NULL"
    params: dict[str, Any] = {"qvec": None, "k": int(k)}  # caller sets qvec = vector_literal(q)
    if owner is not None:
        where += " AND (owner_user = %(owner)s OR owner_user IS NULL)"
        params["owner"] = owner
    if emb_version and table in ("knowledge", "agent_memory"):
        where += " AND (emb_version = %(emb_version)s OR emb_version IS NULL)"
        params["emb_version"] = emb_version
    sql = (
        f"SELECT {proj}, "
        f"1 - (emb <=> %(qvec)s::vector) AS score "
        f"FROM {table} WHERE {where} "
        f"ORDER BY emb <=> %(qvec)s::vector LIMIT %(k)s;"
    )
    return sql, params

def build_fts_query(table: str = "knowledge", k: int = 3,
                    owner: Optional[str] = None,
                    emb_version: Optional[str] = None) -> tuple[str, dict[str, Any]]:
    """Postgres FTS ranked recall query builder for hybrid dense+sparse retrieval."""
    if table == "agent_memory":
        proj = "mem_key AS id, fact, scope, source, ts"
        fts_expr = "to_tsvector('simple', coalesce(fact, '') || ' ' || coalesce(scope, ''))"
    elif table == "mios_rag":
        proj = "id, source, content"
        fts_expr = "to_tsvector('simple', coalesce(content, ''))"
    else:  # knowledge (default)
        proj = "id, q, answer, tier, satisfied, access_count, ts, last_access"
        fts_expr = "fts"

    where = f"{fts_expr} @@ plainto_tsquery('simple', %(query_text)s)"
    params: dict[str, Any] = {"query_text": None, "k": int(k)}
    if owner is not None:
        where += " AND (owner_user = %(owner)s OR owner_user IS NULL)"
        params["owner"] = owner
    if emb_version and table in ("knowledge", "agent_memory"):
        where += " AND (emb_version = %(emb_version)s OR emb_version IS NULL)"
        params["emb_version"] = emb_version

    sql = (
        f"SELECT {proj}, "
        f"ts_rank({fts_expr}, plainto_tsquery('simple', %(query_text)s)) AS score "
        f"FROM {table} WHERE {where} "
        f"ORDER BY score DESC LIMIT %(k)s;"
    )
    return sql, params

async def rerank_candidates(query: str, candidates: list[dict[str, Any]], table: str) -> list[dict[str, Any]]:
    """Query a local cross-encoder model to re-score/re-rank retrieved documents."""
    if not candidates:
        return []

    def get_candidate_text(r: dict[str, Any]) -> str:
        if table == "knowledge":
            return f"Q: {r.get('q', '')} A: {r.get('answer', '')}"
        elif table == "mios_rag":
            return str(r.get("content") or "")
        else:
            return str(r.get("fact") or r.get("answer") or "")

    docs = [get_candidate_text(r) for r in candidates]
    light_port = os.environ.get("MIOS_PORTS_LLM_LIGHT") or "8500"
    url = os.environ.get("MIOS_RERANK_URL") or f"http://localhost:{light_port}/v1/rerank"
    model = os.environ.get("MIOS_RERANK_MODEL") or "bge-reranker-v2-m3"

    payload = {
        "model": model,
        "query": query,
        "documents": docs,
        "top_n": len(candidates)
    }

    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                alt_url = f"http://localhost:{light_port}/rerank"
                resp = await client.post(alt_url, json=payload)

            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results") or []
                ranked_candidates = []
                for res in results:
                    idx = res.get("index")
                    score = res.get("relevance_score")
                    if idx is not None and 0 <= idx < len(candidates):
                        cand = candidates[idx].copy()
                        cand["score"] = score
                        ranked_candidates.append(cand)
                if ranked_candidates:
                    return ranked_candidates
    except Exception as e:
        log.warning("Cross-encoder reranking failed, falling open: %s", e)

    return candidates

def recall_tuning(ef_search: int = 100) -> str:
    """Per-query HNSW recall/speed knob; run before the recall SELECT."""
    return f"SET hnsw.ef_search = {int(ef_search)};"

_RLS_OWNER_GUC = "mios.owner_user"

def rls_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """DB-side Row-Level-Security ENFORCEMENT toggle (SSOT [pgvector].rls_enable ->
    MIOS_PGVECTOR_RLS_ENABLE, bridged by userenv.sh). DEFAULT FALSE: no SET LOCAL is
    emitted, the schema policies stay permissive, and every executed statement is
    byte-identical to the pre-RLS path. Reads the env per call (like pg_config) so a
    live mios.toml edit + mios-sync-env takes effect without a code change."""
    e = env if env is not None else os.environ
    return str(e.get("MIOS_PGVECTOR_RLS_ENABLE", "") or "").strip().lower() in {
        "1", "true", "yes", "on"}

def build_set_owner(owner: str) -> tuple[str, dict[str, Any]]:
    """Parameterized statement that scopes THIS transaction's RLS GUC to ``owner``:
    ``SELECT set_config('mios.owner_user', %(owner)s, true)``. is_local=true gives
    SET LOCAL semantics (transaction-scoped, so it never leaks to a later request on
    a reused/pooled connection). BOTH the GUC name and the owner are BOUND (never
    string-spliced), mirroring the WS-A3 extended-protocol binding precedent."""
    return ("SELECT set_config(%(guc)s, %(owner)s, true)",
            {"guc": _RLS_OWNER_GUC, "owner": str(owner)})

def _principal_enforced() -> bool:
    try:
        import mios_grounding
        fn = getattr(mios_grounding, "_principal_bind_mode", None)
        return bool(callable(fn) and fn() == "enforce")
    except Exception:  # noqa: BLE001 -- unresolvable bind mode -> treat as unverified
        return False

_RLS_UNVERIFIED_WARNED = False

def _warn_rls_unverified_once() -> None:
    global _RLS_UNVERIFIED_WARNED
    if _RLS_UNVERIFIED_WARNED:
        return
    _RLS_UNVERIFIED_WARNED = True
    log.warning(
        "[pgvector].rls_enable is on but [security].principal_bind_mode != enforce -- "
        "the owner principal is unverified/spoofable, so RLS is NOT applied; set "
        "principal_bind_mode=enforce for real per-tenant isolation")

def rls_fail_closed(env: Optional[Mapping[str, str]] = None) -> bool:
    """DB-side RLS fail-closed toggle (SSOT [pgvector].rls_fail_closed ->
    MIOS_PGVECTOR_RLS_FAIL_CLOSED). When true, unauthenticated queries or queries with
    unverified principals fail closed by binding an unauthenticated principal, preventing
    unauthorized reads of tenant rows."""
    e = env if env is not None else os.environ
    return str(e.get("MIOS_PGVECTOR_RLS_FAIL_CLOSED", "") or "").strip().lower() in {
        "1", "true", "yes", "on"}

def _owner_scope(rls_owner: Optional[str],
                 env: Optional[Mapping[str, str]] = None,
                 fail_closed: Optional[bool] = None) -> Optional[tuple[str, dict[str, Any]]]:
    fc = fail_closed if fail_closed is not None else rls_fail_closed(env)
    owner = str(rls_owner).strip() if rls_owner is not None else ""
    if not rls_enabled(env):
        return None
    if not owner:
        if fc:
            return build_set_owner("__unauthenticated__")
        return None
    if not _principal_enforced():
        if fc:
            return build_set_owner("__unauthenticated__")
        _warn_rls_unverified_once()
        return None
    return build_set_owner(owner)

def pool_config(env: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
    """Resolve the opt-in pool settings (SSOT [pgvector].pool_* -> MIOS_PG_POOL_*
    via userenv.sh). Read per call (like rls_enabled) so a live mios.toml edit +
    mios-sync-env takes effect. enable default FALSE -> the per-call connect path."""
    e = env if env is not None else os.environ
    enable = str(e.get("MIOS_PGVECTOR_POOL_ENABLE", "") or "").strip().lower() in {
        "1", "true", "yes", "on"}
    try:
        pmin = max(0, int(e.get("MIOS_PGVECTOR_POOL_MIN", "0") or 0))
    except (TypeError, ValueError):
        pmin = 0
    try:
        pmax = max(1, int(e.get("MIOS_PGVECTOR_POOL_MAX", "8") or 8))
    except (TypeError, ValueError):
        pmax = 8
    return {"enable": enable, "min": min(pmin, pmax), "max": pmax}

async def _open_conn(cfg: Optional[dict[str, Any]] = None) -> Any:
    """Open ONE AsyncConnection with the SAME args as the historic per-call path,
    so a pooled connection is indistinguishable from a direct one (only its
    lifetime differs: reused vs closed)."""
    import psycopg  # lazy: only at cutover, never for the pure helpers
    return await psycopg.AsyncConnection.connect(
        dsn(cfg), autocommit=True, connect_timeout=5)

class AsyncConnPool:

    def __init__(self, *, min_size: int = 0, max_size: int = 8,
                 cfg: Optional[dict[str, Any]] = None) -> None:
        self._min = max(0, int(min_size))
        self._max = max(1, int(max_size))
        self._cfg = cfg
        self._free: list[Any] = []
        self._size = 0            # total live (idle + checked-out)
        self._lock = asyncio.Lock()
        self._warm = False

    @staticmethod
    def _is_live(conn: Any) -> bool:
        return bool(conn) and not getattr(conn, "closed", False) \
            and not getattr(conn, "broken", False)

    @staticmethod
    async def _close(conn: Any) -> None:
        try:
            await conn.close()
        except BaseException:     # noqa: BLE001 -- best-effort teardown
            pass

    async def _clean_for_reuse(self, conn: Any) -> bool:
        """Return the connection to a clean session so it is safe to reuse: roll back
        any transaction left open or aborted (this ALSO discards any transaction-
        scoped SET LOCAL -- the RLS owner GUC -- so it cannot leak to the next
        checkout). True iff still live afterwards; False -> the caller discards it."""
        try:
            status = getattr(getattr(conn, "info", None), "transaction_status", 0)
            if status:            # non-IDLE: a txn/error was left open -> reset it
                await conn.rollback()
            return bool(self._is_live(conn))
        except BaseException:     # noqa: BLE001 -- cannot verify clean -> do not reuse
            return False

    async def _ensure_warm(self, cfg: Optional[dict[str, Any]] = None) -> None:
        """Lazily pre-open up to `min` idle connections on first use. Best-effort:
        a connect failure stops warm-up (degrade-open) and the pool grows on
        demand instead."""
        if self._warm:
            return
        self._warm = True
        target = min(self._min, self._max)
        while True:
            async with self._lock:
                if self._size >= target:
                    return
                self._size += 1
            try:
                conn = await _open_conn(cfg if cfg is not None else self._cfg)
            except BaseException:  # noqa: BLE001 -- degrade-open: stop warming
                async with self._lock:
                    self._size -= 1
                return
            async with self._lock:
                self._free.append(conn)

    async def acquire(self, cfg: Optional[dict[str, Any]] = None) -> tuple[Any, bool]:
        """Check out a connection. Returns (conn, pooled): pooled=True MUST be
        returned via release(); pooled=False is a degrade-open ephemeral connection
        the caller closes. Reuses a live idle connection, else grows up to max, else
        (exhausted) hands back an ephemeral direct connection (never blocks)."""
        await self._ensure_warm(cfg)
        async with self._lock:
            while self._free:
                conn = self._free.pop()
                if self._is_live(conn):
                    return conn, True
                self._size -= 1                # drop a dead idle connection
            grow = self._size < self._max
            if grow:
                self._size += 1
        if grow:
            try:
                conn = await _open_conn(cfg if cfg is not None else self._cfg)
            except BaseException:               # noqa: BLE001 -- undo the reservation
                async with self._lock:
                    self._size -= 1
                raise
            return conn, True
        return await _open_conn(cfg if cfg is not None else self._cfg), False

    async def release(self, conn: Any, pooled: bool, ok: bool = True) -> None:
        """Return a checked-out connection. A healthy, cleanly-finished POOLED
        connection is cleaned + put back on the free-list; an ephemeral (degrade)
        connection or a broken/errored one is closed (and a pooled discard frees its
        slot)."""
        if conn is None:
            return
        if not pooled:                          # ephemeral degrade conn -> always close
            await self._close(conn)
            return
        if ok and self._is_live(conn) and await self._clean_for_reuse(conn):
            async with self._lock:
                self._free.append(conn)
            return
        await self._close(conn)                 # broken / dirty / errored -> discard
        async with self._lock:
            self._size -= 1

    async def closeall(self) -> None:
        """Close every idle connection + drop the pool (graceful teardown / tests)."""
        async with self._lock:
            conns, self._free, self._size, self._warm = self._free, [], 0, False
        for c in conns:
            await self._close(c)

_POOL: Optional[AsyncConnPool] = None

def _get_pool(env: Optional[Mapping[str, str]] = None, cfg: Optional[dict[str, Any]] = None) -> Optional[AsyncConnPool]:
    """The process-wide pool when [pgvector].pool_enable is on, else None (-> the
    byte-identical per-call connect path). Lazily created with the SSOT
    pool_min/pool_max on first enabled use; the flag is read per call so a live
    mios.toml edit + mios-sync-env takes effect without a restart."""
    global _POOL
    pc = pool_config(env)
    if not pc["enable"]:
        return None
    if _POOL is None:
        _POOL = AsyncConnPool(min_size=pc["min"], max_size=pc["max"], cfg=cfg)
    return _POOL

@asynccontextmanager
async def _conn(cfg: Optional[dict[str, Any]] = None) -> AsyncIterator[Any]:
    import psycopg  # lazy; execute()/recall() already guarded its presence
    pool = None
    try:
        pool = _get_pool(cfg=cfg)
    except BaseException:          # noqa: BLE001 -- a pool misconfig never breaks a query
        pool = None
    if pool is None:
        async with await psycopg.AsyncConnection.connect(
                dsn(cfg), autocommit=True, connect_timeout=5) as conn:
            yield conn
        return
    try:
        conn, pooled = await pool.acquire(cfg)
    except BaseException:          # noqa: BLE001 -- exhausted/broken -> direct connect
        async with await psycopg.AsyncConnection.connect(
                dsn(cfg), autocommit=True, connect_timeout=5) as conn:
            yield conn
        return
    ok = True
    try:
        yield conn
    except BaseException:
        ok = False
        raise
    finally:
        await pool.release(conn, pooled, ok)

async def execute(sql: str, params: Optional[Any] = None,
                  *, fetch: bool = False, cfg: Optional[dict[str, Any]] = None,
                  rls_owner: Optional[str] = None,
                  fail_closed: Optional[bool] = None) -> Any:
    if _pg_skip():
        return None

    if params and _redact_targets(sql):
        try:
            from mios_pipe.redact import redact
            if isinstance(params, dict):
                new_params = {}
                for k, v in params.items():
                    if isinstance(v, str):
                        redacted_val, _ = redact(v)
                        new_params[k] = redacted_val
                    else:
                        new_params[k] = v
                params = new_params
            elif isinstance(params, (list, tuple)):
                new_params_list = []
                for v in params:
                    if isinstance(v, str):
                        redacted_val, _ = redact(v)
                        new_params_list.append(redacted_val)
                    else:
                        new_params_list.append(v)
                params = type(params)(new_params_list)
        except Exception as e:  # noqa: BLE001
            if _redact_fail_closed():
                log.error("redact failed for %s; refusing the write (fail_closed): %s",
                          _redact_targets(sql), e)
                return None
            log.warning("redact failed; writing UNREDACTED (fail_closed=false): %s", e)

    try:
        import psycopg  # noqa: F401 -- lazy: only needed at cutover, not for the pure helpers
        from psycopg.rows import dict_row
    except Exception:  # noqa: BLE001 -- psycopg not installed (pre-cutover)
        return None
    try:
        scope = _owner_scope(rls_owner, fail_closed=fail_closed)
        async with _conn(cfg) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                if scope is not None:
                    async with conn.transaction():
                        await cur.execute(scope[0], scope[1])
                        await cur.execute(sql, params or {})
                        return await cur.fetchall() if fetch else True
                await cur.execute(sql, params or {})
                if fetch:
                    return await cur.fetchall()
                return True
    except Exception:  # noqa: BLE001 -- degrade-open
        _pg_mark_down()
        return None

_COLS_CACHE: dict[str, set[str]] = {}

async def _table_columns(table: str, *, cfg: Optional[dict[str, Any]] = None) -> set[str]:
    """Cached set of a table's column names (information_schema). Lets insert()
    drop fields the live schema doesn't have, so code<->schema drift degrades to
    a PARTIAL row instead of a silent total failure (+ a 30s backoff that would
    poison every other table's mirror too)."""
    if table in _COLS_CACHE:
        return set(_COLS_CACHE[table])
    rows = await execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %(t)s",
        {"t": table}, fetch=True, cfg=cfg)
    cols = {str(r["column_name"]) for r in (rows or []) if r.get("column_name")}
    if cols:
        _COLS_CACHE[table] = cols
    return set(cols)

async def insert(table: str, fields: dict[str, Any], *, cfg: Optional[dict[str, Any]] = None,
                 rls_owner: Optional[str] = None,
                 fail_closed: Optional[bool] = None) -> Any:
    cols = await _table_columns(table, cfg=cfg)
    if cols:
        fields = {k: v for k, v in fields.items() if k in cols}
    if not fields:
        return None
    sql, params = build_insert(table, fields)
    return await execute(sql, params, fetch=False, cfg=cfg, rls_owner=rls_owner, fail_closed=fail_closed)

async def recall(qvec: Any, *, table: str = "knowledge", k: int = 3,
                 ef_search: int = 100, owner: Optional[str] = None,
                 emb_version: Optional[str] = None,
                 cfg: Optional[dict[str, Any]] = None,
                 rls_owner: Optional[str] = None,
                 query_text: Optional[str] = None,
                 hybrid: bool = False,
                 rerank: bool = False,
                 fail_closed: Optional[bool] = None) -> list[dict[str, Any]]:
    if _pg_skip():
        return []
    try:
        import psycopg  # noqa: F401
        from psycopg.rows import dict_row
    except Exception:  # noqa: BLE001
        return []
    try:
        scope = _owner_scope(rls_owner, fail_closed=fail_closed)
        dense_rows: list[dict[str, Any]] = []
        sparse_rows: list[dict[str, Any]] = []

        fetch_k = max(k * 4, 60) if (hybrid or rerank) else k
        sql, params = build_recall(table, fetch_k, owner=owner, emb_version=emb_version)
        params["qvec"] = vector_literal(qvec)
        async with _conn(cfg) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                if scope is not None:
                    async with conn.transaction():
                        await cur.execute(scope[0], scope[1])
                        await cur.execute(recall_tuning(ef_search))
                        await cur.execute(sql, params)
                        dense_rows = await cur.fetchall()
                else:
                    await cur.execute(recall_tuning(ef_search))
                    await cur.execute(sql, params)
                    dense_rows = await cur.fetchall()

        if hybrid and query_text:
            try:
                fts_sql, fts_params = build_fts_query(table, fetch_k, owner=owner, emb_version=emb_version)
                fts_params["query_text"] = query_text
                async with _conn(cfg) as conn:
                    async with conn.cursor(row_factory=dict_row) as cur:
                        if scope is not None:
                            async with conn.transaction():
                                await cur.execute(scope[0], scope[1])
                                await cur.execute(fts_sql, fts_params)
                                sparse_rows = await cur.fetchall()
                        else:
                            await cur.execute(fts_sql, fts_params)
                            sparse_rows = await cur.fetchall()
            except Exception as e:
                log.warning("FTS sparse query failed (degrade-open to dense-only): %s", e)

        if hybrid and query_text and (dense_rows or sparse_rows):
            rrf_k = 60
            scores: dict[Any, float] = {}
            docs: dict[Any, dict[str, Any]] = {}
            for rank, r in enumerate(dense_rows, start=1):
                doc_id = r["id"]
                docs[doc_id] = r
                scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (rrf_k + rank)
            for rank, r in enumerate(sparse_rows, start=1):
                doc_id = r["id"]
                if doc_id not in docs:
                    docs[doc_id] = r
                scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (rrf_k + rank)

            sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
            fused_rows: list[dict[str, Any]] = []
            for doc_id in sorted_ids:
                doc = docs[doc_id].copy()
                if "score" not in doc or doc["score"] is None:
                    doc["score"] = 0.65
                else:
                    doc["score"] = max(float(doc["score"]), 0.65)
                fused_rows.append(doc)
            rows = fused_rows
        else:
            rows = dense_rows

        if rerank and query_text and rows:
            rows = await rerank_candidates(query_text, rows, table)

        return rows[:k]
    except Exception:  # noqa: BLE001 -- degrade-open
        _pg_mark_down()
        return []
