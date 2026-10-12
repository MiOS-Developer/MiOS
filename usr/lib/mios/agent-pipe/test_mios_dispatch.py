# AI-hint: Offline stdlib-assert test for mios_dispatch (the verb->bash dispatch chokepoint).
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Stdlib assert-script gates for mios_dispatch -- run: python test_mios_dispatch.py"""
import asyncio
import contextvars
import time

import mios_dispatch

_CATALOG = {
    "web_search": {"cmd": "mios-web-search -n {limit=5} {query!}"},
    "open_url": {"cmd": "mios-open-url {url!}"},
    "knowledge_search": {"cmd": "mios-knowledge {query!}"},
    "launch_app": {"cmd": "mios-launch {name!}", "cmd_args": "mios-launch {name!}{args*}"},
}

_conv = contextvars.ContextVar("conv", default="conv-1")
_prop = contextvars.ContextVar("prop", default=None)
_rec = contextvars.ContextVar("rec", default=None)
_agent = contextvars.ContextVar("agent", default="")

def _base_configure():
    mios_dispatch.configure(
        verb_catalog=_CATALOG,
        high_privilege_verbs=frozenset({"open_url"}),
        launch_verbs=frozenset({"launch_app", "open_url"}),
        web_dispatch_jitter_s=0.0,
        dispatch_dedup=False,
        native_loop_date_in_query=False,
        launcher_sock="/nonexistent/launcher.sock",
        dispatch_inflight={},
        conv_key_var=_conv,
        recency_ctx_var=_rec,
        proposal_var=_prop,
        dispatch_agent_var=_agent,
        resolve_verb_key=lambda n: n,
        current_date_str=lambda: time.strftime("%Y-%m-%d"),
        emit_dispatch_dedup_event=lambda *a, **k: None,
        db_fire=lambda coro: None,
        db_post=lambda *a, **k: None,
        db_create=lambda *a, **k: {},
    )

_base_configure()
cmd = mios_dispatch._build_dispatch_cmd("pc_key", {"key": "Ctrl+S"})
assert cmd == "mios-pc-control key-combo Ctrl+S", repr(cmd)
cmd = mios_dispatch._build_dispatch_cmd("pc_key", {"key": "Enter"})  # single key
assert cmd == "mios-pc-control key Enter", repr(cmd)
cmd = mios_dispatch._build_dispatch_cmd("pc_click", {"x": 10, "y": 20, "button": "left"})
assert cmd == "mios-pc-control click 10 20 left", repr(cmd)

cmd = mios_dispatch._build_dispatch_cmd("web_search", {"query": "hello world", "limit": 3})
assert cmd == "mios-web-search -n 3 'hello world'", repr(cmd)
assert mios_dispatch._build_dispatch_cmd("open_url", {"url": ""}) is None
cmd = mios_dispatch._build_dispatch_cmd("open_url", {"url": "https://x.test"})
assert cmd == "mios-open-url https://x.test", repr(cmd)

_planner_sample = ["web_search", "open_url", "knowledge_search", "pc_key", "pc_click"]
for v in _planner_sample:
    args = {"query": "q", "url": "https://x.test", "key": "a", "x": 1, "y": 2}
    out = mios_dispatch._build_dispatch_cmd(v, args)
    assert out, f"dispatch table missing planner verb {v!r}: {out!r}"
assert mios_dispatch._build_dispatch_cmd("totally_unknown_verb", {}) is None

_SYN_CATALOG = {
    "zqx_verb": {
        "params": {
            "mode_qq": {"enum": ["aa1", "bb2", "cc3"]},
            "free_kk": {},  # no enum -> never enum-validated
        },
    },
}
_SYN_MAP = {"zqx_verb": {"mode_qq": ["mq_alias"]}}
mios_dispatch.configure(
    verb_catalog=_SYN_CATALOG,
    verb_arg_synonyms=_SYN_MAP,
    sandbox_enforce=False,
    sandbox_self_confined=("xx-selfwrap",),
)

assert mios_dispatch._arg_with_synonyms("zqx_verb", "mode_qq", {"mode_qq": "aa1"}) == "aa1"
assert mios_dispatch._arg_with_synonyms("zqx_verb", "mode_qq", {"mq_alias": "bb2"}) == "bb2"
assert mios_dispatch._arg_with_synonyms("zqx_verb", "mode_qq", {"nope": "x"}) == ""

assert mios_dispatch._validate_enum_args("zqx_verb", {"mode_qq": "bb2"}) is None
_err = mios_dispatch._validate_enum_args("zqx_verb", {"mode_qq": "zzz9"})
assert _err and "not allowed" in _err, repr(_err)
assert mios_dispatch._validate_enum_args("zqx_verb", {"free_kk": "whatever"}) is None
assert mios_dispatch._validate_enum_args("unknown_verb_xx", {"a": "b"}) is None

_prof = mios_dispatch._dispatch_sandbox_profile("zqx_verb")
assert hasattr(_prof, "confined") and callable(getattr(_prof, "to_dict", None)), _prof
_c, _ws = mios_dispatch._sandbox_wrap_cmd("zqx_verb", "echo zz", _prof)
assert _c == "echo zz" and _ws is None, (_c, _ws)

_SELF_CATALOG = {"wrapme_vv": {"permission": "write", "sandbox_profile": "workspace"}}
mios_dispatch.configure(verb_catalog=_SELF_CATALOG, sandbox_enforce=True,
                        sandbox_self_confined=("xx-selfwrap",))
_prof_c = mios_dispatch._dispatch_sandbox_profile("wrapme_vv")
assert _prof_c.confined is True, _prof_c.to_dict()
_c3, _ws3 = mios_dispatch._sandbox_wrap_cmd("wrapme_vv", "xx-selfwrap echo hi", _prof_c)
assert _c3 == "xx-selfwrap echo hi" and _ws3 is None, (_c3, _ws3)
_c4, _ws4 = mios_dispatch._sandbox_wrap_cmd("wrapme_vv", "echo hi", _prof_c)
assert _c4 != "echo hi" and "mios-sandbox-exec" in _c4 and _ws4 is not None, (_c4, _ws4)

_broker_called = {"n": 0}

async def _explode_bounded(*a, **k):
    _broker_called["n"] += 1
    raise AssertionError("broker/_dispatch_bounded reached on a gated verb!")

def test_hitl_gate():
    _base_configure()
    mios_dispatch._dispatch_bounded = _explode_bounded
    mios_dispatch._hitl_block_reason = lambda tool, args: "human approval required"
    mios_dispatch._HITL_ARBITER_URL = ""
    mios_dispatch._pending_hash = lambda tool, args: "deadbeef"
    mios_dispatch._hitl_record_pending = lambda *a, **k: None
    res = asyncio.run(mios_dispatch.dispatch_mios_verb("open_url", {"url": "https://x.test"},
                                                       session_id="s1"))
    assert res.get("hitl_blocked") is True, res
    assert res.get("exit_code") == 126, res
    assert res.get("success") is False, res
    assert _broker_called["n"] == 0, "broker must NOT be reached on a HITL-gated verb"

test_hitl_gate()

def test_firewall_gate():
    _base_configure()
    mios_dispatch._dispatch_pdp_reason = lambda tool: None
    mios_dispatch._dispatch_quota_reason = lambda tool: None
    mios_dispatch._validate_enum_args = lambda t, a: None

    async def _tainted(session_id):
        return True, "open_url->external"
    mios_dispatch._session_is_tainted = _tainted
    res = asyncio.run(mios_dispatch._dispatch_mios_verb_inner(
        "open_url", {"url": "https://x.test"}, session_id="s1"))
    assert res.get("tainted") is True, res
    assert res.get("success") is False, res
    assert "firewall_block" in (res.get("stderr") or ""), res

test_firewall_gate()

async def _aret_none(*a, **k):
    return None

def test_hitl_inner_chokepoint_no_bypass():
    _base_configure()
    mios_dispatch._dispatch_pdp_reason = lambda tool: None
    mios_dispatch._dispatch_quota_reason = lambda tool: None
    mios_dispatch._validate_enum_args = lambda t, a: None
    mios_dispatch._HITL_ARBITER_URL = ""
    mios_dispatch._hitl_gate = _aret_none

    mios_dispatch._hitl_block_reason = lambda tool, args: None
    res_off = asyncio.run(mios_dispatch._dispatch_mios_verb_inner(
        "zzz_synth_verb", {"a": 1}, session_id="s1"))
    assert not res_off.get("hitl_blocked"), res_off

    mios_dispatch._hitl_block_reason = lambda tool, args: "human approval required"
    res_on = asyncio.run(mios_dispatch._dispatch_mios_verb_inner(
        "zzz_synth_verb", {"a": 1}, session_id="s1"))
    assert res_on.get("hitl_blocked") is True, res_on
    assert res_on.get("exit_code") == 126 and res_on.get("success") is False, res_on
    print("[PASS] inner chokepoint enforces the [ai] HITL gate (no silent bypass)")

test_hitl_inner_chokepoint_no_bypass()

def test_dispatch_persists_taint_row():
    _base_configure()
    captured = {}

    def _cap_create(table, row=None, **kw):
        captured["table"] = table
        captured["row"] = row
        return "INSERT_SQL;"          # representative SQL the splice appends to

    def _cap_post(sql):
        captured["posted"] = sql
        return ("POSTED", sql)

    def _cap_fire(x):
        captured["fired"] = x

    mios_dispatch.configure(db_create=_cap_create, db_post=_cap_post, db_fire=_cap_fire)

    async def _fake_dispatch(tool, args, *, session_id=None):
        return {"success": True, "tool": "open_url",
                "args": {"url": "https://x.test"},
                "output": "fetched external page", "latency_ms": 7,
                "tainted": True, "taint_reason": "external_open_url:https://x.test"}

    orig_dispatch = mios_dispatch.dispatch_mios_verb
    try:
        mios_dispatch.dispatch_mios_verb = _fake_dispatch

        body = {"tool": "open_url", "args": {"url": "https://x.test"}, "session_id": "s9"}
        asyncio.run(mios_dispatch.dispatch_verb(body))

        assert captured.get("table") == "tool_call", captured
        row = captured.get("row") or {}
        assert row.get("tainted") is True, row
        assert row.get("taint_reason") == "external_open_url:https://x.test", row
        assert row.get("tool") == "open_url", row
        assert captured.get("fired") is not None, "row write must fire"
        assert "session = s9" in str(captured.get("posted") or ""), captured

        import mios_firewall

        async def _fake_read(sql, pg_sql=None, pg_params=None):
            if row.get("tainted"):
                return [{"result": [{"ts": "t", "tool": row["tool"],
                                     "taint_reason": row["taint_reason"]}]}]
            return [{"result": []}]

        mios_firewall._db_read = _fake_read
        tainted, chain = asyncio.run(mios_firewall._session_is_tainted("s9"))
        assert tainted is True and "open_url" in chain, (tainted, chain)

        def _boom_post(sql):
            raise RuntimeError("db down")
        mios_dispatch.configure(db_post=_boom_post)
        out = asyncio.run(mios_dispatch.dispatch_verb(body))
        assert out is not None, "dispatch must still return when the audit write fails"
        print("[PASS] /v1/dispatch persists a tainting tool_call row (A9/F2)")
    finally:
        mios_dispatch.dispatch_mios_verb = orig_dispatch

test_dispatch_persists_taint_row()

def test_sandbox_profile_coverage():
    import os
    import mios_sandbox
    try:
        import tomllib
    except ImportError:  # py<3.11
        import tomli as tomllib
    _here = os.path.dirname(os.path.abspath(__file__))
    _cands = [os.environ.get("MIOS_TOML"),
              "/usr/share/mios/mios.toml",
              os.path.join(_here, "..", "..", "..", "share", "mios", "mios.toml")]
    _path = next((p for p in _cands if p and os.path.exists(p)), None)
    assert _path, f"mios.toml not found (tried {_cands})"
    with open(_path, "rb") as _f:
        verbs = (tomllib.load(_f).get("verbs") or {})
    assert verbs, "no [verbs.*] catalog in mios.toml"

    tagged = {}
    for name, v in verbs.items():
        if not isinstance(v, dict):
            continue
        prof = v.get("sandbox_profile")
        perm = str(v.get("permission", "read")).lower()
        if prof is None:
            continue
        tagged[name] = (prof, perm)
        assert mios_sandbox.resolve_profile(perm, explicit=prof).confined, \
            f"{name}: sandbox_profile={prof!r} did not resolve to a confined profile"
        assert perm != "read", \
            f"{name}: read-only verb must not declare sandbox_profile"

    assert tagged, "A6 regression: ZERO verbs declare sandbox_profile (dead bwrap)"
    _must_confine = [n for n in ("run_code", "coderun", "code_mode",
                                 "text_create", "text_str_replace", "text_insert",
                                 "file_edit", "powershell_run") if n in verbs]
    _missing = [n for n in _must_confine if n not in tagged]
    assert not _missing, f"A6: code/file-mutation verbs missing sandbox_profile: {_missing}"
    print(f"[PASS] A6 sandbox_profile coverage: {len(tagged)} verbs opt in, "
          "all resolve confined + non-read")

test_sandbox_profile_coverage()

def test_agent_access_control():
    _test_catalog = {
        "container_restart": {"cmd": "echo restarting", "tier": "destructive"},
        "safe_verb": {"cmd": "echo safe", "tier": "routine"},
    }
    _test_agents = {
        "routine_agent": {"privilege_group": "routine"},
        "privileged_agent": {"privilege_group": "privileged"},
    }

    orig_hitl_block_reason = mios_dispatch._hitl_block_reason
    orig_pdp = mios_dispatch._dispatch_pdp_reason
    orig_quota = mios_dispatch._dispatch_quota_reason
    orig_enum = mios_dispatch._validate_enum_args
    orig_gate = mios_dispatch._hitl_gate
    orig_arbiter = mios_dispatch._HITL_ARBITER_URL
    orig_bounded = mios_dispatch._dispatch_bounded

    mios_dispatch._hitl_block_reason = lambda tool, args: None
    mios_dispatch._dispatch_pdp_reason = lambda tool: None
    mios_dispatch._dispatch_quota_reason = lambda tool: None
    mios_dispatch._validate_enum_args = lambda t, a: None
    async def _mock_void(*a, **k): return None
    mios_dispatch._hitl_gate = _mock_void
    mios_dispatch._HITL_ARBITER_URL = ""
    async def _mock_bounded(tool, args, *, session_id=None):
        return {"success": True, "output": f"ok-{tool}"}
    mios_dispatch._dispatch_bounded = _mock_bounded

    mios_dispatch.configure(
        verb_catalog=_test_catalog,
        agent_registry=_test_agents,
        db_fire=lambda *a, **k: None,
        db_post=lambda *a, **k: None,
        db_create=lambda *a, **k: {},
    )

    try:
        ctx1 = contextvars.copy_context()
        def _run_routine():
            _agent.set("routine_agent")
            res = asyncio.run(mios_dispatch.dispatch_mios_verb("container_restart", {}))
            assert res.get("hitl_blocked") is True, f"routine_agent calling destructive verb should be HITL blocked: {res}"

            res2 = asyncio.run(mios_dispatch.dispatch_mios_verb("safe_verb", {}))
            assert res2.get("hitl_blocked") is not True, f"routine_agent calling safe verb should NOT be blocked: {res2}"
            assert res2.get("output") == "ok-safe_verb", res2

        ctx1.run(_run_routine)

        ctx2 = contextvars.copy_context()
        def _run_privileged():
            _agent.set("privileged_agent")
            res = asyncio.run(mios_dispatch.dispatch_mios_verb("container_restart", {}))
            assert res.get("hitl_blocked") is not True, f"privileged_agent calling destructive verb should not be HITL blocked: {res}"
            assert res.get("output") == "ok-container_restart", res

        ctx2.run(_run_privileged)
    finally:
        mios_dispatch._hitl_block_reason = orig_hitl_block_reason
        mios_dispatch._dispatch_pdp_reason = orig_pdp
        mios_dispatch._dispatch_quota_reason = orig_quota
        mios_dispatch._validate_enum_args = orig_enum
        mios_dispatch._hitl_gate = orig_gate
        mios_dispatch._HITL_ARBITER_URL = orig_arbiter
        mios_dispatch._dispatch_bounded = orig_bounded
        _base_configure()

    print("[PASS] agent access control: routine blocked on destructive, privileged allowed")

test_agent_access_control()

def test_normalize_container_exec():
    s1 = "docker exec -it my-container bash"
    r1 = mios_dispatch.normalize_container_exec(s1)
    assert "podman" in r1, r1
    assert "docker" not in r1, r1

    s2 = "podman exec -it code-server bash"
    r2 = mios_dispatch.normalize_container_exec(s2)
    assert "mios-agents" in r2, r2
    assert "code-server" not in r2, r2

    s3 = "podman exec -it mios-agents bash"
    r3 = mios_dispatch.normalize_container_exec(s3)
    assert "-it" not in r3, r3
    assert "-t" not in r3, r3
    assert "-i" in r3, r3  # -i should be kept!

    s3_long = "podman exec --tty mios-agents bash"
    r3_long = mios_dispatch.normalize_container_exec(s3_long)
    assert "--tty" not in r3_long, r3_long

    s4 = "podman exec -i mios-agents bash"
    r4 = mios_dispatch.normalize_container_exec(s4)
    assert r4.endswith("true"), r4

    s4_path = "podman exec -i mios-agents /bin/bash"
    r4_path = mios_dispatch.normalize_container_exec(s4_path)
    assert r4_path.endswith("true"), r4_path

    s_multi = "docker exec -it code-server bash\nWrite-Output 'hello'"
    r_multi = mios_dispatch.normalize_container_exec(s_multi)
    assert "podman exec -i mios-agents true" in r_multi, r_multi
    assert "Write-Output 'hello'" in r_multi, r_multi

    print("[PASS] container exec normalization tests")

test_normalize_container_exec()

# ---------------------------------------------------------------------------
# Rule-of-Two and CaMeL quarantine: the two SECURITY gates at the dispatch
# chokepoint. Both had ZERO coverage. Each is exercised through configure()
# with a stubbed taint oracle -- no DB, no network, no live verb.
# ---------------------------------------------------------------------------

_RO2_CATALOG = {
    # tainted + sensitive + state-change = the all-three posture RO2 blocks on.
    "danger": {"cmd": "x", "permission": "write", "sensitive": True},
    # sensitive but read-only: <=2 axes, so RO2 proceeds while quarantine bites.
    "peek":   {"cmd": "x", "permission": "read", "sensitive": True},
    "benign": {"cmd": "x", "permission": "read"},
}

def _wire_gates(*, ro2="off", quarantine="off", tainted=True, approved=None):
    """Configure just enough for the two gates, and stub the taint oracle."""
    hitl_var = contextvars.ContextVar("hitl_approved", default=None)
    if approved is not None:
        hitl_var.set(approved)
    mios_dispatch.configure(
        verb_catalog=_RO2_CATALOG,
        rule_of_two_mode=ro2,
        quarantine_mode=quarantine,
        hitl_approved_var=hitl_var,
    )

    async def _stub_tainted(session_id):
        return tainted, ["stub-chain"]

    mios_dispatch._session_is_tainted = _stub_tainted
    mios_dispatch._hitl_record_pending = lambda *a, **k: None
    return hitl_var

def test_rule_of_two_gate():
    # off -> never consulted, whatever the posture.
    _wire_gates(ro2="off", tainted=True)
    assert asyncio.run(mios_dispatch._rule_of_two_gate(
        "danger", {}, session_id="s1")) is None

    # audit -> observes, never blocks.
    _wire_gates(ro2="audit", tainted=True)
    assert asyncio.run(mios_dispatch._rule_of_two_gate(
        "danger", {}, session_id="s1")) is None

    # enforce + all three axes -> BLOCK.
    _wire_gates(ro2="enforce", tainted=True)
    blocked = asyncio.run(mios_dispatch._rule_of_two_gate(
        "danger", {}, session_id="s1"))
    assert blocked and blocked.get("rule_of_two_blocked") is True, blocked

    # enforce, but untainted -> only two axes -> proceed.
    _wire_gates(ro2="enforce", tainted=False)
    assert asyncio.run(mios_dispatch._rule_of_two_gate(
        "danger", {}, session_id="s1")) is None

    # enforce + tainted, but the verb is neither sensitive nor state-changing.
    _wire_gates(ro2="enforce", tainted=True)
    assert asyncio.run(mios_dispatch._rule_of_two_gate(
        "benign", {}, session_id="s1")) is None

    # No session id at all -> A cannot be established -> proceed.
    _wire_gates(ro2="enforce", tainted=True)
    assert asyncio.run(mios_dispatch._rule_of_two_gate("danger", {})) is None

    print("[PASS] rule-of-two gate: off/audit/enforce, all-three vs fewer")

def test_rule_of_two_same_turn_approval_downgrades():
    """The human who approved THIS exact action may run it -- keyed on the
    action hash, so an approval for a DIFFERENT action must not carry over."""
    args = {"path": "/tmp/x"}
    _wire_gates(ro2="enforce", tainted=True)
    right = mios_dispatch._pending_hash("danger", args)

    _wire_gates(ro2="enforce", tainted=True, approved=right)
    assert asyncio.run(mios_dispatch._rule_of_two_gate(
        "danger", args, session_id="s1")) is None, "matching approval must downgrade"

    _wire_gates(ro2="enforce", tainted=True, approved="not-the-right-hash")
    blocked = asyncio.run(mios_dispatch._rule_of_two_gate(
        "danger", args, session_id="s1"))
    assert blocked, "an approval for a different action must NOT downgrade"
    print("[PASS] rule-of-two: approval is action-hash keyed")

def test_quarantine_gate():
    # off -> never consulted.
    _wire_gates(quarantine="off", tainted=True)
    assert asyncio.run(mios_dispatch._quarantine_gate(
        "danger", {}, session_id="s1")) is None

    # audit -> observes, never blocks.
    _wire_gates(quarantine="audit", tainted=True)
    assert asyncio.run(mios_dispatch._quarantine_gate(
        "danger", {}, session_id="s1")) is None

    # enforce + tainted + privileged -> BITE.
    _wire_gates(quarantine="enforce", tainted=True)
    assert asyncio.run(mios_dispatch._quarantine_gate(
        "danger", {}, session_id="s1")) is not None

    # STRICTER SUPERSET of rule-of-two: a tainted read-only SENSITIVE verb is
    # only two axes, so RO2 proceeds -- quarantine still bites.
    _wire_gates(ro2="enforce", quarantine="enforce", tainted=True)
    assert asyncio.run(mios_dispatch._rule_of_two_gate(
        "peek", {}, session_id="s1")) is None, "RO2 needs all three"
    assert asyncio.run(mios_dispatch._quarantine_gate(
        "peek", {}, session_id="s1")) is not None, "quarantine bites on the superset"

    # untainted -> nothing to quarantine.
    _wire_gates(quarantine="enforce", tainted=False)
    assert asyncio.run(mios_dispatch._quarantine_gate(
        "danger", {}, session_id="s1")) is None

    print("[PASS] quarantine gate: bites on the strict superset of rule-of-two")

def test_gates_degrade_open():
    """ANY internal error must fall back to the EXISTING gates, never crash and
    never newly block everything -- a gate that fails closed here would take the
    whole dispatch plane down."""
    for gate in (mios_dispatch._rule_of_two_gate, mios_dispatch._quarantine_gate):
        _wire_gates(ro2="enforce", quarantine="enforce", tainted=True)

        async def _boom(session_id):
            raise RuntimeError("taint oracle exploded")

        mios_dispatch._session_is_tainted = _boom
        assert asyncio.run(gate("danger", {}, session_id="s1")) is None, \
            f"{gate.__name__} must degrade OPEN on an internal error"
    print("[PASS] both gates degrade open on an internal error")

test_rule_of_two_gate()
test_rule_of_two_same_turn_approval_downgrades()
test_quarantine_gate()
test_gates_degrade_open()

print("test_mios_dispatch: ALL PASS")


# ==============================================================================
# Consolidated from test_mios_dispatch_cmd.py (task_1006)
# ==============================================================================
# AI-hint: Isolation tests for mios_pipe.routing.dispatch_cmd -- the verb->bash command BUILDER extracted from the dispatch chokepoint (task_251).
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md

import sys

from mios_pipe.routing import dispatch_cmd as C

_fails_dispatch_cmd = 0

def _check_dispatch_cmd(name, cond, detail=""):
    global _fails_dispatch_cmd
    if cond:
        print(f"ok   - {name}")
    else:
        _fails_dispatch_cmd += 1
        print(f"FAIL - {name}" + (f" -- {detail}" if detail else ""))

_CATALOG = {
    "web_search":  {"cmd": "mios-web-search -n {limit=5} {query!}", "permission": "read"},
    "open_url":    {"cmd": "mios-open-url {url!}", "permission": "write"},
    # Opts IN to confinement via an explicit profile -- the only shape that may
    # ever be sandbox-wrapped.
    "confined":    {"cmd": "mios-run {x!}", "permission": "write",
                    "sandbox_profile": "strict"},
    # A write verb WITHOUT the explicit opt-in: tier alone must not wrap it.
    "unconfined":  {"cmd": "mios-run {x!}", "permission": "write"},
}

def _wire(*, enforce=False):
    C.configure(verb_catalog=_CATALOG, sandbox_enforce=enforce,
                sandbox_self_confined=("already-confined",))

def t_standalone_configuration():
    """The module is driven by configure() alone -- no mios_dispatch, no server."""
    _wire()
    _check_dispatch_cmd("configure: catalog is injected", "web_search" in C._VERB_CATALOG)
    _check_dispatch_cmd("configure: enforce flag is injected", C.SANDBOX_ENFORCE is False)
    _wire(enforce=True)
    _check_dispatch_cmd("configure: enforce flag updates", C.SANDBOX_ENFORCE is True)

def t_build_dispatch_cmd():
    _wire()
    cmd = C._build_dispatch_cmd("web_search", {"query": "hello world"})
    _check_dispatch_cmd("build: renders the SSOT template", cmd and "mios-web-search" in cmd, str(cmd))
    _check_dispatch_cmd("build: substitutes the required arg", cmd and "hello world" in cmd, str(cmd))
    _check_dispatch_cmd("build: applies the {arg=default} form", cmd and "-n 5" in cmd, str(cmd))

    _check_dispatch_cmd("build: an unknown verb yields None",
          C._build_dispatch_cmd("no_such_verb", {}) is None)

def t_sandbox_profile_resolution():
    _wire()
    prof = C._dispatch_sandbox_profile("confined")
    _check_dispatch_cmd("profile: an explicit override resolves", prof is not None)
    _check_dispatch_cmd("profile: a read verb resolves too",
          C._dispatch_sandbox_profile("web_search") is not None)
    _check_dispatch_cmd("profile: an unknown verb still resolves (fail-closed in mios_sandbox)",
          C._dispatch_sandbox_profile("no_such_verb") is not None)

def t_sandbox_wrap_is_opt_in():
    """The OPT-IN gate is the safety property: an explicit [verbs.*].sandbox_profile,
    NOT the permission tier, is what admits a verb to bwrap. Wrapping a launch or
    OS-control verb on tier alone would break it."""
    _wire(enforce=False)
    prof = C._dispatch_sandbox_profile("confined")
    cmd, ws = C._sandbox_wrap_cmd("confined", "echo hi", prof)
    _check_dispatch_cmd("wrap: enforce OFF -> never wrapped", cmd == "echo hi" and ws is None,
          f"{cmd!r} {ws!r}")

    _wire(enforce=True)
    cmd, ws = C._sandbox_wrap_cmd("unconfined", "echo hi",
                                  C._dispatch_sandbox_profile("unconfined"))
    _check_dispatch_cmd("wrap: a write verb WITHOUT the explicit opt-in is never wrapped",
          cmd == "echo hi" and ws is None, f"{cmd!r} {ws!r}")

    cmd, ws = C._sandbox_wrap_cmd("confined", "already-confined echo hi",
                                  C._dispatch_sandbox_profile("confined"))
    _check_dispatch_cmd("wrap: a self-confining cmd is left alone",
          cmd == "already-confined echo hi" and ws is None, f"{cmd!r}")

def t_normalize_container_exec():
    _check_dispatch_cmd("normalize: docker -> podman",
          C.normalize_container_exec("docker exec -i c bash").startswith("podman"))
    _check_dispatch_cmd("normalize: code-server -> mios-agents",
          "mios-agents" in C.normalize_container_exec("podman exec -i code-server bash"))
    _check_dispatch_cmd("normalize: a tty flag is dropped",
          "--tty" not in C.normalize_container_exec("podman exec --tty c bash"))
    _check_dispatch_cmd("normalize: -i is preserved",
          "-i" in C.normalize_container_exec("podman exec -it c true"))
    _check_dispatch_cmd("normalize: a bare interactive shell becomes `true`",
          C.normalize_container_exec("podman exec -i c /bin/bash").endswith("true"))

def _main_dispatch_cmd():
    t_standalone_configuration()
    t_build_dispatch_cmd()
    t_sandbox_profile_resolution()
    t_sandbox_wrap_is_opt_in()
    t_normalize_container_exec()
    print(f"\n{_fails_dispatch_cmd} FAILED" if _fails_dispatch_cmd else "\nok")
    return 1 if _fails_dispatch_cmd else 0


def _run_extra_dispatch_cmd():
    import os
    _saved_env = dict(os.environ)
    try:
        return _main_dispatch_cmd()
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



# ==============================================================================
# Consolidated from test_mios_dispatch_redos.py (task_1006)
# ==============================================================================
# AI-hint: Regression test for the ReDoS in dispatch_cmd's podman-exec shell-stripper -- pins a wall-clock bound on a pathological input, not a pattern string.
# AI-related: usr/lib/mios/agent-pipe/mios_pipe/routing/dispatch_cmd.py
"""Regression: the podman-exec stripper must not backtrack exponentially.

The flag-repetition group allowed a flag's ARGUMENT to start with '-', so
"-a -b" had two legal parses and the group backtracked exponentially (~1.64^n
measured) on model-controlled script text. The bound pinned here is wall-clock
on a pathological input rather than an assertion about the pattern string,
because the defect is behavioural; flags-with-arguments are pinned too, since
that is what the narrowed character class could plausibly break.
"""

import os
import re
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "mios_pipe/routing/dispatch_cmd.py")

def _shell_strip_pattern():
    """The live pattern, read from the shipped source.

    Extracted rather than imported: importing dispatch_cmd pulls the whole
    agent-pipe dependency tree, and this defect is in one literal.
    """
    with open(SRC, encoding="utf-8") as fh:
        body = fh.read()
    m = re.search(r"r'(\\b\(podman\\s\+exec.*?)',\n", body, re.S)
    if not m:
        raise AssertionError("the podman-exec stripper pattern was not found -- "
                             "renamed or restructured? Update this test.")
    return m.group(1)

class TestNoExponentialBacktracking(unittest.TestCase):
    def setUp(self):
        self.rx = re.compile(_shell_strip_pattern(),
                             re.IGNORECASE | re.MULTILINE)

    def test_a_pathological_input_stays_fast(self):
        # Pre-fix this took ~1.9s at n=32 and grew ~2.7x per +2. n=2000 would
        # not have finished in the lifetime of the process.
        s = "podman exec " + "-- " * 2000 + "!"
        t0 = time.time()
        self.rx.search(s)
        elapsed = time.time() - t0
        self.assertLess(elapsed, 1.0,
                        "shell-stripper took %.3fs on a 2000-repetition input -- "
                        "the backtracking regression is back" % elapsed)

    def test_growth_is_not_exponential(self):
        # Doubling the input must not square the time. A generous factor keeps
        # this stable on a loaded runner while still catching 1.64^n.
        def t(n):
            s = "podman exec " + "-- " * n + "!"
            t0 = time.time()
            self.rx.search(s)
            return time.time() - t0

        small, big = t(200), t(400)
        self.assertLess(big, max(small * 8, 0.5),
                        "doubling the input multiplied the time by %.1f" %
                        (big / small if small else 0))

class TestBehaviourUnchanged(unittest.TestCase):
    def setUp(self):
        self.rx = re.compile(_shell_strip_pattern(),
                             re.IGNORECASE | re.MULTILINE)

    def _strip(self, s):
        return self.rx.sub(r'\1 true', s)

    def test_a_bare_shell_is_neutralised(self):
        self.assertEqual(self._strip("podman exec -it mios-pgvector bash"),
                         "podman exec -it mios-pgvector true")
        self.assertEqual(self._strip("podman exec mios-forge sh"),
                         "podman exec mios-forge true")

    def test_flags_WITH_arguments_still_strip(self):
        # The fix narrowed the argument class to [^-\s]\S*, so these are the
        # cases most likely to break if it were narrowed wrongly.
        for src, want in (
            ("podman exec -i --user 1000 mios-ai /bin/bash",
             "podman exec -i --user 1000 mios-ai true"),
            ("podman exec -e FOO=bar mios-x zsh -l",
             "podman exec -e FOO=bar mios-x true"),
            ("podman exec --workdir /srv mios-y /bin/sh",
             "podman exec --workdir /srv mios-y true"),
        ):
            self.assertEqual(self._strip(src), want, src)

    def test_a_real_command_is_left_alone(self):
        for s in ("echo hello",
                  "podman exec -it mios-z bash -c 'ls'"):
            self.assertEqual(self._strip(s), s, s)


def _run_extra_dispatch_redos():
    import os
    _saved_env = dict(os.environ)
    try:
        import unittest
        suite = unittest.TestSuite()
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestNoExponentialBacktracking))
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestBehaviourUnchanged))
        res = unittest.TextTestRunner().run(suite)
        return 0 if res.wasSuccessful() else 1
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



def test_host_origin_middleware():
    from mios_pipe.auth import host_origin_validation_mw, configure as configure_auth
    from starlette.requests import Request
    from starlette.responses import Response
    import asyncio

    configure_auth(allowlist_hosts=["localhost", "127.0.0.1", "host.containers.internal"])

    async def call_next(req):
        return Response("ok", status_code=200)

    # 1. Allowed host & origin pass
    scope_good = {
        "type": "http",
        "method": "POST",
        "path": "/v1/dispatch",
        "headers": [
            (b"host", b"localhost:8700"),
            (b"origin", b"http://localhost:3000")
        ]
    }
    resp_good = asyncio.run(host_origin_validation_mw(Request(scope_good), call_next))
    assert resp_good.status_code == 200, f"expected 200, got {resp_good.status_code}"

    # 2. Negative: Disallowed Host -> 403
    scope_evil_host = {
        "type": "http",
        "method": "POST",
        "path": "/v1/dispatch",
        "headers": [
            (b"host", b"evil.example"),
            (b"origin", b"http://localhost:3000")
        ]
    }
    resp_evil_host = asyncio.run(host_origin_validation_mw(Request(scope_evil_host), call_next))
    assert resp_evil_host.status_code == 403, f"expected 403, got {resp_evil_host.status_code}"
    assert b"Host" in resp_evil_host.body, "expected Host in error body"

    # 3. Negative: Disallowed Origin -> 403
    scope_evil_origin = {
        "type": "http",
        "method": "POST",
        "path": "/v1/dispatch",
        "headers": [
            (b"host", b"localhost:8700"),
            (b"origin", b"http://evil.example")
        ]
    }
    resp_evil_origin = asyncio.run(host_origin_validation_mw(Request(scope_evil_origin), call_next))
    assert resp_evil_origin.status_code == 403, f"expected 403, got {resp_evil_origin.status_code}"
    assert b"Origin" in resp_evil_origin.body, "expected Origin in error body"

    print("[PASS] Host and Origin header validation rejects evil.example with 403 (P1-15 / task_3562)")

def test_socket_unit_loopback_only():
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    sock_path = os.path.normpath(os.path.join(here, "..", "..", "..", "lib", "systemd", "system", "mios-agent-pipe.socket"))
    with open(sock_path, "r", encoding="utf-8") as fh:
        content = fh.read()
    assert "ListenStream=127.0.0.1:8700" in content, "ListenStream must be 127.0.0.1:8700 (loopback only)"
    assert "\nListenStream=8700\n" not in content, "bare 8700 (0.0.0.0) prohibited"
    print("[PASS] mios-agent-pipe.socket binds TCP to loopback only (task_3562)")

def test_prompt_injection_ssot_defaults():
    import os
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    here = os.path.dirname(os.path.abspath(__file__))
    toml_path = os.path.normpath(os.path.join(here, "..", "..", "..", "share", "mios", "mios.toml"))
    with open(toml_path, "rb") as fh:
        data = tomllib.load(fh)
    sec = data.get("security", {})
    assert sec.get("rule_of_two_mode") == "audit", f"expected audit, got {sec.get('rule_of_two_mode')}"
    assert sec.get("quarantine_mode") == "audit", f"expected audit, got {sec.get('quarantine_mode')}"
    assert sec.get("principal_bind_mode") == "audit", f"expected audit, got {sec.get('principal_bind_mode')}"
    assert sec.get("provenance_taint") is True, f"expected true, got {sec.get('provenance_taint')}"
    print("[PASS] prompt-injection defenses ship in audit mode with provenance_taint = true (task_3563 / Q17)")

def _run_all_folded_dispatch_suites():
    test_host_origin_middleware()
    test_socket_unit_loopback_only()
    test_prompt_injection_ssot_defaults()
    rc = _run_extra_dispatch_cmd()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")
    rc = _run_extra_dispatch_redos()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

_run_all_folded_dispatch_suites()

