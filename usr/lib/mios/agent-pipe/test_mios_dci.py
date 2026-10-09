#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_dci (refactor R6 DCI extraction). Pure stdlib, no server.py/DB/httpx-network/pytest.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_dci (refactor R6)."""

import asyncio
import sys

import mios_dci as e

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def t_acts():
    check("acts: 14 epistemic acts", len(e._DCI_ACTS) == 14, str(len(e._DCI_ACTS)))
    check("acts: each has family + intent",
          all(isinstance(v, dict) and "family" in v and "intent" in v
              for v in e._DCI_ACTS.values()))
    check("acts: 6 distinct families",
          len({v["family"] for v in e._DCI_ACTS.values()}) == 6)
    check("act_names: sorted view of the act keys",
          e._DCI_ACT_NAMES == sorted(e._DCI_ACTS.keys()))
    check("act_names: known anchors present",
          {"frame", "challenge", "recommend"}.issubset(set(e._DCI_ACT_NAMES)))

def t_schema():
    s = e._DCI_ACT_SCHEMA
    check("schema: object type", s.get("type") == "object")
    props = s.get("properties") or {}
    check("schema: has act/content/confidence/targets props",
          {"act", "content", "confidence", "targets"}.issubset(props))
    check("schema: act enum IS _DCI_ACT_NAMES",
          props.get("act", {}).get("enum") == e._DCI_ACT_NAMES)
    check("schema: confidence is bounded 0..1",
          props.get("confidence", {}).get("minimum") == 0.0
          and props.get("confidence", {}).get("maximum") == 1.0)
    check("schema: required = act/content/confidence",
          s.get("required") == ["act", "content", "confidence"])

def t_personas():
    check("personas: 4 (framer/explorer/challenger/integrator)",
          [n for n, _ in e._DCI_PERSONAS] == ["framer", "explorer", "challenger", "integrator"])
    check("personas: every system prompt non-empty",
          all(isinstance(p, str) and p.strip() for _, p in e._DCI_PERSONAS))
    check("critic system prompt non-empty",
          isinstance(e._DCI_CRITIC_SYSTEM, str) and e._DCI_CRITIC_SYSTEM.strip())
    keys = set(e._PERSONA_ALLOWED_ACTS)
    check("allowed-acts: keyed by the 4 personas",
          keys == {"framer", "explorer", "challenger", "integrator"})
    check("allowed-acts: every listed act is a real act",
          all(a in e._DCI_ACTS
              for s in e._PERSONA_ALLOWED_ACTS.values() for a in s))

def t_persona_prompt():
    out = e._persona_prompt("Tester", "do the thing", {"ask", "challenge"})
    check("persona_prompt: returns non-empty str", isinstance(out, str) and bool(out.strip()))
    check("persona_prompt: names the role", "Tester" in out)
    check("persona_prompt: lists each allowed act + its intent",
          "ask" in out and "challenge" in out and e._DCI_ACTS["ask"]["intent"] in out)
    check("persona_prompt: JSON-only contract", "JSON ONLY" in out)

def t_configure():
    def fake_create(table, fields, now_fields=None):
        return f"SQL:{table}"

    async def fake_post(sql, *, timeout=3.0):
        return None

    def fake_fire(coro):
        try:
            coro.close()
        except Exception:
            pass

    def fake_auth(hdrs, ep):
        hdrs["X-Test"] = "1"

    e.configure(db_post=fake_post, db_create=fake_create,
                db_fire=fake_fire, apply_outbound_auth=fake_auth)
    check("configure: db_post injected", e._db_post is fake_post)
    check("configure: db_create injected", e._db_create is fake_create)
    check("configure: db_fire injected", e._db_fire is fake_fire)
    check("configure: apply_outbound_auth injected", e._apply_outbound_auth is fake_auth)

def t_flow():
    """Drive run_dci_flow with a stubbed persona call (no network) + injected
    no-op DB helpers (set up in t_configure). Proves the bounded loop routes
    acts into the workspace, captures the Integrator's `recommend` as the
    decision, early-exits, and extracts the high-confidence challenge as
    unresolved dissent."""
    canned = {
        "framer":     {"act": "frame",     "family": "orienting",  "confidence": 0.5},
        "explorer":   {"act": "propose",   "family": "generative", "confidence": 0.5},
        "challenger": {"act": "challenge", "family": "critical",   "confidence": 0.9},
        "integrator": {"act": "recommend", "family": "decisional", "confidence": 0.8},
    }

    async def stub_call(persona_name, system_prompt, user_text, workspace):
        base = dict(canned[persona_name])
        base["persona"] = persona_name
        base["content"] = f"{persona_name} says hi"
        return base

    orig = e._dci_call_persona
    e._dci_call_persona = stub_call
    try:
        result = asyncio.run(e.run_dci_flow("decide X", {}, session_id=None))
    finally:
        e._dci_call_persona = orig

    check("flow: converged", result.get("converged") is True)
    check("flow: decision is the recommend act",
          (result.get("decision") or {}).get("act") == "recommend")
    check("flow: early-exit after round 1", result.get("rounds_used") == 1)
    check("flow: one unresolved high-conf dissent", len(result.get("dissents") or []) == 1)
    check("flow: challenge routed into workspace",
          (result.get("workspace") or {}).get("challenges") == 1)

def t_dissent_threshold_ssot():
    """The dissent-extraction cutoff must read from the SSOT knob
    (DCI_FLOW_TRIGGER_CONF), not a baked literal. Drive the same flow
    with the knob raised ABOVE the challenger's 0.9 confidence and
    assert the challenge is NO LONGER extracted as dissent -- proving
    the cutoff is config-driven, then restore the knob."""
    canned = {
        "framer":     {"act": "frame",     "family": "orienting",  "confidence": 0.5},
        "explorer":   {"act": "propose",   "family": "generative", "confidence": 0.5},
        "challenger": {"act": "challenge", "family": "critical",   "confidence": 0.9},
        "integrator": {"act": "recommend", "family": "decisional", "confidence": 0.8},
    }

    async def stub_call(persona_name, system_prompt, user_text, workspace):
        base = dict(canned[persona_name])
        base["persona"] = persona_name
        base["content"] = f"{persona_name} says hi"
        return base

    orig_call = e._dci_call_persona
    orig_conf = e.DCI_FLOW_TRIGGER_CONF
    e._dci_call_persona = stub_call
    try:
        e.DCI_FLOW_TRIGGER_CONF = 0.95
        high = asyncio.run(e.run_dci_flow("decide X", {}, session_id=None))
        check("dissent-threshold: knob 0.95 suppresses the 0.9 challenge",
              len(high.get("dissents") or []) == 0)
        e.DCI_FLOW_TRIGGER_CONF = 0.5
        low = asyncio.run(e.run_dci_flow("decide X", {}, session_id=None))
        check("dissent-threshold: knob 0.5 admits the 0.9 challenge",
              len(low.get("dissents") or []) == 1)
    finally:
        e._dci_call_persona = orig_call
        e.DCI_FLOW_TRIGGER_CONF = orig_conf

def t_dissent_acts_ssot():
    """task_29 NO-HARDCODE: the dissent/objection act set is DERIVED from the
    persona-allowed SSOT (the Challenger's acts), not a restated ("challenge",
    "ask") literal -- so it tracks the vocabulary if the persona changes."""
    check("dissent-acts: derived from the challenger persona SSOT",
          set(e._DCI_DISSENT_ACTS) == set(e._PERSONA_ALLOWED_ACTS["challenger"]))
    check("dissent-acts: every member is a real act",
          all(a in e._DCI_ACTS for a in e._DCI_DISSENT_ACTS))
    check("dissent-acts: non-empty", len(e._DCI_DISSENT_ACTS) >= 1)

def t_act_type_emitted():
    """task_28: every DCI act event carries a top-level `act_type` column (not only
    the JSONB payload), so dissent + per-act analytics are first-class queryable.
    Capture the event rows _db_create builds while run_dci_flow executes (stubbed
    persona calls, no network) and assert act_type is set + matches the act on the
    dci_act rows AND on the unresolved-Challenger dissent row."""
    canned = {
        "framer":     {"act": "frame",     "family": "orienting",  "confidence": 0.5},
        "explorer":   {"act": "propose",   "family": "generative", "confidence": 0.5},
        "challenger": {"act": "challenge", "family": "critical",   "confidence": 0.9},
        "integrator": {"act": "recommend", "family": "decisional", "confidence": 0.8},
    }

    async def stub_call(persona_name, system_prompt, user_text, workspace):
        base = dict(canned[persona_name])
        base["persona"] = persona_name
        base["content"] = f"{persona_name} says hi"
        return base

    captured = []

    def cap_create(table, fields, now_fields=None):
        captured.append((table, dict(fields)))
        return f"SQL:{table}"

    async def cap_post(sql, *, timeout=3.0):
        return None

    def cap_fire(coro):
        try:
            coro.close()
        except Exception:
            pass

    orig_call = e._dci_call_persona
    orig_create, orig_post, orig_fire = e._db_create, e._db_post, e._db_fire
    e._dci_call_persona = stub_call
    e._db_create, e._db_post, e._db_fire = cap_create, cap_post, cap_fire
    try:
        asyncio.run(e.run_dci_flow("decide X", {}, session_id="sess-test"))
    finally:
        e._dci_call_persona = orig_call
        e._db_create, e._db_post, e._db_fire = orig_create, orig_post, orig_fire

    events = [f for (t, f) in captured if t == "event"]
    dci_acts = [f for f in events if f.get("kind") == "dci_act"]
    dissents = [f for f in events if f.get("kind") == "dissent"]
    check("act_type: dci_act events were emitted", len(dci_acts) >= 1, str(len(dci_acts)))
    check("act_type: every dci_act row has a top-level act_type",
          bool(dci_acts) and all(f.get("act_type") for f in dci_acts))
    check("act_type: top-level act_type mirrors the payload act",
          all(f.get("act_type") == (f.get("payload") or {}).get("act") for f in dci_acts))
    check("act_type: the Challenger act surfaced as act_type=challenge",
          any(f.get("act_type") == "challenge" for f in dci_acts))
    check("act_type: dissent event recorded (Challenger dissent)",
          len(dissents) == 1, str(len(dissents)))
    check("act_type: dissent row carries act_type (first-class dissent)",
          bool(dissents) and all(f.get("act_type") == "challenge" for f in dissents))

def t_flow_gate():
    """task_29: the heavy B.2 convergent flow is GATED on DCI_FLOW_ENABLED. With a
    high-confidence Challenger objection from the (stubbed) B.1 critic:
      - default / disabled -> run_dci_flow is NOT invoked (no-op B.2);
      - flow enabled       -> run_dci_flow IS invoked.
    Stubs the critic + the flow so nothing hits the network or the DB."""
    async def stub_critic(user_text, envelope, *, session_id=None):
        return {"act": "challenge", "confidence": 0.9, "content": "weak evidence"}

    flow_calls = []

    async def stub_flow(user_text, envelope, *, session_id=None, r_max=None):
        flow_calls.append(r_max)
        return {"dissents": [], "rounds_used": 0}

    orig_critic = e.dci_critic_pass
    orig_flow = e.run_dci_flow
    orig_enabled = e.DCI_ENABLED
    orig_flow_enabled = e.DCI_FLOW_ENABLED
    e.dci_critic_pass = stub_critic
    e.run_dci_flow = stub_flow
    e.DCI_ENABLED = True
    try:
        e.DCI_FLOW_ENABLED = False
        asyncio.run(e.critic_then_maybe_flow("t", {}, session_id="s1"))
        check("flow-gate: disabled -> run_dci_flow NOT called (no-op B.2)",
              flow_calls == [], str(flow_calls))
        e.DCI_FLOW_ENABLED = True
        asyncio.run(e.critic_then_maybe_flow("t", {}, session_id="s1"))
        check("flow-gate: enabled -> run_dci_flow invoked", len(flow_calls) == 1, str(flow_calls))
    finally:
        e.dci_critic_pass = orig_critic
        e.run_dci_flow = orig_flow
        e.DCI_ENABLED = orig_enabled
        e.DCI_FLOW_ENABLED = orig_flow_enabled

def main():
    t_acts()
    t_schema()
    t_personas()
    t_persona_prompt()
    t_configure()
    t_flow()
    t_dissent_threshold_ssot()
    t_dissent_acts_ssot()
    t_act_type_emitted()
    t_flow_gate()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0

if __name__ == "__main__":
    sys.exit(main())
