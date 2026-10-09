# AI-hint: stdlib unit test for mios_daemons -- single-iteration behaviour of the
# AI-related: ./mios_daemons.py
# AI-functions: (tests)
"""Stdlib unit tests for the mios_daemons background loops (no network/DB)."""

import asyncio
import os
import tempfile
import time
import unittest

import mios_daemons

def _run(coro):
    try:
        return asyncio.new_event_loop().run_until_complete(coro)
    except asyncio.CancelledError:
        return None

class _CancelAfter:
    """A fake asyncio.sleep that lets the loop run N iterations then cancels it
    (raising CancelledError) so a single pass is observable without a real wait."""

    def __init__(self, fire_after=1):
        self.calls = 0
        self.fire_after = fire_after

    async def __call__(self, _delay):
        self.calls += 1
        if self.calls >= self.fire_after:
            raise asyncio.CancelledError()

class _Rep:
    def __init__(self):
        self._scores = {}

    def score(self, pid):
        return self._scores.get(pid, 0.5)

class GossipLoopTest(unittest.TestCase):
    def test_merges_discovered_peer(self):
        peers = {"known": {"url": "http://known", "status": "active", "heartbeat": 1}}

        class _Resp:
            status_code = 200
            @staticmethod
            def json(): return {"peers": [{"id": "fresh", "endpoint": "http://fresh", "heartbeat": 2}]}

        class _Client:
            async def get(self, _url, timeout=5.0): return _Resp()

        async def _get_client(): return _Client()

        sleeper = _CancelAfter(fire_after=2)
        orig_sleep = mios_daemons.asyncio.sleep
        orig_toml = mios_daemons._toml_section
        mios_daemons.asyncio.sleep = sleeper
        mios_daemons._toml_section = lambda _s: {"interval_min": 1, "fanout": 3,
                                                 "min_trust": 0.0}
        mios_daemons.configure(
            _get_client=_get_client, _A2A_PEERS=peers,
            _A2A_PEERS_LOCK=asyncio.Lock(), _A2A_REPUTATION=_Rep())
        try:
            _run(mios_daemons._gossip_loop())
        finally:
            mios_daemons.asyncio.sleep = orig_sleep
            mios_daemons._toml_section = orig_toml

        self.assertIn("fresh", peers)
        self.assertEqual(peers["fresh"]["status"], "discovered")

    def test_interval_zero_is_noop(self):
        orig_toml = mios_daemons._toml_section
        mios_daemons._toml_section = lambda _s: {"interval_min": 0}
        try:
            _run(mios_daemons._gossip_loop())
        finally:
            mios_daemons._toml_section = orig_toml

class SelfImproveLoopTest(unittest.TestCase):
    def test_surfaces_high_finding_once(self):
        seen = set()
        calls = {"n": 0}

        async def _report():
            calls["n"] += 1
            return {"findings": [{"severity": "high", "kind": "k", "subject": "s",
                                  "detail": "d", "suggestion": "fix"}]}

        sleeper = _CancelAfter(fire_after=2)
        orig_sleep = mios_daemons.asyncio.sleep
        orig_toml = mios_daemons._toml_section
        orig_report = mios_daemons._selfimprove_report
        mios_daemons.asyncio.sleep = sleeper
        mios_daemons._toml_section = lambda _s: {"interval_min": 1}
        mios_daemons._selfimprove_report = _report
        mios_daemons.configure(_SELFIMPROVE_SEEN=seen)
        try:
            _run(mios_daemons._selfimprove_loop())
        finally:
            mios_daemons.asyncio.sleep = orig_sleep
            mios_daemons._toml_section = orig_toml
            mios_daemons._selfimprove_report = orig_report

        self.assertIn(("k", "s"), seen)

class _PG:
    """Minimal stand-in for the injected pg module (just an async execute)."""

    def __init__(self, fn):
        self.execute = fn

class SelfImproveReportTest(unittest.TestCase):
    def test_degrades_open_when_pg_unreachable(self):
        async def _boom(*_a, **_k):
            raise RuntimeError("pg unreachable")

        orig_pg = mios_daemons._mios_pg
        orig_toml = mios_daemons._toml_section
        mios_daemons._mios_pg = _PG(_boom)
        mios_daemons._toml_section = lambda _s: {}
        try:
            out = _run(mios_daemons._selfimprove_report())
        finally:
            mios_daemons._mios_pg = orig_pg
            mios_daemons._toml_section = orig_toml
        self.assertEqual(out, {"findings": [], "tools_analyzed": 0,
                               "samples": 0, "error": "unavailable"})

    def test_reads_selfimprove_ssot_tunables(self):
        captured = {}

        async def _rows(_sql, params, fetch=False):
            captured["limit"] = params["k"]
            return [{"tool": "x"}]

        def _analyze(rows, **kw):
            captured["kw"] = kw
            return {"findings": [], "ok": True}

        class _Rep2:
            def snapshot(self):
                return {"peer": 0.9}

        orig_pg = mios_daemons._mios_pg
        orig_toml = mios_daemons._toml_section
        orig_analyze = mios_daemons.mios_selfimprove.analyze
        orig_rep = mios_daemons._A2A_REPUTATION
        mios_daemons._mios_pg = _PG(_rows)
        mios_daemons._toml_section = lambda _s: {
            "sample_size": 42, "min_samples": 7,
            "fail_threshold": 0.25, "slow_ms": 9999}
        mios_daemons.mios_selfimprove.analyze = _analyze
        mios_daemons._A2A_REPUTATION = _Rep2()
        try:
            out = _run(mios_daemons._selfimprove_report())
        finally:
            mios_daemons._mios_pg = orig_pg
            mios_daemons._toml_section = orig_toml
            mios_daemons.mios_selfimprove.analyze = orig_analyze
            mios_daemons._A2A_REPUTATION = orig_rep
        self.assertEqual(out, {"findings": [], "ok": True})
        self.assertEqual(captured["limit"], 42)
        self.assertEqual(captured["kw"]["min_samples"], 7)
        self.assertEqual(captured["kw"]["fail_threshold"], 0.25)
        self.assertEqual(captured["kw"]["slow_ms"], 9999)
        self.assertEqual(captured["kw"]["reputation"], {"peer": 0.9})

class MembershipWatchLoopTest(unittest.TestCase):
    def test_reload_fires_on_mtime_change(self):
        import os as _os
        reloads = []

        async def _reload(reason="manual"):
            reloads.append(reason)

        mtimes = {"p": [1.0, 2.0]}  # mtime changes between the seed read and tick 1

        def _fake_stat(_p):
            class _S:
                st_mtime = mtimes["p"].pop(0) if mtimes["p"] else 2.0
            return _S()

        sleeper = _CancelAfter(fire_after=2)
        orig_sleep = mios_daemons.asyncio.sleep
        orig_stat = mios_daemons.os.stat
        mios_daemons.asyncio.sleep = sleeper
        mios_daemons.os.stat = _fake_stat
        mios_daemons.configure(
            _reload_membership=_reload, _MEMBERSHIP_WATCH_PATHS=["p"],
            MEMBERSHIP_WATCH_INTERVAL_S=30)
        try:
            _run(mios_daemons._membership_watch_loop())
        finally:
            mios_daemons.asyncio.sleep = orig_sleep
            mios_daemons.os.stat = orig_stat

        self.assertTrue(reloads)
        self.assertTrue(reloads[0].startswith("mtime:"))

class ReputationHelpersTest(unittest.TestCase):
    def test_flush_and_restore_noop_when_not_primary(self):
        mios_daemons.configure(_PG_PRIMARY=False)
        _run(mios_daemons._reputation_flush())
        _run(mios_daemons._reputation_restore())

class KvGcSweepTest(unittest.TestCase):
    def test_evicts_old_unprotected_matching_only(self):
        old = time.time() - 100000.0
        with tempfile.TemporaryDirectory() as d:
            evictable = os.path.join(d, mios_daemons._kv_filename("zqx7slotA"))
            resident = os.path.join(d, mios_daemons._kv_filename("zqx7resB"))
            unrelated = os.path.join(d, "zqx7unrelated.tmp")
            for p in (evictable, resident, unrelated):
                with open(p, "wb") as f:
                    f.write(b"\x00\x00")
                os.utime(p, (old, old))
            mios_daemons.configure(
                KV_SLOTS_DIR=d, KV_GC_TTL_S=1.0, KV_GC_MAX_BYTES=0,
                _KV_RESIDENT={"slot": "zqx7resB"})
            mios_daemons._kv_gc_sweep_once()
            self.assertFalse(os.path.exists(evictable),
                             "stale unprotected KV file should be evicted")
            self.assertTrue(os.path.exists(resident),
                            "the active-slot (resident) file must be protected")
            self.assertTrue(os.path.exists(unrelated),
                            "a non-KV file must never be touched")

    def test_missing_dir_is_noop(self):
        mios_daemons.configure(KV_SLOTS_DIR=os.path.join(
            tempfile.gettempdir(), "zqx7-does-not-exist"))
        mios_daemons._kv_gc_sweep_once()  # must not raise

class KvGcLoopTest(unittest.TestCase):
    def test_loop_invokes_sweep_then_survives(self):
        calls = {"n": 0}

        def _fake_sweep():
            calls["n"] += 1

        sleeper = _CancelAfter(fire_after=2)  # 1st sleep returns -> sweep, 2nd cancels
        orig_sleep = mios_daemons.asyncio.sleep
        orig_sweep = mios_daemons._kv_gc_sweep_once
        mios_daemons.asyncio.sleep = sleeper
        mios_daemons._kv_gc_sweep_once = _fake_sweep
        mios_daemons.configure(KV_GC_INTERVAL_S=0)
        try:
            _run(mios_daemons._kv_gc_loop())
        finally:
            mios_daemons.asyncio.sleep = orig_sleep
            mios_daemons._kv_gc_sweep_once = orig_sweep
        self.assertEqual(calls["n"], 1)

class SelfImproveActPassTest(unittest.TestCase):
    """The task_64/task_66 ACT pass: propose -> prove utility -> QUEUE for human approval,
    never auto-apply. Synthetic non-dictionary surfaces/ids + stubbed solver scores
    (no live models / DB) -- the structure is what is validated offline."""

    def _restore(self, saved):
        for k, v in saved.items():
            setattr(mios_daemons, k, v)

    def _setup_act(self, report, draft, evaluate, execute):
        saved = {k: getattr(mios_daemons, k) for k in (
            "_toml_section", "_selfimprove_report", "_act_draft_proposal",
            "_act_evaluate_proposal", "_mios_pg"
        )}
        mios_daemons._toml_section = lambda _s: {
            "act_enabled": True, "improvable_targets": ["zq_ok"],
            "protected_targets": ["zp_no"], "accept_margin": 0.0,
            "max_proposals_per_pass": 3}
        mios_daemons._selfimprove_report = report
        mios_daemons._act_draft_proposal = draft
        mios_daemons._act_evaluate_proposal = evaluate
        mios_daemons._mios_pg = _PG(execute)
        return saved

    def test_disabled_is_noop(self):
        drafted = {"called": False}

        async def _draft(_f):
            drafted["called"] = True
            return {"target_kind": "zq_ok", "target_id": "z", "change": "zc",
                    "rationale": "zr"}

        saved = {"_toml_section": mios_daemons._toml_section,
                 "_act_draft_proposal": mios_daemons._act_draft_proposal}
        mios_daemons._toml_section = lambda _s: {"act_enabled": False}
        mios_daemons._act_draft_proposal = _draft
        try:
            out = _run(mios_daemons._selfimprove_act_pass())
        finally:
            self._restore(saved)
        self.assertEqual(out["acted"], False)
        self.assertEqual(out["queued"], 0)
        self.assertFalse(drafted["called"], "a disabled pass must not draft or act")

    def test_queues_non_regressing_proposal(self):
        captured = []

        async def _report():
            return {"findings": [{"severity": "high", "kind": "zk", "subject": "zs",
                                  "detail": "zd", "suggestion": "zfix"}]}

        async def _draft(_f):
            return {"target_kind": "zq_ok", "target_id": "zt1", "change": "zc",
                    "rationale": "zr"}

        async def _evaluate(_p):
            return (0.4, 0.6)              # proposed > baseline -> non-regressing

        async def _execute(sql, params, fetch=False):
            captured.append((sql, params))
            return None

        saved = self._setup_act(_report, _draft, _evaluate, _execute)
        try:
            out = _run(mios_daemons._selfimprove_act_pass())
        finally:
            self._restore(saved)
        self.assertEqual(out["queued"], 1)
        self.assertEqual(out["rejected"], 0)
        self.assertEqual(len(captured), 1, "exactly one queue write")
        sql, params = captured[0]
        self.assertIn("INSERT INTO event", sql)
        self.assertNotIn("UPDATE", sql.upper())          # nothing applied/mutated
        self.assertEqual(params["kind"], mios_daemons._PROPOSAL_EVENT_KIND)
        self.assertIn("pending_review", params["payload"])  # awaits human approval

    def test_rejects_regressing_proposal(self):
        captured = []

        async def _report():
            return {"findings": [{"severity": "high", "kind": "zk", "subject": "zs"}]}

        async def _draft(_f):
            return {"target_kind": "zq_ok", "target_id": "zt1", "change": "zc",
                    "rationale": "zr"}

        async def _evaluate(_p):
            return (0.7, 0.5)              # proposed < baseline -> regression

        async def _execute(sql, params, fetch=False):
            captured.append((sql, params))

        saved = self._setup_act(_report, _draft, _evaluate, _execute)
        try:
            out = _run(mios_daemons._selfimprove_act_pass())
        finally:
            self._restore(saved)
        self.assertEqual(out["queued"], 0)
        self.assertEqual(out["rejected"], 1)
        self.assertEqual(len(captured), 0, "a regressing proposal is never queued")

    def test_rejects_isolation_violation_before_scoring(self):
        eval_called = {"n": 0}
        captured = []

        async def _report():
            return {"findings": [{"severity": "high", "kind": "zk", "subject": "zs"}]}

        async def _draft(_f):
            return {"target_kind": "zp_no", "target_id": "zt1", "change": "zc",
                    "rationale": "zr"}    # targets a PROTECTED kind

        async def _evaluate(_p):
            eval_called["n"] += 1
            return (0.0, 1.0)             # a perfect "improvement" -- must NOT be reached

        async def _execute(sql, params, fetch=False):
            captured.append((sql, params))

        saved = self._setup_act(_report, _draft, _evaluate, _execute)
        try:
            out = _run(mios_daemons._selfimprove_act_pass())
        finally:
            self._restore(saved)
        self.assertEqual(out["queued"], 0)
        self.assertEqual(out["rejected"], 1)
        self.assertEqual(eval_called["n"], 0,
                         "an isolation-violating proposal must never be scored")
        self.assertEqual(len(captured), 0, "never queued")

    def test_loop_runs_act_pass_each_iteration(self):
        calls = {"n": 0}

        async def _report():
            return {"findings": []}

        async def _act():
            calls["n"] += 1
            return {"acted": False}

        sleeper = _CancelAfter(fire_after=2)
        saved = {"asyncio": mios_daemons.asyncio.sleep,
                 "_toml_section": mios_daemons._toml_section,
                 "_selfimprove_report": mios_daemons._selfimprove_report,
                 "_selfimprove_act_pass": mios_daemons._selfimprove_act_pass}
        mios_daemons.asyncio.sleep = sleeper
        mios_daemons._toml_section = lambda _s: {"interval_min": 1}
        mios_daemons._selfimprove_report = _report
        mios_daemons._selfimprove_act_pass = _act
        try:
            _run(mios_daemons._selfimprove_loop())
        finally:
            mios_daemons.asyncio.sleep = saved["asyncio"]
            mios_daemons._toml_section = saved["_toml_section"]
            mios_daemons._selfimprove_report = saved["_selfimprove_report"]
            mios_daemons._selfimprove_act_pass = saved["_selfimprove_act_pass"]
        self.assertEqual(calls["n"], 1)

class SelfImproveProposalsReadTest(unittest.TestCase):
    def test_degrades_open_when_pg_unreachable(self):
        async def _boom(*_a, **_k):
            raise RuntimeError("pg unreachable")

        saved = mios_daemons._mios_pg
        mios_daemons._mios_pg = _PG(_boom)
        try:
            out = _run(mios_daemons._selfimprove_proposals())
        finally:
            mios_daemons._mios_pg = saved
        self.assertEqual(out["proposals"], [])
        self.assertEqual(out["count"], 0)
        self.assertEqual(out["error"], "unavailable")

    def test_reads_queued_proposal_rows(self):
        captured = {}

        async def _rows(_sql, params, fetch=False):
            captured["kind"] = params["kind"]
            captured["sql"] = _sql
            return [{"id": 1, "summary": "zsum", "payload": {"status": "pending_review"}}]

        saved = mios_daemons._mios_pg
        mios_daemons._mios_pg = _PG(_rows)
        try:
            out = _run(mios_daemons._selfimprove_proposals())
        finally:
            mios_daemons._mios_pg = saved
        self.assertEqual(out["count"], 1)
        self.assertEqual(captured["kind"], mios_daemons._PROPOSAL_EVENT_KIND)
        self.assertIn("FROM event", captured["sql"])



# ==============================================================================
# Consolidated from test_mios_account_sync.py (task_1006)
# ==============================================================================
# AI-hint: stdlib unit test for mios-account-sync daemon.
# AI-related: usr/libexec/mios/mios-account-sync
import sys
import os
import unittest
from unittest.mock import patch, MagicMock, mock_open
from collections import namedtuple

struct_passwd = namedtuple("struct_passwd", ["pw_name", "pw_passwd", "pw_uid", "pw_gid", "pw_gecos", "pw_dir", "pw_shell"])
struct_group = namedtuple("struct_group", ["gr_name", "gr_passwd", "gr_gid", "gr_mem"])

class MockPwdModule:
    def __init__(self): self.users = {}
    def getpwnam(self, name):
        if name in self.users: return self.users[name]
        raise KeyError(name)
    def getpwall(self): return list(self.users.values())

class MockGrpModule:
    def __init__(self): self.groups_by_id = {}; self.groups_by_name = {}
    def getgrgid(self, gid):
        if gid in self.groups_by_id: return self.groups_by_id[gid]
        raise KeyError(gid)
    def getgrnam(self, name):
        if name in self.groups_by_name: return self.groups_by_name[name]
        raise KeyError(name)
    def getgrall(self): return list(self.groups_by_name.values())

mock_pwd = MockPwdModule()
mock_grp = MockGrpModule()
sys.modules["pwd"] = mock_pwd
sys.modules["grp"] = mock_grp

from importlib.machinery import SourceFileLoader
import importlib.util
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
script_path = os.path.join(root_dir, "usr/libexec/mios/mios-account-sync")
loader = SourceFileLoader("mios_account_sync", script_path)
spec = importlib.util.spec_from_loader("mios_account_sync", loader)
sync_mod = importlib.util.module_from_spec(spec)
loader.exec_module(sync_mod)

class TestMiosAccountSync(unittest.TestCase):

    def setUp(self):
        mock_pwd.users.clear()
        mock_grp.groups_by_id.clear()
        mock_grp.groups_by_name.clear()
        mock_grp.groups_by_id[1000] = struct_group("mios", "x", 1000, [])
        mock_grp.groups_by_name["mios"] = mock_grp.groups_by_id[1000]

    @patch("subprocess.run")
    @patch("os.path.isfile")
    def test_sync_create_user(self, mock_isfile, mock_run):
        db_accounts = [{"name": "testuser", "password_hash": "hash123", "uid": 1005, "gid": 1000,
                        "display": "Test User", "home_dir": "/var/home/testuser", "shell": "/bin/bash",
                        "groups": "wheel,libvirt", "is_admin": True, "enabled": True}]
        mock_isfile.return_value = False  # no state file
        mock_run.return_value = MagicMock(returncode=0, stdout="")

        with patch.object(sync_mod, "query_db_accounts", return_value=db_accounts):
            with patch.object(sync_mod, "get_local_shadow_hashes", return_value={}):
                with patch("builtins.open", mock_open()) as mock_file:
                    sync_mod.sync_accounts()

        calls = [c[0][0] for c in mock_run.call_args_list]
        useradd_called = any("useradd" in cmd for cmd in calls)
        self.assertTrue(useradd_called, "Should call useradd for new user")

        useradd_cmd = next(cmd for cmd in calls if "useradd" in cmd)
        self.assertIn("-u", useradd_cmd)
        self.assertIn("1005", useradd_cmd)
        self.assertIn("-p", useradd_cmd)
        self.assertIn("hash123", useradd_cmd)
        self.assertIn("testuser", useradd_cmd)

    @patch("subprocess.run")
    @patch("os.path.isfile")
    def test_sync_update_user(self, mock_isfile, mock_run):
        mock_pwd.users["testuser"] = struct_passwd(
            "testuser", "x", 1005, 1000, "Old Name", "/var/home/testuser", "/bin/sh"
        )
        db_accounts = [{"name": "testuser", "password_hash": "hash123", "uid": 1005, "gid": 1000,
                        "display": "New Name", "home_dir": "/var/home/testuser", "shell": "/bin/bash",
                        "groups": "", "is_admin": False, "enabled": True}]
        mock_isfile.return_value = False
        mock_run.return_value = MagicMock(returncode=0)

        with patch.object(sync_mod, "query_db_accounts", return_value=db_accounts):
            with patch.object(sync_mod, "get_local_shadow_hashes", return_value={"testuser": "hash123"}):
                with patch("builtins.open", mock_open()):
                    sync_mod.sync_accounts()

        calls = [c[0][0] for c in mock_run.call_args_list]
        usermod_called = any("usermod" in cmd for cmd in calls)
        self.assertTrue(usermod_called, "Should update existing user parameters via usermod")

        usermod_cmd = next(cmd for cmd in calls if "usermod" in cmd)
        self.assertIn("-c", usermod_cmd)
        self.assertIn("New Name", usermod_cmd)
        self.assertIn("-s", usermod_cmd)
        self.assertIn("/bin/bash", usermod_cmd)

    @patch("subprocess.run")
    @patch("os.path.isfile")
    def test_sync_password_writeback(self, mock_isfile, mock_run):
        mock_pwd.users["testuser"] = struct_passwd(
            "testuser", "x", 1005, 1000, "Test User", "/var/home/testuser", "/bin/bash"
        )
        db_accounts = [{"name": "testuser", "password_hash": "old_hash", "uid": 1005, "gid": 1000,
                        "display": "Test User", "home_dir": "/var/home/testuser", "shell": "/bin/bash",
                        "groups": "", "is_admin": False, "enabled": True}]
        mock_isfile.return_value = True
        mock_run.return_value = MagicMock(returncode=0)
        state_data = '{"testuser": "old_hash"}'
        shadow_data = {"testuser": "new_local_hash"}

        with patch.object(sync_mod, "query_db_accounts", return_value=db_accounts):
            with patch.object(sync_mod, "get_local_shadow_hashes", return_value=shadow_data):
                with patch("builtins.open", mock_open(read_data=state_data)) as mock_file:
                    sync_mod.sync_accounts()

        calls = [c[0][0] for c in mock_run.call_args_list]
        db_writeback_called = any(any("mios-pg-query" in arg for arg in cmd) for cmd in calls)
        self.assertTrue(db_writeback_called, "Should trigger a writeback command to the database")

    @patch("subprocess.run")
    @patch("os.path.isfile")
    def test_sync_lock_disabled_user(self, mock_isfile, mock_run):
        mock_pwd.users["testuser"] = struct_passwd(
            "testuser", "x", 1005, 1000, "Test User", "/var/home/testuser", "/bin/bash"
        )
        db_accounts = [{"name": "otheruser", "password_hash": "hash321", "uid": 1006, "gid": 1000,
                        "display": "Other User", "home_dir": "/var/home/otheruser", "shell": "/bin/bash",
                        "groups": "", "is_admin": False, "enabled": True}]
        mock_isfile.return_value = False
        mock_run.return_value = MagicMock(returncode=0)
        shadow_data = {"testuser": "$6$somehash"}

        with patch.object(sync_mod, "query_db_accounts", return_value=db_accounts):
            with patch.object(sync_mod, "get_local_shadow_hashes", return_value=shadow_data):
                with patch("builtins.open", mock_open()):
                    sync_mod.sync_accounts()

        calls = [c[0][0] for c in mock_run.call_args_list]
        lock_called = any(cmd == ["usermod", "-L", "testuser"] for cmd in calls)
        self.assertTrue(lock_called, "Should lock local user missing from DB using usermod -L")

def _run_extra_account_sync(): return 0



# ==============================================================================
# Consolidated from test_mios_conductor.py (task_1006)
# ==============================================================================
# AI-hint: stub
# AI-related: stub
import asyncio
import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mios_pipe.routing.conductor as mios_conductor

async def _main_conductor():
    with patch.object(mios_conductor, "CONDUCTOR_DIR", "/tmp/mios-conductor-test"), \
         patch("os.path.exists", return_value=True), patch("builtins.open", MagicMock()):
        jinja2_mock = MagicMock()
        template_instance = MagicMock()
        template_instance.render.return_value = "fake_yaml"
        jinja2_mock.Template.return_value = template_instance
        sandbox_mock = MagicMock()
        sandbox_mock.return_value.from_string.return_value = template_instance

        yaml_mock = MagicMock()
        yaml_instance = MagicMock()
        yaml_instance.load.return_value = {
            "steps": [
                {"name": "step1", "action": "exec", "argv": ["/usr/bin/printf", "step 1\n"]},
                {"name": "parallel_group", "parallel": True, "fail_fast": True, "steps": [
                    {"name": "step2a", "action": "exec", "argv": ["/usr/bin/printf", "step 2a\n"]},
                    {"name": "step2b_fail", "action": "exec", "argv": ["/usr/bin/printf", "exit 1"]},
                ]},
                {"name": "step3_skipped", "action": "exec", "argv": ["/usr/bin/printf", "step 3\n"]},
            ]
        }
        yaml_mock.YAML.return_value = yaml_instance

        mios_conductor.jinja2 = jinja2_mock
        mios_conductor.ruamel = MagicMock()
        mios_conductor.ruamel.yaml = yaml_mock
        mios_conductor.SandboxedEnvironment = sandbox_mock
        mios_conductor.configure(allowed_exec_commands={"/usr/bin/printf"})

        process_mock_success = MagicMock()
        process_mock_success.communicate = AsyncMock(return_value=(b"output\n", b""))
        process_mock_success.returncode = 0

        process_mock_fail = MagicMock()
        process_mock_fail.communicate = AsyncMock(return_value=(b"", b"error"))
        process_mock_fail.returncode = 1

        def side_effect(*argv, **kwargs):
            return process_mock_fail if "exit 1" in argv else process_mock_success

        with patch("asyncio.create_subprocess_exec", side_effect=AsyncMock(side_effect=side_effect)) as m_subprocess:
            res = await mios_conductor.execute_conductor_workflow("test-workflow", {})
            print("Result:", res)
            assert res["success"] is False, "Workflow should fail due to step2b_fail"
            assert res["workflow"] == "test-workflow"
            assert len(res["results"]) == 3, f"Expected 3 step results, got {len(res['results'])}"
            assert res["results"][0]["step"] == "step1"
            assert res["results"][1]["step"] == "step2a"
            assert res["results"][2]["step"] == "step2b_fail"
            print("PASS: Conductor deterministic orchestration via DAG handler.")

def _run_extra_conductor():
    _saved_env = dict(os.environ)
    try:
        return asyncio.run(_main_conductor())
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear(); os.environ.update(_saved_env)

class TestFolded_conductor(unittest.TestCase):
    def test_run_folded(self):
        self.assertIn(_run_extra_conductor(), (None, 0))



# ==============================================================================
# Consolidated from test_mios_daemon.py (task_1006)
# ==============================================================================
# AI-hint: stdlib unit test for mios_agent_call daemon runaway controls.
import unittest
import asyncio
import time
from unittest.mock import patch, MagicMock

import mios_agent_call
import mios_pipe.routing.agent_call as target_module

class AsyncContextMock:
    def __init__(self, *args, **kwargs): pass
    async def __aenter__(self): return self
    async def __aexit__(self, exc_type, exc_val, exc_tb): pass
    def __call__(self, *args, **kwargs): return self

async def dummy_async(*args, **kwargs): pass

class TestMiosDaemonGateAndDedup(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        target_module._IN_FLIGHT_PROMPTS.clear()
        target_module._SESSION_TOKENS.clear()
        target_module._AUTONOMOUS_SOURCE_TOKENS.clear()
        target_module._dispatch_depth_var.set(0)
        target_module._opt_int_mb = lambda x: int(x or 0)
        target_module._lane_sem_key = lambda cfg: "test-lane"
        target_module._strip_agent_chrome = lambda text: text
        self.old_rr_enable = target_module.RR_ENABLE
        target_module.RR_ENABLE = False
        class MockSloShed(Exception): pass
        target_module._SloShed = MockSloShed

    async def asyncTearDown(self):
        target_module.RR_ENABLE = self.old_rr_enable

    @patch("mios_pipe.routing.agent_call._get_cpu_load")
    @patch("mios_pipe.routing.agent_call._get_gpu_vram_usage")
    @patch("mios_pipe.routing.agent_call._host_threshold_val")
    @patch("mios_pipe.routing.agent_call._agent_binding")
    @patch("mios_pipe.routing.agent_call._agent_offload_engine")
    async def test_host_pressure_gate_degrades_to_cpu(self, mock_offload, mock_binding, mock_threshold, mock_vram, mock_cpu):
        mock_cpu.return_value = 10.0
        mock_vram.return_value = 95.0 # above 90% threshold
        mock_offload.return_value = None

        mock_threshold.side_effect = lambda key, default: {
            "big_ram_model": "mistral-magistral-small-2509", "max_cpu_percent": 85.0,
            "max_vram_percent": 90.0, "small_ram_model": "granite4.1:8b"
        }.get(key, default)

        mock_binding.side_effect = [
            ("http://localhost:8640/v1", "mistral-magistral-small-2509"), # heavy
            ("http://localhost:8450/v1", "granite4.1:8b"), # degraded cpu
        ]

        cfg = {"vram_mb": 4096}
        body = {"messages": [{"role": "user", "content": "hello"}]}
        called_with_cpu = False

        async def mock_inner(name, cfg, body, headers, client, prefer_cpu=True):
            nonlocal called_with_cpu
            called_with_cpu = True
            return name, "degraded response"

        with patch("mios_pipe.routing.agent_call._call_agent_complete_inner", mock_inner), \
             patch("mios_pipe.routing.agent_call._admit", dummy_async), \
             patch("mios_pipe.routing.agent_call._priority_gate", AsyncContextMock), \
             patch("mios_pipe.routing.agent_call._endpoint_sem", AsyncContextMock), \
             patch("mios_pipe.routing.agent_call._lane_sem", AsyncContextMock), \
             patch("mios_pipe.routing.agent_call._model_active", dummy_async), \
             patch("mios_pipe.routing.agent_call._record_cost", MagicMock()):
            name, text = await target_module._call_agent_complete(
                "test-agent", cfg, body, {}, MagicMock(), prefer_cpu=False, priority=1.0
            )

        self.assertTrue(called_with_cpu)
        self.assertEqual(text, "degraded response")

    @patch("mios_pipe.routing.agent_call._agent_offload_engine")
    async def test_request_dedup_collapses_inflight(self, mock_offload):
        cfg = {"vram_mb": 0}
        body = {"messages": [{"role": "user", "content": "hello"}]}
        mock_offload.return_value = None

        inner_calls = 0
        async def mock_inner(name, cfg, body, headers, client, prefer_cpu=True):
            nonlocal inner_calls
            inner_calls += 1
            await asyncio.sleep(0.1) # yield control so concurrent task can enter
            return name, f"response {inner_calls}"

        with patch("mios_pipe.routing.agent_call._call_agent_complete_inner", mock_inner), \
             patch("mios_pipe.routing.agent_call._admit", dummy_async), \
             patch("mios_pipe.routing.agent_call._priority_gate", AsyncContextMock), \
             patch("mios_pipe.routing.agent_call._endpoint_sem", AsyncContextMock), \
             patch("mios_pipe.routing.agent_call._lane_sem", AsyncContextMock), \
             patch("mios_pipe.routing.agent_call._model_active", dummy_async), \
             patch("mios_pipe.routing.agent_call._record_cost", MagicMock()), \
             patch("mios_pipe.routing.agent_call._agent_binding", lambda c, e: ("http://localhost:8450/v1", "granite4.1:8b")):
            t1 = asyncio.create_task(target_module._call_agent_complete("agent1", cfg, body, {}, MagicMock(), priority=1.0))
            t2 = asyncio.create_task(target_module._call_agent_complete("agent1", cfg, body, {}, MagicMock(), priority=1.0))
            res1, res2 = await t1, await t2

        self.assertEqual(inner_calls, 1)
        self.assertEqual(res1, res2)
        self.assertEqual(res1[1], "response 1")

def _run_all_folded_daemons_suites():
    for fn in (_run_extra_account_sync, _run_extra_conductor):
        if (rc := fn()) not in (None, 0):
            sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _run_all_folded_daemons_suites()
    unittest.main()
