<!-- AI-hint: Concept brief on why and how MiOS unified its agent-plane datastore onto PostgreSQL + pgvector (the FOSS "back to SQL" agent-memory stack), defining the standard schema, the shared mios-pg-query client seam, and the now-completed cutover off legacy datastore.
     AI-related: mios-remember, mios-db, mios-pg-query, mios-daemon, mios-skills, mios-pgvector, mios-services, mios-ai, mios-sync-env, mios-pgvector.container, mios-pgvector.service, mios-llm-light -->
# PostgreSQL + pgvector Unification (WS-9) — the agent-plane datastore

> Status: DONE / standing architecture (drafted 2026-06-04; cutover completed
> 2026-06-05). The agent-plane datastore was migrated off **the legacy datastore (BSL 1.1,
> source-available — not OSI-FOSS)** onto **PostgreSQL + pgvector (OSI-FOSS)**,
> done the standard ("native") way. The legacy datastore is now fully **removed**; `mios.toml`
> `[pgvector].db_backend = "postgres"`. Schema artifact:
> `usr/share/mios/postgres/schema-init.sql`. Section 8 keeps the staged-migration
> history as record.

## 0. Where this fits in MiOS as a whole

MiOS is one thing built two ways at once: an **immutable, bootc/OCI-shaped Fedora
workstation** (the whole OS is a single container image — boot it, `bootc upgrade`
it like a `git pull`, `bootc rollback` it like a Ctrl-Z) that is *also* a **local,
self-replicating, agentic AI operating system**. The same image that ships
GNOME/Wayland and GPU virtualization also ships a full local agent stack behind one
OpenAI-compatible endpoint (`MIOS_AI_ENDPOINT`, Architectural Law 5).

In that agent stack the flow is: a front-end (OWUI, the Discord gateway, the `mios`
CLI) hands a request to the **agent-pipe** orchestrator (port key `agent_pipe`), which refines it,
fans it out across a council/swarm, and dispatches tool/verb calls; **MiOS-Hermes**
(port key `hermes`) is the OpenAI-compatible gateway and tool-loop agent; the **inference
lanes** — primary **mios-llm-light** (port key `llm_light`, llama.cpp behind the
upstream llama-swap proxy image), with gated heavy lanes **mios-llm-heavy** (vLLM,
port key `vllm`) and **mios-llm-heavy-alt** (SGLang, port key `sglang`) — do
generation **and** embeddings; MCP
exposes the tool surface and A2A federates peer agents.

This document is about the **memory and state** of that stack. Every durable thing
the agents learn or do — tiered memory, stored knowledge, sessions, skills, tool
calls, the kanban board, scratchpads, RAG embeddings — lives in **one** datastore.
This brief explains why that datastore is PostgreSQL + pgvector, how it is shaped,
and how the migration onto it was done. Because the repo root *is* the deployed
system root, the quadlet, schema, SSOT, and client described here ship inside the
image and come up under the bootc lifecycle like every other part of MiOS.

## 1. Why this is the standard ("native") pattern, not a bespoke port

The research is unambiguous — the industry has converged on "just use Postgres +
pgvector" for agent state + memory + retrieval:

- **Embeddings live in the same table/transaction as the rows** — no separate
  vector DB. A `vector(768)` column + an HNSW index *is* the vector store.
- **HNSW is the 2026 default index** (build-before-data, <20 ms at 1M vectors,
  >95% recall). Canonical DDL: `CREATE INDEX … USING hnsw (emb vector_cosine_ops)
  WITH (m=16, ef_construction=64)`; query `ORDER BY emb <=> $1 LIMIT k` with
  `SET hnsw.ef_search`. nomic-embed's **768 dims < pgvector's 2000-dim HNSW
  limit**, so plain `vector` (no halfvec).
- **Hybrid search** (vector `<=>` + Postgres `tsvector` full-text) is the
  standard quality lever — MiOS's Python topical-anchor guard becomes a real
  tsvector fusion (a generated `fts` column + GIN index, language-neutral
  `simple` config).
- **Letta/MemGPT, Memori, and OWUI all use exactly this stack.** OWUI is native:
  `DATABASE_URL=postgresql://…` + `VECTOR_DB=pgvector` (pgvector + Chroma are the
  only two OWUI-maintained vector stores) — so OWUI can fold onto the **same**
  container.
- **The embeddings flow is unchanged and unified (Law 5)**: nomic-embed-text via
  the OpenAI-compatible `/v1/embeddings` endpoint served by **mios-llm-light**
  (port key `llm_light`), 768-dim, stored in `vector(768)`. The same engine that does chat does
  embeddings — there is no separate embedding backend to keep alive.

This is also why pgvector replaced **two** legacy components at once: the legacy datastore (the
relational/JSONB agent store) **and** Qdrant (a vestigial standalone vector store).
One engine now does relational + JSONB (document) + vector — the standard "back to
SQL" agent-memory stack — so there is one failure domain, one backup, one set of
credentials, and one license class to reason about.

## 2. What consolidates here vs what stays separate

**Onto the one Postgres+pgvector container (the agent plane):** `knowledge`,
`agent_memory` (mios-remember), `event`, `tool_call`, `session`, `skill` /
`skill_invocation`, `sys_env`, `pending_action` (WS-6 HITL), `run_template` (WS-6),
`scratch` (folds the in-process `_SCRATCHPADS` → restart-survivable), `kanban`
(authoritative; retired Hermes' SQLite + the former legacy datastore shadow), plus the
identity/graph tables `directory_entry`, `person`, `agent_keypair`, `alias`,
`resolves_to`, `app_install`, and `log_digest`. **OWUI**
(`DATABASE_URL`+`VECTOR_DB=pgvector`) and **Guacamole** (already Postgres) can point
at the same instance (separate database) to collapse onto one engine.

**Stay separate (domain appliances — own schema + upgrade path):** Forgejo, Ceph,
K3s/etcd, AdGuard, CrowdSec. Force-merging would couple unrelated failure domains
for ~no gain.

## 3. Schema

Full DDL in `usr/share/mios/postgres/schema-init.sql` — idempotent, the standard
relational + JSONB + `vector(768)` pattern. Highlights: `CREATE EXTENSION vector`;
HNSW cosine indexes on `knowledge.emb` + `agent_memory.emb`; a generated `fts`
tsvector + GIN on `knowledge` for hybrid search; btree indexes on the hot filters
(ts, session_id, tier, status, class); `passport` ed25519 envelopes preserved as
JSONB. The DDL runs once, on first init, via the container's
`/docker-entrypoint-initdb.d` mount.

## 4. The shared `mios-pg-query` client (the migration seam)

A single psycopg-based client replaced the **~9 callers that each reimplemented the
the legacy datastore `/sql` POST** (one of which hardcoded `root:root`). Every query goes
through it → it centralizes credentials (from `mios.toml`/secret, no literals),
gives one place to swap engines, and was the seam the cutover happened behind.
Agent-pipe's `_db_post`/`_db_create`/`_recall_knowledge`, `mios-remember`,
`mios-daemon`, and `mios-skills` all moved to it. This was dedup-audit Cluster 2.

Concrete artifacts: `usr/lib/mios/agent-pipe/mios_pg.py` (the pure-python psycopg
client — DSN / vector-literal / parameterized INSERT + native cosine-`<=>` recall
builders, lazy psycopg, degrade-open), the `mios-pg-query` libexec wrapper, and
`usr/libexec/mios/mios-db --pg '<sql>'` (local `psql` → container `psql` fallback;
SSOT creds, no hardcodes).

## 5. The standing deployment (what ships in the image)

- `usr/share/containers/systemd/mios-pgvector.container` — the quadlet
  (`docker.io/pgvector/pgvector:pg17`, host-net loopback `:5432`, schema-init mount,
  PGDATA volume under `/var/lib/mios/pgvector`, runs as **uid 826** `mios-pgvector`
  in the AI tier so agents can read it). Unit: `mios-pgvector.service`.
- `usr/share/mios/postgres/schema-init.sql` — the canonical pgvector DDL (idempotent;
  first-init only).
- SSOT in `mios.toml`: `[ports].pgvector = 5432`, `[image.sidecars].pgvector` (pg17,
  bound-image per Law 3), `[services.pgvector]` (uid/gid 826), and the `[pgvector]`
  config section (`host`/`user`/`pass`/`db`/`data_dir`/`schema_init`/`embed_model`/
  `db_backend`). Identity/wiring: `sysusers.d/50-mios-services.conf` (mios-pgvector
  826 + mios-ai), `tmpfiles.d/mios-pgvector.conf` (PGDATA dir per Law 2),
  `tools/lib/userenv.sh` (the `MIOS_PG_*`/`MIOS_PGVECTOR_*` maps), and
  `automation/34-render-quadlets.sh` (placeholder render per the Quadlet contract).
- Engine selector: `[pgvector].db_backend` (env `MIOS_PGVECTOR_DB_BACKEND`). Now set to
  **`postgres`** — Postgres is primary and native `<=>` HNSW recall is live. The
  `surreal` and `dual` values remain only as historical/rollback documentation; the
  the legacy datastore path itself is gone.

Build (operator): `just build` bakes the quadlet + sysusers and pulls the pgvector
bound-image. Verify: `mios-db --pg "SELECT extversion FROM pg_extension WHERE
extname='vector';"` and that the agent-plane tables exist (`mios-db --pg '\dt'`).

## 6. License resolution

This closed a consistency gap: MiOS is FOSS, and its unified datastore is now
**OSI-FOSS** (PostgreSQL License + pgvector's PostgreSQL License) instead of
the legacy datastore's BSL 1.1 — the same source-available license class flagged for the
Wide-Moat OCU and declined for the same reason.

## 7. Sources

pgvector (github.com/pgvector/pgvector); OpenAI Cookbook vector-DB / RAG examples;
OWUI docs (`DATABASE_URL` + `VECTOR_DB=pgvector`); Letta/Memori agent-memory-on-
Postgres. Verify against current code (`mios_pg.py`, `schema-init.sql`,
`mios.toml [pgvector]`) before relying on any specific value.

---

## 8. Migration history (record — kept for rationale, not current state)

The cutover was staged, reversible, and default-on per directive. It is complete;
this section documents how it was done.

**Plan (2026-06-04).** 1) Stand up the Postgres+pgvector quadlet
(`docker.io/pgvector/pgvector:pg17`) alongside the legacy datastore — additive, breaks nothing;
run `schema-init.sql`. 2) Build the psycopg client + port the read/write helpers
behind it (the legacy query dialect → SQL), keeping the legacy datastore reads as a fallback during cutover.
3) Replace `_recall_knowledge`'s SELECT-60-then-Python-cosine with native pgvector
`<=>` HNSW (+ optional tsvector hybrid). 4) Move scratchpad + kanban to Postgres
tables (persistence + authoritative). 5) Point OWUI/Guacamole at Postgres. 6)
Backfill existing legacy datastore rows (export → INSERT) and verify counts + a recall
smoke test. 7) Retire the legacy datastore once parity was confirmed.

**Additive step (2026-06-04): DONE.** Stood up the container + client foundation
without touching the then-live legacy datastore path. Added `mios_pg.py` (client
foundation; `test_mios_pg.py` 18/18), the `mios-db --pg` mode, the SSOT entries,
and the identity/tmpfiles/userenv/render wiring. Nothing live moved yet.

**Cutover code (WS-9c, 2026-06-04): DONE, default `dual`.** Wired a backend
selector (`[pgvector].db_backend`, env `MIOS_PGVECTOR_DB_BACKEND`): `surreal` | `dual` |
`postgres`. `dual` (the safe live-migration default) mirrored writes to both stores
while reads stayed on the legacy datastore, so Postgres was exercised and verifiable live
without risking the read path. `mios_pg.py` gained `insert()` + native `recall()`
(one-connection `SET hnsw.ef_search` + `<=>` SELECT), jsonb handling, and a 30 s
connect-failure backoff (`test_mios_pg.py` 23/23). `server.py` added
`import mios_pg`, the `DB_BACKEND`/`_PG_ENABLED`/`_PG_PRIMARY` flags, fire-and-forget
`_pg_mirror()` after the hot writes (`_store_knowledge_task`, `_emit_session_event`,
`_hitl_record_pending`, `_capture_run_template`), and `_recall_knowledge_pg()` for
native recall when Postgres is primary (degrade-open to the fallback otherwise).

**Flip to `postgres` (2026-06-05): DONE.** `[pgvector].db_backend = "postgres"` —
agent-pipe + mios-remember/skills/daemon/kg now read **and** write pgvector and
native `<=>` recall is live (verified live; a chat ran pg-primary). A few minor
deferred read paths (eviction / HITL-edge / miner-SPM / daemon batch+async /
person-owns) degrade gracefully. After the soak, **the legacy datastore was retired** — its
quadlet and BSL image were dropped, leaving pgvector as the single agent-plane
datastore described above. (Setting `db_backend` back to `dual`/`surreal` is now
documentation only; there is no legacy datastore process to fall back to.)
