-- AI-hint: Initializes the PostgreSQL schema for the agent-plane, establishing the knowledge table with pgvector support for 768-dim embeddings and GIN indexes for hybrid-search across Q&A pairs.
-- AI-related: mios-remember, mios-skills, mios-daemon, mios-directory-lookup
-- MiOS agent-plane schema for PostgreSQL + pgvector (WS-9, 2026-06-04).
-- FOSS-pure replacement for the legacy (BSL 1.1) agent store. Standard
-- "back to SQL" agent-memory pattern (Letta/MemGPT/Memori/OWUI all converge
-- here): relational columns + JSONB for flexible/document fields + a vector(768)
-- column with an HNSW cosine index wherever semantic recall is needed.
--
-- Embeddings: 768-dim from nomic-embed-text via the OpenAI-compatible
-- /v1/embeddings endpoint (served by mios-llm-light; MiOS Law 5). 768 < pgvector's 2000-dim HNSW
-- limit for the `vector` type, so no halfvec needed. Cosine distance operator is
-- `<=>` with opclass vector_cosine_ops.
--
-- Idempotent: safe to run on every container start (schema-init step).
-- Domain appliances (Forgejo/Ceph/K3s/AdGuard/CrowdSec) keep their own stores.

CREATE EXTENSION IF NOT EXISTS vector;


-- ── knowledge: every finished Q+A (auto-append) + semantic recall ────────────
CREATE TABLE IF NOT EXISTS knowledge (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    q             text NOT NULL,
    answer        text NOT NULL,
    sources       jsonb        DEFAULT '[]'::jsonb,
    emb           vector(768),
    tier          text         DEFAULT 'warm',     -- warm | hot
    access_count  integer      DEFAULT 0,
    recall_hits   integer      DEFAULT 0,
    satisfied     boolean,                          -- outcome signal (P2)
    pinned        boolean      DEFAULT false,       -- never evicted
    session_id    text,
    passport      jsonb,                            -- ed25519 attribution envelope
    last_access   timestamptz,
    ts            timestamptz  DEFAULT now(),
    origin_node   text         NOT NULL DEFAULT 'local',
    logical_ts    bigint       NOT NULL DEFAULT 0,
    logical_clock bigint       NOT NULL DEFAULT 0,
    -- hybrid-search full-text vector (language-neutral 'simple' config; the
    -- topical-anchor guard becomes a real tsvector fusion). NO stemming so it
    -- carries no language assumption (NO-HARDCODED-ENGLISH spirit).
    fts           tsvector GENERATED ALWAYS AS
                  (to_tsvector('simple', coalesce(q,'') || ' ' || coalesce(answer,''))) STORED
);
CREATE INDEX IF NOT EXISTS knowledge_emb_hnsw
    ON knowledge USING hnsw (emb vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS knowledge_fts_gin   ON knowledge USING gin (fts);
CREATE INDEX IF NOT EXISTS knowledge_ts        ON knowledge (ts DESC);
CREATE INDEX IF NOT EXISTS knowledge_tier      ON knowledge (tier);
CREATE INDEX IF NOT EXISTS knowledge_last_acc  ON knowledge (last_access);
-- #59 WS-5: owner_user -- the principal that produced/owns the row, so per-owner
-- RLS can scope recall in a multi-user deployment. Additive + nullable (NULL =
-- unowned/single-user); the drift-tolerant pg insert populates it when the chat
-- surface forwards a principal. Idempotent (matches the established pattern).
ALTER TABLE knowledge    ADD COLUMN IF NOT EXISTS owner_user text;
CREATE INDEX IF NOT EXISTS knowledge_owner ON knowledge (owner_user);
-- WS-A2 embedding-version hygiene: stamp the embedding identity on every vector
-- so a model/dimension change is detectable + the row can be re-embedded off the
-- hot path (mios_embed_backfill). Additive + nullable (NULL = pre-migration /
-- un-stamped -> a backfill treats it as stale). Idempotent.
ALTER TABLE knowledge    ADD COLUMN IF NOT EXISTS emb_model   text;
ALTER TABLE knowledge    ADD COLUMN IF NOT EXISTS emb_version text;

ALTER TABLE knowledge    ADD COLUMN IF NOT EXISTS valid_from timestamptz DEFAULT now();
ALTER TABLE knowledge    ADD COLUMN IF NOT EXISTS valid_to   timestamptz;
CREATE INDEX IF NOT EXISTS knowledge_valid_to ON knowledge (valid_to);

-- WS-BLADE provenance for divergence reconciliation (ADR-0017 D5 / task_2239)
ALTER TABLE knowledge    ADD COLUMN IF NOT EXISTS origin_node   text NOT NULL DEFAULT 'local';
ALTER TABLE knowledge    ADD COLUMN IF NOT EXISTS logical_clock bigint NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS knowledge_origin ON knowledge (origin_node);


-- ── agent_memory: tool-driven self-editing facts (mios-remember) ─────────────
CREATE TABLE IF NOT EXISTS agent_memory (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fact      text NOT NULL,
    scope     text DEFAULT 'global',               -- global | agent:<name> | conversation:<id>
    mem_key   text,                                -- optional update/forget key
    source    text DEFAULT 'agent',                -- agent | operator
    emb       vector(768),
    passport  jsonb,
    ts        timestamptz DEFAULT now(),
    origin_node  text NOT NULL DEFAULT 'local',
    logical_ts   bigint NOT NULL DEFAULT 0,
    logical_clock bigint NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS agent_memory_emb_hnsw
    ON agent_memory USING hnsw (emb vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS agent_memory_scope ON agent_memory (scope);
CREATE UNIQUE INDEX IF NOT EXISTS agent_memory_scope_key
    ON agent_memory (scope, mem_key) WHERE mem_key IS NOT NULL;
-- WS-5: owner_user for per-owner RLS (additive + nullable; NULL = shared/legacy).
ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS owner_user text;
CREATE INDEX IF NOT EXISTS agent_memory_owner ON agent_memory (owner_user);
-- WS-A2 embedding-version hygiene (see knowledge above): stamp the embedding
-- identity so a model/dim change can trigger an off-hot-path re-embed.
ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS emb_model   text;
ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS emb_version text;

ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS valid_from timestamptz DEFAULT now();
ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS valid_to   timestamptz;
CREATE INDEX IF NOT EXISTS agent_memory_valid_to ON agent_memory (valid_to);

-- MEM-05: Importance score for recall ranking
ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS importance numeric DEFAULT 1.0;

-- WS-BLADE provenance for divergence reconciliation (ADR-0017 D5 / task_2239)
ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS origin_node   text NOT NULL DEFAULT 'local';
ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS logical_clock bigint NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS agent_memory_origin ON agent_memory (origin_node);

-- ── event: append-only observability stream ──────────────────────────────────
CREATE TABLE IF NOT EXISTS event (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source      text,
    kind        text,
    severity    text,
    summary     text,
    payload     jsonb,
    session_id  text,
    passport    jsonb,
    -- WS-A8: per-request trace correlation. An event emitted during a traced
    -- chat_completions request carries the request trace_id + the active span_id
    -- so the observability stream stitches to GET /v1/trace. parent_span_id is
    -- reserved for a future per-span mirror -- currently only trace_id/span_id are
    -- written; finished spans live in the agent-pipe in-memory ring, not the DB.
    trace_id        text,
    span_id         text,
    parent_span_id  text,
    ts          timestamptz DEFAULT now(),
    origin_node  text NOT NULL DEFAULT 'local',
    logical_ts   bigint NOT NULL DEFAULT 0,
    logical_clock bigint NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS event_kind    ON event (kind);
CREATE INDEX IF NOT EXISTS event_session ON event (session_id);
CREATE INDEX IF NOT EXISTS event_ts      ON event (ts DESC);
-- WS-A8: forward-compat for an EXISTING event table (the CREATE above is a
-- no-op once the table exists, so add the trace columns idempotently).
ALTER TABLE event ADD COLUMN IF NOT EXISTS trace_id       text;
ALTER TABLE event ADD COLUMN IF NOT EXISTS span_id        text;
ALTER TABLE event ADD COLUMN IF NOT EXISTS parent_span_id text;
CREATE INDEX IF NOT EXISTS event_trace   ON event (trace_id);
-- SEC-03 (task_34): SHA-256 tamper-evident hash chain over the event stream. Each
-- chained row carries its position (chain_seq, monotonic in WRITE order -- the
-- pgvector mirror INSERT is fire-and-forget so the IDENTITY `id` can reorder, hence
-- a write-order seq), the predecessor's chain_hash (prev_hash), and
-- chain_hash = sha256(prev_hash || canonical-JSON of the immutable content fields).
-- A verifier walks chain_seq order, recomputes each link, and flags the first broken
-- one. Additive + idempotent so an existing event table migrates cleanly; columns are
-- NULL for pre-chain rows (the verifier walks only chained rows, WHERE chain_hash IS
-- NOT NULL, starting from genesis).
ALTER TABLE event ADD COLUMN IF NOT EXISTS chain_seq  bigint;
ALTER TABLE event ADD COLUMN IF NOT EXISTS prev_hash  text;
ALTER TABLE event ADD COLUMN IF NOT EXISTS chain_hash text;
CREATE INDEX IF NOT EXISTS event_chain_seq ON event (chain_seq);
-- DCI act analytics (task_28 / task_3219): the deliberation layer (mios_dci) logs each
-- typed epistemic act as an event row -- kind='dci_act' for a persona/critic act,
-- kind='dissent' for an unresolved Challenger objection. The act name lived ONLY in
-- the JSONB payload, so dissent + per-act analytics needed a JSONB extract on every
-- query. Promote it to a top-level column so "every Challenger dissent" / the
-- act-type distribution is a plain indexed scan (WHERE act_type = ...). Additive +
-- nullable (NULL = a non-DCI event); the drift-tolerant pg insert populates it once
-- the column exists, so a pre-migration row degrades to NULL rather than failing.
-- Partial index keeps it to the act-bearing rows, not the whole event stream.
ALTER TABLE event ADD COLUMN IF NOT EXISTS act_type text;
CREATE INDEX IF NOT EXISTS event_act_type ON event (act_type) WHERE act_type IS NOT NULL;

-- WS-BLADE provenance for divergence reconciliation (ADR-0017 D5 / task_2239)
ALTER TABLE event ADD COLUMN IF NOT EXISTS origin_node   text NOT NULL DEFAULT 'local';
ALTER TABLE event ADD COLUMN IF NOT EXISTS logical_clock bigint NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS event_origin ON event (origin_node);

-- ── tool_call: every dispatched verb + result + taint ────────────────────────
CREATE TABLE IF NOT EXISTS tool_call (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id     text,
    tool           text,
    args           jsonb,
    result_preview text,                              -- code field (matches agent-pipe writes)
    success        boolean,                           -- read by the satisfaction check
    output         text,
    stderr         text,
    exit_code      integer,
    latency_ms     integer,
    tainted        boolean DEFAULT false,
    taint_reason   text,
    passport       jsonb,
    ts             timestamptz DEFAULT now()
);
-- Reconcile pre-existing tool_call tables to the code's actual fields (idempotent).
ALTER TABLE tool_call ADD COLUMN IF NOT EXISTS result_preview text;
ALTER TABLE tool_call ADD COLUMN IF NOT EXISTS success        boolean;
CREATE INDEX IF NOT EXISTS tool_call_session ON tool_call (session_id);
CREATE INDEX IF NOT EXISTS tool_call_tool    ON tool_call (tool);
CREATE INDEX IF NOT EXISTS tool_call_taint   ON tool_call (session_id) WHERE tainted;
CREATE INDEX IF NOT EXISTS tool_call_ts      ON tool_call (ts DESC);

-- ── session: agent-side sessions (link to OWUI chat ids) ─────────────────────
CREATE TABLE IF NOT EXISTS session (
    id            text PRIMARY KEY,                 -- caller-provided id
    kind          text,                             -- hermes | cron | cli | delegate | mcp
    owui_chat_id  text,
    meta          jsonb,
    ts            timestamptz DEFAULT now(),
    origin_node   text NOT NULL DEFAULT 'local',
    logical_ts    bigint NOT NULL DEFAULT 0,
    logical_clock bigint NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS session_chat ON session (owui_chat_id);

-- WS-BLADE provenance for divergence reconciliation (ADR-0017 D5 / task_2239)
ALTER TABLE session ADD COLUMN IF NOT EXISTS origin_node   text NOT NULL DEFAULT 'local';
ALTER TABLE session ADD COLUMN IF NOT EXISTS logical_clock bigint NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS session_origin ON session (origin_node);

-- ── skills (mined/promoted) + per-run audit ──────────────────────────────────
CREATE TABLE IF NOT EXISTS skill (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name         text,
    description  text,                              -- code field (mios-skills writes)
    status       text,                              -- candidate | promoted | retired
    body         jsonb,
    confidence   double precision,
    support      integer,                           -- SPM frequency support
    source       text,                              -- mined | operator | import
    version      integer DEFAULT 1,
    created_at   timestamptz DEFAULT now(),
    updated_at   timestamptz,
    last_used_at timestamptz,
    ts           timestamptz DEFAULT now()
);
-- Reconcile pre-existing skill tables to mios-skills' actual fields (idempotent).
ALTER TABLE skill ADD COLUMN IF NOT EXISTS description  text;
ALTER TABLE skill ADD COLUMN IF NOT EXISTS support      integer;
ALTER TABLE skill ADD COLUMN IF NOT EXISTS created_at   timestamptz;
ALTER TABLE skill ADD COLUMN IF NOT EXISTS updated_at   timestamptz;
ALTER TABLE skill ADD COLUMN IF NOT EXISTS last_used_at timestamptz;
CREATE INDEX IF NOT EXISTS skill_name   ON skill (name);
CREATE INDEX IF NOT EXISTS skill_status ON skill (status);

CREATE TABLE IF NOT EXISTS skill_invocation (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    skill       text,
    success     boolean,
    session_id  text,
    ts          timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS skill_inv_skill ON skill_invocation (skill);
-- A3F-01: the open-time half of the invocation. The row used to be created only
-- at CLOSE (a _pg_mirror of {skill, success, session_id}), because the open-time
-- CREATE went through the legacy seam, which discards writes under pg-primary --
-- so started_at and params never landed anywhere.
ALTER TABLE skill_invocation ADD COLUMN IF NOT EXISTS started_at timestamptz;
ALTER TABLE skill_invocation ADD COLUMN IF NOT EXISTS ended_at   timestamptz;
ALTER TABLE skill_invocation ADD COLUMN IF NOT EXISTS params     jsonb;
ALTER TABLE skill_invocation ADD COLUMN IF NOT EXISTS passport   jsonb;

-- A3F-01: the `emitted` edge, as a table. mios_skills._skill_attribute_tool_call
-- issued `RELATE <inv>->emitted-><tool_call>` through the legacy seam, so under
-- pg-primary EVERY skill->tool_call attribution edge was silently dropped and the
-- miner could not subtract skill-emitted runs from its candidate population.
CREATE TABLE IF NOT EXISTS skill_tool_call (
    invocation_id bigint NOT NULL
        REFERENCES skill_invocation (id) ON DELETE CASCADE,
    tool_call_id  text   NOT NULL,
    step_index    integer NOT NULL DEFAULT 0,
    ts            timestamptz DEFAULT now(),
    PRIMARY KEY (invocation_id, tool_call_id)
);
CREATE INDEX IF NOT EXISTS skill_tool_call_tc
    ON skill_tool_call (tool_call_id);

-- ── quota_ledger: per-principal budget window that survives a restart ─────────
-- KACT-03/task_208: the tracker counted per principal but only in memory, so any
-- restart -- including a `bootc` upgrade -- handed an exhausted account a fresh
-- allowance. The RPM deque is deliberately NOT stored: a sliding minute of
-- request timestamps is meaningless after a restart, and replaying it would
-- deny a caller for traffic sent before the process existed.
CREATE TABLE IF NOT EXISTS quota_ledger (
    principal    text PRIMARY KEY,
    window_start double precision NOT NULL,
    spent        double precision NOT NULL DEFAULT 0,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- ── sys_env: singleton live environment cache ────────────────────────────────
CREATE TABLE IF NOT EXISTS sys_env (
    id    text PRIMARY KEY,                         -- 'current'
    data  jsonb,
    ts    timestamptz DEFAULT now()
);

-- ── WS-6 pending_action (HITL gate) ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pending_action (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tool              text,
    args              jsonb,
    action_hash       text,
    status            text DEFAULT 'pending',       -- pending | approved | denied
    session_id        text,
    approver          text,
    decided_at        timestamptz,
    passport          jsonb,
    approval_passport jsonb,
    ts                timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS pending_action_status ON pending_action (status);
CREATE INDEX IF NOT EXISTS pending_action_hash   ON pending_action (action_hash);

-- ── WS-6 run_template (replayable DAG plans) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS run_template (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    class       text,                               -- structural plan-shape hash
    summary     text,
    node_count  integer,
    dag         jsonb,
    session_id  text,
    ts          timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS run_template_class ON run_template (class);
CREATE INDEX IF NOT EXISTS run_template_ts    ON run_template (ts DESC);
-- task_205: `class` is a hash of the PLAN's shape, so it can only be computed
-- AFTER planning -- useless for deciding whether to plan at all. The intent key
-- is derived from the TURN, which is the question asked before the planner runs.
ALTER TABLE run_template ADD COLUMN IF NOT EXISTS intent     text;
ALTER TABLE run_template ADD COLUMN IF NOT EXISTS intent_key text;
CREATE INDEX IF NOT EXISTS run_template_intent_key ON run_template (intent_key);

-- ── scratch: per-chat working memory (folds the in-process _SCRATCHPADS so it
--    survives agent-pipe restarts) ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scratch (
    id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    chat_id  text,
    agent    text,
    lane     text,
    phase    text,
    note     text,
    ts       timestamptz DEFAULT now(),
    origin_node   text NOT NULL DEFAULT 'local',
    logical_ts    bigint NOT NULL DEFAULT 0,
    logical_clock bigint NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS scratch_chat ON scratch (chat_id, ts DESC);
-- Reconcile the legacy-era scratch shape (source/topic/body) the mios-daemon
-- nudger mirrors verbatim into pg (_db_create('scratch', {source,topic,body}) ->
-- INSERT INTO scratch(source,topic,body)). Both writers coexist additively:
-- the pipe scratchpad uses chat_id/agent/lane/phase/note, the daemon uses
-- source/topic/body. Missing columns broke every nudge write (2026-06-18 audit).
ALTER TABLE scratch ADD COLUMN IF NOT EXISTS source text;
ALTER TABLE scratch ADD COLUMN IF NOT EXISTS topic  text;
ALTER TABLE scratch ADD COLUMN IF NOT EXISTS body   text;
-- WS-5: owner_user for per-owner RLS (additive + nullable; NULL = shared/legacy).
ALTER TABLE scratch ADD COLUMN IF NOT EXISTS owner_user text;
CREATE INDEX IF NOT EXISTS scratch_owner ON scratch (owner_user);

-- WS-BLADE provenance for divergence reconciliation (ADR-0017 D5 / task_2239)
ALTER TABLE scratch ADD COLUMN IF NOT EXISTS origin_node   text NOT NULL DEFAULT 'local';
ALTER TABLE scratch ADD COLUMN IF NOT EXISTS logical_clock bigint NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS scratch_origin ON scratch (origin_node);

-- ── kanban: task queue (authoritative here; retires Hermes' kanban.db + the
--    legacy shadow) ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS kanban (
    id       text PRIMARY KEY,
    title    text,
    status   text,                                  -- todo | doing | done | blocked
    detail   jsonb,
    ts       timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS kanban_status ON kanban (status);

-- ── PKG (personal knowledge graph): app-resolution. Flattens the legacy
--    `alias ->resolves_to-> app_install` GRAPH into relational join tables so
--    kg_lookup becomes a JOIN. Populated by the PKG writer (WS-9c follow-up:
--    convert the writer + the agent-pipe kg_lookup reads to these tables). ─────
CREATE TABLE IF NOT EXISTS app_install (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    short_name   text,
    app_id       text,
    source       text,
    label        text,
    launch_hint  text,
    ts           timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS app_install_short ON app_install (short_name);

CREATE TABLE IF NOT EXISTS alias (
    id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    phrase text,
    ts     timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS alias_phrase ON alias (phrase);

-- Natural-key edge (phrase -> app_install.app_id): avoids RETURNING-id
-- round-trips through subprocess-psql; kg_lookup is a JOIN on app_id.
CREATE TABLE IF NOT EXISTS resolves_to (
    phrase  text,
    app_id  text,
    PRIMARY KEY (phrase, app_id)
);
CREATE INDEX IF NOT EXISTS resolves_to_phrase ON resolves_to (phrase);

-- ── directory_entry: mios-daemon's directory-map index (R15: was the legacy DB).
--    Read by mios-directory-lookup (the directory_lookup verb). The indexer
--    DELETE-by-root then batch-UPSERTs on path. Substring search via pg_trgm
--    GIN on lower(basename|path) (the reader uses strpos/ILIKE). mtime is the
--    file's mtime as an ISO string (display only, not queried). ───────────────
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE TABLE IF NOT EXISTS directory_entry (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    path        text UNIQUE NOT NULL,
    parent      text,
    basename    text,
    kind        text,                                 -- file | dir | symlink
    size        bigint,
    mtime       text,
    ext         text,
    summary     text,
    root_label  text,
    updated_at  timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_direntry_root ON directory_entry (root_label);
CREATE INDEX IF NOT EXISTS idx_direntry_ext  ON directory_entry (ext);
CREATE INDEX IF NOT EXISTS idx_direntry_kind ON directory_entry (kind);
CREATE INDEX IF NOT EXISTS idx_direntry_basename_trgm
    ON directory_entry USING gin (lower(basename) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_direntry_path_trgm
    ON directory_entry USING gin (lower(path) gin_trgm_ops);

-- ── log_digest: mios-daemon's consolidated log analysis (R15: was the legacy DB).
--    Append-only summaries from the classify/rollup loops. ─────────────────────
CREATE TABLE IF NOT EXISTS log_digest (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    summary      text,
    tags         jsonb DEFAULT '[]'::jsonb,
    severity     text,
    event_count  integer,
    batch_count  integer,
    ts           timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS log_digest_ts ON log_digest (ts DESC);


-- ===== WS-9: tables ported from the legacy DB -> pgvector (2026-06-13) =====
-- person: one row per operator on this host (PKG; was the legacy `person`).
-- Read by `mios-kg who`, written by `mios-kg bootstrap`. Singleton in the
-- single-operator case; username is the natural upsert key. No embeddings.
CREATE TABLE IF NOT EXISTS person (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username    text NOT NULL,
    fullname    text,
    hostname    text,
    created_at  timestamptz DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS person_username ON person (username);

-- ── PKG: Person Graph Edges (task_59) ──────────────────────────────────────────
-- Graph edges extending the `person` node for semantic context grounding.
CREATE TABLE IF NOT EXISTS person_pref (
    person_id  bigint REFERENCES person(id) ON DELETE CASCADE,
    pref_key   text NOT NULL,
    pref_value jsonb,
    emb        vector(768),
    PRIMARY KEY (person_id, pref_key)
);
CREATE INDEX IF NOT EXISTS person_pref_emb_hnsw_idx ON person_pref USING hnsw (emb vector_cosine_ops);

CREATE TABLE IF NOT EXISTS person_device (
    person_id   bigint REFERENCES person(id) ON DELETE CASCADE,
    device_id   text NOT NULL,
    device_name text,
    PRIMARY KEY (person_id, device_id)
);

CREATE TABLE IF NOT EXISTS person_app_install (
    person_id bigint REFERENCES person(id) ON DELETE CASCADE,
    app_id    text NOT NULL,
    PRIMARY KEY (person_id, app_id)
);

-- ── account: the tenant / owner identity model (multi-tenant FOUNDATION) ──────
--   The agent-plane shipped with NO account/role model: `owner_user` on the
--   RLS-scoped tables (knowledge/agent_memory/scratch) was a free-text label with
--   no backing entity. This table makes the owner a FIRST-CLASS row so accounts
--   (human users + system/service tenants) can be enumerated, displayed, and later
--   FK-referenced. LINKAGE CONVENTION: owner_user == account.name (a soft natural
--   key today). A hard foreign-key rewrite across the owner-scoped tables is a
--   deliberate follow-up -- this migration is ADDITIVE and never rewrites a row.
--   `kind` distinguishes a human user from a system/service tenant. An account IS
--   the owner, so it carries no owner_user of its own and is intentionally OUTSIDE
--   the RLS set below (federation-global, like peer_reputation). Idempotent.
CREATE TABLE IF NOT EXISTS account (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        text UNIQUE NOT NULL,                -- == owner_user (linkage convention)
    kind        text DEFAULT 'user',                 -- user | system | service
    display     text,
    created_at  timestamptz DEFAULT now(),
    meta        jsonb
);
CREATE INDEX IF NOT EXISTS account_kind ON account (kind);
-- WS-ACCT: OS-account control-plane columns (additive + nullable/defaulted, so
-- existing rows and the seed below are untouched). These let the pgvector
-- `account` table drive NATIVE OS accounts on BOTH platforms -- Linux via
-- libnss-pgsql2/pam_pgsql (the DB IS passwd/shadow/group) and Windows via the
-- DB->SAM live-sync service -- so a DB edit reflects to the live OS. Passwords
-- are stored HASHED only (never plaintext); is_admin drives sudo/Administrators;
-- os_targets scopes a row to linux|windows|both; enabled toggles the account
-- without deleting it. See ROADMAP WS-ACCT / TASKS task_151..task_154.
ALTER TABLE account ADD COLUMN IF NOT EXISTS uid           integer;
ALTER TABLE account ADD COLUMN IF NOT EXISTS gid           integer;
ALTER TABLE account ADD COLUMN IF NOT EXISTS groups        text;              -- CSV of supplementary groups
ALTER TABLE account ADD COLUMN IF NOT EXISTS is_admin      boolean DEFAULT false;
ALTER TABLE account ADD COLUMN IF NOT EXISTS os_targets    text    DEFAULT 'both';   -- linux | windows | both
ALTER TABLE account ADD COLUMN IF NOT EXISTS password_hash text;              -- hashed only; NEVER plaintext
ALTER TABLE account ADD COLUMN IF NOT EXISTS enabled       boolean DEFAULT true;
-- Seed accounts WITHOUT a hardcoded name (NO-HARDCODE law forbids restating the
-- SSOT operator identity as a SQL literal). Derive them from the identities the
-- database ALREADY holds, so every existing owner gains a home:
--   (1) the PKG operator singleton (person, populated from the host [identity]); and
--   (2) every distinct owner_user already attached to an owned row.
-- Both are idempotent (ON CONFLICT (name) DO NOTHING) and harmless no-ops on a
-- fresh DB (person empty / no owned rows yet). There is no separate runtime-default
-- account key: the operator [identity] (via `person`) IS the default owner, and the
-- per-request owner is the principal the chat surface forwards.
INSERT INTO account (name, display)
SELECT username, fullname FROM person
WHERE username IS NOT NULL AND btrim(username) <> ''
ON CONFLICT (name) DO NOTHING;
INSERT INTO account (name)
SELECT DISTINCT owner_user FROM (
    SELECT owner_user FROM knowledge    WHERE owner_user IS NOT NULL
    UNION SELECT owner_user FROM agent_memory WHERE owner_user IS NOT NULL
    UNION SELECT owner_user FROM scratch      WHERE owner_user IS NOT NULL
) o
WHERE btrim(owner_user) <> ''
ON CONFLICT (name) DO NOTHING;

-- ── agent_keypair: Ed25519 agent-passport public-key registry (mios-passport;
--    R15: was the legacy DB). provision/rotate INSERTs a row + retires prior live
--    rows; verify reads the live PEM as a filesystem fallback. public_key_pem
--    is the raw multi-line PEM text. ed25519 keys carry no embedding (no emb). ─
CREATE TABLE IF NOT EXISTS agent_keypair (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    agent           text NOT NULL,
    kid             text,
    alg             text DEFAULT 'ed25519',
    public_key_pem  text,
    retired         boolean DEFAULT false,
    provisioned_at  timestamptz DEFAULT now(),
    rotated_at      timestamptz
);
CREATE INDEX IF NOT EXISTS agent_keypair_agent ON agent_keypair (agent);
CREATE INDEX IF NOT EXISTS agent_keypair_live  ON agent_keypair (agent) WHERE NOT retired;

-- ── mios_rag: document-RAG store (chunk text + 768-dim embedding) ─────────────
--    Was created LAZILY only by `mios-rag ingest`, so a fresh/cleared DB had no
--    table and every `mios-rag query` + agent-pipe recall (mios_pg.build_recall,
--    table=='mios_rag') hit `relation "mios_rag" does not exist` (2026-06-18
--    audit). Declared here so it always exists. 768-dim matches [pgvector].
--    embed_model = nomic-embed-text (same vector(768) as knowledge/agent_memory).
CREATE TABLE IF NOT EXISTS mios_rag (
    id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source   text,
    content  text,
    emb      vector(768),
    ts       timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS mios_rag_emb_hnsw
    ON mios_rag USING hnsw (emb vector_cosine_ops) WITH (m = 16, ef_construction = 64);

-- ── peer_reputation: WS-A18 persistent A2A peer reliability (mios_reputation) ──
-- The in-process PeerReputation flushes its raw counters here so reliability
-- SURVIVES an agent-pipe restart (load via restore() on startup, flush on a
-- timer). Federation-global (not owner-scoped) -> intentionally OUTSIDE the WS-5
-- RLS set below. Idempotent.
CREATE TABLE IF NOT EXISTS peer_reputation (
    peer_id    text PRIMARY KEY,
    ok         integer DEFAULT 0,
    bad        integer DEFAULT 0,
    streak_bad integer DEFAULT 0,
    ts         timestamptz DEFAULT now()
);

-- ── gateway_sessions: conversational history persistence for gateway-agent ──
CREATE TABLE IF NOT EXISTS gateway_sessions (
    session_id text PRIMARY KEY,
    messages   jsonb NOT NULL,
    updated_at timestamptz DEFAULT now()
);


-- ── WS-5: native Postgres Row-Level Security (defense-in-depth owner scoping) ──
-- RESEARCHED best practice (AWS / Supabase / Postgres RLS guides): the app sets a
-- PER-REQUEST session var `mios.owner_user` keyed on the VERIFIED principal
-- (WS-A10), and these policies enforce owner isolation IN THE DATABASE -- so even
-- if the app-side owner filter (mios_pg.build_recall) is bypassed or has a bug, a
-- caller sees only its OWN rows + shared (owner_user IS NULL) rows.
--
-- SAFE / behaviour-preserving: current_setting('mios.owner_user', true) returns
-- NULL when UNSET (single-user, or before the app wires the var), and the policy
-- is PERMISSIVE when unset -> ALL rows visible exactly as today. Isolation engages
-- only once the app SETs the var per request (`SET LOCAL mios.owner_user = ...`).
-- FORCE so the table-owner connection is subject to it too. Idempotent.
--
-- task_70 WIRING (who SETs the var): the agent-pipe pg path (mios_pg.recall/insert/
-- execute) and the confined mios-pg-query CLI emit
-- `set_config('mios.owner_user', <verified-owner>, true)` -- SET LOCAL semantics,
-- with the owner BOUND as a parameter (never spliced) -- gated by the SSOT flag
-- [pgvector].rls_enable (default false -> nothing emitted -> the permissive path
-- above). The var name set there MUST stay equal to the one read here. Owner-less
-- internal connections (daemon / seeding / system) emit nothing, so the GUC is
-- unset and these policies stay permissive for them (never locked out).
DO $mios_rls$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['knowledge', 'agent_memory', 'scratch'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I_owner_rls ON %I', t, t);
    -- USING clause: unset session var -> permissive (single-user / pre-wiring);
    -- shared (NULL-owner) rows always visible; else only the caller's own rows.
    EXECUTE format('CREATE POLICY %I_owner_rls ON %I USING (current_setting(''mios.owner_user'', true) IS NULL OR owner_user IS NULL OR owner_user = current_setting(''mios.owner_user'', true))', t, t);
  END LOOP;
END
$mios_rls$;


-- ── task_3221 (task_30): Fact Ledger & Progress Ledger tables ────────────────────
CREATE TABLE IF NOT EXISTS fact_ledger (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id  text NOT NULL,
    claim       text NOT NULL,
    source      text,
    ts          timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fact_ledger_session ON fact_ledger (session_id);

CREATE TABLE IF NOT EXISTS progress_ledger (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id    text NOT NULL,
    agent         text NOT NULL,
    task          text,
    state         text DEFAULT 'assigned', -- assigned | completed | stalled
    assigned_at   timestamptz DEFAULT now(),
    completed_at  timestamptz
);
CREATE INDEX IF NOT EXISTS progress_ledger_session ON progress_ledger (session_id);


-- ── task_40 (OBS-03): Session Hash Chain Migrations ─────────────────────────────
ALTER TABLE session ADD COLUMN IF NOT EXISTS chain_seq bigint;
ALTER TABLE session ADD COLUMN IF NOT EXISTS prev_hash text;
ALTER TABLE session ADD COLUMN IF NOT EXISTS chain_hash text;


-- ── Consolidated Database-Driven Configurations & Verbs (task_127) ──────────────
CREATE TABLE IF NOT EXISTS config_layer (
    rank integer PRIMARY KEY,
    name text UNIQUE NOT NULL
);

INSERT INTO config_layer (rank, name) VALUES
    (0, 'vendor'),
    (1, 'host'),
    (2, 'user'),
    (3, 'machine')
ON CONFLICT (rank) DO UPDATE SET name = EXCLUDED.name;

-- CONS-02 Goodhart alarm. One row per drift sample: `axis` names the
-- distribution ("verdict", "intent", "score"), `dist` is the normalized
-- histogram, and `kind` marks the frozen reference ('baseline') apart from the
-- periodic observations ('sample'). Divergence is not stored -- it is derived
-- from a (baseline, sample) pair, so re-thresholding never needs a backfill.
CREATE TABLE IF NOT EXISTS drift_snapshot (
    id bigserial PRIMARY KEY,
    ts timestamptz DEFAULT now(),
    kind text NOT NULL DEFAULT 'sample',
    axis text NOT NULL,
    dist jsonb NOT NULL,
    samples integer NOT NULL DEFAULT 0,
    source text
);
CREATE INDEX IF NOT EXISTS drift_snapshot_axis_ts_idx
    ON drift_snapshot (axis, kind, ts DESC);

CREATE TABLE IF NOT EXISTS config_event (
    id          bigserial PRIMARY KEY,
    ts          timestamptz DEFAULT now(),
    scope       text NOT NULL,
    key         text NOT NULL,
    old_value   jsonb,
    new_value   jsonb,
    actor       text DEFAULT 'system',
    source      text DEFAULT 'config'
);

CREATE TABLE IF NOT EXISTS config_kv (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scope        text NOT NULL,
    key          text NOT NULL,
    value        jsonb,
    layer        integer REFERENCES config_layer(rank),
    owner_user   text,
    description  text,
    emb          vector(768),
    emb_model    text,
    emb_version  text,
    ts           timestamptz DEFAULT now(),
    origin_node  text NOT NULL DEFAULT 'local',
    logical_ts   bigint NOT NULL DEFAULT 0,
    logical_clock bigint NOT NULL DEFAULT 0,
    UNIQUE (scope, key, layer)
);
CREATE INDEX IF NOT EXISTS config_kv_emb_hnsw
    ON config_kv USING hnsw (emb vector_cosine_ops) WITH (m = 16, ef_construction = 64);

CREATE TABLE IF NOT EXISTS embeddings (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    hash          text UNIQUE,
    model         text,
    vector        vector(768),
    ts            timestamptz DEFAULT now(),
    origin_node   text NOT NULL DEFAULT 'local',
    logical_ts    bigint NOT NULL DEFAULT 0,
    logical_clock bigint NOT NULL DEFAULT 0
);

-- WS-BLADE provenance for divergence reconciliation (ADR-0017 D5 / task_2239)
ALTER TABLE config_kv ADD COLUMN IF NOT EXISTS origin_node   text NOT NULL DEFAULT 'local';
ALTER TABLE config_kv ADD COLUMN IF NOT EXISTS logical_clock bigint NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS config_kv_origin ON config_kv (origin_node);

CREATE TABLE IF NOT EXISTS system_config (
    key           text PRIMARY KEY,
    value         jsonb NOT NULL,
    description   text,
    last_updated  timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS system_config_last_upd ON system_config (last_updated);

CREATE TABLE IF NOT EXISTS verb (
    name             text PRIMARY KEY,
    sig              text NOT NULL,
    desc_default     text NOT NULL,
    tier             text         DEFAULT 'common',
    permission       text         DEFAULT 'read',
    cmd              text,
    params           jsonb        DEFAULT '{}'::jsonb,
    i18n             jsonb        DEFAULT '{}'::jsonb,
    is_active        boolean      DEFAULT true,
    section          text,
    examples         jsonb,
    model_name       text,
    hidden           boolean      DEFAULT false,
    aliases          jsonb,
    conflict_group   text,
    parallel_limit   integer      DEFAULT 0,
    max_result_chars integer      DEFAULT 0,
    last_updated     timestamptz  DEFAULT now()
);
CREATE INDEX IF NOT EXISTS verb_active ON verb (is_active);

ALTER TABLE verb ADD COLUMN IF NOT EXISTS section          text;
ALTER TABLE verb ADD COLUMN IF NOT EXISTS examples         jsonb;
ALTER TABLE verb ADD COLUMN IF NOT EXISTS model_name       text;
ALTER TABLE verb ADD COLUMN IF NOT EXISTS hidden           boolean DEFAULT false;
ALTER TABLE verb ADD COLUMN IF NOT EXISTS aliases          jsonb;
ALTER TABLE verb ADD COLUMN IF NOT EXISTS conflict_group   text;
ALTER TABLE verb ADD COLUMN IF NOT EXISTS parallel_limit   integer DEFAULT 0;
ALTER TABLE verb ADD COLUMN IF NOT EXISTS max_result_chars integer DEFAULT 0;

CREATE TABLE IF NOT EXISTS domain_verb (
    domain       text NOT NULL,
    verb_name    text NOT NULL REFERENCES verb(name) ON DELETE CASCADE,
    PRIMARY KEY (domain, verb_name)
);

ALTER TABLE domain_verb ADD COLUMN IF NOT EXISTS description text;

-- ===== WS-VECTOR V2: AI-plane vectorization gaps (task_1032) =====
ALTER TABLE skill ADD COLUMN IF NOT EXISTS emb vector(768);
ALTER TABLE skill ADD COLUMN IF NOT EXISTS emb_model varchar(128);
ALTER TABLE skill ADD COLUMN IF NOT EXISTS emb_version varchar(64);

ALTER TABLE verb ADD COLUMN IF NOT EXISTS emb vector(768);
ALTER TABLE verb ADD COLUMN IF NOT EXISTS emb_model varchar(128);
ALTER TABLE verb ADD COLUMN IF NOT EXISTS emb_version varchar(64);

ALTER TABLE tool_call ADD COLUMN IF NOT EXISTS emb vector(768);
ALTER TABLE tool_call ADD COLUMN IF NOT EXISTS emb_model varchar(128);
ALTER TABLE tool_call ADD COLUMN IF NOT EXISTS emb_version varchar(64);

ALTER TABLE directory_entry ADD COLUMN IF NOT EXISTS emb vector(768);
ALTER TABLE directory_entry ADD COLUMN IF NOT EXISTS emb_model varchar(128);
ALTER TABLE directory_entry ADD COLUMN IF NOT EXISTS emb_version varchar(64);

ALTER TABLE event ADD COLUMN IF NOT EXISTS emb vector(768);
ALTER TABLE event ADD COLUMN IF NOT EXISTS emb_model varchar(128);
ALTER TABLE event ADD COLUMN IF NOT EXISTS emb_version varchar(64);

ALTER TABLE session ADD COLUMN IF NOT EXISTS emb vector(768);
ALTER TABLE session ADD COLUMN IF NOT EXISTS emb_model varchar(128);
ALTER TABLE session ADD COLUMN IF NOT EXISTS emb_version varchar(64);

-- Create HNSW indexes (idempotently via CREATE INDEX IF NOT EXISTS)
CREATE INDEX IF NOT EXISTS skill_emb_hnsw_idx ON skill USING hnsw (emb vector_cosine_ops);
CREATE INDEX IF NOT EXISTS verb_emb_hnsw_idx ON verb USING hnsw (emb vector_cosine_ops);
CREATE INDEX IF NOT EXISTS tool_call_emb_hnsw_idx ON tool_call USING hnsw (emb vector_cosine_ops);
CREATE INDEX IF NOT EXISTS directory_entry_emb_hnsw_idx ON directory_entry USING hnsw (emb vector_cosine_ops);
CREATE INDEX IF NOT EXISTS event_emb_hnsw_idx ON event USING hnsw (emb vector_cosine_ops);
CREATE INDEX IF NOT EXISTS session_emb_hnsw_idx ON session USING hnsw (emb vector_cosine_ops);

-- ===== WS-VECTOR V3: build-catalog and xbox catalog (task_1036) =====
CREATE TABLE IF NOT EXISTS package_set (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text UNIQUE NOT NULL,
    section text,
    pkgs jsonb,
    enable boolean DEFAULT true,
    layer integer,
    base_image_ref text,
    emb vector(768),
    emb_model varchar(128),
    emb_version varchar(64),
    ts timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS package_set_emb_hnsw_idx ON package_set USING hnsw (emb vector_cosine_ops);

CREATE TABLE IF NOT EXISTS build_recipe (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text UNIQUE NOT NULL,
    description text,
    emb vector(768),
    emb_model varchar(128),
    emb_version varchar(64),
    ts timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS build_recipe_emb_hnsw_idx ON build_recipe USING hnsw (emb vector_cosine_ops);

CREATE TABLE IF NOT EXISTS build_phase (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ordinal integer,
    script text UNIQUE NOT NULL,
    stage text,
    deps jsonb,
    emb vector(768),
    emb_model varchar(128),
    emb_version varchar(64),
    ts timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS build_phase_emb_hnsw_idx ON build_phase USING hnsw (emb vector_cosine_ops);

CREATE TABLE IF NOT EXISTS xbox_feature (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text UNIQUE NOT NULL,
    description text,
    emb vector(768),
    emb_model varchar(128),
    emb_version varchar(64),
    ts timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS xbox_feature_emb_hnsw_idx ON xbox_feature USING hnsw (emb vector_cosine_ops);

CREATE TABLE IF NOT EXISTS debloat_policy (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text UNIQUE NOT NULL,
    policy_type text,
    rules jsonb,
    emb vector(768),
    emb_model varchar(128),
    emb_version varchar(64),
    ts timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS debloat_policy_emb_hnsw_idx ON debloat_policy USING hnsw (emb vector_cosine_ops);

CREATE TABLE IF NOT EXISTS debloat_profile (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text UNIQUE NOT NULL,
    description text,
    emb vector(768),
    emb_model varchar(128),
    emb_version varchar(64),
    ts timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS debloat_profile_emb_hnsw_idx ON debloat_profile USING hnsw (emb vector_cosine_ops);

CREATE TABLE IF NOT EXISTS feature_set (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text UNIQUE NOT NULL,
    description text,
    emb vector(768),
    emb_model varchar(128),
    emb_version varchar(64),
    ts timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS feature_set_emb_hnsw_idx ON feature_set USING hnsw (emb vector_cosine_ops);

CREATE TABLE IF NOT EXISTS appx_removal (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    package_name text UNIQUE NOT NULL,
    emb vector(768),
    emb_model varchar(128),
    emb_version varchar(64),
    ts timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS appx_removal_emb_hnsw_idx ON appx_removal USING hnsw (emb vector_cosine_ops);

CREATE TABLE IF NOT EXISTS feature_removal (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feature_name text UNIQUE NOT NULL,
    emb vector(768),
    emb_model varchar(128),
    emb_version varchar(64),
    ts timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS feature_removal_emb_hnsw_idx ON feature_removal USING hnsw (emb vector_cosine_ops);

CREATE TABLE IF NOT EXISTS capability_removal (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    capability_name text UNIQUE NOT NULL,
    emb vector(768),
    emb_model varchar(128),
    emb_version varchar(64),
    ts timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS capability_removal_emb_hnsw_idx ON capability_removal USING hnsw (emb vector_cosine_ops);

CREATE TABLE IF NOT EXISTS component_removal (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    component_name text UNIQUE NOT NULL,
    emb vector(768),
    emb_model varchar(128),
    emb_version varchar(64),
    ts timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS component_removal_emb_hnsw_idx ON component_removal USING hnsw (emb vector_cosine_ops);

CREATE TABLE IF NOT EXISTS preset (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text UNIQUE NOT NULL,
    description text,
    features jsonb,
    debloat_profile_name text,
    emb vector(768),
    emb_model varchar(128),
    emb_version varchar(64),
    ts timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS preset_emb_hnsw_idx ON preset USING hnsw (emb vector_cosine_ops);


-- ===== WS-VECTOR V2: event and session tables (task_1043) =====
ALTER TABLE event ADD COLUMN IF NOT EXISTS emb vector(768);
ALTER TABLE event ADD COLUMN IF NOT EXISTS emb_model varchar(128);
ALTER TABLE event ADD COLUMN IF NOT EXISTS emb_version varchar(64);
CREATE INDEX IF NOT EXISTS event_emb_hnsw_idx ON event USING hnsw (emb vector_cosine_ops);

ALTER TABLE session ADD COLUMN IF NOT EXISTS emb vector(768);
ALTER TABLE session ADD COLUMN IF NOT EXISTS emb_model varchar(128);
ALTER TABLE session ADD COLUMN IF NOT EXISTS emb_version varchar(64);
CREATE INDEX IF NOT EXISTS session_emb_hnsw_idx ON session USING hnsw (emb vector_cosine_ops);


-- ===== WS-VECTOR V4: accounts, sequence, functions, preferences (task_1047) =====
ALTER TABLE account ADD COLUMN IF NOT EXISTS home_dir text;
ALTER TABLE account ADD COLUMN IF NOT EXISTS shell text;

CREATE SEQUENCE IF NOT EXISTS uid_alloc START WITH 1000;

-- allocate_gid() drew from uid_alloc, the SAME sequence allocate_uid() draws
-- from. mios-account-sync allocates the gid first and the uid second, so every
-- new account got gid=N / uid=N+1 and a user-private group (uid == gid) -- the
-- Fedora default and what `useradd -m` expects -- was unreachable.
CREATE SEQUENCE IF NOT EXISTS gid_alloc START WITH 1000;

-- An EXISTING cluster already handed gids out of uid_alloc, and a fresh
-- gid_alloc starting at 1000 would re-issue them. Advance past the highest gid
-- on record -- and ONLY that. Advancing past uid_alloc as well would push the
-- first gid to 1001 on a brand-new cluster whose first uid is 1000, which is
-- exactly the user-private group (uid == gid) this change exists to restore.
DO $$
DECLARE
    v_floor bigint;
    v_at    bigint;
BEGIN
    SELECT COALESCE(max(gid), 0) + 1 INTO v_floor
      FROM account WHERE gid IS NOT NULL;
    SELECT CASE WHEN is_called THEN last_value + 1 ELSE last_value END
      INTO v_at FROM gid_alloc;
    IF v_floor > v_at THEN
        PERFORM setval('gid_alloc', v_floor, false);
    END IF;
EXCEPTION WHEN undefined_table OR undefined_column THEN
    -- account not created yet on a first-ever init: START WITH 1000 stands.
    NULL;
END
$$;

CREATE OR REPLACE FUNCTION allocate_uid() RETURNS integer AS $$
BEGIN
    RETURN nextval('uid_alloc');
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION allocate_gid() RETURNS integer AS $$
BEGIN
    RETURN nextval('gid_alloc');
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS account_preference (
    account_id bigint REFERENCES account(id) ON DELETE CASCADE,
    layer integer NOT NULL,
    key text NOT NULL,
    value jsonb,
    emb vector(768),
    emb_model varchar(128),
    emb_version varchar(64),
    PRIMARY KEY (account_id, layer, key)
);
CREATE INDEX IF NOT EXISTS account_preference_emb_hnsw_idx ON account_preference USING hnsw (emb vector_cosine_ops);


-- ===== WS-VECTOR V5: config_event and audit triggers (task_1049) =====
CREATE TABLE IF NOT EXISTS config_event (
    id bigserial PRIMARY KEY,
    ts timestamptz DEFAULT now(),
    scope text,
    key text,
    old_value jsonb,
    new_value jsonb,
    actor text,
    source text
);

-- Secret-redaction for the audit trail. seed-db-config.py seeds whole config
-- sections into config_kv INCLUDING identity.default_password, [security].* and
-- [agent_passport].*; the config_kv audit trigger below MUST NOT persist those
-- verbatim into config_event (which config-history.py echoes). Redact by
-- scope/key denylist before writing. Over-redaction in the audit is the safe
-- direction -- the live value still lives in config_kv, only the history is
-- masked. Mirrors the intent of agent-pipe redact.py's MIOS_SECRET_PATTERN but
-- matches config_kv's dotted-key shape (which that env-var regex never would).
CREATE OR REPLACE FUNCTION mios_redact_jsonb_recursive(p_val jsonb)
RETURNS jsonb AS $$
DECLARE
    v_type text;
    v_key text;
    v_item jsonb;
    v_res jsonb;
    v_str text;
    v_idx int;
BEGIN
    IF p_val IS NULL THEN
        RETURN NULL;
    END IF;
    v_type := jsonb_typeof(p_val);
    IF v_type = 'object' THEN
        v_res := '{}'::jsonb;
        FOR v_key, v_item IN SELECT * FROM jsonb_each(p_val) LOOP
            IF lower(v_key) ~ '(password|passwd|secret|token|passphrase|api[_]?key|private_key|credential)' THEN
                v_res := jsonb_set(v_res, ARRAY[v_key], '"[REDACTED_SECRET]"'::jsonb, true);
            ELSE
                v_res := jsonb_set(v_res, ARRAY[v_key], mios_redact_jsonb_recursive(v_item), true);
            END IF;
        END LOOP;
        RETURN v_res;
    ELSIF v_type = 'array' THEN
        v_res := '[]'::jsonb;
        FOR v_item IN SELECT * FROM jsonb_array_elements(p_val) LOOP
            v_res := v_res || jsonb_build_array(mios_redact_jsonb_recursive(v_item));
        END LOOP;
        RETURN v_res;
    ELSIF v_type = 'string' THEN
        v_str := p_val ->> 0;
        IF v_str ~ '^[^=]+=' THEN
            DECLARE
                v_name text := split_part(v_str, '=', 1);
            BEGIN
                IF lower(v_name) ~ '(password|passwd|secret|token|passphrase|api[_]?key|private_key|credential)' THEN
                    RETURN to_jsonb(v_name || '=[REDACTED_SECRET]');
                END IF;
            END;
        END IF;
        RETURN p_val;
    ELSE
        RETURN p_val;
    END IF;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION mios_redact_config_value(p_scope text, p_key text, p_value jsonb)
RETURNS jsonb AS $$
BEGIN
    IF p_value IS NULL THEN
        RETURN NULL;
    END IF;
    IF lower(coalesce(p_scope, '')) IN ('security', 'agent_passport')
       OR lower(coalesce(p_key, '')) ~ '(password|passwd|secret|token|passphrase|api[_]?key|private_key|credential)'
    THEN
        RETURN '"[REDACTED_SECRET]"'::jsonb;
    END IF;
    RETURN mios_redact_jsonb_recursive(p_value);
END;
$$ LANGUAGE plpgsql IMMUTABLE;


CREATE OR REPLACE FUNCTION log_config_kv_change() RETURNS TRIGGER AS $$
BEGIN
    IF (TG_OP = 'INSERT') THEN
        INSERT INTO config_event (scope, key, new_value, actor, source)
        VALUES (TG_TABLE_NAME, NEW.scope || '.' || NEW.key,
                mios_redact_config_value(NEW.scope, NEW.key, NEW.value), NEW.owner_user, 'config_kv_trigger');
        RETURN NEW;
    ELSIF (TG_OP = 'UPDATE') THEN
        INSERT INTO config_event (scope, key, old_value, new_value, actor, source)
        VALUES (TG_TABLE_NAME, NEW.scope || '.' || NEW.key,
                mios_redact_config_value(OLD.scope, OLD.key, OLD.value),
                mios_redact_config_value(NEW.scope, NEW.key, NEW.value), NEW.owner_user, 'config_kv_trigger');
        RETURN NEW;
    ELSIF (TG_OP = 'DELETE') THEN
        INSERT INTO config_event (scope, key, old_value, actor, source)
        VALUES (TG_TABLE_NAME, OLD.scope || '.' || OLD.key,
                mios_redact_config_value(OLD.scope, OLD.key, OLD.value), OLD.owner_user, 'config_kv_trigger');
        RETURN OLD;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION mios_redact_verb_secrets(p_val jsonb)
RETURNS jsonb AS $$
DECLARE
    res jsonb;
BEGIN
    IF p_val IS NULL THEN
        RETURN NULL;
    END IF;
    res := p_val;
    IF res ? 'cmd' AND jsonb_typeof(res -> 'cmd') = 'string' THEN
        IF (res ->> 'cmd') ~* '(password|passwd|secret|token|passphrase|api[_]?key|private_key|credential|bearer\s+[a-zA-Z0-9_\-\.\~]+)' THEN
            res := jsonb_set(res, ARRAY['cmd'], '"[REDACTED_SECRET]"'::jsonb);
        END IF;
    END IF;
    RETURN res;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE TRIGGER config_kv_audit_trigger
    AFTER INSERT OR UPDATE OR DELETE ON config_kv
    FOR EACH ROW EXECUTE FUNCTION log_config_kv_change();

CREATE OR REPLACE FUNCTION log_verb_change() RETURNS TRIGGER AS $$
BEGIN
    IF (TG_OP = 'INSERT') THEN
        INSERT INTO config_event (scope, key, new_value, source)
        VALUES (TG_TABLE_NAME, NEW.name, mios_redact_verb_secrets(to_jsonb(NEW)), 'verb_trigger');
        RETURN NEW;
    ELSIF (TG_OP = 'UPDATE') THEN
        INSERT INTO config_event (scope, key, old_value, new_value, source)
        VALUES (TG_TABLE_NAME, NEW.name, mios_redact_verb_secrets(to_jsonb(OLD)), mios_redact_verb_secrets(to_jsonb(NEW)), 'verb_trigger');
        RETURN NEW;
    ELSIF (TG_OP = 'DELETE') THEN
        INSERT INTO config_event (scope, key, old_value, source)
        VALUES (TG_TABLE_NAME, OLD.name, mios_redact_verb_secrets(to_jsonb(OLD)), 'verb_trigger');
        RETURN OLD;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER verb_audit_trigger
    AFTER INSERT OR UPDATE OR DELETE ON verb
    FOR EACH ROW EXECUTE FUNCTION log_verb_change();

CREATE OR REPLACE FUNCTION log_domain_verb_change() RETURNS TRIGGER AS $$
BEGIN
    IF (TG_OP = 'INSERT') THEN
        INSERT INTO config_event (scope, key, new_value, source)
        VALUES (TG_TABLE_NAME, NEW.domain || '.' || NEW.verb_name, mios_redact_verb_secrets(to_jsonb(NEW)), 'domain_verb_trigger');
        RETURN NEW;
    ELSIF (TG_OP = 'UPDATE') THEN
        INSERT INTO config_event (scope, key, old_value, new_value, source)
        VALUES (TG_TABLE_NAME, NEW.domain || '.' || NEW.verb_name, mios_redact_verb_secrets(to_jsonb(OLD)), mios_redact_verb_secrets(to_jsonb(NEW)), 'domain_verb_trigger');
        RETURN NEW;
    ELSIF (TG_OP = 'DELETE') THEN
        INSERT INTO config_event (scope, key, old_value, source)
        VALUES (TG_TABLE_NAME, OLD.domain || '.' || OLD.verb_name, mios_redact_verb_secrets(to_jsonb(OLD)), 'domain_verb_trigger');
        RETURN OLD;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER domain_verb_audit_trigger
    AFTER INSERT OR UPDATE OR DELETE ON domain_verb
    FOR EACH ROW EXECUTE FUNCTION log_domain_verb_change();


-- ===== WS-ACCT: PostgresOS identity schema (task_151 / ACCT-01) =====
CREATE SCHEMA IF NOT EXISTS mios_identity;

CREATE TABLE IF NOT EXISTS mios_identity.canonical_users (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    display_name     text NOT NULL,
    uid              integer UNIQUE NOT NULL CHECK (uid >= 1000),
    gid              integer NOT NULL DEFAULT 1000,
    homedir          text NOT NULL DEFAULT '/var/home/shared_profile',
    shell            text NOT NULL DEFAULT '/bin/bash',
    sid              text UNIQUE NOT NULL,
    profile_path_win text NOT NULL DEFAULT 'C:\Users\shared_profile',
    email            text DEFAULT NULL,
    pronouns         varchar(64) DEFAULT NULL,
    bio              text DEFAULT NULL,
    likes            text[] DEFAULT '{}'::text[],
    role             text DEFAULT NULL,
    job              text DEFAULT NULL,
    strengths        text[] DEFAULT '{}'::text[],
    meta             jsonb DEFAULT '{}'::jsonb,
    created_at       timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mios_identity.aliases (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_id         UUID REFERENCES mios_identity.canonical_users(id) ON DELETE CASCADE,
    alias_username       varchar(32) UNIQUE NOT NULL,
    password_hash_sha512 text NOT NULL,
    password_hash_ntlm   char(32) NOT NULL,
    is_active            boolean DEFAULT true,
    created_at           timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_alias_lookup ON mios_identity.aliases (alias_username) WHERE is_active = true;

-- ===== WS-SEC: PostgresOS security schema (task_152 / ACCT-02) =====
CREATE SCHEMA IF NOT EXISTS mios_security;

CREATE TABLE IF NOT EXISTS mios_security.fido2_keys (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_id  UUID REFERENCES mios_identity.canonical_users(id) ON DELETE CASCADE,
    key_name      text NOT NULL,
    credential_id text NOT NULL,
    public_key    text NOT NULL,
    is_active     boolean DEFAULT true,
    created_at    timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mios_security.usb_rules (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id    text NOT NULL,
    device_name  text NOT NULL,
    authorized   boolean DEFAULT false,
    allowed_uids integer[] DEFAULT NULL,
    last_seen    timestamptz
);

CREATE TABLE IF NOT EXISTS mios_security.headscale_users (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_id       UUID REFERENCES mios_identity.canonical_users(id) ON DELETE CASCADE,
    headscale_username varchar(64) UNIQUE NOT NULL,
    created_at         timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mios_security.headscale_preauth_keys (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    headscale_user_id UUID REFERENCES mios_security.headscale_users(id) ON DELETE CASCADE,
    key_value         varchar(128) UNIQUE NOT NULL,
    expiration        timestamptz NOT NULL,
    reusable          boolean DEFAULT false,
    used              boolean DEFAULT false
);

CREATE TABLE IF NOT EXISTS mios_security.headscale_acl_rules (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    src        text NOT NULL,
    dst        text NOT NULL,
    action     varchar(16) DEFAULT 'accept',
    created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mios_security.keepass_vaults (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_id   UUID REFERENCES mios_identity.canonical_users(id) ON DELETE CASCADE,
    vault_path     text NOT NULL,
    keyfile_blob   bytea DEFAULT NULL,
    fido2_required boolean DEFAULT true
);


-- ===== WS-ACCT: Trigger Notifications for Account Sync (task_152 / task_153) =====
CREATE OR REPLACE FUNCTION notify_account_changes()
RETURNS TRIGGER AS $$
BEGIN
    PERFORM pg_notify('account_sync', json_build_object(
        'table', TG_TABLE_NAME,
        'action', TG_OP,
        'name', COALESCE(NEW.name, OLD.name),
        'uid', COALESCE(NEW.uid, OLD.uid),
        'gid', COALESCE(NEW.gid, OLD.gid),
        'display', COALESCE(NEW.display, OLD.display),
        'groups', COALESCE(NEW.groups, OLD.groups),
        'is_admin', COALESCE(NEW.is_admin, OLD.is_admin),
        'os_targets', COALESCE(NEW.os_targets, OLD.os_targets),
        'password_hash', COALESCE(NEW.password_hash, OLD.password_hash),
        'enabled', COALESCE(NEW.enabled, OLD.enabled)
    )::text);
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER account_sync_trigger
    AFTER INSERT OR UPDATE OR DELETE ON account
    FOR EACH ROW EXECUTE FUNCTION notify_account_changes();

CREATE OR REPLACE FUNCTION notify_alias_changes()
RETURNS TRIGGER AS $$
BEGIN
    PERFORM pg_notify('account_sync', json_build_object(
        'table', TG_TABLE_NAME,
        'action', TG_OP,
        'alias_username', COALESCE(NEW.alias_username, OLD.alias_username),
        'password_hash_sha512', COALESCE(NEW.password_hash_sha512, OLD.password_hash_sha512),
        'password_hash_ntlm', COALESCE(NEW.password_hash_ntlm, OLD.password_hash_ntlm),
        'is_active', COALESCE(NEW.is_active, OLD.is_active),
        'canonical_id', COALESCE(NEW.canonical_id, OLD.canonical_id)
    )::text);
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER alias_sync_trigger
    AFTER INSERT OR UPDATE OR DELETE ON mios_identity.aliases
    FOR EACH ROW EXECUTE FUNCTION notify_alias_changes();


-- ===== WS-ACCT: Database-owned Preferences (task_224) =====
CREATE TABLE IF NOT EXISTS mios_identity.account_preferences (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_id UUID REFERENCES mios_identity.canonical_users(id) ON DELETE CASCADE,
    pref_key     varchar(128) NOT NULL,
    pref_value   text NOT NULL,
    emb          vector(768),
    emb_version  varchar(32),
    UNIQUE(canonical_id, pref_key)
);

-- ===== Hindsight Multi-Strategy Retrieval Engine (task_57 / MEM-04) =====
CREATE OR REPLACE FUNCTION hindsight_search(
    p_query text,
    p_emb vector(768),
    p_limit integer DEFAULT 5
) RETURNS TABLE (
    id bigint,
    q text,
    answer text,
    score numeric,
    strategy text
) AS $$
BEGIN
    RETURN QUERY
    WITH semantic AS (
        SELECT k.id, 
               1.0 - (k.emb <=> p_emb) AS s_score,
               k.session_id,
               k.owner_user
        FROM knowledge k
        WHERE k.emb IS NOT NULL
        ORDER BY k.emb <=> p_emb LIMIT p_limit * 2
    ),
    keyword AS (
        SELECT k.id,
               ts_rank_cd(k.fts, plainto_tsquery('simple', p_query)) AS k_score
        FROM knowledge k
        WHERE k.fts @@ plainto_tsquery('simple', p_query)
        ORDER BY k_score DESC LIMIT p_limit * 2
    ),
    graph AS (
        -- graph relational: expand context by fetching adjacent chunks sharing session_id or owner_user with top semantic hits
        SELECT k.id, 1.0 AS g_score
        FROM knowledge k
        WHERE k.session_id IN (SELECT s.session_id FROM semantic s WHERE s.session_id IS NOT NULL)
           OR k.owner_user IN (SELECT s.owner_user FROM semantic s WHERE s.owner_user IS NOT NULL)
    ),
    temporal AS (
        -- recent chunks get a decay score boost
        SELECT k.id,
               exp(-extract(epoch from now() - k.ts) / (86400.0 * 30.0)) AS t_score
        FROM knowledge k
        ORDER BY k.ts DESC LIMIT p_limit * 2
    ),
    combined AS (
        SELECT k.id, k.q, k.answer,
               (COALESCE(s.s_score, 0) * 0.4 + 
                COALESCE(kwd.k_score, 0) * 0.3 + 
                COALESCE(g.g_score, 0) * 0.1 + 
                COALESCE(t.t_score, 0) * 0.2)::numeric AS final_score
        FROM knowledge k
        LEFT JOIN semantic s ON s.id = k.id
        LEFT JOIN keyword kwd ON kwd.id = k.id
        LEFT JOIN graph g ON g.id = k.id
        LEFT JOIN temporal t ON t.id = k.id
        WHERE s.id IS NOT NULL OR kwd.id IS NOT NULL OR g.id IS NOT NULL
    )
    SELECT c.id, c.q, c.answer, round(c.final_score, 4), 'hindsight'::text
    FROM combined c
    ORDER BY c.final_score DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql STABLE;

-- MEM-05: Sleep-Time Consolidation - recall ranking
CREATE OR REPLACE FUNCTION agent_memory_search(
    p_query text,
    p_emb vector(768),
    p_limit integer DEFAULT 5
) RETURNS TABLE (
    id bigint,
    fact text,
    score numeric
) AS $$
BEGIN
    RETURN QUERY
    SELECT am.id, am.fact,
           -- recency x importance x relevance
           (exp(-extract(epoch from now() - am.ts) / (86400.0 * 30.0)) * 
            COALESCE(am.importance, 1.0) * 
            (1.0 - (am.emb <=> p_emb)))::numeric AS final_score
    FROM agent_memory am
    WHERE am.emb IS NOT NULL
    ORDER BY (exp(-extract(epoch from now() - am.ts) / (86400.0 * 30.0)) * 
              COALESCE(am.importance, 1.0) * 
              (1.0 - (am.emb <=> p_emb))) DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql STABLE;

-- LSFS-01: Agent tasks protocol for durable cross-turn execution state
CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('backlog', 'in-progress', 'done')),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ── system_logs: unified journald stream with pgvector semantic indexing (task_355) ───
CREATE TABLE IF NOT EXISTS system_logs (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    unit        text NOT NULL,
    priority    integer NOT NULL,
    message     text NOT NULL,
    metadata    jsonb DEFAULT '{}'::jsonb,
    emb         vector(768),
    ts          timestamptz DEFAULT now(),
    origin_node text NOT NULL DEFAULT 'local'
);
-- task_979: every table the embed backfiller covers carries emb_version; without
-- it `WHERE emb IS NULL OR emb_version IS DISTINCT FROM %(ver)s` cannot run, and
-- system_logs could not be added to PK_MAP at all.
ALTER TABLE system_logs ADD COLUMN IF NOT EXISTS emb_version varchar(64);
CREATE INDEX IF NOT EXISTS system_logs_emb_hnsw
    ON system_logs USING hnsw (emb vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS system_logs_unit_ts ON system_logs (unit, ts DESC);
CREATE INDEX IF NOT EXISTS system_logs_priority_ts ON system_logs (priority, ts DESC);
CREATE INDEX IF NOT EXISTS system_logs_ts ON system_logs (ts DESC);
CREATE INDEX IF NOT EXISTS system_logs_origin ON system_logs (origin_node);

-- ── threat_events: security anomaly vector sink (task_456) ────────────────────────
CREATE TABLE IF NOT EXISTS threat_events (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type    text NOT NULL,                      -- e.g. 'network_anomaly', 'lateral_movement'
    divergence    double precision NOT NULL,          -- Jensen-Shannon divergence score
    description   text NOT NULL,
    flow_summary  jsonb DEFAULT '{}'::jsonb,
    emb           vector(768),
    emb_version   varchar(64) DEFAULT 'nomic-embed-text-v1.5',
    severity      text DEFAULT 'medium',              -- low | medium | high | critical
    resolved      boolean DEFAULT false,
    ts            timestamptz DEFAULT now(),
    origin_node   text NOT NULL DEFAULT 'local'
);
ALTER TABLE threat_events ADD COLUMN IF NOT EXISTS emb_model varchar(128);
CREATE INDEX IF NOT EXISTS threat_events_emb_hnsw
    ON threat_events USING hnsw (emb vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS threat_events_divergence ON threat_events (divergence DESC);
CREATE INDEX IF NOT EXISTS threat_events_severity ON threat_events (severity);
CREATE INDEX IF NOT EXISTS threat_events_ts ON threat_events (ts DESC);

-- ── agent_inbox: HMAC-SHA256 authenticated webhook queue (task_461) ──────────────
CREATE TABLE IF NOT EXISTS agent_inbox (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    idempotency_key text UNIQUE NOT NULL,             -- SHA256 payload hash to prevent duplicates
    source          text NOT NULL,                    -- e.g. 'github', 'forgejo', 'alertmanager'
    event_type      text NOT NULL,                    -- e.g. 'push', 'issue', 'alert'
    payload         jsonb NOT NULL,
    status          text DEFAULT 'pending',           -- pending | processing | dispatched | duplicate | failed
    retry_count     integer DEFAULT 0,
    created_at      timestamptz DEFAULT now(),
    processed_at    timestamptz,
    origin_node     text NOT NULL DEFAULT 'local'
);
CREATE INDEX IF NOT EXISTS agent_inbox_idempotency ON agent_inbox (idempotency_key);
CREATE INDEX IF NOT EXISTS agent_inbox_status_created ON agent_inbox (status, created_at);
CREATE INDEX IF NOT EXISTS agent_inbox_source_event ON agent_inbox (source, event_type);

