# AI-hint: MiOS system and orchestration module providing session capabilities.
# AI-functions: _get_psycopg, get_dsn

import json
import os
import logging
from typing import Optional

psycopg = None

log = logging.getLogger("gateway-agent-session")

def _get_psycopg():
    global psycopg
    if psycopg is None:
        import psycopg as pg
        psycopg = pg
    return psycopg

def get_dsn() -> str:
    host = os.environ.get("MIOS_PGVECTOR_HOST", "localhost")
    port = int(os.environ.get("MIOS_PORTS_PGVECTOR", "8600") or 8600)
    user = os.environ.get("MIOS_PGVECTOR_USER", "mios")
    password = os.environ.get("MIOS_PGVECTOR_PASS", "mios")
    dbname = os.environ.get("MIOS_PGVECTOR_DB", "mios")
    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"

async def get_session(session_id: str) -> list[dict]:
    pg = _get_psycopg()
    try:
        async with await pg.AsyncConnection.connect(get_dsn(), autocommit=True, connect_timeout=5) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT messages FROM gateway_sessions WHERE session_id = %s",
                    (session_id,)
                )
                row = await cur.fetchone()
                if row:
                    messages = row[0]
                    if isinstance(messages, str):
                        return json.loads(messages)
                    return messages
    except Exception as e:
        log.warning("Database error fetching session %s: %s", session_id, e)
    return []

async def save_session(session_id: str, messages: list[dict]) -> None:
    pg = _get_psycopg()
    try:
        async with await pg.AsyncConnection.connect(get_dsn(), autocommit=True, connect_timeout=5) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO gateway_sessions (session_id, messages, updated_at)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (session_id)
                    DO UPDATE SET messages = EXCLUDED.messages, updated_at = CURRENT_TIMESTAMP
                    """,
                    (session_id, json.dumps(messages))
                )
    except Exception as e:
        log.warning("Database error saving session %s: %s", session_id, e)
