<!-- AI-hint: Upstream reference for pgvector, the PostgreSQL vector extension behind the mios-pgvector unified agent datastore — what it is, how the ref floats on the pgNN family tag, how that flows through the SSOT projections, the extension-upgrade procedure, and how the PostgreSQL MAJOR is migrated by mios-pgvector-major-upgrade so the pgNN suffix can advance without stranding the datastore.
     AI-related: mios-pgvector, usr/share/mios/mios.toml, usr/share/containers/systemd/mios-pgvector.container, usr/share/mios/postgres/schema-init.sql, usr/libexec/mios/mios-resolve-latest, automation/support/bringup-pgvector.sh -->

# pgvector — PostgreSQL vector search (the agent datastore)

> Used by MiOS for: the unified agent datastore. `mios-pgvector` is
> PostgreSQL + the `vector` extension holding agent memory, events, tool
> calls, sessions, skills and the `knowledge` table with HNSW vector recall —
> the persistent brain every lane and agent reads through.
> Source: `usr/share/mios/mios.toml` §[pgvector] + §[containers.mios-pgvector],
> `usr/share/mios/postgres/schema-init.sql`,
> `usr/share/doc/mios/concepts/architecture.md` §AI plane.

## Why this matters to MiOS

MiOS is one thing built two ways at once: an immutable, bootc/OCI-shaped Fedora
workstation that is *also* a local, self-hosted, agentic AI operating system.
Everything the agent half *remembers* lives in this one container. Losing an
inference lane costs a reply; corrupting the datastore costs the operator's
accumulated memory — which is why the major move below is guarded rather than
assumed, even though the pgvector version itself floats like every other image.

## Projects

- pgvector — <https://github.com/pgvector/pgvector> (PostgreSQL License).
  Vector types + HNSW/IVFFlat indexes for PostgreSQL. Publishes git *tags*
  (`v0.8.6` style), not GitHub Releases.
- Official images — <https://hub.docker.com/r/pgvector/pgvector>. Tag scheme
  `<pgvector-version>-pg<PG-major>` (e.g. `0.8.6-pg17`, `0.8.6-pg18`) plus
  floating `pgNN` family tags.

## Versioning — floats on the `pgNN` family tag

| Surface | Value | Why |
|---|---|---|
| `mios.toml [image.sidecars].pgvector` | `docker.io/pgvector/pgvector:pg18` | **Floats** (ADR-0012): always the newest pgvector built for that PG major, resolved to a digest at build and recorded in the SBOM. No hand-pinned version to rot. Newest always satisfies `hnsw.iterative_scan` (pgvector ≥ 0.8.0), which the Quadlet `Exec=` sets unconditionally. |
| The `pgNN` suffix | the PostgreSQL **major** | A newer-major image refuses to start on an older `PGDATA`, and the upstream images ship only one major's binaries, so in-place `pg_upgrade` is unavailable. `mios-pgvector-major-upgrade.service` is what makes the major movable: see below. |
| Projections | Quadlet `Image=`, `automation/lib/globals.{sh,ps1}` (`MIOS_PGVECTOR_IMAGE`), `[build.bake].core`, `usr/lib/mios/bake/plan.d/04-extra.list`, env-baseline | Never hand-edit the projections: change `[image.sidecars]` and run `tools/sync-generated.sh` (Law 8). |

There is no version-bump procedure, and that is the point: the float re-resolves
on every build. To pick up a new pgvector release, rebuild. What *does* need a
deliberate procedure is the PG major, below.

On the first start of a newer image, update the extension inside the database
(extension name is `vector`, not "pgvector"; the version column is
`pg_extension.extversion`):

```sql
SELECT extversion FROM pg_extension WHERE extname = 'vector';
ALTER EXTENSION vector UPDATE;
-- integrity audit: any invalid index would show here (none expected on 0.8.x minors)
SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid OR NOT indisready;
```

pgvector minor updates require no reindex.

### Moving the PostgreSQL major

`usr/libexec/mios/mios-pgvector-major-upgrade`, run by a oneshot ordered
`Before=mios-pgvector.service`, makes the `pgNN` suffix advanceable:

1. Compares `PG_VERSION` in the data dir against the `pgNN` major in the image ref.
   Equal, or no cluster yet → no-op (and it blanks any spent restore slot).
2. On a mismatch it dumps the old cluster **with an image of the OLD major**
   (`pg_dump --clean --if-exists`) into `[pgvector].restore_sql`, which the Quadlet
   bind-mounts at `docker-entrypoint-initdb.d/20-mios-restore.sql`.
3. It then *stashes* — never deletes — the old data dir as
   `<data_dir>.pg<old>.<timestamp>`, so the new major initdb's cleanly, runs
   `10-mios-schema.sql`, then replays the dump over it (`--clean` makes that
   ordering idempotent).

It is **non-destructive by construction**: if the old-major image is absent or the
dump fails or comes back empty, it touches nothing and exits 0 — pgvector then
stops on Postgres's own major-mismatch error with the data exactly where it was.
A downgrade (image major older than the cluster) is refused outright. Remove the
stash yourself once you have verified the new cluster.

## CVE posture

CVE-2026-3172 (CVSS 8.1 — buffer overflow in *parallel* HNSW build, affects
0.6.0–0.8.1, fixed 0.8.2) never reached a shipped MiOS image: the ref was
already at 0.8.3 when the CVE published, and the `pg18` float now resolves to
0.8.6 or newer on every build. CVE response for this image is therefore
"rebuild so the float re-resolves", not a pin. Interim mitigation for
any future index-build CVE of the same shape:
`SET max_parallel_maintenance_workers = 0;` disables the parallel build path
without touching data. Verification record:
`usr/share/doc/mios/reference/upstream-gaps-2026-08.md` §CVE matrix.

## MiOS integration points

| Piece | Where |
|---|---|
| Quadlet | `usr/share/containers/systemd/mios-pgvector.container` — `User=826`, `Delegate=yes`, joins `mios-ai.pod` (host net), `Exec=postgres -c port=${MIOS_PORTS_PGVECTOR:-8600} -c listen_addresses=127.0.0.1 -c hnsw.iterative_scan=strict_order ...` |
| Schema | `usr/share/mios/postgres/schema-init.sql` (knowledge/memory/events/tools/sessions + HNSW indexes) |
| SSOT knobs | `mios.toml [pgvector]` — db/user `mios`, `data_dir=/var/lib/mios/pgvector`, `emb_version`, HNSW scan tuning, backups |
| Bring-up check | `automation/support/bringup-pgvector.sh` (renders the Quadlet, verifies the `vector` extension) |
| Boot health | `usr/lib/greenboot/check/required.d/40-mios-ai-plane.sh` probes pgvector; failure triggers bootc rollback |

## Cross-refs

- `usr/share/doc/mios/reference/upstream-gaps-2026-08.md` — the 2026-08 verification pass that floated this ref.
- `usr/share/doc/mios/reference/upstream-gaps-2026-07.md` §pgvector-rag — feature gaps (hybrid recall, reranking, halfvec).
- `usr/share/doc/mios/upstream/inference-engines.md` — the lanes whose embeddings this store indexes.
- `usr/lib/systemd/system/mios-pgvector-major-upgrade.service` — the guard that makes the `pgNN` major movable.
- `usr/share/doc/mios/adr/0012-float-latest-no-hand-pinned-versions.md` — the float-latest principle this ref now follows.
- `ROADMAP.md` §WS-UPSTREAM — the workstream this ref's float and major-migration landed under.
