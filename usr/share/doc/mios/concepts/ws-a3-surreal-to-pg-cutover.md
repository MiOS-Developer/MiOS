<!-- AI-hint: WS-A3 completion record -- the legacy datastore->Postgres+pgvector cutover for the agent CLIs: the mios-pg-query extended-protocol (--exec-json) parameter-binding keystone, parameterized knowledge eviction, the canonical pg kanban queue, the per-tool CLI SQL-injection fixes + dead legacy query dialect deletions, the drift-check 10 regression lock, and the explicitly-named deferred residuals (mios-daemon dead legacy query dialect + read-gating, mios-viking migration).
     AI-related: ../../../../../usr/libexec/mios/mios-pg-query, ../../../../../usr/libexec/mios/mios-db, ../../../../../usr/lib/mios/agent-pipe/mios_evict.py, ../../../../../automation/98-drift-checks.sh -->
# WS-A3 — the legacy datastore → Postgres cutover: parameterized eviction, canonical kanban, CLI SQL-safety

Status: **offline-complete + gated** (operator live-tests in the VM). 2026-06-20.

the legacy datastore (`:8000`, BSL 1.1) is retired; the agent datastore is Postgres+pgvector.
Any code still emitting the legacy query dialect **silently no-ops** on pg (the connection fails,
the error is swallowed, a 30s backoff arms) — so dead legacy query dialect is not just cruft,
it is a *silently broken feature*. And every value that was f-string-spliced into
SQL (mitigated only by hand-rolled `_pgesc`/`_pgq` single-quote doubling) was a
SQL-injection surface. WS-A3 closes both.

## The keystone: parameter binding for the pure-stdlib pg client

`mios-pg-query` only spoke the **simple** query protocol (raw SQL in a `Q`
message) — it *could not bind parameters at all*, which is *why* every CLI
f-string-spliced values. It now also speaks the **extended** protocol
(Parse/Bind/Execute/Sync, text format, backend-inferred types) behind a
backward-compatible `--exec-json` mode reading a stdin envelope:

```
{"sql": "... $1 ... $2 ...", "params": [v1, v2]}            # single statement
{"statements": [{"sql": "...", "params": [...]}, ...]}      # atomic BEGIN/COMMIT
```

No flag → the legacy simple-`Q` path is byte-for-byte unchanged (zero risk to
existing callers). `mios-db --pg-json` forwards the same envelope with the
SSOT-mapped `MIOS_PG_*` connection env, so it stays the single DB entry point.
Integers (LIMIT, interval counts) stay inline via `int()` coercion; strings /
vectors / text bind out-of-band. 36 byte-level wire asserts in
`test_mios_pgwire.py`.

## Done

- **Parameterized eviction** (`mios_evict.py` + server.py `_db_count`/
  `_evict_select_ids`/`_evict_delete_ids`/`_evict_knowledge`): was the legacy query dialect
  (`??`, `time::now() - Nd`, record-id `DELETE a,b;`) → never ran on pg → the
  knowledge table never evicted. Now parameterized pg (`COALESCE`, `now() -
  make_interval`, `id = ANY(%(ids)s)`). 25 asserts.
- **Canonical kanban** (server.py `_shadow_queue_tasks`): wrote to the legacy datastore
  `kanban_shadow` (dead) whose pg mirror targeted a non-existent table → the
  refined multi-task queue was invisible. Now a parameterized upsert into the
  canonical pg `kanban` (`INSERT … ON CONFLICT (id) DO UPDATE`).
- **CLI SQL-safety** — parameterized via the envelope + dead legacy query dialect deleted:
  `mios-remember`, `mios-kg`, `mios-rag`, `mios-ingest`, `mios-directory-lookup`,
  `mios-skills` (this also un-breaks the SPM miner, which in the default `dual`
  mode read the retired `:8000` and *never mined a skill*), `mios-day0-reset`
  (bash), and `mios-db` (the `--embed` python-source-injection nit). The
  `mios-knowledge-add`/`-search` tools were already parameterized (sqlite `?`).
  Adversarial `test_mios_cli_sqlsafety.py` fires a `drop table` / `delete from`
  payload and asserts it lands **only** in `params`, never in any SQL statement.
- **mios-daemon** live pg writes parameterized (`_pg_insert`,
  `_pg_replace_directory_entries` — the latter splices filesystem paths + file
  content) via a signature-preserving internal rewrite (zero call-site churn for
  the runtime-critical daemon).
- **Regression lock**: `98-drift-checks.sh` check (10) fails the gate if any
  libexec tool reintroduces `post_sql`/`_sql`/`:8000/sql`/`_pgesc`/`_pgq`.
- **Build gate**: all `usr/libexec/mios/test_mios_*.py` now run in `build.sh`.

## Residuals — built-but-gated / partial / introspection-only (see aios-engineering-blueprint.md)

Every libexec tool is cut over; the gate enforces it everywhere with no
exceptions. The former residuals, all now done:

1. ~~**mios-daemon dead legacy query dialect**~~ — DONE: both surreal transports (`_db_post`,
   `_db_post_sync`) are behavior-preserving no-op stubs (the backend was already
   dead → `[]`/`None`, now without the per-call 3s timeout + 30s backoff);
   `_db_create` is mirror-only (`_pg_insert` + `return ""`, so the dead
   CREATE/`time::now()` string-build vanishes with zero call-site change); unused
   `_pgesc` + the dead `DB_URL/USER/PASS/NS/DB/_DB_AUTH` constants removed. (A few
   `time::now()` strings remain ONLY as the unused `surreal_sql` positional arg of
   pg-translated `_db_read` calls — documented-dead, ungated.)
2. ~~**mios-daemon read-gating**~~ — DONE: `_db_read` gates on `_PG_ENABLED`;
   `_tool_calls_for_refine` `refine_ts` bound (`$1`); the two previously-untranslated
   reads (`rolling_report_loop`, `_daemon_global_log_context`) now have pg_sql
   translations against the live `log_digest` table; the task_collector kanban sync
   writes the canonical pg `kanban` (was dead `kanban_shadow`).
3. ~~**mios-viking**~~ — DONE: dead `_db_sql` transport removed; knowledge-ns reads
   bound via `mios-db --pg-json`.

Operator: live-test the memory/skill/kanban/RAG/daemon paths in the VM
(`just build` → boot → `mios-remember add`, `mios-kg lookup`, `mios-skills mine`,
a multi-task prompt, `mios-rag query`, and check the daemon rolling-report +
nudger kanban rows land in pg). Optional: flip default `MIOS_PGVECTOR_DB_BACKEND`
dual→postgres to drop the now-pointless dead-surreal write *attempts* in the
agent-pipe (its `_db_read` gate is the one central-path flip I left for the VM
loop).
