#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_embed_backfill (WS-A2 embedding-version hygiene).
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_embed_backfill (WS-A2)."""

import sys

import mios_embed_backfill as bf

_fails = 0

def check(name: str, cond: bool, detail: str = "") -> None:
    global _fails
    tag = "PASS" if cond else "FAIL"
    if not cond:
        _fails += 1
    print(f"[{tag}] {name}" + (f" -- {detail}" if detail else ""))

def t_needs_reembed():
    cur = "nomic-768-v1"
    check("stale: emb present, old version -> reembed",
          bf.needs_reembed(True, "nomic-768-v0", cur) is True)
    check("stale: emb present, NULL version -> reembed",
          bf.needs_reembed(True, None, cur) is True)
    check("current: emb present, same version -> skip",
          bf.needs_reembed(True, cur, cur) is False)
    check("no vector: never reembed (left for embed-on-write)",
          bf.needs_reembed(False, "anything", cur) is False)
    check("whitespace-insensitive version compare",
          bf.needs_reembed(True, "  nomic-768-v1 ", cur) is False)

def t_select_sql():
    sql, params = bf.select_candidates_sql("knowledge", "v2", limit=100)
    check("select: targets the table", "FROM knowledge" in sql)
    check("select: only emb-present rows", "emb IS NOT NULL" in sql)
    check("select: version mismatch clause", "emb_version IS DISTINCT FROM %(ver)s" in sql)
    check("select: parameterized (no literal version)", "v2" not in sql)
    check("select: params carry version + limit",
          params["ver"] == "v2" and params["lim"] == 100)
    _, p2 = bf.select_candidates_sql("agent_memory", "v2", limit=0)
    check("select: limit floored to >=1", p2["lim"] == 1)

def t_stamp_sql():
    sql = bf.stamp_version_sql("knowledge")
    check("stamp: UPDATE the table", sql.startswith("UPDATE knowledge"))
    check("stamp: writes emb+model+version", all(
        s in sql for s in ("emb = %(emb)s", "emb_model = %(model)s", "emb_version = %(ver)s")))
    check("stamp: keyed by id", "WHERE id = %(id)s" in sql)

def t_batches():
    check("batch: splits into chunks", bf.plan_batches(list(range(10)), 4) ==
          [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9]])
    check("batch: empty -> []", bf.plan_batches([], 5) == [])
    check("batch: size floored to >=1", len(bf.plan_batches([1, 2, 3], 0)) == 3)
    check("batch: single batch when small", bf.plan_batches([1, 2], 50) == [[1, 2]])

def t_summary():
    s = bf.summarize(125, 50)
    check("summary: candidate count", s["candidates"] == 125)
    check("summary: batch count (ceil)", s["batches"] == 3, f"{s}")
    check("summary: zero candidates -> 0 batches", bf.summarize(0, 50)["batches"] == 0)

def main() -> int:
    t_needs_reembed()
    t_select_sql()
    t_stamp_sql()
    t_batches()
    t_summary()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0



# ==============================================================================
# Consolidated from test_mios_backfill.py (task_1006)
# ==============================================================================
# AI-hint: Unit and regression test suite for mios_backfill functionality.
# AI-related: mios_pipe.memory.embed_backfill
# AI-functions: test_text_projections, execute_side_effect, TestMiosEmbedBackfill

import unittest
from unittest.mock import patch, AsyncMock, MagicMock
import mios_pipe.memory.embed_backfill as eb

class TestMiosEmbedBackfill(unittest.IsolatedAsyncioTestCase):

    @patch("mios_pipe.memory.pg.execute", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    async def test_threat_event_backfill(self, mock_client_cls, mock_execute):
        row = {"id": 71, "description": "Network flow anomaly"}
        client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = client
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"data": [{"embedding": [0.1] * 768}]}
        client.post.return_value = response
        mock_execute.side_effect = lambda sql, params=None, **kwargs: [row] if kwargs.get("fetch") else True
        with patch.dict(eb.PK_MAP, {"threat_events": "id"}, clear=True):
            result = await eb.run_backfill("new-vector-version")
        self.assertEqual(result, {"threat_events": 1})
        self.assertIn("SELECT id, description FROM threat_events", mock_execute.call_args_list[0].args[0])
        update = mock_execute.call_args_list[1]
        self.assertIn("UPDATE threat_events", update.args[0])
        self.assertIn("emb_model = %(model)s", update.args[0])
        self.assertEqual(update.args[1]["id"], 71)
        self.assertEqual(update.args[1]["ver"], "new-vector-version")
        self.assertEqual(client.post.call_args.kwargs["json"]["input"], "search_document: Network flow anomaly")
        self.assertIsNone(eb.get_text_projection("threat_events", {"description": " "}))

    @patch("mios_pipe.memory.pg.execute", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    async def test_threat_event_inference_failure_leaves_pending(self, mock_client_cls, mock_execute):
        client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = client
        response = MagicMock()
        response.status_code = 503
        client.post.return_value = response
        mock_execute.return_value = [{"id": 71, "description": "Network flow anomaly"}]
        with patch.dict(eb.PK_MAP, {"threat_events": "id"}, clear=True), patch("asyncio.sleep", new_callable=AsyncMock):
            result = await eb.run_backfill("new-vector-version")
        self.assertEqual(result, {"threat_events": 0})
        self.assertEqual(client.post.await_count, 3)
        self.assertEqual(mock_execute.await_count, 1, "failed inference must not write a vector or model stamp")

    def test_text_projections(self):
        s_row = {"name": "TestSkill", "description": "Doing cool things"}
        self.assertEqual(eb.get_text_projection("skill", s_row), "Skill: TestSkill\nDescription: Doing cool things")

        v_row = {
            "name": "test_verb",
            "desc_default": "A description of a verb",
            "model_name": "TestVerb",
            "examples": ["test standard usage", "another test"]
        }
        self.assertEqual(
            eb.get_text_projection("verb", v_row),
            "TestVerb: A description of a verb\nExample requests: test standard usage | another test"
        )

        tc_row = {
            "tool": "web_search",
            "args": {"query": "hello"},
            "result_preview": "some search results"
        }
        self.assertEqual(
            eb.get_text_projection("tool_call", tc_row),
            'Tool Call: web_search\nArguments: {"query": "hello"}\nResult: some search results'
        )

        de_row = {
            "path": "/etc/hosts",
            "kind": "file",
            "size": 128,
            "summary": "Local DNS mappings"
        }
        self.assertEqual(
            eb.get_text_projection("directory_entry", de_row),
            "File: /etc/hosts\nKind: file\nSize: 128 bytes\nSummary: Local DNS mappings"
        )

        ev_row = {
            "act_type": "critic",
            "summary": "Critic rejected action"
        }
        self.assertEqual(
            eb.get_text_projection("event", ev_row),
            "Event: critic\nSummary: Critic rejected action"
        )

        sess_row = {
            "title": "My Session",
            "meta": {"first_prompt": "Hello AI"}
        }
        self.assertEqual(
            eb.get_text_projection("session", sess_row),
            "Session Title: My Session\nPrompt: Hello AI"
        )

    @patch("mios_pipe.memory.pg.execute", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    async def test_run_backfill_success(self, mock_client_cls, mock_execute):
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"embedding": [0.1] * 768}
        mock_client.post.return_value = mock_response

        select_results = {
            "skill": [{"id": 1, "name": "skill_1", "description": "desc_1"}],
            "verb": [{"name": "verb_1", "desc_default": "desc_1", "examples": None, "model_name": None}],
            "tool_call": [{"id": 10, "tool": "tc_1", "args": "{}", "result_preview": "res", "output": None}],
            "directory_entry": [{"id": 100, "path": "p_1", "kind": "file", "size": 10, "summary": "sum_1"}],
            "event": [{"id": 500, "act_type": "critic", "summary": "Critic check"}],
            "session": [{"id": "sess_1", "meta": {"title": "Sess", "first_prompt": "Hello"}}]
        }

        def execute_side_effect(sql, params=None, fetch=False, **kwargs):
            if "SELECT" in sql:
                for k in select_results:
                    if f"FROM {k}" in sql:
                        return select_results[k]
                return []
            return True

        mock_execute.side_effect = execute_side_effect

        res = await eb.run_backfill("nomic-768-v1")

        self.assertEqual(res["skill"], 1)
        self.assertEqual(res["verb"], 1)
        self.assertEqual(res["tool_call"], 1)
        self.assertEqual(res["directory_entry"], 1)
        self.assertEqual(res["event"], 1)
        self.assertEqual(res["session"], 1)

    @patch("mios_pipe.memory.pg.execute", new_callable=AsyncMock)
    @patch("httpx.AsyncClient")
    async def test_run_backfill_fail_open(self, mock_client_cls, mock_execute):
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        mock_client.post.side_effect = Exception("Connection refused")

        select_results = {
            "skill": [{"id": 1, "name": "skill_1", "description": "desc_1"}]
        }

        def execute_side_effect(sql, params=None, fetch=False, **kwargs):
            if "SELECT" in sql:
                if "FROM skill" in sql:
                    return select_results["skill"]
                return []
            return None

        mock_execute.side_effect = execute_side_effect

        res = await eb.run_backfill("nomic-768-v1")
        self.assertEqual(res.get("skill"), 0)


def _run_extra_backfill():
    import os
    _saved_env = dict(os.environ)
    try:
        import unittest
        suite = unittest.TestSuite()
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestMiosEmbedBackfill))
        res = unittest.TextTestRunner().run(suite)
        return 0 if res.wasSuccessful() else 1
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



def _run_all_folded_embed_backfill_suites():
    rc = _run_extra_backfill()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _rc_main = main()
    _run_all_folded_embed_backfill_suites()
    sys.exit(_rc_main)
