#!/usr/bin/env python3
# AI-hint: Unit and regression test suite for seed_db_config functionality.
# AI-functions: test_get_pg_config_defaults, TestSeedDbConfig

import os
import sys
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

if __name__ == "__main__":
    unittest.main()
