# AI-hint: Stdlib assert-script for mios_policy (R7 security wave). Proves the
# AI-related: ./mios_policy.py, ./mios_pdp.py
"""Standalone test: python test_mios_policy.py (exit 0 = pass)."""

import contextvars
import os

_here = os.path.dirname(os.path.abspath(__file__))
_repo = os.path.abspath(os.path.join(_here, "..", "..", "..", ".."))
_toml = os.path.join(_repo, "usr", "share", "mios", "mios.toml")
if "MIOS_TOML" not in os.environ and os.path.isfile(_toml):
    os.environ["MIOS_TOML"] = _toml

import mios_policy

_dispatch_agent_var = contextvars.ContextVar("dispatch_agent", default="")
_client_env_var = contextvars.ContextVar("client_env", default=None)
_hitl_approved_var = contextvars.ContextVar("hitl_approved", default=None)
_hitl_blocked_var = contextvars.ContextVar("hitl_blocked", default=None)

_VERB_CATALOG = {
    "web_search": {"permission": "read"},
    "create_file": {"permission": "write"},
    "powershell_run": {"permission": "interactive"},
    "os_recipe": {"permission": "interactive"},
}
_RECIPE_CATALOG = {
    "service-status": {"permission": "read"},
}
_AGENT_REGISTRY = {
    "researcher": {"denied_verbs": ["powershell_run"]},
    "readonly": {"max_permission": "read"},
}

mios_policy.configure(
    verb_catalog=_VERB_CATALOG,
    recipe_catalog=_RECIPE_CATALOG,
    agent_registry=_AGENT_REGISTRY,
    hitl_approved_var=_hitl_approved_var,
    hitl_blocked_var=_hitl_blocked_var,
    client_env_var=_client_env_var,
    dispatch_agent_var=_dispatch_agent_var,
    pending_hash=lambda tool, args: "stub-hash",
    get_client=lambda: None,
    db_fire=lambda *a, **k: None,
    db_post=lambda *a, **k: None,
    db_create=lambda *a, **k: None,
)

def _tool(name):
    return {"type": "function", "function": {"name": name}}

r_read = mios_policy._perm_rank("read")
r_write = mios_policy._perm_rank("write")
r_inter = mios_policy._perm_rank("interactive")
assert r_read < r_write < r_inter, (r_read, r_write, r_inter)
assert mios_policy._perm_rank("nonsense-tier") > r_inter
print("ok  _perm_rank read<write<interactive + unknown fail-closed")

assert mios_policy._effective_perm("web_search") == "read"
assert mios_policy._effective_perm("powershell_run") == "interactive"
assert mios_policy._effective_perm("os_recipe", {"name": "service_status"}) == "read"
assert mios_policy._effective_perm("os_recipe", {"name": "no-such"}) == "interactive"
print("ok  _effective_perm verb + recipe-aware resolution")

surface = [_tool("web_search"), _tool("create_file"), _tool("powershell_run")]
filtered = mios_policy._agent_rbac_filter("researcher", surface)
fnames = {t["function"]["name"] for t in filtered}
assert "powershell_run" not in fnames, fnames  # denied verb dropped
assert "web_search" in fnames and "create_file" in fnames, fnames  # safe verbs kept
ro = mios_policy._agent_rbac_filter("readonly", surface)
ronames = {t["function"]["name"] for t in ro}
assert ronames == {"web_search"}, ronames
assert len(mios_policy._agent_rbac_filter("ghost", surface)) == len(surface)
print("ok  _agent_rbac_filter denied/ceiling drop + safe pass-through")

def _as_agent(name, fn):
    ctx = contextvars.copy_context()
    return ctx.run(lambda: (_dispatch_agent_var.set(name), fn())[1])

blocked = _as_agent("researcher", lambda: mios_policy._dispatch_pdp_reason("powershell_run"))
assert blocked is not None and "powershell_run" in blocked, blocked
allowed = _as_agent("researcher", lambda: mios_policy._dispatch_pdp_reason("web_search"))
assert allowed is None, allowed
assert mios_policy._dispatch_pdp_reason("powershell_run") is None
print("ok  _dispatch_pdp_reason blocks denied / allows safe / no-op off-agent")

mios_policy._HITL_MODE = "block"
mios_policy._HITL_THRESHOLD = "interactive"
try:
    reason = mios_policy._hitl_block_reason("powershell_run")
    assert reason is not None and "powershell_run" in reason, reason  # high-tier blocked
    assert mios_policy._hitl_block_reason("web_search") is None  # below threshold proceeds
finally:
    mios_policy._HITL_MODE = "off"
print("ok  _hitl_block_reason blocks interactive / passes read in block-mode")

# ---------------------------------------------------------------------------
# task_208: the durable quota ledger. Two principals accrue separately, the window
# is written through, and a RESTART (a fresh module state seeded from the same
# rows) leaves both balances -- and the enforcement -- intact.
# ---------------------------------------------------------------------------
import asyncio as _asyncio

class _FakePg:
    """A quota_ledger in a dict, with the same SELECT/UPSERT shape as psycopg."""

    def __init__(self, rows=None, fail=False):
        self.rows = dict(rows or {})
        self.fail = fail
        self.writes = 0

    async def execute(self, sql, params=None, *, fetch=False, cfg=None, rls_owner=None):
        if self.fail:
            raise RuntimeError("store unreachable")
        if sql.strip().upper().startswith("SELECT"):
            return [{"principal": k, "window_start": v[0], "spent": v[1]}
                    for k, v in sorted(self.rows.items())]
        self.writes += 1
        self.rows[params["p"]] = (params["w"], params["s"])
        return None

def _reseat(pg):
    """Simulate a process restart: clear every in-memory quota structure, then
    re-run the startup preload against the same store."""
    mios_policy._QUOTA_TRACKERS.clear()
    mios_policy._QUOTA_HYDRATED.clear()
    mios_policy._QUOTA_LEDGER.clear()
    mios_policy._QUOTA_PERSIST = False
    mios_policy._mios_pg = pg
    return _asyncio.run(mios_policy.quota_preload())

async def _spend_async(principal, cost, budget):
    tr = mios_policy._quota_for(principal, {"daily_budget": budget})
    now = _time.time()
    mios_policy._quota_hydrate(principal, tr, now)
    v = tr.check(principal, now, cost=cost)
    if v.allowed:
        mios_policy._quota_persist(principal, tr)
        await _asyncio.sleep(0)   # let the fire-and-forget upsert run
        await _asyncio.sleep(0)
    return v

def _spend(principal, cost, budget=10.0):
    """The gate runs inside the event loop, so the write-through does too --
    _quota_save deliberately no-ops when no loop is running."""
    return _asyncio.run(_spend_async(principal, cost, budget))

import time as _time

_pg = _FakePg()
assert _reseat(_pg) == 0, "an empty ledger preloads nothing"
_spend("alice", 3.0)
_spend("alice", 2.0)
_spend("bob", 1.0)
assert _pg.rows["alice"][1] == 5.0, _pg.rows
assert _pg.rows["bob"][1] == 1.0, _pg.rows
print("ok  quota ledger: two principals accrue SEPARATELY and are written through")

n = _reseat(_pg)
assert n == 2, n
tr_a = mios_policy._quota_for("alice", {"daily_budget": 10.0})
tr_b = mios_policy._quota_for("bob", {"daily_budget": 10.0})
_now = _time.time()
mios_policy._quota_hydrate("alice", tr_a, _now)
mios_policy._quota_hydrate("bob", tr_b, _now)
assert tr_a.spent("alice", _now) == 5.0, tr_a.spent("alice", _now)
assert tr_b.spent("bob", _now) == 1.0, tr_b.spent("bob", _now)
print("ok  quota ledger: a RESTART leaves both balances intact, not reset to zero")

assert _spend("alice", 6.0).allowed is False, "an exhausted budget must stay exhausted"
assert _spend("bob", 6.0).allowed is True, "a principal under budget is unaffected"
print("ok  quota ledger: the budget is still ENFORCED across the restart")

_hydrated_once = _pg.writes
mios_policy._quota_hydrate("alice", tr_a, _now)
assert _pg.writes == _hydrated_once, "hydrate must not write"
print("ok  quota ledger: hydration happens once and never writes")

_bad = _FakePg(fail=True)
assert _reseat(_bad) == 0, "an unreachable store must preload nothing, not raise"
assert mios_policy._QUOTA_PERSIST is False, "a failed preload must not claim persistence"
assert _spend("carol", 1.0).allowed is True, "an unreachable store must not block work"
print("ok  quota ledger: an unreachable store degrades OPEN")

mios_policy._QUOTA_TRACKERS.clear()
mios_policy._QUOTA_HYDRATED.clear()
mios_policy._QUOTA_LEDGER.clear()
mios_policy._QUOTA_PERSIST = False

# ---------------------------------------------------------------------------
# Task 3159: policy arbiter consultation and fail-closed integration.
# A dangerous verb routed through a stub arbiter that denies is refused.
# The same verb with the arbiter unreachable is ALSO refused (fail closed).
# ---------------------------------------------------------------------------
class _StubArbiterClient:
    def __init__(self, allow=False, reason="Action denied by policy arbiter", status_code=200):
        self.allow = allow
        self.reason = reason
        self.status_code = status_code
        self.posted = []

    async def post(self, url, json=None, timeout=None):
        self.posted.append({"url": url, "json": json})
        class _Resp:
            def __init__(self, sc, body):
                self.status_code = sc
                self.content = b"true"
                self._body = body
            def json(self):
                return self._body
        return _Resp(self.status_code, {"allow": self.allow, "reason": self.reason})

class _UnreachableArbiterClient:
    async def post(self, url, json=None, timeout=None):
        raise ConnectionRefusedError(f"Connection refused to {url}")

_audit_events = []
def _audit_db_fire(coro_or_val):
    pass
def _audit_db_post(coro_or_val):
    return coro_or_val
def _audit_db_create(kind, data, **kw):
    _audit_events.append((kind, data))
    return data

# Verify default fallback URL and fail mode configuration
assert mios_policy._HITL_ARBITER_URL.startswith("http://127.0.0.1:8760"), mios_policy._HITL_ARBITER_URL
assert mios_policy._HITL_ARBITER_FAIL == "closed", mios_policy._HITL_ARBITER_FAIL
print("ok  hitl_arbiter: default URL is port 8760 and default fail mode is closed")

# Case 1: Dangerous verb routed through a stub arbiter that denies is refused
_stub_deny = _StubArbiterClient(allow=False, reason="stub policy deny: powershell is forbidden")
async def _get_stub_deny():
    return _stub_deny

_audit_events.clear()
mios_policy.configure(
    get_client=_get_stub_deny,
    db_fire=_audit_db_fire,
    db_post=_audit_db_post,
    db_create=_audit_db_create,
)

_res_deny = _asyncio.run(mios_policy._hitl_arbiter_verdict("powershell_run", {"command": "dir"}))
assert _res_deny is not None, "Dangerous verb routed through stub arbiter that denies must be refused"
assert "stub policy deny" in _res_deny or "denied" in _res_deny, _res_deny
assert len(_stub_deny.posted) == 1, _stub_deny.posted
assert _stub_deny.posted[0]["json"]["verb"] == "powershell_run"
assert any(ev[1].get("kind") == "arbiter_deny" for ev in _audit_events), _audit_events
print("ok  hitl_arbiter: dangerous verb routed through denying stub arbiter is refused")

# Case 2: Same verb with the arbiter unreachable is ALSO refused (fail closed)
async def _get_unreachable():
    return _UnreachableArbiterClient()

_audit_events.clear()
mios_policy.configure(
    get_client=_get_unreachable,
    db_fire=_audit_db_fire,
    db_post=_audit_db_post,
    db_create=_audit_db_create,
)

_res_unreach = _asyncio.run(mios_policy._hitl_arbiter_verdict("powershell_run", {"command": "dir"}))
assert _res_unreach is not None, "Dangerous verb with unreachable arbiter must be refused (fail-closed)"
assert "unreachable" in _res_unreach and "fail-closed" in _res_unreach, _res_unreach
assert any(ev[1].get("kind") == "arbiter_unreachable" for ev in _audit_events), _audit_events
print("ok  hitl_arbiter: unreachable arbiter fails closed and refuses action")

# Case 3: Arbiter explicit allow permits execution
_stub_allow = _StubArbiterClient(allow=True)
async def _get_stub_allow():
    return _stub_allow

mios_policy.configure(get_client=_get_stub_allow)
_res_allow = _asyncio.run(mios_policy._hitl_arbiter_verdict("powershell_run", {"command": "dir"}))
assert _res_allow is None, "Explicit allow from arbiter must permit verb execution"
print("ok  hitl_arbiter: allowing arbiter permits action")

# Case 4: Safe verb below threshold is not consulted or blocked
_res_safe = _asyncio.run(mios_policy._hitl_arbiter_verdict("web_search", {"query": "test"}))
assert _res_safe is None, "Safe verb below threshold must proceed"
print("ok  hitl_arbiter: safe verb below threshold proceeds without arbiter block")

print("\nALL mios_policy tests passed")
