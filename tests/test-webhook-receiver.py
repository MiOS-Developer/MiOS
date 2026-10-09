#!/usr/bin/env python3
# AI-hint: Automated unit test suite for HMAC-SHA256 authenticated webhook receiver and agent_inbox queue (task_461).
# AI-doc: usr/share/doc/mios/manual/ch04-the-agentic-ai-stack.md
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import pathlib
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_AGENT_PIPE_DIR = os.path.join(_ROOT, "usr", "lib", "mios", "agent-pipe")
if _AGENT_PIPE_DIR not in sys.path:
    sys.path.insert(0, _AGENT_PIPE_DIR)

from mios_webhook import WebhookReceiver


class TestWebhookReceiver(unittest.TestCase):
    """Validates HMAC-SHA256 verification, idempotency key generation, and deduplication."""

    def setUp(self):
        self.secret = "test-webhook-secret-key-32chars!"
        self.receiver = WebhookReceiver(secret=self.secret)
        self.payload = b'{"action": "opened", "issue": {"number": 42, "title": "Test Bug"}}'

    def _sign(self, body: bytes, secret: str) -> str:
        mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256)
        return "sha256=" + mac.hexdigest()

    def test_signature_verification_success(self):
        sig = self._sign(self.payload, self.secret)
        self.assertTrue(self.receiver.verify_signature(self.payload, sig))
        # Raw hex without prefix should also match
        raw_hex = sig[7:]
        self.assertTrue(self.receiver.verify_signature(self.payload, raw_hex))

    def test_signature_verification_tampered_body(self):
        sig = self._sign(self.payload, self.secret)
        tampered = b'{"action": "opened", "issue": {"number": 43, "title": "Tampered"}}'
        self.assertFalse(self.receiver.verify_signature(tampered, sig))

    def test_signature_verification_wrong_secret(self):
        sig = self._sign(self.payload, "wrong-secret-key-xyz")
        self.assertFalse(self.receiver.verify_signature(self.payload, sig))

    def test_signature_missing_or_empty(self):
        self.assertFalse(self.receiver.verify_signature(self.payload, None))
        self.assertFalse(self.receiver.verify_signature(self.payload, ""))

    def test_idempotency_key_deterministic(self):
        k1 = self.receiver.compute_idempotency_key("github", "issues", self.payload)
        k2 = self.receiver.compute_idempotency_key("github", "issues", self.payload)
        self.assertEqual(k1, k2)
        self.assertEqual(len(k1), 64)

        # Different event type gives different key
        k3 = self.receiver.compute_idempotency_key("github", "push", self.payload)
        self.assertNotEqual(k1, k3)

    def test_ingest_webhook_flow_and_deduplication(self):
        async def run_test():
            sig = self._sign(self.payload, self.secret)
            headers = {
                "x-hub-signature-256": sig,
                "x-github-event": "issues",
            }
            # First ingestion -> queued
            ok, status, details = await self.receiver.ingest_webhook(
                raw_body=self.payload,
                headers=headers,
                source="github",
                event_type="issues",
            )
            self.assertTrue(ok)
            self.assertEqual(status, "queued")
            self.assertIn("idempotency_key", details)

            # Second identical ingestion -> duplicate
            ok2, status2, details2 = await self.receiver.ingest_webhook(
                raw_body=self.payload,
                headers=headers,
                source="github",
                event_type="issues",
            )
            self.assertTrue(ok2)
            self.assertEqual(status2, "duplicate")
            self.assertEqual(details["idempotency_key"], details2["idempotency_key"])

            # Ingestion with invalid signature -> rejected
            bad_headers = {"x-hub-signature-256": "sha256=deadbeefcafebabe"}
            ok3, status3, _ = await self.receiver.ingest_webhook(
                raw_body=self.payload,
                headers=bad_headers,
                source="github",
            )
            self.assertFalse(ok3)
            self.assertEqual(status3, "invalid_signature")

        asyncio.run(run_test())

    def test_sql_schema_inbox_table(self):
        schema_path = os.path.join(_ROOT, "usr", "share", "mios", "postgres", "schema-init.sql")
        content = pathlib.Path(schema_path).read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS agent_inbox", content)
        self.assertIn("idempotency_key", content)
        self.assertIn("agent_inbox_idempotency", content)


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestWebhookReceiver)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
