<!-- AI-hint: Manual pages distilled from the source comments of lifecycle, sanitized, each passage anchored to the comment it came from. -->

# lifecycle

### mios_capreg -- unified, RBAC-filtered capability registry...

mios_capreg -- unified, RBAC-filtered capability registry projection (WS-2).

MiOS's capability surface is three-projected (verbs / MCP / A2A), and mios_manifest
projects the verb catalog -- but recipes (the [recipes.*] OS-command templates) and
their permission tiers were never unified into one RBAC-filtered manifest. This is
that projection: given the verb catalog + the recipe table + a caller's permission
CEILING, emit the single list of capabilities that caller may use, each tagged
kind (verb|recipe) + tier (+ platforms for recipes).

FAIL-CLOSED (security, mirrors mios_pdp.resolve_ceiling): a capability whose tier
is unknown is NEVER included, and an unknown ceiling admits NOTHING. Tiers are
ascending privilege (read < write < interactive); a capability is admitted iff
its tier-rank <= the ceiling's tier-rank AND the ceiling is itself a known tier.

server.py owns: reading the SSOT sections, resolving the caller's ceiling via
mios_pdp, choosing the host platform, and the generative-refusal (LLM) layer that
WS-2 also calls for. This module owns the deterministic, testable projection.

<!-- mios-src:5f9d3c8c60c1 from usr/lib/mios/agent-pipe/mios_pipe/lifecycle/capreg.py:4-21 -->

### Project ONE RBAC-filtered capability manifest from the verb...

Project ONE RBAC-filtered capability manifest from the verb catalog +
    recipe table + skill set for a caller whose permission ceiling is `ceiling`.
    Each entry: {name, kind: "verb"|"recipe"|"skill", tier, description
    [, platforms][, uses]}.
    Verbs/recipes use `permission` (default "read"); a recipe is dropped when
    `platform` is given and it has no template for it. A SKILL's tier is the max
    over its component verbs (skill_effective_tier) and it is admitted only when
    BOTH that tier is allowed AND every component verb is itself admitted
    (reachability fail-closed -- a skill you cannot fully execute is not offered).
    Deterministic (sorted by kind then name); fail-closed via `allowed`.

<!-- mios-src:d1c8003f0eba from usr/lib/mios/agent-pipe/mios_pipe/lifecycle/capreg.py:100-109 -->

### The structured capability DAG (WS-2): nodes...

The structured capability DAG (WS-2): nodes (verbs|recipes|skills) + edges
    (skill -> the verb/skill each step invokes). Recipes + verbs are leaves; only
    skills have out-edges. Returns {nodes, edges, cycles, dangling}: `cycles` are
    skill->skill reference cycles (a malformed skill set; the manifest fails such
    a skill closed via skill_effective_tier) and `dangling` are step targets that
    are neither a known verb nor a known skill. Pure + deterministic.

<!-- mios-src:fd397e9f3e92 from usr/lib/mios/agent-pipe/mios_pipe/lifecycle/capreg.py:205-210 -->

### mios_manifest -- verb-catalog -> ai/v1 manifest projection...

mios_manifest -- verb-catalog -> ai/v1 manifest projection (WS-A1, the AIOS
SSOT anti-drift layer).

Pure stdlib (tomllib + json). The agent-pipe's _VERB_CATALOG is the live SSOT
for the model-facing verb surface, but there was no COMMITTED, diffable
projection of it -- so the surface could drift from the SSOT silently. This
module projects the catalog into a deterministic manifest object; a CLI writes
it to ai/v1/tools.generated.json and a drift gate runs `--check` (regenerate +
diff) to FAIL when the committed projection no longer matches the SSOT.

registry_kind
=============
The existing ai/v1/tools.json is the file-backed HERMES build-tools registry
(9 tool descriptors pointing at chat-completions-api/responses-api/dispatcher
JSON). It is a DISJOINT namespace from the 100+ mios.toml [verbs.*] (which
project live via MCP /v1/verbs, not a static file). To stop the two being
conflated, manifests carry an explicit registry_kind: "hermes-build-tools" for
tools.json, "verb-catalog" for the generated verb projection.

<!-- mios-src:58782077aa39 from usr/lib/mios/agent-pipe/mios_pipe/lifecycle/manifest.py:4-22 -->

### Self-improvement analysis for #64 (federation +...

Self-improvement analysis for #64 (federation + self-improve loop).

The risky part of "self-improvement" is an agent modifying itself; the safe,
high-value part is HONESTLY SEEING what is going wrong. This module is that safe
part: given the local outcome record (tool_call successes/latencies + peer
reputation), it surfaces concrete, ranked findings ("tool X fails 40% of the
time", "peer Y is unreliable") that a human -- or, later, a gated closed loop --
can act on. Pure functions over plain dicts: no DB, no server import, no I/O.

<!-- mios-src:f7ef37bfb8a1 from usr/lib/mios/agent-pipe/mios_pipe/lifecycle/selfimprove.py:4-12 -->

### mios_selfimprove_act -- pure ACT-half decision core for the...

mios_selfimprove_act -- pure ACT-half decision core for the self-curation loop.

The risky part of self-improvement is an agent MODIFYING itself; the safe,
high-value part is honestly DECIDING whether a candidate change is worth a
human's review. ``mios_selfimprove.analyze`` is the OBSERVE half (what is going
wrong); this is the ACT half's brain: given a candidate change PROPOSAL plus the
scores of the current baseline vs the proposed variant on a held-out eval, it
returns a single accept/reject verdict.

It is deliberately split into three composable decisions, each grounded in the
Autodata "agentic data scientist" loop (arXiv:2606.25996):

1. **Structural isolation (anti-reward-hacking).** Autodata observed its
   self-rewriting agent editing the *weak solver's* prompt to fake a result --
   i.e. tampering with the thing that judged it. The structural defence is to
   make the evaluator / eval-data / lane-config UN-TOUCHABLE by a proposal: a
   proposal may only target a kind in the SSOT *improvable* surface and never one
   in the SSOT *protected* surface, with deny winning. This is enforced BEFORE
   any score is read, so a proposal aimed at the evaluator is rejected outright.

2. **Solver-gap curation.** A training/eval task carries signal only if a strong
   solver beats a weak one on it; a task both lanes pass (trivial) or both fail
   (impossible) is discarded. The light lane is the natural weak solver and the
   heavy/council lane the strong solver -- but this module only consumes the two
   numeric scores, never a model id.

3. **Proof-of-utility.** A proposal is accepted only if it does not regress the
   baseline beyond an SSOT margin (and, when required, strictly improves) -- the
   ``pass^k`` reliability metric from :mod:`mios_bench`. Autodata accepted only
   126/233 of its own proposals; rejecting the non-improving majority is the
   load-bearing mechanism, not optional caution.

Pure functions over plain dicts/numbers: no DB, no server import, no model call,
no I/O. Every threshold, flag, and surface set is supplied by the caller from the
``[selfimprove]`` SSOT section -- this module bakes in no numeric weight, no lane
id, and no English/keyword gate (target membership is structural set membership,
the gap is a numeric verifier signal).

<!-- mios-src:242be2b4e1dd from usr/lib/mios/agent-pipe/mios_pipe/lifecycle/selfimprove_act.py:4-41 -->

### True iff a proposal targeting ``target_kind`` is in the...

True iff a proposal targeting ``target_kind`` is in the improvable surface
    and NOT in the protected surface. DENY WINS: a kind in ``protected`` is refused
    even if it also appears in ``improvable`` (fail-safe, like the HITL resolver
    erring toward blocking) so the evaluator / eval-data / lane-config can never be
    edited by a proposal. Both surfaces come from the caller (SSOT) -- an empty
    improvable surface allows nothing (degrade-closed).

<!-- mios-src:5283289e5765 from usr/lib/mios/agent-pipe/mios_pipe/lifecycle/selfimprove_act.py:51-56 -->

### Validate a proposal's SHAPE + its target isolation. Returns...

Validate a proposal's SHAPE + its target isolation. Returns ``(ok, reason)``.

    A proposal is ``{target_kind, target_id, change, rationale}`` (change/rationale
    are the human-reviewable description -- a diff/tweak + why). Rejected when it is
    not a dict, lacks an identified target, or its ``target_kind`` is not in the
    improvable surface / is in the protected surface (the structural isolation).
    The reason is a stable machine token (not prose) so callers can log/branch on it
    without a keyword match.

<!-- mios-src:2e96b10c7e81 from usr/lib/mios/agent-pipe/mios_pipe/lifecycle/selfimprove_act.py:68-75 -->

### Keep only the DISCRIMINATIVE held-out eval candidates...

Keep only the DISCRIMINATIVE held-out eval candidates (Autodata curation).

    Each candidate carries the two lane scores under ``weak`` and ``strong`` (the
    light vs heavy/council pass-rates on that task). A candidate with no numeric
    pair is dropped (it cannot be judged). The kept set is the held-out eval the
    proof-of-utility scores baseline-vs-proposed on -- so a non-discriminative task
    can never inflate or mask a regression.

<!-- mios-src:9dd396861109 from usr/lib/mios/agent-pipe/mios_pipe/lifecycle/selfimprove_act.py:105-111 -->

### The pass^k reliability score over a held-out eval, via...

The pass^k reliability score over a held-out eval, via :mod:`mios_bench`.
    ``tasks`` = ``[(n_trials, c_correct), ...]`` per task. pass^k ("ALL k repeats
    succeed", tau-bench) is the worst-case reliability number production needs --
    the same metric the skill-promotion gate (task_49) uses, here applied to score a
    variant rather than to promote a skill. Thin wrapper so the ACT module names its
    scoring in its own domain; the math lives in mios_bench (single source).

<!-- mios-src:9035aa3695ff from usr/lib/mios/agent-pipe/mios_pipe/lifecycle/selfimprove_act.py:125-130 -->

### task_66 accept criterion. Returns ``(accept, delta)`` where...

task_66 accept criterion. Returns ``(accept, delta)`` where
    ``delta = proposed - baseline``.

    ACCEPT iff the proposed variant does not regress the baseline beyond ``margin``
    (``delta >= -margin``; ``margin = 0`` => strict non-regression ``proposed >=
    baseline``). When ``require_improvement`` is set, a strict improvement is also
    required (``delta > 0``) -- used where a discriminative eval applies and a
    no-op change should not be queued. Both ``margin`` and ``require_improvement``
    are SSOT-supplied; nothing is baked here.

<!-- mios-src:16e8821b51af from usr/lib/mios/agent-pipe/mios_pipe/lifecycle/selfimprove_act.py:137-145 -->

### THE single ACT verdict, composing isolation +...

THE single ACT verdict, composing isolation + proof-of-utility.

    Order is load-bearing: STRUCTURAL ISOLATION is checked FIRST, so a proposal
    that targets the evaluator / eval-data / lane-config (or anything outside the
    improvable surface) is rejected BEFORE its scores are even consulted -- a
    reward-hacking proposal can never "earn" its way in. Only an isolation-valid
    proposal is then put to the proof-of-utility (pass^k non-regression) gate.

    Returns a verdict dict::

        {accept: bool, reason: <token>, delta: float|None,
         target_kind, target_id}

    ``reason`` is a stable machine token (``isolation_rejected`` / ``regression`` /
    ``accepted``), never prose. ``delta`` is None when the proposal was rejected on
    isolation (it was never scored). Pure + total: it never raises and never
    applies -- queuing/dropping is the caller's job.

<!-- mios-src:be078fa0d95b from usr/lib/mios/agent-pipe/mios_pipe/lifecycle/selfimprove_act.py:156-172 -->

### Anti-fabrication POLISH/VERITY cluster (final-answer...

Anti-fabrication POLISH/VERITY cluster (final-answer fact-check + figure guard).

Extracted verbatim from ``server.py``. Holds the final-pass VERITY fact-check
(``_verity_factcheck``), the deterministic ungrounded-figure output guard
(``_strip_ungrounded_figures``) and the sub-agent answer re-shaper
(``polish_response``). ``server.py`` re-imports every name under its original
alias so the module's public surface is byte-identical.

The model-call constants (REFINE_*/POLISH_*) and the server-side DB/format/store
helpers are injected via :func:`configure` (one-way module boundary -- this
module never imports ``server``); ``_loads_lenient`` and ``_env_grounding`` come
from sibling modules directly.

<!-- mios-src:e58b1580e92d from usr/lib/mios/agent-pipe/mios_pipe/lifecycle/verity.py:4-16 -->

### Generative judge (NO keywords -- operator "NOTHING...

Generative judge (NO keywords -- operator "NOTHING HARDCODED"): is `answer`
    PRIMARILY asking the USER for information it NEEDS to proceed (a clarification /
    missing detail / a choice between options), vs a complete answer or an incidental/
    rhetorical question? If yes, return the SINGLE clearest question to put to the user;
    else ''. The caller gates on a '?' present (cheap structural pre-filter) so this runs
    rarely. Degrade -> '' (no prompt).

<!-- mios-src:2687d0c8001e from usr/lib/mios/agent-pipe/mios_pipe/lifecycle/verity.py:146-151 -->

### Drop sentences whose PRICE ($/C$/US$ + digits) or PERCENT...

Drop sentences whose PRICE ($/C$/US$ + digits) or PERCENT (N%) figures are
    absent from the source material polish was given.

 The recurring 4b-polish failure ('FAILURE'): the final
    pass APPENDS invented specifics -- 'deals as low as $184 ... Skyscanner [3]'
    and 'morning departures ~2% cheaper [1]' -- with SCRAMBLED citations, even
    though _POLISH_SYSTEM forbids it. The prompt rule alone doesn't hold on the
    small model, and verity only checks the INPUT draft, never polish's OUTPUT.
    This is the deterministic output-side guard.

    The `haystack` is the FULL material polish saw -- the raw research AND the
    agents' own findings (+ web sources). So a figure is 'grounded' if ANY
    agent or source produced it; only figures polish INVENTED get stripped.
    This is why a general-knowledge numeric answer (agent says '~120 million
    rods') is safe: the number is in the agents' findings = in the haystack.

    Conservative by design: only $-prices and N%-percentages are policed (NOT
    durations / counts / dates / years); prices match by distinctive >=3-digit
    number presence; percentages must have a matching '<n>%' in the source;
    drops at the SENTENCE level within a line (markdown structure preserved);
    and a fail-safe leaves the answer untouched if it would strip more than half
    the figure-bearing sentences (a sign the grounding capture, not the model,
    is at fault).

<!-- mios-src:ff354421da5a from usr/lib/mios/agent-pipe/mios_pipe/lifecycle/verity.py:268-290 -->

### Polish a sub-agent's raw response into the final...

Polish a sub-agent's raw response into the final user-facing
    answer. Returns the polished string or None on error (caller
    keeps the raw answer).

    When session_id is supplied, the polish prompt receives the
    recent tool_call history as ground truth. The CRITICAL rule in
    _POLISH_SYSTEM tells the model to REWRITE the response when it
 contradicts the tool history (Operator-flagged
    'open nautilus' -> assistant claimed 'The move command failed
    because the destination directory wasn't writable' -- a
    completely fabricated unrelated error).

    `original_user_text` is the operator's ACTUAL last message and is
    the authoritative LANGUAGE anchor. refined_text is a rewrite the
    (all-English) refine prompt can translate to English -- keying
    polish's reply language off it made a Polish question come back in
 English / mixed. Polish answers in the
    language of the original message; refined_text feeds CONTENT only.

<!-- mios-src:de560e3accca from usr/lib/mios/agent-pipe/mios_pipe/lifecycle/verity.py:351-368 -->
