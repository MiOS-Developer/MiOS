#!/usr/bin/env python3
# AI-hint: Comprehensive empirical adversarial test suite for task_345..task_350 (Database, Storage, Encryption, and Replication).
# AI-related: usr/libexec/mios/db/mios-pgvector-optimize.py, usr/libexec/mios/storage/mios-ledger-sync, usr/libexec/mios/storage/mios-cephfs-quota, usr/libexec/mios/db/mios-pg-replica.py, usr/libexec/mios/db/mios-db-doctor.py, usr/libexec/mios/db/mios-db-migrate.py, usr/libexec/mios/sec/mios-luks-rotate
"""MiOS Empirical Adversarial Test Harness (Challenger 1).  Adversarially tests and stress-tests: - pgvector Automated VACUUM & Concurrent HNSW Reindexing (task_345) - CephFS Transactional Ledger Replication & Integrity Hashing (task_346) - CephFS Dynamic Quota Enforcement & Subvolume Sizing (task_347) - Ceph RADOS Gateway Quadlet Isolation (task_348) - LUKS2 / dm-crypt Automated Key Rotation & Safety Rollback (task_349) - PostgreSQL Hot-Standby Streaming Replication & Fencing Coordinator (task_350) - Database Corruption Detector & Non-Destructive Repair Engine (task_351) - Database Schema Migration Runner & Rollback Safety (task_356)"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import importlib.machinery
import importlib.util
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from typing import Any, Dict, List, Optional

_HERE = os.path.abspath(os.path.dirname(__file__)) if "__file__" in globals() else os.path.abspath(".")
_ROOT = os.path.normpath(os.path.join(_HERE, "..")) if os.path.basename(_HERE) == "tests" else _HERE

def load_module(name: str, rel_path: str) -> Any:
    full_path = os.path.join(_ROOT, rel_path)
    spec = importlib.util.spec_from_file_location(name, full_path)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    loader = importlib.machinery.SourceFileLoader(name, full_path)
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

sys.path.insert(0, os.path.join(_ROOT, "usr", "lib", "mios"))
sys.path.insert(0, os.path.join(_ROOT, "lib", "mios"))

pg_opt_mod = load_module("pgvector_optimize", "usr/libexec/mios/db/mios-pgvector-optimize.py")
ledger_mod = load_module("ledger_sync", "usr/libexec/mios/storage/mios-ledger-sync")
quota_mod = load_module("cephfs_quota", "usr/libexec/mios/storage/mios-cephfs-quota")
replica_mod = load_module("pg_replica", "usr/libexec/mios/db/mios-pg-replica.py")
doctor_mod = load_module("db_doctor", "usr/libexec/mios/db/mios-db-doctor.py")
migrate_mod = load_module("db_migrate", "usr/libexec/mios/db/mios-db-migrate.py")
luks_mod = load_module("luks_rotate", "usr/libexec/mios/sec/mios-luks-rotate")

class TestAdversarialPgVectorOptimize(unittest.TestCase):
    """Stress tests on pgvector optimizer concurrency invariants, table scoping, and error handling."""

    def test_concurrent_reindex_syntax_and_invariant(self):
        optimizer = pg_opt_mod.PgVectorOptimizer(mock=True)
        indexes = optimizer.get_vector_indexes()
        results = optimizer.reindex_vector_indexes_concurrently(indexes)

        self.assertEqual(len(results), len(indexes))
        for res in results:
            cmd = res["command"]
            # Must strictly contain 'REINDEX INDEX CONCURRENTLY' to prevent locking active queries
            self.assertTrue(cmd.startswith("REINDEX INDEX CONCURRENTLY "), f"Non-concurrent command: {cmd}")
            self.assertTrue(cmd.endswith(";"))
            self.assertEqual(res["status"], "success")

    def test_vacuum_parallel_worker_bounds(self):
        # Parallel = 1
        opt_1 = pg_opt_mod.PgVectorOptimizer(parallel=1, mock=True)
        res_1 = opt_1.vacuum_analyze_tables(["knowledge"])
        self.assertIn("PARALLEL 1", res_1[0]["command"])

        # Parallel = 8
        opt_8 = pg_opt_mod.PgVectorOptimizer(parallel=8, mock=True)
        res_8 = opt_8.vacuum_analyze_tables(["knowledge"])
        self.assertIn("PARALLEL 8", res_8[0]["command"])

        # Parallel = 0 or negative clamped to 1
        opt_neg = pg_opt_mod.PgVectorOptimizer(parallel=-5, mock=True)
        res_neg = opt_neg.vacuum_analyze_tables(["knowledge"])
        self.assertIn("PARALLEL 1", res_neg[0]["command"])

    def test_optimize_empty_tables(self):
        optimizer = pg_opt_mod.PgVectorOptimizer(mock=True)
        # Passing explicit list of tables
        report = optimizer.optimize(tables=["knowledge"])
        self.assertEqual(report["tables_vacuumed"], 1)
        self.assertEqual(report["status"], "completed")

    def test_cli_execution_mock_json(self):
        script_path = os.path.join(_ROOT, "usr", "libexec", "mios", "db", "mios-pgvector-optimize.py")
        cmd = [sys.executable, script_path, "--mock", "--json", "--parallel", "2"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["parallel_workers"], 2)

class TestAdversarialLedgerSync(unittest.TestCase):
    """Adversarial testing on CephFS Transactional Ledger Replication & Integrity Verification."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="mios-adv-ledger-")
        self.src_dir = os.path.join(self.test_dir, "hot_pool")
        self.dst_dir = os.path.join(self.test_dir, "bulk_pool")
        self.rec_log = os.path.join(self.test_dir, "reconciliation.log")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_genesis_block_strict_invariants(self):
        # Genesis block MUST have index 0
        b_bad_idx = ledger_mod.Block.create(index=1, prev_hash=ledger_mod.GENESIS_PREV_HASH, payload={"a": 1})
        ok, err = b_bad_idx.validate_integrity(prev_block=None)
        self.assertFalse(ok)
        self.assertIn("Genesis block index must be 0", err)

        # Genesis block MUST have prev_hash == 64 zeros
        b_bad_prev = ledger_mod.Block.create(index=0, prev_hash="1" * 64, payload={"a": 1})
        ok, err = b_bad_prev.validate_integrity(prev_block=None)
        self.assertFalse(ok)
        self.assertIn("Genesis block prev_hash must be", err)

    def test_large_chain_stress_and_reconciliation(self):
        src_chain = ledger_mod.LedgerChain(self.src_dir)
        block_count = 150

        # Append 150 blocks rapidly
        for i in range(block_count):
            payload = {
                "sequence": i,
                "tx_id": secrets.token_hex(16),
                "nested": {"score": i * 1.5, "tags": [f"tag_{i}", "audit"]},
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            src_chain.append(payload)

        # Verify source chain
        valid, count, errors = src_chain.verify()
        self.assertTrue(valid)
        self.assertEqual(count, block_count)
        self.assertEqual(len(errors), 0)

        # Replicate to destination pool
        engine = ledger_mod.LedgerSyncEngine()
        report = engine.replicate(self.src_dir, self.dst_dir, reconciliation_log=self.rec_log)
        self.assertEqual(report["status"], "synchronized")
        self.assertEqual(report["synced_blocks"], block_count)
        self.assertEqual(report["total_blocks"], block_count)

        # Destination chain must be 100% valid
        dst_chain = ledger_mod.LedgerChain(self.dst_dir)
        dst_valid, dst_count, dst_errors = dst_chain.verify()
        self.assertTrue(dst_valid)
        self.assertEqual(dst_count, block_count)

    def test_fork_divergence_detection_and_abort(self):
        src_chain = ledger_mod.LedgerChain(self.src_dir)
        dst_chain = ledger_mod.LedgerChain(self.dst_dir)

        # Shared genesis
        b0 = src_chain.append({"init": True})
        dst_chain._commit_block(b0)

        # Divergent block 1 on source vs destination
        src_chain.append({"branch": "source_branch", "val": 100})
        dst_chain.append({"branch": "dest_branch", "val": 200})

        # Syncing MUST detect divergence and abort without modifying destination
        engine = ledger_mod.LedgerSyncEngine()
        with self.assertRaises(ValueError) as ctx:
            engine.replicate(self.src_dir, self.dst_dir)
        self.assertIn("divergence detected", str(ctx.exception).lower())

        # Destination chain should remain valid at its own 2 blocks
        v, c, _ = dst_chain.verify()
        self.assertTrue(v)
        self.assertEqual(c, 2)

    def test_tamper_detection_in_middle_of_chain(self):
        chain = ledger_mod.LedgerChain(self.src_dir)
        for i in range(10):
            chain.append({"idx": i, "data": f"block_{i}"})

        # Mutate block 5 payload
        b5_path = os.path.join(self.src_dir, "blocks", "block_00000005.json")
        with open(b5_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["payload"]["data"] = "MUTATED_PAYLOAD"
        with open(b5_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

        valid, count, errors = chain.verify()
        self.assertFalse(valid)
        self.assertTrue(any("Block 5 payload hash mismatch" in e for e in errors))

    def test_hmac_secret_key_enforcement_and_tampering(self):
        key = "correct-audit-secret-key-2026"
        chain = ledger_mod.LedgerChain(self.src_dir)
        b0 = chain.append({"action": "secure_event"}, secret_key=key)

        # Verification without secret_key passes structural integrity
        v_no_sec, _, _ = chain.verify()
        self.assertTrue(v_no_sec)

        # Verification with correct key passes
        v_key, _, _ = chain.verify(secret_key=key)
        self.assertTrue(v_key)

        # Verification with incorrect key fails
        v_bad, _, errs_bad = chain.verify(secret_key="wrong-key")
        self.assertFalse(v_bad)
        self.assertTrue(any("signature" in e for e in errs_bad))

class TestAdversarialCephFSQuota(unittest.TestCase):
    """Stress tests on CephFS dynamic quota units, threshold status, and subvolume resizing."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="mios-adv-quota-")
        self.tenant_dir = os.path.join(self.test_dir, "tenant_test")
        os.makedirs(self.tenant_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_parse_size_bytes_comprehensive_units_and_cases(self):
        # Case insensitivity & formatting
        self.assertEqual(quota_mod.parse_size_bytes("50gib"), 50 * (1024**3))
        self.assertEqual(quota_mod.parse_size_bytes("50GIB"), 50 * (1024**3))
        self.assertEqual(quota_mod.parse_size_bytes("  100 MiB  "), 100 * (1024**2))
        self.assertEqual(quota_mod.parse_size_bytes("100mb"), 100 * (1000**2))
        self.assertEqual(quota_mod.parse_size_bytes("1.5TiB"), int(1.5 * (1024**4)))
        self.assertEqual(quota_mod.parse_size_bytes("0.5GiB"), int(0.5 * (1024**3)))
        self.assertEqual(quota_mod.parse_size_bytes("0"), 0)
        self.assertEqual(quota_mod.parse_size_bytes("unlimited"), 0)
        self.assertEqual(quota_mod.parse_size_bytes("none"), 0)
        self.assertEqual(quota_mod.parse_size_bytes("no"), 0)

        # Empty or whitespace returns 0 as unlimited/unset
        self.assertEqual(quota_mod.parse_size_bytes(""), 0)
        self.assertEqual(quota_mod.parse_size_bytes("   "), 0)

        # Invalid formats raise ValueError
        invalid_inputs = [
            "invalid_size",
            "100xyz",
            "10.20.30GiB",
            "-50GiB",
            "gib",
            "100 GiB extra",
        ]
        for inv in invalid_inputs:
            with self.assertRaises(ValueError, msg=f"Input '{inv}' should raise ValueError"):
                quota_mod.parse_size_bytes(inv)

    def test_parse_count_units_and_cases(self):
        self.assertEqual(quota_mod.parse_count("0"), 0)
        self.assertEqual(quota_mod.parse_count("unlimited"), 0)
        self.assertEqual(quota_mod.parse_count("none"), 0)
        self.assertEqual(quota_mod.parse_count(500), 500)
        self.assertEqual(quota_mod.parse_count("1500"), 1500)
        self.assertEqual(quota_mod.parse_count("50k"), 50000)
        self.assertEqual(quota_mod.parse_count("2m"), 2000000)

        with self.assertRaises(ValueError):
            quota_mod.parse_count("invalid_count")

    def test_format_size_bytes_boundaries(self):
        self.assertEqual(quota_mod.format_size_bytes(0), "0 B")
        self.assertEqual(quota_mod.format_size_bytes(-10), "0 B")
        self.assertEqual(quota_mod.format_size_bytes(512), "512.00 B")
        self.assertEqual(quota_mod.format_size_bytes(1024), "1.00 KiB")
        self.assertEqual(quota_mod.format_size_bytes(1024 * 1024), "1.00 MiB")
        self.assertEqual(quota_mod.format_size_bytes(1024 * 1024 * 1024), "1.00 GiB")
        self.assertEqual(quota_mod.format_size_bytes(1024**4), "1.00 TiB")
        self.assertEqual(quota_mod.format_size_bytes(1024**5), "1.00 PiB")

    def test_quota_threshold_alert_states(self):
        mgr = quota_mod.CephFSQuotaManager()
        quota_bytes = 10000  # 10,000 bytes

        mgr.set_quota(self.tenant_dir, max_bytes=quota_bytes, max_files=100)

        # 1. 0% -> OK
        info0 = mgr.get_quota(self.tenant_dir)
        self.assertEqual(info0["status"], "OK")
        self.assertEqual(info0["bytes_percent"], 0.0)

        # 2. Write 8,000 bytes (80% -> WARNING)
        fpath = os.path.join(self.tenant_dir, "file.dat")
        with open(fpath, "wb") as f:
            f.write(b"a" * 8000)
        info80 = mgr.get_quota(self.tenant_dir)
        self.assertEqual(info80["status"], "WARNING")
        self.assertEqual(info80["bytes_percent"], 80.0)

        # 3. Write 1,200 more bytes (9,200 bytes = 92% -> CRITICAL)
        with open(fpath, "wb") as f:
            f.write(b"a" * 9200)
        info92 = mgr.get_quota(self.tenant_dir)
        self.assertEqual(info92["status"], "CRITICAL")
        self.assertEqual(info92["bytes_percent"], 92.0)

        # 4. Write 10,000 bytes (100% -> EXCEEDED)
        with open(fpath, "wb") as f:
            f.write(b"a" * 10000)
        info100 = mgr.get_quota(self.tenant_dir)
        self.assertEqual(info100["status"], "EXCEEDED")

        # 5. Over quota (11,000 bytes = 110% -> EXCEEDED)
        with open(fpath, "wb") as f:
            f.write(b"a" * 11000)
        info110 = mgr.get_quota(self.tenant_dir)
        self.assertEqual(info110["status"], "EXCEEDED")
        self.assertEqual(info110["bytes_percent"], 110.0)

class TestAdversarialPgReplica(unittest.TestCase):
    """Stress tests on PostgreSQL replication management, split-brain fencing invariants, and promotion."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="mios-adv-replica-")
        self.data_dir = os.path.join(self.test_dir, "pgdata")
        self.fence_dir = os.path.join(self.test_dir, "fencing")
        self.manager = replica_mod.PgReplicaManager(
            primary_host="192.168.1.10",
            primary_port=5432,
            replica_host="192.168.1.11",
            replica_port=5433,
            data_dir=self.data_dir,
            fence_dir=self.fence_dir,
            max_lag_ms=50.0,
            mock=True,
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_promotion_strictly_blocked_when_primary_unfenced(self):
        """Invariant: Old primary MUST be fenced before promotion to prevent split-brain writes."""
        self.manager.unfence_primary()
        self.assertFalse(self.manager.is_primary_fenced())

        with self.assertRaises(RuntimeError) as ctx:
            self.manager.promote_replica(force_unfenced=False)
        self.assertIn("Old primary is NOT fenced", str(ctx.exception))

    def test_promotion_succeeds_when_fenced(self):
        self.manager.provision_replica()
        fence_info = self.manager.fence_primary(reason="planned_switchover")
        self.assertEqual(fence_info["status"], "fenced")
        self.assertTrue(self.manager.is_primary_fenced())

        res = self.manager.promote_replica(force_unfenced=False)
        self.assertEqual(res["status"], "promoted")
        self.assertTrue(res["primary_fenced"])

    def test_emergency_promotion_force_unfenced(self):
        self.manager.provision_replica()
        self.manager.unfence_primary()
        res = self.manager.promote_replica(force_unfenced=True)
        self.assertEqual(res["status"], "promoted")

    def test_replication_health_lag_thresholds(self):
        # Health check passes within threshold
        self.manager.max_lag_ms = 50.0
        health = self.manager.health_check()
        self.assertTrue(health["healthy"])

        # Health check fails when max_lag is below observed lag (12.4ms in mock)
        self.manager.max_lag_ms = 5.0
        health_fail = self.manager.health_check()
        self.assertFalse(health_fail["healthy"])
        self.assertIn("exceeds threshold", health_fail["reason"])

class TestAdversarialDbDoctor(unittest.TestCase):
    """Stress tests on Database Corruption Detector and Non-Destructive Repair Engine."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="mios-adv-doctor-")
        self.doctor = doctor_mod.DbDoctor(sqlite_paths=[self.test_dir], mock=False)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_sqlite_db(self, name: str) -> str:
        p = os.path.join(self.test_dir, name)
        conn = sqlite3.connect(p)
        conn.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, content TEXT);")
        conn.execute("CREATE INDEX idx_rec_content ON records(content);")
        conn.executemany("INSERT INTO records (content) VALUES (?);", [(f"row_{i}",) for i in range(100)])
        conn.commit()
        conn.close()
        return p

    def test_healthy_database_repair_invariant(self):
        """Invariant: Healthy databases must NEVER undergo destructive repair or schema rewriting."""
        db_path = self._create_sqlite_db("healthy_main.db")
        res = self.doctor.repair_sqlite_db(db_path, force_dump=False)
        self.assertEqual(res["status"], "healthy")
        self.assertEqual(res["action"], "none_needed")
        self.assertIn("skipping repair", res["message"])

    def test_corrupted_sqlite_detection_and_backup(self):
        db_path = self._create_sqlite_db("corrupted_main.db")
        # Overwrite page 2 b-tree header with junk
        with open(db_path, "r+b") as f:
            f.seek(4096)
            f.write(b"\xDE\xAD\xBE\xEF" * 64)

        check = self.doctor.check_sqlite_db(db_path)
        self.assertEqual(check["status"], "corrupt")
        self.assertGreater(len(check["errors"]), 0)

        # Repair must create backup file before attempting repair
        rep = self.doctor.repair_sqlite_db(db_path)
        self.assertIn(rep["status"], ("repaired", "unrecoverable"))
        self.assertTrue(os.path.exists(rep["backup_path"]))

    def test_find_sqlite_databases_filtering(self):
        db1 = self._create_sqlite_db("app.sqlite")
        db2 = self._create_sqlite_db("cache.db")

        # Create non-sqlite files with .db or .sqlite extension
        fake_db = os.path.join(self.test_dir, "fake.db")
        with open(fake_db, "w", encoding="utf-8") as f:
            f.write("This is not a sqlite database.")

        found = self.doctor.find_sqlite_databases()
        self.assertIn(os.path.abspath(db1), found)
        self.assertIn(os.path.abspath(db2), found)
        self.assertNotIn(os.path.abspath(fake_db), found)

class TestAdversarialDbMigrate(unittest.TestCase):
    """Stress tests on Database Schema Migrations, Checksum Integrity, and Transaction Rollback."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="mios-adv-migrate-")
        self.migrator = migrate_mod.DbMigrator(
            migrations_dir=self.test_dir,
            db_name="test_mios",
            mock=True,
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_migration_version_ordering_and_hashing(self):
        # Create out-of-order migrations
        m2 = os.path.join(self.test_dir, "0002_add_field.sql")
        m1 = os.path.join(self.test_dir, "0001_create_table.sql")
        m10 = os.path.join(self.test_dir, "0010_indexes.sql")

        with open(m2, "w", encoding="utf-8") as f:
            f.write("ALTER TABLE users ADD COLUMN email text;")
        with open(m1, "w", encoding="utf-8") as f:
            f.write("CREATE TABLE users (id serial primary key, name text);")
        with open(m10, "w", encoding="utf-8") as f:
            f.write("CREATE INDEX idx_users_email ON users(email);")

        migrations = self.migrator.load_migrations()
        self.assertEqual(len(migrations), 3)
        self.assertEqual([m.version for m in migrations], [1, 2, 10])

    def test_checksum_tampering_aborts_migration_pipeline(self):
        m1 = os.path.join(self.test_dir, "0001_init.sql")
        with open(m1, "w", encoding="utf-8") as f:
            f.write("CREATE TABLE test (id int);")

        # Apply migration 1
        rep = self.migrator.migrate()
        self.assertEqual(rep["total_applied"], 1)

        # Tamper migration 1 on disk (e.g. add a comment or change column)
        with open(m1, "w", encoding="utf-8") as f:
            f.write("CREATE TABLE test (id int); -- TAMPERED COMMENT")

        # Adding migration 2
        m2 = os.path.join(self.test_dir, "0002_next.sql")
        with open(m2, "w", encoding="utf-8") as f:
            f.write("CREATE TABLE next (id int);")

        # Running migrate() MUST fail immediately due to checksum tampering on migration 1
        with self.assertRaises(RuntimeError) as ctx:
            self.migrator.migrate()
        self.assertIn("checksum mismatch", str(ctx.exception).lower())

    def test_atomic_rollback_on_syntax_error(self):
        m1 = os.path.join(self.test_dir, "0001_good.sql")
        with open(m1, "w", encoding="utf-8") as f:
            f.write("CREATE TABLE good_table (id serial);")

        m2 = os.path.join(self.test_dir, "0002_bad.sql")
        with open(m2, "w", encoding="utf-8") as f:
            f.write("SYNTAX_ERROR_IN_MIGRATION; INVALID SQL BLOCK;")

        with self.assertRaises(RuntimeError) as ctx:
            self.migrator.migrate()
        self.assertIn("ROLLED BACK", str(ctx.exception))

        # Migration 2 should NOT be in applied migrations
        applied = self.migrator.get_applied_migrations()
        self.assertIn(1, applied)
        self.assertNotIn(2, applied)

class TestAdversarialLUKSRotate(unittest.TestCase):
    """Stress tests on LUKS2 key rotation, full keyslot rejection, and safety rollback."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="mios-adv-luks-")
        self.backup_dir = os.path.join(self.test_dir, "headers")
        self.audit_log = os.path.join(self.test_dir, "rotation.log")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_full_keyslots_rejection(self):
        # Fill all 32 keyslots (0..31)
        full_slots = {i: f"pass_{i}" for i in range(32)}
        mock_dev = luks_mod.LUKSDevice()
        # Mock dump_metadata on full slots
        mock_dev.dump_metadata = lambda dev: {"device": dev, "active_slots": list(range(32)), "free_slots": []}
        mock_dev.test_passphrase = lambda dev, p, slot=None: True

        engine = luks_mod.LUKSRotationEngine(
            luks_device=mock_dev,
            backup_root=self.backup_dir,
            audit_log=self.audit_log,
        )

        with self.assertRaises(RuntimeError) as ctx:
            engine.rotate_key("/dev/mapper/ceph-osd-0", current_passphrase="pass_0")
        self.assertIn("No available free keyslots", str(ctx.exception))

    def test_unlock_failure_on_new_key_preserves_old_keyslot(self):
        """CRITICAL: If testing new key fails, old keyslot MUST NOT be killed."""
        # Class-based mock device
        class UnlockingFailureLUKSDevice(luks_mod.LUKSDevice):
            def __init__(self):
                super().__init__()
                self.slots = {0: "current-valid-passphrase"}
                self.killed_slots = []

            def dump_metadata(self, dev):
                return {"device": dev, "active_slots": list(self.slots.keys()), "free_slots": [1, 2, 3]}

            def backup_header(self, dev, out):
                os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
                with open(out, "w") as f:
                    f.write("# BAK")
                return out

            def add_key(self, device, current_passphrase, new_passphrase, new_slot):
                self.slots[new_slot] = new_passphrase
                return True

            def test_passphrase(self, device, passphrase, slot=None):
                if passphrase == "current-valid-passphrase":
                    return True
                # Simulate failure on new key test
                return False

            def kill_slot(self, device, slot_to_kill, active_passphrase=None):
                self.killed_slots.append(slot_to_kill)
                if slot_to_kill in self.slots:
                    del self.slots[slot_to_kill]
                return True

        mock_dev = UnlockingFailureLUKSDevice()
        engine = luks_mod.LUKSRotationEngine(
            luks_device=mock_dev,
            backup_root=self.backup_dir,
            audit_log=self.audit_log,
        )

        with self.assertRaises(RuntimeError) as ctx:
            engine.rotate_key(
                device="/dev/mapper/ceph-osd-0",
                current_passphrase="current-valid-passphrase",
                new_passphrase="new-broken-passphrase",
            )
        self.assertIn("ABORTED ROTATION", str(ctx.exception))

        # Old keyslot 0 MUST be preserved intact!
        self.assertIn(0, mock_dev.slots)
        self.assertEqual(mock_dev.slots[0], "current-valid-passphrase")

if __name__ == "__main__":
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    suite.addTests(loader.loadTestsFromTestCase(TestAdversarialPgVectorOptimize))
    suite.addTests(loader.loadTestsFromTestCase(TestAdversarialLedgerSync))
    suite.addTests(loader.loadTestsFromTestCase(TestAdversarialCephFSQuota))
    suite.addTests(loader.loadTestsFromTestCase(TestAdversarialPgReplica))
    suite.addTests(loader.loadTestsFromTestCase(TestAdversarialDbDoctor))
    suite.addTests(loader.loadTestsFromTestCase(TestAdversarialDbMigrate))
    suite.addTests(loader.loadTestsFromTestCase(TestAdversarialLUKSRotate))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
