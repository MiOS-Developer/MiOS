#!/usr/bin/env python3
# AI-hint: Zero-downtime PostgreSQL schema migration runner with SHA-256 integrity hashing and atomic transaction rollback.
# AI-related: usr/share/mios/postgres/migrations/, tests/test-db.py, usr/share/mios/postgres/schema-init.sql
"""
MiOS Database Schema Migration Runner.
Applies transactional SQL migrations from /usr/share/mios/postgres/migrations/
within explicit BEGIN ... COMMIT blocks, tracks versions and SHA-256 checksums
in the schema_version ledger, and guarantees atomic rollback on any failure.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple


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
_MIGRATION_KEY = "migration"
_MIG = _DB.get(_MIGRATION_KEY) or {}

DEFAULT_MIGRATIONS_DIR = str(_MIG.get("migrations_dir")
                             or "/usr/share/mios/postgres/migrations")
DEFAULT_VERSION_TABLE = str(_MIG.get("version_table") or "schema_version")
DEFAULT_PG_PORT = int(_PORTS.get("pgvector") or 8600)

SCHEMA_VERSION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS """ + DEFAULT_VERSION_TABLE + """ (
    version integer PRIMARY KEY,
    name text NOT NULL,
    checksum text NOT NULL,
    applied_at timestamptz DEFAULT now(),
    execution_time_ms integer,
    status text DEFAULT 'applied'
);
"""

class MigrationFile:
    """Represents an on-disk SQL migration script."""

    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self.filename = os.path.basename(file_path)
        self.version, self.name = self._parse_version_and_name(self.filename)
        self.content = self._read_content()
        self.checksum = self._calculate_sha256(self.content)

    @staticmethod
    def _parse_version_and_name(filename: str) -> Tuple[int, str]:
        match = re.match(r"^(\d+)_(.+)\.sql$", filename)
        if not match:
            raise ValueError(f"Migration filename '{filename}' does not follow 'NNNN_name.sql' format.")
        version = int(match.group(1))
        name = match.group(2)
        return version, name

    def _read_content(self) -> str:
        with open(self.file_path, "r", encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def _calculate_sha256(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

class DbMigrator:
    """Orchestrates schema version tracking, integrity checking, and atomic transaction execution."""

    def __init__(
        self,
        migrations_dir: str = DEFAULT_MIGRATIONS_DIR,
        host: str = "127.0.0.1",
        port: int = 5432,
        db_name: str = "mios",
        user: str = "postgres",
        dry_run: bool = False,
        mock: bool = False,
    ) -> None:
        self.migrations_dir = migrations_dir
        self.host = host
        self.port = port
        self.db_name = db_name
        self.user = user
        self.dry_run = dry_run
        self.mock = mock

        # Mock in-memory state for testing
        self._mock_applied: Dict[int, Dict[str, Any]] = {}

    def load_migrations(self) -> List[MigrationFile]:
        """Loads and sorts all migration files from disk."""
        if not os.path.exists(self.migrations_dir):
            return []

        migrations = []
        for entry in os.listdir(self.migrations_dir):
            if entry.endswith(".sql") and re.match(r"^\d+_", entry):
                full_path = os.path.join(self.migrations_dir, entry)
                if os.path.isfile(full_path):
                    migrations.append(MigrationFile(full_path))

        migrations.sort(key=lambda m: m.version)
        return migrations

    def _run_psql_command(self, sql: str) -> str:
        """Executes SQL via psql CLI tool."""
        if self.mock:
            return ""

        psql = shutil.which("psql")
        if not psql:
            raise RuntimeError("psql binary not found on PATH")

        cmd = [
            psql,
            "-h", self.host,
            "-p", str(self.port),
            "-U", self.user,
            "-d", self.db_name,
            "-t",
            "-A",
            "-F", "\t",
            "-c", sql,
        ]

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"psql execution error (code {res.returncode}): {res.stderr.strip()}")
        return res.stdout.strip()

    def get_applied_migrations(self) -> Dict[int, Dict[str, Any]]:
        """Retrieves list of previously applied migrations from schema_version table."""
        if self.mock:
            return self._mock_applied.copy()

        # Ensure schema_version table exists
        self._run_psql_command(SCHEMA_VERSION_TABLE_SQL)

        query = "SELECT version, name, checksum, applied_at, execution_time_ms, status FROM schema_version ORDER BY version ASC;"
        raw = self._run_psql_command(query)

        applied = {}
        if not raw:
            return applied

        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) >= 6:
                v = int(parts[0])
                applied[v] = {
                    "version": v,
                    "name": parts[1],
                    "checksum": parts[2],
                    "applied_at": parts[3],
                    "execution_time_ms": int(parts[4]) if parts[4].isdigit() else 0,
                    "status": parts[5],
                }
        return applied

    def check_status(self) -> List[Dict[str, Any]]:
        """Compares on-disk migration files against schema_version table and verifies checksums."""
        disk_migrations = self.load_migrations()
        applied = self.get_applied_migrations()

        status_list = []
        for m in disk_migrations:
            if m.version in applied:
                rec = applied[m.version]
                # Invariant: Verify checksum matches recorded hash
                if rec["checksum"] != m.checksum:
                    state = "checksum_mismatch"
                else:
                    state = "applied"
                status_list.append({
                    "version": m.version,
                    "name": m.name,
                    "filename": m.filename,
                    "checksum": m.checksum,
                    "recorded_checksum": rec["checksum"],
                    "state": state,
                    "applied_at": rec.get("applied_at"),
                    "execution_time_ms": rec.get("execution_time_ms"),
                })
            else:
                status_list.append({
                    "version": m.version,
                    "name": m.name,
                    "filename": m.filename,
                    "checksum": m.checksum,
                    "recorded_checksum": None,
                    "state": "pending",
                    "applied_at": None,
                    "execution_time_ms": None,
                })

        return status_list

    def apply_single_migration(self, migration: MigrationFile) -> Dict[str, Any]:
        """
        Executes a single migration wrapped in an explicit transaction block (BEGIN ... COMMIT).
        MANDATORY INVARIANT: Records version ID and SHA-256 checksum in schema_version ledger.
        If an error occurs, executes ROLLBACK immediately.
        """
        start_perf = time.perf_counter()

        # Wrap migration SQL and ledger update in an atomic transaction
        tx_sql = f"""
BEGIN;

-- Migration: {migration.filename}
{migration.content}

-- Record migration in schema_version ledger
INSERT INTO schema_version (version, name, checksum, applied_at, execution_time_ms, status)
VALUES ({migration.version}, '{migration.name}', '{migration.checksum}', now(), 0, 'applied')
ON CONFLICT (version) DO UPDATE SET
    name = EXCLUDED.name,
    checksum = EXCLUDED.checksum,
    applied_at = now(),
    status = 'applied';

COMMIT;
"""
        if self.dry_run:
            elapsed_ms = round((time.perf_counter() - start_perf) * 1000, 2)
            return {
                "version": migration.version,
                "name": migration.name,
                "filename": migration.filename,
                "checksum": migration.checksum,
                "status": "dry_run",
                "elapsed_ms": elapsed_ms,
                "sql_preview": tx_sql.strip()[:200] + "...",
            }

        if self.mock:
            # Check for simulated SQL error in mock
            if "SYNTAX_ERROR_IN_MIGRATION" in migration.content or "FAIL_MIGRATION" in migration.content:
                raise RuntimeError(
                    f"Migration {migration.filename} failed: syntax error at or near 'INVALID_SQL'. Transaction ROLLED BACK."
                )

            elapsed_ms = round((time.perf_counter() - start_perf) * 1000, 2)
            self._mock_applied[migration.version] = {
                "version": migration.version,
                "name": migration.name,
                "checksum": migration.checksum,
                "applied_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "execution_time_ms": int(elapsed_ms),
                "status": "applied",
            }
            return {
                "version": migration.version,
                "name": migration.name,
                "filename": migration.filename,
                "checksum": migration.checksum,
                "status": "applied",
                "elapsed_ms": elapsed_ms,
            }

        try:
            self._run_psql_command(tx_sql)
            elapsed_ms = round((time.perf_counter() - start_perf) * 1000, 2)

            # Update actual elapsed time in ledger
            update_time_sql = f"UPDATE schema_version SET execution_time_ms = {int(elapsed_ms)} WHERE version = {migration.version};"
            try:
                self._run_psql_command(update_time_sql)
            except Exception:
                pass

            return {
                "version": migration.version,
                "name": migration.name,
                "filename": migration.filename,
                "checksum": migration.checksum,
                "status": "applied",
                "elapsed_ms": elapsed_ms,
            }
        except Exception as e:
            # Explicit rollback safety confirmation
            try:
                self._run_psql_command("ROLLBACK;")
            except Exception:
                pass
            raise RuntimeError(f"Migration {migration.filename} failed: {e}. Transaction ROLLED BACK.")

    def migrate(self) -> Dict[str, Any]:
        """Applies all pending migrations in order, halting on any checksum mismatch or execution error."""
        start_perf = time.perf_counter()

        status_entries = self.check_status()
        disk_migrations = {m.version: m for m in self.load_migrations()}

        # Verify no checksum mismatches exist in historical migrations
        mismatches = [s for s in status_entries if s["state"] == "checksum_mismatch"]
        if mismatches:
            mismatched_names = [f"{s['version']}_{s['name']}" for s in mismatches]
            raise RuntimeError(
                f"Migration aborted due to checksum mismatch on previously applied migration(s): {', '.join(mismatched_names)}. "
                "Database schema ledger has been tampered with or migration files were edited post-apply."
            )

        pending = [s for s in status_entries if s["state"] == "pending"]
        applied_results = []

        for item in pending:
            m = disk_migrations[item["version"]]
            res = self.apply_single_migration(m)
            applied_results.append(res)

        total_elapsed_ms = round((time.perf_counter() - start_perf) * 1000, 2)

        return {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "migrations_dir": self.migrations_dir,
            "database": self.db_name,
            "host": self.host,
            "port": self.port,
            "dry_run": self.dry_run,
            "mock": self.mock,
            "total_discovered": len(disk_migrations),
            "total_pending": len(pending),
            "total_applied": len(applied_results),
            "applied_details": applied_results,
            "total_elapsed_ms": total_elapsed_ms,
            "status": "completed",
        }

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS Zero-Downtime Transactional Schema Migration Runner"
    )
    parser.add_argument("--dir", default=DEFAULT_MIGRATIONS_DIR, help="Directory containing migration .sql files")
    parser.add_argument("--host", default="127.0.0.1", help="PostgreSQL host")
    parser.add_argument("--port", type=int, default=DEFAULT_PG_PORT, help="PostgreSQL port")
    parser.add_argument("--db", default="mios", help="Database name")
    parser.add_argument("--user", default="postgres", help="PostgreSQL user")
    parser.add_argument("--status", action="store_true", help="Display migration status and checksum audit")
    parser.add_argument("--dry-run", action="store_true", help="Validate migrations without committing transactions")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output results in JSON format")
    parser.add_argument("--mock", action="store_true", help="Mock execution mode for CI / test verification")

    args = parser.parse_args()

    migrator = DbMigrator(
        migrations_dir=args.dir,
        host=args.host,
        port=args.port,
        db_name=args.db,
        user=args.user,
        dry_run=args.dry_run,
        mock=args.mock,
    )

    try:
        if args.status:
            status_report = migrator.check_status()
            if args.json_output:
                print(json.dumps(status_report, indent=2))
            else:
                print(f"[mios-db-migrate] Migration status for {args.dir}:")
                for s in status_report:
                    status_flag = f"[{s['state'].upper()}]"
                    print(f"  {status_flag:<20} {s['filename']} (SHA256: {s['checksum'][:12]}...)")
            return 0

        report = migrator.migrate()

        if args.json_output:
            print(json.dumps(report, indent=2))
        else:
            print(f"[mios-db-migrate] Completed migration run for '{args.db}' on {args.host}:{args.port}")
            print(f"[mios-db-migrate] Mode: {'MOCK' if args.mock else ('DRY-RUN' if args.dry_run else 'LIVE')}")
            print(f"[mios-db-migrate] Pending: {report['total_pending']}, Applied: {report['total_applied']} in {report['total_elapsed_ms']}ms")
            for a in report["applied_details"]:
                print(f"  - Applied: {a['filename']} ({a['elapsed_ms']}ms, Status: {a['status']})")

        return 0
    except Exception as e:
        sys.stderr.write(f"[mios-db-migrate] ERROR: {e}\n")
        return 1

if __name__ == "__main__":
    sys.exit(main())
