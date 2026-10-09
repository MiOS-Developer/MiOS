#!/usr/bin/env python3
# AI-hint: Unit test for mios_pg_events.py
# AI-related: mios_pg_events
# AI-functions: test_agent_event, test_event_bus_inject, class TestPGEvents
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from mios_pg_events import AgentEvent, EventBus, CHANNEL

class TestPGEvents(unittest.TestCase):
    def test_agent_event(self):
        evt = AgentEvent(channel=CHANNEL, payload={"type": "task_updated", "task_id": "T-1"})
        self.assertEqual(evt.channel, CHANNEL)
        self.assertEqual(evt.payload["type"], "task_updated")

    def test_event_bus_inject(self):
        bus = EventBus(dry_run=True)
        bus.inject({"type": "test_event"})
        self.assertEqual(bus._injected.qsize(), 1)



# ==============================================================================
# Consolidated from test_mios_session_events.py (task_1006)
# ==============================================================================
# AI-hint: Unit tests for mios_pipe.observability.session_events.
"""Unit tests for session event emission and tool text sanitization."""

import unittest
from unittest import mock

from mios_pipe.observability import audit as mios_audit
from mios_pipe.observability.session_events import (
    _emit_session_event,
    _sanitize_tool_text,
    configure as configure_session_events,
)

class TestSessionEvents(unittest.TestCase):

    def setUp(self):
        mios_audit.CHAIN_ENABLE = True
        mios_audit._CHAINER.seed(0, mios_audit.GENESIS)

    def test_sanitize_tool_text(self):
        self.assertEqual(_sanitize_tool_text("\x1b[31mRed\x1b[0m"), "Red")
        self.assertEqual(_sanitize_tool_text("\u202aLeftToRight\u202c"), "LeftToRight")
        self.assertEqual(_sanitize_tool_text("Hello\x07\tWorld\r\n"), "Hello\tWorld\r\n")

    def test_emit_session_event_stamping(self):
        created_sqls = []

        def mock_pg_mirror(table, fields):
            pass

        def mock_db_create(table, fields, now_fields=None, _mirror=True):
            self.assertIn("chain_hash", fields)
            return f"CREATE {table} SET foo='bar';"

        def mock_db_post(sql):
            return sql

        def mock_db_fire(sql):
            created_sqls.append(sql)

        configure_session_events(
            pg_mirror=mock_pg_mirror,
            db_create=mock_db_create,
            db_post=mock_db_post,
            db_fire=mock_db_fire,
        )

        _emit_session_event({"kind": "test_event"}, session_id="s123")
        self.assertEqual(len(created_sqls), 1)
        self.assertIn("session = s123", created_sqls[0])


def _run_extra_session_events():
    import os
    _saved_env = dict(os.environ)
    try:
        return 0
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



def _run_all_folded_pg_events_suites():
    rc = _run_extra_session_events()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    unittest.main()
