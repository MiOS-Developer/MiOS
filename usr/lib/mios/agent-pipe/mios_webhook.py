#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# AI-hint: HMAC-SHA256 authenticated webhook receiver and idempotent agent_inbox queue (task_461).
# AI-doc: usr/share/doc/mios/manual/ch04-the-agentic-ai-stack.md
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sys
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger("mios-webhook")


class WebhookReceiver:
    """Ingests and queues incoming webhooks with HMAC-SHA256 verification and deduplication."""

    def __init__(self, secret: Optional[str] = None) -> None:
        self.secret = secret or os.environ.get("MIOS_WEBHOOK_SECRET", "")
        # In-memory deduplication set for fast checks / fallback when DB offline
        self._processed_keys: set[str] = set()

    def verify_signature(self, raw_body: bytes, signature_header: Optional[str], secret: Optional[str] = None) -> bool:
        """Verifies HMAC-SHA256 signature (e.g. X-Hub-Signature-256)."""
        sec = secret if secret is not None else self.secret
        if not sec:
            # If no secret configured, fail-safe unless explicitly empty string in test
            return False

        if not signature_header:
            return False

        sig_val = signature_header.strip()
        if sig_val.startswith("sha256="):
            sig_val = sig_val[7:]

        mac = hmac.new(sec.encode("utf-8"), raw_body, hashlib.sha256)
        expected = mac.hexdigest()

        return hmac.compare_digest(expected.lower(), sig_val.lower())

    def compute_idempotency_key(self, source: str, event_type: str, raw_body: bytes) -> str:
        """Computes deterministic SHA256 idempotency key for payload deduplication."""
        h = hashlib.sha256()
        h.update(source.encode("utf-8"))
        h.update(b":")
        h.update(event_type.encode("utf-8"))
        h.update(b":")
        h.update(raw_body)
        return h.hexdigest()

    async def ingest_webhook(
        self,
        raw_body: bytes,
        headers: Dict[str, str],
        source: str = "generic",
        event_type: Optional[str] = None,
        secret: Optional[str] = None,
        db_pool: Any = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Validates, deduplicates, and queues incoming webhook payload.
        Returns: (success: bool, status_msg: str, details: dict)
        """
        # Resolve signature header
        sig_header = (
            headers.get("x-hub-signature-256") or
            headers.get("X-Hub-Signature-256") or
            headers.get("x-signature-256") or
            headers.get("X-Signature-256")
        )

        sec = secret if secret is not None else self.secret
        if sec:
            if not self.verify_signature(raw_body, sig_header, sec):
                log.warning("Webhook authentication failed for source=%s", source)
                return False, "invalid_signature", {"error": "HMAC-SHA256 signature verification failed"}

        # Resolve event type
        evt = event_type or headers.get("x-github-event") or headers.get("X-GitHub-Event") or headers.get("x-event-type") or "event"

        # Compute idempotency key
        idemp_key = self.compute_idempotency_key(source, evt, raw_body)

        # Parse payload
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception:
            payload = {"raw": raw_body.decode("utf-8", errors="replace")}

        # Check memory deduplication
        if idemp_key in self._processed_keys:
            log.info("Duplicate webhook event skipped: %s (source=%s)", idemp_key, source)
            return True, "duplicate", {"idempotency_key": idemp_key, "source": source, "event_type": evt}

        self._processed_keys.add(idemp_key)

        # Insert into PostgreSQL agent_inbox if pool provided
        if db_pool:
            try:
                async with db_pool.acquire() as conn:
                    query = """
                        INSERT INTO agent_inbox (idempotency_key, source, event_type, payload, status)
                        VALUES ($1, $2, $3, $4, 'pending')
                        ON CONFLICT (idempotency_key) DO UPDATE SET retry_count = agent_inbox.retry_count + 1
                        RETURNING id, status;
                    """
                    row = await conn.fetchrow(query, idemp_key, source, evt, json.dumps(payload))
                    return True, "queued", {
                        "idempotency_key": idemp_key,
                        "source": source,
                        "event_type": evt,
                        "inbox_id": row["id"] if row else None,
                        "status": row["status"] if row else "queued",
                    }
            except Exception as e:
                log.error("Failed persisting webhook to agent_inbox: %s", e)

        return True, "queued", {
            "idempotency_key": idemp_key,
            "source": source,
            "event_type": evt,
            "status": "queued",
        }
