<!-- AI-hint: Manual pages distilled from the source comments of access, sanitized, each passage anchored to the comment it came from. -->

# access

### mios_arbiter -- the MiOS out-of-process policy-arbiter...

mios_arbiter -- the MiOS out-of-process policy-arbiter decision core (WS-9).

Pure stdlib. The agent-pipe already has a HITL arbiter CLIENT
(_hitl_arbiter_verdict) that POSTs a high-risk action to an external arbiter for
an allow/deny verdict -- but no arbiter SERVICE existed. This is the decision
logic that service runs: a deterministic, auditable second opinion that the
operator can own/change independently of the agent-pipe.

Policy (first match wins):
  1. verb in deny  -> DENY (always; the hard floor)
  2. allow set AND verb in allow -> ALLOW
  3. allow set AND verb NOT in allow -> DENY (allow-list is exclusive)
  4. tier rank >= block_tier rank -> DENY (risk ceiling)
  5. otherwise -> ALLOW
Fail-closed inputs (an unknown tier ranks above the top) keep an unclassified
high-risk verb gated rather than waved through.

<!-- mios-src:145f61874a4f from usr/lib/mios/agent-pipe/mios_pipe/access/arbiter.py:4-20 -->

### Provenance-taint + Semantic Firewall (lethal-trifecta...

Provenance-taint + Semantic Firewall (lethal-trifecta defense).

Extracted verbatim from ``server.py``. A session that has ingested external /
untrusted content is BLOCKED (by the caller, using ``_session_is_tainted``) from
high-privilege + exfiltration verbs. The three moved functions are unchanged;
``server.py`` re-imports each under its original alias so the public surface is
byte-identical.

SECURITY-CRITICAL: the gates are NAME-KEYED on verb keys. Nothing is renamed and
no set is inlined -- the SSOT-derived always-taint verb set (``_TAINT_VERBS``,
built from ``mios_secset.taint_verb_set`` in server.py), the ``PROVENANCE_TAINT_ENABLE``
opt-in flag, the operator-infrastructure ``_ALLOWLIST_HOSTS`` host set, the
``_MCP_CLIENT_TOOLS`` registry and the ``_db_read`` pg taint-chain
reader are all dependency-injected via :func:`configure` (one-way module
boundary -- this module never imports ``server``).

<!-- mios-src:93bab5862ce1 from usr/lib/mios/agent-pipe/mios_pipe/access/firewall.py:4-19 -->

### Inject server.py's SSOT-derived sets, the provenance flag...

Inject server.py's SSOT-derived sets, the provenance flag and the DB
    reader under their EXACT original server-side global names.

    Injected via ``is not None`` guards so a falsey-but-real value (False, an
    empty set) still overrides the placeholder; the sets/dict are shared by
    reference so server-side mutation stays visible.

<!-- mios-src:f5e7d99606f6 from usr/lib/mios/agent-pipe/mios_pipe/access/firewall.py:46-51 -->

### Return True if the URL points OUTSIDE the operator's own...

Return True if the URL points OUTSIDE the operator's own
    infrastructure (i.e. a taint source). Best-effort host parse;
    anything ambiguous defaults to External (fail-safe).

<!-- mios-src:236778cb0e43 from usr/lib/mios/agent-pipe/mios_pipe/access/firewall.py:67-69 -->

### mios_hitl -- pure decision helpers for the WS-6 runtime...

mios_hitl -- pure decision helpers for the WS-6 runtime HITL approval gate.

DB-free + stdlib-only so the scope-resolution and gate-decision logic unit-tests
in isolation (sibling-module pattern, like mios_sched / mios_evict). server.py
owns the pgvector pending_action I/O, the event emission, and the approval
endpoints; this module owns only the deterministic, testable decisions.

Modes:
  "log"  (default) -- NON-BLOCKING: record + emit an observability event, then
                      proceed. The autonomous swarm is never deadlocked.
  "gate"           -- BLOCKING: a scoped verb is refused (block_result) and a
                      pending_action row is written until approved out-of-band;
                      the agent's later retry of the same action then passes.

<!-- mios-src:af720917be27 from usr/lib/mios/agent-pipe/mios_pipe/access/hitl.py:3-16 -->

### THE single HITL verdict, reconciling the [ai] risk-tier...

THE single HITL verdict, reconciling the [ai] risk-tier gate, the [hitl]
    verb-scope gate, the Rule-of-Two architectural gate AND the CaMeL quarantine gate.
    Each gate is evaluated ONLY within its own scope; the result is the STRICTER of
    their postures (proceed < observe < block) so that if ANY gate would block this
    verb, it blocks (fail-safe -- the gates can never disagree on the blocking
    outcome). The Rule-of-Two gate contributes a BLOCK posture (`ro2_block=True`) when a
    dispatch holds all three dangerous properties under enforce mode -- the
    deterministic kill-chain refusal (mios_ruleof2). The CaMeL quarantine gate
    contributes a BLOCK posture (`quarantine_block=True`) when a TAINTED session would
    autonomously drive a PRIVILEGED (sensitive-read OR state-change) action under
    enforce mode -- the stricter dual-context refusal (mios_quarantine). `approved`
    downgrades a BLOCK to OBSERVE so an explicitly-approved action runs. Returns
    PROCEED / OBSERVE / BLOCK. Pure + total: it never raises (call-sites stay
    degrade-open on their own I/O, but the DECISION itself errs toward blocking, never
    toward a silent execution). `ro2_block` / `quarantine_block` both default False ->
    inert for the existing call-sites (byte-identical verdict).

<!-- mios-src:52ccc6eeb8ae from usr/lib/mios/agent-pipe/mios_pipe/access/hitl.py:89-104 -->

### mios_hitlflow -- HITL ask-to-run + runtime approval-gate...

mios_hitlflow -- HITL ask-to-run + runtime approval-gate flow.

Extracted verbatim from ``server.py`` (R7 security wave). Holds the WS-6 runtime
HITL gate, the structural action-identity hashers, the chat-native ask-to-run
approval round-trip (propose -> model-classified approval -> per-action-hash
bypass -> re-dispatch) and the Reflexion episodic read-side. ``server.py``
re-imports every name under its original alias so the public surface is
byte-identical.

SECURITY-CRITICAL: the gates are NAME-KEYED on verb keys + permission tiers.
Nothing is renamed; the moved bodies are unchanged. ``mios_hitl`` (pure decision
helpers), ``mios_jsonsalvage``, ``mios_pg`` and ``mios_sse`` are imported
directly from their sibling modules; every other server-side symbol the flow
touches (the HITL/ASK config scalars, the router/planner endpoints, the
``_db_*`` / ``_pg_mirror`` DB helpers, ``_emit_session_event``,
``_row_age_seconds``, ``_usage_estimate``, the ``_hitl_approved_var``
ContextVar and ``dispatch_mios_verb``) is injected via :func:`configure`
(one-way module boundary -- this module never imports ``server``).

<!-- mios-src:68ed64864f19 from usr/lib/mios/agent-pipe/mios_pipe/access/hitlflow.py:4-22 -->

### The runtime HITL gate ([hitl] verb-scope half), called from...

The runtime HITL gate ([hitl] verb-scope half), called from
    _dispatch_mios_verb_inner for scoped verbs. Returns a block_result dict to REFUSE
    the dispatch (gate mode, not yet approved) or None to PROCEED. The block/proceed
    verdict is computed by the SINGLE shared resolver (``mios_hitl.decide``) that the
    [ai] risk-tier gate also routes through, so the two HITL gates can no longer
    disagree. Always emits an observability event. Never raises -> degrade-open to
    PROCEED (an agent is never wedged by the gate failing).

<!-- mios-src:472346b93151 from usr/lib/mios/agent-pipe/mios_pipe/access/hitlflow.py:201-207 -->

### Generative judge (NO phrase list -- operator "NOTHING...

Generative judge (NO phrase list -- operator "NOTHING HARDCODED"): given the
    PROPOSED action + the user's reply, classify BY MEANING as 'approve' (run it now),
    'reject' (skip it), or 'unrelated' (a new request, not an answer to the proposal).
    Only called when a proposal is actually pending. Degrade -> 'unrelated' on any
    error (SAFE: the action stays un-run; the user can re-confirm). Never auto-runs on
    ambiguity.

<!-- mios-src:a5e3a762f92c from usr/lib/mios/agent-pipe/mios_pipe/access/hitlflow.py:234-239 -->

### Reflexion episodic buffer (ref AIOS B.3 / Shinn et al....

Reflexion episodic buffer (ref AIOS B.3 / Shinn et al. 2023): pull
    recent `reflect_corrected` events for THIS session so a fresh
    reflection can REUSE a prior fix instead of re-deriving it. The audit
    flagged these rows as write-only -- this is the missing read side.
    Best-effort: returns [] on any DB miss so reflection never blocks.

<!-- mios-src:5011b5d04f9e from usr/lib/mios/agent-pipe/mios_pipe/access/hitlflow.py:383-387 -->

### mios_memguard -- write-time memory-poisoning validation...

mios_memguard -- write-time memory-poisoning validation (WS-MEM-VALIDATE, OWASP ASI08).

A durable-memory store (the knowledge Q/A append) is an injection vector: text
persisted today is RECALLED later and folded into a future turn's context, where
an embedded imperative ("ignore previous instructions...") or a code/exfil
payload can steer the model. MiOS already verdict-gates storage (an UNSATISFIED
turn is not stored), but a SATISFIED answer can still carry poisoned content.

This module is the detector + policy:
  * scan_fact()        -- PURE structural scan -> {flags, severity, has_*}: only
                          language-neutral SHAPES (inert URL / code fence -> low;
                          a control-token delimiter -> a HIGH escalation signal).
  * _judge_severity()  -- MODEL-DRIVEN injection judge: the micro-model classifies
                          whether the write is a prompt-injection / poisoning
                          attempt + its severity. No keyword/English phrase list --
                          intent is judged, so paraphrase / non-English is caught.
  * validate_for_store(mode) -- off | log | strip | reject.

The severity verdict is the MODEL's; the structural scan is a fast-path that can
only ESCALATE (an obvious control-token), never the sole gate. The judge path is
flag-gated ([pgvector].memguard_judge_mode). When the micro lane is unavailable
the verdict DEGRADES to the structural scan (fail-safe -- an obvious control-token
still escalates while benign content still stores; never the deleted keyword gate).

FAIL-OPEN: a scanner/judge error never blocks a store (the memory guard must not
become a new way to drop the user's own answer). server.py owns the wiring + the
SSOT policy mode; this is the deterministic, unit-testable policy.

<!-- mios-src:70884b1cc1a3 from usr/lib/mios/agent-pipe/mios_pipe/access/memguard.py:4-31 -->

### PURE structural scan of a candidate durable-memory fact....

PURE structural scan of a candidate durable-memory fact. Returns
    {flags: [str], severity: none|low|high, has_control_token, has_url,
    has_code_fence}. Deterministic + language-neutral: it flags only SHAPES, never
    English/keyword content. A control-token delimiter -> HIGH (an unambiguous
    injection shape that ESCALATES the model verdict); an inert URL / code fence ->
    LOW; else NONE. The injection/poisoning SEVERITY proper is the MODEL judge's
    (_judge_severity); this scan is the escalation fast-path + the degrade-open
    fallback when the judge is unavailable.

<!-- mios-src:cab8bae7503a from usr/lib/mios/agent-pipe/mios_pipe/access/memguard.py:66-73 -->

### MODEL-DRIVEN prompt-injection / memory-poisoning judge...

MODEL-DRIVEN prompt-injection / memory-poisoning judge (OWASP ASI08): the
    always-warm micro-model decides whether THIS candidate durable-memory write is
    an injection / poisoning attempt and at what SEVERITY. Replaces the deleted
    English-regex phrase gate -- a paraphrased or non-English injection is caught
    because the MODEL classifies INTENT, not a keyword list. Returns "high" (an
    injection/identity-override/poisoning attempt or a dangerous code/exfil payload),
    "low" (benign content, possibly with an inert URL / code sample), "none" (plain
    benign fact), or ``None`` to signal the judge is UNAVAILABLE (lane down / non-200
    / unparseable) -> the caller DEGRADES to the structural verdict (fail-safe, never
    the deleted keyword gate). Degrade-open on any error: never block a store.

<!-- mios-src:5e52985f75ad from usr/lib/mios/agent-pipe/mios_pipe/access/memguard.py:107-116 -->

### Apply the WS-MEM-VALIDATE policy to a candidate fact....

Apply the WS-MEM-VALIDATE policy to a candidate fact. Returns
    {ok, store_text, flags, severity}:
      off    -> always ok, text unchanged (no-op; zero behaviour change).
      log    -> always ok, text unchanged, flags/severity reported (the caller
                emits an audit event when flagged) -- observe-only.
      strip  -> always ok, store_text is the NEUTRALIZED text when flagged.
      reject -> ok=False ONLY on HIGH severity (drop the poisoned fact); LOW/none
                store unchanged.

    SEVERITY is MODEL-DRIVEN: the micro-model injection judge (_judge_severity)
    classifies intent (flag-gated by ``judge_mode`` / [pgvector].memguard_judge_mode,
    default "model"); the structural scan can only ESCALATE it (an obvious
    control-token) and is the DEGRADE-OPEN fallback when the judge is unavailable
    (fail-safe -- an obvious injection still escalates, benign content still stores;
    NEVER a keyword gate, never a silent drop). FAIL-OPEN: any scanner/judge error
    -> ok=True, text unchanged (never lose a store).

<!-- mios-src:c43a4af008b0 from usr/lib/mios/agent-pipe/mios_pipe/access/memguard.py:168-183 -->

### mios_pdp -- the MiOS agent-pipe Policy Decision Point...

mios_pdp -- the MiOS agent-pipe Policy Decision Point (WS-A9, the AIOS
Access-Manager capability gate).

Pure stdlib so it unit-tests in isolation, in the sibling-module style of
mios_sched / mios_toolconflict / mios_trace. server.py owns the wiring (the
dispatching-agent + request-user contextvars, the audit-event emit, and the
SSOT [agents.<name>] / [users.<name>] policy keys); this module owns only the
DECISION: given a verb + a caller's policy, allow or deny.

The bypass it closes
====================
Before WS-A9 the per-agent and per-user RBAC ran ONLY at surface-build time
(pruning the model-facing tool list). The dispatch chokepoint did taint-firewall
+ HITL + enum validation but NO capability check -- so a verb absent from the
filtered surface (a stale tool_call, a direct/MCP/A2A caller, a model that
fabricated a name) would still dispatch. WS-A9 routes BOTH the surface filters
AND the dispatch gate through THIS one decide(), so surface and dispatch can
never diverge.

The fail-OPEN defect it fixes
=============================
The old filters computed `max_rank = rank(mp) if mp in TIERS else None`, i.e. a
max_permission naming an UNKNOWN tier (a config typo) collapsed to None == "no
ceiling" -> the caller silently kept the FULL surface. That is fail-OPEN on the
security axis. resolve_ceiling() now returns rank 0 (the safest tier only) for a
non-empty-but-unknown ceiling -> FAIL CLOSED. (An empty/absent max_permission is
still "no ceiling", the genuine no-op default.)

Decision semantics (decide)
===========================
  * `name` in denied_verbs            -> DENY  (applies to verbs AND non-verbs).
  * not a catalog verb (recipe/skill/MCP/client tool) -> ALLOW (only denied applies).
  * allowed_verbs set and `name` not in it            -> DENY.
  * max_permission ceiling set and the verb's tier outranks it -> DENY.
  * otherwise ALLOW.
An empty policy (no denied/allowed/ceiling) trivially allows everything -> the
ZERO-behaviour-change default for single-user MiOS.

<!-- mios-src:9ca01ba07d17 from usr/lib/mios/agent-pipe/mios_pipe/access/pdp.py:4-41 -->

### Ceiling rank for a configured max_permission. "" / absent...

Ceiling rank for a configured max_permission.

      ""  / absent      -> None  (no ceiling -- the genuine no-op default)
      a KNOWN tier       -> its rank
      a NON-EMPTY UNKNOWN tier -> 0  (FAIL CLOSED: only the safest tier passes)

    The last case is the WS-A9 fix for the old fail-OPEN behaviour (unknown ->
    None -> no ceiling -> full surface granted on a config typo).

<!-- mios-src:463678e6cce3 from usr/lib/mios/agent-pipe/mios_pipe/access/pdp.py:80-87 -->

### RBAC / PDP / quota + human-in-the-loop policy decision...

RBAC / PDP / quota + human-in-the-loop policy decision plane.

Extracted verbatim from ``server.py``. Holds the least-privilege capability
gate (the #55 risk lattice + per-agent/per-user surface filters routed through
the shared :mod:`mios_pdp` core), the #62 human-in-the-loop block-reason + the
out-of-process policy arbiter, the WS-6 per-user quota gate, and the WS-A9
dispatch-time Policy Decision Point.

SECURITY-CRITICAL: the gates are NAME-KEYED on verb keys and permission tiers.
The moved bodies are byte-identical to the originals -- no verb key, gate name,
permission tier, or set-membership test was renamed or rewritten. ``mios_pdp``
(aliased ``_pdp``) and ``mios_quota`` are imported directly; ``_toml_section``
comes from :mod:`mios_config`; every other server-side symbol these helpers
touch (the verb / recipe catalogs, the agent registry, the HITL / client /
dispatch ContextVars, ``_pending_hash``, ``_get_client`` and the DB-event
helpers) is injected via :func:`configure` (one-way module boundary -- this
module never imports ``server``). ``server.py`` re-imports every name under its
original alias so the module's public surface is byte-identical.

<!-- mios-src:d0f2431b5d62 from usr/lib/mios/agent-pipe/mios_pipe/access/policy.py:4-22 -->

### The permission tier that actually governs THIS call....

The permission tier that actually governs THIS call. Umbrella verbs that
    dispatch to a NAMED sub-action with its own permission (os_recipe -> a named
    [recipes.*]) must be gated by the RECIPE's tier, not the umbrella verb's
    worst-case 'interactive' -- otherwise HITL block-mode neutralizes even the
    read-only recipes (service-status / show-network / disk-usage / os-control-
    health) the agent needs for routine OS introspection. Falls back to the
    verb's own permission. Degrade-open: any lookup miss -> the verb tier.

<!-- mios-src:9316435d079d from usr/lib/mios/agent-pipe/mios_pipe/access/policy.py:118-124 -->

### 62: the [ai] RISK-TIER half of the HITL decision. In BLOCK...

#62: the [ai] RISK-TIER half of the HITL decision. In BLOCK mode return a
    human-readable refusal reason if `tool`'s effective tier is at/above
    [ai].hitl_threshold; AUDIT logs + proceeds (None); OFF is inert (None). The
    block/observe verdict is computed by the SINGLE shared resolver
    (``mios_hitl.decide``) that the [hitl] verb-scope gate also routes through, so the
    two HITL gates can no longer disagree (the stricter-wins / fail-safe combine lives
    in the resolver). Degrade-open: never raises, never gates on error. For os_recipe
    the effective tier is the NAMED recipe's, not the umbrella verb's.

<!-- mios-src:2767dbc352c3 from usr/lib/mios/agent-pipe/mios_pipe/access/policy.py:138-145 -->

### Consult the external policy arbiter for a high-risk action...

Consult the external policy arbiter for a high-risk action; return a refusal
    reason on DENY, else None (allow/not-applicable). No-op when no arbiter URL is
    configured. Degrade-open per _HITL_ARBITER_FAIL.

<!-- mios-src:58c6ac6a1c98 from usr/lib/mios/agent-pipe/mios_pipe/access/policy.py:184-186 -->

### WS-2 per-agent RBAC + #55 capability/risk gate: restrict a...

WS-2 per-agent RBAC + #55 capability/risk gate: restrict a dispatched
    agent's tool surface to what its role is permitted. SSOT:
    [agents.<name>].denied_verbs / .allowed_verbs / .max_permission in mios.toml
    (layered vendor<etc<user, surfaced via _AGENT_REGISTRY). No-op when none is
    set -> ZERO behaviour change. Only gates BARE VERBS (names in _VERB_CATALOG):
    names in denied_verbs are dropped; if allowed_verbs is set, any verb NOT in it
    is dropped; if max_permission is set, any verb whose permission tier outranks
    it is dropped. Non-verb tools (recipes/skills/MCP/client tools) pass through
    untouched unless explicitly named in denied_verbs.

<!-- mios-src:b8a1aaa7cac3 from usr/lib/mios/agent-pipe/mios_pipe/access/policy.py:217-225 -->

### 60 WS-6 per-USER authz: restrict the dispatched tool...

#60 WS-6 per-USER authz: restrict the dispatched tool surface by WHO the
    request is from -- the per-USER axis, complementing _agent_rbac_filter's
    per-AGENT axis. SSOT: [users.<name>].denied_verbs / .allowed_verbs /
    .max_permission in mios.toml, matched to the principal the chat surface
    forwarded (_client_env user_name / user_email). No-op when no [users.*] entry
    matches the current user -> ZERO behaviour change (default; single-user MiOS is
    unaffected). Same verb-gating semantics + risk lattice as #55.

    SCOPE NOTE: this keys on the surface-CLAIMED identity. Cryptographic
    SIGNED-principal verification (the 'signed principal' half of #60) is a further
    step -- until then this is policy over a TRUSTED-surface identity, not an auth
    boundary against a forged caller.

<!-- mios-src:950e87369b91 from usr/lib/mios/agent-pipe/mios_pipe/access/policy.py:275-286 -->

### WS-6 per-user rate/budget gate at the dispatch chokepoint....

WS-6 per-user rate/budget gate at the dispatch chokepoint. Counts one
    request per verb dispatch for the matched [users.*] principal; DENY when over
    the user's rpm_limit / daily_budget. Returns a refusal reason on deny, else
    None. INERT when the principal has no quota config (the default), and
    degrade-open (a quota bug must never block real work).

<!-- mios-src:940d7287c531 from usr/lib/mios/agent-pipe/mios_pipe/access/policy.py:334-338 -->

### WS-A9 dispatch-time Policy Decision Point. Re-checks the...

WS-A9 dispatch-time Policy Decision Point. Re-checks the per-AGENT
    (_dispatch_agent_var) and per-USER (_match_user_cfg) capability policy for
    `verb` at the SINGLE dispatch chokepoint, through the SAME mios_pdp core the
    surface filters use -- so a verb pruned from the model surface (or named by a
    stale/MCP/A2A/fabricated call) can NOT still dispatch (the RBAC bypass WS-A9
    closes). Returns a refusal reason on DENY, else None. Degrade-open: an
    unexpected error proceeds (a PDP bug must never block real work); an EXPLICIT
    policy deny always blocks.

<!-- mios-src:5d1bfd67a70c from usr/lib/mios/agent-pipe/mios_pipe/access/policy.py:356-363 -->

### mios_quarantine -- the CaMeL dual-context quarantine gate...

mios_quarantine -- the CaMeL dual-context quarantine gate (F2/task_33 deeper half).

Pure stdlib (+ the pure mios_ruleof2 sibling for the shared mode enum and the
tier->side-effect derivation). The CaMeL design (Debenedetti et al., "Defeating
Prompt Injections by Design") keeps untrusted/attacker-controllable content from
autonomously driving privileged actions. The SOUND, brick-safe MiOS expression of
that boundary is a DETERMINISTIC dispatch gate:

  A  untrusted-input : the session ingested attacker-controllable content (the
                       EXISTING provenance-taint chain; passed in as ``session_tainted``).
  B  sensitive-access: the verb READS sensitive / private / cross-tenant data (the SSOT
                       ``[verbs.*].sensitive`` flag -- additive metadata, not a keyword
                       classifier).
  C  state-change    : the verb mutates state / has external side-effects (derived from
                       the SSOT ``[verbs.*].permission`` tier via the EXISTING
                       ``mios_ruleof2.is_state_change`` policy).

The quarantine boundary BITES when the session is TAINTED (A) AND the verb is
PRIVILEGED -- it either reads sensitive data (B) OR changes state (C). When it bites
the dispatch must be GATED (routed to human review) or BLOCKED; otherwise it proceeds.

This is the STRICTER superset of the Rule-of-Two gate (mios_ruleof2). Rule-of-Two
gates only the all-three chain (A AND B AND C); quarantine-enforce additionally gates
the tainted + (B OR C) case -- the posture you want when you require full CaMeL
isolation: untrusted-content-derived privileged actions cannot fire autonomously; a
human (or a non-tainted plan) must authorize them.

This module is the testable DECISION only. It composes signals the rest of the pipe
already computes -- it does NOT re-derive taint (mios_firewall owns A) or privilege
(the SSOT verb metadata owns B; mios_ruleof2 owns C's derivation). It NEVER imports
server; the wiring (the mode flag, the chokepoint placement, the HITL routing) lives
in mios_dispatch / server.py, composing this gate with the existing
firewall/HITL/Rule-of-Two gates via stricter-wins at the SINGLE dispatch chokepoint
(so there is no second action path that bypasses it).

SOUNDNESS NOTE: the boundary is sound because it sits at the SAME single chokepoint as
the existing gates and only ADDS refusals (stricter-wins composition) -- enabling
quarantine can make the posture stricter, never weaker. The Q-LLM extraction seam
below (``quarantined_extract``) is the OPTIMIZATION on top of this required core; it is
STUBBED (degrade-open to None) as the documented next increment.

<!-- mios-src:05f8230994f2 from usr/lib/mios/agent-pipe/mios_pipe/access/quarantine.py:4-44 -->

### Resolve the SSOT ``[security].quarantine_mode`` value to a...

Resolve the SSOT ``[security].quarantine_mode`` value to a known enum; an
    empty/unknown token -> off (degrade-open: an unrecognised mode never silently
    enforces or audits). Delegates to the SHARED task_33 normaliser so the two
    architectural-gate modes can never drift in their parsing.

<!-- mios-src:894d17edc683 from usr/lib/mios/agent-pipe/mios_pipe/access/quarantine.py:63-66 -->

### Evaluate the quarantine boundary for one verb dispatch....

Evaluate the quarantine boundary for one verb dispatch. Inputs:

      session_tainted -- axis A, the EXISTING provenance-taint signal (bool;
                         mios_firewall owns it -- not re-derived here).
      permission_tier -- the verb's SSOT ``[verbs.*].permission`` (drives axis C via
                         the SAME ``mios_ruleof2.is_state_change`` derivation task_33 uses).
      sensitive       -- the verb's SSOT ``[verbs.*].sensitive`` flag (axis B).
      mode            -- the SSOT ``[security].quarantine_mode`` in force.

    Returns a :class:`QuarantineVerdict`. Total + pure: never raises (an unclassifiable
    tier degrades to side-effecting via :func:`mios_ruleof2.is_state_change`), so a
    call-site can treat any exception as impossible and keep its own degrade-open
    fallback for the I/O around it. Re-derives NOTHING -- it composes the three signals
    the rest of the pipe already computes.

<!-- mios-src:eec468986ef1 from usr/lib/mios/agent-pipe/mios_pipe/access/quarantine.py:105-118 -->

### Q-LLM EXTRACTION SEAM (CaMeL dual-context) -- STUBBED...

Q-LLM EXTRACTION SEAM (CaMeL dual-context) -- STUBBED, degrade-open to None.

    The full CaMeL design routes untrusted content to a QUARANTINED LLM that may ONLY
    extract structured data and CANNOT emit actions, while a privileged planner LLM --
    which never sees the raw untrusted text -- composes the action plan over that
    extracted data (capability-tracked dataflow between two isolated contexts). That
    dual-context split is a larger change to the orchestrator's context plumbing (a
    second constrained inference lane + the data-vs-control flow tracking between the
    contexts), so it is STUBBED here as the documented NEXT INCREMENT.

    The SOUND GATE (:func:`evaluate` wired at the dispatch chokepoint) is the REQUIRED
    core and is INDEPENDENT of this seam: it makes untrusted-content-derived privileged
    actions non-autonomous whether or not this extraction lane exists. This stub
    returning ``None`` means "no constrained extraction available" -> the caller
    proceeds exactly as today (degrade-open); it NEVER newly-opens the gate (the gate
    does not depend on this seam, so a None here cannot weaken the boundary).

    Intended interface (future): ``untrusted_content`` is the raw attacker-controllable
    text; ``schema`` constrains the structured shape the quarantined extractor may emit;
    the return is that structured data (no free-form text, no action tokens) or None.

<!-- mios-src:360c65b513a9 from usr/lib/mios/agent-pipe/mios_pipe/access/quarantine.py:127-146 -->

### mios_quota -- per-user quota + rate limiting (WS-6, the...

mios_quota -- per-user quota + rate limiting (WS-6, the AIOS multi-tenant
fairness layer).

Pure stdlib. RESEARCH NOTE: the production pattern for an LLM gateway (LiteLLM
per-key budgets + RPM/TPM limits) is a PER-PRINCIPAL request-rate cap plus a
spend budget over a rolling window. This is that tracker: a sliding-window RPM
limiter + a per-window cost budget, per user. server.py keys it on the verified
principal (WS-A10) and persists the spend; this owns the deterministic decision.

limits <= 0 disable that dimension -> a user with no [users.*] quota (the
single-user default) is unlimited, so this is a zero-behaviour-change default.

Sources: LiteLLM per-key budgets + rate limiting / cost tracking (docs.litellm.ai).

<!-- mios-src:cc5d12fd8551 from usr/lib/mios/agent-pipe/mios_pipe/access/quota.py:4-17 -->

### mios_sandbox -- risk-tier dispatch sandbox profiles...

mios_sandbox -- risk-tier dispatch sandbox profiles (WS-A13, the AIOS
Access-Manager confinement layer).

Pure stdlib. Every verb dispatch should run confined to the LEAST privilege its
risk tier needs; before WS-A13 there was no per-verb sandbox policy. This module
resolves a verb's permission tier -> a SandboxProfile (mechanism + workspace +
ro/net posture). It is deliberately FAIL-CLOSED: a security control must not
degrade-open, so an unknown tier (a typo, a new tier) maps to the STRICTEST
profile rather than 'none'. server.py runs the profile (bwrap/seccomp/podman +
the per-dispatch /var/lib/mios/ai/dispatch/<verbhash>-<uuid> workspace); this is
the testable decision.

<!-- mios-src:e45dd7beb84a from usr/lib/mios/agent-pipe/mios_pipe/access/sandbox.py:4-15 -->

### Resolve the sandbox profile for a verb. `explicit` -- an...

Resolve the sandbox profile for a verb.

    `explicit` -- an [verbs.*].sandbox_profile override naming a tier-equivalent
    profile ("none"/"workspace"/"strict"); wins when set + recognised.
    Otherwise map `permission_tier` via the tier table. FAIL-CLOSED: an unknown
    tier (or unknown explicit) -> the STRICTEST profile, never 'none'.

<!-- mios-src:55b5de7a0118 from usr/lib/mios/agent-pipe/mios_pipe/access/sandbox.py:55-60 -->

### The mios-sandbox-exec argv PREFIX (ending in '--') a...

The mios-sandbox-exec argv PREFIX (ending in '--') a confined profile maps
    to, or [] for an unconfined ('none') profile. server.py prepends this to a
    verb's broker command so a write/interactive verb runs under the MiOS sandbox
    CLI (which wraps bwrap with progressive --level + cgroup caps). `--level
    enforce` => read-only root + one writable workspace; `--net` is added ONLY when
    the tier permits egress (so 'strict' stays no-net). This is the testable policy
    half; server.py owns the workspace mkdir + the actual exec.

<!-- mios-src:d8830dfbc1b1 from usr/lib/mios/agent-pipe/mios_pipe/access/sandbox.py:86-92 -->

### WS-A13 enforcement primitive

WS-A13 enforcement primitive: translate a resolved SandboxProfile into the
    concrete bubblewrap argv server.py should exec (the PURE, testable half; the
    actual exec/seccomp + workspace mkdir stays in server.py). `cmd` is the verb's
    argv. Flags verified against bubblewrap docs (ArchWiki Bubblewrap/Examples):

      mechanism 'none'  -> NO wrapper: returns cmd unchanged (run direct).
      confined          -> bwrap --die-with-parent --new-session --unshare-all
                           [--share-net IFF profile.network] (no --share-net =>
                           --unshare-all already dropped the net namespace = no net),
                           --ro-bind / /  (read_only_root) | --bind / /  (else),
                           --proc /proc --dev /dev --tmpfs /tmp,
                           [--bind WS WS --chdir WS  IFF workspace given], -- CMD...

    --unshare-all isolates every namespace; --share-net re-adds only networking
    for tiers that need it. Later binds override earlier ones, so --ro-bind / /
    then --bind WS WS yields a read-only root with one writable workspace. The
    `--` ends bwrap's options so the verb's own argv is never mis-parsed.

<!-- mios-src:0b03debdfc29 from usr/lib/mios/agent-pipe/mios_pipe/access/sandbox.py:107-123 -->

### mios_secset -- SSOT-derived security verb sets (WS-A14, the...

mios_secset -- SSOT-derived security verb sets (WS-A14, the AIOS Access-Manager
firewall/HITL scope layer).

Pure stdlib. The taint firewall + the HITL block gate key off a "high-privilege"
verb set; before WS-A14 that set was a hardcoded Python literal that could drift
from the SSOT [security].firewall_high_privilege_verbs list (which existed but
was never consumed). This module derives the EFFECTIVE set as
curated_base ∪ SSOT_list -- the curated base is the never-removed floor (a verb
the code knows is dangerous can't be dropped by an SSOT edit), and the SSOT can
ADD verbs without a code change. Same pattern for the always-taint verb set.

<!-- mios-src:3843966bb641 from usr/lib/mios/agent-pipe/mios_pipe/access/secset.py:4-14 -->
### WS-A13 REFERENCE argv for a resolved SandboxProfile. NOT...

WS-A13 REFERENCE argv for a resolved SandboxProfile.

    NOT what runs. The executor is `usr/libexec/mios/mios-seccomp-filter` +
    `usr/libexec/mios/mios-sandbox-exec`, which builds its own flag set (narrower
    namespace unsharing, plus --cap-drop ALL and the task_210 --seccomp filter this
    function does not model). Read the wrapper, not this, for what a confined
    verb actually gets; reconciling the two is task_253. `cmd` is the verb's argv.
    Flags verified against bubblewrap docs (ArchWiki Bubblewrap/Examples):

      mechanism 'none'  -> NO wrapper: returns cmd unchanged (run direct).
      confined          -> bwrap --die-with-parent --new-session --unshare-all
                           [--share-net IFF profile.network] (no --share-net =>
                           --unshare-all already dropped the net namespace = no net),
                           --ro-bind / /  (read_only_root) | --bind / /  (else),
                           --proc /proc --dev /dev --tmpfs /tmp,
                           [--bind WS WS --chdir WS  IFF workspace given], -- CMD...

    --unshare-all isolates every namespace; --share-net re-adds only networking
    for tiers that need it. Later binds override earlier ones, so --ro-bind / /
    then --bind WS WS yields a read-only root with one writable workspace. The
    `--` ends bwrap's options so the verb's own argv is never mis-parsed.

<!-- mios-src:d0ba2b5efdbb from usr/lib/mios/agent-pipe/mios_pipe/access/sandbox.py:106-126 -->
