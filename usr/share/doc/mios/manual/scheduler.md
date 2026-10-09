<!-- AI-hint: Manual pages distilled from the source comments of scheduler, sanitized, each passage anchored to the comment it came from. -->

# scheduler

### mios_batch -- batch-interval coalescing for the MiOS...

mios_batch -- batch-interval coalescing for the MiOS agent-pipe (WS-A6, the
AIOS scheduler call-coalescing layer).

Pure stdlib. RESEARCH NOTE (the proper solution): the modern inference engines
MiOS runs locally -- vLLM (PagedAttention), SGLang (RadixAttention), and
llama.cpp -- all implement CONTINUOUS BATCHING: the engine's own scheduler forms
a rolling batch from concurrent requests with no fixed timer/count, which is
strictly better than any client-side grouping. So coalescing must NOT touch
those lanes (double-batching only adds head-of-line latency). It applies ONLY to
endpoints WITHOUT native continuous batching -- a rate-limited remote API where
grouping calls in a short window genuinely reduces request count. Hence the core
here is: bypass native lanes; window-bound the rest.

Sources: vLLM continuous batching (docs.vllm.ai), SGLang OpenAI-compatible
serving, BentoML "Static, dynamic and continuous batching" (LLM Inference Handbook).

<!-- mios-src:5201464ad550 from usr/lib/mios/agent-pipe/mios_pipe/scheduler/batch.py:4-19 -->

### mios_bench -- pure scoring core for the MiOS...

mios_bench -- pure scoring core for the MiOS capability-benchmark harness.

The AIOS engineering blueprint flagged the single clearest external-validation
gap: MiOS instruments the *operational* CLASSic dimensions (cost/latency/
stability/security via mios_quota / mios_trace / mios_stress / the fitness gates)
but had NO standard agentic-capability benchmark runner. This module is the pure,
deterministic half of that harness: the reliability metrics + the CLASSic rollup.
The libexec `mios-bench` CLI drives trials against the agent-pipe endpoint
(port key `agent_pipe`) -- that half needs the live VM -- then scores the results through here.

RESEARCH GROUNDING (web-verified):
  * pass@k -- "at least one of k samples passes". Unbiased estimator
    (OpenAI Codex / HumanEval): 1 - C(n-c, k) / C(n, k) for n samples, c correct.
  * pass^k -- tau-bench's worst-case RELIABILITY metric, "ALL k attempts
    succeed" (arXiv 2406.12045). Unbiased estimator: C(c, k) / C(n, k). The i.i.d.
    closed form is p^k (a 93%-pass@1 agent is only ~0.93^8 ~= 0.56 reliable at
    k=8) -- consistency, not average, is what production needs.
  * CLASSic (arXiv 2511.14136 / Aisera) -- Cost, Latency, Accuracy, Stability,
    Security: production agent quality is multi-dimensional, not just accuracy.

<!-- mios-src:42187aaa7ab6 from usr/lib/mios/agent-pipe/mios_pipe/scheduler/bench.py:4-23 -->

### Fraction of tasks that CLEAR the HARD pass^k gate -- the...

Fraction of tasks that CLEAR the HARD pass^k gate -- the suite-wide analogue
    of the mios-skills promotion gate. That gate demands ALL k repeats succeed, so
    a task clears iff its pass^k reliability is a perfect 1.0 (every trial passed ->
    any k-subset all-succeeds; pass_hat_k(n,c,k)==1 iff c==n). This is DISTINCT from
    the MEAN pass^k (aggregate_pass_hat_k): the mean averages partial reliabilities,
    this counts how many tasks would survive the all-or-nothing gate. Reuses
    pass_hat_k. Tasks with fewer than k trials are skipped. 0.0 if none qualify.

<!-- mios-src:f313cd7fa8e2 from usr/lib/mios/agent-pipe/mios_pipe/scheduler/bench.py:84-90 -->

### Roll a flat list of per-trial records into the CLASSic...

Roll a flat list of per-trial records into the CLASSic dimensions. Each
    record: {task: str, ok: bool, cost: float, latency_ms: float,
    error: bool, security_violation: bool}. Returns:

      cost_total / cost_mean        -- sum + mean of `cost` (Cost)
      latency_p50 / latency_p95     -- ms percentiles of `latency_ms` (Latency)
      accuracy                      -- fraction ok (Accuracy)
      stability                     -- mean pass^k across tasks grouped by `task`
                                       (worst-case reliability, NOT average); falls
                                       back to (1 - error_rate) if k<=1 (Stability)
      security                      -- 1 - fraction with security_violation (Security)
      n / n_tasks                   -- trial + distinct-task counts

    Pure + deterministic; the CLI passes the trial log straight in.

<!-- mios-src:ebf3c28e2367 from usr/lib/mios/agent-pipe/mios_pipe/scheduler/bench.py:114-127 -->

### mios_blades -- blade (machine) topology + per-blade...

mios_blades -- blade (machine) topology + per-blade capacity model.

V4 makes "nodes X, Y, Z are one machine" EXPRESSIBLE: each [nodes.*] may carry an
optional `blade` (which physical machine it lives on), and [blades.<name>] declares
that machine's capacity. V5 gives the model a real consumer: the admission gate
compares a node's residents against ITS blade's VRAM budget instead of the single
LOCAL scalar (the "remote residents vs one local VRAM scalar" bug).

DEFAULT-PRESERVING by construction: a node with no `blade` belongs to the LOCAL blade
(name from the [identity] hostname SSOT), whose capacity defaults to the caller's
existing VRAM_BUDGET_MB. So a config with no [blades.*] and no blade fields resolves
every endpoint to one local blade at the local budget -- i.e. exactly today. Every
lookup degrades OPEN (unknown blade/capacity -> the local scalar) so admission can
never wedge on a missing blade.

<!-- mios-src:2d964abe9c10 from usr/lib/mios/agent-pipe/mios_pipe/scheduler/blades.py:4-18 -->

### Resolve THIS machine's blade name from SSOT, NOT a baked...

Resolve THIS machine's blade name from SSOT, NOT a baked literal.

    Precedence: env ``MIOS_IDENTITY_HOSTNAME`` (the install.env bridge derived from
    [identity].hostname) -> [identity].hostname -> the OS hostname
    (``socket.gethostname()``) as the degrade-open fallback. Always returns a
    non-empty name when the OS can report one; only a total failure yields ''.

<!-- mios-src:be081ba9cb54 from usr/lib/mios/agent-pipe/mios_pipe/scheduler/blades.py:44-50 -->

### Build ``{blade_name: {"vram_budget_mb": int, "load_ceil"...

Build ``{blade_name: {"vram_budget_mb": int, "load_ceil": float|None}}``.

    The LOCAL blade is ALWAYS present and defaults to the caller's existing
    VRAM_BUDGET_MB scalar (and optional local load ceiling), so a config with NO
    [blades.*] section reproduces today's single-blade capacity byte-for-byte. A
    declared [blades.<local>] may OVERRIDE the local capacity; remote blades carry
    their own. A declared blade that omits ``vram_budget_mb`` degrades OPEN to the
    local scalar (unknown capacity is never a wedge). Degrade-open: a malformed or
    absent section -> just the local blade at the local scalar.

<!-- mios-src:364670302905 from usr/lib/mios/agent-pipe/mios_pipe/scheduler/blades.py:67-76 -->

### Map each registry endpoint (``host:port`` via...

Map each registry endpoint (``host:port`` via ``endpoint_key``) to its blade.

    A [nodes.*]/[agents.*] entry with an explicit ``blade`` carries it; one WITHOUT a
    blade belongs to the LOCAL blade -- so a config with no blade fields makes every
    endpoint local (today). Returns ``{endpoint_key: blade_name}``. Endpoints absent
    from this map resolve to the local blade at lookup time (see blade_for_endpoint).

<!-- mios-src:182df9a6bf99 from usr/lib/mios/agent-pipe/mios_pipe/scheduler/blades.py:113-119 -->

### mios_evict -- pure helpers for the knowledge-table eviction...

mios_evict -- pure helpers for the knowledge-table eviction sweep (WS-A3).

DB-free + stdlib-only so the SQL-building, response-parsing, and planning logic
unit-tests in isolation (sibling-module pattern). server.py owns the actual
Postgres I/O (mios_pg.execute), the config knobs, and the background loop.

WS-A3 cutover: this emits PARAMETERIZED Postgres (named placeholders bound by
mios_pg) -- the previous legacy query fragments (`??`, record-id
`DELETE a, b;`) silently no-op'd once db_backend='postgres' (the legacy :8000
backend is retired), so eviction never ran. The knowledge table is append-only; eviction
removes only STALE, never-recalled, neutral-outcome rows and NEVER a
hot/satisfied/pinned/recently-accessed one.

<!-- mios-src:8727fd3fb90c from usr/lib/mios/agent-pipe/mios_pipe/scheduler/evict.py:4-16 -->

### mios_preempt -- round-robin preemption policy + snapshot...

mios_preempt -- round-robin preemption policy + snapshot contract (WS-A12, the
AIOS scheduler time-slice layer).

Pure stdlib (time passed in -> deterministic). Strict-priority scheduling can let
a long high-priority generation hog the lane; RR time-slicing preempts it after a
QUANTUM, snapshots its partial state, requeues it, and lets the next waiter run.
This module owns the BOOKKEEPING: quantum expiry, a bounded free-list of snapshot
slots (so suspensions can't grow unbounded), and a priority-ordered suspended
queue. server.py owns the engine-side interruptible decode + snapshot
restore/save (which needs llama.cpp/SGLang support); this is the testable policy.

TURN-boundary preemption seam (task_19 / task_19)
================================================
:func:`turn_boundary` is the agent-pipe's TURN-level preemption hook -- DISTINCT
from the decode-loop RR time-slice ([dispatch].rr_*) and from the priority SCORER
([sched]). The dispatch turn loop calls it AFTER a turn's AIOS priority is known;
when enabled the turn-boundary :class:`PreemptScheduler` may snapshot + yield the
turn to a higher-priority waiter and resume it. It is FLAG-GATED on
``mios.toml [scheduler].preempt_enable`` (DEFAULT-OFF -> byte-identical no-op) and
DEGRADE-OPEN (any scheduler error runs the turn normally -- a turn is never
dropped). It is the clean substrate later scheduler policies build on. The module
reads its ``[scheduler]`` SSOT itself (mios_sched-style) so it is self-contained +
unit-testable; server.py injects the live "is a higher-priority turn waiting?"
probe via :func:`configure`. ONE-WAY BOUNDARY: this module never imports server.

Token-time-sliced priority queue (task_20 / task_20)
===================================================
:class:`TokenSliceQueue` is the queueing POLICY that sits ON TOP of the task_19
turn_boundary mechanism. Turns enqueue with a priority + a per-turn SLICE BUDGET
measured in TOKENS (a token-time quantum -- NOT wall-clock); the scheduler
dispatches the highest-priority ready turn, accounts the tokens it generates
against its slice (via the shared :mod:`mios_tokenize` seam -- never a re-derived
chars//N), and at each slice boundary :func:`slice_boundary` re-evaluates through
turn_boundary: yield the lane to a higher-priority waiter (the existing
snapshot/resume) or continue. It has its OWN master gate
(``[scheduler].queue_enable``, DEFAULT-OFF) so the default path is byte-identical
(no queue interposition) and is DEGRADE-OPEN (any queue error runs the turn
normally -- never dropped/stalled). The queue is bounded so advisory bookkeeping
can never grow without limit. The live token feed + the precise gate-relative
enqueue/dispatch placement are operator-live-validated; this module owns the
ordering + slice-accounting policy + the boundary re-eval.

<!-- mios-src:cfd7c1f1160f from usr/lib/mios/agent-pipe/mios_pipe/scheduler/preempt.py:4-45 -->

### Pure per-slice-boundary decision for an interruptible...

Pure per-slice-boundary decision for an interruptible generation.

    - finished -> COMPLETE (the decode loop saw a stop/EOS within the slice).
    - a higher-priority waiter IS queued AND this run has spent its quantum AND
      we can bound the suspension (a free snapshot slot exists) -> PREEMPT.
    - otherwise CONTINUE (run another slice).

    Bounded-suspension safety: when no snapshot slot is free (`can_suspend` is
    False) we NEVER preempt -- the task runs to completion instead -- so the set
    of suspended generations can never exceed the cap and a preempted task is
    never dropped on the floor. A generation is only ever preempted at a slice
    boundary, so its partial output up to that boundary is always captured.

<!-- mios-src:1cc1431d8b8e from usr/lib/mios/agent-pipe/mios_pipe/scheduler/preempt.py:68-79 -->

### Remove a SPECIFIC suspended task and free its slot...

Remove a SPECIFIC suspended task and free its slot, returning its
        Snapshot (None if it was not suspended). This is the self-resume path for
        the gate-driven driver: a preempted generation re-acquires the lane via
        the priority gate (which already orders waiters by priority), so it
        discharges ITS OWN snapshot rather than popping the global highest via
        resume() -- that would let one coroutine steal another's saved state.

<!-- mios-src:a24e575e741c from usr/lib/mios/agent-pipe/mios_pipe/scheduler/preempt.py:174-179 -->

### Token-time-sliced priority queue (task_20 / task_20) -- the...

Token-time-sliced priority queue (task_20 / task_20) -- the queueing POLICY on
    top of the task_19 turn-boundary mechanism. It orders ready turns by priority and,
    for a dispatched turn, accounts the tokens it generates against a per-turn SLICE
    BUDGET (a token-time quantum -- N tokens, NOT wall-clock). When a turn crosses its
    slice budget the scheduler re-evaluates (the caller drives that via slice_boundary
    -> turn_boundary): a higher-priority waiter may preempt it, else it continues.

    Pure bookkeeping (deterministic; token counts are passed IN) so it unit-tests in
    isolation, like PreemptScheduler. Single-threaded asyncio: every method is
    allocation-light and lock-free (no await between a check and its mutation). The
    queue is BOUNDED (max_turns) and evicts only STALE ready entries, so its advisory
    bookkeeping can never grow without limit even if a caller misses a remove(); a
    turn's coroutine is never affected by eviction (degrade-open). The live token feed
    + the precise gate-relative enqueue/dispatch placement are operator-live-validated;
    this class owns the MLFQ ordering (LRU eviction + contention-gated priority decay)
    + slice accounting.

<!-- mios-src:e1f4efb7cddc from usr/lib/mios/agent-pipe/mios_pipe/scheduler/preempt.py:198-213 -->

### Add `tokens` to the turn's CURRENT-slice counter. Returns...

Add `tokens` to the turn's CURRENT-slice counter. Returns True iff it
        crossed its slice budget (a slice boundary -- the caller then re-evaluates),
        carrying the remainder into the next slice. A 0/absent budget never trips
        (slicing off). Unknown task / bad count -> False (degrade-open).

<!-- mios-src:5354ab1aab26 from usr/lib/mios/agent-pipe/mios_pipe/scheduler/preempt.py:273-276 -->

### Resolve the [scheduler] table: layered mios.toml merged...

Resolve the [scheduler] table: layered mios.toml merged over the degrade-open
    fallbacks, then MIOS_SCHEDULER_* env overrides (highest precedence). Pure SSOT
    (mios_config); never raises (missing/broken config -> fallbacks).

<!-- mios-src:1d6aa0808674 from usr/lib/mios/agent-pipe/mios_pipe/scheduler/preempt.py:357-359 -->

### Override the turn-boundary wiring under exact names...

Override the turn-boundary wiring under exact names (one-way boundary).

    server.py calls this to inject the live PriorityGate head-priority probe
    (``head_priority=``) so the seam can tell when a higher-priority turn waits;
    tests inject a spy ``turn_scheduler=`` / ``clock=`` / ``preempt_enable=``.
    Friendly aliases (head_priority/turn_scheduler/clock/quantum_s/...) map to the
    module globals. Unknown keys are ignored (partial-injection safe).

<!-- mios-src:3cf51353a9a4 from usr/lib/mios/agent-pipe/mios_pipe/scheduler/preempt.py:429-435 -->

### True iff a higher-priority turn is waiting. Two OR-combined...

True iff a higher-priority turn is waiting. Two OR-combined signals: the
    injected gate head-priority probe (task_19) and -- ONLY when the token-time-sliced
    queue is enabled ([scheduler].queue_enable, task_20) -- the queue's highest-priority
    READY waiter (excluding this turn). False when unwired or on any probe error -- no
    signal => no preemption (degrade-open). With queue_enable OFF this is byte-
    identical to the prior probe-only behaviour.

<!-- mios-src:71b6dbb44fdc from usr/lib/mios/agent-pipe/mios_pipe/scheduler/preempt.py:445-450 -->

### Turn-boundary preemption seam (task_19 / task_19). The...

Turn-boundary preemption seam (task_19 / task_19). The dispatch turn loop
    calls this AFTER a turn's AIOS priority is known -- the clean point at which a
    scheduler decides whether to preempt. Returns True iff this turn was preempted
    (snapshotted + yielded + resumed), else False.

    DEFAULT-OFF ([scheduler].preempt_enable=false): returns False IMMEDIATELY --
    the PreemptScheduler is NOT consulted, so the turn runs byte-identically.

    ENABLED: consults the turn-boundary PreemptScheduler via the pure decide()
    primitive (at a boundary the prior slice is spent, so quantum_expired=True ->
    preempt iff a higher-priority turn waits AND a snapshot slot is free, else run
    to completion -- bounded suspension). On PREEMPT it SNAPSHOTS this turn into a
    slot, cooperatively YIELDS the event loop (bounded by max_preempt_depth ticks
    AND the quantum, so a turn is never starved or busy-waited), then RESUMES by
    discharging ITS OWN snapshot -- the snapshot/resume round-trip per the
    PreemptScheduler API. The richer cross-turn blocking policy that later
    schedulers (task_20/task_60) layer on this seam is operator-live-validated.

    DEGRADE-OPEN: ANY scheduler error -> returns False and the turn proceeds
    normally; a best-effort discharge prevents a leaked snapshot. Preemption NEVER
    drops or corrupts a turn.

<!-- mios-src:29831a5f65d6 from usr/lib/mios/agent-pipe/mios_pipe/scheduler/preempt.py:472-492 -->

### Token-time-slice boundary hook (task_20 / task_20) -- the...

Token-time-slice boundary hook (task_20 / task_20) -- the queueing POLICY on
    top of the task_19 turn_boundary mechanism. The generation loop calls this as a
    dispatched turn produces output: it ACCOUNTS the tokens generated this step
    against the turn's SLICE BUDGET (a token-time quantum -- SLICE_TOKENS tokens, NOT
    wall-clock) and, ONLY when the budget is crossed (a slice boundary), RE-EVALUATES
    via turn_boundary -- snapshot + yield to a higher-priority waiter, or continue.
    Returns True iff the turn was preempted at this boundary.

    Token-time accounting: `tokens` is a count the caller already measured through the
    mios_tokenize seam; alternatively pass `text` and it is counted HERE through the
    SAME seam (never a re-derived chars//N).

    DEFAULT-OFF ([scheduler].queue_enable=false): returns False IMMEDIATELY -- the
    queue is NOT consulted, so the turn runs byte-identically (no interposition).

    ENABLED: accounts the tokens; a slice boundary delegates to turn_boundary (itself
    gated on preempt_enable + a higher-priority waiter -- the queue head is one such
    signal, see _higher_priority_waiting). On a real preempt the turn is REQUEUED
    (ready) so the next dispatch() re-orders it by priority.

    DEGRADE-OPEN: ANY queue/scheduler error -> returns False and the turn runs
    normally; a turn is never dropped or stalled.

<!-- mios-src:0c8ade64051e from usr/lib/mios/agent-pipe/mios_pipe/scheduler/preempt.py:532-553 -->

### mios_sched -- scheduler primitives for the MiOS agent-pipe...

mios_sched -- scheduler primitives for the MiOS agent-pipe (WS-1, the AIOS
Agent Scheduler reordering layer).

Pure stdlib (asyncio / time / collections) so it unit-tests in isolation, in the
sibling-module style of mios_jsonsalvage / mios_owui. server.py owns the wiring
(the SSOT flag, the global instance, the degrade-open context manager); this
module owns only the reusable mechanism.

PriorityGate
============
A bounded concurrency gate -- like asyncio.Semaphore -- EXCEPT that, when
contended, it hands the next freed permit to the HIGHEST-PRIORITY waiter
(FIFO tie-break) instead of the earliest arrival. That is the reordering a plain
Semaphore cannot do: with a Semaphore, once a dispatch is queued behind the
global cap, a later higher-priority dispatch can never jump ahead. The MiOS
agent-pipe already computes a per-turn / per-lane priority (_sched_priority /
_dispatch_priority) but, before WS-1, those were advisory only because the global
cap admitted FIFO. PriorityGate makes them ACTIVE.

Anti-starvation
---------------
Strict priority can starve low-priority work forever under sustained
high-priority load. `starvation_s > 0` enables aging: a waiter that has been
queued longer than `starvation_s` is served AHEAD of priority, so the lowest
lanes still make progress.

Key invariant
-------------
    available > 0  =>  no waiters

Release hands a permit DIRECTLY to the chosen waiter (it never bumps
`available` while any waiter exists). Therefore the acquire fast path -- "a
permit is free, take it" -- can only run when the queue is empty, so it can
never jump ahead of a queued higher-priority dispatch. This keeps the fast path
allocation-free (no future, no heap) while preserving correctness.

Concurrency model: single-threaded asyncio. There is no await between the check
and the mutation in any method, so no lock is needed.

<!-- mios-src:c188199820c6 from usr/lib/mios/agent-pipe/mios_pipe/scheduler/sched.py:4-42 -->

### Pick a LIGHT engine the agent can run on for concurrent...

Pick a LIGHT engine the agent can run on for concurrent fan-out, else None
    (caller uses the agent's OWN endpoint). DEFAULT: None for everything
    (DISPATCH_OFFLOAD_CPU off) so each distinct node runs on its OWN hardware
    concurrently instead of all funneling to the single CPU lane (operator
).

<!-- mios-src:be593746a089 from usr/lib/mios/agent-pipe/mios_pipe/scheduler/sched.py:234-238 -->

### Compute the turn priority from ``refined`` + the resolved...

Compute the turn priority from ``refined`` + the resolved [sched] ``cfg`` table.
    Every tier/weight resolves from ``cfg`` with an _SCHED_FALLBACK default (== the
    historical literal), so an empty ``cfg`` is byte-identical to the prior heuristic.
    Split from _sched_priority so the wrapper can degrade-open (cfg={}) on any error.

<!-- mios-src:59f15a0c6e2b from usr/lib/mios/agent-pipe/mios_pipe/scheduler/sched.py:271-274 -->

### Score a turn the AIOS way

Score a turn the AIOS way: priority = f(complexity, urgency, resource-need).
    Derived from the refined plan (no hardcoded topic map): complexity from the
    task/step count + tool count; urgency from the model's refined.urgency signal (a
    numeric model value when [sched].priority_mode='model', else membership in the
    operator-localizable [sched] urgency vocabulary, matched Unicode-casefold);
    resource-need from the target lane. Higher = sooner. Tiers + weights are SSOT in
    mios.toml [sched] with degrade-open fallbacks EQUAL to the historical literals, so
    an absent section is byte-identical. Currently ADVISORY (logged + exposed) -- the
    lane semaphores still admit in arrival order; this is the hook a future policy
    engine would order on. Degrades open on any error to the literal-fallback path.

<!-- mios-src:5cbaa53282a7 from usr/lib/mios/agent-pipe/mios_pipe/scheduler/sched.py:336-345 -->

### SEMAPHORE KEY -- the distinct HARDWARE UNIT an agent runs...

SEMAPHORE KEY -- the distinct HARDWARE UNIT an agent runs on, which is
    NOT the same as its lane CATEGORY (_agent_lane). With more than one machine
    of a category on the tailnet (e.g. the local 4090 AND a remote GPU box),
    'gpu' is no longer ONE piece of hardware, so a shared 'gpu' semaphore would
    throttle BOTH boxes to a single per-lane budget. A custom per-node lane
    (e.g. 'potato-gpu' -- any lane outside the base category set) therefore gets
    its OWN semaphore so distinct machines fire with INDEPENDENT concurrency
 budgets ("each remote node gets its OWN semaphore").
    Agents without a custom lane fall back to the category (local hardware).
    NOTE: _agent_lane stays the CATEGORY (gpu/cpu/igpu/mobile/accelerator) so
    SLOW_LANES trimming + the cpu-parallelism bonus keep working -- e.g. a
    'potato-cpu' node is category 'cpu' (slow -> trimmed) yet has its OWN sem.

 SWARM Phase-0 : an explicit `sub_lane` is the FINEST
    semaphore key -- it lets N single-model servers on the SAME device (e.g.
    'gpu0' for several concurrent small llama-server instances on the one 4090)
    each hold an INDEPENDENT concurrency budget instead of all collapsing onto a
    single 'gpu' semaphore (the documented OOM-cascade mode). Defaults to the
    prior behaviour when unset -> byte-identical for today's nodes (none set it).

<!-- mios-src:168ee3cf97dc from usr/lib/mios/agent-pipe/mios_pipe/scheduler/sched.py:362-380 -->

### mios_slo -- SLO-class admission + EDF ordering +...

mios_slo -- SLO-class admission + EDF ordering + fail-closed shed (WS-SCHED-SLO).

The modern SLO-serving frontier (SCORPIO/Andes/QLM): each request carries a
deadline/SLO class, the scheduler orders least-deadline-first, and best-effort
work is SHED under contention rather than unconditionally admitted. MiOS's
`_admit` is capacity-only (it always admits after a bounded wait) and worse,
degrades OPEN -- a DB/VRAM-probe failure during a storm silently disables
backpressure entirely.

This module is the PURE policy:
  * classify()     -- turn signals -> SLO class (interactive | best_effort).
  * deadline()     -- now + the class's wall-clock budget.
  * edf_key()      -- least-deadline-first sort key (earliest deadline served
                      first; interactive breaks ties).
  * should_shed()  -- FAIL-CLOSED: shed a best_effort dispatch under contention
                      OR when health is UNKNOWN (probe failed); NEVER shed
                      interactive. This inverts the current degrade-open hole.

server.py owns wiring (classify the turn, feed edf_key into PriorityGate._pick,
call should_shed in _admit), all flag-gated. Deterministic, no I/O.

<!-- mios-src:551d74690cd4 from usr/lib/mios/agent-pipe/mios_pipe/scheduler/slo.py:4-24 -->

### Map turn signals to an SLO class. An AUTONOMOUS /...

Map turn signals to an SLO class. An AUTONOMOUS / background turn is
    best_effort; a FOREGROUND turn is interactive UNLESS its scheduling priority
    was clamped below `interactive_priority` (the autonomous-clamp path), in which
    case it is best_effort too. Fail-safe default (foreground, unclamped) ->
    interactive (protect the human). Unspecified priority / interactive_priority
    fall back to the SSOT-injected defaults (`_DEFAULT_PRIORITY` /
    `_INTERACTIVE_PRIORITY`).

<!-- mios-src:0708625fadb4 from usr/lib/mios/agent-pipe/mios_pipe/scheduler/slo.py:57-63 -->

### FAIL-CLOSED shed decision. An INTERACTIVE turn is NEVER...

FAIL-CLOSED shed decision. An INTERACTIVE turn is NEVER shed (the human is
    protected). A BEST_EFFORT dispatch is shed when the system is over its
    capacity ceiling OR when health is UNKNOWN (`healthy=False`, e.g. the load/mem
    probe failed) -- the latter is the correctness fix: where `_admit` currently
    degrades OPEN (admit-on-probe-failure), best_effort here degrades CLOSED (shed
    when we can't confirm headroom), so a probe failure during a storm tightens
    backpressure instead of disabling it.

<!-- mios-src:46ddfd3a5992 from usr/lib/mios/agent-pipe/mios_pipe/scheduler/slo.py:91-97 -->

### mios_stress -- end-to-end direct-chat stress harness for...

mios_stress -- end-to-end direct-chat stress harness for the MiOS agent-pipe.

Drives the OpenAI /v1/chat/completions path under BOUNDED, load-aware concurrency
and reports latency / throughput / error-rate + a pass/fail verdict. Built for
the full-conversion validation goal (llama.cpp + KV-paging primary, pgvector
backend, all features on).

SAFETY -- the operator's hard-won lessons baked in:
  * COMPLETES every turn (awaits to done) -- NEVER orphans a request. The server
    historically has a request-cancellation gap; abandoning turns (the classic
    bounded-curl mistake) leaves the DAG+deepen churning for minutes -> loadavg
    spikes -> wedge (the documented loadavg-361 incident). This harness never
    abandons a turn.
  * LOAD-AWARE circuit breaker: polls /v1/scheduler between waves; over the load
    ceiling it stops RAMPING and backs off (AIMD) -- "saturate the backlog,
    never the cores."
  * RAMPED concurrency: starts low, climbs toward the target only while healthy.

The pure helpers (percentile/aggregate/ramp/throttle/scenarios/verdict) are
stdlib-only + unit-tested (test_mios_stress.py); the async runner uses httpx
(already an agent-pipe dep) and is exercised live by the operator via
`mios-stresstest`.

<!-- mios-src:8f5d4ac44379 from usr/lib/mios/agent-pipe/mios_pipe/scheduler/stress.py:5-27 -->
