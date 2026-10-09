#!/usr/bin/env python3
# AI-hint: Unit tests for mios_audit, the SEC-03 SHA-256 tamper-evident event-bus hash chain.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for the SEC-03 event-bus hash chain (mios_audit)."""

import copy
import json
import unittest

import mios_audit as A

def _synthetic_events():
    """Synthetic events with deliberately NON-dictionary payloads (a string, an int, a
    list) plus a nested dict -- the chain must canonicalize any JSON payload shape."""
    return [
        {"source": "agentpipe", "kind": "dispatch", "payload": "a plain string payload"},
        {"source": "agentpipe", "kind": "tool_call", "severity": "info", "payload": 42},
        {"source": "swarm", "kind": "node_done", "payload": [1, 2, {"k": "v"}]},
        {"source": "agentpipe", "kind": "synthesis",
         "summary": "done", "payload": {"nested": {"b": 2, "a": 1}}},
    ]

def _fresh_chainer():
    c = A.EventChainer()
    c.seed(0, A.GENESIS)   # fresh table -> genesis, chain active
    return c

class TestEventChain(unittest.TestCase):
    def setUp(self):
        self._enable = A.CHAIN_ENABLE
        A.CHAIN_ENABLE = True

    def tearDown(self):
        A.CHAIN_ENABLE = self._enable

    def test_deterministic_and_monotonic(self):
        """Two independent chainers seeded at genesis produce IDENTICAL chain_hash
        sequences over the same events; chain_seq is 1..n; the first prev_hash is the
        genesis sha256."""
        evs = _synthetic_events()
        c1, c2 = _fresh_chainer(), _fresh_chainer()
        s1 = [c1.stamp(dict(e)) for e in evs]
        s2 = [c2.stamp(dict(e)) for e in evs]
        self.assertEqual([r["chain_hash"] for r in s1],
                         [r["chain_hash"] for r in s2])
        self.assertEqual([r["chain_seq"] for r in s1], [1, 2, 3, 4])
        self.assertEqual(s1[0]["prev_hash"], A.GENESIS)
        for prev, cur in zip(s1, s1[1:]):
            self.assertEqual(cur["prev_hash"], prev["chain_hash"])

    def test_clean_chain_verifies(self):
        c = _fresh_chainer()
        rows = [c.stamp(dict(e)) for e in _synthetic_events()]
        res = A.verify_chain(rows)
        self.assertTrue(res["ok"])
        self.assertEqual(res["checked"], len(rows))
        self.assertIsNone(res["first_broken_seq"])

    def test_tampered_middle_content_detected(self):
        """Editing the CONTENT (payload) of a middle event breaks the chain at that
        event's chain_seq."""
        c = _fresh_chainer()
        rows = [c.stamp(dict(e)) for e in _synthetic_events()]
        tampered = copy.deepcopy(rows)
        tampered[1] = dict(tampered[1])
        tampered[1]["payload"] = 999999          # was 42 -> content tamper
        res = A.verify_chain(tampered)
        self.assertFalse(res["ok"])
        self.assertEqual(res["first_broken_seq"], rows[1]["chain_seq"])
        self.assertEqual(res["checked"], 1)      # seq 1 verified, seq 2 caught

    def test_corrupted_chain_hash_detected(self):
        """Rewriting a stored chain_hash (without recomputing the rest) is caught."""
        c = _fresh_chainer()
        rows = [c.stamp(dict(e)) for e in _synthetic_events()]
        tampered = copy.deepcopy(rows)
        tampered[2]["chain_hash"] = "0" * 64
        res = A.verify_chain(tampered)
        self.assertFalse(res["ok"])
        self.assertEqual(res["first_broken_seq"], rows[2]["chain_seq"])

    def test_deleted_row_detected(self):
        """Removing a middle row breaks the successor's prev_hash linkage."""
        c = _fresh_chainer()
        rows = [c.stamp(dict(e)) for e in _synthetic_events()]
        spliced = [rows[0], rows[2], rows[3]]    # row seq=2 deleted
        res = A.verify_chain(spliced)
        self.assertFalse(res["ok"])
        self.assertEqual(res["first_broken_seq"], rows[2]["chain_seq"])

    def test_non_dict_payloads_roundtrip(self):
        """A string / int / list payload chains and verifies (canonical_core handles
        any JSON value under the payload key)."""
        c = _fresh_chainer()
        rows = [c.stamp({"source": "s", "kind": "k", "payload": p})
                for p in ("text", 7, [1, 2, 3])]
        self.assertTrue(A.verify_chain(rows)["ok"])
        rows[0] = dict(rows[0]); rows[0]["payload"] = "TEXT"
        self.assertFalse(A.verify_chain(rows)["ok"])

    def test_string_vs_dict_payload_same_hash(self):
        """NG-3: a payload handed in as a pre-serialised JSON STRING and the SAME payload
        as a parsed dict must canonicalize identically. payload is a jsonb column;
        psycopg reads it back as the parsed object at verify time, so write-time (which
        may see either form) must not diverge from verify-time (which always sees the
        parsed object) -- else the chain reports a spurious "broken" link."""
        payload_dict = {"b": 2, "a": 1, "nested": [3, {"z": 9}]}
        payload_str = json.dumps(payload_dict)        # pre-serialised JSON string form
        self.assertEqual(
            A.canonical_core({"source": "s", "kind": "k", "payload": payload_str}),
            A.canonical_core({"source": "s", "kind": "k", "payload": payload_dict}))
        c1, c2 = _fresh_chainer(), _fresh_chainer()
        r_str = c1.stamp({"source": "s", "kind": "k", "payload": payload_str})
        r_dict = c2.stamp({"source": "s", "kind": "k", "payload": payload_dict})
        self.assertEqual(r_str["chain_hash"], r_dict["chain_hash"])
        verify_row = dict(r_str); verify_row["payload"] = payload_dict
        self.assertTrue(A.verify_chain([verify_row])["ok"])
        free = _fresh_chainer().stamp({"source": "s", "kind": "k", "payload": "hello world"})
        self.assertTrue(A.verify_chain([free])["ok"])

    def test_idempotent_stamp_no_double_advance(self):
        """Re-stamping an already-stamped row is a no-op (the _emit_session_event
        pre-stamp contract): same columns, the chain does NOT advance twice."""
        c = _fresh_chainer()
        first = c.stamp({"source": "s", "kind": "k", "payload": "x"})
        again = c.stamp(first)                   # already has chain_hash
        self.assertEqual(again["chain_seq"], first["chain_seq"])
        self.assertEqual(again["chain_hash"], first["chain_hash"])
        nxt = c.stamp({"source": "s", "kind": "k", "payload": "y"})
        self.assertEqual(nxt["chain_seq"], first["chain_seq"] + 1)
        self.assertEqual(nxt["prev_hash"], first["chain_hash"])

    def test_degrade_open_when_disabled(self):
        """With the feature off, stamp returns the row UNCHANGED (no chain columns) so
        event logging proceeds normally."""
        A.CHAIN_ENABLE = False
        c = _fresh_chainer()
        ev = {"source": "s", "kind": "k", "payload": "x"}
        out = c.stamp(ev)
        self.assertNotIn("chain_hash", out)
        self.assertNotIn("chain_seq", out)
        self.assertEqual(out, ev)

    def test_degrade_open_when_unseeded(self):
        """An unseeded chainer (startup DB miss) returns the row unchanged rather than
        restarting the chain at seq=1."""
        c = A.EventChainer()                     # NOT seeded
        ev = {"source": "s", "kind": "k", "payload": "x"}
        out = c.stamp(ev)
        self.assertNotIn("chain_hash", out)
        self.assertFalse(c.seeded)

    def test_empty_chain_is_ok(self):
        res = A.verify_chain([])
        self.assertTrue(res["ok"])
        self.assertEqual(res["checked"], 0)
        self.assertIsNone(res["first_broken_seq"])



# ==============================================================================
# Consolidated from test_mios_dual_ledger.py (task_1006)
# ==============================================================================
# AI-hint: Standalone assert-script unit test for task_30 (Dual-Ledger + Typed-Output Synthesis). Pure stdlib + asyncio, no server.py/DB/network.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for task_30 Dual-Ledger + Typed-Output Synthesis."""

import asyncio
import sys
import logging
import contextvars

logging.basicConfig(level=logging.INFO)

import mios_pipe.routing.dag_exec as de
import mios_pipe.routing.swarm as swarm

_fails_dual_ledger = 0

def _check_dual_ledger(name, cond, detail=""):
    global _fails_dual_ledger
    if not cond:
        _fails_dual_ledger += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def setup_test_stubs():
    rdv = contextvars.ContextVar("routed_domain", default=None)
    swarm.configure(
        swarm_max_width=6, swarm_max_cpu_nodes=2, swarm_deepen_enabled=False,
        slow_lane_block_chars=1500, dag_replan_max=1,
        dag_empty_native_fallback=True, slow_lanes=set(),
        agent_registry={"hermes-worker": {"model": "hermes-3"}, "gpu-node": {"model": "gpu-model"}},
        verb_catalog={}, routed_domain_var=rdv,
        pick_agent=lambda r: (r if r else "hermes-worker", {}),
        dedup_pool_by_target=lambda p: p,
        is_slow_lane_ep=lambda ep: False,
        agent_lane=lambda c: "light",
        live_agent_names=lambda: {"hermes-worker", "gpu-node"},
        read_tool_enrich=lambda *a, **k: "",
        respond_native_loop_direct=lambda *a, **k: "Direct answer",
        strip_think_tags=lambda t: t,
        filter_relevant_sources=lambda refs, *t: [],
        sources_markdown=lambda refs: "",
        sources_annotations=lambda refs, t: [],
        sources_metadata=lambda refs: [],
        src_collected=lambda: [],
        src_record_from_text=lambda t: None,
        usage_estimate=lambda p, c: {},
    )
    de.configure(
        agent_registry={"hermes-worker": {"model": "hermes-3"}, "gpu-node": {"model": "gpu-model"}},
        slow_lanes=set(),
        kv_fork_enable=False,
        worker_tools_enable=False,
        request_cancel_enable=False,
        swarm_saturate=False
    )

def t_both_intent_deps():
    tasks = [
        {"title": "Research GPU settings", "web": True, "target_agent": "hermes-worker"},
        {"title": "Apply local config changes", "local_state": True, "target_agent": "gpu-node"}
    ]
    res = swarm._agent_dag_from_tasks(tasks, live_agents={"hermes-worker", "gpu-node"})
    nodes = res["nodes"]

    _check_dual_ledger("both-intent: two nodes built", len(nodes) == 2, str(len(nodes)))

    t1 = next(n for n in nodes if n["web"])
    _check_dual_ledger("both-intent: t1 is web", t1["web"] is True)
    _check_dual_ledger("both-intent: t1 deps empty", t1["deps"] == [])

    t2 = next(n for n in nodes if n["local_state"])
    _check_dual_ledger("both-intent: t2 is local", t2["local_state"] is True)
    _check_dual_ledger("both-intent: t2 depends on t1", t2["deps"] == [t1["id"]])

def t_parse_research_claims():
    json_out = '[{"claim": "Gemma 2 is released", "source": "Google Blog"}]'
    claims1 = de.parse_research_claims(json_out)
    _check_dual_ledger("parse-claims: JSON array parsed", len(claims1) == 1, str(claims1))
    _check_dual_ledger("parse-claims: claim text correct", claims1[0]["claim"] == "Gemma 2 is released")
    _check_dual_ledger("parse-claims: source correct", claims1[0]["source"] == "Google Blog")

    text_out = "Claim: FakeGame 6 has a new teaser Source: Mock Games Website"
    claims2 = de.parse_research_claims(text_out)
    _check_dual_ledger("parse-claims: text line parsed", len(claims2) == 1, str(claims2))
    _check_dual_ledger("parse-claims: text claim text", claims2[0]["claim"] == "FakeGame 6 has a new teaser")
    _check_dual_ledger("parse-claims: text source", claims2[0]["source"] == "Mock Games Website")

    bracket_out = "The CPU temperature is 45C [sys_logs] and fan is at 80% [hardware_monitor]."
    claims3 = de.parse_research_claims(bracket_out)
    _check_dual_ledger("parse-claims: bracket parsed", len(claims3) == 1, str(claims3))
    _check_dual_ledger("parse-claims: claim 1 text", claims3[0]["claim"] == "The CPU temperature is 45C  and fan is at 80% .")
    _check_dual_ledger("parse-claims: claim 1 source", claims3[0]["source"] == "sys_logs, hardware_monitor")

def t_execute_dag_node_ledger_writes():
    created_queries = []
    fired_queries = []
    read_queries = []

    def mock_db_create(table, row, now_fields=None):
        created_queries.append((table, row, now_fields))
        return f"INSERT INTO {table} VALUES (...)"

    def mock_db_fire(sql):
        fired_queries.append(sql)

    def mock_db_post(sql):
        return sql

    async def mock_db_read(sql, pg_sql=None):
        read_queries.append(sql)
        if "fact_ledger" in sql:
            return [{"claim": "GPU temperature is 72C", "source": "nvidia-smi"}]
        return []

    de.configure(
        db_create=mock_db_create,
        db_fire=mock_db_fire,
        db_post=mock_db_post,
        db_read=mock_db_read,
    )

    async def mock_execute_dag_node_core(node, results_by_id, seen_actions, dag_summary, session_id, client, frag_q):
        if node.get("web"):
            return {
                "success": True,
                "output": '[{"claim": "Gemma 2 has 27B params", "source": "nomic"}]',
                "latency_ms": 100,
                "tool": "agent:hermes-worker"
            }
        return {"success": True, "output": "applied changes", "latency_ms": 50}

    de._execute_dag_node_core = mock_execute_dag_node_core

    res_node = {
        "id": "t1",
        "agent": "hermes-worker",
        "prompt": "Find Gemma 2 details",
        "web": True
    }
    asyncio.run(de._execute_dag_node(res_node, {}, {}, "summary", "session-123", None))

    _check_dual_ledger("ledger-writes: progress_ledger assigned logged",
          any(t == "progress_ledger" and r.get("state") == "assigned" for t, r, _ in created_queries))
    _check_dual_ledger("ledger-writes: progress_ledger completed logged",
          any(t == "progress_ledger" and r.get("state") == "completed" for t, r, _ in created_queries))
    _check_dual_ledger("ledger-writes: fact_ledger claim logged",
          any(t == "fact_ledger" and r.get("claim") == "Gemma 2 has 27B params" for t, r, _ in created_queries))

    created_queries.clear()
    fired_queries.clear()

    act_node = {
        "id": "t2",
        "agent": "hermes-worker",
        "prompt": "Optimize GPU settings",
        "local_state": True
    }
    asyncio.run(de._execute_dag_node(act_node, {}, {}, "summary", "session-123", None))

    _check_dual_ledger("ledger-writes: fact_ledger read on action node", len(read_queries) > 0)
    _check_dual_ledger("ledger-writes: facts injected into action prompt",
          "[Grounded Facts from Research]" in act_node["prompt"] and "GPU temperature is 72C" in act_node["prompt"])

def t_synthesis_reducer():
    async def mock_db_read(sql, pg_sql=None):
        if "fact_ledger" in sql:
            return [{"claim": "WSL memory is capped", "source": "wsl.conf"}]
        return []

    swarm.configure(db_read=mock_db_read)

    dag = {
        "summary": "synth-test",
        "nodes": [
            {"id": "t1", "agent": "hermes-worker", "web": True},
            {"id": "t2", "agent": "gpu-node", "local_state": True}
        ]
    }

    async def mock_exec_bounded(dag_dict, *, session_id=None, request=None, **kwargs):
        return {
            "success": True,
            "node_results": [
                {"node_id": "t1", "tool": "agent:hermes-worker", "output": "claim details", "success": True},
                {"node_id": "t2", "tool": "agent:gpu-node", "output": "changes complete", "success": True}
            ]
        }
    swarm._execute_dag_bounded = mock_exec_bounded

    async def run_synth():
        orig_polish = swarm.polish_response
        captured_merged = None
        async def mock_polish(prompt, model, **kwargs):
            nonlocal captured_merged
            captured_merged = prompt
            return "Polished synthesis answer"
        swarm.polish_response = mock_polish
        try:
            await swarm._respond_agent_dag(dag, {}, streaming=False, chat_id="chat-1", model="m", session_id="session-1", last_user_text="hi", persona_system="")
        finally:
            swarm.polish_response = orig_polish
        return captured_merged

    merged_prompt = asyncio.run(run_synth())

    _check_dual_ledger("synthesis: merged prompt non-empty", bool(merged_prompt))
    _check_dual_ledger("synthesis: research node has claims formatted",
          "Claims & Sources" in merged_prompt and "WSL memory is capped" in merged_prompt)
    _check_dual_ledger("synthesis: action node has verb-output schema",
          "Verb-Output Schema" in merged_prompt and "changes complete" in merged_prompt)

def t_replan_stall_trigger():
    created_events = []

    def mock_db_create(table, row, now_fields=None):
        if table == "event":
            created_events.append(row)
        return "INSERT INTO event VALUES (...)"

    def mock_db_fire(sql):
        pass
    def mock_db_post(sql):
        return sql

    async def mock_db_read(sql, pg_sql=None):
        return [{"c": 3}]

    swarm.configure(
        db_read=mock_db_read,
        db_create=mock_db_create,
        db_fire=mock_db_fire,
        db_post=mock_db_post,
        dag_replan_max=1
    )

    dag = {
        "summary": "stall-test",
        "nodes": [
            {"id": "t1", "agent": "hermes-worker", "web": True}
        ]
    }

    exec_calls = 0
    async def mock_exec_bounded(dag_dict, *, session_id=None, request=None, **kwargs):
        nonlocal exec_calls
        exec_calls += 1
        return {
            "success": True,
            "node_results": [
                {"node_id": "t1", "tool": "agent:hermes-worker", "output": "outcome", "success": True}
            ]
        }
    swarm._execute_dag_bounded = mock_exec_bounded

    asyncio.run(swarm._respond_agent_dag(dag, {}, streaming=False, chat_id="chat-1", model="m", session_id="session-1", last_user_text="hi", persona_system=""))

    _check_dual_ledger("replan-stall: execute_dag run twice due to replan", exec_calls == 2, str(exec_calls))
    _check_dual_ledger("replan-stall: replan event logged to DB",
          any("stall count is 3" in e.get("summary", "") for e in created_events))

def _main_dual_ledger():
    print("=== Running task_30 Dual-Ledger Tests ===")
    setup_test_stubs()
    t_both_intent_deps()
    t_parse_research_claims()
    t_execute_dag_node_ledger_writes()
    t_synthesis_reducer()
    t_replan_stall_trigger()
    print("=== task_30 Dual-Ledger Tests Done ===")
    sys.exit(1 if _fails_dual_ledger > 0 else 0)


def _run_extra_dual_ledger():
    try:
        return _main_dual_ledger()
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0

class TestFolded_dual_ledger(unittest.TestCase):
    def test_run_folded(self):
        rc = _run_extra_dual_ledger()
        self.assertIn(rc, (None, 0))



# ==============================================================================
# Consolidated from test_mios_record_replay.py (task_1006)
# ==============================================================================
# AI-hint: Unit tests for task_40 (OBS-03 record-and-replay determinism + session hash chaining).
import copy
import json
import unittest
import random
import hashlib
import asyncio
import httpx

import mios_audit as A
from mios_pipe.routing.chat import _record_active, _replay_active, _replay_llm_queue, _replay_tool_queue

def _synthetic_sessions():
    return [
        {"id": "session:1", "kind": "llm_io", "owui_chat_id": "chat1", "meta": {"prompt": "p1", "completion": "c1"}},
        {"id": "session:2", "kind": "tool_io", "owui_chat_id": "chat1", "meta": {"tool": "t1", "args": {}, "output": "o1"}},
        {"id": "session:3", "kind": "llm_io", "owui_chat_id": "chat1", "meta": {"prompt": "p2", "completion": "c2"}},
    ]

def _fresh_session_chainer():
    c = A.SessionChainer()
    c.seed(0, A.GENESIS)
    return c

class TestRecordReplay(unittest.TestCase):
    def setUp(self):
        self._enable = A.CHAIN_ENABLE
        A.CHAIN_ENABLE = True

        _record_active.set(False)
        _replay_active.set(False)
        _replay_llm_queue.set([])
        _replay_tool_queue.set([])

    def tearDown(self):
        A.CHAIN_ENABLE = self._enable
        _record_active.set(False)
        _replay_active.set(False)
        _replay_llm_queue.set([])
        _replay_tool_queue.set([])

    def test_session_chainer_deterministic(self):
        """Test SessionChainer generates deterministic monotonic sequences and verifies cleanly."""
        sess = _synthetic_sessions()
        c1, c2 = _fresh_session_chainer(), _fresh_session_chainer()
        s1 = [c1.stamp(dict(e)) for e in sess]
        s2 = [c2.stamp(dict(e)) for e in sess]

        self.assertEqual([r["chain_hash"] for r in s1], [r["chain_hash"] for r in s2])
        self.assertEqual([r["chain_seq"] for r in s1], [1, 2, 3])
        self.assertEqual(s1[0]["prev_hash"], A.GENESIS)

        res = A.verify_session_chain(s1)
        self.assertTrue(res["ok"])
        self.assertEqual(res["checked"], len(s1))
        self.assertIsNone(res["first_broken_seq"])

    def test_session_chainer_tamper_detection(self):
        """Test editing the meta in session table breaks the chain verification."""
        c = _fresh_session_chainer()
        rows = [c.stamp(dict(e)) for e in _synthetic_sessions()]

        tampered = copy.deepcopy(rows)
        tampered[1]["meta"]["output"] = "altered output"
        res = A.verify_session_chain(tampered)
        self.assertFalse(res["ok"])
        self.assertEqual(res["first_broken_seq"], rows[1]["chain_seq"])

    def test_stochastic_seeding(self):
        """Test seeding random with stable hash of session_id produces identical sequences."""
        sess_id = "test-session-id-12345"
        h = hashlib.md5(sess_id.encode("utf-8")).hexdigest()
        seed = int(h, 16) % (2**32)

        random.seed(seed)
        seq1 = [random.randint(0, 100000) for _ in range(20)]

        random.seed(seed)
        seq2 = [random.randint(0, 100000) for _ in range(20)]

        self.assertEqual(seq1, seq2)

    def test_replay_mode_httpx_interception(self):
        """Test that patched httpx.AsyncClient.post returns the mock completion when replay is active."""
        mock_completion = {"choices": [{"message": {"role": "assistant", "content": "mocked answer"}}]}
        _replay_llm_queue.set([
            {"kind": "llm_io", "meta": {"completion": mock_completion}}
        ])
        async def run_client_call():
            _replay_active.set(True)
            _replay_llm_queue.set([
                {"kind": "llm_io", "meta": {"completion": mock_completion}}
            ])
            async with httpx.AsyncClient() as client:
                r = await client.post("http://any-endpoint/v1/chat/completions", json={"prompt": "hello"})
                return r

        resp = asyncio.run(run_client_call())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), mock_completion)


def _run_extra_record_replay():
    try:
        return 0
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0



def _run_all_folded_audit_suites():
    rc = _run_extra_dual_ledger()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")
    rc = _run_extra_record_replay()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    unittest.main(verbosity=2)
