# AI-hint: In-process SQLite vector store (sqlite-vec) scratchpad module for ephemeral tool outputs (CONV-08).

import hashlib
import os
from pathlib import Path

SQLITE_VEC_ENABLE = os.environ.get("MIOS_CONVERGE_MEMORY_SQLITE_VEC_ENABLE", "false").lower() in ("true", "1", "yes", "on")


def _scratchpad_path(session_id: str, scratchpad_dir: str) -> Path:
    """Keep arbitrary external session identifiers out of filesystem paths."""
    identity = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return Path(scratchpad_dir or "/tmp") / f"mios-session-{identity}.sqlite"


if SQLITE_VEC_ENABLE:
    import sqlite3
    import sqlite_vec

    def create_scratchpad(session_id: str, scratchpad_dir: str) -> tuple:
        db_dir = Path(scratchpad_dir or "/tmp")
        db_dir.mkdir(parents=True, exist_ok=True)
        path = _scratchpad_path(session_id, scratchpad_dir)

        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)

        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS vec_scratch USING vec0(content TEXT, tainted INTEGER, embedding float[768])")
        conn.commit()
        return conn, path

    def destroy_scratchpad(conn, path: Path) -> None:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass

    def vec_insert(conn, content: str, embedding: list[float], tainted: bool = False) -> None:
        if not conn:
            return
        serialized = sqlite_vec.serialize_float32(embedding)
        conn.execute("INSERT INTO vec_scratch(content, tainted, embedding) VALUES (?, ?, ?)", (content, int(tainted), serialized))
        conn.commit()

    def vec_search(conn, query_embedding: list[float], k: int = 5) -> list[dict]:
        if not conn:
            return []
        serialized = sqlite_vec.serialize_float32(query_embedding)
        cursor = conn.execute(
            "SELECT content, tainted, distance FROM vec_scratch WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
            (serialized, k)
        )
        return [{"content": row[0], "tainted": bool(row[1]), "distance": row[2]} for row in cursor.fetchall()]

    def has_tainted(session_id: str, scratchpad_dir: str) -> bool:
        path = _scratchpad_path(session_id, scratchpad_dir)
        if not path.exists():
            return False
        try:
            conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            cursor = conn.execute("SELECT 1 FROM vec_scratch WHERE tainted = 1 LIMIT 1")
            res = cursor.fetchone()
            conn.close()
            return res is not None
        except Exception:
            return False

else:
    def create_scratchpad(session_id: str, scratchpad_dir: str) -> tuple:
        return None, None

    def destroy_scratchpad(conn, path) -> None:
        pass

    def vec_insert(conn, content: str, embedding: list[float], tainted: bool = False) -> None:
        pass

    def vec_search(conn, query_embedding: list[float], k: int = 5) -> list[dict]:
        return []

    def has_tainted(session_id: str, scratchpad_dir: str) -> bool:
        return False
