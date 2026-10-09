<!-- AI-hint: Manual pages distilled from the source comments of memory, sanitized, each passage anchored to the comment it came from. -->

# memory

### mios_embed_backfill -- embedding-version hygiene for the...

mios_embed_backfill -- embedding-version hygiene for the MiOS agent-pipe
(WS-A2, the AIOS Memory-Manager embedding-identity layer).

Pure stdlib so it unit-tests in isolation, in the sibling-module style of
mios_sched / mios_pdp. server.py (or a maintenance CLI) owns the DB I/O and the
embedding call; this module owns only the DECISIONS: is a row's vector stale,
which rows are candidates, and how to batch the work so a backfill never
stampedes the embedder or the DB.

Why versioning
==============
Every embedded row carries emb_model + emb_version. The embedding space is only
comparable WITHIN one identity: if the model (or its dimensionality) changes,
old vectors are meaningless under the new model, so cosine recall silently
returns garbage neighbours. Tagging each row lets a backfill find + re-embed the
stale rows off the hot path, and lets recall optionally restrict to the current
identity until the backfill catches up.

<!-- mios-src:fd01946b3519 from usr/lib/mios/agent-pipe/mios_pipe/memory/embed_backfill.py:4-21 -->

### Tiered pgvector KNOWLEDGE memory

Tiered pgvector KNOWLEDGE memory: store + recency-weighted recall + eviction.

Extracted verbatim from ``server.py``. The store/recall/evict functions are
unchanged; ``server.py`` re-imports every name under its original alias so the
public surface is byte-identical. Pure eviction SQL/plan logic lives in
``mios_evict``; the Postgres+pgvector client is ``mios_pg``; the write-time
memory-poisoning scan is ``mios_memguard``. Every server-side runtime helper,
request contextvar and ``KNOWLEDGE_*``/``EMB_*`` config constant the moved code
reads is dependency-injected via :func:`configure` (one-way module boundary --
this module never imports ``server``).

<!-- mios-src:b382d8f44c00 from usr/lib/mios/agent-pipe/mios_pipe/memory/knowledge.py:4-14 -->

### Effective cosine floor for a recall query. A...

Effective cosine floor for a recall query. A self-referential ask about
    the user's own stored state (a possessive pronoun present) uses the LOWER
    preference floor, because the question template cosines below a stored
    statement of the same fact. Signal is purely structural (possessive present),
    never raises the floor above the default, and is tunable off by setting
    recall_pref_min_score == recall_min_score in mios.toml [knowledge].

<!-- mios-src:587d187df8ee from usr/lib/mios/agent-pipe/mios_pipe/memory/knowledge.py:185-190 -->

### Bounded multiplicative recency factor in [1 - rank_age...

Bounded multiplicative recency factor in [1 - rank_age, 1.0] for a recalled
    row: 1.0 when brand-new, -> (1 - rank_age) as age >> half-life. rank_age == 0
    (default) -> 1.0 (inert). See KNOWLEDGE_RECALL_HALFLIFE_DAYS for the rationale.

<!-- mios-src:ec113ee80f31 from usr/lib/mios/agent-pipe/mios_pipe/memory/knowledge.py:247-249 -->

### Blended recall score SHARED by every tiered-recall path...

Blended recall score SHARED by every tiered-recall path (knowledge pgvector,
    knowledge legacy fallback, agent_memory) so the tiers rank CONSISTENTLY instead of each
    re-deriving the blend. Cosine SIMILARITY (the row's ``score``) stays dominant; the
    outcome / tier / access signals are each added with their ``[knowledge]`` rank_*
    SSOT weight, and the whole is scaled by the bounded recency multiplier
    (``_recency_mult``, driven by ``[knowledge]`` rank_age / recall_halflife_days). A
    stale-but-relevant hit still wins on cosine; the blend only re-orders near-ties.

    DEGRADE-OPEN: a row missing a signal column reads it as absent via ``.get()`` and
    that term contributes its NEUTRAL value (no ``satisfied`` -> outcome 0; no ``tier``
    -> hot 0; no ``access_count`` -> access 0; no ``last_access``/``ts`` -> recency 1.0),
    so a tier like agent_memory -- which carries only cosine + ts -- ranks on exactly
    the signals it has and never crashes. Any error falls back to pure cosine.

<!-- mios-src:6946733db188 from usr/lib/mios/agent-pipe/mios_pipe/memory/knowledge.py:263-275 -->

### Compact, auditable source list for a stored answer: the...

Compact, auditable source list for a stored answer: the verbs the
    turn invoked + any URLs they touched (web_search / web_extract args +
    result previews). A recalled answer then carries WHERE it came from
    instead of being an unattributed assertion.

<!-- mios-src:b69551e94a16 from usr/lib/mios/agent-pipe/mios_pipe/memory/knowledge.py:292-295 -->

### Persist a finished Q+A (with derived sources + a query...

Persist a finished Q+A (with derived sources + a query embedding for
    recall) to the global knowledge table, fire-and-forget. NEVER raises -- a
    storage failure must not affect the answer the operator already received.

    P2: `satisfied` (the turn's Definition-of-Done verdict, or None when not
    available in scope) is stored as an outcome signal the blended recall rank
    can weight. None -> the field is simply omitted (degrade-open).

<!-- mios-src:5ba13de2aac3 from usr/lib/mios/agent-pipe/mios_pipe/memory/knowledge.py:319-325 -->

### Embed the question (so recall is a cheap cosine) then write...

Embed the question (so recall is a cheap cosine) then write the row.
    Embedding is best-effort: a miss just stores the row without `emb` -- still
    persisted + auditable, just not semantically recallable.

    P2 tiering fields are seeded at write time: access_count/recall_hits at 0
    (so the page-in bump's `(field ?? 0) + 1` has a base + plain reads are
    NULL-safe), tier='warm' (neutral default; hot/cold transitions are a
    deferred P2 pass), and `satisfied` (omitted by _db_create when None).

<!-- mios-src:01a5b6733049 from usr/lib/mios/agent-pipe/mios_pipe/memory/knowledge.py:348-355 -->

### WS-9c native pgvector recall (used when...

WS-9c native pgvector recall (used when DB_BACKEND='postgres'). Returns the
    injectable block, '' on a clean miss, or None to fall through to the
    legacy fallback path on any error (degrade-open). #59 WS-5: scoped to the request
    principal when [pgvector].rls_mode == 'enforce' (else unfiltered).

<!-- mios-src:8c17e2b9c5e4 from usr/lib/mios/agent-pipe/mios_pipe/memory/knowledge.py:413-416 -->

### Semantic recall of PRIOR stored answers relevant to...

Semantic recall of PRIOR stored answers relevant to `query`: embed the
    query, cosine it against the query-embeddings of recent knowledge rows,
    return the top-K above a threshold as an injectable context block (or '' on
    miss). Best-effort, never blocks the turn -- the read half of the
 store/recall loop. Recalled answers are framed as
    PRIOR/own knowledge that may be outdated, never as fresh ground truth.

<!-- mios-src:c68cbcb0f334 from usr/lib/mios/agent-pipe/mios_pipe/memory/knowledge.py:478-483 -->

### WS-A3

WS-A3: best-effort COUNT over the EVICTABLE knowledge set via parameterized
    Postgres (mios_pg). Degrade-open -> 0. (Was SurrealQL via _db_post, which
    no-op'd under db_backend=postgres -> eviction never ran.)

<!-- mios-src:39fe6d2c33a2 from usr/lib/mios/agent-pipe/mios_pipe/memory/knowledge.py:582-584 -->

### One K-LRU + TTL eviction sweep. DRY-RUN (evict_enable off)...

One K-LRU + TTL eviction sweep. DRY-RUN (evict_enable off) only COUNTS +
    LOGS what it WOULD remove; otherwise it DELETEs (bounded by the batch).
    Degrade-open: any DB error -> no-op. Returns a small report (observability/
    tests).

<!-- mios-src:16c8e40746a6 from usr/lib/mios/agent-pipe/mios_pipe/memory/knowledge.py:626-629 -->

### 59 WS-5: the owner to scope knowledge recall to, or None to...

#59 WS-5: the owner to scope knowledge recall to, or None to disable
    filtering. Active ONLY when [pgvector].rls_mode == 'enforce' AND the chat
    surface forwarded a principal (user_name/user_email). Default ('off') -> None
    -> recall SQL is byte-identical to pre-RLS. Legacy/shared rows (owner_user IS
    NULL) stay visible (see build_recall), so flipping to enforce never blanks the
    existing single-user knowledge base. Degrade-open: any error -> None.

<!-- mios-src:a3f0971490c4 from usr/lib/mios/agent-pipe/mios_pipe/memory/knowledge.py:761-766 -->

### task_70

task_70: the owner fed to the DB-side `SET LOCAL mios.owner_user` -- the
    principal the chat surface forwarded for THIS request. This is the V2 owner only
    RECONCILED against the token-bound account when [security].principal_bind_mode=
    enforce; under the default 'off' (or 'verify') it is the raw, SPOOFABLE forwarded
    body/header `user`. Returns None when no principal was forwarded (single-user /
    daemon / seeding).

    UNLIKE _rls_owner (the app-side recall WHERE-filter, gated by [pgvector].rls_mode),
    this is UNGATED here: the DB-side SET LOCAL emission is gated INSIDE mios_pg, which
    emits it ONLY when [pgvector].rls_enable is on AND the principal is enforce-verified
    (mios_pg._owner_scope's P2-1 gate -- so an UNVERIFIED owner can never falsely DB-scope
    rows). Callers pass the best-known owner and mios_pg decides. None -> mios_pg emits
    no SET LOCAL -> the schema policy stays permissive (degrade-open: a system/daemon
    path is NEVER locked out).

<!-- mios-src:900dc25a2291 from usr/lib/mios/agent-pipe/mios_pipe/memory/knowledge.py:779-792 -->

### Semantic recall of the agent's SELF-EDITED durable facts...

Semantic recall of the agent's SELF-EDITED durable facts (agent_memory:
    fact/scope, written by remember/memory_update with embed-on-write). Embed the
    query, HNSW-cosine against the fact embeddings, return top-K above threshold
    as an injectable block (or '' on miss). Default-OFF; degrade-open -> ''.

<!-- mios-src:4ee216285bb7 from usr/lib/mios/agent-pipe/mios_pipe/memory/knowledge.py:803-806 -->

### Look up a phrase in the operator's PKG. Returns the first...

Look up a phrase in the operator's PKG. Returns the first
    matching app_install record as a dict, or None if no match.
    Tries alias first (operator-defined shortcuts), then a fuzzy
    short_name match on app_install.

<!-- mios-src:d57864a9d419 from usr/lib/mios/agent-pipe/mios_pipe/memory/knowledge.py:850-853 -->

### mios_memory -- pluggable agent-memory provider seam...

mios_memory -- pluggable agent-memory provider seam (WS-A15, the AIOS
Memory-Manager abstraction).

Pure stdlib so it unit-tests in isolation (the default provider takes its
backend by INJECTION, so a fake stands in for mios_pg with no DB). server.py
owns the wiring (SSOT [pgvector].memory_provider, the module-global _MEMORY, and
routing the recall call sites through it); this module owns only the interface +
the pgvector-backed default.

Why a seam
==========
Before WS-A15 the recall path called mios_pg.recall(...) directly at each site,
so the storage backend was hard-wired. The MemoryProvider interface (retrieve /
add) lets the backend be swapped -- a different vector DB, a remote memory
service, or a test double -- behind ONE resolution point, without editing the
recall logic. The default (pgvector) is a verbatim pass-through to mios_pg, so
behaviour is byte-identical until a different provider is configured.

<!-- mios-src:4d3f14779fe0 from usr/lib/mios/agent-pipe/mios_pipe/memory/memory.py:4-21 -->

### mios_pg -- PostgreSQL + pgvector client foundation for the...

mios_pg -- PostgreSQL + pgvector client foundation for the agent plane (WS-9).

FOSS-pure PostgreSQL + pgvector client for the agent plane. The PURE, deterministic
parts (DSN building, pgvector literal formatting, parameterized INSERT/recall SQL
builders) are stdlib-only so they unit-test in isolation (sibling-module pattern,
like mios_sched / mios_evict / mios_hitl). The actual connection + execute use
psycopg (v3) imported LAZILY, so importing this module + testing the builders
needs no psycopg and no live database.

Standard pattern (the "native" way): values are NEVER string-interpolated into
SQL -- every builder returns (sql, params) with %(name)s placeholders for psycopg
to bind, which kills SQL-injection and is how OpenAI's / pgvector's own cookbooks
do it. Vector recall uses the pgvector cosine operator `<=>` against an HNSW
index (`ORDER BY emb <=> %(qvec)s::vector LIMIT k`); similarity = 1 - distance.

This is the native agent-plane datastore client (WS-9c cutover: db_backend=postgres).

<!-- mios-src:22543cd68850 from usr/lib/mios/agent-pipe/mios_pipe/memory/pg.py:3-19 -->

### Extract the Postgres bigint id from an agent-plane row id...

Extract the Postgres bigint id from an agent-plane row id that may be a
    legacy 'table:NNN' record-string OR a bare bigint (int or numeric str).

    WS-MEM-TIER: several agent-plane UPDATE sites round-trip a row id from a
    SELECT back into an UPDATE. A legacy record-string id is 'knowledge:abc';
    on pgvector it is a bigint. A caller converting such an
    UPDATE to a parameterized PG statement needs the bigint. Returns None when the
    trailing segment is not an integer (e.g. a legacy alpha id with no pg
    analog) so the caller can SKIP the pg write rather than bind a bad id. Pure +
    deterministic (no DB).

<!-- mios-src:adaca6b76fc4 from usr/lib/mios/agent-pipe/mios_pipe/memory/pg.py:47-56 -->

### pgvector HNSW cosine recall

pgvector HNSW cosine recall: nearest `k` rows to %(qvec)s, returning the
    cosine SIMILARITY (1 - distance). Threshold-filter app-side (matches the
    current recall). Pair with `SET hnsw.ef_search` (see recall_tuning).

    TABLE-AWARE projection (P1/P3): non-knowledge tables don't have q/answer.
    agent_memory has fact/scope/mem_key; mios_rag has source/content. Projecting
    the knowledge columns against them raises UndefinedColumn -> recall()'s
    degrade-open arms the 30s global _pg_mark_down backoff, which would blank the
    LIVE knowledge recall inside every turn. Project the right columns per table.

    owner -- `owner_user = %(owner)s OR owner_user IS NULL` (legacy/shared rows
    with a NULL owner stay visible, so turning enforcement on never blanks
    existing recall). owner=None (the default) leaves the SQL BYTE-IDENTICAL to
    the pre-RLS query -> zero behaviour change when off. The CALLER decides policy
    (read [pgvector].rls_mode) and MUST pass owner only for a table that HAS an
    owner_user column (knowledge AND agent_memory both do; schema-init.sql);
    passing it for an owner_user-less table would raise UndefinedColumn and arm
    the backoff.

    A3 embedding-version hygiene: when an ACTIVE `emb_version` is passed AND the
    table carries the WS-A2 emb_version column (knowledge / agent_memory --
    schema-init.sql; mios_rag does NOT), scope recall to rows of the SAME
    embedding space -- `emb_version = %(emb_version)s OR emb_version IS NULL` --
    so a model/dimension change can't silently mix incompatible vector spaces in
    one cosine query. DEGRADE-OPEN: NULL/un-stamped rows (pre-migration data) stay
    visible, and emb_version=None/'' (active version unknown) adds NO filter, so
    the SQL is byte-identical to the pre-A3 query. Only filter when BOTH the active
    version AND a versioned table are present.

<!-- mios-src:6ff8c56a2993 from usr/lib/mios/agent-pipe/mios_pipe/memory/pg.py:117-144 -->

### Whether THIS deployment VERIFIES the request principal --...

Whether THIS deployment VERIFIES the request principal -- i.e.
    [security].principal_bind_mode == 'enforce', the ONLY mode under which the owner
    fed to the RLS GUC is reconciled against the AUTHENTICATED caller-key's bound
    account (mios_grounding._client_env) instead of the spoofable forwarded body/header
    `user`. Read through that flag's SSOT owner, mios_grounding._principal_bind_mode,
    via a LAZY import so this module keeps its stdlib-only module surface and the pure
    builders still test without grounding/server (the default RLS-off path never reaches
    here, so the import only loads once RLS is enabled). Degrade-CLOSED for RLS: any
    failure -> NOT verified -> no SET LOCAL (honest: never claim isolation we can't
    back, never lock anyone out).

<!-- mios-src:15d0eefec29f from usr/lib/mios/agent-pipe/mios_pipe/memory/pg.py:282-291 -->

### The (sql, params) binding the per-request RLS owner GUC, or...

The (sql, params) binding the per-request RLS owner GUC, or None to emit
    NOTHING. None whenever DB-side RLS is disabled (the default) OR no owner is
    resolvable -- so an owner-less system/daemon/seeding connection leaves the GUC
    UNSET and the schema policy stays permissive (degrade-open: NEVER locked out).

    SECURITY (P2-1): when RLS IS enabled, the owner GUC is emitted ONLY for an
    ENFORCE-VERIFIED principal (_principal_enforced). The owner derives from the
    forwarded body/header `user`, which a direct caller can spoof; it is reconciled to
    the authenticated caller-key ONLY under [security].principal_bind_mode=enforce. With
    RLS on but bind-mode NOT enforce, emitting SET LOCAL would DB-scope rows on an
    attacker-controlled string -- FALSE isolation -- so we emit NOTHING (degrade to the
    permissive policy = HONEST) and warn ONCE. rls_enable=false stays byte-identical.

    The `env` arg drives the rls_enable read (synthetic-env unit-testable); the
    verified-ness read goes through the live SSOT (env MIOS_PRINCIPAL_BIND_MODE ->
    mios.toml [security]), exactly as the request path resolves it.

<!-- mios-src:d84e4ed57bb5 from usr/lib/mios/agent-pipe/mios_pipe/memory/pg.py:315-330 -->

### Minimal bounded reuse pool for psycopg AsyncConnections...

Minimal bounded reuse pool for psycopg AsyncConnections (opt-in).

    `max_size` caps the total live (idle + checked-out) connections; `min_size`
    pre-opens that many on first use (psycopg_pool min_size semantics). Idle
    connections are health-checked on checkout and discarded if dead; on check-in a
    connection is cleaned (any open/aborted transaction rolled back -- which also
    discards a transaction-scoped SET LOCAL owner GUC) so no per-request state
    leaks to the next checkout. When the pool is exhausted, acquire() returns an
    ephemeral (un-pooled) connection so the request path never blocks/fails on the
    pool. Single asyncio event loop (the agent-pipe's), guarded by one Lock.

<!-- mios-src:997db546507a from usr/lib/mios/agent-pipe/mios_pipe/memory/pg.py:368-377 -->

### Lazily pre-open up to `min` idle connections on first use....

Lazily pre-open up to `min` idle connections on first use. Best-effort:
        a connect failure stops warm-up (degrade-open) and the pool grows on
        demand instead.

<!-- mios-src:ee728aad9dc4 from usr/lib/mios/agent-pipe/mios_pipe/memory/pg.py:415-417 -->

### Check out a connection. Returns (conn, pooled): pooled=True...

Check out a connection. Returns (conn, pooled): pooled=True MUST be
        returned via release(); pooled=False is a degrade-open ephemeral connection
        the caller closes. Reuses a live idle connection, else grows up to max, else
        (exhausted) hands back an ephemeral direct connection (never blocks).

<!-- mios-src:f9f2fe18a108 from usr/lib/mios/agent-pipe/mios_pipe/memory/pg.py:437-440 -->

### Return a checked-out connection. A healthy...

Return a checked-out connection. A healthy, cleanly-finished POOLED
        connection is cleaned + put back on the free-list; an ephemeral (degrade)
        connection or a broken/errored one is closed (and a pooled discard frees its
        slot).

<!-- mios-src:09bbb8d7d1b6 from usr/lib/mios/agent-pipe/mios_pipe/memory/pg.py:462-465 -->

### Async-context connection for ONE query bracket, shared by...

Async-context connection for ONE query bracket, shared by execute()/recall().

    DEFAULT (pool disabled): byte-identical to the historic per-call path -- open a
    fresh AsyncConnection, use it, close it (psycopg closes the connection on
    ``async with conn`` exit). Pool ENABLED: check a reused connection out of the
    bounded pool and return it on exit (a body that raises discards it). DEGRADE-
    OPEN: any pool checkout error falls back to a direct ephemeral connect, so a
    query never fails because of the pool.

<!-- mios-src:a106a4568520 from usr/lib/mios/agent-pipe/mios_pipe/memory/pg.py:514-521 -->

### Best-effort async query via psycopg v3. Returns rows...

Best-effort async query via psycopg v3. Returns rows (list[dict]) when
    fetch=True, else None. Degrade-open: any error -> None (mirrors _db_post),
    so a DB hiccup never breaks a turn.

    ``rls_owner`` (task_70): when _owner_scope emits a scope (DB-side RLS enabled AND an
    enforce-verified owner -- see its P2-1 gate), the per-request owner GUC is bound +
    SET LOCAL inside the SAME transaction as the query, so the schema RLS policies scope
    rows to that owner. Default (None / RLS off / unverified) emits NO extra statement
    -> byte-identical to the pre-RLS path.

<!-- mios-src:27713e2677e8 from usr/lib/mios/agent-pipe/mios_pipe/memory/pg.py:553-561 -->

### Cached set of a table's column names (information_schema)....

Cached set of a table's column names (information_schema). Lets insert()
    drop fields the live schema doesn't have, so code<->schema drift degrades to
    a PARTIAL row instead of a silent total failure (+ a 30s backoff that would
    poison every other table's mirror too).

<!-- mios-src:ce08711b6560 from usr/lib/mios/agent-pipe/mios_pipe/memory/pg.py:616-619 -->

### Build + run a parameterized INSERT (WS-9c dual-write...

Build + run a parameterized INSERT (WS-9c dual-write mirror). Filters the
    fields to the live table's columns first (drift-tolerant -- a code field the
    schema lacks is dropped, not fatal). Degrade-open -> None (psycopg/PG absent
    or error never breaks the caller). ``rls_owner`` (task_70) is forwarded to
    execute(): with RLS enabled AND an enforce-verified principal it SET-LOCALs the
    owner GUC in the insert's transaction so FORCE row-level security validates the new
    row (owner_user is written == this owner); default None / RLS off / unverified ->
    byte-identical (see _owner_scope's P2-1 gate).

<!-- mios-src:d7a6b2222530 from usr/lib/mios/agent-pipe/mios_pipe/memory/pg.py:634-641 -->

### Native pgvector HNSW cosine recall on ONE connection (SET...

Native pgvector HNSW cosine recall on ONE connection (SET hnsw.ef_search
    then the SELECT must share a session). Returns rows [{id,q,answer,tier,
    satisfied,access_count,score}] (score = cosine similarity), or [] on any
    error / no psycopg. Caller applies the score threshold (matches the
    prior recall). `owner` (#59 WS-5): when set, scopes recall to that owner
    (+ NULL/shared rows); None = no filter, byte-identical to pre-RLS. Pass only
    for owner_user-bearing tables -- see build_recall. `emb_version` (A3): when
    set, scopes recall to the active embedding space (+ NULL/un-stamped rows) for
    the emb_version-bearing tables; None = no filter, byte-identical. The caller
    passes the SSOT [pgvector].emb_version (degrade-open if unset).

    `rls_owner` (task_70, the DB-side defense-in-depth layer, DISTINCT from the
    app-side `owner` WHERE-filter above): with RLS enabled AND an enforce-verified
    principal it SET-LOCALs the owner GUC in the recall transaction so the schema
    policies enforce owner isolation IN THE DATABASE -- even if the app-side filter is
    bypassed, the caller sees only its own + shared rows. Default None / RLS off /
    unverified emits NOTHING -> byte-identical (see _owner_scope's P2-1 gate).

<!-- mios-src:6e7309d758c6 from usr/lib/mios/agent-pipe/mios_pipe/memory/pg.py:659-675 -->

### Tool-surface reranker

Tool-surface reranker: BM25 lexical arm + RRF fusion + greedy-MMR diversify.

Extracted verbatim from ``server.py`` (refactor R4). Holds the pure, deterministic
ranking core used to choose a sub-agent's intent-relevant tool subset: the
weak-lane tool-priority helpers, the lazy in-process BM25 lexicon over the verb
embed-text corpus, and the stage-2 retrieve->rerank (RRF-fuse cosine with BM25,
then greedy MMR), all degrade-open to plain cosine.

The worker-surface builders/selectors stay in ``server.py`` (their memo caches are
rebound at external invalidation sites). Server-side functions/catalog and the
rerank flags are injected via :func:`configure` -- this module never imports
``server`` (one-way boundary, 98-drift-checks check 6 ``check_module_boundary``). ``server.py`` re-imports
every name under its original alias so the importable surface is byte-identical.

<!-- mios-src:187e7f82a46b from usr/lib/mios/agent-pipe/mios_pipe/memory/worker_tools.py:4-17 -->

### Rank a tool for the CAPPED surface a weak lane...

Rank a tool for the CAPPED surface a weak lane (iGPU/mobile) gets: the
    read/discovery tools a reasoning node actually needs come FIRST, so a small cap
    still yields a USEFUL toolset (every agent MUST get tools -- a weak device gets a
    CAPPED surface, never none). Lower = kept first.

    NO English name substrings: rank is driven by the verb's PERMISSION + the
    reranker's own core-tier signal (the RadixAttention stable-prefix set the module
    already classifies), both read from the verb catalog. rank-0 = the curated
    high-frequency READ verbs (perm=read AND tier=core) -- the SSOT replacement for
    the old keyword set. Degrade-open: when the core-tier signal is off/absent the
    read verbs fall to rank 1 (permission order alone), never a lexical gate.

<!-- mios-src:1479d1f92b97 from usr/lib/mios/agent-pipe/mios_pipe/memory/worker_tools.py:94-104 -->

### STABLE-PREFIX membership

STABLE-PREFIX membership: a tool belongs in the byte-identical core block iff
    its base verb is tier `core`. Intent-FREE + deterministic -> the core block never
    changes turn-to-turn (RadixAttention caches it). The `core` tier is the curated
    high-frequency set (~23: the *_search/web_* tools, system_status, mios_apps,
    launch/open, schedule, discord_send, tool_search). `common`/`rare` verbs, recipes,
    skills and MCP tools are NOT core -- they reach the model via the small per-turn
 cosine TAIL or tool_search (tier core+common == 69 tools drowned
    the 8B + regressed apps/recall selection; core-only == 23 keeps ~33 visible, near the
    working legacy 36). Reuses the existing `tier` field (no new catalog field).

<!-- mios-src:9f6fca09cf47 from usr/lib/mios/agent-pipe/mios_pipe/memory/worker_tools.py:135-143 -->

### P2 stage-2 over the cosine-sorted `scored` [(rel, tool...

P2 stage-2 over the cosine-sorted `scored` [(rel, tool, vec)]: over-fetch a top-K
    window, RRF-fuse the cosine rank with the BM25 lexical rank, then greedy-MMR diversify
    -> the top-n tools. DEGRADE-OPEN: rerank off / window already fits / confident cosine
    cut / any error -> the plain cosine top-n (never fewer than n).

<!-- mios-src:c8c5f24a5e86 from usr/lib/mios/agent-pipe/mios_pipe/memory/worker_tools.py:220-223 -->
