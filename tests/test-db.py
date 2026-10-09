#!/usr/bin/env python3
# AI-hint: Consolidated database test suite: backup/retention, db doctor/corruption, migrations/rollback, SSOT materialization, PG events, streaming replica, autovacuum tuner, pgvector halfvec HNSW, and pgvector vacuum/reindexing.
# AI-related: usr/libexec/mios/db/, usr/lib/mios/ai/pgvector_hnsw.py, tests/test-db.py
"""Consolidated MiOS database tests (backup, doctor, migrate, ssot materialize, events, replica, vacuum, hnsw, optimize)."""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
# Every top-level table of the vendor SSOT; a round trip must keep them all.
with open(os.path.join(_ROOT, "usr", "share", "mios", "mios.toml"), "rb") as _fh:
    _VENDOR_TABLES = len(__import__("tomllib").load(_fh))


# ======================================================================
# from tests/test-backup-pgvector.py
# ======================================================================
_BACKUP_PATH = os.path.join(_ROOT, "usr", "libexec", "mios", "db", "mios-backup-pgvector.py")
spec_backup = importlib.util.spec_from_file_location("backup_pgvector", _BACKUP_PATH)
if spec_backup and spec_backup.loader:
    backup_pgvector = importlib.util.module_from_spec(spec_backup)
    sys.modules[spec_backup.name] = backup_pgvector
    spec_backup.loader.exec_module(backup_pgvector)
else:
    raise ImportError(f"Could not load backup_pgvector module from {_BACKUP_PATH}")


class TestBackupPgVector(unittest.TestCase):
    """Validates backup filename format, mock snapshot generation, and 7-day retention rotation."""

    def test_backup_filename_format(self):
        filename = backup_pgvector.generate_backup_filename("mios_test")
        self.assertTrue(filename.startswith("mios_test_backup_"))
        self.assertTrue(filename.endswith(".sql.zst"))

    def test_mock_snapshot_generation_and_storage(self):
        with tempfile.TemporaryDirectory(prefix="mios-backup-test-") as tmpdir:
            out_path = backup_pgvector.execute_backup(
                db_name="test_agent_db",
                output_dir=tmpdir,
                mock=True,
            )
            self.assertTrue(os.path.isfile(out_path))
            self.assertGreater(os.path.getsize(out_path), 10)

    def test_rolling_retention_purge(self):
        with tempfile.TemporaryDirectory(prefix="mios-retention-test-") as tmpdir:
            # Create a recent backup
            recent_file = os.path.join(tmpdir, "mios_backup_recent.sql.zst")
            with open(recent_file, "wb") as f:
                f.write(b"RECENT_SNAPSHOT")

            # Create an old backup (10 days old)
            old_file = os.path.join(tmpdir, "mios_backup_old.sql.zst")
            with open(old_file, "wb") as f:
                f.write(b"OLD_SNAPSHOT")
            old_time = time.time() - (10 * 86400)
            os.utime(old_file, (old_time, old_time))

            # Enforce 7-day retention
            deleted = backup_pgvector.enforce_retention(output_dir=tmpdir, retention_days=7)
            self.assertEqual(len(deleted), 1)
            self.assertEqual(deleted[0], old_file)
            self.assertFalse(os.path.exists(old_file))
            self.assertTrue(os.path.exists(recent_file))


# ======================================================================
# from tests/test-db-doctor.py
# ======================================================================
_DOCTOR_PATH = os.path.join(_ROOT, "usr", "libexec", "mios", "db", "mios-db-doctor.py")
spec_doctor = importlib.util.spec_from_file_location("db_doctor", _DOCTOR_PATH)
if spec_doctor and spec_doctor.loader:
    db_doctor = importlib.util.module_from_spec(spec_doctor)
    sys.modules[spec_doctor.name] = db_doctor
    spec_doctor.loader.exec_module(db_doctor)
else:
    raise ImportError(f"Could not load db_doctor module from {_DOCTOR_PATH}")


class TestDbDoctor(unittest.TestCase):
    """Validates SQLite and PostgreSQL integrity checking and non-destructive repair logic."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios-db-doctor-test-")
        self.doctor = db_doctor.DbDoctor(
            sqlite_paths=[self.tmpdir],
            pg_data_dir=os.path.join(self.tmpdir, "pgdata"),
            mock=False,
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _create_healthy_sqlite(self, filename: str = "healthy.sqlite") -> str:
        path = os.path.join(self.tmpdir, filename)
        conn = sqlite3.connect(path)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);")
        cursor.execute("CREATE INDEX idx_users_name ON users(name);")
        cursor.executemany("INSERT INTO users (name) VALUES (?);", [("alice",), ("bob",), ("charlie",)])
        conn.commit()
        conn.close()
        return path

    def test_sqlite_discovery(self):
        db1 = self._create_healthy_sqlite("app1.db")
        db2 = self._create_healthy_sqlite("app2.sqlite")
        with open(os.path.join(self.tmpdir, "notes.txt"), "w") as f:
            f.write("just text")

        found = self.doctor.find_sqlite_databases()
        self.assertEqual(len(found), 2)
        self.assertIn(os.path.abspath(db1), found)
        self.assertIn(os.path.abspath(db2), found)

    def test_sqlite_healthy_check(self):
        db = self._create_healthy_sqlite("valid.sqlite3")
        res = self.doctor.check_sqlite_db(db)
        self.assertEqual(res["status"], "healthy")
        self.assertEqual(res["quick_check"], "ok")
        self.assertEqual(res["integrity_check"], "ok")
        self.assertEqual(len(res["errors"]), 0)

    def test_sqlite_healthy_repair_invariant(self):
        """Invariant check: Do NOT run destructive recovery over healthy databases."""
        db = self._create_healthy_sqlite("valid_untouched.db")
        before_mtime = os.path.getmtime(db)
        repair_res = self.doctor.repair_sqlite_db(db, force_dump=False)
        self.assertEqual(repair_res["status"], "healthy")
        self.assertEqual(repair_res["action"], "none_needed")
        self.assertIn("skipping repair", repair_res["message"])

    def test_sqlite_corrupt_detection_and_repair(self):
        db = self._create_healthy_sqlite("corrupt_test.db")
        # Corrupt table page 2 by overwriting b-tree page header
        with open(db, "r+b") as f:
            f.seek(4096)
            f.write(b"\xFF\xFE\xFD\xFC" * 64)

        check_res = self.doctor.check_sqlite_db(db)
        self.assertEqual(check_res["status"], "corrupt")
        self.assertGreater(len(check_res["errors"]), 0)

        repair_res = self.doctor.repair_sqlite_db(db)
        self.assertIn(repair_res["status"], ("repaired", "unrecoverable"))
        self.assertTrue(os.path.exists(repair_res["backup_path"]))

    def test_postgres_diagnostics_mock(self):
        mock_doctor = db_doctor.DbDoctor(
            sqlite_paths=[self.tmpdir],
            pg_data_dir=os.path.join(self.tmpdir, "pgdata"),
            mock=True,
        )
        pg_check = mock_doctor.check_postgres()
        self.assertEqual(pg_check["status"], "healthy")
        self.assertEqual(pg_check["corrupted_blocks"], 0)

        pg_repair = mock_doctor.repair_postgres()
        self.assertEqual(pg_repair["status"], "repaired")
        self.assertEqual(pg_repair["action"], "reindex_database")

    def test_overall_diagnostics_report(self):
        self._create_healthy_sqlite("sys.sqlite")
        mock_doctor = db_doctor.DbDoctor(
            sqlite_paths=[self.tmpdir],
            pg_data_dir=os.path.join(self.tmpdir, "pgdata"),
            mock=True,
        )
        report = mock_doctor.run_diagnostics(repair=False, db_type="all")
        self.assertEqual(report["overall_status"], "healthy")
        self.assertEqual(report["mode"], "check")
        self.assertIn("sqlite_databases", report)
        self.assertIn("postgres", report)


# ======================================================================
# from tests/test-db-migrate.py
# ======================================================================
_MIGRATE_PATH = os.path.join(_ROOT, "usr", "libexec", "mios", "db", "mios-db-migrate.py")
spec_migrate = importlib.util.spec_from_file_location("db_migrate", _MIGRATE_PATH)
if spec_migrate and spec_migrate.loader:
    db_migrate = importlib.util.module_from_spec(spec_migrate)
    sys.modules[spec_migrate.name] = db_migrate
    spec_migrate.loader.exec_module(db_migrate)
else:
    raise ImportError(f"Could not load db_migrate module from {_MIGRATE_PATH}")


class TestDbMigrate(unittest.TestCase):
    """Validates migration discovery, SHA-256 hashing, transactional application, and rollback handling."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios-db-migrate-test-")
        self._create_sample_migrations()
        self.migrator = db_migrate.DbMigrator(
            migrations_dir=self.tmpdir,
            db_name="test_mios",
            mock=True,
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _create_sample_migrations(self):
        m1 = os.path.join(self.tmpdir, "0001_init.sql")
        with open(m1, "w", encoding="utf-8") as f:
            f.write("CREATE TABLE users (id serial primary key, name text);")

        m2 = os.path.join(self.tmpdir, "0002_add_index.sql")
        with open(m2, "w", encoding="utf-8") as f:
            f.write("CREATE INDEX idx_users_name ON users(name);")

    def test_load_migrations_and_checksum(self):
        migrations = self.migrator.load_migrations()
        self.assertEqual(len(migrations), 2)
        self.assertEqual(migrations[0].version, 1)
        self.assertEqual(migrations[0].name, "init")
        self.assertEqual(migrations[1].version, 2)
        self.assertEqual(migrations[1].name, "add_index")
        for m in migrations:
            self.assertEqual(len(m.checksum), 64)

    def test_initial_pending_status(self):
        status = self.migrator.check_status()
        self.assertEqual(len(status), 2)
        self.assertEqual(status[0]["state"], "pending")
        self.assertEqual(status[1]["state"], "pending")
        self.assertIsNone(status[0]["recorded_checksum"])

    def test_migration_application_and_ledger_recording(self):
        """Invariant check: Every applied migration MUST record version ID and SHA-256 checksum."""
        report = self.migrator.migrate()
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["total_applied"], 2)

        post_status = self.migrator.check_status()
        self.assertEqual(len(post_status), 2)
        for s in post_status:
            self.assertEqual(s["state"], "applied")
            self.assertIsNotNone(s["recorded_checksum"])
            self.assertEqual(s["checksum"], s["recorded_checksum"])

    def test_checksum_mismatch_detection(self):
        """Detects if a previously applied migration script was modified post-application."""
        self.migrator.migrate()

        m1 = os.path.join(self.tmpdir, "0001_init.sql")
        with open(m1, "w", encoding="utf-8") as f:
            f.write("CREATE TABLE users (id serial primary key, name text, TAMPERED text);")

        status = self.migrator.check_status()
        self.assertEqual(status[0]["state"], "checksum_mismatch")

        with self.assertRaises(RuntimeError) as ctx:
            self.migrator.migrate()
        self.assertIn("checksum mismatch", str(ctx.exception).lower())

    def test_transaction_rollback_on_failure(self):
        """Invariant check: Migration errors must trigger transaction rollback."""
        m3_bad = os.path.join(self.tmpdir, "0003_bad_migration.sql")
        with open(m3_bad, "w", encoding="utf-8") as f:
            f.write("SYNTAX_ERROR_IN_MIGRATION; INVALID SQL BLOCK;")

        with self.assertRaises(RuntimeError) as ctx:
            self.migrator.migrate()
        self.assertIn("ROLLED BACK", str(ctx.exception))

        applied = self.migrator.get_applied_migrations()
        self.assertNotIn(3, applied)

    def test_dry_run_mode(self):
        dry_migrator = db_migrate.DbMigrator(
            migrations_dir=self.tmpdir,
            db_name="test_mios",
            dry_run=True,
            mock=True,
        )
        report = dry_migrator.migrate()
        self.assertEqual(report["status"], "completed")
        for detail in report["applied_details"]:
            self.assertEqual(detail["status"], "dry_run")

        status = dry_migrator.check_status()
        for s in status:
            self.assertEqual(s["state"], "pending")

    def test_real_repo_migrations(self):
        """Verifies the actual migrations in usr/share/mios/postgres/migrations/ parse cleanly."""
        repo_migrations_dir = os.path.join(_ROOT, "usr", "share", "mios", "postgres", "migrations")
        real_migrator = db_migrate.DbMigrator(
            migrations_dir=repo_migrations_dir,
            db_name="mios",
            mock=True,
        )
        migrations = real_migrator.load_migrations()
        self.assertGreaterEqual(len(migrations), 3)
        versions = [m.version for m in migrations]
        self.assertEqual(versions, sorted(versions))
        report = real_migrator.migrate()
        self.assertEqual(report["status"], "completed")
        self.assertGreaterEqual(report["total_applied"], 3)


# ======================================================================
# from tests/test-db-ssot-materialize.py
# ======================================================================
_MAT_PATH = os.path.join(_ROOT, "usr", "libexec", "mios", "materialize-config-toml.py")
spec_mat = importlib.util.spec_from_file_location("materialize_config_toml", _MAT_PATH)
if spec_mat and spec_mat.loader:
    mat = importlib.util.module_from_spec(spec_mat)
    sys.modules[spec_mat.name] = mat
    spec_mat.loader.exec_module(mat)
else:
    raise ImportError(f"Could not load materialize-config-toml module from {_MAT_PATH}")


try:
    import tomllib
except ImportError:
    import tomli as tomllib

_LIB_DIR = os.path.join(_ROOT, "usr", "lib", "mios")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)
import mios_toml
import mios_db_config


class _MockCursor:
    def __init__(self, query_results):
        self.query_results = query_results
        self._current_result = []
        self._iter = iter(self._current_result)

    def execute(self, query, params=None):
        q_lower = query.lower()
        if "from config_kv" in q_lower:
            if "key = '_defaults'" in q_lower:
                res = self.query_results.get("verbs_defaults", [])
            else:
                if params and params[0] == 0:
                    res = [r for r in self.query_results.get("config_kv", []) if r[3] == 0]
                elif params and params[0] == 1:
                    res = [r for r in self.query_results.get("config_kv", []) if r[3] == 1]
                else:
                    res = self.query_results.get("config_kv", [])
        elif "from package_set" in q_lower:
            if params and params[0] == 0:
                res = [r for r in self.query_results.get("package_set", []) if r[4] == 0]
            elif params and params[0] == 1:
                res = [r for r in self.query_results.get("package_set", []) if r[4] == 1]
            else:
                res = self.query_results.get("package_set", [])
        elif "from domain_verb" in q_lower:
            res = self.query_results.get("domain_verb", [])
        elif "from verb" in q_lower:
            res = self.query_results.get("verb", [])
        else:
            res = []
        self._current_result = res
        self._iter = iter(self._current_result)

    def fetchall(self):
        return list(self._current_result)

    def fetchone(self):
        try:
            return next(self._iter)
        except StopIteration:
            return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class _MockConnection:
    def __init__(self, query_results):
        self.query_results = query_results

    def cursor(self):
        return _MockCursor(self.query_results)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class TestDBSSOTMaterialize(unittest.TestCase):
    """Validates TOML key escaping, value formatting, list/dict serialization, and integrity."""

    def test_key_escaping(self):
        self.assertEqual(mat.escape_toml_key("simple_key"), "simple_key")
        self.assertEqual(mat.escape_toml_key("hyphen-key-123"), "hyphen-key-123")
        self.assertEqual(mat.escape_toml_key("dotted.key"), '"dotted.key"')
        self.assertEqual(mat.escape_toml_key("space key"), '"space key"')

    def test_value_formatting(self):
        self.assertEqual(mat.format_toml_value(True), "true")
        self.assertEqual(mat.format_toml_value(False), "false")
        self.assertEqual(mat.format_toml_value(8600), "8600")
        self.assertEqual(mat.format_toml_value("test_val"), '"test_val"')
        self.assertEqual(mat.format_toml_value(["a", "b", 123]), '["a", "b", 123]')
        self.assertEqual(mat.format_toml_value({"port": 8640, "host": "127.0.0.1"}), '{host = "127.0.0.1", port = 8640}')

    def _sample_db_data(self):
        return {
            "config_kv": [
                ("ports", "forge_http", 8080, 0),
                ("ports", "forge_http", 9090, 1),
                ("identity", "username", "mios", 0),
                ("identity", "username", "", 1),
                ("packages", "sections", json.dumps(["base", "security", "dev_overlay"]), 0),
                ("verbs", "_defaults", '{"tier": "common", "permission": "read"}', 0),
            ],
            "package_set": [
                ("base", "System", json.dumps(["audit", "firewalld"]), True, 0, ""),
                ("security", "Sec", json.dumps(["fapolicyd", "usbguard"]), True, 0, ""),
                ("dev_overlay", "Dev", json.dumps(["base", "security", "utils"]), True, 0, ""),
            ],
            "domain_verb": [
                ("fs", "Filesystem verbs", ["find_file", "file_edit"]),
            ],
            "verb": [
                ("find_file", "find_file <query>", "Find files", "common", "read", "mios-find",
                 json.dumps({"query": {"type": "string", "desc": "Search pattern"}}),
                 "filesystem", json.dumps(["find_file foo"]), "qwen", False,
                 json.dumps(["ff", "locate"]), "fs_read", 2, 1000),
            ]
        }

    def test_materialize_roundtrip_complete(self):
        """Asserts that every vendor section is retained and packages contains all package sub-tables."""
        conn = _MockConnection(self._sample_db_data())
        cfg = mat.materialize_from_db(conn, merged=True)
        toml_out = mat.emit_toml(cfg)
        parsed = tomllib.loads(toml_out)

        self.assertEqual(len(parsed), _VENDOR_TABLES, f"Expected {_VENDOR_TABLES} top-level tables, got {len(parsed)}")
        self.assertIn("packages", parsed)
        self.assertGreaterEqual(len(parsed["packages"]), 53, "Packages must retain all 53 package entries")
        self.assertIn("base", parsed["packages"])
        self.assertEqual(parsed["packages"]["base"]["pkgs"], ["audit", "firewalld"])
        self.assertIn("dev_overlay", parsed["packages"])
        self.assertIn("sections", parsed["packages"]["dev_overlay"])
        self.assertEqual(parsed["packages"]["dev_overlay"]["sections"], ["base", "security", "utils"])

    def test_materialize_layering_override(self):
        """Seed Layer 0 with forge_http = 8080 and Layer 1 with forge_http = 9090; assert 9090."""
        conn = _MockConnection(self._sample_db_data())
        cfg = mat.materialize_from_db(conn, merged=True)
        self.assertEqual(cfg["ports"]["forge_http"], 9090)

    def test_json_string_scalars_preserve_type_and_content(self):
        values = ["1.0", "42", "true", "null", '{"nested":true}', "[1,2]", "", 'quoted "text"']
        fixture = {"config_kv": [("a2a", f"value_{index}", json.dumps(value), 0)
                                  for index, value in enumerate(values)]}
        cursor = _MockCursor(fixture)
        original_execute = cursor.execute
        queries = []
        def capture(query, params=None):
            queries.append(query)
            original_execute(query, params)
        cursor.execute = capture
        connection = _MockConnection(fixture)
        connection.cursor = lambda: cursor
        cfg = mat.materialize_from_db(connection, merged=True)
        self.assertIn("value::text", queries[0])
        parsed = tomllib.loads(mat.emit_toml(cfg))
        for index, value in enumerate(values):
            actual = parsed["a2a"][f"value_{index}"]
            self.assertIsInstance(actual, str)
            self.assertEqual(value, actual)

    def test_materialize_layer_specific(self):
        """Asserts layer-specific materialization with --layer 0 vs --layer 1."""
        conn = _MockConnection(self._sample_db_data())
        cfg_0 = mat.materialize_from_db(conn, layer=0, merged=False)
        self.assertEqual(cfg_0["ports"]["forge_http"], 8080)
        cfg_1 = mat.materialize_from_db(conn, layer=1, merged=False)
        self.assertEqual(cfg_1["ports"]["forge_http"], 9090)

    def test_materialize_empty_string_no_clobber(self):
        """Seed Layer 0 with username = 'mios' and Layer 1 with username = ''; assert 'mios' retained."""
        conn = _MockConnection(self._sample_db_data())
        cfg = mat.materialize_from_db(conn, merged=True)
        self.assertEqual(cfg["identity"]["username"], "mios")

    def test_verbs_no_crash_on_empty_or_string_defaults(self):
        """Verify verbs serialize cleanly without AttributeError when _defaults is string-valued."""
        bad_defaults_data = {
            "config_kv": [
                ("verbs", "_defaults", '"{}"', 0),
            ],
            "verb": [
                ("simple_verb", "simple()", "Simple test verb", "common", "read", "echo 1",
                 None, None, None, None, False, None, None, 0, 0),
            ]
        }
        conn = _MockConnection(bad_defaults_data)
        cfg = mat.materialize_from_db(conn, merged=True)
        self.assertIn("verbs", cfg)
        self.assertIn("simple_verb", cfg["verbs"])

    def test_verb_fields_retention(self):
        """Verify all verb metadata fields are retained upon materialization."""
        conn = _MockConnection(self._sample_db_data())
        cfg = mat.materialize_from_db(conn, merged=True)
        self.assertIn("verbs", cfg)
        self.assertIn("find_file", cfg["verbs"])
        v = cfg["verbs"]["find_file"]
        self.assertEqual(v["cmd"], "mios-find")
        self.assertEqual(v["sig"], "find_file <query>")
        self.assertEqual(v["desc"], "Find files")
        self.assertEqual(v["section"], "filesystem")
        self.assertEqual(v["examples"], ["find_file foo"])
        self.assertEqual(v["model_name"], "qwen")
        self.assertEqual(v["hidden_aliases"], ["ff", "locate"])
        self.assertEqual(v["conflict_group"], "fs_read")
        self.assertEqual(v["parallel_limit"], 2)
        self.assertEqual(v["max_result_chars"], 1000)
        self.assertIn("params", v)
        self.assertIn("query", v["params"])

    def test_materialize_offline_fallback(self):
        """Verify graceful fallback to mios_toml.load_merged() produces TOML with every vendor table."""
        fallback_toml = mat.materialize_fallback(merged=True)
        self.assertGreater(len(fallback_toml), 1000)
        parsed = tomllib.loads(fallback_toml)
        self.assertEqual(len(parsed), _VENDOR_TABLES)
        self.assertIn("packages", parsed)
        self.assertGreaterEqual(len(parsed["packages"]), 53)

    def test_is_db_authoritative_preserves_packages_and_tables(self):
        """Verify mios_toml.load_merged() preserves every vendor table and package set when db authoritative."""
        old_auth = mios_db_config.is_db_authoritative
        old_load = mios_db_config.load_db_config
        try:
            mios_db_config.is_db_authoritative = lambda: True
            mios_db_config.load_db_config = lambda: {
                "ports": {"categories": {"forge": {"base": 9200, "stride": 10, "members": ["forge_http"]}}},
                "packages": {},
            }
            mios_toml.clear_cache()
            merged = mios_toml.load_merged()
            self.assertEqual(len(merged), _VENDOR_TABLES)
            self.assertIn("packages", merged)
            self.assertGreaterEqual(len(merged["packages"]), 53)
            self.assertIn("dev_overlay", merged["packages"])
            self.assertIn("sections", merged["packages"]["dev_overlay"])
            self.assertEqual(merged["ports"]["forge_http"], 9200)
        finally:
            mios_db_config.is_db_authoritative = old_auth
            mios_db_config.load_db_config = old_load
            mios_toml.clear_cache()



class TestNativeResolverDBOverlay(unittest.TestCase):
    """The optional native baseline must obey the same DB precedence as TOML."""

    def setUp(self):
        mios_toml.clear_cache()
        self.addCleanup(mios_toml.clear_cache)
        self.vendor = {
            "ports": {"categories": {"forge": {
                "base": 8400, "stride": 10, "members": ["forge_http"],
            }}},
            "packages": {"dev_overlay": {"sections": ["editor"]}},
            "vendor_only": {"keep": True},
        }

    def test_database_overlay_after_native_or_file_baseline(self):
        for native in (True, False):
            for packages in ({}, {"db_only": {"sections": ["shell"]}}, None):
                with self.subTest(native=native, packages=packages):
                    baseline = copy.deepcopy(self.vendor)
                    database = {
                        "ports": {"categories": {"forge": {"base": 9200}}},
                        "packages": packages,
                    }
                    mios_toml.clear_cache()
                    with patch.object(mios_toml, "_native_resolver_json", return_value={"merged": baseline} if native else None), \
                         patch.object(mios_toml, "layer_paths", return_value=["fixture"]), \
                         patch.object(mios_toml, "_load_one", return_value=baseline) as read_layer, \
                         patch.object(mios_db_config, "is_db_authoritative", return_value=True), \
                         patch.object(mios_db_config, "load_db_config", return_value=database) as read_db:
                        merged = mios_toml.load_merged()
                        self.assertEqual(merged["ports"]["forge_http"], 9200)
                        self.assertEqual(merged["packages"]["dev_overlay"], self.vendor["packages"]["dev_overlay"])
                        self.assertTrue(merged["vendor_only"]["keep"])
                        if packages:
                            self.assertIn("db_only", merged["packages"])
                        self.assertIs(mios_toml.load_merged(), merged)
                        read_db.assert_called_once_with()
                        self.assertEqual(read_layer.call_count, 0 if native else 1)

    def test_native_baseline_when_database_disabled_or_unavailable(self):
        for authoritative, database in ((False, {}), (True, {}), (True, OSError("offline fixture"))):
            with self.subTest(authoritative=authoritative, database=database):
                mios_toml.clear_cache()
                with patch.object(mios_toml, "_native_resolver_json", return_value={"merged": copy.deepcopy(self.vendor)}), \
                     patch.object(mios_db_config, "is_db_authoritative", return_value=authoritative), \
                     patch.object(mios_db_config, "load_db_config", return_value=database, side_effect=database if isinstance(database, Exception) else None) as read_db:
                    merged = mios_toml.load_merged()
                    self.assertEqual(merged["ports"]["forge_http"], 8400)
                    self.assertTrue(merged["vendor_only"]["keep"])
                    self.assertEqual(read_db.call_count, int(authoritative))

    def test_explicit_layers_bypass_native_database_and_cache(self):
        mios_toml._LOAD_MERGED_CACHE = {"cached": True}
        with patch.object(mios_toml, "_native_resolver_json", side_effect=AssertionError("unexpected native read")), \
             patch.object(mios_db_config, "is_db_authoritative", side_effect=AssertionError("unexpected DB read")) as read_db, \
             patch.object(mios_toml, "_load_one", return_value=self.vendor) as read_layer:
            merged = mios_toml.load_merged(layers=["explicit"])
            self.assertEqual(merged["ports"]["forge_http"], 8400)
            self.assertNotIn("cached", merged)
            read_layer.assert_called_once_with("explicit")
            read_db.assert_not_called()
            self.assertEqual(mios_toml._LOAD_MERGED_CACHE, {"cached": True})


# ======================================================================
# from tests/test-pg-events.py
# ======================================================================
_AGENT_PIPE_DIR = os.path.join(_ROOT, "usr", "lib", "mios", "agent-pipe")
if _AGENT_PIPE_DIR not in sys.path:
    sys.path.insert(0, _AGENT_PIPE_DIR)
from mios_pg_events import EventBus, AgentEvent


class TestPgEvents(unittest.TestCase):
    """Tests for task_286: mios_pg_events -- PostgreSQL LISTEN/NOTIFY event bus."""

    def test_inject_and_dispatch(self):
        """Injected events are dispatched to subscribers within SLA."""
        bus = EventBus(dry_run=True)
        received = []

        async def handler(evt: AgentEvent):
            received.append(evt)

        bus.subscribe(handler)
        bus.inject({"table": "tasks", "op": "INSERT", "row_id": 42})
        events = asyncio.run(bus.run_once(timeout_s=0.1))
        self.assertEqual(len(events), 1)
        self.assertEqual(received[0].payload["table"], "tasks")
        self.assertEqual(received[0].payload["row_id"], 42)

    def test_multiple_subscribers(self):
        """Multiple handlers all receive each event."""
        bus = EventBus(dry_run=True)
        counts = [0, 0]

        async def h1(evt):
            counts[0] += 1

        async def h2(evt):
            counts[1] += 1

        bus.subscribe(h1)
        bus.subscribe(h2)
        bus.inject({"op": "NOTIFY"})
        asyncio.run(bus.run_once())
        self.assertEqual(counts, [1, 1], f"Expected [1,1] got {counts}")

    def test_no_events_returns_empty(self):
        """run_once() with no injected events returns empty list quickly."""
        bus = EventBus(dry_run=True)
        events = asyncio.run(bus.run_once(timeout_s=0.05))
        self.assertEqual(events, [])


# ======================================================================
# from tests/test-pg-replica.py
# ======================================================================
_REPLICA_PATH = os.path.join(_ROOT, "usr", "libexec", "mios", "db", "mios-pg-replica.py")
spec_replica = importlib.util.spec_from_file_location("pg_replica", _REPLICA_PATH)
if spec_replica and spec_replica.loader:
    pg_replica = importlib.util.module_from_spec(spec_replica)
    sys.modules[spec_replica.name] = pg_replica
    spec_replica.loader.exec_module(pg_replica)
else:
    raise ImportError(f"Could not load pg_replica module from {_REPLICA_PATH}")


class TestPgReplica(unittest.TestCase):
    """Validates replication provisioning, WAL lag calculation, fencing enforcement, and promotion."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios-pg-replica-test-")
        self.data_dir = os.path.join(self.tmpdir, "data")
        self.fence_dir = os.path.join(self.tmpdir, "fencing")
        self.manager = pg_replica.PgReplicaManager(
            primary_host="10.0.0.1",
            primary_port=5432,
            replica_host="10.0.0.2",
            replica_port=5432,
            slot_name="test_slot",
            data_dir=self.data_dir,
            fence_dir=self.fence_dir,
            max_lag_ms=50.0,
            mock=True,
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_provision_replica(self):
        res = self.manager.provision_replica()
        self.assertEqual(res["status"], "success")
        self.assertTrue(res["primary_conninfo_written"])
        standby_signal = os.path.join(self.data_dir, "standby.signal")
        auto_conf = os.path.join(self.data_dir, "postgresql.auto.conf")
        self.assertTrue(os.path.isfile(standby_signal))
        self.assertTrue(os.path.isfile(auto_conf))
        with open(auto_conf, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("10.0.0.1", content)
            self.assertIn("test_slot", content)

    def test_replication_status_and_lag(self):
        status = self.manager.get_replication_status()
        self.assertEqual(status["status"], "active")
        self.assertTrue(status["is_healthy"])
        self.assertLessEqual(status["lag_ms"], 50.0)
        self.assertEqual(status["sync_state"], "streaming")

    def test_health_check_within_threshold(self):
        health = self.manager.health_check()
        self.assertTrue(health["healthy"])
        self.assertIn("healthy", health["reason"])

    def test_fencing_primary(self):
        self.assertFalse(self.manager.is_primary_fenced())
        fence_record = self.manager.fence_primary(reason="planned_failover")
        self.assertEqual(fence_record["status"], "fenced")
        self.assertTrue(self.manager.is_primary_fenced())

        ok = self.manager.unfence_primary()
        self.assertTrue(ok)
        self.assertFalse(self.manager.is_primary_fenced())

    def test_promotion_blocked_when_unfenced(self):
        """Invariant check: Promotion MUST fail if primary is not fenced (split-brain prevention)."""
        self.manager.unfence_primary()
        self.assertFalse(self.manager.is_primary_fenced())

        with self.assertRaises(RuntimeError) as ctx:
            self.manager.promote_replica(force_unfenced=False)
        self.assertIn("Old primary is NOT fenced", str(ctx.exception))

    def test_promotion_succeeds_when_fenced(self):
        self.manager.provision_replica()
        self.manager.fence_primary()
        self.assertTrue(self.manager.is_primary_fenced())

        res = self.manager.promote_replica(force_unfenced=False)
        self.assertEqual(res["status"], "promoted")
        self.assertTrue(res["primary_fenced"])

    def test_promotion_force_unfenced_override(self):
        self.manager.provision_replica()
        self.manager.unfence_primary()
        res = self.manager.promote_replica(force_unfenced=True)
        self.assertEqual(res["status"], "promoted")


# ======================================================================
# from tests/test-pg-vacuum.py
# ======================================================================
_DB_LIBEXEC_DIR = os.path.join(_ROOT, "usr", "libexec", "mios", "db")
if _DB_LIBEXEC_DIR not in sys.path:
    sys.path.insert(0, _DB_LIBEXEC_DIR)
from pg_vacuum_tuner import PGVacuumTuner


class TestPGVacuumTuner(unittest.TestCase):
    """Unit tests for MiOS PostgreSQL autovacuum tuner and pg_cron HNSW reindexer."""

    def setUp(self):
        self.tuner = PGVacuumTuner(dry_run=True)

    def test_render_pg_conf(self):
        conf = self.tuner.render_pg_conf()
        self.assertIn("autovacuum = on", conf)
        self.assertIn("autovacuum_vacuum_scale_factor = 0.05", conf)
        self.assertIn("wal_compression = 'zstd'", conf)
        self.assertIn("max_parallel_maintenance_workers = 4", conf)

    def test_render_pg_cron_reindex_sql(self):
        sql = self.tuner.render_pg_cron_reindex_sql()
        self.assertIn("REINDEX TABLE CONCURRENTLY", sql)
        self.assertIn("system_logs_rag", sql)
        self.assertIn("agent_memories", sql)
        self.assertIn("0 3 * * *", sql)


# ======================================================================
# from tests/test-pgvector-hnsw.py
# ======================================================================
_AI_LIB_DIR = os.path.join(_ROOT, "usr", "lib", "mios", "ai")
if _AI_LIB_DIR not in sys.path:
    sys.path.insert(0, _AI_LIB_DIR)
from pgvector_hnsw import MAX_KNN_SEARCH_MS, MIN_RECALL_ACCURACY_PCT, PgVectorHNSWManager


class TestPgVectorHNSW(unittest.TestCase):
    """Automated unit test suite for MiOS PgVector HNSW Manager."""

    def setUp(self):
        self.mgr = PgVectorHNSWManager(dry_run=True)

    def test_schema_sql_specifies_halfvec_and_partitioning(self):
        """Test generated SQL uses halfvec(1536) and partitioned tables."""
        sql = self.mgr.generate_partition_schema_sql()
        self.assertIn("halfvec(1536)", sql)
        self.assertIn("PARTITION BY LIST", sql)
        self.assertIn("USING hnsw", sql)

    def test_sub_5ms_knn_search_latency_and_high_recall(self):
        """Test kNN search executes in <5ms with >98% recall accuracy."""
        res = self.mgr.execute_knn_query("test_vec_01", k=10)
        self.assertLess(res.search_latency_ms, MAX_KNN_SEARCH_MS)
        self.assertGreaterEqual(res.recall_accuracy_pct, MIN_RECALL_ACCURACY_PCT)
        self.assertGreaterEqual(res.memory_reduction_pct, 70.0)


# ======================================================================
# from tests/test-pgvector-optimize.py
# ======================================================================
_OPTIMIZE_PATH = os.path.join(_ROOT, "usr", "libexec", "mios", "db", "mios-pgvector-optimize.py")
spec_optimize = importlib.util.spec_from_file_location("pgvector_optimize", _OPTIMIZE_PATH)
if spec_optimize and spec_optimize.loader:
    pgvector_optimize = importlib.util.module_from_spec(spec_optimize)
    sys.modules[spec_optimize.name] = pgvector_optimize
    spec_optimize.loader.exec_module(pgvector_optimize)
else:
    raise ImportError(f"Could not load pgvector_optimize module from {_OPTIMIZE_PATH}")


class TestPgVectorOptimize(unittest.TestCase):
    """Validates dead tuple statistics retrieval, concurrent index reindexing, and full optimization cycle."""

    def setUp(self):
        self.optimizer = pgvector_optimize.PgVectorOptimizer(
            db="mios",
            host="127.0.0.1",
            port=5432,
            user="postgres",
            parallel=4,
            dry_run=False,
            mock=True,
        )

    def test_dead_tuple_discovery(self):
        stats = self.optimizer.get_dead_tuples()
        self.assertIsInstance(stats, list)
        self.assertGreater(len(stats), 0)
        table_names = [s["table_name"] for s in stats]
        self.assertIn("knowledge", table_names)
        self.assertIn("agent_memory", table_names)
        for entry in stats:
            self.assertIn("live_tuples", entry)
            self.assertIn("dead_tuples", entry)
            self.assertIn("dead_tuple_pct", entry)

    def test_vector_index_discovery(self):
        indexes = self.optimizer.get_vector_indexes()
        self.assertIsInstance(indexes, list)
        self.assertGreater(len(indexes), 0)
        index_names = [i["index_name"] for i in indexes]
        self.assertIn("knowledge_emb_hnsw", index_names)
        self.assertIn("agent_memory_emb_hnsw", index_names)
        for idx in indexes:
            self.assertEqual(idx["index_type"], "hnsw")
            self.assertGreater(idx["index_bytes"], 0)

    def test_vacuum_analyze_tables(self):
        results = self.optimizer.vacuum_analyze_tables(["knowledge", "agent_memory"])
        self.assertEqual(len(results), 2)
        for res in results:
            self.assertEqual(res["status"], "success")
            self.assertIn("VACUUM (ANALYZE, PARALLEL 4)", res["command"])
            self.assertIsNone(res["error"])

    def test_concurrent_reindex_invariant(self):
        """Invariant check: Reindex operations MUST be CONCURRENT to avoid blocking read queries."""
        results = self.optimizer.reindex_vector_indexes_concurrently()
        self.assertGreater(len(results), 0)
        for res in results:
            self.assertEqual(res["status"], "success")
            self.assertTrue(
                res["command"].startswith("REINDEX INDEX CONCURRENTLY "),
                f"Command '{res['command']}' violates non-blocking concurrency invariant!",
            )

    def test_full_optimization_report(self):
        report = self.optimizer.optimize()
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["database"], "mios")
        self.assertGreater(report["tables_vacuumed"], 0)
        self.assertGreater(report["indexes_reindexed"], 0)
        self.assertIn("dead_tuple_stats", report)
        self.assertIn("total_elapsed_ms", report)

    def test_dry_run_mode(self):
        dry_optimizer = pgvector_optimize.PgVectorOptimizer(
            db="mios",
            host="127.0.0.1",
            port=5432,
            user="postgres",
            parallel=2,
            dry_run=True,
            mock=True,
        )
        report = dry_optimizer.optimize()
        self.assertEqual(report["status"], "completed")
        self.assertTrue(report["dry_run"])
        for v in report["vacuum_details"]:
            self.assertEqual(v["status"], "dry_run")
        for r in report["reindex_details"]:
            self.assertEqual(r["status"], "dry_run")


def main() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
