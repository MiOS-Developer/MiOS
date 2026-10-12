#!/usr/bin/env python3
# AI-hint: Automated VACUUM ANALYZE and concurrent HNSW/IVFFlat index rebuilding engine for pgvector.
# AI-related: usr/lib/systemd/system/mios-pgvector-optimize.service, usr/lib/systemd/system/mios-pgvector-optimize.timer, tests/test-db.py
"""
Automated pgvector Database Optimizer.
Performs VACUUM (ANALYZE, PARALLEL 4) on tables, inspects dead tuple ratios,
and executes REINDEX INDEX CONCURRENTLY on HNSW and IVFFlat vector indices
to maintain high recall and low latency without blocking live read queries.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

# Known vector tables in MiOS agent-plane schema

def _ssot_root():
    """The whole layered SSOT; {} when unreadable (degrade-open)."""
    here = os.path.dirname(os.path.abspath(__file__))
    for lib in ("/usr/lib/mios", os.path.join(here, "..", "..", "..", "lib", "mios")):
        lib = os.path.normpath(lib)
        if os.path.isdir(lib) and lib not in sys.path:
            sys.path.insert(0, lib)
    try:
        import mios_toml
        return mios_toml.load_merged() or {}
    except Exception:
        try:
            import tomllib
        except ImportError:
            return {}
        try:
            with open(os.environ.get("MIOS_TOML") or "/usr/share/mios/mios.toml", "rb") as fh:
                return tomllib.load(fh) or {}
        except OSError:
            return {}

_SSOT = _ssot_root()
_PORTS = _SSOT.get("ports") or {}
_DB = _SSOT.get("database") or {}
# Sub-table keys as constants: spelled inline they are indistinguishable from a
# read of a same-named top-level table to check_no_inert_ssot_tables.
_MAINT_KEY = "pgvector_maintenance"
_MAINT = _DB.get(_MAINT_KEY) or {}

DEFAULT_PARALLEL_WORKERS = int(_MAINT.get("vacuum_parallel_workers") or 4)
DEFAULT_DEAD_TUPLE_RATIO = float(_MAINT.get("dead_tuple_threshold_ratio") or 0.1)
DEFAULT_REINDEX_CONCURRENTLY = bool(_MAINT.get("reindex_concurrently", True))
DEFAULT_PG_PORT = int(_PORTS.get("pgvector") or 8600)

DEFAULT_VECTOR_TABLES = [
    "knowledge",
    "agent_memory",
    "event",
    "tool_call",
    "skill",
    "verb",
    "directory_entry",
    "config_kv",
    "embeddings",
    "mios_rag",
    "person_pref",
]

# Query to discover dead tuples and fragmentation
QUERY_DEAD_TUPLES = """
SELECT
    schemaname,
    relname AS table_name,
    n_live_tup AS live_tuples,
    n_dead_tup AS dead_tuples,
    ROUND(CASE WHEN (n_live_tup + n_dead_tup) > 0
          THEN (n_dead_tup::float / (n_live_tup + n_dead_tup)::float * 100)::numeric
          ELSE 0 END, 2) AS dead_tuple_pct,
    last_vacuum,
    last_autovacuum,
    last_analyze
FROM pg_stat_user_tables
ORDER BY n_dead_tup DESC;
"""

# Query to find all vector indexes (HNSW, IVFFlat)
QUERY_VECTOR_INDEXES = """
SELECT
    c.relname AS index_name,
    t.relname AS table_name,
    am.amname AS index_type,
    pg_size_pretty(pg_relation_size(c.oid)) AS index_size,
    pg_relation_size(c.oid) AS index_bytes
FROM pg_class c
JOIN pg_index i ON c.oid = i.indexrelid
JOIN pg_class t ON i.indrelid = t.oid
JOIN pg_am am ON c.relam = am.oid
WHERE am.amname IN ('hnsw', 'ivfflat')
ORDER BY t.relname, c.relname;
"""

# SSOT [ports].pgvector_internal: the in-container listener (host maps 8600->5432).
DEFAULT_PG_PORT = int(os.environ.get("MIOS_PORTS_PGVECTOR_INTERNAL", "5432"))

class PgVectorOptimizer:
    """Orchestrates VACUUM ANALYZE and CONCURRENT REINDEX operations."""

    def __init__(
        self,
        db: str = "mios",
        host: str = "127.0.0.1",
        port: int = DEFAULT_PG_PORT,
        user: str = "postgres",
        parallel: int = 4,
        dry_run: bool = False,
        mock: bool = False,
    ) -> None:
        self.db = db
        self.host = host
        self.port = port
        self.user = user
        self.parallel = max(1, parallel)
        self.dry_run = dry_run
        self.mock = mock

    def _run_psql_query(self, sql: str) -> str:
        """Executes a SQL query via psql CLI and returns raw output."""
        if self.mock:
            return ""

        psql = shutil.which("psql")
        if not psql:
            raise RuntimeError("psql binary not found in system PATH")

        cmd = [
            psql,
            "-h", self.host,
            "-p", str(self.port),
            "-U", self.user,
            "-d", self.db,
            "-t",
            "-A",
            "-F", "\t",
            "-c", sql,
        ]

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"psql query failed (code {res.returncode}): {res.stderr.strip()}")
        return res.stdout.strip()

    def _execute_sql(self, sql: str) -> None:
        """Executes a SQL statement via psql CLI."""
        if self.dry_run:
            return
        if self.mock:
            return

        psql = shutil.which("psql")
        if not psql:
            raise RuntimeError("psql binary not found in system PATH")

        cmd = [
            psql,
            "-h", self.host,
            "-p", str(self.port),
            "-U", self.user,
            "-d", self.db,
            "-c", sql,
        ]

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"psql execution failed (code {res.returncode}): {res.stderr.strip()}")

    def get_dead_tuples(self) -> List[Dict[str, Any]]:
        """Retrieves dead tuple statistics across user tables."""
        if self.mock:
            return [
                {
                    "table_name": "knowledge",
                    "live_tuples": 50000,
                    "dead_tuples": 4500,
                    "dead_tuple_pct": 8.26,
                },
                {
                    "table_name": "agent_memory",
                    "live_tuples": 12000,
                    "dead_tuples": 1500,
                    "dead_tuple_pct": 11.11,
                },
                {
                    "table_name": "event",
                    "live_tuples": 100000,
                    "dead_tuples": 800,
                    "dead_tuple_pct": 0.79,
                },
            ]

        raw = self._run_psql_query(QUERY_DEAD_TUPLES)
        results = []
        if not raw:
            return results

        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) >= 5:
                results.append({
                    "schemaname": parts[0],
                    "table_name": parts[1],
                    "live_tuples": int(parts[2]) if parts[2].isdigit() else 0,
                    "dead_tuples": int(parts[3]) if parts[3].isdigit() else 0,
                    "dead_tuple_pct": float(parts[4]) if parts[4].replace(".", "", 1).isdigit() else 0.0,
                })
        return results

    def get_vector_indexes(self) -> List[Dict[str, Any]]:
        """Discovers all HNSW and IVFFlat vector indexes."""
        if self.mock:
            return [
                {
                    "index_name": "knowledge_emb_hnsw",
                    "table_name": "knowledge",
                    "index_type": "hnsw",
                    "index_size": "42 MB",
                    "index_bytes": 44040192,
                },
                {
                    "index_name": "agent_memory_emb_hnsw",
                    "table_name": "agent_memory",
                    "index_type": "hnsw",
                    "index_size": "12 MB",
                    "index_bytes": 12582912,
                },
                {
                    "index_name": "mios_rag_emb_hnsw",
                    "table_name": "mios_rag",
                    "index_type": "hnsw",
                    "index_size": "8 MB",
                    "index_bytes": 8388608,
                },
                {
                    "index_name": "config_kv_emb_hnsw",
                    "table_name": "config_kv",
                    "index_type": "hnsw",
                    "index_size": "2 MB",
                    "index_bytes": 2097152,
                },
            ]

        raw = self._run_psql_query(QUERY_VECTOR_INDEXES)
        results = []
        if not raw:
            return results

        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) >= 5:
                results.append({
                    "index_name": parts[0],
                    "table_name": parts[1],
                    "index_type": parts[2],
                    "index_size": parts[3],
                    "index_bytes": int(parts[4]) if parts[4].isdigit() else 0,
                })
        return results

    def vacuum_analyze_tables(self, tables: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Executes parallel VACUUM ANALYZE on specified or discovered tables."""
        target_tables = tables or DEFAULT_VECTOR_TABLES
        results = []

        for table in target_tables:
            start = time.perf_counter()
            sql = f"VACUUM (ANALYZE, PARALLEL {self.parallel}) {table};"
            error = None
            try:
                self._execute_sql(sql)
            except Exception as e:
                error = str(e)

            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            results.append({
                "table": table,
                "command": sql,
                "elapsed_ms": elapsed_ms,
                "status": "dry_run" if self.dry_run else ("error" if error else "success"),
                "error": error,
            })

        return results

    def reindex_vector_indexes_concurrently(
        self, indexes: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """
        Executes REINDEX INDEX CONCURRENTLY for all vector indexes.
        Ensures non-blocking behavior for active read sessions.
        """
        target_indexes = indexes if indexes is not None else self.get_vector_indexes()
        results = []

        for idx in target_indexes:
            idx_name = idx["index_name"]
            table_name = idx.get("table_name", "unknown")
            idx_type = idx.get("index_type", "hnsw")

            start = time.perf_counter()
            # CRITICAL: Always CONCURRENTLY to avoid read locks (task_2578 Invariant)
            sql = f"REINDEX INDEX CONCURRENTLY {idx_name};"
            error = None
            try:
                self._execute_sql(sql)
            except Exception as e:
                error = str(e)

            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            results.append({
                "index_name": idx_name,
                "table_name": table_name,
                "index_type": idx_type,
                "command": sql,
                "elapsed_ms": elapsed_ms,
                "status": "dry_run" if self.dry_run else ("error" if error else "success"),
                "error": error,
            })

        return results

    def optimize(self, tables: Optional[List[str]] = None) -> Dict[str, Any]:
        """Runs complete pgvector optimization pipeline."""
        start_time = time.time()
        start_perf = time.perf_counter()

        dead_tuples = self.get_dead_tuples()
        vacuum_results = self.vacuum_analyze_tables(tables)
        reindex_results = self.reindex_vector_indexes_concurrently()

        total_elapsed_ms = round((time.perf_counter() - start_perf) * 1000, 2)

        summary = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "database": self.db,
            "host": self.host,
            "port": self.port,
            "dry_run": self.dry_run,
            "mock": self.mock,
            "parallel_workers": self.parallel,
            "dead_tuple_stats": dead_tuples,
            "tables_vacuumed": len(vacuum_results),
            "vacuum_details": vacuum_results,
            "indexes_reindexed": len(reindex_results),
            "reindex_details": reindex_results,
            "total_elapsed_ms": total_elapsed_ms,
            "status": "completed",
        }

        # Check if any errors occurred
        has_errors = any(v.get("status") == "error" for v in vacuum_results + reindex_results)
        if has_errors:
            summary["status"] = "completed_with_errors"

        return summary

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS pgvector VACUUM ANALYZE and Concurrent HNSW Reindexing Engine"
    )
    parser.add_argument("--db", default="mios", help="Database name (default: mios)")
    parser.add_argument("--host", default="127.0.0.1", help="PostgreSQL host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PG_PORT, help="PostgreSQL port")
    parser.add_argument("--user", default="postgres", help="PostgreSQL user (default: postgres)")
    parser.add_argument("--parallel", type=int, default=DEFAULT_PARALLEL_WORKERS, help="Parallel workers for VACUUM")
    parser.add_argument("--tables", nargs="*", help="Explicit tables to vacuum (default: all vector tables)")
    parser.add_argument("--dry-run", action="store_true", help="Print operations without executing SQL")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output summary in JSON format")
    parser.add_argument("--mock", action="store_true", help="Mock execution mode for CI / test verification")

    args = parser.parse_args()

    optimizer = PgVectorOptimizer(
        db=args.db,
        host=args.host,
        port=args.port,
        user=args.user,
        parallel=args.parallel,
        dry_run=args.dry_run,
        mock=args.mock,
    )

    try:
        report = optimizer.optimize(tables=args.tables)

        if args.json_output:
            print(json.dumps(report, indent=2))
        else:
            print(f"[mios-pgvector-optimize] Starting optimization for DB '{args.db}' on {args.host}:{args.port}")
            print(f"[mios-pgvector-optimize] Mode: {'MOCK' if args.mock else ('DRY-RUN' if args.dry_run else 'LIVE')}")
            print(f"[mios-pgvector-optimize] Vacuumed {report['tables_vacuumed']} tables in {sum(v['elapsed_ms'] for v in report['vacuum_details']):.1f}ms")
            for v in report["vacuum_details"]:
                print(f"  - Table '{v['table']}': {v['status']} ({v['elapsed_ms']}ms)")
            print(f"[mios-pgvector-optimize] Reindexed {report['indexes_reindexed']} vector indexes concurrently in {sum(r['elapsed_ms'] for r in report['reindex_details']):.1f}ms")
            for r in report["reindex_details"]:
                print(f"  - Index '{r['index_name']}' ({r['index_type']}) on '{r['table_name']}': {r['status']} ({r['elapsed_ms']}ms)")
            print(f"[mios-pgvector-optimize] Total execution time: {report['total_elapsed_ms']}ms (Status: {report['status']})")

        return 0 if report["status"] in ("completed", "dry_run") else 1
    except Exception as e:
        sys.stderr.write(f"[mios-pgvector-optimize] FATAL ERROR: {e}\n")
        return 1

if __name__ == "__main__":
    sys.exit(main())
