<!-- AI-hint: Manual pages distilled from the source comments of kernel, sanitized, each passage anchored to the comment it came from. -->

# kernel

### Cluster / scheduler / health route-handler logic (refactor...

Cluster / scheduler / health route-handler logic (refactor ROUTE-SURFACE wave).

Extracted VERBATIM from ``server.py``: the bodies behind the three deferred
liveness/observability endpoints -- ``/v1/cluster/health`` (per-agent + per-
endpoint probe), ``/v1/scheduler`` (AIOS-style per-lane concurrency + priority
posture), and ``/health`` (capability/health rollup). Each body is moved byte-
identically into a ``*_logic`` function; the ``@app`` routes stay in ``server.py``
as thin wrappers calling these through ``sys.modules`` so the HTTP + importable
surface is unchanged.

The live lane resolver is read through ``mios_lanes_resolver._lane_resolver_current()``
(via ``sys.modules``) inside ``cluster_health_logic`` -- the runtime-reassigned
singleton is never captured by value. Static config / DCI / SLO / secset symbols are
imported directly; every server-resident runtime dependency is injected via
:func:`configure` (one-way boundary -- this module never imports ``server``).

<!-- mios-src:1571b9c075f3 from usr/lib/mios/agent-pipe/mios_pipe/kernel/clusterhealth.py:4-19 -->

### Expand an agent name into the FULL failover chain ( 'remove...

Expand an agent name into the FULL failover chain (
    'remove SPOFs'): self -> declared failover_agents (mios.toml) -> self's
    cpu_endpoint as a last-resort virtual agent. Each entry is {name, endpoint,
    model, kind in {primary,failover,cpu-twin}}. Names already visited in the
    chain are skipped so a config loop can't recurse. Reads the injected-by-
    reference _AGENT_REGISTRY (the only server-side dep), so the move is
    behaviour-identical; the sole caller is cluster_health_logic below.

<!-- mios-src:7cc15e3f5be9 from usr/lib/mios/agent-pipe/mios_pipe/kernel/clusterhealth.py:272-278 -->

### AIOS-style scheduler observability

AIOS-style scheduler observability: live per-lane concurrency state
    (cap / in-flight / available / queued) across every hardware lane the
    swarm dispatches to. Proves the resource-aware concurrency is real +
    shows where contention is. Includes the priority-scoring shape used to
    rank turns.

<!-- mios-src:d9263915d926 from usr/lib/mios/agent-pipe/mios_pipe/kernel/clusterhealth.py:398-402 -->

### Pure config constants + SSOT mios.toml readers (extracted...

Pure config constants + SSOT mios.toml readers (extracted from server.py).

Moved verbatim from ``server.py`` (refactor R1); the module is pure (stdlib only
-- ``os`` / ``logging`` / lazily-imported ``tomllib``) and ``server.py`` re-imports
every name so its importable surface is unchanged. ``mios_config`` MUST NOT import
``server`` (the one-way boundary enforced by ``98-drift-checks.sh`` check 6 ``check_module_boundary``).

<!-- mios-src:bad8df8dfd78 from usr/lib/mios/agent-pipe/mios_pipe/kernel/config.py:4-10 -->

### SAFETY-validate a posted mios.toml replacement. Args...

SAFETY-validate a posted mios.toml replacement.

    Args:
        toml_text: the raw replacement TOML text (already parse-checked by the
            caller, but re-parsed here so this helper is standalone/testable).
        live_config: the current live merged config dict (used ONLY to detect a
            DROPPED critical section). Omit / pass None to skip the drop check
            (degrade-open: if the live config can't be read we don't block).

    Returns:
        (ok: bool, errors: list[str]). ``ok`` is True with an empty ``errors``
        list when the config is safe to write.

<!-- mios-src:8cbd4a5336ce from usr/lib/mios/agent-pipe/mios_pipe/kernel/config.py:367-379 -->

### BACKGROUND async daemon loops (strangler-fig refactor)....

BACKGROUND async daemon loops (strangler-fig refactor).

Extracted VERBATIM from ``server.py``. These are the long-lived ``create_task()``
loop bodies the FastAPI startup hooks spawn: ``_membership_watch_loop`` (FED-G3
live membership hot-reload), ``_gossip_loop`` (WS-A18 trust-gated epidemic peer
discovery), ``_selfimprove_loop`` (#64 proactive finding surfacing), and the
``_reputation_restore`` / ``_reputation_flush`` persistence helpers the reputation
flush loop drives. Every heuristic/guard/comment stays byte-identical. Leaf deps
are imported directly; every server-side symbol is injected via :func:`configure`
(one-way boundary -- this module never imports ``server``). ``server.py`` keeps the
``@app.on_event`` startup hooks and re-imports each loop under its original alias so
the importable surface stays byte-identical.

<!-- mios-src:4c85c3f6901d from usr/lib/mios/agent-pipe/mios_pipe/kernel/daemons.py:4-16 -->

### Inject server-side deps under their EXACT original names...

Inject server-side deps under their EXACT original names (one-way boundary).

    Called once from ``server.py`` after every injected symbol is defined. Each
    keyword equals the module global it sets; the mutable registries are injected by
    reference so in-place mutation stays shared with server.

<!-- mios-src:70224711ad06 from usr/lib/mios/agent-pipe/mios_pipe/kernel/daemons.py:71-76 -->

### Draft a bounded change PROPOSAL for one finding (the...

Draft a bounded change PROPOSAL for one finding (the Autodata "implementer").

    A proposal is {target_kind, target_id, change, rationale}: the artifact to change
    (a prompt / skill / config entry IN the improvable surface), a described tweak,
    and the rationale. Mapping a finding to a CONCRETE improvable target + a change is
    a reasoning step that needs a live model -- and must NOT be a hardcoded
    finding->artifact heuristic (that would be an English gate banned by Law 7) -- so
    this seam is wired to a model-backed drafter validated by the operator. Until then
    it returns None: nothing is fabricated, so even a flipped act_enabled never queues
    a guessed change. Patched in tests to exercise the propose->prove->queue path.

<!-- mios-src:d53ab1e39fdd from usr/lib/mios/agent-pipe/mios_pipe/kernel/daemons.py:230-239 -->

### Score the current baseline vs the proposed variant on a...

Score the current baseline vs the proposed variant on a DISCRIMINATIVE held-out
    eval (task_66). Returns (baseline_score, proposed_score) as pass^k reliabilities, or
    None when no eval/solver is available (-> the proposal cannot be proven and is not
    queued). The live path fetches eval candidates, curates them with the solver-gap
    (mios_selfimprove_act.curate_eval over the SSOT weak/strong lane pair), runs each
    variant through the lanes, and scores via mios_selfimprove_act.pass_hat_k_score --
    a live, operator-validated step, so the offline default is None. Patched in tests
    to supply synthetic baseline/proposed scores.

<!-- mios-src:eee0f87c8475 from usr/lib/mios/agent-pipe/mios_pipe/kernel/daemons.py:244-251 -->

### QUEUE a validated, non-regressing proposal for human review...

QUEUE a validated, non-regressing proposal for human review (an `event` row).
    NEVER applies it. Degrade-open: a pg miss logs + drops the proposal and returns
    False; live serving is never affected. Returns True iff the row was written.

<!-- mios-src:2eedf15df7d9 from usr/lib/mios/agent-pipe/mios_pipe/kernel/daemons.py:256-258 -->

### One ACT pass

One ACT pass: findings -> proposals -> proof-of-utility -> QUEUE (no apply).

    DEFAULT-OFF: returns a no-op summary unless [selfimprove].act_enabled. For each
    high/medium finding (bounded by max_proposals_per_pass): draft a proposal, REJECT
    it up front if it is not in the SSOT improvable surface / is in the protected
    surface (structural isolation -- it is never even scored), else prove utility
    (pass^k non-regression) and QUEUE only a non-regressing proposal. Every accept/
    reject is logged with the score delta (Autodata rejects ~half its own proposals).
    Degrade-open: any error drops the current proposal, never the loop. Returns a
    summary {acted, findings, drafted, queued, rejected}.

<!-- mios-src:a8bec89292c6 from usr/lib/mios/agent-pipe/mios_pipe/kernel/daemons.py:279-288 -->

### The QUEUED self-improvement proposals awaiting human...

The QUEUED self-improvement proposals awaiting human approval (read-only).
    Degrade-open -> {proposals:[], error} when pg is unreachable so the route stays
    up. These are validated + non-regressing (task_66) but NEVER auto-applied.

<!-- mios-src:9c21095bf1c0 from usr/lib/mios/agent-pipe/mios_pipe/kernel/daemons.py:342-344 -->

### WS-A4

WS-A4: one GC pass over the LOCAL KV slot dir (no-op when it's remote /
    unset / empty). Plans TTL+size eviction via mios_kvgc, protecting the file of
    whatever conversation is resident in the active slot, then removes evictees.
    Best-effort + degrade-open: any error leaves the files (the tmpfiles age-out
    is the backstop).

<!-- mios-src:6546a5af00a6 from usr/lib/mios/agent-pipe/mios_pipe/kernel/daemons.py:390-394 -->

### Read-only

Read-only: the QUEUED self-improvement proposals awaiting human approval -- the
    ACT half of #64 (task_64). Each was validated for target isolation and proven
    non-regressing (task_66 proof-of-utility) before queuing, but is NEVER auto-applied:
    the operator reviews + approves out of band, then a separate path applies it.

<!-- mios-src:fb34bca1c23f from usr/lib/mios/agent-pipe/mios_pipe/kernel/daemons.py:453-456 -->

### mios_gossip -- federated agent discovery via epidemic...

mios_gossip -- federated agent discovery via epidemic gossip + anti-entropy
(WS-A18, the AIOS peer-discovery layer).

Pure stdlib. MiOS federates agents over A2A; mios_reputation scores peers but
there was no DISCOVERY mechanism -- how a node learns which peers exist and keeps
that set fresh + trustworthy without a central registry. This is the classic
answer: epidemic gossip (each round, push/pull rumors to a small random fanout)
with SWIM-style failure detection (an incrementing per-peer heartbeat /
incarnation; higher wins on merge; unheard peers age out by TTL).

Two MiOS-specific properties:
  * TRUST-GATED merge -- a peer rumor is only accepted if its trust (from
    mios_reputation, and gated by mios_crl revocation upstream) clears
    `min_trust`. This is the OWASP-Agentic "rogue agent / unauthorized
    delegation" defense applied to discovery: a low-reputation or revoked peer
    cannot inject itself (or poison the peer set) via gossip.
  * DETERMINISTIC selection -- `select_gossip_peers` is seeded (caller passes the
    round number), so a round is reproducible + unit-testable; no global RNG.

server.py owns the transport (push the `digest()` to the selected peers, pull
theirs, `merge_peer_set` the response) + the periodic round + wiring trust to
mios_reputation; this module owns the deterministic convergence math.

<!-- mios-src:58549bf3c205 from usr/lib/mios/agent-pipe/mios_pipe/kernel/gossip.py:4-26 -->

### mios_kernel -- the MiOS agent-pipe Kernel facade...

mios_kernel -- the MiOS agent-pipe Kernel facade (WS-A11/WS-3, Stage 1b).

A thin composition that gives the decomposed agent-pipe ONE object holding the
Router (decide), the Dispatcher (run), and the five AIOS manager seams. The
managers + dispatcher are INJECTED by server.py (concrete adapters over the
existing scheduler/memory/context/tool/access code paths) so this module imports
NOTHING from server.py and is fully testable with fakes. Stage 2 builds the
KERNEL once and rewires chat_completions to `KERNEL.handle(refined, ...)`,
replacing the inline intent cascade.

Contract:
    decision = kernel.router.route(refined)        # pure (mios_router)
    result   = await kernel.dispatcher.run(decision, refined=refined, **ctx)
The Dispatcher is duck-typed: any object exposing `async run(decision, **ctx)`.

<!-- mios-src:719fdeb5222b from usr/lib/mios/agent-pipe/mios_pipe/kernel/kernel.py:4-18 -->
### Headless kernel crash dump triage engine and symbol...

Headless kernel crash dump triage engine and symbol resolver for MiOS.

Decompresses /var/crash/vmcore.zst post-panic, analyzes kdump/vmcore/pstore/dmesg oops outputs,
demangles C/Rust backtraces, isolates faulting kernel/DKMS modules, formats markdown incident
reports in /var/log/mios/crash/, and files structured tickets into PostgreSQL bug_tracker.

<!-- mios-src:b787735fa70d from usr/libexec/mios/kernel/crash_triage.py:4-9 -->

### dkms_engine.py — task_709 WS-BUILD Ephemeral containerized...

dkms_engine.py — task_709 WS-BUILD
Ephemeral containerized DKMS engine and MOK kernel module signer.

Compiles out-of-tree .ko drivers inside bubblewrap sandbox against active UKI kernel,
signs with local MOK key, and caches in /var/lib/dkms/<kver>/ by build hash.

<!-- mios-src:db0ac3c48043 from usr/libexec/mios/kernel/dkms_engine.py:4-10 -->
