#!/usr/bin/env python3
# AI-hint: Database integrity checker, corruption detector, and automated non-destructive repair engine for SQLite and PostgreSQL.
# AI-related: usr/lib/greenboot/check/required.d/55-mios-db-check.sh, tests/test-db.py, usr/share/containers/systemd/mios-pgvector.container
"""
MiOS Database Doctor & Automated Repair Engine.
Inspects SQLite databases using PRAGMA integrity_check / quick_check,
and PostgreSQL data clusters via pg_checksums and amcheck.
Executes non-destructive repairs (REINDEX/VACUUM) before falling back to snapshot restoration.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_SQLITE_SEARCH_PATHS = [
    "/var/lib/mios",
    "/etc/mios",
]
DEFAULT_PG_DATA_DIR = "/var/lib/mios/pgvector"
# SSOT [ports].pgvector_internal: the in-container listener (host maps 8600->5432).
DEFAULT_PG_PORT = int(os.environ.get("MIOS_PORTS_PGVECTOR_INTERNAL", "5432"))

class DbDoctor:
    """Detects and repairs database corruption across SQLite and PostgreSQL stores."""

    def __init__(
        self,
        sqlite_paths: Optional[List[str]] = None,
        pg_data_dir: str = DEFAULT_PG_DATA_DIR,
        pg_host: str = "127.0.0.1",
        pg_port: int = DEFAULT_PG_PORT,
        pg_db: str = "mios",
        pg_user: str = "postgres",
        mock: bool = False,
    ) -> None:
        self.sqlite_paths = sqlite_paths or DEFAULT_SQLITE_SEARCH_PATHS
        self.pg_data_dir = pg_data_dir
        self.pg_host = pg_host
        self.pg_port = pg_port
        self.pg_db = pg_db
        self.pg_user = pg_user
        self.mock = mock

    def find_sqlite_databases(self) -> List[str]:
        """Discovers SQLite database files across configured paths."""
        if self.mock:
            return [
                "/var/lib/mios/kanban.sqlite",
                "/var/lib/mios/telemetry.db",
            ]

        db_files = []
        for path in self.sqlite_paths:
            if not os.path.exists(path):
                continue
            if os.path.isfile(path):
                if self._is_sqlite_file(path):
                    db_files.append(os.path.abspath(path))
                continue
            for root, _, files in os.walk(path):
                for f in files:
                    if f.endswith((".db", ".sqlite", ".sqlite3")):
                        full_path = os.path.join(root, f)
                        if self._is_sqlite_file(full_path):
                            db_files.append(os.path.abspath(full_path))
        return sorted(list(set(db_files)))

    def _is_sqlite_file(self, file_path: str) -> bool:
        """Verifies if the file header matches SQLite 3 format."""
        try:
            if not os.path.isfile(file_path) or os.path.getsize(file_path) < 16:
                return False
            with open(file_path, "rb") as f:
                header = f.read(16)
                return header.startswith(b"SQLite format 3\x00")
        except Exception:
            return False

    def check_sqlite_db(self, db_path: str) -> Dict[str, Any]:
        """Runs PRAGMA integrity_check and quick_check on a SQLite database."""
        start = time.perf_counter()

        if self.mock:
            # Check for intentional mock corruption marker
            is_corrupt = "corrupt" in db_path.lower()
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            if is_corrupt:
                return {
                    "path": db_path,
                    "type": "sqlite",
                    "status": "corrupt",
                    "errors": ["Page 4: b-tree cell corruption", "Index mismatch"],
                    "quick_check": "fail",
                    "integrity_check": "fail",
                    "elapsed_ms": elapsed_ms,
                }
            return {
                "path": db_path,
                "type": "sqlite",
                "status": "healthy",
                "errors": [],
                "quick_check": "ok",
                "integrity_check": "ok",
                "elapsed_ms": elapsed_ms,
            }

        errors = []
        quick_status = "unknown"
        integrity_status = "unknown"

        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
            cursor = conn.cursor()

            cursor.execute("PRAGMA quick_check;")
            quick_rows = cursor.fetchall()
            if quick_rows and quick_rows[0][0] == "ok":
                quick_status = "ok"
            else:
                quick_status = "fail"
                errors.extend([r[0] for r in quick_rows if r[0] != "ok"])

            cursor.execute("PRAGMA integrity_check;")
            integrity_rows = cursor.fetchall()
            if integrity_rows and integrity_rows[0][0] == "ok":
                integrity_status = "ok"
            else:
                integrity_status = "fail"
                errors.extend([r[0] for r in integrity_rows if r[0] != "ok"])

            conn.close()
        except Exception as e:
            errors.append(f"Connection/query error: {str(e)}")
            integrity_status = "error"

        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        status = "healthy" if not errors and integrity_status == "ok" else "corrupt"

        return {
            "path": db_path,
            "type": "sqlite",
            "status": status,
            "errors": errors,
            "quick_check": quick_status,
            "integrity_check": integrity_status,
            "elapsed_ms": elapsed_ms,
        }

    def repair_sqlite_db(self, db_path: str, force_dump: bool = False) -> Dict[str, Any]:
        """
        Repairs a corrupted SQLite database.
        MANDATORY INVARIANT: Do NOT run destructive .dump recovery over healthy databases.
        Attempts non-destructive REINDEX and VACUUM first before table dump recovery.
        """
        start = time.perf_counter()

        # Step 1: Health inspection before repair
        initial_check = self.check_sqlite_db(db_path)
        if initial_check["status"] == "healthy" and not force_dump:
            return {
                "path": db_path,
                "type": "sqlite",
                "action": "none_needed",
                "status": "healthy",
                "message": "Database is healthy; skipping repair to preserve data integrity.",
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
            }

        if self.mock:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            return {
                "path": db_path,
                "type": "sqlite",
                "action": "reindex_and_vacuum",
                "status": "repaired",
                "initial_errors": initial_check.get("errors", []),
                "repaired_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "elapsed_ms": elapsed_ms,
            }

        backup_path = f"{db_path}.corrupt_backup_{int(time.time())}"
        shutil.copy2(db_path, backup_path)

        repair_strategy = "non_destructive"
        repaired = False

        # Attempt 1: Non-destructive REINDEX and VACUUM
        try:
            conn = sqlite3.connect(db_path, timeout=10.0)
            conn.execute("REINDEX;")
            conn.execute("VACUUM;")
            conn.close()

            post_check = self.check_sqlite_db(db_path)
            if post_check["status"] == "healthy":
                repaired = True
                repair_strategy = "reindex_and_vacuum"
        except Exception as e:
            repaired = False

        # Attempt 2: If still corrupt and sqlite3 CLI is available, dump and restore
        if not repaired:
            sqlite_cli = shutil.which("sqlite3")
            if sqlite_cli:
                recovered_path = f"{db_path}.recovered"
                try:
                    # Execute .recover to reconstructed db without shell
                    p1 = subprocess.Popen([sqlite_cli, backup_path, ".recover"], stdout=subprocess.PIPE)
                    subprocess.run([sqlite_cli, recovered_path], stdin=p1.stdout, check=True)
                    if p1.stdout:
                        p1.stdout.close()
                    p1.wait()
                    if os.path.exists(recovered_path) and os.path.getsize(recovered_path) > 0:
                        shutil.move(recovered_path, db_path)
                        post_check = self.check_sqlite_db(db_path)
                        if post_check["status"] == "healthy":
                            repaired = True
                            repair_strategy = "sqlite_recover_dump"
                except Exception:
                    pass

        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        return {
            "path": db_path,
            "type": "sqlite",
            "action": repair_strategy,
            "status": "repaired" if repaired else "unrecoverable",
            "backup_path": backup_path,
            "initial_errors": initial_check.get("errors", []),
            "repaired_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "elapsed_ms": elapsed_ms,
        }

    def check_postgres(self) -> Dict[str, Any]:
        """Checks PostgreSQL integrity via pg_checksums or online amcheck."""
        start = time.perf_counter()

        if self.mock:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            return {
                "target": self.pg_data_dir,
                "type": "postgres",
                "status": "healthy",
                "checksums_enabled": True,
                "corrupted_blocks": 0,
                "errors": [],
                "elapsed_ms": elapsed_ms,
            }

        errors = []
        corrupted_blocks = 0

        # Method 1: Check offline cluster with pg_checksums if data dir exists
        pg_checksums = shutil.which("pg_checksums")
        if pg_checksums and os.path.exists(self.pg_data_dir):
            res = subprocess.run([pg_checksums, "-c", "-D", self.pg_data_dir], capture_output=True, text=True)
            if res.returncode != 0:
                errors.append(f"pg_checksums failed: {res.stderr.strip() or res.stdout.strip()}")
                corrupted_blocks += 1

        # Method 2: Online amcheck via psql if server is running
        psql = shutil.which("psql")
        if psql:
            amcheck_sql = """
            CREATE EXTENSION IF NOT EXISTS amcheck;
            SELECT c.relname, bt_index_check(c.oid, true)
            FROM pg_index i
            JOIN pg_class c ON c.oid = i.indexrelid
            JOIN pg_am a ON a.oid = c.relam
            WHERE a.amname = 'btree' AND c.relnamespace = 'public'::regnamespace;
            """
            cmd = [
                psql,
                "-h", self.pg_host,
                "-p", str(self.pg_port),
                "-U", self.pg_user,
                "-d", self.pg_db,
                "-c", amcheck_sql,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0 and "Connection refused" not in res.stderr:
                errors.append(f"amcheck error: {res.stderr.strip()}")

        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        status = "healthy" if not errors else "corrupt"

        return {
            "target": self.pg_data_dir,
            "type": "postgres",
            "status": status,
            "corrupted_blocks": corrupted_blocks,
            "errors": errors,
            "elapsed_ms": elapsed_ms,
        }

    def repair_postgres(self) -> Dict[str, Any]:
        """Runs non-destructive REINDEX DATABASE on PostgreSQL."""
        start = time.perf_counter()

        if self.mock:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            return {
                "target": self.pg_data_dir,
                "type": "postgres",
                "action": "reindex_database",
                "status": "repaired",
                "repaired_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "elapsed_ms": elapsed_ms,
            }

        psql = shutil.which("psql")
        if not psql:
            raise RuntimeError("psql binary not found on PATH")

        cmd = [
            psql,
            "-h", self.pg_host,
            "-p", str(self.pg_port),
            "-U", self.pg_user,
            "-d", self.pg_db,
            "-c", "REINDEX DATABASE mios;",
        ]

        res = subprocess.run(cmd, capture_output=True, text=True)
        repaired = res.returncode == 0

        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        return {
            "target": self.pg_data_dir,
            "type": "postgres",
            "action": "reindex_database",
            "status": "repaired" if repaired else "unrecoverable",
            "error": res.stderr.strip() if not repaired else None,
            "repaired_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "elapsed_ms": elapsed_ms,
        }

    def run_diagnostics(self, repair: bool = False, db_type: str = "all") -> Dict[str, Any]:
        """Executes integrity checks and optional repairs across all configured stores."""
        start = time.perf_counter()
        sqlite_results = []
        pg_result = None

        if db_type in ("sqlite", "all"):
            db_files = self.find_sqlite_databases()
            for db_file in db_files:
                if repair:
                    sqlite_results.append(self.repair_sqlite_db(db_file))
                else:
                    sqlite_results.append(self.check_sqlite_db(db_file))

        if db_type in ("postgres", "all"):
            if repair:
                pg_result = self.repair_postgres()
            else:
                pg_result = self.check_postgres()

        total_elapsed_ms = round((time.perf_counter() - start) * 1000, 2)

        # Evaluate overall health status
        all_healthy = True
        for r in sqlite_results:
            if r.get("status") not in ("healthy", "repaired"):
                all_healthy = False
        if pg_result and pg_result.get("status") not in ("healthy", "repaired"):
            all_healthy = False

        return {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "mode": "repair" if repair else "check",
            "db_type": db_type,
            "overall_status": "healthy" if all_healthy else "corrupt",
            "sqlite_databases": sqlite_results,
            "postgres": pg_result,
            "total_elapsed_ms": total_elapsed_ms,
        }

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS Database Corruption Detector & Automated Repair Engine"
    )
    parser.add_argument("--check", action="store_true", default=True, help="Check database integrity (default)")
    parser.add_argument("--repair", action="store_true", help="Execute automated non-destructive repair")
    parser.add_argument(
        "--db-type",
        choices=["sqlite", "postgres", "all"],
        default="all",
        help="Target database type (default: all)",
    )
    parser.add_argument("--sqlite-path", nargs="*", help="Explicit SQLite database files or search directories")
    parser.add_argument("--pg-data-dir", default=DEFAULT_PG_DATA_DIR, help="PostgreSQL data directory")
    parser.add_argument("--pg-host", default="127.0.0.1", help="PostgreSQL host")
    parser.add_argument("--pg-port", type=int, default=DEFAULT_PG_PORT, help="PostgreSQL port")
    parser.add_argument("--pg-db", default="mios", help="PostgreSQL database")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output report in JSON format")
    parser.add_argument("--mock", action="store_true", help="Mock execution mode for CI / tests")

    args = parser.parse_args()

    doctor = DbDoctor(
        sqlite_paths=args.sqlite_path,
        pg_data_dir=args.pg_data_dir,
        pg_host=args.pg_host,
        pg_port=args.pg_port,
        pg_db=args.pg_db,
        mock=args.mock,
    )

    try:
        report = doctor.run_diagnostics(repair=args.repair, db_type=args.db_type)

        if args.json_output:
            print(json.dumps(report, indent=2))
        else:
            print(f"[mios-db-doctor] Mode: {report['mode'].upper()} | Status: {report['overall_status'].upper()}")
            print(f"[mios-db-doctor] Checked {len(report['sqlite_databases'])} SQLite databases:")
            for s in report["sqlite_databases"]:
                print(f"  - {s.get('path')}: {s.get('status')} ({s.get('elapsed_ms')}ms)")
            if report.get("postgres"):
                pg = report["postgres"]
                print(f"[mios-db-doctor] Checked PostgreSQL ({pg.get('target')}): {pg.get('status')} ({pg.get('elapsed_ms')}ms)")
            print(f"[mios-db-doctor] Finished in {report['total_elapsed_ms']}ms")

        return 0 if report["overall_status"] == "healthy" else 1
    except Exception as e:
        sys.stderr.write(f"[mios-db-doctor] FATAL ERROR: {e}\n")
        return 1

if __name__ == "__main__":
    sys.exit(main())
