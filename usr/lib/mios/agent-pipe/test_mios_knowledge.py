#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_knowledge (refactor R6 KNOWLEDGE-cluster extraction). Pure stdlib, no server.p...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_knowledge (refactor R6). No DB / network / pytest."""

import asyncio
import re
import sys
import time

import mios_knowledge as k

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def t_recall_floor():
    k.configure(
        recall_possessive_re=re.compile(r"\b(my|mine|your|yours|our|ours)\b", re.I),
        knowledge_recall_min_score=0.62,
        knowledge_recall_pref_min_score=0.50,
    )
    check("recall_floor default (no possessive)",
          k._recall_floor("what is the capital of France") == 0.62,
          str(k._recall_floor("what is the capital of France")))
    check("recall_floor preference (possessive)",
          k._recall_floor("what is my favorite editor") == 0.50,
          str(k._recall_floor("what is my favorite editor")))
    k.configure(knowledge_recall_pref_min_score=0.99)
    check("recall_floor never above default",
          k._recall_floor("what is my X") == 0.62,
          str(k._recall_floor("what is my X")))
    k.configure(knowledge_recall_pref_min_score=0.50)

def t_recency_mult():
    k.configure(knowledge_rank_age=0.0, knowledge_recall_halflife_days=7.0)
    check("recency_mult inert when rank_age==0",
          k._recency_mult({"ts": time.time() - 9 * 86400}) == 1.0)

    k.configure(knowledge_rank_age=0.3, knowledge_recall_halflife_days=7.0)
    now = time.time()
    m_fresh = k._recency_mult({"last_access": now})
    m_halflife = k._recency_mult({"last_access": now - 7 * 86400})
    m_old = k._recency_mult({"last_access": now - 60 * 86400})
    check("recency_mult fresh ~1.0", abs(m_fresh - 1.0) < 1e-6, str(m_fresh))
    check("recency_mult newer > older within half-life", m_fresh > m_halflife > m_old,
          f"{m_fresh:.4f} {m_halflife:.4f} {m_old:.4f}")
    check("recency_mult at one half-life == (1-ra)+ra*0.5",
          abs(m_halflife - ((1 - 0.3) + 0.3 * 0.5)) < 1e-6, str(m_halflife))
    check("recency_mult bounded below by 1-rank_age", m_old >= (1 - 0.3) - 1e-9, str(m_old))
    check("recency_mult no timestamp -> 1.0", k._recency_mult({}) == 1.0)
    k.configure(knowledge_rank_age=0.0)  # restore inert for the blend test

def t_recall_blend():
    """_recall_knowledge_pg blended rerank: at equal cosine, a hot+satisfied+
    frequently-accessed row must outrank a cold+unsatisfied one. Recency held
    inert (rank_age==0) so the ordering isolates the cosine+outcome+tier+access
    blend. All deps supplied through the DI seam with async stubs -- no DB."""
    fired = []

    async def _embed_one(_q, *args, **kwargs):
        return [0.1] * 8

    class _Mem:
        async def retrieve(self, qv, *, table=None, k=None, owner=None, emb_version=None, rls_owner=None, **kwargs):
            return [
                {"id": 2, "score": 0.70, "q": "COLD question", "answer": "a2",
                 "ts": time.time(), "satisfied": False, "tier": "warm",
                 "access_count": 0},
                {"id": 1, "score": 0.70, "q": "HOT question", "answer": "a1",
                 "ts": time.time(), "satisfied": True, "tier": "hot",
                 "access_count": 10},
            ]

    def _db_fire(coro):
        fired.append(coro)
        try:
            coro.close()  # we don't run the bump; just don't leak the coro
        except Exception:
            pass

    async def _db_update(*a, **kw):
        return None

    k.configure(
        embed_one=_embed_one,
        memory=_Mem(),
        db_fire=_db_fire,
        db_update=_db_update,
        knowledge_table="knowledge",
        knowledge_recall_k=3,
        knowledge_recall_candidates=60,
        knowledge_recall_min_score=0.62,
        knowledge_rank_outcome=0.05,
        knowledge_rank_hot=0.03,
        knowledge_rank_access=0.02,
        knowledge_hot_threshold=5,
        knowledge_rank_age=0.0,
    )
    block = asyncio.run(k._recall_knowledge_pg("anything"))
    check("recall_pg returns a non-empty injectable block",
          isinstance(block, str) and "HOT question" in block and "COLD question" in block,
          repr(block[:80]))
    check("recall_pg blended order: hot+satisfied outranks cold+unsatisfied",
          block.index("HOT question") < block.index("COLD question"))
    check("recall_pg queued page-in bump", len(fired) >= 1, str(len(fired)))

    class _MemLow:
        async def retrieve(self, qv, *, table=None, k=None, owner=None, emb_version=None, rls_owner=None, **kwargs):
            return [{"id": 9, "score": 0.10, "q": "x", "answer": "y",
                     "ts": time.time()}]
    k.configure(memory=_MemLow())
    check("recall_pg below-floor -> ''", asyncio.run(k._recall_knowledge_pg("q")) == "")

class _FakeVar:
    """Minimal contextvar stand-in: .get([default]) -> the stored value."""
    def __init__(self, v):
        self._v = v
    def get(self, default=None):
        return self._v

def t_rls_owner():
    """_rls_owner: None unless [pgvector].rls_mode == 'enforce' AND a principal was
    forwarded. Drive _toml_section + _client_env_var directly (module globals)."""
    _orig_toml = k._toml_section
    _orig_env = k._client_env_var
    try:
        k._toml_section = lambda _s: {}
        k._client_env_var = _FakeVar({"user_name": "zqxprincipal"})
        check("rls_owner off -> None", k._rls_owner() is None, repr(k._rls_owner()))
        k._toml_section = lambda _s: {"rls_mode": "enforce"}
        check("rls_owner enforce+principal -> owner",
              k._rls_owner() == "zqxprincipal", repr(k._rls_owner()))
        k._client_env_var = _FakeVar({})
        check("rls_owner enforce+no-principal -> None",
              k._rls_owner() is None, repr(k._rls_owner()))
        k._client_env_var = _FakeVar({"user_email": "wbz@vorp"})
        check("rls_owner falls back to user_email",
              k._rls_owner() == "wbz@vorp", repr(k._rls_owner()))
    finally:
        k._toml_section = _orig_toml
        k._client_env_var = _orig_env

def t_recall_agent_memory():
    """_recall_agent_memory: default-off -> ''; enabled -> an injectable block of
    the durable facts above the score floor, scope-tagged for non-global scopes."""
    async def _embed_one(_q):
        return [0.2] * 8

    class _Mem:
        async def retrieve(self, qv, *, table=None, k=None, owner=None, emb_version=None, rls_owner=None):
            return [
                {"fact": "the zqxw codeword is vorpaline", "score": 0.91,
                 "scope": "global"},
                {"fact": "blixfap lives on port wibble", "score": 0.77,
                 "scope": "devbox"},
                {"fact": "below-floor noise", "score": 0.10, "scope": "global"},
            ]

    _orig_toml = k._toml_section
    try:
        k._toml_section = lambda _s: {}  # rls off -> _rls_owner() None (unscoped)
        k.configure(agent_memory_recall_enabled=False, pg_primary=True,
                    embed_one=_embed_one, memory=_Mem(),
                    agent_memory_recall_min_score=0.45)
        check("agent-memory recall default-off -> ''",
              asyncio.run(k._recall_agent_memory("anything")) == "")
        k.configure(agent_memory_recall_enabled=True,
                    agent_memory_table="agent_memory", agent_memory_recall_k=3)
        block = asyncio.run(k._recall_agent_memory("anything"))
        check("agent-memory recall surfaces above-floor facts",
              "vorpaline" in block and "blixfap" in block, repr(block[:80]))
        check("agent-memory recall drops below-floor row",
              "below-floor noise" not in block)
        check("agent-memory recall tags non-global scope",
              "(devbox)" in block and "(global)" not in block, repr(block[-80:]))
        k.configure(pg_primary=False)
        check("agent-memory recall no-PG -> ''",
              asyncio.run(k._recall_agent_memory("x")) == "")
        k.configure(pg_primary=True)
    finally:
        k._toml_section = _orig_toml

def t_recall_agent_memory_recency():
    """A7: agent_memory recall applies the SHARED blended rerank (not flat cosine).
    With rank_age>0 a recently-saved fact OUTRANKS a stale one at EQUAL cosine
    (recency breaks the tie); at rank_age==0 the blend is inert (pure cosine), so the
    contrast proves the recency weighting drove the order. DEGRADE-OPEN: agent_memory
    has no access/tier/outcome columns -> those blend terms read neutral, only cosine
    + ts contribute, and nothing crashes."""
    now = time.time()

    async def _embed_one(_q, *args, **kwargs):
        return [0.3] * 8

    class _Mem:
        async def retrieve(self, qv, *, table=None, k=None, owner=None, emb_version=None, rls_owner=None, **kwargs):
            return [
                {"fact": "STALE fact zqx", "score": 0.80, "scope": "global",
                 "ts": now - 60 * 86400},
                {"fact": "FRESH fact zqx", "score": 0.80, "scope": "global",
                 "ts": now},
            ]

    _orig_toml = k._toml_section
    try:
        k._toml_section = lambda _s: {}  # rls off -> _rls_owner() None (unscoped)
        k.configure(agent_memory_recall_enabled=True, pg_primary=True,
                    embed_one=_embed_one, memory=_Mem(),
                    agent_memory_table="agent_memory", agent_memory_recall_k=3,
                    agent_memory_recall_min_score=0.45,
                    knowledge_recall_candidates=60,
                    knowledge_rank_outcome=0.05, knowledge_rank_hot=0.03,
                    knowledge_rank_access=0.02,
                    knowledge_rank_age=0.3, knowledge_recall_halflife_days=7.0)
        block = asyncio.run(k._recall_agent_memory("anything"))
        check("agent-memory recency: fresh fact outranks stale at equal cosine",
              "FRESH fact zqx" in block and "STALE fact zqx" in block
              and block.index("FRESH fact zqx") < block.index("STALE fact zqx"),
              repr(block[:120]))
        k.configure(knowledge_rank_age=0.0)
        block0 = asyncio.run(k._recall_agent_memory("anything"))
        check("agent-memory recency: inert at rank_age==0 (input order kept)",
              block0.index("STALE fact zqx") < block0.index("FRESH fact zqx"),
              repr(block0[:120]))
    finally:
        k._toml_section = _orig_toml
        k.configure(knowledge_rank_age=0.0)  # leave inert for any later test

def t_kg_lookup():
    """kg_lookup: alias exact-match returns the resolved app_install row; an empty
    phrase short-circuits to None; an all-empty result-set falls through to None."""
    hit = {"short_name": "vorpalapp", "app_id": "wz1", "source": "winget",
           "label": "Vorpal", "launch_hint": "vorpal://"}

    async def _db_read_hit(sql, **kw):
        return [{"result": [{"phrase": "wibblephrase", "apps": [hit]}]}]

    async def _db_read_miss(sql, **kw):
        return [{"result": []}]

    k.configure(db_read=_db_read_hit)
    check("kg_lookup empty phrase -> None",
          asyncio.run(k.kg_lookup("")) is None)
    res = asyncio.run(k.kg_lookup("wibblephrase"))
    check("kg_lookup alias hit -> resolved app",
          isinstance(res, dict) and res.get("source") == "alias"
          and res.get("app") == hit, repr(res))
    k.configure(db_read=_db_read_miss)
    check("kg_lookup no match -> None",
          asyncio.run(k.kg_lookup("nothere")) is None)

def t_hybrid_and_rerank():
    async def _embed_one(_q, *args, **kwargs):
        return [0.1] * 8

    retrieved_kwargs = {}

    class _MemTrack:
        async def retrieve(self, qv, **kwargs):
            retrieved_kwargs.update(kwargs)
            return [
                {"id": 1, "score": 0.8, "q": "what is your favorite editor", "answer": "Neovim",
                 "ts": time.time(), "satisfied": True, "tier": "hot", "access_count": 5}
            ]

    k.configure(
        embed_one=_embed_one,
        memory=_MemTrack(),
        knowledge_table="knowledge",
        knowledge_recall_k=3,
        knowledge_recall_candidates=60,
        knowledge_recall_min_score=0.62,
        knowledge_rank_age=0.0,
        knowledge_rag_hybrid=True,
        knowledge_rag_rerank=True,
    )

    try:
        block = asyncio.run(k._recall_knowledge_pg("what is your favorite editor"))
        check("hybrid: query_text passed to memory.retrieve", retrieved_kwargs.get("query_text") == "what is your favorite editor")
        check("hybrid: hybrid=True passed to memory.retrieve", retrieved_kwargs.get("hybrid") is True)
        check("hybrid: rerank=True passed to memory.retrieve", retrieved_kwargs.get("rerank") is True)
        check("hybrid: recall returns content correctly", "Neovim" in block, repr(block))
    finally:
        k.configure(
            knowledge_rag_hybrid=False,
            knowledge_rag_rerank=False,
        )

def main():
    t_recall_floor()
    t_recency_mult()
    t_recall_blend()
    t_rls_owner()
    t_recall_agent_memory()
    t_recall_agent_memory_recency()
    t_kg_lookup()
    t_hybrid_and_rerank()
    if _fails:
        print(f"\n{_fails} FAILED")
        sys.exit(1)
    print("\nok")



# ==============================================================================
# Consolidated from test_mios_vector.py (task_1006)
# ==============================================================================
# AI-hint: stdlib unit test for pgvector schema and cosine similarity matching.
import os
import unittest
try:
    import psycopg
except ImportError:
    psycopg = None

def setUpModule():
    if psycopg is None:
        raise unittest.SkipTest("no live pgvector -- integration test")
    port = os.environ.get("MIOS_PORTS_PGVECTOR", "8600")
    conn_str = f"postgresql://mios:mios@localhost:{port}/mios"
    try:
        with psycopg.connect(conn_str, connect_timeout=1) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(1) FROM verb")
                if cur.fetchone()[0] == 0:
                    raise unittest.SkipTest("no seeded pgvector -- integration test")
    except Exception:
        raise unittest.SkipTest("no live pgvector -- integration test")

class TestMiosVectorDb(unittest.TestCase):

    def setUp(self):
        self.conn_str = "postgresql://mios:mios@localhost:8432/mios"
        with psycopg.connect(self.conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM verb WHERE name LIKE 'test_verb_%%'")

                vec_a = [0.0] * 768
                vec_a[0] = 1.0

                vec_b = [0.0] * 768
                vec_b[1] = 1.0

                cur.execute(
                    "INSERT INTO verb (name, sig, desc_default, tier, permission, emb, emb_model, emb_version) "
                    "VALUES ('test_verb_a', 'test_sig', 'test search query', 'common', 'read', %s::vector, 'test-model', 'v1')",
                    (vec_a,)
                )
                cur.execute(
                    "INSERT INTO verb (name, sig, desc_default, tier, permission, emb, emb_model, emb_version) "
                    "VALUES ('test_verb_b', 'test_sig', 'completely different action', 'common', 'read', %s::vector, 'test-model', 'v1')",
                    (vec_b,)
                )
            conn.commit()

    def tearDown(self):
        with psycopg.connect(self.conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM verb WHERE name LIKE 'test_verb_%%'")
            conn.commit()

    def test_pgvector_cosine_similarity(self):
        with psycopg.connect(self.conn_str) as conn:
            with conn.cursor() as cur:
                target_vec = [0.0] * 768
                target_vec[0] = 0.9
                target_vec[1] = 0.1

                cur.execute(
                    "SELECT name, emb <=> %s::vector as distance "
                    "FROM verb WHERE name LIKE 'test_verb_%%' "
                    "ORDER BY emb <=> %s::vector ASC LIMIT 1",
                    (target_vec, target_vec)
                )
                row = cur.fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[0], "test_verb_a")

                dist_a = row[1]

                cur.execute(
                    "SELECT name, emb <=> %s::vector as distance "
                    "FROM verb WHERE name = 'test_verb_b'",
                    (target_vec,)
                )
                row_b = cur.fetchone()
                self.assertIsNotNone(row_b)
                dist_b = row_b[1]

                self.assertTrue(dist_a < dist_b, f"dist_a={dist_a} should be less than dist_b={dist_b}")


def _run_extra_vector():
    import os
    _saved_env = dict(os.environ)
    try:
        import unittest
        suite = unittest.TestSuite()
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestMiosVectorDb))
        res = unittest.TextTestRunner().run(suite)
        return 0 if res.wasSuccessful() else 1
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



def _run_all_folded_knowledge_suites():
    rc = _run_extra_vector()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    main()
    _run_all_folded_knowledge_suites()
