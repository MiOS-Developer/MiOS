#!/usr/bin/env python3
# AI-hint: Automated unit test suite for Jensen-Shannon divergence anomaly alarm and PostgreSQL threat_events vector sink (task_456).
# AI-doc: usr/share/doc/mios/manual/ch17-defense-in-depth-hardening.md
from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_ANOMALY_BIN = os.path.join(_ROOT, "usr", "lib", "mios", "agent-pipe", "mios_net_anomaly.py")
_SCHEMA_SQL = os.path.join(_ROOT, "usr", "share", "mios", "postgres", "schema-init.sql")


class TestNetAnomaly(unittest.TestCase):
    """Validates JSD network anomaly detector and threat_events vector schema."""

    def test_files_exist_and_executable(self):
        self.assertTrue(os.path.isfile(_ANOMALY_BIN), f"Missing {_ANOMALY_BIN}")
        self.assertTrue(os.access(_ANOMALY_BIN, os.X_OK), f"Not executable: {_ANOMALY_BIN}")
        self.assertTrue(os.path.isfile(_SCHEMA_SQL), f"Missing {_SCHEMA_SQL}")

    def test_threat_events_schema_ddl(self):
        with open(_SCHEMA_SQL, "r", encoding="utf-8") as f:
            sql = f.read()

        self.assertIn("CREATE TABLE IF NOT EXISTS threat_events", sql)
        self.assertIn("divergence    double precision NOT NULL", sql)
        self.assertIn("emb           vector(768)", sql)
        self.assertIn("CREATE INDEX IF NOT EXISTS threat_events_emb_hnsw", sql)
        self.assertIn("CREATE INDEX IF NOT EXISTS threat_events_divergence", sql)
        self.assertIn("ALTER TABLE threat_events ADD COLUMN IF NOT EXISTS emb_model varchar(128)", sql)

    def test_mock_normal_traffic(self):
        res = subprocess.run([_ANOMALY_BIN, "--mock-normal", "--json"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Normal check failed: {res.stderr}")
        data = json.loads(res.stdout)
        self.assertFalse(data["is_anomaly"])
        self.assertLess(data["divergence"], 0.35)
        self.assertNotIn("threat_event", data)

    def test_mock_anomalous_traffic(self):
        res = subprocess.run([_ANOMALY_BIN, "--mock-anomaly", "--json"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Anomaly check failed: {res.stderr}")
        data = json.loads(res.stdout)
        self.assertTrue(data["is_anomaly"])
        self.assertGreater(data["divergence"], 0.35)
        self.assertIn("threat_event", data)
        self.assertEqual(data["threat_event"]["emb_dim"], 0)
        self.assertTrue(data["threat_event"]["embedding_pending"])
        self.assertEqual(data["threat_event"]["event_type"], "network_anomaly")

    def test_persistence_queues_real_embeddings(self):
        spec = importlib.util.spec_from_file_location("net_anomaly", _ANOMALY_BIN)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        driver = MagicMock()
        connection = driver.connect.return_value.__enter__.return_value
        cursor = connection.cursor.return_value.__enter__.return_value
        summary = {"flows": [{"dst_port": 4444, "packets": 900}]}
        with patch.dict(sys.modules, {"psycopg": driver}):
            result = module.NetAnomalyDetector(pg_dsn="fixture").analyze_flow_window(summary)
        event = result["threat_event"]
        self.assertTrue(event["stored"])
        self.assertTrue(event["embedding_pending"])
        self.assertEqual(event["emb_dim"], 0)
        sql, params = cursor.execute.call_args.args
        columns = sql.split("(", 1)[1].split(")", 1)[0].split(",")
        self.assertNotIn("emb", [column.strip() for column in columns])
        self.assertIn("NULL", sql, "embedding version must remain unset until inference succeeds")
        self.assertEqual(len(params), 5)
        self.assertEqual(json.loads(params[3]), summary)
        connection.commit.assert_called_once()
        driver.connect.side_effect = RuntimeError("database unavailable")
        with patch.dict(sys.modules, {"psycopg": driver}):
            failed = module.NetAnomalyDetector(pg_dsn="fixture").analyze_flow_window(summary)
        self.assertFalse(failed["threat_event"]["stored"])
        self.assertEqual(failed["threat_event"]["emb_dim"], 0)
        self.assertIn("storage_error", failed["threat_event"])


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestNetAnomaly)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
