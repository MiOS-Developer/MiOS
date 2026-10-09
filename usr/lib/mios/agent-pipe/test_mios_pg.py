# AI-hint: Standalone unit test for mios_pg to verify pure-python PostgreSQL helper logic, including DSN construction, vector literal formatting, and SQL insert g...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md

import asyncio
import os
import sys
import types

import mios_pg as P

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover -- py<3.11
    import tomli as tomllib  # type: ignore

def _ssot_pgvector_port() -> int:
    """[ports].pgvector, read rather than restated: this assertion used to carry
    its own literal, and that literal was a retired port."""
    for cand in ("/usr/share/mios/mios.toml",
                 os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "../../../../usr/share/mios/mios.toml")):
        try:
            with open(cand, "rb") as fh:
                return int(tomllib.load(fh)["ports"]["pgvector"])
        except (OSError, KeyError, ValueError):
            continue
    raise SystemExit("test_mios_pg: cannot read [ports].pgvector from the SSOT")

_RESULTS: list = []

def _check(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def t_config_dsn() -> None:
    env = {"MIOS_PGVECTOR_HOST": "h", "MIOS_PORTS_PGVECTOR": "5544",
           "MIOS_PGVECTOR_USER": "u", "MIOS_PGVECTOR_PASS": "p", "MIOS_PGVECTOR_DB": "d"}
    c = P.pg_config(env)
    _check("config: parsed", c == {"host": "h", "port": 5544, "user": "u",
                                   "password": "p", "dbname": "d"}, str(c))
    _check("dsn: built", P.dsn(c) == "postgresql://u:p@h:5544/d", P.dsn(c))
    d = P.pg_config({})
    want = _ssot_pgvector_port()
    _check("config: local defaults", d["host"] == "localhost" and d["port"] == want
           and d["user"] == "mios" and d["dbname"] == "mios", "%s (SSOT port %d)" % (d, want))

def t_vector_literal() -> None:
    _check("vec: format", P.vector_literal([0.1, 0.2, 0.3]) == "[0.1,0.2,0.3]",
           P.vector_literal([0.1, 0.2, 0.3]))
    _check("vec: empty", P.vector_literal([]) == "[]")
    _check("vec: coerces ints", P.vector_literal([1, 2]) == "[1.0,2.0]")

def t_build_insert() -> None:
    sql, params = P.build_insert("knowledge",
                                 {"q": "hi", "answer": "yo", "tier": "warm",
                                  "emb": [0.1, 0.2]})
    _check("insert: cols", "INSERT INTO knowledge (q, answer, tier, emb)" in sql, sql)
    _check("insert: emb cast", "%(emb)s::vector" in sql, sql)
    _check("insert: returning id", sql.strip().endswith("RETURNING id;"))
    _check("insert: no value interpolation", "hi" not in sql and "yo" not in sql)
    _check("insert: params bound", params["q"] == "hi" and params["answer"] == "yo")
    _check("insert: emb -> literal", params["emb"] == "[0.1,0.2]", str(params["emb"]))

def t_build_insert_jsonb() -> None:
    sql, params = P.build_insert("event",
                                 {"kind": "x", "payload": {"a": 1, "b": [2, 3]},
                                  "sources": ["u:remember", "url"]})
    _check("jsonb: dict cast", "%(payload)s::jsonb" in sql, sql)
    _check("jsonb: list cast", "%(sources)s::jsonb" in sql, sql)
    _check("jsonb: scalar plain", "%(kind)s," in sql or "%(kind)s)" in sql, sql)
    _check("jsonb: dict serialized", params["payload"] == '{"a": 1, "b": [2, 3]}',
           str(params["payload"]))
    _check("jsonb: list serialized", params["sources"] == '["u:remember", "url"]',
           str(params["sources"]))

def t_build_recall() -> None:
    sql, params = P.build_recall("knowledge", k=5)
    _check("recall: cosine op", "emb <=> %(qvec)s::vector" in sql, sql)
    _check("recall: similarity expr", "1 - (emb <=> %(qvec)s::vector) AS score" in sql)
    _check("recall: order+limit", "ORDER BY emb <=> %(qvec)s::vector LIMIT %(k)s" in sql)
    _check("recall: filters null emb", "WHERE emb IS NOT NULL" in sql)
    _check("recall: k bound", params["k"] == 5)

def t_build_recall_emb_version() -> None:
    sql, params = P.build_recall("knowledge", k=3, emb_version="v2")
    _check("emb_ver: filter present",
           "(emb_version = %(emb_version)s OR emb_version IS NULL)" in sql, sql)
    _check("emb_ver: bound param", params.get("emb_version") == "v2", str(params))
    sql0, p0 = P.build_recall("knowledge", k=3)
    _check("emb_ver: none -> no filter",
           "emb_version" not in sql0 and "emb_version" not in p0, sql0)
    sqle, pe = P.build_recall("knowledge", k=3, emb_version="")
    _check("emb_ver: empty -> no filter (degrade-open)",
           "emb_version" not in sqle and "emb_version" not in pe, sqle)
    sql_am, _ = P.build_recall("agent_memory", k=3, emb_version="v2")
    _check("emb_ver: agent_memory filtered", "emb_version = %(emb_version)s" in sql_am, sql_am)
    sql_rag, p_rag = P.build_recall("mios_rag", k=3, emb_version="v2")
    _check("emb_ver: mios_rag NOT filtered (no column)",
           "emb_version" not in sql_rag and "emb_version" not in p_rag, sql_rag)
    def _kept(row_ver, active="v2"):
        return row_ver is None or row_ver == active
    _check("emb_ver: keeps matching row", _kept("v2") is True)
    _check("emb_ver: keeps NULL/un-stamped row", _kept(None) is True)
    _check("emb_ver: excludes mismatched row", _kept("v1") is False)

def t_build_fts_query() -> None:
    sql, params = P.build_fts_query("knowledge", k=5)
    _check("fts: expr on knowledge", "fts @@ plainto_tsquery('simple', %(query_text)s)" in sql, sql)
    _check("fts: ts_rank score", "ts_rank(fts, plainto_tsquery('simple', %(query_text)s)) AS score" in sql, sql)
    _check("fts: order by score", "ORDER BY score DESC LIMIT %(k)s" in sql, sql)
    _check("fts: k bound", params["k"] == 5)

    sql_rag, _ = P.build_fts_query("mios_rag", k=3)
    _check("fts: expr on mios_rag", "to_tsvector('simple', coalesce(content, '')) @@ plainto_tsquery" in sql_rag, sql_rag)

    sql_am, _ = P.build_fts_query("agent_memory", k=3, emb_version="v2")
    _check("fts: expr on agent_memory", "to_tsvector('simple', coalesce(fact, '') || ' ' || coalesce(scope, ''))" in sql_am, sql_am)
    _check("fts: emb_version on agent_memory", "emb_version = %(emb_version)s" in sql_am, sql_am)

def t_recall_tuning() -> None:
    _check("tuning: ef_search", P.recall_tuning(120) == "SET hnsw.ef_search = 120;",
           P.recall_tuning(120))

def t_rid_to_pg_id() -> None:
    _check("rid: legacy numeric tail", P.rid_to_pg_id("knowledge:123") == 123)
    _check("rid: pending_action tail", P.rid_to_pg_id("pending_action:42") == 42)
    _check("rid: bare int", P.rid_to_pg_id(789) == 789)
    _check("rid: bare numeric str", P.rid_to_pg_id("456") == 456)
    _check("rid: alpha legacy id -> None", P.rid_to_pg_id("knowledge:abc") is None)
    _check("rid: None -> None", P.rid_to_pg_id(None) is None)
    _check("rid: empty -> None", P.rid_to_pg_id("") is None)

def t_rls_owner_scope() -> None:
    sql, params = P.build_set_owner("alice")
    _check("rls: set_config call shape",
           "set_config(%(guc)s, %(owner)s, true)" in sql, sql)
    _check("rls: guc bound, not spliced",
           params["guc"] == "mios.owner_user" and "mios.owner_user" not in sql, sql)
    _check("rls: owner bound, not spliced",
           params["owner"] == "alice" and "alice" not in sql, sql)

    _check("rls: disabled by default (no env)", P.rls_enabled({}) is False)
    _check("rls: enabled on truthy",
           P.rls_enabled({"MIOS_PGVECTOR_RLS_ENABLE": "true"}) is True
           and P.rls_enabled({"MIOS_PGVECTOR_RLS_ENABLE": "1"}) is True
           and P.rls_enabled({"MIOS_PGVECTOR_RLS_ENABLE": "ON"}) is True)
    _check("rls: disabled on falsy",
           P.rls_enabled({"MIOS_PGVECTOR_RLS_ENABLE": "0"}) is False
           and P.rls_enabled({"MIOS_PGVECTOR_RLS_ENABLE": "false"}) is False
           and P.rls_enabled({"MIOS_PGVECTOR_RLS_ENABLE": ""}) is False)

    _prior_bm = os.environ.get("MIOS_PRINCIPAL_BIND_MODE")
    try:
        os.environ["MIOS_PRINCIPAL_BIND_MODE"] = "enforce"
        _check("rls: scope None when rls_enable=false (byte-identical no-op, even w/ enforce)",
               P._owner_scope("alice", {}) is None
               and P._owner_scope("alice", {"MIOS_PGVECTOR_RLS_ENABLE": "0"}) is None)

        P._RLS_UNVERIFIED_WARNED = False
        sc = P._owner_scope("alice", {"MIOS_PGVECTOR_RLS_ENABLE": "1"})
        _check("rls: scope emitted when enabled+enforce+owner",
               sc is not None and "set_config" in sc[0]
               and sc[1] == {"guc": "mios.owner_user", "owner": "alice"}, str(sc))
        _check("rls: no false-warn on the verified emit path",
               P._RLS_UNVERIFIED_WARNED is False)

        for _mode in ("off", "verify"):
            os.environ["MIOS_PRINCIPAL_BIND_MODE"] = _mode
            P._RLS_UNVERIFIED_WARNED = False
            sc_unv = P._owner_scope("victim", {"MIOS_PGVECTOR_RLS_ENABLE": "1"})
            _check(f"rls: NO scope when enabled but bind-mode={_mode} (no false isolation)",
                   sc_unv is None, str(sc_unv))
            _check(f"rls: one-time WARN fired (bind-mode={_mode})",
                   P._RLS_UNVERIFIED_WARNED is True)

        os.environ["MIOS_PRINCIPAL_BIND_MODE"] = "enforce"
        P._RLS_UNVERIFIED_WARNED = False
        _check("rls: scope None when enabled+no-owner (degrade-open, no lockout)",
               P._owner_scope(None, {"MIOS_PGVECTOR_RLS_ENABLE": "1"}) is None
               and P._owner_scope("", {"MIOS_PGVECTOR_RLS_ENABLE": "1"}) is None
               and P._owner_scope("   ", {"MIOS_PGVECTOR_RLS_ENABLE": "1"}) is None)
        _check("rls: no warn on the owner-less (intentional) path",
               P._RLS_UNVERIFIED_WARNED is False)
    finally:
        if _prior_bm is None:
            os.environ.pop("MIOS_PRINCIPAL_BIND_MODE", None)
        else:
            os.environ["MIOS_PRINCIPAL_BIND_MODE"] = _prior_bm
        P._RLS_UNVERIFIED_WARNED = False

class _FakeInfo:
    def __init__(self) -> None:
        self.transaction_status = 0   # 0 == IDLE (psycopg pq.TransactionStatus.IDLE)

class _FakeCursor:
    def __init__(self, conn) -> None: self.conn = conn
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def execute(self, sql, params=None):
        self.conn.executed.append((str(sql), dict(params) if params else None))
        if "set_config" in str(sql) and params and params.get("guc"):
            target = self.conn.local_guc if self.conn._in_txn else self.conn.session_guc
            target[params["guc"]] = params.get("owner")
        return self
    async def fetchall(self): return list(self.conn.fetch_rows)

class _FakeTxn:
    def __init__(self, conn) -> None: self.conn = conn
    async def __aenter__(self):
        self.conn._in_txn = True; self.conn.info.transaction_status = 1; return self
    async def __aexit__(self, et, ev, tb):
        self.conn.local_guc.clear(); self.conn._in_txn = False; self.conn.info.transaction_status = 0; return False

class _FakeConn:
    def __init__(self) -> None:
        self.closed = False; self.broken = False; self.info = _FakeInfo(); self.executed: list = []
        self.local_guc: dict = {}; self.session_guc: dict = {}; self._in_txn = False
        self.fetch_rows: list = []; self.rolled_back = 0
    def cursor(self, row_factory=None): return _FakeCursor(self)
    def transaction(self): return _FakeTxn(self)
    async def rollback(self):
        self.rolled_back += 1; self.local_guc.clear(); self._in_txn = False; self.info.transaction_status = 0
    async def close(self): self.closed = True
    async def __aenter__(self): return self
    async def __aexit__(self, *a): self.closed = True; return False

class _FakeAsyncConnection:
    opened: list = []   # class-level connect log

    @classmethod
    async def connect(cls, conninfo=None, autocommit=None, connect_timeout=None, **kw):
        c = _FakeConn()
        cls.opened.append(c)
        return c

def _install_fake_psycopg() -> None:
    mod = types.ModuleType("psycopg")
    mod.AsyncConnection = _FakeAsyncConnection
    rows = types.ModuleType("psycopg.rows")
    rows.dict_row = object()
    mod.rows = rows
    sys.modules["psycopg"] = mod
    sys.modules["psycopg.rows"] = rows
    _FakeAsyncConnection.opened = []

async def _two_executes(*, owner1=None):
    _FakeAsyncConnection.opened = []
    await P.execute("SELECT 1", fetch=False, rls_owner=owner1)
    await P.execute("SELECT 2", fetch=False)
    return list(_FakeAsyncConnection.opened)

def _set_env(**kw):
    """Set/clear env vars, returning a restore callable (snapshot+restore)."""
    prior = {k: os.environ.get(k) for k in kw}

    def _restore():
        for k, v in prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    for k, v in kw.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return _restore

def t_pool_default_off_per_call_connect() -> None:
    _install_fake_psycopg()
    restore = _set_env(MIOS_PGVECTOR_POOL_ENABLE=None)
    P._POOL = None
    P._pg_down_until = 0.0
    try:
        opened = asyncio.run(_two_executes())
        _check("pool off: 2 calls -> 2 fresh connects (per-call connect, unchanged)",
               len(opened) == 2, f"connects={len(opened)}")
        _check("pool off: each connection closed after its call (per-call lifecycle)",
               all(c.closed for c in opened), str([c.closed for c in opened]))
        _check("pool off: pool object never created (byte-identical path)",
               P._POOL is None)
    finally:
        P._POOL = None
        restore()

def t_pool_on_reuses_connection() -> None:
    _install_fake_psycopg()
    restore = _set_env(MIOS_PGVECTOR_POOL_ENABLE="1")
    P._POOL = None
    P._pg_down_until = 0.0
    try:
        opened = asyncio.run(_two_executes())
        _check("pool on: 2 calls REUSE one connection (1 connect total)",
               len(opened) == 1, f"connects={len(opened)}")
        _check("pool on: the reused connection stays open between calls",
               bool(opened) and opened[0].closed is False)
        _check("pool on: both queries ran on the one reused connection",
               bool(opened) and len(opened[0].executed) == 2,
               str(opened[0].executed if opened else None))
    finally:
        P._POOL = None
        restore()

class _PoisonPool:
    """A pool whose checkout always fails -- exercises _conn's degrade-open path."""
    async def acquire(self, cfg=None):
        raise RuntimeError("pool poisoned")

    async def release(self, *a, **k):
        return None

def t_pool_degrade_open_poisoned() -> None:
    _install_fake_psycopg()
    restore = _set_env(MIOS_PGVECTOR_POOL_ENABLE="1")
    P._POOL = _PoisonPool()   # enabled + non-None -> _get_pool hands back this broken pool
    P._pg_down_until = 0.0
    try:
        async def _go():
            _FakeAsyncConnection.opened = []
            r = await P.execute("SELECT 1", fetch=True)
            return r, list(_FakeAsyncConnection.opened)
        r, opened = asyncio.run(_go())
        _check("pool degrade: poisoned checkout falls back to a direct connect",
               len(opened) == 1, f"connects={len(opened)}")
        _check("pool degrade: the query still returns a result (never fails on the pool)",
               r == [], str(r))
    finally:
        P._POOL = None
        restore()

def t_pool_no_owner_guc_leak() -> None:
    _install_fake_psycopg()
    restore = _set_env(MIOS_PGVECTOR_POOL_ENABLE="1", MIOS_PGVECTOR_RLS_ENABLE="1",
                       MIOS_PRINCIPAL_BIND_MODE="enforce")
    P._POOL = None
    P._pg_down_until = 0.0
    P._RLS_UNVERIFIED_WARNED = False
    try:
        opened = asyncio.run(_two_executes(owner1="alice"))
        _check("pool+RLS: owner-scoped call + plain call REUSE one connection",
               len(opened) == 1, f"connects={len(opened)}")
        conn = opened[0] if opened else None
        _check("pool+RLS: owner GUC never leaked to the SESSION (no cross-checkout leak)",
               conn is not None and conn.session_guc == {},
               str(conn.session_guc if conn else None))
        _check("pool+RLS: transaction-scoped owner GUC cleared after the scoped call",
               conn is not None and conn.local_guc == {},
               str(conn.local_guc if conn else None))
        setcfgs = [e for e in (conn.executed if conn else []) if "set_config" in e[0]]
        _check("pool+RLS: exactly ONE owner-scope statement (call 1 only; plain call inherited none)",
               len(setcfgs) == 1, str(setcfgs))
    finally:
        P._POOL = None
        P._RLS_UNVERIFIED_WARNED = False
        restore()

def t_pool_checkin_cleans_connection() -> None:
    _install_fake_psycopg()

    async def _dirty_then_release():
        pool = P.AsyncConnPool(min_size=0, max_size=2)
        conn, pooled = await pool.acquire()
        conn.info.transaction_status = 1            # a transaction was left open
        conn.local_guc["mios.owner_user"] = "ghost"  # ... with a stray SET LOCAL
        await pool.release(conn, pooled, ok=True)
        return conn, pool

    async def _broken_release():
        pool = P.AsyncConnPool(min_size=0, max_size=2)
        conn, pooled = await pool.acquire()
        conn.broken = True
        await pool.release(conn, pooled, ok=True)
        return pool

    conn, pool = asyncio.run(_dirty_then_release())
    _check("pool checkin: open transaction rolled back on release (SET LOCAL discarded)",
           conn.rolled_back >= 1 and conn.local_guc == {},
           f"rolled={conn.rolled_back} local={conn.local_guc}")
    _check("pool checkin: the cleaned connection is returned for reuse",
           pool._free == [conn] and pool._size == 1)

    pool2 = asyncio.run(_broken_release())
    _check("pool checkin: a broken connection is discarded, not reused",
           pool2._free == [] and pool2._size == 0)

def t_pool_warm_and_exhaustion() -> None:
    _install_fake_psycopg()

    async def _warm():
        _FakeAsyncConnection.opened = []
        pool = P.AsyncConnPool(min_size=2, max_size=4)
        conn, pooled = await pool.acquire()
        return pool, pooled, list(_FakeAsyncConnection.opened)

    async def _exhaust():
        _FakeAsyncConnection.opened = []
        pool = P.AsyncConnPool(min_size=0, max_size=1)
        _c1, p1 = await pool.acquire()      # grows to the cap
        _c2, p2 = await pool.acquire()      # exhausted -> ephemeral
        return p1, p2, list(_FakeAsyncConnection.opened)

    pool, pooled, opened = asyncio.run(_warm())
    _check("pool warm: min_size pre-opens that many connections on first use",
           len(opened) == 2, f"opened={len(opened)}")
    _check("pool warm: one warm conn handed out (pooled), one stays idle",
           pooled is True and pool._size == 2 and len(pool._free) == 1,
           f"size={pool._size} free={len(pool._free)}")

    p1, p2, opened2 = asyncio.run(_exhaust())
    _check("pool exhaust: at-capacity checkout degrades to an ephemeral conn (pooled=False)",
           p1 is True and p2 is False, f"p1={p1} p2={p2}")
    _check("pool exhaust: the ephemeral conn is opened beyond the cap (never blocks)",
           len(opened2) == 2, f"opened={len(opened2)}")

def main() -> int:
    for t in (t_config_dsn, t_vector_literal, t_build_insert, t_build_insert_jsonb,
              t_build_recall, t_build_recall_emb_version, t_build_fts_query, t_recall_tuning,
              t_rid_to_pg_id, t_rls_owner_scope,
              t_pool_default_off_per_call_connect, t_pool_on_reuses_connection,
              t_pool_degrade_open_poisoned, t_pool_no_owner_guc_leak,
              t_pool_checkin_cleans_connection, t_pool_warm_and_exhaustion):
        t()
    passed = sum(1 for _, ok in _RESULTS if ok)
    total = len(_RESULTS)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1



# ==============================================================================
# mios_pipe.db transport, client and Postgres injected; task_1006 had folded only a placeholder here.
# ==============================================================================
import time
import unittest

from mios_pipe import db as _db


class _FakeReply:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _FakeTransport:
    def __init__(self, outcome):
        self.outcome = outcome   # a _FakeReply, or an exception to raise
        self.calls = []

    async def post(self, url, *, content, headers, timeout):
        self.calls.append({"url": url, "content": content, "headers": headers, "timeout": timeout})
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class _FakePg:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def execute(self, sql, params, fetch):
        self.calls.append((sql, params, fetch))
        return self.rows if fetch else None


class TestDbTransport(unittest.TestCase):
    _STATE = ("_PG_PRIMARY", "_mios_pg", "DB_NS", "DB_DB", "DB_URL", "_DB_AUTH",
              "_db_down_until", "client")

    def setUp(self):
        self._saved = {k: getattr(_db, k) for k in self._STATE}
        _db.configure(pg_primary=False, db_ns="ns1", db_db="db1",
                      db_url="http://db.invalid:1", db_auth="Basic abc")
        _db._db_down_until = 0.0

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(_db, k, v)

    def _wire(self, outcome):
        transport = _FakeTransport(outcome)
        _db.client = lambda: transport
        return transport

    def test_post_frames_the_statement_for_the_configured_namespace(self):
        transport = self._wire(_FakeReply(200, [{"result": [1]}]))
        self.assertEqual(asyncio.run(_db.post("SELECT 1", timeout=2.5)), [{"result": [1]}])
        call = transport.calls[0]
        self.assertEqual(call["url"], "http://db.invalid:1/sql")
        self.assertEqual(call["content"], b"USE NS ns1 DB db1; SELECT 1")
        self.assertEqual(call["headers"]["Authorization"], "Basic abc")
        self.assertEqual(call["timeout"], 2.5)

    def test_an_empty_statement_never_reaches_the_transport(self):
        transport = self._wire(AssertionError("transport reached"))
        for sql in ("", "   ", None):
            self.assertIsNone(asyncio.run(_db.post(sql)), repr(sql))
        self.assertEqual(transport.calls, [])

    def test_postgres_primary_refuses_legacy_writes(self):
        transport = self._wire(AssertionError("transport reached"))
        _db.configure(pg_primary=True)
        for sql in ("CREATE x", "  update y", "Delete z", "INSERT w", "relate a->b->c"):
            self.assertIsNone(asyncio.run(_db.post(sql)), sql)
        self.assertEqual(transport.calls, [])
        reads = self._wire(_FakeReply(200, []))
        asyncio.run(_db.post("SELECT 1"))
        self.assertEqual(len(reads.calls), 1, "a read was refused along with the writes")

    def test_a_failure_opens_a_down_window_that_short_circuits(self):
        for outcome in (_FakeReply(503, None), OSError("connection refused")):
            _db._db_down_until = 0.0
            transport = self._wire(outcome)
            started = time.time()
            self.assertIsNone(asyncio.run(_db.post("SELECT 1")))
            self.assertGreaterEqual(_db._db_down_until, started + 29, repr(outcome))
            self.assertIsNone(asyncio.run(_db.post("SELECT 1")))
            self.assertEqual(len(transport.calls), 1,
                             "a request inside the down window reached the transport")

    def test_read_and_update_route_to_postgres_when_primary(self):
        pg = _FakePg([{"id": 1}])
        transport = self._wire(AssertionError("legacy transport reached"))
        _db.configure(pg_primary=True, mios_pg=pg)
        got = asyncio.run(_db.read("SELECT * FROM t", pg_sql="SELECT * FROM t WHERE id=%(id)s",
                                   pg_params={"id": 1}))
        self.assertEqual(got, [{"result": [{"id": 1}]}])
        asyncio.run(_db.update("UPDATE t", pg_sql="UPDATE t SET a=1"))
        self.assertEqual(pg.calls, [("SELECT * FROM t WHERE id=%(id)s", {"id": 1}, True),
                                    ("UPDATE t SET a=1", {}, False)])
        pg.rows = None
        self.assertEqual(asyncio.run(_db.read("x", pg_sql="SELECT 1")), [{"result": []}])
        self.assertEqual(transport.calls, [])

    def test_read_and_update_fall_back_to_the_transport_without_pg_sql(self):
        transport = self._wire(_FakeReply(200, [{"result": []}]))
        self.assertEqual(asyncio.run(_db.read("SELECT 1")), [{"result": []}])
        asyncio.run(_db.update("UPDATE t SET a=1"))
        self.assertEqual([c["content"] for c in transport.calls],
                         [b"USE NS ns1 DB db1; SELECT 1", b"USE NS ns1 DB db1; UPDATE t SET a=1"])
        _db.configure(pg_primary=True, mios_pg=_FakePg([]))
        asyncio.run(_db.update("UPDATE t SET a=2"))
        self.assertEqual(len(transport.calls), 2, "a legacy write ran under postgres primary")


def _run_case(tc):
    _saved = dict(os.environ)
    try:
        s = unittest.TestSuite()
        s.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(tc))
        return 0 if unittest.TextTestRunner().run(s).wasSuccessful() else 1
    finally:
        os.environ.clear(); os.environ.update(_saved)

def _run_extra_db():
    return _run_case(TestDbTransport)



# ==============================================================================
# Consolidated from test_mios_db_config.py (task_1006)
# ==============================================================================
# AI-hint: stdlib unit test for mios_db_config resolver.
import sys
import os
import unittest
from unittest.mock import patch
sys.path.insert(0, "/usr/lib/mios")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import psycopg
except ImportError:
    psycopg = None
import mios_db_config

def _require_seeded_pgvector():
    """The live-DB guard for the integration class alone. It was a module-level
    setUpModule, and task_1006 folded three suites into this one module, so with
    no live pgvector it skipped the hermetic TestDbTransport and TestDbWrite
    too: CI ran 0 of their tests and reported OK (skipped=1)."""
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

class TestMiosDbConfig(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _require_seeded_pgvector()

    def setUp(self):
        self.conn_str = "postgresql://mios:mios@localhost:8432/mios"
        mios_db_config.clear_cache()
        mios_db_config.reset_divergences()

    def test_is_db_authoritative(self):
        os.environ["MIOS_DB_AUTHORITATIVE"] = "True"
        mios_db_config.clear_cache()
        self.assertTrue(mios_db_config.is_db_authoritative())

        os.environ["MIOS_DB_AUTHORITATIVE"] = "False"
        mios_db_config.clear_cache()
        self.assertFalse(mios_db_config.is_db_authoritative())

        del os.environ["MIOS_DB_AUTHORITATIVE"]
        mios_db_config.clear_cache()

    def test_toml_fail_open(self):
        os.environ["MIOS_PORTS_PGVECTOR"] = "9999"
        try:
            os.environ["MIOS_DB_AUTHORITATIVE"] = "True"
            val = mios_db_config.get("ai", "kernel_dispatch")
            self.assertTrue(val)
        finally:
            del os.environ["MIOS_PORTS_PGVECTOR"]
            if "MIOS_DB_AUTHORITATIVE" in os.environ:
                del os.environ["MIOS_DB_AUTHORITATIVE"]

    def test_shadow_compare_divergence(self):
        with psycopg.connect(self.conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO config_layer (rank, name)
                    VALUES (3, 'machine')
                    ON CONFLICT (rank) DO NOTHING
                    """
                )
                cur.execute(
                    """
                    INSERT INTO config_kv (scope, key, value, layer, description)
                    VALUES ('mcp', 'port', '11111'::jsonb, 3, 'Test machine layer override')
                    ON CONFLICT (scope, key, layer) DO UPDATE SET value = EXCLUDED.value
                    """
                )
            conn.commit()

        try:
            os.environ["MIOS_DB_AUTHORITATIVE"] = "False"
            mios_db_config.reset_divergences()

            val = mios_db_config.get("mcp", "port")

            self.assertNotEqual(val, 11111)

            self.assertTrue(mios_db_config.get_divergences() > 0)

            os.environ["MIOS_DB_AUTHORITATIVE"] = "True"
            val_db = mios_db_config.get("mcp", "port")
            self.assertEqual(val_db, 11111)

        finally:
            with psycopg.connect(self.conn_str) as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM config_kv WHERE scope = 'mcp' AND key = 'port' AND layer = 3")
                conn.commit()
            if "MIOS_DB_AUTHORITATIVE" in os.environ:
                del os.environ["MIOS_DB_AUTHORITATIVE"]

    def test_health_logic_divergences(self):
        class MockApp:
            version = "test-version"

        import mios_pipe.kernel.clusterhealth as ch

        ch.configure(
            app=MockApp(), BACKEND="http://localhost:8000", BACKEND_MODEL="test-model",
            ROUTER_ENABLED=False, ROUTER_MODEL="test-router", ROUTER_ENDPOINT="test-ep",
            PLANNER_ENABLED=False, PLANNER_MODEL="test-planner", PLANNER_ENDPOINT="test-ep",
            PLANNER_MAX_NODES=3, PLANNER_REFLEXION_CAP=3,
            DCI_ENABLED=False, DCI_MODEL="test-dci", DCI_ENDPOINT="test-ep",
            _DCI_ACTS=[], DCI_FLOW_ENABLED=False, DCI_FLOW_R_MAX=3, _DCI_PERSONAS=[],
            DCI_FLOW_TRIGGER_CONF=0.5, _ALLOWLIST_HOSTS={"localhost", "127.0.0.1"},
            _HIGH_PRIVILEGE_VERBS={"shell_exec"}, _HIGH_PRIVILEGE_CURATED={"shell_exec"},
            _toml_section=lambda s: {}, _TAINT_VERBS={"web_extract"},
            SKILLS_ENABLED=False, SKILLS_MIN_LENGTH=0, SKILLS_MAX_LENGTH=0,
            SKILLS_MIN_SUPPORT=0, SKILLS_WINDOW_HOURS=0, SKILLS_AUTO_PROMOTE_THRESHOLD=0,
            PASSPORT_ENABLE=False, PASSPORT_ALGO="RS256", PASSPORT_AGENT_NAME="test",
            PASSPORT_KEY_DIR="/test", PASSPORT_VERIFY_ON_READ=False,
            _passport_load_priv=lambda: None, _passport_kid=lambda: None,
            REFINE_ENABLED=False, REFINE_MODEL="test", REFINE_ENDPOINT="test", REFINE_BYPASS_CHARS=0,
            POLISH_ENABLED=False, POLISH_MODEL="test", POLISH_ENDPOINT="test",
            _AGENT_REGISTRY={}, _agent_lane=lambda a: "gpu", LAUNCHER_SOCK="/test.sock",
            DB_URL="postgresql://test", PORT=8640,
        )

        mios_db_config.reset_divergences()

        with psycopg.connect(self.conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO config_layer (rank, name)
                    VALUES (3, 'machine')
                    ON CONFLICT (rank) DO NOTHING
                    """
                )
                cur.execute(
                    """
                    INSERT INTO config_kv (scope, key, value, layer, description)
                    VALUES ('mcp', 'port', '22222'::jsonb, 3, 'Divergent Test')
                    ON CONFLICT (scope, key, layer) DO UPDATE SET value = EXCLUDED.value
                    """
                )
            conn.commit()

        try:
            os.environ["MIOS_DB_AUTHORITATIVE"] = "False"
            _ = mios_db_config.get("mcp", "port")

            import asyncio
            res = asyncio.run(ch.health_logic())

            self.assertIn("config_divergences", res)
            self.assertEqual(res["config_divergences"], mios_db_config.get_divergences())
            self.assertTrue(res["config_divergences"] > 0)
        finally:
            with psycopg.connect(self.conn_str) as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM config_kv WHERE scope = 'mcp' AND key = 'port' AND layer = 3")
                conn.commit()
            if "MIOS_DB_AUTHORITATIVE" in os.environ:
                del os.environ["MIOS_DB_AUTHORITATIVE"]

    def test_verb_catalog_sentinel_and_shadow(self):
        import mios_pipe.routing.verbcatalog as vc

        with psycopg.connect(self.conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE verb
                    SET parallel_limit = 99
                    WHERE name = 'list_windows'
                    """
                )
            conn.commit()

        try:
            os.environ["MIOS_DB_AUTHORITATIVE"] = "False"
            mios_db_config.reset_divergences()

            cat = vc._load_verb_catalog()
            self.assertNotEqual(cat["list_windows"]["parallel_limit"], 99)
            import time
            for _ in range(50):
                if mios_db_config.get_divergences() > 0:
                    break
                time.sleep(0.05)
            self.assertTrue(mios_db_config.get_divergences() > 0)

            os.environ["MIOS_DB_AUTHORITATIVE"] = "True"
            cat_db = vc._load_verb_catalog()
            self.assertEqual(cat_db["list_windows"]["parallel_limit"], 99)

        finally:
            with psycopg.connect(self.conn_str) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE verb
                        SET parallel_limit = 0
                        WHERE name = 'list_windows'
                        """
                    )
                conn.commit()
            if "MIOS_DB_AUTHORITATIVE" in os.environ:
                del os.environ["MIOS_DB_AUTHORITATIVE"]

    @patch("mios_toml.load_merged")
    @patch("mios_db_config.load_db_config")
    def test_zero_divergence_on_seeded_tree(self, mock_db_config, mock_load):
        import mios_toml
        import mios_pipe.routing.verbcatalog as vc

        vendor_data = mios_toml.load_vendor()
        mock_load.return_value = vendor_data
        mock_db_config.return_value = vendor_data

        mios_db_config.reset_divergences()

        merged = mios_db_config.load_merged()
        self.assertIsNotNone(merged)

        for sec in ["ai", "mcp", "routing", "recipes", "security"]:
            sec_val = mios_db_config.section(None, sec)
            self.assertIsNotNone(sec_val)

        self.assertEqual(
            mios_db_config.get("ai", "kernel_dispatch"),
            vendor_data.get("ai", {}).get("kernel_dispatch")
        )

        self.assertIsNotNone(mios_db_config.colors())

        toml_cat = vc._load_verb_catalog()
        db_cat = vc._load_verb_catalog_from_db()
        self.assertEqual(vc._compare_catalogs(toml_cat, db_cat), set())

        self.assertEqual(mios_db_config.get_divergences(), 0)

    def test_record_divergence_deduplication(self):
        mios_db_config.reset_divergences()
        mios_db_config.record_divergence("scope.key1")
        mios_db_config.record_divergence("scope.key1")
        mios_db_config.record_divergence({"scope.key1", "scope.key2"})
        self.assertEqual(mios_db_config.get_divergences(), 2)


def _run_extra_db_config():
    return _run_case(TestMiosDbConfig)

# ==============================================================================
# Consolidated from test_mios_dbwrite.py (task_1006)
# ==============================================================================
# AI-hint: Unit tests for mios_pipe.dbwrite.
"""Unit tests for database writer layer."""

import unittest

from mios_pipe.dbwrite import (
    _db_create,
    _db_fire,
    _db_write,
    _pg_mirror,
    configure as configure_dbwrite,
)

class TestDbWrite(unittest.TestCase):

    def setUp(self):
        self.posts = []
        configure_dbwrite(
            pg_enabled=True,
            pg_primary=False,
            db_post=lambda sql: self.posts.append(sql),
        )

    def test_db_create_sql_formatting(self):
        sql = _db_create("test_table", {"name": "test", "val": 123}, now_fields=("created_at",), _mirror=False)
        self.assertIn("CREATE test_table SET", sql)
        self.assertIn("created_at = time::now()", sql)
        self.assertIn('name = "test"', sql)
        self.assertIn("val = 123", sql)

    def test_db_write_dispatches_post(self):
        _db_write("event", {"action": "login"})
        self.assertEqual(len(self.posts), 1)
        self.assertIn("CREATE event SET", self.posts[0])

    def test_pg_mirror_degrade_open(self):
        _pg_mirror("event", {"action": "logout"})

def _run_extra_dbwrite():
    return _run_case(TestDbWrite)

def _run_all_folded_pg_suites():
    for fn in (_run_extra_db, _run_extra_db_config, _run_extra_dbwrite):
        if (rc := fn()) not in (None, 0):
            import sys
            sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _rc_main = main()
    _run_all_folded_pg_suites()
    sys.exit(_rc_main)
