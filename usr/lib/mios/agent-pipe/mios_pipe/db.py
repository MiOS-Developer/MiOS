# AI-hint: Extracted module for db.py.
"""Database client and read/update handlers."""

from __future__ import annotations

import httpx
import os
import time
from typing import Optional

_PG_PRIMARY = False
_mios_pg = None
DB_NS = "mios"
DB_DB = "mios"
_pg_port = os.environ.get("MIOS_PORTS_PGVECTOR", "8600")
DB_URL = os.environ.get("MIOS_DB_URL", f"http://localhost:{_pg_port}")
_DB_AUTH = ""

_DB_CLIENT: Optional[httpx.AsyncClient] = None
_db_down_until: float = 0.0

def configure(*, pg_primary=None, mios_pg=None, db_ns=None, db_db=None, db_url=None, db_auth=None):
    global _PG_PRIMARY, _mios_pg, DB_NS, DB_DB, DB_URL, _DB_AUTH
    if pg_primary is not None: _PG_PRIMARY = pg_primary
    if mios_pg is not None: _mios_pg = mios_pg
    if db_ns is not None: DB_NS = db_ns
    if db_db is not None: DB_DB = db_db
    if db_url is not None: DB_URL = db_url
    if db_auth is not None: _DB_AUTH = db_auth

def client() -> httpx.AsyncClient:
    global _DB_CLIENT
    if _DB_CLIENT is None:
        _DB_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=10.0),
            limits=httpx.Limits(max_keepalive_connections=8, max_connections=16))
    return _DB_CLIENT

async def post(sql: str, *, timeout: float = 3.0) -> Optional[list]:
    global _db_down_until
    if not sql or not sql.strip(): return None
    if _PG_PRIMARY:
        _verb = sql.lstrip().split(None, 1)[0].upper()
        if _verb in ("CREATE", "UPDATE", "DELETE", "INSERT", "RELATE"): return None
    if time.time() < _db_down_until: return None
    body = (f"USE NS {DB_NS} DB {DB_DB}; " + sql).encode()
    try:
        r = await client().post(f"{DB_URL}/sql", content=body,
            headers={"Authorization": _DB_AUTH, "Accept": "application/json"},
            timeout=timeout)
        if r.status_code != 200:
            _db_down_until = time.time() + 30
            return None
        return r.json()
    except Exception:
        _db_down_until = time.time() + 30
        return None

async def read(surreal_sql: str, *, pg_sql: Optional[str] = None, pg_params: Optional[dict] = None, timeout: float = 3.0) -> Optional[list]:
    if _PG_PRIMARY and pg_sql:
        rows = await _mios_pg.execute(pg_sql, pg_params or {}, fetch=True)
        return [{"result": rows or []}]
    return await post(surreal_sql, timeout=timeout)

async def update(surreal_sql: str, *, pg_sql: Optional[str] = None, pg_params: Optional[dict] = None) -> None:
    if _PG_PRIMARY and pg_sql:
        await _mios_pg.execute(pg_sql, pg_params or {}, fetch=False)
    else:
        await post(surreal_sql)
