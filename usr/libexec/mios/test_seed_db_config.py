#!/usr/bin/env python3
# AI-hint: Unit and regression test suite for seed_db_config functionality.
# AI-functions: test_get_pg_config_defaults, TestSeedDbConfig

import os
import sys
import json
import unittest

sys.path.insert(0, os.path.dirname(__file__))

# Import seed-db-config module dynamically or directly
import importlib.util
spec = importlib.util.spec_from_file_location("seed_db_config", os.path.join(os.path.dirname(__file__), "seed-db-config.py"))
seed_db_config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed_db_config)

class TestSeedDbConfig(unittest.TestCase):
    def test_get_pg_config_defaults(self):
        cfg = seed_db_config.get_pg_config()
        self.assertEqual(cfg["user"], os.environ.get("MIOS_PGVECTOR_USER", "mios"))
        self.assertIn("port", cfg)


class TestLogicalPhaseCatalog(unittest.TestCase):
    def setUp(self):
        from pathlib import Path
        import tempfile
        self.root = Path(__file__).resolve().parents[3]
        self.manifest = self.root / "usr/share/mios/mios.toml"
        self.temporary = tempfile.TemporaryDirectory(prefix="mios-seed-phases-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def test_real_native_catalog_preserves_native_members_and_dependency_order(self):
        import tomllib
        original = tomllib.loads(self.manifest.read_text())["build"]["phases"]["list"]
        rows = seed_db_config.get_build_phases(str(self.manifest))
        self.assertEqual([row["script"] for row in rows], [phase["script"] for phase in original])
        self.assertEqual(sum(row.get("native") is not None for row in rows), 2)
        for index, row in enumerate(rows):
            self.assertEqual(row["deps"], [rows[index-1]["script"]] if index else [])
            self.assertEqual(row["stage"], "container")

    def test_catalog_error_and_explicit_missing_native_binary_fail_before_connection(self):
        from unittest.mock import MagicMock, patch
        import types
        invalid = self.directory / "invalid.toml"
        invalid.write_text(self.manifest.read_text().replace('native = "chrony"', 'native = "unknown"'))
        psycopg = types.SimpleNamespace(connect=MagicMock())
        for environment in [
            {"MIOS_TOML": str(invalid)},
            {"MIOS_TOML": str(self.manifest), "MIOS_MIOSD_BIN": str(self.directory/"missing")},
            {"MIOS_TOML": str(self.manifest), "MIOS_MIOSD_BIN": ""},
        ]:
            with self.subTest(environment=environment), patch.dict(sys.modules, {"psycopg": psycopg}), patch.dict(os.environ, environment):
                self.assertEqual(seed_db_config.main(), 1)
                psycopg.connect.assert_not_called()

    def test_seed_records_all_logical_phases_with_real_native_provider_and_firstboot(self):
        from unittest.mock import MagicMock, patch
        import types
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        psycopg = types.SimpleNamespace(connect=MagicMock())
        psycopg.connect.return_value.__enter__.return_value = connection
        with patch.dict(sys.modules, {"psycopg": psycopg}), patch.dict(os.environ, {"MIOS_TOML": str(self.manifest)}):
            self.assertEqual(seed_db_config.main(), 0)
        records = [call.args[1] for call in cursor.execute.call_args_list
                   if "INSERT INTO build_phase" in call.args[0]]
        containers = [row for row in records if len(row) == 3]
        expected = seed_db_config.get_build_phases(str(self.manifest))
        self.assertEqual([row[1] for row in containers], [row["script"] for row in expected])
        self.assertIn("42-chrony-render.sh", [row[1] for row in containers])
        self.assertIn("43-nut-render.sh", [row[1] for row in containers])
        for actual, row in zip(containers, expected):
            self.assertEqual(json.loads(actual[2]), row["deps"])
        firstboot = [row[0] for row in records if len(row) == 1]
        expected_firstboot = {"firstboot/"+path.name for path in (self.root/"automation/firstboot").glob("*.sh")}
        self.assertEqual(set(firstboot), expected_firstboot)

    def test_unavailable_database_dependency_still_fails_honestly(self):
        from unittest.mock import patch
        with patch.dict(sys.modules, {"psycopg": None}):
            self.assertEqual(seed_db_config.main(), 2)

if __name__ == "__main__":
    unittest.main()
