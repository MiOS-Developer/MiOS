#!/usr/bin/env python3
# AI-hint: Automated pg_dump and zstd snapshot generator with rolling retention for pgvector.
# AI-related: usr/lib/systemd/system/mios-backup-pgvector.service, tests/test-db.py, usr/share/doc/mios/manual/ch66-v5-authority-inversion-and-cephfs-tiering.md
"""
Automated PostgreSQL+pgvector Backup & zstd Snapshot Engine.
Dumps database state via pg_dump, compresses with zstd, and enforces rolling retention.
"""

from __future__ import annotations

import argparse
import datetime
import os
import shutil
import subprocess
import sys
import time
from typing import List, Tuple

# SSOT [ports].pgvector: host-published listener is 8600 (in-container postgres also listens on 8600).
DEFAULT_PG_PORT = int(
    os.environ.get("MIOS_PORTS_PGVECTOR")
    or os.environ.get("MIOS_PORTS_PGVECTOR_INTERNAL")
    or "8600"
)
DEFAULT_PG_USER = os.environ.get("MIOS_PGVECTOR_USER") or "mios"
DEFAULT_PG_DB = os.environ.get("MIOS_PGVECTOR_DB") or "mios"
DEFAULT_PG_CONTAINER = os.environ.get("MIOS_PGVECTOR_CONTAINER") or "mios-pgvector"

def generate_backup_filename(db_name: str = DEFAULT_PG_DB) -> str:
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    return f"{db_name}_backup_{timestamp}.sql.zst"

def execute_backup(
    db_name: str = DEFAULT_PG_DB,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PG_PORT,
    user: str = DEFAULT_PG_USER,
    output_dir: str = "/var/lib/mios/backups/pgvector",
    zstd_level: int = 3,
    mock: bool = False,
    container: str = DEFAULT_PG_CONTAINER,
) -> str:
    os.makedirs(output_dir, exist_ok=True)
    filename = generate_backup_filename(db_name)
    target_path = os.path.join(output_dir, filename)

    if mock:
        with open(target_path, "wb") as f:
            f.write(b"MOCK_ZSTD_POSTGRES_PGVECTOR_DUMP_DATA\x28\xb5\x2f\xfd")
        return target_path

    pg_dump = shutil.which("pg_dump")
    podman = shutil.which("podman")
    zstd = shutil.which("zstd")

    if not zstd or (not pg_dump and not podman):
        raise RuntimeError("zstd and either pg_dump or podman binary are required on system PATH")

    if pg_dump:
        dump_cmd = [pg_dump, "-h", host, "-p", str(port), "-U", user, "-d", db_name, "--clean", "--if-exists"]
    else:
        dump_cmd = [
            podman,
            "exec",
            container,
            "pg_dump",
            "-h",
            host,
            "-p",
            str(port),
            "-U",
            user,
            "--no-password",
            "-d",
            db_name,
            "--clean",
            "--if-exists",
        ]
    zstd_cmd = [zstd, f"-{zstd_level}", "--check", "-T0", "-o", target_path]

    p1 = subprocess.Popen(dump_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p2 = subprocess.Popen(zstd_cmd, stdin=p1.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p1.stdout:
        p1.stdout.close()

    _, err2 = p2.communicate()
    _, err1 = p1.communicate()

    if p1.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {err1.decode('utf-8', errors='ignore')}")
    if p2.returncode != 0:
        raise RuntimeError(f"zstd compression failed: {err2.decode('utf-8', errors='ignore')}")

    return target_path

def enforce_retention(
    output_dir: str = "/var/lib/mios/backups/pgvector",
    retention_days: int = 7,
) -> List[str]:
    """Removes snapshot archives older than retention_days; returns list of deleted files."""
    if not os.path.exists(output_dir):
        return []

    cutoff_time = time.time() - (retention_days * 86400)
    deleted = []

    for entry in os.listdir(output_dir):
        if entry.endswith(".sql.zst"):
            file_path = os.path.join(output_dir, entry)
            if os.path.isfile(file_path):
                stat = os.stat(file_path)
                if stat.st_mtime < cutoff_time:
                    os.remove(file_path)
                    deleted.append(file_path)

    return deleted

def main() -> int:
    parser = argparse.ArgumentParser(description="MiOS pgvector zstd backup engine")
    parser.add_argument("--db", default=DEFAULT_PG_DB, help="Database name")
    parser.add_argument("--host", default="127.0.0.1", help="PostgreSQL host")
    parser.add_argument("--port", type=int, default=DEFAULT_PG_PORT, help="PostgreSQL port")
    parser.add_argument("--user", default=DEFAULT_PG_USER, help="PostgreSQL user")
    parser.add_argument("--container", default=DEFAULT_PG_CONTAINER, help="Podman container fallback when host pg_dump is absent")
    parser.add_argument("--output-dir", default="/var/lib/mios/backups/pgvector", help="Backup storage directory")
    parser.add_argument("--retention-days", type=int, default=7, help="Retention period in days")
    parser.add_argument("--mock", action="store_true", help="Generate mock snapshot for testing")

    args = parser.parse_args()

    try:
        backup_path = execute_backup(
            db_name=args.db,
            host=args.host,
            port=args.port,
            user=args.user,
            output_dir=args.output_dir,
            mock=args.mock,
            container=args.container,
        )
        print(f"[mios-backup-pgvector] Snapshot written: {backup_path}")
        deleted = enforce_retention(output_dir=args.output_dir, retention_days=args.retention_days)
        if deleted:
            print(f"[mios-backup-pgvector] Purged {len(deleted)} expired snapshots")
        return 0
    except Exception as e:
        sys.stderr.write(f"[mios-backup-pgvector] Error: {e}\n")
        return 1

if __name__ == "__main__":
    sys.exit(main())
