<!-- AI-hint: Manual pages distilled from the source comments of routing, sanitized, each passage anchored to the comment it came from. -->

# routing

### mios_aci -- pure Agent-Computer Interface output normalizer...

mios_aci -- pure Agent-Computer Interface output normalizer (WS-5).

DB-free + stdlib-only so the truncation logic unit-tests in isolation
(sibling-module pattern, like mios_sched / mios_evict / mios_hitl).

The problem: feeding raw tool/terminal output back to a model either saturates
the context window or, with a naive head-only slice (`out[:N]`), DROPS THE TAIL
-- which for command/terminal output is exactly where the error, exit code, or
final result lands. The ACI pattern keeps the most informative ENDS (head AND
tail) and elides the middle with an explicit, anti-fabrication marker, bounding
both line count and char count.

server.py owns the knobs + where this is applied; this module owns the pure
transform.

<!-- mios-src:260300d15f4c from usr/lib/mios/agent-pipe/mios_pipe/routing/aci.py:3-17 -->

### Bound `text` to a context budget by keeping the head AND...

Bound `text` to a context budget by keeping the head AND the tail and
    eliding the middle with a marker. Applies an optional line cap first, then a
    char cap. Returns `text` unchanged when already within budget.

    head_frac in (0,1) splits the kept budget between head and tail; the default
    keeps slightly more head (early context) while preserving the tail (the
    result/error). Degrade-open: any error returns a plain head slice.

<!-- mios-src:513eab2ffc9e from usr/lib/mios/agent-pipe/mios_pipe/routing/aci.py:32-38 -->

### Shared sub-agent completion-call primitive (council...

Shared sub-agent completion-call primitive (council secondaries + DAG nodes).

Extracted verbatim from ``server.py``. ``_call_agent_complete`` is the bounded
dispatch entry point (admission + per-lane semaphores + RR preemption + cost +
chrome strip); ``_call_agent_complete_inner`` is its best-effort non-streaming
/v1 call with the pipe-side secondary tool-loop, KV fork/paging bracket,
outbound auth, source harvest and the P3.2b failover-chain recursion.

The moved bodies are unchanged. ``_endpoint_is_llamacpp`` is imported directly
from its sibling module ``mios_endpoints``; every other server-side symbol the
two functions touch (the lane semaphores, the binding/priority helpers, the
secondary tool-loops, the KV helpers, the ContextVars, the header/trace helpers,
the agent registry and the config scalars) is injected via :func:`configure`
(one-way module boundary -- this module never imports ``server``). ``server.py``
re-imports both names under their original aliases so the public surface stays
byte-identical, and re-injects the agent registry on a live membership reload.

<!-- mios-src:8c690553850c from usr/lib/mios/agent-pipe/mios_pipe/routing/agent_call.py:4-20 -->

### Open the circuit for a REMOTE agent that just failed a...

Open the circuit for a REMOTE agent that just failed a dispatch: mark it
    DOWN in _NODE_LIVE so the next turn prunes it (no repeated inline retries on a
    dead node -- reachability becomes a precondition, retries go off the hot path).
    No-op for local lanes (a transient local error must not strand a core agent for
    the whole TTL). Rejoins automatically when the TTL re-probe finds it back up.

<!-- mios-src:0e149845016e from usr/lib/mios/agent-pipe/mios_pipe/routing/agent_call.py:289-293 -->

### WS-RES-GOV observe-only

WS-RES-GOV observe-only: record one dispatch's energy/$ cost into the
    ledger. No-op unless COST_ACCOUNTING_ENABLE; degrade-open (accounting must
    never break a turn). Token counts come from the tokenizer seam (energy is
    dominated by elapsed x watts; tokens matter only for a remote $/Mtok lane).

<!-- mios-src:65e58443f412 from usr/lib/mios/agent-pipe/mios_pipe/routing/agent_call.py:309-312 -->

### Bounded entry point (/24): concurrent agent dispatches --...

Bounded entry point (/24): concurrent agent dispatches
    -- council secondaries AND DAG-level nodes -- acquire the PER-LANE semaphore
    for the engine/node they actually run on, so distinct hardware (dGPU, CPU,
    iGPU, accelerator, each remote node) all fire CONCURRENTLY and only same-lane
    agents queue. No nested agent calls, so no deadlock. `priority` feeds the
    capacity-aware _admit gate; default None -> lane-derived (_dispatch_priority)
    so slow/remote lanes self-shed under load ('all nodes
    enabled by default').

<!-- mios-src:1b0606db7c89 from usr/lib/mios/agent-pipe/mios_pipe/routing/agent_call.py:381-388 -->

### Best-effort non-streaming /v1 call to a secondary fan-out...

Best-effort non-streaming /v1 call to a secondary fan-out agent.
    Returns (name, text); text='' -> dropped from the merge. A dead or
    absent endpoint (e.g. opencode on the `opencode_gateway` port when not served as /v1) just
    yields '' and is skipped, so fan-out degrades to the live agents.

 CPU-lane offload : a secondary always runs
    CONCURRENTLY with the GPU primary, so if the agent declares a CPU
    twin (a declared CPU/light engine binding -> the mios-llm-light lane) we
    dispatch THAT -- the secondary works on the light iGPU/CPU lane while
    the dGPU stays dedicated to the primary. No twin -> its own endpoint.

    Every lane speaks the OpenAI /v1 surface (MiOS is /v1-only): the call
    posts to {ep}/chat/completions with the thinking channel disabled
    (chat_template_kwargs enable_thinking=False) -- a qwen3 model left on its
    default thinking split dumps its answer into message.reasoning with EMPTY
    content, so a secondary would fold in nothing. Custom gateways (opencode
    on `opencode_gateway`, hermes on `hermes`) share the exact same /v1 path.

 P3.2b AUTO-FAILOVER ('remove SPOFs'): when a
    transport-level failure (unreachable endpoint, non-200, timeout)
    leaves THIS hop empty AND the agent declares a failover_agents chain
    (mios.toml SSOT), retry the SAME body against the next live agent in
    the chain. _failover_depth bounds the recursion + skips already-visited
    names. A semantically-empty answer (model returned content="") DOES NOT
    trigger failover -- the agent succeeded; the council merge handles
    quality. Only TRANSPORT failure flips us into failover.

<!-- mios-src:c61863c05f79 from usr/lib/mios/agent-pipe/mios_pipe/routing/agent_call.py:572-597 -->

### Demand-page this conversation's llama.cpp KV around a...

Demand-page this conversation's llama.cpp KV around a completion: on a
    conversation SWITCH, page the resident one OUT (save=unload) and this one IN
    (restore=load); a same-conversation turn is a no-op (warm in-slot KV). Holds
    a per-(endpoint,slot) lock across the bracket so a concurrent conversation
    can't swap the slot mid-flight. No-op + zero overhead unless paging is on
    AND `ep` is a llama.cpp endpoint with /slots.

<!-- mios-src:6e26c4fdaa5f from usr/lib/mios/agent-pipe/mios_pipe/routing/agent_call.py:949-954 -->

### WS-8

WS-8: fork `src_conv`'s saved llama.cpp KV into a NEW file for `dst_conv`
    so a swarm branch can page in the shared prefix independently. Drives the
    PURE plan from mios_kvfork over the existing _kv_slot_action primitive, under
    the per-(endpoint,slot) lock so a concurrent conversation can't swap the slot
    between the restore and the save. DEFAULT-OFF + degrade-open: returns
    {forked: bool, reason: str} and NEVER raises -- a disabled flag, a non-
    llama.cpp endpoint, a bad request, or a failed slot op all just mean the
    child starts cold (as today). After a successful fork the slot resident is
    the CHILD (it was just saved from the slot), so _KV_RESIDENT is updated to
    keep the demand-pager's bookkeeping honest.

<!-- mios-src:249c2e53c5a1 from usr/lib/mios/agent-pipe/mios_pipe/routing/agent_call.py:993-1002 -->

### Interruptible chunked decode (WS-A12). SINGLE-OWNER of the...

Interruptible chunked decode (WS-A12). SINGLE-OWNER of the global priority
    gate: acquires once, releases once in `finally`, and across a preemption does
    a balanced release->re-acquire (held tracked precisely) so permit accounting
    can never drift. Returns the full assistant text. Degrade-open: ANY failure
    falls back to one completion of the whole budget; the partial is never lost.

<!-- mios-src:f0e0ec90733e from usr/lib/mios/agent-pipe/mios_pipe/routing/agent_call.py:1078-1082 -->

### mios_agentreg -- agent/node registry builders (R3...

mios_agentreg -- agent/node registry builders (R3 strangler-fig extraction).

Verbatim move of the mios.toml [agents.*] / [nodes.*] registry parsers out of the
server.py monolith. Pure config readers + constants come straight from
mios_config; the few server.py runtime symbols these parsers touch
(_is_remote_endpoint, _opt_int_mb, the logger, CATALOG_FAIL_MODE,
NODES_RESEARCH_ONLY) are injected once via :func:`configure` AFTER they are
defined in server.py. server.py keeps the module-load assignment of the result to
_AGENT_REGISTRY and the node-pool injection -- these functions only BUILD the
dict, they never own it.

<!-- mios-src:37e05abb35dc from usr/lib/mios/agent-pipe/mios_pipe/routing/agentreg.py:4-14 -->

### Inject the server.py runtime helpers/flags the registry...

Inject the server.py runtime helpers/flags the registry builders + helpers read.

    Called from server.py possibly MORE THAN ONCE with a partial set: the builders'
    deps are injected as soon as they are defined, while the helpers' deps (the hot
    _AGENT_REGISTRY, _agent_binding / _endpoint_key, the EFFORT_DEFAULT / SWARM_MAX_WIDTH
    scalars and _ROLE_SYSTEM_DIR) are injected later -- once defined -- and _AGENT_REGISTRY
    is re-injected on a live membership reload (it is reassigned there). Each field gates
    on ``is not None`` so a partial call never clobbers an already-injected dep.

<!-- mios-src:332f18ec24ab from usr/lib/mios/agent-pipe/mios_pipe/routing/agentreg.py:47-54 -->

### Fold an agent's bindings into an {engine: {endpoint...

Fold an agent's bindings into an {engine: {endpoint, model}} map.
    Precedence (low -> high): the primary endpoint/model as the agent's HOME
    engine (its lane, or 'gpu'); the legacy cpu_endpoint/cpu_model as
    engines['cpu']; explicit [agents.<name>.engines.<engine>] tables WIN. So
    legacy 2-lane configs keep working unchanged AND any agent can declare a
    binding on any engine. iGPU stays DISTINCT from cpu here (the operator lists
    it as its own engine), though _agent_lane still collapses them for fan-out
    diversity.

<!-- mios-src:413616d20c40 from usr/lib/mios/agent-pipe/mios_pipe/routing/agentreg.py:84-91 -->

### Parse mios.toml [agents.*] sections into a registry dict....

Parse mios.toml [agents.*] sections into a registry dict.
    Returns {name: {endpoint, model, role, default, strengths}}.
    Read at module load + cached -- operator restarts agent-pipe
    to pick up changes (same pattern as ports/security/...).

    Fallback: when the TOML can't be read or has no [agents.*],
    returns a single hermes entry pointing at MIOS_AGENT_PIPE_
    BACKEND so the legacy path still works.

<!-- mios-src:f55f6f03ffff from usr/lib/mios/agent-pipe/mios_pipe/routing/agentreg.py:113-120 -->

### Synthesise ONE canonical research-worker agent PER compute...

Synthesise ONE canonical research-worker agent PER compute NODE from the
 mios.toml [nodes.*] table -- "don't have separate CPU
    1,2,3 / dGPU 1,2,3 replicas -- there should just be a MiOS Modelfile dispatched
    as many times as needed to ANY node(s)".

    ONE canonical brain x N nodes -- not N hand-partitioned per-lane research
    workers, and never a raw base model (a raw base cold-loaded on a CPU-only lane
    was the loadavg runaway). Each [nodes.<name>] declares an endpoint + a CANONICAL
    Modelfile tag (mios-agent on GPU, mios-agent-cpu on CPU/light, mios-igpu) +
    lane; we inject `node:<name>` into the registry with research_only defaulting to
    the SSOT NODES_RESEARCH_ONLY and fanout=true, so
    the EXISTING capacity-aware fan-out / swarm-DAG logic (_pick_fanout_agents /
    _agent_dag_from_tasks, bounded by the P1 admission controller + per-lane / per-
    endpoint semaphores) dispatches the ONE worker brain across the pool by
    capacity. Mirrors the a2a:<pid> synthetic-agent injection.

    Layered read (vendor <- /etc <- ~/.config) via _toml_section so the operator
    overlay adds real REMOTE node endpoints (potato/phone/cluster) without baking
    tailnet IPs into the public vendor file. Degrade-open: no [nodes.*] -> 0 nodes
    injected, registry unchanged. Returns the count injected.

    Per-node fields (all but endpoint optional):
      endpoint    -- OpenAI /v1 URL; EMPTY = inert node, skipped.
      model       -- canonical Modelfile tag the node serves (default mios-agent).
      lane        -- gpu/cpu/igpu/mobile/... (semaphore + fan-out diversity bucket).
      health_gate -- true for a come-and-go remote node (auto-join/drop); local
                     nodes omit it (always live). Defaults true for non-local lanes.
      fanout      -- fan-out opt-out (default true).
      role/job/strengths -- optional metadata; sensible worker defaults applied.
    The light-lane model is additionally force-capped to the micro model at
    dispatch by _cap_cpu_lane_model (belt + suspenders).

<!-- mios-src:ee1a20c489d8 from usr/lib/mios/agent-pipe/mios_pipe/routing/agentreg.py:186-216 -->

### Resolve an agent's COMPUTE LANE -- the distinct hardware it...

Resolve an agent's COMPUTE LANE -- the distinct hardware it runs on:
    'gpu' (the dGPU/4090), 'cpu' (the in-VM CPU), 'igpu' (an iGPU, e.g. the
    Windows llama.cpp node :11436), 'accelerator', or 'mobile' (a client node).
    DISTINCT lanes do NOT contend, so the council fires one agent PER LANE
 CONCURRENTLY and each gets its own _lane_sem ("iGPU
    fires WITH CPU cores as well as the rest of the engines/hardware/nodes").
    Explicit [agents.*].lane wins; else infer from endpoint/model. iGPU is now
    its OWN lane (was collapsed into 'cpu', which queued it behind CPU work).

<!-- mios-src:7e9e22fd90d0 from usr/lib/mios/agent-pipe/mios_pipe/routing/agentreg.py:272-279 -->

### Render the sub-agent roster for the planner as JOBS, not...

Render the sub-agent roster for the planner as JOBS, not fixed roles
 ("no fixed roles -- MiOS-Agents are modelfiles for
    jobs and tools/skills/recipes"). Each agent is described by its `job`
    (mios.toml [agents.<name>].job, SSOT) -- what its Modelfile is BEST at --
    falling back to a blurb derived from role + strengths tags when no job is
    set. Every agent has GLOBAL access to all MiOS verbs/recipes/skills, so the
    planner routes purely by CAPABILITY + compute LANE (to spread work across
    CPU/GPU/iGPU concurrently), never by tool availability. Pulled from
    _AGENT_REGISTRY (mios.toml [agents.*] SSOT).

<!-- mios-src:9b8d6ea95585 from usr/lib/mios/agent-pipe/mios_pipe/routing/agentreg.py:296-304 -->

### Per-role DEVELOPER overlay (OpenAI developer-message...

Per-role DEVELOPER overlay (OpenAI developer-message pattern), layered
    AFTER the /MiOS.md SYSTEM identity. Generated by mios-gen-role-system from the
    SSOT (thin: role + tool-focus pointer + live fleet, ~340 B). Degrade-open to ''
 so a missing/unreadable overlay never breaks dispatch..

<!-- mios-src:e53e0f502244 from usr/lib/mios/agent-pipe/mios_pipe/routing/agentreg.py:324-327 -->

### Collapse a fan-out pool to DISTINCT (endpoint, model)...

Collapse a fan-out pool to DISTINCT (endpoint, model) targets + cap width
 ("all these hardcoded agents" / 16-agent explosion). Several
    pool members -- node-pool synthetics and/or research_only agents -- can resolve
    to the SAME endpoint+model -> N IDENTICAL dispatches = pure redundancy + idle
    thrash. Keep ONE agent per (endpoint, model) so the swarm fans across DISTINCT
    compute targets, not duplicates (model diversity on one endpoint is preserved --
    a different model => a different key). PREFER a node:* synthetic, then a
    first-class agent, over a plain research_only agent for the same target. Then cap to SWARM_MAX_WIDTH.
    Agents with no resolvable endpoint (a2a peers, bespoke gateways) are keyed by
    name so they're never collapsed.

<!-- mios-src:cece60dc3a86 from usr/lib/mios/agent-pipe/mios_pipe/routing/agentreg.py:339-348 -->

### CHAT-COMPLETIONS router-brain (strangler-fig refactor...

CHAT-COMPLETIONS router-brain (strangler-fig refactor capstone).

Extracted VERBATIM from ``server.py``. :func:`chat_completions_logic` is the
per-turn orchestrator that routes a request through the precedence vision ->
client-tools -> OS fast-path -> trivial-chat -> memory/local-state -> native
loop -> multi-task -> council/swarm -> polish, keeping every heuristic, guard
and comment byte-identical. The dispatched responders are imported directly
from their siblings; every server-resident helper/scalar/ContextVar plus the
live verb catalog and agent registry are injected via :func:`configure` under
their exact original names (one-way boundary -- this module never imports
``server``). ``server.py`` keeps the route + ``chat_completions`` handler as a
thin wrapper reaching this logic through ``sys.modules`` so the importable
surface stays byte-identical.

<!-- mios-src:97d94589de0e from usr/lib/mios/agent-pipe/mios_pipe/routing/chat.py:4-17 -->

### Inject server-side deps under their EXACT original names...

Inject server-side deps under their EXACT original names (one-way boundary).

    Called from ``server.py`` after every injected symbol is defined; re-called by
    ``_reload_membership`` with ``_AGENT_REGISTRY`` on a live agent add/drop. Each
    keyword equals the module global it sets; unknown keys are ignored.

<!-- mios-src:9a804c054c4c from usr/lib/mios/agent-pipe/mios_pipe/routing/chat.py:286-291 -->

### Cap each system-prefix block for a SLOW lane ("add per-lane...

Cap each system-prefix block for a SLOW lane ("add
    per-lane context trimming") so a slow-prefill node (iGPU / phone / remote
    accelerator) finishes within its read budget instead of being abandoned
    mid-compute by the big ~7K pipeline web-research block. The gist survives
    (top stories / top RAG hits lead each block); the tail is dropped. gpu + cpu
    (local) keep the FULL prefix. Returns the list unchanged for a fast lane.

<!-- mios-src:a3e8a0e9c572 from usr/lib/mios/agent-pipe/mios_pipe/routing/chat.py:330-335 -->

### Generate the conversational reply for an intent=chat turn....

Generate the conversational reply for an intent=chat turn.

    Separate from refine because the JSON classifier reliably tags chat
 but does NOT reliably emit a `reply` field (operator test
    greetings classified chat with reply=None -> the turn fell through to
    Hermes, which then tried a nonexistent 'chat' verb). think=False on
    the micro lane; plain prose, GENERATED in the user's language (never
    a canned/hardcoded string).

<!-- mios-src:a680be076e74 from usr/lib/mios/agent-pipe/mios_pipe/routing/chat.py:384-391 -->

### Generative knowledge-gap judge ("use web tools for...

Generative knowledge-gap judge ("use web tools for
    knowledge gaps EVERY TURN"; NO keyword lists). For a LOCAL-STATE turn, decide
    whether FULLY answering ALSO requires facts that exist only OFF this machine --
    published/theoretical specs, benchmarks, capabilities, ratings, reviews, prices,
    or whether an installed version is the latest. Inspecting the machine yields its
    own identity/state (which GPU/CPU/app it HAS, live usage) but NOT such external
    facts, so a small model collapses "the theoretical specs of MY GPU" to local-only
    and then DROPS or FABRICATES the external half. A focused yes/no (constrained
    enum, thinking-off) is far more reliable than asking the big refine call to juggle
    local+web. True only on a confident yes; degrade-CLOSED (error/None -> False =
    unchanged pure-local behaviour, so 'what's open'/'list my games' never web-search).

<!-- mios-src:6650d49a0b9d from usr/lib/mios/agent-pipe/mios_pipe/routing/chat.py:508-518 -->

### Write one row per refined multi-task entry to the CANONICAL...

Write one row per refined multi-task entry to the CANONICAL pg `kanban`
    table. Returns the same list augmented with `hermes_task_id` so the
    dispatcher + polish can refer to each row by id.

    WS-A3: this was the legacy `kanban_shadow` shadow-queue, which silently
    no-op'd once the legacy backend (:8000) was retired (and whose pg mirror targeted a
    `kanban_shadow` table that doesn't exist) -- so the multi-task queue was
    invisible. It now upserts the canonical pg `kanban` (id/title/status/detail
    jsonb) via a PARAMETERIZED statement (psycopg binds values; never spliced),
    giving every agent a single pg-visible queue. Hermes (or whichever sub-agent
    picks up a task) syncs its native kanban entry back via the existing path.

<!-- mios-src:55eb2de9c647 from usr/lib/mios/agent-pipe/mios_pipe/routing/chat.py:561-571 -->

### Aggregate-budget admission for a NEW turn. Returns...

Aggregate-budget admission for a NEW turn. Returns (allowed, reason).

    HARD-HALTS (allowed=False) when the conversation OR the autonomous-source
    token ceiling is already exhausted within the window, or when the concurrent
    autonomous in-flight cap is reached. On ADMIT it debit-on-admits a
    conservative per-turn estimate to both relevant buckets and (for an
    autonomous turn with a turn_token) registers the turn in-flight -- so the
    NEXT turn for an exhausted bucket is refused, which is the runaway tripwire
    (it stops the SOURCE re-firing). DEGRADE-OPEN: any error -> allowed.

    The check is BEFORE this turn's real tokens are known; the rolling window
    ages the estimate out, so the ceiling bounds the RATE of turns per window.

<!-- mios-src:8ba7b380b78d from usr/lib/mios/agent-pipe/mios_pipe/routing/chat.py:688-699 -->

### Drop an autonomous turn's in-flight token (best-effort...

Drop an autonomous turn's in-flight token (best-effort; degrade-open).
    Idempotent. The autonomous turn registers in-flight in _budget_admit; this
    is the PROMPT release for paths that have a clean terminal point. The
    leak-proof backstop is _budget_prune_inflight (TTL): the streaming path
    returns its generator BEFORE the turn truly ends, so there is no single
    reliable removal point in the giant handler -- the TTL guarantees no slot
    leaks even when no explicit release fires.

<!-- mios-src:f6dfbf590edb from usr/lib/mios/agent-pipe/mios_pipe/routing/chat.py:736-742 -->

### OpenAI Responses API (Tier-2, additive). A THIN facade...

OpenAI Responses API (Tier-2, additive). A THIN facade: translates the
    Responses request to a chat/completions call against THIS server's own full
    pipeline (self-proxy -> reuse refine/route/swarm/polish, no duplication), then
    reshapes the answer into the Responses items model. /v1/chat/completions is
    untouched. Minimal v1: text/message `input` -> one output_text message item +
    usage; `instructions` -> a system message. Streaming/items/hosted-tools TODO.

<!-- mios-src:d1292eac08c0 from usr/lib/mios/agent-pipe/mios_pipe/routing/chat.py:1434-1439 -->

### Stage-1 of the domain router

Stage-1 of the domain router: classify the query into ONE [routing.domains]
    label via a constrained enum (response_format json_schema), THINKING-OFF
    (llama.cpp #20345 silently drops the grammar when thinking is on). Returns the
    validated domain, or None to fall through to the FULL surface (router off / no
    domains / classify error / out-of-enum result). We VALIDATE the label in code
    and never trust HTTP 200 alone (fail-open #19051).

<!-- mios-src:073a9eccad06 from usr/lib/mios/agent-pipe/mios_pipe/routing/classify.py:129-134 -->

### Council diversity gate + aggregation bypass (task_47 GAP-1 /...

Council diversity gate + aggregation bypass (task_47 GAP-1 / task_48 GAP-2).

The council/swarm fan-out produces ``k`` responses that are then handed to a
final aggregator LLM (``polish_response`` in :mod:`mios_pipe.routing.swarm`).
Two failure modes this module addresses, BOTH riding the 768-d nomic embeddings
that already exist on that path (no extra model calls, no per-pair calls):

* **task_47 (RouteMoA input diversity).** An echo-chamber council -- several
  near-identical responses -- wastes the aggregator's context and degrades
  synthesis. :func:`select_diverse` prunes the inputs to a semantically diverse
  subset: a lowest-mean-similarity seed, then minimax-orthogonal expansion; any
  candidate whose similarity to the selected set exceeds ``diversity_threshold``
  is redundant and is replaced by the next most-orthogonal candidate (dropped
  when even the most-orthogonal remaining candidate is over threshold).

* **task_48 (MOSAIC confidence-aware bypass).** When the whole council converges
  (every pairwise cosine exceeds ``aggregator_bypass_threshold``) the expensive
  aggregator call adds nothing. :func:`should_bypass` detects that; the caller
  then ships the highest-confidence individual response (:func:`medoid_index`,
  the consensus medoid) and skips the aggregator LLM.

The decision is pure cosine geometry -- no hand-coded scoring weight, no keyword
or language gate. Both gates default OFF (degrade-open); with both off nothing
here runs and the synthesis path is byte-identical. This module never imports
``server`` (one-way boundary); the cosine metric is the single SSOT one shared
with the verb-retrieval cache in :mod:`mios_toolsearch`.

<!-- mios-src:786379e6ddc5 from usr/lib/mios/agent-pipe/mios_pipe/routing/council_diversity.py:4-30 -->

### task_47 RouteMoA input-diversity selection. Returns the...

task_47 RouteMoA input-diversity selection. Returns the SELECTED indices
    (a subset of ``range(len(vectors))``) of the council responses to hand the
    aggregator:

      * seed ``i0 = argmin_i mean_{j!=i} S_ij`` -- the most peripheral response
        (lowest mean similarity to the rest);
      * expand by minimax: repeatedly add the remaining candidate whose MAXIMUM
        similarity to the already-selected set is smallest (the most orthogonal);
      * a candidate whose max-similarity to the selected set exceeds ``threshold``
        is redundant -- it is passed over for the next most-orthogonal candidate;
        once even the most-orthogonal remaining candidate is over threshold every
        remaining response is a near-duplicate of the set and they are dropped.

    The ranking is purely the cosine geometry -- no hand-coded weight. With <=1
    response there is nothing to diversify (returns all indices).

<!-- mios-src:6b04ebc4b812 from usr/lib/mios/agent-pipe/mios_pipe/routing/council_diversity.py:82-96 -->

### Index of the highest-confidence individual response: the...

Index of the highest-confidence individual response: the medoid -- the
    response with the HIGHEST mean cosine similarity to the others, i.e. the one
    most representative of the converged council. When the bypass precondition
    holds every candidate is near-identical, so this is a principled, weight-free
    choice of the single response to ship instead of the aggregator's output.

<!-- mios-src:b8c5bbf3aa40 from usr/lib/mios/agent-pipe/mios_pipe/routing/council_diversity.py:138-142 -->

### Apply the task_47 diversity gate + task_48 aggregation bypass...

Apply the task_47 diversity gate + task_48 aggregation bypass over the council
    response ``nodes`` (each a dict carrying ``output_key`` text). Embeds every
    response's text ONCE via ``embed_one`` (the 768-d nomic vectors) and REUSES
    those vectors for both gates -- zero per-pair model calls.

    Returns ``(selected_nodes, bypass)`` where:
      * ``selected_nodes`` -- the (possibly diversity-pruned) nodes for the
        aggregator (unchanged when the diversity gate is off / nothing pruned);
      * ``bypass`` -- ``None``, or ``{"node", "mean_similarity", "council_size"}``
        when the council converged and the aggregator LLM must be SKIPPED (task_48).

    Convergence (bypass) takes precedence over diversity pruning -- a converged
    council needs neither aggregation nor trimming. Degrades OPEN: with both gates
    off, <2 nodes, no embedder, or any missing embedding it returns the nodes
    unchanged with ``bypass=None`` (behaviour identical to gates-off).

<!-- mios-src:39854be0b856 from usr/lib/mios/agent-pipe/mios_pipe/routing/council_diversity.py:159-173 -->

### mios_cua -- unified computer-use perceive->act->verify loop...

mios_cua -- unified computer-use perceive->act->verify loop (WS-8).

A VLM-grounded computer-use agent runs a closed loop: PERCEIVE (screenshot ->
the VLM locates UI / plans the next action) -> ACT (dispatch a click/type/key
verb) -> VERIFY (screenshot -> the VLM checks whether the goal state holds) ->
repeat until the goal is reached or a budget/stall guard fires. Before WS-8 the
pieces existed (the Holo1.5 VLM lane + windows_desktop_* / linux_desktop_*
verbs) but were never unified into one cross-platform loop.

This module is the PURE control layer:
  * resolve_verb()      -- ONE logical action vocabulary -> the right verb per
                           platform (Windows host vs in-VM Linux desktop),
                           fail-closed so a caller never invents a verb.
  * loop_status()       -- the terminal decision after each VERIFY: goal reached
                           / out of step budget / stalled (no screen change) /
                           keep going.
  * parse_verify_verdict() -- interpret the VLM's verify answer; FAIL-SAFE: an
                           unparseable verdict is NOT-done, so the loop can never
                           falsely declare success (it just runs to the budget).

server.py owns the I/O (the VLM call, the verb dispatch, the screenshots) +
the flag-gating; this is the deterministic, unit-testable policy.

<!-- mios-src:84a46ce41022 from usr/lib/mios/agent-pipe/mios_pipe/routing/cua.py:4-26 -->

### Inject the computer-use route + I/O-loop deps under their...

Inject the computer-use route + I/O-loop deps under their EXACT original
    names: the CUA_ENABLE gate flag, the verb-dispatch chokepoint
    (_dispatch_mios_verb_inner), the shared httpx client (_get_client), the vision
    backend-failure gate (_vision_backend_failed), and the config constants the
    loop reads (_BACKEND_KEY / VISION_MODEL / VISION_ENDPOINT / CUA_MAX_STEPS).
    Each field is gated on ``is not None`` (an empty backend key or a False flag is
    a legitimate value), so an unset keyword leaves the prior binding. The loop
    (_cua_loop) is module-local now, so it is NOT injected back.

<!-- mios-src:d0e4fa859cbc from usr/lib/mios/agent-pipe/mios_pipe/routing/cua.py:58-65 -->

### Interpret a VLM verify answer into {done: bool, reason...

Interpret a VLM verify answer into {done: bool, reason: str}. Accepts a
    JSON object {"done": ..., "reason": ...} anywhere in the text, else the
    sentinels GOAL_REACHED / DONE=YES / NOT_DONE (case-insensitive).

    FAIL-SAFE: anything unparseable -> done=False. The loop therefore NEVER
    falsely declares the goal reached on a malformed/ambiguous verify; it simply
    keeps working until the step budget (the operator's 'never claim success you
    didn't achieve' rule, enforced structurally).

<!-- mios-src:6e46b0debac6 from usr/lib/mios/agent-pipe/mios_pipe/routing/cua.py:146-153 -->

### WS-8 perceive->act->verify computer-use. Body: {goal...

WS-8 perceive->act->verify computer-use. Body: {goal, platform?
    (windows|linux), max_steps?}. Runs the closed VLM loop and returns the trace
    {status, reached, steps[...]}. DEFAULT-OFF (MIOS_CUA_ENABLE): returns a clear
    disabled notice until the operator opts in AND a GPU VLM is loaded. Never
    claims a goal it did not verify (fail-safe in mios_cua).

<!-- mios-src:2c799238d125 from usr/lib/mios/agent-pipe/mios_pipe/routing/cua.py:213-217 -->

### Pull a screenshot PNG path out of a screenshot verb's...

Pull a screenshot PNG path out of a screenshot verb's result. The
    *_desktop_screenshot verbs write a PNG + name it in stdout; degrade-open ->
    None when no path is found.

<!-- mios-src:eb73a4780520 from usr/lib/mios/agent-pipe/mios_pipe/routing/cua.py:255-257 -->

### Take a screenshot via the platform's verb, read the PNG...

Take a screenshot via the platform's verb, read the PNG, return
    (data_uri, raw_observation). Degrade-open -> (None, ""). The data URI is what
    the VLM 'sees'; the raw observation digest drives stall detection.

<!-- mios-src:639d74e7153d from usr/lib/mios/agent-pipe/mios_pipe/routing/cua.py:264-266 -->

### One VLM call returning the model's parsed JSON object (a...

One VLM call returning the model's parsed JSON object (a plan or a verify
    verdict). Degrade-open -> {} on any backend/parse failure (the caller's
    fail-safe handles an empty verdict as NOT-done).

<!-- mios-src:a4bd18e9fb36 from usr/lib/mios/agent-pipe/mios_pipe/routing/cua.py:312-314 -->

### Run the perceive->act->verify loop until the VLM verifies...

Run the perceive->act->verify loop until the VLM verifies the goal or a
    budget/stall guard fires. Returns mios_cua.CuaTrace.to_dict(). VLM-gated +
    degrade-open: no vision model / no screenshot -> an honest non-reached stop
    (it never fabricates success).

<!-- mios-src:f60733d1b26a from usr/lib/mios/agent-pipe/mios_pipe/routing/cua.py:435-438 -->

### DAG execution entrypoints (refactor R8). Extracted VERBATIM...

DAG execution entrypoints (refactor R8).

Extracted VERBATIM from ``server.py`` -- the planned-DAG execution brain that
runs a topological DAG of agent/verb nodes with retry, grounding, work-steal
deepen and live per-node emit. The five execute_dag entrypoints
(``_execute_dag_node``, ``_execute_dag_saturated``, ``execute_dag``,
``_execute_dag_bounded``, ``_execute_dag_emitting``) are moved byte-identically;
their later consolidation is a SEPARATE task. ``server.py`` re-imports every name
under its original alias so the module's public surface is byte-identical.

Sibling functions (``_call_agent_complete``, ``_web_research_enrich``,
``_dag_levels``, the SSE node emitters, ``_env_grounding``, ``_action_hash``, the
RBAC filters, ``_toml_section``) are imported directly; every other server-side
symbol they touch (the config scalars, ``_AGENT_REGISTRY``, the ContextVars, the
broker ``dispatch_mios_verb``, the agent-call/scratchpad/db/a2a/worker-tool
helpers) is injected via :func:`configure` (one-way boundary -- this module never
imports ``server``).

<!-- mios-src:f74d2fcf415b from usr/lib/mios/agent-pipe/mios_pipe/routing/dag_exec.py:4-21 -->

### A fast swarm node that finished its primary BEFORE the...

A fast swarm node that finished its primary BEFORE the global barrier (i.e.
    it computed faster than its peers) keeps producing ADDITIONAL, DISTINCT
    coverage -- new angles / items / facets -- until the barrier fires (every
    node's primary done): it does NOT idle. The intent is "wait for ALL nodes to
    complete; the faster lanes just do another pass from ANOTHER facet -- everything
    concurrent, every source every turn". The slowest node trips the barrier and
    never enters here.

    EARLY-EXIT (A8, SSOT [dispatch].deepen_early_exit, default OFF): when enabled,
    each pass first asks the per-node Definition-of-Done judge whether the node's
    CURRENT answer already satisfies its sub-query; if so the node STOPS deepening so
    the heaviest compute is not spent re-answering an already-good node and the freed
    lane lets slower nodes finish sooner. Default off -> runs to the bound (no
    behaviour change). Degrade-open: the judge is bounded by DEEPEN_JUDGE_TIMEOUT_S
    and ANY timeout / error / absent judge falls THROUGH to the deadline-bound loop
    -- it can only ever STOP early on a clean 'satisfied', never under-compute.

 DETAIL-FILL ("also can loop to gather data in detail-fill
    passes"): when DEEPEN_FETCH is on AND the node carries a web-capable refined
    plan (a web/news turn), each pass FIRST fetches MORE web data on the facet
    (bounded by DEEPEN_WEB_TIMEOUT_S; the fan-out diversifies sub-queries so each
    pass surfaces fresh stories) and APPENDS the new stories to the shared
    grounding -- so the loop ENRICHES the facts, not just re-reasons them. The
    enriched grounding flows to the final synthesis. A non-web turn (no refined /
    no web hint) just reasons over the grounding in hand (no contention).
    Hard-bounded by DEEPEN_MAX_ITERS + DEEPEN_DEADLINE_S + the barrier.

<!-- mios-src:54837a11a10f from usr/lib/mios/agent-pipe/mios_pipe/routing/dag_exec.py:254-279 -->

### Execute ONE DAG node -- an `agent` delegation OR a `tool`...

Execute ONE DAG node -- an `agent` delegation OR a `tool` verb --
    and return its node_result (standard tool_call shape + node_id + _act).
    READS the shared maps (a snapshot of completed levels) but does NOT
    mutate them; execute_dag merges results after each level so concurrent
    same-level nodes never race on writes. ReWOO #E<id> refs in args (verb)
    or in the prompt (agent) resolve against the completed-level outputs.
    When `frag_q` is supplied (the streaming DAG paths), an agent node STREAMS
    its reasoning LIVE onto that queue as ("SF", name, fragment) events so the
    emitting wrapper renders the agents' actual thinking into the dropdown --
    not just engage/done status pings (operator: 'no thinking blocks').

<!-- mios-src:3e398e5884d3 from usr/lib/mios/agent-pipe/mios_pipe/routing/dag_exec.py:497-506 -->

### CONTINUOUS READY-QUEUE DAG executor ("nothing in the...

CONTINUOUS READY-QUEUE DAG executor ("nothing in the
    pipeline is idle until synthesis"). A node dispatches the MOMENT its own deps
    finish -- NOT at a topological-LEVEL barrier -- so a fast node's lane picks up
    the next ready node immediately instead of idling for the slowest node in its
    level. REAL concurrency is bounded by the global/endpoint/lane semaphores in
    _call_agent_complete (saturate to capacity, never over). A finished AGENT node
    deepens until the GLOBAL barrier (all primaries done) so no lane idles while
    the swarm finishes. Same recording / emit / ReWOO-#E / dedup semantics as the
    level path; dependents of a FAILED node are SKIPPED (independent branches keep
    running -- more robust than the level path's whole-DAG fail-fast). Gated by
    SWARM_SATURATE.

<!-- mios-src:66cc141b875c from usr/lib/mios/agent-pipe/mios_pipe/routing/dag_exec.py:682-692 -->

### Execute the DAG. SWARM_SATURATE -> the continuous...

Execute the DAG. SWARM_SATURATE -> the continuous ready-queue
    (_execute_dag_saturated, "nothing idle until synthesis"); else the legacy
    concurrent topological-LEVEL path below (the proven fallback).

    LEVEL path: every node whose deps are satisfied runs in PARALLEL
    (asyncio.gather), so independent
    sub-tasks -- including agent-delegation nodes routed to DIFFERENT sub-
    agents -- run concurrently across the CPU + GPU lanes (operator
 "separate prompts per refinement step -> sub-agents...
    concurrent Compute"). A level only starts once all earlier levels
    finish, so ReWOO #E<id> refs always resolve. Reflexion-retries failed
    verb nodes; fail-fast when a level has an unrecoverable failure.
    Returns aggregate {success, node_results[], summary}.

<!-- mios-src:7878ed757105 from usr/lib/mios/agent-pipe/mios_pipe/routing/dag_exec.py:856-868 -->

### Non-streaming execute_dag with a hard TURN_DEADLINE_S...

Non-streaming execute_dag with a hard TURN_DEADLINE_S wall-clock backstop
 (runaway fix) PLUS client-disconnect cancellation (T21,
) when `request` is provided: a non-streaming caller that hangs up
    stops the swarm IMMEDIATELY rather than churning DAG+deepen to the deadline.
    The STREAMING path self-bounds on disconnect in _execute_dag_emitting; this
    closes the non-streaming gap. On timeout/disconnect, wait_for/cancel stops the
    executor -> _execute_dag_saturated's CancelledError handler cancels in-flight
    node tasks so they stop dispatching. Returns a partial result. Degrade-open:
    request=None or REQUEST_CANCEL_ENABLE=false -> deadline-only (unchanged).

<!-- mios-src:2b3e11b58b95 from usr/lib/mios/agent-pipe/mios_pipe/routing/dag_exec.py:1007-1015 -->

### Run execute_dag while LIVE-yielding per-node endpoint...

Run execute_dag while LIVE-yielding per-node endpoint emitter bytes
 ("endpoint emitters for each ai endpoint/node") AND
    the agents' streamed REASONING. Yields ("event", sse_bytes) as each DAG
    node ENGAGES + finishes, then a final ("result", dag_result). Agent nodes
    carry their registry endpoint / lane / model; verb nodes show 'verb
    · <tool>'. Agent nodes also stream their thinking ("SF", name, frag onto
    the shared queue) which is buffered per-agent and checkpoint-flushed as
    reasoning_content deltas -- so the think dropdown shows the facets ACTUALLY
    reasoning, not just engage/done pings (operator: "no thinking blocks
    populating it"). The 0.25s poll lets the drainer notice the DAG finishing
    even if the sentinel is lost to an unexpected raise -- then `await task`
    re-raises it (parity with a plain `await execute_dag`).

<!-- mios-src:64113c46dc5d from usr/lib/mios/agent-pipe/mios_pipe/routing/dag_exec.py:1057-1068 -->

### Pull the most-useful single field out of a JSON-ish blob so...

Pull the most-useful single field out of a JSON-ish blob so a
    ReWOO bare `#E<id>` ref doesn't paste the whole multi-line dump
    into a downstream arg. Trace failure: mios_apps returns NDJSON
    (one app per line). #En1 substituted the FULL stdout into
    open_app(name=...), producing args like
    `{"category":"linux-flatpak","name":"devel",...}\n{"...":"..."}\n`
    which mios-launch can't resolve to anything.

    Resolution order:
      1. Single JSON object -> prefer `name`, then `launch`, then
         `title`, then `id`, then `path`, then first string field.
      2. NDJSON (one object per line) -> use the FIRST object's
         best field via the same rule.
      3. Not JSON -> return the first line, capped at 1024 chars
         (matches the prior naive behavior for plain-text upstream).

<!-- mios-src:82521d11cd42 from usr/lib/mios/agent-pipe/mios_pipe/routing/dag_exec.py:1174-1188 -->

### ReWOO-style substitution

ReWOO-style substitution: replace `#E<node-id>` tokens in arg
    values with the captured stdout of the upstream node. Two forms
    supported:

                           the upstream output (handles JSON objects
                           + NDJSON streams; falls back to first line
                           for plain text). Caps at 1024 chars.
                           JSON output. Use this when the planner
                           knows which field it needs (e.g.,
                           open_app(name='#En1.launch') to use the
                           launch line from a mios_apps row).

    Per ReWOO (Xu et al. 2023): the planner emits #E<id> placeholders
    and the worker substitutes them with actual outputs at execute
    time. Removes the per-step LLM re-plan that other frameworks
    need.

    Only handles string args (the common case for shell verbs).
    Object / list args pass through unchanged.

<!-- mios-src:9cfa00c17b14 from usr/lib/mios/agent-pipe/mios_pipe/routing/dag_exec.py:1228-1246 -->

### AIOS gap5 L2

AIOS gap5 L2: dynamically size num_ctx to FIT the actual prompt+tool weight.
    FAST lanes: raise toward WORKER_TOOL_CTX_MAX only as needed (never shrink, never
    trim the contract). SLOW lanes: leave pinned at want_ctx (Layer 1 already shrank
    their surface). Returns num_ctx. Degrade-open: CTX_FIT off / any error ->
    want_ctx (today's static value).

<!-- mios-src:64e9b709a8ea from usr/lib/mios/agent-pipe/mios_pipe/routing/dag_exec.py:1288-1292 -->

### True only for a node on a FAST lane (DEEPEN_LANES...

True only for a node on a FAST lane (DEEPEN_LANES: dGPU/accelerator) -- the
    work-stealing lanes that do EXTRA coverage passes until the barrier (operator
 "dGPU and accelerators that compute faster should just do another
    pass from another facet"). A SLOW lane (CPU/iGPU/phone) does its ONE grounded
    pass and then its primary trips the barrier for the fast lanes; it must NOT
    deepen (it can barely finish one pass) -- the runaway/abandon source the
    operator hit with local-cpu.

<!-- mios-src:cf5656338a51 from usr/lib/mios/agent-pipe/mios_pipe/routing/dag_exec.py:1305-1311 -->

### No-op on the /v1 plane. A llama.cpp / llama-swap generation...

No-op on the /v1 plane. A llama.cpp / llama-swap generation ABORTS the moment
    the client connection closes (unlike a legacy un-abortable backend), so a
    cancelled / deadline-exceeded turn releases the lane on its own -- there is no
    /v1 model-unload primitive to call and none is needed. Kept as a gated hook
    (RUNAWAY_REAP_ENABLE) should a future backend ever need an explicit reap; never
    raises into a turn.

<!-- mios-src:2244d79395b0 from usr/lib/mios/agent-pipe/mios_pipe/routing/dag_exec.py:1319-1324 -->

### Perform pre-execution validation and Kahn topological...

Perform pre-execution validation and Kahn topological classification over plan nodes.

    Checks for:
    1. Duplicate node IDs
    2. Self-loop dependencies
    3. Dangling dependencies (referencing non-existent node IDs)
    4. Cycles (via Kahn's algorithm)
    5. Orphan roots (graphs with nodes but no valid entry point)

    Returns a DAGValidationVerdict containing classification and a sanitized remediation order.

<!-- mios-src:623923225a95 from usr/lib/mios/agent-pipe/mios_pipe/routing/dag_validate.py:36-46 -->

### Deliberative Collective Intelligence (DCI) vocab + critic +...

Deliberative Collective Intelligence (DCI) vocab + critic + convergent flow.

Extracted verbatim from ``server.py``. Holds the DCI epistemic-act vocabulary +
JSON schema, the four persona system prompts, the single-persona B.1 critic
(``dci_critic_pass``), the 4-persona B.2 convergent flow (``run_dci_flow`` /
``_dci_call_persona``) and the B.3 conditional-escalation chain
(``critic_then_maybe_flow``). ``server.py`` re-imports every name under its
original alias so the module's public surface is byte-identical.

Config constants come from ``mios_config``; the server-side DB-event helpers and
the outbound-auth header stamper are injected via :func:`configure` (one-way
module boundary -- this module never imports ``server``).

<!-- mios-src:9204f1c1d38b from usr/lib/mios/agent-pipe/mios_pipe/routing/dci.py:4-16 -->

### Run the DCI-CF convergent flow on (user_text, envelope)....

Run the DCI-CF convergent flow on (user_text, envelope).
    Returns a structured deliberation result:
      {decision: <Integrator's final recommend act>,
       rounds: [[act_per_persona, ...], ...],
       dissents: [<tension acts>],
       converged: bool}
    Always returns -- the bounded loop guarantees termination.

<!-- mios-src:52c3686dedfd from usr/lib/mios/agent-pipe/mios_pipe/routing/dci.py:297-303 -->

### Chain B.1 critic -> conditional B.2 flow. Fire-and-forget...

Chain B.1 critic -> conditional B.2 flow. Fire-and-forget
    via _db_fire so the dispatch reply isn't delayed.

    Phase B.3 flow:
      1. Run dci_critic_pass (single-persona Challenger).
      2. If the act is in (challenge, ask) AND confidence is high,
         escalate to run_dci_flow (4 personas, bounded loop).
      3. If the flow surfaces unresolved dissent, write a tainted
         tool_call row keyed to the session so any subsequent
         high-privilege verb in this session gets firewalled.

<!-- mios-src:26ddc9d86479 from usr/lib/mios/agent-pipe/mios_pipe/routing/dci.py:421-431 -->

### Post-dispatch critic

Post-dispatch critic: invokes the DCI Challenger persona on
    the (user_text, envelope) pair and emits ONE typed epistemic
    act. Returns the parsed act dict, or None on any error.

    Fire-and-forget at the caller's discretion -- the chat reply is
    already rendered by the time this runs. Event row
    written automatically (kind=dci_act, source=mios-agent-pipe).

<!-- mios-src:ef943498b2b0 from usr/lib/mios/agent-pipe/mios_pipe/routing/dci.py:477-484 -->

### mios_dispatcher -- the pure mode Dispatcher (WS-A11/WS-3...

mios_dispatcher -- the pure mode Dispatcher (WS-A11/WS-3, Stage 1c).

The "run" half of the AIOS Router/Dispatcher split. mios_router classifies a
refined plan into a RouteDecision(mode, ...); this Dispatcher routes that mode to
a registered async handler. Handlers are injected by server.py (the concrete
chat / dispatch / multi_task / dag / agent execution paths, lifted from the
current inline cascade), so the routing table is pure + testable while the heavy
bodies stay where they are until Stage 2 rewires them behind this seam.

<!-- mios-src:5d320ab2c8f9 from usr/lib/mios/agent-pipe/mios_pipe/routing/dispatcher.py:4-12 -->

### Run the decision via its mode handler. Falls back to the...

Run the decision via its mode handler. Falls back to the default-mode
        handler for an unknown/missing mode; raises KeyError if neither exists
        (a fail-loud wiring error, not a runtime degrade).

<!-- mios-src:20f5579b3de8 from usr/lib/mios/agent-pipe/mios_pipe/routing/dispatcher.py:35-37 -->

### Council/swarm fan-out agent SELECTION -- model-driven...

Council/swarm fan-out agent SELECTION -- model-driven relevance, no hardcoded scorer.

Extracted from ``server.py`` (R3) and de-hardcoded. ``_pick_fanout_agents``
returns the secondary ``(name, cfg)`` agents to dispatch concurrently with the primary.

Three paths, NONE of which uses a hand-coded relevance heuristic:
  * ``force_council`` -- engage every eligible non-primary live agent (explicit swarm).
  * ``mode == "council"`` -- equal-weight: every eligible agent, sub-lane-diverse, capped.
  * default -- **model-driven**: the micro-model picks the relevant specialists from the
    refined plan + each eligible agent's published card. Degrades open to council-equal-weight.

The module is pure of ``server.py`` (one-way boundary). The live registry, dispatch
config, and the depth/lane/dedup/admission helpers are injected via :func:`configure`;
the relevance model-call is the module's own ``httpx`` POST to the SSOT micro endpoint
(same pattern as ``mios_refine``/``mios_dci``). ``server.py`` re-imports
``_pick_fanout_agents`` under its original alias and ``await``\s it (surface byte-identical).

<!-- mios-src:ed220f396db2 from usr/lib/mios/agent-pipe/mios_pipe/routing/fanout.py:4-20 -->

### Inject the server.py registry/config + helpers/constants...

Inject the server.py registry/config + helpers/constants the selector uses.

    Unchanged signature from the pre-de-hardcode version -- the model-driven
    relevance call uses the module's own httpx to the SSOT micro endpoint
    (mios_config._MICRO_MODEL/_MICRO_ENDPOINT) + the injected ``dispatch_cfg``
    for the mode + timeout, so no new injected dependency is required.

<!-- mios-src:098aa28a1137 from usr/lib/mios/agent-pipe/mios_pipe/routing/fanout.py:61-67 -->

### The eligible secondary pool

The eligible secondary pool: every registered agent except the primary that
    is not opted-out, is live (OUTAGE prune), and is research-OK. NO relevance
    scoring -- this is the deterministic membership filter only. ``research_only``
 agents/nodes join ONLY on a research/deep turn (runaway fix:
    keep the research workers OUT of an everyday turn so a trivial prompt
    doesn't cold-load the whole pool at once).

<!-- mios-src:6c436bf18ced from usr/lib/mios/agent-pipe/mios_pipe/routing/fanout.py:116-121 -->

### Equal-weight council selection over the eligible pool...

Equal-weight council selection over the eligible pool: sub-lane-diverse
    first (a CPU agent parallelises a GPU primary at zero dGPU cost -- a hardware
    concurrency concern, NOT a relevance heuristic), endpoint/model-deduped, capped
    at ``want``. This is the degrade-open path when model selection is off/unreachable
    and the body of council mode -- it engages secondaries (never primary-only) while
    the cap bounds width. No hand-coded relevance scoring.

<!-- mios-src:b44fa075e897 from usr/lib/mios/agent-pipe/mios_pipe/routing/fanout.py:135-140 -->

### A federated peer's FULL published AgentCard ``skills[]``...

A federated peer's FULL published AgentCard ``skills[]`` rendered as compact
    capability lines -- each skill's own ``name`` + ``description`` + ``tags``. This
    is the RICH advertised surface an A2A peer publishes (stored on the synthetic
    peer registry entry as ``card_skills``), NOT the collapsed strength-token id list
    the peer registration also keeps; routing on it lets the model reason over what
    the peer actually claims to do. Empty for a local ``[agents.*]`` agent (no
    published card_skills) -- purely additive to the existing card corpus.

<!-- mios-src:39664e1ba1dd from usr/lib/mios/agent-pipe/mios_pipe/routing/fanout.py:150-156 -->

### A compact, SSOT-sourced card for the relevance model: the...

A compact, SSOT-sourced card for the relevance model: the agent's OWN
    declared role / strengths / A2A skill-tags ([agents.*] in mios.toml + the
    AgentCard the peer publishes). No hardcoded topic text -- the card IS the
    capability surface the model reasons over.

    FED-G7 (task_51, flag-gated): when ROUTE_ON_CARD_SKILLS is set, a federated peer's
    FULL published skills[] (name/description/tags) are folded in alongside the
    strength tokens so the model routes on the advertised skill, not just the token
    proximity. OFF -> byte-identical to the strength-token-only card.

<!-- mios-src:e413f9fc449f from usr/lib/mios/agent-pipe/mios_pipe/routing/fanout.py:182-190 -->

### MODEL-DRIVEN relevance

MODEL-DRIVEN relevance: ask the micro-model which of the eligible agents are
    worth engaging concurrently for this plan. Returns the chosen candidate names
    (subset, capped), or ``None`` to signal degrade-open (selection off, no candidates,
    timeout, unparseable). Pure generative selection -- no scoring, no keyword map.
    The model sees the refined plan + each agent's own card and returns a JSON name
    array; we validate the names against the candidate set.

<!-- mios-src:ac1e2289c664 from usr/lib/mios/agent-pipe/mios_pipe/routing/fanout.py:229-234 -->

### Pick SECONDARY (name, cfg) agents to run CONCURRENTLY...

Pick SECONDARY (name, cfg) agents to run CONCURRENTLY alongside the chosen
 primary -- 'a couple at a time' + 'self-delegate to CPU
    concurrently' + 'make sure hermes isn't always the only dispatched agent'.

 Relevance is MODEL-DRIVEN (the old role/strengths-token
    overlap scoring + magic CPU-lane bonus + ASCII tokenizer was itself a hardcode):
    the micro-model picks the relevant specialists from the refined plan + each
    eligible agent's own card. NO hand-coded scoring/weight/topic map. Degrades open
    to council-equal-weight (all eligible, lane-diverse, deduped, capped) when model
    selection is off/unreachable -- never primary-only, never an unbounded runaway.
    Returns [] when fan-out is disabled / capped at 1 / nothing relevant.

 force_council (SWARM toggle): engage EVERY eligible agent
    this turn, bypassing enable/fanout_max/relevance -- the manual 'full swarm'.

<!-- mios-src:aaf4f5248bf1 from usr/lib/mios/agent-pipe/mios_pipe/routing/fanout.py:296-309 -->

### mios_hopbudget -- hop-budget recursion guard + effort...

mios_hopbudget -- hop-budget recursion guard + effort scaling (WS-4, the AIOS
orchestrator-worker structural-guard layer).

Pure stdlib. The agent-pipe's fan-out can re-enter the gateway over HTTP (a
thin-gateway-as-worker, an A2A peer); a process-local depth counter resets to 0
across that hop -> unbounded recursion. The guard carries the depth + an
agent-id Via chain as headers (RFC 9110 Max-Forwards + Via) and kills a loop the
moment a self-id reappears. These functions are the pure decisions behind that
guard, plus the effort->width scaling that makes orchestration intensity a
first-class function of query complexity rather than a fixed cap.

<!-- mios-src:2872d53f220e from usr/lib/mios/agent-pipe/mios_pipe/routing/hopbudget.py:4-14 -->

### Map an 'effort' level to an orchestration fan-out width in...

Map an 'effort' level to an orchestration fan-out width in [1, cap].
    Accepts a named tier (low|medium|high|max|xhigh) or a 0..1 float (complexity
    score). Unknown/empty -> `base`. This is the first-class knob that scales
    swarm intensity to query complexity instead of a single fixed width.

<!-- mios-src:f586e915fa56 from usr/lib/mios/agent-pipe/mios_pipe/routing/hopbudget.py:62-65 -->

### mios_interop -- 3-projection interop for the MiOS...

mios_interop -- 3-projection interop for the MiOS agent-pipe (WS-11).

Pure stdlib. A capability (verb/recipe/skill) is advertised three ways: the MCP
`tools/list` shape, the OpenAI function shape (both already projected in
server.py), and -- the missing third -- the A2A AgentCard `skills[]` shape so a
federated peer discovers MiOS capabilities over the open A2A standard. This
module renders that A2A shape + a parity view of all three, deterministically.

A2A skill entry (AgentCard.skills[], stable across A2A 0.3/1.0):
  {id, name, description, tags[]}  -- id is the canonical capability key.

<!-- mios-src:98598f6a982f from usr/lib/mios/agent-pipe/mios_pipe/routing/interop.py:4-14 -->

### Generic JSON-grammar salvage for small-model output....

Generic JSON-grammar salvage for small-model output.

Extracted from server.py (modularization). Pure stdlib (re + json) --
NO coupling to the agent-pipe globals, NO schema/field/topic/English knowledge.
This is the FIRST module split out of the 19k-line monolith; keep it dependency-free
so it stays trivially testable and importable.

<!-- mios-src:2350aa2f9f89 from usr/lib/mios/agent-pipe/mios_pipe/routing/jsonsalvage.py:3-9 -->

### Best-effort recovery of a JSON OBJECT from a small model's...

Best-effort recovery of a JSON OBJECT from a small model's NEAR-json output.
    operator binding NO-HARDCODES: this is generic STRUCTURAL repair of the JSON
    grammar -- it knows nothing about the schema, fields, topics, or any English.

    A tiny refine/planner model (qwen3:1.7b) intermittently emits ONE malformed
    token -- an empty value after a colon (`"k":` then `,`/`}`), a trailing comma,
    a // or /* */ comment, a Python True/False/None literal, or a truncated tail --
    and strict json.loads then DISCARDS THE ENTIRE otherwise-perfect object. That
 is the failure: refine produced a flawless trending plan
    (intent=agent, news=true, a clean refined_text) but one empty `inventory_filter`
    field at line 11 made json.loads raise -> the whole plan was dropped -> the
    degraded fallback web-searched "worldwide trends today" (dictionary/shipping
    junk) and punted. Recover the object instead of throwing it away.

    Returns the parsed dict, or None if it genuinely can't be salvaged.

<!-- mios-src:177a458ee298 from usr/lib/mios/agent-pipe/mios_pipe/routing/jsonsalvage.py:19-33 -->

### mios_lanes -- unified inference-lane resolver for the MiOS...

mios_lanes -- unified inference-lane resolver for the MiOS agent-pipe (WS-1, the
AIOS lane-selection layer).

A LANE is a single inference endpoint: ``(id, url, model)``. The resolver is given a
map of lanes and, per ROLE, an ordered PREFERENCE CHAIN of lane ids; ``pick(role)``
returns the first REACHABLE lane in the chain. Health is probed via an INJECTED async
callable and cached for ``ttl`` seconds; a lane that fails a probe is parked on
``cooldown`` so it is skipped (not re-probed) until it expires -- so a dead heavy lane
fails straight over to the next lane instead of 404ing every request, and recovers
automatically once the cooldown lapses and a probe succeeds. The terminal (light)
lane is returned as the floor even if its own probe is failing, so a turn degrades
rather than dead-ends.

Pure stdlib (only ``time``) in the sibling-module style of mios_sched / mios_owui:
NO server.py import, NO globals. server.py owns the wiring -- it constructs the lane
map from its already-resolved endpoint constants + the [ai].heavy_engine SSOT, injects
an httpx probe, and exposes the module-level instance. test_mios_lanes.py drives this
module with a fake clock + fake probe, no agent-pipe runtime deps.

<!-- mios-src:bf2e92aad965 from usr/lib/mios/agent-pipe/mios_pipe/routing/lanes.py:4-22 -->

### Ordered preference chain of lane ids from the...

Ordered preference chain of lane ids from the [ai].heavy_engine selector.

    ``available`` -- iterable of the lane ids the resolver was given (e.g.
    ``{'sglang','vllm','light'}``).
    ``heavy_engine`` -- either a single preferred engine (``'sglang'`` | ``'vllm'`` |
    ``'light'``) OR an explicit comma-list (``'sglang,vllm,light'``, honoured
    verbatim). Empty/None defaults to ``'sglang'`` (the SSOT default).

    Rules: drop ids that are not available; dedupe preserving order; keep the
    ``light`` terminal lane LAST when it is present (the always-on floor) but never
    add it if an explicit comma-chain omitted it (respect the operator's choice).
    ``'light'`` as a single engine forces a light-only chain (no heavy).

<!-- mios-src:97fdd80013b3 from usr/lib/mios/agent-pipe/mios_pipe/routing/lanes.py:45-56 -->

### Health-aware lane picker. Construct with...

Health-aware lane picker. Construct with::

        LaneResolver(lanes, chains, probe, ttl=30.0, cooldown=60.0)

    ``lanes``    -- {id: Lane}.
    ``chains``   -- {role: [lane_id, ...]} ordered preference per role.
    ``probe``    -- async callable ``probe(url) -> bool`` (True == lane serving).
    ``ttl``      -- seconds a health result is cached (probe at most once / window).
    ``cooldown`` -- seconds a FAILED lane is skipped before it is re-probed.
    ``clock``    -- injectable monotonic clock (tests pass a fake).

<!-- mios-src:6c01efb48734 from usr/lib/mios/agent-pipe/mios_pipe/routing/lanes.py:78-87 -->

### INFERENCE lane-resolver cluster (strangler-fig refactor)....

INFERENCE lane-resolver cluster (strangler-fig refactor).

Extracted VERBATIM from ``server.py``. ``_lane_resolver`` lazily builds the WS-1
unified :class:`mios_lanes.LaneResolver` from SSOT and caches it in the
module-owned ``_LANE_RESOLVER`` singleton (rebound at runtime); ``_pick_tool_backend``
returns the ``(url, model)`` for the client-tools loop via that resolver with a
legacy heavy/light-probe fallback; ``_heavy_lane_up`` is the cached SGLang-heavy
reachability probe. The config scalars are imported from :mod:`mios_config`;
``mios_lanes`` is imported directly; every server-resident symbol (``_get_client``,
``_is_remote_endpoint``) is injected via :func:`configure` (one-way boundary -- this
module never imports ``server``). server.py re-imports the moved names under their
original aliases, and reads the live ``_LANE_RESOLVER`` through
:func:`_lane_resolver_current` so the importable surface stays byte-identical.

<!-- mios-src:d8fceacfb041 from usr/lib/mios/agent-pipe/mios_pipe/routing/lanes_resolver.py:4-17 -->

### WS-1 unified lane resolver (mios_lanes), built LAZILY from...

WS-1 unified lane resolver (mios_lanes), built LAZILY from SSOT so _toml_section
    / _get_client are defined, then cached. ONE place a model lane is chosen: the
    [ai].heavy_engine-preferred heavy lane, then the other heavy lane, then the always-on
    light lane, with a per-lane cooldown so a dead lane fails over (never 404s). Collapses
    the two 'mios-heavy' lanes (vLLM on `vllm` + SGLang on `sglang`) behind one selector.

<!-- mios-src:8200d2470c87 from usr/lib/mios/agent-pipe/mios_pipe/routing/lanes_resolver.py:86-90 -->

### (url, model) for the client-tools loop -- delegated to the...

(url, model) for the client-tools loop -- delegated to the WS-1 unified lane
    resolver: the preferred heavy reasoner when reachable, else the other heavy lane,
    else the always-on light lane (with per-lane cooldown so a dead lane fails over,
    never 404s). Degrade-open: any resolver error falls back to the legacy heavy/light
    probe so the agentic surface never hard-fails.

<!-- mios-src:812932e88c19 from usr/lib/mios/agent-pipe/mios_pipe/routing/lanes_resolver.py:143-147 -->

### NATIVE single-agent tool-loop responders (strangler-fig...

NATIVE single-agent tool-loop responders (strangler-fig refactor).

Extracted VERBATIM from ``server.py``. ``_respond_native_loop_direct`` runs the
mios-heavy + full-tool-surface agentic loop (prefetch grounding -> secondary tool
loop -> failover -> polish -> relay ladder -> sources); ``_respond_local_state`` is
the deterministic local-READ fast-path. Both keep every heuristic/guard/comment
byte-identical. Sibling leaf helpers are imported directly; every server-side symbol
is injected via :func:`configure` (one-way boundary -- this module never imports
``server``). ``server.py`` re-imports both responders under their original aliases so
the importable surface stays byte-identical.

<!-- mios-src:b2a94ca96631 from usr/lib/mios/agent-pipe/mios_pipe/routing/native_loop.py:4-14 -->

### Minimum candidate entities a section must carry before its...

Minimum candidate entities a section must carry before its grounding is
    judged; below this the signal is too thin to trust -> degrade-open. SSOT:
    [verity].antifab_min_entities -> MIOS_VERITY_ANTIFAB_MIN_ENTITIES (live).

<!-- mios-src:8b66b16d3086 from usr/lib/mios/agent-pipe/mios_pipe/routing/native_loop.py:70-72 -->

### FAB-01 guard body (extracted). SYNTHESIZED answer -> strip...

FAB-01 guard body (extracted). SYNTHESIZED answer -> strip all evidence
    blocks; RAW-evidence answer -> keep only success-JSON matching real tool
    output. Degrade-OPEN: disabled / empty / error -> return `ans` byte-identical.

<!-- mios-src:edb3339ecf0d from usr/lib/mios/agent-pipe/mios_pipe/routing/native_loop.py:127-129 -->

### Structural, UNICODE-aware candidate entities (Law 7: NO...

Structural, UNICODE-aware candidate entities (Law 7: NO English word list).
    Bare registrable domains/hosts, digit-bearing tokens (years / versions /
    counts), and proper-noun-shaped word tokens (unicode upper-initial or all-caps).
    A caseless script (e.g. CJK) yields few/none -> callers see too-few entities
    and degrade-open rather than strip.

<!-- mios-src:9777487e9b59 from usr/lib/mios/agent-pipe/mios_pipe/routing/native_loop.py:156-160 -->

### FAB-02 per-SECTION grounding. Split the answer structurally...

FAB-02 per-SECTION grounding. Split the answer structurally (blank lines +
    markdown heading boundaries) and drop ONLY a section that carries at least
    `min_entities` candidate entities AND whose grounded fraction (entities whose
    normalized form is a substring of the normalized fetched `corpus`) is below
    `ground_min`. A section with too few entities is always kept (degrade-open,
    covers caseless scripts). Returns (kept_text, stripped_any).

<!-- mios-src:27eedc41e152 from usr/lib/mios/agent-pipe/mios_pipe/routing/native_loop.py:175-180 -->

### FAB-02 guard body (extracted). Degrade-OPEN: disabled /...

FAB-02 guard body (extracted). Degrade-OPEN: disabled / ungated / empty
    corpus / nothing stripped / error -> return `ans` unchanged. When it strips a
    fabricated section it keeps the grounded sections and appends `note` (a
    user-facing honest line -- output prose, NOT a decision gate).

<!-- mios-src:a0a4a5f4de9a from usr/lib/mios/agent-pipe/mios_pipe/routing/native_loop.py:209-212 -->

### Inject server-side deps under their EXACT original names...

Inject server-side deps under their EXACT original names (one-way boundary).

    Called once from ``server.py`` after every injected symbol is defined. Each
    keyword equals the module global it sets; ``_worker_tools_core_cache`` is a live
    zero-arg getter for server's rebindable ``_WORKER_TOOLS_CORE_CACHE`` cache.

<!-- mios-src:45d7a7b2be09 from usr/lib/mios/agent-pipe/mios_pipe/routing/native_loop.py:317-322 -->

### Have the micro-LLM EXTRACT the calculation the user is...

Have the micro-LLM EXTRACT the calculation the user is asking for as a short,
    self-contained Python 3 snippet that PRINTS the result (mirrors _formulate_web_query).
    The snippet runs PIPE-SIDE in the coderun sandbox so the answer is COMPUTED, not
    guessed. Code-only output; '' on empty/error -> degrade-open (no compute prefetch).

<!-- mios-src:b89be3241743 from usr/lib/mios/agent-pipe/mios_pipe/routing/native_loop.py:1070-1073 -->

### For a HYBRID local+web turn, rewrite a vague...

For a HYBRID local+web turn, rewrite a vague SELF-referential question ("the
    theoretical specs of MY GPU") into a CONCRETE web query naming the components the
    local tools just IDENTIFIED -- so web_search finds the actual GPU/CPU spec pages,
    not dictionary definitions of "theoretical". Model-formulated (no templates);
    degrade-open to the raw user text on any error/empty (search still runs).

<!-- mios-src:8c1c0169397c from usr/lib/mios/agent-pipe/mios_pipe/routing/native_loop.py:1105-1109 -->

### OS-control fast-path responder + window enum/verify helpers...

OS-control fast-path responder + window enum/verify helpers (refactor R9).

Extracted VERBATIM from ``server.py`` -- the deterministic one-verb OS-control
action path (``_respond_os_control``) and the window-enumeration / before-after
diff / launch-verification / anti-fabrication-verdict helpers it owns. Every
function is moved byte-identically (LIVE hot path: computer-use / launch /
window-op); their consolidation is NOT in scope. ``server.py`` re-imports every
name under its original alias so the module's public surface is byte-identical.

Sibling functions (the ``_sse_*`` emitters, the broker ``dispatch_mios_verb``,
``polish_response``, ``_store_knowledge``, ``loads_lenient``, the DCI critic) are
imported directly; every server-side symbol the path touches (the ``OS_CONTROL_*``
config scalars, the ``_OS_CONTROL_ACTION_VERBS`` / ``_LAUNCH_VERBS`` verb sets, the
conv-key ContextVar, ``_get_client``, ``_scratchpad_note``, the ``_db_*`` helpers,
``_inline_satisfaction_check``, ``_strip_think_tags``) is injected via
:func:`configure` (one-way boundary -- this module never imports ``server``).

<!-- mios-src:01f018fae7b9 from usr/lib/mios/agent-pipe/mios_pipe/routing/oscontrol.py:4-20 -->

### Inject server.py's OS-control config scalars, the verb...

Inject server.py's OS-control config scalars, the verb sets, the conv-key
    ContextVar and the runtime helpers the fast-path calls back into.

    Callable more than once with a partial set (mios_sched-style): server.py
    injects ``fastpath_verbs`` / ``verb_catalog`` EARLY (the import-time stage --
    ``_render_os_control_verbs`` is called at server import) and the remaining
    runtime deps LATE, once they are all defined.

<!-- mios-src:f85144cbbb20 from usr/lib/mios/agent-pipe/mios_pipe/routing/oscontrol.py:78-84 -->

### Resolve the cross-desktop window-probe endpoints from the...

Resolve the cross-desktop window-probe endpoints from the SSOT
    (vendor /usr/share + /etc/mios + ~/.config). Returns a list of
    {"label","url"} dicts -- the local-host executor (when set) plus every
    [os_control.nodes.<name>].endpoint declared with a non-empty URL.
    Cached once per process; the lazy-load means a build without ANY
    overlay incurs zero work (returns []).

<!-- mios-src:167eb97bc5ed from usr/lib/mios/agent-pipe/mios_pipe/routing/oscontrol.py:132-137 -->

### Snapshot all open top-level windows. Calls the WSL-side...

Snapshot all open top-level windows. Calls the WSL-side list_windows verb
    AND every configured cross-desktop executor in parallel ([os_control].
    executor_endpoint + every [os_control.nodes.*].endpoint), merging the
    results. Without remote endpoints this collapses to the original WSL-only
    behavior (vendor empty = no overhead). Returns {"ok", "count", "windows":[...]}
    with each window carrying a `_source` tag so the diff can attribute opens to
    a specific desktop. Never raises.

<!-- mios-src:559eb6deb1db from usr/lib/mios/agent-pipe/mios_pipe/routing/oscontrol.py:206-212 -->

### RECORD + INDEX the before/after window snapshots + delta so...

RECORD + INDEX the before/after window snapshots + delta so FUTURE
    queries recall them (RAG: embedded knowledge row via _store_knowledge) and
    same-conversation agents see them (scratchpad). Fire-and-forget; the
 "check before, diff after" grounding the operator asked for.

<!-- mios-src:3c984772596b from usr/lib/mios/agent-pipe/mios_pipe/routing/oscontrol.py:306-309 -->

### Center the given window(s) on their desktop (operator...

Center the given window(s) on their desktop (operator binding
    'launches are ALWAYS centered -- that should be the default MiOS AI opening
    pattern'). WSLg / flatpak windows IGNORE Win32 launch-time placement, so we
    center AFTER the window maps. Picks the LARGEST window per owning executor
    (the MAIN app window -- a launch also spawns ~11 tiny PopupHost/tooltip
    windows) and POSTs /window/center to the Windows-native executor that owns
    it (only executor-sourced windows have movable Win32 hwnds; the WSL
    list_windows hwnds are a different namespace). The executor's center is a
    non-blocking async SetWindowPos, so this never stalls the turn. Best-effort;
    returns the list of centered window titles. Never raises.

<!-- mios-src:f1621ac9483a from usr/lib/mios/agent-pipe/mios_pipe/routing/oscontrol.py:345-354 -->

### Process-name patterns to pgrep for to confirm a launch...

Process-name patterns to pgrep for to confirm a launch ACTUALLY started
 ('should JUST search for PIDs globally for
    verifications'). The robust signal is the PROCESS existing -- WSLg windows
    carry content titles + proc=msrdc, never the app name, so title/count are
    unreliable. The launcher echoes the resolved ref ('launching <id>' /
    'fired <id>' / 'run <id>'); take both the reverse-DNS id AND its lowercased
    leaf (the bwrap binary, e.g. org.gnome.Epiphany -> 'epiphany'), plus the
    bare target name as a last-resort weak pattern.

<!-- mios-src:f2f48c60b206 from usr/lib/mios/agent-pipe/mios_pipe/routing/oscontrol.py:392-399 -->

### True if ANY pattern matches a running process command line...

True if ANY pattern matches a running process command line (global
    `pgrep -if` or Windows host `tasklist.exe`). /proc is world-readable, so the
    agent uid sees EVERY user's process cmdlines -- including the operator's flatpak
    GUIs running under bwrap. On WSL2, also queries tasklist.exe for host processes.

<!-- mios-src:289a93dc2018 from usr/lib/mios/agent-pipe/mios_pipe/routing/oscontrol.py:419-422 -->

### OS-control action fast-path. A single concrete...

OS-control action fast-path. A single concrete
    app/window/URL action is a DETERMINISTIC one-verb action: fire that ONE
    verb through the broker, report the REAL verdict, and STOP. NO council
    fan-out, NO web_search, NO synthesis of fabricated detail -- the failure
    mode that ran a 4-agent web-search swarm for "Launch Forza" (inventing
    window coordinates, never stopping after the launch had already
    succeeded) and narrated a fake tool call for "Close Forza".

    The polish prompt forbids claiming a success the verb's own output does
    not show (anti-fabrication; mirrors the launch_verified / verify_launch
    'presented, not merely process-alive' Definition-of-Done rule in SOUL).

<!-- mios-src:1753313590fd from usr/lib/mios/agent-pipe/mios_pipe/routing/oscontrol.py:514-524 -->

### Open WebUI request adapter. Extracted from server.py...

Open WebUI request adapter.

Extracted from server.py (monolith split). Pure stdlib (re) -- NO
coupling to the agent-pipe globals. Isolates the OWUI-specific quirk of wrapping
the user message in its RAG/task template so the rest of the pipe only ever sees
the operator's genuine question. The marker strings here are OWUI's OWN fixed
template text (an external-format adapter, like a protocol constant) -- not
operator-tunable config.

<!-- mios-src:41917a903e89 from usr/lib/mios/agent-pipe/mios_pipe/routing/owui.py:3-11 -->

### Return the operator's genuine question, unwrapping any OWUI...

Return the operator's genuine question, unwrapping any OWUI task template.

 OWUI's native web-search/RAG (ENABLE_WEB_SEARCH, confirmed live)
    wraps the message in its DEFAULT_RAG_TEMPLATE -- "### Task:\nRespond to the
    user query using the provided context ... <context>{sources}</context>" -- and
    the CURRENT default has NO <user_query> placeholder: the real question is just
    APPENDED after </context>. So the old strip (which required a <user_query> tag)
    silently passed the WHOLE blob through, and that blob became refine's text +
    every swarm facet title + the web-search query + each node's prompt ("respond
    using the provided context" -> the node RAG-answers / refuses tools) -- the
    operator's "PRIOR PROMPTS SATURATE PIPELINE" + the "### Task:" facet searches +
    the punts. Recover the genuine question. (Native-OpenAI pattern: retrieved
    context belongs in a system message, never concatenated into the user turn;
    MiOS does its OWN retrieval, so OWUI's injected context is dropped here.)

    Safe by construction: only unwraps a RECOGNISED OWUI scaffold (its marker
    sentence, or '### task:' + a '</context>' block, or an explicit <user_query>);
    a normal message that merely says 'task' or contains '<' is returned as-is.

<!-- mios-src:dad236898017 from usr/lib/mios/agent-pipe/mios_pipe/routing/owui.py:28-45 -->

### Planner / DAG-decomposition layer (Phase A.1). Extracted...

Planner / DAG-decomposition layer (Phase A.1).

Extracted verbatim from ``server.py``. ``_PLANNER_SYSTEM`` is the
function-calling-shaped planner system prompt -- it embeds the SSOT verb /
recipe / agent catalogs (rendered server-side and injected via
:func:`configure`) so the planner only emits real verbs / agents.
``decompose_intent`` calls the planner LLM and returns a validated DAG of
dispatch-verb / sub-agent nodes (or ``None`` to fall through to the backend).
``_topological_order`` / ``_dag_levels`` order that DAG for the executor.

``_planner_system_for`` / ``_action_domain_verbs`` narrow that prompt to a
single routed domain's verb slice (Stage-2 of the domain router); they live
here beside their only caller (``decompose_intent``).

Config constants (``PLANNER_*``) are re-read from ``os.environ`` (bases
``_STACK_MODEL`` / ``_LIGHT_BASE`` from ``mios_config``); ``_render_verb_catalog``
is imported from ``mios_verbcatalog``; the rendered catalogs, the routed-domain
contextvar, the raw verb-catalog / routing-domains SSOT, the
``_is_action_domain`` / ``_build_dispatch_cmd`` helpers and the live
``_AGENT_REGISTRY`` are injected via :func:`configure` (one-way boundary --
this module never imports ``server``).

<!-- mios-src:6b9bc902f148 from usr/lib/mios/agent-pipe/mios_pipe/routing/planner.py:4-25 -->

### Inject the server.py runtime deps the planner calls back...

Inject the server.py runtime deps the planner calls back into, then
    (re)build _PLANNER_SYSTEM once the rendered catalogs are available. The
    verb_catalog / routing_domains args feed the now-native _planner_system_for /
    _action_domain_verbs helpers (raw SSOT they read at call time). The
    short_prompt_chars / short_prompt_words args carry the SSOT [planner]
    short-prompt-skip cutoffs (None = keep the baseline).

<!-- mios-src:2a1ffbee28a0 from usr/lib/mios/agent-pipe/mios_pipe/routing/planner.py:80-85 -->

### Call the planner LLM to emit a DAG of dispatch verbs for a...

Call the planner LLM to emit a DAG of dispatch verbs for a
    multi-step user intent. Returns the parsed dict, or None on
    error / unparseable response.

    Short-prompt skip: short inputs (heuristic: under the SSOT
    [planner] char/word cutoffs) almost always map to a SINGLE
    dispatch verb, not a multi-step plan. Return None so the chain
    falls through to the backend single-dispatch path -- mios-launch
    resolves the verb directly. The planner used to over-decompose
    these into 2-step DAGs whose ReWOO substitution then misfired
    on NDJSON-emitting tools.

<!-- mios-src:fe317328c969 from usr/lib/mios/agent-pipe/mios_pipe/routing/planner.py:312-322 -->

### Group nodes into concurrent execution LEVELS (Kahn...

Group nodes into concurrent execution LEVELS (Kahn layering): each
    level is the set of not-yet-run nodes whose deps are ALL already
    satisfied, so every node in a level can run CONCURRENTLY. A level only
    starts after all earlier levels finish, preserving topological order
    (so ReWOO #E<id> refs resolve). Cyclic / dangling deps degrade to one
    forced node per round (declaration order) so the DAG never hangs --
    same safety stance as _topological_order.

<!-- mios-src:96292ba56b64 from usr/lib/mios/agent-pipe/mios_pipe/routing/planner.py:418-424 -->

### Web portal helpers + PWA asset builders + the swarm-roster...

Web portal helpers + PWA asset builders + the swarm-roster probe (refactor R10).

Extracted VERBATIM from ``server.py`` -- the portal config/auth SSOT, the Quadlet
service auto-discovery + host/container telemetry, the dashboard/login/PWA asset
strings, and the per-agent reachability probe. Every name is moved byte-identically
and re-imported by ``server.py``; the @app portal routes stay there as thin
wrappers, so the module's public + HTTP surface is unchanged.

``loads_lenient`` is imported directly; the two server helpers the swarm probe
calls (``_probe_auth_headers``, ``_agent_lane``) are injected via :func:`configure`
(one-way boundary -- this module never imports ``server``).

<!-- mios-src:6d49b54bdcf1 from usr/lib/mios/agent-pipe/mios_pipe/routing/portal.py:4-15 -->

### Inject server.py's runtime deps under their original...

Inject server.py's runtime deps under their original module-level names.
    _probe_auth_headers + _agent_lane back the swarm probe; _AGENT_REGISTRY backs
    the swarm-roster route (injected by reference -> server must re-configure on a
    membership reload); _sanitize_tool_text scrubs the service-detail logs; the
    ``websockets`` client module backs the terminal WS bridge. A None arg is
    skipped so server may call with a partial set (e.g. only the registry on a
    reload).

<!-- mios-src:e509cff5fb0b from usr/lib/mios/agent-pipe/mios_pipe/routing/portal.py:53-59 -->

### True when login is disabled or the request carries a valid...

True when login is disabled or the request carries a valid session --
    either the browser's httponly cookie, OR an 'Authorization: Bearer
    <token>' header. Same signed token either way (_portal_token_ok); the
    header form exists for NATIVE (non-browser) local clients -- e.g. the
    Quickshell PortalData.qml widget (design spec: mios-app-browser-portal-
    dashboard-design-*.md, native-unification roadmap addendum) --
    that call portal_login_logic once and reuse a Bearer token instead of
    implementing cookie-jar + redirect handling for a login flow that was
    designed for browsers.

<!-- mios-src:9b9da7620fc4 from usr/lib/mios/agent-pipe/mios_pipe/routing/portal.py:142-150 -->

### True if a Quadlet's generated unit is MASKED or was skipped...

True if a Quadlet's generated unit is MASKED or was skipped by a FAILED
    start condition (ConditionResult=no) -- i.e. retired (a legacy lane -> mios-llm-light)
    or gated OFF (vllm/guacamole: model not provisioned / wrong virtualization).
    Such a unit can only ever show as a phantom 'down' in the portal, so drop it.
    A unit that is MEANT to run but crashed keeps ConditionResult=yes and stays
    visible -> genuine outages are still surfaced. The unit's own systemd state
 is the SSOT -- no service-name list. Fail-OPEN: any
    query error returns False (visible), so a probe glitch never hides a real
    service.

<!-- mios-src:372d409b7c57 from usr/lib/mios/agent-pipe/mios_pipe/routing/portal.py:162-170 -->

### Best-effort host-port -> {container,state,image} map from...

Best-effort host-port -> {container,state,image} map from podman.
    Returns {} on any failure (podman absent / no perms) so the portal
    degrades to health-only without erroring.

    PREFERS the root-written snapshot at MIOS_PODMAN_PS_SNAPSHOT: this service
    runs hardened + non-root and CANNOT reach the rootful /run/podman socket
    (/run/podman is 0700 root:root), so a direct `podman ps` here sees an empty
 rootless context -> "podman present but no containers".
    mios-podman-ps.timer refreshes the snapshot every ~15s. Falls back to a
    direct `podman ps` for unrestricted/rootless-visible deployments.

<!-- mios-src:7f309ad4c2f5 from usr/lib/mios/agent-pipe/mios_pipe/routing/portal.py:285-294 -->

### Build a :root override from mios.toml [colors] (SSOT) so...

Build a :root override from mios.toml [colors] (SSOT) so the portal
    tracks the operator's palette. Maps the MiOS color ROLES to the portal's
    CSS vars; derived surfaces (--card/--line) recompute via color-mix in the
    page CSS. Returns '' on any failure -> the static MiOS-default :root
    stands. Per the no-hardcode rule: the toml is the source, the static
    block is just the documented fallback.

<!-- mios-src:9968379ebdfc from usr/lib/mios/agent-pipe/mios_pipe/routing/portal.py:907-912 -->

### Same-origin WebSocket bridge to a loopback ttyd. The...

Same-origin WebSocket bridge to a loopback ttyd. The operator's device
    reaches the portal but NOT ttyd's 127.0.0.1:<port> directly (loopback-only,
    not tailscale-served), so the native xterm embed connects here and we proxy
    to ttyd inside the VM -- works from any device with no per-port serve.

<!-- mios-src:4317314e084c from usr/lib/mios/agent-pipe/mios_pipe/routing/portal.py:1229-1232 -->

### Serve the MiOS Configurator as a unified portal sub-page...

Serve the MiOS Configurator as a unified portal sub-page (auth-gated).
    Reads mios.html from disk at request time so live edits are reflected
    immediately without a process restart. Injects the SSOT palette so the
    configurator tracks the operator's theme just like the dashboard does.

<!-- mios-src:0699d7f0e816 from usr/lib/mios/agent-pipe/mios_pipe/routing/portal.py:1340-1343 -->

### Run ``mios-theme-render check`` and report the projection...

Run ``mios-theme-render check`` and report the projection state WITHOUT
    ever writing. Returns {state, exit, summary}: state is 'PASS' (exit 0),
    'FAIL' (non-zero exit), or 'unknown' (the check could not be run at all --
    degrade-open, never raises). summary is the first PASS/FAIL line emitted.

<!-- mios-src:e69a61268947 from usr/lib/mios/agent-pipe/mios_pipe/routing/portal.py:1592-1595 -->

### READ-ONLY summary for the dashboard's System Config card...

READ-ONLY summary for the dashboard's System Config card: the resolved
    identity user + deploy version, the top-level section count, whether a
    user-layer override is present, and the theme-projection state. Reuses the
    Portal's layered tomllib load (the mios_toml vendor<host<user overlay,
    falling back to the single-file read) -- no new deps, NO writes anywhere.
    Degrade-open throughout: any probe failure yields a safe placeholder.

<!-- mios-src:53a594cd7b7b from usr/lib/mios/agent-pipe/mios_pipe/routing/portal.py:1613-1618 -->

### GET /portal/config/status -> small READ-ONLY JSON summary...

GET /portal/config/status -> small READ-ONLY JSON summary of live config
    health (resolved user/version, top-level section count, user-override
    presence, theme-projection PASS/FAIL) for the dashboard's System Config
    card. Auth-gated; NEVER writes; degrade-open (a probe failure yields a
    placeholder, not an error). The blocking reads + subprocess run off the
    event loop via asyncio.to_thread.

<!-- mios-src:d645e54114f6 from usr/lib/mios/agent-pipe/mios_pipe/routing/portal.py:1649-1654 -->

### Evaluate output quality against deterministic rules....

Evaluate output quality against deterministic rules.

    Returns:
        (quality_ok: bool, reason: str)
        If quality_ok is False, smartroute.should_escalate() will trigger escalation.
        Degrades open (returns True, "degrade_open") on unexpected exceptions.

<!-- mios-src:cf7bb41b5fd5 from usr/lib/mios/agent-pipe/mios_pipe/routing/quality_gate.py:56-62 -->

### MiOS agent-pipe -- REFINE intent classifier (extracted from...

MiOS agent-pipe -- REFINE intent classifier (extracted from server.py).

Verbatim move: the refine pass is the primary classifier feeding routing.
The _REFINE_SYSTEM / _REFINE_SYSTEM_LITE prompts and the refine_intent /
_salvage_refine_dispatch bodies are byte-identical to their server.py origin
(prompt-sensitive -- do not edit). server.py injects every dep that stays
behind via :func:`configure` and re-imports the names verbatim.

<!-- mios-src:653b493e232d from usr/lib/mios/agent-pipe/mios_pipe/routing/refine.py:4-11 -->

### Inject the server.py symbols the refine classifier reads....

Inject the server.py symbols the refine classifier reads. Each arg keeps
    its original server name as a module global; None means 'leave as-is' so a
    partial re-inject (e.g. the live agent-registry refresh) is safe. The routing
    cutoff args (promote_chars / dispatch_arg_max_words / chat_chars /
    dispatch_chars) carry the SSOT [refine] thresholds; injecting any of them
    re-renders _REFINE_SYSTEM so its length cues match the new gates.

<!-- mios-src:3d3c1734afcd from usr/lib/mios/agent-pipe/mios_pipe/routing/refine.py:80-85 -->

### Render the full REFINE classifier prompt, interpolating the...

Render the full REFINE classifier prompt, interpolating the SSOT length
    cues (REFINE_CHAT_CHARS / REFINE_DISPATCH_CHARS / REFINE_PROMOTE_CHARS) into
    the 'Length cue' block so the prompt's char hints always match the runtime
    promotion guards (one constant feeds both). Byte-identical to the original
    apart from those three interpolated cue numbers; configure() re-renders it
    after the cutoffs are injected so an mios.toml override flows into the cue.

<!-- mios-src:ea7bbec00ecd from usr/lib/mios/agent-pipe/mios_pipe/routing/refine.py:173-178 -->

### Recover a deterministic one-verb dispatch when refine emits...

Recover a deterministic one-verb dispatch when refine emits PROSE.

    A small refine model (qwen3.5:4b) occasionally NARRATES instead of emitting
    the JSON envelope -- even with format=json -- when the request invites
 reasoning ("Open discord on my desktop" -> the model
    replied 'To open Discord on your desktop, I will launch_app(Discord PTB)'
    as prose, json.loads failed at char 0, the turn DROPPED to the research
    swarm -> 477s, 8 agents, fabrication, NO launch). Rather than discard the
    obvious action, salvage it. Fully generative: it only matches verb NAMES
    from the live fast-path catalog (no hardcoded app/English list).

    Returns a {"intent":"dispatch","tool":...,"args":...} dict or None.

<!-- mios-src:e8ac3c23e59c from usr/lib/mios/agent-pipe/mios_pipe/routing/refine.py:570-582 -->

### Quick-refine pass. Returns the parsed plan dict or None on...

Quick-refine pass. Returns the parsed plan dict or None on
    bypass / error (caller falls through to the legacy router path).

    Bypass: trivial inputs (greetings, single-word commands) skip
    refine entirely. The existing classify_intent router handles
    them with its own chat-reply path in one LLM call -- adding a
    refine pass on top would be wasted latency. Local-compute-aware
 per operator directive 'fast and efficient for pure
    local compute'.

<!-- mios-src:5583c82b606c from usr/lib/mios/agent-pipe/mios_pipe/routing/refine.py:621-629 -->

### Critic->refiner for the HEAVY agent path (ref AIOS B.1 /...

Critic->refiner for the HEAVY agent path (ref AIOS B.1 / OS-Copilot
    executor-critic-refiner). Run the DCI critic on the buffered agent
    answer; if it raises a high-confidence challenge/ask (a genuinely
    contested/complex resolution), re-invoke the backend ONCE with the
    critic's concern so the answer is revised, then return the revision.

    Fires AS NEEDED: short/simple answers (< CRITIC_REFINE_MIN_CHARS) and
    the mios-os-control dispatch fast path never reach here, so CPU
    usecases stay fast; GPU/heavy answers earn the loop. Bounded by
    CRITIC_REFINE_MAX; returns the ORIGINAL answer on any error or when
    the critic is satisfied (the common case).

<!-- mios-src:741ccf697188 from usr/lib/mios/agent-pipe/mios_pipe/routing/refine.py:1051-1061 -->

### Reflection / self-assessment cluster (per-turn DoD verdict...

Reflection / self-assessment cluster (per-turn DoD verdict + failed-step reflection).

Extracted verbatim from ``server.py``. ``_inline_satisfaction_check`` runs the
synchronous Definition-of-Done check on the CURRENT turn and emits a
``user_query_(un)satisfied`` event; ``reflect_on_step_failure`` is the ReWOO
single-step reflection that turns a failed DAG node into one corrected step.
``server.py`` re-imports both names under their original aliases so the public
surface is byte-identical.

The DB writers, the verb catalog, the REFINE_* model-call constants and the
``_REFLECT_SYSTEM`` prompt are injected via :func:`configure` (one-way module
boundary -- this module never imports ``server``); ``_recent_reflections`` and
``loads_lenient`` come from sibling modules directly.

<!-- mios-src:222c023806a2 from usr/lib/mios/agent-pipe/mios_pipe/routing/reflect.py:4-17 -->

### CONFIRMATION ENGINE. Run a synchronous Definition-of-Done...

CONFIRMATION ENGINE. Run a synchronous
    Definition-of-Done check on THIS turn and emit a
    user_query_(un)satisfied event for the current session. mios-daemon's
    async loop ticks every 30s and only sees PRIOR turns; without this
    inline check, polish never knows whether the current turn actually
    succeeded and can't ground-truth the wrapped reply against it.

    Two signal sources, in priority order:
      1. tool_call rows agent-pipe recorded this turn (dispatch / DAG
         fast-paths write these) -> AND-fold their success fields.
      2. The agent-path signals `agent_tools_called` (verb names the
         sub-agent invoked inside its OWN tool-loop, captured from the
         stream) + `agent_answered` (the sub-agent produced a non-empty
         final answer). Under unify-on a verb like mios-os-control runs
         INSIDE Hermes, so agent-pipe records NO tool_call row for it --
         "no rows" then means the agent handled the turn, NOT that it
         failed. Treating that as `no_tools_seen -> unsatisfied` was the
         false-negative that made polish report failure on a succeeded
         verb and made the critic re-litigate a done answer (the
         "succeeds early then reports failed" bug). A delivered answer
         is DoD-met: the turn is DONE. Whether the ACTION inside it
         succeeded is then carried by the agent's own answer + any
         recorded rows -- polish relays a failure the agent states, but
         is no longer told the whole turn failed.

    Returns the emitted verdict dict {kind, payload} or None when
    there is nothing to judge. The agent-path caller uses the returned
    kind to HALT the chain (skip the critic re-pass) on a confirmed
    success. Best-effort: any DB hiccup returns None instead of
    failing the turn.

<!-- mios-src:ea24cac422b4 from usr/lib/mios/agent-pipe/mios_pipe/routing/reflect.py:86-115 -->

### ReWOO-style reflection

ReWOO-style reflection: route a failed DAG step back to the
    SAME small refine model with the failure context and ask for a
    single corrected step. Returns {tool, args, rationale} dict
    or None on timeout/empty.

    Distinct from the retry-same-args loop (PLANNER_REFLEXION_CAP):
    that retries transient errors; this REPLACES the args/tool when
    the failure is structural (wrong verb, missing arg, wrong path).
    Three-stage Reflect/Call/Final pipeline -- caller bounds the
    number of reflection turns to 1, so a stubborn failure surfaces
    as a real error instead of looping (per the published
    Structured Reflection termination contract).

<!-- mios-src:53cf4589cdee from usr/lib/mios/agent-pipe/mios_pipe/routing/reflect.py:225-236 -->

### Pull recent mios-daemon satisfaction verdicts (Phase E.1)....

Pull recent mios-daemon satisfaction verdicts (Phase E.1).
    These are post-hoc audit rows the daemon emits every ~30s based
    on AND-folding tool_call outcomes against refine intent. Polish
    uses them to ground the response in CROSS-TURN truth -- if the
    operator's previous query was flagged unsatisfied, the next
    response shouldn't paraphrase it as having worked.

<!-- mios-src:490cafed2754 from usr/lib/mios/agent-pipe/mios_pipe/routing/reflect.py:350-355 -->

### Adapt and issue a chat completion request to a remote node...

Adapt and issue a chat completion request to a remote node endpoint.

    If node_cfg['api'] is 'anthropic' or 'gemini', translates request format and
    translates provider response back to OpenAI Chat Completion format.
    Otherwise (openai / unset / unknown), passes the request through directly.

<!-- mios-src:1f0e307102d3 from usr/lib/mios/agent-pipe/mios_pipe/routing/remote_adapter.py:29-34 -->

### mios_router -- the pure routing decision for the MiOS...

mios_router -- the pure routing decision for the MiOS agent-pipe (WS-A11/WS-3
kernel decomposition, Stage 1).

A request's refined plan carries an `intent`; today chat_completions selects its
execution shape through a large, scattered `refined.get('intent')` cascade. This
module extracts the PRIMARY classification into one pure function: refined plan
-> RouteDecision. The Dispatcher (Stage 2) runs the decision; the Kernel facade
(Stage 2) composes Router + Dispatcher + the manager seams. Keeping Stage 1
additive + unwired means it is fully testable with ZERO risk to the live path
until the Stage-2 delegation is verified in the VM.

Modes (the execution shape the Dispatcher will run):
  chat       -- conversational reply, no tools / no fan-out
  dispatch   -- exactly ONE MiOS verb call (RouteDecision.tool)
  multi_task -- broad swarm fan-out (parallel facets)
  dag        -- a structured multi-node DAG plan
  agent      -- general single-agent tool-loop (the safe default; may deepen)

<!-- mios-src:facc301ef708 from usr/lib/mios/agent-pipe/mios_pipe/routing/router.py:4-21 -->

### ROUTING layer -- deterministic SSOT-config routing loaders...

ROUTING layer -- deterministic SSOT-config routing loaders + the
catalog-derived deterministic pre-router.

Extracted verbatim from ``server.py``. ``_load_routing_domains`` /
``_load_routing_phrases`` / ``_load_launch_fillers`` read the routing
vocabulary from ``mios.toml`` ``[routing]`` (domains, launch fillers,
trigger phrases) -- all SSOT data, no hardcoded English. ``_deterministic
_action_route`` maps an unambiguous launch / type request to a single
concrete verb override before the refine micro can mis-route it.

The fast-path verb sets and launch phrase frozensets (``_FASTPATH_VERBS``,
``_LAUNCH_TRIGGERS``, ``_LAUNCH_FILLERS``, ``_LAUNCH_LEAD_WORDS``,
``_LAUNCH_TRAIL_WORDS``, ``_COMPOUND_ACTION_ALT``) and the module logger
are injected via :func:`configure` -- they stay in ``server.py`` because
they derive from the ``_VERB_CATALOG`` server global. ``server.py``
re-imports every name under its original alias so the module's public
surface is byte-identical (one-way boundary: this module never imports
``server``).

<!-- mios-src:88be9c73b0d1 from usr/lib/mios/agent-pipe/mios_pipe/routing/routing.py:4-22 -->

### Parse mios.toml [routing.domains.*] -> {domain...

Parse mios.toml [routing.domains.*] -> {domain: {"desc","verbs"}} plus the
    router_enable switch. The 2-stage domain router's Stage-1 classifier consumes
    `desc` as each enum label's meaning; Stage-2 filters the planner catalog to the
 chosen domain's `verbs`. SSOT (fix the 82-tool mis-routing
    via schema-routing, NO english prose rules). FAIL-SAFE: router disabled / no
    domains / load error -> ({}, False) -> full-surface behaviour, nothing lost.

<!-- mios-src:6d6e628f7b27 from usr/lib/mios/agent-pipe/mios_pipe/routing/routing.py:68-73 -->

### Research-backed (OpenAI function-calling + AIOS routing)...

Research-backed (OpenAI function-calling + AIOS routing) deterministic
    pre-router. An unambiguous 'launch/open <app>' is a single concrete action;
    bind it to open_app(name=<app>) HERE so the qwen-class refine micro never
    gets to misclassify it as a research swarm -- the failure where 'launch
    epiphany' fired mios_find/list_windows and FABRICATED 'it's open' instead of
    launching. Returns the override dict, or None to fall through to the LLM
    router for compound/ambiguous phrasing (URLs, 'in <app>', conjunctions,
    questions) which the stronger refine model resolves. Triggers are
    catalog-derived (verb names), not hardcoded words (operator no-hardcode rule).

<!-- mios-src:87675a088155 from usr/lib/mios/agent-pipe/mios_pipe/routing/routing.py:145-153 -->

### mios_ruleof2 -- the Rule-of-Two architectural...

mios_ruleof2 -- the Rule-of-Two architectural prompt-injection gate (CaMeL-class).

Pure stdlib (+ the pure mios_sandbox sibling for the tier->side-effect policy). The
Rule of Two (Meta, "Agents Rule of Two") is a DETERMINISTIC invariant: an agent action
may combine at most TWO of three dangerous properties without human review --

  A  untrusted-input : the session ingested attacker-controllable content (the EXISTING
                       provenance-taint chain; passed in as ``session_tainted``).
  B  sensitive-access: the verb READS sensitive / private / cross-tenant data (the SSOT
                       ``[verbs.*].sensitive`` flag -- additive metadata, not a keyword
                       classifier).
  C  state-change    : the verb mutates state / has external side-effects (derived from
                       the SSOT ``[verbs.*].permission`` tier via the EXISTING
                       ``mios_sandbox`` tier->confinement policy).

When all three hold, the chain is the prompt-injection kill-chain (untrusted text ->
reads secrets -> exfiltrates/acts) and the dispatch must be GATED (routed to human
review) or BLOCKED. With two or fewer, it proceeds.

This module is the testable DECISION only. It composes signals the rest of the pipe
already computes -- it does NOT re-derive taint (mios_firewall owns A) or privilege
(the SSOT verb metadata owns B/C). It NEVER imports server; the wiring (the mode flag,
the chokepoint placement, the HITL routing) lives in mios_dispatch / server.py.

FOLLOW-UP (flagged, NOT built here): the deeper CaMeL design (Debenedetti et al.,
"Defeating Prompt Injections by Design") routes untrusted content to a QUARANTINED LLM
that may only extract structured data and CANNOT emit actions, while a privileged
planner LLM -- which never sees the raw untrusted text -- composes the action plan over
that data (dual-context / capability-tracked dataflow). That is a larger architectural
change to the orchestrator's context plumbing. This wave ships only the Rule-of-Two
COMPOSITION gate (the deterministic ceiling on dangerous-property combinations); the
quarantined-LLM / dual-context split is the natural next step on top of it.

<!-- mios-src:6eb0f4fdd6a9 from usr/lib/mios/agent-pipe/mios_pipe/routing/ruleof2.py:4-36 -->

### Property C

Property C: does the verb mutate state / have side-effects? Derived from the
    SSOT ``[verbs.*].permission`` tier via the EXISTING tier->confinement policy in
    mios_sandbox -- ``read`` is a pure-info tier (no confinement) so NOT a state
    change; ``write`` / ``interactive`` resolve to a confined profile (touches the
    fs / injects input) so they ARE. Reusing ``resolve_profile`` keeps the tier
    semantics SSOT (no restated ``{write, interactive}`` literal) and inherits its
    FAIL-CLOSED posture: an unknown/missing tier resolves to the strictest (confined)
    profile, so it counts as a state change (conservative -- fail toward gating).

<!-- mios-src:ee830813846c from usr/lib/mios/agent-pipe/mios_pipe/routing/ruleof2.py:61-68 -->

### Evaluate the Rule of Two for one verb dispatch. Inputs...

Evaluate the Rule of Two for one verb dispatch. Inputs:

      session_tainted -- property A, the EXISTING provenance-taint signal (bool).
      permission_tier -- the verb's SSOT ``[verbs.*].permission`` (drives property C).
      sensitive       -- the verb's SSOT ``[verbs.*].sensitive`` flag (property B).
      mode            -- the SSOT ``[security].rule_of_two_mode`` in force.

    Returns a :class:`RuleOfTwoVerdict`. Total + pure: never raises (an unclassifiable
    tier degrades to side-effecting via :func:`is_state_change`), so a call-site can
    treat any exception as impossible and keep its own degrade-open fallback for I/O.

<!-- mios-src:2e7c89d49b84 from usr/lib/mios/agent-pipe/mios_pipe/routing/ruleof2.py:97-106 -->

### Sub-agent /v1 tool-loop + its anti-disclaimer / closed-loop...

Sub-agent /v1 tool-loop + its anti-disclaimer / closed-loop guards.

Extracted verbatim from ``server.py``. Holds ``_v1_secondary_tool_loop`` (the
universal pipe-side OpenAI tool-loop every /v1 sub-agent runs through -- MiOS is
/v1-only, so this is the single tool-loop mechanism), plus the load-bearing loop
guards it relies on: the anti-disclaimer ``_TOOL_NUDGE`` +
``_looks_like_disclaimer``, the no-progress signature ``_tool_call_sig``, the
failure verdict ``_tmsgs_indicate_failure``, the closed-loop ``_REPLAN_NUDGE``
and the ``_daemon_diagnose`` monitor pass. ``server.py`` re-imports every name
under its original alias so the module's public surface is byte-identical.

The moved bodies are unchanged. ``_exec_tool_calls`` / ``_rescue_tool_calls``
(mios_toolexec) and ``loads_lenient`` (mios_jsonsalvage) are imported directly
from those siblings; the remaining server-side symbols the loops touch (the
config scalars, the ``_DAEMON_DIAGNOSE_*`` constants and the helpers
``_apply_outbound_auth`` / ``_endpoint_supports_parallel_tools``) are injected
via :func:`configure` (one-way module boundary -- this module never imports
``server``).

<!-- mios-src:d764a4a8ab48 from usr/lib/mios/agent-pipe/mios_pipe/routing/secondary_loop.py:4-22 -->

### Stable (name + sorted-args) signature of a tool_call, for...

Stable (name + sorted-args) signature of a tool_call, for the loop's
    no-progress / runaway guard: if a round re-emits ONLY calls already made,
    the loop breaks instead of repeating forever (universal-loop slice 3).

<!-- mios-src:03dd8b4132ca from usr/lib/mios/agent-pipe/mios_pipe/routing/secondary_loop.py:38-40 -->

### True if any call in the batch targets a state-changing...

True if any call in the batch targets a state-changing verb, judged by
    the SSOT verb-catalog permission tier (write/interactive) -- NOT a hardcoded
    lexical read-only allowlist (Law 7). Unknown/read-tier verbs are read-only.
    Same permission classification reflect.py and the risk-tier sandbox use, so
    the write/failure gate generalises across every verb and stays SSOT-driven.

<!-- mios-src:644a494943c3 from usr/lib/mios/agent-pipe/mios_pipe/routing/secondary_loop.py:149-153 -->

### DAEMON-DIAGNOSE ("the daemon monitors the pipeline and...

DAEMON-DIAGNOSE ("the daemon monitors the pipeline and reports
    back"): a FRESH monitor-LLM pass over a FAILED step -- WHY it likely failed + a
    DIFFERENT concrete action to try -- so the closed-loop retry is GUIDED, not a blind
    re-run. A SECOND perspective (not the model that just gave up). Short + bounded +
    degrade-open: any error/empty/disabled -> '' (caller falls back to the generic nudge).

<!-- mios-src:6b1e7b3d9c13 from usr/lib/mios/agent-pipe/mios_pipe/routing/secondary_loop.py:181-185 -->

### Pipe-side READ-ONLY OpenAI tool-loop for a /v1 sub-agent...

Pipe-side READ-ONLY OpenAI tool-loop for a /v1 sub-agent (opencode on the `opencode_gateway` port,
    hermes, daemon-agent, any node bound to a /v1 endpoint -- MiOS is /v1-only, so
    this is the single tool-loop mechanism for the /chat/completions shape): POST
    (non-streaming) -> read message.tool_calls (RESCUING a narrated call from
    content when the field is empty -- the opencode ```json webfetch``` lie) ->
    execute the read verbs via the broker -> append role:tool -> re-call, up to
    SECONDARY_TOOL_MAX_ITERS or until the agent stops calling tools (SATISFIED).
    A self-looping agent returns no tool_calls -> ONE pass, no-op. Returns the
    augmented messages ready for the final (streamed or complete) answer.
    Endpoint-agnostic: `ep` comes from the agent's binding map, no port literals
 here ('any agent/model on any node/endpoint, no
    hardcodes').

<!-- mios-src:598439faa436 from usr/lib/mios/agent-pipe/mios_pipe/routing/secondary_loop.py:217-228 -->

### mios_smartroute -- cost/quality SmartRouting for the MiOS...

mios_smartroute -- cost/quality SmartRouting for the MiOS agent-pipe (WS-A16,
the AIOS SmartRouting / remote-core escalation layer).

Pure stdlib. RESEARCH NOTE (the proper solution): the production pattern (LiteLLM
router, adaptive/cascading routing) is LOCAL-FIRST with quality-gated escalation
-- run the cheap local lane first, escalate to a stronger/remote core only when
the local output fails a quality check or the local group is exhausted, so the
premium (a paid remote token) is paid only when it actually buys quality.
Escalation is also bounded by a per-day cost budget (a runaway can't drain it).
This module is the routing DECISION; server.py runs the lanes + the quality gate
+ the real remote adapter calls.

Sources: LiteLLM Router (docs.litellm.ai/docs/routing), LiteLLM Adaptive Router,
"LLM Gateways & Model Routing" cost-optimization guides (2026).

<!-- mios-src:5395ee0379d5 from usr/lib/mios/agent-pipe/mios_pipe/routing/smartroute.py:4-18 -->

### OpenAI-streaming SSE chunk + status-emit primitives...

OpenAI-streaming SSE chunk + status-emit primitives (extracted from server.py).

Every builder returns ``bytes`` ready to write to the SSE response stream, or (for
``_stream_answer``) async-yields them. Moved verbatim from ``server.py``; the
module is pure (stdlib + ``json`` only) and ``server.py`` re-imports every name so
its public surface is unchanged.

<!-- mios-src:34fb1217e136 from usr/lib/mios/agent-pipe/mios_pipe/routing/sse.py:4-10 -->

### Build an OpenAI-streaming SSE chunk. `reasoning` populates...

Build an OpenAI-streaming SSE chunk. `reasoning` populates the
    standard `delta.reasoning_content` field (OpenAI/OpenRouter/DeepSeek
    convention) -- OWUI renders it as a native Thinking dropdown and
    strict clients (Firefox Smart Window) ignore it, showing only the
    clean `content` answer. Optional `mios_status` carries pipe-internal
    phase emits (👂 prompt, 🧭 route, 🛠️ tool, ✅) that translator gateways
    lift into their native status surfaces; stock clients ignore it.

<!-- mios-src:f82f51405a58 from usr/lib/mios/agent-pipe/mios_pipe/routing/sse.py:34-40 -->

### Stream a reasoning/trace delta on the correct channel for...

Stream a reasoning/trace delta on the correct channel for the surface.

    ``reasoning_ok`` carries the consuming surface's capability (set per-request
    from the ``x-mios-reasoning-ok`` hint the OWUI pipe advertises; ``None`` when
    unknown):

    * ``True``  -- reasoning-aware surface (OWUI / Hermes desktop): pin the trace
      to ``delta.reasoning_content`` REGARDLESS of ``[observability].debug`` so it
      renders live in the native Thinking pane and never pollutes the answer
      ``content`` (final answer stays the only thing in ``content`` -- KV-safe,
      OWUI #21815). Full visibility, replay-safe.
    * ``False`` -- a surface that DECLARED itself content-only: fold the trace
      inline as ``content`` so strict clients (which ignore ``reasoning_content``)
      still render it. Visibility preserved; MiOS owns the replay-strip.
    * ``None``  -- unknown surface: legacy routing, ``[observability].debug``
      decides (byte-identical to before the hint existed -- degrade-open).

    The mandate is full visibility on EVERY surface; this only routes WHICH
    channel carries the trace, never suppresses it.

<!-- mios-src:2484901f8e75 from usr/lib/mios/agent-pipe/mios_pipe/routing/sse.py:67-85 -->

### Phase -> (emoji, label) for the SSE status strip....

Phase -> (emoji, label) for the SSE status strip. Personable
    defaults here; each phase is OVERRIDABLE from mios.toml
    [owui.status_phases.<phase>] = { emoji = "..", label = ".." } so the
    operator tunes MiOS-Agent's voice without touching code (SSOT; no
 hardcoded UI strings locked in the hot path).
    'better emitters / more detailed and personable'.

<!-- mios-src:deb4d8c6c02a from usr/lib/mios/agent-pipe/mios_pipe/routing/sse.py:96-101 -->

### Humanistic-label variant of _sse_status. Looks up the phase...

Humanistic-label variant of _sse_status. Looks up the phase
    in _HUMAN_LABELS, emits the casual label + emoji. `detail` is
    optional and should ALSO be human-facing prose (e.g. "for 22
    seconds", "almost there") -- NOT a model id / args JSON /
    intent token. If you find yourself wanting to thread technical
    info through here, log it to the event table instead.

<!-- mios-src:f1b6e271b360 from usr/lib/mios/agent-pipe/mios_pipe/routing/sse.py:126-131 -->

### Emit a content-empty SSE chunk whose only purpose is the...

Emit a content-empty SSE chunk whose only purpose is the
    `mios_status` field. Standard OpenAI clients see a no-op delta
    + ignore the extra field. Translator gateways pull the phase
    info from `mios_status` and surface it natively (OWUI's
    event_emitter status, Hermes Discord's reactions, etc.).

    Prefer _sse_status_phase() for new emit sites -- it picks the
    canonical humanistic label from _HUMAN_LABELS. This raw form
    stays available for one-off cases where the phase mapping
    doesn't fit.

<!-- mios-src:2de1893c0a0b from usr/lib/mios/agent-pipe/mios_pipe/routing/sse.py:139-148 -->

### Yield ONE _sse_status per recorded enrich STEP ("need...

Yield ONE _sse_status per recorded enrich STEP ("need
    emitters for every step end-to-end" -- not one whole-loop summary). Covers
    the web steps (search / each page read / each deep-crawl / each drill pass,
    recorded by _web_research_enrich) and the READ-only tool runs (recorded by
    _read_tool_enrich). Each emit also persists in the reasoning log via
    _sse_status. Yields nothing when no steps ran.

<!-- mios-src:a3f95f845ed3 from usr/lib/mios/agent-pipe/mios_pipe/routing/sse.py:173-178 -->

### SHORT, operator-facing description of what a DAG node is...

SHORT, operator-facing description of what a DAG node is DOING -- the
 active step's CONTEXT ("emits should show actual steps
    relevant to the current active step's context"). Derived from the node's
    OWN data -- an agent node's sub-task, or a verb node's key arg -- NOT the
    internal model/endpoint (which read as a leak). No LLM call, no hardcoded
    topic text: it's the step's literal intent.

<!-- mios-src:1ce609869330 from usr/lib/mios/agent-pipe/mios_pipe/routing/sse.py:194-199 -->

### Per-endpoint live emitter ("endpoint emitters for each ai...

Per-endpoint live emitter ("endpoint emitters for
    each ai endpoint/node"). One status event naming an AI node as the chain
 ENGAGES it / it RESPONDS / goes silent. `context` is
    a short description of the node's CURRENT STEP -- its sub-task or the verb
    arg -- so the emit reflects the active step's context, not just a glyph.
    The lane/model/endpoint internals stay OUT (they read as a leak); context
    is the WHAT (operator-facing), not the HOW (plumbing).

 the LABEL must be GENERATIVE -- indicative of the
    FUNCTION being performed, NOT the internal agent/function name (research-
    dgpu-1, hermes, opencode, ...). So the label = the node's actual sub-task
    (`context`), falling back to its semantic ROLE as a plain word (research /
    reasoning / coding -- a capability descriptor, not a node name) and never
    the registry key. The internal name is dropped entirely from the emit.

<!-- mios-src:544a9e760256 from usr/lib/mios/agent-pipe/mios_pipe/routing/sse.py:220-233 -->

### Yield the final answer in small character-exact chunks so...

Yield the final answer in small character-exact chunks so OWUI renders
    it progressively (live 'typing') instead of one end-of-turn burst -- the
    "thinking prints then switches to the refined copy" jolt (operator
). Pacing is bounded so long answers stream in ~1.2s, not slower.
    Char-slicing preserves the text byte-for-byte (markdown/code intact).

<!-- mios-src:5806fe0e99a1 from usr/lib/mios/agent-pipe/mios_pipe/routing/sse.py:244-248 -->

### Best-effort read of the war-room activity sink (task_3176): a...

Best-effort read of the war-room activity sink (task_3176): a JSONL sibling of
    the hermes-tail state file into which mios-a2o appends per-task start/finish
    transitions when `[frontier].stream_to_reasoning` is on. Returns event dicts
    newer than seen_ts (may be empty). Degrade-open: when the flag is off the file
    is never created, so this returns [] and `_tail_latest_status` is byte-
    identical to before. Path from MIOS_FRONTIER_STREAM_PATH (SSOT), else derived as a
    sibling of the hermes-tail path so no transport constant is restated.

<!-- mios-src:2ce205584700 from usr/lib/mios/agent-pipe/mios_pipe/routing/sse.py:279-285 -->

### SWARM brain (refactor R8). Extracted VERBATIM from...

SWARM brain (refactor R8).

Extracted VERBATIM from ``server.py`` -- the multi-agent fan-out + synthesis
core. ``_agent_dag_from_tasks`` builds a CONCURRENT per-agent DAG from refine's
``multi_task`` array; ``_respond_agent_dag`` executes that DAG concurrently and
SYNTHESISES the agents' outputs into one polished answer. The nested
``_synthesise`` holds the anti-fabrication logic (raw research is the only ground
truth, honest-when-empty, punt-drop, closed-loop replan, audit envelope) moved
byte-identically. ``server.py`` re-imports both names under their original alias
so the importable surface is byte-identical; every server-side symbol is injected
via :func:`configure` (one-way boundary -- this module never imports ``server``).

<!-- mios-src:83210d579b3b from usr/lib/mios/agent-pipe/mios_pipe/routing/swarm.py:4-15 -->

### Re-route any DAG `agent` node assigned to a node that is...

Re-route any DAG `agent` node assigned to a node that is currently DOWN
    onto a LIVE agent, preserving swarm width under an outage (operator
 "iGPU is down"). Universal chokepoint: runs on the FINAL DAG
    regardless of which planner built it (multi_task / _plan_swarm / the
    decompose_intent fallback). Spreads like _agent_dag_from_tasks -- prefer an
    UNUSED live agent so the facets still fan out across DISTINCT engines, only
    reusing a live agent when none are left. The default agent (Hermes, dGPU) is
    never health_gate, so a live target always exists. Mutates nodes in place;
    returns [(node_id, from, to), ...] for the log/emit. No-op when `live` is
    empty/None (degrade open -- never strand a turn on a bad probe).

<!-- mios-src:e0e0e9e16b3e from usr/lib/mios/agent-pipe/mios_pipe/routing/swarm.py:201-210 -->

### Build a CONCURRENT per-agent DAG from refine's multi_task...

Build a CONCURRENT per-agent DAG from refine's multi_task array:
    one agent node per independent task, routed to the task's target_agent
    (a registry key as-is, else role-matched via _pick_agent, else the
    default agent), all deps=[] so they run in PARALLEL. This is refine's
    OWN decomposition -- each sub-task already carries a target_agent hint
    -- so no extra planner LLM call is needed. Realises the operator's
    "separate prompts per refinement step -> sub-agents ... concurrent
    Compute" directly. Returns {summary, nodes}.

<!-- mios-src:7c4653cf5dd7 from usr/lib/mios/agent-pipe/mios_pipe/routing/swarm.py:235-242 -->

### Execute a per-agent DAG concurrently and SYNTHESISE the...

Execute a per-agent DAG concurrently and SYNTHESISE the agents'
    outputs into ONE polished answer (multi_task -> parallel sub-agents).
    The per-node audit envelope rides the reasoning channel; the polished
    synthesis is the operator-facing answer -- same answer/dropdown split
    as the agent + council paths. Streaming emits LIVE per-node endpoint
 statuses as the DAG runs, before the synthesis.

<!-- mios-src:f651a0ddde80 from usr/lib/mios/agent-pipe/mios_pipe/routing/swarm.py:348-353 -->

### Empty-DAG safety net

Empty-DAG safety net : the swarm grounded nothing,
        so re-answer via the ALWAYS-UP light-lane native loop (it does its own web
        grounding + cites REAL urls). Returns (text, sources) on success, else
        (None, []) -> the caller keeps the original DAG `main`. Degrade-open: never
        raises, never recurses (the native loop never re-enters the DAG).

<!-- mios-src:d065a657be05 from usr/lib/mios/agent-pipe/mios_pipe/routing/swarm.py:611-615 -->

### CASUAL swarm grounding ("ridiculous runtimes"): run...

CASUAL swarm grounding ("ridiculous runtimes"): run
        web_search ONCE on the user query and inject the SAME grounding into EVERY
        agent node, so the nodes reason over shared facts instead of each running a
        redundant per-node web_search tool-loop (6 nodes re-searching the same
        single-intent query contended on the dGPU + SearXNG, so even hermes blew
        the per-node deadline). _web_research_enrich self-gates on the web signal,
        so a pure-local query is a no-op. Breadth preserved -- all nodes still fire,
        they just share ONE search. Nodes flagged _no_tools so they don't re-search.

<!-- mios-src:c89aab48459e from usr/lib/mios/agent-pipe/mios_pipe/routing/swarm.py:725-732 -->

### Dedicated SWARM decomposer ('AI SWARM', Layer B): a...

Dedicated SWARM decomposer ('AI SWARM', Layer B):
    a narrowly-scoped planner call that splits a request into independent
    {agent, task} assignments for CONCURRENT dispatch. More reliable at
    emitting AGENT assignments than the general verb-DAG planner (which
    skews toward verb nodes). Returns task dicts shaped for
    _agent_dag_from_tasks ({target_agent, refined_text, title}), or [].

    `history` (recent chat turns) is fed to the planner so a TERSE follow-up
    inherits the established subject instead of the model inventing one
 (a terse "do deep research on it" follow-up lost the
    subject established in prior turns and the planner fabricated unrelated
    routes + constraints that searched garbage).

<!-- mios-src:6527119da2be from usr/lib/mios/agent-pipe/mios_pipe/routing/swarm.py:886-897 -->

### Generate ADDITIONAL distinct sub-topic facets so each live...

Generate ADDITIONAL distinct sub-topic facets so each live node works its
 OWN angle instead of the backfill round-robining a handful (
    "diversify the backfill facets per node"). MODEL-generated -- NO hardcoded angle
    list; self-gates to [] when the request genuinely has no more real angles (a
    thin ask -> the backfill round-robins as before). Each item is a CLEAN
    web-search phrase (the TOPIC, not an imperative). Returns up to (target_n -
    len(existing)) NEW facets, deduped against the existing ones.

<!-- mios-src:d547878ece46 from usr/lib/mios/agent-pipe/mios_pipe/routing/swarm.py:980-986 -->

### mios_toolconflict -- per-verb dispatch serialization for...

mios_toolconflict -- per-verb dispatch serialization for the MiOS agent-pipe
(WS-A7, the AIOS Tool Manager conflict/parallel-limit layer).

Pure stdlib (asyncio / collections) so it unit-tests in isolation, in the
sibling-module style of mios_sched / mios_jsonsalvage. server.py owns the wiring
(parsing the SSOT [verbs.*] fields, building the module-global instance, and
wrapping the dispatch chokepoint); this module owns only the reusable mechanism.

The problem
===========
Before WS-A7 the dispatch chokepoint (_dispatch_bounded) special-cased ONE verb
(web_search, a global SearXNG bulkhead) and let every other verb pass straight
through with unbounded concurrency. But several verbs are *stateful and
single-instance*: there is exactly one foreground window and one keyboard, so a
council/DAG fan-out that issues `open_app`, `focus_window` and `pc_type`
concurrently races them against each other -- the keystrokes land in whatever
window won the focus race. Such verbs need to SERIALIZE, not stampede.

The mechanism
=============
Two orthogonal, SSOT-declared controls, both keyed off the verb name:

  parallel_limit (int >= 1)
      A per-verb concurrency cap. `parallel_limit = 1` makes the verb strictly
      single-flight; `= N` admits at most N concurrent dispatches. Backed by a
      per-verb asyncio.Semaphore(N).

  conflict_group (str)
      A named mutual-exclusion set. All verbs sharing a group serialize against
      *each other* (one member of the group runs at a time), not just against
      themselves. Backed by an asyncio.Semaphore(1) per group name.

A verb may declare either, both, or neither. `guard(verb)` returns an async
context manager:

    async with CONFLICT.guard(verb):
        ... dispatch the verb ...

Deadlock-freedom
----------------
A call acquires AT MOST one group lock and AT MOST one verb semaphore, always in
the fixed order group-lock -> verb-semaphore, and releases in reverse. Because
the order is global and each call holds at most one of each kind, no acquire
cycle can form. Cancellation/exception while acquiring rolls back whatever was
already held (the _Guard rollback in __aenter__).

Fast path
---------
A verb that declares neither control hits a no-op guard (two dict lookups, no
semaphore, no await) -- so the overwhelming majority of dispatches are
unaffected. This is the degrade-open default: an empty ConflictGate serializes
nothing.

Concurrency model: single-threaded asyncio. Semaphores are created lazily on
first use (inside a running loop). All bookkeeping mutations happen with no
await between check and mutation, so no lock is needed.

<!-- mios-src:0cfbb5413ebc from usr/lib/mios/agent-pipe/mios_pipe/routing/toolconflict.py:4-60 -->

### Build a gate from the _VERB_CATALOG dict

Build a gate from the _VERB_CATALOG dict: read each verb's
        `parallel_limit` (int) and `conflict_group` (str). Tolerant of missing
        / malformed fields (degrade-open: unparseable -> unconstrained).

<!-- mios-src:f6b724b5d37c from usr/lib/mios/agent-pipe/mios_pipe/routing/toolconflict.py:94-96 -->

### Tool-call execution primitive + narrated-tool-call rescue...

Tool-call execution primitive + narrated-tool-call rescue corpus.

Extracted verbatim from ``server.py``. Holds the universal pipe-side tool
executor (``_exec_tool_calls``), the hard-won narrated-tool-call salvage
(``_rescue_tool_calls`` / ``_norm_tool_call`` + the ``_RESCUE_*`` regexes), the
ACI result capping (``_cap_verb_result`` / ``_verb_result_cap``) and the broker
error shaper (``_format_tool_error``). ``server.py`` re-imports every name under
its original alias so the module's public surface is byte-identical.

The moved bodies are unchanged. ``_loads_lenient`` (mios_jsonsalvage),
``_aci_normalize`` (mios_aci) and ``execute_skill`` (mios_skills) are imported
directly from their sibling modules; every other server-side symbol they touch
(the verb/recipe/security catalogs, the orchestrator-context ContextVar, the
config scalars and the DB / dispatch / swarm helpers) is injected via
:func:`configure` (one-way module boundary -- this module never imports
``server``).

<!-- mios-src:a4659ed89ee1 from usr/lib/mios/agent-pipe/mios_pipe/routing/toolexec.py:4-20 -->

### Promote a NARRATED tool call in `content` into OpenAI...

Promote a NARRATED tool call in `content` into OpenAI tool_calls[].
    Parses (a) Qwen <function=NAME><parameter=K>V</parameter></function> XML,
    and (b) JSON objects -- bare or in a ```fence -- of shape
    {"name","arguments"|"args"|"parameters"}, OpenAI {"function":{"name",
    "arguments"}}, or {"tool","args"}. Returns [] when nothing matches a known
    tool. GUARD: only names in _allowed_tool_names are promoted.

<!-- mios-src:380929d682a0 from usr/lib/mios/agent-pipe/mios_pipe/routing/toolexec.py:211-216 -->

### Cap a verb result to its char budget, FLAGGING truncation...

Cap a verb result to its char budget, FLAGGING truncation loudly.

    A bare mid-record slice (the old `out[:cap]`) invites the model to FABRICATE
 the omitted tail -- "what's open" invented window PIDs/
    titles + a whole process list PAST a cut-off list_windows/process_list,
    because the slice looked like a complete (just short) list. This marker +
    the grounding instruction make the model report ONLY the complete entries
    shown and say the list continues, instead of completing it from imagination.
    Returns `out` unchanged when within budget.

<!-- mios-src:e4c4371acd66 from usr/lib/mios/agent-pipe/mios_pipe/routing/toolexec.py:271-279 -->

### Execute the verbs in an OpenAI tool_calls[] list via the...

Execute the verbs in an OpenAI tool_calls[] list via the broker and return
    (tool_result_messages, ran_any). Shared by every pipe-side sub-agent tool-loop
 (every sub-agent lane, all /v1) so the OpenAI loop is ONE mechanism ('full
    loop ... to OpenAI Standards'). tool_call_id is preserved for OpenAI-spec
    linkage; the result is also keyed by `name` (some models match by name).

    allow_write: when False (the PRIMARY's pipe-side pre-resolution) only
    permission=read verbs auto-execute -- the primary's OWN loop performs writes.
    When True (a WORKER/agent loop) write/launch verbs execute too: the MiOS
    agents ACT -- the no-live-launch binding is CLAUDE's alone, not the agents'
. The broker's conversation-scoped single-flight dedup
    collapses duplicate actions across the parallel swarm, so a write fires once.

<!-- mios-src:8e5a3bf79ceb from usr/lib/mios/agent-pipe/mios_pipe/routing/toolexec.py:332-343 -->

### Embedding-backed tool/app semantic search for the...

Embedding-backed tool/app semantic search for the agent-pipe surface.

Extracted verbatim from ``server.py`` (refactor R10). Holds the cosine retrieval
core for ``GET /v1/tool-search`` (native verbs + external MCP tools, RAG-MCP
progressive disclosure) and ``GET /v1/app-search`` (the installed-app inventory):
the lazy, fingerprint-keyed verb-embedding cache and its disk persistence, the
per-MCP-tool embedder, and the app-inventory refresh/embed loop. Both routes stay
in ``server.py`` as thin wrappers calling :func:`tool_search_logic` /
:func:`app_search_logic` here.

The cosine metric (``_cosine``) and the verb embed-text / fingerprint helpers are
owned here now (maximally cohesive with the verb-embedding cache). Only the
per-vector embedder ``_embed_one`` stays server-resident -- it drives the HTTP
embed lane via the injected client -- and is injected via :func:`configure`,
together with the HTTP client factory, the verb catalog, the MCP-client registry +
lock, and the lenient JSON loader. This module never imports ``server`` (one-way
boundary, 98-drift-checks check 6 ``check_module_boundary``); ``server.py`` re-imports every moved name under
its original alias (and re-injects the cosine / verb-embed helpers into the other
planes that depend on them) so the importable surface is byte-identical.

<!-- mios-src:9775108b1e1e from usr/lib/mios/agent-pipe/mios_pipe/routing/toolsearch.py:4-23 -->

### Hash over every embeddable verb's (key, embed-text). Any...

Hash over every embeddable verb's (key, embed-text). Any rename / desc edit /
    example change flips it -> the persisted cache is rebuilt instead of serving stale
    vectors (the old gap-fill loader only added NEW verbs; it never noticed a changed
    description, so a re-described verb kept its old embedding forever).

<!-- mios-src:58734c591ff4 from usr/lib/mios/agent-pipe/mios_pipe/routing/toolsearch.py:120-123 -->

### Embed every registered MCP tool not yet in _MCP_EMBEDDINGS...

Embed every registered MCP tool not yet in _MCP_EMBEDDINGS (best-effort, off the
    hot path -- called at the end of a server probe). Degrade-open: an embed outage just
    leaves the tool on its name-keyword priority fallback, never breaks the surface.

<!-- mios-src:f9ef7e47179a from usr/lib/mios/agent-pipe/mios_pipe/routing/toolsearch.py:147-149 -->

### Find verbs + external MCP tools by natural-language query...

Find verbs + external MCP tools by natural-language query (cosine over the verb
    and MCP embeddings; substring fallback when embeddings are down). P3 progressive
    disclosure: optional `namespace` (e.g. browser_/duckdb_/pg_) and `tier`
    (core/common/rare) FILTERS to scope a large catalog, and `detail_level` --
    full (name+sig+desc+tier+namespace, the back-compat default) | brief (name+desc+tier)
    | names (name only) -- to trade tokens for breadth. Embeddings cached after first use.

<!-- mios-src:8ea023df4574 from usr/lib/mios/agent-pipe/mios_pipe/routing/toolsearch.py:499-504 -->

### PER-TURN message-prep + agent-selection helpers...

PER-TURN message-prep + agent-selection helpers (strangler-fig refactor).

Extracted VERBATIM from ``server.py``. These are the small cohesive turn-prep
helpers the chat router + responders call each turn: last-user-text extraction,
role-based sub-agent selection (with degrade-open on a dead gated node), the
generic agent surface label, the per-turn live-agent roster (health-probed +
TTL-cached), and the <think>-tag reasoning/answer split. Every server-resident
symbol -- the live agent registry, the node-liveness cache, the health-probe +
probe-auth helpers, the liveness TTL/connect scalars, and the think-tag regexes
-- is injected via :func:`configure` (one-way boundary -- this module never
imports ``server``). ``server.py`` re-imports each name under its original alias
so the importable surface stays byte-identical.

<!-- mios-src:2ff157824ebb from usr/lib/mios/agent-pipe/mios_pipe/routing/turn.py:4-16 -->

### Inject server-side deps under their EXACT original names...

Inject server-side deps under their EXACT original names (one-way boundary).

    Called from ``server.py`` after every injected symbol is defined, and again
    from ``_reload_membership`` to re-bind ``_AGENT_REGISTRY`` after a live add/drop.
    Each keyword equals the module global it sets.

<!-- mios-src:a04f508515b4 from usr/lib/mios/agent-pipe/mios_pipe/routing/turn.py:47-52 -->

### Set of agent names currently USABLE for dispatch ( "iGPU is...

Set of agent names currently USABLE for dispatch (
    "iGPU is down"). Non-health_gate agents are ALWAYS live -- they are local
    lanes whose failure is a separate, louder problem and probing them every
    turn only adds latency. Only health_gate client/Tailscale nodes (the iGPU,
    a phone) -- the ones that legitimately come and go -- are connect-probed,
    TTL-cached in _NODE_LIVE so an OUTAGE drops the node from the swarm roster
    WITHOUT re-probing every turn (it rejoins within the TTL once back up).
    Used to prune dead nodes before the planner/DAG assigns them a facet, so the
    freed concurrent lane re-routes to live compute instead of vanishing.

<!-- mios-src:df5db8813728 from usr/lib/mios/agent-pipe/mios_pipe/routing/turn.py:60-68 -->

### Pick a sub-agent by role match. Order

Pick a sub-agent by role match. Order: exact-role -> default
    -> first registered. Returns (name, cfg).

 Degrade-open (install-robustness): if the chosen agent is a
    health_gate (come-and-go) node -- e.g. the :8643 hermes-worker bound to the
    heavy GPU lane, which is gated off by default -- that the liveness cache does
    NOT confirm reachable, blank its endpoint so the caller's `endpoint or
    BACKEND` falls back to the always-on local lane. Without this the PRIMARY
    dispatch went to a dead gated worker -> httpx "All connection attempts
    failed" -> 502 on EVERY turn on any host where that lane is down (a fresh
    dev VM, a CPU host). The worker is still used the moment the probe confirms
    it live (heavy lane enabled).

<!-- mios-src:e497f81c02cf from usr/lib/mios/agent-pipe/mios_pipe/routing/turn.py:113-124 -->

### Split model output into (reasoning, answer). 'there SHOULD...

Split model output into (reasoning, answer).

 'there SHOULD be thinking -- as a dropdown' AND
    'thinking bleeding into the final response makes it look like it
    answered twice'. The fix is to CAPTURE the <think>-family reasoning
    (so it can go in a collapsed dropdown) instead of discarding it, and
    return the answer with the reasoning removed (clean main reply).
    Handles closed + unclosed + orphan tags across the qwen3 <think> and
    <thinking>/<thought>/<reasoning>/<reflection>/<scratchpad> variants.
    Tag-based only -- structural, no English content matching.

<!-- mios-src:446930282ae5 from usr/lib/mios/agent-pipe/mios_pipe/routing/turn.py:150-159 -->

### Map registered sub-agent name -> casual MiOS-convention...

Map registered sub-agent name -> casual MiOS-convention label
    for SSE status emission + dropdown summaries. Operator binding:
    surface labels stay generic ('sub-agent' / role), the specific
    daemon name lives in event payloads + journal, not in the chat
    UI. Same agent can be renamed via mios.toml [agents.*] without
    leaking the old name to the operator's screen.

<!-- mios-src:ef2fdfc3553c from usr/lib/mios/agent-pipe/mios_pipe/routing/turn.py:187-192 -->

### Verb/recipe catalog loader + the three-projection SSOT...

Verb/recipe catalog loader + the three-projection SSOT source.

Extracted verbatim from ``server.py``. Parses the ``mios.toml`` ``[verbs.*]`` and
``[recipes.*]`` sections into the canonical catalogs and projects them into the
planner prose block, the OpenAI/MCP function-tool schemas, and the model_name /
hidden_alias reverse map. Every function is moved byte-for-byte; ``server.py``
re-imports each under its original ``_``-prefixed name so the importable surface
is unchanged.

The HOT globals ``_VERB_CATALOG`` and ``_MODEL_NAME_TO_VERB`` are OWNED by
``server.py`` (it runs the assignments by calling the re-imported builders) and
injected here via :func:`configure` AFTER they are built, so the catalog readers
(``_resolve_verb_key``, ``_identity_answer``, ``_load_verb_arg_synonyms``) see the
live catalog. ``CATALOG_FAIL_MODE`` is injected before the first catalog build.
One-way module boundary: this module never imports ``server``.

<!-- mios-src:9220873cfcff from usr/lib/mios/agent-pipe/mios_pipe/routing/verbcatalog.py:4-19 -->

### Deterministic reply to "who are you / what can you do"...

Deterministic reply to "who are you / what can you do", built from the LIVE
 capability catalog + a generic persona intro (the 14B
    confabulated its identity from the literal model name -- "Zabbix agent",
    "Mio's Pizza" -- and varied wildly run to run, because a small model cannot be
    trusted to self-describe). Composed deterministically, like the `remember`
    handler. All specifics come from _VERB_CATALOG (the mios.toml [verbs.*] SSOT),
    so the reply is accurate AND baked: a freshly-imaged Day-0 agent describes
    itself correctly with zero chat history. Returns '' if no catalog is loaded.

<!-- mios-src:5fe3db3b5887 from usr/lib/mios/agent-pipe/mios_pipe/routing/verbcatalog.py:491-498 -->

### P1 PA-Tool reverse map {model_name -> canonical verb key}...

P1 PA-Tool reverse map {model_name -> canonical verb key} for every verb that
    declares a model_name alias. The model emits tool_calls under the alias; dispatch +
    the permission gate + the tier/selection lookups resolve it back to the key. A
    collision (alias == a real verb key, or two verbs claim the same alias) is logged and
    the offending alias dropped -- real keys always win, so a bad alias degrades to the
    key being shown, never to a mis-dispatch.

<!-- mios-src:32329cdffeb2 from usr/lib/mios/agent-pipe/mios_pipe/routing/verbcatalog.py:520-525 -->

### Parse mios.toml [recipes.*] -> {name: {description, args...

Parse mios.toml [recipes.*] -> {name: {description, args, permission}}.
    SSOT for the os_recipe verb. Rendered into the planner prompt so EVERY
    recipe is natively discoverable by every agent -- no recipe names baked
 in code ("ALL agents know to use these functions";
    "no hardcodes unless modelfile/docs"). Add a [recipes.*] block in TOML
    and it appears here + in every consumer automatically (self-iterating).

<!-- mios-src:406ad1e1993b from usr/lib/mios/agent-pipe/mios_pipe/routing/verbcatalog.py:585-590 -->

### Render one [recipes.*] entry as an OpenAI function-tool...

Render one [recipes.*] entry as an OpenAI function-tool schema --
    the SAME `{type:function, function:{name,description,parameters}}` shape
    as _verb_to_openai_tool / _skill_to_openai_tool. The function name is
    mangled to `mios_recipe__<name>` so a relay (mios-mcp-server) can route a
    returned tool_call back through the opaque `os_recipe` verb -- strip the
    prefix, then POST /v1/dispatch {tool:'os_recipe', args:{name, params}}.
    Recipe args are free-form per [recipes.*].args (SSOT in mios.toml); every
    arg is exposed as a string property, plus an optional `os` selector (some
    recipes branch on the target OS). No arg is marked required -- recipes
    fill sensible defaults, and the os_recipe verb tolerates a partial
    params map. Discover here, execute via os_recipe at /v1/dispatch.

<!-- mios-src:400012602b1b from usr/lib/mios/agent-pipe/mios_pipe/routing/verbcatalog.py:625-635 -->

### Render one [verbs.*] entry as an OpenAI function-tool...

Render one [verbs.*] entry as an OpenAI function-tool schema --
    the SAME `{type:function, function:{name,description,parameters}}`
    shape Hermes/OpenCode already consume from /skills/openai-tools (see
    _skill_to_openai_tool). Tool name == the bare verb name, so a returned
    tool_call executes verbatim via POST /v1/dispatch {tool, args} (the
    launcher-broker path the MCP server also uses). No name mangling ->
    discover here, execute there, one contract.

<!-- mios-src:f0a9e9d59ed5 from usr/lib/mios/agent-pipe/mios_pipe/routing/verbcatalog.py:685-691 -->

### VISION + CLIENT-TOOLS responders (refactor R9). Extracted...

VISION + CLIENT-TOOLS responders (refactor R9).

Extracted VERBATIM from ``server.py`` -- the two image-/tool-bearing fast-path
branches of ``/v1/chat/completions`` that bypass refine/council/polish. The
VISION branch (``_vision_complete`` + the inline-remote-image pre-step + the
honest-error gate) proxies an image turn to the local VLM and never fabricates a
description. The CLIENT-TOOLS hybrid loop (``_client_tools_complete`` and its
cluster) runs an OpenAI client-tools turn where MiOS asserts its identity, merges
its verb surface server-side, executes MiOS verbs via the broker, and rides only
the caller's own tool_calls back. Both clusters moved byte-identically.

Sibling helpers are imported directly; every server-side symbol is injected via
:func:`configure` (one-way boundary -- this module never imports ``server``).
``server.py`` re-imports every moved name under its original alias so the
importable surface is byte-identical.

<!-- mios-src:54abcbacdadb from usr/lib/mios/agent-pipe/mios_pipe/routing/vision.py:4-19 -->

### True if any message carries OpenAI vision content (a...

True if any message carries OpenAI vision content (a content list with
    an image_url / input_image part) -- the signal to route this turn to the
    local VLM instead of the text executor (which cannot see images).

<!-- mios-src:2935f6240fc4 from usr/lib/mios/agent-pipe/mios_pipe/routing/vision.py:98-100 -->

### Resolve a media-asset URL from a page's HTML metadata --...

Resolve a media-asset URL from a page's HTML metadata -- GENERIC (JSON-LD
    contentUrl, og:image, og:video, twitter:image), no site-specific keyword, so it
    works for Tenor/Imgur/etc. First hit wins (operator rule: no hardcoded domains).

<!-- mios-src:cca5b62281b8 from usr/lib/mios/agent-pipe/mios_pipe/routing/vision.py:161-163 -->

### Rewrite remote image_url URLs in `messages` to INLINED...

Rewrite remote image_url URLs in `messages` to INLINED base64 data URLs the
    local llama.cpp VLM can actually see (it doesn't fetch URLs + rejects page URLs).
    Per image: fetch the URL; if it's a PAGE (text/html, e.g. a Tenor GIF page),
    resolve to its real media via HTML metadata then fetch that; for an animated
    GIF/WEBP extract a middle frame (Pillow); re-encode to PNG; inline. Mutates
    `messages` in place. Returns False if a REMOTE image could NOT be inlined, so the
    caller returns an honest 'couldn't fetch' turn instead of letting the VLM guess.
    Already-inlined data: URLs (OWUI) and non-image parts are untouched (no regress).

<!-- mios-src:403353c36f89 from usr/lib/mios/agent-pipe/mios_pipe/routing/vision.py:180-187 -->

### Proxy an image-bearing turn to the local VLM...

Proxy an image-bearing turn to the local VLM (OpenAI-compatible, on the
    dGPU lane). Streams the VLM SSE verbatim; non-stream returns its JSON. When
    the vision model is unprovisioned / fails to load, returns an HONEST 'vision
 unavailable' assistant turn instead of relaying a raw 5xx (
    'FIX ALL VISION' -- the confusing leaf error was the reported failure).

<!-- mios-src:e3b078589475 from usr/lib/mios/agent-pipe/mios_pipe/routing/vision.py:246-250 -->

### True when the CALLER supplied its own OpenAI tools[] -- the...

True when the CALLER supplied its own OpenAI tools[] -- the signal that this
    is client-side tool-calling (the client executes the functions and wants
    tool_calls back), NOT a MiOS-orchestrated turn. OWUI strips tools before
    calling the pipe and the mios CLI is Hermes-direct, so this is False for them
    (zero regression). Empty/missing tools -> False (normal orchestration).

<!-- mios-src:4e8dd585c337 from usr/lib/mios/agent-pipe/mios_pipe/routing/vision.py:312-316 -->

### A returned tool_call is MiOS-executable SERVER-SIDE when it...

A returned tool_call is MiOS-executable SERVER-SIDE when it resolves to a real
    MiOS verb -- EVEN IF the client also shipped it. The Hermes desktop app ships the
    WHOLE MiOS MCP surface (launch_windows_app, windows_desktop_type_text, ...) as its
    own tools; relaying those back for it to self-execute via MCP was the failure path
    ('open notepad and type hello' mis-fired -- malformed/parallel calls, nothing ran,
). Running MiOS verbs HERE via the proven broker (dispatch_mios_
    verb) is reliable, ORDER-preserving, and does NOT double-execute (the loop appends
    the RESULT, not the tool_call, so nothing rides back for the client to re-run).
    Only genuinely non-MiOS client tools (browser_*, terminal, IDE ops) -- which the
    server CANNOT run -- ride back to the caller.

<!-- mios-src:875be6931f08 from usr/lib/mios/agent-pipe/mios_pipe/routing/vision.py:361-370 -->

### Prepend the FULL MiOS root contract (/MiOS.md via...

Prepend the FULL MiOS root contract (/MiOS.md via _agent_contract) PLUS the
    client-tools addendum to the caller's leading system message (or add one).
    WS-B: the Zen path now gets the SAME root-MD grounding every other MiOS agent
    gets, instead of drifting on a bespoke identity string. Server-side only -- the
    client never sees it, so it can't accumulate across the multi-request loop.

<!-- mios-src:b89537e47c56 from usr/lib/mios/agent-pipe/mios_pipe/routing/vision.py:380-384 -->

### One non-stream POST to the tool backend, with heavy->light...

One non-stream POST to the tool backend, with heavy->light FALLBACK on any
    non-200 + diagnostic logging. The heavy lane (SGLang) can 400 a tool surface it
 rejects (the Hermes REPL got 'No reply' because the loop
    treated a heavy-lane 400 as an empty completion). On a non-200 we LOG the body +
    a request summary (so the cause is finally visible) and retry the always-on light
    lane (a different engine often accepts what the heavy lane rejected). Returns {}
    (never raises) when neither lane yields a 200, so the loop's synthesis / never-
    empty fallback engages instead of the whole turn erroring out.

<!-- mios-src:b702f79c4173 from usr/lib/mios/agent-pipe/mios_pipe/routing/vision.py:396-403 -->

### STREAM the backend response verbatim for a full-agent...

STREAM the backend response verbatim for a full-agent client that carries its
    OWN MiOS tools (Hermes desktop app): inject MiOS identity, enable thinking, forward
    the client's tools, and relay the SSE byte-for-byte so content / reasoning /
    tool_calls stream LIVE -- no compute-then-burst dead wait. The client executes its
    own tool_calls in its own loop (it has the tools), so no server-side merge is
    needed; that merge is only for tool-less clients (Zen) via the hybrid loop.

<!-- mios-src:19d09ac17f1e from usr/lib/mios/agent-pipe/mios_pipe/routing/vision.py:621-626 -->

### OpenAI client-tool turn (Zen smart-window et al.) as a...

OpenAI client-tool turn (Zen smart-window et al.) as a HYBRID loop: MiOS
    asserts its own identity, the MiOS verb surface is merged alongside the
    caller's browser tools, MiOS verbs execute server-side (so 'open notepad'
    actually launches), and only the caller's own tool_calls ride back to it.
    Falls back to a verbatim relay if the loop errors so browsing never regresses.
    NEVER runs refine/council/polish. Twin of _vision_complete.

<!-- mios-src:2428458e8e4b from usr/lib/mios/agent-pipe/mios_pipe/routing/vision.py:663-668 -->

### Pipeline-side WEB-RESEARCH enrichment

Pipeline-side WEB-RESEARCH enrichment: search -> multi-engine fetch -> judge.

Extracted verbatim from ``server.py``. ``_web_research_enrich`` runs the FULL
web toolchain itself (SearXNG metasearch with fan-out, concurrent web_extract +
crawl4ai + Firecrawl fetch race, a 2-hop article-link drill) under a
MODEL-driven satisfaction gate (``_judge_satisfied``) that is the load-bearing
anti-fabrication Definition-of-Done -- it decides when enough REAL evidence was
gathered instead of letting the swarm fabricate. The functions are unchanged;
``server.py`` re-imports every name under its original alias so the public
surface is byte-identical. Every server-side runtime helper, request contextvar
and ``WEB_RESEARCH_*``/``_JUDGE_*`` config constant the moved code reads is
dependency-injected via :func:`configure` (one-way module boundary -- this
module never imports ``server``); ``_loads_lenient`` is imported directly from
``mios_jsonsalvage``.

<!-- mios-src:60fb92b4b206 from usr/lib/mios/agent-pipe/mios_pipe/routing/web_research.py:4-18 -->

### Resolve the anchor stopword screen from SSOT: a CSV env...

Resolve the anchor stopword screen from SSOT: a CSV env override (rendered from
    mios.toml by the userenv slot map) -> the layered mios.toml [search].anchor_stopwords
    -> empty (degrade-open: no baked list in code, never over-filter). Lowercased.

<!-- mios-src:078abb7955e8 from usr/lib/mios/agent-pipe/mios_pipe/routing/web_research.py:185-187 -->

### Resolve the article-link scorer's mode + weights/thresholds...

Resolve the article-link scorer's mode + weights/thresholds from SSOT
    (mios.toml [web_research]) layered over the degrade-open defaults. Each key
    falls back INDEPENDENTLY, so a partial or malformed [web_research] table still
    yields the byte-identical structural ranking for every key it omits. Never
    raises (degrade-open): any read/parse error returns the full defaults.

<!-- mios-src:3502bb52559d from usr/lib/mios/agent-pipe/mios_pipe/routing/web_research.py:245-249 -->

### Structural 'real-headline' ranker...

Structural 'real-headline' ranker (link_rank_mode='heuristic', the default).
    Scores each (anchor_text, url) candidate by URL STRUCTURE ONLY -- path depth, a
    long hyphenated headline slug, a date/id digit, and a long anchor -- with NO
    hardcoded domain/keyword/topic list. Every weight/threshold/cutoff/top-N comes
    from `cfg` (SSOT via _link_rank_cfg); the default `cfg` reproduces today's ranking
    byte-for-byte. Returns the top-N article URLs, score-descending.

<!-- mios-src:c560d3298e3d from usr/lib/mios/agent-pipe/mios_pipe/routing/web_research.py:275-280 -->

### OPT-IN embedding-cosine link ranker...

OPT-IN embedding-cosine link ranker (link_rank_mode='embed'). STUB: no
    embeddings client is reachable from THIS module today (the embeddings lane lives
    behind the agent-pipe broker, not imported here), so this returns None to
    DEGRADE-OPEN to the structural ranker. The hook exists so enabling model ranking
    is an SSOT flip + a wired embed client -- never a fabricated/invented path. A real
    impl would cosine each candidate's anchor/url text against the turn's topical
    `anchor` (or query) embedding and return the top-N URLs.

<!-- mios-src:4bd17b806e25 from usr/lib/mios/agent-pipe/mios_pipe/routing/web_research.py:315-321 -->

### Rank candidate article links per the SSOT link_rank_mode....

Rank candidate article links per the SSOT link_rank_mode. Default 'heuristic'
    = the structural ranker. A non-default mode is tried first and DEGRADES OPEN to
    the structural ranker on a None result or ANY error (operator binding: a mode flip
    never breaks the drill).

<!-- mios-src:85514a343616 from usr/lib/mios/agent-pipe/mios_pipe/routing/web_research.py:327-330 -->

### Pipeline-side WEB-RESEARCH loop ("the MiOS pipeline ITSELF...

Pipeline-side WEB-RESEARCH loop ("the MiOS pipeline
    ITSELF loops for web use and web tools"). For a web-needing turn the PIPELINE
    runs the web toolchain itself: SearXNG web_search WITH FAN-OUT (multiple
    diverse sub-queries) then web_extract the top result pages for their REAL
    text, over WEB_RESEARCH_PASSES drill passes. The fetched content is injected
    as grounding for EVERY agent (primary + reasoning-only secondaries), so the
    swarm answers from actual stories instead of shallow homepage snippets,
    regardless of any single agent's tool-loop depth. Best-effort + bounded;
    '' when disabled / not a web turn / nothing fetched.

<!-- mios-src:5dbc814c4c6d from usr/lib/mios/agent-pipe/mios_pipe/routing/web_research.py:358-366 -->

### Record real (title,url) pairs from a web_search/extract...

Record real (title,url) pairs from a web_search/extract result list into BOTH
    the turn-scoped contextvar bucket AND the module-level registry (keyed by the
    turn key) so the parent finalize sees sources collected by child agents too.
    Degrade-open: odd shape / no turn key -> safe no-op.

<!-- mios-src:f96ab7882d52 from usr/lib/mios/agent-pipe/mios_pipe/routing/web_research.py:833-836 -->

### OpenAI url_citation annotations (Chat/Responses parity)...

OpenAI url_citation annotations (Chat/Responses parity): one
    {type:'url_citation', url, title, start_index, end_index} per cited source.
    start/end are char offsets into `text` where the URL appears inline (so a UI
    renders a clickable cite); 0/0 when the source is a turn-source not inlined.
    This is OpenAI's canonical citation contract -- attaching it lets MiOS clients
 render web citations the same way ChatGPT does. web-tools hardening.

<!-- mios-src:b306aaabfe61 from usr/lib/mios/agent-pipe/mios_pipe/routing/web_research.py:905-910 -->

### OpenAI grounding rule

OpenAI grounding rule: 'include only search results/citations that support
    the cited response text -- irrelevant sources permanently degrade user trust.'
    Keep a source only when its title shares a content word (>=4 chars) with the
    answer/query, OR its registrable-domain stem appears in them. DEGRADE-OPEN: if
    the filter would drop EVERYTHING (the answer echoed no source token), return the
    originals -- never strip citations to empty. Kills the off-topic-source bleed
 (a Fedora answer citing 'Shaolin monks'). web-tools hardening.

<!-- mios-src:903c4a53bef3 from usr/lib/mios/agent-pipe/mios_pipe/routing/web_research.py:932-938 -->
### Both are baked into the image; if either is absent, fail...

Both are baked into the image; if either is absent, fail with a legible
dependency error instead of a NameError leaking out of the render path.

<!-- mios-src:be6c567e45a7 from usr/lib/mios/agent-pipe/mios_pipe/routing/conductor.py:14-15 -->

### Map a workflow name to its YAML under CONDUCTOR_DIR. The...

Map a workflow name to its YAML under CONDUCTOR_DIR.

    The name arrives from model-refined dispatch input, so it is untrusted:
    reject anything that is not a bare name, then confirm the resolved path
    really sits inside CONDUCTOR_DIR (catches symlink escapes) BEFORE the file
    is read and its shell steps are executed.

<!-- mios-src:607474d3f265 from usr/lib/mios/agent-pipe/mios_pipe/routing/conductor.py:52-57 -->

### One [ports] value: MIOS_PORT_* -> [ports].<key> -> the SSOT...

One [ports] value: MIOS_PORT_* -> [ports].<key> -> the SSOT literal.

    Law 5/7: the literal is the SSOT's own number (check_port_fallbacks holds the
    two in agreement), not a stale one -- the previous defaults named stale and
    retired ports. A set-but-EMPTY env var (bare `KEY=` in install.env, which
    Law 10 permits) must not win over the config tier, and a non-numeric value
    must not 500 the portal page, so each candidate is tried in turn.

<!-- mios-src:2bd0469dd60c from usr/lib/mios/agent-pipe/mios_pipe/routing/portal.py:66-72 -->

### Resolve the anchor stopword screen from SSOT...

Resolve the anchor stopword screen from SSOT: MIOS_SEARCH_ANCHOR_STOPWORDS, the
    CSV the resolver emits for [search].anchor_stopwords (Law 9: one canonical name, so
    the override is reachable) -> that table -> empty (degrade-open). Lowercased.

<!-- mios-src:1c3a403a2d2f from usr/lib/mios/agent-pipe/mios_pipe/routing/web_research.py:163-165 -->
### Interruptible chunked decode (WS-A12). Enforces unified...

Interruptible chunked decode (WS-A12). Enforces unified lock order
    _GLOBAL_PRIORITY_GATE -> endpoint semaphore -> lane semaphore.
    Across preemption, releases lane + endpoint semaphores before yielding the
    priority gate, ensuring no dispatch holds an endpoint permit while waiting
    on the gate (task_3483). Returns the full assistant text. Degrade-open: ANY failure
    falls back to one completion of the whole budget; the partial is never lost.

<!-- mios-src:4358e2ec6536 from usr/lib/mios/agent-pipe/mios_pipe/routing/agent_call.py:984-989 -->

### Ordered lane ids for a route. The heavy lane is one lane...

Ordered lane ids for a route. The heavy lane is one lane whatever engine
    [ai].heavy_engine runs on it, so the value only steers the ROUTE: "light"
    skips the heavy lane, a comma list is an explicit order, and anything else
    (an engine name such as "vllm"/"sglang", or empty) puts the heavy lane first.
    Inside a comma list an engine name -- any id that is not an available lane --
    means the heavy lane. "light" is always the terminal floor.

<!-- mios-src:e6bdb1072ec4 from usr/lib/mios/agent-pipe/mios_pipe/routing/lanes.py:25-30 -->

### (url, model) for the client-tools loop -- delegated to the...

(url, model) for the client-tools loop -- delegated to the WS-1 unified lane
    resolver: the heavy lane when reachable, else the always-on light lane (with
    per-lane cooldown so a dead lane fails over, never 404s). Degrade-open: any resolver error falls back to the legacy heavy/light
    probe so the agentic surface never hard-fails.

<!-- mios-src:d029438c1e1c from usr/lib/mios/agent-pipe/mios_pipe/routing/lanes_resolver.py:111-114 -->

### Minimum candidate entities a section must carry before its...

Minimum candidate entities a section must carry before its grounding is
    judged; below this the signal is too thin to trust -> degrade-open. SSOT:
    [verity].antifab_min_entities -> MIOS_VERITY_ANTIFAB_MIN_ENTITIES (live).

<!-- mios-src:9d94ccc00719 from usr/lib/mios/agent-pipe/mios_pipe/routing/native_loop.py:54-56 -->

### Stretch password-derived keys once per worker; explicit...

Stretch password-derived keys once per worker; explicit secrets stay stable.

    A fixed, versioned domain separator preserves restart/multiworker agreement
    without storing another secret. Changing the password or work factor revokes
    derived sessions. Upgrade from the old single-hash key requires a new login.

<!-- mios-src:bffebc2d19b0 from usr/lib/mios/agent-pipe/mios_pipe/routing/portal.py:100-105 -->

### Context window for the client-tools lane (env...

Context window for the client-tools lane (env MIOS_AGENT_PIPE_TOOL_CTX).
    An unparsable or non-positive value falls back to the default instead of
    raising out of the degrade path.

<!-- mios-src:8b06b1414786 from usr/lib/mios/agent-pipe/mios_pipe/routing/vision_context.py:22-24 -->

### Restore an OpenAI-valid sequence after messages were...

Restore an OpenAI-valid sequence after messages were dropped.

    * every assistant tool_call keeps a matching tool result (unanswered calls
      are removed from the assistant message);
    * every tool result answers a tool_call of the nearest preceding assistant
      turn (orphans are dropped);
    * the first non-system turn is a user turn;
    * consecutive plain user/assistant turns are merged so roles alternate.

<!-- mios-src:8e13e59da0d0 from usr/lib/mios/agent-pipe/mios_pipe/routing/vision_context.py:40-48 -->

### Principled multi-tier request pruning before backend...

Principled multi-tier request pruning before backend dispatch so the
    payload fits the backend context instead of failing with HTTP 400.

    Tiers (each runs only while still over budget):
      1. tool_choice 'none' -> drop tools and tool_choice.
      2. Elide stale tool results (placeholder keeps tool_call pairing).
      3. Compact middle turns via plan_compaction, then repair the sequence.
      4. Budget tool definitions: compact schemas, then keep tools in caller
         order (forced tool always kept) within half the budget.
      5. Truncate each oversized message (content-part aware).
      6. Iteratively truncate the largest message.
    Finally repair the sequence and clamp max_tokens to what actually fits.

<!-- mios-src:296e360d4944 from usr/lib/mios/agent-pipe/mios_pipe/routing/vision_context.py:216-228 -->
