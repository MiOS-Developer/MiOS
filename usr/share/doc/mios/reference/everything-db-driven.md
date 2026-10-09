<!-- AI-hint: WS-VECTOR research + workflow -- make EVERYTHING in MiOS DB-driven + vectorized: mios.toml is the cold image-baked authoring seed, Postgres/pgvector (mios-pgvector, db=mios, /var) is the LIVE runtime SSOT. Unified schema (config/verbs/recipes/packages/build/xbox/accounts/prefs, all with emb columns), 6-phase offline-safe migration, new vectorization targets. From the 2026-07-10 ultracode survey of the AI plane, installs/build, accounts, xbox-build, config, + external best practice. -->
<!-- AI-related: usr/share/mios/postgres/schema-init.sql, usr/lib/mios/mios_toml.py, usr/lib/mios/agent-pipe/mios_pipe/memory/pg.py, usr/libexec/mios/seed-db-config.py, automation/98-drift-checks.sh, usr/share/mios/mios.toml -->

# WS-VECTOR — Everything DB-driven + vectorized (unified pgvector control plane)

**Status:** planned (workstream) · **Source:** operator directive + 2026-07-10 ultracode survey (AI plane · installs/build · accounts/users · MiOS-Xbox build · config/verbs · external best practice) · **Effort:** XL (6-phase, offline-safe)

## The law (resolves the file-vs-DB tension)

`mios.toml` is the **human-readable, git-diffable, image-baked (`/usr`) COLD-START
authoring seed**. **Postgres/pgvector** (`mios-pgvector`, `db=mios`, in `/var`) is the
**LIVE RUNTIME SSOT**. On first boot a deterministic seeder projects the baked TOML →
DB; thereafter the DB is authoritative and every resolver reads the DB with TOML as a
**fail-open** fallback. A DB→TOML materialize step regenerates the file for the next
image build. Same regenerate-and-diff discipline as the theme SSOT (drift check 25 `check_container_ports`),
now spanning the build boundary.

**Storage law.** Ground-truth values stay in typed relational columns; every
semantically-recallable row gets a separate `emb vector(768)` (nomic-embed-text via
`mios-llm-light /v1/embeddings`) over a TEXT PROJECTION, `HNSW(emb vector_cosine_ops,
m=16, ef_construction=64)`, with `emb_model`/`emb_version` provenance. **Vectors find;
SQL reads. Never vectorize the authoritative value.**

## Current state (survey)

Already DB-backed + (partly) vectorized — the AI plane: `knowledge`, `agent_memory`,
`mios_rag` (all `emb vector(768)` HNSW cosine + hybrid fts), plus `tool_call`,
`session`/`gateway_sessions`, `skill`, `event`, `pending_action`, `run_template`,
`person`, `account` (the OS-account control plane driving NSS/SAM), `agent_keypair`,
`peer_reputation`, and the **seeded-but-unread** `system_config`/`verb`/`domain_verb`.
Schema SSOT: `usr/share/mios/postgres/schema-init.sql`; client seam:
`mios_pipe/memory/pg.py`; seeder: `usr/libexec/mios/seed-db-config.py`.

The gap: the **runtime verb/recipe catalog + config resolution still read `mios.toml`**
(`verbcatalog.py`, `mios_toml.py`) — the DB `verb`/`system_config` copy is **write-only
drift**. Only `knowledge`/`agent_memory`/`mios_rag` are vectorized. Installs, the OCI +
MiOS-Xbox builds, package sets, debloat/feature policy, user preferences/skel remain
file/TOML/XML/JSON.

## Unified schema (adds to schema-init.sql)

- `config_layer(rank PK, name)` — canonical precedence {0:vendor,1:host,2:user,3:machine}.
- `config_kv(scope,key,value jsonb,layer FK,owner_user,description,emb)` — every knob.
- `verb`/`domain_verb` — gain `layer`+`owner_user`+`emb`; the runtime read-path.
- `recipe`/`routing_phrase` — verb recipes + deterministic phrase buckets (with `emb`).
- `package_set(name,section,pkgs jsonb,enable,layer,base_image_ref,emb)` — the `[packages.*]` SSOT.
- `build_recipe`/`build_phase` — OCI + Xbox edition recipes; `build_phase(ordinal,script,stage∈{container,runtime,firstboot},deps)` — the WS-DEPLOY DAG as rows.
- `xbox_feature`/`debloat_policy`/`debloat_profile`/`feature_set`/`{appx,feature,capability,component}_removal`/`preset` — the MiOS-Xbox build catalog (with `emb`).
- `account`(+`home_dir`,`shell`) / `uid_alloc` SEQUENCE + `allocate_uid()/gid()` / `account_preference(account_id FK,layer,key,value,emb)` — DB-owned ids + per-user prefs → render dotfiles from the DB, retire static skel.

## Phases (offline-bootstrap-safe, drift-gated, no functionality loss)

- **V0 Foundation** — unified DB in `/var`; `emb`/`emb_model`/`emb_version` provenance; DB→TOML materialize (invert direction); drift-gates 29+ (`drift_projection`); make the verb round-trip lossless.
- **V1 Config read-path** — a config resolver peer of `mios_toml.py` reading `config_kv`/`verb`/`domain_verb`/`recipe`/`routing_phrase`, TOML fail-open; kill the write-only `system_config` drift.
- **V2 AI-plane vectors** — add `emb` to `skill`,`verb`,`tool_call`,`event`,`session`,`directory_entry`; retire the in-process verb/apps embedding caches for native `<=>`.
- **V3 Build catalog** — the `package_set`/`build_recipe`/`xbox_feature`/`debloat_policy`/`*_removal` tables; DB→`/ctx` materialize solves the clean-container chicken-and-egg; unify build-time vs runtime identity onto `account`.
- **V4 Accounts/users** — `account.home_dir`/`shell`, uid_alloc sequence, `account_preference` (render dotfiles from DB), bidirectional write-back (Linux pam/getent, Windows SAM watcher).
- **V5 Invert authority** — DB is SSOT, TOML is generated export; the configurator CRUDs the DB (emits `config_event`); event-sourced install/build/config/account with time-travel + rollback (bootc atomic-upgrade alignment).

## Vectorization targets

EXISTING (unchanged): `knowledge.emb`, `agent_memory.emb`, `mios_rag.emb`.
NEW: `verb.emb` (native tool-search, retires the in-process BM25/cosine rebuild),
`skill.emb`, `recipe.emb`+`package_set.emb`+`build_recipe.emb`, `xbox_feature.emb`
(semantic "find the feature that does X"), `debloat_policy.emb`+`preset.emb`,
`config_kv.emb` (fuzzy "find the setting that does X"),
`account.emb`+`person.emb`+`account_preference.emb`.

## Invariants / guardrails

- DB lives in `/var` (Docker VOLUME), never image-baked; factory-reset reseeds from the `/usr` TOML.
- Cold boot with empty `/var` must self-heal: seed from baked TOML, set an authority sentinel, then run DB-authoritative — every resolver keeps a TOML fail-open branch.
- Authority flips **per-surface** only when read-path + lossless round-trip + drift-gate are all green.
- WS-NAME model-facing aliases + load-bearing legacy verbs (winget_*/flatpak_* re-dispatch, memory_append/replace) migrate via coordinated fold-refactor, never blind-drop.
- `password_hash` hash-only; reconcile the `/etc/shadow` parallel store via a pam write-back or the two credential planes drift.

Cross-refs: theme-ssot-projection (projection engine + check 25), mios-flatten-consolidation
(shared `mios_toml.py`, drift-gates 25-28, load-bearing-verb caution). New drift-gates
land in `automation/98-drift-checks.sh` (29+). Tasks: **task_221–task_225** (V0–V5).

## V1 Config read-path Flip & Rollback Runbook

Database authority is designed to be flipped safely and reverted instantly.

### surfaces and sentinel values

The system reads settings from `config_kv`, `verb`, and `domain_verb` when the sentinel is enabled. The sentinel can be controlled in two ways:
1. **Configuration File:** The `db_authoritative` key under the `[ai]` section in `mios.toml` (or its overlay `/etc/mios/mios.toml`).
   ```toml
   [ai]
   db_authoritative = true
   ```
2. **Environment Override:** The `MIOS_DB_AUTHORITATIVE` environment variable (e.g. `MIOS_DB_AUTHORITATIVE=true`). This takes precedence over the TOML setting.

### pre-flip validation
Before enabling database authority:
1. Run `just drift-gate` (or verify that unit tests pass) to verify that the seeded database matches the disk configuration.
2. Check the `/health` or `/v1/cluster/health` observability endpoint. The `config_divergences` metric must be `0`.
3. Check system logs for any `Config divergence in ...` warning logs.

### promotion (flipping to DB-authoritative)
1. Add `db_authoritative = true` to the `[ai]` section of `/etc/mios/mios.toml`.
2. Restart the `mios-agent-pipe` service to load the new config:
   ```bash
   sudo systemctl restart mios-agent-pipe
   ```

### rollback procedure
If any misbehavior is observed:
1. Revert `db_authoritative = false` in `/etc/mios/mios.toml` (or delete the line).
2. Restart the service:
   ```bash
   sudo systemctl restart mios-agent-pipe
   ```
3. The system will instantly fall back to reading from `mios.toml`, maintaining full service availability.
