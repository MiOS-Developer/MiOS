#!/usr/bin/env python3
# AI-hint: Automated unit test suite for WS-DURA unified log aggregation pipeline streaming journald events to pgvector.
# AI-related: usr/libexec/mios/log/mios-log-streamer, usr/lib/systemd/system/mios-log-streamer.service
"""Automated tests for WS-DURA unified journald log streaming to pgvector (task_355 / task_2588)."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import math
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_STREAMER_PATH = os.path.join(_ROOT, "usr", "libexec", "mios", "log", "mios-log-streamer")

loader = importlib.machinery.SourceFileLoader("log_streamer", _STREAMER_PATH)
spec = importlib.util.spec_from_loader("log_streamer", loader)
if spec and spec.loader:
    log_streamer = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = log_streamer
    spec.loader.exec_module(log_streamer)
else:
    raise ImportError(f"Could not load log_streamer module from {_STREAMER_PATH}")

def cosine_similarity(v1: list[float], v2: list[float]) -> float:
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = math.sqrt(sum(a * a for a in v1))
    n2 = math.sqrt(sum(b * b for b in v2))
    return dot / (n1 * n2) if (n1 > 0 and n2 > 0) else 0.0

class TestLogStreamer(unittest.TestCase):
    """Validates journal record parsing, priority filtering, 768-dim embeddings, SQL batch formatting, and cursor persistence."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="mios_test_log_streamer_")

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_journal_record_priority_filtering(self):
        """
        Verify that priority <= 3 (emerg, alert, crit, err) are accepted,
        while priority > 3 (warning, notice, info, debug) are discarded to avoid embedding saturation.
        """
        # Critical error log (priority 2 = CRIT)
        crit_raw = {
            "PRIORITY": "2",
            "_SYSTEMD_UNIT": "mios-llm-light.service",
            "MESSAGE": "CUDA out of memory error during KV slot allocation",
            "__REALTIME_TIMESTAMP": "1724698800000000",
            "_PID": "10442",
            "_HOSTNAME": "mios-node-01",
        }
        parsed_crit = log_streamer.parse_journal_record(crit_raw, max_priority=3)
        self.assertIsNotNone(parsed_crit)
        self.assertEqual(parsed_crit["unit"], "mios-llm-light.service")
        self.assertEqual(parsed_crit["priority"], 2)
        self.assertEqual(parsed_crit["message"], "CUDA out of memory error during KV slot allocation")
        self.assertEqual(parsed_crit["metadata"]["pid"], "10442")

        # Verbose info log (priority 6 = INFO, should be skipped)
        info_raw = {
            "PRIORITY": "6",
            "_SYSTEMD_UNIT": "systemd.service",
            "MESSAGE": "Periodic timer tick heartbeat OK",
        }
        parsed_info = log_streamer.parse_journal_record(info_raw, max_priority=3)
        self.assertIsNone(parsed_info, "Info level log should be filtered out")

        # Warning log (priority 4 = WARNING, should be skipped with max_priority=3)
        warn_raw = {
            "PRIORITY": "4",
            "_SYSTEMD_UNIT": "sshd.service",
            "MESSAGE": "Connection closed by authenticating user",
        }
        parsed_warn = log_streamer.parse_journal_record(warn_raw, max_priority=3)
        self.assertIsNone(parsed_warn, "Warning level log should be filtered out")

    def test_deterministic_768_dim_embeddings(self):
        msg = "Fatal: CephFS OSD daemon failed to acquire lock on /dev/nvme0n1"
        emb = log_streamer.generate_deterministic_embedding(msg, dim=768)

        self.assertEqual(len(emb), 768)
        # Verify L2 normalization: sqrt(sum(x^2)) ~= 1.0
        norm = math.sqrt(sum(x * x for x in emb))
        self.assertAlmostEqual(norm, 1.0, places=3)

        # Batch embedding generation
        batch_texts = [
            "Error: PostgreSQL connection pool exhausted",
            "Critical: Thermal throttling active on GPU 0",
        ]
        batch_embs = log_streamer.generate_embeddings_batch(batch_texts, mock_embeddings=True)
        self.assertEqual(len(batch_embs), 2)
        self.assertEqual(len(batch_embs[0]), 768)
        self.assertEqual(len(batch_embs[1]), 768)

    def test_sql_insert_formatting(self):
        records = [
            {
                "unit": "mios-agent-pipe.service",
                "priority": 3,
                "message": "Failed to connect to Hermes gateway at 127.0.0.1:8642: Connection refused",
                "ts": "2026-08-26T19:00:00+00:00",
                "origin_node": "local",
                "metadata": {"pid": "8821", "hostname": "mios-dev"},
                "emb": [0.01] * 768,
            }
        ]

        sql = log_streamer.format_sql_insert(records)
        self.assertIn("INSERT INTO system_logs", sql)
        self.assertIn("mios-agent-pipe.service", sql)
        self.assertIn("Connection refused", sql)
        self.assertIn("::jsonb", sql)
        self.assertIn("::vector", sql)

    def test_cursor_load_and_save(self):
        cursor_file = os.path.join(self.test_dir, "cursor.state")
        test_cursor = "s=548a349b81f34f719001;i=1a4b;b=9d832"

        self.assertIsNone(log_streamer.load_cursor(cursor_file))

        log_streamer.save_cursor(cursor_file, test_cursor)
        loaded = log_streamer.load_cursor(cursor_file)
        self.assertEqual(loaded, test_cursor)

    def test_stream_from_input_file(self):
        log_file = os.path.join(self.test_dir, "journal_sample.jsonl")
        sample_logs = [
            {"PRIORITY": "3", "_SYSTEMD_UNIT": "systemd-networkd", "MESSAGE": "Failed to configure interface eth0"},
            {"PRIORITY": "6", "_SYSTEMD_UNIT": "cron", "MESSAGE": "Job completed successfully"},
            {"PRIORITY": "1", "_SYSTEMD_UNIT": "kernel", "MESSAGE": "Kernel panic: unable to handle kernel paging request"},
        ]

        with open(log_file, "w", encoding="utf-8") as f:
            for item in sample_logs:
                f.write(json.dumps(item) + "\n")

        streamed = list(log_streamer.stream_journal_records(input_file=log_file, max_priority=3))
        self.assertEqual(len(streamed), 2)
        self.assertEqual(streamed[0]["unit"], "systemd-networkd")
        self.assertEqual(streamed[1]["unit"], "kernel")
        self.assertEqual(streamed[1]["priority"], 1)

    def test_end_to_end_batch_processing(self):
        cursor_file = os.path.join(self.test_dir, "test_cursor.state")
        batch = [
            {
                "unit": "mios-backup-remote.service",
                "priority": 3,
                "message": "Remote endpoint s3://backup-bucket unreachable after 3 retries",
                "ts": "2026-08-26T19:15:00+00:00",
                "cursor": "cursor_marker_999",
                "metadata": {"pid": "5512"},
            }
        ]

        success = log_streamer.process_log_batch(
            batch=batch,
            mock_embeddings=True,
            mock_db=True,
            cursor_path=cursor_file,
        )

        self.assertTrue(success)
        self.assertEqual(len(batch[0]["emb"]), 768)
        # Verify cursor was saved
        saved_cursor = log_streamer.load_cursor(cursor_file)
        self.assertEqual(saved_cursor, "cursor_marker_999")

def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestLogStreamer)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1

if __name__ == "__main__":
    sys.exit(main())
